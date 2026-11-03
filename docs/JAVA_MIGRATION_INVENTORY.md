# Java planner migration inventory

This is a development implementation ledger, not live qualification. Segmented
production callers use the body planner; the Python search and terrain-beam
implementations are explicit offline reference oracles. The original installed
mod and pre-migration baseline remain separate from this candidate.

| Production path or invariant | Migration state | Evidence or work remaining |
| --- | --- | --- |
| Position/radius, shaped movement, known and unknown geometry, edit edges | Java `RoutePlanner` | Java fixture suite and 90-case Python/Java differential harness; live candidate checks remain |
| Interaction goal, actual eye reach and clear ray | Java planner plus controller actual-pose check and centering | Visible/unknown/occluded differential scenes, raised target, blocked ray and excluded stance fixtures; live checks remain |
| Mob and hazard costs | Java planner receives current visible threats and cost profile | Java hazard/hostile tests and differential scenes; live survival acceptance remains open |
| Climb and route-local door/vine policy | Planner and objective-specific motor settings; raw development controls use session policy | Ladder on/off, permission-isolation and consecutive-request tests; live transition checks remain |
| Tick/unit cost, search weighting, shapes and diagonals ablations | Java planner policy fields | Differential fixtures cover nine settings; broader live ablations remain |
| Terrain beam, staircase and pillar macros | Java `TerrainPlanner`, with policy and stock request/report path | Java fixtures and 30 matching Python bridge/resource cases; live macros remain |
| Memory current/session/persistent and historical geometry | Snapshot selected by controller, sent as versioned bounded section deltas | Encoder, immutable-reader, unknown, eviction and acknowledgement tests; live mode coverage and performance remain |
| Resource limits and policies for edits | Java planner and terrain beam with full protected footprints and ordered large-request framing | Protected/budget/stock fixtures and large-payload checks; end-to-end large schematic remains |
| Replanning, cancellation, stale revisions and backpressure | Serialized per-bridge snapshot transfer and explicit plan cancellation | Supersession, frame/ack failures and immutable-generation tests; long-running live stress remains |
| Production Python search removal | No production route/terrain search imports or selection flag | Isolated subprocess import/CLI boundary test; reference controllers live under tests |
| Precise stance arrival and near-goal replanning | Checked same-cell centering and actual interaction eye position | Controller and Java motor fixtures; reproduce the baseline house case live |
| Qualification, matched calm and survival checks | Not run on migrated build | Independent review first; source-bound development regression and paired baselines before any scored admission |

The map transport uses palette IDs, compressed 16-cube sections and explicit
unknown state zero. Planning may use allowed historical geometry; the motor uses
fresh local observations. No candidate live run or scored promotion is claimed here.
