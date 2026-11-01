# Step C development progress — planner core (D11)

Development evidence only, not acceptance or qualification. The governing plan is
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) §0 step C;
the ordered work list is in [ROADMAP.md](ROADMAP.md).
Every cohort below is kept, including failed ones; do not pool them.

## What was built

All three parts sit behind their own `TerrainPolicy` flag (default off, so older
profiles behave as before). Profiles: `config/stepc/terrain-stepc-*.json`.

| Flag | Phase | What it does |
|---|---|---|
| `planner_tick_costs` | 1 | Edge costs in game ticks, reimplemented from Baritone `ActionCosts` (walk 4.63, sprint 3.56, `JUMP_ONE_BLOCK_COST`, `distanceToTicks` falls, parkour). Break time from vanilla hardness/tool (`break_ticks`), Baritone break (+2) and place (20) costs, plus MCB-measured overheads (below). Widens the breakable set (`WIDE_EXCAVATABLE`: terracotta colours, sandstone, andesite/diorite/granite, tuff, deepslate, ores, soils, sand/gravel, logs, leaves). The body's `StepMotor` allowlist matches it (a test enforces this). Heuristic uses Baritone's `GoalBlock` form: horizontal + separate vertical term. |
| `planner_unknown` | 2 | A known node beside unknown space can end the search; its remainder costs `unknown_penalty` × walking time (D3). The route itself only contains known, checked cells. Unknown cells inside the body's always-refreshed box (±8 h, ±4 v) do not count. The best exit seen is followed when a search is cut by its budget. `heuristic_weight` 0 = weighted A* (remaining priced like unknown space); 1..10 = Baritone-form heuristic at sprint rate × weight. |
| `planner_edits` | 4 | Cut-steps and footholds (`prepare_step`) are edges in the main search, with a per-node overlay of their predicted edits. The controller hands each edit edge to the existing step executor; Python still confirms fresh geometry and stock. The motor is never offered cells past an unexecuted edit, and no prefetch is planned past one. |

MCB-measured edit overheads (live, not Baritone values): 15 ticks per edit step
(stop, aim, confirm, restart), 6 per extra block re-aim, 20 per stacked second
placement, 15 per side-anchored bridge placement.

Other fixes found by the live runs:

- `splice_route` never joins a route that revisits a node. A stale prefetch led back
  over the active route; the looping list never shrank and was re-offered forever.
- A native pillar step no longer creates a vertical committed walk (RouteMotor
  correctly refused it: `route_edge_permission`).
- A failed native edit step marks that edge failed and replans, instead of
  invalidating the attempt.
- Final map-archive flush waits 60 s (was 10 s) for large awareness sweeps.
- CLI `natural run` accepts `dev-native-route-v1` and `--stall-seconds`.

Offline tooling: a closed-loop simulator (scratch, not committed) replays the
planner on recorded maps. It catches loops and gross regressions but cannot rank
variants: cells the live bot never saw are unknowable. Committed analysis scripts
(Oct 31) are in `scripts/analysis/`: `planner_trace.py` (planner state per tick),
`time_budget.py` (stopped/slow/walk/sprint by controller state, plus a position
timeline), `timeline.py` (requests, routes, steps) and `replay_delivered.py`
(rebuild the map the controller actually had and replay one planning decision).
Replay only batches Python read: the body's `policy_sample` log also contains
batches the controller never received (see the Oct 31 findings).

## Awareness finding (D8 parity)

The `chunk` preset with `-1` (full height) scans 2,048 cells per 5 ticks in an
expanding cube that restarts each time the body moves 16 blocks. Recorded sweeps
reached ~100k of 6.4M cells, so MCB actually knew ~23 blocks around itself, not
64 (R5 stall: nothing known past z=-92 or above y=96). Baritone receives whole
loaded chunks. `config/stepc/awareness-chunk-64-fast.json` (8,192 cells/update,
±48 vertical) reaches ~30–43 blocks before each restart. Real parity needs a
section-based scanner in `mcb-body` (bulk chunk-section reads).

**Update Oct 30–31:** the `sections` preset (`SectionScheduler`,
`config/stepc/awareness-sections-64.json`) now sends whole loaded sections,
nearest first. On Oct 31 it turned out the first batches never reached the
controller (below); with delivery fixed, MCB's first plan on R1 sees what Baritone
sees and takes Baritone's route.

## Live cohorts (seed 1337, five calm routes, view distance 4, native motor, 5 s stall)

