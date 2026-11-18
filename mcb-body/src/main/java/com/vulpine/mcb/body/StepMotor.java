package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.item.FallingBlockEntity;
import net.minecraft.world.level.ClipContext;
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
    /** Non-colliding ground cover the body clears by hand when it blocks the edit ray. */
    private static final Set<String> COVER = Set.of("minecraft:short_grass","minecraft:tall_grass",
            "minecraft:fern","minecraft:large_fern","minecraft:dead_bush","minecraft:vine",
            "minecraft:glow_lichen","minecraft:bush","minecraft:firefly_bush","minecraft:leaf_litter",
            "minecraft:wildflowers","minecraft:pink_petals");
    private static final Set<String> PLANTS = Set.of("minecraft:short_grass","minecraft:tall_grass",
            "minecraft:fern","minecraft:large_fern","minecraft:dead_bush","minecraft:dandelion","minecraft:poppy",
            "minecraft:blue_orchid","minecraft:allium","minecraft:azure_bluet","minecraft:red_tulip",
            "minecraft:orange_tulip","minecraft:white_tulip","minecraft:pink_tulip","minecraft:oxeye_daisy",
            "minecraft:cornflower","minecraft:lily_of_the_valley","minecraft:lilac","minecraft:rose_bush",
            "minecraft:peony","minecraft:sunflower","minecraft:sweet_berry_bush");
    private static final Map<String,String> TOOLS = Map.ofEntries(
            Map.entry("minecraft:oak_planks","minecraft:iron_axe"),
            Map.entry("minecraft:spruce_planks","minecraft:iron_axe"),
            Map.entry("minecraft:birch_planks","minecraft:iron_axe"),
            Map.entry("minecraft:jungle_planks","minecraft:iron_axe"),
            Map.entry("minecraft:acacia_planks","minecraft:iron_axe"),
            Map.entry("minecraft:dark_oak_planks","minecraft:iron_axe"),
            Map.entry("minecraft:mangrove_planks","minecraft:iron_axe"),
            Map.entry("minecraft:cherry_planks","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_oak_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_spruce_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_birch_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_jungle_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_acacia_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_dark_oak_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_mangrove_log","minecraft:iron_axe"),
            Map.entry("minecraft:stripped_cherry_log","minecraft:iron_axe"),
            Map.entry("minecraft:stone_bricks","minecraft:iron_pickaxe"),
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
    /** The exit's feet height: a stair top or slab is a half step (76.5), not a block. */
    private final double exitY;
    private final List<Operation> operations;
    private final Set<BlockPos> protectedPositions;
    private final Set<String> protectedBlocks, materials;
    // Cells the planner chose scaffolding for (it stays up: supported within 6 of the ground); the rest are solid.
    private final Set<BlockPos> scaffoldCells = new HashSet<>();
    private final Map<String,String> tools;
    private final boolean bridge, downward;
    private final int reserve, tickLimit;
    // Baritone Settings.pauseMiningForFallingBlocks: wait this long after a cut under a
    // falling-block stack, and while any falling block is near, before trusting the cell.
    private static final int SETTLE_TICKS = 6;
    private final Set<BlockPos> settleWatch = new HashSet<>();
    private int settleUntil = -1;
    private int index, started = -1, issueTick = -1, finishedTick = -1;
    private boolean issued, launched;
    private int blind, approachTick = -1, deadTicks, handTicks;
    private final Set<BlockPos> handCells = new HashSet<>();
    private double approachProgress;
    private Vec3 actAnchor;
    private String state = "following", reason = "", placedItem = "";
    private final Set<BlockPos> broken = new HashSet<>();
    private final Set<BlockPos> issuedBreaks = new HashSet<>();
    private final Map<BlockPos,String> placed = new HashMap<>();

    /** Cells whose break was issued (LeakPlug watches them for a fluid). */
    Set<BlockPos> issuedBreaks() { return issuedBreaks; }

    StepMotor(JsonObject request) {
        id = request.get("step_id").getAsString();
        if (!id.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("step_id");
        var plan = request.getAsJsonObject("plan");
        var policy = request.getAsJsonObject("terrain_policy");
        source = point(plan.getAsJsonArray("source")); target = point(plan.getAsJsonArray("target"));
        kind = plan.get("kind").getAsString();
        // The exit may stand on a stair top or slab (y 76.5): a run stalled on the roof with
        // every pillar refused "step_point" (run 20261118T125548315616Z).
        var exitArray = plan.has("exit") && !plan.get("exit").isJsonNull() ? plan.getAsJsonArray("exit") : null;
        exitY = exitArray == null ? 0 : feetHeight(exitArray);
        exit = exitArray == null ? null : floorPoint(exitArray);
        bridge = bool(policy,"allow_bridge"); reserve = integer(policy,"reserve_blocks",0,128);
        tickLimit = integer(policy,"interaction_tick_limit",1,1000);
        protectedBlocks = strings(policy.getAsJsonArray("protected_blocks"));
        // Python's tool_choice: block -> the exact hotbar item to cut it with (optional).
        tools = new HashMap<>();
        if (request.has("tools") && request.get("tools").isJsonObject())
            for (var e:request.getAsJsonObject("tools").entrySet())
                if (e.getValue().isJsonPrimitive()) tools.put(e.getKey(),e.getValue().getAsString());
        materials = strings(policy.getAsJsonArray("material_allowlist"));
        if (materials.stream().anyMatch(m -> m.indexOf(':') <= 0))
            throw new IllegalArgumentException("native_step_materials");
        if (plan.has("scaffold") && plan.get("scaffold").isJsonArray())
            for (var value:plan.getAsJsonArray("scaffold")) scaffoldCells.add(point(value.getAsJsonArray()));
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
            // Baritone MovementDescend: clear the lower target's feet, head and the
            // two cells above (the fourth keeps the +1 ascent edge legal on return).
            if (horizontal!=1 || rise!=-1 || places.size()!=0 || breaks.size()<1 || breaks.size()>4)
                throw new IllegalArgumentException("native_descend_plan");
            for (var value:breaks) {
                var b=point(value.getAsJsonArray());
                if (b.getX()!=target.getX() || b.getZ()!=target.getZ() || b.getY()<target.getY() || b.getY()>target.getY()+3)
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
        // No solid block allowed: every placed cell must be one the planner made scaffolding
        // (a body holding only scaffolding builds its whole tower of it).
        if (materials.isEmpty() && operations.stream().anyMatch(o -> o.place && !scaffoldCells.contains(o.point)))
            throw new IllegalArgumentException("native_step_materials");
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
    /** A stance point: whole x and z, feet y on any half or finer step of a block. */
    static double feetHeight(JsonArray a) {
        if (a==null || a.size()!=3) throw new IllegalArgumentException("step_point");
        var v=a.get(1);
        if(!v.isJsonPrimitive() || !v.getAsJsonPrimitive().isNumber() || !Double.isFinite(v.getAsDouble()))
            throw new IllegalArgumentException("step_point");
        double y=v.getAsDouble();
        if(y<-2048 || y>2048) throw new IllegalArgumentException("step_height");
        return y;
    }
    static BlockPos floorPoint(JsonArray a) {
        var cell=new JsonArray();
        cell.add(a.get(0)); cell.add(Math.floor(feetHeight(a))); cell.add(a.get(2));
        return point(cell);
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
    /** Ground cover (short grass, ferns, vines) replaced directly by placing a block onto it. */
    static boolean replaceableCover(Map<String,Object> c) {
        return c!=null && !empty(c) && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty"))
                && c.get("collision_boxes") instanceof List<?> boxes && boxes.isEmpty()
                && !Set.of("minecraft:air","minecraft:cave_air","minecraft:void_air").contains(c.get("block"))
                && (Boolean.TRUE.equals(c.get("replaceable")) || (c.get("replaceable")==null && COVER.contains(c.get("block"))));
    }
    /** Non-replaceable flower/bush that must be punched first before placing a block in its cell. */
    static boolean flower(Map<String,Object> c) {
        return c!=null && !empty(c) && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty"))
                && c.get("collision_boxes") instanceof List<?> boxes && boxes.isEmpty()
                && !Set.of("minecraft:air","minecraft:cave_air","minecraft:void_air").contains(c.get("block"))
                && !replaceableCover(c) && PLANTS.contains(c.get("block"));
    }
    /** Any non-air, non-colliding plant/cover cell (replaceable cover or flower). */
    static boolean replaceable(Map<String,Object> c) { return replaceableCover(c) || flower(c); }
    static boolean placeHit(BlockPos hitPos, String hitFace, BlockPos point, BlockPos expected, String face, boolean directReplace) {
        return (hitPos.equals(expected) && (face==null || hitFace.equals(face)))
                || (directReplace && hitPos.equals(point));
    }
    private static final String SCAFFOLDING="minecraft:scaffolding";
    /** Scaffolding senses as empty collision, yet it is placed on and stood on. */
    static boolean scaffolding(Map<String,Object> c) { return c!=null && SCAFFOLDING.equals(c.get("block")); }
    /** Something to click a block onto: a full cube or scaffolding (supported: the planner only counts it so). */
    static boolean sturdy(Map<String,Object> c) { return cube(c) || scaffolding(c); }
    /** Sneaking in or on scaffolding drops the body through it. */
    private boolean inScaffold(RouteGeometry.Reader reader) {
        return scaffolding(read(reader,source)) || scaffolding(read(reader,source.below()));
    }
    /** A full cube beside the cell to click its face, north/south/east/west. */
    private BlockPos sideAnchor(RouteGeometry.Reader reader, BlockPos point) {
        for(var d:List.of(net.minecraft.core.Direction.NORTH,net.minecraft.core.Direction.SOUTH,
                net.minecraft.core.Direction.EAST,net.minecraft.core.Direction.WEST)) {
            var n=point.relative(d);
            if(cube(read(reader,n))) return n;
        }
        return null;
    }
    static boolean cube(Map<String,Object> c) {
        return c!=null && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty"))
                && c.get("collision_boxes") instanceof List<?> boxes && boxes.size()==1
                && boxes.get(0) instanceof List<?> b && b.size()==6
                && ((Number)b.get(0)).doubleValue()==0 && ((Number)b.get(1)).doubleValue()==0 && ((Number)b.get(2)).doubleValue()==0
                && ((Number)b.get(3)).doubleValue()==1 && ((Number)b.get(4)).doubleValue()==1 && ((Number)b.get(5)).doubleValue()==1;
    }
    private Map<String,Object> read(RouteGeometry.Reader reader,BlockPos p) { return reader.cell(p.getX(),p.getY(),p.getZ()); }
    /** Tiers tried, best first, when the table names an iron tool (terrain_steps.hotbar). */
    private static final List<String> TOOL_TIERS = List.of("netherite","diamond","iron","stone","golden","wooden");

    /** Hotbar slot of item; for an iron tool, any tier of that tool (a survival body digs with wood). */
    private static int slot(ServerPlayer p,String item) {
        int exact=exactSlot(p,item);
        if(exact>=0 || item==null || !item.startsWith("minecraft:iron_")) return exact;
        String kind=item.substring("minecraft:iron_".length());
        if(!List.of("pickaxe","shovel","axe","hoe").contains(kind)) return -1;
        for(var tier:TOOL_TIERS) {
            int n=exactSlot(p,"minecraft:"+tier+"_"+kind);
            if(n>=0) return n;
        }
        return -1;
    }
    /**
     * The tool's slot, else an empty hand: with every tool broken the body still digs
     * (dirt, leaves, and stone too, slowly and without drops) rather than freezing.
     * The step deadline grows once per hand-dug cell by its vanilla bare-hand time:
     * hardness x 100 ticks when the block needs a tool for drops, else x 30.
     */
    private int breakSlot(ServerPlayer p,BlockPos pos,String block) {
        int n=exactSlot(p,tools.get(block));
        if(n<0) n=slot(p,TOOLS.get(block));
        if(n>=0) return n;
        if(handCells.add(pos)) {
            var s=p.level().getBlockState(pos);
            handTicks+=(int)Math.ceil(Math.max(0,s.getDestroySpeed(p.level(),pos))*(s.requiresCorrectToolForDrops()?100:30));
        }
        return BreakTask.emptyHotbarSlot(p);
    }
    /** Hotbar slot of item, brought up from the main inventory when only held there. */
    private static int exactSlot(ServerPlayer p,String item) {
        for(int n=0;n<36;n++) {  // hotbar first
            var stack=p.getInventory().getItem(n);
            if(stack.getCount()>0 && BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals(item))
                return BodyActions.toHotbar(p,n,-1);
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
    /** Where to look to cut a cell: its centre if in view, else the first face in view (Baritone
     *  RotationUtils.reachable). Live run 20261112T181731902927Z: a tunnel ceiling hid the centre of
     *  the top descend cut and the step stood aligned for 116 ticks without mining. */
    static Vec3 aim(BlockPos cell,java.util.function.Predicate<Vec3> seen) {
        Vec3 centre=Vec3.atCenterOf(cell);
        if(seen.test(centre)) return centre;
        for(var d:net.minecraft.core.Direction.values()) {
            Vec3 face=centre.add(d.getStepX()*.45,d.getStepY()*.45,d.getStepZ()*.45);
            if(seen.test(face)) return face;
        }
        return centre;
    }
    private static double wrap(double x) { return ((x+180)%360+360)%360-180; }

    BodyRuntime.Controls tick(ServerPlayer p,int tick,RouteGeometry.Reader reader) {
        if(started<0) started=tick;
        if(tick-started>tickLimit+handTicks) return stopInput(tick,"native_step_deadline");
        double distance=Math.hypot(p.getX()-source.getX()-.5,p.getZ()-source.getZ()-.5);
        boolean pillar=kind.equals("pillar");
        if(distance>(pillar?.45:.85) || p.getY()<(downward?target.getY():source.getY())-.2 || p.getY()>source.getY()+(pillar?1.4:.2))
            return stopInput(tick,"native_step_displaced");
        var geometry=new RouteGeometry(reader);
        // A step that neither moves the body nor works toward an edit is dead water:
        // the placement-aim branch can spin on a non-converging rotation with
        // issued=false forever (iron run 20261113T022026552455Z: a same-level
        // foothold held the drive 5 s to Python's idle cutoff; the vertical variant
        // held one 81.2 s, 20261112T233253015689Z). Movement or an issued edit
        // zeroes the timer; 60 accumulated dead ticks (3 s, with slack over any
        // 6-tick settle wait) fail the edge so the replanner bans it - below the
        // five-second rule without ever touching a step that is doing something.
        if(issued){ deadTicks=0; actAnchor=null; }
        else if(index<operations.size()){
            Vec3 nowPos=p.position();
            if(actAnchor==null || nowPos.distanceToSqr(actAnchor)>=0.05*0.05) { actAnchor=nowPos; deadTicks=0; }
            else if(++deadTicks>60) return stopInput(tick,"native_step_no_progress");
        } else { deadTicks=0; actAnchor=null; }
        for(var cut:issuedBreaks) if(empty(read(reader,cut))) broken.add(cut);
        if(pillar && !geometry.clear(p.getX(),p.getZ(),p.getY(),p.getY()+1.8+(launched?0:1.3)))
            return stopInput(tick,"native_pillar_headroom_changed");
        // A pillar whose next move is another pillar (a tower going on up) exits straight above:
        // that cell is only standable once the next block is under it, so it need only be clear.
        // A tower's top block stays on its own top: the exit is the target itself.
        boolean climbOn=pillar && exit!=null && exit.getX()==target.getX() && exit.getZ()==target.getZ()
                && (exit.getY()==target.getY()+1 || exit.getY()==target.getY());
        if(pillar && (exit==null || !(climbOn
                ? geometry.clear(exit.getX()+.5,exit.getZ()+.5,exit.getY(),exit.getY()+1.8)
                : geometry.standable(new RouteMotor.Point(exit.getX(),exitY,exit.getZ())))))
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
        boolean complete=op.place?(scaffoldCells.contains(op.point)?scaffolding(cell):cube(cell)):empty(cell);
        if(complete) {
            if(op.place) {
                if(!issued || !placedItem.equals(cell.get("block"))) return stopInput(tick,"native_placement_changed");
                placed.put(op.point,placedItem);
            } else if(issuedBreaks.contains(op.point)) broken.add(op.point);
            index++; issued=false; placedItem=""; issueTick=-1; approachTick=-1;
            return input(tick,0,0,0,false,false,false,false,0);
        }
        if(issued && op.place) {
            if(tick-issueTick>15) return stopInput(tick,"native_placement_not_observed");
            return input(tick,0,0,0,false,!pillar && !inScaffold(reader),false,false,0);
        }
        // In a scaffolding cell (a hanging one's bottom plate, or a column's lower top) the body stands as on a floor.
        boolean sourceOk=geometry.standable(new RouteMotor.Point(source.getX(),source.getY(),source.getZ()))
                || scaffolding(read(reader,source))
                || (!op.place && cube(read(reader,source.below())) && (op.point.equals(source) || op.point.equals(source.above())));
        if(!pillar && (!p.onGround() || !sourceOk))
            return stopInput(tick,"native_edit_stance_changed");
        Vec3 aim; BlockPos expected=op.point; String face=null; int selected;
        boolean sneak=false, directReplace=false, stackOnScaffold=false;
        if(!op.place) {
            if(!cube(cell) || !TOOLS.containsKey(cell.get("block"))) return stopInput(tick,"native_cut_not_permitted");
            if(downward && !cube(read(reader,target.below()))) return stopInput(tick,"native_downward_landing_changed");
            for(var direction:List.of(net.minecraft.core.Direction.UP,net.minecraft.core.Direction.NORTH,
                    net.minecraft.core.Direction.SOUTH,net.minecraft.core.Direction.EAST,net.minecraft.core.Direction.WEST)) {
                var neighbor=read(reader,op.point.relative(direction));
                if(neighbor==null || !"minecraft:empty".equals(neighbor.getOrDefault("fluid","minecraft:empty")))
                    return stopInput(tick,"native_cut_neighbor_unsafe");
                // Sand/gravel above falls into the cut: wait it out and mine again. Beside: no hazard (owner ruling).
                if(direction==net.minecraft.core.Direction.UP && FALLING.contains(neighbor.get("block"))) settleWatch.add(op.point);
            }
            selected=breakSlot(p,op.point,(String)cell.get("block"));
            // Rays end inside the cut cell, so they cross only the columns between eye and cell.
            Vec3 from=p.getEyePosition(); BlockPos cut=op.point;
            for(int cx=Math.min((int)Math.floor(from.x),cut.getX())>>4;cx<=Math.max((int)Math.floor(from.x),cut.getX())>>4;cx++)
                for(int cz=Math.min((int)Math.floor(from.z),cut.getZ())>>4;cz<=Math.max((int)Math.floor(from.z),cut.getZ())>>4;cz++)
                    if(p.level().getChunkSource().getChunkNow(cx,cz)==null) return stopInput(tick,"native_ray_chunk_unavailable");
            aim=aim(cut,v->{
                var seen=p.level().clip(new ClipContext(from,v,ClipContext.Block.OUTLINE,ClipContext.Fluid.NONE,p));
                return seen.getType()==HitResult.Type.BLOCK && seen.getBlockPos().equals(cut);
            });
        } else {
            // Flowers are non-replaceable and block placement: punch first (instant), then place next tick.
            if(flower(cell)) {
                if(protectedBlocks.contains(cell.get("block"))) return stopInput(tick,"native_edit_protected");
                Vec3 e=p.getEyePosition(),c=Vec3.atCenterOf(op.point);
                double py=wrap(Math.toDegrees(Math.atan2(-(c.x-e.x),c.z-e.z))-p.getYRot());
                double pp=-Math.toDegrees(Math.atan2(c.y-e.y,Math.hypot(c.x-e.x,c.z-e.z)))-p.getXRot();
                boolean on=Math.abs(py)<2 && Math.abs(pp)<2;
                return input(tick,0,Math.max(-45,Math.min(45,py)),Math.max(-45,Math.min(45,pp)),false,false,on,false,BreakTask.emptyHotbarSlot(p));
            }
            directReplace=replaceableCover(cell);
            if(!empty(cell) && !directReplace) return stopInput(tick,"native_placement_not_empty");
            selected=-1; int stock=0;
            boolean scaffold=scaffoldCells.contains(op.point);
            for(String material:scaffold?Set.of(SCAFFOLDING):materials) {
                for(int n=0;n<36;n++) {  // all of the inventory, hotbar first
                    var stack=p.getInventory().getItem(n);
                    if(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals(material)) {
                        stock+=stack.getCount(); if(selected<0 && stack.getCount()>0) selected=n;
                    }
                }
            }
            if(stock<=(scaffold?0:reserve)) return stopInput(tick,"native_material_reserve");
            if(selected>=9) selected=BodyActions.toHotbar(p,selected,-1);
            expected=op.point.below(); face="UP";
            int dx=op.point.getX()-source.getX(),dz=op.point.getZ()-source.getZ();
            double progress=(p.getX()-source.getX()-.5)*dx+(p.getZ()-source.getZ()-.5)*dz;
            aim=directReplace
                    ? new Vec3(op.point.getX()+.5,op.point.getY()+.05,op.point.getZ()+.5)
                    : new Vec3(op.point.getX()+.5,op.point.getY()-.001,op.point.getZ()+.5);
            boolean below=sturdy(read(reader,expected)), redirect=false;
            // ScaffoldingBlockItem sends a plain click on scaffolding sideways (top face) or up (side face). A sneaking
            // click places right on the clicked face, so a column is stacked with the use sneaking (vanilla: the
            // secondary use). Not while standing on a scaffolding floor: sneaking there drops the body through it.
            stackOnScaffold=below && scaffold && scaffolding(read(reader,expected));
            if(stackOnScaffold && !pillar && scaffolding(read(reader,source.below())))
                return stopInput(tick,"native_scaffold_redirect");
            BlockPos side=null;
            if(!below) {
                expected=source.below();
                boolean fromSource=bridge && op.point.getY()==source.getY()-1 && Math.abs(dx)+Math.abs(dz)==1
                        && (empty(read(reader,op.point.below())) || replaceable(read(reader,op.point.below())))
                        && sturdy(read(reader,expected));
                // Else any full cube beside the cell (a wall, a log): RoutePlanner.anchored, terrain_placement.
                if(!fromSource && bridge) side=sideAnchor(reader,op.point);
                if(!fromSource && side==null) return stopInput(tick,"native_placement_anchor_changed");
            }
            if(side!=null) {
                int sx=op.point.getX()-side.getX(),sz=op.point.getZ()-side.getZ();
                expected=side;
                face=sx==1?"EAST":sx==-1?"WEST":sz==1?"SOUTH":"NORTH";
                aim=new Vec3(side.getX()+.5+sx*.499,side.getY()+.5,side.getZ()+.5+sz*.499);
            } else if(!below) {
                face=dx==1?"EAST":dx==-1?"WEST":dz==1?"SOUTH":"NORTH";
                aim=new Vec3(source.getX()+.5+dx*.499,op.point.getY()+.5,source.getZ()+.5+dz*.499);
                if(scaffold && scaffolding(read(reader,expected))) {
                    // Off a scaffolding floor: click its top face, not sneaking; the item extends it the way the body faces.
                    face="UP"; redirect=true;
                    aim=new Vec3(source.getX()+.5+dx*.3,source.getY()-.001,source.getZ()+.5+dz*.3);
                }
            }
            // Full input while sneaking: sneak alone already slows to 30% and the
            // vanilla edge clamp keeps the body on the block. 0.3 input on top cost
            // ~30 ticks per placement (Step C run 20261030T023556227338Z: median 39).
            // Sneaking on scaffolding drops the body through it: never sneak on a scaffolding floor.
            boolean sneakOk=!inScaffold(reader);
            boolean approach=!pillar && !redirect && side==null && op.point.getY()<source.getY() && Math.abs(dx)+Math.abs(dz)==1;
            if(approach && progress<.6) {
                // The sneak edge clamp pins the centre at the support's rim, so a
                // stance whose neighbour is empty below can never cross progress .6:
                // the approach looped forever, issued=false, reason="" (iron run
                // 20261112T233253015689Z: one step re-issued 13 times over 81.2 s).
                // A stalling approach is a failed edge: report it, let the replanner
                // ban it and route around.
                if(progress>approachProgress+.02) { approachProgress=progress; approachTick=tick; }
                else if(approachTick<0) approachTick=tick;
                if(!issued && tick-approachTick>60) return stopInput(tick,"native_placement_no_progress");
                double turn=wrap(Math.toDegrees(Math.atan2(-dx,dz))-p.getYRot());
                return input(tick,Math.abs(turn)<8?1:0,Math.max(-45,Math.min(45,turn)),0,false,sneakOk,false,false,Math.max(0,selected));
            }
            if(!pillar && op.point.getY()>=source.getY() && progress>.1) {
                double turn=wrap(Math.toDegrees(Math.atan2(-(source.getX()+.5-p.getX()),source.getZ()+.5-p.getZ()))-p.getYRot());
                return input(tick,Math.abs(turn)<8?1:0,Math.max(-45,Math.min(45,turn)),0,false,sneakOk,false,false,Math.max(0,selected));
            }
            sneak=approach && sneakOk;
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
                && placeHit(block.getBlockPos(),block.getDirection().name(),op.point,expected,face,op.place && directReplace);
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
                            || (direction==net.minecraft.core.Direction.UP && FALLING.contains(neighbor.get("block")))) safe=false;
                }
                if(safe) { miningPoint=block.getBlockPos(); selected=breakSlot(p,miningPoint,(String)occluder.get("block")); hit=true; }
            }
        }
        // A plant (short grass, a flower) in the stance or target line is hit by the ray before the
        // cell being cut, and only the edit target counted as a hit: aligned for the whole step, never
        // mining, then native_step_deadline (natural run 20261109T232225692032Z: 5 steps x 160 ticks).
        // Such a plant is broken first; it is not part of the plan's edit list.
        boolean plant=false;
        if(!op.place && aligned && !hit && ray instanceof BlockHitResult cover && ray.getType()==HitResult.Type.BLOCK) {
            var plantCell=read(reader,cover.getBlockPos());
            if(plantCell!=null && (PLANTS.contains(plantCell.get("block")) || replaceableCover(plantCell))
                    && "minecraft:empty".equals(plantCell.getOrDefault("fluid","minecraft:empty"))
                    && !protectedBlocks.contains(plantCell.get("block"))) {
                miningPoint=cover.getBlockPos(); hit=true; plant=true;
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
        // Looking straight at the cut and something else is in the way: fail the edge now, not at
        // the 160-tick deadline (the five-second standstill rule).
        if(!op.place && aligned && !mine) { if(++blind>30) return stopInput(tick,"native_edit_occluded"); } else blind=0;
        if(mine && !plant) issuedBreaks.add(miningPoint);
        boolean use=op.place && aligned && hit;
        if((mine && !plant) || use) { issued=true; issueTick=tick; }
        if(use) placedItem=BuiltInRegistries.ITEM.getKey(p.getInventory().getItem(selected).getItem()).toString();
        if(use && stackOnScaffold) sneak=true;  // only on the click tick: the body is in the air (pillar) or on solid ground
        return input(tick,0,Math.max(-45,Math.min(45,yaw)),Math.max(-45,Math.min(45,pitch)),jump,sneak,mine,use,selected);
    }
}
