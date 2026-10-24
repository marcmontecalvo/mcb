package com.vulpine.mclab;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import java.util.*;
import java.util.function.BiConsumer;

/** Evaluator-only state. Never serialized into policy observations. */
public final class CourseEpisode {
    private final MinecraftServer server;
    private final CarpetBodyAdapter body;
    private final BiConsumer<String, Map<String,Object>> events;
    private final BlockPos target;
    private final AABB bounds;
    private final double pitY;
    private final int startTick, deadlineTick;
    private String status = "running", reason = "";
    private final CausalPickupLedger ledger;
    private int endTick = -1;

    public CourseEpisode(MinecraftServer server, CarpetBodyAdapter body, JsonObject spec,
                         BiConsumer<String,Map<String,Object>> events) {
        this.server = server; this.body = body; this.events = events;
        ledger=new CausalPickupLedger(body.player().getUUID().toString());
        var o=spec.getAsJsonArray("origin"); var t=spec.getAsJsonArray("target");
        int x=o.get(0).getAsInt(), y=o.get(1).getAsInt(), z=o.get(2).getAsInt();
        target=new BlockPos(x+t.get(0).getAsInt(),y+t.get(1).getAsInt(),z+t.get(2).getAsInt());
        var a=spec.getAsJsonObject("allowed_region").getAsJsonArray("min");
        var b=spec.getAsJsonObject("allowed_region").getAsJsonArray("max");
        bounds=new AABB(x+a.get(0).getAsInt(),y+a.get(1).getAsInt(),z+a.get(2).getAsInt(),
                x+b.get(0).getAsInt()+1,y+b.get(1).getAsInt()+1,z+b.get(2).getAsInt()+1);
        pitY=y+spec.get("pit_plane").getAsDouble();
        if (!server.overworld().getBlockState(target).is(Blocks.OAK_LOG)) throw new IllegalStateException("target_not_intact");
        body.resetFingerprint();
        startTick=server.getTickCount(); deadlineTick=startTick+spec.get("tick_limit").getAsInt();
    }
    public boolean running() { return status.equals("running"); }
    public boolean owns(ServerPlayer p) { return body.owns(p); }
    public void tick() {
        if (!running()) return;
        var p=body.player();
        if (p==null || !p.isAlive()) { finish("failed","death_or_disconnect"); return; }
        for (var human:server.getPlayerList().getPlayers()) {
            if (CarpetBodyAdapter.isHuman(human) && human.level()==server.overworld()
                    && human.getBoundingBox().intersects(bounds.inflate(8))) {
                finish("invalid","human_intrusion"); return;
            }
        }
        if (p.level()!=server.overworld() || !bounds.contains(p.position())) { finish("failed","out_of_bounds"); return; }
        if (p.getY()<pitY) { finish("failed","pit"); return; }
        if (!p.gameMode.isSurvival() || !p.getActiveEffects().isEmpty()) { finish("invalid","body_state_changed"); return; }
        for (var item:server.overworld().getEntitiesOfClass(ItemEntity.class,bounds.inflate(8))) {
            if (ledger.drop().isEmpty() || !ledger.drop().equals(item.getUUID().toString())) { finish("invalid","untracked_item_in_arena"); return; }
        }
        if (!ledger.broken() && !server.overworld().getBlockState(target).is(Blocks.OAK_LOG)) { finish("invalid","target_changed_without_break"); return; }
        if (server.getTickCount()>=deadlineTick) finish("failed","tick_limit");
    }
    public boolean allowBreak(ServerPlayer p, BlockPos pos) {
        if (!owns(p) || !running()) return false;
        boolean allowed=pos.equals(target) && !ledger.broken() && server.overworld().getBlockState(pos).is(Blocks.OAK_LOG);
        events.accept("course_interaction_attempt",Map.of("kind","break","allowed",allowed,"position",List.of(pos.getX(),pos.getY(),pos.getZ())));
        return allowed;
    }
    public void beforeBreak(ServerPlayer p, BlockPos pos) {
        if(running() && owns(p) && pos.equals(target)) ledger.begin();
    }
    public void afterBreak(ServerPlayer p, BlockPos pos, boolean success) {
        if (!running() || !owns(p) || !ledger.breaking()) return;
        try {
            ledger.complete(success);
            if(success) events.accept("course_milestone",Map.of("milestone","target_broken",
                    "position",List.of(pos.getX(),pos.getY(),pos.getZ())));
        } catch(IllegalStateException error) { finish("invalid",error.getMessage()); }
    }
    public void created(Entity entity, boolean added) {
        if (!running() || !added || !(entity instanceof ItemEntity item)) return;
        try {
            if(ledger.created(item.getUUID().toString(),item.getItem().is(Items.OAK_LOG),
                    item.getItem().getCount(),item.position().distanceToSqr(Vec3.atCenterOf(target))<=3))
                events.accept("course_milestone",Map.of("milestone","causal_drop_created","drop_uuid",ledger.drop()));
        } catch(IllegalStateException error) { finish("invalid",error.getMessage()); }
    }
    public void pickup(ServerPlayer p, ItemEntity item, int before, int after) {
        if (!running() || after>=before) return;
        if(!owns(p) && !ledger.drop().equals(item.getUUID().toString())) return;
        try {
            if(ledger.pickup(p.getUUID().toString(),item.getUUID().toString(),before,after)) {
                events.accept("course_milestone",Map.of("milestone","target_collected","drop_uuid",ledger.drop()));
                finish("success","causal_target_break_drop_pickup");
            }
        } catch(IllegalStateException error) { finish("invalid",error.getMessage()); }
    }
    public boolean permitMutation(BlockPos pos, net.minecraft.world.level.block.state.BlockState state) {
        if (!running() || !bounds.contains(Vec3.atCenterOf(pos))) return true;
        if (ledger.breaking() && pos.equals(target) && state.isAir()) return true;
        finish("invalid","unauthorized_world_mutation");
        return false;
    }
    public void attempted(String kind) { events.accept("course_interaction_attempt",Map.of("kind",kind,"allowed",false)); }
    public void finish(String outcome,String why) {
        if (!running()) return;
        status=outcome; reason=why; endTick=server.getTickCount();
        events.accept("course_episode_complete",fullState());
    }
    public Map<String,Object> publicState() {
        return Map.of("status",status,"reason",reason,"start_tick",startTick,"deadline_tick",deadlineTick);
    }
    public Map<String,Object> fullState() {
        var result=new LinkedHashMap<String,Object>(publicState());
        result.put("end_tick",endTick); result.put("target_broken",ledger.broken());
        result.put("drop_uuid",ledger.drop()); result.put("picked_up",ledger.collected());
        return Map.copyOf(result);
    }
}
