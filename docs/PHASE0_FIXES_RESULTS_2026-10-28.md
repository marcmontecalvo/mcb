# Phase 0 fixes: results

October 28, 2026. Results for [PHASE0_FIX_PLAN_2026-10-28.md](PHASE0_FIX_PLAN_2026-10-28.md)
(compact pathing grid, pipelined control loop). Development-only evidence: no gate,
no scored admission. Mod source unchanged (`df5aec8e…`). Tracked summaries for every
run below, including failed ones: [evidence/2026-10-28-phase0-fixes](evidence/2026-10-28-phase0-fixes).

## Outcome

- **Calm natural routes: 3/5 reached the goal, against 0/5 in Phase 0.** Same
  routes, profiles and matched awareness. R1, R2 and R4 arrive in about 2.4–2.7×
  Baritone's time. R3 was invalidated by late actions, and R5 still stalls.
- **Map gaps are gone:** zero map-gap invalids after Fix 1, against 4 of 10 MCB
  attempts in Phase 0. The whole 64-block, full-height view is retained.
- **Late actions are now the main harness problem.** Polling every tick at
  64-block awareness produces 3–10 late commands per natural attempt. One attempt
  exceeded the discard limit. See [Open issues](#open-issues).
- **The pipelined loop doubles decisions per tick on flat ground** (0.31 → 0.75)
  but gains only 3–5 % in ticks-to-goal. Speed is bounded by stops at corners and
  steps (Phase 5), not by control cadence.

## Fix 1: compact pathing grid

**What changed.**
- `pathing_grid.py` stores state ids and provenance ids in arrays per 16³ section.
  Each palette state carries flags derived from the existing predicates.
- `GridView` is a read-only mapping:
  - It returns shared geometry facts.
  - Unknown cells, evicted sections and non-integral coordinates all read as
    absent, exactly like a missing dict key.
  - `record(p)` gives position and provenance.
- `SpatialMemory` no longer has a cell cap. It never evicts inside the awareness
  volume; when over its byte budget, it ages out whole sections outside the
  volume, farthest first.
- Persistence coalesces per section (latest cell wins). The backlog is bounded by
  section count, and the writer streams one section at a time through
  `merge_rows`, which keeps the same stored format.
- Planning snapshots are copy-on-write section references.
- Automatic recall decodes only sections that can add history: sections from
  earlier processes, and sections this memory evicted.

**Bench** (`scripts/bench-pathing-grid.py`, synthetic 64-radius full-height view,
6.39M cells in 8192-cell samples; [output](evidence/2026-10-28-phase0-fixes/bench-pathing-grid.json)):

| | Pre-grid `SpatialMemory` | Grid |
|---|---|---|
| Cells retained | 20,000 | 6,390,144 (whole view) |
| Outcome | gap after 9 samples ("persistence byte budget") | no gap, 0 evictions |
| Admission per sample, p50 / p95 | 36 / 57 ms (before failing) | 37 / 59 ms |
| Memory (tracemalloc) | n/a | 65 MB after writer drain (48 MB grid arrays); 97 MB while 1,296 sections await the writer |
| Writer throughput | n/a | 159k cells/s against about 33k/s live inflow |
| A* expansions/s | 34.6k on a dict of the same surface band | 27.1k (0.78×) |

**Live** (lockstep control, Phase 0 profiles, matched awareness):

| Run | Attempts | Result |
|---|---|---|
| `111020341814Z` | calm R2–R5 | R2, R3 and R4 invalid with "map observation gap"; R5 timed out 96 blocks short. Cause: automatic recall decoded about 32 of the bot's own sections (about 250 ms) on each section crossing, overflowing admission. Fixed by decoding only history sections. |
| `112017555040Z` | calm R2–R5 | **R2 reached (1530 ticks), R3 reached (1879), R4 reached (2631, 2 placements)**; R5 timed out 47 short (planner: background search budget) |
| `113205885901Z` | night R5 | invalid: the bot was killed at tick 611. The mod then rejected the in-flight command with `body_not_registered` before the death event arrived. This is the same race as Phase 0 R5 night. |
| `113258089658Z` | calm R1 | invalid at tick 1410, 15 blocks short, after 4 late actions (lockstep limit 3). No walkway was built: 0 placements, against 8 in Phase 0. |

R1's walkway and R4's stall do not reappear: R1 used no placements, and R4 reached
the goal. That fits the Phase 0 suspicion that eviction caused both.

## Fix 2: pipelined control loop

**What changed.**
- `control_loop.py` now holds the motor loop. `model_execution.py` re-exports
  `drive` for existing callers, and action ids are `action-N`.
- `lockstep-three-tick-v1` is unchanged; a review compared it line by line.
- `dev-pipelined-v1` works as follows:
  - **Cadence:** one decision per new observation tick.
  - **Commands:** at most 3 unacknowledged. Each applies at observation tick + 3,
    lasts 5 ticks, then expires.
  - **Orientation:** the controller sees yaw and pitch as they will be once
    unapplied turns land.
  - **Late or stale acks:** discarded and recorded, never retried. The limit is
    3 × in-flight.
  - **Barrier:** after any jump or interaction, and whenever the controller is not
    `pipeline_safe` (terrain, pillar, gap, committed steps, ascents, swimming or
    air recovery), it waits until the result is observable.
  - **Polling:** `/policy` is fetched once per new tick.
  - **Body death:** a rejection that reports the body or episode gone returns the
    native terminal outcome instead of a controller error.
- Natural runs take `--control-profile`, which applies to `segmented_goal` only.
  It is recorded as `control_profiles`, and each attempt records its loop summary.

**Fixture cohorts.**
- Setup: 10 variants each, fresh server per cohort, background planning,
  diagonals, sprint, shapes, drop ≤3, gap ≤2.
- In the table, "flat" is the 40–90-block `prefetch` fixture.

| Family | Lockstep: pass, median ticks, decisions/tick | Pipelined: pass, median ticks, decisions/tick | Pipelined late discards |
|---|---|---|---|
| flat | 10/10, 317.5, 0.31 | 10/10, 306.5, 0.75 | 2 (attempts valid) |
| slab | 10/10, 182, 0.29 | 10/10, 175, 0.36 | 0 |
| stairs | 10/10, 239, 0.31 | 10/10, 226, 0.50 | 0 |
| drop | 10/10, 139, 0.29 | 10/10, 144.5, 0.40 | 0 |
| mixed | 7/8, 268, 0.31 | 7/8, 271, 0.34 | 0 |

Mixed variant 7 fails in both profiles: it stops two blocks below the goal, with
"no terrain work or walking progress within horizon". This is **not caused by
these fixes**:
- It fails with the original Oct 27 flags (`120433001847Z`).
- It fails on an unmodified checkout of `a444508` (`120921844943Z`).

The runner stops at the first failure, so variants 8–9 did not run.

**Natural, calm R1–R5, MCB pipelined** (`121257083859Z`):

| Route | Result | Ticks | Baritone (Phase 0) | Late discards | Decisions/tick |
|---|---|---|---|---|---|
| R1 | reached | 1392 | 577 | 6 | 0.65 |
| R2 | reached | 1361 | 549 | 6 | 0.63 |
| R3 | invalid: 10 late actions (limit 9), 33 blocks short at 1438 | — | 938 | 10 | 0.65 |
| R4 | reached (2 placements) | 2196 | 800 | 7 | 0.56 |
| R5 | timeout, 47 short | 6000 | 955 | 3 | 0.85 |

R2 (1361 against 1530) and R4 (2196 against 2631) are faster than under lockstep
earlier today.

## Acceptance against the plan

| Item | Status |
|---|---|
| Fix 1 unit: parity with `standable`/`collision_geometry`; unknown never passable | Met (`tests/test_pathing_grid.py`). Reviewed: no violation. |
| Fix 1 bench: whole view retained | Met |
| Fix 1 bench: memory < 64 MB | About 65 MB after drain; about 97 MB during writer backlog |
| Fix 1 bench: admission p95 well under one control period | p95 59 ms: under the 250 ms sample interval and the lockstep period, not under one tick |
| Fix 1 live: zero map-gap invalids | Met after the recall fix |
| Fix 2 unit/fake bridge: in-flight bound, supersession, expiry, cancel releases, nothing applied after cancel | Met (`tests/test_control_loop.py`). Reviewed; both HIGH findings fixed. |
| Fix 2 live fixtures pass with zero invalid/late/stale | 48/48 non-mixed and mixed 7/8 (pre-existing failure), zero invalid. **Not met for late:** 2 late discards in the flat cohort. |
| Fix 2 natural rerun | 3/5 reached; one invalid on late actions |

## Open issues

1. **Late actions at 64-block awareness.**
   - **Why:** each pipelined tick makes about 3 HTTP calls and decodes a large
     `/policy` payload, while admission (about 40 ms per sample) and planning
     share the GIL. Commands then miss the three-tick lead.
   - **Effect:** a late pipelined command only skips one update, since newer
     commands supersede it.
   - **Options:**
     - Count *consecutive* late commands instead of the per-attempt total.
     - Or measure where the lead is lost and push observations from the mod
       (plan item 4).
   - **Constraint:** either change is a new configuration and needs a new cohort,
     not a re-scoring of `121257083859Z`.
2. **R5 calm stalls about 47 blocks short:** "background search budget",
   incomplete search. This is planner work (Phases 1–2).
3. **Night death race:** lockstep (legacy) still reports a death that arrives one
   tick after the command rejection as a controller error. Pipelined handles it.
4. **Mixed variant 7 regression:** present at `a444508`. Bisect between the Oct 27
   10/10 cohort and `a444508`.

## Reproduce

```powershell
$d='docs/evidence/2026-10-28-phase0'
rtk proxy uv run --locked python scripts/bench-pathing-grid.py [--trace-memory]
rtk proxy uv run --locked python -m mclab.cli natural run --seed 1337 --routes results/natural/routes-1337.json --entrant segmented_goal --condition calm --repetitions 1 --view-distance 4 --awareness-profile $d/awareness-chunk-64.json --movement-profile $d/movement-passing-families.json --terrain-profile $d/terrain-passing-families.json --control-profile dev-pipelined-v1
rtk proxy uv run --locked python scripts/check-hill-navigation.py --kind prefetch --variants 10 --background-planning --diagonals --sprint --collision-shapes --max-drop 3 --max-gap 2 --control-profile dev-pipelined-v1
```
