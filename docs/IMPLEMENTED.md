# Implemented behavior and verified scope

This page summarizes capabilities backed by repository evidence. It separates
code availability, automated checks, development/live runs, and qualification.
Evidence is source-bound where the artifact records a source hash; recheck that
hash against the installed code before relying on qualification. Detailed
attempts and historical provenance remain in their linked proof files and run
records.

## Lab foundation and qualification

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Local Minecraft 26.3 bootstrap | G0 passed: generated C1 geometry was checked across 98,441 blocks with zero mismatches; human join and inspection were recorded. | [gates.json](gates.json), [SETUP_STATUS.md](SETUP_STATUS.md). G0 is bootstrap evidence, not body qualification. |
| Initial C1 body profile | G1 passed for `initial-c1-survival-v1`: movement, aiming, log mining, and pickup within the frozen profile. Automated mechanics 39/39 and supplementary protocol checks 24/24. | [gates.json](gates.json), [G1 capability review](G1_CAPABILITY_REVIEW.json), [G1 body proof](G1_BODY_PROOF.json). Entity attack, combat knockback, construction, and dimension travel remain outside this initial profile. |
| Reset, episode recording, and execution | G2/G3 records cover the frozen initial C1 scope and deterministic perception-goal attempts. | [gates.json](gates.json), [G2 reset proof](G2_RESET_PROOF.json), [G2 episode proof](G2_EPISODE_PROOF.json), [G3 execution proof](G3_EXECUTION_PROOF.json). Gate evidence applies to its recorded source/configuration only. |

