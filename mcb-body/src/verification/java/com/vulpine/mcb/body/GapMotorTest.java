package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class GapMotorTest {
    private static int checks;
    private static void check(boolean yes,String label) { checks++; if(!yes) throw new AssertionError(label); }
    private static void reject(JsonObject request) {
        try { new GapMotor(request); throw new AssertionError("Unsafe gap admitted"); }
        catch(IllegalArgumentException expected) { checks++; }
    }
    public static void main(String[] args) {
        var request=JsonParser.parseString("{\"gap_id\":\"g\",\"plan\":{\"source\":[0,1,0],\"target\":[3,1,0]},\"movement_policy\":{\"max_gap\":2,\"allow_sprint\":true}}").getAsJsonObject();
        var motor=new GapMotor(request); check(motor.active(),"admitted gap");
        var bad=request.deepCopy(); bad.getAsJsonObject("movement_policy").addProperty("allow_sprint",false); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("movement_policy").addProperty("max_gap",1); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("movement_policy").addProperty("max_gap",true); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[2,1,1]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[3,2,0]")); reject(bad);
        var cells=new HashMap<String,Map<String,Object>>();
        for(int x=-2;x<8;x++) for(int y=-3;y<6;y++) for(int z=-1;z<=1;z++) {
            boolean solid=y==0 && (x<1 || x>2);
            cells.put(x+","+y+","+z,Map.of("block",solid?"minecraft:stone":"minecraft:air",
                    "collision_boxes",solid?List.of(List.of(0,0,0,1,1,1)):List.of(),"fluid","minecraft:empty"));
        }
        RouteGeometry.Reader reader=(x,y,z)->cells.get(x+","+y+","+z);
        check(motor.geometry(reader),"known runway, gap and landing");
        cells.remove("2,2,0"); check(!motor.geometry(reader),"unknown overhead rejects");
        motor.stop("explicit"); check(!motor.active(),"cancel stops gap");
        System.out.println("GapMotor: "+checks+" permission/geometry checks passed");
    }
}
