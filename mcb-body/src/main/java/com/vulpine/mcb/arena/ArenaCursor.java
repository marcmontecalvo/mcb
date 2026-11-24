package com.vulpine.mcb.arena;

import java.util.List;

/**
 * Walks a plan's fills in order, cell by cell: within a fill a layer at a time from the bottom (x fastest, then z), so a
 * floor exists before what stands on it. {@link #next} hands out at most a batch, so the build never holds the server
 * tick for long (pure; the runtime sets the blocks).
 */
public final class ArenaCursor {
    /** What a batch does with each cell: its position and the index of the fill that owns it. */
    @FunctionalInterface
    public interface CellSink { void cell(int x, int y, int z, int fill); }

    private final List<ArenaPlan.Fill> fills;
    private final long total;
    private long placed;
    private int fill;
    private int x, y, z;

    public ArenaCursor(List<ArenaPlan.Fill> fills) {
        this.fills = fills;
        long n = 0;
        for (var f : fills) n += f.volume();
        total = n;
        enter(0);
    }

    private void enter(int index) {
        fill = index;
        if (index < fills.size()) {
            var min = fills.get(index).min();
            x = min[0]; y = min[1]; z = min[2];
        }
    }

    public long total() { return total; }

    public long placed() { return placed; }

    public boolean done() { return fill >= fills.size(); }

    /** Places the next cells, at most {@code max}; returns how many were handed out. */
    public int next(int max, CellSink sink) {
        int n = 0;
        while (n < max && !done()) {
            var f = fills.get(fill);
            sink.cell(x, y, z, fill);
            n++;
            placed++;
            if (++x > f.max()[0]) {
                x = f.min()[0];
                if (++z > f.max()[2]) {
                    z = f.min()[2];
                    if (++y > f.max()[1]) enter(fill + 1);
                }
            }
        }
        return n;
    }
}
