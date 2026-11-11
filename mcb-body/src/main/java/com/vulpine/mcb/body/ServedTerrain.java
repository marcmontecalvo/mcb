package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * The terrain the body served on /policy, read by plans without a Python upload
 * ("known = served": a served batch counts as delivered). A reader answers as of the
 * served sample tick the plan request names, never newer, so a batch served while
 * Python plans on an older read stays invisible to that plan.
 *
 * Threads: batches are staged on the server thread and applied on the HTTP thread
 * just before /policy writes them; readers run on the server thread. Published
 * versions and their arrays are never modified.
 */
final class ServedTerrain {
    static final int SECTION_CELLS = 4096;
    /** char[4096] (8,208 B with header) plus version record, key and map entry. */
    static final long SECTION_BYTES = 8_208 + 96;
    static final long MAX_BYTES = 128L << 20; // Python's active-map budget
    /** Superseded versions stay readable this long so a multi-tick plan reads one map. */
    static final int HORIZON_TICKS = 1200;

    record Key(int x, int y, int z) { }
    private record Version(int tick, char[] states, boolean shared, Version previous) { }
    private record Staged(int tick, List<Map<String,Object>> cells, AwarenessSensor.SectionBatch bulk,
                          int cx, int cy, int cz) { }

    private final Map<Key, Version> sections = new ConcurrentHashMap<>();
    private final List<Map<String,Object>> palette = new CopyOnWriteArrayList<>(List.of(Map.of()));
    private final Map<Map<String,Object>, Character> ids = new HashMap<>();
    private final Map<Character, char[]> uniforms = new HashMap<>();
    private final IdentityHashMap<Object, Staged> staged = new IdentityHashMap<>();
    private final List<Object> stagedOrder = new ArrayList<>();
    private final List<Map<String,Object>> evicted = new ArrayList<>();
    private volatile int latestTick = Integer.MIN_VALUE;
    private long bytes;

    int latestTick() { return latestTick; }
    synchronized long bytes() { return bytes; }
    int sectionCount() { return sections.size(); }

    /** Server thread: what this batch carries, until /policy serves it. */
    synchronized void stage(Object batch, int tick, List<Map<String,Object>> cells,
                            AwarenessSensor.SectionBatch bulk, int cx, int cy, int cz) {
        staged.put(batch, new Staged(tick, cells, bulk, cx, cy, cz));
        stagedOrder.add(batch);
        // ponytail: one sequential client reads /policy; two pending batches cover a late serve.
        while (stagedOrder.size() > 2) staged.remove(stagedOrder.remove(0));
    }

    /** HTTP thread, before the response is written: the served batch becomes known. */
    synchronized void serve(Object batch) {
        var s = staged.remove(batch);
        stagedOrder.remove(batch);
        if (s == null || s.tick <= latestTick) return;
        var fresh = new HashMap<Key, char[]>();
        var shared = new HashMap<Key, Boolean>();
        if (s.bulk != null) for (var d : s.bulk.sections()) {
            var key = new Key(d.x(), d.y(), d.z());
            if (d.ids() == null) {
                fresh.put(key, uniform(intern(s.bulk.facts().get(d.uniform())))); shared.put(key, true);
                continue;
            }
            var local = new HashMap<Integer, Character>();
            char[] states = new char[SECTION_CELLS];
            for (int i = 0; i < SECTION_CELLS; i++) {
                int id = d.ids()[i];
                Character c = local.get(id);
                if (c == null) { c = intern(s.bulk.facts().get(id)); local.put(id, c); }
                states[i] = c;
            }
            fresh.put(key, states); shared.put(key, false);
        }
        for (var cell : s.cells) {
            if (!(cell.get("position") instanceof List<?> p) || p.size() != 3) continue;
            int x = ((Number) p.get(0)).intValue(), y = ((Number) p.get(1)).intValue(), z = ((Number) p.get(2)).intValue();
            var facts = new HashMap<>(cell);
            facts.remove("position"); facts.remove("observed_tick");
            char c = intern(Map.copyOf(facts));
            var key = new Key(x >> 4, y >> 4, z >> 4);
            char[] states = fresh.get(key);
            if (states == null || shared.get(key)) {
                var base = states != null ? states : current(key);
                states = base == null ? new char[SECTION_CELLS] : base.clone();
                fresh.put(key, states); shared.put(key, false);
            }
            states[((x & 15) << 8) | ((y & 15) << 4) | (z & 15)] = c;
        }
        int horizon = s.tick - HORIZON_TICKS;
        for (var entry : fresh.entrySet()) {
            var key = entry.getKey();
            var old = sections.get(key);
            if (old != null && Arrays.equals(old.states, entry.getValue())) continue;
            var version = new Version(s.tick, entry.getValue(), shared.get(key), prune(old, horizon));
            bytes += chainCost(version) - chainCost(old);
            sections.put(key, version);
        }
        latestTick = s.tick;
        if (bytes > MAX_BYTES) evict(s);
    }

