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
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[\"tnt\"]")); reject(bad);
        // Which blocks are temporaries is Python's choice (mclab.temporary_blocks); the motor needs an id.
        bad=request.deepCopy(); bad.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[]")); reject(bad);
        var planks=request.deepCopy(); planks.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[\"minecraft:oak_planks\"]"));
        if(!new StepMotor(planks).active()) throw new AssertionError("planks as a temporary"); checks++;
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
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[1,5,0]]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[1,2,0]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[1,0,0]")); reject(bad);
        bad=descend.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString("[[1,0,0]]")); reject(bad);
        // Live run 20261112T181731902927Z: a descend cut from a two-high tunnel. Aimed at its centre the
        // top cut (137,66,-45) is hidden behind the tunnel ceiling (137,66,-46); its underside is in view.
        var eye=new net.minecraft.world.phys.Vec3(137.5,65.62,-45.85);
        var top=new net.minecraft.core.BlockPos(137,66,-45);
        var centre=net.minecraft.world.phys.Vec3.atCenterOf(top);
        java.util.function.Predicate<net.minecraft.world.phys.Vec3> underCeiling=
            v->eye.z+(v.z-eye.z)*(66-eye.y)/(v.y-eye.y)>=-45;
        if(underCeiling.test(centre)) throw new AssertionError("fixture: the centre is hidden"); checks++;
        var aim=StepMotor.aim(top,underCeiling);
        if(!underCeiling.test(aim) || aim.y>66.1) throw new AssertionError("a cut hidden at its centre is aimed at a face in view"); checks++;
        if(!StepMotor.aim(top,v->true).equals(centre)) throw new AssertionError("a centre in view stays the aim"); checks++;
        if(!StepMotor.aim(top,v->false).equals(centre)) throw new AssertionError("with nothing in view the centre stays the aim"); checks++;
        // Flowers vs grass: short grass & ferns are replaced directly; flowers must be punched first.
        var grass=java.util.Map.<String,Object>of("block","minecraft:short_grass","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of(),"replaceable",true);
        var fern=java.util.Map.<String,Object>of("block","minecraft:fern","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of());
        var dandelion=java.util.Map.<String,Object>of("block","minecraft:dandelion","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of(),"replaceable",false);
        var poppy=java.util.Map.<String,Object>of("block","minecraft:poppy","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of());
        var air=java.util.Map.<String,Object>of("block","minecraft:air","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of(),"replaceable",true);
        if(!StepMotor.replaceableCover(grass) || StepMotor.flower(grass)) throw new AssertionError("short grass replaces directly"); checks++;
        if(!StepMotor.replaceableCover(fern) || StepMotor.flower(fern)) throw new AssertionError("fern replaces directly even without replaceable flag"); checks++;
        if(StepMotor.replaceableCover(dandelion) || !StepMotor.flower(dandelion)) throw new AssertionError("dandelion must be punched first"); checks++;
        if(StepMotor.replaceableCover(poppy) || !StepMotor.flower(poppy)) throw new AssertionError("poppy must be punched first"); checks++;
        if(StepMotor.replaceableCover(air) || StepMotor.flower(air)) throw new AssertionError("air is neither cover nor flower"); checks++;
        var pt=new net.minecraft.core.BlockPos(1,1,0); var exp=pt.below();
        if(!StepMotor.placeHit(pt,"NORTH",pt,exp,"UP",true)) throw new AssertionError("direct replace accepts hitting the grass cell itself"); checks++;
        if(!StepMotor.placeHit(exp,"UP",pt,exp,"UP",true)) throw new AssertionError("direct replace also accepts hitting anchor face"); checks++;
        if(StepMotor.placeHit(pt,"UP",pt,exp,"UP",false)) throw new AssertionError("air placement requires anchor face"); checks++;
        // Source-clearing cuts (e.g. gravel on feet/head) are valid step breaks.
        var gravelSource=request.deepCopy();
        gravelSource.getAsJsonObject("plan").add("breaks",JsonParser.parseString("[[0,2,0],[0,1,0],[1,1,0]]"));
        if(!new StepMotor(gravelSource).active()) throw new AssertionError("source-clearing cuts admitted"); checks++;
        System.out.println("StepMotor: "+checks+" admission checks passed");
    }
}
