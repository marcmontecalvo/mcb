# Minecraft 26.3 server-side combat mechanics (from javap of minecraft-merged-deobf-26.3.jar)

Units: ticks = 1/20 s. "verified" = read in bytecode. Extracted classes are in the scratchpad `mc/` folder (javap dumps).

## 1. Melee crit (Player.attack, canCriticalAttack)
- `Player.attack(Entity)`: `scale = getAttackStrengthScale(0.5f)`; `strong = scale > 0.9f`.
- Damage: `base = ATTACK_DAMAGE attr`; `enchantBonus = getEnchantedDamage(target, base, src) - base` (called as `*scale`); `base *= baseDamageScaleFactor()` = `0.2 + scale^2 * 0.8`; then `base += Item.getAttackDamageBonus`; if crit: `base *= 1.5` (applies to base only, not to the enchant part); final = `base + enchantBonus*scale`.
- `canCriticalAttack(target)` (all required): `strong` AND `fallDistance > 0` AND `!onGround` AND `!onClimbable` AND `!isInWater` AND `!isMobilityRestricted()` (= has BLINDNESS) AND `!isPassenger` AND target instanceof LivingEntity AND `!isSprinting`.
- No velocity or "falling" check beyond `fallDistance > 0`. No cooldown beyond `strong` (scale > 0.9).
- A ServerPlayer CAN crit via a normal jump. `fallDistance` is raised in `Entity.checkFallDamage` (reached through `Entity.move` -> `doCheckFallDamage`, and from `handleMovePlayer`). Any descent (dy < 0 while not onGround and not in water) adds `-dy`. So: be airborne, past the jump apex, not sprinting, with scale > 0.9, then call `attack`. Sprint-jump disables the crit.

## 2. Sprint knockback and sweep
- Sprint-knockback hit: `isSprinting() && strong` -> plays PLAYER_ATTACK_KNOCKBACK and adds `+0.5` to `causeExtraKnockback` (strength = `getKnockback(...) + 0.5`). The sprint-reset question is unverified: `attack` has no `setSprinting` call, but `causeExtraKnockback` was only partly read.
- `isSweepAttack(strong, crit, knockbackHit)`: `strong && !crit && !knockbackHit && onGround && getKnownMovement().horizontalDistanceSqr() < (getSpeed()*2.5)^2 && mainhand.is(ItemTags.SWORDS)`. Sword only; needs onGround, no crit, no sprint-knockback.
  - Because sprint+strong sets `knockbackHit`, a sweep effectively needs a non-sprinting hit.
  - `getKnownMovement` for a ServerPlayer = `lastKnownClientMovement` (set by `ServerPlayer.setKnownMovement(Vec3)`, per-tick displacement). A bot that never sets it gets a zero or stale value (zero passes the check).
- `doSweepAttack(target, damage, src, scale)`: victims = `level.getEntitiesOfClass(LivingEntity, target.bbox.inflate(1.0, 0.25, 1.0))`, minus self, minus the target, minus `isAlliedTo` (same team), minus marker ArmorStands; need `distanceToSqr(attacker) < 9.0` (3 blocks).
  - There is NO filter on hostility: passive animals, villagers and pets (unless allied) are hit.
  - Sweep damage = `getEnchantedDamage(1 + SWEEPING_DAMAGE_RATIO*damage) * scale`; ratio attr default 0 (max 1), so 1 damage * scale without Sweeping Edge.
  - Each victim gets knockback 0.4.
- Exhaustion 0.1 per hit (`causeFoodExhaustion(0.1f)`).

## 3. Cooldown and weapon stats
- `getCurrentItemAttackStrengthDelay() = (1 / ATTACK_SPEED) * 20` ticks. `getAttackStrengthScale(pt) = clamp((attackStrengthTicker + pt) / delay, 0, 1)`. The ticker resets in `onAttack/resetAttackStrengthTicker` (swing).
- Base attributes: ATTACK_SPEED 4.0, ATTACK_DAMAGE 1.0 (Player), reach ENTITY_INTERACTION_RANGE 3.0 (default).
- Item modifiers are ADD_VALUE onto those. Item damage modifier = `param + ToolMaterial.attackDamageBonus`; item speed modifier = `speedParam`.
- Material bonus: wood 0, gold 0, stone 1, copper 1, iron 2, diamond 3, netherite 4. (Durability: wood 59, stone 131, copper 190, iron 250, gold 32, diamond 1561, netherite 2031.)
- Totals below include the player base 1.

