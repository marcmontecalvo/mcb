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
| Spatial recall and live terrain | Spatial-memory and navigation proofs describe bounded memory and planner/motor boundaries. Live plans and uploaded-map replays have different evidence semantics. | [SPATIAL_NAVIGATION_PROOF.json](SPATIAL_NAVIGATION_PROOF.json), [AWARENESS_MODES.md](AWARENESS_MODES.md), [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md), [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md). Never treat delivered batches as the complete map for a `terrain: live` plan. |

## Gather, build, and lab UI

| Capability | Verified scope | Evidence and limits |
|---|---|---|
| Versioned run config | Shared strict resolution supports presets and explicit CLI precedence for gather, build, natural routes, courses, tactical/tandem model runs, battery checks, and companion checks. Effective settings and input/provenance identity are recorded for every launch family before arena/world work. | [RUN_CONFIG.md](RUN_CONFIG.md), [PHASE1_SETTINGS_INVENTORY.md](PHASE1_SETTINGS_INVENTORY.md), [run_config.py](../src/mclab/run_config.py), [run_launch.py](../src/mclab/run_launch.py). Config resolution does not qualify Minecraft behavior. |
| Generated typed settings schema | `GET /api/schema` is generated from typed settings metadata and includes family capabilities. Server-side validation rejects unknown, invalid, or unsupported values before arena acquisition. | [RUN_CONFIG.md](RUN_CONFIG.md), [PHASE1_SETTINGS_INVENTORY.md](PHASE1_SETTINGS_INVENTORY.md). A schema field is not proof of consumption by a runner unless its policy path is wired. |
| Policy propagation | `Awareness`, `MovementPolicy`, `TerrainPolicy`, and `MemoryPolicy` are resolved and consumed by supported companion/session paths. Build `GatherOptions` reach planning, refill, and chest/storage paths. Tactical/tandem model settings include backend, request limits, and typed planner profile overrides. | [RUN_CONFIG.md](RUN_CONFIG.md), [run_config.py](../src/mclab/run_config.py), [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md). Other behavior still requires its own live qualification. |
| Run provenance | Immutable launch config/source records are written under `results/launches/` and the same snapshot is attached to native result folders. Outcomes retain completed, failed, startup-error, and interrupted states. Battery launch records precede its restart boundary. | [RUN_CONFIG.md](RUN_CONFIG.md), [run_launch.py](../src/mclab/run_launch.py). Unknown evidence remains unknown; recorded identity does not itself prove a source-to-installed-mod match. |
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