MCB `segmented_goal`, ticks to goal (fail = stalled or tick limit, inv = invalid).
`results/natural/<stamp>/summary.json`. Each row's code differs from the previous
one; never pool rows. Baritone reference (same setup, run 20261029T205639210651Z):
R1 581, R2 531, R3 935, R4 801, R5 848. Step B planner (same run): 1,130, 982,
1,525, 1,836, fail.

| Cohort | Stamp | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|---|
| 1: all flags, weighted heuristic | `20261030T002908756029Z` | fail | fail | fail | 1716 | fail |
| 2: + splice-cycle and near-box frontier fixes | `20261030T004308126362Z` | 1793 | 1823 | fail | inv | fail |
| 3: + fast awareness, edit overhead | `20261030T011203175248Z` | 1500 | fail | fail | 1731 | inv |
| ablation: ticks + unknown, no edit edges (stopped early) | `20261030T012618920529Z` | inv | fail | — | — | — |
| 4: GoalBlock heuristic | `20261030T013611935507Z` | 1759 | 2034 | 2303 | fail | fail |
| 5: ticks + edits, unknown off | `20261030T015237755787Z` | 1139 | 1416 | 2676 | 2012 | fail |
| 6: as 5, fast awareness | `20261030T020748319789Z` | 1060 | 1043 | 2745 | 1562 | fail |
| 7: all flags, heuristic weight 1, budgets 64 | `20261030T022610693410Z` | 1162 | 2759 | 1464 | 1624 | 1610 |
| 7 repeated ×2 | `20261030T023556227338Z` | fail/fail | 1296/fail | 1411/1492 | 1731/1605 | fail/fail |
| 8: + full-speed sneak edge approach (mcb-body) | `20261030T030553486229Z` | 1472/1449 | 2188/fail | 1556/1544 | 1746/1706 | fail/fail |
| 9: + escalating edit-chain cost | `20261030T033614954664Z` | 1363/1549 | 1283/1344 | fail/1467 | 1731/1695 | 1969/3623 |
| 10: + steep-rise exit pricing | `20261030T040009493308Z` | 1332/1119 | 1325/1299 | 1443/fail | 1495/1653 | 3535/2013 |
| 11: + buried exits priced as dig-out | `20261030T185050835580Z` | 1353/1537 | 1287/1090 | 1547/1336 | 1452/1540 | 3356/3603 |

| 12a: cohort 11 profile on mod `4b2e014f` (sprint-jump ascents, swim/ladder/new step kinds unused) | `20261030T192842174436Z` | 1367/1538 | fail/fail | 1465/1418 | 1740/1595 | 3647/3372 |
| 12b: Baritone profile (`movement-baritone`, `terrain-stepc-baritone-moves`) | `20261030T194952532182Z` | fail/fail | fail/fail | fail/fail | fail/fail | fail/fail |

12a R2: rep 1 froze 56 ticks aiming a stacked placement from an off-centre stance
(crouched, pitch 83°; same placement code as cohort 11); rep 2 spent 63 of 64
breaks digging out of a pit next to the goal. 12b: 8 of 10 looped on one diagonal
the body rejects (`route_swept_geometry_unknown_or_unsafe`) — Baritone's rule cuts
over holes deeper than `max_drop`, but `RouteGeometry.safe` needs every hitbox
corner over support — and the controller resubmitted it until the stall cutoff.
Fixed before 12c: side columns need known support within `max_drop`, no edging,
and geometry/drop rejections mark the edge failed.

| 12c: + diagonal stopgap, failed-edge marking | `20261030T200723012823Z` | 983/fail | fail/fail | fail/fail | 1638/1334 | 1803/fail |

12c causes: Carpet's "once" jump never fires in water, so swimmers sank under
their route (R1 rep 2); stale route starts after a water landing or a walk
during planning were rejected as `route_start_displaced` (R2, R3); a 250 ms
partial search tunnelled 30 blocks (R2 rep 1); a 45-block rising causeway used the
whole place budget (R5 rep 2). Fixed for 12d: jump held in water and on climbables,
start re-sync, no edits from wall-cut partials, and Baritone corner cuts and
edging in the body (mod `d6713d36`).

| 12d: mod `d6713d36` (jump held in water, start re-sync, Baritone corner cuts/edging), no edits from wall-cut partials | `20261030T203732413689Z` | fail/fail | 1110/fail | fail/1520 | 1415/1401 | fail/fail |

