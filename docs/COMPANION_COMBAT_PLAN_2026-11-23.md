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
| `melee` | close in and jump-crit where the target will be (`CritForecast`, section 12): hop in place, backward from a mob walking in, or walking or sprinting at one backing off, so the strike on the way down lands in reach and outside the mob's own reach. Where a jump cannot crit (low ceiling, water, ladder), strike at full cooldown. |
| `lance` | spear charge (`LanceLogic`, section 12): hold the use and sprint at the target so it crosses the look 2.3 to 4.2 from the eye on a fast tick; sprint-jump for the burst when a ground sprint is too slow (a skeleton backing off). Inside 2.4 the `melee` weapon, when carried, takes over and hands back past 6 blocks; a lone spear jabs there instead (held 2.5 to 4.0), never backing off for a run-up while its jab reaches; a creeper is outrun after each hit. |
| `poke` | spear jab (still served by the mod, no longer picked): hold 2.5 to 4 blocks off and jab at full charge. |
| `hit_and_back` | sprint hit (extra knockback), then back off past 7 blocks while the creeper's swell runs down; again when the swell is back to zero. |
| `snipe` | bow: keep 8 to 15 blocks off, draw 20 ticks, aim with drop and lead, release. Switch to a melee weapon if the target closes within 4. |
| `hunt` | animals: the owner's rule (Nov 23, replacing "never the bow"): the bow while there are arrows (shot, never swung, `snipe` closing in past 8); else the held weapon that kills in the fewest ticks, then with the least wear, that the kill does not break; else the fist. No sweep while another animal stands within 3 blocks of the target. |
| `avoid` | never engaged: `escape` instead. |

**Profiles** (entity type to tactic; a table, not code branches):

| targets | tactic | notes |
|---|---|---|
| zombie, husk, drowned, zombie_villager, spider, cave_spider, silverfish, endermite, slime, magma_cube, witch, vindicator, hoglin, zoglin, piglin_brute, wither_skeleton | `melee` (a carried spear opens with `lance`, handing over to the best of sword > axe > trident), else `snipe` with a bow | crit melee; a bow alone shoots rather than runs |
| vex | `melee` | too small and quick to shoot |
| skeleton, stray, bogged, pillager | `snipe` with a bow, else `melee` | archers: duel from range or close fast; an escape from them is always a sprint |
| creeper | the owner's order: `snipe` with a bow, else `lance` with a spear (never handed to a sword), else `hit_and_back` with a sword, axe or trident, else `avoid` | the swell starts within 3 blocks (feet to feet) and runs down past 7; a charge reaches 4.5. A crit from outside the fuse has about 0.05 blocks to spare (reach 3.0 eye to box against the fuse's 3.0 feet to feet), so hit-and-back keeps the sprint hit at the reach's edge |
| evoker | `melee`, else `snipe`; first in line | kill it before its vexes |
| enderman | not a threat until it targets the bot; then `melee` | never stare at it (provoked by gaze); a carved pumpkin on the head prevents it |
| phantom, blaze, ghast | `snipe` with a bow, else `avoid` | the ghast cannot be reached for melee |
| breeze | `melee` only | it deflects arrows |
| warden, ravager, elder_guardian, guardian | `avoid` | |
| ender_dragon, wither | `avoid` (a boss) | a planned fight later: end crystals with a bow, melee at the perch |
| animals (pig, cow, sheep, chicken, rabbit, ...) | `hunt` (bow with arrows; else the quickest kill that does not break the weapon; a spear charges) | an iron axe crit is 13.5 against a cow's 10 HP; an axe spends 2 uses a hit, the rest 1 |

The weapon is picked by the tactic's allowed kinds, then expected damage per second
(melee) or ticks to kill (hunt). A spear is ranked by its charge (1 + floor(5.6 b/s ×
its multiplier) once a 20-tick pass), not its jab. A bow needs arrows.

Animals are hunted when a player asks for what they drop (`gather` porkchop, beef,
leather, ...): the gather plan's `Hunt` task runs the hunt fight, which crits and never
sweeps. The plan picks the hunt weapon by the same rule as `hunt`
(`companion_combat.hunt_weapon`, from `gather_plan._find_weapon`), so a held iron axe
beats an iron sword for a cow, and a bow with arrows beats both. An animal already
inside the fight's radius is fought at once, without a walk to it first. The guard
never hunts: animals are not threats.

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

### 12. The owner's second round (Nov 23): forecast crits, the charge, 10x matrices

The owner asked for:
- jump crits aimed at where the mob will be;
- the spear's charge, swapping weapons when that is quicker;
- dead mobs skipped;
- the hunt rule (the bow with arrows, else the quickest kill that does not break);
- creepers fought from range first;
- ten trials per cell, then groups.

