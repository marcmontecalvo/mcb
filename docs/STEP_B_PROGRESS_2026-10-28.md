# Step B development progress

Active implementation, not acceptance or qualification. Governing order and
acceptance remain in NAVIGATION_DIRECTION_2026-10-28.md. Do not mark Step B done
until its fixture families, five calm natural routes and Baritone timings pass.

## Step A independent-client proof

`results/lab_only_client/20261028T232805064827Z/summary.json`: body jar moved out of
the server mods directory, port 8765 verified unbound, real Baritone client joined
and completed natural R1 in 579 ticks with zero damage/edits. Lab alone reset and
scored it. Jar and original server properties restored afterward. The standalone
proof harness is retained at `runtime/check-lab-only.py` (ignored runtime file).
Formal source-bound reset/legacy requalification remains open and must run on the
final frozen native source; existing gates remain closed/stale until then.

## Implemented so far

- Opt-in `dev-native-route-v1`; old lockstep/pipelined profiles retained.
- `RouteMotor.java`: bounded, validated ordinary route; per-tick position/velocity
  feedback, walk/ascent/drop/diagonal permissions, straight lookahead, sprint food
  gate, progress/step deadlines. No world edits or lab dependency.
- `RouteGeometry.java`: fresh local sensor reads only inside configured awareness,
  no chunk loads, unknown/hazards/fluids refuse, body clearance and support checks.
- `BodyRuntime`: follow/cancel ops, identity/sequence/development-only admission,
  progress/status, watchdog/explicit release/retire cancellation, no mixing native
  routing with scheduled Python controls. All actuation remains Carpet inputs.
- `native_driver.py`: route handoff, acquisition and background prefetch while
  native movement runs, status identity checks, normal Python planning preserved.
  Native route selection bypasses the Python ordinary-movement gate: the native
  motor owns steering and fresh swept-body checks. Gap preparation and Python
  edit/stock confirmation remain in place. Regression coverage retains the
  observed plan/discard loop from natural run `20261029T122717114577Z`; this
  Python handoff correction is not yet a successful live natural-route result.
- `StepMotor.java` executes bounded cut/place/bridge/support-stack/pillar edits
  using ordinary inputs; Python independently confirms fresh geometry and actual
  material debits, including pickup credits, before committing movement.
- `GapMotor.java` executes validated level one/two-block gaps with native runway,
  launch and landing timing. `RouteReflex.java` selects a known safe retreat from
  nearby observed hostiles; movement never waits for Python/model inference.
- No legacy motor fallback is hidden inside the native profile. The dashboard
  now offers `dev-native-route-v1` as its default for new natural sessions,
  explicitly labeled development-only. Broader acceptance remains open.

## Retained development attempts

| Artifact (results/hills/) | Outcome |
| --- | --- |
| 20261028T233355757800Z | Invalid, first driver rejected walking-only terrain forecast as unsupported macro; corrected driver handoff |
| 20261028T233434742078Z | Reached 40-block goal in 180 ticks, but fixture rejected missing prefetch adoption; corrected concurrent prefetch |
| 20261028T233602722622Z | Passed 40-block prefetch fixture, 183 ticks, one prefetch started/adopted, zero route replans |
| 20261028T233801266362Z | Walk variant 0 passed |
| 20261028T233817285273Z | Slab variant 0 passed |
| 20261028T233832129670Z | Stairs variant 0 passed |
| 20261028T233852103147Z | Drop variant 0 passed |
| 20261028T234231871508Z | Cut variant 0 passed |
| 20261028T234252442337Z | Place variant 0 passed |
| 20261028T234311030348Z | Bridge variant 0 passed exact geometry/stock proof |
| 20261028T234331004513Z | Reached staircase via explicitly enabled pillar alternative (3 blocks), failed exact staircase proof; retained; rerun without pillar permission |
| 20261028T234628964022Z | Staircase variant 0 passed exact six-block geometry/stock/ascent proof, 239 ticks |
| 20261028T234651978798Z | Pillar variant 0 passed exact proof, 70 ticks |
| 20261028T234707650147Z | Gap variants 0/1 passed exact no-fall/jump/no-edit proof, 87/84 ticks |

