# Lab operations

How an agent (or a person) runs the MCB lab: what each action costs, what to run before
a live run, how to wait, and how to keep a world for inspection. The step-by-step
versions are in the skills under `.claude/skills/` (`mcb-lab-run`, `mcb-qualify`,
`mcb-log-query`, `mcb-planner-ab`).

## Keep the world for inspection

`scripts/run-build.py ... --inspect` builds as usual, then holds the session:

- the script prints `INSPECT <run folder>`;
- `progress.json` reads `stage: inspection`;
- the world is joinable at `127.0.0.1:25575`.

The hold lasts until `<run folder>/STOP` exists. Creating STOP restores (wipes) the arena,
so it is created only once the person inspecting says they are done: the agent running
the session creates it when told in chat (the lab page has no stop button for this yet). `--resume <checkpoint> --stage` shows a saved
attempt without building (`--preview` adds a reference copy beside it;
docs/BUILD_DEBUGGING.md). "Run it and let me know so I can take a look" always means
`--inspect`.

## Action catalog

| Action | Command | Typical duration |
|---|---|---|
| Server status / start / stop | `uv run --locked mclab server status\|start\|stop` | seconds; start ≈ 1 min |
| Build the mod | `scripts/build-mod.ps1` (sets `JAVA_HOME=runtime/toolchains/jdk`, `GRADLE_USER_HOME=runtime/gradle-cache`) | 1–3 min |
| Install the mod | `uv run --locked mclab mod-install` (server stopped) | seconds |
| Unit tests | `rtk test uv run --locked pytest -q` | ~2.5 min full |
| One check script | `scripts/check-*.py` | 0.3–2 min |
| Qualify (one command) | `uv run --locked mclab qual [--fast] [--only …] [--keep-all]` or `scripts/qualify.ps1` | 1–2 h full |
| Battery | `scripts/run-battery.py [--out results/battery/<stamp>] [--only …] [--keep-all] [--list]` | 1–15 min per check, 1–2 h full |
| Build / gather run | `scripts/run-build.py`, `scripts/run-gather.py` | median 8 min, p90 64 min, max ~90 min |
| Compare attempts | `uv run --locked mclab runs [KEY] [--last N] [--json]` | seconds |
| Full data prune | `uv run --locked python -m mclab.prune [--apply]` | minutes (ledger VACUUM) |

Battery checks: wading, wading-trapped, vine-escape, scaffold, scaffold-course, hill-default,
hill-water, hill-ladder, animals, natural-ops, site-fill, iron-xray, iron-mine, iron-raw,
small-house.

The reference large build is `tests/fixtures/schematics/house-large.build.json`, key
`build:house-large@-24,64,56#31415926` (`--start -24 64 56 --seed 31415926 --supply`).

## Qualifier ladder

| Level | Content | When |
|---|---|---|
| Q0 | ruff + tests of the touched modules (plus `compileJava` / ui typecheck if touched) | every edit loop |
| Q1 | full pytest, ruff, gradle build / ui test+build if touched | before any live run and before commit |
| Q2 | battery subset from the path map | fast subset before runs > ~15 min; full before merge, overnight runs, after mod-install |
| Q3 | iron-*, small-house, scaffold-course | the experiment itself, not a gate |
| Q4 | `mclab body qualify`, `mclab course reset-check`, `execution-check`, `docs/gates.json` | scored/legacy admission after native changes |

`mclab qual` runs every check back to back (restarting the server first) and ends with
a table plus `QUAL: N PASS, M FAIL, K UNSURE`; exit 0 only if all PASS. `--fast` skips
iron-xray, iron-mine, iron-raw, small-house and scaffold-course. Output goes to
`results/battery/<UTC stamp>/` (`SUMMARY.md`, `summary.json` with verdict, seconds and
kept/discarded per check). A clean pass (no standstill, no search-cap finding, under 75 %
of its timeout) has its log and run folder deleted, except a run id cited in `docs/`;
FAIL, UNSURE and anything that looked bad keeps its log and folder. `--keep-all` keeps
everything.

Path map (touched path → Q2 fast subset):

- `mcb-body/**`, `companion_body.py`, `native_driver.py`, `navigation_*`, `terrain_*`,
  `planner_map_transport.py` → hill-default, hill-water, hill-ladder, wading,
  wading-trapped, scaffold
- `gather_*` → natural-ops, animals
- `build_*`, `site_prep.py`, `scaffolding.py`, `pillar_steps.py` → scaffold, site-fill
- `dashboard*`, `ui/**` → Q1 only
- `mcb-lab/**` → full Q2 (+ Q4 if evaluator or reset code changed)

A live run without its Q2 is **diagnostic**: report it as such and do not count it as
evidence. The lab refuses bodies after 32 qualification spawns; restart the server
between long Q2 sets.

## Waiting and watching

- Start long work as a background task and end the turn; the harness wakes the agent when
  it exits. No sleep/poll loops, no Monitor busy-waits.
- With `--inspect` the task does not exit after the build; watch for the `INSPECT` line or
  `progress.json` `stage == "inspection"`.
- Progress: `progress.json` (`stage`, `done/total`, `updated_at`). A run whose
  `updated_at` keeps moving is progressing; never stop it. Standing still ≥ 5 s is
  normal only while breaking, placing or crafting.
- Live tail: `scripts/analysis/watch_digest.py <run> --seconds 120`.
- Read `/state`, never poll `/policy` (a served terrain batch is not resent).
- One live job at a time; check `results/sessions/*/session.json` and the newest
  `progress.json` before starting.

## Launch records

Every dashboard/CLI launch writes `results/launches/<stamp>/`. Tests write theirs to
their tmp folder (`tests/conftest.py` patches `mclab.run_launch.LAUNCHES`). Until
2026-11-19 they leaked into the real folder: 2,707 of 2,904 records (1,871 of the 1,976
"failed") came from pytest and were deleted with
`scripts/maintenance/drop_test_launch_records.py`. The remaining failures are real
(build/gather checks failed, companion `RuntimeError`, nonzero exits).