12d: R3 rep 2 swam 236 ticks and succeeded. R1 (both) and R5 rep 2 sat in
"planning" for 5 s: an edit-first partial kept resuming until the 8,192-node cap,
which takes ~5 s at ~1,700 nodes/s (fixed: 1 s patience, `EDIT_PATIENCE_MS`). R5
also chose a 20-block drop into a lake the motor's swim sweep then rejected. Time
budget (cohort 11 R1): path 223 blocks for 136 straight-line; sprint 21% of ticks,
turning 26%, walking on straight ground 21%, climbing 19%. Next work: motor speed
(instant heading, sprint everywhere safe, early segment planning), section-based
awareness parity (D8), and planner search speed (~1.2 ms per node now).
(Oct 30–31: the first two were done for cohort 13; the current ordered list is in
[ROADMAP.md](ROADMAP.md).)

| 13: mod `a5daaa48` + all Oct 30 improvements (sections awareness, planner speed, early splice), Baritone profiles | `20261030T211900646949Z` | fail/fail | 923/951 | 1114/1118 | 1208/fail | 1825/1851 |

13 causes (diagnosed Oct 31 with the planner trace added to `navigation_tick`):
- R1 rep 1: the body refused every dig in a sand pit as `native_cut_neighbor_unsafe`.
  The planner (`cut_allowed`, `baritone_moves`) allows a side sand/gravel
  neighbour that rests on a solid block; `StepMotor` refused any side falling
  block. Fixed in the body to match (a side one falls only if its own support goes).
- R1 rep 2 and R4 rep 2: after a route ended, the planner spent 100 ticks in
  `planning` without a result, with an offline replay finding a route in ~1 s:
  most likely the shared-lane cancel bug found in 13b (below).
- Live search runs at ~3,000 nodes/s (about 750 per 250 ms job; offline about
  6,000 with a plain dict): control, observation and search threads share the GIL.
- Both R1 reps (and the 13b rerun) reach the same sand pit at `[-63,61,-63]`. An
  830-node partial route from `[-59,67,-55]` dives into it; the complete search
  from there (31,675 nodes) walks round it with no edits. Getting out takes
  ~24,000 nodes per stop, so every edit costs the full 1 s `EDIT_PATIENCE_MS`
  window plus the edit, and the 5 s stall cutoff ends the attempt.
  *Superseded Oct 31:* this was first read as "the binding limit is planner
  throughput". The real cause of reaching the pit was missing map data: the
  sections south of the start were never delivered, so the bot went west at all.
  With delivery fixed, R1 takes the southern plateau and never meets the pit.
  Throughput is still low (it decides how bad partial routes are), but it was
  not the cause of the R1 gap.

| 13b: as 13 + planner trace only (R1, R4; old mod) | `20261031T002351544397Z` | fail/fail | — | — | 981/fail | — |

13b R4 rep 2 found a lane bug. Main search and prefetch share one worker lane,
and a submission replaces the running job. The main search (token 20) replaced a
prefetch; 5 ticks later discarding that prefetch called `lane.cancel()`, which
cancelled the main search instead. A superseded `BackgroundSearch` answered
"pending" forever (100 ticks, stall). It is likely the same cause as 13 R1 rep 2
and R4 rep 2 (no trace then). Fixed: `cancel(token)` only cancels the job if it
is still current, and a superseded `BackgroundSearch` resubmits once the lane is
idle (`tests/test_background_navigation.py`).

| 14: 13 + side-sand cut fix (mod `ed2c1c0b`), lane cancel/resubmit fix | `20261031T003318288661Z` | 1062/1023 | 947/fail | 952/1024 | 913/1034 | 1748/2180 |

Cohort 14 is 9/10. R2 rep 2 stalled after 4,119 ticks; **not yet diagnosed**.
Medians against Baritone: R1 1.8×, R3 1.06×, R4 1.2×, R5 2.3×.

### Oct 31: why R1 took twice Baritone's time

Baritone (run `20261029T205639210651Z`) stayed between y 67 and 73, broke and
placed nothing, and averaged ~0.23 blocks/tick: it went **south** from the start
onto a plateau (y 69–76) and then west to the goal. Between start and goal lies a
low area (x −64…−112, z −58…−100, y 42–60) with a sand pit and a flooded valley.
MCB went **west** into it every time (rep 1: the sand pit, 360 ticks for ~15
blocks; rep 2: 180 ticks swimming). On open ground MCB already matched Baritone's
pace (~0.22 blocks/tick); the whole gap was this detour plus a slow start.

Cause, found by replaying planning decisions on exactly the map the controller
had (`scripts/analysis/replay_delivered.py`):

1. **Section batches sent before the controller polled were lost for good.** The
   body sends each section once and resends it only when it changes. It starts
   at about tick 35, nearest first, so the first batches describe the start area.
   Python's first `/policy` read came 40–70 ticks later; those batches were
   overwritten unread (cohort 14: 9–13 of ~210 batches per attempt, all near the
   start). The controller began with a hole around itself, including the
   southern corridor.
