package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/** Plan admission must reject policy/shape violations before any player input. */
public final class StepMotorTest {
    private static int checks;
    private static void reject(JsonObject request) {
        try { new StepMotor(request); throw new AssertionError("Bad native step admitted"); }
        catch(IllegalArgumentException|IllegalStateException expected) { checks++; }
    }
    public static void main(String[] args) {
        var request=JsonParser.parseString("""
          {"step_id":"s","plan":{"source":[0,1,0],"target":[1,1,0],"breaks":[[1,1,0]],"places":[],"kind":"step","exit":null},
          "terrain_policy":{"allow_break":true,"allow_place":true,"break_budget":8,"place_budget":8,"reserve_blocks":0,
          "interaction_tick_limit":160,"material_allowlist":["minecraft:cobblestone","minecraft:dirt"],"protected_blocks":[],
          "protected_positions":[],"allow_bridge":false,"allow_pillar":false,"max_support_stack":1}}
          """).getAsJsonObject();
        if(!new StepMotor(request).active()) throw new AssertionError("valid cut"); checks++;
        var bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").addProperty("allow_break",false); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").addProperty("break_budget",0); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").addProperty("break_budget",true); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[2,1,0]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,0,0]]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").add("protected_positions",JsonParser.parseString("[[1,1,0]]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[\"minecraft:tnt\"]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[1,1,0],[1,1,0]]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("source",JsonParser.parseString("[0,1.5,0]")); reject(bad);
        bad=request.deepCopy(); bad.getAsJsonObject("plan").addProperty("kind","pillar"); reject(bad);
        System.out.println("StepMotor: "+checks+" admission checks passed");
    }
}
