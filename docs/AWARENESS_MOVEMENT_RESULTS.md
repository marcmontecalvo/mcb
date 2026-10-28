# Awareness and hill-navigation development results

Updated October 27, 2026. The initial acquisition/store/search/motor path and the first
hill block-use families work. The full [ten-day sprint](AWARENESS_MOVEMENT_SPRINT.md)
remains incomplete. No new Baritone comparison or natural-terrain parity claim
is made by these results.

Work resumed at Marc's request. The [control-latency follow-up](CONTROL_LATENCY_FOLLOWUP_2026-10-27.md)
records the current measurements, retained failed trials and 418-test checkpoint.
Ten mixed layouts passed with motor p95 3.94 ms and decode-plus-motor p95 11.79 ms,
zero discarded stale actions and lab restoration. This meets the processing
target for those development fixtures; broader performance acceptance remains open.
The [pause handoff](PAUSE_HANDOFF_2026-10-27.md) preserves the earlier checkpoint.

## Current increment

Terrain planning retains four moves, including downstream cuts/placements and
resource reservations. Ten mixed layouts passed both the foreground stage and
a subsequent background stage. Ten longer flat layouts each adopted a prefetched
segment. These stages retain their individual source identities and raw events
in [BACKGROUND_NAVIGATION_PROOF.json](BACKGROUND_NAVIGATION_PROOF.json) and
[ROLLING_TERRAIN_PROOF.json](ROLLING_TERRAIN_PROOF.json).

The current archive uses compressed palette sections with transactional migration
from old cell archives. Current/session/persistent retention, historical access
and hot/persistence byte budgets are implemented. Map admission now runs outside
control renewal. Its first live mixed case completed in 273 ticks with motor
p50/p95/p99 of 2.83/12.88/20.89 ms
(`results/hills/20261027T131303307685Z/summary.json`). Earlier accounting prototypes
were much slower; their evidence remains. At that stage the 20 ms p95 target
required a broader frozen cohort. The current follow-up supplies separate passing
fixture evidence; total acquisition/MSPT/transport acceptance remains pending.
At that earlier increment, all 387 Python tests and Ruff checks passed.

## Historical initial hill cohort

Raw cohort: `results/hills/20261027T030540500259Z/summary.json` and sibling events
and per-case SQLite maps. This was a frozen development cohort on the installed
Minecraft 26.3/protocol 777 stack. Ten distinct width/slope-position layouts were
run for each family. Scoring required actual summit height and on-ground arrival,
in addition to proximity to the goal.

| Family | Completions | Game ticks min / median / max | Authoritative edits |
| --- | --- | --- | --- |
| Walking, edits disabled | 10/10 | 128 / 218 / 390 | No breaks or placements |
| Cut steps, placement disabled | 10/10 | 315 / 342.5 / 410 | Three blocks removed per case |
| Placed foothold, excavation disabled | 10/10 | 256 / 279 / 298 | One block added and one kit block consumed per case |

The cohort recorded no discarded stale actions or controller errors. All fixture
terrain was restored, followed by a C1 reset and despawn. Working memory stayed
within the 20,000-cell cap. Timings include acquisition/planning as encountered;
these short fixture times must not be compared to earlier natural-route times.

## Implemented and verified boundaries

- Awareness: legacy/visible/chunk modes, adjustable horizontal/vertical reach,
  separate entity settings, bounded extra acquisition and explicit partial
  coverage. Client-equivalent chunk subscription is still unimplemented.
- Memory in this historical cohort: transactional indexed cells, bounded hot memory and queued persistence;
  independent-process recall; world/reset/dimension/exact-access partitioning;
  explicit unknown geometry and air replacement. Remembered cells cannot satisfy
  the fresh motor safety window after a restart.
- Search: resumable bounded A*, reusable route segments and an immutable planning
  revision. The durable-detour test traverses a U-shaped route that initially
  moves away from the goal, after working-map eviction and archive reopening.
