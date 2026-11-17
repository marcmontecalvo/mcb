# Risk and damage: when taking a hit is the better move

Status: open design note (2026-11-17). Today only one piece is implemented (the build's
last-resort drop, below). The rest is for when movement, combat and hostile mobs get
their own work.

## The user's direction

Damage is a cost, not a wall. Avoiding it is good, but not at any price:

- A drop that costs a heart or two is fine. Going down to half health, or even to
  about three hearts, is acceptable when the alternative is worse.
- Running down a hill and taking a few 4-5 block drops beats creeping down, placing
  blocks to make steps, and standing still long enough for hostile mobs to arrive.
- Slow and careful has its own risk: time exposed at night, time near mobs, blocks
  spent, and the job not getting done.
- After taking damage, eat if there is food, so health regenerates (vanilla needs
  food at 18 or more to heal by itself).

## What exists now

- `companion_body.KEEP_HEALTH = 6` (three hearts). `hurt_drop()` returns the longest
  drop that keeps at least that much: a fall of h blocks costs h-3 HP, capped at
  `HURT_DROP_MAX = 12` (Python `movement_policy`, Java `RouteMotor`).
- `allowing_hurt_drops()` raises `max_drop` for one goto. The build runner uses it
  only as a last resort in `_Runner._stance`, after the normal stance search and the
  unwedge retry fail (run 20261117T144843447702Z: shut out on an eave 6 above the
  ground).
- `eat_to_heal()` eats the most filling held food when hurt and not full. The build
  calls it after a hurt-drop goto. The gather runner has its own hunger rule
  (`EAT_BELOW = 14`).
- Ordinary routes still use `max_drop = 3`: no route plans a damaging drop unless the
  stance search has run out of options.

## What is missing (for the movement / combat work)

1. **Damage as a route cost, not a gate.** The planner should price a drop of h > 3 as
   time saved versus HP spent, with the HP price rising as health falls (cheap at full
   health, prohibitive near the floor). Today `max_drop` is a hard limit.
2. **Exposure as a cost.** Time spent standing still or edit-building (pillar, bridge,
   step cuts) should carry a risk price that grows at night, in the open, and with
   hostiles in view. That is what makes "run down and take the hit" win over "creep
   down placing blocks".
3. **A health budget per task.** Jobs say how low they will let health go (a build
   near home might accept three hearts; a long trip out might keep more in reserve).
4. **Eat to heal as a standing habit.** Not only after a hurt drop: whenever health is
   below full, food is below 18 and food is held, between actions.
5. **Fall damage modifiers.** Water, hay bales, slime, powder snow, beds and similar
   cushion or cancel a fall; armour and Feather Falling reduce it. Pricing drops should
   use them.
6. **Retreat and recovery.** Below the floor: stop taking damaging moves, back off from
   mobs, eat, and wait to heal before carrying on.

Prove each with a battery scenario (for example: a hillside descent at night with
hostiles spawning, timing the careful route against the fast one and counting the
damage taken).
