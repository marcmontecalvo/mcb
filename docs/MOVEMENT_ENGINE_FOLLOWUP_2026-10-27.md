# Movement engine and comparison harness follow-up

The full sprint remains active. This increment follows
[the latency checkpoint](CONTROL_LATENCY_FOLLOWUP_2026-10-27.md); it does not
establish Baritone parity or requalify the changed native server source.

## Implemented in this increment

- The background observation worker automatically recalls nearby archived
  geometry when entering a new 16-block section. Reads decode at most 32 sections
  and return at most 4096 cells. Recalled cells fit the existing cell/byte budgets,
  cannot evict live observations, and keep original sessions and timestamps.
  Current/session memory and disabled historical access prohibit these reads.
  Foreground navigation retains explicit recall to keep disk reads off its motor.
- `place_graph.py` stores namespaced places, directed verified connections and
  future teleport-edge requirements in the existing SQLite archive. Bounded
  coarse routing excludes stale places/edges and unsatisfied requirements.
  Updating a place invalidates incident connections. This is a storage/query API;
  automatic place discovery and controller coarse-route integration remain open.
  No Waystone integration or teleport execution is implemented.
- Development `MovementPolicy` adds opt-in safe level diagonals and sprint,
  plus a solid-ground drop limit of zero through three blocks (default one).
  Diagonals require both swept side columns to be standable. Sprint requires
  food, a sufficiently long level segment and an enlarged fresh safety sweep.
  Drops require known landing and shaft clearance. Permissions also constrain
  queued terrain walking and terrain search. Frozen legacy navigation is unchanged.
- Opt-in `allow_shapes` adds observed collision-surface heights for ordinary
  slabs/stairs, fractional-height walking nodes, fresh swept motor checks and
  conservative jump/headroom checks. Unknown headroom, fluids and dangerous
  surface blocks are rejected. Complex doors/fences/mechanisms remain outside
  this implementation. Use `--collision-shapes` in the fixture runner or
  `"allow_shapes": true` in a natural-run movement profile.
- Walking search excludes recorded failed directed edges. A confirmed terrain
  step that exceeds its motion deadline now excludes that edge on replanning.
- Terrain policies now expose permitted building materials, protected block IDs
  and coordinates, and independent break, placement and tool-wear penalties.
  Material reserves count permitted supplies only. These are planning cost knobs;
  actual tool durability/time/risk modeling remains unfinished.
- Opt-in short bridges use a known full-block anchor, slow sneak approach,
  observed eye height, exact ray face and ordinary Survival placement. Every
  foothold requires both fresh geometry and an inventory debit before movement.
  Forecasts protect already traversed body cells from edits, preserving retreat
  space and preventing loops that create an unnecessary obstacle behind the bot.
  A complete walking-only horizon also competes with edited forecasts when it
  makes progress toward the goal. It cannot spend reserved materials or replace
  a missing forecast with a short isolated action.
  Edited horizons dominated by a cheaper walking route to their endpoint are
  rejected. Local horizons must reduce the existing weighted goal-distance
  heuristic; otherwise the planner reports incompleteness. Long detours that
  initially move away from the goal still require the pending coarse-route
  integration and are not certified by these bridge fixtures.
- `max_support_stack` defaults to one and permits explicit limits up to three.
  Staircase forecasts can build bottom-up support columns from known ground,
  reserving every block and honoring material reserves, protected positions and
  the placement budget. Low top-face interactions use an observed-eye-height
  sneak edge stance. Execution confirms each layer's geometry and stock debit
  before attempting the next. A debit observed before its map delta now holds
  stance instead of charging the pending block twice during revalidation.
  Unit coverage includes a four-move/six-block staircase and adverse limits.
  The native staircase fixture now provides ten approach distances to a
  four-block bedrock ledge; its exact six-block construction/ascent audit is
  implemented. The enabled ten-case development cohort now passes; adverse cases
  and final integrated source qualification remain open.
