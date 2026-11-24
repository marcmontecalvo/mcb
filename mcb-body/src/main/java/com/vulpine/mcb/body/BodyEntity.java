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
import net.minecraft.world.item.component.KineticWeapon;
import net.minecraft.world.item.enchantment.EnchantmentHelper;
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
        /** The body died of `source` (hurtSource) while the task ran; failure follows. */
        default void died(ServerPlayer player, String source) {}
        /** Movement for this tick, applied after step by the runtime with a 2-tick expiry; null releases the body. */
        default BodyRuntime.Controls controls(ServerPlayer player, int tick) { return null; }
    }

    static String typeId(Entity entity) { return BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(); }

    /**
     * What dealt the body's last damage: the direct entity's type (an evoker's fangs, an arrow, a vex), else the
     * damage kind ("fall", "magic"); null once it is too old to be this tick's.
     */
    static String hurtSource(ServerPlayer player) { return hurtSource(player.getLastDamageSource()); }

    static String hurtSource(net.minecraft.world.damagesource.DamageSource source) {
        if (source == null) return null;
        return source.getDirectEntity() != null ? typeId(source.getDirectEntity()) : source.getMsgId();
    }

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

    /**
     * The line from the body to the target for CritForecast: gaps, the target's box, both velocities
     * along it, and the target's intent and reach. step: the target's move over the last tick, or null.
     */
    static CritForecast.Scene critScene(ServerPlayer player, LivingEntity target, double[] step) {
        double dx = target.getX() - player.getX(), dz = target.getZ() - player.getZ();
        double gap = Math.hypot(dx, dz), ux = gap > 1e-6 ? dx / gap : 1, uz = gap > 1e-6 ? dz / gap : 0;
        var box = target.getBoundingBox();
        var eye = player.getEyePosition();
        double hx = Math.max(0, Math.max(box.minX - eye.x, eye.x - box.maxX));
        double hz = Math.max(0, Math.max(box.minZ - eye.z, eye.z - box.maxZ));
        double half = gap - Math.hypot(hx, hz);
        var v = player.getDeltaMovement();
        boolean chasing = target instanceof net.minecraft.world.entity.Mob mob && mob.getTarget() == player;
        boolean archer = target instanceof net.minecraft.world.entity.monster.RangedAttackMob
                && target.getMainHandItem().getItem() instanceof net.minecraft.world.item.ProjectileWeaponItem;
        double seen = step == null ? Double.NaN : step[0] * ux + step[1] * uz;
        double speed = target.getAttributeValue(net.minecraft.world.entity.ai.attributes.Attributes.MOVEMENT_SPEED);
        double intent = CritForecast.intent(chasing, archer, gap, Double.isNaN(seen) ? 0 : seen, speed);
        if (Double.isNaN(seen)) seen = intent;  // a new target: as it heads
        boolean melees = target instanceof net.minecraft.world.entity.monster.Enemy && !archer;
        double reach = melees ? CritForecast.mobReach(target.getBbWidth() / 2, ux, uz) : 0;
        float scale = player.getAttackStrengthScale(0.5f);
        double toFull = Math.max(0, (1 - scale) * player.getCurrentItemAttackStrengthDelay());
        return new CritForecast.Scene(gap, half, box.minY - player.getY(), box.maxY - player.getY(),
                v.x * ux + v.z * uz, seen, intent, target.onGround(), reach, toFull, player.entityInteractionRange());
    }

    /** A grounded sword hit would sweep a non-hostile living thing beside the target. */
    private static boolean bystanders(ServerPlayer player, LivingEntity target) {
        for (var e : player.level().getEntitiesOfClass(LivingEntity.class, target.getBoundingBox().inflate(1, .25, 1))) {
            if (e == player || e == target || !e.isAlive() || e instanceof net.minecraft.world.entity.monster.Enemy) continue;
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
        /** The target's feet {x, z} a tick ago. */
        private double[] at;

        /**
         * On the ground, the body stays put: jump when a jump in place lands the crit where the target
         * will be (CritForecast), wait while one will once the cooldown is back, and with none to come
         * (it backs off, or is upon the body) take the plain full-strength hit instead.
         */
        private static CombatLogic.Act standAct(ServerPlayer player, LivingEntity target, double[] step) {
            var plan = CritForecast.plan(critScene(player, target, step), false, false, false);
            if (plan.hop() == CritForecast.Hop.STAND) return CombatLogic.Act.JUMP;
            if (plan.step() == CritForecast.Step.HOLD && plan.strikeIn() >= 0) return CombatLogic.Act.NONE;
            if (player.getAttackStrengthScale(0.5f) <= CombatLogic.STRONG) return CombatLogic.Act.NONE;
            // a grounded sword hit would sweep the herd round a hunted animal: sprint it
            return bystanders(player, target) ? CombatLogic.Act.SPRINT_STRIKE : CombatLogic.Act.STRIKE;
        }

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
            double[] step = at == null ? null : new double[] {living.getX() - at[0], living.getZ() - at[1]};
            at = new double[] {living.getX(), living.getZ()};
            var act = player.onGround() && CombatLogic.kind(heldId(player)).crits() && critHere(player)
                    ? standAct(player, living, step) : meleeAct(player, living, true, true, false);
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
        int ticks, hits, crits, shots;
        /** Targets that died with the body as their killer (FightLogic.Kills). */
        final FightLogic.Kills kills = new FightLogic.Kills();
        /** The damage the body took, by source; from the first tick's health. */
        private FightLogic.Hurt hurt;
        /** Where the requested target died (its drops lie there, a hunt collects them), or null. */
        private java.util.List<Double> diedAt;
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
        /** hit_and_back after a hit, poke from a swelling creeper: backing off until its swell is gone. */
        private boolean retreating;
        /** Flee: the escape's heading kept between ticks, the tick of the last flee step, flees begun. */
        private int fleeHeading = -1, fleeTick = -2, flees;
        /** The first FLEE_LOG flee starts (fleeStart), reported as the result's flee_log. */
        static final int FLEE_LOG = 8;
        private final java.util.List<Map<String, Object>> fleeLog = new java.util.ArrayList<>();
        /** The mobs this tick ({x, z}), steered away from by a flee. */
        private final java.util.List<double[]> around = new java.util.ArrayList<>();
        /** The mobs the fight sees this tick (a raised shield answers any of them, not just the target). */
        private final java.util.List<LivingEntity> near = new java.util.ArrayList<>();
        /** Times the shield went up, and ticks it was up. */
        private int raises, blockTicks;
        /** Poke: the eye-to-box gap a tick ago (NaN for a new target), to lead the band by its closing. */
        private double lastEye = Double.NaN;
        /** The nearest the target came (feet to feet), the fuse margin's evidence. */
        private double closest = Double.POSITIVE_INFINITY;
        /** Set by step for controls: forward (-1 back, 0, 1), jump, sprint. A draw is an item use of its own. */
        private double forward;
        private boolean jump, sprint;
        /** The crit hop taken (CritForecast), its input held until it lands; null on the ground. */
        private CritForecast.Hop hop;
        /** The tracked target's feet {x, z} a tick ago (null: new target), and its step since ({dx, dz}). */
        private double[] targetAt, targetStep;
        /** Health the tracked target lost since the last tick (a charge hit lands in the player's own tick). */
        private double dealtNow;
        /** The body's feet a tick ago: its move, given to the server as the known movement a charge reads. */
        private Vec3 lastPos;
        /** Lance: the melee weapon has the target (LanceLogic hands back only far out). */
        private boolean lanceMelee;
        /** Lance evidence: ticks per move, and the first FLEE_LOG charge hits (tick, eye gap, last move). */
        private final Map<String, Integer> lanceMoves = new java.util.TreeMap<>();
        private final java.util.List<Map<String, Object>> chargeLog = new java.util.ArrayList<>();
        private LanceLogic.Move lastLance;

        private FightTask(long sequence, FightLogic.Params params, CombatLogic.Tactic tactic, UUID requested,
                          java.util.function.Function<ServerPlayer, RouteGeometry.Reader> reader) {
            this.sequence = sequence; this.params = params; this.tactic = tactic; this.requested = requested;
            this.reader = reader;
            this.reach = FightLogic.reach(params.radius(), tactic.keepMax());
        }

        /** Adds what the tracked target lost since the last look; a dead one is counted (an arrow's kill) and let go. */
        /** The death's own source: the last tick's damage source may be gone by the time the death is seen. */
        @Override public void died(ServerPlayer player, String source) {
            if (hurt == null) hurt = new FightLogic.Hurt(player.getMaxHealth());
            hurt.seen(0, source);
        }

        private void account(ServerPlayer player) {
            if (hurt == null) hurt = new FightLogic.Hurt(player.getHealth());
            hurt.seen(player.getHealth(), hurtSource(player));
            dealtNow = 0;
            if (tracked == null) return;
            dealtNow = FightLogic.dealt(trackedHealth, tracked.getHealth());
            damage += dealtNow;
            trackedHealth = tracked.getHealth();
            credit(player, tracked);
            if (!tracked.isAlive()) tracked = null;
        }

        private void credit(ServerPlayer player, LivingEntity target) {
            if (kills.credit(target.getUUID().toString(), typeId(target), target.getHealth(), target.getKillCredit() == player)
                    && target.getUUID().equals(requested))
                diedAt = java.util.List.of(target.getX(), target.getY(), target.getZ());
        }

        private void track(LivingEntity target) {
            if (target != tracked) {
                tracked = target;
                trackedHealth = target.getHealth();
                lastEye = Double.NaN;
                targetAt = null;
            }
            targetStep = targetAt == null ? null : new double[] {target.getX() - targetAt[0], target.getZ() - targetAt[1]};
            targetAt = new double[] {target.getX(), target.getZ()};
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
            // A client reports each move, and the server keeps it as the known movement a spear's charge
            // reads (KineticWeapon.getMotion); the body has no client, so its last move is given here.
            Vec3 pos = player.position();
            if (lastPos != null) player.setKnownMovement(pos.subtract(lastPos));
            lastPos = pos;
            account(player);
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
            if (FightLogic.avoid(candidates, CombatLogic.fightsCreepers(tactic))) return "avoid";
            var pick = FightLogic.pick(requested == null ? null : requested.toString(), candidates, reach);
            if (pick == null) { stopDraw(player); return "done"; }
            LivingEntity target = entities.get(pick.uuid());
            track(target);
            around.clear();
            near.clear();
            for (var e : entities.values()) { around.add(new double[] {e.getX(), e.getZ()}); near.add(e); }
            double distance = Math.sqrt(player.distanceToSqr(target));
            closest = Math.min(closest, distance);
            if (noPathSince >= 0) {
                // no walk to it: hold and let it come; one that keeps off is left to the guard
                String call = FightLogic.noPath(noPathSince, noPathAt, ticks, distance);
                if ("closing".equals(call)) { noPathSince = ticks; noPathAt = distance; }
                else if ("unreachable".equals(call)) { stopDraw(player); return "unreachable"; }
            }
            switch (tactic.style()) {
                case "snipe" -> {
                    if (CombatLogic.snipeClose(distance, tactic.melee() != null)) { stopDraw(player); melee(player, target, tactic.melee()); }
                    else { lowerShield(player); snipe(player, target, distance); }
                }
                case "poke" -> { lowerShield(player); poke(player, target); }
                case "lance" -> { lowerShield(player); lance(player, target, distance); }
                case "hit_and_back" -> hitAndBack(player, target, distance);
                case "throw" -> throwTrident(player, target, distance);
                default -> melee(player, target, params.weapon());  // melee and hunt
            }
            return null;
        }

        // -- techniques (rules in CombatLogic; game facts in COMBAT_MECHANICS_26.3.md)

        private boolean inReach(ServerPlayer player, Entity target) {
            return player.isWithinEntityInteractionRange(target, 0) && player.hasLineOfSight(target);
        }

        /**
         * The off-hand shield (ShieldLogic): up while a melee mob would reach the body before a late raise blocks,
         * down the tick a strike is ready in reach. One an axe disabled is on cooldown: none until it ends.
         */
        private void guard(ServerPlayer player, boolean reach) {
            ItemStack off = player.getOffhandItem();
            boolean shield = off.has(DataComponents.BLOCKS_ATTACKS) && !player.getCooldowns().isOnCooldown(off);
            boolean blocking = shielded(player);
            if (blocking) blockTicks++;
            boolean strike = reach && player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG;
            switch (ShieldLogic.act(shield, blocking, strike, shield && meleeThreat(player))) {
                case RAISE -> { player.gameMode.useItem(player, player.level(), off, InteractionHand.OFF_HAND); raises++; }
                case LOWER -> player.stopUsingItem();
                case HOLD, NONE -> { }
            }
        }

        private static boolean shielded(ServerPlayer player) {
            return player.isUsingItem() && player.getUsedItemHand() == InteractionHand.OFF_HAND;
        }

        /** A shield the fight raised comes down before a technique that uses the main hand (a draw, a charge). */
        private void lowerShield(ServerPlayer player) { if (shielded(player)) player.stopUsingItem(); }

        /**
         * Whether some mob in the fight hits in melee and will be in reach before a raise blocks: archers,
         * spellcasters and creepers do not swing, and a stunned ravager is the opening to strike, not a threat.
         */
        private boolean meleeThreat(ServerPlayer player) {
            var body = player.getBoundingBox();
            for (var e : near) {
                if (e instanceof net.minecraft.world.entity.monster.RangedAttackMob
                        || e instanceof net.minecraft.world.entity.monster.illager.SpellcasterIllager
                        || e instanceof net.minecraft.world.entity.monster.Creeper) continue;
                if (e instanceof net.minecraft.world.entity.monster.Ravager r && r.getStunnedTick() > 0) continue;
                var box = e.getBoundingBox();
                double gx = Math.max(0, Math.max(box.minX - body.maxX, body.minX - box.maxX));
                double gy = Math.max(0, Math.max(box.minY - body.maxY, body.minY - box.maxY));
                double gz = Math.max(0, Math.max(box.minZ - body.maxZ, body.minZ - box.maxZ));
                double dx = player.getX() - e.getX(), dz = player.getZ() - e.getZ(), d = Math.hypot(dx, dz);
                Vec3 v = e.getDeltaMovement();
                double closing = d < 1e-6 ? 0 : (v.x * dx + v.z * dz) / d;
                if (ShieldLogic.threatened(Math.sqrt(gx * gx + gy * gy + gz * gz), closing)) return true;
            }
            return false;
        }

        /**
         * Crit melee: jump so the strike on the way down lands where the target will be (CritForecast:
         * in place, backward from a mob walking in, walking or sprinting at one backing off), and step
         * on the ground until a hop will land. Round an obstacle or out of sight, it walks in as before.
         */
        private void melee(ServerPlayer player, LivingEntity target, String weapon) {
            holdWeapon(player, weapon);
            aim(player, target);
            boolean reach = inReach(player, target);
            guard(player, reach);
            if (tactic.crit() && CombatLogic.kind(heldId(player)).crits() && critHere(player) && chase == null
                    && player.hasLineOfSight(target)) {
                forecastMelee(player, target, reach);
                if (reach) noPathSince = -1;
                return;
            }
            hop = null;
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

        private void forecastMelee(ServerPlayer player, LivingEntity target, boolean reach) {
            if (!player.onGround()) {  // mid-hop: keep its input, strike on the way down
                if (hop != null) { forward = hop.forward; sprint = hop.sprint; }
                if (reach && falling(player) && player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG) {
                    player.setSprinting(false);
                    strike(player, target);
                }
                return;
            }
            hop = null;
            boolean ahead = stepCode(player, target.getX() - player.getX(), target.getZ() - player.getZ()) != 0;
            boolean behind = stepCode(player, player.getX() - target.getX(), player.getZ() - target.getZ()) != 0;
            boolean run = player.getFoodData().getFoodLevel() > EscapeLogic.SPRINT_FOOD;
            var plan = CritForecast.plan(critScene(player, target, targetStep), ahead, behind, run);
            if (plan.hop() != null) {
                hop = plan.hop();
                jump = true; forward = hop.forward; sprint = hop.sprint;
                return;
            }
            switch (plan.step()) {
                case APPROACH -> { approach(player, target); sprint = true; }
                case RETREAT -> walk(player, target, -1);
                case STRIKE -> {  // upon the body with no crit landing safely: a sprint hit knocks it back
                    if (reach && player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG) {
                        player.setSprinting(true);
                        strike(player, target);
                    }
                }
                case HOLD -> { }
            }
        }

        /** A flee's start, the evidence of why it fled: tick, cause, gaps, and a creeper's swell state. */
        private Map<String, Object> fleeStart(ServerPlayer player, LivingEntity target, String cause) {
            var out = new LinkedHashMap<String, Object>();
            out.put("tick", ticks);
            out.put("cause", cause);
            out.put("distance", round1(Math.sqrt(player.distanceToSqr(target))));
            out.put("eye", round1(Math.sqrt(target.getBoundingBox().distanceToSqr(player.getEyePosition()))));
            if (target instanceof net.minecraft.world.entity.monster.Creeper c) {
                out.put("swell_dir", c.getSwellDir());
                out.put("swelling", Math.round(c.getSwelling(0) * 100) / 100.0);
                out.put("ignited", c.isIgnited());
            }
            return out;
        }

        private static boolean swelling(LivingEntity target) {
            return target instanceof net.minecraft.world.entity.monster.Creeper c && CombatLogic.lit(c.getSwellDir(), c.getSwelling(0));
        }

        /** Any swell left: hit-and-back walks back inside the fuse, so it waits for the swell to drain fully. */
        private static boolean swollen(LivingEntity target) {
            return target instanceof net.minecraft.world.entity.monster.Creeper c
                    && (c.getSwellDir() > 0 || c.getSwelling(0) > 0);
        }

        /**
         * Face away and sprint as the escape op does: each grounded tick the safe heading with the least
         * pressure from every mob around (EscapeTask.steer), jumping where a sprint-jump lands level, so a
         * blocked straight line is stepped round instead of stood in. Walking backwards is too slow to
         * outrun a swell. Cornered (no safe heading): it holds.
         */
        private void flee(ServerPlayer player, LivingEntity target, String cause) {
            if (ticks - fleeTick > 1) {  // a new flee: its heading is picked afresh, and its start recorded
                flees++;
                fleeHeading = -1;
                if (fleeLog.size() < FLEE_LOG) fleeLog.add(fleeStart(player, target, cause));
            }
            fleeTick = ticks;
            if (!player.onGround()) {  // mid-jump: hold the heading taken off on
                forward = fleeHeading < 0 ? 0 : 1;
                sprint = forward != 0;
                return;
            }
            var steer = EscapeTask.steer(player, new RouteGeometry(reader.apply(player)), around, fleeHeading);
            fleeHeading = steer.heading();
            if (fleeHeading < 0) return;
            forward = 1;
            sprint = steer.sprint();
            jump = steer.jump();
        }

        /**
         * Spear: hold the band just past the target's reach and jab at full charge. The band is
         * judged a few ticks ahead at the gap's closing speed (CombatLogic.poke), so the body backs off
         * before a target walking in is inside it. A creeper's band (FUSE_KEEP) keeps it from lighting;
         * one that lights anyway is outrun first. A back step that is blocked is stepped round (flee).
         */
        private void poke(ServerPlayer player, LivingEntity target) {
            poke(player, target, params.weapon(), tactic.keepMin(), tactic.keepMax());
        }

        /** The jab held in keepMin..keepMax (eye to box) with the given spear; the lance's close technique too. */
        private void poke(ServerPlayer player, LivingEntity target, String weapon, double keepMin, double keepMax) {
            holdWeapon(player, weapon);
            double eye = Math.sqrt(target.getBoundingBox().distanceToSqr(player.getEyePosition()));
            double closing = Double.isNaN(lastEye) ? 0 : lastEye - eye;
            lastEye = eye;
            var move = CombatLogic.poke(eye, closing, Math.sqrt(player.distanceToSqr(target)), keepMin,
                    keepMax, swelling(target), retreating);
            retreating = move == CombatLogic.Move.FLEE;
            if (retreating) { flee(player, target, "swell"); return; }
            aim(player, target);
            if (move == CombatLogic.Move.APPROACH) approach(player, target);
            else if (move == CombatLogic.Move.RETREAT) {
                walk(player, target, -1);
                if (forward == 0) { flee(player, target, "blocked"); return; }  // the back step is blocked: round it
            }
            if (move != CombatLogic.Move.APPROACH) noPathSince = -1;  // in its band: reached
            var stack = player.getMainHandItem();
            var spear = stack.get(DataComponents.PIERCING_WEAPON);
            // the item's own minimum charge, and full charge for full damage (the jab resets it)
            boolean charged = !player.cannotAttackWithItem(stack, 5) && player.getAttackStrengthScale(0.5f) > CombatLogic.STRONG;
            if (spear != null && player.hasLineOfSight(target) && CombatLogic.jabReach(eye) && charged) {
                float before = target.getHealth();
                spear.attack(player, net.minecraft.world.entity.EquipmentSlot.MAINHAND);  // the server's STAB path
                if (target.getHealth() < before || !target.isAlive()) hits++;
                credit(player, target);
            }
        }

        /** The carried spear the lance charges with: the requested one, else any; null when none is carried. */
        private ItemStack spearStack(ServerPlayer player) {
            var inv = player.getInventory();
            ItemStack any = null;
            for (int i = 0; i < 36; i++) {
                var stack = inv.getItem(i);
                if (stack.isEmpty() || stack.get(DataComponents.KINETIC_WEAPON) == null) continue;
                String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                if (id.equals(params.weapon())) return stack;
                if (any == null) any = stack;
            }
            return any;
        }

        /**
         * Spear charge (LanceLogic): hold the use and sprint at the target so its box crosses the look 2.3 to
         * 4.2 from the eye on a fast tick, sprint-jumping when the ground run is too slow for it (a skeleton
         * backing off). Inside the charge's reach the melee weapon, when carried, takes over until the target
         * is far out again; a lone spear jabs there instead of backing off for a run-up; a creeper is outrun
         * after each hit. Without a spear it is the melee fight.
         */
        private void lance(ServerPlayer player, LivingEntity target, double distance) {
            var stack = spearStack(player);
            var kinetic = stack == null ? null : stack.get(DataComponents.KINETIC_WEAPON);
            if (kinetic == null) { lanceMelee = false; melee(player, target, tactic.melee()); return; }
            String spear = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
            boolean creeper = target instanceof net.minecraft.world.entity.monster.Creeper;
            double eye = Math.sqrt(target.getBoundingBox().distanceToSqr(player.getEyePosition()));
            double dx = target.getX() - player.getX(), dz = target.getZ() - player.getZ(), across = Math.hypot(dx, dz);
            double ux = across > 1e-6 ? dx / across : 0, uz = across > 1e-6 ? dz / across : 0;
            var velocity = player.getDeltaMovement();
            double bodyV = velocity.x * ux + velocity.z * uz;
            double closing = targetStep == null ? 0 : -(targetStep[0] * ux + targetStep[1] * uz);
            boolean using = player.isUsingItem() && player.getUseItem().get(DataComponents.KINETIC_WEAPON) != null;
            boolean hit = using && dealtNow > 0;
            if (hit) {
                hits++;
                credit(player, target);
                if (chargeLog.size() < FLEE_LOG) {
                    var known = player.getKnownMovement();
                    chargeLog.add(new LinkedHashMap<>(Map.of("tick", ticks, "eye", round1(eye),
                            "dealt", round1(dealtNow), "speed", round1(Math.hypot(known.x, known.z) * 20),
                            "after", lastLance == null ? "none" : lastLance.name().toLowerCase())));
                }
            }
            int window = kinetic.damageConditions().map(KineticWeapon.Condition::maxDurationTicks).orElse(0);
            var act = LanceLogic.decide(new LanceLogic.View(eye, distance, bodyV, closing, player.onGround(), using,
                    using ? player.getTicksUsingItem() : 0, kinetic.delayTicks(), window, tactic.melee() != null,
                    creeper, swelling(target), swollen(target), retreating, lanceMelee, hit));
            retreating = act.fleeing();
            lanceMelee = act.inMelee();
            lanceMoves.merge(act.move().name().toLowerCase(), 1, Integer::sum);
            lastLance = act.move();
            if (act.move() == LanceLogic.Move.MELEE || act.move() == LanceLogic.Move.JAB) {
                if (using) player.stopUsingItem();
                if (act.move() == LanceLogic.Move.JAB) {
                    poke(player, target, spear, LanceLogic.JAB_KEEP_MIN, LanceLogic.JAB_KEEP_MAX);
                }
                else melee(player, target, tactic.melee());
                return;
            }
            hop = null;
            holdWeapon(player, spear);
            switch (act.use()) {
                case START -> useHeld(player);
                case RESTART -> { player.stopUsingItem(); useHeld(player); }
                case STOP -> player.stopUsingItem();
                case KEEP -> { }
            }
            if (act.move() == LanceLogic.Move.FLEE) { flee(player, target, hit ? "after_hit" : "swell"); return; }
            aimLevel(player, target);
            switch (act.move()) {
                case CHARGE -> { approach(player, target); sprint = true; }
                case BURST -> {
                    if (stepCode(player, dx, dz) != 0) { jump = true; forward = 1; sprint = true; }
                    else { approach(player, target); sprint = true; }  // the way ahead is blocked: round it
                }
                case BACK -> {
                    walk(player, target, -1);
                    if (forward == 0) { flee(player, target, "blocked"); return; }
                }
                case HOLD, FLEE, MELEE, JAB -> { }
            }
            if (eye <= LanceLogic.FAR + 1) noPathSince = -1;
        }

        private static void useHeld(ServerPlayer player) {
            player.gameMode.useItem(player, player.level(), player.getMainHandItem(), InteractionHand.MAIN_HAND);
        }

        /**
         * The charge hits along the look, and its speed is the move along it: look level (at the target's
         * box where the eye's height crosses it), so the run is not spent climbing the look. An enderman's
         * legs, never its eyes.
         */
        private void aimLevel(ServerPlayer player, LivingEntity target) {
            if (tactic.noGaze()) { aim(player, target); return; }
            var box = target.getBoundingBox();
            Vec3 eye = player.getEyePosition();
            double y = Math.clamp(eye.y, box.minY + .2, Math.max(box.minY + .2, box.maxY - .2));
            BodyBuild.aim(player, (float) Math.toDegrees(Math.atan2(-(target.getX() - eye.x), target.getZ() - eye.z)),
                    PlacementLogic.pitchTo(target.getX() - eye.x, y - eye.y, target.getZ() - eye.z));
        }

        /** Creeper: a sprint hit knocks it back, then keep off until its swell is gone. */
        private void hitAndBack(ServerPlayer player, LivingEntity target, double distance) {
            holdWeapon(player, params.weapon());
            boolean reach = inReach(player, target);
            guard(player, reach);
            String plan = CombatLogic.hitAndBack(retreating, distance, swollen(target), reach, player.getAttackStrengthScale(0.5f));
            switch (plan) {
                case "strike" -> { aim(player, target); player.setSprinting(true); strike(player, target); retreating = true; }
                case "retreat" -> flee(player, target, "after_hit");
                default -> {
                    retreating = false; aim(player, target); approach(player, target); sprint = true;
                    if (reach) noPathSince = -1;
                }
            }
        }

        /**
         * Trident (Loyalty): the bow's band, a 10-tick draw, a throw with drop and lead at 2.5 a tick. While it is
         * out (in flight, coming back), a target that closes in meets the backup weapon and the band is kept
         * otherwise. One without Loyalty is never thrown (it would not come back): it is swung.
         */
        private void throwTrident(ServerPlayer player, LivingEntity target, double distance) {
            String trident = params.weapon();
            ItemStack held = carried(player, trident);
            boolean close = distance < CombatLogic.SNIPE_CLOSE;
            if (held != null && EnchantmentHelper.getTridentReturnToOwnerAcceleration(
                    (net.minecraft.server.level.ServerLevel) player.level(), held, player) <= 0) {
                stopDraw(player);
                melee(player, target, trident);
                return;
            }
            if (held == null || close) {  // out and coming back, or upon the body: swing what there is
                stopDraw(player);
                String swing = tactic.melee() != null ? tactic.melee() : held != null ? trident : null;
                if (close && swing != null) { melee(player, target, swing); return; }
                aim(player, target);
                var move = CombatLogic.keep(distance, tactic.keepMin(), tactic.keepMax());
                if (move == CombatLogic.Move.APPROACH) walk(player, target, 1);
                else if (move == CombatLogic.Move.RETREAT) walk(player, target, -1);
                if (player.hasLineOfSight(target)) noPathSince = -1;
                return;
            }
            lowerShield(player);
            shoot(player, target, distance, CombatLogic.TRIDENT_DRAW, CombatLogic.TRIDENT_SPEED);
        }

        /** The stack of `item` in the main inventory, or null. */
        private static ItemStack carried(ServerPlayer player, String item) {
            var inv = player.getInventory();
            for (int i = 0; i < 36; i++) {
                var stack = inv.getItem(i);
                if (!stack.isEmpty() && BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals(item)) return stack;
            }
            return null;
        }

        /** Bow: keep the band, draw 20 ticks, aim with drop and lead, release. */
        private void snipe(ServerPlayer player, LivingEntity target, double distance) {
            shoot(player, target, distance, CombatLogic.FULL_DRAW, CombatLogic.ARROW_SPEED);
        }

        /** The held projectile weapon (a bow, a trident): keep the band, draw `fullDraw`, aim at `speed`, release. */
        private void shoot(ServerPlayer player, LivingEntity target, double distance, int fullDraw, double speed) {
            holdWeapon(player, params.weapon());
            var move = CombatLogic.keep(distance, tactic.keepMin(), tactic.keepMax());
            boolean sight = player.hasLineOfSight(target);
            Vec3 from = player.getEyePosition().subtract(0, .1, 0), at = target.getBoundingBox().getCenter();
            Vec3 v = target.getDeltaMovement();
            double[] shot = CombatLogic.aim(at.x - from.x, at.y - from.y, at.z - from.z, v.x, v.y, v.z, speed);
            if (shot != null) BodyBuild.aim(player, (float) shot[0], (float) shot[1]);
            else aim(player, target);
            // out of sight: walk round what hides it (no shot is released without sight, so the
            // chase may turn the body); in sight, step with the aim kept on the shot
            if (!sight) approach(player, target);
            else if (move == CombatLogic.Move.APPROACH || shot == null) walk(player, target, 1);
            else if (move == CombatLogic.Move.RETREAT) walk(player, target, -1);
            if (sight) noPathSince = -1;
            // The draw is an item use run here, as a client sends use-item when no entity is in its reach;
            // through the click helper, its entity-reach guard cancelled the draw whenever a mob stood 3 to
            // 4.5 blocks along the aim (CombatLogic.draw).
            switch (CombatLogic.draw(player.isUsingItem(), player.getTicksUsingItem(), fullDraw, sight, shot != null)) {
                case START -> player.gameMode.useItem(player, player.level(), player.getMainHandItem(), InteractionHand.MAIN_HAND);
                case RELEASE -> { player.releaseUsingItem(); shots++; }
                case HOLD, NONE -> { }
            }
        }

        /** Ends a draw without loosing the arrow (the fight turns to its melee weapon). */
        private void stopDraw(ServerPlayer player) {
            if (player.isUsingItem() && player.getUsedItemHand() == InteractionHand.MAIN_HAND) player.stopUsingItem();
        }

        private void strike(ServerPlayer player, LivingEntity target) {
            if (BodyEntity.strike(player, target)) crits++;
            hits++;
            credit(player, target);
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
            // an item in use (a draw) keeps the body held, so a release of the controls cannot end it
            if (forward == 0 && !jump && !player.isUsingItem()) return null;
            boolean run = sprint && forward > 0 && player.getFoodData().getFoodLevel() > EscapeLogic.SPRINT_FOOD;
            return new BodyRuntime.Controls("native-fight", tick, tick, tick + 2, 2, forward, 0, 0, 0, jump,
                    false, run, false, false, false, false, player.getInventory().getSelectedSlot());
        }

        @Override public void stop(ServerPlayer player) { if (player.isUsingItem()) player.stopUsingItem(); }

        @Override public Map<String, Object> result(ServerPlayer player) {
            account(player);  // a last look: an arrow may have landed since the step
            var out = new LinkedHashMap<String, Object>();
            out.put("kills", kills.count());
            if (kills.count() > 0) out.put("kill_types", new LinkedHashMap<>(new java.util.TreeMap<>(kills.types())));
            if (!hurt.taken().isEmpty()) out.put("hurt_by", new LinkedHashMap<>(new java.util.TreeMap<>(hurt.taken())));
            if (!player.isAlive() && hurt.last() != null) out.put("killed_by", hurt.last());
            if (raises > 0) { out.put("raises", raises); out.put("block_ticks", blockTicks); }
            out.put("hits", hits);
            out.put("crits", crits);
            out.put("shots", shots);
            out.put("damage", Math.round(damage * 10) / 10.0);
            out.put("style", tactic.style());
            out.put("ticks", ticks);
            out.put("health", player.getHealth());
            out.put("flees", flees);
            if (!fleeLog.isEmpty()) out.put("flee_log", fleeLog);
            if (Double.isFinite(closest)) out.put("closest", round1(closest));
            if (!lanceMoves.isEmpty()) out.put("lance", new LinkedHashMap<>(lanceMoves));
            if (!chargeLog.isEmpty()) out.put("charge_log", chargeLog);
            if (requested != null) {
                out.put("killed", kills.has(requested.toString()));
                if (diedAt != null) out.put("at", diedAt);
            }
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
