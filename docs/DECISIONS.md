# Implementation decisions

## Bootstrap boundary

The handoff orders milestones and requires G0 (pinned server starts, human joins,
generated C1 loads) before body qualification. This increment establishes the
toolchain, desktop lifecycle, course compiler, and authoritative bootstrap geometry
evidence. Human inspection has passed; no later gate is inferred from a build.

## Dependencies

The 26.3 server was resolved through Mojang's version manifest, checked against its
published SHA-1 and additionally pinned by SHA-256. Datapack format 121.0 comes from
the downloaded server's `version.json`. Fabric API and Loom fixed artifacts were
resolved from Fabric Maven metadata; the handoff's Loader and Carpet pins were kept.
Carpet's published asset SHA-256 matches the handoff. A Windows x64 Temurin JDK 25
ZIP is installed under `runtime/toolchains`; no machine-wide PATH changes are made.

The Fabric example-mod 26.3 build was consulted for the unobfuscated dependency
configuration. The Java verifier is original code using public Fabric tick/lifecycle
events and Minecraft APIs. No Carpet internals, jevtests code, or other baseline
source has been copied. Downloaded runtime jars are ignored, not redistributed.
Gradle wrapper files retain their Apache license headers.

Primary references:

- https://fabricmc.net/2026/09/15/263.html
- https://github.com/FabricMC/fabric-example-mod/tree/26.3
- https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3
- https://github.com/gnembon/fabric-carpet/releases/tag/v26.3
- https://meta.fabricmc.net/
- https://piston-meta.mojang.com/mc/game/version_manifest_v2.json
- https://api.adoptium.net/

## Process and evidence

Python starts a background supervisor with a hidden Windows console. It owns Java's
stdin and provides only status/stop over a private bearer-token loopback HTTP
endpoint. Tokens stay in ignored runtime files and never appear in reports. This
is process management, separate from the implemented authenticated gameplay
bridge on port 8765. It must not become a
per-motor-tick RCON or console control path.

The Java verifier reads its specification before simulation, checks 2048 blocks
per END_SERVER_TICK after warmup, and sends immutable value records to a bounded
off-thread writer. The verifier checks block identity across the complete generated
clear envelope. It does not qualify fluids, scheduled ticks, block entities, body
state, restrictions, drops, or scored reset determinism. Launch IDs reject stale
evidence from earlier server processes. Human presence and human inspection are
separate facts.

Course manifests remain evaluator-only. The G2-qualified local-visibility-v1
pipeline exposes occlusion-tested observed geometry and body state; unknown cells
remain unknown. Tactical inputs derive solely from this permitted policy stream.
Do not expose evaluator manifests to decision backends.

## Qualified scope and next gates

G1 is passed for `initial-c1-survival-v1`, frozen in
`config/initial_body_profile.json` and reviewed in `G1_CAPABILITY_REVIEW.json`.
The handoff permits leaving capabilities unqualified. The initial C1 profile
covers movement, aiming, mining and pickup, supported by completed human
walk/jump, strafe/release, terrain, sprint and view references plus native
reach predicates and the live cancellation/death/replacement suite. Entity
attacks, combat knockback, construction and dimension travel are excluded from
this initial policy profile; their development evidence does not imply scored
qualification. Callers must use `gates.require_initial_profile`, which rejects
profile/runtime-pin drift and unsupported courses or capabilities.

Incomplete/operator-mistake human captures were removed at Marc's request.
Implementation failures remain diagnostic development history. The completed
references retain their recording hashes and client/server timing limitations.

The G2 reset foundation now reconstructs and verifies the full C1 envelope,
creates fresh generations/identities/bodies, warms the body and checks a second
terrain scan with a stable physical fingerprint. Twenty consecutive resets
matched. JSONL plus SQLite record each attempt. Native clock/weather setters
make environment normalization idempotent in 26.3; `time set noon` is not an
idempotent command when the clock is already at that marker.

