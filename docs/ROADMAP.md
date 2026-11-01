# MCB roadmap and document index

**Start here.** This file is the single list of what is done, what is next, and
where each plan or ledger lives. It holds no decisions of its own: decisions and
the phase plan are in [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md)
(authoritative; it wins any conflict). Results live in the ledgers listed below.
Update this file whenever an item here is finished or a new one is found.

Last updated: November 1, 2026 (cohorts 16–28 on `main`). Installed mod source
hash: `5ffbde56…` (adds the planner core class; cohorts 27–28 ran on `2eed1251`).

## Where things stand

| Area | State | Detail |
|---|---|---|
| Mod split, D9 (step A) | Done (development evidence) | Direction doc §0 step A |
| In-mod route follower, D10 (step B) | Done for development; formal qualification open | [STEP_B_PROGRESS](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN](STEP_B_RERUN_2026-10-29.md) |
| Planner core, D11 (step C) | In progress; cohort 28 is 10/10, every route within 1.09× of Baritone | [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md) (cohorts 1–28) |
| Baritone parity, D12 | Weights and moves reimplemented | [BARITONE_WEIGHTS_AND_MOVES](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md), `config/weights/baritone-v1.20.0.json` |
| Awareness parity, D8 | `sections` preset with delivery fixed (Oct 31) | [AWARENESS_MODES](AWARENESS_MODES.md) |
| Swimming (§5 of the direction doc) | Planner and body done; live fixture cohorts not run | Direction doc §5 status lines |
| Qualification | Current for mod `a94dcdef` (Nov 1) | [NAVIGATION_QUALIFICATION.json](NAVIGATION_QUALIFICATION.json), [gates.json](gates.json) |
| Gates | G0–G3, G5 passed; G4 in progress; G6–G8 not run | [gates.json](gates.json) |

Latest numbers against Baritone (ticks, calm, seed 1337, Baritone run
`20261029T205639210651Z`: R1 581, R2 531, R3 935, R4 801, R5 848):

- Cohort 14 (`20261031T003318288661Z`): R1 1062/1023, R2 947/fail,
  R3 952/1024, R4 913/1034, R5 1748/2180.
- Cohort 20 (`20261031T035652070241Z`): R1 609/619, R2 558/559, R3 916/937,
  R4 831/828, R5 2896/3052. 10/10; R5 swam through a lake Baritone walks around.
- Cohort 22 (`20261031T144601883497Z`): R1 602/607, R2 558/558, R3 807/824,
  R4 829/838, R5 1430/1334. 10/10.
- Cohort 24 (`20261031T151251308104Z`, mod `51e5089f`): R1 618/612, R2
  567/578, R3 701/700, R4 866/865, R5 921/909. 10/10; medians 1.06×, 1.08×,
  0.75×, 1.08×, 1.08×.
- Cohort 25 (`20261031T200524257733Z`, mod `51e5089f`): R1 611/607, R2
  563/563, R3 703/772, R4 862/839, R5 899/902. 10/10; medians 1.05×, 1.06×,
  0.79×, 1.06×, 1.06×.
- Cohort 26 (`20261031T214556225885Z`, mod `2eed1251`): R1 608/606, R2 563/564,
  R3 732/735, R4 836/830, R5 924/904. 10/10, no motor stops.
- **Cohort 28 (`20261101T005033322463Z`, current code): R1 623/619, R2 576/568,
  R3 736/738, R4 841/848, R5 905/940.** 10/10; medians 1.07×, 1.08×, 0.79×, 1.05×,
  1.09×. Cohorts 21–27 and the fixes between are in the ledger.

## Oct 31 findings that change earlier assumptions

Details and evidence: [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md), "Oct 31".

1. **Section batches were lost before the controller started reading.** The body
   sends each map section once. Batches produced before Python's first `/policy`
   read (40–70 ticks after the course starts) were never delivered, and they are
   the ones covering the start area. *Fixed:* unread sections are carried into the
   next batch (mod `fdc9e533`). Earlier analyses that read the body's
   `policy_sample` log as "what MCB knew" overstated its knowledge.
2. **Reading `/policy` consumes terrain.** A served batch counts as delivered.
   Never poll `/policy` and discard the result; use `/state` to wait for a state
   (`natural.py` fixed).
