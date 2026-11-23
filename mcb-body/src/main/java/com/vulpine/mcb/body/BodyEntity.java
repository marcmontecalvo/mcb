package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.OwnableEntity;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.entity.animal.sheep.Sheep;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Entity ops of the body: attack (per tick), use, eat (per tick). Player-legal calls only. */
final class BodyEntity {
    private BodyEntity() {}

    /** A per-tick task: step returns null while running, "done", or a failure reason. */
    interface Task {
        String name();
        long requestSequence();
        String step(ServerPlayer player);
        Map<String, Object> result(ServerPlayer player);
        /** Called when the task is cancelled or ends early. */
        default void stop(ServerPlayer player) {}
        /** Movement for this tick, applied after step by the runtime with a 2-tick expiry; null releases the body. */
        default BodyRuntime.Controls controls(ServerPlayer player, int tick) { return null; }
    }

    static String typeId(Entity entity) { return BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(); }

    static Entity find(ServerPlayer player, JsonObject request) {
        UUID id;
        try { id = UUID.fromString(request.get("entity").getAsString()); }
        catch (RuntimeException e) { throw new BodyActions.Fail("not_found"); }
        Entity entity = player.level().getEntity(id);
        if (entity == null || !entity.isAlive()) throw new BodyActions.Fail("not_found");
        return entity;
    }

    private static void checkReach(ServerPlayer player, Entity entity) {
        if (!player.isWithinEntityInteractionRange(entity, 0)) throw new BodyActions.Fail("out_of_reach");
        if (!player.hasLineOfSight(entity)) throw new BodyActions.Fail("no_line_of_sight");
    }

