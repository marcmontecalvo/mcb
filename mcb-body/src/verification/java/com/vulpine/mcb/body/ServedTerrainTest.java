package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;

/** Served sections are readable by plans as of a served tick; nothing newer, nothing unserved. */
public final class ServedTerrainTest {
    private static void check(boolean ok, String what) { if (!ok) throw new AssertionError(what); }

    public static void main(String[] args) {
        var air = Map.<String,Object>of("block", "minecraft:air", "fluid", "minecraft:empty", "collision_boxes", List.of());
        var stone = Map.<String,Object>of("block", "minecraft:stone", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)));
        RouteGeometry.Reader nothing = (x, y, z) -> null;
        var facts = Map.of(0, air, 1, stone);
        int[] mixed = new int[4096]; mixed[0] = 1;
        var bulk = new AwarenessSensor.SectionBatch(List.of(
                new AwarenessSensor.SectionData(0, 4, 0, 0, null, 10),
                new AwarenessSensor.SectionData(1, 4, 0, -1, mixed, 10)), facts);
        var store = new ServedTerrain();
        Object b1 = new Object(), b2 = new Object(), unread = new Object();

        // Staged but not served: unknown, and a plan cannot name its tick.
        store.stage(b1, 10, List.of(), bulk, 8, 70, 8);
        try { store.reader(10, nothing); throw new AssertionError("unserved tick accepted"); }
        catch (IllegalArgumentException expected) { }
        store.serve(b1);
        var at10 = store.reader(10, nothing);
        check(air.equals(at10.cell(3, 70, 3)), "uniform served section readable");
        check(stone.equals(at10.cell(16, 64, 0)) && air.equals(at10.cell(17, 64, 0)), "dense served section readable");
        check(at10.cell(40, 70, 0) == null, "unserved section is unknown");

        // A near cell changes at tick 15 (stone placed); the tick-10 reader keeps the old map.
        var placed = Map.<String,Object>of("block", "minecraft:stone", "fluid", "minecraft:empty",
                "collision_boxes", List.of(List.of(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)), "position", List.of(3, 70, 3),
                "observed_tick", 15);
        store.stage(b2, 15, List.of(placed), new AwarenessSensor.SectionBatch(List.of(), Map.of()), 8, 70, 8);
        // A batch built later but never served stays invisible.
        var far = new AwarenessSensor.SectionBatch(List.of(new AwarenessSensor.SectionData(5, 4, 5, 1, null, 20)), facts);
        store.serve(b2);
        store.stage(unread, 20, List.of(), far, 8, 70, 8);
        var at15 = store.reader(15, nothing);
        check(stone.equals(at15.cell(3, 70, 3)), "served near cell overrides the section");
        check(air.equals(at10.cell(3, 70, 3)), "older reader answers as of its own tick");
        check(air.equals(store.reader(12, nothing).cell(3, 70, 3)), "as-of picks the newest version not after the tick");
        check(store.reader(15, nothing).cell(80, 70, 80) == null, "unread batch invisible");
        try { store.reader(20, nothing); throw new AssertionError("unread tick accepted"); }
        catch (IllegalArgumentException expected) { }

        // Unknown served cells fall through to the uploaded overlay (recalled sections); known ones win.
        RouteGeometry.Reader overlay = (x, y, z) -> stone;
        check(stone.equals(store.reader(15, overlay).cell(40, 70, 0)), "overlay fills unknown");
        check(air.equals(store.reader(15, overlay).cell(4, 70, 4)), "served beats overlay");

        // Memory: uniform sections share one array; dense ones are counted.
        check(store.bytes() == 2 * ServedTerrain.SECTION_BYTES, "dense section and its copy-on-write version counted: " + store.bytes());

        // Past the horizon a stale tick is refused rather than answered from a pruned map.
        Object late = new Object();
        store.stage(late, 15 + ServedTerrain.HORIZON_TICKS, List.of(), new AwarenessSensor.SectionBatch(List.of(), Map.of()), 8, 70, 8);
        store.serve(late);
        try { store.reader(15, nothing); throw new AssertionError("stale tick accepted"); }
        catch (IllegalArgumentException expected) { }

        // The cap evicts the farthest sections first and reports them.
        var big = new ServedTerrain();
        int count = (int) (ServedTerrain.MAX_BYTES / ServedTerrain.SECTION_BYTES) + 10;
        var many = new java.util.ArrayList<AwarenessSensor.SectionData>();
        for (int i = 0; i < count; i++) many.add(new AwarenessSensor.SectionData(i % 200, 4, i / 200, -1, mixed, 30));
        Object huge = new Object();
        big.stage(huge, 30, List.of(), new AwarenessSensor.SectionBatch(many, facts), 0, 70, 0);
        big.serve(huge);
        check(big.bytes() <= ServedTerrain.MAX_BYTES, "cap holds");
        var evicted = big.drainEvicted();
        check(evicted.size() == 1 && ((List<?>) evicted.get(0).get("sections")).size() > 0, "eviction reported");
        check(big.reader(30, nothing).cell(0, 64, 0) != null, "nearest section kept");
        check(big.drainEvicted().isEmpty(), "drained once");

        // Reset is a new store: nothing survives.
        check(new ServedTerrain().sectionCount() == 0, "fresh store is empty");
        System.out.println("ServedTerrainTest ok");
    }
}