G2 passed for the frozen initial C1 scope: twenty matching authoritative resets,
29 adversarial/sensor checks, native causal item scoring and durable episode
recording. Policy admission opens only after explicit begin revalidation. G2
qualification binds the evaluator source hash and sensor profile; drift blocks
scored execution. Native pickup observation runs after Inventory.add but before
Player.take because native playerTouch restores the discarded stack count before
returning. Early implementation failures remain recorded.

G3 passed: the final deterministic perception-goal C1 profile completed ten of ten
attempts. Its causal chains, complete policy ticks and source/configuration hashes
were audited; earlier failed configurations remain in the report. Provider Choice/chat
adapters have bounded requests/deadlines, zero retries and sanitized exchanges;
Atom Qwen chat and hosted OpenRouter Jev have passed real probes and C1 live
development attempts (see G4_LIVE_INTEGRATION.md). Local SystemOne and direct-scoring
candidates have now been screened and retained as failed tactical baselines.
G4 remains open for broader actual-observation and controlled native model trials.
Upstream jevtests files
are reference-only (no root license found); no implementation was copied.
Baritone v1.20.0 artifact/API is pinned, without a client bridge or live trial.

Development planning hashes geometry, spawn, facing, target and inventory rather
than treating seed metadata as diversity. The versioned navigation plan now has
25 variants per C2–C5; C3–C5 each have 25 distinct physical geometries. The earlier
frozen plan is retained. C2–C5 reset qualification and the frozen V8 deterministic
development cohort passed; model/learned-policy qualification remains separate.
Offline exports contain actual filtered observations
with pending independent action labels. They are not synchronized demonstrations;
training and fresh holdout evaluation remain pending. The authorized hosted Jev
experiment made 23 paid calls with settled usage of $0.000637602; the verified
wallet/key cap is $1. Those are historical initial-call costs. Later screening and
a follow-up C1 trial brought authenticated settled usage to $0.004860996, as
recorded in G4_JEV_CURRENT_STACK_PROOF.json. See EXPERIMENT_QUEUE.md for current work.

The broader synthetic useful-action screen contains 48 independently reviewed
states in three option orders. Jev completed 144 cases with 141 correct, one wrong
choice and two rejected probability sums. Atom was stopped after a shared-load
deadline; it remains a partial comparison. Marc reports concurrent Hermes agents
heavily use that service. Preserve the initial runner's admission failures as
history; the corrected runner stops rather than consuming unadmitted cases.
No native sensing, construction, calibration or full G4 pass follows from this
screen. See G4_SCREENING.md and G4_SCREENING_PROOF.json.

Synchronous Python audit serialization interrupted motor renewal and exposed
stale-action failures. Those scored batches are preserved. The event writer now
uses a bounded queue and its own SQLite connection; critical write/flush failure
invalidates the attempt before success finalization. A start event is not the
publication of the running snapshot, so the runner waits for both. Stale controls
are discarded, followed by release and a fresh observation/decision, at most three
times per episode. No action age, duration, physics or course budget was relaxed.

## Optional tandem experiment boundary

Tandem planner/tactical models are a hypothesis to test against single-model
alternatives. `tandem.py` adds independent asynchronous roles, bounded
Choice-planner strategy context, expiry/replan revision checks and stale tactical
reply rejection. `models live-smoke` remains single-model unless a second backend
and planner profile are explicitly supplied. Per-role request caps and combined
conservative remote cost are checked before world access; both roles' provenance
and request totals survive shutdown failures. A configured degraded planner policy
is recorded separately from default fail-on-planner-error behavior.

The local HTTP concurrency check uses fake model responses, and the full Python
suite passes 274 tests. No real two-model Minecraft run or winning architecture
is claimed. Free-form strategic plans, pickup interruption/resumption, later-course
model navigation and combat require their own work and evidence. The original
technical handoff and native body/physics profiles remain unchanged. See
TANDEM_HARNESS_PROOF.json and HYBRID_ARCHITECTURE.md.

