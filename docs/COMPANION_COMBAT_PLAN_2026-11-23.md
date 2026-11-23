# Companion combat technique (Nov 23, 2026)

Status: slices 1 to 6 **built** on `feat/companion-llm` (unit-tested; Java `check`
green); slice 7 (live trials) is under way: the first matrix's fixes are in (section 6). It builds on the auto-defence
(`COMPANION_DEFENSE_PLAN_2026-11-23.md`). Every game rule used below comes from the
26.3 bytecode, collected in `COMBAT_MECHANICS_26.3.md`; no number is taken from
wiki memory.

## What the owner asked for (Nov 23)

- **Walking away and escaping must look different.** An escape sprint-jumps
  (jumping while sprinting is faster). It steers away from every hostile around it,
  including ones that spawn while it runs, not only the one it reacts to.
- **Use each weapon properly:** sword, axe, spear ("javelin"), trident and bow. Never
  click as fast as possible. Get the most damage, including jump crits for the sword
  and axe.
- **Different rules for different targets:** hunting an animal is not fighting a
  mob. Mobs differ too: a creeper, a skeleton, a zombie and the Ender Dragon each need
  their own method, and all of them differ from a pig.
- **Try the weapons live:** sword, axe, spear and bow.

## Design

D6 and D10 hold. Python picks the tactic and the weapon (pure, data-driven, tested).
The mod carries the tactic out tick by tick (crit timing, cooldowns, aiming, footwork),
because no Python poll is fast enough for a jump crit or a bow release.

### 1. Walk away versus escape (`companion_defense.decide`)

The single `flee` action becomes two:

| action | when | how |
|---|---|---|
| `walk_away` | healthy, by day, one mob, none closer than 6 blocks, no archer in view | the planned walk that `flee` used before (`walk_to` away from the threats) |
| `escape` | at or below the health floor, at night, two or more mobs, a mob within 6 blocks, an archer in view, a swelling creeper, a warden | the new mod `escape` op (section 3) |

`avoid` (creeper or warden near) becomes `escape`, unless a tactic in section 2 can
fight the creeper.

### 2. Tactics and weapons (`companion_combat.py`, pure)

**Weapons** (bytecode stats; damage includes the player's base 1; cooldown =
20 / attack speed):

| kind | damage by tier | cooldown (ticks) | technique |
|---|---|---|---|
| sword | 4 to 8 | 12.5 | jump crit (×1.5); a grounded hit sweeps |
| axe | 7 to 10 | 20 to 25 | jump crit; disables a shield for 5 s |
| spear | jab 1 to 5; charge 1 + floor(speed × mult) | swing 13 to 23 | jab reaches 2.0 to 4.5 (outreaches a zombie); a sprint charge opens from range |
| trident | 9 melee; 8 thrown | 18.2 | melee with crits; thrown only with Loyalty, or at a target out of reach |
| bow | 6 at full draw (crit arrow 6 to 10) | 20-tick draw | snipe from range with drop and lead |

A crit needs: cooldown above 0.9, falling (`fallDistance > 0`), not on the ground, not
sprinting, not in water or on a ladder. It applies only to base damage. A crit or a
sprint hit never sweeps; a grounded sword hit sweeps every living thing within 3
blocks, animals and pets included.

**Tactic** = style + allowed weapon kinds (best first) + options:

| style | what the mod does |
|---|---|
| `melee` | close in. Jump when the cooldown will be full at the jump's apex, strike on the way down (crit). Where a jump cannot crit (low ceiling, water, ladder), strike at full cooldown. |
| `poke` | spear: hold 2.5 to 4 blocks off and jab at full charge; back off when the target closes inside 2.5. Against a creeper (`outreach`) the band is 3.3 to 4.2, past its fuse; one that swells anyway is outrun first. |
| `hit_and_back` | sprint hit (extra knockback), then back off past 7 blocks while the creeper's swell runs down; again when the swell is back to zero. |
| `snipe` | bow: keep 8 to 15 blocks off, draw 20 ticks, aim with drop and lead, release. Switch to a melee weapon if the target closes within 4. |
| `hunt` | animals: chase at a sprint and take the biggest single hit (one-shot). No sweep while another animal stands within 3 blocks of the target. Never the bow (arrows are wasted on food). |
| `avoid` | never engaged: `escape` instead. |

**Profiles** (entity type to tactic; a table, not code branches):

