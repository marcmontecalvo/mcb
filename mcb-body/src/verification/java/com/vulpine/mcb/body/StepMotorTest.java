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
        var cutOnly=request.deepCopy(); cutOnly.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[]"));
        if(!new StepMotor(cutOnly).active()) throw new AssertionError("a cut needs no building block"); checks++;
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
        // The exit may be a stair top or slab, half a block up (run 20261118T125548315616Z).
        var stairExit=pillar.deepCopy(); stairExit.getAsJsonObject("plan").add("exit",JsonParser.parseString("[1,2.5,0]"));
        if(!new StepMotor(stairExit).active()) throw new AssertionError("a pillar exit on a stair top"); checks++;
        bad=pillar.deepCopy(); bad.getAsJsonObject("plan").add("exit",JsonParser.parseString("[1.5,2,0]")); reject(bad);
        // Holding only scaffolding: no solid block may be placed, a scaffolding cell may.
        bad=pillar.deepCopy(); bad.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[]")); reject(bad);
        var scaffoldOnly=bad.deepCopy(); scaffoldOnly.getAsJsonObject("plan").add("scaffold",JsonParser.parseString("[[0,1,0]]"));
        if(!new StepMotor(scaffoldOnly).active()) throw new AssertionError("a scaffolding pillar with no solid block held"); checks++;
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
        // Scaffolding bridge redirect aim: aligns perpendicular axis to the eye and aims near the far edge;
        // returns null when too close to the front edge or too far off-center so the body repositions without sneaking.
        var sPos=new net.minecraft.core.BlockPos(10,5,20);
        var aimPosX=StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.5,6.62,20.5),1,0);
        if(aimPosX==null || Math.abs(aimPosX.x-10.95)>1e-6 || Math.abs(aimPosX.y-4.999)>1e-6 || Math.abs(aimPosX.z-20.5)>1e-6)
            throw new AssertionError("centered redirectAim along +X: "+aimPosX); checks++;
        var aimOffCenter=StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.5,6.62,20.15),1,0);
        if(aimOffCenter==null || Math.abs(aimOffCenter.z-20.15)>1e-6)
            throw new AssertionError("off-center redirectAim tracks eye perp coordinate: "+aimOffCenter); checks++;
        if(StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.85,6.62,20.5),1,0)!=null)
            throw new AssertionError("redirectAim too close to +X edge must return null"); checks++;
        if(StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.5,6.62,19.70),1,0)!=null)
            throw new AssertionError("redirectAim far off-axis must return null"); checks++;
        var aimNegZ=StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.5,6.62,20.5),0,-1);
        if(aimNegZ==null || Math.abs(aimNegZ.z-20.05)>1e-6 || Math.abs(aimNegZ.x-10.5)>1e-6)
            throw new AssertionError("centered redirectAim along -Z: "+aimNegZ); checks++;
        if(StepMotor.redirectAim(sPos,new net.minecraft.world.phys.Vec3(10.5,6.62,20.15),0,-1)!=null)
            throw new AssertionError("redirectAim too close to -Z edge must return null"); checks++;
        // Tower-top pillar exit ([0,2,0] = target itself) is admitted cleanly.
        var topExit=pillar.deepCopy(); topExit.getAsJsonObject("plan").add("exit",JsonParser.parseString("[0,2,0]"));
        if(!new StepMotor(topExit).active()) throw new AssertionError("tower-top pillar self-exit admitted"); checks++;
        // Drop-scaffolding descent (ROADMAP B2a): 5-block overhang crane + contiguous vertical shaft column.
        var drop=request.deepCopy(); var drp=drop.getAsJsonObject("plan");
        drp.add("source",JsonParser.parseString("[1,0,0]")); drp.add("target",JsonParser.parseString("[2,-1,0]"));
        drp.add("breaks",JsonParser.parseString("[]")); drp.addProperty("kind","scaffold_drop");
        var crane="[[0,0,0],[0,1,0],[0,2,0],[1,2,0],[2,2,0]";
        var col=crane+",[2,-10,0],[2,-9,0],[2,-8,0],[2,-7,0],[2,-6,0],[2,-5,0],[2,-4,0],[2,-3,0],[2,-2,0],[2,-1,0]]";
        drp.add("places",JsonParser.parseString(col)); drp.add("scaffold",JsonParser.parseString(col));
        drop.getAsJsonObject("terrain_policy").addProperty("place_budget",16);
        drop.getAsJsonObject("terrain_policy").add("material_allowlist",JsonParser.parseString("[]"));
        if(!new StepMotor(drop).active()) throw new AssertionError("valid 10-deep crane+shaft scaffold_drop"); checks++;
        bad=drop.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString(crane+",[2,-1,0]]"));
        bad.getAsJsonObject("plan").add("scaffold",JsonParser.parseString(crane+",[2,-1,0]]")); reject(bad);
        bad=drop.deepCopy(); bad.getAsJsonObject("plan").add("scaffold",JsonParser.parseString("[]")); reject(bad);
        bad=drop.deepCopy(); bad.getAsJsonObject("plan").add("target",JsonParser.parseString("[2,0,0]")); reject(bad);
        bad=drop.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString(crane+",[2,-3,0],[2,-1,0]]"));
        bad.getAsJsonObject("plan").add("scaffold",JsonParser.parseString(crane+",[2,-3,0],[2,-1,0]]")); reject(bad);
        bad=drop.deepCopy(); bad.getAsJsonObject("plan").add("places",JsonParser.parseString("[[2,-10,0],[2,-9,0],[2,-8,0],[2,-7,0],[2,-6,0],[2,-5,0],[2,-4,0],[2,-3,0],[2,-2,0],[2,-1,0]]"));
        bad.getAsJsonObject("plan").add("scaffold",JsonParser.parseString("[[2,-10,0],[2,-9,0],[2,-8,0],[2,-7,0],[2,-6,0],[2,-5,0],[2,-4,0],[2,-3,0],[2,-2,0],[2,-1,0]]")); reject(bad);
        bad=drop.deepCopy(); bad.getAsJsonObject("terrain_policy").add("protected_positions",JsonParser.parseString("[[2,-5,0]]")); reject(bad);
        // settledScaffold rejects distance=7 (just landed, scheduled tick has not settled yet) and accepts distance=0.
        var unsettled=java.util.Map.<String,Object>of("block","minecraft:scaffolding","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of(),"properties",java.util.Map.of("distance","7","bottom","true"));
        var settled=java.util.Map.<String,Object>of("block","minecraft:scaffolding","fluid","minecraft:empty",
                "collision_boxes",java.util.List.of(),"properties",java.util.Map.of("distance","0","bottom","false"));
        if(StepMotor.settledScaffold(unsettled) || !StepMotor.settledScaffold(settled))
            throw new AssertionError("settledScaffold must distinguish distance=7 from distance=0"); checks++;
        // A block is clicked onto its top face when its collision covers that face: a top stair or top slab
        // carries a pillar like a cube (run 20261122T045703502025Z: a body shut in on a roof stair).
        java.util.function.Function<String,java.util.Map<String,Object>> shape=boxes->java.util.Map.<String,Object>of(
                "block","minecraft:oak_stairs","fluid","minecraft:empty","properties",java.util.Map.of(),
                "collision_boxes",new com.google.gson.Gson().fromJson(boxes,java.util.List.class));
        var topStair=shape.apply("[[0,0.5,0,1,1,1],[0,0,0,1,0.5,0.5]]");
        var topSlab=shape.apply("[[0,0.5,0,1,1,1]]");
        var bottomSlab=shape.apply("[[0,0,0,1,0.5,1]]");
        var bottomStair=shape.apply("[[0,0,0,1,0.5,1],[0,0.5,0,1,1,0.5]]");
        var split=shape.apply("[[0,0.5,0,0.5,1,1],[0.5,0.5,0,1,1,1]]");
        var cubeCell=shape.apply("[[0,0,0,1,1,1]]");
        var wet=new java.util.HashMap<>(topSlab); wet.put("fluid","minecraft:water");
        if(!StepMotor.fullTop(topStair) || !StepMotor.fullTop(topSlab) || !StepMotor.fullTop(split) || !StepMotor.fullTop(cubeCell))
            throw new AssertionError("a top stair, top slab, two half boxes and a cube have a full top face"); checks++;
        if(StepMotor.fullTop(bottomSlab) || StepMotor.fullTop(bottomStair) || StepMotor.fullTop(shape.apply("[]"))
                || StepMotor.fullTop(wet) || StepMotor.fullTop(null))
            throw new AssertionError("a bottom slab, bottom stair, an empty or a wet cell has no full top face"); checks++;
        System.out.println("StepMotor: "+checks+" admission checks passed");
    }
}
