package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.core.Holder;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.alchemy.Potion;
import net.minecraft.world.phys.Vec3;

/**
 * Potions on the body (PotionLogic and ThrowLogic decide; this reads and acts on the game): how an item or an active
 * effect is reported, which slot holds what, and the throw_potion op. drink is a ConsumeTask. Docs: BODY_ACTION_OPS.md.
 */
final class BodyPotion {
    private BodyPotion() {}

    /** The registry id of a holder's key, or null when it has none (a direct holder). */
    private static <T> String idOf(Holder<T> holder) {
        return holder.unwrapKey().map(key -> key.identifier().toString()).orElse(null);
    }

    /** A potion's registry id (minecraft:strong_healing), or null when the stack names no potion. */
    static String potionId(ItemStack stack) {
        var contents = stack.get(DataComponents.POTION_CONTENTS);
        return contents == null ? null : contents.potion().map(BodyPotion::idOf).orElse(null);
    }

    /** One effect as the wire carries it: its id, amplifier and duration in ticks (-1 for infinite). */
    static Map<String, Object> effect(MobEffectInstance instance) {
        var out = new LinkedHashMap<String, Object>();
        out.put("id", idOf(instance.getEffect()));
        out.put("amplifier", instance.getAmplifier());
        out.put("duration", instance.isInfiniteDuration() ? -1 : instance.getDuration());
        return out;
    }

    static List<Map<String, Object>> effects(Iterable<MobEffectInstance> instances) {
        var out = new ArrayList<Map<String, Object>>();
        for (var instance : instances) out.add(effect(instance));
        return out;
    }

    /** What the body is under now. */
    static List<Map<String, Object>> activeEffects(LivingEntity entity) { return effects(entity.getActiveEffects()); }

    /**
     * Adds an inventory row's "potion" (omitted when the contents name none) and "effects" (the potion's and the
     * custom ones) when the stack carries potion contents: a potion, a splash or lingering one, a tipped arrow.
     */
    static void describe(ItemStack stack, Map<String, Object> row) {
        var contents = stack.get(DataComponents.POTION_CONTENTS);
        if (contents == null) return;
        String potion = potionId(stack);
        if (potion != null) row.put("potion", potion);
        row.put("effects", effects(contents.getAllEffects()));
    }

    /** The slots 0-35 as PotionLogic chooses among them. */
    static List<PotionLogic.Held> held(ServerPlayer player) {
        var out = new ArrayList<PotionLogic.Held>();
        var inventory = player.getInventory();
        for (int slot = 0; slot < PotionLogic.SLOTS; slot++) {
            var stack = inventory.getItem(slot);
            if (!stack.isEmpty()) out.add(new PotionLogic.Held(slot, BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(),
                    potionId(stack)));
        }
        return out;
    }

    /** The registry's potion for an id, or null when it has none (BodyTrial refuses it as unknown_potion). */
    static Holder<Potion> lookup(String id) {
        var key = Identifier.tryParse(id);
        return key == null ? null : BuiltInRegistries.POTION.get(key).map(holder -> (Holder<Potion>) holder).orElse(null);
    }

    // ---- throw_potion

    /**
     * Refuses ("blocked") a throw whose flight meets a block nearer the body than ThrowLogic.BREAKS_NEAR: it would
     * break there and splash the body (vault raid 20261124T045928Z: a harming splash broke on a block in front and
     * took the bot for 10). A block met farther off only breaks it short of the target.
     */
    private static void refuseBlockedNear(ServerPlayer player, Vec3 eye, float yaw, float pitch, int flight) {
        double[][] path = ThrowLogic.path(eye.x, eye.y, eye.z, yaw, pitch, flight);
        Vec3 feet = player.position();
        for (int i = 1; i < path.length; i++) {
            Vec3 from = new Vec3(path[i - 1][0], path[i - 1][1], path[i - 1][2]);
            Vec3 to = new Vec3(path[i][0], path[i][1], path[i][2]);
            var hit = player.level().clip(new net.minecraft.world.level.ClipContext(from, to,
                    net.minecraft.world.level.ClipContext.Block.COLLIDER, net.minecraft.world.level.ClipContext.Fluid.NONE,
                    player));
            if (hit.getType() == net.minecraft.world.phys.HitResult.Type.BLOCK) {
                if (hit.getLocation().distanceTo(feet) < ThrowLogic.BREAKS_NEAR) throw new BodyActions.Fail("blocked");
                return;
            }
        }
    }

    /**
     * Throws a splash or lingering potion: at the body's feet (self, straight down), at an entity or at a point
     * (the lowest arc ThrowLogic finds, an entity led by its velocity). The target is resolved and the flight
     * checked against the blocks before anything moves, so a refusal leaves the hand and the aim as they were.
     * Nothing is restored afterwards: the fight op selects its weapon again each call.
     */
    static Map<String, Object> throwPotion(ServerPlayer player, JsonObject request) {
        var throwing = PotionLogic.parseThrow(request);
        int slot = PotionLogic.pickThrow(held(player), throwing);
        if (slot < 0) throw new BodyActions.Fail("no_potion");
        Vec3 eye = player.getEyePosition();
        Vec3 target;
        float yaw, pitch;
        int flight;
        switch (throwing.at().kind()) {
            case SELF -> {
                target = player.position();
                yaw = player.getYRot();
                pitch = ThrowLogic.SELF_PITCH;
                flight = 0;
            }
            case ENTITY -> {
                var entity = BodyEntity.find(player, throwing.at().entity());
                target = entity.position();  // its feet: the splash reaches four blocks round and two up
                Vec3 velocity = entity.getKnownMovement();
                var aim = ThrowLogic.solveMoving(eye.x, eye.y, eye.z, target.x, target.y, target.z, velocity.x, velocity.z);
                if (aim == null) throw new BodyActions.Fail("out_of_range");
                yaw = aim.yaw();
                pitch = aim.pitch();
                flight = aim.flightTicks();
                target = new Vec3(aim.landX(), aim.landY(), aim.landZ());
            }
            default -> {
                target = new Vec3(throwing.at().x(), throwing.at().y(), throwing.at().z());
                var aim = ThrowLogic.solve(eye.x, eye.y, eye.z, target.x, target.y, target.z);
                if (aim == null) throw new BodyActions.Fail("out_of_range");
                yaw = aim.yaw();
                pitch = aim.pitch();
                flight = aim.flightTicks();
            }
        }
        if (flight > 0) refuseBlockedNear(player, eye, yaw, pitch, flight);
        BodyBuild.selectSlot(player, slot);
        BodyBuild.aim(player, yaw, pitch);
        var stack = player.getMainHandItem();
        var result = player.gameMode.useItem(player, player.level(), stack, InteractionHand.MAIN_HAND);
        if (!result.consumesAction()) throw new BodyActions.Fail("no_effect");
        var out = new LinkedHashMap<String, Object>();
        out.put("potion", throwing.potion());
        out.put("item", throwing.item());
        out.put("target", BodyBuild.vec(target));
        out.put("yaw", yaw);
        out.put("pitch", pitch);
        out.put("flight_ticks", flight);
        return out;
    }
}