Native source hash at the first installed development build:
`5eed71dbb9257a99a0903d93db4f55d981ed4e15c89def23d128be9ea38e9b2b`.
Both Gradle projects built; RouteMotor safety tests run via Gradle check. Python
native/bridge/control-loop targeted tests passed. Broader testing follows changes.

Current installed native development source (ordinary + edits + gap + reflex):
`9cecafb23799a7fb3cedbc5d557ea018fb7cac950d16ac6e2cdf716841d7f20c`.
Java check includes RouteMotor (21), StepMotor (11), GapMotor (9) and RouteReflex
(5) safety/admission checks. Ten-variant family cohorts are now running sequentially
with a fresh server per cohort; ledger/harness: `runtime/run-native-cohorts.py`,
`results/native_cohorts/*/summary.json`. Never mix their source identities.

### Broad cohort journal

Active ledger: `results/native_cohorts/20261028T234820581705Z/summary.json`.
The wrapper restarts between families and continues after a retained failed family.
Its ordered families are mixed, walk, prefetch, slab, stairs, drop, cut, place,
bridge, staircase, pillar, gap. Do not start another live world operation while it
owns the arena. Latest complete Python suite: 669 passed before the newest driver
boundary/cleanup tests; rerun appropriate checks on the final source.

- Mixed `234835594848Z`: variants 0–3 passed, variant 4 timed out 1200 ticks
  at `[1280.5,82,1288.695...]`, about two blocks below the goal. Native batching
  consumed the final walking approach that the existing Python follower reserves
  for a terrain ascent. Fixed `native_driver.offered_route` to stop at the same
  `_terrain_approach_nodes` boundary for unreachable walking routes. Regression
  added; rerun mixed as a fresh cohort after this sweep (do not rescore old rows).
- Walk `235057877579Z`: 10/10 passed.
- Prefetch `235248833366Z`: 10/10 passed with required prefetch proof.
- Slab cohort is running next; final ledger is authoritative for remaining families.

The Python driver also now releases/cancels the native objective on terminal/error,
honors cancellation/goal revisions and records actual native timing rejections.
Each cohort's recorded Python hashes distinguish these changes; native source
remains `9cecafb...` so far. Do not pool old/new Python driver cohorts for timing.

Pending independent protocol proof harness (not yet run):
`runtime/check-native-controls.py`; run after cohort wrapper finishes. It tests
wrong identity, refusal to mix legacy controls, explicit cancellation, no-contact
watchdog, and refusal of native ops for a legacy course body. It starts/stops the
lab and despawns its own bodies in cleanup.

## Next work

1. Validate ordinary live fixtures (walk/slab/stairs/drop), including disabled and
   changed-geometry cases. Keep failed attempts and fix source rather than tests.
2. Broaden native cut/place/bridge/pillar/staircase/gap and local threat-reflex
   verification, retaining Python edit/stock confirmation and budgets.
3. Extend native protocol/driver tests; build/install provenance; regression,
   source-bound reset requalification; all five calm natural routes at matched
   awareness and per-route Baritone comparison. Preserve original C1 lab setup.
4. Update protocol/UI/docs and acceptance ledger only to verified status.

## Natural run 20261029T122717114577Z diagnosis (Oct 29, 2026)

Development-only, `dev-native-route-v1`, seed 1337, calm. Kept in the ledger.

| Route | Outcome | Cause |
|---|---|---|
| R1 | tick limit | Unmoved for 5,734 ticks, alternating `planning` / `awaiting_fresh_geometry`. Start cell is supported; an offline replay finds a normal first hop. Not yet pinned: route heads are not logged. |
| R2 | invalid at tick 45 | Driver error: "Native route status lost its accepted identity". |
| R3 | death at tick 2,432 | Airborne and unmoved for 1,210 ticks before falling. Not yet analysed. |
| R4 | tick limit | Edge stance (below). Unmoved for ~4,900 ticks. |
| R5 | tick limit | Edge stance (below). Unmoved for 5,679 ticks. |