- Opt-in `allow_pillar` adds bounded jump-placement underfoot, with a configurable
  maximum height of one through three blocks. A macro verifies a known solid exit,
  accounts for every future pillar block, and returns the next four moves.
  Execution centers on the source, aims at its support, launches an ordinary
  jump and only places during an observed rising window compatible with the
  unchanged three-tick control application delay. It confirms block geometry,
  stock debit and actual landing before the next ascent. Changed exits, missing
  headroom, displacement, missing velocity/eye-height observations and late
  placement windows stop execution. Pillar work has explicit kind/exit fields in
  forecast events and uses the same reservation/recovery path as other steps.
- `MovementPolicy.max_gap` defaults to zero and admits checked level gaps up to
  one or two blocks. Two-block gaps also require sprint permission and food.
  Known-route search checks full-cube takeoff/run-up support, empty trench facts,
  jump headroom, landing and two supported run-out cells before adding a gap edge.
  A dedicated bounded executor owns run-up, the three-tick anticipated takeoff,
  flight and confirmed grounding; ordinary route following cannot walk across
  the missing support. It rechecks fresh geometry, never places blocks, records
  failed directed edges and clears its commitment on cancellation. This currently
  covers cardinal level gaps with sufficient runway/run-out, not arbitrary parkour.
  The four-move forecast requirement remains unchanged.
- The natural harness accepts one through three repetitions with unique attempt
  directories. `--entrant comparison` selects segmented MCB, Baritone and the
  existing v8 goal controller. `--movement-profile` resolves development movement
  settings. Explicit profiles remain rejected when no segmented entrant is used.
- `summarize-natural-comparison.py` preserves failure, invalid and missing
  denominators, reports per-route paired-success timing, and rejects duplicate
  trials and mixed source/profile identities. Its numerical natural target
  requires ten routes, three seeds, three repetitions, at least 27 candidate
  successes, complete valid candidate/reference trials and a median paired route
  ratio at most two. It does not certify fresh seeds, awareness equality,
  restoration or the full sprint independently of the underlying evidence.

## Live evidence

`results/hills/20261027T180414802116Z/summary.json` passed ten walking layouts
with diagonals and sprint enabled. The lab was restored, with zero invalid
attempts and zero discarded stale actions. Motor p95 was 3.28 ms;
decode-plus-motor p95 was 9.67 ms. This source predates the subsequent
disabled-drop terrain fallback fix and is a retained stage, not the final freeze.
Enabling an option does not independently prove its use in every attempt.

The first slab smoke, `results/hills/20261027T181842093149Z/summary.json`,
failed at the tick limit and restored the lab. The body landed on the leading
edge of the next slab while its center remained in the previous column; dropping
the jump waypoint caused replanning from a nonstandable rounded node. A focused
regression test now preserves that short commitment with fresh safety checks.
The fixed smoke, `results/hills/20261027T182114154952Z/summary.json`, passed and
restored the lab. Neither smoke is a ten-layout capability pass.

The first ten-layout slab cohort stopped after five arrivals when the sixth
attempt's control was rejected as `stale_queued_request`:
`results/hills/20261027T182236843983Z/summary.json`. Restoration passed. The server
logged a 2,004 ms overload warning around tick 6000. The available GC counters
do not establish the cause. This is not a passing cohort. Its native terminal
reported `failed/controller_cancel`; newer harness results explicitly classify
controller-execution failures as invalid while retaining the native status.
Derived metrics now also expose controller-error and timing-error attempt counts
for historical logs, without rewriting their source results.

The first stair smoke passed in
`results/hills/20261027T183041593291Z/summary.json`, but the subsequent cohort
failed its first layout at the tick limit and restored the lab
(`results/hills/20261027T183115883448Z/summary.json`). That attempt landed on a
stair's lower half, below the queued upper waypoint. A second focused regression
extends the short movement commitment across this ordinary half-step while
retaining fresh support/clearance checks. A successful smoke alone did not
establish repeatability.

The fresh slab cohort, `results/hills/20261027T182721311662Z/summary.json`,
passed 10/10 and restored the lab with zero invalid, controller-error or
timing-error attempts. Motor p95 was 2.78 ms and decode-plus-motor p95 was
10.17 ms. This cohort predates the later stair half-step extension; it is a
retained source-bound stage rather than a final whole-sprint freeze.

