# Minecraft 26.3 mob and player movement in a fight (from javap of minecraft-merged-26.3.jar)

Units: ticks = 1/20 s, distances in blocks. "verified" = read in the bytecode (class.method named). "derived" = arithmetic on verified formulas, not sampled in a live world. "unverified" = not read. Companion to `COMBAT_MECHANICS_26.3.md` (crits, sweep, cooldowns, bow, trident basics); this file does not repeat those.
Javap dumps live in the scratchpad `mc/` folder of the research session.

## 1. Player movement per tick

Order inside one `LivingEntity.aiStep` (verified): `applyInput` (`xxa *= 0.98`, `zza *= 0.98`) -> `isImmobile`/`serverAiStep` (mobs only) -> jump check -> `travel(Vec3(xxa, yya, zza))`. The jump check: if `jumping && onGround && noJumpDelay == 0` then `jumpFromGround()`, `noJumpDelay = 10`; `noJumpDelay` counts down once per tick.

`LivingEntity.travel` -> `travelInAir` (verified) does, in this order:
1. `friction = onGround ? computeModifiedFriction(blockBelow.getFriction(), FRICTION_MODIFIER) : 1.0`. `onGround` is the flag as it stood BEFORE this tick's move.
2. `moveRelative(getFrictionInfluencedSpeed(friction), input)`: input is normalised only if its length > 1, scaled by the speed, rotated by `yRot` (`Entity.getInputVector`), and ADDED to the velocity.
3. `Entity.move(velocity)`.
4. `vy = vy_after_move - getEffectiveGravity()` (0.08; capped at 0.01 with Slow Falling while falling).
5. `vx, vz *= friction * 0.91`; `vy *= 0.98` (air drag `getAirDrag` 0.98, modifiable by the AIR_DRAG_MODIFIER attribute).

| quantity | value | source |
|---|---|---|
| walk speed attribute | 0.1 (Player `createAttributes` constant 0.10000000149) | Player |
| sprint | +0.3 ADD_MULTIPLIED_TOTAL -> 0.13 | `LivingEntity.SPEED_MODIFIER_SPRINTING`, `setSprinting` |
| speed used by travel | `Player.aiStep`: `setSpeed(MOVEMENT_SPEED attr)` every tick | Player.aiStep |
| ground accel `a` | `getSpeed()` unchanged when block friction <= 0.6 (stone, dirt, grass 0.6); for friction > 0.6 (ice) it is `speed * 0.216 / f^3`. Applied to input * 0.98 | `getFrictionInfluencedSpeed` |
| air accel (non-player mob) | 0.02 constant | `LivingEntity.getFlyingSpeed` |
| air accel (Player) | 0.02 walking, 0.025999999 sprinting (x input 0.98); creative flying uses abilities | `Player.getFlyingSpeed` |
| ground drag after move | 0.6 * 0.91 = 0.546 | travelInAir |
| air drag after move | 0.91 horizontal, 0.98 vertical | travelInAir |
| gravity | attribute GRAVITY 0.08 | `Attributes`, `getDefaultGravity` |
| jump power | `JUMP_STRENGTH` 0.42 (0.41999998688) * blockJumpFactor + 0.1*(JumpBoost amp+1) | `getJumpPower` |
| jump sets | `vy = max(vy, jumpPower)` (replaces, not adds) | `jumpFromGround` |
| sprint-jump boost | if `isSprinting`: `+= (-sin(yaw)*0.2, 0, cos(yaw)*0.2)` along the FACING yaw (not the key direction), added before this tick's travel | `jumpFromGround` |
| step height | 0.6 (`STEP_HEIGHT`) | Attributes |
| steady walk displacement | `0.1*0.98/0.454 = 0.2159` b/tick (4.32 b/s); sprint `0.13*0.98/0.454 = 0.2806` (5.61 b/s) | derived |
| stored velocity after a steady ground tick | walk 0.1179, sprint 0.1532 (= displacement * 0.546) | derived |

Notes:
- The jump tick still counts as a ground tick (flag read before the move), so it uses ground accel and ground drag; the sprint boost 0.2 and the ground accel both land in the jump tick's displacement. Air physics starts on the tick after.
- Backward walking has the same speed as forward in this code (no backward factor found in `travel`; the key-to-input mapping (0.98, sneak slowdown) is client side and unverified). A ServerPlayer on a real connection takes its position from move packets; a bot must integrate this itself (unverified how the bot body does it).
- Sprint-jump forbids a crit (`canCriticalAttack` needs `!isSprinting`), see COMBAT_MECHANICS section 1.