| item | param dmg | total dmg | speed attr | cooldown ticks |
|---|---|---|---|---|
| sword (all tiers) | 3 | wood 4, gold 4, stone 5, copper 5, iron 6, diamond 7, netherite 8 | 1.6 (-2.4) | 12.5 |
| wooden axe | 6 | 7 | 0.8 (-3.2) | 25 |
| gold axe | 6 | 7 | 1.0 (-3.0) | 20 |
| stone axe | 7 | 9 | 0.8 | 25 |
| copper axe | 7 | 9 | 0.8 | 25 |
| iron axe | 6 | 9 | 0.9 (-3.1) | 22.2 |
| diamond axe | 5 | 9 | 1.0 | 20 |
| netherite axe | 5 | 10 | 1.0 | 20 |
| trident (melee) | 8 | 9 | 1.1 (-2.9) | 18.2 |
| spear jab | 0 | wood/gold 1, stone/copper 2, iron 3, diamond 4, netherite 5 | `1/swingSec` (see 4) | `swingSec*20` |

- Tool `Weapon` component: `itemDamagePerAttack` 2 (axes/tools), `disableBlockingForSeconds` 5.0 for axes (`Weapon.AXE_DISABLES_BLOCKING_FOR_SECONDS`).
- Items with `MINIMUM_ATTACK_CHARGE` (spears = 1.0) refuse to attack unless `(ticker + lookahead)/delay >= 1` (`Player.cannotAttackWithItem(stack, lookahead)`).

## 4. Spear (wooden/stone/copper/iron/golden/diamond/netherite_spear; tag `#spears`)
Built by `Item.Properties.spear(material, swingSec, dmgMult, delaySec, dismountDurSec, dismountSpd, kbDurSec, kbSpd, dmgDurSec, dmgRelSpd)`. Components set:
- `KINETIC_WEAPON` = KineticWeapon(contactCooldown 10, delayTicks, dismount/knockback/damage Conditions, forwardMovement 0.38, damageMultiplier, sounds).
- `PIERCING_WEAPON` = PiercingWeapon(dealsKnockback true, dismounts false).
- `ATTACK_RANGE` = AttackRange(minReach 2.0, maxReach 4.5, minCreative 2.0, maxCreative 6.5, hitboxMargin 0.125, mobFactor 0.5).
- `MINIMUM_ATTACK_CHARGE` 1.0.
- `ATTACK_ANIMATION` STAB, `USE_EFFECTS` (true, false, 1.0), `WEAPON` Weapon(1), `DAMAGE_TYPE` spear.
- Attribute modifiers as in the section 3 table. Use animation SPEAR; `getUseDuration` = 72000.

| tier | swingSec | dmgMult | delay s (ticks) | dismount (max s, min b/s) | knockback (max s, min b/s) | damage (max s, min rel b/s) |
|---|---|---|---|---|---|---|
| wood | 0.65 | 0.70 | 0.75 (15) | 5.0, 14 | 10.0, 5.1 | 15.0, 4.6 |
| stone | 0.75 | 0.82 | 0.70 (14) | 4.5, 13 | 9.0, 5.1 | 13.75, 4.6 |
| copper | 0.85 | 0.82 | 0.65 (13) | 4.0, 12 | 8.25, 5.1 | 12.5, 4.6 |
| iron | 0.95 | 0.95 | 0.60 (12) | 2.5, 11 | 6.75, 5.1 | 11.25, 4.6 |
| gold | 0.95 | 0.70 | 0.70 (14) | 3.5, 13 | 8.5, 5.1 | 13.75, 4.6 |
| diamond | 1.05 | 1.075 | 0.50 (10) | 3.0, 10 | 6.5, 5.1 | 10.0, 4.6 |
| netherite | 1.15 | 1.20 | 0.40 (8) | 2.5, 9 | 5.5, 5.1 | 8.75, 4.6 |

(Seconds are converted with `(int)(s*20)`.)

