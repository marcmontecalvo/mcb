package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Content-addressed store for the large, slowly changing edit sets of a plan request
 * (body_search.edit_payload "protected" and "costly"). Python uploads a set once
 * (planner_set begin/part/commit, its id the sha256 of its JSON) and each plan_route
 * names it as edits.&lt;field&gt;_set, sending only the cells added since inline. Every
 * plan_route used to carry the whole build (28 KB; house-large run 20261119T133618892664Z).
 * A reference this store does not hold is refused (plan_set_unknown): a plan never runs
 * without the protection it asked for. Reset with the body (spawn), like PlannerMap.
 */
final class PlanSets {
    static final int MAX_SETS = 16;
    /** Route edits: protected, costly; terrain plans: top-level protected, policy.*_positions. */
    static final Set<String> FIELDS = Set.of("protected", "costly", "protected_positions", "costly_positions");
    private static final int MAX_ENTRIES = 4_000_000;
    private record Stored(String field, JsonArray entries) { }

    private final PlanData transfer = new PlanData();
    private final LinkedHashMap<String, Stored> sets = new LinkedHashMap<>(32, .75f, true) {
        @Override protected boolean removeEldestEntry(Map.Entry<String, Stored> eldest) { return size() > MAX_SETS; }
    };

    void begin(String id, int encodedLength, int rawLength, String digest) {
        if (!id.equals(digest)) throw new IllegalArgumentException("plan_set_id");
        transfer.begin(id, encodedLength, rawLength, digest);
    }

    void append(String id, int offset, String data) { transfer.append(id, offset, data); }

    /** Verifies the digest (PlanData.take) and stores the set under it. */
    void commit(String id) {
        JsonObject set = transfer.take(id);
        if (!set.has("field") || !set.get("field").isJsonPrimitive() || !FIELDS.contains(set.get("field").getAsString())
                || !set.has("entries") || !set.get("entries").isJsonArray()
                || set.getAsJsonArray("entries").size() > MAX_ENTRIES)
            throw new IllegalArgumentException("plan_set_shape");
        sets.put(id, new Stored(set.get("field").getAsString(), set.getAsJsonArray("entries")));
    }

    void abort() { transfer.abort(); }

    int size() { return sets.size(); }

    /**
     * The edits object with each &lt;field&gt;_set reference replaced by the stored entries
     * followed by the inline ones (a later inline entry wins for the same cell). The input
     * is not changed; a request without references is returned as an equal copy.
     */
    JsonObject resolve(JsonObject edits) {
        var out = new JsonObject();
        for (var e : edits.entrySet()) if (!e.getKey().endsWith("_set")) out.add(e.getKey(), e.getValue());
        for (var e : edits.entrySet()) {
            if (!e.getKey().endsWith("_set")) continue;
            String field = e.getKey().substring(0, e.getKey().length() - 4);
            if (!FIELDS.contains(field)) throw new IllegalArgumentException("plan_set_field");
            Stored stored = e.getValue().isJsonPrimitive() ? sets.get(e.getValue().getAsString()) : null;
            if (stored == null) throw new IllegalArgumentException("plan_set_unknown");
            if (!stored.field().equals(field)) throw new IllegalArgumentException("plan_set_field");
            var merged = new JsonArray(stored.entries().size() + 16);
            merged.addAll(stored.entries());
            if (edits.has(field)) {
                if (!edits.get(field).isJsonArray()) throw new IllegalArgumentException("plan_set_field");
                merged.addAll(edits.getAsJsonArray(field));
            }
            out.add(field, merged);
        }
        return out;
    }
}