3. **R1's gap was missing data, not planner throughput.** The Oct 30–31 reading
   "the binding limit is planner throughput" is withdrawn for R1. Throughput is
   still low (about 3,000 nodes/s live, against Baritone's 25–30k nodes per
   decision) and still matters for partial routes and pit escapes.
4. **Faster awareness is not better.** Ingesting every 2 ticks took Python time
   from the planner. The `-fast` awareness profiles are rejected; use
   `awareness-sections-64.json`.
5. **The saved `map.sqlite3` files are accurate.** Missing sections were never
   delivered, not lost on save.
6. **One planner worker lane serves both search and prefetch.** Cancelling must
   name its job (`cancel(token)`), and a superseded search resubmits itself.
   Before this, searches could hang "pending" until the stall cutoff.

## Oct 31 (later) findings, cohorts 16–20

Details: [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md), cohorts 16–20.

1. **The delivery fix carried over to every route** (cohort 16): R1, R2 and R4
   came within 4–14% of Baritone with no other change.
2. **`replay_delivered.py` under-reported the map after mod `fdc9e533`.** Carried
   sections are logged only when scanned; the script kept only read batches. Fixed
   (`OLD_MOD=1` for older attempts). One diagnosis ("drops through unknown space")
   was wrong because of it.
3. **Partial-route ping-pong was the common failure** (cohort 14 R2, cohort 17
   R5): partials from neighbouring starts end in opposite directions. A new
   partial now replaces the current plan's tail only if it ends ≥1 block closer to
   the goal; favouring holds the whole plan.
4. Fixed body/planner mismatches: a sunk start under a surface stance; a lake drop
   off a ledge overhanging the water (`swimSafe`); a lake bed more than 8 blocks
   under its surface; identical splices that reset the motor's step deadline.
5. The controller is built before the course starts: the first `/policy` read is
   on the start tick, and following starts 7–9 ticks in (was 11–41).

## Next work, in order

Each item names where its detail or evidence lives. Numbers are priority, not
dependencies, except where noted.

**Confirm and close gaps**

1. Falling blocks over a cut: settle cost added (cohort 22); watch for repeats.
2. **Route choice from partial plans** varies rep to rep (R3: ~30 ticks between
   the x=-77 and x=-84 lines). Low priority; it falls under partial-route quality
   (item 9). Done: the R3 grounded swept-unsafe stop was a bend-sweep heading bug,
   fixed in cohort 26.
3. Done (Oct 31): `stall_seconds = 0` also disables `goal_no_progress`
   (`controller.goal_progress_ticks = None`; `test_native_driver.py`).
4. Closed Nov 1 (water exit onto a +1 bank): not possible in vanilla, so nothing
   to build. `LivingEntity.jumpOutOfFluid` (26.3 bytecode) sets vertical speed 0.3
   only while in water, against a wall, with the box 0.6 higher free; the boost
   ends when the feet leave the water. Cohort 19 R5 peaked at 63.92 about 350 times
   under a bank top at 64.00. Baritone's ascend from water is our level exit; it
   has no +2. The planner has not offered this move since cohort 21.
5. **More reps and a second seed** before trusting the 10/10: two reps on one
   seed is development evidence only.

**Speed**

6. Done Oct 31 (planner speed on the grid view). Separated on the cohort 25 R3
   map, an 8,202-node search from the start, best of 7: grid with swimming 3,836
   nodes/s, grid without 4,475, plain dict with 4,512, dict without 4,563. Both
   costs were 10–20%, not 3×. Fixed: `GridView.get` converts whole-number float
   heights inline (a third of lookups went through `_locate`), and swim checks
   rule out land with one lookup (`surface_water`, `_landings`). Now 4,184 / 5,244
   (grid with / without swimming), +9% / +17%; replayed routes unchanged. What is
   left is per-node interpreter cost: items 7 and 8.
7. Superseded Nov 1 by item 8 (search outside the interpreter lock): the Java
   core runs in the body, outside Python's lock, at 5–10× Python's offline speed;
   a separate Python process would copy map snapshots for about 2×.
