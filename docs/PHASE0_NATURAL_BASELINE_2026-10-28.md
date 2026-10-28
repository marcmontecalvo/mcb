# Phase 0: natural-terrain baseline at matched awareness

Run October 28, 2026 for Phase 0 of
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).
Development-only evidence; no gate, no scored admission. No planner changes.

## Setup

- **World and routes:** seed 1337 snapshot, the five routes in
  `results/natural/routes-1337.json` (117–141 blocks), restored and server
  restarted before every attempt. Goal: within 3 blocks horizontally and 4
  vertically, within 6000 ticks.
- **Kit (all):** iron pickaxe, shovel and axe; 64 cobblestone; 64 dirt.
- **Conditions:** calm (noon, no mobs) and night (midnight, mob spawning on).
  One attempt per route, condition and entrant.
- **Matched awareness (D8):**
  - Server `view-distance=4` (64 blocks) for the run; the lab value (6) is
    restored afterwards. Baritone's client render distance is 16.
  - MCB awareness `chunk`, horizontal radius 64, full height, entity radius 64.
  - **Baritone's on-disk chunk cache is deleted before each attempt.** Before
    this, the cache persisted across attempts and was shared with the flat lab
    world, giving Baritone remembered terrain beyond its view. The October 26
    comparison ran with that cache.
  - All three are recorded in each run manifest.
- **Segmented MCB profiles** (every family with a passing enabled ten-case
  cohort; swimming off, no cohort): movement
  [`movement-passing-families.json`](evidence/2026-10-28-phase0/movement-passing-families.json)
  (diagonals, sprint, slabs/stairs, drop ≤3, gap ≤2); terrain
  [`terrain-passing-families.json`](evidence/2026-10-28-phase0/terrain-passing-families.json)
  (break, place, bridges, staircases with stacks ≤3, pillars ≤3, current
  budgets of 8 breaks / 8 places).
- **Sources:** native mod `df5aec8e…` (includes the game-rule fix below); Python
  hashes in the summary manifest.

## Results

Run `results/natural/20261028T032921621937Z` (tracked copy:
[summary](evidence/2026-10-28-phase0/20261028T032921621937Z-summary.json)).
Times in ticks; "short" is the closest the entrant got to the goal, in blocks.

| Route | Calm: Baritone | Calm: MCB segmented | Calm: V8 goal | Night: Baritone | Night: MCB segmented | Night: V8 goal |
|---|---|---|---|---|---|---|
| R1 (136) | 577 | timeout, 121 short | **3361** | 581 | killed at 472 | killed at 1018 |
| R2 (117) | 549 | invalid: map gap | **1482** | 546 | killed at 2501 | killed at 654 |
| R3 (141) | 938 | invalid: late action, 102 short | timeout, 65 short | 600 (3.5 dmg) | killed at 547 | invalid: late action |
| R4 (133) | 800 | timeout, 55 short | timeout (see note) | 798 | killed at 393 | killed at 946 |
| R5 (126) | 955 | invalid: map gap | invalid: late action | 1033 (17 dmg) | invalid: episode not running | killed at 1589 |

- **Baritone:** 10/10. Broke 0–10 blocks, placed 0–2. Without its cache, R3 and
  R5 took 938/955 ticks against 601/853 on October 26.
- **MCB segmented:** 0/10, of which 4 invalid. Never broke a block.
- **V8 goal:** 2/10 (R1, R2 calm). On October 26, with ±20-block memory, it was
  0/10. V8 R4 calm came within 0 blocks horizontally but never satisfied the
  vertical tolerance before the tick limit.
- Night: every MCB and V8 attempt that stayed valid died, after 20–125 seconds.

## Failure classification (MCB)

Facts per attempt come from `scripts/classify-natural-attempts.py`
([output](evidence/2026-10-28-phase0/20261028T032921621937Z-classification.jsonl));
the category is a reviewer's call from those facts.

