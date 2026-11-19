# MCB — start here

> **"Run it and let me know so I can take a look"** means: run the build with
> **`--inspect`** (`uv run --locked python scripts/run-build.py ... --inspect`, or
> `--resume <checkpoint> --stage` to show a saved attempt without building). The script
> prints `INSPECT <run folder>` and `progress.json` says `stage: inspection` when the
> build is done; the world stays as built, joinable at `127.0.0.1:25575`. Tell the
> user then, and **do not** create `<run folder>/STOP`, stop the server, or start
> another run: STOP restores (wipes) the arena. The user creates STOP when finished.
> Skills: `.claude/skills/mcb-lab-run`, `mcb-qualify`, `mcb-log-query`, `mcb-planner-ab`.

1. Read [docs/ROADMAP.md](docs/ROADMAP.md) for upcoming work.
   [docs/IMPLEMENTED.md](docs/IMPLEMENTED.md) summarizes verified behavior and its
   limits; [docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md) separates settled
   choices from open decisions; [docs/INDEX.md](docs/INDEX.md) routes to plans,
   proof, contracts, and history.
2. [docs/NAVIGATION_DIRECTION_2026-10-28.md](docs/NAVIGATION_DIRECTION_2026-10-28.md)
   remains authoritative for navigation decisions and phases. Check the linked
   gate records and source hashes before treating dated results as current.
3. To operate the lab, read the README and [docs/LAB_UI.md](docs/LAB_UI.md). Recheck
   live server status rather than trusting recorded PIDs. Attempt analysis scripts
   are in `scripts/analysis/`.

## Document precedence

- The direction document above overrides older docs where they conflict.
  Docs with a "Partly superseded (Oct 28, 2026)" or "History (Oct 31, 2026)" banner
  contain legacy sections. `docs/INDEX.md` lists each document's role.
- When work finishes an item or finds a new one, update `docs/ROADMAP.md`; keep
  implementation/proof claims in `docs/IMPLEMENTED.md` and choices in
  `docs/DECISIONS.md` / `docs/DECISION_MATRIX.md`.
- When you edit an older doc for other reasons and find a conflict, update it to
  match the direction document or mark the section legacy. Do not leave two answers.
- `Minecraft_AI_Technical_Handoff.md` is the original specification: do not edit it.
- `docs/SPRINT_ACCEPTANCE.json` remains the acceptance tracker.

## Working rules carried forward

- Commands run from the repo root in PowerShell as `uv run --locked ...`. Don't type `rtk proxy`:
  the Claude Code rtk hook already filters output, and `rtk proxy` disables that filtering.
  Tests: `rtk test uv run --locked pytest -q <path>` (failures + summary only). Lint:
  `rtk err uv run --locked ruff check <path>`. Use raw output only when a filter hid something.
- Unknown cells are never treated as support or air by the **motor**. (The planner may
  plan through unknown space at a penalty; see the direction document, D3.)
- Git is the record of how the code was. `results/` is working data, not an archive.
  `mclab.prune` deletes old raw logs automatically when every arena session ends
  (run-build, run-gather, navigation, natural); `python -m mclab.prune [--apply]` does
  a full pass. To keep a run's logs, cite its id in `docs/`. Policy and numbers:
  [docs/DATA_RETENTION.md](docs/DATA_RETENTION.md). `MCLAB_NO_PRUNE=1` turns it off.
- Every build/gather run writes `digest.json` when it ends; compare attempts with
  `mclab runs [KEY]` instead of opening logs (`mclab runs backfill` digests older folders).
- Do not pool earlier configurations into a new denominator when comparing results.
  Native changes require source-bound requalification before legacy scored admission.
- To test a build from a known point, do not rerun gathering: each build run writes
  `results/companion/<run>/checkpoints/NNNN.json` and `final.json`; restore one with
  `run-build.py --resume <file>` (same seed, template cells and inventory, build only).
- Swimming is implemented in the planner and body; status per item in §5 of the
  direction document.
