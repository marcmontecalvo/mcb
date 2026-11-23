package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ExperienceOrb;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.levelgen.Heightmap;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The trial op (TrialLogic parses it): sets the scene for one combat trial on the play
 * server. Order: move to the surface (when asked), clear the area, then the inventory,
 * give, heal, set the time, summon. Every registry id is checked before anything
 * changes; a summon with no room where it would stand fails (never left to suffocate in
 * a hillside). Docs:
 * COMPANION_COMBAT_PLAN_2026-11-23.md section 5.
 */
final class BodyTrial {
    private BodyTrial() {}

    /** Entities a trial summoned carry this tag; a later clear removes them first. */
    static final String TAG = "mcb_trial";

    static Map<String, Object> run(MinecraftServer server, ServerPlayer player, JsonObject request) {
        var trial = TrialLogic.parse(request);
        var stacks = new ArrayList<ItemStack>();
        for (var give : trial.give()) {
            var id = Identifier.tryParse(give.item());
            if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) throw new BodyActions.Fail("unknown_item");
            stacks.add(new ItemStack(BuiltInRegistries.ITEM.getValue(id), give.count()));
        }
        for (var summon : trial.summon()) {
            var id = Identifier.tryParse(summon.type());
            if (id == null || !BuiltInRegistries.ENTITY_TYPE.containsKey(id)) throw new BodyActions.Fail("unknown_entity");
        }
        var level = player.level();
        var out = new LinkedHashMap<String, Object>();
        BlockPos feet = player.blockPosition();
        if (trial.surface()) {  // out of a hole or a cave: the trial is fought in the open
            int ground = level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, feet.getX(), feet.getZ());
            if (ground != feet.getY()) {
                player.teleportTo(level, feet.getX() + .5, ground, feet.getZ() + .5, java.util.Set.of(),
                        player.getYRot(), player.getXRot(), true);
                feet = new BlockPos(feet.getX(), ground, feet.getZ());
            }
            out.put("surface", ground);
        }
        if (trial.clear() > 0) out.put("cleared", clear(player, trial.clear()));
        if (trial.clearInventory()) player.getInventory().clearContent();
        int given = 0;
        for (var stack : stacks) {
            // more than a stack goes in as several; a trial that does not fit is a bad setup
            int left = stack.getCount();
            while (left > 0) {
                int n = Math.min(left, stack.getMaxStackSize());
                if (!player.getInventory().add(stack.copyWithCount(n))) throw new BodyActions.Fail("inventory_full");
                left -= n;
            }
            given++;
        }
        if (given > 0) player.containerMenu.broadcastChanges();
        if (trial.heal()) {
            player.setHealth(player.getMaxHealth());
            player.getFoodData().setFoodLevel(20);
            player.getFoodData().setSaturation(5f);
            player.clearFire();
            player.removeAllEffects();
        }
        if (trial.time() != null) {
            long now = level.getDefaultClockTime(), day = now - Math.floorMod(now, 24000L);
            long total = day + trial.time() < now ? day + 24000L + trial.time() : day + trial.time();  // forward only
            level.dimensionType().defaultClock().ifPresent(clock -> server.clockManager().setTotalTicks(clock, total));
            out.put("time", trial.time());
        }
        var summoned = new ArrayList<Map<String, Object>>();
        for (var summon : trial.summon()) {
            var type = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(summon.type()));
            Entity entity = type.create(level, EntitySpawnReason.COMMAND);
            if (entity == null) throw new BodyActions.Fail("cannot_create_entity");
            BlockPos at = summon.distance() != null ? inSight(player, feet, entity, summon.distance())
                    : atOffset(player, feet, entity, summon, trial.surface());
            entity.addTag(TAG);
            if (entity instanceof Mob mob) {
                // as a natural spawn: a skeleton gets its bow, a zombie maybe a sword
                mob.finalizeSpawn(level, level.getCurrentDifficultyAt(mob.blockPosition()), EntitySpawnReason.COMMAND, null);
                if (summon.helmet()) {  // undead burn by day; a helmet keeps a day trial fair
                    mob.setItemSlot(EquipmentSlot.HEAD, new ItemStack(Items.LEATHER_HELMET));
                    mob.setDropChance(EquipmentSlot.HEAD, 0f);
                }
            }
            level.addFreshEntity(entity);
            summoned.add(Map.of("type", summon.type(), "uuid", entity.getUUID().toString(),
                    "at", List.of(at.getX(), at.getY(), at.getZ())));
        }
        out.put("given", given);
        out.put("summoned", summoned);
        out.put("health", player.getHealth());
        return out;
    }

    private static BlockPos atOffset(ServerPlayer player, BlockPos feet, Entity entity, TrialLogic.Summon summon,
                                     boolean surface) {
        var level = player.level();
        int x = feet.getX() + summon.dx(), z = feet.getZ() + summon.dz();
        int y = TrialLogic.summonY(surface, feet.getY(), summon.dy(),
                level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z));
        entity.snapTo(x + .5, y, z + .5, level.getRandom().nextFloat() * 360f, 0f);
        if (!level.noCollision(entity)) throw new BodyActions.Fail("no_room");
        return new BlockPos(x, y, z);
    }

    /**
     * The first cell of TrialLogic.ring(distance) where the mob stands on dry ground level with the body, has room,
     * and the body can see it: a trial measures the fight, not whether the guard noticed a mob behind a hill.
     */
    private static BlockPos inSight(ServerPlayer player, BlockPos feet, Entity entity, int distance) {
        var level = player.level();
        float yaw = level.getRandom().nextFloat() * 360f;
        for (int[] cell : TrialLogic.ring(distance)) {
            int x = feet.getX() + cell[0], z = feet.getZ() + cell[1];
            int ground = level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z);
            if (!TrialLogic.level(feet.getY(), ground)) continue;
            var pos = new BlockPos(x, ground, z);
            if (!level.getFluidState(pos).isEmpty() || !level.getFluidState(pos.below()).isEmpty()) continue;
            entity.snapTo(x + .5, ground, z + .5, yaw, 0f);
            if (level.noCollision(entity) && player.hasLineOfSight(entity)) return pos;
        }
        throw new BodyActions.Fail("no_spot");
    }

    /**
     * Removes what an earlier trial summoned anywhere loaded (one left alive by an escape or a death
     * waits where the bot was, not where it respawns), and within the radius hostile mobs, drops
     * and projectiles.
     */
    private static int clear(ServerPlayer player, int radius) {
        var box = player.getBoundingBox().inflate(radius);
        var found = new ArrayList<Entity>();
        for (var e : player.level().getAllEntities()) {
            if (e instanceof ServerPlayer) continue;
            boolean ours = e.entityTags().contains(TAG);
            boolean litter = e instanceof ItemEntity || e instanceof ExperienceOrb || e instanceof Projectile;
            if (ours || (litter || e instanceof Enemy) && box.intersects(e.getBoundingBox())) found.add(e);
        }
        found.forEach(Entity::discard);  // after the walk: never discard while iterating the level
        return found.size();
    }
}