## Navigation direction (October 28, 2026)

Marc approved a new navigation direction after a code and docs review:
deterministic, tick-costed route search over observed and remembered geometry;
optimistic planning through unknown space at a penalty, with the motor still
refusing unobserved cells; walking, edits and movement macros competing in one
search; interaction goals as reachable-cell sets; survival hazards as cost terms;
and Jev setting goals, cost profiles and tactical interrupts instead of choosing
waypoints. Server-side Carpet bodies remain primary. Baritone is the reference,
and becomes a candidate motor only if MCB still loses at matched terrain awareness.
Mob avoidance is a weight, not a policy: the bot is meant to play and fight
alongside human players. Details, phases and the list of superseded documents
are in NAVIGATION_DIRECTION_2026-10-28.md.

## Mod split, in-mod movement, Baritone-derived baseline (October 28, 2026)

Marc decided: split the mod into independent `mcb-body` and `mcb-lab` mods so
the lab can evaluate any body; move per-tick movement and reflexes into
`mcb-body`; build the planner core (tick costs, optimistic unknown space, unified
edges) as one flagged phase with ablations. Baritone (LGPL-3.0) may be read and
its move logic and cost values reimplemented as MCB's own code with reference
notes; Baritone source is not copied. Earlier wording that no baseline source was
consulted described the bootstrap increment, not a standing rule. See section 0
of NAVIGATION_DIRECTION_2026-10-28.md.


## Mod split implementation (October 28, 2026)

Step A of the direction document. The lab never spawns or drives a body: every
subject is a named player that may join after the request (600-tick window), so a
Carpet body and a Baritone client go through the same reset, fingerprint and scoring
code. Rules the single mod enforced via shared state were moved to their owner, not
dropped. Course control limits are a body spawn option. Motion before `course_begin`
fails begin verification in the lab. The controller watchdog is split: the body
releases controls, and the lab fails the episode only for courses that opt in
(bridge-driven bodies), because Baritone and human clients are not heartbeated. The
Python bridge keeps the single-bridge interface and fans compound ops out to both mods
so the existing harness did not need rewriting; step B can replace it. Duplicated
small helpers (player reset, interaction observers) are intentional: D9 forbids
shared code paths. Legacy qualified evidence is bound to the old source hash and must
be requalified before scored use.

## Swimming survival reflex (October 30, 2026)

Getting the head above water is allowed when `allow_swim` is false. It is a
survival reflex, never a route: `water_movement.air_recovery_command` only
surfaces straight up or steps into one adjacent column that is open to air, and
returns `{}` otherwise. Route search and `swim_command` still require
`allow_swim`, so the no-recovery-fallback rule for planned movement is unchanged.
With `allow_swim` the reflex may swim to the nearest breathable cell within the
remaining air (air / `SWIM_TICKS` blocks). Recovery ends when the eyes are out of
water (`eye_in_water`, falling back to `in_water`), not at 90 % air.

## Terrain delivery and planning start (October 31, 2026)

- **Delivered means read.** In the `sections` preset a terrain batch counts as
  delivered only when `/policy` serves it; unread sections are carried into the
  next batch. Rescanning them instead (tried first) cannot catch up with a
  controller that starts 40–70 ticks late.
- **The event log records scans, not deliveries.** `policy_sample` carries each
  section once, when scanned. What the controller received is read from the
  `policy_observation` sample ticks.
- **No discarded `/policy` reads.** Harness code waits on `/state`.
- **Plan only on a known start.** The controller does nothing ("awaiting_map")
  until the cell under its feet is admitted, instead of walking blind fallback
  steps. The body's near refresh always covers that cell, so the wait is bounded.
- **One planning lane, scoped cancels.** Search and prefetch share one worker;
  `cancel(token)` cancels only the job it names, and a superseded search
  resubmits once the lane is idle.