**Edge stance (fixed).** The controller took its start cell as
`floor(body centre)`. When the body stood on a ledge edge, the centre column had
air below while the hitbox rested on the neighbouring block. `RouteSearch` then
returned "start is not known standable" in about 0.1 ms on every call, and the
terrain fallback did the same, forever. R4: body (124.52, 89, -46.95) on
terracotta at (124, 88, -48). `SegmentedController._stance_node` now picks the
supported cell under the hitbox, as Baritone's `pathStart` does. Replay on the
recorded R4 map: the old start gives no route; the corrected start gives a
58-node walking route to the goal. Replay on R5: 0 nodes before, 61 nodes after.
This code is shared by all three control profiles, which is why changing the
profile never fixed it.

**Slowness (not yet fixed).** Native walking segments are 6–13 cells. Each ends with the
motor stopping, Python polling, and a 2–6 tick replan. The goal sits outside the
known map, so every search ends "unreachable" and `offered_route` trims the
terrain-approach reserve. Terrain steps run one block at a time, at 16–20 ticks
each. Step C (planning through unknown space, D3) addresses the cause; a longer
native segment is only a mitigation.

## Natural run 20261029T125842184277Z (edge-stance fix live, Oct 29, 2026)

R1 reached goal (1,021 ticks), R4 reached goal (2,001 ticks). R2 and R3 invalid:
"Native step failed: native_step_displaced". R5 tick limit: unmoved 3,796 ticks
while the native motor rejected the planner's route 465 times with
`route_swept_geometry_unknown_or_unsafe` (Python and in-mod safety disagree).

**Step-down stutter (fixed in RouteMotor).** Across R4's 47 step-downs, speed fell to
~0.1 block/s at nearly every landing, and 12 reversed (backward velocity after
landing). Causes: (1) the motor eased off within 0.3 block of every target,
including a drop's landing cell; (2) when momentum carried the body past that
column, arrival failed and the motor turned back. The motor now skips ahead to a
later route point it is grounded on (Baritone PathExecutor re-sync) and keeps full
throttle through a drop the route continues straight through. Corners, ascents and
the final point still brake. Unverified live.

## Natural run 20261029T134955375284Z (R4, R5; Oct 29, 2026)

Both ended `stalled` (new cutoff) with the same pattern: Python offered a route,
and the native motor rejected it on its first tick with
`route_swept_geometry_unknown_or_unsafe`, repeated until the cutoff (R4 26 times, R5 ~60).

- R5: the body fell through a dead bush (no collision) onto a ledge edge, with two
  hitbox corners over an 11+ block cliff. `RouteGeometry.safe` required support
  under all four corners at every sample, including the current pose, so no move
  could pass, even away from the void. Now: all four on normal ground; a body that
  already overhangs may move while the number of supported corners never drops.
- R4: the body stood 0.37 off-centre beside a wall. The direct line to the next
  cell would clip it, though the step is clear from the cell centre. The motor now
  re-centres on the source cell once per step, then retries.

Both have motor verification cases; neither is verified live yet.

## Natural run 20261029T135915643474Z (Oct 29, 2026)

R1, R3 and R4 reached the goal (1,069 / 1,423 / 1,886 ticks). R2 (tick 1,564) and R5
(tick 240) were invalid: "Native route status lost its accepted identity". Not a
movement failure: one `/policy` read in each lacked all `*_follower` fields.
`BodyRuntime.updatePolicySnapshot` published the volatile snapshot in three stages
(base, compact terrain, followers), so the HTTP thread could read a partial one.
It is now built locally and published once. This likely also explains the
same error in R2 of run 20261029T122717114577Z (tick 45).

## Natural run 20261029T190159944470Z, Baritone 20261029T190943213516Z (Oct 29, 2026)

No identity errors: the snapshot-publish fix held. MCB 4/5, Baritone 5/5.

| Route | MCB ticks | Baritone ticks | Ratio |
|---|---|---|---|
| R1 | 998 | 577 | 1.73 |
| R2 | 1,268 | 531 | 2.39 |
| R3 | 1,519 | 598 | 2.54 |
| R4 | 1,811 | 771 | 2.35 |
| R5 | stalled at tick 1,342 | 855 | — |