    private char[] current(Key key) { var v = sections.get(key); return v == null ? null : v.states; }

    private char intern(Map<String,Object> facts) {
        if (facts == null) throw new IllegalArgumentException("served_fact");
        var id = ids.get(facts);
        if (id != null) return id;
        if (palette.size() > 65535) throw new IllegalStateException("served_palette_exhausted");
        char c = (char) palette.size();
        palette.add(facts); ids.put(facts, c);
        return c;
    }

    private char[] uniform(char state) {
        return uniforms.computeIfAbsent(state, s -> { var a = new char[SECTION_CELLS]; Arrays.fill(a, s); return a; });
    }

    private static long cost(Version v) { return v.shared ? 0 : SECTION_BYTES; }
    private static long chainCost(Version v) { long total = 0; for (; v != null; v = v.previous) total += cost(v); return total; }

    /** v is readable for ticks from v.tick up to its successor's; drop what no reader inside the horizon needs. */
    private static Version prune(Version v, int horizon) {
        if (v == null) return null;
        return new Version(v.tick, v.states, v.shared, v.tick > horizon ? prune(v.previous, horizon) : null);
    }

    /** Farthest sections from the body go first, down to 90% of the cap. */
    private void evict(Staged s) {
        var keys = new ArrayList<>(sections.keySet());
        keys.sort(Comparator.comparingLong((Key k) -> -distance(k, s)));
        var dropped = new ArrayList<List<Integer>>();
        for (var key : keys) {
            if (bytes <= MAX_BYTES * 9 / 10) break;
            bytes -= chainCost(sections.remove(key));
            dropped.add(List.of(key.x(), key.y(), key.z()));
        }
        evicted.add(Map.of("tick", s.tick, "sections", List.copyOf(dropped)));
    }

    private static long distance(Key k, Staged s) {
        long dx = Math.abs(k.x() - (s.cx >> 4)), dy = Math.abs(k.y() - (s.cy >> 4)), dz = Math.abs(k.z() - (s.cz >> 4));
        return Math.max(dx, Math.max(dy, dz));
    }

    /** Server thread: evictions since the last call, for the event log (replay subtracts them). */
    synchronized List<Map<String,Object>> drainEvicted() {
        var result = List.copyOf(evicted); evicted.clear(); return result;
    }

    /** As of a served tick; unknown cells fall through to the uploaded overlay (recalled sections). */
    RouteGeometry.Reader reader(int asOf, RouteGeometry.Reader overlay) {
        int latest = latestTick;
        if (asOf > latest) throw new IllegalArgumentException("terrain_tick_unserved");
        if ((long) asOf < (long) latest - HORIZON_TICKS / 2) throw new IllegalArgumentException("terrain_tick_stale");
        return (x, y, z) -> {
            var v = sections.get(new Key(x >> 4, y >> 4, z >> 4));
            while (v != null && v.tick > asOf) v = v.previous;
            int state = v == null ? 0 : v.states[((x & 15) << 8) | ((y & 15) << 4) | (z & 15)];
            return state == 0 ? overlay.cell(x, y, z) : palette.get(state);
        };
    }
}
