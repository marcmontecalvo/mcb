# Script audit - 2026-11-15

## Findings and recommendation

The initial audit found **56 tracked files under scripts/**; the authorized cleanup
removed **19** unused diagnostics, leaving **37** maintained files. Normal operation needs only
**mclab.ps1** and **lab-ui.ps1**. Keep their existing names and paths; another
launcher would add to the clutter. See [LAB_QUICKSTART.md](LAB_QUICKSTART.md).

This is a static dependency/content audit, supplemented by read-only checks of
the installed lab. "Integrated" means called/imported by current runners,
dashboard dispatch, battery or tests; it does not mean recently executed or
live-qualified. "Specialist/manual" means a useful explicit operation with
no established automatic consumer. "Cleanup candidate" means an ad hoc diagnostic
or fixed replay with no established runtime dependency, not proof that its author
never runs it manually. Git references cannot establish frequency of use.

- **Integrated: 15**
- **Specialist/manual: 15**
- **Removed diagnostics: 19**
- **Maintenance: 5**
- **Daily: 2**

The 19 removed diagnostics had no runtime, test-import or task consumers.
A final stem/import search confirmed the apparent test_stances matches were
unrelated test function names. These were overlapping latest-run readers and
fixed November 8/9 plan/checkpoint replays. Git history retains their source.
No runtime results, checkpoints, worlds or maintained runners were removed.

Use dashboard status/history, the parameterized scripts/analysis/ tools and
[BUILD_DEBUGGING.md](BUILD_DEBUGGING.md) for supported diagnostics. These are
alternatives, not exact replacements for every historical filter.

## Current scripts/ inventory (37 files)

References below are exact tracked filename mentions (plus Python module imports
for live_common and idle_gaps), excluding the file itself. Documentation mentions
are evidence of intended/manual or historical use, not runtime callers. Dynamic
check dispatch is explicitly identified; filename searches alone miss it.

| Script | Classification | Purpose / limitation | Reference evidence |
|---|---|---|---|
| [scripts/analysis/idle_gaps.py](../scripts/analysis/idle_gaps.py) | Integrated | Independent recorded standstill audit, consumed by battery and unit tests. | [scripts/analysis/silence_gaps.py:11](../scripts/analysis/silence_gaps.py#L11), [scripts/run-battery.py:176](../scripts/run-battery.py#L176), [tests/test_idle_gaps.py:1](../tests/test_idle_gaps.py#L1) |
| [scripts/analysis/planner_parity.py](../scripts/analysis/planner_parity.py) | Specialist/manual | Offline map replay through Java planner; builds/runs Gradle parity task. | [CLAUDE.md:51](../CLAUDE.md#L51), [docs/AWARENESS_MODES.md:134](../docs/AWARENESS_MODES.md#L134), [docs/JAVA_MIGRATION_INVENTORY.md:20](../docs/JAVA_MIGRATION_INVENTORY.md#L20) |
| [scripts/analysis/planner_trace.py](../scripts/analysis/planner_trace.py) | Specialist/manual | Recorded planner state changes. | [docs/STEP_C_PROGRESS_2026-10-29.md:37](../docs/STEP_C_PROGRESS_2026-10-29.md#L37) |
| [scripts/analysis/silence_gaps.py](../scripts/analysis/silence_gaps.py) | Specialist/manual | Recorded Python request silence analysis; silence alone is not a standstill. | No external tracked filename reference |
| [scripts/analysis/site_scan.py](../scripts/analysis/site_scan.py) | Specialist/manual | Offline generated-world build-site search. | [README.md:45](../README.md#L45), [docs/ROADMAP_HISTORY_2026-11-15.md:899](../docs/ROADMAP_HISTORY_2026-11-15.md#L899) |
| [scripts/analysis/time_budget.py](../scripts/analysis/time_budget.py) | Specialist/manual | Recorded speed/controller-state timeline. | [docs/STEP_C_PROGRESS_2026-10-29.md:38](../docs/STEP_C_PROGRESS_2026-10-29.md#L38) |
| [scripts/analysis/timeline.py](../scripts/analysis/timeline.py) | Specialist/manual | Event timeline and position export. | [docs/STEP_C_PROGRESS_2026-10-29.md:39](../docs/STEP_C_PROGRESS_2026-10-29.md#L39) |
| [scripts/analysis/watch_digest.py](../scripts/analysis/watch_digest.py) | Specialist/manual | Parameterized tail digest of a recorded/live build log. | No external tracked filename reference |
| [scripts/audit-model-smokes.py](../scripts/audit-model-smokes.py) | Integrated | Regenerate hash-bound historical model evidence; writes proof artifacts. | [tests/test_model_audit.py:10](../tests/test_model_audit.py#L10), [docs/G4_LIVE_INTEGRATION.md:73](../docs/G4_LIVE_INTEGRATION.md#L73), [docs/TANDEM_HARNESS_PROOF.json:15](../docs/TANDEM_HARNESS_PROOF.json#L15) |
| [scripts/bench-pathing-grid.py](../scripts/bench-pathing-grid.py) | Specialist/manual | Synthetic spatial-memory admission benchmark. | [docs/PHASE0_FIXES_RESULTS_2026-10-28.md:42](../docs/PHASE0_FIXES_RESULTS_2026-10-28.md#L42) |
| [scripts/benchmark-section-merge.py](../scripts/benchmark-section-merge.py) | Specialist/manual | Offline codec/merge benchmark with parity checks. | [docs/CONTROL_LATENCY_FOLLOWUP_2026-10-27.md:67](../docs/CONTROL_LATENCY_FOLLOWUP_2026-10-27.md#L67), [docs/SPATIAL_NAVIGATION.md:180](../docs/SPATIAL_NAVIGATION.md#L180) |
| [scripts/build-client-bridge.ps1](../scripts/build-client-bridge.ps1) | Maintenance | Build optional Baritone client bridge. | [src/mclab/baritone_client.py:48](../src/mclab/baritone_client.py#L48) |
| [scripts/build-mod.ps1](../scripts/build-mod.ps1) | Maintenance | Build and verify both server mods; not an everyday startup step. | [.vscode/tasks.json:9](../.vscode/tasks.json#L9), [scripts/setup.ps1:11](../scripts/setup.ps1#L11), [src/mclab/modbuild.py:35](../src/mclab/modbuild.py#L35) |
| [scripts/check-animals.py](../scripts/check-animals.py) | Integrated | Live development regression; temporarily mutates lab/session state. | Dynamic dashboard dispatch: dashboard_runs.py COMPANION_CHECKS/script_name; [scripts/run-battery.py:72](../scripts/run-battery.py#L72), [docs/BODY_ACTION_OPS.md:85](../docs/BODY_ACTION_OPS.md#L85), [docs/LAB_UI.md:250](../docs/LAB_UI.md#L250) |
| [scripts/check-companion-scaffold.py](../scripts/check-companion-scaffold.py) | Integrated | Live development regression; temporarily mutates lab/session state. | [scripts/run-battery.py:46](../scripts/run-battery.py#L46), [docs/BUILD_DEBUGGING.md:159](../docs/BUILD_DEBUGGING.md#L159) |
| [scripts/check-companion-wading.py](../scripts/check-companion-wading.py) | Integrated | Live development regression; temporarily mutates lab/session state. | [scripts/run-battery.py:44](../scripts/run-battery.py#L44), [docs/BUILD_DEBUGGING.md:80](../docs/BUILD_DEBUGGING.md#L80) |
| [scripts/check-containers.py](../scripts/check-containers.py) | Integrated | Live development regression; temporarily mutates lab/session state. | Dynamic dashboard dispatch: dashboard_runs.py COMPANION_CHECKS/script_name; [tests/test_run_launch.py:422](../tests/test_run_launch.py#L422), [docs/BODY_ACTION_OPS.md:85](../docs/BODY_ACTION_OPS.md#L85), [docs/LAB_UI.md:250](../docs/LAB_UI.md#L250) |
| [scripts/check-crafting.py](../scripts/check-crafting.py) | Integrated | Live development regression; temporarily mutates lab/session state. | Dynamic dashboard dispatch: dashboard_runs.py COMPANION_CHECKS/script_name; [tests/test_dashboard.py:903](../tests/test_dashboard.py#L903), [docs/BODY_ACTION_OPS.md:23](../docs/BODY_ACTION_OPS.md#L23), [docs/BUILD_AND_GATHER_PLAN_2026-11-01.md:269](../docs/BUILD_AND_GATHER_PLAN_2026-11-01.md#L269) |
| [scripts/check-hill-navigation.py](../scripts/check-hill-navigation.py) | Integrated | Live development regression; temporarily mutates lab/session state. | [scripts/run-battery.py:47](../scripts/run-battery.py#L47), [docs/BUILD_DEBUGGING.md:83](../docs/BUILD_DEBUGGING.md#L83), [docs/CONTROL_LATENCY_FOLLOWUP_2026-10-27.md:53](../docs/CONTROL_LATENCY_FOLLOWUP_2026-10-27.md#L53) |
| [scripts/check-natural-ops.py](../scripts/check-natural-ops.py) | Integrated | Live development regression; temporarily mutates lab/session state. | [scripts/run-battery.py:73](../scripts/run-battery.py#L73), [src/mclab/dashboard.py:64](../src/mclab/dashboard.py#L64), [tests/test_dashboard_baseline.py:24](../tests/test_dashboard_baseline.py#L24) |
| [scripts/check-natural-reporting.py](../scripts/check-natural-reporting.py) | Specialist/manual | Manual lethal-hit/reporting regression; changes live world temporarily. | No external tracked filename reference |
| [scripts/check-placement.py](../scripts/check-placement.py) | Integrated | Live development regression; temporarily mutates lab/session state. | Dynamic dashboard dispatch: dashboard_runs.py COMPANION_CHECKS/script_name; [docs/BODY_ACTION_OPS.md:23](../docs/BODY_ACTION_OPS.md#L23), [docs/BUILD_AND_GATHER_PLAN_2026-11-01.md:264](../docs/BUILD_AND_GATHER_PLAN_2026-11-01.md#L264), [docs/LAB_UI.md:250](../docs/LAB_UI.md#L250) |
| [scripts/check-site-fill.py](../scripts/check-site-fill.py) | Integrated | Live development regression; temporarily mutates lab/session state. | [scripts/run-battery.py:74](../scripts/run-battery.py#L74), [docs/BUILD_DEBUGGING.md:132](../docs/BUILD_DEBUGGING.md#L132) |
| [scripts/check-spatial-navigation.py](../scripts/check-spatial-navigation.py) | Specialist/manual | Manual spatial-memory/navigation live smoke. | [docs/SPATIAL_NAVIGATION.md:10](../docs/SPATIAL_NAVIGATION.md#L10) |
| [scripts/classify-natural-attempts.py](../scripts/classify-natural-attempts.py) | Specialist/manual | Offline natural-run failure facts; hints require review. | [docs/PHASE0_NATURAL_BASELINE_2026-10-28.md:59](../docs/PHASE0_NATURAL_BASELINE_2026-10-28.md#L59) |
| [scripts/discover.ps1](../scripts/discover.ps1) | Maintenance | Fetch dependency discovery metadata; maintainer operation. | [README.md:194](../README.md#L194) |
| [scripts/frozen-comparison.ps1](../scripts/frozen-comparison.ps1) | Specialist/manual | Historical fixed Phase 7 cohort; expects existing route artifacts; review failure handling before reuse. | [docs/ROADMAP_HISTORY_2026-11-15.md:507](../docs/ROADMAP_HISTORY_2026-11-15.md#L507) |
| [scripts/lab-ui.ps1](../scripts/lab-ui.ps1) | Daily | Open/reuse dashboard; optional verified dashboard restart. | [README.md:121](../README.md#L121), [docs/LAB_UI.md:21](../docs/LAB_UI.md#L21) |
| [scripts/live_common.py](../scripts/live_common.py) | Integrated | Imported live-check helper library; not a standalone command. | [scripts/check-animals.py:9](../scripts/check-animals.py#L9), [scripts/check-companion-scaffold.py:7](../scripts/check-companion-scaffold.py#L7), [scripts/check-containers.py:7](../scripts/check-containers.py#L7) |
| [scripts/mclab.ps1](../scripts/mclab.ps1) | Daily | Canonical CLI wrapper, repository directory and uv cache. | [.vscode/tasks.json:5](../.vscode/tasks.json#L5), [README.md:93](../README.md#L93) |
| [scripts/resolve-dependencies.py](../scripts/resolve-dependencies.py) | Maintenance | Resolve/update dependency locks; not routine setup. | [README.md:194](../README.md#L194) |
| [scripts/run-battery.py](../scripts/run-battery.py) | Integrated | Sequential regression battery with standstill evidence. | [src/mclab/dashboard_suites.py:24](../src/mclab/dashboard_suites.py#L24), [src/mclab/run_launch.py:401](../src/mclab/run_launch.py#L401), [tests/test_run_battery.py:1](../tests/test_run_battery.py#L1) |
| [scripts/run-build.py](../scripts/run-build.py) | Integrated | Build, gather-and-build, checkpoint resume and inspection. | [scripts/run-battery.py:85](../scripts/run-battery.py#L85), [src/mclab/build_stats.py:44](../src/mclab/build_stats.py#L44) |
| [scripts/run-gather.py](../scripts/run-gather.py) | Integrated | Gather/craft from empty inventory. | [scripts/run-battery.py:38](../scripts/run-battery.py#L38), [src/mclab/dashboard.py:67](../src/mclab/dashboard.py#L67), [src/mclab/dashboard_runs.py:501](../src/mclab/dashboard_runs.py#L501) |
| [scripts/setup.ps1](../scripts/setup.ps1) | Maintenance | Initial Python/bootstrap/C1 generation/mod build/install/doctor; stop lab first. | [.vscode/tasks.json:4](../.vscode/tasks.json#L4), [README.md:92](../README.md#L92) |
| [scripts/summarize-natural-comparison.py](../scripts/summarize-natural-comparison.py) | Specialist/manual | Aggregate completed natural-run evidence. | [docs/MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md:92](../docs/MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md#L92) |
| [scripts/summarize-navigation-performance.py](../scripts/summarize-navigation-performance.py) | Specialist/manual | Aggregate completed hill/navigation evidence. | [docs/PAUSE_HANDOFF_2026-10-27.md:184](../docs/PAUSE_HANDOFF_2026-10-27.md#L184) |

## Removed diagnostics (19 files)

Removed after the operator authorized cleanup on 2026-11-15. Paths below are
historical names, not runnable commands; restore from Git only for deliberate
historical investigation.

| Former script | Disposition | Former purpose / limitation | Reference evidence |
|---|---|---|---|
| `scripts/analyze_latest_run.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/analyze_places.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/check_events.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/decode_plan.py` | Removed | Fixed Nov 9 run and plan-282. | No external tracked filename reference |
| `scripts/diagnose_bot.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/extract_checkpoint.py` | Removed | Reconstruct a checkpoint from fixed Nov 9 evidence. | No external tracked filename reference |
| `scripts/find_act.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/find_req.py` | Removed | Fixed Nov 8 run and plan-627. | No external tracked filename reference |
| `scripts/find_rp.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/inspect_break_events.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/inspect_build_progress.py` | Removed | Latest run with fixed large-house origin and template. | No external tracked filename reference |
| `scripts/inspect_map.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/inspect_requests.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/recent_ops.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/run_house_large_inspect.ps1` | Removed | Fixed Nov 9 checkpoint, D: path and Hermes uv path; unsuitable as general launcher. | No external tracked filename reference |
| `scripts/status_summary.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/test_stances.py` | Removed | Fixed Nov 9 recovered checkpoint stance diagnostic; not a pytest test. | No external tracked filename reference |
| `scripts/test_tree19.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |
| `scripts/watch_felling.py` | Removed | Ad hoc latest-run/event diagnostic; review fixed filters and assumptions before reuse. | No external tracked filename reference |

## Entry points outside scripts/

- pyproject.toml: the installed **mclab** command enters src/mclab/cli.py.
  Application modules under src/ are runtime code, not disposable scripts.
- ui/package.json: **dev**, **typecheck**, **build**, **test**, **test:e2e** are the
  frontend's supported commands. ui/e2e/server.py is test-server infrastructure.
- mcb-body/gradlew(.bat), mcb-lab/gradlew(.bat), and
  client-bridge/gradlew(.bat) are required build wrappers. Their build.gradle and
  settings.gradle files are build definitions, not cleanup candidates.
- experiments/qwen35-direct/scorer.py is the preserved optional model service;
  its Dockerfile/README reproduce a historical experiment. Keep it with that
  experiment, separate from normal lab startup.
- .vscode/tasks.json exposes setup, server, build/install and verification tasks.
- Ignored .qwen/, runtime/, build outputs, dependency trees, caches and results/
  are local/generated artifacts, not maintained script entry points. No untracked
  nonignored scripts/ files were present at audit time.

## Verified state and gaps

Read-only checks on 2026-11-15: server status returned **running**, Minecraft
26.3/protocol 777, game address **127.0.0.1:25575**, zero human players.
ui/dist/index.html and runtime/mod-build.json exist. Dashboard --help confirms
--port and --no-browser. Existing build artifacts are not proof that they match
every current source file. No live runs, server restart, install, world reset or
browser flow were performed for this documentation audit.

The dashboard **--sessions** crash is fixed. Qualification jobs have labels/IDs
without an entrant; listing now uses entrant, label, ID, kind, then unknown.
Legacy entrant output and recorded status are preserved; session files are
not rewritten. The listing against all 26 existing local session records now
exits successfully, including qualification records that previously crashed.
All 122 focused dashboard/runner/battery tests pass, including five new mixed-job
listing regression cases; Ruff over src, tests and retained scripts passes.
Documentation links and diff whitespace checks pass. No full repository suite or
live Minecraft regression was run for this cleanup.

Other limitations: run-gather.py's header still says "Written, not yet run"
despite current dashboard/battery integration; frozen-comparison.ps1 does not
check native exit codes between entrants. Neither should be read as a current
qualification claim.