**R5 stall (fixed in `planning_worker.py`).** The bot reached the top of a walkable
pocket on a badlands slope (2–3 block rises per block; goal 41 blocks higher).
Offline replay on the recorded map: the known-walking search exhausts in 5,489
expansions (~0.7 s) and returns `unreachable`, which starts the terrain fallback. Live,
the background worker cuts every search at 250 ms and reports `incomplete`, which
does not; each new map snapshot restarted the search from zero, forever. A wall-cut
search with no move to follow now resumes on its own snapshot until it finishes or
reaches the 8,192-expansion cap. Regression: `test_wall_cut_dead_end_search_resumes_until_unreachable`.

**R5 remainder is Step C.** With the stall fixed, the terrain fallback still finds
no work here: `EXCAVATABLE` covers only stone, cobblestone, dirt and grass, not
terracotta, and the slope is too steep for its staircase/pillar macros. Widening the
breakable set with tick costs is Phase 1; choosing edits versus detours is Phase 4
(D11). Not a motor issue.

**Speed split.** Of motor-active samples, 17% sprinted (0.238 block/tick); the rest
ran at 0.107–0.158 (airborne 24%, before a corner 21%, before a height change 17%,
before a route's last point 13%, ascents 6%). Handoffs stood ~7 ticks each, 15–34
per route. Step B changes (verified by checks only, not yet live):

- `RouteMotor`: steer and move in the same tick when the turn is ≤ 45° and the
  swept body is safe on the new heading (previously every corner turned in place);
  keep sprint into straight-through points, step-downs and while airborne (Carpet
  drops sprint immediately on `false`). Corners, ascents and the last point still
  drop sprint. 40 RouteMotor checks.
- `native_driver`: prefetch from the active route's end while the motor drives
  (previously only once the goal was in view) and splice the result onto the
  unfinished route, so the motor does not stop at segment ends. Dead ends, terrain
  work, gaps and committed steps keep the stop-and-decide path. `routes_spliced` is
  counted in the control-loop summary.

Not done here, left for later: sprint-jump ascents (Baritone `sprintAscends`).
Step C: the 250 ms searches that almost always end `incomplete`, plus path shape
(no string-pulling) and planning through unknown space.

Installed source hash after these changes: `2ca44cfc6f5bf4686d6c080925d07e9c333e985393d892555c8a153e8a2d606b`.

### Live rerun, October 29

See [the complete rerun note](STEP_B_RERUN_2026-10-29.md) and
`results/natural/20261029T205639210651Z/summary.json`. On the unchanged installed
source, at server view distance 4 and MCB chunk radius 64: MCB 4/5, Baritone 5/5;
zero damage, zero MCB timing rejections, no accepted-identity errors, and 99 route
splices. R5 still stalls, but its recording now reaches `terrain_planning` /
`terrain_incomplete`; no digging step executes. Retain this planner failure for
Step C. Full five-goal acceptance and formal qualification remain open.

## Reference method (D12)

Read, do not copy, Baritone v1.20.0 sources. New motor code is independently
written. Relevant source classes:

- [ActionCosts](https://github.com/cabaletta/baritone/blob/v1.20.0/src/api/java/baritone/api/pathing/movement/ActionCosts.java): walk/sprint block rates.
- [MovementTraverse](https://github.com/cabaletta/baritone/blob/v1.20.0/src/main/java/baritone/pathing/movement/movements/MovementTraverse.java): destination/support, forward execution.
- [MovementAscend](https://github.com/cabaletta/baritone/blob/v1.20.0/src/main/java/baritone/pathing/movement/movements/MovementAscend.java): near-rise jump timing.
- [MovementDescend](https://github.com/cabaletta/baritone/blob/v1.20.0/src/main/java/baritone/pathing/movement/movements/MovementDescend.java), [MovementFall](https://github.com/cabaletta/baritone/blob/v1.20.0/src/main/java/baritone/pathing/movement/movements/MovementFall.java): destination-relative descent/fall completion.
- [MovementDiagonal](https://github.com/cabaletta/baritone/blob/v1.20.0/src/main/java/baritone/pathing/movement/movements/MovementDiagonal.java): swept side-support conditions.

This records source review; it does not claim tick-for-tick Baritone parity or a
completed planner tick-cost model (Step C).