8. **Compiled planner core** (Java in the body). Started Oct 31 at Marc's request.
   Stage A done: `mcb-body/.../RoutePlanner.java` is the land subset of
   `route_search.py` (tick costs, Baritone search and diagonals, shapes).
   Stage B done: `scripts/analysis/planner_parity.py` exports a delivered map and
   runs both. Cohort 25 R3 start, 8,000 cap: same 6,010 expansions, route and
   cost (608.08); Java 125,608 nodes/s against Python's 12,603 (10×). Cohort 27 R5
   start at 1,500 / 3,000 / exhaustion: same partial routes, Java 5–8× faster.
   Stage C started: unknown-space exits (D3 frontier, exit pricing, buried
   dig-out rise, walk-rate partial estimate) match Python on both maps
   (`UNKNOWN=1`; R3 8,000 cap: same route and cost 511.61, 84k against 13k
   nodes/s). Stage C done Nov 1: swimming, water landings, ladders and gaps
   match Python exactly on the R3 and R5 maps (9–13× faster). Stage E done as
   opt-in: body `plan_route` op (10 ms per tick), `BodySearch`, `--body-planner`.
   Cohort 29 with it: 10/10 but 15% slower (R5 skips a 2-block cut it cannot
   price; R2/R3 7–9% slower, likely because the body reads the live world in
   range while Python plans on delivered cells). Open: stage D (edit edges in
   Java) and planning on the delivered map in the body; then make it default. A Java search
   runs outside Python's interpreter lock, so it also answers item 7.
9. **Partial-route quality.** ~1,000-node partials can still lead into pits and
   lakes (R5). Commitment to a plan's tail is done (cohort 18). Oct 31: the first
   search of an attempt uses the full 3,000-node floor (cohort 27 R5 rep 2 went
   into the lake from a 1,500-node start partial; 3,000 picks the ridge in
   replay). Cohort 28: 10/10, no lake, R1/R2 5–15 ticks slower (accepted).
10. **Several edits per plan.** Oct 31: `_begin_route_edit` queues the whole run
   of consecutive edit edges from one search (each re-checked on fresh geometry
   before it starts), instead of one search per edit. Unit test in
   `test_planner_core.py`; cohorts 27–28 had only single edits, so no live
   multi-edit chain yet.

**Scored comparison**

11. Done Nov 1 (requalify the mod): `results/awareness/20261101T020439115161Z-regression`
    passed all seven checks (awareness 15/15, 20 initial resets, 29 adversarial,
    C2–C5 20 resets each) on mod `a94dcdef`; promoted by `runtime/finalize-movement.py`
    to `docs/MOVEMENT_PARITY_PROOF.json`, G2 and `NAVIGATION_QUALIFICATION.json`.
    Any later body change needs this rerun.
12. **Frozen comparison, Phase 7:** 10 routes × ≥3 seeds × 3 reps; MCB ≥ 27/30
    and median ≤ 2× Baritone at matched awareness, plus night diagnostics.
    **Calm part met Nov 1** (`scripts/frozen-comparison.ps1`, commit `3cf9426`,
    development runs, Baritone with parkour, view distance 4, awareness-sections-64):
    | Seed | MCB run | MCB ok | MCB median | Baritone run | Baritone ok | Baritone median | Ratio |
    |---|---|---|---|---|---|---|---|
    | 1337 | `20261101T023131287363Z` | 30/30 | 717.5 | `20261101T030328345130Z` | 30/30 | 773.5 | 0.93 |
    | 9001 | `20261101T034420070608Z` | 30/30 | 670 | `20261101T041609691919Z` | 29/30 | 747 | 0.90 |
    | 42424242 | `20261101T045505457355Z` | 30/30 | 674.5 | `20261101T052517944411Z` | 30/30 | 594.5 | 1.13 |
    Baritone's miss (9001 R9 rep 3) is `invalid`, a harness error ("Stop the lab
    server before replacing runtime/course files"), not a navigation failure.
    Paired by route and rep, Baritone is faster in 77 of 89 attempts both
    completed (total ticks 60,057 vs MCB 66,190, about 10% faster); the per-seed
    medians favour MCB on two seeds only because route lengths differ.
    MCB broke 12 blocks in total, Baritone 71. MCB took 1 damage on each 42424242 R8 attempt (3 total), no deaths.
    Still open: night diagnostics for this item.