- Never poll `/policy` and discard the result: a served terrain batch counts as
  delivered and is not resent. Use `/state` to wait for a run state.
- To judge what the planner knew, replay only the terrain batches Python read
  through the Java planner (`GOAL="[x,y,z]" ... scripts/analysis/planner_parity.py <events.jsonl> <tick> <start>`,
  which exports the delivered map and runs the `routePlannerParity` gradle task), not
  the body's `policy_sample` log.
  Exact only for plans whose `plan_accepted` says `terrain: uploaded`; `live` plans read
  the world within their `terrain_radius`, so delivered batches are a lower bound.

## Architecture contracts (read before opening the big files)

- **Java plans, Python asks.** Route and terrain planning run in mcb-body:
  `RoutePlanner.java` (tick costs, Baritone-style search, swim/gap/ladder/water landing,
  unknown-space exits, edit edges) and `TerrainPlanner.java` (rolling forecast; edits live
  only in branches). Python search (`body_search.py` reference, `navigation_segmented.py`
  legacy) is an offline oracle and test reference only. If Python appears to replan,
  treat it as a bug or a stale path and find the request to Java
  (docs/JAVA_MIGRATION_STATUS_2026-11-03.md).
- **The mod drives every tick (D10).** Python sends a route or objective. `RouteMotor`
  follows it (no planner, no edits), `StepMotor` executes one bounded planned edit,
  `BodyRuntime` is the bridge. Courses and scoring live in mcb-lab.
- **Follower outcomes.** A route or gap ends `done`. An edit ends `edits_ready`, which is
  not a committed move: Python confirms fresh geometry and the inventory debit, then hands
  over the next step. Failure is `needs_replan`; Python requests a new Java plan. Replan
  when admitted geometry changes on or near the route.
- **Unknown is never air or support** for the motor or build planning; the planner may
  cross unknown space at a penalty (D3). Map uploads are acknowledged per generation
  (`planner_map_transport.py`).
- **Companion layers.** `gather_plan` / `build_plan` are pure planners; `gather_runner` /
  `build_runner` execute against the `GatherBody` / `BuildBody` protocols;
  `companion_body.py` is the real adapter over `Bridge` (fakes: `gather_fake.py`);
  `companion_jobs.build_job` gathers, then builds. Ops: docs/BODY_ACTION_OPS.md.
- **Movement and placement choices are costs, not switches.** Prefer pricing an action
  over hard-disabling it. For vertical assist, scaffolding comes before generic blocks
  (dirt/cobble are priced as a last resort; docs/SCAFFOLDING.md).
- **Stall rule.** Standing still 5 s or more is allowed only while breaking, placing or
  crafting (`progress_watch.py`, battery idle analysis). A node-cap hit near the goal is
  a bug (`python -m mclab.search_caps RUN`).
- Before reading `companion_body.py` (97 KB), `gather_runner.py`, `navigation_segmented.py`
  or `RoutePlanner.java` in full, run `codegraph explore "<symbol>"`.

## Lab operation

Details, durations and the qualifier ladder: [docs/LAB_OPERATIONS.md](docs/LAB_OPERATIONS.md).

- Read `/state`; never poll `/policy`. One live job at a time.
- Java change: stop the server, `scripts/build-mod.ps1`, `mclab mod-install`, start the
  server, requalify. A dashboard restart does not install Java.
- Evidence: `results/companion/<run>/` (`digest.json`, `summary.json`, `progress.json`,
  `walkthrough.md`, `checkpoints/`). `events.sqlite3` runs to gigabytes: query it with
  `python -m mclab.event_log` or `scripts/analysis/*`, never read it whole.
- Long runs go in a background task; you are woken when it exits. No sleep/poll loops.
  Never stop a progressing run: judge from `progress.json` `updated_at` and the stall rule.
- Do A/B work in worktrees under `D:/mcb-wt/<name>`. Never `git stash` in `D:\mcb`:
  other sessions keep uncommitted work there.
- Worktrees: `D:/mcb-wt/*`. Tests write launch records to their tmp folder, not
  `results/launches` (tests/conftest.py).
