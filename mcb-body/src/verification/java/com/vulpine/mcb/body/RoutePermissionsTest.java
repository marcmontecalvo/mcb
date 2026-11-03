package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.List;
import java.util.Map;

public final class RoutePermissionsTest {
    public static void main(String[] args) {
        var door = Map.<String, Object>of("block", "minecraft:oak_door", "collision_boxes", List.of(List.of(0,0,0,1,1,1)));
        var vine = Map.<String, Object>of("block", "minecraft:vine", "collision_boxes", List.of());
        RouteGeometry.Reader base = (x,y,z) -> x == 0 ? door : x == 1 ? vine : null;
        var active = RoutePermissions.route(JsonParser.parseString("{\"use_doors\":true,\"climb_vines\":false}").getAsJsonObject());
        var activeReader = active.reader(base);
        var prefetch = RoutePermissions.edits(JsonParser.parseString("{\"doors\":false,\"vines\":true}").getAsJsonObject());
        var prefetchReader = prefetch.reader(base);
        if (!activeReader.cell(0,0,0).get("collision_boxes").equals(List.of())) throw new AssertionError("active door");
        if (!activeReader.cell(1,0,0).get("block").equals("minecraft:air")) throw new AssertionError("active vines");
        if (prefetchReader.cell(0,0,0).get("collision_boxes").equals(List.of())) throw new AssertionError("prefetch door");
        if (!prefetchReader.cell(1,0,0).get("block").equals("minecraft:vine")) throw new AssertionError("prefetch vines");
        if (activeReader.cell(2,0,0) != null) throw new AssertionError("unknown");
        if (!activeReader.cell(0,0,0).get("collision_boxes").equals(List.of())) throw new AssertionError("active changed");
        try {
            RoutePermissions.route(JsonParser.parseString("{\"use_doors\":\"true\"}").getAsJsonObject());
            throw new AssertionError("malformed permission accepted");
        } catch (IllegalArgumentException expected) { }
        System.out.println("RoutePermissions: route and prefetch isolation passed");
    }
}