2. **`natural.py` consumed one batch.** Its wait-for-running loop read `/policy`
   and discarded the result; that was the batch holding the sections just south
   of the start.
3. **The first plan ran before the first observation was admitted** (admission is
   asynchronous), reported "start is not known standable", and the fallback walked
   four blind single steps west. Baritone-style favouring of the previous route
   (×0.5) then kept the bot going west.

With every batch delivered, even a 1,000-node partial search from the start
picks the southern route (planned 578 ticks against Baritone's 581).

A second bug showed up on the way: a `downward` dig from an off-centre stance
left the body standing on the neighbour's edge over the hole for 99 ticks (stall).

| 15: R1 only, ×2 (Baritone 581) | Stamp | Rep 1 | Rep 2 |
|---|---|---|---|
| 15a: body requeues unread batches (mod `fe8a01ec`) | `20261031T010536703388Z` | 670 | fail (dig-down on hole edge) |
| 15b: + dig-down centring, controller waits for its start cell (mod `1b32c2bc`) | `20261031T010956578922Z` | 871 | 1166 |
| 15c: + `natural.py` waits on `/state` | `20261031T011406604483Z` | 978 | 656 |
| 15d: 15c with `awareness-sections-64-fast` (65,536 cells every 2 ticks) | `20261031T013251307370Z` | 1068 | 1045 |
| 15e: carry unread sections forward (mod `2228f374`), fast profile | `20261031T013837391936Z` | inv | inv |
| 15f: + event log records only scanned sections (mod `fdc9e533`), fast profile | `20261031T014112773939Z` | 1303 | 1315 |
| 15g: 15f, normal `awareness-sections-64` profile | `20261031T014527502610Z` | **608** | **621** |

- 15a–15c: requeueing unread sections rescans them at the normal budget, so the
  first read still delivered only ~21 nearest sections. 15d rep 1's first plan on
  exactly those 21 sections went west; two ticks later (23 more) it would have
  gone south. Rescanning cannot catch up with a controller that starts late.
- 15e: carrying all unread sections forward put 2.4–3 MB into every
  `policy_sample` event until the first read; `/events` timed out (bridge 4 s) and
  both attempts were invalid. Fixed in 15f: the event log records each section
  once, when scanned; `/policy` carries the full batch.
- 15d/15f: the fast profile (ingest every 2 ticks) took Python time from the
  planner (540–760 nodes per 250 ms job instead of ~1,000) and added 300+ ticks of
  stops. **Rejected**; the file stays only as evidence for these rows.
- 15g: both reps take Baritone's route (y 67–73.25, no blocks broken, no water),
  ~83% of ticks sprinting. 608/621 is within 5–7% of Baritone. Two reps only;
  the other routes have not been rerun on this code.

Corrections to earlier readings, from the same work:

- Cohort 13's "binding limit is planner throughput" for R1 is withdrawn (see the
  13 notes above).
- The saved `map.sqlite3` files were accurate. Sections missing from them were
  exactly the ones in batches Python never read.
- Do not judge what the planner knew from the body's `policy_sample` log alone;
  use the batches the controller read (`policy_observation` sample ticks).
  *Amended Oct 31 (later):* from mod `fdc9e533` on, unread sections are carried
  into the next read but logged only when scanned, so every batch scanned up to
  the latest read one was delivered. `replay_delivered.py` used the read-only rule
  and under-reported the map (cohort 16 R5 at tick 115: 130 sections instead of
  276). Fixed; `OLD_MOD=1` keeps the old rule for attempts before `fdc9e533`.

### Oct 31: cohort 14 R2 rep 2 (the undiagnosed stall)

At tick ~582 a 981-node partial route from (−102,65,−108) dropped into the basin
south of rep 1's high route (840 water ticks, y 52–56). From ~1,400 to ~3,900 it
cycled about 12 times: dig one cell east into a sand slope at x −80…−76 (one
`step` edit, 2 breaks), then a partial or 16-move "known goal route" walked ~14
blocks back west to (−90…−95, −62…−65), and the next partial came back to the
wall. Net progress was ~1 block per 290 ticks. The attempt ended `stalled` at
4,258 when a sand step near (−66,59,−50) sat 58 ticks before reporting
`native_edit_stance_changed` (sand refilling the cut is inferred, not verified).
The lane-cancel bug was not involved; the start fallback cost ~50 ticks. New
failure modes: partial-route ping-pong around a wall (no progress memory), and a
slow failure on a falling-block step. Not yet fixed.