- **Normal awareness cadence.** `awareness-sections-64.json` (every 5 ticks) is
  the development profile; the `-fast` profiles are rejected (they slowed the
  planner).

## Build stops, supply access and /mcb authority (November 21, 2026)

- **A materials stop cleans up.** `run_build` stops on `need_materials` only when
  nothing else can be placed, so it takes its scaffolding and footholds down first; a
  later resume builds its own towers again. Run 20261121T171016886377Z left every
  tower standing.
- **Containers are opened like any other block.** A chest or shulker box withdraw
  goes through `reach_and_do`: a blocked view or reach tries other stances instead
  of failing the refill.
- **/mcb authority is open by default.** An empty or missing owner list in
  `config/companion.json` lets every player in the game command the bot; once anyone
  is listed, only the listed owners and allowlist may (UUID or name). An unreadable
  config refuses everyone. The shipped config lists the operator as owner.
- **The agent ends inspection holds.** The user never creates `<run>/STOP`; when they
  say they are done looking, the agent running the session creates it. A stop
  button on the lab page is planned (B6).

## Materials on hand for build-speed work (November 21, 2026)

B2b measures building, not gathering or crafting: a `--supply` run must hold every
block of the house between the bot and the chests.

- **The house's blocks are never spent on anything else.** Cobblestone and dirt stay
  free to use for levelling and plugs, in any order, but only beyond what the build
  still has to place. The body's water plug now gets that count (`plug_keep`); site
  prep fills with any dug-up solid beyond the house's own count (before, a block the
  house used was never fill at all). Run 20261121T180806599463Z plugged site-prep
  water with 21 of the house's 54 cobblestone, and the foundation waited 8 minutes
  for more.
- **Levelled ground is stone below, soil on top.** Fill puts cobblestone and other
  stone in the lower layers and dirt in the ground layer; exposed cobblestone in the
  ground looks wrong unless it is a path. The water plug, always under the surface,
  uses cobblestone first.
- **A build that carries scaffolding climbs on scaffolding only.** No dirt,
  cobblestone or other solid footholds, steps, pillars or bridges (`scaffold_only`:
  the planner gets no solid material at all). A place scaffolding cannot make is a
  fault in our rules to fix, not a reason for a solid fallback. The supply holds two
  stacks and the bot carries both (`SCAFFOLD_CARRY`); the foothold stock tops up
  scaffolding.
- **Dirt goes to the chests after site prep.** Once the site is level, the bot keeps
  only the dirt the house places; the rest goes into the chests then and at every
  refill. Run 20261121T180806599463Z carried dirt into the build, left a dirt step on
  the roof and tunnelled through a wall to reach it.

## Stage labs (November 21, 2026)

A whole house-large run takes 40–50 minutes, which is too slow to iterate on one part of
it. The build is split at two milestones:

- `site_ready`: the site is cleared and level, and no house block is placed yet.
- `structure_done`: only details and doors are left.

A run can stop at a milestone (`--until`) or start from one (`--from`).

- **A stage is a world snapshot, not a replayed log.** It holds the cells of the site
  box, the inventory and the chests with their contents. It is restored with the lab
  kit into a fresh copy of the seed's world. Gathering outside the site (cut trees,
  stations) is not kept: nothing after site prep depends on it.
- **The library is newest-wins per build key, and every run feeds it.** Any build that
  passes a milestone republishes that stage, with the run and commit it came from. A
  run from a stage doesn't republish that stage or any earlier one.
- **A stage is tied to its build file** (sha256). An edited template refuses an old stage
  rather than build on cells that no longer match.
- **Parts don't compete with wholes.** A stage-lab run's build key carries its segment,
  so `mclab runs`, pruning and comparisons keep parts and whole builds apart. A whole
  build stays the acceptance measure; the parts are for iterating.

## Storey build order and band stages (November 22, 2026)

