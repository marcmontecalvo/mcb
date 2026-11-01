# MCB roadmap and document index

**Start here.** This file is the single list of what is done, what is next, and
where each plan or ledger lives. It holds no decisions of its own: decisions and
the phase plan are in [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md)
(authoritative; it wins any conflict). Results live in the ledgers listed below.
Update this file whenever an item here is finished or a new one is found.

Last updated: October 31, 2026 (`main` at `23e0857` plus uncommitted Oct 31 work:
cohorts 16–26). Installed mod source hash: `2eed1251…`.

## Where things stand

| Area | State | Detail |
|---|---|---|
| Mod split, D9 (step A) | Done (development evidence) | Direction doc §0 step A |
| In-mod route follower, D10 (step B) | Done for development; formal qualification open | [STEP_B_PROGRESS](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN](STEP_B_RERUN_2026-10-29.md) |
| Planner core, D11 (step C) | In progress; cohort 26 is 10/10, every route within 1.08× of Baritone, no motor stops | [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md) (cohorts 1–26) |
| Baritone parity, D12 | Weights and moves reimplemented | [BARITONE_WEIGHTS_AND_MOVES](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md), `config/weights/baritone-v1.20.0.json` |
| Awareness parity, D8 | `sections` preset with delivery fixed (Oct 31) | [AWARENESS_MODES](AWARENESS_MODES.md) |
| Swimming (§5 of the direction doc) | Planner and body done; live fixture cohorts not run | Direction doc §5 status lines |
| Qualification | Stale: the body source changed | [NAVIGATION_QUALIFICATION.json](NAVIGATION_QUALIFICATION.json), [gates.json](gates.json) |
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
- **Cohort 26 (`20261031T214556225885Z`, mod `2eed1251`, current code): R1
  608/606, R2 563/564, R3 732/735, R4 836/830, R5 924/904.** 10/10, no motor
  stops; medians 1.04×, 1.06×, 0.78×, 1.04×, 1.08×. Cohorts 21–25 and the fixes
  between are in the ledger.

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
4. **Climbing out of water where the bank is +1:** no move does this now. Baritone
   has none either (unverified); a place/pillar edit would be the route if needed.
5. **More reps and a second seed** before trusting the 10/10: two reps on one
   seed is development evidence only.

**Speed**

6. **Planner speed on the live grid view.** Offline, a grid view with swimming on
   ran at ~1.7k nodes/s against ~5k nodes/s for a plain dict with swimming off.
   The two variables are not yet separated; profile before fixing.
7. **Search outside the controller's interpreter lock.** Control, map admission and
   search share one Python process: live search runs at about half the offline
   speed. A separate process with a shared grid is the cheap step.
8. **Compiled planner core** (Java in the body, or a typed-array expand loop).
   This changes the D9/D10 split, approved in principle; decide after 6–7 show what
   is left. Handoff note: [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md) cohort 13.
9. **Partial-route quality.** ~1,000-node partials can still lead into pits and
   lakes (R5). Commitment to a plan's tail is done (cohort 18); a longer search
   while standing still is the other option.
10. **Several edits per plan.** Climbing out of a pit re-plans for ~1 s
   (`EDIT_PATIENCE_MS`) before every single dig or place.

**Scored comparison**

11. **Requalify the mod** (needs the body to stop changing): run
    `runtime/requal-awareness.py` with the server stopped, then
    `runtime/finalize-movement.py`. Scored navigation refuses to run until then.
12. **Frozen comparison, Phase 7:** 10 routes × ≥3 seeds × 3 reps; MCB ≥ 27/30
    and median ≤ 2× Baritone at matched awareness, plus night diagnostics.
    Needs more routes and seeds, and a recorded decision on Baritone's settings
    (see open questions).

**Features from the plan**

13. **Survival weights and Jev profiles, Phase 6** (`race`, `safe`, `frugal`,
    `hunt`, `escort`), based on `config/weights/baritone-v1.20.0.json`.
14. **Interaction goals, Phase 3** ("reach block X"): small and independent.
15. **Movement leftovers:** flowing-water push, ladder facing, a cost for using up
    blocks on long causeways, the placement-aim stall from an off-centre stance,
    and the 0.01 edging-bend margin (R5's lake route choice is item 1).
16. **Live fixture cohorts** for swimming and ladders (10 cases each,
    [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json)). The code exists; the
    acceptance runs do not.

Housekeeping: delete the `-fast` awareness profiles once the cohort-15 evidence no
longer needs them; commit or push `main` (`origin/main` is behind).

## Open questions for Marc

- Baritone's settings for the frozen comparison. MCB has parkour, swimming and
  section awareness; Baritone runs with its defaults (parkour off).
- Whether to start the compiled planner core (item 8) now or after items 6–7.
- Whether to commit the Oct 31 work (cohorts 16–20 code and docs) to `main`.
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
