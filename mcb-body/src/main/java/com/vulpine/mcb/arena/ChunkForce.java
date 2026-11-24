package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import net.minecraft.server.level.ServerLevel;

/**
 * Reference-counted chunk forceloading for builds and matches. A chunk someone else already forced (an operator's
 * /forceload) is never unforced here; the chunks this class forced are what an arena record persists, so the next start
 * can release them after a crash. Server thread only.
 */
final class ChunkForce {
    private final Map<String, Map<Long, Integer>> counts = new HashMap<>();
    private final Map<String, Set<Long>> foreign = new HashMap<>();

    void hold(ServerLevel level, String dimension, long[] keys) {
        var held = counts.computeIfAbsent(dimension, d -> new HashMap<>());
        var others = foreign.computeIfAbsent(dimension, d -> new HashSet<>());
        for (long key : keys) {
            int n = held.merge(key, 1, Integer::sum);
            if (n == 1 && !level.setChunkForced(ArenaBox.chunkX(key), ArenaBox.chunkZ(key), true)) others.add(key);
        }
    }

    void release(ServerLevel level, String dimension, long[] keys) {
        var held = counts.get(dimension);
        if (held == null) return;
        var others = foreign.getOrDefault(dimension, Set.of());
        for (long key : keys) {
            Integer n = held.get(key);
            if (n == null) continue;
            if (n > 1) { held.put(key, n - 1); continue; }
            held.remove(key);
            if (!others.remove(key)) level.setChunkForced(ArenaBox.chunkX(key), ArenaBox.chunkZ(key), false);
        }
    }

    /** Of these keys, the ones this class forced itself (what a record persists for crash recovery). */
    List<Long> owned(String dimension, long[] keys) {
        var others = foreign.getOrDefault(dimension, Set.of());
        var out = new ArrayList<Long>();
        for (long key : keys) if (!others.contains(key)) out.add(key);
        return out;
    }

    /** Releases chunks a previous run forced and never freed. */
    static void unforceAll(ServerLevel level, List<Long> keys) {
        for (long key : keys) level.setChunkForced(ArenaBox.chunkX(key), ArenaBox.chunkZ(key), false);
    }
}
