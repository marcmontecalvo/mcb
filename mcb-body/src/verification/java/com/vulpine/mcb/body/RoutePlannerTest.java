package com.vulpine.mcb.body;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

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
        var floorCache = floor(12);
        var cardinal = new RoutePlanner((x, y, z) -> floorCache.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(5, 1, 5), 0, 3, true)
                .withPolicy(false, false, false, false, false, 0).search(100_000);
        check(cardinal.status().equals("reached") && cardinal.route().size() == 11 && cardinal.cost() == 10,
                "legacy unit costs and disabled diagonals");
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
        // House doorway: floor one block up, 2-high opening, roof right above it (y=4).
        var porch = floor(6);
        for (int z = -2; z <= 6; z++) { porch.put(new Key(3, 1, z), stone()); porch.put(new Key(3, 4, z), stone()); }
        for (int z = -2; z <= 6; z++) if (z != 0) { porch.put(new Key(3, 2, z), stone()); porch.put(new Key(3, 3, z), stone()); }
        var door = plan(porch, new RoutePlanner.Node(3, 2, 0), 10_000);
        check(door.status().equals("reached") && door.expanded() < 100, "steps up into a roofed 2-high doorway");
        for (int z = -2; z <= 6; z++) porch.put(new Key(2, 3, z), stone());
        check(!plan(porch, new RoutePlanner.Node(3, 2, 0), 10_000).status().equals("reached"),
                "no jump room above the take-off: no step");
        // Small-house shutter: the goal centre is clear, but its entry face is not.
        var shutter = floor(4);
        shutter.put(new Key(1, 1, 0), stone());
        shutter.put(new Key(1, 2, 0), Map.of("block", "minecraft:oak_trapdoor", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0, 0, 0, .1875, 1, 1))));
        var shutterGoal = new RoutePlanner.Node(1, 2, 0);
        var shutterRoute = plan(shutter, shutterGoal, 10_000);
        check(shutterRoute.status().equals("reached") && shutterRoute.route().size() > 2,
                "open trapdoor blocks direct ascent, requiring an unobstructed approach");
        // Wading onto a slab under a roof at y=4: admitted, and RouteMotorTest checks the motor follows it.
        var roofed = floor(4);
        roofed.put(new Key(0, 1, 0), water());
        roofed.put(new Key(1, 1, 0), Map.of("block", "minecraft:stone_slab", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0, 0, 0, 1, .5, 1))));
        for (int x = -1; x <= 2; x++) for (int z = -1; z <= 1; z++) roofed.put(new Key(x, 4, z), stone());
        var slabWade = plan(roofed, new RoutePlanner.Node(1, 1.5, 0), 10_000);
        check(slabWade.status().equals("reached") && slabWade.route().size() == 2, "wades straight onto a roofed slab");
        // Diagonal past an open trapdoor on the target's near face: the swept body hits the panel.
        var latch = floor(4);
        latch.put(new Key(1, 1, 1), Map.of("block", "minecraft:oak_trapdoor", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0, 0, 0, .1875, 1, 1))));
        var latchRoute = plan(latch, new RoutePlanner.Node(1, 1, 1), 10_000);
        check(latchRoute.status().equals("reached") && latchRoute.route().size() > 2,
                "open trapdoor on a diagonal target face is not cut through");
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
        // Wading: a moat one block deep is walked through on its bed (feet wet, head dry).
        var shallow = floor(12);
        for (int x = 3; x <= 5; x++) for (int z = -2; z <= 12; z++) {
            shallow.put(new Key(x, 0, z), water()); shallow.put(new Key(x, -1, z), stone());
        }
        RoutePlanner.Node far = new RoutePlanner.Node(8, 1, 0);
        var waded = plan(shallow, far, 100_000);
        check(waded.status().equals("reached") && waded.route().stream().anyMatch(n -> n.x() == 4 && n.y() == 0),
                "wades a one-deep moat on its bed");
        // Swimming: a 3-wide moat of still water two deep over a stone bed, walls beside it.
        var moat = floor(12);
        for (int x = 3; x <= 5; x++) for (int z = -2; z <= 12; z++) {
            moat.put(new Key(x, 0, z), water()); moat.put(new Key(x, -1, z), water()); moat.put(new Key(x, -2, z), stone());
        }
        check(!plan(moat, far, 100_000).status().equals("reached"), "without swimming the moat blocks the route");
        var swum = new RoutePlanner((x, y, z) -> moat.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), far, 0, 3, true)
                .withMoves(true, true).search(100_000);
        check(swum.status().equals("reached") && swum.route().stream().anyMatch(n -> n.x() == 4), "swims across the moat");
        // A body already underwater in a flooded 1x1 floodedShaft (no swim flag) plans out over the lip.
        var floodedShaft = floor(12);
        for (int x = -2; x <= 12; x++) for (int z = -2; z <= 12; z++) for (int y = 1; y <= 3; y++)
            floodedShaft.put(new Key(x, y, z), x == 0 && z == 0 ? water() : stone());
        var surfaced = new RoutePlanner((x, y, z) -> floodedShaft.get(new Key(x, y, z)), new RoutePlanner.Node(0, 2, 0),
                new RoutePlanner.Node(4, 4, 0), 0, 3, true).search(100_000);
        check(surfaced.status().equals("reached"), "a body in a flooded floodedShaft plans out: " + surfaced.status());
        // Ladder: a 3-high wall at x=3 with a ladder column on its west face at x=2.
        var wall = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = 1; y <= 3; y++) wall.put(new Key(3, y, z), stone());
        for (int y = 1; y <= 4; y++) wall.put(new Key(2, y, 0), Map.of("block", "minecraft:ladder", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(.8125, 0, 0, 1, 1, 1))));
        var climbed = new RoutePlanner((x, y, z) -> wall.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 4, 4), 0, 3, true).withMoves(true, true).search(100_000);
        check(climbed.status().equals("reached") && climbed.route().stream().anyMatch(n -> n.x() == 2 && n.y() == 3),
                "climbs the ladder onto the wall");
        var noClimb = new RoutePlanner((x, y, z) -> wall.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 4, 4), 0, 3, true).withMoves(true, true).withClimb(false).search(100_000);
        check(!noClimb.status().equals("reached"), "disabled climbing cannot use the ladder");
        var ordinaryClimb = new RoutePlanner((x, y, z) -> wall.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 4, 4), 0, 3, true).withMoves(false, false).search(100_000);
        check(ordinaryClimb.status().equals("reached"), "climbing permission is independent of optional extra moves");
        // A vine over a pond (run 20261117T013535177027Z): let go, drop in and swim out.
        var pond = floor(12);
        for (int x = 0; x <= 5; x++) for (int z = -2; z <= 2; z++) {
            pond.put(new Key(x, 0, z), water()); pond.put(new Key(x, -1, z), water()); pond.put(new Key(x, -2, z), stone());
        }
        var vine = Map.<String, Object>of("block", "minecraft:vine", "fluid", "minecraft:empty", "collision_boxes", List.of());
        pond.put(new Key(2, 1, 0), vine); pond.put(new Key(2, 2, 0), vine); pond.put(new Key(2, 3, 0), stone());
        var offVine = new RoutePlanner((x, y, z) -> pond.get(new Key(x, y, z)), new RoutePlanner.Node(2, 1, 0),
                new RoutePlanner.Node(8, 1, 0), 0, 3, true).withMoves(true, true).search(100_000);
        check(offVine.status().equals("reached"), "lets go of a vine over water and swims out: " + offVine.status());
        var scaffold = floor(4);
        for (int y = 1; y <= 3; y++) scaffold.put(new Key(2, y, 0), Map.of("block", "minecraft:scaffolding",
                "fluid", "minecraft:empty", "collision_boxes", List.of(List.of(0, .875, 0, 1, 1, 1))));
        var scaffoldClimb = new RoutePlanner((x, y, z) -> scaffold.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(2, 4, 0), 0, 3, true).withPolicy(true,true,true,true,true,1).withMoves(false, false).search(100_000);
        check(scaffoldClimb.status().equals("reached"), "normal build policy stands above the top scaffold platform");
        for (double sy : new double[]{1, 2, 2.38, 2.5}) {
            var inside = new RoutePlanner((x, y, z) -> scaffold.get(new Key(x, y, z)), new RoutePlanner.Node(2, sy, 0),
                    new RoutePlanner.Node(3, 1, 2), 0, 3, true).withPolicy(true,true,true,true,true,1).withMoves(false, false).search(100_000);
            check(inside.status().equals("reached"), "a start inside a scaffold column (y=" + sy + ") plans out: " + inside.status());
        }
        // Run 20261117T214048223018Z: the sensor reports scaffolding with its legs and top plate, the shape a
        // body above it meets. A body beside or inside it meets nothing, so a floor cell with scaffolding in its
        // feet is still a stance, and a walk passes through the column.
        var legs = List.of(List.of(0, 0, 0, .125, 1, .125), List.of(.875, 0, .875, 1, 1, 1), List.of(0, .875, 0, 1, 1, 1));
        var through = floor(6);
        for (int y = 1; y <= 2; y++) for (int z = -1; z <= 1; z++)
            through.put(new Key(3, y, z), Map.of("block", "minecraft:scaffolding", "fluid", "minecraft:empty", "collision_boxes", legs));
        var walkIn = new RoutePlanner((x, y, z) -> through.get(new Key(x, y, z)), new RoutePlanner.Node(3, 1, 0),
                new RoutePlanner.Node(5, 1, 0), 0, 3, false).withMoves(false, false).search(100_000);
        check(walkIn.status().equals("reached"), "a start with scaffolding in its feet cell plans out: " + walkIn.status());
        // Run 20261117T221236234752Z: a lily pad (top at 0.094) is a floor like any real shape; a fence is not.
        var padPond = floor(6);
        padPond.put(new Key(2, 0, 0), Map.of("block", "minecraft:water", "fluid", "minecraft:water", "collision_boxes", List.of()));
        padPond.put(new Key(2, 1, 0), Map.of("block", "minecraft:lily_pad", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0, 0, 0, 1, .09375, 1))));
        var offPad = new RoutePlanner((x, y, z) -> padPond.get(new Key(x, y, z)), new RoutePlanner.Node(2, 1.09375, 0),
                new RoutePlanner.Node(5, 1, 0), 0, 3, false).withMoves(false, false).search(100_000);
        check(offPad.status().equals("reached"), "a start on a lily pad plans out: " + offPad.status());
        padPond.put(new Key(4, 1, 0), Map.of("block", "minecraft:oak_fence", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(.375, 0, .375, .625, 1.5, .625))));
        var fenced = new RoutePlanner((x, y, z) -> padPond.get(new Key(x, y, z)), new RoutePlanner.Node(4, 2.5, 0),
                new RoutePlanner.Node(4, 2.5, 0), 0, 3, false);
        check(!fenced.standable(new RoutePlanner.Node(4, 2.5, 0)) && !fenced.standable(new RoutePlanner.Node(4, 2, 0)),
                "a fence top is no floor");
        var interaction = floor(12);
        interaction.put(new Key(4, 4, 0), stone());
        var reachedBlock = new RoutePlanner((x, y, z) -> interaction.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(4, 4, 0), 0, 3, true)
                .withInteraction(4.5).search(100_000);
        check(reachedBlock.status().equals("reached") && reachedBlock.route().get(reachedBlock.route().size() - 1).y() == 1,
                "interaction reaches a raised target from below");
        var avoidedStart = new RoutePlanner.Node(0, 1, 0);
        var alternate = new RoutePlanner((x, y, z) -> interaction.get(new Key(x, y, z)),
                avoidedStart, new RoutePlanner.Node(4, 4, 0), 0, 3, true)
                .withInteraction(4.5).withAvoidStances(Set.of(avoidedStart)).search(100_000);
        check(alternate.status().equals("reached") && !alternate.route().get(alternate.route().size() - 1).equals(avoidedStart),
                "interaction goal excludes a refused stance");
        var blockedRay = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = 1; y <= 6; y++) blockedRay.put(new Key(2, y, z), stone());
        var blockedInteraction = new RoutePlanner((x, y, z) -> blockedRay.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(4, 1, 0), 0, 3, true)
                .withInteraction(4.5).search(100_000);
        check(!blockedInteraction.status().equals("reached"), "interaction does not see through a wall");
        var unsafe = floor(12);
        unsafe.put(new Key(5, 0, 1), Map.of("block", "minecraft:lava", "fluid", "minecraft:lava", "collision_boxes", List.of()));
        var safeRoute = new RoutePlanner((x, y, z) -> unsafe.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(10, 1, 0), 0, 3, true)
                .withSurvival(40, 1, 8, List.of()).search(100_000);
        check(safeRoute.status().equals("reached") && !safeRoute.route().contains(new RoutePlanner.Node(5, 1, 0)),
                "hazard cost steers away from lava");
        var avoidMob = new RoutePlanner((x, y, z) -> unsafe.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(10, 1, 0), 0, 3, true)
                .withSurvival(0, 5, 2, List.of(new double[]{5, 1, 0})).search(100_000);
        check(avoidMob.status().equals("reached") && avoidMob.route().stream().anyMatch(n -> Math.abs(n.z()) >= 2),
                "mob cost steers away from a hostile");
        // Gap jump: a 1-wide trench, 2 deep with nothing known under it, across the floor.
        var trench = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = -1; y <= 0; y++) trench.put(new Key(3, y, z), air());
        for (int z = -2; z <= 12; z++) trench.remove(new Key(3, -2, z));
        RoutePlanner.Node across = new RoutePlanner.Node(6, 1, 0);
        check(!plan(trench, across, 100_000).status().equals("reached"), "without gaps the trench blocks the route");
        var jumped = new RoutePlanner((x, y, z) -> trench.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), across, 0, 3, true)
                .withGaps(2).search(100_000);
        check(jumped.status().equals("reached") && jumped.route().stream().noneMatch(n -> n.x() == 3), "jumps the trench");
        // A drop that hurts is a last resort: from a 6-high ledge with a staircase down, a
        // body allowed 12-block drops still takes the stairs; with no stairs it drops.
        for (boolean stairs : new boolean[] {true, false}) {
            var ledge = new HashMap<Key, Map<String, Object>>();
            for (int x = -3; x <= 9; x++) for (int z = -2; z <= 2; z++) for (int y = -1; y <= 9; y++) {
                boolean tower = (x == 0 || x == 1) && z == 0 && y <= 5;
                boolean stair = stairs && z == 0 && x >= 2 && x <= 5 && y <= 6 - x;
                ledge.put(new Key(x, y, z), y == 0 || tower || stair ? stone() : air());
            }
            var down = new RoutePlanner((x, y, z) -> ledge.get(new Key(x, y, z)), new RoutePlanner.Node(0, 6, 0),
                    new RoutePlanner.Node(-1, 1, 0), 0, 12, true).search(100_000);
            check(down.status().equals("reached"), "gets down from the ledge, stairs " + stairs);
            boolean dropped = false;
            for (int i = 1; i < down.route().size(); i++)
                if (down.route().get(i - 1).y() - down.route().get(i).y() > 3) dropped = true;
            check(dropped != stairs, stairs ? "takes the stairs over a drop that hurts" : "drops when there are no stairs");
        }
        // Edits (stage D): a full 3-high stone wall across the floor. Without edits it blocks.
        var solid = floor(12);
        for (int z = -2; z <= 12; z++) for (int y = 1; y <= 4; y++) solid.put(new Key(5, y, z), stone());
        RoutePlanner.Node beyond = new RoutePlanner.Node(8, 1, 0);
        check(!plan(solid, beyond, 100_000).status().equals("reached"), "without edits the wall blocks the route");
        var dug = withEdits(solid, beyond, true).search(100_000);
        check(dug.status().equals("reached") && dug.steps().size() == 1 && dug.steps().get(0).breaks().size() == 2
                && dug.steps().get(0).kind().equals("step"), "digs a two-block opening through the wall");
        double expected = RoutePlanner.SPRINT + RoutePlanner.EDIT_STEP + 2 * STONE_BREAK;
        var edge = dug.steps().get(0);
        check(Math.abs(dug.cost() - (7 * RoutePlanner.SPRINT + expected)) < 1e-9 && edge.source().x() == 4,
                "the cut is priced as a sprint block, the edit step and two stone breaks");
        // Pit: start 2 deep in a 1x1 hole; pillaring (or a stacked step) gets out.
        var pit = floor(12);
        for (int x = -2; x <= 12; x++) for (int z = -2; z <= 12; z++) { pit.put(new Key(x, 1, z), stone()); pit.put(new Key(x, 2, z), stone()); }
        pit.put(new Key(0, 1, 0), air()); pit.put(new Key(0, 2, 0), air());
        var out = new RoutePlanner((x, y, z) -> pit.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(4, 3, 0), 0, 3, true).withMoves(false, true).withEdits(policy(false)).search(100_000);
        check(out.status().equals("reached") && !out.steps().isEmpty(), "edits its way out of a pit");
        // Bridge: the 2-deep trench with nothing known below, gaps off; one side-anchored block crosses it.
        check(!withEdits(trench, across, false).search(100_000).status().equals("reached"),
                "without side anchoring no support can be placed in the trench");
        var bridged = withEdits(trench, across, true).search(100_000);
        check(bridged.status().equals("reached") && bridged.steps().size() == 1
                && bridged.steps().get(0).places().size() == 1 && bridged.steps().get(0).places().get(0)[1] == 0,
                "bridges the trench with one side-anchored block");
        // Dig down: the goal is under solid ground; MovementDownward breaks the floor.
        var ground = floor(12);
        for (int x = -2; x <= 12; x++) for (int z = -2; z <= 12; z++) ground.put(new Key(x, -1, z), stone());
        var down = new RoutePlanner((x, y, z) -> ground.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(0, 0, 0), 0, 3, true).withMoves(false, true).withEdits(policy(false)).search(100_000);
        check(down.status().equals("reached") && down.steps().get(0).kind().equals("downward"), "digs down onto known stone");
        // Buried goal (run 20261112T033141102600Z): 4,000-node partials 2 blocks at a time
        // for an ore 4-6 below: a node saw only its last edit, so a shaft never got deeper than 2.
        var plain = new HashMap<Key, Map<String, Object>>();
        for (int x = -30; x <= 30; x++) for (int z = -30; z <= 30; z++) for (int y = -10; y <= 6; y++)
            plain.put(new Key(x, y, z), y <= 0 ? stone() : air());
        var deep = new RoutePlanner((x, y, z) -> plain.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(0, -5, 0), 0, 3, true).withPolicy(true, true, true, true, true, 1)
                .withMoves(false, true).withEdits(policy(false)).search(4000);
        check(deep.status().equals("reached"), "digs 6 down to a buried goal within 4,000 nodes: " + deep.status() + " " + deep.expanded());
        // A goal over a pond whose surface cells are protected (run 20261117T015107803188Z): no stance can be
        // stood or placed at goal height, so the answer comes before any search, not after the node limit.
        var pondEdge = floor(12);
        for (int x = 4; x <= 8; x++) for (int z = 4; z <= 8; z++) pondEdge.put(new Key(x, 0, z), water());
        var surface = new java.util.HashSet<Long>();
        for (int x = 4; x <= 8; x++) for (int z = 4; z <= 8; z++) surface.add(RoutePlanner.key(x, 0, z));
        var keepSurface = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), true, true, true,
                3, 64, 64, 3, surface, java.util.Set.of(), "minecraft:cobblestone");
        var overPond = new RoutePlanner((x, y, z) -> pondEdge.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(6, 1, 6), 1.3, 3, true).withMoves(false, true).withEdits(keepSurface).search(10_000);
        check(overPond.status().equals("unreachable") && overPond.expanded() == 0,
                "no stance possible over a protected pond: unreachable at once: " + overPond.status() + " " + overPond.expanded());
        var unguarded = new RoutePlanner((x, y, z) -> pondEdge.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(6, 1, 6), 1.3, 3, true).withMoves(false, true).withEdits(policy(true)).search(100_000);
        check(!unguarded.status().equals("unreachable") || unguarded.expanded() > 0,
                "an unprotected pond may be built over: the search runs: " + unguarded.status());
        var shore = new RoutePlanner((x, y, z) -> pondEdge.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(3, 1, 6), 1.3, 3, true).withMoves(false, true).withEdits(keepSurface).search(10_000);
        check(shore.status().equals("reached"), "a goal disk touching the shore is reached: " + shore.status());
        // A swimmer's stance is the cell over still water (surfaceNode), so with swimming on the same goal over the
        // pond is a real stance (wading-trapped plant_water goal, run 20261119T201738668398Z: unreachable, 0 nodes).
        var swimOver = new RoutePlanner((x, y, z) -> pondEdge.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                new RoutePlanner.Node(6, 1, 6), 0.25, 3, true).withMoves(true, true).withEdits(keepSurface).search(10_000);
        check(swimOver.status().equals("reached"), "a swimmer reaches a goal on the pond surface: "
                + swimOver.status() + " " + swimOver.expanded());
        // Protected cells are never broken.
        var guarded = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), false, false, false,
                1, 64, 64, 3, java.util.Set.of(RoutePlanner.key(5, 1, 0), RoutePlanner.key(5, 2, 0)), java.util.Set.of(),
                "minecraft:cobblestone");
        var around = new RoutePlanner((x, y, z) -> solid.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), beyond, 0, 3, true)
                .withEdits(guarded).search(100_000);
        check(around.status().equals("reached") && around.steps().stream().noneMatch(e -> e.breaks().stream()
                .anyMatch(p -> p[0] == 5 && p[2] == 0)), "protected cells are left alone");
        // Enclosed in stone: a goal 25 blocks down is dug to within the node cap (dig-aware heuristic).
        var rock = new HashMap<Key, Map<String, Object>>();
        for (int x = -12; x <= 12; x++) for (int z = -12; z <= 12; z++) for (int y = 10; y <= 44; y++)
            rock.put(new Key(x, y, z), y <= 40 || y == 44 ? stone() : air());
        rock.put(new Key(0, 16, 0), air()); rock.put(new Key(0, 17, 0), air());
        var shaft = new RoutePlanner((x, y, z) -> rock.get(new Key(x, y, z)), new RoutePlanner.Node(0, 41, 0),
                new RoutePlanner.Node(0, 16, 0), 0, 3, true).withMoves(false, true).withEdits(policy(false)).search(10_000);
        check(shaft.status().equals("reached") && shaft.expanded() < 10_000, "digs 25 blocks down under the node cap");
        check(shaft.expanded() < 200 && shaft.steps().get(shaft.steps().size() - 1).target().y() == 16,
                "the stance field guides the shaft straight down and the last cut drops into the pocket: " + shaft.expanded());

        // Grass-slope descent with oak/acacia leaf canopy and full placement permissions: zero placements!
        var slope = new HashMap<Key, Map<String, Object>>();
        var grassBlock = Map.<String, Object>of("block", "minecraft:grass_block", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        var shortGrass = Map.<String, Object>of("block", "minecraft:short_grass", "collision_boxes", List.of(), "fluid", "minecraft:empty");
        var oakLeaves = Map.<String, Object>of("block", "minecraft:oak_leaves", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        for (int x = -2; x <= 12; x++) {
            int surf = Math.max(1, 10 - Math.max(0, x));
            for (int z = -3; z <= 3; z++) {
                for (int y = 0; y <= 18; y++) {
                    if (y <= surf) slope.put(new Key(x, y, z), y == surf ? grassBlock : stone());
                    else if (y == surf + 1 && x % 2 == 0) slope.put(new Key(x, y, z), shortGrass);
                    else if (y == 16 && x >= 1 && x <= 9) slope.put(new Key(x, y, z), oakLeaves);
                    else slope.put(new Key(x, y, z), air());
                }
            }
        }
        var slopePolicy = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK, "minecraft:grass_block", 8.0),
                Map.of(), true, true, true, 3, 64, 64, 64, Set.of(), Set.of(), "minecraft:cobblestone", List.of("minecraft:cobblestone"));
        var slopeRes = new RoutePlanner((x, y, z) -> slope.get(new Key(x, y, z)), new RoutePlanner.Node(0, 11, 0),
                new RoutePlanner.Node(9, 2, 0), 0.4, 3, true, 2.0)
                .withPolicy(true, true, true, true, true, 4.0)
                .withMoves(false, true).withEdits(slopePolicy).search(10_000);
        check(slopeRes.status().equals("reached") && slopeRes.steps().isEmpty(),
                "grass slope under leaf canopy descends with zero placements or breaks: steps=" + slopeRes.steps().size());

        // Air vs rock: deep unknown cells (>16 below stone ceiling) price as rock; under leaf canopy or open cave air price as air.
        var ceilingRock = new HashMap<Key, Map<String, Object>>();
        for (int x = -4; x <= 4; x++) for (int z = -4; z <= 4; z++) ceilingRock.put(new Key(x, 40, z), stone());
        var underRock = new RoutePlanner((x, y, z) -> ceilingRock.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 15, 0), new RoutePlanner.Node(0, 11, 0), 0, 3, true, 2.0)
                .withMoves(false, true).withEdits(policy(true));
        check(underRock.estimate(new RoutePlanner.Node(0, 15, 0)) > 100,
                "unknown cells 25 below a stone ceiling are priced as rock, not air");

        var canopyOnly = new HashMap<Key, Map<String, Object>>();
        for (int x = -4; x <= 4; x++) for (int z = -4; z <= 4; z++) canopyOnly.put(new Key(x, 40, z), oakLeaves);
        var underLeaves = new RoutePlanner((x, y, z) -> canopyOnly.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 15, 0), new RoutePlanner.Node(0, 11, 0), 0, 3, true, 2.0)
                .withMoves(false, true).withEdits(policy(true));
        check(underLeaves.estimate(new RoutePlanner.Node(0, 15, 0)) < 50,
                "unknown cells under a leaf canopy only are not priced as rock");

        var caveRoof = new HashMap<Key, Map<String, Object>>();
        for (int x = -4; x <= 4; x++) for (int z = -4; z <= 4; z++) {
            caveRoof.put(new Key(x, 40, z), stone());
            caveRoof.put(new Key(x, 20, z), air());
        }
        var underCaveAir = new RoutePlanner((x, y, z) -> caveRoof.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 15, 0), new RoutePlanner.Node(0, 11, 0), 0, 3, true, 2.0)
                .withMoves(false, true).withEdits(policy(true));
        check(underCaveAir.estimate(new RoutePlanner.Node(0, 15, 0)) < 50,
                "unknown cells below known open cave air are not priced as rock");

        // Buried interact_reach target: field does not seed unreachable surface cells with 0; digs straight down and sees via overlay.
        var buriedTarget = new HashMap<Key, Map<String, Object>>();
        for (int x = -12; x <= 12; x++) for (int z = -12; z <= 12; z++) for (int y = -6; y <= 10; y++)
            buriedTarget.put(new Key(x, y, z), y <= 4 ? stone() : air());
        var buriedRes = new RoutePlanner((x, y, z) -> buriedTarget.get(new Key(x, y, z)),
                new RoutePlanner.Node(4, 5, 0), new RoutePlanner.Node(0, 1, 0), 2.0, 3, true, 2.0)
                .withPolicy(true, true, true, true, true, 4.0)
                .withInteraction(4.5).withMoves(false, true).withEdits(policy(true)).search(10_000);
        check(buriedRes.status().equals("reached") && !buriedRes.steps().isEmpty() && buriedRes.expanded() < 200,
                "buried interact_reach target digs down and terminates cleanly: " + buriedRes.status() + " " + buriedRes.expanded());

        // EditPolicy.fromJson: parses materials list (including empty) and place_supply > 3 up to 4096.
        var jsonMulti = com.google.gson.JsonParser.parseString("""
                {"step":true,"break_costs":{"minecraft:stone":16},"fall_costs":{},
                "allow_place":true,"allow_bridge":true,"allow_pillar":true,
                "max_stack":3,"break_budget":64,"place_budget":64,"place_supply":256,
                "protected":[],"protected_blocks":[],
                "materials":["minecraft:cobblestone","minecraft:dirt"],"material":"minecraft:cobblestone"}
                """).getAsJsonObject();
        var parsedMulti = RoutePlanner.EditPolicy.fromJson(jsonMulti);
        check(parsedMulti.placeSupply() == 256 && parsedMulti.materials().equals(List.of("minecraft:cobblestone", "minecraft:dirt")),
                "EditPolicy parses place_supply > 3 and materials list");
        var jsonEmpty = com.google.gson.JsonParser.parseString("""
                {"step":true,"break_costs":{"minecraft:stone":16},"fall_costs":{},
                "allow_place":true,"allow_bridge":true,"allow_pillar":true,
                "max_stack":3,"break_budget":64,"place_budget":64,"place_supply":0,
                "protected":[],"protected_blocks":[],
                "materials":[],"material":"minecraft:cobblestone"}
                """).getAsJsonObject();
        var parsedEmpty = RoutePlanner.EditPolicy.fromJson(jsonEmpty);
        check(parsedEmpty.placeSupply() == 0 && parsedEmpty.materials().isEmpty(),
                "EditPolicy accepts empty materials list");

        // !stance(start) recovery: gravel at feet and head over dry stone breaks out; without edits or on air stays unreachable.
        var gravelTrap = floor(6);
        var gravel = Map.<String, Object>of("block", "minecraft:gravel", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        gravelTrap.put(new Key(0, 1, 0), gravel);
        gravelTrap.put(new Key(0, 2, 0), gravel);
        var gravelGoal = new RoutePlanner.Node(4, 1, 0);
        check(plan(gravelTrap, gravelGoal, 1_000).status().equals("unreachable"),
                "buried start without edits is unreachable");
        var gravelPolicy = new RoutePlanner.EditPolicy(true, Map.of("minecraft:gravel", 10.0, "minecraft:stone", STONE_BREAK),
                Map.of("minecraft:gravel", 10.0), true, true, true, 3, 64, 64, 3, Set.of(), Set.of(), "minecraft:cobblestone");
        var recovered = new RoutePlanner((x, y, z) -> gravelTrap.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 1, 0), gravelGoal, 0, 3, true).withEdits(gravelPolicy).search(10_000);
        check(recovered.status().equals("reached") && !recovered.steps().isEmpty()
                && recovered.steps().get(0).breaks().size() == 2,
                "start buried in gravel recovers by breaking head and feet");
        var airBelow = floor(6);
        airBelow.put(new Key(0, 0, 0), air());
        airBelow.put(new Key(0, 1, 0), gravel);
        check(new RoutePlanner((x, y, z) -> airBelow.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 1, 0), gravelGoal, 0, 3, true).withEdits(gravelPolicy).search(10_000).status().equals("unreachable"),
                "non-standable start without dry floor below remains unreachable");

        // Flowing water (minecraft:flowing_water) one block deep over stone is wadeable with edits and walking.
        var flowWade = floor(6);
        flowWade.put(new Key(0, 1, 0), Map.of("block", "minecraft:water", "collision_boxes", List.of(),
                "fluid", "minecraft:flowing_water", "properties", Map.of("level", "2")));
        var flowRes = new RoutePlanner((x, y, z) -> flowWade.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(4, 1, 0), 0, 3, true)
                .withEdits(policy(true)).search(10_000);
        check(flowRes.status().equals("reached") && flowRes.route().size() == 5,
                "wades out of one-deep flowing water onto dry floor: " + flowRes.status());

        // No edit can create support: a 2-high wall with the goal on top is proven unreachable by the backward
        // flood without burning the node limit; with place/pillar it is still found; a one-block staircase is found.
        var noPlace = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), false, false, false,
                3, 64, 64, 0, java.util.Set.of(), java.util.Set.of(), "minecraft:cobblestone");
        RoutePlanner.Node wallTop = new RoutePlanner.Node(3, 3, 0);
        var wallSearch = new RoutePlanner((x, y, z) -> y <= 0 || (x == 3 && z == 0 && y <= 2) ? stone() : air(),
                new RoutePlanner.Node(0, 1, 0), wallTop, 0, 3, true).withEdits(noPlace);
        var wallResult = wallSearch.search(10_000);
        check(wallResult.status().equals("unreachable") && wallSearch.expanded() < 500,
                "wall top with no place edits is proven unreachable: " + wallResult.status() + " " + wallSearch.expanded());
        var wallPlace = new RoutePlanner((x, y, z) -> y <= 0 || (x == 3 && z == 0 && y <= 2) ? stone() : air(),
                new RoutePlanner.Node(0, 1, 0), wallTop, 0, 3, true).withEdits(policy(false)).search(10_000);
        check(wallPlace.status().equals("reached"), "wall top reachable with place and pillar edits: " + wallPlace.status());
        var stairs = new RoutePlanner((x, y, z) -> y <= 0 || (x >= 1 && x <= 3 && z == 0 && y <= x) ? stone() : air(),
                new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(3, 4, 0), 0, 3, true).withEdits(noPlace).search(10_000);
        check(stairs.status().equals("reached"), "one-block staircase is still found: " + stairs.status());

        // Costly cells (the build's own walls): broken only when no way round exists; protected stays whole.
        for (var mode : new String[]{"closed", "gap", "protected", "slab"}) {
            var room = new HashMap<Key, Map<String, Object>>();
            var prot = new java.util.HashSet<Long>();
            for (int x = -4; x <= 6; x++) for (int z = -4; z <= 4; z++) for (int y = -1; y <= 4; y++) {
                boolean shell = y <= 0 || y >= 3 || Math.abs(x) == 1 && Math.abs(z) <= 1 || Math.abs(z) == 1 && Math.abs(x) <= 1;
                boolean isDoor = x == 1 && z == 0 && y >= 1 && y <= 2;
                boolean isGap = mode.equals("gap") && x == -1 && z == 0 && y >= 1 && y <= 2;
                room.put(new Key(x, y, z), shell && !isGap ? stone() : air());
                if (shell && !isGap && !(isDoor && !mode.equals("protected"))) prot.add(RoutePlanner.key(x, y, z));
            }
            // Standing on a top slab (an eave): still a stance edits may start from
            // (large house run 20261117T075353967859Z: unreachable after one node).
            if (mode.equals("slab")) room.put(new Key(0, 0, 0), Map.of("block", "minecraft:stone_slab",
                    "collision_boxes", List.of(List.of(0, .5, 0, 1, 1, 1)), "fluid", "minecraft:empty"));
            var extra = mode.equals("protected") ? Map.<Long, Double>of()
                    : Map.of(RoutePlanner.key(1, 1, 0), 24.0, RoutePlanner.key(1, 2, 0), 24.0);
            var pol = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), false, false, false,
                    1, 64, 64, 3, prot, java.util.Set.of(), "minecraft:cobblestone", List.of("minecraft:cobblestone"), extra);
            var res = new RoutePlanner((x, y, z) -> room.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                    new RoutePlanner.Node(3, 1, 0), 0, 3, true).withMoves(false, true).withEdits(pol).search(50_000);
            boolean brokeDoor = res.steps().stream().anyMatch(e -> e.breaks().stream().anyMatch(q -> q[0] == 1 && q[2] == 0));
            if (mode.equals("closed")) check(res.status().equals("reached") && brokeDoor, "costly wall is broken when sealed in: " + res.status());
            if (mode.equals("gap")) check(res.status().equals("reached") && !brokeDoor, "walks round rather than break a costly wall: " + res.status());
            if (mode.equals("slab")) check(res.status().equals("reached") && brokeDoor, "breaks out while standing on a top slab: " + res.status());
            if (mode.equals("protected")) check(!res.status().equals("reached") && !brokeDoor, "protected wall stays whole: " + res.status());
        }

        // Large-house run 20261119T105747626310Z: wading in 1-deep water beside a +2 high bank and a 2-deep
        // channel rises to the surface to swim out (without diagonal corner-cutting past solid walls).
        var wadeTrap = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x <= 4; x++) for (int z = -2; z <= 3; z++) for (int y = -1; y <= 6; y++) {
            var c = air();
            if (y <= 0) c = stone();
            else if (y == 1) {
                if (x >= 1 && z >= 1) c = water();
                else if (x == 2 && z == 0) c = water();
                else c = stone();
            } else if (y == 2) {
                if (x <= 1 && z <= 0) c = stone();
                else if (z >= 1 || (x == 2 && z == 0)) c = water();
                else c = air();
            } else if (y == 3 && x <= 1 && z <= 0) {
                c = stone();
            }
            wadeTrap.put(new Key(x, y, z), c);
        }
        // Start at (0,2,2): 1-deep water on stone (y=1 stone, y=2 water); north/west/northwest at z<=0 has +2 bank
        // (stone y=2..3, feet y=4); (1,1..2,2) & (2,1..2,0..2) are 2-deep water; (1,3,0) is wall at corner;
        // (2,1,-1) is stone so (2,2,-1) -> (2,3,-1) -> (1,4,-1) is a walk-up shore.
        wadeTrap.put(new Key(0, 1, 2), stone()); wadeTrap.put(new Key(0, 2, 2), water());
        wadeTrap.put(new Key(0, 1, 1), stone()); wadeTrap.put(new Key(0, 2, 1), water());
        wadeTrap.put(new Key(1, 2, 1), stone()); wadeTrap.put(new Key(1, 3, 1), stone());
        wadeTrap.put(new Key(2, 1, -1), stone()); wadeTrap.put(new Key(2, 2, -1), stone());
        var trapPolicy = new RoutePlanner.EditPolicy(true, Map.of(), Map.of(),
                false, false, false, 3, 64, 0, 0, Set.of(), Set.of("minecraft:stone"), "minecraft:cobblestone");
        var trapGoal = new RoutePlanner.Node(1, 4, -1);
        var trapEscaped = new RoutePlanner((x, y, z) -> wadeTrap.get(new Key(x, y, z)),
                new RoutePlanner.Node(0, 2, 2), trapGoal, 0.4, 3, true, 2.0)
                .withPolicy(true, true, true, true, true, 4.0)
                .withMoves(true, true).withEdits(trapPolicy).search(10_000);
        check(trapEscaped.status().equals("reached"),
                "wading stance rises to water surface and escapes past +2 bank: " + trapEscaped.status());
        check(trapEscaped.route().contains(new RoutePlanner.Node(0, 3, 2))
                        && trapEscaped.route().stream().noneMatch(n -> n.x() == 2 && n.z() == 1 && n.y() == 2),
                "wading escape surfaces at start instead of swimming submerged: " + trapEscaped.route());
        boolean cutsSolidCorner = false;
        for (int i = 1; i < trapEscaped.route().size(); i++) {
            var a = trapEscaped.route().get(i - 1); var b = trapEscaped.route().get(i);
            if (a.x() == 1 && a.z() == 2 && b.x() == 2 && b.z() == 1) cutsSolidCorner = true;
        }
        check(!cutsSolidCorner, "swim route never takes diagonal clipping solid corner (1,3,1): " + trapEscaped.route());

        scaffoldChecks();
        courseChecks();
        System.out.println("RoutePlanner: " + checks + " planner checks passed");
    }

    // Scaffolding as the planner's temporary block (docs/SCAFFOLDING.md): per placed cell, where it stays up.
    private static Map<String, Object> scaffold() {  // as the sensor reports it: no collision boxes
        return Map.of("block", "minecraft:scaffolding", "collision_boxes", List.of(), "fluid", "minecraft:empty");
    }
    private static RoutePlanner.EditPolicy scaffoldPolicy(int scaffoldSupply) {
        return new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), true, true, true,
                3, 64, 64, 64, Set.of(), Set.of(), "minecraft:dirt", List.of("minecraft:dirt"), Map.of(), scaffoldSupply);
    }
    private static RoutePlanner.Result scaffoldRun(Map<Key, Map<String, Object>> cells, RoutePlanner.Node start,
            RoutePlanner.Node goal, int scaffoldSupply) {
        return new RoutePlanner((x, y, z) -> cells.get(new Key(x, y, z)), start, goal, 0, 3, true)
                .withMoves(false, true).withEdits(scaffoldPolicy(scaffoldSupply)).search(100_000);
    }
    private static boolean isScaffold(RoutePlanner.Edit e, int i) {
        return e.scaffold().stream().anyMatch(s -> s[0] == e.places().get(i)[0] && s[1] == e.places().get(i)[1]
                && s[2] == e.places().get(i)[2]);
    }
    private static void scaffoldChecks() {
        // Pit: the pillar is scaffolding all the way up; a block on scaffolding is stacked with a sneaking click
        // (large house run 20261117T233128776004Z: one scaffolding block, then dirt on it). With none held, solid.
        var pit = floor(12);
        var held = scaffoldRun(pit, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(0, 3, 0), 64);
        var none = scaffoldRun(pit, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(0, 3, 0), 0);
        check(held.status().equals("reached") && none.status().equals("reached"), "pit is left with and without scaffolding");
        var first = held.steps().get(0);
        check(!first.places().isEmpty() && isScaffold(first, 0), "the first pillar/step-up block on solid ground is scaffolding");
        check(held.steps().stream().allMatch(e -> e.scaffold().size() == e.places().size()),
                "every pillar block is scaffolding, the ones on scaffolding too");
        check(none.steps().stream().allMatch(e -> e.scaffold().isEmpty()), "with no scaffolding held everything is solid");

        // Straight up from a wide flat floor: height above every floor is only had by placing, so the search pillars
        // at once instead of flooding the floor (scaffold course plan-5: 8000 nodes for 5 blocks up).
        var plain = floor(60);
        var up = new RoutePlanner((x, y, z) -> plain.get(new Key(x, y, z)), new RoutePlanner.Node(30, 1, 30),
                new RoutePlanner.Node(30, 4, 30), 0, 3, true).withPolicy(true, true, true, true, true, 1)
                .withMoves(false, true).withEdits(scaffoldPolicy(64)).search(100_000);
        check(up.status().equals("reached") && up.expanded() < 100,
                "a goal straight up from a wide floor pillars without flooding the floor (" + up.status() + " " + up.expanded() + ")");

        // Across open air at height: each column on the way needs a placed block, so the search bridges at once
        // instead of flooding the floor below (scaffold course plan-4: 5600 nodes for a 4-block bridge).
        var tall = floor(60);
        for (int y = 1; y <= 3; y++) tall.put(new Key(30, y, 30), stone());
        var across = new RoutePlanner((x, y, z) -> tall.get(new Key(x, y, z)), new RoutePlanner.Node(30, 4, 30),
                new RoutePlanner.Node(34, 4, 30), 0, 3, true).withPolicy(true, true, true, true, true, 1)
                .withMoves(false, true).withEdits(scaffoldPolicy(64)).search(100_000);
        check(across.status().equals("reached") && across.expanded() < 200,
                "a goal across open air at height bridges without flooding the floor (" + across.status() + " " + across.expanded() + ")");

        // In a hanging scaffolding cell (its bottom plate) beside a wall: the body places a block against the wall and
        // steps onto it. With only climb moves from the cell it searched 10000 nodes for a goal 1 block off (large
        // house run 20261118T080749937951Z, plan-4815).
        var hang = floor(12);
        var hanging = new HashMap<String, Object>(scaffold());
        hanging.put("properties", Map.of("bottom", "true", "distance", "4"));
        hang.put(new Key(2, 5, 2), hanging);
        hang.put(new Key(3, 4, 1), stone());
        var off = new RoutePlanner((x, y, z) -> hang.get(new Key(x, y, z)), new RoutePlanner.Node(2, 5, 2),
                new RoutePlanner.Node(3, 5, 2), 0, 3, true).withMoves(false, true).withEdits(scaffoldPolicy(0)).search(10_000);
        check(off.status().equals("reached") && off.expanded() < 50,
                "from a hanging scaffolding cell a block goes against the wall beside it (" + off.status() + " " + off.expanded() + ")");

        // A hanging cell's bottom plate holds the body, sneaking too (ScaffoldingBlock.getCollisionShape): there is
        // no letting go of it. Run 20261119T225430670386Z was planned a drop from one and stood on the plate.
        var plate = floor(12);
        plate.put(new Key(2, 3, 2), hanging);
        var drop = new RoutePlanner((x, y, z) -> plate.get(new Key(x, y, z)), new RoutePlanner.Node(2, 3, 2),
                new RoutePlanner.Node(2, 1, 2), 0, 3, true).withMoves(false, false).search(10_000);
        var route = drop.route();
        boolean through = false;
        for (int i = 1; i < route.size(); i++)
            through |= route.get(i - 1).equals(new RoutePlanner.Node(2, 3, 2)) && route.get(i).x() == 2
                    && route.get(i).z() == 2 && route.get(i).y() < 3;
        // Up onto its top plate and off the side is a real way down.
        check(drop.status().equals("reached") && !through,
                "no drop through a hanging scaffolding cell's bottom plate: " + drop.status() + " " + route);

        // Bridge off a scaffolding tower: the walkway is scaffolding while its distance is at most 6, solid beyond.
        var world = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x <= 14; x++) for (int z = -3; z <= 3; z++) for (int y = -1; y <= 6; y++)
            world.put(new Key(x, y, z), x <= 0 && y <= 0 ? stone() : air());
        world.put(new Key(1, -1, 0), stone());
        world.put(new Key(1, 0, 0), scaffold());
        var near = scaffoldRun(world, new RoutePlanner.Node(1, 1, 0), new RoutePlanner.Node(7, 1, 0), 64);
        check(near.status().equals("reached") && near.steps().stream().allMatch(e -> e.scaffold().size() == e.places().size()),
                "a walkway 6 out from the tower is all scaffolding: " + near.status());
        var far = scaffoldRun(world, new RoutePlanner.Node(1, 1, 0), new RoutePlanner.Node(8, 1, 0), 64);
        var lastEdit = far.steps().isEmpty() ? null : far.steps().get(far.steps().size() - 1);
        check(far.status().equals("reached") && lastEdit != null && lastEdit.scaffold().isEmpty() && !lastEdit.places().isEmpty()
                && far.steps().get(0).scaffold().size() == 1,
                "7 out from the tower is solid, nearer cells scaffolding: status=" + far.status() + " expanded=" + far.expanded()
                        + " steps=" + far.steps().size() + " first=" + (far.steps().isEmpty() ? "none" : far.steps().get(0))
                        + " last=" + lastEdit);
        // A bridge beside a scaffolding pillar on solid ground.
        var beside = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x <= 4; x++) for (int z = -3; z <= 3; z++) for (int y = -1; y <= 6; y++)
            beside.put(new Key(x, y, z), x <= 0 && y <= 0 ? stone() : air());
        beside.put(new Key(1, -1, 1), stone());
        beside.put(new Key(1, 0, 1), scaffold());
        var next = scaffoldRun(beside, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(1, 1, 0), 64);
        check(next.status().equals("reached") && next.steps().get(0).scaffold().size() == 1,
                "a bridge block beside a scaffolding pillar is scaffolding: " + next.status());
        var bare = new HashMap<>(beside);
        bare.remove(new Key(1, 0, 1)); bare.put(new Key(1, 0, 1), air()); bare.put(new Key(1, -1, 1), air());
        var bedrock = Map.<String, Object>of("block", "minecraft:bedrock", "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        for (int x = -2; x <= 4; x++) for (int z = -3; z <= 3; z++) bare.put(new Key(x, 3, z), bedrock);
        var alone = scaffoldRun(bare, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(1, 1, 0), 64);
        check(alone.status().equals("reached") && alone.steps().get(0).scaffold().isEmpty(),
                "a bridge block with nothing to hang on stays solid");
        var json = com.google.gson.JsonParser.parseString("""
                {"step":true,"break_costs":{},"fall_costs":{},"allow_place":true,"allow_bridge":true,"allow_pillar":true,
                "max_stack":3,"break_budget":64,"place_budget":64,"place_supply":9,"scaffold_supply":5,
                "protected":[],"protected_blocks":[],"materials":["minecraft:dirt"]}
                """).getAsJsonObject();
        check(RoutePlanner.EditPolicy.fromJson(json).scaffoldSupply() == 5, "EditPolicy parses scaffold_supply");

        // No floating dirt floor off an upper wall: when dirt and scaffolding are both held, reaching across open
        // air from a 3-high wall top drops/walks and uses scaffolding rather than bridging a floating dirt floor.
        var upperWall = floor(12);
        for (int y = 1; y <= 3; y++) upperWall.put(new Key(0, y, 0), stone());
        var noDirtFloor = scaffoldRun(upperWall, new RoutePlanner.Node(0, 4, 0), new RoutePlanner.Node(3, 4, 0), 64);
        check(noDirtFloor.status().equals("reached")
                && noDirtFloor.steps().stream().allMatch(e -> e.scaffold().size() == e.places().size()),
                "wall top with ground below uses scaffolding, not a floating dirt floor: " + noDirtFloor.status());

        // No [dirt, scaffolding] stack off a wall beside a ground-backed gap: pure scaffolding tower from ground.
        var gapWall = floor(12);
        for (int y = 1; y <= 4; y++) gapWall.put(new Key(2, y, 0), stone());
        gapWall.put(new Key(1, 0, 0), air());
        var noDirtStack = scaffoldRun(gapWall, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(2, 5, 0), 64);
        check(noDirtStack.status().equals("reached")
                && noDirtStack.steps().stream().allMatch(e -> e.scaffold().size() == e.places().size()),
                "wall top beside air gap builds pure scaffolding, not dirt+scaffolding: " + noDirtStack.status());

        // PolicySensor properties.distance is read from existing scaffolding: distance 4 extends 5 & 6 as scaffolding,
        // and falls back to solid dirt at distance 7.
        var propWorld = new HashMap<Key, Map<String, Object>>();
        for (int x = -2; x <= 6; x++) for (int z = -2; z <= 2; z++) for (int y = -1; y <= 4; y++)
            propWorld.put(new Key(x, y, z), x <= 0 && y <= 0 ? stone() : air());
        var d4 = new HashMap<String, Object>(scaffold());
        d4.put("properties", Map.of("distance", "4", "bottom", "true"));
        propWorld.put(new Key(1, 0, 0), d4);
        var propExt = scaffoldRun(propWorld, new RoutePlanner.Node(1, 1, 0), new RoutePlanner.Node(4, 1, 0), 64);
        check(propExt.status().equals("reached") && propExt.steps().size() == 3
                && propExt.steps().get(0).scaffold().size() == 1
                && propExt.steps().get(1).scaffold().size() == 1
                && propExt.steps().get(2).scaffold().isEmpty(),
                "properties.distance=4 allows scaffolding at 5 & 6 and falls back to dirt at 7: " + propExt.status());

        // No unexecutable non-pillar step-up from a scaffolding floor onto scaffolding-over-scaffolding
        // (native_scaffold_redirect).
        var onScaff = floor(8);
        onScaff.put(new Key(1, 1, 0), scaffold());
        onScaff.put(new Key(2, 1, 0), scaffold());
        var noRedirect = scaffoldRun(onScaff, new RoutePlanner.Node(1, 2, 0), new RoutePlanner.Node(2, 3, 0), 64);
        check(noRedirect.status().equals("reached")
                && noRedirect.steps().stream().allMatch(e -> e.scaffold().size() == e.places().size())
                && noRedirect.steps().stream().noneMatch(e -> !e.kind().equals("pillar")
                        && e.target().y() > e.source().y() && "minecraft:scaffolding".equals(
                                onScaff.get(new Key(e.source().x(), (int) e.source().y() - 1, e.source().z())).get("block"))),
                "standing on scaffolding never emits a non-pillar step-up onto scaffolding-over-scaffolding: " + noRedirect.status());
    }

    // The live scaffolding course (scripts/check-companion-scaffold-course.py): a hole in flat ground, the air of it
    // protected, scaffolding the only thing held. Ground top y=0, so a stance is y=1; the hole is x 3..hi.
    private static Map<Key, Map<String, Object>> hole(int hi, boolean ravine, int[] towers, int[] bridgeSpan) {
        var w = new HashMap<Key, Map<String, Object>>();
        for (int x = -4; x <= hi + 5; x++) for (int z = -6; z <= 6; z++) for (int y = -5; y <= 12; y++) {
            boolean in = x >= 3 && x <= hi && (ravine || Math.abs(z) <= 1) && y >= -3 && y <= 0;
            w.put(new Key(x, y, z), y <= 0 && !in ? stone() : air());
        }
        for (int tx : towers) for (int y = 1; y <= 5; y++) w.put(new Key(tx, y, 0), scaffold());
        for (int x = bridgeSpan[0]; x <= bridgeSpan[1]; x++) w.put(new Key(x, 5, 0), scaffold());
        return w;
    }
    private static Set<Long> holeAir(Map<Key, Map<String, Object>> w) {
        var out = new java.util.HashSet<Long>();
        for (var e : w.entrySet()) if (e.getKey().y() <= 0 && e.getKey().y() >= -3 && "minecraft:air".equals(e.getValue().get("block")))
            out.add(RoutePlanner.key(e.getKey().x(), e.getKey().y(), e.getKey().z()));
        return out;
    }
    private static RoutePlanner.Result courseRun(Map<Key, Map<String, Object>> w, RoutePlanner.Node start, RoutePlanner.Node goal) {
        var pol = new RoutePlanner.EditPolicy(true, Map.of(), Map.of(), true, true, true, 3, 64, 64, 64, holeAir(w), Set.of(),
                "minecraft:cobblestone", List.of("minecraft:cobblestone"), Map.of(), 64);
        return new RoutePlanner((x, y, z) -> w.get(new Key(x, y, z)), start, goal, 0, 3, true)
                .withMoves(false, true).withEdits(pol).search(200_000);
    }
    private static String allScaffold(RoutePlanner.Result r) {
        for (var e : r.steps()) {
            if (e.scaffold().size() != e.places().size()) return " (a placement is solid: " + e.kind() + ")";
            for (var s : e.scaffold()) if (s[3] > 6) return " (distance " + s[3] + ")";
        }
        return "";
    }
    private static boolean placesAll(RoutePlanner.Result r, java.util.function.Predicate<int[]> where) {
        return r.steps().stream().flatMap(e -> e.places().stream()).allMatch(where);
    }
    private static boolean staysUp(RoutePlanner.Result r, double minY) {
        return r.route().stream().allMatch(n -> n.y() >= minY - 1e-9);
    }
    private static void courseChecks() {
        var none = new int[0];
        var noSpan = new int[]{1, 0};
        // 1. Up: a tower of scaffolding, every block, from the ground to 5 above it.
        var ground = hole(13, false, none, noSpan);
        var up = courseRun(ground, new RoutePlanner.Node(0, 1, 0), new RoutePlanner.Node(2, 6, 0));
        check(up.status().equals("reached") && !up.steps().isEmpty(), "course 1: tower up reached: " + up.status());
        check(allScaffold(up).isEmpty(), "course 1: every tower block is scaffolding" + allScaffold(up));
        check(placesAll(up, p -> p[1] >= 1 && p[1] <= 5), "course 1: nothing placed above the stance floor");
        // 2. Out: from the tower top, a bridge over the hole at the same height.
        var tower = hole(13, false, new int[]{2}, noSpan);
        var out = courseRun(tower, new RoutePlanner.Node(2, 6, 0), new RoutePlanner.Node(5, 6, 0));
        check(out.status().equals("reached") && allScaffold(out).isEmpty(), "course 2: bridge out: " + out.status() + allScaffold(out));
        check(placesAll(out, p -> p[1] == 5 && p[0] >= 3 && p[0] <= 5), "course 2: bridge cells at floor height over the hole");
        // 3. Up again: scaffolding on top of the bridge (distance of the cell below, not 7).
        var bridge = hole(13, false, new int[]{2}, new int[]{3, 5});
        var again = courseRun(bridge, new RoutePlanner.Node(5, 6, 0), new RoutePlanner.Node(5, 8, 0));
        check(again.status().equals("reached") && allScaffold(again).isEmpty(), "course 3: up on the bridge: " + again.status() + allScaffold(again));
        check(placesAll(again, p -> p[0] == 5 && p[1] >= 6 && p[1] <= 7), "course 3: a column on the bridge end");
        // 4. Down a column (sneak), then tower B on the east rim.
        var down = courseRun(tower, new RoutePlanner.Node(2, 6, 0), new RoutePlanner.Node(0, 1, 0));
check(down.status().equals("reached") && down.steps().isEmpty()
                && down.route().stream().anyMatch(n -> n.x() == 2 && n.y() == 5), "course 4: down the tower, sinking into the column, no edits: " + down.status());
        var b = courseRun(ground, new RoutePlanner.Node(16, 1, 0), new RoutePlanner.Node(14, 6, 0));
        check(b.status().equals("reached") && allScaffold(b).isEmpty(), "course 4: tower B: " + b.status() + allScaffold(b));
        // 5. Bridge across: A's bridge reaches x 8 (6 out); the rest hangs off B (x 9..13 is 5..1 out).
        var span = hole(13, true, new int[]{2, 14}, new int[]{3, 8});
        var across = courseRun(span, new RoutePlanner.Node(14, 6, 0), new RoutePlanner.Node(2, 6, 0));
        check(across.status().equals("reached") && allScaffold(across).isEmpty(), "course 5: bridge across: " + across.status() + allScaffold(across));
        check(!across.steps().isEmpty() && placesAll(across, p -> p[1] == 5 && p[0] >= 9 && p[0] <= 13), "course 5: the gap cells are placed");
        check(staysUp(across, 6), "course 5: the route stays on the bridge");
        // 6. Walk the finished bridge: no edits, never down.
        var full = hole(13, false, new int[]{2, 14}, new int[]{3, 13});
        var walk = courseRun(full, new RoutePlanner.Node(14, 6, 0), new RoutePlanner.Node(2, 6, 0));
        check(walk.status().equals("reached") && walk.steps().isEmpty() && staysUp(walk, 6), "course 6: walks the bridge: " + walk.status());
        // Reach: a hole 13 wide leaves one cell 7 from both towers: it is solid, never scaffolding.
        var wide = hole(15, true, new int[]{2, 16}, new int[]{3, 8});
        for (int x = 10; x <= 15; x++) wide.put(new Key(x, 5, 0), scaffold());
        var seven = courseRun(wide, new RoutePlanner.Node(16, 6, 0), new RoutePlanner.Node(2, 6, 0));
        var sevenCells = seven.steps().stream().flatMap(e -> e.places().stream()).toList();
        check(seven.status().equals("reached") && sevenCells.size() == 1 && sevenCells.get(0)[0] == 9
                && seven.steps().get(0).scaffold().isEmpty(), "course: the cell 7 from both towers is solid: " + seven.status());
        var six = hole(14, true, new int[]{2, 15}, new int[]{3, 8});
        for (int x = 10; x <= 14; x++) six.put(new Key(x, 5, 0), scaffold());
        var join = courseRun(six, new RoutePlanner.Node(15, 6, 0), new RoutePlanner.Node(2, 6, 0));
        check(join.status().equals("reached") && allScaffold(join).isEmpty() && placesAll(join, p -> p[0] == 9),
                "course: the cell 6 from one tower is scaffolding: " + join.status());
    }

    // Break cost of stone: 8 ticks with an iron pickaxe + 2 extra + 6 aim.
    private static final double STONE_BREAK = 16;
    private static RoutePlanner.EditPolicy policy(boolean bridge) {
        return new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), true, bridge, true,
                3, 64, 64, 3, java.util.Set.of(), java.util.Set.of(), "minecraft:cobblestone");
    }
    private static RoutePlanner withEdits(Map<Key, Map<String, Object>> cells, RoutePlanner.Node goal, boolean bridge) {
        return new RoutePlanner((x, y, z) -> cells.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0), goal, 0, 3, true)
                .withEdits(policy(bridge));
    }
}