## Navigation and planner

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Java navigation planner and body motor | Java is the production planner; Python navigation modules were removed as runtime oracles, while the delivered-map parity tool replays maps through Java. | See the dated implementation record in [ROADMAP_HISTORY_2026-11-15.md](ROADMAP_HISTORY_2026-11-15.md) and the authoritative [navigation direction](NAVIGATION_DIRECTION_2026-10-28.md). Recheck source and gate hashes before treating any historical pass as current. |
| Deterministic C2-C5 development navigation | A frozen development cohort is recorded as 80/80. This is deterministic development evidence, not model performance or a fresh qualification of later body edits. | [G5 navigation proof](G5_NAVIGATION_PROOF.json), [gates.json](gates.json). Consult the proof's source/config hashes and acceptance scope. |
| House-large speed fixes (Nov 19, 2026) | RouteMotor follows routes over any collision-box top when shapes are on and uses the request's arrival radius for centre-only climbable arrivals. Bridge route refusals ban the route's edges and stop as `stuck` after a refusal storm; refused centrings are not requested again. Plan requests reference uploaded protected/costly sets by sha256 (`planner_set`, `plan_set_unknown` retry); route plan 47,956 B to 5,423 B, terrain plan 42,848 B to 1,318 B. Prefetch and dry-terrain verdicts compare snapshot cells (`map_change.py`) instead of revision numbers. Build: path-aware `on_pass`, 8-block zones, scaffold teardown only when short or the zone is done. Goto: per-candidate progress budget, failed-stance memory, polled drop pickup; `ProgressWatch` no longer counts far failures as progress. Site prep skips fills out of reach. | Unit and Java verification only (1678 Python tests; RouteMotor 207, RoutePlanner 107, PlanSets 21 checks). Needs mod rebuild, `mclab mod-install` and live requalification; zone size 8 and the 180-tick candidate budget are untested live. Source: run 20261119T133618892664Z analysis. |
| Water and vine escape (Nov 19, 2026) | `RoutePlanner.noGoalStance` no longer proves a goal over still water unreachable when swimming is on (the swim stance is the cell above the water); this was the `wading-trapped` regression (0-node unreachable). RouteMotor keeps a rising climber within 0.15 of the column centre (`CLIMB_CENTRE`) so its head does not catch leaves in the next column, and holds jump on a climb edge while in water below the target (a level swim-stance-to-vine edge used to sink the body). Failed route edges are remembered for the body's session (`native_driver.EdgeFailures`): each repeat doubles the ban up to 6,400 ticks and a new walk starts with them banned, but a ban holds only while every block in the edge's footprint (both ends' box, one cell wider, floor to one above head) reads as it did when it failed; a changed block forgets the edge and its count, an unknown cell does not. | Python tests; RouteMotor 211, RoutePlanner 108 checks. Live: battery `wading-trapped` PASS; new battery check `vine-escape` (`scripts/check-companion-vine-escape.py`, pond and vine column from run 20261119T202232979013Z) reached its goal but stood still 9.6 s in run 20261119T212820324510Z (the wet climb edge), fixed after. |
| One-command qualification (Nov 19, 2026) | `mclab qual [--fast] [--only ...] [--keep-all]` (or `scripts/qualify.ps1`) runs the battery back to back and prints one table and a `QUAL:` line; exit 0 only if every check passes. Clean passes have their log and run folders deleted; failures, UNSURE, standstills, search-cap hits and checks over 75% of their timeout keep everything; runs cited in docs are never deleted. | `src/mclab/qualification_battery.py`, `tests/test_qualification_battery.py`; [LAB_OPERATIONS.md](LAB_OPERATIONS.md). |
| Spatial recall and live terrain | Spatial-memory and navigation proofs describe bounded memory and planner/motor boundaries. Live plans and uploaded-map replays have different evidence semantics. | [SPATIAL_NAVIGATION_PROOF.json](SPATIAL_NAVIGATION_PROOF.json), [AWARENESS_MODES.md](AWARENESS_MODES.md), [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md), [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md). Never treat delivered batches as the complete map for a `terrain: live` plan. |

## Gather, build, and lab UI

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Versioned run config | Shared strict resolution supports presets and explicit CLI precedence for gather, build, natural routes, courses, tactical/tandem model runs, battery checks, and companion checks. Effective settings and input/provenance identity are recorded for every launch family before arena/world work. | [RUN_CONFIG.md](RUN_CONFIG.md), [PHASE1_SETTINGS_INVENTORY.md](PHASE1_SETTINGS_INVENTORY.md), [run_config.py](../src/mclab/run_config.py), [run_launch.py](../src/mclab/run_launch.py). Config resolution does not qualify Minecraft behavior. |
| Generated typed settings schema | `GET /api/schema` is generated from typed settings metadata and includes family capabilities. Server-side validation rejects unknown, invalid, or unsupported values before arena acquisition. | [RUN_CONFIG.md](RUN_CONFIG.md), [PHASE1_SETTINGS_INVENTORY.md](PHASE1_SETTINGS_INVENTORY.md). A schema field is not proof of consumption by a runner unless its policy path is wired. |
| Policy propagation | `Awareness`, `MovementPolicy`, `TerrainPolicy`, and `MemoryPolicy` are resolved and consumed by supported companion/session paths. Build `GatherOptions` reach planning, refill, and chest/storage paths. Tactical/tandem model settings include backend, request limits, and typed planner profile overrides. | [RUN_CONFIG.md](RUN_CONFIG.md), [run_config.py](../src/mclab/run_config.py), [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md). Other behavior still requires its own live qualification. |
| Run provenance | Immutable launch config/source records are written under `results/launches/` and the same snapshot is attached to native result folders. Outcomes retain completed, failed, startup-error, and interrupted states. Battery launch records precede its restart boundary. | [RUN_CONFIG.md](RUN_CONFIG.md), [run_launch.py](../src/mclab/run_launch.py). Unknown evidence remains unknown; recorded identity does not itself prove a source-to-installed-mod match. |
| Build travel (Nov 20, 2026, branch fix/build-travel) | Placement uses a server-equivalent sight test (`place_sight`: 19 face points from the real eye) and the click that yields the template state (`place_state`: slab type, stair/trapdoor half, facing, axis); stances are ranked by upcoming cells covered (`stance_cover`) and re-chosen on arrival; plan sets travel as deltas; mid-build scaffold reclaim runs only when the new tower is short and takes the cheapest column per block first. Pillar edits follow StepMotor's exit contract (straight up while a tower goes on, the tower's own top, or a stair/slab/climbable side exit); refused route edits are logged as `terrain_step_refused`. | Unit tests in `tests/test_place_sight.py`, `test_place_state.py`, `test_build_runner.py`, `test_pillar_steps.py`. Live: run 20261120T113050800552Z (59:32, 1225/1230) found the pillar refusal; the pillar fix is not yet run live. |
| Dashboard launch integration | Dashboard gather/build stores run config and original input references. Legacy natural-route/backend references are resolved at batch start, then the effective config is frozen. Existing host/origin/token, arena, worker, stop, and kill boundaries are retained. | [RUN_CONFIG.md](RUN_CONFIG.md), [LAB_UI.md](LAB_UI.md), [dashboard.py](../src/mclab/dashboard.py). |

