---
name: mcb-planner-ab
description: Use when comparing two MCB planner, motor or cost variants (two commits, branches or flag settings) — offline route replay through the Java RoutePlanner first, then a same-seed live A/B — and when judging whether a planner change made routes better or worse.
---

# Planner / motor A/B

Java plans and Python asks: the production planner is `mcb-body` `RoutePlanner.java` /
`TerrainPlanner.java`. A/B work compares Java variants. Python search is only a reference.

## Setup: one worktree per arm

```powershell
git -C D:/mcb worktree add D:/mcb-wt/ab-base origin/main
git -C D:/mcb worktree add D:/mcb-wt/ab-change <branch>
```

- **Never `git stash` in `D:\mcb`.** Other sessions keep uncommitted work there.
- Each arm is pinned to a commit. Record `git rev-parse --short HEAD` per arm, and for live
  arms the installed source hash in `runtime/mod-build.json`.

## 1. Offline first (no server needed)

Pick the decision to replay: a run folder, the tick, start and goal. Find them with skill
`mcb-log-query` (`planner_trace.py`, `event_log show --type plan_accepted`).

```powershell
$env:GOAL="[x,y,z]"; $env:CAP="8000"
uv run --locked python scripts/analysis/planner_parity.py <attempt>/events.sqlite3 <tick> "[x,y,z]"
```

- The script rebuilds the map Python had at `<tick>`, exports it (path printed) and runs
  `gradlew -p mcb-body routePlannerParity` on it.
- Flags: `UNKNOWN=1` (plan through unknown space), `SWIM=1` (swim and Baritone moves),
  `EDITS=1` (production terrain profile, edits included), `PLAN=plan-N` (replay that
  recorded request).
- Export once, then replay the **same export** in each arm:
  `mcb-body/gradlew.bat -q -p mcb-body routePlannerParity "-Pargs=<export.json> <nodeLimit> 1"`
  from `D:/mcb-wt/ab-base` and `D:/mcb-wt/ab-change`.
- Compare: found/not found, route length, tick cost, nodes expanded, ms searched, edit
  count. Use several decisions, including the one that motivated the change and at least
  two unrelated ones as regression guards.
- The replay is exact only for plans whose `plan_accepted` says `terrain: uploaded`. For
  `live` plans the delivered batches are a lower bound.

## 2. Live A/B (only if offline looks right)

1. Q1 in each worktree (skill `mcb-qualify`).
2. Arm A: `scripts/build-mod.ps1`, stop the server, `uv run --locked mclab mod-install`,
   start the server, run the Q2 subset for the touched paths, then the target scenario.
3. Arm B: the same steps from the other worktree. Run arms **one after the other**, never
   at the same time: there is one server and one arena.
4. Same scenario, seed and start for both arms (e.g. a battery `--only` list, or
   `run-build.py --start … --seed …`). Run each arm at least as many times as the noise
   needs. One run each is an anecdote.
5. Compare with `mclab runs <key> --last N` and the digests: outcome, duration, stalls,
   replans, search caps (`python -m mclab.search_caps RUN`), idle gaps.

## Rules

- Don't pool earlier configurations into a new denominator. Count only runs of this
  arm's exact commit and mod hash.
- If a run started without its Q2, it is diagnostic and doesn't count.
- After native changes, legacy scored admission needs source-bound requalification
  (Q4: `mclab body qualify`, `docs/gates.json`).
- Reinstall arm A's mod before you return the lab to main. Leave the server running the
  main build.
- Report a table per arm (commit, mod hash, n, pass/fail/unsure, median duration, stalls),
  then a verdict and its confidence. Cite the run ids in `docs/` if the result matters:
  cited runs keep their raw logs.
