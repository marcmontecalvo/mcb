package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/** Copy-on-write planner snapshots. Zero and absent sections always mean unknown. */
final class PlannerMap {
    private static final int SECTION_CELLS = 4096, MAX_SECTIONS = 262144, MAX_PALETTE = 65535;
    private record Key(int x, int y, int z) { }
    private record Snapshot(long generation, Map<Key, int[]> sections, List<Map<String, Object>> palette) { }

    private Snapshot committed = new Snapshot(0, Map.of(), List.of(Map.of()));
    private long pendingGeneration = -1;
    private Map<Key, int[]> sections;
    private List<Map<String, Object>> palette;
    private final Set<Key> copied = new HashSet<>();

    long generation() { return committed.generation; }

    void begin(long baseGeneration, long generation) {
        if (pendingGeneration != -1 || baseGeneration != committed.generation || generation <= baseGeneration)
            throw new IllegalArgumentException("map_generation");
        pendingGeneration = generation;
        sections = new HashMap<>(committed.sections);
        palette = new ArrayList<>(committed.palette);
        copied.clear();
    }

    void palette(int first, List<Map<String, Object>> facts) {
        requirePending();
        if (first != palette.size() || facts.isEmpty() || palette.size() + facts.size() > MAX_PALETTE + 1)
            throw new IllegalArgumentException("map_palette");
        for (var fact : facts) {
            if (fact == null || (fact.containsKey("block") && !(fact.get("block") instanceof String))
                    || (fact.containsKey("fluid") && !(fact.get("fluid") instanceof String)))
                throw new IllegalArgumentException("map_fact");
            if (fact.containsKey("collision_boxes")) {
                if (!(fact.get("collision_boxes") instanceof List<?> boxes) || boxes.size() > 4096)
                    throw new IllegalArgumentException("map_collision_boxes");
                for (var entry : boxes) {
                    if (!(entry instanceof List<?> box) || box.size() != 6) throw new IllegalArgumentException("map_collision_box");
                    for (int i = 0; i < 6; i++)
                        if (!(box.get(i) instanceof Number number) || !Double.isFinite(number.doubleValue()))
                            throw new IllegalArgumentException("map_collision_box");
                    for (int i = 0; i < 3; i++)
                        if (((Number)box.get(i)).doubleValue() > ((Number)box.get(i + 3)).doubleValue())
                            throw new IllegalArgumentException("map_collision_box");
                }
            }
            var frozen = new HashMap<String, Object>();
            for (var entry : fact.entrySet()) frozen.put(entry.getKey(), freeze(entry.getValue(), 0));
            palette.add(Map.copyOf(frozen));
        }
    }

    private static Object freeze(Object value, int depth) {
        if (depth > 16 || value == null) throw new IllegalArgumentException("map_fact_value");
        if (value instanceof String || value instanceof Boolean) return value;
        if (value instanceof Number number && Double.isFinite(number.doubleValue())) return value;
        if (value instanceof List<?> list) {
            if (list.size() > 4096) throw new IllegalArgumentException("map_fact_value");
            var frozen = new ArrayList<>();
            for (var item : list) frozen.add(freeze(item, depth + 1));
            return List.copyOf(frozen);
        }
        if (value instanceof Map<?, ?> map) {
            if (map.size() > 4096) throw new IllegalArgumentException("map_fact_value");
            var frozen = new HashMap<String, Object>();
            for (var entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String key)) throw new IllegalArgumentException("map_fact_value");
                frozen.put(key, freeze(entry.getValue(), depth + 1));
            }
            return Map.copyOf(frozen);
        }
        throw new IllegalArgumentException("map_fact_value");
    }

    void page(int sx, int sy, int sz, int offset, int[] states) {
        requirePending();
        if (offset < 0 || states.length == 0 || offset + states.length > SECTION_CELLS)
            throw new IllegalArgumentException("map_page");
        for (int state : states) if (state < 0 || state >= palette.size()) throw new IllegalArgumentException("map_state");
        var key = new Key(sx, sy, sz);
        var section = sections.get(key);
        if (section == null) {
            if (sections.size() >= MAX_SECTIONS) throw new IllegalArgumentException("map_sections");
            section = new int[SECTION_CELLS]; sections.put(key, section); copied.add(key);
        } else if (copied.add(key)) {
            section = section.clone(); sections.put(key, section);
        }
        System.arraycopy(states, 0, section, offset, states.length);
    }

    void section(int sx, int sy, int sz, String encoded) {
        byte[] compressed = Base64.getDecoder().decode(encoded);
        if (compressed.length > 12_000) throw new IllegalArgumentException("map_compressed_size");
        var inflater = new Inflater();
        try {
            inflater.setInput(compressed);
            byte[] bytes = new byte[SECTION_CELLS * 2 + 1];
            int size = 0;
            while (!inflater.finished() && size < bytes.length) {
                int count = inflater.inflate(bytes, size, bytes.length - size);
                if (count == 0) break;
                size += count;
            }
            if (size != SECTION_CELLS * 2 || !inflater.finished() || inflater.getRemaining() != 0)
                throw new IllegalArgumentException("map_section_encoding");
            int[] states = new int[SECTION_CELLS];
            for (int i = 0; i < states.length; i++) states[i] = (bytes[2 * i] & 255) | ((bytes[2 * i + 1] & 255) << 8);
            page(sx, sy, sz, 0, states);
        } catch (DataFormatException error) {
            throw new IllegalArgumentException("map_section_encoding", error);
        } finally { inflater.end(); }
    }

    void remove(int sx, int sy, int sz) { requirePending(); sections.remove(new Key(sx, sy, sz)); }

    void commit(long generation) {
        requirePending();
        if (generation != pendingGeneration) throw new IllegalArgumentException("map_generation");
        committed = new Snapshot(generation, Map.copyOf(sections), List.copyOf(palette));
        abort();
    }

    void abort() { pendingGeneration = -1; sections = null; palette = null; copied.clear(); }
    private void requirePending() { if (pendingGeneration == -1) throw new IllegalStateException("map_not_started"); }

    RouteGeometry.Reader reader(long generation) {
        var snapshot = committed;
        if (generation != snapshot.generation) throw new IllegalArgumentException("map_generation");
        return (x, y, z) -> {
            var section = snapshot.sections.get(new Key(x >> 4, y >> 4, z >> 4));
            if (section == null) return null;
            int state = section[((x & 15) << 8) | ((y & 15) << 4) | (z & 15)];
            return state == 0 ? null : snapshot.palette.get(state);
        };
    }
}