The revised stair cohort, `results/hills/20261027T183400782361Z/summary.json`,
passed 10/10 and restored the lab with zero invalid, controller-error or
timing-error attempts. Motor p95 was 3.47 ms and decode-plus-motor p95 was
10.12 ms. Later body-position collision checks and descent handling are separate
source changes; final integrated qualification remains required.

The first drop probe, `results/hills/20261027T184004723476Z/summary.json`,
stopped at a ledge and reached the tick limit, then restored the lab. Descent
waypoints now survive the body's trailing-edge ground contact. Clearance checks
use its actual height while it straddles the ledge; known shaft and landing
checks remain required. Regression cases cover both ordinary and shape-aware
motors. The three-height smoke passed in
`results/hills/20261027T184454132137Z/summary.json`. The subsequent ten varied
one/two/three-block drops passed with zero damage and restored the lab:
`results/hills/20261027T184534333831Z/summary.json`. There were no invalid,
controller-error or timing-error attempts. Motor p95 was 2.60 ms;
decode-plus-motor p95 was 8.95 ms. Drops were explicitly enabled up to three blocks.

The latest full Python suite passed **567 tests**, with Ruff lint/format and
`git diff --check` passing. Receipt: `results/background/movement-resume-tests.xml`.
The rebuilt mod passed geometry, awareness and terrain-palette build checks.
Native source hash for the slab/stair checkpoints:
`d5db16ba5a44ee731568e5cd8493da317587f88acbf77dc6c1d90d31d4ecbcb0`.
Corresponding jar SHA-256:
`e9e12b254882c826d221df5bc1b264d2e50866c333619f83e5d3e36f10f9e68c`.
The drop checkpoints used native source
`ac15d7b2f3b081669e54b8579255e1ef0745d583eb9828cf5b0d746a22fe6fe9`
and jar `d36edca102b3a7fdf902ea10340d478bc98e06e9d32950897bab928cb8ab48d2`.
The subsequent reporting/refusal-check build used source
`77becd7246526909ea363a854daed7346b8e4cdb62bed99ab5d17348976d8c51`,
jar `a522c5f0884127d4459d2d49b369ab6c73d17dfd4b86ba2c8b544d8ce156ac3a`.
It reports minimum feet height, separate death/disconnection flags and hostile
exposure in game ticks within 16 blocks. These are evaluator diagnostics, not
policy access to hidden threats.
Legacy scored admission remains closed pending source-bound requalification.
No new natural comparison or scored v8 result is claimed.

The bridge/reporting checkpoint used native source
`d6ee52c354c84408128d4bea83e672e01d67eb7c217c9d125bc6b7327a57d63f`
and jar `0ff3695eac9957011e7018e6594dfbf5cc531c0152affa1efe593552ae9efe54`.
The first bridge smoke (`results/hills/20261027T190631394345Z/summary.json`)
crossed a two-cell gap without falling. The next cohort
(`results/hills/20261027T191726522887Z/summary.json`) reached two goals but
failed the second attempt's strict audit: it placed the three required bridge
blocks plus an unnecessary fourth block behind the start. Lab restoration passed.
This is a retained failed cohort, despite native arrival. A regression now
requires the full four-move approach to place only into the gap; a new cohort
must establish the fix. Bridge acceptance requires exact gap cells, exact
confirmed placements and stock debits, zero damage, and no fall below the deck.

The next cohort (`results/hills/20261027T222850135871Z/summary.json`) passed the
first three exact bridge audits, then failed the fourth despite arrival and
zero damage. The two-cell bridge was correct, but an unnecessary step was built
four cells before it. The planner had excluded all walking-only horizons even
when the entire four-move route was available. Lab restoration passed. A second
regression now requires this walking forecast to compete without edits; blocked
non-progressing walking alternatives still cannot bypass exhausted material
reservations. Both failed cohorts remain in the evidence ledger.

