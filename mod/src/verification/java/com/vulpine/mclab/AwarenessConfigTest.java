package com.vulpine.mclab;

import com.google.gson.Gson;
import com.google.gson.JsonObject;

/** Configuration admission runs without starting a Minecraft server. */
public final class AwarenessConfigTest {
    private static void reject(JsonObject value) {
        try {
            AwarenessConfig.parse(value);
            throw new AssertionError("Invalid awareness admitted: " + value);
        } catch (IllegalArgumentException | IllegalStateException expected) { }
    }

    public static void main(String[] args) {
        for(int radius:new int[]{1,2,4}) for(int[] limits:new int[][]{
                {-4,4},{-8,-2},{2,7},{-64,319},{-1,1},{5,5},{0,0},{-7,-7},{3,2}}) {
            var volume=new AwarenessVolume(radius,limits[0],limits[1]);
            int expected=(radius*2+1)*(radius*2+1)*Math.max(0,limits[1]-limits[0]+1);
            if(volume.size()!=expected) throw new AssertionError("inclusive volume height");
            var unique=new java.util.HashSet<AwarenessVolume.Cell>();
            int previousShell=-1;
            for(int i=0;i<volume.size();i++) {
                var cell=volume.at(i);
                int shell=Math.max(Math.abs(cell.x()),Math.max(Math.abs(cell.y()),Math.abs(cell.z())));
                if(!unique.add(cell) || Math.abs(cell.x())>radius || Math.abs(cell.z())>radius
                        || cell.y()<limits[0] || cell.y()>limits[1] || shell<previousShell)
                    throw new AssertionError("unique in-range nearest-shell enumeration");
                previousShell=shell;
            }
        }
        if(new AwarenessVolume(32,-64,319).size()!=65*65*384) throw new AssertionError("full height includes max Y");
        var gson=new Gson();
        var legacy=AwarenessConfig.legacy();
        var json=gson.toJsonTree(legacy.payload()).getAsJsonObject();
        if(!AwarenessConfig.parse(json).equals(legacy)) throw new AssertionError("round trip");
        var changed=json.deepCopy(); changed.addProperty("horizontal_radius_blocks",9); reject(changed);
        changed=json.deepCopy(); changed.addProperty("extra",1); reject(changed);
        changed=json.deepCopy(); changed.remove("entity_visibility"); reject(changed);
        changed=json.deepCopy(); changed.addProperty("preset","bad"); reject(changed);
        for(String key: new String[]{"schema_version","horizontal_radius_blocks","vertical_radius_blocks",
                "entity_radius_blocks","entity_vertical_radius_blocks","update_interval_ticks",
                "extra_scan_cells_per_update","extra_scan_budget_ms"}) {
            changed=json.deepCopy(); changed.addProperty(key,0); reject(changed);
            changed=json.deepCopy(); changed.addProperty(key,1.5); reject(changed);
            changed=json.deepCopy(); changed.addProperty(key,"5"); reject(changed);
            changed=json.deepCopy(); changed.addProperty(key,true); reject(changed);
        }
        changed=json.deepCopy(); changed.addProperty("preset","visible");
        changed.addProperty("horizontal_radius_blocks",64); changed.addProperty("vertical_radius_blocks",64);
        AwarenessConfig.parse(changed);
        changed.addProperty("horizontal_radius_blocks",65); reject(changed);
        changed.addProperty("horizontal_radius_blocks",32); changed.addProperty("vertical_radius_blocks",-1); reject(changed);
        changed.addProperty("preset","chunk");
        if(AwarenessConfig.parse(changed).occluded()) throw new AssertionError("chunk occlusion");
        changed.addProperty("entity_visibility","unoccluded"); AwarenessConfig.parse(changed);
        changed.addProperty("entity_visibility","bad"); reject(changed);
        System.out.println("Awareness configuration: profiles, bounds, types and frozen legacy verified");
    }
}