JAB (tap attack), verified in `ServerGamePacketListenerImpl.handlePlayerAction` (case STAB):
- Server does `stack = player.getItemInHand(MAIN_HAND); if (player.cannotAttackWithItem(stack, 5)) return; piercing.attack(player, EquipmentSlot.MAINHAND)`.
- `PiercingWeapon.attack(attacker, slot)`: `dmg = attacker.getAttributeValue(ATTACK_DAMAGE)`; hits ALL entities along the look ray within the stack's `AttackRange` (`ProjectileUtil.getHitEntitiesAlong`, block-collider clip), each via `LivingEntity.stabAttack(slot, entity, dmg, true(knockback), false(dismount), ...)`; then `onAttack`, `postPiercingAttack` (Lunge enchant effects), `swingAndResetAttackStrength`.
- `stabAttack` scales damage by `getAttackStrengthScale*baseDamageScaleFactor` unless the item is the one currently in use; knockback 0.4 for the stab.
- Reach is the item's AttackRange 2.0..4.5 measured from the eye (margin 0.125), not the 3.0 entity-interaction range. Jab does not need onGround or fall and cannot crit (separate code path). Needs full charge (`MINIMUM_ATTACK_CHARGE` 1.0).
- A ServerPlayer bot calls `PiercingWeapon.attack(player, MAINHAND)` after `!player.cannotAttackWithItem(stack, 5)`.

CHARGE (hold use), verified in `Item.use` (KINETIC_WEAPON -> `player.startUsingItem(hand)`, returns CONSUME) and `ItemStack.onUseTick` (server only -> `KineticWeapon.damageEntities(stack, remainingUseTicks, user, slot)` every tick). No release call is needed or used; hold `startUsingItem` and keep moving; `stopUsingItem` ends it.
- `damageEntities` per tick: `elapsed = useDuration(72000) - remaining`; return if `elapsed < delayTicks`; `t = elapsed - delayTicks`.
  - `mySpeed = lookAngle . KineticWeapon.getMotion(user)`; `getMotion = entity.getKnownSpeed() * 20` (blocks/s). For a ServerPlayer `getKnownSpeed` = `lastKnownClientMovement` (set via `ServerPlayer.setKnownMovement(Vec3 perTickDisplacement)`; the vanilla connection sets it on each move packet and zeroes it at `handleClientTickEnd` if no movement arrived). A bot MUST call `setKnownMovement` each tick or speed reads 0 and nothing hits (whether a bot with a fake connection also gets zeroed is unverified).
  - Targets: `ProjectileUtil.getHitEntitiesAlong` over the AttackRange (`getAttackRangeWith(stack)`; player scale 1.0, mob scale 0.2 on speeds).
  - Per target: skip if `wasRecentlyStabbed(target, contactCooldown=10)`; `rel = max(0, mySpeed - lookAngle . getMotion(target))`.
  - Condition.test(t, mySpeed, rel, scale): `t <= maxDurationTicks && mySpeed >= minSpeed*scale && rel >= minRelSpeed*scale`. dismount and knockback use attacker speed; damage uses relative speed (`minRelSpeed`).
  - If none of dismount/knockback/damage pass, no hit. Damage = `ATTACK_DAMAGE base value (1.0, item modifiers NOT included) + floor(rel * damageMultiplier)`, then `stabAttack` (enchants; no cooldown scaling while that item is in use). Dismount via `stopRiding` (not for CANNOT_BE_DISMOUNTED_BY_ITEM_USAGE).
  - Example: wood spear needs mySpeed >= 14 b/s for dismount, >= 5.1 for knockback, rel >= 4.6 b/s for damage. Sprinting is ~5.6 b/s, so ordinary sprinting does damage `1 + floor(5.6*0.7)=4` (wood) but no knockback beyond ~5.1.
- Lunge enchant (`data/minecraft/enchantment/lunge.json`, max 3, hand slot): `post_piercing_attack` effects = 1 item damage, exhaustion 4*level, `apply_impulse` forward magnitude 0.458*level (horizontal only), sound. Requires: not riding, not fall-flying, not in water, food >= 7 (unless creative). Triggered after a jab.

