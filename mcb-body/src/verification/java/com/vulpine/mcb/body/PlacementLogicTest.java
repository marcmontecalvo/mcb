package com.vulpine.mcb.body;

public final class PlacementLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    public static void main(String[] args) {
        var target = PlacementLogic.parse("minecraft:oak_stairs[half=bottom,facing=north,shape=straight]");
        check(target.block().equals("minecraft:oak_stairs") && target.properties().size() == 3, "parse");
        check(PlacementLogic.render(target).equals("minecraft:oak_stairs[facing=north,half=bottom,shape=straight]"), "render sorted");
        check(PlacementLogic.parse("stone").block().equals("minecraft:stone"), "bare id gets namespace");

        var placed = PlacementLogic.parse("minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left,waterlogged=false]");
        check(PlacementLogic.matches(target, placed), "shape and waterlogged ignored");
        check(!PlacementLogic.matches(target, PlacementLogic.parse("minecraft:oak_stairs[facing=east,half=bottom]")), "facing differs");
        check(!PlacementLogic.matches(target, PlacementLogic.parse("minecraft:oak_stairs[facing=north,half=top]")), "half differs");
        check(!PlacementLogic.matches(target, PlacementLogic.parse("minecraft:stone")), "block differs");
        check(PlacementLogic.matches(PlacementLogic.parse("minecraft:oak_log"), PlacementLogic.parse("minecraft:oak_log[axis=x]")),
                "unnamed property is free");
        check(!PlacementLogic.matches(PlacementLogic.parse("minecraft:oak_log[axis=x]"), PlacementLogic.parse("minecraft:oak_log[axis=y]")),
                "axis differs");
        check(PlacementLogic.matches(PlacementLogic.parse("minecraft:oak_door[open=true,half=lower]"),
                PlacementLogic.parse("minecraft:oak_door[open=false,half=lower]")), "open ignored");
        boolean threw = false;
        try { PlacementLogic.parse("minecraft:oak_stairs[facing]"); } catch (IllegalArgumentException e) { threw = true; }
        check(threw, "malformed rejected");

        check(PlacementLogic.yawFor("south") == 0f && PlacementLogic.yawFor("west") == 90f
                && PlacementLogic.yawFor("north") == 180f && PlacementLogic.yawFor("east") == -90f, "yaws");
        var order = PlacementLogic.yawOrder(target);
        check(order.size() == 4 && order.get(0).equals("north") && order.get(1).equals("south"), "yaw order " + order);
        check(PlacementLogic.yawOrder(PlacementLogic.parse("minecraft:stone")).size() == 4, "default yaw order");

        check(PlacementLogic.HIT_OFFSETS.length == 9 && PlacementLogic.HIT_OFFSETS[0][0] == 0, "hit grid");
        for (String face : new String[] {"up", "down", "north", "south", "east", "west"}) {
            double[] centre = PlacementLogic.facePoint(face, 0, 0);
            double[] quarter = PlacementLogic.facePoint(face, 0.25, -0.25);
            int axis = centre[0] != 0 ? 0 : centre[1] != 0 ? 1 : 2;
            check(Math.abs(centre[axis]) == 0.5 && quarter[axis] == centre[axis], "points stay on the " + face + " plane");
        }
        check(Math.abs(PlacementLogic.pitchTo(0, -1, 1) - 45f) < 1e-4, "looking down is positive pitch");
        check(Math.abs(PlacementLogic.pitchTo(0, 0, 3)) < 1e-4, "level pitch");
        System.out.println("PlacementLogicTest ok");
    }
}