### Worked flat-ground jump (all three start from rest at y=0 on 0.6 friction; (b) from full sprint, (c) from steady walk, backward)
`y` and `vy` are identical for all cases. `vy_end` is the velocity at the END of the tick (what the next tick moves with); the tick moves by the previous row's `vy_end` (tick 1 moves by 0.42). `dh` = horizontal displacement that tick (b/tick, along facing; negative = backward).

| tick | y after move | vy_end | (a) standing dh | (b) sprint-jump fwd dh | (c) walk-back jump dh |
|---|---|---|---|---|---|
| 1 (jump tick) | 0.4200 | 0.3332 | 0 | 0.4806 | -0.2159 |
| 2 | 0.7532 | 0.2481 | 0 | 0.2879 | -0.1375 |
| 3 | 1.0013 | 0.1648 | 0 | 0.2875 | -0.1447 |
| 4 | 1.1661 | 0.0831 | 0 | 0.2871 | -0.1513 |
| 5 | 1.2492 | 0.0030 | 0 | 0.2867 | -0.1573 |
| 6 (apex) | 1.2522 | -0.0754 | 0 | 0.2864 | -0.1627 |
| 7 | 1.1768 | -0.1523 | 0 | 0.2861 | -0.1677 |
| 8 | 1.0244 | -0.2277 | 0 | 0.2858 | -0.1722 |
| 9 | 0.7967 | -0.3015 | 0 | 0.2856 | -0.1763 |
| 10 | 0.4952 | -0.3739 | 0 | 0.2854 | -0.1800 |
| 11 | 0.1213 | -0.4448 | 0 | 0.2852 | -0.1834 |
| 12 (lands) | 0.0000 | - | 0 | 0.2850 | -0.1865 |

Totals (derived): apex 1.2522 at tick 6; `vy_end < 0` first at the end of tick 6; first DOWNWARD displacement is tick 7, so `fallDistance > 0` is first true after tick 7's move (y 1.1768). On the landing tick 12 the move is clipped by the floor and `onGround` becomes true; the next tick (13) can jump again (`noJumpDelay` expired after 10), so a held-jump hop cycle is 12 ticks. Horizontal travel over ticks 1-12: standing 0, sprint-jump 3.63 blocks, walk jump 2.04 blocks (backward -2.04). A crit-capable window (no sprint, scale > 0.9) therefore spans ticks 7-11 of a 12 tick hop; the attack sees the state after the previous tick.
Sprint jump keeps ~5.6 b/s in the air; releasing sprint mid-air drops the air accel to 0.02 but the stored velocity is kept and decays only by 0.91/tick.

## 2. Reach

Player (verified):
- `Player.isWithinEntityInteractionRange(Entity, margin)`: false if removed; else `AABB.distanceToSqr(eyePosition) < (ENTITY_INTERACTION_RANGE + margin)^2`. So it is eye point to the nearest point of the target's bounding box (not centre, not feet), strict `<`. Default `ENTITY_INTERACTION_RANGE` attribute = 3.0 (max 64).
- `Player.isWithinAttackRange(stack, aabb, margin)` uses the item's `AttackRange` component; `AttackRange.defaultFor(entity)` = max reach = `ENTITY_INTERACTION_RANGE` (3.0), min 0, margin 0.
- Server tolerance: `ServerGamePacketListenerImpl.handleAttack` and `handleInteract` pass margin 3.0 (check is eye-to-box < 6.0); the real reach limit is enforced by the client raycast. `Player.attack(Entity)` itself performs NO range check (no range call in its bytecode), so a bot must gate its own range.
- Eye height is 1.62 above the feet (standing); the zombie box is 0.6 wide x 1.95 tall, so at equal height the player reaches a zombie at about 3.0 + 0.3 = 3.3 centre-to-centre.

Mob melee (verified, `Mob.isWithinMeleeAttackRange`, `Mob.getAttackBoundingBox`, `Mob.DEFAULT_ATTACK_REACH`):
- `DEFAULT_ATTACK_REACH = sqrt(2.04) - 0.6 = 0.8283`.
- Hit test: `getAttackBoundingBox(reach) = mob.getBoundingBox().inflate(reach, 0, reach)` (unioned with the vehicle box if riding) must INTERSECT the target's hitbox. Inflation is on X and Z only: no vertical reach, and it is a box test, not a radius.
- Axis-aligned: zombie (0.6 wide) vs player (0.6 wide): hits when centre distance along an axis < 0.3 + 0.8283 + 0.3 = 1.428. On the diagonal both axes can be 1.428, i.e. centre distance up to 2.02. Vertically the two boxes must overlap (zombie y..y+1.95, player y..y+1.8): a player jumping up to ~1.25 above a zombie still overlaps (jump apex 1.25).
- If the mob holds an item with an `ATTACK_RANGE` component (spear) `AttackRange.effectiveMaxRange/MinRange` replace the default (mobFactor 0.5 on spear max 4.5).
- `PathfinderMob`/`MeleeAttackGoal.canPerformAttack` = `isTimeToAttack` && `isWithinMeleeAttackRange` && `Sensing.hasLineOfSight`.

