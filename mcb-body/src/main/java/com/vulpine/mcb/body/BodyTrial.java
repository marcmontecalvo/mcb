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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The trial op (TrialLogic parses it): sets the scene for one combat trial on the play
 * server. Order: clear the area, then the inventory, give, heal, set the time, summon.
 * Every registry id is checked before anything changes. Docs:
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
        BlockPos feet = player.blockPosition();
        for (var summon : trial.summon()) {
            var type = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(summon.type()));
            Entity entity = type.create(level, EntitySpawnReason.COMMAND);
            if (entity == null) throw new BodyActions.Fail("cannot_create_entity");
            entity.snapTo(feet.getX() + summon.dx() + .5, feet.getY() + summon.dy(), feet.getZ() + summon.dz() + .5,
                    level.getRandom().nextFloat() * 360f, 0f);
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
            summoned.add(Map.of("type", summon.type(), "uuid", entity.getUUID().toString()));
        }
        out.put("given", given);
        out.put("summoned", summoned);
        out.put("health", player.getHealth());
        return out;
    }

    /** Removes, within the radius: what an earlier trial summoned, hostile mobs, drops and projectiles. */
    private static int clear(ServerPlayer player, int radius) {
        var box = player.getBoundingBox().inflate(radius);
        int cleared = 0;
        List<Entity> found = player.level().getEntities(player, box);
        for (var e : found) {
            boolean ours = e.entityTags().contains(TAG);
            boolean litter = e instanceof ItemEntity || e instanceof ExperienceOrb || e instanceof Projectile;
            if (!ours && !litter && !(e instanceof Enemy) || e instanceof ServerPlayer) continue;
            e.discard();
            cleared++;
        }
        return cleared;
    }
}