    private static void aimAt(ServerPlayer player, Entity entity) {
        Vec3 eye = player.getEyePosition(), at = entity.getBoundingBox().getCenter();
        BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(at.x - eye.x), at.z - eye.z)),
                PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z));
    }

    // ---- attack_entity
    static final class AttackTask implements Task {
        final long sequence;
        final Entity target;
        final String type;
        int ticks, hits;

        private AttackTask(long sequence, Entity target) {
            this.sequence = sequence; this.target = target; this.type = typeId(target);
        }

        static AttackTask start(ServerPlayer player, JsonObject request, long sequence) {
            Entity target = find(player, request);
            boolean tamed = target instanceof OwnableEntity owned && owned.getOwnerReference() != null;
            if (!ActionLogic.allowedTarget(target instanceof Animal, target instanceof net.minecraft.world.entity.monster.Enemy, tamed, target instanceof net.minecraft.world.entity.player.Player,
                    target instanceof net.minecraft.world.entity.npc.villager.AbstractVillager))
                throw new BodyActions.Fail("not_allowed");
            checkReach(player, target);
            String weapon = request.has("weapon") ? request.get("weapon").getAsString() : bestWeapon(player);
            if (weapon != null) BodyBuild.selectItem(player, weapon);
            else player.getInventory().setSelectedSlot(emptyHotbar(player));
            return new AttackTask(sequence, target);
        }

        @Override public String name() { return "attack_entity"; }
        @Override public long requestSequence() { return sequence; }

        @Override public String step(ServerPlayer player) {
            ticks++;
            if (!target.isAlive()) return "done";
            if (ticks > ActionLogic.ATTACK_TIMEOUT_TICKS) return "timeout";
            try { checkReach(player, target); }
            catch (BodyActions.Fail fail) { return fail.getMessage(); }
            aimAt(player, target);
            if (ActionLogic.shouldSwing(player.getAttackStrengthScale(0.5f))) {
                player.attack(target);
                player.swingAndResetAttackStrength(InteractionHand.MAIN_HAND, player.getMainHandItem().getInteractAnimation(), false);
                player.resetAttackStrengthTicker();
                hits++;
                if (!target.isAlive()) return "done";
            }
            return null;
        }

        @Override public Map<String, Object> result(ServerPlayer player) {
            var out = new LinkedHashMap<String, Object>();
            out.put("entity", target.getUUID().toString());
            out.put("type", type);
            out.put("killed", !target.isAlive());
            out.put("ticks", ticks);
            out.put("hits", hits);
            return out;
        }
    }

    // ---- fight
    static final class FightTask implements Task {
        final long sequence;
        final FightLogic.Params params;
        final java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader;
        final UUID requested;
        int ticks, hits, kills;
        /** Set by step: 0 hold still, 1 walk forward, 2 walk forward and jump. */
        private int move;

        private FightTask(long sequence, FightLogic.Params params, UUID requested,
                          java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader) {
            this.sequence = sequence; this.params = params; this.requested = requested; this.reader = reader;
        }

        static FightTask start(ServerPlayer player, JsonObject request, long sequence,
                               java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader) {
            var params = FightLogic.parse(request);
            UUID requested = null;
            if (params.entity() != null) {
                Entity target = find(player, request);
                boolean tamed = target instanceof OwnableEntity owned && owned.getOwnerReference() != null;
                if (!ActionLogic.allowedTarget(target instanceof Animal, target instanceof net.minecraft.world.entity.monster.Enemy, tamed,
                        target instanceof net.minecraft.world.entity.player.Player,
                        target instanceof net.minecraft.world.entity.npc.villager.AbstractVillager))
                    throw new BodyActions.Fail("not_allowed");
                requested = target.getUUID();
            }
            var task = new FightTask(sequence, params, requested, reader);
            task.holdWeapon(player);
            return task;
        }

        @Override public String name() { return "fight"; }
        @Override public long requestSequence() { return sequence; }

        private static String heldId(ServerPlayer player) {
            var held = player.getMainHandItem();
            return held.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(held.getItem()).toString();
        }

        /** Hold the requested weapon while carried, else the best one; re-picks after a break. */
        private void holdWeapon(ServerPlayer player) {
            boolean carried = false;
            if (params.weapon() != null) {
                var inv = player.getInventory();
                for (int i = 0; i < 36 && !carried; i++)
                    carried = !inv.getItem(i).isEmpty()
                            && BuiltInRegistries.ITEM.getKey(inv.getItem(i).getItem()).toString().equals(params.weapon());
            }
            String desired = FightLogic.desiredWeapon(params.weapon(), carried, bestWeapon(player));
            if (!FightLogic.needsReselect(heldId(player), desired)) return;
            if (desired != null) BodyBuild.selectItem(player, desired);
            else player.getInventory().setSelectedSlot(emptyHotbar(player));
        }

        private void addMob(ServerPlayer player, LivingEntity living, String type,
                            java.util.List<FightLogic.Mob> candidates, java.util.Map<String, LivingEntity> entities) {
            double distance = Math.sqrt(player.distanceToSqr(living));
            candidates.add(new FightLogic.Mob(living.getUUID().toString(), type, distance, living.getY() - player.getY(),
                    distance <= params.radius() && player.hasLineOfSight(living)));
            entities.put(living.getUUID().toString(), living);
        }

        @Override public String step(ServerPlayer player) {
            ticks++;
            move = 0;
            String end = FightLogic.endReason(player.getHealth(), params.floor(), ticks, params.maxTicks());
            if (end != null) return end;
            var candidates = new java.util.ArrayList<FightLogic.Mob>();
            var entities = new java.util.HashMap<String, LivingEntity>();
            var box = player.getBoundingBox().inflate(params.radius(), FightLogic.MAX_DY, params.radius());
            for (Entity e : player.level().getEntities(player, box)) {
                if (!(e instanceof LivingEntity living) || !living.isAlive()) continue;
                String type = typeId(e);
                if (!RouteReflex.HOSTILES.contains(type) && !e.getUUID().equals(requested)) continue;
                addMob(player, living, type, candidates, entities);
            }
            if (requested != null && !entities.containsKey(requested.toString())
                    && player.level().getEntity(requested) instanceof LivingEntity living && living.isAlive())
                addMob(player, living, typeId(living), candidates, entities);
            if (FightLogic.avoid(candidates)) return "avoid";
            var pick = FightLogic.pick(requested == null ? null : requested.toString(), candidates, params.radius());
            if (pick == null) return "done";
            LivingEntity target = entities.get(pick.uuid());
            holdWeapon(player);
            aimAt(player, target);
            if (player.isWithinEntityInteractionRange(target, 0) && player.hasLineOfSight(target)) {
                if (ActionLogic.shouldSwing(player.getAttackStrengthScale(0.5f))) {
                    player.attack(target);
                    player.swingAndResetAttackStrength(InteractionHand.MAIN_HAND, player.getMainHandItem().getInteractAnimation(), false);
                    player.resetAttackStrengthTicker();
                    hits++;
                    if (!target.isAlive()) kills++;
                }
                return null;
            }
            move = approach(player, target);
            return null;
        }

        /** One forward step toward the target when the next cell is standable, safe and inside the leash. */
        private int approach(ServerPlayer player, Entity target) {
            if (!player.onGround()) return 0;
            int[] dir = FightLogic.direction(target.getX() - player.getX(), target.getZ() - player.getZ());
            int cx = (int) Math.floor(player.getX()) + dir[0], cz = (int) Math.floor(player.getZ()) + dir[1];
            double y = Math.rint(player.getY() * 2) / 2;
            var geometry = new RouteGeometry(reader.apply(player));
            var velocity = player.getDeltaMovement();
            var pose = new RouteMotor.Pose(player.getX(), player.getY(), player.getZ(), velocity.x, velocity.z,
                    player.getYRot(), true, player.getFoodData().getFoodLevel());
            for (int offset : FightLogic.STEP_OFFSETS) {
                var cell = new RouteMotor.Point(cx, y + offset, cz);
                if (!FightLogic.withinLeash(params.anchor(), params.leash(), cx, cell.y(), cz)) continue;
                if (geometry.standable(cell) && geometry.safe(pose, cell, y, .6, offset > 0, FightLogic.MAX_DROP))
                    return offset > 0 ? 2 : 1;
            }
            return 0;
        }

        @Override public BodyRuntime.Controls controls(ServerPlayer player, int tick) {
            if (move == 0) return null;
            return new BodyRuntime.Controls("native-fight", tick, tick, tick + 2, 2, 1, 0, 0, 0, move == 2,
                    false, false, false, false, false, false, player.getInventory().getSelectedSlot());
        }

        @Override public Map<String, Object> result(ServerPlayer player) {
            var out = new LinkedHashMap<String, Object>();
            out.put("kills", kills);
            out.put("hits", hits);
            out.put("ticks", ticks);
            out.put("health", player.getHealth());
            out.put("end", "clear");
            return out;
        }
    }

    /** Best sword, else axe, by durability (ponytail: a proxy for tier). Null means an empty hand. */
    private static String bestWeapon(ServerPlayer player) {
        String best = null;
        int bestScore = 0;
        var inv = player.getInventory();
        for (int i = 0; i < 36; i++) {
            ItemStack s = inv.getItem(i);
            if (s.isEmpty()) continue;
            int score = s.is(ItemTags.SWORDS) ? 100_000 + s.getMaxDamage() : s.is(ItemTags.AXES) ? s.getMaxDamage() + 1 : 0;
            if (score > bestScore) { bestScore = score; best = BuiltInRegistries.ITEM.getKey(s.getItem()).toString(); }
        }
        return best;
    }

    private static int emptyHotbar(ServerPlayer player) {
        var inv = player.getInventory();
        for (int i = 0; i < 9; i++) if (inv.getItem(i).isEmpty()) return i;
        return inv.getSelectedSlot();
    }

    // ---- use_entity
    static Map<String, Object> use(ServerPlayer player, JsonObject request) {
        Entity entity = find(player, request);
        checkReach(player, entity);
        BodyBuild.checkPermission(player, entity.blockPosition());
        if (request.has("item")) BodyBuild.selectItem(player, request.get("item").getAsString());
        aimAt(player, entity);
        String before = state(entity);
        boolean sneaking = player.isShiftKeyDown();
        player.setShiftKeyDown(false);
        try {
            Vec3 local = entity.getBoundingBox().getCenter().subtract(entity.position());
            String held = snapshot(player);
            var result = player.interactOn(entity, InteractionHand.MAIN_HAND, local);
            // The game answers CONSUME to shears on a sheared sheep: judge by what changed, not by the result alone.
            if (!result.consumesAction() || (before.equals(state(entity)) && held.equals(snapshot(player)))) throw new BodyActions.Fail("no_effect");
            if (result instanceof net.minecraft.world.InteractionResult.Success s && s.shouldSwing()) player.swingAndResetAttackStrength(InteractionHand.MAIN_HAND, player.getMainHandItem().getInteractAnimation(), false);
            var out = new LinkedHashMap<String, Object>();
            out.put("entity", entity.getUUID().toString());
            out.put("type", typeId(entity));
            out.put("result", result.getClass().getSimpleName());
            out.put("before", before);
            out.put("after", state(entity));
            return out;
        } finally {
            player.setShiftKeyDown(sneaking);
            if (player.containerMenu != player.inventoryMenu) player.closeContainer();
        }
    }

    private static String snapshot(ServerPlayer player) {
        var sb = new StringBuilder();
        var inv = player.getInventory();
        for (int i = 0; i < 36; i++) {
            var s = inv.getItem(i);
            if (!s.isEmpty()) sb.append(i).append(BuiltInRegistries.ITEM.getKey(s.getItem())).append(s.getCount()).append('/').append(s.getDamageValue()).append(';');
        }
        return sb.toString();
    }

    private static String state(Entity entity) {
        return entity instanceof Sheep sheep ? "sheared=" + sheep.isSheared() : entity instanceof LivingEntity living ? "health=" + living.getHealth() : "";
    }

    // ---- eat
    static final class EatTask implements Task {
        final long sequence;
        final String item;
        final int foodBefore;
        final int stackBefore;
        int ticks;

        private EatTask(long sequence, String item, int foodBefore, int stackBefore) {
            this.sequence = sequence; this.item = item; this.foodBefore = foodBefore; this.stackBefore = stackBefore;
        }

        static EatTask start(ServerPlayer player, JsonObject request, long sequence) {
            var foods = new LinkedHashMap<String, Integer>();
            var inv = player.getInventory();
            for (int i = 0; i < 36; i++) {
                var food = inv.getItem(i).get(DataComponents.FOOD);
                if (food != null) foods.put(BuiltInRegistries.ITEM.getKey(inv.getItem(i).getItem()).toString(), food.nutrition());
            }
            String item = request.has("item") ? request.get("item").getAsString() : ActionLogic.bestFood(foods);
            if (item == null || !foods.containsKey(item)) throw new BodyActions.Fail("no_food");
            BodyBuild.selectItem(player, item);
            var stack = player.getMainHandItem();
            if (!player.canEat(stack.get(DataComponents.FOOD).canAlwaysEat())) throw new BodyActions.Fail("not_hungry");
            int before = player.getFoodData().getFoodLevel();
            var result = player.gameMode.useItem(player, player.level(), stack, InteractionHand.MAIN_HAND);
            if (!result.consumesAction() || !player.isUsingItem()) throw new BodyActions.Fail("interrupted");
            return new EatTask(sequence, item, before, stack.getCount());
        }

        @Override public String name() { return "eat"; }
        @Override public long requestSequence() { return sequence; }

        @Override public String step(ServerPlayer player) {
            ticks++;
            if (player.isUsingItem()) return ticks > 200 ? "interrupted" : null;
            // Finished using: eaten when the stack shrank (it is the held stack, so compare against its start count).
            return player.getMainHandItem().getCount() < stackBefore || player.getMainHandItem().isEmpty() ? "done" : "interrupted";
        }

        @Override public void stop(ServerPlayer player) { if (player.isUsingItem()) player.stopUsingItem(); }

        @Override public Map<String, Object> result(ServerPlayer player) {
            var out = new LinkedHashMap<String, Object>();
            out.put("item", item);
            out.put("food_before", foodBefore);
            out.put("food_after", player.getFoodData().getFoodLevel());
            out.put("saturation_after", player.getFoodData().getSaturationLevel());
            out.put("ticks", ticks);
            return out;
        }
    }
}
