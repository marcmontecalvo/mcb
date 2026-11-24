package com.vulpine.mcb.arena;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

/**
 * One placing job: a plan's fills set cell by cell over several ticks within a time budget, or (kind RESTORE) the
 * arena's whole box set to air. The blocks are parsed before the job exists, so it can only fail for lack of loaded chunks.
 * Server thread only.
 */
final class ArenaBuild {
    enum Kind { BUILD, RESTORE }

    /** No neighbour or shape updates (the plan's states are exact), no drops from what is replaced. */
    static final int FLAGS = Block.UPDATE_CLIENTS | Block.UPDATE_KNOWN_SHAPE | Block.UPDATE_SUPPRESS_DROPS;
    /** Ticks to wait for the forceloaded chunks to be there before giving up. */
    static final int CHUNK_WAIT_TICKS = 1200;
    private static final int BATCH = 512;

    final ArenaRecord record;
    final Kind kind;
    private final ServerLevel level;
    private final BlockState[] states;
    private final ArenaCursor cursor;
    private final long[] chunks;
    private final BlockPos.MutableBlockPos cell = new BlockPos.MutableBlockPos();
    private int waited, ticks;
    private boolean chunksReady;
    private int lastPercent = -1;
    private int sinceProgress;
    /** Why the job gave up, or null. */
    private String aborted;

    ArenaBuild(ArenaRecord record, ServerLevel level, Kind kind, List<ArenaPlan.Fill> fills, BlockState[] states) {
        this.record = record;
        this.level = level;
        this.kind = kind;
        this.states = states;
        this.cursor = new ArenaCursor(fills);
        this.chunks = record.box().chunks();
    }

    /** The job that sets the arena's whole box to air. */
    static ArenaBuild restore(ArenaRecord record, ServerLevel level) {
        var size = record.size();
        var fill = new ArenaPlan.Fill(new int[] {0, 0, 0}, new int[] {size[0] - 1, size[1] - 1, size[2] - 1}, "minecraft:air");
        return new ArenaBuild(record, level, Kind.RESTORE, List.of(fill), new BlockState[] {Blocks.AIR.defaultBlockState()});
    }

    ServerLevel level() { return level; }

    String aborted() { return aborted; }

    boolean done() { return cursor.done(); }

    Map<String, Object> progress() {
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        out.put("kind", kind.name().toLowerCase());
        out.put("percent", ArenaLogic.percent(cursor.placed(), cursor.total()));
        out.put("placed", cursor.placed());
        out.put("total", cursor.total());
        return out;
    }

    /** Places what the budget allows; true once the job has finished or given up. */
    boolean tick(long budgetNanos, ArenaEmit emit) {
        ticks++;
        if (!chunksReady) {
            for (long key : chunks) {
                if (level.getChunkSource().getChunkNow(ArenaBox.chunkX(key), ArenaBox.chunkZ(key)) == null) {
                    if (++waited > CHUNK_WAIT_TICKS) aborted = "chunks_not_loaded";
                    return aborted != null;
                }
            }
            chunksReady = true;
        }
        var origin = record.origin();
        long deadline = System.nanoTime() + budgetNanos;
        do {
            cursor.next(BATCH, (x, y, z, fill) -> level.setBlock(cell.set(origin[0] + x, origin[1] + y, origin[2] + z), states[fill], FLAGS));
        } while (!cursor.done() && System.nanoTime() < deadline);
        int percent = ArenaLogic.percent(cursor.placed(), cursor.total());
        if (!cursor.done() && percent != lastPercent && ++sinceProgress >= 20) {
            lastPercent = percent;
            sinceProgress = 0;
            emit.emit("arena_build_progress", progress());
        }
        return cursor.done();
    }

    int ticks() { return ticks; }
}
