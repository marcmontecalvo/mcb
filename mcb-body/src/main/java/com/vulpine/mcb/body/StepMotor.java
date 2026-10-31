package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.item.FallingBlockEntity;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Executes a bounded planned edit through normal inputs; Python confirms stock/geometry. */
final class StepMotor {
    // Must match terrain_steps.WIDE_EXCAVATABLE (tests/test_planner_core.py checks).
    // Python decides which blocks a plan may cut; this is the body-side allowlist.
    private static final Map<String,String> TOOLS = Map.ofEntries(
            Map.entry("minecraft:acacia_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:acacia_log","minecraft:iron_axe"),
            Map.entry("minecraft:andesite","minecraft:iron_pickaxe"),
            Map.entry("minecraft:azalea_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:birch_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:birch_log","minecraft:iron_axe"),
            Map.entry("minecraft:black_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:blue_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:brown_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:calcite","minecraft:iron_pickaxe"),
            Map.entry("minecraft:cherry_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:cherry_log","minecraft:iron_axe"),
            Map.entry("minecraft:clay","minecraft:iron_shovel"),
            Map.entry("minecraft:coal_ore","minecraft:iron_pickaxe"),
            Map.entry("minecraft:coarse_dirt","minecraft:iron_shovel"),
            Map.entry("minecraft:cobblestone","minecraft:iron_pickaxe"),
            Map.entry("minecraft:cyan_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:dark_oak_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:dark_oak_log","minecraft:iron_axe"),
            Map.entry("minecraft:deepslate","minecraft:iron_pickaxe"),
            Map.entry("minecraft:diorite","minecraft:iron_pickaxe"),
            Map.entry("minecraft:dirt","minecraft:iron_shovel"),
            Map.entry("minecraft:granite","minecraft:iron_pickaxe"),
            Map.entry("minecraft:grass_block","minecraft:iron_shovel"),
            Map.entry("minecraft:gravel","minecraft:iron_shovel"),
            Map.entry("minecraft:gray_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:green_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:iron_ore","minecraft:iron_pickaxe"),
            Map.entry("minecraft:jungle_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:jungle_log","minecraft:iron_axe"),
            Map.entry("minecraft:light_blue_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:light_gray_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:lime_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:magenta_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:mangrove_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:mangrove_log","minecraft:iron_axe"),
            Map.entry("minecraft:mud","minecraft:iron_shovel"),
            Map.entry("minecraft:mycelium","minecraft:iron_shovel"),
            Map.entry("minecraft:oak_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:oak_log","minecraft:iron_axe"),
            Map.entry("minecraft:orange_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:pink_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:podzol","minecraft:iron_shovel"),
            Map.entry("minecraft:purple_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:red_sand","minecraft:iron_shovel"),
            Map.entry("minecraft:red_sandstone","minecraft:iron_pickaxe"),
            Map.entry("minecraft:red_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:rooted_dirt","minecraft:iron_shovel"),
            Map.entry("minecraft:sand","minecraft:iron_shovel"),
            Map.entry("minecraft:sandstone","minecraft:iron_pickaxe"),
            Map.entry("minecraft:snow_block","minecraft:iron_shovel"),
            Map.entry("minecraft:spruce_leaves","minecraft:iron_axe"),
            Map.entry("minecraft:spruce_log","minecraft:iron_axe"),
            Map.entry("minecraft:stone","minecraft:iron_pickaxe"),
            Map.entry("minecraft:terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:tuff","minecraft:iron_pickaxe"),
            Map.entry("minecraft:white_terracotta","minecraft:iron_pickaxe"),
            Map.entry("minecraft:yellow_terracotta","minecraft:iron_pickaxe"));
    private static final Set<String> FALLING = Set.of("minecraft:gravel","minecraft:red_sand","minecraft:sand","minecraft:suspicious_gravel","minecraft:suspicious_sand");
    private record Operation(boolean place, BlockPos point) { }
    private final String id, kind;
    private final BlockPos source, target, exit;
    private final List<Operation> operations;
    private final Set<BlockPos> protectedPositions;
    private final Set<String> protectedBlocks, materials;
    private final boolean bridge, downward;
    private final int reserve, tickLimit;
    // Baritone Settings.pauseMiningForFallingBlocks: wait this long after a cut under a
    // falling-block stack, and while any falling block is near, before trusting the cell.
    private static final int SETTLE_TICKS = 6;
    private final Set<BlockPos> settleWatch = new HashSet<>();
    private int settleUntil = -1;
    private int index, started = -1, issueTick = -1, finishedTick = -1;
    private boolean issued, launched;
    private String state = "following", reason = "", placedItem = "";
    private final Set<BlockPos> broken = new HashSet<>();
    private final Set<BlockPos> issuedBreaks = new HashSet<>();
    private final Map<BlockPos,String> placed = new HashMap<>();

