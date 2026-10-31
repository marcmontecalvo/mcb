# Step C development progress — planner core (D11)

Development evidence only, not acceptance or qualification. The governing plan is
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) §0 step C.
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

Offline tooling (scratch, not committed): a closed-loop simulator replays the
planner on recorded maps. It is useful for catching loops and gross regressions
but cannot rank variants: cells the live bot never saw are unknowable.

## Awareness finding (D8 parity)

The `chunk` preset with `-1` (full height) scans 2,048 cells per 5 ticks in an
expanding cube that restarts each time the body moves 16 blocks. Recorded sweeps
reached ~100k of 6.4M cells, so MCB actually knew ~23 blocks around itself, not
64 (R5 stall: nothing known past z=-92 or above y=96). Baritone receives whole
loaded chunks. `config/stepc/awareness-chunk-64-fast.json` (8,192 cells/update,
±48 vertical) reaches ~30–43 blocks before each restart. Real parity needs a
section-based scanner in `mcb-body` (bulk chunk-section reads); not done.

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
  **The binding limit is planner throughput**, not a rule: Baritone covers 25–30k
  nodes inside its 500 ms primary timeout.

| 13b: as 13 + planner trace only (R1, R4; old mod) | `20261031T002351544397Z` | fail/fail | — | — | 981/fail | — |

13b R4 rep 2 found a lane bug. Main search and prefetch share one worker lane,
and a submission replaces the running job. The main search (token 20) replaced a
prefetch; 5 ticks later discarding that prefetch called `lane.cancel()`, which
cancelled the main search instead. A superseded `BackgroundSearch` answered
"pending" forever (100 ticks, stall). It is likely the same cause as 13 R1 rep 2
and R4 rep 2 (no trace then). Fixed: `cancel(token)` only cancels the job if it
is still current, and a superseded `BackgroundSearch` resubmits once the lane is
idle (`tests/test_background_navigation.py`).

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
