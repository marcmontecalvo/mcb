package com.vulpine.mcb.body;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Compiled planner core, stage A: land moves, costs, unknown handling, partial routes. */
public final class RoutePlannerTest {
    private record Key(int x, int y, int z) { }
    private static int checks;
    private static void check(boolean yes, String label) { checks++; if (!yes) throw new AssertionError(label); }
    private static Map<String, Object> stone() {
        return Map.of("block", "minecraft:stone", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
    }
    private static Map<String, Object> water() {
        return Map.of("block", "minecraft:water", "collision_boxes", List.of(), "fluid", "minecraft:water",
                "properties", Map.of("level", "0"));
    }
    private static Map<String, Object> air() {
        return Map.of("block", "minecraft:air", "collision_boxes", List.of(), "fluid", "minecraft:empty");
    }
    private static Map<Key, Map<String, Object>> floor(int size) {
        var cells = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x <= size; x++) for (int z = -2; z <= size; z++) for (int y = -1; y <= 6; y++)
            cells.put(new Key(x, y, z), y == 0 ? stone() : air());
        return cells;
    }
    private static RoutePlanner.Result plan(Map<Key, Map<String, Object>> cells, RoutePlanner.Node goal, int limit) {
        return new RoutePlanner((x, y, z) -> cells.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), goal, 0, 3, true).search(limit);
    }

    public static void main(String[] args) {
        var open = plan(floor(12), new RoutePlanner.Node(10, 1, 0), 100_000);
        check(open.status().equals("reached") && open.route().size() == 11, "straight walk reaches the goal");
        check(Math.abs(open.cost() - 10 * RoutePlanner.SPRINT) < 1e-9, "flat edges cost a sprint block each");
        var diagonal = plan(floor(12), new RoutePlanner.Node(5, 1, 5), 100_000);
        check(diagonal.route().size() == 6, "open diagonal is taken");
        // Wall with a gap: the route must use the gap.
        var walled = floor(12);
        for (int z = -2; z <= 12; z++) if (z != 6) { walled.put(new Key(5, 1, z), stone()); walled.put(new Key(5, 2, z), stone()); walled.put(new Key(5, 3, z), stone()); }
        var gap = plan(walled, new RoutePlanner.Node(10, 1, 0), 100_000);
        check(gap.status().equals("reached") && gap.route().stream().anyMatch(n -> n.x() == 5 && n.z() == 6), "detours through the wall gap");
        // One-block step up costs an ascend; unknown cells are never support.
        var step = floor(12); step.put(new Key(3, 1, 0), stone());
        for (int z = -2; z <= 12; z++) if (z != 0) { step.put(new Key(3, 1, z), stone()); step.put(new Key(3, 2, z), stone()); step.put(new Key(3, 3, z), stone()); }
        var up = plan(step, new RoutePlanner.Node(3, 2, 0), 100_000);
        check(up.status().equals("reached"), "climbs a one-block step");
        var unknown = floor(12); for (int y = -1; y <= 6; y++) unknown.remove(new Key(1, y, 0));
        for (int z = -2; z <= 12; z++) for (int y = -1; y <= 6; y++) unknown.remove(new Key(1, y, z));
        var blocked = plan(unknown, new RoutePlanner.Node(4, 1, 0), 100_000);
        check(!blocked.status().equals("reached"), "an unknown column is never walked through");
        // A capped search returns a partial route that heads toward the goal.
        var capped = plan(floor(40), new RoutePlanner.Node(38, 1, 0), 30);
        check(capped.status().equals("incomplete") && capped.route().get(capped.route().size() - 1).x() > 5, "capped search returns a partial toward the goal");
        // Slab: half-block standing height.
        var slab = floor(12);
        slab.put(new Key(2, 1, 0), Map.of("block", "minecraft:stone_slab", "collision_boxes", List.of(List.of(0, 0, 0, 1, .5, 1)), "fluid", "minecraft:empty"));
        check(new RoutePlanner((x, y, z) -> slab.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(2, 1.5, 0), 0, 3, true)
                .search(10_000).status().equals("reached"), "stands on a bottom slab");
        // Swimming: a 3-wide moat of still water over a stone bed, walls beside it.
        var moat = floor(12);
        for (int x = 3; x <= 5; x++) for (int z = -2; z <= 12; z++) {
            moat.put(new Key(x, 0, z), water()); moat.put(new Key(x, -1, z), stone());
        }
        RoutePlanner.Node far = new RoutePlanner.Node(8, 1, 0);
        check(!plan(moat, far, 100_000).status().equals("reached"), "without swimming the moat blocks the route");
        var swum = new RoutePlanner((x, y, z) -> moat.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), far, 0, 3, true)
                .withMoves(true, true).search(100_000);
        check(swum.status().equals("reached") && swum.route().stream().anyMatch(n -> n.x() == 4), "swims across the moat");
        // Ladder: a 3-high wall at x=3 with a ladder column on its west face at x=2.
        var wall = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = 1; y <= 3; y++) wall.put(new Key(3, y, z), stone());
        for (int y = 1; y <= 4; y++) wall.put(new Key(2, y, 0), Map.of("block", "minecraft:ladder", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(.8125, 0, 0, 1, 1, 1))));
        var climbed = new RoutePlanner((x, y, z) -> wall.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 4, 4), 0, 3, true).withMoves(true, true).search(100_000);
        check(climbed.status().equals("reached") && climbed.route().stream().anyMatch(n -> n.x() == 2 && n.y() == 3),
                "climbs the ladder onto the wall");
        // Gap jump: a 1-wide trench, 2 deep with nothing known under it, across the floor.
        var trench = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = -1; y <= 0; y++) trench.put(new Key(3, y, z), air());
        for (int z = -2; z <= 12; z++) trench.remove(new Key(3, -2, z));
        RoutePlanner.Node across = new RoutePlanner.Node(6, 1, 0);
        check(!plan(trench, across, 100_000).status().equals("reached"), "without gaps the trench blocks the route");
        var jumped = new RoutePlanner((x, y, z) -> trench.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), across, 0, 3, true)
                .withGaps(2).search(100_000);
        check(jumped.status().equals("reached") && jumped.route().stream().noneMatch(n -> n.x() == 3), "jumps the trench");
        System.out.println("RoutePlanner: " + checks + " planner checks passed");
    }
}