    StepMotor(JsonObject request) {
        id = request.get("step_id").getAsString();
        if (!id.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("step_id");
        var plan = request.getAsJsonObject("plan");
        var policy = request.getAsJsonObject("terrain_policy");
        source = point(plan.getAsJsonArray("source")); target = point(plan.getAsJsonArray("target"));
        kind = plan.get("kind").getAsString();
        exit = plan.has("exit") && !plan.get("exit").isJsonNull() ? point(plan.getAsJsonArray("exit")) : null;
        bridge = bool(policy,"allow_bridge"); reserve = integer(policy,"reserve_blocks",0,128);
        tickLimit = integer(policy,"interaction_tick_limit",1,1000);
        protectedBlocks = strings(policy.getAsJsonArray("protected_blocks"));
        materials = strings(policy.getAsJsonArray("material_allowlist"));
        if (materials.isEmpty() || !Set.of("minecraft:cobblestone","minecraft:dirt").containsAll(materials))
            throw new IllegalArgumentException("native_step_materials");
        protectedPositions = new HashSet<>();
        for (var value:policy.getAsJsonArray("protected_positions")) protectedPositions.add(point(value.getAsJsonArray()));
        operations = new ArrayList<>();
        var breaks = plan.getAsJsonArray("breaks"); var places = plan.getAsJsonArray("places");
        if (breaks.size()>integer(policy,"break_budget",0,64) || places.size()>integer(policy,"place_budget",0,64)
                || (breaks.size()>0 && !bool(policy,"allow_break")) || (places.size()>0 && !bool(policy,"allow_place")))
            throw new IllegalArgumentException("native_step_budget");
        int horizontal = Math.abs(source.getX()-target.getX())+Math.abs(source.getZ()-target.getZ());
        int rise=target.getY()-source.getY();
        downward = kind.equals("downward");
        if (downward) {
            // Baritone MovementDownward: break the block under the feet and drop one.
            if (!bool(policy,"allow_break") || horizontal!=0 || rise!=-1 || breaks.size()!=1 || places.size()!=0
                    || !point(breaks.get(0).getAsJsonArray()).equals(source.below())) throw new IllegalArgumentException("native_downward_plan");
        } else if (kind.equals("descend")) {
            // Baritone MovementDescend: clear the lower target's feet, head and headroom first.
            if (horizontal!=1 || rise!=-1 || places.size()!=0 || breaks.size()<1 || breaks.size()>3)
                throw new IllegalArgumentException("native_descend_plan");
            for (var value:breaks) {
                var b=point(value.getAsJsonArray());
                if (b.getX()!=target.getX() || b.getZ()!=target.getZ() || b.getY()<target.getY() || b.getY()>target.getY()+2)
                    throw new IllegalArgumentException("native_descend_plan");
            }
        } else if (kind.equals("pillar")) {
            if (!bool(policy,"allow_pillar") || horizontal!=0 || rise!=1 || exit==null || breaks.size()!=0
                    || places.size()!=1 || !point(places.get(0).getAsJsonArray()).equals(source))
                throw new IllegalArgumentException("native_pillar_plan");
        } else if (!kind.equals("step") || horizontal!=1 || rise<0 || rise>1
                || places.size()>integer(policy,"max_support_stack",1,3)) throw new IllegalArgumentException("native_step_shape");
        var unique=new HashSet<BlockPos>();
        for (var value:breaks) addOperation(false,point(value.getAsJsonArray()),unique);
        for (var value:places) addOperation(true,point(value.getAsJsonArray()),unique);
        if (operations.isEmpty()) throw new IllegalArgumentException("native_step_requires_edits");
        // Highest cell first so each cut exposes the next and nothing is mined through.
        if (kind.equals("descend")) operations.sort(java.util.Comparator.comparingInt((Operation o)->o.point.getY()).reversed());
    }
    private void addOperation(boolean place,BlockPos p,Set<BlockPos> unique) {
        if (!unique.add(p) || protectedPositions.contains(p) || Math.abs(p.getX()-source.getX())>1
                || Math.abs(p.getZ()-source.getZ())>1 || p.getY()<source.getY()-3 || p.getY()>source.getY()+3
                || (!place && !downward && p.equals(source.below()))) throw new IllegalArgumentException("native_step_protected_or_bounds");
        operations.add(new Operation(place,p));
    }
    private static boolean bool(JsonObject p,String key) {
        var v=p.get(key);
        if(v==null || !v.isJsonPrimitive() || !v.getAsJsonPrimitive().isBoolean()) throw new IllegalArgumentException("step_boolean");
        return v.getAsBoolean();
    }
    private static int integer(JsonObject p,String key,int low,int high) {
        var v=p.get(key);
        if(v==null || !v.isJsonPrimitive() || !v.getAsJsonPrimitive().isNumber() || v.getAsDouble()!=v.getAsInt()
                || v.getAsInt()<low || v.getAsInt()>high) throw new IllegalArgumentException("step_integer");
        return v.getAsInt();
    }
    static BlockPos point(JsonArray a) {
        if (a==null || a.size()!=3) throw new IllegalArgumentException("step_point");
        int[] xyz=new int[3];
        for(int i=0;i<3;i++) {
            var v=a.get(i);
            if(!v.isJsonPrimitive() || !v.getAsJsonPrimitive().isNumber() || !Double.isFinite(v.getAsDouble())
                    || v.getAsDouble()!=v.getAsInt() || Math.abs(v.getAsDouble())>30_000_000) throw new IllegalArgumentException("step_point");
            xyz[i]=v.getAsInt();
        }
        if(xyz[1]<-2048 || xyz[1]>2048) throw new IllegalArgumentException("step_height");
        return new BlockPos(xyz[0],xyz[1],xyz[2]);
    }
    private static Set<String> strings(JsonArray array) {
        var result=new LinkedHashSet<String>();
        for(var v:array) {
            if(!v.isJsonPrimitive() || !v.getAsJsonPrimitive().isString()) throw new IllegalArgumentException("step_string");
            result.add(v.getAsString());
        }
        return java.util.Collections.unmodifiableSet(result);
    }
    private static List<Integer> xyz(BlockPos p) { return List.of(p.getX(),p.getY(),p.getZ()); }
    boolean active() { return state.equals("following"); }
    void stop(String why) { state="needs_replan"; reason=why; }
    Map<String,Object> status() {
        return Map.of("step_id",id,"state",state,"reason",reason,"operation",index,"issued",issued,
                "finished_tick",finishedTick,"broken_positions",broken.stream().map(StepMotor::xyz).toList(),
                "placed_positions",placed.entrySet().stream().map(e->Map.of("position",xyz(e.getKey()),"item",e.getValue())).toList());
    }
    static boolean empty(Map<String,Object> c) {
        return c!=null && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty"))
                && c.get("collision_boxes") instanceof List<?> boxes && boxes.isEmpty()
                && Set.of("minecraft:air","minecraft:cave_air","minecraft:void_air").contains(c.get("block"));
    }
    static boolean cube(Map<String,Object> c) {
        return c!=null && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty"))
                && c.get("collision_boxes") instanceof List<?> boxes && boxes.size()==1
                && boxes.get(0) instanceof List<?> b && b.size()==6
                && ((Number)b.get(0)).doubleValue()==0 && ((Number)b.get(1)).doubleValue()==0 && ((Number)b.get(2)).doubleValue()==0
                && ((Number)b.get(3)).doubleValue()==1 && ((Number)b.get(4)).doubleValue()==1 && ((Number)b.get(5)).doubleValue()==1;
    }
    private Map<String,Object> read(RouteGeometry.Reader reader,BlockPos p) { return reader.cell(p.getX(),p.getY(),p.getZ()); }
    private static int slot(ServerPlayer p,String item) {
        for(int n=0;n<9;n++) {
            var stack=p.getInventory().getItem(n);
            if(stack.getCount()>0 && BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals(item)) return n;
        }
        return -1;
    }
    private BodyRuntime.Controls input(int tick,double forward,double yaw,double pitch,boolean jump,boolean sneak,boolean mine,boolean use,int slot) {
        return new BodyRuntime.Controls("native-step",tick,tick,tick+2,2,forward,0,yaw,pitch,jump,sneak,false,mine,false,false,use,slot);
    }
    private BodyRuntime.Controls stopInput(int tick,String why) { stop(why); return input(tick,0,0,0,false,false,false,false,0); }
    private static boolean fallingNear(ServerPlayer p,BlockPos pos) {
        return !p.level().getEntitiesOfClass(FallingBlockEntity.class,new AABB(pos).inflate(.5,0,.5).expandTowards(0,16,0)).isEmpty();
    }
    private static double wrap(double x) { return ((x+180)%360+360)%360-180; }

    BodyRuntime.Controls tick(ServerPlayer p,int tick,RouteGeometry.Reader reader) {
        if(started<0) started=tick;
        if(tick-started>tickLimit) return stopInput(tick,"native_step_deadline");
        double distance=Math.hypot(p.getX()-source.getX()-.5,p.getZ()-source.getZ()-.5);
        boolean pillar=kind.equals("pillar");
        if(distance>(pillar?.45:.85) || p.getY()<(downward?target.getY():source.getY())-.2 || p.getY()>source.getY()+(pillar?1.4:.2))
            return stopInput(tick,"native_step_displaced");
        var geometry=new RouteGeometry(reader);
        for(var cut:issuedBreaks) if(empty(read(reader,cut))) broken.add(cut);
        if(pillar && !geometry.clear(p.getX(),p.getZ(),p.getY(),p.getY()+1.8+(launched?0:1.3)))
            return stopInput(tick,"native_pillar_headroom_changed");
        if(pillar && (exit==null || !geometry.standable(new RouteMotor.Point(exit.getX(),exit.getY(),exit.getZ()))))
            return stopInput(tick,"native_pillar_exit_changed");
        if(index>=operations.size()) {
            if(downward) {
                if(!geometry.standable(new RouteMotor.Point(target.getX(),target.getY(),target.getZ())))
                    return stopInput(tick,"native_downward_landing_changed");
                if(!p.onGround() || Math.abs(p.getY()-target.getY())>.1) {
                    // An off-centre body still stands on the neighbour's edge after the cut
                    // (cohort 15 R1 rep 2: 99 ticks at x-.48). Walk over the hole, as
                    // Baritone's MovementDownward centres the player.
                    double cx=target.getX()+.5-p.getX(),cz=target.getZ()+.5-p.getZ();
                    if(!p.onGround() || Math.hypot(cx,cz)<.15) return input(tick,0,0,0,false,false,false,false,0);
                    double turn=wrap(Math.toDegrees(Math.atan2(-cx,cz))-p.getYRot());
                    return input(tick,Math.abs(turn)<30?.5:0,Math.max(-45,Math.min(45,turn)),0,false,false,false,false,0);
                }
            }
            if(pillar && (!p.onGround() || Math.abs(p.getY()-target.getY())>.1)) return input(tick,0,0,0,false,false,false,false,0);
            state="edits_ready"; finishedTick=tick;
            return input(tick,0,0,0,false,false,false,false,0);
        }
        var op=operations.get(index); var cell=read(reader,op.point);
        if(cell==null) return stopInput(tick,"native_edit_geometry_unknown");
        if(protectedBlocks.contains(cell.get("block"))) return stopInput(tick,"native_edit_protected");
        if(!op.place && empty(cell) && settleWatch.contains(op.point)) {
            settleUntil=fallingNear(p,op.point)||settleUntil<0 ? tick+SETTLE_TICKS : settleUntil;
            if(tick<settleUntil) return input(tick,0,0,0,false,false,false,false,0);
            settleUntil=-1; settleWatch.remove(op.point);
        } else if(!empty(cell)) settleUntil=-1;
        boolean complete=op.place?cube(cell):empty(cell);
        if(complete) {
            if(op.place) {
                if(!issued || !placedItem.equals(cell.get("block"))) return stopInput(tick,"native_placement_changed");
                placed.put(op.point,placedItem);
            } else if(issuedBreaks.contains(op.point)) broken.add(op.point);
            index++; issued=false; placedItem=""; issueTick=-1;
            return input(tick,0,0,0,false,false,false,false,0);
        }
        if(issued && op.place) {
            if(tick-issueTick>15) return stopInput(tick,"native_placement_not_observed");
            return input(tick,0,0,0,false,!pillar,false,false,0);
        }
        if(!pillar && (!p.onGround() || !geometry.standable(new RouteMotor.Point(source.getX(),source.getY(),source.getZ()))))
            return stopInput(tick,"native_edit_stance_changed");
        Vec3 aim; BlockPos expected=op.point; String face=null; int selected;
        boolean sneak=false;
        if(!op.place) {
            if(!cube(cell) || !TOOLS.containsKey(cell.get("block"))) return stopInput(tick,"native_cut_not_permitted");
            if(downward && !cube(read(reader,target.below()))) return stopInput(tick,"native_downward_landing_changed");
            for(var direction:List.of(net.minecraft.core.Direction.UP,net.minecraft.core.Direction.NORTH,
                    net.minecraft.core.Direction.SOUTH,net.minecraft.core.Direction.EAST,net.minecraft.core.Direction.WEST)) {
                var neighbor=read(reader,op.point.relative(direction));
                if(neighbor==null || !"minecraft:empty".equals(neighbor.getOrDefault("fluid","minecraft:empty")))
                    return stopInput(tick,"native_cut_neighbor_unsafe");
                if(FALLING.contains(neighbor.get("block"))) {
                    // Sand/gravel straight above may be cut under (Baritone prices the stack);
                    // it then falls into the cell, which is waited out and mined again.
                    // A side one only falls if its own support goes: allowed while that is solid
                    // (matches the planner's cut_allowed and Baritone avoidBreaking).
                    if(direction!=net.minecraft.core.Direction.UP) {
                        if(!cube(read(reader,op.point.relative(direction).below())))
                            return stopInput(tick,"native_cut_neighbor_unsafe");
                    } else settleWatch.add(op.point);
                }
            }
            selected=slot(p,TOOLS.get(cell.get("block"))); aim=Vec3.atCenterOf(op.point);
        } else {
            if(!empty(cell)) return stopInput(tick,"native_placement_not_empty");
            selected=-1; int stock=0;
            for(String material:materials) {
                for(int n=0;n<9;n++) {
                    var stack=p.getInventory().getItem(n);
                    if(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals(material)) {
                        stock+=stack.getCount(); if(selected<0 && stack.getCount()>0) selected=n;
                    }
                }
            }
            if(stock<=reserve) return stopInput(tick,"native_material_reserve");
            expected=op.point.below(); face="UP";
            int dx=op.point.getX()-source.getX(),dz=op.point.getZ()-source.getZ();
            double progress=(p.getX()-source.getX()-.5)*dx+(p.getZ()-source.getZ()-.5)*dz;
            aim=new Vec3(op.point.getX()+.5,op.point.getY()-.001,op.point.getZ()+.5);
            boolean below=cube(read(reader,expected));
            if(!below) {
                expected=source.below();
                if(!bridge || op.point.getY()!=source.getY()-1 || Math.abs(dx)+Math.abs(dz)!=1
                        || !empty(read(reader,op.point.below())) || !cube(read(reader,expected)))
                    return stopInput(tick,"native_placement_anchor_changed");
                face=dx==1?"EAST":dx==-1?"WEST":dz==1?"SOUTH":"NORTH";
                aim=new Vec3(source.getX()+.5+dx*.499,op.point.getY()+.5,source.getZ()+.5+dz*.499);
            }
            // Full input while sneaking: sneak alone already slows to 30% and the
            // vanilla edge clamp keeps the body on the block. 0.3 input on top cost
            // ~30 ticks per placement (Step C run 20261030T023556227338Z: median 39).
            boolean approach=!pillar && op.point.getY()<source.getY() && Math.abs(dx)+Math.abs(dz)==1;
            if(approach && progress<.6) {
                double turn=wrap(Math.toDegrees(Math.atan2(-dx,dz))-p.getYRot());
                return input(tick,Math.abs(turn)<8?1:0,Math.max(-45,Math.min(45,turn)),0,false,true,false,false,Math.max(0,selected));
            }
            if(!pillar && op.point.getY()>=source.getY() && progress>.1) {
                double turn=wrap(Math.toDegrees(Math.atan2(-(source.getX()+.5-p.getX()),source.getZ()+.5-p.getZ()))-p.getYRot());
                return input(tick,Math.abs(turn)<8?1:0,Math.max(-45,Math.min(45,turn)),0,false,true,false,false,Math.max(0,selected));
            }
            sneak=approach;
        }
        if(selected<0) return stopInput(tick,"native_hotbar_supply_missing");
        Vec3 eye=p.getEyePosition();
        if(eye.distanceTo(aim)>4.2) return stopInput(tick,"native_edit_out_of_reach");
        double dx=aim.x-eye.x,dz=aim.z-eye.z;
        double yaw=wrap(Math.toDegrees(Math.atan2(-dx,dz))-p.getYRot());
        double pitch=-Math.toDegrees(Math.atan2(aim.y-eye.y,Math.hypot(dx,dz)))-p.getXRot();
        boolean aligned=Math.abs(yaw)<2 && Math.abs(pitch)<2;
        // The ordinary ray must not force-load any intervening chunk either.
        var rayEnd=eye.add(p.getViewVector(1).scale(4.2));
        for(int cx=Math.min((int)Math.floor(eye.x)>>4,(int)Math.floor(rayEnd.x)>>4);
                cx<=Math.max((int)Math.floor(eye.x)>>4,(int)Math.floor(rayEnd.x)>>4);cx++)
            for(int cz=Math.min((int)Math.floor(eye.z)>>4,(int)Math.floor(rayEnd.z)>>4);
                    cz<=Math.max((int)Math.floor(eye.z)>>4,(int)Math.floor(rayEnd.z)>>4);cz++)
                if(p.level().getChunkSource().getChunkNow(cx,cz)==null) return stopInput(tick,"native_ray_chunk_unavailable");
        var ray=p.pick(4.2,1,false);
        boolean hit=ray.getType()==HitResult.Type.BLOCK && ray instanceof BlockHitResult block
                && block.getBlockPos().equals(expected) && (face==null || block.getDirection().name().equals(face));
        BlockPos miningPoint=op.point;
        if(!op.place && aligned && ray instanceof BlockHitResult block && ray.getType()==HitResult.Type.BLOCK
                && operations.stream().anyMatch(candidate->!candidate.place && candidate.point.equals(block.getBlockPos()))) {
            var occluder=read(reader,block.getBlockPos());
            if(cube(occluder) && TOOLS.containsKey(occluder.get("block")) && !protectedBlocks.contains(occluder.get("block"))) {
                boolean safe=true;
                for(var direction:List.of(net.minecraft.core.Direction.UP,net.minecraft.core.Direction.NORTH,
                        net.minecraft.core.Direction.SOUTH,net.minecraft.core.Direction.EAST,net.minecraft.core.Direction.WEST)) {
                    var neighbor=read(reader,block.getBlockPos().relative(direction));
                    if(neighbor==null || !"minecraft:empty".equals(neighbor.getOrDefault("fluid","minecraft:empty"))
                            || FALLING.contains(neighbor.get("block"))) safe=false;
                }
                if(safe) { miningPoint=block.getBlockPos(); selected=slot(p,TOOLS.get(occluder.get("block"))); hit=selected>=0; }
            }
        }
        boolean jump=false;
        if(pillar) {
            if(!launched && p.onGround() && aligned && hit && Math.hypot(p.getDeltaMovement().x,p.getDeltaMovement().z)<.03) {
                jump=true; launched=true;
            }
            if(p.getY()<source.getY()+1 || p.getDeltaMovement().y<.01) hit=false;
        }
        boolean mine=!op.place && aligned && hit;
        if(mine) issuedBreaks.add(miningPoint);
        boolean use=op.place && aligned && hit;
        if(mine || use) { issued=true; issueTick=tick; }
        if(use) placedItem=BuiltInRegistries.ITEM.getKey(p.getInventory().getItem(selected).getItem()).toString();
        return input(tick,0,Math.max(-45,Math.min(45,yaw)),Math.max(-45,Math.min(45,pitch)),jump,sneak,mine,use,selected);
    }
}
