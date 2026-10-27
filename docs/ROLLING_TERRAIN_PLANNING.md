# Rolling multi-move terrain planning

October 27, 2026. The full sprint is active; acceptance is tracked in
[SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json). This replaces the earlier
single-step terrain chooser.

The terrain planner forecasts **four moves** at a time. Each move declares its
standing position, blocks to break and blocks to place. A bounded beam explores
walking, cutting and placement together on a private predicted overlay, tracks
remaining stock and edit budgets across the full sequence, and protects footholds
from being immediately mined away. The real observed map remains unchanged.

Known walking search remains the first choice. When a walking segment approaches
an unresolved obstacle, the terrain horizon starts before the final step into
that obstacle. A complete horizon normally has four moves. A shorter sequence
is accepted when it reaches the goal inside that horizon. Insufficient known
geometry produces an explicit incomplete result rather than invented support.

Execution retains the forecast's subsequent moves. Every queued step is checked
against fresh actual support, clearance, inventory and permitted edits before it
starts. Changed preconditions discard the unissued remainder and trigger a new
plan. Inputs already issued retain their resource reservation; reservations for
unissued replacement steps can be released. Each actual edit is confirmed before
the corresponding movement commitment. `terrain_route_planned` events and
controller status expose the forecast, including future edit coordinates.

The native mixed fixture combines a low-headroom stone rise with a protected
upper ledge. The candidate passed ten distinct layouts using both cuts and placed
footholds. Raw cohort: `results/hills/20261027T114953140342Z/summary.json`.
The fixture's changed blocks, server pickup-adjusted stock count and controller
per-placement confirmations must agree. Net inventory change alone is inadequate:
mining stone supplies cobblestone while placements consume it.

Placement confirmation now accounts for cumulative per-material pickups in the
natural development body observation. A pickup can refill the selected stack
between an actual placement and the next geometry refresh. Confirmation requires
the expected block and a one-item debit after those transfers are accounted for.
Older observations without the ledger may establish the debit before geometry
refresh, but a block alone never establishes a placement. Issued placement cells
remain protected during recovery, including when confirmation fails. The native
inventory-only counter is absent from qualified legacy course observations.

Focused tests verify a four-move sequence with a cut on move one, placement on
move two and walking on moves three/four; cross-move stock/reserve checks;
unknown-support rejection; preservation of later moves after an initial walk;
and confirmation of admitted cuts that occlude one another.

The development natural entrant now runs walking and terrain search in a bounded
background lane. It prefetches a walking segment while following the current
segment and adopts that result at its matching endpoint. Goal replacement and
cancellation invalidate pending results. Fresh motor checks still govern every
movement. The foreground option remains available for development comparison.

Ten background mixed layouts passed in
`results/hills/20261027T125058049515Z/summary.json`. A 40-block flat case adopted
a prefetched segment and reached its goal in 202 game ticks, with motor p95
17.52 ms (`results/hills/20261027T124750772423Z/summary.json`). Mixed-layout motor
p95 was 27.19–33.20 ms, above the 20 ms target. A compressed archive increment
subsequently passed one mixed layout, but its motor p95 remained 28.23 ms
(`results/hills/20261027T125435671683Z/summary.json`). These are separate frozen
development stages, not a full performance pass. Coarse routing, broader
traversal, bridging/pillaring and natural-terrain acceptance remain unfinished.

The installed server source includes the new fixture, pickup accounting and
lethal-hit reporting changes. Earlier source-bound qualification proofs remain
historical; scored admission rejects the changed source until requalification.

The later admission-worker/transfer-accounting candidate passed ten mixed layouts
(`results/hills/20261027T132148920054Z/summary.json`) and ten longer layouts with
prefetch adoption (`results/hills/20261027T132526447497Z/summary.json`). Aggregate
motor p95 was 22.03 ms for mixed hills and 3.30 ms for longer flat routes. Mixed
performance therefore still misses the 20 ms target. The respective
`performance.json` files retain acquisition, rolling MSPT, HTTP and recorded
payload statistics plus their measurement limits. Native sampling p95 was
1.55/0.97 ms; this excludes snapshot serialization. These cases and 392 passing
Python tests are recorded in [BACKGROUND_NAVIGATION_PROOF.json](BACKGROUND_NAVIGATION_PROOF.json).

[Stage evidence](ROLLING_TERRAIN_PROOF.json) includes the ten mixed cases,
four-move forecast records and 360 passing Python tests. A separate native
single-lethal-hit test now records all 20 HP lost, before Carpet's death handling
can reset/remove the fake player. Mixed and reporting evidence retain their
individual mod-source identities; this is not a final frozen sprint candidate.
