package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.List;

public final class EscapeLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static void rejects(String json) {
        try { EscapeLogic.parse(JsonParser.parseString(json).getAsJsonObject()); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals("bad_request"), json); return; }
        throw new AssertionError("accepted " + json);
    }

    private static final boolean[] ALL = {true, true, true, true, true, true, true, true};

    public static void main(String[] args) {
        var d = EscapeLogic.parse(JsonParser.parseString("{}").getAsJsonObject());
        check(d.radius() == 24 && d.clear() == 16 && d.maxTicks() == 200, "defaults");
        var p = EscapeLogic.parse(JsonParser.parseString("{\"radius\":32,\"clear\":4,\"max_ticks\":1200}").getAsJsonObject());
        check(p.radius() == 32 && p.clear() == 4 && p.maxTicks() == 1200, "bounds accepted");
        rejects("{\"radius\":3}"); rejects("{\"radius\":33}"); rejects("{\"clear\":3}"); rejects("{\"clear\":33}");
        rejects("{\"max_ticks\":19}"); rejects("{\"max_ticks\":1201}"); rejects("{\"radius\":\"9\"}");
        rejects("{\"radius\":8,\"clear\":9}");  // the clear ring must lie inside the scan

        // headings: 0 = +x (east), counter-clockwise in x/z: 2 = +z
        check(EscapeLogic.DX.length == 8 && EscapeLogic.DX[0] == 1 && EscapeLogic.DZ[2] == 1, "heading table");

        // one zombie east: run west
        var east = List.of(new double[]{5, 0});
        check(EscapeLogic.heading(0, 0, east, ALL, -1) == 4, "away from a lone mob");
        // a mob east and one west: north or south (z), never into either
        var both = List.of(new double[]{5, 0}, new double[]{-5, 0});
        int h = EscapeLogic.heading(0, 0, both, ALL, -1);
        check(h == 2 || h == 6, "between two mobs: sideways, got " + h);
        // a new spawn in the escape path turns the run: mob east, new one south-west
        var spawned = List.of(new double[]{5, 0}, new double[]{-4, -4});
        int turned = EscapeLogic.heading(0, 0, spawned, ALL, 4);
        check(turned != 4 && turned != 5 && turned != 0, "turns from the new spawn, got " + turned);
        // the best heading blocked: the next best safe one
        boolean[] noWest = ALL.clone(); noWest[4] = false;
        int around = EscapeLogic.heading(0, 0, east, noWest, -1);
        check(around == 3 || around == 5, "around a blocked heading, got " + around);
        // nothing safe: cornered
        check(EscapeLogic.heading(0, 0, east, new boolean[8], -1) == -1, "cornered");
        // no hostiles left: keep going the same way
        check(EscapeLogic.heading(0, 0, List.of(), ALL, 3) == 3, "keeps its heading when clear");
        // near-ties keep the current heading instead of jittering
        var far = List.of(new double[]{20, 0.4});
        check(EscapeLogic.heading(0, 0, far, ALL, 3) == 3 || EscapeLogic.heading(0, 0, far, ALL, 5) == 5,
                "a near tie keeps the current heading");

        // sprint needs food above 6 (vanilla); jump only to sprint-jump on the ground with headroom
        check(EscapeLogic.sprint(7) && !EscapeLogic.sprint(6), "sprint food rule");
        check(EscapeLogic.jump(true, true, true, true), "sprint-jump");
        check(!EscapeLogic.jump(false, true, true, true), "not airborne");
        check(!EscapeLogic.jump(true, false, true, true), "no jump without sprint: no gain");
        check(!EscapeLogic.jump(true, true, false, true), "no headroom");
        check(!EscapeLogic.jump(true, true, true, false), "the next cell drops: no jump");

        // ends
        check(EscapeLogic.end(5, 200, EscapeLogic.CLEAR_TICKS - 1, EscapeLogic.CORNERED_TICKS - 1) == null, "running");
        check("clear".equals(EscapeLogic.end(5, 200, EscapeLogic.CLEAR_TICKS, 0)), "clear long enough");
        check("cornered".equals(EscapeLogic.end(5, 200, 0, EscapeLogic.CORNERED_TICKS)), "cornered long enough");
        check("timeout".equals(EscapeLogic.end(201, 200, 0, 0)), "timeout");
        headingsFaceTheirWay();
        System.out.println("EscapeLogicTest ok");
    }

    private record Key(int x, int y, int z) { }

    /**
     * Each heading is checked as the body would run it, facing that way: a body facing a trench to its south
     * may still run east or west along it (the safety sweep samples along the pose's facing).
     */
    private static void headingsFaceTheirWay() {
        var air = java.util.Map.<String, Object>of("block", "minecraft:air", "collision_boxes", List.of(),
                "fluid", "minecraft:empty");
        var stone = java.util.Map.<String, Object>of("block", "minecraft:stone",
                "collision_boxes", List.of(List.of(0, 0, 0, 1, 1, 1)), "fluid", "minecraft:empty");
        var cells = new java.util.HashMap<Key, java.util.Map<String, Object>>();
        for (int x = -4; x <= 4; x++) for (int z = -4; z <= 4; z++)
            for (int y = 55; y <= 67; y++) cells.put(new Key(x, y, z), z == 1 || y >= 63 ? air : stone);
        var geometry = new RouteGeometry((x, y, z) -> cells.get(new Key(x, y, z)));
        var facingSouth = new RouteMotor.Pose(.5, 63, .5, 0, 0, 0, true, 20);
        int east = -1, south = -1;
        for (int h = 0; h < 8; h++) {
            if (EscapeLogic.DX[h] == 1 && EscapeLogic.DZ[h] == 0) east = h;
            if (EscapeLogic.DX[h] == 0 && EscapeLogic.DZ[h] == 1) south = h;
        }
        check(EscapeTask.nextCell(geometry, facingSouth, 0, 0, 63, east) != null, "east along the trench");
        check(EscapeTask.nextCell(geometry, facingSouth, 0, 0, 63, south) == null, "south into it is closed");
    }
}