**Features from the plan**

13. **Survival weights and Jev profiles, Phase 6.** Oct 31, code done:
    `TerrainPolicy` survival terms (Baritone mob avoidance `mob_coefficient` /
    `mob_radius`, `hazard_penalty` beside lava/fire); profiles in
    `config/profiles/` with `--cost-profile`; visible hostiles feed the search;
    `jev_contract.py` (situation, decision, validation, deterministic fallback,
    source recorded). No fall-damage gate: dry drops stop at 3 blocks, below
    vanilla's damage threshold. **Night acceptance run Nov 1 and not met.**
    `race` (`20261101T010410539420Z`): 6/10, 3 deaths, damage 94.2,
    exposure 3,736 hostile-ticks within 16. `safe` (`20261101T011522361531Z`):
    7/10, 3 deaths, damage 93.8, exposure 6,162 (one R5 attempt took 3,239 ticks).
    Every death was close-range: a creeper within 3 blocks, or a zombie villager
    and spider within 1.5, or three creepers and a husk within 2. Plan-time
    avoidance of mobs seen when the route was planned cannot escape an attacker
    already beside the body. A longer reactive flight was tried and rejected:
    `safe` with a 6-block sprinting retreat (creepers from 7 blocks;
    `20261101T015007016363Z`) went 6/10, 2 deaths, damage 118, exposure 7,240, two stalls:
    fleeing and re-planning kept the body among the mobs. The flight setting stays
    available (`follow_route` `flee`, profile `flee`) but no profile uses it; the
    reflex's hostile list now includes zombie villagers. **Open research:** night
    survival needs combat or shelter (direction doc "Later"), not route weights.
    Screening Jev waits for the model track.
14. Done Oct 31 (interaction goals, Phase 3): `RouteSearch(interact_reach=4.5)`
    and `SegmentedController(interact_reach=...)` treat the goal as a block to
    reach with the eye (feet + 1.62, centre ray, every crossed cell known and
    clear). Acceptance fixtures in `tests/test_planner_core.py`: ledge table
    reached from below without climbing; never through a wall or unknown cell.
    Not yet exposed in the natural-run CLI (no route needs it).
15. Closed Oct 31 (movement leftovers), each on evidence:
    - 0.01 edging-bend margin: fixed by sweeping on the exact bearing (cohort 26,
      no motor stops in 10 attempts).
    - Causeway block cost: `placement_penalty` (4 per place) plus the escalating
      edit-chain cost (cohort 9) already price it; no long causeway since cohort 12.
    - Placement-aim stall from an off-centre stance: seen once (cohort 12a R2) and
      not in cohorts 13–26. Reopen with the attempt if it recurs.
    - Ladder facing: vanilla climbs any ladder while jump is held, and the motor
      holds jump; the wall side is blocked by the wall itself. No change.
    - Flowing-water push: native routes are cut before flowing water, so push
      cannot mislead the motor. Model it when a route needs to cross a current.
16. Done Nov 1 (live fixture cohorts): water `results/hills/20261101T014540110734Z` and ladder
    `results/hills/20261101T014706699506Z`, 10/10 each with every proof (mod `a94dcdef`). New
    ladder fixture in `DevelopmentHill`. The low-air proof also accepts a native
    surface swim (air never below the injected 90, refilled to at least 200).

Housekeeping (Nov 1): `main` is committed and pushed after each item. The `-fast`
awareness profiles stay: cohorts 15d–15f name them, and the ledger keeps every
cohort reproducible. `scripts/analysis/` now passes ruff (long lines and late
imports allowed there).

## Open questions for Marc

- Answered Oct 31: the frozen comparison runs Baritone with moves matched to
  MCB's (parkour on), which needs a new Baritone baseline; the compiled planner
  core (item 8) starts now; finished work is committed and pushed to `main`.
- Carried from the direction doc: `race` profile block budgets; server view
  distance for parity runs (currently 4).

## Document index

**Plan and decisions (current)**

