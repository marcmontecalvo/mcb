package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;
import java.util.Base64;
import java.util.zip.Deflater;

/** Unknown cells and earlier snapshots remain intact across incremental updates. */
public final class PlannerMapTest {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) {
        var map = new PlannerMap();
        var air = Map.<String, Object>of("block", "minecraft:air", "fluid", "minecraft:empty", "collision_boxes", List.of());
        map.begin(0, 1); map.palette(1, List.of(air));
        map.page(-1, 0, 0, 15 << 8, new int[]{1}); map.commit(1);
        var old = map.reader(1);
        check(old.cell(-1, 0, 0).equals(air));
        check(old.cell(-1, 0, 1) == null);
        map.begin(1, 2); map.page(-1, 0, 0, 15 << 8, new int[]{0, 1}); map.commit(2);
        var current = map.reader(2);
        check(current.cell(-1, 0, 0) == null && current.cell(-1, 0, 1).equals(air));
        check(old.cell(-1, 0, 0).equals(air) && old.cell(-1, 0, 1) == null);
        try { map.begin(1, 3); throw new AssertionError(); } catch (IllegalArgumentException expected) { }
        map.begin(2, 3);
        try { map.page(0, 0, 0, 0, new int[]{2}); throw new AssertionError(); } catch (IllegalArgumentException expected) { }
        map.abort(); check(map.generation() == 2);
        byte[] plain = new byte[8192]; plain[0] = 1;
        var deflater = new Deflater(); deflater.setInput(plain); deflater.finish();
        byte[] encoded = new byte[9000]; int size = deflater.deflate(encoded); deflater.end();
        map.begin(2, 3);
        map.section(1, 0, 0, Base64.getEncoder().encodeToString(java.util.Arrays.copyOf(encoded, size)));
        map.commit(3);
        check(map.reader(3).cell(16, 0, 0).equals(air));
        check(current.cell(16, 0, 0) == null);
        map.begin(3, 4);
        try { map.section(0, 0, 0, "AAAA"); throw new AssertionError(); } catch (IllegalArgumentException expected) { }
        try {
            map.palette(2, List.of(Map.of("block", "minecraft:stone", "collision_boxes", List.of(List.of(0, 1)))));
            throw new AssertionError("malformed box accepted");
        } catch (IllegalArgumentException expected) { }
        map.abort(); check(map.generation() == 3);
        System.out.println("PlannerMap: snapshot checks passed");
    }
}
