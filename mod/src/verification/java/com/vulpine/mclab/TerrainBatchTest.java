package com.vulpine.mclab;

import com.google.gson.Gson;
import com.google.gson.JsonParser;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

public final class TerrainBatchTest {
    private static Map<String,Object> cell(int x,int y,int z,String block) {
        return Map.of("position",List.of(x,y,z),"observed_tick",7,"block",block,
                "fluid","minecraft:empty","replaceable",block.equals("minecraft:air"),
                "properties",Map.of(),"collision_boxes",List.of());
    }

    public static void main(String[] args) throws Exception {
        var batch=new TerrainBatch(List.of(cell(-16,-1,31,"minecraft:air"),
                cell(-1,-16,16,"minecraft:air"),cell(0,0,0,"minecraft:stone")),7);
        var gson=new Gson();
        String text=gson.toJson(Map.of("terrain_sections",batch));
        var data=JsonParser.parseString(text).getAsJsonObject().getAsJsonObject("terrain_sections");
        if(data.get("known_cells").getAsInt()!=3 || data.getAsJsonArray("palette").size()!=2
                || data.getAsJsonArray("sections").size()!=2
                || data.get("unknown_is_air").getAsBoolean() || text.contains("position"))
            throw new AssertionError("palette serialization lost sparse facts");
        var first=data.getAsJsonArray("sections").get(0).getAsJsonObject();
        if(!first.get("section").toString().equals("[-1,-1,1]")
                || !first.getAsJsonArray("cells").get(0).toString().equals("[255,0,7]")
                || !first.getAsJsonArray("cells").get(1).toString().equals("[3840,0,7]"))
            throw new AssertionError("negative coordinates or cell ages changed");
        try(var workers=Executors.newFixedThreadPool(4)) {
            for(int i=0;i<8;i++) {
                var future=workers.submit(()->gson.toJson(Map.of("terrain_sections",batch)));
                if(!future.get().equals(text)) throw new AssertionError("mutable payload");
            }
        }
        System.out.println("Terrain palette: sparse facts, negative sections and concurrent serialization verified");
    }
}
