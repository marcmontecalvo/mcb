# Phase 0 fixes: compact pathing grid and pipelined control loop

> **Status (Oct 28):** both fixes are implemented and the reruns are done. Results
> and the remaining late-action issue are in
> [PHASE0_FIXES_RESULTS_2026-10-28.md](PHASE0_FIXES_RESULTS_2026-10-28.md).

October 28, 2026. Do this before Phase 1 of
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).
Evidence that motivates it: [PHASE0_NATURAL_BASELINE_2026-10-28.md](PHASE0_NATURAL_BASELINE_2026-10-28.md).
Development paths only; legacy scored profiles, sensors and timing stay frozen.

## Fix 1 — Compact pathing grid (replaces per-cell dict hot map for planning)

**Problem.** `SpatialMemory` keeps one Python dict per cell in an `OrderedDict`
(`active_cell_limit=20000`, oldest evicted) and queues those dicts to SQLite.
A 64-block view is ~16.6k columns (~6.4M cells full height), so the planner only
sees recent scans; the persistence queue overflows ("map gap"); copying/serializing
dicts adds control-loop latency. Raising the cap does not fix this.

**Change.**
1. New `pathing_grid.py`: per 16³ section, a `numpy.uint8` array of flags,
   keyed by section coordinates. Flags (bit layout is yours): unknown (default),
   air/passable, full solid, partial shape (slab/stair etc.), water, lava/hazard,
   breakable-with-kit, falling block, protected. Keep palette/block ID per section
   only where executors need it (exact block, shape boxes).
2. Admission writes observations straight into the grid (vectorized per section
   where possible). Nothing is evicted inside the active awareness volume; distant
   sections age out to the archive by section, never cell by cell.
3. `RouteSearch`, `TerrainRouteSearch`, gap/pillar/bridge preparation and motor
   revalidation read flags through one accessor. Full cell detail is fetched only
   for cells an edge actually edits or needs shape boxes for.
4. Persistence writes compressed section deltas via the existing section codec,
   batched by section and coalesced in the queue (latest delta per section wins),
   so backlog is bounded by section count, not observation count. A true gap still
   fails closed.
5. Planning snapshots become section-array references with copy-on-write on
   admission, not dict copies.

**Acceptance.**
- Unit: flag classification parity with current `standable`/`collision_geometry`
  on existing fixtures; all existing tests pass; no unknown cell ever reads as
  passable to the motor.
- Bench: admit a 64-radius full-height chunk-mode snapshot; report admission ms,
  memory MB, A* nodes/sec vs current. Target: whole view retained, admission p95
  well under one control period, memory < 64 MB for the view.
- Live: rerun the 4 invalid Phase 0 attempts + R4 calm with the same profiles.
  Zero map-gap invalids. Record whether R1/R4 behavior changes.

## Fix 2 — Pipelined control loop (development profile)

**Problem.** `model_execution.drive()` is lockstep: observe → decide → send
(apply at tick+3, duration 5) → wait for `applied` ack → observe again. MCB can
change controls only every ~3–4 ticks; Baritone acts every tick. This costs speed
and precision (overshoot, `awaiting_fresh_geometry`, slow progress, night deaths).

**Change.**
1. Rename `model_execution.py` → `control_loop.py` (keep a shim import for legacy
   callers) and action IDs `model-N` → `action-N`. Natural runs never call a model;
   the names currently suggest they do.
2. Add a development control profile (e.g. `dev-pipelined-v1`, recorded in the
   manifest) that does not wait for the previous `applied` ack: decide on every
   new observation tick, keep at most N (start 3) commands in flight, each with
   duration/expiry still bounded. Newer commands supersede older ones for the same
   ticks; stale or out-of-order acks are discarded, not retried.
3. Keep the motor's fresh-geometry checks per decision. Keep the legacy lockstep
   profile unchanged for scored runs.
4. Consider observation push from the mod (per tick or per 2 ticks) if polling
   becomes the limit; measure first.

**Acceptance.**
- Unit/fake-bridge: in-flight bound, supersession, expiry, cancel releases
  controls, no stale command applied after cancel.
- Live fixtures: flat, slab/stair, drop and mixed-hill cohorts (10 each) pass with
  zero invalid/late/stale attempts; report decisions per tick and ticks-to-goal
  vs the lockstep runs.
- Natural: rerun Phase 0 calm R1–R5 for MCB only; report progress and time.

## Order and reporting

Fix 1, then Fix 2, then the Phase 0 reruns above. Append a result note to the
direction doc. Retain all failed cohorts. Then start Phase 1.
