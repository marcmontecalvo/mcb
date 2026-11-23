# Companion auto-defence (Nov 23, 2026)

Status: **built, not yet tested live** on `feat/companion-llm` (slices 1-5; slice 6
is the live night test). Requested by the owner on
Nov 23 after the first live gather ended "slain by Zombie" at night. This replaces
the Nov 7 nightfall protocol's "no combat, no reactive flight" (DECISION_MATRIX):
the bot now fights when it can, and hides or runs when it cannot.

## What the owner asked for

- Run, fight or hide, decided by the bot, whatever job it is on (or idle).
- **Hide:** dig down three blocks and place a block above, sealed in a hole that
  mobs cannot reach.
- **Base:** if a base is nearby, run to it and go inside. If the base holds a
  weapon (an axe counts), go and get it, then fight.
- **Health threshold:** adjustable; default three hearts (6 HP). At or below it the
  bot stops fighting and escapes.
- **Allies:** with another player or bot near, stay with them during the fight.

## Design

D6 and D10 hold: Python decides what to do (fight, arm, regroup, shelter, burrow,
avoid); the mod drives every tick of a fight. Model latency is never involved: the
talker can change the settings, but defence runs in code.

### 1. Settings (`bots/<id>/bot.json` `defense`, intent `defense`)

| Field | Default | Meaning |
|---|---|---|
| `floor_health` | 6 (3 hearts) | At or below: never fight; escape. |
| `threat_radius` | 12 | Hostiles nearer than this (and within 6 vertically) are threats. |
| `ally_radius` | 32 | Players or other bots within this count as allies near. |
| `base_radius` | 64 | A base within this is nearby: the place `base`, else `home` unless that is only where the bot first spawned. |

The `defense` intent reads and changes them (`floor_hearts` for people, who count
hearts). Changes persist in `companion-defense.json` beside the host state file
(over the `bot.json` values; a bad saved file is ignored and logged; a bad
`bot.json` key stops the host at start). Defence itself is always on
(abilities are never switched off per task).

### 2. Decision (`companion_defense.decide`, pure)

From the situation: health, position, hostiles seen (type, position, uuid), the best
weapon held, allies (visible players, and bodies of other bots), the base place, base
containers known to hold a weapon (known containers within 16 blocks of the base
place), and whether the ground here can be burrowed (`plan_burrow` from the known
cells; a spot where a burrow failed this defence does not count). Hostiles come from
the observation's `visible_entities`, which the companion's awareness config
(`entity_visibility: visible`) limits to entities in line of sight, so a mob in a
sealed cave is no threat.

- No threat: nothing.
- Only creepers or a warden: **avoid** (move out of their range; never melee).
- Health above the floor:
  - armed: **fight** the nearest threat, leashed to the ally when one is near;
  - a base chest holds a weapon: **arm** (fetch it), then decide again;
  - an ally near: **regroup** beside the ally and fight what comes within reach;
  - a base near: **shelter** (go inside the base);
  - by day: **flee** (walk away; zombies burn and none spawn in the light);
  - at night **burrow**, else **flee**.
- At or below the floor: **regroup** with an ally (they can fight for it), else
  **shelter** at the base, else **burrow** (at night, or by day while it can heal:
  fed to 18 or food held), else **flee** (run from the threats).

A hole is only worth it while waiting gets something (day, or health back). By day
a healthy bot would leave at once, see the same mob and dig in three blocks deeper,
round after round, so the decision takes `night` and `can_heal` from the
observation.

### 3. The fight motor (mcb-body `fight` op)

`attack_entity` allows animals only and gives up when the target steps out of reach
(knockback pushes it out after the first hit). A new per-tick `FightTask`:

- targets the nearest hostile (`RouteReflex.HOSTILES`) within `radius` that is in
  line of sight, or the given uuid first;
- swings at full cooldown when in reach, with the best sword, else axe;
- steps toward a target out of reach only onto safe ground (`RouteMotor.Geometry`),
  and never past `leash` from `anchor` (the ally, the base door);
- ends `clear` when no hostile is left in the radius, `low_health` at or below
  `floor`, or `timeout`. Its result reports kills, hits and health.

`attack_entity` also accepts hostile mobs (players, villagers and tamed animals
stay off limits). The observation gains `time_of_day` (world day time).

### 4. Burrow (`companion_defense.burrow`)

Dig the cell under the feet three times (the body drops into the hole), then place
a held block (dirt, cobblestone, planks, ...) in the cell above the head. Checked
first from known terrain: no fluid, cave or air beside or below the 1x2 pocket,
diggable with the tools held, and a block to seal (the dug blocks count). The bot
waits sealed, eating, until its health is back to the floor plus 4 (or it cannot
heal: hungry with no food held) and it is armed or it is day (`time_of_day` below
12000). "No threat seen for a while" cannot work: sealed in, it sees nothing. Then
it unseals and the guard decides again; getting out is ordinary navigation (the
planner digs or pillars). A failed dig-in marks that spot for this defence.

### 5. The guard (host)

- The host's poll hook checks the latest observation for threats. When the decision
  is not "nothing", it raises `Defend` (a `BaseException`, like `JobCancelled`), so
  the job unwinds through its cleanup and the body halts.
- The worker runs the defence routine until the decision is "nothing", then runs the
  interrupted job again (jobs re-plan from the world and inventory, so a rerun
  resumes). The job stays `running` with stage `defending` meanwhile.
- The idle loop checks the same way.
- `stop` still wins: it cancels a defence routine like any job.
- Chat: the bot says what it does ("Zombie! fighting", "hiding in a hole", "going
  home").

### 6. One step at a time (`Session.defend`)

The host calls `defend(action)` and then `threat()` again, so every step is short:

- **fight:** the `fight` op for at most 200 ticks; a fight that ends at once (no
  target in reach, `busy`) waits 10 ticks so the loop does not spin; a uuid the body
  refuses is fought as "the nearest" instead.
- **avoid / flee:** walk 12 blocks straight away from the threats' centre, else 45
  or 90 degrees off it; none walkable: wait 10 ticks.
- **regroup:** walk to within 3 of the ally, then eat and wait beside them.
- **shelter:** walk to the base place, then eat and wait there.
- **arm:** stand within reach of the chest and withdraw one weapon; the chest's
  contents are learned from the result (a stale record is not fetched twice); a
  chest that cannot be reached or opened is left until the threat is gone.
- **burrow:** as section 4, in one step (a stop or a death still ends it).

`threat()` runs inside every guarded bridge poll, so it reads the newest sample
and never polls; it decides once per server tick.

## Slices (TDD each; one commit each)

1. Settings and the pure decision (`companion_defense.py`).
2. Java: `fight` op, hostile `attack_entity`, `time_of_day`; Python `fight()`.
3. Burrow and unseal.
4. Host guard, resume, idle check, `defense` intent, chat lines, status.
5. Arm from a base chest; shelter at the base.
6. Live on the play instance at night: a zombie while gathering (fight), low health
   (burrow), with the owner near (regroup).

## Open

- Ranged mobs (skeletons) are fought by closing in; a shield and bow come later.
- Armour: wearing what is held is a separate step (it changes who wins).
- Sleeping in a bed instead of hiding (B5 step 4).
- A mob in view that neither side can reach (behind a fence, across a ravine):
  the fight motor holds still until its step ends, and the guard keeps choosing
  fight, so the job waits. Watch for it in the live test; a `fight` end reason
  `unreachable` would let the guard ignore that mob.
