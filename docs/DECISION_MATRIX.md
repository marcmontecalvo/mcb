# Decision matrix

This page separates settled direction from questions that still need a product
or architecture decision. Implementation tasks and verification gates are not
open decisions.

## Settled

| Topic | Decision | Authority |
|---|---|---|
| Navigation strategy | Deterministic Java route planning over admitted/remembered geometry; the motor refuses unknown cells. Models set goals and profiles, not per-tick movement. | [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md), [DECISIONS.md](DECISIONS.md) |
| Body and lab split | `mcb-body` owns sensing and control; `mcb-lab` owns evaluation and must score other named players without the MCB body. | [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) |
| Baritone comparison | Baritone is a matched-awareness reference; its relevant movement/cost behavior may be reimplemented with attribution, but source is not copied. Pivot only if matched comparisons justify it. | [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md), [BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md) |
| Lab frontend stack | Keep Python as backend; use React, TypeScript, Vite, Tailwind, shared theme tokens, and `@homenet/ui`, served as static assets. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md), [DECISIONS.md](DECISIONS.md) |
| External-agent interface | MCP is the integration endpoint for external agents. The first release supports one bot and validated jobs; it does not expose direct motor control. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| Companion runtime (Nov 23, 2026) | The companion plays on a separate play server (its own world and ports, mcb-body only) through one persistent host process; MCP and in-game chat (local Atom Qwen) are built together on one validated intent path. A model picks tasks, never motor actions; `stop` is handled in code. | [COMPANION_LLM_PLAN_2026-11-23.md](COMPANION_LLM_PLAN_2026-11-23.md), [DECISIONS.md](DECISIONS.md) |
| Companion planner overrides | Gather and build accept planner profile overrides and record effective settings. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md), [RUN_CONFIG.md](RUN_CONFIG.md) |
| Night survival for now | Lab episodes: the nightfall shelter protocol; route-weight changes and reactive flight were rejected for the lab direction. The companion (Nov 23, 2026) defends itself instead: fight when armed above the health floor, else arm, regroup, shelter or burrow (row below). | Historical decision recorded in [ROADMAP_HISTORY_2026-11-15.md](ROADMAP_HISTORY_2026-11-15.md). |
| Companion auto-defence (Nov 23, 2026) | Always on; Python decides (fight, arm, regroup, shelter, burrow, avoid, flee) and the mod's `fight` op does the melee. The host guard interrupts a job, defends, then reruns the job. Floor (default 3 hearts) and radii are settings, never an off switch. | [COMPANION_DEFENSE_PLAN_2026-11-23.md](COMPANION_DEFENSE_PLAN_2026-11-23.md), [DECISIONS.md](DECISIONS.md) |
| Companion combat technique (Nov 23, 2026) | Python picks a tactic per target type and the weapon (a table, not branches); the mod carries it out per tick (crit timing, cooldowns, aim, footwork). Walking away and escaping differ: `escape` sprint-jumps and steers by every hostile. A mob a melee fight cannot reach is left to range or escape, never given up on by a failure cap. A creeper is fought only from past its fuse (hit-and-back, or a spear's band), and the guard's per-poll checks read memory, never storage. Live evidence comes from operator-only `trial` matrices on the play instance, each fought on one flat arena. | [COMPANION_COMBAT_PLAN_2026-11-23.md](COMPANION_COMBAT_PLAN_2026-11-23.md), [DECISIONS.md](DECISIONS.md) |

## Open decisions

No unresolved decision blocks the active phase 2 work. The plan explicitly defers
these later choices until their prerequisites are available:

| Topic | What remains to choose | Timing / constraint | Authority |
|---|---|---|---|
| Agent hooks and plugin/tool packaging | Decide which agent-side hooks or packaging improve capability discovery, job follow-through and cancellation beyond the bespoke skills and MCP contract. | Revisit in phase 4 against the selected agents; do not add a framework during phase 2. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| Browser voice engines | Select local transcription and speech playback engines after measuring latency and usability. | Phase 5; use the same validated chat path and retain text fallback. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| Default build order (zones or storeys) | Keep zones as the default or switch to `--order storeys`. On d07edfc the structure labs tie (storeys mean 22:33, zones 22:46, spread 18:59–24:41; DECISIONS "Storey build order and band stages"). | After #11 / #12 (cleanup leftovers) and a whole house-large comparison on one commit (#14). Zones stays the default until then. | [DECISIONS.md](DECISIONS.md), issue #14 |
| Skin source and workflow | Establish which skin options are practical and choose a workflow based on the spike. | Phase 6, before enabling multi-bot support. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |

Other active items are implementation or verification tasks and belong in
[ROADMAP.md](ROADMAP.md). Do not re-open settled stack, navigation, planner-role,
MCP, or night-protocol choices merely because older documents contain unanswered-
question sections.

Add a row here only when an authorized owner needs to choose between materially
different behaviors or architectures. Record the options, consequences, owner,
date, selected choice, and any evidence that informed it. Keep implementation
status and verification gaps in the roadmap instead.