| Document | Role |
|---|---|
| [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) | Authoritative plan: decisions D1–D12, phases, step order, swimming notes |
| [DECISIONS.md](DECISIONS.md) | Decision log |
| [ROADMAP.md](ROADMAP.md) | This file: status, ordered work, index |
| [BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md) | Baritone weights and the move-by-move comparison |
| [BARITONE_REFERENCE.md](BARITONE_REFERENCE.md) | Baritone baseline setup (pinned client, launcher, how it is run) |
| `Minecraft_AI_Technical_Handoff.md` (repo root) | Original specification; never edited |

**Ledgers and trackers (results; keep every cohort)**

| Document | Role |
|---|---|
| [STEP_C_PROGRESS_2026-10-29.md](STEP_C_PROGRESS_2026-10-29.md) | Planner-core cohorts 1–20 and causes; Oct 31 findings |
| [STEP_B_PROGRESS_2026-10-28.md](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN_2026-10-29.md](STEP_B_RERUN_2026-10-29.md) | In-mod follower work and its live rerun |
| [PHASE0_NATURAL_BASELINE_2026-10-28.md](PHASE0_NATURAL_BASELINE_2026-10-28.md), [PHASE0_FIX_PLAN_2026-10-28.md](PHASE0_FIX_PLAN_2026-10-28.md), [PHASE0_FIXES_RESULTS_2026-10-28.md](PHASE0_FIXES_RESULTS_2026-10-28.md) | Phase 0 baseline and fixes (done) |
| [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json) | Acceptance tracker for the awareness/movement sprint |
| [gates.json](gates.json) | Gate ledger G0–G8 |
| [NAVIGATION_QUALIFICATION.json](NAVIGATION_QUALIFICATION.json) | Reset/navigation qualification (stale until item 10) |
| `docs/*_PROOF.json`, `docs/G*_*.json` | Evidence files bound to source hashes |

**Reference (how things work)**

| Document | Role |
|---|---|
| [AWARENESS_MODES.md](AWARENESS_MODES.md) | Awareness presets, `sections` delivery semantics |
| [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md) | Body/lab HTTP protocol, `/policy` delivery rule |
| [SPATIAL_NAVIGATION.md](SPATIAL_NAVIGATION.md) | Map memory, recall, planner/motor boundary |
| [LAB_UI.md](LAB_UI.md) | Dashboard and how to run the lab |
| [SETUP_STATUS.md](SETUP_STATUS.md) | Runtime setup and G0–G3 history |
| [CONTROL_LATENCY_FOLLOWUP_2026-10-27.md](CONTROL_LATENCY_FOLLOWUP_2026-10-27.md), [CONTROL_PROFILE_R2_2026-10-28.md](CONTROL_PROFILE_R2_2026-10-28.md) | Control-loop latency findings |
| `scripts/analysis/` | Attempt analysis: planner trace, time budget, timeline, delivered-map replay |

**Model work (separate track, paused behind navigation)**

| Document | Role |
|---|---|
| [EXPERIMENT_QUEUE.md](EXPERIMENT_QUEUE.md) | Queued model experiments; its "next sprint" section is history |
| [G4_SCREENING.md](G4_SCREENING.md), [G4_LIVE_INTEGRATION.md](G4_LIVE_INTEGRATION.md) | Model screening and live integration |

**History or partly superseded** (banners in each; see the direction doc's
"Superseded and legacy documents")

[AWARENESS_MOVEMENT_SPRINT.md](AWARENESS_MOVEMENT_SPRINT.md),
[AWARENESS_MOVEMENT_RESULTS.md](AWARENESS_MOVEMENT_RESULTS.md),
[AUTONOMOUS_RUN.md](AUTONOMOUS_RUN.md),
[PAUSE_HANDOFF_2026-10-27.md](PAUSE_HANDOFF_2026-10-27.md),
[MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md](MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md),
[NATURAL_TERRAIN_COMPARISON.md](NATURAL_TERRAIN_COMPARISON.md),
[ROLLING_TERRAIN_PLANNING.md](ROLLING_TERRAIN_PLANNING.md),
[HILL_NAVIGATION.md](HILL_NAVIGATION.md),
[NAVIGATION_IMPLEMENTATION.md](NAVIGATION_IMPLEMENTATION.md),
[JEV_NAVIGATION_PLAN.md](JEV_NAVIGATION_PLAN.md),
[HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md),
[BARITONE_MOTOR_DESIGN.md](BARITONE_MOTOR_DESIGN.md).
