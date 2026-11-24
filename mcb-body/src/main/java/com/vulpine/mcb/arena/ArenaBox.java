package com.vulpine.mcb.arena;

/** An arena's bounds: the inclusive block cells min..max (pure; the one place the arena containment rule lives). */
public record ArenaBox(int minX, int minY, int minZ, int maxX, int maxY, int maxZ) {
    public static ArenaBox of(int[] origin, int[] size) {
        return new ArenaBox(origin[0], origin[1], origin[2],
                origin[0] + size[0] - 1, origin[1] + size[1] - 1, origin[2] + size[2] - 1);
    }

    /** A block cell inside the bounds. */
    public boolean contains(int x, int y, int z) {
        return x >= minX && x <= maxX && y >= minY && y <= maxY && z >= minZ && z <= maxZ;
    }

    /** An entity position (continuous): inside up to the far face of the last cell. */
    public boolean containsPoint(double x, double y, double z) {
        return x >= minX && x < maxX + 1.0 && y >= minY && y < maxY + 1.0 && z >= minZ && z < maxZ + 1.0;
    }

    public ArenaBox inflate(int n) {
        return new ArenaBox(minX - n, minY - n, minZ - n, maxX + n, maxY + n, maxZ + n);
    }

    public boolean intersects(ArenaBox o) {
        return minX <= o.maxX && maxX >= o.minX && minY <= o.maxY && maxY >= o.minY && minZ <= o.maxZ && maxZ >= o.minZ;
    }

    public long volume() {
        return (long) (maxX - minX + 1) * (maxY - minY + 1) * (maxZ - minZ + 1);
    }

    public int[] min() { return new int[] {minX, minY, minZ}; }

    public int[] max() { return new int[] {maxX, maxY, maxZ}; }

    /** The chunks the bounds touch, packed as {@link #chunkKey}. */
    public long[] chunks() {
        int x0 = minX >> 4, x1 = maxX >> 4, z0 = minZ >> 4, z1 = maxZ >> 4;
        var out = new long[(x1 - x0 + 1) * (z1 - z0 + 1)];
        int i = 0;
        for (int cx = x0; cx <= x1; cx++) for (int cz = z0; cz <= z1; cz++) out[i++] = chunkKey(cx, cz);
        return out;
    }

    /** The same packing as ChunkPos.asLong. */
    public static long chunkKey(int cx, int cz) {
        return (cx & 0xFFFFFFFFL) | ((cz & 0xFFFFFFFFL) << 32);
    }

    public static int chunkX(long key) { return (int) key; }

    public static int chunkZ(long key) { return (int) (key >> 32); }
}