What was built:

| item | change |
|---|---|
| jump crits | `CritForecast` (mod, pure) predicts the target's position when the strike falls (a zombie walks in, a skeleton backs off) and picks the hop: in place, backward from a mob walking in, or walking or sprinting at one backing off. Every sword and axe hit in both 10x matrices was a crit. |
| spear charge | `LanceLogic` (mod, pure). Holding the use, the bot sprints so the target crosses the look 2.3 to 4.2 from the eye on a tick 4.6 b/s faster than the target. It sprint-jumps only at a target backing off, since a hop at one walking in carries the body onto it. Inside 2.4 a lone spear jabs (held 2.5 to 4.0) rather than backing off for a run-up, and charges again past 6 blocks. The result reports `lance` (ticks per move) and `charge_log` (each hit's gap, speed and damage). |
| charge then swap | `companion_combat.charge_first`. Open with the charge and hand over to the swing weapon only when that kills sooner: alone, (n − 1) swing cycles; after a charge, m cycles, since the swap resets the cooldown. No current weapon pair passes, because a charge (iron 6, netherite 7) saves at most one swing. |
| creeper | the owner's order: the bow, then the spear's charge from past the fuse (never handed to a sword; outrun after each hit), then hit-and-back, then avoid. A crit from outside the fuse has about 0.05 blocks to spare (reach 3.0, eye to box, against a 3.0 fuse, feet to feet), so hit-and-back keeps its sprint hit at the reach's edge instead. |
| trials | a mob left alive (an escape, a timeout, a death) is cleared at once and again before a walk back; the body clears trial mobs anywhere loaded. Live, a skeleton a spear escaped from shot the bot dead on the walk back. The settle waits for the session's state, since the status's body state is a snapshot that still reads "ready" while the bot lies dead. |

Ten trials per cell, iron weapons (`20261123T230049Z` before the charge, `20261123T235754Z` after; median seconds, then the lowest health where any was lost):

| weapon | zombie | skeleton | creeper | pig |
|---|---|---|---|---|
| sword | 2.7 | 2.6, health ≥ 16.0 | 6.3, 5.4 flees | 3.5 |
| axe | 2.3 | 2.2, ≥ 13.8 | 5.0, 3.8 flees | 3.0 |
| spear (jab, before) | 7.2 | 10.3, median 11.4, 5 timeouts | 7.3 | 5.5, 9/10 |
| spear (charge and jab) | 6.3 | 6.0 (9/10), median 14.2, ≥ 6.3 | 7.0 | 5.2 |
| bow | 3.5 | 3.5, ≥ 17.2 | 3.5 | 4.7 |
| spear+sword (charge, then the sword) | 3.5 | 2.9, ≥ 15.8 | 7.0 | 3.4 |

No cell regressed against the earlier matrix. The spear+sword row measured the swap: it was
slower than the sword alone, so `charge_first` now keeps the sword alone.

Open:
- a lone spear against a skeleton (one escape in ten);
- the trident throw (needs Loyalty, or a walk to pick it up);
- the bow drawn after a charge once the target is far.

### 13. Groups (Nov 23–24): what they showed and what changed

Scenes: 2 skeletons, 3 skeletons, 3 zombies, and 2 zombies with a skeleton; ten trials per
cell. Each further mob stands a block further off. A trial is killed only when every mob
of the scene is down.

Cells show kills out of ten, then the median seconds. The first run is
`20261124T002512Z`; the second, `20261124T011202Z`, came after the archer threat fix
below.

| weapon | skeleton×2 | skeleton×3 | zombie×3 | zombie×2+skeleton |
|---|---|---|---|---|
| sword (second run) | 10, 4.7 | 10, 7.1 | 10, 6.6 | 10, 5.9 |
| axe (second run) | 10, 4.3 | 9, 6.9 | 10, 6.5 | 10, 7.1 |
| spear+sword (the sword alone) | 10, 4.3 | 10, 6.5 | 10, 6.3 | 10, 7.1 |
| bow (first run) | 10 | 6 | 10 | 5 |
| bow (second run) | 10, 6.8 | 6, 9.8 | 7, 11.4 | 5, 9.9 |
| lone spear (first run) | 11 of 40 across the four | | | |

What each showed and what changed:

1. **An archer is a threat as far as it shoots** (`companion_defense.ARCHER_RANGE` 16).
   - A bow killed two of three skeletons at near full health, then the guard called the
     defence clear: the third was shooting from past the 12-block threat radius.
   - The fight also looks as far as the farthest threat seen (`fight_radius`).
2. **A mob the bot would shoot is a threat as far as the bow's stand-off band (15).**
   - The second run's bow misses all ended "clear" at full health. A sniper backs off to
     8–15 blocks, so after one kill the zombies still coming stood past 12.
   - Fixed in `threats(..., inventory)`; not yet re-measured.
3. **Melee picks the weapon that kills this mob soonest** (`companion_combat.kill_ticks`).
   - Picking by damage per second always took the sword. The one-on-one matrix measured
     the iron axe quicker on 20-health mobs: two crits in one cycle against three in two
     (zombie 2.3 s against 2.7).
   - Hit-and-back takes the fewest plain hits, and a sniper hands over to the quickest
     swing weapon.
4. **A lone spear against a group** jabs most of the time and loses health (11/40, one
   death). A kite tactic (charge, retreat, charge again) is open.

### 14. Commanded attack and the command surface (Nov 23)

The owner can order the bot to fight: a player, the nearest mob of a type, or the
nearest hostile. Auto-defence is unchanged and never fights a player (`HOSTILE`
holds mobs only; a player is an ally to it).

#### The `attack` job

| field | meaning |
|---|---|
| `player` / `mob` / `nearest_hostile` | exactly one target. `player` is an online name (case-insensitive); `mob` is an entity type (`zombie`, `minecraft:cow`); `mob: player` is refused (use `player`). |
| `weapon` | optional item id. The weapon decides the style (`companion_combat.forced_tactic`): a bow snipes (a held swing weapon backs it up close in), a spear charges, a sword, axe or trident swings for crits, any other item is a plain hit. An item not held, or a bow with no arrows, fails the job in plain words. |
| `when` | `now` (default) or `next`, as every job. |

The session loop (`Session.attack`, rules in `companion_attack.py`): read the
observation, find the target, end if it is gone or cannot be hurt, end at the health
floor, walk to it when it is out of sight or further than 24 blocks, else run one
`fight` op (200 ticks) with the tactic `companion_combat.tactic` picks (the named
weapon when there is one; the fists when no profile suits what is held), then look
again. A fight that ends `done`, `timeout`, `avoid` or `unreachable` with the target
alive means chase again: nothing counts failures or gives up, only these end it.

| ends with | when |
|---|---|
| `killed` | the fight op reports the requested entity among its kills |
| `lost` | a player logged out, is in another dimension or cannot be located; no mob of that type (or no hostile) in sight |
| `invulnerable` | the roster says creative, spectator or invulnerable, or the fight op ends `invulnerable` |
| `low_health` | health at or below the defence floor (the existing floor rule; the guard then decides as it always does) |
| stop | `/mcb stop` or the `stop` intent: the mod cancels a running fight op at once (`cancelled:player_stop`), the job ends `cancelled` |
| a new job | `when: now` cancels it like any job |

The result is `{outcome, target, kills, hits, damage, crits, fights}`. The tally
belongs to the job, so a defence in the middle (which unwinds and reruns the job)
does not reset it (`session.current_job`).

#### Who may order it, and the self-target decision

- `attack` is not in `UNTRUSTED_INTENTS`: only the owner and allowlist (or the local
  operator through MCP and the API) may call it.
- Hitting a **player** is stricter than commanding the bot. An empty owners and
  allowlist lets every player in the game command the bot, but it names nobody, so it
  does not let anyone attack a player: that needs the operator (`mcp`, `api`) or a
  name or UUID the config lists (`companion_service.allowed(..., listed=True)`).
- The bot is never its own target (refused in the host, and `ActionLogic.allowedTarget`
  refuses `self` in the mod).
- The owner **may** name themselves: the target is whoever is named, and the order is
  the owner's. Recorded in [DECISIONS.md](DECISIONS.md) so it is easy to reverse.

#### The mod side

- `allow_player` on the `fight` and `attack_entity` ops. Python sets it only for an
  attack job against a player; auto-defence never sends it. Default false, so a
  player target is `not_allowed` as before.
- Pure rules in `ActionLogic.allowedTarget(animal, hostile, tamed, player, villager,
  allowPlayer, self)` and `ActionLogic.refusal` (creative, spectator or invulnerable
  is `invulnerable`); `FightLogic.allowPlayer(request)` parses the flag. `BodyEntity`
  only calls them. The fight task rechecks each step: a player who went creative ends
  the fight `invulnerable`.
- `GET /players`: every online player (name, uuid, position, dimension, mode,
  invulnerable, fake), published each tick, so the job can tell a logout from "out of
  sight" and a creative target from a survival one without widening the frozen
  observation.

#### The command surface

Every combat action is the same intent from every surface; a test
(`tests/test_companion_parity.py`) pins it.

| action | `/mcb` | control API and MCP | chat model |
|---|---|---|---|
| attack | `/mcb attack <player\|mob\|hostile> [weapon]` | `attack {player\|mob\|nearest_hostile, weapon?, when?}` | tool `attack` (trusted speakers only) |
| defence settings | `/mcb defense`, `defense floor <hearts>`, `defense radius <blocks>` | `defense {floor_hearts?, threat_radius?, ...}` | tool `defense` |
| hunting | `/mcb gather porkchop <n>` | `gather {item: porkchop, count}` (the drop; the gather fights the animal) | tool `gather` |
| stop | `/mcb stop` (cancels the body at once, in the mod) | `stop` | tool `stop` |
| trial | none | none (the operator-only `POST /trial` route only) | none |

`/mcb attack <word>`: Python resolves the word to an online player first (so
`/mcb attack flamingchicken` attacks that player), else `hostile` or `nearest_hostile`
to the nearest hostile, else a mob type. Tab-completion lists online names.

#### Not built

`guard` (follow a player and fight hostiles near them). It needs a second
re-anchoring loop beside `follow`; the defence already fights beside an ally within
the ally radius (`anchor`, `leash`), so it waits for the shield and trident work.

### 15. Food and potions (Nov 24)

The owner asked for food and for potions, drunk and splashed, with different effects, each used
in the circumstances that suit it. `companion_consumables.decide` (pure) picks at most one use
per look; the session carries it out (`eat`, `drink`, `throw_potion`) and looks again. First
match wins:

| # | When | Use |
|---|---|---|
| 1 | burning, no fire resistance running | fire resistance: splashed on itself (instant), else drunk |
| 2 | health at `HEAL_AT` (10) or below, a foe within `CLOSE` (6) | healing splashed on itself, else a golden apple, else healing drunk, else regeneration |
| 2 | the same with foes farther off | healing drunk (the splash is kept), golden apple, regeneration |
| 2 | the same with no foe within `QUIET` (16) | eat to a full bar (the fastest regeneration); a potion only when the bar cannot regenerate |
| 3 | food at 6 or less (no sprinting), no foe close | eat |
| 4 | a splash can reach foes: it aims at a point 5..5.25 blocks out toward a foe (outside its own 4-block splash, inside the ~5.5-block throw), never past a foe already within 2.5 blocks of that line, at least 4.5 blocks from any friend; each point is scored by the game's 1 - d/4 intensity on every foe it takes, and a throw needs 1.2 of weighted effect (0.5 when it is alone with a brute) | harming on the living (not witches, which shrug it off; never where undead would be healed); healing on the undead (one healing splash kept back); weakness on melee brutes; slowness on a melee pack; poison on the living (not spiders) |
| 5 | a group or a brute within 16 | strength; swiftness when archers are in the group; fire resistance against blazes, ghasts and magma cubes. Drunk while every foe is 7+ blocks off, else splashed on itself while none is within 5 |
| 6 | no foe within 16, hungry or hurt | eat the best plain food (golden apples are kept for fights) |

A throw needs two foes in the splash, or one brute at its centre. No harmful splash breaks within
4.5 blocks of a player in sight. The body cannot see a mob's effects, so the session remembers
what it threw on whom (`Marks`) until the effect wears off. While something held heals, a fight
op breaks off at `HEAL_AT` (`fight_floor`), and the bot heals and fights on. The guard does the
same before each defence step. The combat-trial kit parts `food` (16 steak, 2 golden apples) and
`potions` (drinkable healing II x2, strength, swiftness, regeneration, fire resistance; splash
healing II x3, harming II x2, weakness, slowness, poison) are given one entry per potion, since
potions never stack. The body side is in BODY_ACTION_OPS (`drink`, `throw_potion`, the rows'
`potion`/`effects`, the observation's `effects`, `absorption`, `saturation`, `fire_ticks`).

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
7. Done: Live trials: sword, axe, spear and bow against a zombie, skeleton, creeper
   and pig. Six matrices, then two 10x matrices (section 12); every fix is
   unit-tested.
8. Done: forecast jump crits, the spear charge (`lance`), and the charge-first rule
   (section 12).
9. Done: groups (section 13): the archer and sniper threat ranges, the quickest weapon per mob.
10. In progress: raids (section 14). The trial counts kills by type (`kill_types`), so an
    evoker's vexes do not stand in for it. The `all` kit carries every weapon; the
    ravager is shot (avoided without a bow), and the illusioner is an archer.
11. Next: full iron armor worn from whatever the bot carries, shield blocking, and the
    trident (swung, and thrown with Loyalty).
12. Later: the bow after a charge, a kite for the lone spear against a group, the dragon.
