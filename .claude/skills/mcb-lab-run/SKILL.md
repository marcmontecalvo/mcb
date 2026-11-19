---
name: mcb-lab-run
description: Use when starting, waiting for, watching or reporting on any MCB live run — run-build.py / run-gather.py jobs, battery rounds, check-* scripts, navigation or natural sessions — and whenever the user says "run it", "let me look at it", "what is it doing now" or "is it stuck".
---

# Running the MCB lab

All commands run from the repo root (`D:\mcb`, or a worktree under `D:/mcb-wt/`).

## 0. "Run it and let me know so I can take a look"

Use **`--inspect`** on run-build.py. The world must survive the run.

```powershell
uv run --locked python scripts/run-build.py --start -24 64 56 --supply --seed 31415926 `
  --build tests/fixtures/schematics/house-large.build.json --inspect
```

- The build runs, then the script prints `INSPECT <run folder>`, `progress.json` turns to
  `stage: inspection`, and the session holds the world open. The script keeps running
  until `<run folder>/STOP` exists.
- When you see `INSPECT`, tell the user: the run folder, the outcome line, and
  "join 127.0.0.1:25575". Then **stop and wait**.
- **Never** create `STOP`, stop the server, `mclab server stop`, TaskStop the script, or
  start another run while it is inspecting. STOP restores (wipes) the arena. The user
  creates STOP when finished.
- To show a saved attempt without rebuilding it:
  `run-build.py --resume results/companion/<run>/checkpoints/final.json --stage`.
  Add `--preview` to place a reference copy beside it (docs/BUILD_DEBUGGING.md).

## 1. Before launching

1. `uv run --locked mclab server status`. If it's stopped: `mclab server start`.
2. Qualify first (skill `mcb-qualify`). Run Q1 before any live run, and the Q2 fast subset
   before anything longer than ~15 min. A run without its Q2 is **diagnostic**: say so
   when you report it.
3. Run one live job at a time. If a dashboard session or another run is active
   (`results/sessions/*/session.json` status `running` with a fresh `progress.json`),
   ask before starting.
4. After a Java change: stop the server, `scripts/build-mod.ps1`,
   `uv run --locked mclab mod-install`, then start the server. A dashboard restart
   does not install Java.

## 2. Launching and waiting

- Use a **background** Bash task (`run_in_background: true`, `timeout: 7200000`). You are
  woken when it exits. No `sleep` loops, no polling, no Monitor busy-waits. In T3 Code,
  call `t3_background_wait` with the task id before ending the turn.
- Durations: check-* 0.3–2 min. One battery check 1–15 min, the full battery 1–2 h.
  Build/gather runs take 8 min at the median, 64 min at p90 and up to 90 min.
- Battery: `uv run --locked python scripts/run-battery.py --out results/battery/<name>/<round> [--only a b]`.
  Read only the `RESULT …` and `BATTERY: …` lines (or `SUMMARY.md`).
- With `--inspect`, the background task does **not** exit when the build ends. Watch for
  the `INSPECT` line in its output file, or for `progress.json` `stage == "inspection"`.

## 3. Watching a live run ("what is it doing now")

- `results/companion/<run>/progress.json`: `stage`, `done/total`, `detail`, `updated_at`.
  If `updated_at` is fresh, the run is progressing.
- Last few op results: `uv run --locked python -m mclab.event_log show <run>/events.sqlite3 --tail 30`.
- Standstills so far: `uv run --locked python scripts/analysis/idle_gaps.py <run>`.
- Read `/state` only. Never poll `/policy`: a served terrain batch is not resent.

## 4. Never stop a progressing run

A run is progressing while `progress.json` `updated_at` keeps moving or `done` rises.
Standing still for 5 s or more while breaking, placing or crafting is normal.
- Even when it looks slow or wrong, report what you see and let the user decide.
- Stop a run only if it has made no progress for minutes **and** the user agrees. Use
  the dashboard's cancel / stop-after-batch, never TaskStop on a progressing run.

## 5. Reporting

- Read `results/companion/<run>/digest.json` (outcome, duration, phases, stalls, failure
  reasons) and `summary.json`. Don't read event logs whole: they run to gigabytes. For
  deeper questions use skill `mcb-log-query`.
- Compare with earlier attempts: `uv run --locked mclab runs <build key> --last 5`.
- To keep a run's raw logs past automatic pruning, cite its run id in `docs/`.
- Raw logs of older runs are pruned automatically when each arena session ends
  (docs/DATA_RETENTION.md). The newest run per build key always stays whole.