- Execution: fresh support/clearance checks; separate bounded terrain plans and
  interaction executor; exact ray/reach and hotbar selection; observed block and
  placement-stock confirmation. Known walking search precedes local terrain work.
- Historical local checks: 348 Python tests passed, with Ruff/format and Java checks.
  That source's requalification passed 15 awareness and 11 legacy sensor checks,
  20 C1 resets, 29 adversarial evaluator checks and 20 resets each for C2–C5.
  Legacy scoring admission was accepted; development awareness was rejected by
  the frozen legacy scoring gate. These are admission checks, not new scored runs.

That initial version used per-cell SQLite and foreground search. Current section
compression, background admission/search, prefetch and rolling terrain forecasts
are later increments with separate evidence. Coarse routing remains pending.
Neither version globally optimizes all possible terrain edits.

`independent-recall.json` beside the cohort summary records a separate Python
process reopening the completed cut-9 live archive. It recalled 500 cells into
a working map capped at 500, with zero cells admitted as fresh motor evidence.

## Diagnostic failures retained

Earlier diagnostic folders are retained under `results/hills/`. They exposed
full-map copying/GC pauses causing command-age failures, a lowercase Minecraft
placement face mismatching an uppercase executor expectation, and a request
budget incorrectly shared across an entire cohort. The fixes removed full-map
copying from renewal, used the existing motor GC policy, normalized the actual
face value and gave each attempt its own bounded budget plus cleanup allowance.
The final cohort above was run after those fixes and a follow-up audit that
enforced exact standing height at route goals and retained edit-budget
reservations through cancellation. The earlier successful 30-case cohort at
`results/hills/20261027T023500851674Z/summary.json` remains historical evidence.

The independent recall collector initially hashed the SQLite file before its
last connection closed. That receipt is retained as
`independent-recall-before-close.json`; the passing receipt was regenerated after
closing all connections and verified against the resulting file hash.

The interrupted budget cohort is
`results/hills/20261027T023019834165Z/summary.json`. It contains 14 completed
successes and an incomplete next case, and is not a passing 30-case cohort.
Its active fixture was explicitly stopped/restored and C1 reset with recovery
events in `runtime/hill-recovery-stop.jsonl` before the fresh cohort. Its original
incomplete restoration flag is retained.

## Remaining sprint acceptance

Later source-bound movement stages are recorded in the
[movement follow-up](MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md) and
[movement proof](MOVEMENT_ENGINE_PROOF.json). Short bridges and bounded
staircases each passed ten enabled layouts and ten safe disabled-action refusals.
Pillars then passed ten exact ascents and ten safe pillar-disabled refusals with
ordinary placement still enabled. The staircase and pillar pairs each share
Python/native/runner hashes on their respective stage sources. Slabs, stairs and enabled/
disabled drops also have retained development cohorts on their respective stage
sources. These stages are not pooled into final integrated qualification.

Cardinal level one/two-block gaps also passed ten checked crossings and ten
safe disabled-action refusals on matching Python/native/runner sources. Their
native audit requires real jump height and forbids falling, edits and stock use.

Broader construction/collision/drop/gap adverse cases, water, ladders and
their corresponding adversarial cohorts remain pending. So do client-envelope
calibration, byte-budgeted transport deltas,
coarse places/connections and larger-world performance
measurements. Cancellation/recovery and resource accounting need broader mixed
interaction coverage beyond the current focused tests.

The five-route same-controller awareness comparison, fresh 30-attempt natural
comparison against Baritone, night diagnostics and sprint latency/MSPT targets
have not been rerun or achieved by this increment. The lethal-hit reporting gap
was repaired and verified with a native 20-HP lethal hit; see the rolling proof.
The sprint cannot be called
complete on the strength of the hill fixtures.

The earlier milestone is recorded in the historical
[goal audit](AWARENESS_MOVEMENT_GOAL_AUDIT.json) and
[combined proof](AWARENESS_MOVEMENT_PROOF.json). Server changes since that
qualification require source-bound requalification before legacy scored admission.
The full sprint remains active in [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json).
