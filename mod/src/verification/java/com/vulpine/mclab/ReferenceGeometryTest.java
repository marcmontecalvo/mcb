package com.vulpine.mclab;

import java.util.HashSet;
import java.util.Set;

/** Standalone geometry checks executed by Gradle check without a test framework. */
public final class ReferenceGeometryTest {
    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static String block(ReferenceGeometry.Layout layout, int dx, int dy, int dz) {
        return layout.cells().stream()
                .filter(c -> c.x() == layout.x()+dx && c.y() == layout.y()+dy && c.z() == layout.z()+dz)
                .findFirst().orElseThrow().block();
    }

    public static void main(String[] args) {
        for (String mechanic : Set.of("collision", "step", "ledge", "fall")) {
            var layout = ReferenceGeometry.plan(mechanic, 990, 80, 988);
            require(layout.cells().size() == 847, "bounded volume");
            var coordinates = new HashSet<String>();
            for (var cell : layout.cells()) {
                require(coordinates.add(cell.x()+","+cell.y()+","+cell.z()), "unique cells");
                require(cell.x() >= 987 && cell.x() <= 993, "x bounds");
                require(cell.y() >= 75 && cell.y() <= 85, "y bounds");
                require(cell.z() >= 986 && cell.z() <= 996, "z bounds");
                if (cell.y() == 75) require(cell.block().equals("stone"), "catch floor");
            }
            require(layout.spawn().x() == 990.5 && layout.spawn().z() == 988.5, "spawn alignment");
            require(layout.spawn().y() == (mechanic.equals("fall") ? 85 : 80), "spawn height");
            require(block(layout, 0, -1, 0).equals("stone"), "spawn support");
        }
        var collision = ReferenceGeometry.plan("collision", 0, 0, 0);
        require(block(collision, 0, 0, 2).equals("stone"), "collision foot block");
        require(block(collision, 0, 1, 2).equals("stone"), "collision head block");
        require(block(collision, 0, 2, 2).equals("air"), "collision height");
        var step = ReferenceGeometry.plan("step", 0, 0, 0);
        require(block(step, 0, 0, 2).equals("stone_slab"), "half step");
        var ledge = ReferenceGeometry.plan("ledge", 0, 0, 0);
        require(block(ledge, 0, -1, 1).equals("stone"), "ledge edge");
        require(block(ledge, 0, -1, 2).equals("air"), "ledge gap");
        require(block(ledge, 0, -4, 7).equals("air"), "gap depth");
        require(block(ledge, 0, -5, 7).equals("stone"), "ledge catch floor");
        try {
            ReferenceGeometry.plan("unknown", 0, 0, 0);
            throw new AssertionError("unknown mechanic accepted");
        } catch (IllegalArgumentException expected) {
            require(expected.getMessage().equals("unknown_reference_mechanic"), "error reason");
        }
        System.out.println("Reference geometry: all four layouts, boundaries and catch floors verified");
    }
}
