# Documentation index

Use the short current-status documents first. The dated roadmap snapshot and
experiment reports preserve provenance; they are not current work instructions.

## Current navigation and decisions

| Document | Role |
|---|---|
| [ROADMAP.md](ROADMAP.md) | Current upcoming work and dependency order |
| [IMPLEMENTED.md](IMPLEMENTED.md) | Current implemented scope, proof links, limits, and verification freshness |
| [DECISION_MATRIX.md](DECISION_MATRIX.md) | Settled choices and genuinely unresolved decisions |
| [DECISIONS.md](DECISIONS.md) | Durable technical decision record and implementation boundaries |
| [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) | Authoritative navigation strategy, phases, acceptance, and superseded-doc guidance |
| [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) | Chosen lab UI/companion scope and phase definitions; current delivery status is in the roadmap and implementation summary |

## Setup and current contracts

| Document | Role |
|---|---|
| [RUN_CONFIG.md](RUN_CONFIG.md) | Shared typed configuration for all seven launch families, canonical fields, resolution, provenance, CLI/dashboard behavior, and verification limits |
| [PHASE1_SETTINGS_INVENTORY.md](PHASE1_SETTINGS_INVENTORY.md) | Phase 1 field/consumer inventory, bridge payloads, and prompt construction references |
| [LAB_UI.md](LAB_UI.md) | Current React UI, catalog/forms, queue/SSE, lifecycle and security behavior; legacy page and evidence are retained below |
| [ui/README.md](../ui/README.md) | Frontend build/test commands, package provenance, static serving and component extension guide |
| [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md) | Body/lab protocol and terrain delivery contract |
| [BODY_ACTION_OPS.md](BODY_ACTION_OPS.md) | Native action operations, inputs, and constraints |
| [AWARENESS_MODES.md](AWARENESS_MODES.md) | Awareness presets and delivery semantics |
| [SPATIAL_NAVIGATION.md](SPATIAL_NAVIGATION.md) | Spatial memory and planner/motor boundary |
| [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md) | Companion gathering/building plan and milestones |
| [KNOWLEDGE_PACK_FORMAT.md](KNOWLEDGE_PACK_FORMAT.md) | Runtime knowledge pack and schematic format |
| [COMPANION_ABILITIES.md](COMPANION_ABILITIES.md) | Ability inventory and implementation coverage |

## Model work

| Document | Role |
|---|---|
| [EXPERIMENT_QUEUE.md](EXPERIMENT_QUEUE.md) | Queued model experiments; treat old “next sprint” text as historical |
| [G4_SCREENING.md](G4_SCREENING.md), [G4_LIVE_INTEGRATION.md](G4_LIVE_INTEGRATION.md) | Model screening and live integration scope; neither alone closes G4 |
| [TANDEM_HARNESS_PROOF.json](TANDEM_HARNESS_PROOF.json) | Optional tandem harness verification and limitations |

## Proof and gate records

| Artifact | Role |
|---|---|
| [gates.json](gates.json) | Current gate status and source/config-bound evidence pointers |
| `*_PROOF.json`, `G*_*.json` | Individual proof records; use the linked source hash and run ID in each file |
| [JAVA_MIGRATION_STATUS_2026-11-03.md](JAVA_MIGRATION_STATUS_2026-11-03.md) | Migration review status and its evidence boundary |
| [SETUP_STATUS.md](SETUP_STATUS.md) | Bootstrap and gate history |

## Analysis ledgers and history

| Document | Role |
|---|---|
| [ROADMAP_HISTORY_2026-11-15.md](ROADMAP_HISTORY_2026-11-15.md) | Verbatim previous roadmap snapshot; historical evidence IDs and prune-retention citations |
| [STEP_C_PROGRESS_2026-10-29.md](STEP_C_PROGRESS_2026-10-29.md) | Planner-core cohort ledger; dated findings, not current work ordering |
| [STEP_B_PROGRESS_2026-10-28.md](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN_2026-10-29.md](STEP_B_RERUN_2026-10-29.md) | Route follower implementation and rerun history |
| [PHASE0_NATURAL_BASELINE_2026-10-28.md](PHASE0_NATURAL_BASELINE_2026-10-28.md), [PHASE0_FIX_PLAN_2026-10-28.md](PHASE0_FIX_PLAN_2026-10-28.md), [PHASE0_FIXES_RESULTS_2026-10-28.md](PHASE0_FIXES_RESULTS_2026-10-28.md) | Phase 0 baseline and corrections |
| `results/` | Run data. A run ID cited in docs may be protected from pruning; do not cite a run as proof without checking its source/config identity. |

## Older or superseded references

Use the superseded-document table in
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) before
relying on older plans such as `JEV_NAVIGATION_PLAN.md`, `HYBRID_ARCHITECTURE.md`,
`ROLLING_TERRAIN_PLANNING.md`, or `HILL_NAVIGATION.md`. Their raw results may
remain useful as history even when their work ordering or conclusion is obsolete.

The original root [technical handoff](../Minecraft_AI_Technical_Handoff.md) is
the specification and must remain unchanged.