The corrected cohort (`results/hills/20261027T223210640385Z/summary.json`)
passed all ten exact bridge audits and restored the lab. Each layout crossed
its generated two/three/four-cell gap with exactly the required deck cells and
matching confirmed placements/stock use, zero damage and minimum feet height
80.0. There were zero invalid, controller-error, timing-error or discarded-stale
actions. Motor p95 was 4.07 ms; decode-plus-motor p95 was 11.37 ms. This establishes
the enabled short-bridge development cohort, not final integrated qualification.
The 483-test tracked receipt is
`docs/evidence/2026-10-27-awareness-navigation/movement-bridge-tests.xml`.

The initial disabled-bridge cohort
(`results/hills/20261027T223626364133Z/summary.json`) safely refused three
layouts, then failed the fourth's no-edit audit. It did not cross or fall, but
placed one unnecessary step during retreat. Restoration passed. Regression
coverage now rejects walking-dominated edited horizons and non-progressing
local construction. Fresh enabled and disabled cohorts are required for that
later source; the preceding enabled ten-case success remains a separate stage.

The fresh disabled cohort (`results/hills/20261027T223858628169Z/summary.json`)
verified all ten safe refusals and restored the lab. Every native attempt ended
at its expected tick limit with no controller error, no descent below the start,
zero damage and zero added/removed blocks. A refusal is counted separately from
an arrival. The 484-test receipt is
`docs/evidence/2026-10-27-awareness-navigation/movement-bridge-refusal-tests.xml`.

The source-matched enabled rerun
(`results/hills/20261027T224116424853Z/summary.json`) passed all ten exact bridge
crossings. Its recorded Python hashes and native provenance exactly match the
disabled cohort. Both restored the lab with zero invalid attempts, controller
errors or timing errors. Enabled motor p95 was 3.66 ms; decode-plus-motor p95 was
10.59 ms. The evaluator subsequently confirmed actual idle state with no body.
These two cohorts establish the short-bridge permission pair; changed geometry,
resource-adverse cases, staircases and pillars remain open.
The later support-stack changes are not included in that source-matched pair;
final integrated cohorts must use the eventual frozen source.

The staircase checkpoint used native source
`7449c1f35744f9ccd19309258ccdb466e1bfbbe002b057698c69d98bcbc9cc84`
and jar `2b26a296e24ab399cf9a52a278488fcc308d0360a93cfb134e6ada4b080aa0c7`.
Its native geometry, awareness and terrain-palette build checks passed.
The first staircase probe (`results/hills/20261027T225037835951Z/summary.json`)
failed at the tick limit after confirming two blocks, then restored the lab.
The lower stack layer was placed from the sneak edge stance, but the bot's
leaning body overlapped the next layer at feet height, preventing placement.
The executor now retreats to its known source footprint before such a placement;
a focused regression covers this ordinary Survival collision constraint.

The corrected staircase smoke
(`results/hills/20261027T225258475224Z/summary.json`) passed its exact six-block
audit in 413 game ticks with zero damage and restored the lab. The following
cohort (`results/hills/20261027T225355476536Z/summary.json`) passed layout zero
but failed layout one at the tick limit, then restored the lab. The local beam
walked to the cliff before reserving the necessary staircase approach, then
started constructing in retreat. This is retained as a planning failure.

The planner now enumerates bounded straight staircase macros with known solid
exits up to twelve cells away. Each full macro verifies its approach, bottom-up
layers, resources and exit on a private overlay before returning the first four
moves. Later moves still require fresh live revalidation. Enumeration consumes
the existing search-node/time slices; it does not expand an unbounded world-edit
tree. Rolling simulations for all ten fixture distances reach the ledge/goal
using exactly the six expected blocks. A new live cohort is required to establish
repeatability. `--no-place --expect-refusal` also enables the staircase's explicit
no-placement diagnostic; safe refusals remain separate from arrivals.

The macro-stage cohort (`results/hills/20261027T225938990478Z/summary.json`)
again passed layout zero and failed layout one at its tick limit, then restored
the lab. Its initial incremental sample lacked upper headroom beyond the near
refresh. The macro correctly declined unknown geometry, but ordinary walking
fallback still consumed the needed approach. The planner now keeps the safe
stance when a known solid ledge has unscanned approach/exit headroom. It retries
after admitted scan updates; unknown space is never treated as clear. A focused
partial-scan regression verifies that waiting changes to a complete four-move
staircase forecast once the missing upper geometry arrives.

