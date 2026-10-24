package com.vulpine.mclab;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Pure, bounded G1 development geometry; no simulation, player or world access. */
public final class ReferenceGeometry {
    private ReferenceGeometry() {}

    public record Cell(int x, int y, int z, String block) {}
    public record Spawn(double x, double y, double z) {}
    public record Layout(String mechanic, int x, int y, int z, Spawn spawn, List<Cell> cells) {}

    public static Layout plan(String mechanic, int x, int y, int z) {
        if (!Set.of("collision", "step", "ledge", "fall").contains(mechanic)) {
            throw new IllegalArgumentException("unknown_reference_mechanic");
        }
        var cells = new ArrayList<Cell>();
        for (int dx = -3; dx <= 3; dx++) for (int dz = -2; dz <= 8; dz++) {
            for (int dy = -5; dy <= 5; dy++) {
                String block = dy < 0 ? "stone" : "air";
                if (mechanic.equals("ledge") && dz >= 2 && dz <= 7 && dy >= -4 && dy < 0) {
                    block = "air";
                }
                if (dx == 0 && dz == 2) {
                    if (mechanic.equals("collision") && (dy == 0 || dy == 1)) block = "stone";
                    if (mechanic.equals("step") && dy == 0) block = "stone_slab";
                }
                cells.add(new Cell(x+dx, y+dy, z+dz, block));
            }
        }
        // The ledge has a floor five blocks below, rather than exposing the void.
        var spawn = new Spawn(x+0.5, y+(mechanic.equals("fall") ? 5 : 0), z+0.5);
        return new Layout(mechanic, x, y, z, spawn, List.copyOf(cells));
    }
}