## React lab UI and queue

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Static React app | React/TypeScript, Vite, Tailwind and pinned portable `@homenet/ui` are built into `ui/dist`, served by the Python dashboard at `/` with `/assets`; `/legacy` serves the previous page and `/` falls back to it when no build exists. | [LAB_UI.md](LAB_UI.md), [ui/README.md](../ui/README.md), [dashboard.py](../src/mclab/dashboard.py). The Python server remains API and worker owner. |
| Dynamic run catalog and forms | Catalog currently contains 46 entries across gather, build, natural, courses, models, battery, and companion families. Schema-generated forms, partial presets, saved suite references, override matrices, seeds/repetitions, and resolved-config preview are implemented. Batches are capped at 60 jobs. | [LAB_UI.md](LAB_UI.md), [ui/README.md](../ui/README.md). Suite files reference presets/overrides; they do not duplicate the configuration registry. |
| Durable queue and live updates | The existing detached worker/session queue is retained. Authenticated SSE sends an `event: snapshot` containing full durable state at connect/reconnect and bounds listeners to eight. Page refresh/reconnect resynchronizes from the server. | [LAB_UI.md](LAB_UI.md), [dashboard.py](../src/mclab/dashboard.py). Browser tests do not launch Minecraft. |
| Lifecycle and request boundaries | Stop-after-batch, current-job cancellation, and dashboard shutdown have distinct effects; shutting down the dashboard does not stop Minecraft. Existing loopback, exact Host/Origin checks, query-token SSE authentication, POST authorization, arena ownership, and worker boundaries remain in force. | [LAB_UI.md](LAB_UI.md), [ui/README.md](../ui/README.md). |

## Models and companion

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Provider adapters and experiments | Provider probes, bounded adapters, and scoped model trials are recorded. | [G4 live integration](G4_LIVE_INTEGRATION.md), [G4 screening](G4_SCREENING.md), [G4 proof files](G4_SCREENING_PROOF.json). G4 remains open; a probe, synthetic screen, or mock is not full qualification. |
| Optional tandem harness | Independent role requests, expiry/revision checks, provenance, and request caps are implemented and locally tested. | [TANDEM_HARNESS_PROOF.json](TANDEM_HARNESS_PROOF.json), [DECISIONS.md](DECISIONS.md). No real two-model Minecraft run or winning architecture is claimed. |
| MCP, agent skills, chat, voice, multi-bot | Planned. Bespoke versioned skills ship with the phase 4 MCP tools; hooks and plugin/tool packaging will be evaluated then. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md). No companion MCP runtime or agent integration is claimed. |

## Verification freshness

This page records the evidence currently cited by repository docs; it is not a
live-machine check. Check [gates.json](gates.json), the linked proof artifacts,
installed mod identity, and the associated run artifacts before a scored run or
release. Phase 1 completed local verification on November 15, 2026:
**1,349 tests passed**, Ruff checks and formatting passed for the changed Python
files, and local documentation links and `git diff --check` passed. Config-only
CLI resolution was also checked across all seven run families without launching
Minecraft. The phase 1 implementation and automated acceptance are complete.
No live Minecraft qualification was run for this configuration slice.

Phase 2 completed local verification on November 15, 2026: **1,382 Python tests**,
**6 UI unit tests**, and **4 desktop/390px browser tests** passed. TypeScript,
production build, changed-file Ruff/format checks, and documentation links passed.
The browser fixture exercises real static serving, API validation, suite storage,
and SSE with simulated launches and cancellation; it never starts Minecraft or
calls model providers. Shipped JavaScript is about **84 KB gzip**, CSS **10 KB**.
These checks do not advance live Minecraft qualification.
A review pass the same day fixed an SSE slot leak on early disconnect, added UI
stream reconnect after a refused stream, kept one corrupt `session.json` from
hiding all sessions, stopped redacting `*_tokens` counts, showed stop/cancel
requests per session, and rejected planner model settings without a planner
backend (**1,386 Python tests** afterwards). Still open: no inspection-wait
state exists, and the per-second snapshot reads every session under the
dashboard lock.