The partial-scan probe passed two layouts in
`results/hills/20261027T230303704915Z/summary.json`. The following cohort
(`results/hills/20261027T230409683816Z/summary.json`) passed those layouts but
failed the third and restored the lab. Its normal walking lookahead had advanced
three cells before requesting any terrain forecast, leaving too little ascent
space. The controller now starts the terrain handoff earlier for larger support
stacks: six remaining route nodes for three-layer stacks, preserving the existing
four-node behavior at the default one-layer limit. An integration regression
checks the actual controller-to-planner handoff before movement, in addition to
the pure macro simulations. The fresh cohort must establish live repeatability.

The next cohort (`results/hills/20261027T230800759677Z/summary.json`) passed five
layouts, then failed layout five at its tick limit and restored the lab. Its
first required foothold was confirmed, but the motor never launched the checked
jump. At the recorded stance, 0.58-wide support sampling missed a thin strip
included by the actual 0.60-wide collision check, producing an inconsistent
clearance decision. Shape-aware support sampling and body clearance now share
one 0.30 radius with the same boundary tolerance. Four nearby stance regressions
admit the ordinary clear jump and still reject obstructed headroom. The retained
five-layout success is not promoted to a ten-case pass or final qualification.

The corrected full staircase cohort
(`results/hills/20261027T231407261070Z/summary.json`) passed all ten approach
distances. Every case reached the upper deck with exactly the six expected
support cells, six confirmed placements and six adjusted stock debits, zero
excavation, zero damage and minimum feet height 80.0. Lab restoration passed.
There were zero invalid attempts, controller errors or timing errors. Motor p95
was 3.30 ms; decode-plus-motor p95 was 9.96 ms. This is source-bound development
evidence; the earlier failed cohorts remain part of the ledger.

The matching placement-disabled staircase cohort
(`results/hills/20261027T231858026135Z/summary.json`) verified ten safe refusals
with zero damage, block changes or adjusted stock spending. Native outcomes
were the expected `failed/tick_limit`; these are not counted as arrivals. Python
source hashes, native provenance and runner hash exactly match the enabled
cohort. Restoration passed with zero invalid attempts, controller errors or
timing errors; motor p95 was 2.09 ms and decode-plus-motor p95 was 8.77 ms.
An independent `/state` check confirmed actual idle state and no body afterward.
The tracked 512-test receipt is
`docs/evidence/2026-10-27-awareness-navigation/movement-staircase-tests.xml`.

## Pillar development evidence

The first pillar probe (`results/hills/20261027T232846671417Z/summary.json`)
failed at its tick limit without edits or damage and restored the lab. It used
native source `57983c65baa4cd27d66aa9063624160c92768006f94f340f6cf309b221fb0622`.
The background forecast had upper exit headroom, but the fresh near sample did
not include it, so execution correctly refused the stale preconditions.

Nonlegacy acquisition now supplements the existing near box with a narrow 5x5
vertical spine up to eight blocks above/below the body, clipped to the requested
awareness envelope. This adds at most 200 fresh cells (2801 total near cells) and
preserves visibility filtering and available-chunk limits. Extra scanning skips
these cells rather than duplicating them. Native tests verify shape size, bounds,
small requested envelopes and the unchanged 2601-cell legacy shape; no control
age, duration, expiry or watchdog was changed. The transport/admission ceiling
accounts for the additional bounded cells.

The pillar checkpoint used native source
`288427ff6073fd843de82a195a0026d6995cf22a6e42ef27652f10e764ed3986`,
jar `4d205ec26ffe91bc6890ed60da86b55572ed936df316a9c080b4a2a65cff49a4`.
Native geometry, awareness and terrain-palette build checks passed. Source-bound
legacy requalification remains required before scored admission.

