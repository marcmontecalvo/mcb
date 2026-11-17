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
        for (var mode : new String[]{"closed", "gap", "protected"}) {
            var room = new HashMap<Key, Map<String, Object>>();
            var prot = new java.util.HashSet<Long>();
            for (int x = -4; x <= 6; x++) for (int z = -4; z <= 4; z++) for (int y = -1; y <= 4; y++) {
                boolean shell = y <= 0 || y >= 3 || Math.abs(x) == 1 && Math.abs(z) <= 1 || Math.abs(z) == 1 && Math.abs(x) <= 1;
                boolean isDoor = x == 1 && z == 0 && y >= 1 && y <= 2;
                boolean isGap = mode.equals("gap") && x == -1 && z == 0 && y >= 1 && y <= 2;
                room.put(new Key(x, y, z), shell && !isGap ? stone() : air());
                if (shell && !isGap && !(isDoor && !mode.equals("protected"))) prot.add(RoutePlanner.key(x, y, z));
            }
            var extra = mode.equals("protected") ? Map.<Long, Double>of()
                    : Map.of(RoutePlanner.key(1, 1, 0), 24.0, RoutePlanner.key(1, 2, 0), 24.0);
            var pol = new RoutePlanner.EditPolicy(true, Map.of("minecraft:stone", STONE_BREAK), Map.of(), false, false, false,
                    1, 64, 64, 3, prot, java.util.Set.of(), "minecraft:cobblestone", List.of("minecraft:cobblestone"), extra);
            var res = new RoutePlanner((x, y, z) -> room.get(new Key(x, y, z)), new RoutePlanner.Node(0, 1, 0),
                    new RoutePlanner.Node(3, 1, 0), 0, 3, true).withMoves(false, true).withEdits(pol).search(50_000);
            boolean brokeDoor = res.steps().stream().anyMatch(e -> e.breaks().stream().anyMatch(q -> q[0] == 1 && q[2] == 0));
            if (mode.equals("closed")) check(res.status().equals("reached") && brokeDoor, "costly wall is broken when sealed in: " + res.status());
            if (mode.equals("gap")) check(res.status().equals("reached") && !brokeDoor, "walks round rather than break a costly wall: " + res.status());
            if (mode.equals("protected")) check(!res.status().equals("reached") && !brokeDoor, "protected wall stays whole: " + res.status());
        }

        System.out.println("RoutePlanner: " + checks + " planner checks passed");
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