## 5. Bow
- `Item.use` for BowItem -> `startUsingItem`; `getUseDuration` 72000. Shoot with `player.releaseUsingItem()` after N ticks (`BowItem.releaseUsing(stack, level, entity, remaining)`; `n = 72000 - remaining`). Needs a projectile in inventory (`Player.getProjectile`); returns false if none.
- `BowItem.getPowerForTime(n)`: `f = n/20; p = (f*f + 2f)/3; clamp max 1.0`. Full power at n = 20. If `p < 0.1` no shot (n < 3).
- `shoot(... velocity = p*3.0, inaccuracy 1.0, crit = (p == 1.0))`: arrow speed 3.0 b/tick at full draw. Spread: `Projectile.getMovementToShoot` adds triangle(0, 0.0172275*inaccuracy) per axis.
- Arrow: gravity `getDefaultGravity()` = 0.05/tick; in-air drag 0.99/tick (AbstractArrow INERTIA constant; the 0.99f literal is present); in-water 0.6 (`getWaterInertia`). `ARROW_BASE_DAMAGE` = 2.0.
- Damage on hit (`AbstractArrow.onHitEntity`): `d = ceil(clamp(|velocity| * baseDamage(enchant Power via EnchantmentHelper.modifyDamage), 0, MAX_INT))`; if crit flag, `d += random.nextInt(d/2 + 2)`. Full draw: 6, crit arrow 6..10. Pierce level and Punch apply as usual. Crit arrows only at full draw.

## 6. Trident (item `trident`, durability 250, melee 9 dmg / 1.1 speed)
- `TridentItem.releaseUsing`: `n = useDuration - remaining`; needs `n >= 10` (min 10 ticks). Riptide (`getTridentSpinAttackStrength > 0`) needs `isInWaterOrRain && !isPassenger`, else returns false (no throw).
- Also aborts if `nextDamageWillBreak`. Throw: `hurtWithoutBreaking(1)`; spawns `ThrownTrident` via `Projectile.spawnProjectileFromRotation(..., speed 2.5f, inaccuracy 1.0f)` (if riptide == 0); trident goes to the thrown entity (pickup CREATIVE_ONLY if infinite materials).
- Riptide (`strength > 0`): `startAutoSpinAttack` with the launch vector from yaw/pitch scaled by strength; rain/water only.
- `ThrownTrident`: base damage 8.0 (float literal in class), gravity default arrow 0.05, water inertia 0.99. Loyalty (`ID_LOYALTY` byte): in `tick`, when `dealtDamage` or noPhysics and the owner is acceptable, it sets noPhysics, moves up `0.015*loyalty`, and each tick `delta = delta*0.95 + toOwner.normalize()*0.05*loyalty`; discards when within ~1 block of the owner (or drops/discards if the owner is invalid).
- Pickup via `tryPickup`/`playerTouch`.

## 7. Mobs
Creeper (`Creeper`, `SwellGoal`): `maxSwell` default 30 (1.5 s), `explosionRadius` default 3 (explosion power `radius * (powered?2:1)`).
- `swell += swellDir` per tick (clamped >= 0); explodes when `swell >= maxSwell`.
- `SwellGoal.canUse`: `swellDir > 0` OR (target alive AND `distanceToSqr < 9.0` => within 3 blocks) -> `setSwellDir(1)`.
- `tick`: no target or dead -> -1; `distanceToSqr > 49.0` (> 7 blocks) -> -1; no line of sight (`Sensing.hasLineOfSight`) -> -1; else +1. The goal also stops navigation.

Enderman (`Enderman`, `EndermanLookForPlayerGoal`): `isBeingStaredBy(player)` = `PLAYER_NOT_WEARING_DISGUISE_ITEM.test(player)` AND `enderman.isLookingAtMe(player, tol 0.025, scaleByDistance true, visualShape false, enderman.eyeY)`.
- `LivingEntity.isLookingAtMe(looker, tol, scale, visual, ys...)`: `view = looker.getViewVector(1).normalize(); v = (this.x - looker.x, y - looker.eyeY, this.z - looker.z); dist = |v|; hit if view . v/|v| > 1 - tol/dist` (tol divided by distance when scaled) AND `looker.hasLineOfSight(this, COLLIDER, Fluid NONE, y)`. Looking at the enderman's eye height. Angular tolerance shrinks with distance.
- A carved pumpkin on the HEAD slot blocks provocation: `PLAYER_NOT_WEARING_DISGUISE_ITEM` checks `getItemBySlot(HEAD).is(ItemTags.GAZE_DISGUISE_EQUIPMENT)`; the tag contains only `minecraft:carved_pumpkin`.
- Target search range = follow range 64 (attr 64.0); on stare it sets target, `aggroTime` counts ~5 ticks, then locks on. Enderman attrs: health 40, speed 0.3, attack 7.