| 16: current `main` (mod `6c3480fb`, same jars as 15g) | `20261031T022912020518Z` | 678/632 | 558/567 | 946/fail | 829/839 | fail/fail |

Cohort 16 is 7/10. Medians against Baritone: R1 1.13×, R2 1.06×, R4 1.04×;
R3 rep 1 1.01×. The source hash changed from `fdc9e533` only through timestamps
and verification files; the rebuilt jars were byte-identical.

- First `/policy` read came 3–31 ticks after `course_started`, following 9–11
  ticks later: the map memory and controller were built after the start. Fixed:
  `natural.py` builds them before `natural_start`.
- R3 rep 2 (stalled at 523): fell into deep water at (−91.5,54,−61.4) and stood
  on the bed, 2 blocks under the surface. The planner lifts a wet start to the
  surface stance (y 56); `RouteMotor`'s first-tick check called that start
  displaced (`route_start_displaced`, ~15 times in 47 ticks) and stopped, so the
  body never swam up. Fixed: a body in water in the start's column below a
  swim-surface start is not displaced; the swim edge holds jump.
- R5 (both reps; rep 1 ran to the tick limit, rep 2 stalled at 1,749): the
  planner chose a known 19-block drop off a 2-block ledge at (109,82,−7) into a
  lake (water to y 62 under the ledge and beyond). The body refused every such
  drop (`route_swept_geometry_unknown_or_unsafe`): in `RouteGeometry.swimSafe` a
  corner over water at the landing height ranked above the ledge it stood on, so
  the sweep floor dropped to y 63 and the clearance check hit the ledge. This is
  the "R5 lake-dive" refusal first seen in 12d. Fixed: support first, water only
  where there is none (`lakeDropChecks`). With the drops refused, rep 1 then
  ping-ponged between (108,86,−5) and (123,83,−4) for ~5,500 ticks; a 15-block
  oscillation never trips the 1-block stall rule. Same no-progress pattern as
  cohort 14 R2 rep 2.

| 17: 16 + controller built before start, sunk-start rule, ledge-over-lake sweep (mod `05e8948e`) | `20261031T025351963130Z` | 604/602 | 561/605 | 920/917 | 829/841 | fail/fail |

Cohort 17 is 8/10, no blocks broken or placed anywhere. Medians against Baritone:
R1 1.04×, R2 1.10×, R3 0.98×, R4 1.04×. First `/policy` read now comes on the
`course_started` tick in every attempt, and the bot follows a route 7–9 ticks in
(the first 250 ms search). R5 (both tick limit): takes the lake drop now, reaches
y 55.5 and stops improving 62 blocks from the goal.

17 R5 cause: from ~tick 1,000 both reps swam between (136,63,−60) and (124,63,−45)
for 5,000 ticks, ~24 cycles of ~145 ticks, every plan a 1,130–1,450-node partial
(no rejections, no edits). From (136,−60) the partial is a 101-move climb out of
the lake ending at (115,77,−44); from (124,−45) a 1,229-node search ends at the
lake's NE corner instead, and only ~30,000 nodes find the climb. The controller
followed 16 moves of each and then took the other start's partial. Two changes:
`previous_route` (Baritone's ×0.5 favouring) now holds the whole plan, not the
segment (offline this alone did not break the loop); and **a new partial whose
end is not at least 1 block closer to the goal than the current plan's end no
longer replaces that plan's unfollowed tail** (`SegmentedController._committed`;
a tail with a failed edge is dropped). Baritone follows the whole partial path.

| 18: 17 + whole-plan favouring, partial-plan commitment (Python only) | `20261031T032156392795Z` | 603/602 | 559/558 | 923/944 | 835/827 | 2697/fail |

Cohort 18 is 9/10; R1–R4 unchanged within noise. R5 rep 1 left the lake and
reached the goal (2,697 ticks, 3.2× Baritone; Baritone stays on y 75–116 and
never enters the water). R5 rep 2 (stalled at 469): the 19-block drop carried the
body to the lake bed at (108,53,−10), 10 below the surface; `surface_height`
scanned only 8 cells up, so every plan reported "start is not known standable".
Fixed: it follows the water column up to 40 cells and stops at anything that is
not water; the body's sunk-start rule allows the same 40 (mod `707114f3`).

| 19: 18 + deep-water surface scan (mod `707114f3`) | `20261031T033648199621Z` | 603/603 | 558/579 | 927/941 | 827/911 | 2974/fail |