## 3. Knockback

`LivingEntity.knockback(strength, dx, dz, src, ...)` (verified):
1. `strength *= 1 - KNOCKBACK_RESISTANCE attr`; return if `strength <= 0`.
2. direction `(dx,dz)` normalised (random tiny vector if both ~0), `push = dir * strength`.
3. `vx = vx/2 - push.x`, `vz = vz/2 - push.z`, `vy = onGround ? min(0.4, vy/2 + strength) : vy`.
Sources of knockback on a player-hit mob:
- Default hurt knockback in `LivingEntity.hurtServer`: `knockback(0.4, sourcePos.x - this.x, sourcePos.z - this.z)` (source position = attacker; pushes away from the attacker). `DEFAULT_KNOCKBACK` = 0.4.
- `Player.causeExtraKnockback` (verified): called from `attack` with `strength = getKnockback(target, src) + (sprint && strong ? 0.5 : 0)`; `getKnockback` = `(ATTACK_KNOCKBACK attr (default 0) + enchant) / 2`. If `strength > 0` and the target is a LivingEntity: `target.knockback(strength, sin(yaw), -cos(yaw))` (pushes along the player's FACING, not the line between them). Then the ATTACKER does `vel *= (0.6, 1, 0.6)` and `setSprinting(false)` (verified: sprint is cancelled after a sprint hit; this also resolves the old caveat). Plain non-sprint melee has strength 0 here: only the 0.4 default applies.
- Resistance: zombie family gets a random bonus `nextDouble() * 0.05` (ADD_VALUE modifier at spawn). Skeleton, creeper, spider, enderman, passive animals have no KNOCKBACK_RESISTANCE entry in `createAttributes` (0).

Resulting horizontal velocity, mob on the ground at rest (derived): non-sprint hit `vx = 0.4`, `vy = 0.4`. Sprint hit: two calls, `vx = 0.4/2 + 0.5 = 0.7`, `vy = min(0.4, 0.2+0.5) = 0.4`. A mob already walking toward you at ~0.06 stored speed loses half of it first.
Passive travel afterwards (derived; tick 1 uses ground drag 0.546 because `onGround` was true, then air drag 0.91; lands at tick 11; mob's own thrust ignored, it is small in the air: accel 0.02*zza):

| ticks after hit | cumulative push, non-sprint 0.4 | cumulative push, sprint 0.7 |
|---|---|---|
| 1 | 0.40 | 0.70 |
| 2 | 0.62 | 1.08 |
| 3 | 0.82 | 1.43 |
| 5 | 1.16 | 2.03 |
| 8 | 1.57 | 2.75 |
| 10 | 1.79 | 3.13 |
| 20 (settled) | 2.07 | 3.62 |

With the zombie bonus resistance (0..0.05) subtract up to 5 percent. A knocked-up mob (vy 0.4) is airborne ~10 ticks with almost no steering. Zombie closing speed is only 0.1165 b/tick, so after a sprint hit it needs ~24 ticks to return the 2.75 blocks it lost by tick 8.

## 4. Mob movement model (how a mob's goals turn into motion)

Shared machinery (verified):
- `Mob.serverAiStep`: every tick sensing, then target/goal selectors (new goals start only every 2nd tick: `(tickCount + id) % 2`), `navigation.tick`, `moveControl.tick`, `lookControl.tick`, `jumpControl.tick`.
- `MoveControl.tick` MOVE_TO: operation resets to WAIT every tick (navigation re-issues it each tick). Computes `f = speedModifier * MOVEMENT_SPEED attr`; `Mob.setSpeed(f)` which ALSO does `setZza(f)` (verified `Mob.setSpeed`); yaw turns toward the target at up to 90 degrees per tick; if the wanted point is within ~0.0005 (dist^2 < 2.5e-7) it sets `zza = 0`; jumps (`jumpControl.jump`) when the wanted Y is above `maxUpStep` and horizontal dist^2 < max(1, bbWidth).
- So the forward thrust is `a = f * zza = f^2` per tick on the ground (zza = f is not 1.0). Steady displacement = `f^2 / 0.454` b/tick (flat 0.6 block, derived). Table (derived, validate by sampling positions):

| mob / mode | attr | modifier | f | a | b/tick | b/s |
|---|---|---|---|---|---|---|
| zombie, husk, drowned (melee) | 0.23 | 1.0 | 0.23 | 0.0529 | 0.1165 | 2.33 |
| baby zombie (+0.5 ADD_MULTIPLIED_BASE) | 0.345 | 1.0 | 0.345 | 0.119 | 0.262 | 5.24 |
| spider, cave spider (melee) | 0.30 | 1.0 | 0.30 | 0.090 | 0.198 | 3.96 |
| creeper (melee approach) | 0.25 | 1.0 | 0.25 | 0.0625 | 0.1377 | 2.75 |
| skeleton / stray / bogged approach | 0.25 | 1.0 | 0.25 | 0.0625 | 0.1377 | 2.75 |
| skeleton family melee (no bow) | 0.25 | 1.2 | 0.30 | 0.090 | 0.198 | 3.96 |
| enderman (melee) | 0.30 | 1.0 | 0.30 | 0.090 | 0.198 | 3.96 |
| pig panic | 0.25 | 1.25 | 0.3125 | 0.0977 | 0.215 | 4.30 |
| cow panic | 0.20 | 2.0 | 0.40 | 0.160 | 0.352 | 7.05 |
| sheep panic | 0.23 | 1.25 | 0.2875 | 0.0827 | 0.182 | 3.64 |
| chicken panic | 0.25 | 1.4 | 0.35 | 0.1225 | 0.270 | 5.40 |

Reaching 90 percent of steady speed takes ~4 ticks (v_n = 0.546 v_{n-1} + a, then drag). Bogged has 16 health; skeleton family createAttributes speed 0.25; Spider health 16 speed 0.3, cave spider health 12 and the same speed; Enderman health 40, attack 7, follow range 64; zombie follow range 35, attack 3.
- `MeleeAttackGoal(mob, speedModifier, followEvenIfNotSeen)` (verified, used by zombie via `ZombieAttackGoal(1.0, false)`, spider `SpiderAttackGoal(1.0, true)`, creeper `(1.0, false)`, enderman `(1.0, false)`, skeleton melee fallback `(1.2, false)`, flags MOVE+LOOK):
  - `canUse` is evaluated at most every 20 ticks per goal (`lastCanUseCheck`); needs a living target and a path to it (or already within melee range). `start`: `moveTo(path, speed)`, aggressive, `ticksUntilNextAttack = 0`.
  - `tick`: look at target (30, 30); `ticksUntilNextPathRecalculation` counts down; when it is <= 0 AND (line of sight OR followEvenIfNotSeen) AND (no pathed point yet OR target moved >= 1 block from the pathed point OR `random < 0.05`): record the target position, `delay = 4 + nextInt(7)`; `+10` if distSq > 1024, `+5` if distSq > 256; `+15` if `navigation.moveTo(target)` fails. So the path to a moving target is refreshed every 4-10 ticks when it moves more than 1 block.
  - Attack: `ticksUntilNextAttack` decrements each tick (min 0); `checkAndPerformAttack`: if 0 AND `isWithinMeleeAttackRange` AND line of sight -> `resetAttackCooldown` (20 ticks) -> swing -> `doHurtTarget`. Attack interval 20 ticks; the FIRST hit is immediate on contact (counter starts at 0).
  - `Mob.doHurtTarget`: damage = ATTACK_DAMAGE attr, then `hurtServer`, then on success `causeExtraKnockback(getKnockback...)` (mobs have attack_knockback 0 by default: only the 0.4 hurt knockback).
- `RandomStrollGoal` (idle): `canUse` random `nextInt(interval)==0` (120 default), position `LandRandomPos`/`WaterAvoiding` within 10 horizontal / 7 vertical.

### 4.1 Zombie, husk, drowned (`Zombie.addBehaviourGoals`, `ZombieAttackGoal`)
- Goal priorities: SpearUse 2, ZombieAttackGoal 3 (speed 1.0), MoveThroughVillage 6, WaterAvoidingStroll 7 (1.0); targets HurtByTarget 1, NearestAttackable(Player, mustSee=true) 2 (follow range 35).
- `ZombieAttackGoal.tick`: calls MeleeAttackGoal.tick; raises arms after 5 ticks when `ticksUntilNextAttack < interval/2` (cosmetic).
- Drowned: `DrownedAttackGoal(1.0, false)` canUse requires `okTarget` = target exists AND (not bright outside OR target in water). `DrownedTridentAttackGoal(1.0, 40, 10.0)` (interval 40, radius 10) when holding a trident. Drowned also has swim goals.
- Forecast rule: the zombie walks the pathfinder route at 0.117 b/tick toward the player's current block, refreshing every 4-10 ticks. Predict `pos(t) = pos0 + dir_to_player * 0.1165 * min(t,4-ish ramp)`. It hits the moment the box test of section 2 passes (axis gap < 0.83) and then every 20 ticks while it stays in range. Retreating from a zombie at >0.12 b/tick (anything faster than 2.4 b/s) is never caught. A knocked-back zombie (section 3) loses 1.6 blocks in 8 ticks (non-sprint) and recovers at 0.117 b/tick.

### 4.2 Spider and cave spider (`Spider.registerGoals`, `LeapAtTargetGoal`, `SpiderAttackGoal`)
- Goals: Float 1, Leap 3 (yd 0.4), SpiderAttackGoal 4 (MeleeAttackGoal 1.0, followEvenIfNotSeen true), stroll 5 (0.8); targets: HurtByTarget, SpiderTargetGoal(Player), SpiderTargetGoal(IronGolem). Navigation: WallClimberNavigation.
- `SpiderTargetGoal.canUse` and `SpiderAttackGoal.canContinueToUse`: if `getLightLevelDependentMagicValue >= 0.5` (day-bright) it will not newly target, and an existing attack drops the target with 1/100 per tick. Hurt spiders still counter via HurtByTarget.
- `LeapAtTargetGoal.canUse` (verified): has a target, not controlled by passenger, `4 <= distSq <= 16` (centre distance 2.0 to 4.0, 3D), `onGround`, and `nextInt(5) == 0` (20 percent per goal check; checks happen every 2nd tick for a non-running goal, so roughly 10 percent per tick). `start`: `delta = (target.x - x, 0, target.z - z)`; if `lengthSqr > 1e-7`: `delta = delta.normalize()*0.4 + oldVelocity*0.2`; `setDeltaMovement(delta.x, 0.4 (yd), delta.z)`. `canContinueToUse` while `!onGround`.
- Leap path (derived): horizontal 0.4 -> ground drag on tick 1, then 0.91/tick; total ~2.07 blocks, airborne ~11 ticks (vy 0.4 start: the same arc as a knockback-up, apex ~0.84, lands tick ~11). A pre-existing run adds 0.2 * its velocity to the launch.
- Forecast: inside 2..4 blocks the spider commits to a leap within ~10 ticks, covers ~2 blocks in a straight line along the launch bearing (it cannot steer in the air: accel 0.02*zza), then melee-attacks every 20 ticks. Predict a landing point 2.07 blocks along the bearing at the launch tick. Dodging sideways 1 block after launch avoids it; the leap itself deals no damage (damage is the melee `doHurtTarget`).

### 4.3 Creeper (`Creeper`, `SwellGoal`, MeleeAttackGoal 1.0)
- Goals: Float 1, SwellGoal 2 (flag MOVE), avoid Ocelot/Cat 3 (6 blocks, 1.0/1.2), MeleeAttack 4, stroll 5 (0.8). `maxSwell` 30 ticks, explosion radius 3. Movement speed attr 0.25.
- `SwellGoal.canUse`: `swellDir > 0` OR (target alive AND `distanceToSqr < 9` (3 blocks)). `start`: `navigation.stop()` (it also holds the MOVE flag at priority 2 so MeleeAttackGoal at 4 cannot run). `tick`: `swellDir = -1` if no target / dead, or `distSq > 49` (more than 7 blocks), or no line of sight; else `+1`. `Creeper.tick`: `swell += swellDir`, clamp >= 0; `swell >= 30` -> explode.
- Does it stop moving while swelling? Yes after the first tick: navigation is stopped, MoveControl is not re-armed, so it coasts (drag 0.546/tick, essentially stopped within ~3 ticks). It does NOT chase while the fuse runs. If you move out to > 7 blocks or break line of sight the fuse decays 1/tick and it resumes chasing; if you stay inside 3..7 blocks while `swellDir > 0` the fuse keeps counting even though you are outside 3 blocks (`canUse` is `swellDir > 0 ||...`).
- Forecast: creeper closes at 0.138 b/tick to < 3 blocks, stops, and explodes 30 ticks later unless you get > 7 blocks away or out of sight. A sprint away (0.28 b/tick) beats it trivially; a hit's knockback of 2.07-3.6 blocks does not by itself stop the fuse but a sprint-hit usually sends it beyond 3 blocks (still swelling).

### 4.4 Skeleton, stray, bogged (`AbstractSkeleton`, `RangedBowAttackGoal(1.0, interval, 15.0F)`)
- With a bow in either hand (`reassessWeaponGoal`): bow goal at priority 4 with `setMinAttackInterval`: skeleton and stray 40 (normal) / 20 (hard), bogged 70 / 50 (verified `getAttackInterval`/`getHardAttackInterval`; Hard is `Difficulty.HARD`). Otherwise melee goal at priority 4: `MeleeAttackGoal(1.2, false)`.
- Skeleton goals also: RestrictSun 2, FleeSun 3 (1.0), AvoidEntity Wolf 3 (6.0, 1.0/1.2), stroll 5 (1.0). Speed attr 0.25.
- `RangedBowAttackGoal` (flags MOVE+LOOK, `radiusSqr = 15^2 = 225`) `tick` (all verified):
  1. `d = distSq(target)`, `canSee = hasLineOfSight`. If `canSee != (seeTime > 0)` then `seeTime = 0`; `canSee ? ++seeTime : --seeTime`.
  2. If `d <= 225` AND `seeTime >= 20`: `navigation.stop()` and `++strafingTime`; else `navigation.moveTo(target, 1.0)` and `strafingTime = -1`.
  3. If `strafingTime >= 20`: with probability 0.3 flip `strafingClockwise`, with probability 0.3 flip `strafingBackwards` (independent), `strafingTime = 0`.
  4. If `strafingTime > -1`: if `d > 225*0.75 = 168.75` (more than 12.99 blocks) `strafingBackwards = false`; else if `d < 225*0.25 = 56.25` (less than 7.5 blocks) `strafingBackwards = true`; then `moveControl.strafe(backwards ? -0.5 : 0.5, clockwise ? 0.5 : -0.5)` (forward, right). Between 7.5 and 13 blocks the backwards flag keeps its last value.
  5. Shooting: if using the item: if `!canSee && seeTime < -60` stop using; else if `canSee`: `t = ticksUsingItem`; at `t >= 20` stop using, `performRangedAttack(target, power(t))`, `attackTime = interval`. If not using: `--attackTime <= 0 && seeTime >= -60` -> `startUsingItem`. `attackTime` starts at -1, so the first draw begins immediately.
- Strafe motion (`MoveControl.strafe` sets `speedModifier = 0.25`, STRAFE operation): `f = 0.25 attr * 0.25 = 0.0625`; `zza = +-0.5`, `xxa = +-0.5` (verified they are taken from the raw strafe fields), so the input vector has length 0.707 and `a = 0.0442` total (0.03125 per axis). Steady displacement derived: 0.0688 b/tick on each axis, 0.0973 b/tick total (1.95 b/s: 1.38 b/s along the facing axis, 1.38 b/s sideways). The mob's yaw faces the target (`lookAt`), so "backwards" is straight away from you and the sideways part circles you. `MoveControl` also checks `isWalkable`; if the strafe step is blocked it falls back to `strafeForwards = 1, strafeRight = 0`.
- Cycle length: draw 20 ticks + wait `interval` ticks = 60 ticks between arrows on Easy/Normal skeleton, 40 on Hard, 90 / 70 for bogged. Arrow velocity 1.6 b/tick, inaccuracy 14 - difficultyId*4 (COMBAT_MECHANICS section 7). Mob does not shoot while `seeTime < 20` after regaining line of sight.
- Forecast for a retreating skeleton: inside 15 blocks with sight it stands still on its feet but is strafing at only ~1.4 b/s away + ~1.4 b/s sideways. Inside 7.5 blocks the backwards flag is forced on and stays until > 13 blocks or a 0.3 flip (every 20 ticks), so a player closing at sprint speed (0.28 b/tick) gains ~0.21 b/tick on a retreating skeleton: from 7 blocks it is in reach (3.0 + 0.3) after ~18 ticks. When the skeleton has no line of sight or is > 15 blocks away it walks toward you at 0.138 b/tick. It never "kites" faster than you can walk. Time the jump-crit so the downward phase (ticks 7-11 of the hop) starts when the gap is ~1.3-3.3 blocks including the mob's retreat; the skeleton keeps shooting at 60 tick intervals regardless of your jumping.

### 4.5 Enderman (`Enderman`, `EndermanLookForPlayerGoal`, `EndermanFreezeWhenLookedAt`)
- Goals: Float 0, FreezeWhenLookedAt 1 (flags JUMP+MOVE), MeleeAttack 2 (1.0, false), stroll 7 (1.0). Speed 0.3 (derived 0.198 b/tick), health 40, attack 7, follow range 64.
- `FreezeWhenLookedAt.canUse`: target is a Player, within 16 blocks (`distSq <= 256`), and `isBeingStaredBy(player)`. While true the enderman does not move (it only turns its head). `isBeingStaredBy` = no carved pumpkin AND `isLookingAtMe` (COMBAT_MECHANICS section 7).
- Teleport triggers (verified):
  - `hurtServer`: damage from a projectile (`#is_projectile`: arrow, trident, thrown) or a thrown potion calls `repeatedlyTryToTeleport` and returns false (an arrow does no damage); other hits go to `Monster.hurtServer` and if the damage's entity is NOT a LivingEntity there is a 90 percent chance (`nextInt(10) != 0`) to `teleport()`. A melee hit from a player/mob (living) does NOT teleport it.
  - `customServerAiStep`: if `isBrightOutside` AND `tickCount >= targetChangeTime + <constant not read>` AND light value > 0.5 AND can see sky AND `(nextFloat()*30 - 0.4)*2 < lightValue` (a tiny chance per tick): clear target and `teleport()`. Water contact also teleports it (`aiStep`, only partly read).
  - `EndermanLookForPlayerGoal.tick`: target staring at it: if within 4 blocks (`distSq < 16`) it `teleport()`s away (`teleportTime = 0`); if the target is more than 16 blocks away (`distSq > 256`) it `teleportTowards(target)` every 30 ticks.
  - `teleport()`: random point `+- 32` horizontally (`(nextDouble-0.5)*64`), `y + nextInt(64) - 32`; `teleportTowards` moves ~16 blocks / 8 vertical toward the entity.
- Forecast: while you look at it within 16 blocks it freezes (a free target); the moment you stop looking it walks at 0.198 b/tick; it does not teleport from your melee hits. Do not shoot it (guaranteed teleport, no damage).

### 4.6 Pig, cow, sheep, chicken (`PanicGoal`)
- Plain `PanicGoal(mob, speed)` (verified constructor constants): pig 1.25, cow 2.0, sheep 1.25, chicken 1.4. Base speed attrs 0.25, 0.20, 0.23, 0.25; health 10, 10, 8, 4. Flag MOVE.
- `shouldPanic` = `getLastDamageSource(40)` is within the last 40 ticks AND is tagged `panic_causes` (data tag: arrow, explosion, mob_attack, mob_projectile, thrown, trident, wind charge, wither, `#is_player_attack`, plus `#panic_environmental_causes` cactus, freeze, hot floor, in_fire, lava, lightning, on_fire). A player melee hit counts (`#is_player_attack`).
- `canUse`: if `shouldPanic`: on fire and water within 5 -> run to water; else `findRandomPosition` = `DefaultRandomPos.getPos(mob, 5, 4)` = a RANDOM reachable point within 5 horizontal / 4 vertical blocks (NOT directed away from the attacker). `start`: `navigation.moveTo(pos, speed)`. `canContinueToUse` = navigation not done.
- Result: after a hit the animal runs to a random nearby point (<= ~5 blocks), then (while the last damage is < 40 ticks old) picks another; a new hit refreshes the 40 tick window. Panic ends 40 ticks after the last damage. The flee direction is unpredictable per hop but the hop length is <= 5 blocks, and the speed is known (table in section 4: pig 0.215, cow 0.352, sheep 0.182, chicken 0.270 b/tick). The panic move cannot outrun a sprinting player (0.28 b/tick) except the cow (0.35) and chicken (0.27 about equal). Hit knockback (section 3) adds 2.07 blocks of passive travel in the first ~10 ticks.

### 4.7 Slime (and magma cube, new `AbstractCubeMob`)
- `AbstractCubeMob$CubeMobMoveControl` (verified): when on the ground it counts down `jumpDelay`; at 0 sets `jumpDelay = 10 + nextInt(20)` (`getJumpDelay`), divided by 3 when aggressive (so 3-9 ticks while hunting) and calls `jumpControl.jump()`. On the ground between jumps it sets `xxa = zza = 0, speed = 0` (it stands still). In the air it keeps `speed = modifier * attr` (so tiny accel 0.02 * zza). `CubeMobAttackGoal.tick` faces the target (10 degrees) and calls `setDirection(yaw, canDealDamage)`; `CubeMobKeepOnJumpingGoal` sets the wanted movement 1.0.
- `jumpFromGround` sets `vy = getJumpPower()` (0.42 base) keeping x/z; speed attr `0.2 + 0.1 * size` (verified constants in `setSize`).
- Contact damage: `push(Entity)` only handles IronGolem; players take damage in `playerTouch` -> `dealDamage`: requires alive, `isWithinMeleeAttackRange`, line of sight, then `hurtServer(mobAttack, getAttackDamage())`; `canDealDamage` = not tiny (size > 1) and effective AI. So there is no attack cooldown: the slime damages you every tick it overlaps you (subject to the player's invulnerability frames).
- Forecast: hop cadence 3-9 ticks while hunting; each hop covers about one block (derived: the jump tick has ground thrust `f^2` plus the 0.02*f air drift, so well under 1 block per hop for a size 1 slime, larger for size 4; unverified by sampling). Time attacks at the ground phases (it is stationary there).

## 5. Trident throw (`TridentItem.releaseUsing`, `ThrownTrident`, `AbstractArrow`)
- Charge: needs `useDuration - remaining >= 10` ticks (verified `bipush 10 if_icmpge`). Throw speed 2.5 b/tick (verified `ldc 2.5f`), inaccuracy 1.0, `hurtWithoutBreaking(1)`. Riptide variant only in water/rain.
- Flight (`AbstractArrow.tick`, verified order): move to new position, then `applyInertia(0.99)` (air: `INERTIA = 0.99f`; in water `ThrownTrident.getWaterInertia = 0.99f`, not the arrow's 0.6), then `applyGravity` (`getDefaultGravity = 0.05`). So `v_{n+1}.xyz = v_n * 0.99` then `vy -= 0.05`.
- Damage on entity hit: `ThrownTrident.onHitEntity` base 8.0 (verified `ldc 8.0f`), then `EnchantmentHelper.modifyDamage` with `DamageSources.trident`; flat (does NOT scale with speed); a hit sets `dealtDamage = true`, `doKnockback`, (post-hit motion not read).
- Loyalty (`ThrownTrident.tick`, verified): when `dealtDamage` (set also after `inGroundTime > 4`) and the owner is acceptable, the entity sets `noPhysics`, lifts `0.015 * loyalty` in Y once, then each tick `delta = delta * 0.95 + normalize(ownerEye - pos) * 0.05 * loyalty`; returns to the owner and is discarded when within ~1 block (`bbWidth` check) of a Player owner (it only returns if `isAcceptibleReturnOwner`: owner exists, alive, not a spectator).
- Forecast: speed 2.5 decays to ~2.0 after 20 ticks, arc `drop ~ 0.05 * n^2/2` -> ~0.4 blocks after 4 ticks, ~2.5 blocks after 10 ticks (derived, add the 0.99 drag).

## 6. Using this to time a jump-crit

Versus an approaching zombie (0.1165 b/tick, melee gate < ~1.43 centre axis-aligned, first hit immediate on contact then 20 tick interval):
1. Player reach is 3.0 eye-to-box, zombie reach is 0.83 box-to-box. The gap of ~1.9 blocks takes the zombie ~16 ticks to cross at full speed (4 ticks ramp).
2. A standing jump lasts 12 ticks and its crit window (ticks 7-11) fits inside that gap. The zombie closes 0.1165 b/tick, so 7 ticks of jump costs ~0.8 blocks of gap: start the jump when the zombie centre is ~4.1 blocks away so that at tick 7 it is ~3.3 away (inside your reach, outside its 1.43). The weapon needs cooldown scale > 0.9 at the attack tick (sword 12.5 ticks).
3. A retreat jump (walking backward) drifts you ~2 blocks back over the hop and the zombie closes ~1.4 blocks; the net spacing stays roughly constant, which makes it a stable way to hit and keep range.
4. After the hit: the zombie is pushed 0.4 (non-sprint crit) i.e. ~1.6 blocks in 8 ticks; its first swing after being re-engaged still comes immediately on contact.

Versus a retreating skeleton (strafes 1.38 b/s away only when its backwards flag is on and distance < 13): a sprint approach closes ~0.21 b/tick faster; a crit needs `!isSprinting`, so plan a sprint to ~4 blocks, release sprint (the stored 0.153 velocity carries), jump so tick 7 lands at 3.3 from the skeleton's box.

## 7. Not verified or open
- Server-side physics of a connectionless ServerPlayer bot (does `travel` run with a synthesised `xxa/zza` on the bot, and does the bot's input get the 0.98 factor): not read. The numbers above are the client-side/`LivingEntity` formulas.
- The player's input mapping (sneak slowdown, backward walk factor) lives in client classes not read.
- The derived mob speeds (`f^2/0.454`) rely on `Mob.setSpeed` setting `zza = f` and no later overwrite; no live sampling was done. A 20-tick position sample of a zombie chasing across flat ground should read ~2.3 blocks.
- Damage invulnerability ticks after a hit (hurt cooldown) and the exact `hurtServer` path order were not traced.
- `Enderman.customServerAiStep` daylight random threshold, `Slime` hop distance, `Spider` leap pre-conditions beyond `canUse`, and `Husk` (inherits Zombie constants; only its hunger effect differs) were only partly read. Panic target selection for sheep/cow/pig variants was read only through the shared `PanicGoal`.
- Vertical block-friction variants other than 0.6 (ice 0.98, slime 0.8, soul sand speed factor) follow the formulas above but were not tabulated.
