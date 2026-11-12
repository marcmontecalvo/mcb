# Bot regression continuation — November 12, 2026

## Scope and evidence rules

This continues the November 11–12 review and regression work. The large house
was excluded. Terra agents reviewed and repaired source changes; one operator
owned the live lab so world resets and fixtures did not overlap. Existing
uncommitted changes were preserved and included in the reviewed work.

The fresh raw evidence and command ledger are under
`results/regression-20261112-terra/`. Earlier rounds in
`results/regression-20261111/` remain historical evidence and are not pooled
with the fresh runs. Per-run source hashes are in the fresh batch provenance
files. A functional pass, an idle-analysis exit code, and complete positional
coverage are separate claims. Sparse intervals must remain visible.

## Findings and repairs

| Finding | Repair and checks |
| --- | --- |
| Site preparation falsely rejected dirt that the server had placed. | Edit overlays now use action-completion ticks. Sensor reconciliation reads actual cell capture provenance, including compact sparse/dense/uniform terrain, rather than the later policy-delivery tick. Session checks prevent old-world samples invalidating current edits. Focused real-ingestion and overlay-expiry tests cover the ordering. |
| Furnace spare work could select known blocks outside its intended radius. | Candidate selection is furnace-centered and radius-limited, with exploration disabled during spare work. |
| An empty work batch permanently disabled the smelting callback, and partial ahead work could lose the remaining gather task. | The callback is retained for later batches. Partial progress reduces the pending task instead of silently dropping it. Tests include a multi-block task whose remaining block lies outside the furnace radius. |
| Furnace placement failed in an excavated one-block-wide staircase, where no supported adjacent air existed at the bot's current level. | The first worksite-return fix reached the top of the crafting table and still scanned the wrong level. The reviewed correction searches known supported air at the table's base level, then uses normal stance navigation. Unknown terrain remains excluded; unreachable placement retains the item and fails. Focused gather/dig tests: 49 passed. Live verification is pending. |
| Roof placements repeatedly attempted an invisible supporting top face, then repeated parked stance searches. | Direct actions now require valid reach, body clearance and visible support. Identical failed stance walks are temporarily reused and invalidated by edits; a failed first candidate still permits a reachable second candidate. Reviewed build/body tests: 98 passed. This does not itself prove the five-second requirement; saved-world replay is pending. |
| Mob clearing could attack a passive bystander or overlook a protected blocker. | Attack candidates must intersect the swept path. Known animal widths are used where bounds are absent. Nearby named or unknown-size entities conservatively suppress clearing, including when their feet are below the route. Retry/push and temporary edge-ban behavior remain. |
| Diagnostic scripts had 66 lint errors, an undefined SQLite cursor, and silently skipped malformed JSON. | Eighteen diagnostics were cleaned up. Map lookup opens an existing database read-only and closes it; malformed JSON is reported. Diagnostics were reviewed without executing them against the active lab. |
| Idle analysis ignored ordinary observations and could report zero violations without positional evidence. | It now selects a canonical stream per run/episode/body/launch, checks active lifecycle boundaries and sampling coverage, preserves same-tick wall-time evidence, and checks overlapping idle windows. Missing coverage returns inconclusive. Completed attack/eat work is bounded separately from unexplained time. Eighteen focused tests and independent reviews cover the corrections. |
| Animal fixture teardown closed the recording after a queued despawn. | The fixture now acknowledges the request, waits for idle and drains confirmed end events before closing. The event-order race was independently reviewed; the older animal recording remains inconclusive. |

The conservative mob guard can suppress an attack because of a protected entity
above or below the route. Exposing authoritative entity bounds would allow a
less conservative decision; the current fallback prioritizes avoiding an
incorrect target.

The earlier Java swim-clearance change (`50a8acf`) also received scoped source
inspection. Its reduced headroom applies only to level samples whose four
swept corners remain unsupported still water; finite support, ascent/descent,
and open-fall samples retain the previous clearance rule. Known collision and
hazard checks remain. The existing synthetic Java test covers the recorded
diagonal geometry, but that is separate from an original-world live replay.

## Earlier failures retained

- Supplied small house `companion/20261112T134722151875Z` failed before
  construction because of stale site-fill observations: 84 blocks missing.
- Accidental empty-supply house `companion/20261112T132940952617Z` is not a
  supplied-house baseline. It exposed the under-leaves swimming rejection fixed
  in `50a8acf`. Generic trapped wading is a different location and does not
  establish that the original edge works live.
- Fresh iron `companion/20261112T140455643180Z` completed the stone-pickaxe plan
  but failed to find a furnace spot. It never smelted, so it does not verify
  useful work during the furnace wait.
- Iron retry `companion/20261112T141516131352Z` also never smelted: the bot
  arrived atop the crafting table and searched at Y72 rather than its base Y71.
  The recording has sparse sampling intervals and is not clean idle evidence.
- Fresh supplied house `companion/20261112T141032699146Z` completed site prep
  but repeated unreachable roof-stair placements. It was stopped after about
  76 steps with 60 placements; eight observed idle violations reached 27.3
  seconds. The forced stop leaves final coverage incomplete, but does not
  invalidate those observed failures. The saved world is
  `results/regression-20261112-terra/failed-house-world`.

## Validation ledger

The full Python suite at `ffe5701` passed **1,325 tests** in 80.60 seconds.
The later furnace-base correction passed its 49 focused gather/dig tests.
Ruff passed across
`src`, `tests`, and `scripts`. A repository-wide Ruff invocation still includes
77 findings in `.qwen/tmp` scratch scripts; those were left intact. Later
runtime or analyzer changes require their own recorded checks.

Commits so far: `594cd94` diagnostic cleanup; `c3ed53c` overlay, furnace-work
and mob recovery; `1d6c463` first furnace-worksite fallback; `ffe5701` reviewed
placement geometry and idle-evidence analysis; `509f1ee` corrected furnace
base-level selection; `972a7e2` confirmed animal-fixture teardown. Failed
attempts above remain evidence; later corrections do not turn them into passes.

Live results and final source/coverage status are recorded in the batch ledger;
the continuation remains in progress until its final results are added here.

## Remaining boundaries

Scored course admission remains subject to G2 for the installed mod hash;
unscored obstacle results must not be represented as scored qualification.
Bubble columns (including soul sand, magma and breathing in bubbles) and bot
chat/voice are roadmap items, not implemented features. Far-field travel in a
kept world, live-terrain per-tick profiling, and process-exit latency remain
separate follow-up work. Python silence diagnostics identify stack locations;
a silence while the native body progresses is not itself a stationary interval.
