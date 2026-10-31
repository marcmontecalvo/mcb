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
        // Pillar already existed: place under the feet while jumping.
        var pillar=request.deepCopy(); var pp=pillar.getAsJsonObject("plan");
        pp.add("source",JsonParser.parseString("[0,1,0]")); pp.add("target",JsonParser.parseString("[0,2,0]"));
        pp.add("exit",JsonParser.parseString("[1,2,0]")); pp.add("breaks",JsonParser.parseString("[]"));
        pp.add("places",JsonParser.parseString("[[0,1,0]]")); pp.addProperty("kind","pillar");
        pillar.getAsJsonObject("terrain_policy").addProperty("allow_pillar",true);
        if(!new StepMotor(pillar).active()) throw new AssertionError("valid pillar"); checks++;
        // Dig down: break the block under the feet and drop one.
        var down=request.deepCopy(); var dp=down.getAsJsonObject("plan");
        dp.add("source",JsonParser.parseString("[0,2,0]")); dp.add("target",JsonParser.parseString("[0,1,0]"));
        dp.add("breaks",JsonParser.parseString("[[0,1,0]]")); dp.addProperty("kind","downward");
        if(!new StepMotor(down).active()) throw new AssertionError("valid downward"); checks++;
        bad=down.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[1,1,0]")); reject(bad);
        bad=down.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,2,1]]")); reject(bad);
        bad=down.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,1,0],[0,0,0]]")); reject(bad);
        bad=down.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString("[[0,1,0]]")); reject(bad);
        bad=down.deepCopy(); bad.getAsJsonObject("terrain_policy").addProperty("allow_break",false); reject(bad);
        bad=down.deepCopy(); bad.getAsJsonObject("terrain_policy").add("protected_positions",JsonParser.parseString("[[0,1,0]]")); reject(bad);
        // The under-feet cut is legal only for "downward"; a plain step still refuses it.
        bad=request.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,0,0]]")); reject(bad);
        // Descend with breaks: clear the lower target's feet, head and headroom.
        var descend=request.deepCopy(); var sp=descend.getAsJsonObject("plan");
        sp.add("source",JsonParser.parseString("[0,2,0]")); sp.add("target",JsonParser.parseString("[1,1,0]"));
        sp.add("breaks",JsonParser.parseString("[[1,1,0],[1,2,0],[1,3,0]]")); sp.addProperty("kind","descend");
        if(!new StepMotor(descend).active()) throw new AssertionError("valid descend"); checks++;
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,1,0]]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[1,4,0]]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[1,2,0]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[1,0,0]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString("[[1,0,0]]")); reject(bad);
        System.out.println("StepMotor: "+checks+" admission checks passed");
    }
}