The zones order builds one 8-block zone up to its roof before the next, so the body climbs
each zone's scaffolding and the walls of the next zone stand open while it does. A builder
in survival puts down the floor, takes the walls up a few layers all the way round, then
does the next floor. `--order storeys` builds the house that way.

- **Bands.** Each floor is a band of its own (`bp.storey_bands`); the walls between floors
  go up in bands of three layers (`RING`). Within a band the nearest column goes first and
  each column goes up from the bottom in one batch, from one stance. Nothing is built over
  a cell the sweep failed: a skipped or given-up cell still holds its column.
- **Zones stays the default for now.** Storeys is opt-in (`--order storeys`, run config
  `order`). On the final code the storeys structure lab beat the zones one (table below),
  but the whole-build comparison has not been run yet.
- **A band is a stage.** `band_N` is saved when the structure of bands 0..N is in, with
  the footholds standing then, so a run can start at any storey (`--from band_N`). The
  last band of house-large holds details only, so `band_5` is the structure just before
  cleanup.
- **A batch waits on its own cells.** A cell whose state can only be clicked from a batch
  cell not yet in waits for it (`_waiting`) instead of wanting a prop. A cell offered next
  to a batch cell not yet placed (the gate says floating) waits for the next plan and
  costs no try.
- **Asks are totals.** `on_need_materials` asks for the total to hold, because the
  refill tops up to it (`_hold_more`). A tower, prop or foothold ask that gave only the
  shortfall was already held, so nothing was fetched and the cell was skipped as
  `no_material`.
- **One chest trip per phase.** While structure is left, a refill carries only the
  structure (`phase_left`) and loads what was asked for first. Decor rides along only once
  the structure is in. A decor-only ask mid-structure is still served.
- **Skipped structure cells are retried before `structure_done`.** Once only details are
  offered and a structure cell has been skipped, the retry round runs then, not after the
  details.
- **Cleanup puts dug ground back in nearest-first order.** Restores join the same sweep as
  the breaks instead of going first.
- **Bodies get out.** These are fixes the storeys runs exposed:
  - A scaffolding top carries only a body that is already above it.
  - A body shut in (its whole region searched, no move off it) gets down, whatever the
    region's size. The goal decides the order: below the body, get down first; level or
    above, tower first and get down if no tower reaches it. A size limit (32 nodes)
    stood in for this until zones run 20261122T125350762344Z ended trapped on a roof
    slope of 40 nodes (b98da15).
  - No scaffolding is placed from inside a scaffolding column, because the click ray hits
    the column.
  - A body shut in on its own unbuilt cell pillars that cell's block to climb out
    (StepMotor clicks the top face of top stairs and slabs).
- **Watch, don't just count.** `mclab.build_view <run> --every 300` draws the build's
  layers to `<run>/views/` and gives a verdict (progressing, stuck) every five minutes.
  Every lab below was watched that way, and none sat still.

Structure labs (`--from site_ready --until structure`, house-large, seed 31415926):

| Run | Order, code | Job | Structure | Cleanup | Skipped |
|---|---|---|---|---|---|
| 20261122T060550689437Z | zones | 23:08 | 20:10 | 2:27 | — |
| 20261122T054201856122Z | storeys, before band stages | 22:02 | 16:10 | 5:19 | — |
| 20261122T064728624126Z | storeys, band stages | 20:58 | 17:53 | 2:31 | 2 |
| 20261122T071747687093Z | + total asks | 18:54 | 17:09 | 1:22 | 1 |
| 20261122T074325272877Z | + batch-mate wait | 21:19 | 17:58 | 3:11 | 1 (ridge) |
| 20261122T081253390045Z | + structure retry (0139f61) | 18:50 | 16:10 | 2:23 | 0 |
| 20261122T125350762344Z | zones, main (d3b27a1) | trapped | — | — | 814 placed |
| 20261122T131804981949Z | zones, + shut-in exit (b98da15) | 21:51 | 18:29 | 2:22 | 0 |
| 20261122T134204587491Z | storeys, + shut-in exit (b98da15) | 19:02 | 16:27 | 2:11 | 0 |