Skeleton (`AbstractSkeleton.reassessWeaponGoal`, `RangedBowAttackGoal<>(this, 1.0, 20, 15.0F)`):
- `attackIntervalMin`: 20 on HARD (`getHardAttackInterval`), 40 otherwise (`getAttackInterval`) - set via `setMinAttackInterval`.
- `attackRadiusSqr = 15*15 = 225`: shoots when `distSq <= 225` with LOS and `seeTime >= 20`... it stops moving (navigation stop) once in range with LOS held `seeTime >= 20`.
- Draw: releases when `getTicksUsingItem() >= 20`, then `attackTime = attackIntervalMin`. Strafes +/-0.5 (random 30% reversal every 20 ticks); backs off when `distSq < radiusSqr*0.25`, approaches when `> radiusSqr*0.75`.
- `performRangedAttack`: arrow speed 1.6, inaccuracy = `14 - difficultyId*4` (`RangedAttackMob.rangedAttackUncertainty`: Easy 10, Normal 6, Hard 2), aims at target eye with `+ sqrt(dx^2+dz^2)*0.2` arc compensation.

Phantom (`Phantom`, goals): attack damage 6 attribute. Attack strategy: after circling, `nextSweepTick = (8 + nextInt(4)) * 20` ticks (160-220); sweep goal (`PhantomSweepAttackGoal.tick`) flies to the target and hits with `doHurtTarget` when `bbox.inflate(0.2)` intersects the target, then returns to CIRCLE; cancels if it was hurt recently, the target dies, or a cat within 16 blocks scares it. Circle around an anchor ~15-20 blocks above target (`setAnchorAboveTarget` uses `20 + nextInt(20)` offset). Exact speeds/geometry: partly verified only.

Witch (`RangedAttackGoal(this, 1.0, 60, 10.0F)`): attack interval 60 ticks, radius 10 blocks. `performRangedAttack` picks: HARMING default; if target is Player/raid..., HEALING <=4 hp?, REGENERATION; SLOWNESS if `dist >= 8` and target lacks it; POISON if target health >= 8 and lacks it; WEAKNESS if `dist <= 3` and lacks it and `random < 0.25`. Splash potion thrown with velocity 0.75 (0.45 if `dist < 2.0`), aim y = `target.eyeY - 1.1`.

## 8. Shield (BlocksAttacks)
- Shield item: `BLOCKS_ATTACKS` = BlocksAttacks(blockDelaySeconds 0.25, disableCooldownScale 1.0, DamageReduction(horizontalBlockingAngle 90, type empty -> all, base 0, factor 1), itemDamage(threshold 3.0, base 1, factor 1), bypassedBy `#bypasses_shield`, sounds). Durability 336.
- `Item.use`: `if stack.has(BLOCKS_ATTACKS) -> player.startUsingItem(hand)` (CONSUME). `getUseDuration` 72000; animation BLOCK. So a ServerPlayer startUsingItem on a shield does block.
- `LivingEntity.getItemBlockingWith()`: requires `isUsingItem()` and `useItem.getUseDuration - useItemRemaining >= blockDelayTicks` where `blockDelayTicks = round(0.25*20) = 5`. Before 5 ticks of use there is NO block (warmup = 5 ticks).
- Block: damage within the 90 degree horizontal arc in front is fully absorbed (factor 1) unless source is in `#bypasses_shield`; arrows with pierce level > 0 are not blocked (`getItemBlockingWith` checks `AbstractArrow.getPierceLevel`). Shield takes item damage via `hurtBlockingItem`.
- Axe disables shield: `Player.blockUsingItem` reads `attacker.getSecondsToDisableBlocking()` (from the attacker weapon's `Weapon.disableBlockingForSeconds`, axes 5.0) and calls `BlocksAttacks.disable(level, blocker, secs, stack)`: item cooldown `round(secs * disableCooldownScale * 20)` = 100 ticks and `stopUsingItem()`. Only axes (Weapon(2, 5.0f)); swords and spears use the 1-arg Weapon constructor (0 seconds, no disable).

## Unverified / caveats
- Whether a spear hit through the normal entity-interact ATTACK packet (instead of STAB) is rejected: not checked.
- Whether a connectionless bot ServerPlayer has `handleClientTickEnd` zeroing `lastKnownClientMovement` (affects spear charge speed and sweep check): not checked; set it yourself each tick.
- Sprint reset after sprint-knockback: no `setSprinting` in `Player.attack`; `causeExtraKnockback` not fully read.
- Exact Phantom speed/geometry numbers, ThrownTrident damage literal 8.0 (read as a float constant, not traced to the exact call).
- Arrow in-air drag shown as `0.99f` constant in AbstractArrow; not traced to the `getInertia` call site.