| targets | tactic | notes |
|---|---|---|
| zombie, husk, drowned, zombie_villager, spider, cave_spider, silverfish, endermite, slime, magma_cube, witch, vindicator, hoglin, zoglin, piglin_brute, wither_skeleton | `melee` (sword > axe > trident > spear-poke), else `snipe` with a bow | crit melee; spear pokes outreach them; a bow alone shoots rather than runs |
| vex | `melee` | too small and quick to shoot |
| skeleton, stray, bogged, pillager | `snipe` with a bow, else `melee` | archers: duel from range or close fast; an escape from them is always a sprint |
| creeper | `snipe` with a bow, else `hit_and_back` with a sword, axe or trident, else `outreach` (a spear's poke from past the fuse), else `avoid` | the swell starts within 3 blocks (feet to feet) and runs down past 7; a jab reaches 4.5 |
| evoker | `melee`, else `snipe`; first in line | kill it before its vexes |
| enderman | not a threat until it targets the bot; then `melee` | never stare at it (provoked by gaze); a carved pumpkin on the head prevents it |
| phantom, blaze, ghast | `snipe` with a bow, else `avoid` | the ghast cannot be reached for melee |
| breeze | `melee` only | it deflects arrows |
| warden, ravager, elder_guardian, guardian | `avoid` | |
| ender_dragon, wither | `avoid` (a boss) | a planned fight later: end crystals with a bow, melee at the perch |
| animals (pig, cow, sheep, chicken, rabbit, ...) | `hunt` (axe > sword > spear) | the axe one-shots: an iron axe crit is 13.5 against a cow's 10 HP |

The weapon is picked by the tactic's allowed kinds, then expected damage per second
(melee) or per hit (hunt). A bow needs arrows.

Animals are hunted when a player asks for what they drop (`gather` porkchop, beef,
leather, ...): the gather plan's `Hunt` task runs `attack_entity`, which crits and never
sweeps. The plan picks the hunt weapon by the same rule as `hunt` (biggest single hit,
then damage per second; `gather_plan._find_weapon` asks `companion_combat`), so a held
iron axe beats an iron sword for a cow. The guard never hunts: animals are not threats.

### 3. The `escape` op (mod, per tick)

Each tick on the ground it gathers every hostile within 24 blocks (new spawns
included). It scores the 8 headings whose next cell is standable and safe
(`RouteGeometry`, drops at most 3, a step up) by the hostiles' field Σ1/d² two blocks
along each; the current heading's field counts ×0.9 so it does not jitter. It sprints
when food is above 6 and jumps over level ground with headroom (sprint-jumping) and on
any step up. It ends `clear` after 20 ticks with no hostile within the clear radius
(16), `cornered` after 20 ticks with no safe heading (Python then walks away by
route), or `timeout`. Each heading's cell is checked facing that heading, as the body
will run it (the safety sweep samples along the pose's facing). Exact contract:
`BODY_ACTION_OPS.md` `escape`.

### 4. Melee technique (mod; shared by `fight` and `attack_entity`)

The same per-tick technique serves defence fights and hunting. `attack_entity` gains
crits, so hunting gets better too. Both report `crits`, `hits` and `kills`.

### 5. Trials (play instance only)

The body op `trial` (`BODY_ACTION_OPS.md`) clears the area and the inventory, heals,
gives listed items, sets the time and summons a mob at a distance from the bot, on level
ground the bot can see (or at a fixed offset). It uses
structured fields, never a free command, and refuses bosses. It reaches the body only
through the host's control API (`POST /trial`, operator only, never an MCP tool), so
the mod's request sequence stays the host's.

`scripts/combat-trial.py` (logic in `mclab.combat_trial`) runs the matrix (weapon ×
mob) at noon, with a leather helmet on undead that burn. For a hostile, the
companion's own guard acts. The host reports each step (`defense_step`: kills, hits,
crits, shots, jumps, health, end) and the defence's end (`defense_end`: clear,
stopped, died). A trial ends at the first kill, at `defense_end` or at its timeout
(then `stop`). For an animal it asks `gather` for the raw drop and times the job. Each
row (kills, crits, hits, shots, damage, health, seconds, actions) goes to
`results/companion/trials/<UTC stamp>.json`.

Before each trial the runner waits (up to 30 s, then one `stop` and 30 s more) for a
quiet bot: no job, no defence, the guard not resting. The trial's clock starts with the
guard's own `defense` event, and a `defense_end` that is not a death or a stop is given
3 s for the guard to engage again (it re-decides every observation), so a fight that
pauses to switch weapons or to walk round a ledge is followed to its kill.

Every trial is fought on one arena: `--arena X,Y,Z`, or where the bot stands when the
matrix starts. After settling, a bot more than 4 blocks across from it walks back
(`goto`, up to 120 s, then `stop`) and settles again; the row's `arena` holds how far
it was and how the walk ended (`there`, `succeeded`, `failed`, `timeout`, `unknown`).
Choose a flat, open, dry arena: a trial on rough ground measures the terrain, not the
weapon.