Same code (b98da15), one run each: storeys 2:49 faster than zones (structure 2:03, refills
36 s). Storeys runs on near-identical code spread from 18:50 to 21:19, so one pair does not
settle it; the whole-build comparison is still to run.

### Validation series (November 22, afternoon)

Site labs (`--supply --until site`): 20261122T142224546926Z, 20261122T143231913671Z,
20261122T144210171062Z on 3f8739a and 20261122T151804484862Z on b02b1a2, all 4/4 in
8:28–8:34. Two of the 3f8739a runs crafted twice from used-up ingredients; that was fixed
by keeping the newest held inventory by server tick (b02b1a2, #9).

Structure labs on d07edfc (#10, scaffold-only foothold asks), all 5/5:

| Run | Order | Job | Note |
|---|---|---|---|
| 20261122T152815791116Z | storeys (b02b1a2) | 52:20 | dirt-ask loop (fixed in #10), left out |
| 20261122T163308723728Z | storeys | 23:59 | |
| 20261122T170052910305Z | zones | 24:00 | |
| 20261122T172716055930Z | storeys | 24:41 | |
| 20261122T180636433203Z | zones | 20:24 | |
| 20261122T182906280327Z | storeys | 18:59 | |
| 20261122T185137031693Z | zones | 23:53 | 23 scaffolding left standing (#11) |

Mean job time: storeys 22:33, zones 22:46. That is a tie inside an 18:59–24:41 spread, so
**zones stays the default** until a whole-build comparison (#14). The biggest single time
loss in the series is `goal_no_progress` cutting off valid detours (about 322 s in
152815791116Z and 243 s in one zones run), because the progress check measures straight-line
distance to the goal (#13). Cleanup can leave temporaries: `get_down` can land inside the
same pocket (#11), and cleanup never retries a failed cell (#12). Neither fault came from
the storey order. The `structure_done` stage in the library is from 185137031693Z, so it
carries those 23 blocks.

Earlier runs that found the faults: 20261122T013145553434Z (props for batch cells),
20261122T021157007245Z (scaffolding top), 20261122T024835715542Z (closed ledge),
20261122T031908319185Z, 20261122T034141954170Z (scaffolding place from inside a column),
20261122T045703502025Z (shut in on its own cell), 20261122T063256626024Z.

## Companion LLM wiring (November 23, 2026)

Marc chose to build both LLM paths at once (an MCP stdio server for external agents
and in-game chat answered by the local Atom Qwen) and to run the companion on a
separate play server now, so playing and lab runs can happen at the same time. The
design is [COMPANION_LLM_PLAN_2026-11-23.md](COMPANION_LLM_PLAN_2026-11-23.md).

- **Not the lab episode path.** `/mcb` jobs ran as lab episodes: `natural_start`
  freezes the clock, turns mob spawning off, replaces the inventory with a kit and
  the arena is wiped afterwards. A companion needs one body in a kept world, so
  the host spawns it straight through mcb-body with `keep_state` (the saved
  inventory, health and food stay).
- **One host process per play instance**, started and restarted by that
  instance's supervisor. Every adapter (MCP via the loopback control API, the
  talker, `/mcb`) calls one `Host.call(intent, args, actor)`; validation and
  authority live there once.
- **Cancel from the bridge poll.** Every blocking wait goes through
  `Bridge.state()`, so the host's cancel hook there ends any job at the next poll.
  `JobCancelled` is a `BaseException`, so runner code that catches `Exception`
  cannot swallow a stop. A stop never tears the session down.
- **Stop in code.** A chat line made only of stop words stops the bot before any
  model sees it, and a model's own tool calls run with the speaker's authority:
  players outside the owner list can chat and look (`status`, `list_places`) but
  start no job.
- **Stopping the host politely.** On Windows `terminate()` is a hard kill, so the
  supervisor first asks over `POST /shutdown` (the host halts and despawns the
  body, a logout that saves) and only then terminates.
- **Where the bot comes back (live, Nov 23).** A first start uses the world spawn
  from `level.dat`, never a guessed column (one over the ocean drowned it). Later
  starts use the last good position, which must be a dry, healthy stance on the
  ground; a start that fails there forgets it, so the host's retry uses the world
  spawn. A bot that *died* respawns at the world spawn like a vanilla player, not
  beside what killed it; the spot becomes the place `death`.
- **A body adapter owns one entity.** mcb-body adapters bind the entity they
  spawned; a retired adapter never touches a later body of the same name (it killed
  the respawned companion every tick), and retired adapters whose entity is gone
  are dropped, so dying for hours never reaches `MAX_RETIRED_BODIES`.
- **"Wood" is any log (Nov 23).** In a spruce-only biome an `oak_log` gather swept
  7 minutes and found nothing. `gather` takes a group tag (`#minecraft:logs`; the
  words "wood", "logs" and "planks" map to tags) and the planner treats it as one
  member with every other member as a stand-in: the runner already searches
  stand-ins together and takes the nearest, held members of any kind count, and a
  crafted group (planks) takes any log in its recipe slot. The member planned is one
  seen nearby, else the cheapest by the planner's cost (held ingredients, then the
  oak preference). A named id (`spruce_log`) still means exactly that log.
- **Auto-defence replaces "no combat" for the companion (Nov 23).** The first live
  gather ended "slain by Zombie" at night, and Marc asked the bot to run, fight or
  hide on its own. This overrides the Nov 7 nightfall protocol's "no combat, no
  reactive flight" for the companion (the lab's episodes still clear mobs). Python
  decides (`companion_defense.decide`: fight when armed above the health floor,
  arm from a base chest, regroup with an ally, shelter at the base, burrow into a
  sealed hole, avoid creepers and wardens, flee); the mod's per-tick `fight` op
  does the melee. The host's guard runs in every bridge poll of a job or idle and
  raises `Defend` (a `BaseException`, like `JobCancelled`); the job unwinds, the
  defence runs a step at a time until nothing threatens, then the job runs again.
  The floor (default 3 hearts) and radii are settings (`bot.json` `defense`, the
  `defense` intent), never a switch: defence is always on. Plan:
  [COMPANION_DEFENSE_PLAN_2026-11-23.md](COMPANION_DEFENSE_PLAN_2026-11-23.md).
- **Combat technique per target, carried out by the mod (Nov 23).** Marc asked for
  an escape that looks unlike a walk (sprint-jumping, steering from every mob,
  new spawns included), proper use of the sword, axe, spear and bow (no click
  spam; jump crits), and different methods for animals and for each mob.
  `companion_combat` maps each target type to a tactic and picks the weapon; the
  mod times crits, cooldowns, bow draws and footwork per tick, since no Python
  poll is fast enough. A fight that finds no walkable way to its target (a mob on
  a ledge) ends `unreachable` and the target is fought only from range for 30 s;
  this is a fact about the terrain fed back to the decision, not a cap on
  attempts. "No walkable way" means no path and the target not coming closer
  for 2 s: a zombie walking up to the bot is waited for, not given up on.
  Trials run through an operator-only `trial` op (never an intent or
  MCP tool) that summons on level ground in the bot's sight, and every trial of
  a matrix is fought on one flat arena the bot walks back to, so a trial
  measures the fight, not the terrain. Plan:
  [COMPANION_COMBAT_PLAN_2026-11-23.md](COMPANION_COMBAT_PLAN_2026-11-23.md).
- **Open:** LAN access for a second PC (the game port binds loopback for now),
  rotating a long session's `events.sqlite3`, and the live night test of the
  defence (built, not yet run against real mobs).
