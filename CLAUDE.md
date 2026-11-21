# MCB — Start Here

> **"Run it and let me know so I can take a look"** means: run the build with **`--inspect`** (`uv run --locked python scripts/run-build.py ... --inspect`, or `--resume <checkpoint> --stage` to show a saved attempt without building). The script prints `INSPECT <run folder>` and `progress.json` says `stage: inspection` when done; the world stays joinable at `127.0.0.1:25575`. Tell the user then, and wait: don't create `<run folder>/STOP`, stop the server, or start another run **until the user says they're done looking** (or asks for the next run). Then **you** create `STOP` and let the script exit: the user never creates it themselves; they tell you in chat, or use the lab page once it has a stop button. `STOP` wipes the arena.
> Skills: `.claude/skills/mcb-lab-run`, `mcb-qualify`, `mcb-log-query`, `mcb-planner-ab`.

## Core Working & Lab Rules

- **Commands:** Run from repo root in PowerShell via `uv run --locked ...`. Tests: `rtk test uv run --locked pytest -q <path>`. Lint: `rtk err uv run --locked ruff check <path>`. Don't use `rtk proxy` routinely.
- **State vs. policy:** Read `/state`; **never** poll `/policy` and discard the result (a served terrain batch counts as delivered and is not resent). One live job at a time.
- **Java changes:** Stop server → `scripts/build-mod.ps1` → `mclab mod-install` → start server → requalify. A dashboard restart does not install Java.
- **Evidence & logs:** `results/companion/<run>/` (`digest.json`, `summary.json`, `progress.json`, `walkthrough.md`, `checkpoints/`). Compare runs with `mclab runs [KEY]`. `events.sqlite3` grows to gigabytes — query with `python -m mclab.event_log` or `scripts/analysis/*`, never read whole.
- **Long runs:** Run in the background and wait for exit notifications (no sleep/poll loops). Never stop a progressing run (check `progress.json` `updated_at` and the 5s stall rule).
- **Worktrees:** Do A/B work under `D:/mcb-wt/<name>`. Never `git stash` in `D:\mcb` (other sessions keep uncommitted work there). Tests write launch records to tmp (`tests/conftest.py`), not `results/launches`.
- **Doc precedence:** `docs/NAVIGATION_DIRECTION_2026-10-28.md` overrides older docs where they conflict. Update `docs/ROADMAP.md`, `docs/IMPLEMENTED.md`, and `docs/DECISIONS.md` / `docs/DECISION_MATRIX.md` when finishing items. Do not edit `Minecraft_AI_Technical_Handoff.md`.

## On-Demand Docs (Read Before Deep Work)

| Topic | Path |
|---|---|
| **Architecture contracts** (Java vs. Python, motors, follower outcomes, companion layers, stall rule, parity replay, pruning) | [docs/ARCHITECTURE_CONTRACTS.md](docs/ARCHITECTURE_CONTRACTS.md) |
| **Doc index, roadmap, verified behavior & decisions** | [docs/INDEX.md](docs/INDEX.md), [docs/ROADMAP.md](docs/ROADMAP.md), [docs/IMPLEMENTED.md](docs/IMPLEMENTED.md), [docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md) |
| **Authoritative navigation direction & acceptance tracker** | [docs/NAVIGATION_DIRECTION_2026-10-28.md](docs/NAVIGATION_DIRECTION_2026-10-28.md), `docs/SPRINT_ACCEPTANCE.json` |
| **Lab operations, UI & data retention** | [docs/LAB_OPERATIONS.md](docs/LAB_OPERATIONS.md), [docs/LAB_UI.md](docs/LAB_UI.md), [docs/DATA_RETENTION.md](docs/DATA_RETENTION.md) |
| **Java migration, body ops & scaffolding** | `docs/JAVA_MIGRATION_STATUS_2026-11-03.md`, `docs/BODY_ACTION_OPS.md`, `docs/SCAFFOLDING.md` |