The fresh two-height smoke passed and restored the lab:
`results/hills/20261027T233315784255Z/summary.json`.
The full cohort (`results/hills/20261027T233347579294Z/summary.json`) then passed
all ten combinations of approach distance and ledge height. Every case used
exactly its required two/three underfoot blocks and stock debits, reached the
upper deck, took zero damage and stayed at or above feet height 80.0. Restoration
passed with zero invalid attempts, controller errors or timing errors. Motor p95
was 3.19 ms; decode-plus-motor p95 was 10.26 ms. Native sampling p95 was 1.44 ms;
that sampling figure still excludes some snapshot/serialization work and does
not establish the full acquisition target.

The matching disabled-pillar cohort
(`results/hills/20261027T233613505483Z/summary.json`) verified ten safe refusals
with ordinary placement still enabled and only `allow_pillar` disabled. Every
attempt had zero damage, block changes and adjusted stock spending. Python,
native and runner hashes match the enabled cohort. Restoration passed with zero
invalid attempts, controller errors or timing errors; motor p95 was 1.68 ms and
decode-plus-motor p95 was 8.72 ms. An independent state check confirmed idle/no
body afterward. The 547-test receipt is
`docs/evidence/2026-10-27-awareness-navigation/movement-pillar-tests.xml`.

## Gap jump development evidence

The gap fixture provides ten combinations of one/two-block trenches and approach
distance. It prohibits edits. Arrival must include one confirmed gap crossing,
native maximum feet height at least 80.9, minimum feet height at least 79.9,
zero damage and zero block/stock changes. The native maximum-height diagnostic
records running game ticks; a walk into the trench cannot satisfy this audit.

The installed native source is
`045fae7a8c5fec661ba3a49c9d4f5777e081b29c8379bbe487c37abd043c48f1`,
jar `839f40fcfaf0369df2dc28491975277d11af4c0ad6c24fb6c08bb953dc40fa45`.
Its geometry, awareness and terrain-palette build checks passed. Legacy scored
admission remains closed pending final source-bound requalification.

The initial probe (`results/hills/20261027T234812963327Z/summary.json`) failed
at its tick limit with no takeoff, damage or edits, then restored the lab. Fixed
positioning speed made it oscillate around the run-up point under the unchanged
three-tick delay. Final positioning now slows proportionally; a regression uses
the recorded offset. No deadline, control age or expiry was relaxed.

The revised probe (`results/hills/20261027T235104157280Z/summary.json`) passed
both gap widths, took zero damage and restored the lab. Native feet height stayed
at or above 80.0 and peaked at 81.2522; the attempts took 129 and 130 game ticks.
This is a smoke result, not the required ten-layout acceptance cohort.

The full gap cohort (`results/hills/20261027T235211585208Z/summary.json`) passed
all ten width/approach combinations. Every case confirmed one gap crossing,
had a native jump peak above 80.9, never fell below 80.0, and had zero damage,
edits or stock spending. Restoration passed with zero invalid attempts,
controller errors or timing errors. Motor p95 was 3.52 ms; decode-plus-motor p95
was 10.60 ms. These are source-bound level-gap development results; changed
landings/headroom, adverse recovery and final integrated qualification remain.

The matching gap-disabled cohort
(`results/hills/20261027T235407043582Z/summary.json`) verified ten safe refusals
with zero damage, edits or stock spending. Python, native and runner hashes
match the enabled cohort; only the jump limit changed. Restoration passed with
zero invalid attempts, controller errors or timing errors. Motor p95 was 2.21 ms;
decode-plus-motor p95 was 9.38 ms. An independent state check confirmed idle/no
body afterward. The 567-test receipt is
`docs/evidence/2026-10-27-awareness-navigation/movement-gap-tests.xml`.

## Remaining work

The acceptance tracker still governs completion. Dedicated live diagonal/sprint
use cases, broader adverse drops/shape cases, water, ladders, adverse gap jumps,
adverse construction/recovery cases remain open. So do byte-budgeted
transport deltas/resynchronization, client-envelope calibration, automatic coarse
routing, larger-world memory/performance measurements, frozen natural/night
comparisons, investigation of the retained server stall and final legacy
requalification. The numerical comparison tool is
test infrastructure, not a passing gameplay result.