Cohort 19 is 9/10. The commitment rule fired three times in R5 rep 2 (reason
"kept previous partial plan") and took the bot across the lake to its NW shore.
There, from tick ~2,110, the body bobbed at (106.3,63,−2.5) unable to climb a +1
bank at (105,64,−3), until the tick limit. Meanwhile the controller resubmitted
the identical 32-point route every ~6 ticks (`native-54` … `native-642`):
`splice_route` joined the active route to a prefetch from its unchanged end and
truncated to the 32-point cap, giving the same list. Each resubmission reset the
motor's 200-tick step deadline, so the edge was never given up. Fixed: a splice
that changes nothing is not submitted, and `route_step_deadline` marks the edge
failed like the other motor refusals. **Open:** why the body cannot climb out
onto that bank. Likely cause, not verified: the motor drops jump at the top of
each bob (`in_water` false, not grounded).

| 20: 19 + no-op splices skipped, step deadline marks the edge failed (Python only) | `20261031T035652070241Z` | 609/619 | 558/559 | 916/937 | 831/828 | 2896/3052 |

**Cohort 20 is 10/10**, with 6 blocks broken and none placed in total. Medians
against Baritone: R1 1.06×, R2 1.05×, R3 0.99×, R4 1.04×, R5 3.5×. R1–R4 are at
Baritone's pace. R5 is slow because MCB drops into the lake and swims ~2,000
ticks; Baritone stays on the ridge (y 75–116) and breaks 5 blocks. Two reps per
route, one seed: development evidence only.

### Oct 31 (late): fixes after cohort 20, not yet run live

Diagnoses by Sonnet subagents; each claim was re-checked against code and logs.

- **R5 lake (roadmap item 1).** `_diagonal_cost` priced a diagonal across a water
  surface as a land diagonal (5.04 ticks with sprint), not swimming (9.09 × √2 =
  12.86). The lake looked about 2.5× too cheap per block. Replaying the delivered
  map at the tick-295 prefetch with the patch kept the 1,229-node search on the
  ridge. At 30,000 nodes the ridge cost 671 against the old lake's 665. Fixed:
  water at either end now uses swim cost (Baritone `MovementDiagonal`). Only rep 1
  was checked.
- **Water exit onto a +1 bank (item 2).** The jump-at-the-top hypothesis is wrong.
  The bank top was 1.11 above the water surface (source at y 62, red sand at y 63).
  The wall-hop peaked at feet y 63.92 about 350 times, 0.08 short. The planner's
  surface stance is one block above Baritone's feet-in-water node, so its "+1
  exit" is a +2 for the body. It was the only such edge executed in the Oct 31
  attempts, and it never succeeded. Fixed: the planner's `water_exit_up` move is
  removed; the level exit remains. The body never gave up because swim's stall
  counter used 3D movement (bobbing counted as progress) and reset on airborne
  ticks. Fixed: horizontal movement only, 70 ticks.
- **Long oscillation (item 3).** Added `goal_no_progress`: the attempt ends when the
  best straight-line goal distance has not improved by 1 block for 2,400 ticks.
  The first proposal of 1,200 ticks would have ended both successful cohort-20 R5
  runs (longest gaps 1,303 and 1,385). The stuck cohort-19 run's gap was 3,934. The
  rule is not tied to `stall_seconds` (0 does not turn it off).
- **Sand step (item 4): mechanism not confirmed.** Cohort 14 R2 rep 2 step-17
  broke each cell once. About 25 ticks went on the falling-block settle wait while
  the sand column fell, then the step failed with `native_edit_stance_changed`. A
  proposed refill fail-fast would not have fired, so it was not kept. A likelier
  fix is planner-side, from memory of Baritone, not verified: add the mining cost
  of falling blocks stacked above a cut, as `getMiningDurationTicks(includeFalling)`
  does.

| 21: 20 + water diagonals priced as swimming, +1 water exit removed, swim stall horizontal, `goal_no_progress` (mod `a1566980`) | `20261031T143033425943Z` | 605/604 | 559/569 | 834/675 | 942/921 | 1279/fail |

**Cohort 21 is 9/10.** R5 rep 1 stayed on the ridge: 1,279 ticks (1.5× Baritone,
was 2,896). R3 is faster than Baritone. Two findings:

- **R5 rep 2 stalled underwater** (tick 535, y 53). After a step's edits the body
  kept the step motor's pitch, 84° down, and dropped off the ridge into the lake.
  The route motor never set pitch. In the swimming pose, vertical motion follows
  the look vector, so a sprint-swim drove it onto the lake bed against held jump.
  The swim stall fired (`route_no_progress`, 72 ticks), but the 5 s body stall
  ended the attempt first. Not caused by the cohort-21 changes. Fixed: route
  following levels the pitch (`RouteMotor.levelPitch`, at most 45° a tick).