### 6. First live matrix (Nov 23): what it showed and what changed

Sixteen trials (sword, axe, spear, bow × zombie, skeleton, creeper, pig) on the play
instance; diagnosed from the host's event log before any change:

| seen | cause | change |
|---|---|---|
| three mobs summoned 6 blocks east stood on a rise out of sight; the guard never reacted | an offset summon ignores the terrain | `trial` `distance`: a level, dry, roomy cell in the bot's sight |
| an axe stood two blocks under a skeleton for 200 ticks, knocked back by arrows | a fight step climbs one block; a mob on a ledge is never reached | the fight's chase path (`FightLogic.chase`), `unreachable` after three failed searches; Python then fights it only from range for 30 s (a bow) or walks away / escapes |
| a bow fight ended `done` with the skeleton alive | the fight radius (12) was inside the bow's keep band (8 to 15) | the radius widens to the keep maximum + 3 |
| a pig was killed at 30 s, but the gather explored 60 s more | the drop showed a tick after the kill's receipt, and collect looked where the pig had been seen | `attack_entity` reports where the target died; the hunt waits up to 20 ticks for the drop there |
| arrow hits could not be told from misses | `shots` counts releases | the fight reports `damage` (health its targets lost) |
| the next trial began while the guard still rested after the last one's `stop` | a stop with no defence under way also rested the guard | only a stop during a defence rests it |

### 7. Second live matrix (Nov 23): what it showed and what changed

The same sixteen trials with the section 6 fixes, diagnosed from the host's event log:

| seen | cause | change |
|---|---|---|
| later trials summoned `no_spot` or fought from a trench and a frozen pond | each trial began where the last one ended; the bot drifted into rough ground | a fixed arena (section 5) the bot walks back to |
| a hunt for one porkchop killed two pigs | the plan's spare animal (margin) was always hunted, even with the item held | a hunt stops once it holds what it was for (`gather_runner.mob`) |
| a zombie walking up to the bot was left `unreachable` | three failed path searches in a row gave up on any target | with no path the fight holds still; a target that came 1 block nearer resets the wait; `unreachable` only after 40 ticks (2 s) without that (`FightLogic.noPath`) |
| chase and escape refused steps along a trench's edge | each step's safety sweep used the body's current facing, not the step's | each step is checked facing its own direction (`FightLogic.steps`, `EscapeTask.nextCell`) |
| a failed fight said only `unreachable` | `<op>_failed` carried the reason alone | a failed op's data has its counters (`Task.failure`), and a fight with no path its last search (`chase`: from, to, cells expanded, each side's exit) |

Open: a sword fight on the frozen pond found no path to a zombie; a synthetic copy of
the pond finds one, so the next occurrence's `chase` evidence decides the cause. The
axe landed no crits on the skeleton or the creeper (0 of 3).

### 8. Third live matrix (Nov 23, arena -281,77,-37): what it showed and what changed

The bot walked back to the arena before most trials (from 4 to 64 blocks). The sword
and axe killed every zombie and skeleton with every hit a crit (3 of 3, 2 of 2), and
the creeper by hit-and-back with no harm taken. The spear killed the zombie and the
skeleton in 8 jabs each (a jab cannot crit: the charge is its big hit, slice 8). The
axe crits of the second matrix are no longer missing.

| seen | cause | change |
|---|---|---|
| each bow trial dealt 20 damage in 3 shots but reported no kill | kills were counted at a melee swing only; an arrow kills ticks after the release | a target that dies with the body as its killer (kill credit) counts once, however it died (`FightLogic.Kills`) |
| a sword took 73 s over one pig (the axe's stood still: 4 s) | the hunt walked to where the pig had been and struck in place; the pig had walked on, `out_of_reach`, again | the hunt fights its animal with `fight` (tactic `hunt`), which chases it per tick; the result says `killed` and `at` |

### 9. Fourth live matrix (Nov 23): what it showed and what changed

Fifteen of sixteen trials ended in a kill; every bow trial now counts its kills (4, 3
and 3 shots). The hunt's fight killed its pig in 9 to 165 ticks (axe 3.1 s, spear
5.6 s, bow by hand 12.7 s). The spear with a creeper walked away (by design).

| seen | cause | change |
|---|---|---|
| the sword killed its pig at 5 s, then hunted a second one (18 s) | the porkchop lay in a one-layer snow cover; the entity ray aimed at its box centre, level with the snow's outline, so the drop went unseen for 4 s and the hunt moved on | a development awareness preset also aims a ray just under the box's top; legacy is frozen (`AWARENESS_MODES.md`) |

