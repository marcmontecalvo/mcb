---
name: mcb-qualify
description: Use before any MCB live run, before a merge to main, after mod-install, or when asked whether a change is safe to run — picks the qualifier level (Q0–Q4) and the battery subset from the touched paths.
---

# MCB qualifier ladder

| Level | What | When | Cost |
|---|---|---|---|
| Q0 touched | `rtk err uv run --locked ruff check <files>` + `rtk test uv run --locked pytest -q tests/test_<module>*.py`; `npm --prefix ui run typecheck` if ui/ changed; `mcb-body/gradlew.bat -p mcb-body compileJava` if Java changed | every edit loop | 5–30 s |
| Q1 offline full | `rtk test uv run --locked pytest -q`, `ruff check`, `ruff format --check` on touched files; `gradlew -p mcb-body build` if Java changed; `npm --prefix ui run test` + `build` if ui changed | before ANY live run, before commit | 1–4 min |
| Q2 live baseline | battery subset (path map below) via `scripts/run-battery.py --only …` | fast subset before a run > ~15 min; full set before a merge, an overnight run, and after mod-install | 0.3–2 min per check, ~15–25 min all |
| Q3 targets | iron-xray, iron-mine, iron-raw, small-house, scaffold-course | these are the experiment, not a gate | ~15 min each |
| Q4 gate requalify | `mclab body qualify`, `mclab course reset-check`, `execution-check`, `docs/gates.json` hashes | scored/legacy admission after native changes | 10+ min |

Q2 command:
`uv run --locked python scripts/run-battery.py --out results/battery/qual-<short sha>/<stamp> --only <checks>`.
List the checks with `--list`. Restart the server between long Q2 sets: the lab refuses
bodies after 32 qualification spawns.

## Path map: touched paths → Q2 fast subset

- `mcb-body/**`, `companion_body.py`, `native_driver.py`, `navigation_*`, `terrain_*`,
  `planner_map_transport.py` → `hill-default hill-water hill-ladder wading wading-trapped scaffold`
- `gather_*` → `natural-ops animals` (iron-* are Q3)
- `build_*`, `site_prep.py`, `scaffolding.py`, `pillar_steps.py` → `scaffold site-fill`
  (small-house is Q3)
- `dashboard*`, `ui/**` → Q1 only
- `mcb-lab/**` → full Q2, plus Q4 if the evaluator or reset code changed
- docs only → nothing

Get the touched paths with `git diff --name-only origin/main...HEAD` plus `git status --short`.

## Rules

- Iterating on one subsystem: Q0 only.
- Q1 before every live run. It takes minutes, and about a third of check runs fail, so it
  isn't a formality.
- Mod change: `scripts/build-mod.ps1`, stop the server, `uv run --locked mclab mod-install`,
  start the server, then Q2. `runtime/mod-build.json` holds the installed source hash.
- If a run started without its Q2, label it **diagnostic** in reports. Don't cite it as
  evidence or compare it with a qualified run.
- Each RESULT line is PASS, FAIL or UNSURE. UNSURE is neither: report it as UNSURE. A
  near-goal `search cap: VIOLATION` is a bug.
- Not built yet (design: `mcb-lab-tooling.md` §C): `mclab check --touched` and
  `mclab qual --fast|--full`. Until they exist, run the commands above.
