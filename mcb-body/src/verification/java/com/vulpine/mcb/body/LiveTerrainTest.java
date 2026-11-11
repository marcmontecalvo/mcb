package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Live terrain inside the radius, the uploaded map outside it, unknown below both. */
public final class LiveTerrainTest {
    private static void check(boolean value, String what) { if (!value) throw new AssertionError(what); }
    private static void rejects(String json) {
        try { LiveTerrain.radius(JsonParser.parseString(json).getAsJsonObject()); }
        catch (IllegalArgumentException expected) { return; }
        throw new AssertionError("accepted " + json);
    }
    public static void main(String[] args) {
        var air = Map.<String, Object>of("block", "minecraft:air", "fluid", "minecraft:empty", "collision_boxes", List.of());
        var stone = Map.<String, Object>of("block", "minecraft:stone", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)));
        var world = new HashMap<List<Integer>, Map<String, Object>>();  // absent = unloaded chunk
        LiveTerrain.Source live = (x, y, z) -> world.get(List.of(x, y, z));
        var uploaded = new HashMap<List<Integer>, Map<String, Object>>();
        RouteGeometry.Reader fallback = (x, y, z) -> uploaded.get(List.of(x, y, z));
        var reader = LiveTerrain.reader(live, 0, 64, 0, 16, 8, fallback);

        world.put(List.of(16, 72, -16), air);
        check(air.equals(reader.cell(16, 72, -16)), "the radius edge is inside");
        world.put(List.of(17, 64, 0), air); uploaded.put(List.of(17, 64, 0), stone);
        check(stone.equals(reader.cell(17, 64, 0)), "outside the radius the uploaded map answers");
        world.put(List.of(0, 73, 0), air);
        check(reader.cell(0, 73, 0) == null, "above the vertical radius with no upload is unknown");
        uploaded.put(List.of(1, 64, 1), air);
        check(air.equals(reader.cell(1, 64, 1)), "an unloaded cell inside falls through to the upload");
        check(reader.cell(2, 64, 2) == null, "unloaded and never uploaded is unknown, never air");
        world.put(List.of(3, 64, 3), air);
        check(air.equals(reader.cell(3, 64, 3)), "live read");
        world.put(List.of(3, 64, 3), stone);
        check(stone.equals(reader.cell(3, 64, 3)), "a block change is visible to the next read");
        uploaded.put(List.of(4, 64, 4), air); world.put(List.of(4, 64, 4), stone);
        check(stone.equals(reader.cell(4, 64, 4)), "inside the radius the live world wins over the upload");
        world.put(List.of(0, -60, 0), stone);
        check(stone.equals(LiveTerrain.reader(live, 0, 64, 0, 16, -1, fallback).cell(0, -60, 0)),
                "vertical -1 is the whole column");

        check(LiveTerrain.radius(JsonParser.parseString("{\"map_generation\":0}").getAsJsonObject()) == null,
                "a flagless plan keeps the uploaded map");
        var radius = LiveTerrain.radius(JsonParser.parseString(
                "{\"terrain\":\"live\",\"terrain_radius\":[64,48]}").getAsJsonObject());
        check(radius[0] == 64 && radius[1] == 48, "radius parsed");
        rejects("{\"terrain\":\"served\",\"terrain_radius\":[64,48]}");
        rejects("{\"terrain\":\"live\"}");
        rejects("{\"terrain\":\"live\",\"terrain_radius\":[-1,48]}");
        rejects("{\"terrain\":\"live\",\"terrain_radius\":[64,-2]}");
        rejects("{\"terrain\":\"live\",\"terrain_radius\":[64.5,48]}");
        rejects("{\"terrain\":\"live\",\"terrain_radius\":[64]}");
        System.out.println("LiveTerrainTest ok");
    }
}