Hit-and-back strikes sprinting for knockback, so it never crits (expected).

### 10. Fifth live matrix (Nov 23): what it showed and what changed

Fifteen of sixteen trials killed. The sword's pig hunt took 5.1 s, down from 18.2 s:
the porkchop in the snow was seen and no second pig was hunted. The sword and axe
crit every zombie and skeleton hit; the bow killed all three mobs in 3 to 4 shots.

| seen | cause | change |
|---|---|---|
| a lone spear walked away from a creeper four times over 32 s until it lost it | a creeper was fought only by hit-and-back (sword, axe, trident) | `outreach`: a jab reaches 4.5 blocks and a creeper lights inside 3 (feet to feet), so a spear pokes it from a band (eye to box) that never lights it; a swell that starts anyway is outrun as in hit-and-back (`CombatLogic.poke`, `fightsCreepers`). A sword, axe or trident still comes first. |

The first outreach trials, and what they changed:

| seen | cause | change |
|---|---|---|
| an exploded creeper was counted a kill | the kill credit read only "removed" | a kill is a target removed at health 0 (`FightLogic.Kills.credit`); an explosion removes the creeper with health left |
| the band 3.3 to 4.2 let a walking creeper light | the body takes about five ticks to turn from walking in to backing off, and a box edge is 0.3 nearer than the feet | the band is 3.6 to 4.4, and the poke judges the gap `POKE_LEAD` (6) ticks on at the rate it is closing |
| the bot fled after every jab | a jab leaves the creeper at swell 1, draining (direction −1): a flicker, not a lit fuse | a creeper is lit while its swell climbs, or while a draining swell is still half its fuse or more (`CombatLogic.lit`) |
| a flee in a straight line froze against a wall | the flee only faced away | every flee (poke, hit-and-back, a blocked back-off) steers as the escape op does, sprint-jumping round other mobs (`EscapeTask.steer`) |

The fight's result now reports `flees`, the `closest` it let the target come and,
for each flee, its cause, distance and swell (`flee_log`). Three spear trials then
killed their creeper in 7.2 to 8.8 s without one flee, never closer than 4.2 blocks.

### 11. Sixth live matrix (Nov 23): what it showed and what changed

All sixteen trials killed. Only the skeletons hurt the bot (it ended at 17.5 with the
axe, 18.3 with the bow and 7.7 with the spear).

| weapon | zombie | skeleton | creeper | pig |
|---|---|---|---|---|
| sword | 2.9 s | 10.2 s | 5.3 s, 6 flees | 13.8 s |
| axe | 2.9 s | 2.5 s | 5.0 s, 3 flees | 3.3 s |
| spear | 8.0 s | 12.9 s, health 7.7 | 7.2 s, no flee | 6.7 s |
| bow | 3.5 s | 3.5 s | 2.4 s | 10.3 s |

| seen | cause | change |
|---|---|---|
| the sword killed its pig in 3.4 s, but the trial ran 13.8 s | the guard's threat check looks the base up on every poll; that opened the world archive, and one open waited 8.9 s inside sqlite while terrain was being written, freezing the motor loop | the place graph reads its places once and keeps them in memory, written through on every change: no poll touches storage (`PlaceGraph`) |
| the spear lost 12 health to a skeleton | a skeleton backs off inside about 7.5 blocks, out of a 4.5-block jab | open: a spear needs the charge, or the bow when one is carried |

## Slices (TDD each; one commit each)

1. Done: `companion_combat.py`: weapons, profiles, tactic and weapon choice.
   `companion_defense` uses it, and splits `walk_away` from `escape`.
2. Done: Mod: tactic parsing, weapon kinds, the crit cycle, the sweep guard, spear jab,
   hit-and-back, crit reporting (pure `CombatLogic`/`FightLogic`; `BodyEntity.meleeAct`).
3. Done: Mod: the `escape` op (pure `EscapeLogic` plus `EscapeTask`).
4. Done: Mod: bow snipe (pure ballistic solve with drop and lead).
5. Done: the `trial` body op (`TrialLogic`, `BodyTrial`), the host's `/trial` route,
   `defense_step`/`defense_end` events, `scripts/combat-trial.py`.
6. Done: Session: `escape` and `walk_away` steps; `fight` passes the tactic.
7. In progress: Live trials: sword, axe, spear and bow against a zombie, skeleton, creeper
   and pig. Six matrices ran; their fixes (sections 6 to 11) are built and
   unit-tested. The sixth killed in all 16 trials. Open: a spear against a
   skeleton.
8. Later: spear charge, trident throw with Loyalty, shield blocking, the dragon.
