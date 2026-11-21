# MCB — Architecture Contracts & Detailed Working Rules (On-Demand)

Read this before modifying route/terrain planners, motors, companion runners, or opening large files (`companion_body.py`, `gather_runner.py`, `navigation_segmented.py`, `RoutePlanner.java`).

## Architecture Contracts

- **Java plans, Python asks.** Route and terrain planning run in `mcb-body`:
  - `RoutePlanner.java` (tick costs, Baritone-style search, swim/gap/ladder/water landing, unknown-space exits, edit edges)
  - `TerrainPlanner.java` (rolling forecast; edits live only in branches)
  - Python search (`body_search.py` reference, `navigation_segmented.py` legacy) is an offline oracle and test reference only. If Python appears to replan, treat it as a bug or stale path and find the request to Java (`docs/JAVA_MIGRATION_STATUS_2026-11-03.md`).
- **The mod drives every tick (D10).** Python sends a route or objective. `RouteMotor` follows it (no planner, no edits), `StepMotor` executes one bounded planned edit, `BodyRuntime` is the bridge. Courses and scoring live in `mcb-lab`.
- **Follower outcomes.** A route or gap ends `done`. An edit ends `edits_ready` (not a committed move: Python confirms fresh geometry and inventory debit, then hands over the next step). Failure is `needs_replan`; Python requests a new Java plan. Replan when admitted geometry changes on or near the route.
- **Unknown cells** are never treated as air or support by the motor or build planning; the planner may cross unknown space at a penalty (D3). Map uploads are acknowledged per generation (`planner_map_transport.py`).
- **Companion layers.** `gather_plan` / `build_plan` are pure planners; `gather_runner` / `build_runner` execute against `GatherBody` / `BuildBody` protocols; `companion_body.py` is the real adapter over `Bridge` (fakes: `gather_fake.py`); `companion_jobs.build_job` gathers, then builds. Ops: `docs/BODY_ACTION_OPS.md`.
- **Movement & placement choices are costs, not switches.** Prefer pricing an action over hard-disabling it. For vertical assist, scaffolding comes before generic blocks (dirt/cobble priced as last resort; `docs/SCAFFOLDING.md`).
- **Stall rule.** Standing still ≥ 5 s is allowed only while breaking, placing, or crafting (`progress_watch.py`, battery idle analysis). A node-cap hit near the goal is a bug (`python -m mclab.search_caps RUN`).
- **CodeGraph before full reads:** Before reading `companion_body.py` (97 KB), `gather_runner.py`, `navigation_segmented.py`, or `RoutePlanner.java` in full, run `codegraph explore "<symbol>"`.

## Detailed Lab & Replay Rules

- **Swimming:** Implemented in planner and body; status per item in §5 of `docs/NAVIGATION_DIRECTION_2026-10-28.md`.
- **Data retention & pruning:** Git is the record of code; `results/` is working data, not an archive. `mclab.prune` deletes old raw logs automatically when every arena session ends (`python -m mclab.prune [--apply]` for a full pass; `MCLAB_NO_PRUNE=1` disables). To keep a run's logs, cite its id in `docs/`. Policy: `docs/DATA_RETENTION.md`.
- **Digests & comparisons:** Every build/gather run writes `digest.json`; compare attempts with `mclab runs [KEY]` (`mclab runs backfill` for older folders). Never pool earlier configurations into a new denominator; native changes require source-bound requalification before legacy scored admission.
- **Resuming builds:** Do not rerun gathering to test a build from a known point — restore `results/companion/<run>/checkpoints/NNNN.json` or `final.json` via `run-build.py --resume <file>`.
- **Planner parity replay:** To judge what the planner knew, replay only the terrain batches Python read through the Java planner (`GOAL="[x,y,z]" ... scripts/analysis/planner_parity.py <events.jsonl> <tick> <start>`, running `routePlannerParity`), not `policy_sample`. Exact only for `terrain: uploaded` plans; `live` plans read within `terrain_radius`.