- **R4 is ~13% slower, not from the cohort-21 changes.** No swimming on R4. A
  replan at tick 672 was cut off by the 250 ms budget (589 expansions) and
  committed to a west detour. Replaying the delivered map: cap 600 gives the
  detour, cap 1,229 finds cohort 20's direct route; turning the water-diagonal
  rule off changes neither. This is planner throughput (roadmap items 6–9). The
  final approach also stopped ~70 ticks on "start is not known standable".

Also applied before cohort 22: falling blocks over a cut now add 17 settle ticks
each (`FALL_SETTLE_TICKS`). The planner already priced their breaking (Baritone
`includeFalling`) but not the wait. In an offline replay of cohort 14 R2 rep 2
step-17, the planner then walks along y 59 instead of cutting under sand.

| 22: 21 + route following levels pitch, falling-block settle cost (mod `ad843232`) | `20261031T144601883497Z` | 602/607 | 558/558 | 807/824 | 829/838 | 1430/1334 |

**Cohort 22 is 10/10**: 5 blocks broken and 3 placed in total. Medians against
Baritone: R1 1.04×, R2 1.05×, R3 0.87×, R4 1.04×, R5 1.63×. R5 stays on the ridge
in both reps. R4 is back to cohort 20's time. Cohort 21's R4 detour came from a
budget-cut search, so it may recur. Two reps on one seed: development evidence
only.

| 23: 22 + wall-cut partials below 1,500 nodes keep searching (≤1 s; Python only) | `20261031T145932251116Z` | 605/605 | 562/562 | 676/682 | 839/837 | 1283/925 |

**Cohort 23 is 10/10.** Medians against Baritone: R1 1.04×, R2 1.06×, R3 0.73×,
R4 1.05×, R5 1.30×. A read-only subagent ran offline replays during this cohort,
so CPU contention may have shortened some live searches.

Diagnoses between cohorts 22 and 24:

- **R5's remaining gap is start-zone dithering** (cohort 22). Both reps lost
  ~500 ticks in the first ~500. Budget-cut partials (1,000–2,200 nodes) sent the
  body west to a pocket at x 92–97 and back. In rep 1, one partial committed an
  unseen 15-block drop into water (y 56), costing ~300 ticks. Replays at a cap of
  8,000 picked the eastern route. The remaining time matches Baritone. Baritone's
  run has no position trace, so the path comparison is by summary only.
- **The R4 "unstandable start" stop was 12 airborne ticks.** The start height
  was read mid-hop; each search failed instantly until landing. The ~60 ticks
  after that were a pillar and a break the walls needed. No change made.
- **Swept-unsafe stop in mid-air** (cohort 21 R4, both reps at the same
  staircase). An ascent hop held forward for ~8 ticks and landed 0.25 past the
  point's centre. The next sweep clipped a wall while airborne, and only the
  grounded recentre could recover. Fixed for cohort 24: airborne, the motor coasts
  and retries after landing (bounded by the 200-tick step deadline).

| 24: 23 + node floor 3,000, airborne blocked sweep coasts (mod `51e5089f`) | `20261031T151251308104Z` | 618/612 | 567/578 | 701/700 | 866/865 | 921/909 |

**Cohort 24 is 10/10.** Medians against Baritone: R1 1.06×, R2 1.08×, R3 0.75×,
R4 1.08×, R5 1.08×. R5 no longer dithers at the start. R1–R4 are 1–4% slower than
cohort 23, probably from the longer wait on fresh searches. The ten-attempt total
is 7,337 ticks (cohort 23: 7,576). No other load ran during this cohort.

| 25: 24 + node floor 3,000 only while the body has route left (prefetch splice), 1,500 when stopped (Python only; mod `51e5089f`) | `20261031T200524257733Z` | 611/607 | 563/563 | 703/772 | 862/839 | 899/902 |

**Cohort 25 is 10/10.** Medians against Baritone: R1 1.05×, R2 1.06×, R3 0.79×,
R4 1.06×, R5 1.06×. R1, R2, R4 and R5 are 6–15 ticks faster than cohort 24, and
R5 keeps its cohort-24 gain. Total 7,321 ticks (cohort 24: 7,337). R3 rep 2 is
the one slower attempt (772; rep 1 703). Its route had already split from rep 1's
by tick ~440 (a y 65 line against y 66). At tick 461 the motor stopped while
grounded with `route_swept_geometry_unknown_or_unsafe`, and the stopped search
committed a 1,628-node partial. Replaying that decision on the delivered map, caps
of 3,000 and 8,000 finish at 1,723 nodes on the same route, so the lower stopped
floor did not cost R3 here. Two reps on one seed: development evidence only.

