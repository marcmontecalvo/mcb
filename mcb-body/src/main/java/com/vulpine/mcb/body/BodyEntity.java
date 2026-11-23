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
        /** Evidence for a failure (counters so far, why): merged under the reason in &lt;name&gt;_failed. */
        default Map<String, Object> failure(ServerPlayer player) { return Map.of(); }
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

    // ---- melee technique, shared by attack_entity and fight (rules in CombatLogic)

    private static String heldId(ServerPlayer player) {
        var held = player.getMainHandItem();
        return held.isEmpty() ? null : BuiltInRegistries.ITEM.getKey(held.getItem()).toString();
    }

    /** This tick's swing: jump for a crit, strike (sprinting to spare bystanders a sweep), or wait. */
    private static CombatLogic.Act meleeAct(ServerPlayer player, LivingEntity target, boolean inReach,
                                            boolean critWanted, boolean sweepOk) {
        float scale = player.getAttackStrengthScale(0.5f);
        double toFull = Math.max(0, (1 - scale) * player.getCurrentItemAttackStrengthDelay());
        var kind = CombatLogic.kind(heldId(player));
        return CombatLogic.melee(inReach, scale, toFull, player.onGround(), falling(player), critHere(player),
                critWanted, kind.crits(), kind == CombatLogic.Kind.SWORD && bystanders(player, target), sweepOk);
    }

    /** One swing at full use; true when it was a crit (Player.canCriticalAttack's conditions). */
    private static boolean strike(ServerPlayer player, LivingEntity target) {
        boolean crit = player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG && falling(player) && critHere(player)
                && !player.isSprinting();
        player.attack(target);
        player.swingAndResetAttackStrength(InteractionHand.MAIN_HAND, player.getMainHandItem().getInteractAnimation(), false);
        player.resetAttackStrengthTicker();
        return crit;
    }

    private static boolean falling(ServerPlayer player) {
        return !player.onGround() && player.fallDistance > 0 && player.getDeltaMovement().y < 0;
    }

    /** Player.canCriticalAttack's conditions that a jump cannot fix, plus room to jump. */
    private static boolean critHere(ServerPlayer player) {
        return !player.isInWater() && !player.onClimbable() && !player.isPassenger()
                && !player.hasEffect(net.minecraft.world.effect.MobEffects.BLINDNESS)
                && (!player.onGround() || player.level().noCollision(player, player.getBoundingBox().move(0, .5, 0)));
    }

    /** A grounded sword hit would sweep a non-hostile living thing beside the target. */
    private static boolean bystanders(ServerPlayer player, LivingEntity target) {
        for (var e : player.level().getEntitiesOfClass(LivingEntity.class, target.getBoundingBox().inflate(1, .25, 1))) {
            if (e == player || e == target || e instanceof net.minecraft.world.entity.monster.Enemy) continue;
            if (player.distanceToSqr(e) < 9) return true;
        }
        return false;
    }

    // ---- attack_entity
    /**
     * Hits one entity in reach until it dies or leaves reach (the caller walks after it).
     * Jump-crit technique as the fight's, and a sword hit never sweeps the herd round a hunted animal.
     */
    static final class AttackTask implements Task {
        final long sequence;
        final Entity target;
        final String type;
        int ticks, hits, crits;
        private boolean jump;

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
            jump = false;
            if (!target.isAlive()) return "done";
            if (ticks > ActionLogic.ATTACK_TIMEOUT_TICKS) return "timeout";
            try { checkReach(player, target); }
            catch (BodyActions.Fail fail) { return player.onGround() ? fail.getMessage() : null; }  // mid-jump: land first
            aimAt(player, target);
            if (!(target instanceof LivingEntity living)) {  // an item frame, a boat: no technique
                if (ActionLogic.shouldSwing(player.getAttackStrengthScale(0.5f))) { player.attack(target); hits++; }
                return target.isAlive() ? null : "done";
            }
            var act = meleeAct(player, living, true, true, false);
            switch (act) {
                case JUMP -> jump = true;
                case STRIKE, SPRINT_STRIKE -> {
                    player.setSprinting(act == CombatLogic.Act.SPRINT_STRIKE);  // a sprint hit never sweeps
                    if (strike(player, living)) crits++;
                    hits++;
                }
                case NONE -> { }
            }
            return living.isAlive() ? null : "done";
        }

        @Override public BodyRuntime.Controls controls(ServerPlayer player, int tick) {
            if (!jump) return null;
            return new BodyRuntime.Controls("native-attack", tick, tick, tick + 2, 2, 0, 0, 0, 0, true,
                    false, false, false, false, false, false, player.getInventory().getSelectedSlot());
        }

        @Override public Map<String, Object> result(ServerPlayer player) {
            var out = new LinkedHashMap<String, Object>();
            out.put("entity", target.getUUID().toString());
            out.put("type", type);
            out.put("killed", !target.isAlive());
            // where it ended up: its drops lie there, not where the hunt first saw it
            out.put("at", java.util.List.of(target.getX(), target.getY(), target.getZ()));
            out.put("ticks", ticks);
            out.put("hits", hits);
            out.put("crits", crits);
            return out;
        }
    }

    // ---- fight
    /**
     * The fight motor: per tick it picks the target and carries out the tactic Python chose
     * (CombatLogic: crit melee, spear poke, creeper hit-and-back, bow snipe, hunt).
     * Docs: COMPANION_COMBAT_PLAN_2026-11-23.md.
     */
    static final class FightTask implements Task {
        final long sequence;
        final FightLogic.Params params;
        final CombatLogic.Tactic tactic;
        final java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader;
        final UUID requested;
        int ticks, hits, kills, crits, shots;
        /** The radius the fight looks in: params.radius widened to the tactic's stand-off band (FightLogic.reach). */
        final int reach;
        /** Health the targets lost while targeted (arrows land ticks after the shot, so it is read per tick). */
        double damage;
        private LivingEntity tracked;
        private float trackedHealth;
        /** Chase: a walk round what blocks the straight step (FightLogic.chase); null while the step is open. */
        static final int CHASE_REPLAN = 20, CHASE_RETRY = 10;
        static final double CHASE_RANGE = 2.5;  // across: a melee hit lands from here
        private java.util.List<double[]> chase;
        private int chaseIndex, chasedTick, nextChase;
        private double[] chasedAt;
        /** No chase path since this tick (-1: a path, or in reach), the target this far off then (FightLogic.noPath). */
        private int noPathSince = -1;
        private double noPathAt;
        /** The last failed search, for the failure's evidence: from, to, cells reached, the start's four exits. */
        private Map<String, Object> lostPath;
        /** hit_and_back: backing off after a hit until the creeper's swell is gone. */
        private boolean retreating;
        /** Set by step for controls: forward (-1 back, 0, 1), jump, sprint, hold use (bow draw). */
        private double forward;
        private boolean jump, sprint, useHold;

        private FightTask(long sequence, FightLogic.Params params, CombatLogic.Tactic tactic, UUID requested,
                          java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader) {
            this.sequence = sequence; this.params = params; this.tactic = tactic; this.requested = requested;
            this.reader = reader;
            this.reach = FightLogic.reach(params.radius(), tactic.keepMax());
        }

        /** Adds what the tracked target lost since the last look; a dead one is let go. */
        private void account() {
            if (tracked == null) return;
            damage += FightLogic.dealt(trackedHealth, tracked.getHealth());
            trackedHealth = tracked.getHealth();
            if (!tracked.isAlive()) tracked = null;
        }

        private void track(LivingEntity target) {
            if (target == tracked) return;
            tracked = target;
            trackedHealth = target.getHealth();
        }

        static FightTask start(ServerPlayer player, JsonObject request, long sequence,
                               java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader) {
            var params = FightLogic.parse(request);
            var tactic = CombatLogic.parseTactic(request);
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
            var task = new FightTask(sequence, params, tactic, requested, reader);
            task.holdWeapon(player, params.weapon());
            return task;
        }

        @Override public String name() { return "fight"; }
        @Override public long requestSequence() { return sequence; }

        /** Hold the requested weapon while carried, else the best one; re-picks after a break. */
        private void holdWeapon(ServerPlayer player, String weapon) {
            boolean carried = false;
            if (weapon != null) {
                var inv = player.getInventory();
                for (int i = 0; i < 36 && !carried; i++)
                    carried = !inv.getItem(i).isEmpty()
                            && BuiltInRegistries.ITEM.getKey(inv.getItem(i).getItem()).toString().equals(weapon);
            }
            String desired = FightLogic.desiredWeapon(weapon, carried, bestWeapon(player));
            if (!FightLogic.needsReselect(heldId(player), desired)) return;
            if (desired != null) BodyBuild.selectItem(player, desired);
            else player.getInventory().setSelectedSlot(emptyHotbar(player));
        }

        private void addMob(ServerPlayer player, LivingEntity living, String type,
                            java.util.List<FightLogic.Mob> candidates, java.util.Map<String, LivingEntity> entities) {
            double distance = Math.sqrt(player.distanceToSqr(living));
            candidates.add(new FightLogic.Mob(living.getUUID().toString(), type, distance, living.getY() - player.getY(),
                    distance <= reach && player.hasLineOfSight(living)));
            entities.put(living.getUUID().toString(), living);
        }

        @Override public String step(ServerPlayer player) {
            ticks++;
            forward = 0; jump = false; sprint = false;
            account();
            String end = FightLogic.endReason(player.getHealth(), params.floor(), ticks, params.maxTicks());
            if (end != null) return end;
            var candidates = new java.util.ArrayList<FightLogic.Mob>();
            var entities = new java.util.HashMap<String, LivingEntity>();
            var box = player.getBoundingBox().inflate(reach, FightLogic.MAX_DY, reach);
            for (Entity e : player.level().getEntities(player, box)) {
                if (!(e instanceof LivingEntity living) || !living.isAlive()) continue;
                String type = typeId(e);
                if (!RouteReflex.HOSTILES.contains(type) && !e.getUUID().equals(requested)) continue;
                addMob(player, living, type, candidates, entities);
            }
            if (requested != null && !entities.containsKey(requested.toString())
                    && player.level().getEntity(requested) instanceof LivingEntity living && living.isAlive())
                addMob(player, living, typeId(living), candidates, entities);
            if (FightLogic.avoid(candidates, tactic.style().equals("hit_and_back"))) return "avoid";
            var pick = FightLogic.pick(requested == null ? null : requested.toString(), candidates, reach);
            if (pick == null) { stopDraw(); return "done"; }
            LivingEntity target = entities.get(pick.uuid());
            track(target);
            double distance = Math.sqrt(player.distanceToSqr(target));
            if (noPathSince >= 0) {
                // no walk to it: hold and let it come; one that keeps off is left to the guard
                String call = FightLogic.noPath(noPathSince, noPathAt, ticks, distance);
                if ("closing".equals(call)) { noPathSince = ticks; noPathAt = distance; }
                else if ("unreachable".equals(call)) { stopDraw(); return "unreachable"; }
            }
            switch (tactic.style()) {
                case "snipe" -> {
                    if (CombatLogic.snipeClose(distance, tactic.melee() != null)) { stopDraw(); melee(player, target, tactic.melee()); }
                    else snipe(player, target, distance);
                }
                case "poke" -> poke(player, target);
                case "hit_and_back" -> hitAndBack(player, target, distance);
                default -> melee(player, target, params.weapon());  // melee and hunt
            }
            return null;
        }

        // -- techniques (rules in CombatLogic; game facts in COMBAT_MECHANICS_26.3.md)

        private boolean inReach(ServerPlayer player, Entity target) {
            return player.isWithinEntityInteractionRange(target, 0) && player.hasLineOfSight(target);
        }

        /** Crit melee: jump so the cooldown is full as the fall starts, strike on the way down. */
        private void melee(ServerPlayer player, LivingEntity target, String weapon) {
            holdWeapon(player, weapon);
            aim(player, target);
            boolean reach = inReach(player, target);
            var act = meleeAct(player, target, reach, tactic.crit(), tactic.sweep());
            switch (act) {
                case JUMP -> jump = true;
                case STRIKE -> { player.setSprinting(false); strike(player, target); }
                case SPRINT_STRIKE -> { player.setSprinting(true); strike(player, target); }
                case NONE -> { }
            }
            // sprint in: the strike itself drops the sprint when it wants the crit
            if (!reach) { approach(player, target); sprint = true; }
            else noPathSince = -1;
        }

        /** Spear: hold the band just past the target's reach and jab at full charge. */
        private void poke(ServerPlayer player, LivingEntity target) {
            holdWeapon(player, params.weapon());
            aim(player, target);
            double eye = Math.sqrt(target.getBoundingBox().distanceToSqr(player.getEyePosition()));
            var move = CombatLogic.keep(eye, tactic.keepMin(), tactic.keepMax());
            if (move == CombatLogic.Move.APPROACH) approach(player, target);
            else if (move == CombatLogic.Move.RETREAT) walk(player, target, -1);
            if (move != CombatLogic.Move.APPROACH) noPathSince = -1;  // in its band: reached
            var stack = player.getMainHandItem();
            var spear = stack.get(DataComponents.PIERCING_WEAPON);
            // the item's own minimum charge, and full charge for full damage (the jab resets it)
            boolean charged = !player.cannotAttackWithItem(stack, 5) && player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG;
            if (spear != null && player.hasLineOfSight(target) && CombatLogic.jabReach(eye) && charged) {
                float before = target.getHealth();
                spear.attack(player, net.minecraft.world.entity.EquipmentSlot.MAINHAND);  // the server's STAB path
                if (target.getHealth() < before || !target.isAlive()) hits++;
                if (!target.isAlive()) kills++;
            }
        }

        /** Creeper: a sprint hit knocks it back, then keep off until its swell is gone. */
        private void hitAndBack(ServerPlayer player, LivingEntity target, double distance) {
            holdWeapon(player, params.weapon());
            boolean swelling = target instanceof net.minecraft.world.entity.monster.Creeper c
                    && (c.getSwellDir() > 0 || c.getSwelling(0) > 0);
            boolean reach = inReach(player, target);
            String plan = CombatLogic.hitAndBack(retreating, distance, swelling, reach, player.getAttackStrengthScale(0.5f));
            switch (plan) {
                case "strike" -> { aim(player, target); player.setSprinting(true); strike(player, target); retreating = true; }
                case "retreat" -> {
                    // face away and sprint: walking backwards is too slow to outrun a swell
                    float away = (float) Math.toDegrees(Math.atan2(target.getX() - player.getX(), -(target.getZ() - player.getZ())));
                    BodyBuild.aim(player, away, 0);
                    walk(player, target, -1);
                    if (forward != 0) forward = 1;  // facing away: a backward step is forward
                    sprint = forward != 0;
                }
                default -> {
                    retreating = false; aim(player, target); approach(player, target); sprint = true;
                    if (reach) noPathSince = -1;
                }
            }
        }

        /** Bow: keep the band, draw 20 ticks, aim with drop and lead, release. */
        private void snipe(ServerPlayer player, LivingEntity target, double distance) {
            holdWeapon(player, params.weapon());
            var move = CombatLogic.keep(distance, tactic.keepMin(), tactic.keepMax());
            boolean sight = player.hasLineOfSight(target);
            Vec3 from = player.getEyePosition().subtract(0, .1, 0), at = target.getBoundingBox().getCenter();
            Vec3 v = target.getDeltaMovement();
            double[] shot = CombatLogic.aim(at.x - from.x, at.y - from.y, at.z - from.z, v.x, v.y, v.z);
            if (shot != null) BodyBuild.aim(player, (float) shot[0], (float) shot[1]);
            else aim(player, target);
            // out of sight: walk round what hides it (no shot is released without sight, so the
            // chase may turn the body); in sight, step with the aim kept on the shot
            if (!sight) approach(player, target);
            else if (move == CombatLogic.Move.APPROACH || shot == null) walk(player, target, 1);
            else if (move == CombatLogic.Move.RETREAT) walk(player, target, -1);
            if (sight) noPathSince = -1;
            boolean drawn = player.isUsingItem() && player.getTicksUsingItem() >= CombatLogic.FULL_DRAW;
            if (drawn && sight && shot != null) { useHold = false; shots++; }  // released with the controls
            else useHold = sight || player.isUsingItem();  // start in sight; hold a draw while it is lost
        }

        private void stopDraw() { useHold = false; }

        private void strike(ServerPlayer player, LivingEntity target) {
            if (BodyEntity.strike(player, target)) crits++;
            hits++;
            if (!target.isAlive()) kills++;
        }

        /** Aim at the target; an enderman's legs, never its eyes (looking at them provokes it). */
        private void aim(ServerPlayer player, LivingEntity target) {
            if (!tactic.noGaze()) { aimAt(player, target); return; }
            Vec3 eye = player.getEyePosition(), at = target.position().add(0, .5, 0);
            BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(at.x - eye.x), at.z - eye.z)),
                    PlacementLogic.pitchTo(at.x - eye.x, at.y - eye.y, at.z - eye.z));
        }

        /** One step toward (sign 1) or away from (-1) the target when that cell is standable, safe, in the leash. */
        private void walk(ServerPlayer player, Entity target, int sign) {
            int code = stepCode(player, sign * (target.getX() - player.getX()), sign * (target.getZ() - player.getZ()));
            if (code == 0) return;
            forward = sign;
            jump = jump || code == 2;
        }

        /** 0: no safe cell that way; 1: walk; 2: walk and jump (a step up). */
        private int stepCode(ServerPlayer player, double dx, double dz) {
            if (!player.onGround()) return 0;
            int[] dir = FightLogic.direction(dx, dz);
            int cx = (int) Math.floor(player.getX()) + dir[0], cz = (int) Math.floor(player.getZ()) + dir[1];
            double y = Math.rint(player.getY() * 2) / 2;
            var velocity = player.getDeltaMovement();
            var pose = new RouteMotor.Pose(player.getX(), player.getY(), player.getZ(), velocity.x, velocity.z,
                    player.getYRot(), true, player.getFoodData().getFoodLevel());
            Double next = stance(new RouteGeometry(reader.apply(player)), pose, cx, y, cz);
            return next == null ? 0 : next > y ? 2 : 1;
        }

        private Double stance(RouteGeometry geometry, RouteMotor.Pose pose, int cx, double y, int cz) {
            return FightLogic.stance(geometry, pose, cx, y, cz, params.anchor(), params.leash());
        }

        /**
         * Toward the target: the straight step while it is open, else a chase path round what is in the way (a
         * ledge, a wall). With no path the body holds still and lets the target come (FightLogic.noPath); one
         * that keeps off ends the fight "unreachable".
         */
        private void approach(ServerPlayer player, LivingEntity target) {
            if (chase == null) {
                walk(player, target, 1);
                if (forward != 0) return;  // the straight step is open
            }
            boolean moved = chase != null && Math.hypot(target.getX() - chasedAt[0], target.getZ() - chasedAt[1]) > 1.5;
            boolean stale = chase == null || moved || ticks - chasedTick >= CHASE_REPLAN;
            if (stale && player.onGround() && ticks >= nextChase) planChase(player, target);
            if (chase == null) return;
            int bx = (int) Math.floor(player.getX()), bz = (int) Math.floor(player.getZ());
            while (chaseIndex < chase.size() && (int) chase.get(chaseIndex)[0] == bx && (int) chase.get(chaseIndex)[2] == bz)
                chaseIndex++;
            if (chaseIndex >= chase.size()) { chase = null; return; }
            double[] next = chase.get(chaseIndex);
            if (Math.abs(next[0] - bx) + Math.abs(next[2] - bz) != 1) { chase = null; return; }  // knocked off it
            BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(next[0] + .5 - player.getX()), next[2] + .5 - player.getZ())), 0);
            forward = 1;
            sprint = true;
            jump = jump || next[1] > player.getY() + .25;
        }

        private void planChase(ServerPlayer player, LivingEntity target) {
            var steps = FightLogic.steps(new RouteGeometry(reader.apply(player)), player.getFoodData().getFoodLevel(),
                    params.anchor(), params.leash());
            double[] start = {Math.floor(player.getX()), Math.rint(player.getY() * 2) / 2, Math.floor(player.getZ())};
            var search = FightLogic.search(start, target.getX(), target.getY(), target.getZ(), CHASE_RANGE, steps,
                    FightLogic.CHASE_NODES);
            chase = search.path();
            chaseIndex = 0;
            chasedTick = ticks;
            chasedAt = new double[] {target.getX(), target.getZ()};
            if (chase != null) { noPathSince = -1; return; }
            if (noPathSince < 0) { noPathSince = ticks; noPathAt = Math.sqrt(player.distanceToSqr(target)); }
            nextChase = ticks + CHASE_RETRY;  // a failed search is not repeated every tick
            var exits = new java.util.ArrayList<Object>();  // feet y east, west, south, north (null: no step)
            for (int[] d : new int[][] {{1, 0}, {-1, 0}, {0, 1}, {0, -1}})
                exits.add(steps.next((int) start[0], start[1], (int) start[2], d[0], d[1]));
            var lost = new LinkedHashMap<String, Object>();
            lost.put("from", java.util.List.of(start[0], start[1], start[2]));
            lost.put("to", java.util.List.of(round1(target.getX()), round1(target.getY()), round1(target.getZ())));
            lost.put("expanded", search.expanded());
            lost.put("exits", exits);
            lostPath = lost;
        }

        private static double round1(double v) { return Math.round(v * 10) / 10.0; }

        @Override public BodyRuntime.Controls controls(ServerPlayer player, int tick) {
            if (forward == 0 && !jump && !useHold && !player.isUsingItem()) return null;
            boolean run = sprint && forward > 0 && player.getFoodData().getFoodLevel() > EscapeLogic.SPRINT_FOOD;
            return new BodyRuntime.Controls("native-fight", tick, tick, tick + 2, 2, forward, 0, 0, 0, jump,
                    false, run, false, false, useHold, false, player.getInventory().getSelectedSlot());
        }

        @Override public void stop(ServerPlayer player) { if (player.isUsingItem()) player.stopUsingItem(); }

        @Override public Map<String, Object> result(ServerPlayer player) {
            var out = new LinkedHashMap<String, Object>();
            out.put("kills", kills);
            out.put("hits", hits);
            out.put("crits", crits);
            out.put("shots", shots);
            account();
            out.put("damage", Math.round(damage * 10) / 10.0);
            out.put("style", tactic.style());
            out.put("ticks", ticks);
            out.put("health", player.getHealth());
            out.put("end", "clear");
            return out;
        }

        /** The counters so far and, for an unreachable target, the last search that found no walk to it. */
        @Override public Map<String, Object> failure(ServerPlayer player) {
            var out = result(player);
            out.remove("end");
            if (lostPath != null) out.put("chase", lostPath);
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