| Attempt | Progress | Category | Evidence |
|---|---|---|---|
| R1 calm | 16/136 | **edit-vs-detour misjudgment** | Spent all 8 placements in the first 16 blocks on a raised walkway (four level steps each on a placed support) plus a 2-block stair; then 4923 ticks with no progress, mostly `terrain_incomplete` / "background search pending" |
| R2 calm | — | **other: persistence backlog** | `Map observation gap; start a new generation and rescan`, consistent with the SQLite persistence queue overflowing at 64-block sampling |
| R3 calm | 39/141 | **other: late action** (edit-vs-detour before it) | 7 placements, 2291 ticks without progress, then a fourth late action |
| R4 calm | 78/133 | **planner blind spot / stall** | Walking only, no edits; last 4465 ticks split between `planning` (762), `awaiting_fresh_geometry` (728) and `following` (454) |
| R5 calm | — | **other: persistence backlog** | Same map-gap error |
| R1 night | 20/136 | **survival** | Killed at 472 ticks, 3 hostiles within 16 blocks |
| R2 night | 33/117 | **survival** (budget also exhausted) | 8 placements, then killed at 2501 ticks |
| R3 night | 24/141 | **survival** | Killed at 547 ticks |
| R4 night | 24/133 | **survival** | Killed at 393 ticks |
| R5 night | — | **other: harness** | `episode_not_running` request rejection; 4 failed terrain steps before it |

Totals: survival 4, other/harness 4, edit-vs-detour 1 (plus contributing in R2 night
and R3 calm), planner blind spot/stall 1, motor stall 0.

## What this means for Phases 1–5

1. **The 64-block awareness pipeline is not yet fit for measurement.** 4 of 10 MCB
   attempts (and 2 V8) are invalid for infrastructure reasons that only appear at this
   radius. This comes before Phase 1, or every later re-run inherits it:
   - The live map is capped at **20,000 cells** (`SpatialMemory(active_cell_limit=20000)`)
     and evicts least-recently-observed cells first. The byte budget (128 MB) would
     allow about four times that. A 64-block full-height view does not fit, so the
     planner sees only the most recent scans. This probably feeds both the R1
     walkway (support placed over evicted ground) and the R4 stall.
   - The persistence queue overflows ("map gap").
   - Late actions still occur occasionally after the recorder fix below.
2. **Edit-vs-detour (Phase 1, then 4).** The current flat penalties let 4–8 placements
   on gentle terrain look cheaper than walking around. Once budgets run out, the
   fallback leaves the bot stuck rather than walking a detour.
3. **Survival (Phase 6).** Every valid night attempt died quickly. MCB's slow progress
   (16–33 blocks before death) exposes it far longer than Baritone. Some of this
   falls to Phase 5 speed work; the rest needs D7's hazard terms.
4. **Blind spot / stall (Phase 2, 5).** R4 shows the walking-only stall. Re-test it after
   the hot-cell cap is resolved, since eviction may be the real cause.

## Harness changes made for Phase 0

- `natural run --view-distance N` sets the server view distance for the run and
  records it (`server_view_distance_chunks`).
- Baritone's chunk cache is deleted before each Baritone attempt.
- **Native fix:** `NaturalRun.prepareWorld` aborted every attempt whose snapshot
  already held the opposite `spawn_mobs` value (the flip-then-set command failed
  on the flip). The flip may now fail; only the final set must succeed.
- **Recorder:** natural runs no longer write expanded-cell `policy_map_full`
  snapshots. They re-serialized the whole accumulated map every 20th observation
  inside the control loop, reaching 29 MB and causing every late action in
  `20261028T025756029115Z`. The compact wire payload is still recorded with every
  observation, so attempts remain replayable. Scored legacy runners are unchanged.

## Evidence ledger (all retained)

| Run | Scope | Outcome |
|---|---|---|
| `20261028T025411194980Z` | full comparison, mod `ca0d2c60` | Stopped after 2 attempts; both invalid from the `spawn_mobs` bug |
| `20261028T025756029115Z` | full comparison, mod `df5aec8e`, expanded-cell recorder on | Baritone 10/10; MCB 0/10 and V8 0/10, all invalid on late actions from recorder stalls |
| `20261028T032344545463Z` | MCB R1 calm smoke, recorder off | Valid; timeout, 8 placements, 20,000-cell map |
| `20261028T032921621937Z` | full comparison, recorder off | The table above |

Tracked summaries are in [evidence/2026-10-28-phase0](evidence/2026-10-28-phase0).
Raw event logs (≈200–250 MB per MCB attempt) and map archives stay local under
`results/natural/`.

## Reproduce

```powershell
$d='docs/evidence/2026-10-28-phase0'
rtk proxy uv run --locked python -m mclab.cli natural run --seed 1337 --routes results/natural/routes-1337.json --entrant comparison --condition both --repetitions 1 --view-distance 4 --awareness-profile $d/awareness-chunk-64.json --movement-profile $d/movement-passing-families.json --terrain-profile $d/terrain-passing-families.json
rtk proxy uv run --locked python scripts/classify-natural-attempts.py results/natural/<stamp>
```