**R3 rep 2 diagnosis (after cohort 25).** The reps' routes first differ at
native-5 (~100 ticks in): partials planned from slightly different body positions
send rep 2 west to x=-84. The 69-tick gap is ~17 from that detour, ~13 from the
stop at tick 461, and ~39 from the slower eastern line (x≈-59..-61) planned after
the stop. The stop was a motor bug. The diagonal (-63,65,-61)→(-62,65,-62) has sand
at (-62,65,-61) and a hole at (-63,64,-62), so the motor edged round the sand via a
bend waypoint 0.31 off the column (0.01 clearance for the 0.3 hitbox).
`followBend` turned at most 45° per tick, treated ≤8° as aligned, and swept along
the body's yaw rather than the bearing. After 0.03 blocks of coasting, the
5–8° left over clipped the sand. A Python port of the sweep on the rebuilt terrain
fails on the body's yaw and passes on the exact bearing at every pose.
*Fixed* (mod `2eed1251`): `followBend` and `recentre` steer instantly and sweep on
the exact bearing, as the main path already did. `RouteMotorTest` has a regression
check that fails on the old code.

| 26: 25 + instant-heading bend and recentre (mod `2eed1251`) | `20261031T214556225885Z` | 608/606 | 563/564 | 732/735 | 836/830 | 924/904 |

**Cohort 26 is 10/10, with no motor stops in any attempt.** Medians against
Baritone: R1 1.04×, R2 1.06×, R3 0.78×, R4 1.04×, R5 1.08×. Total 7,302 ticks
(cohort 25: 7,321). Both R3 reps took the western line that cohort 25 rep 2 took,
without the stop: 732/735 against 772. The rest of the R3 spread (cohort 25 rep 1
took 703 on the other line) is route choice from partial plans, not a defect.
Two reps on one seed: development evidence only.

| 27: 26 + grid float lookups, swim land early-outs, whole edit chains queued (Python only; mod `2eed1251`) | `20261101T003648618347Z` | 615/607 | 576/563 | 733/746 | 855/860 | 917/1271 |

**Cohort 27 is 10/10.** R1–R4 and R5 rep 1 are within a few ticks of cohort 26.
Edits barely ran (one step per R5 rep), so the edit-queue change was hardly
exercised. R5 rep 2 (1,271) dropped into the lake: its first commitment was a
~1,500-node partial west to (113,86,−3), and the prefetch from there landed in
the lake at (108,63,−15). Replaying the start decision on each rep's delivered
map: 1,500 nodes go west in both reps, 3,000 and 8,000 nodes go south along the
ridge in both. Rep 1 had simply searched further before committing. Fixed for
cohort 28: the first search of an attempt uses the full 3,000-node floor.

| 28: 27 + first search of an attempt held to 3,000 nodes, D7/profile/interaction code inert by default (Python only; mod `2eed1251`) | `20261101T005033322463Z` | 623/619 | 576/568 | 736/738 | 841/848 | 905/940 |

**Cohort 28 is 10/10.** Neither R5 rep went into the lake. Medians against
Baritone: R1 1.07×, R2 1.08×, R3 0.79×, R4 1.05×, R5 1.09×. The start floor costs
what the replay predicted: R1 and R2 are 5–15 ticks slower than cohort 26 (the
extra ~0.5 s of start search). Total 7,394 ticks (cohort 26: 7,302; cohort 27,
with the lake: 7,783). Accepted: a fixed ~10 ticks against a 370-tick detour; the
compiled core (item 8) should remove most of the wait.

Cohort 11 is the first 10/10. Median ratio to Baritone: R1 2.5×, R2 2.2×, R3
1.5×, R4 1.9×, R5 4.1× (R5 still places ~62 blocks per attempt).

Failure causes, in order found: route loops from splicing stale prefetches (1);
frontier ping-pong on not-yet-refreshed cells (1–2); weighted A* plus cheap edits
tunnelling and falling into ravines, then digging out (2–4); paths wandering after
optimistic exits (4); known-only planning building long bridges it could not see
around (5–7); a 5 s stall during slow edge placements (7: 39 ticks each in the body,
now 7–8); 50-block causeways and 30-step dig-outs (8); a cave exit under the goal (9,
R3 rep 1); chained segments into a cave mouth, then deeper cave exits whose rise
was priced as a jump instead of a dig-out (10, R3 rep 2; fixed by `_buried` in
`route_search.py`, cohort 11).
