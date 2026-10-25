# Autonomous implementation and test run

Marc requested continued implementation/testing in this session, independent
course trials wherever possible, and a final evidence-backed report. Keep this
file and the gate ledger current across context compaction. Do not request another
manual testing pass for work that the native harness can verify itself.

## Current phase

Resumed on D:\mcb on October 25, 2026, with approximately 541 GiB free at startup.
The moved toolchain passes doctor. Python launchers were reinstalled; normal
`uv run pytest` works. Native source/jar hashes remain `622e3361` / `77d33e31`,
and installed-mod provenance now names the D: path.

Qualified baseline: **deterministic-navigation-v8**, with explicit cyclic garbage
collection between episodes and persistent bridge HTTP connections. V7's first
full C5 batch (`20261025T185751674023Z`) stopped after five successes on WinError
10048; that invalid batch is retained. A persistent-connection V7 smoke
(`20261025T194911009219Z`) passed 1/2: seed 24 remembered a falling item just below
a crest and waited on the upper platform. V8 excludes pickup cells above the
observed item. The two difficult seeds 21/24 now pass 2/2 in the live V8 smoke
`20261025T195240674190Z`.
The matched full matrix completed **80/80** in the order **C5, C2, C3, C4**, with
fresh server launches between courses and one frozen production stack.
The raw audit passed every check; G5 is passed for deterministic development
navigation. The ten-attempt C1 recorder/transport regression passed **10/10** at
`results/execution/20261025T204029446655Z/summary.json`, with a complete raw audit in
`C1_DRIVE_MOVE_REGRESSION.json`. Completion took 127–187 ticks (median 129.5).
The original G3 evidence remains intact. A final unscored reset restored C1
(`results/qualification/20261025T204427777771Z/summary.json`), the aggregate reports
were refreshed with all historical failures retained, and the server stopped
cleanly. Doctor passes; game/bridge/supervisor ports are free.

Current V8 cohort (all raw audits passed):

| Course | Run | Native result |
| --- | --- | --- |
| C5 | `20261025T195504621562Z` | 20/20, completed and C1 restored |
| C2 | `20261025T200547002259Z` | 20/20, completed and C1 restored |
| C3 | `20261025T201746203189Z` | 20/20, completed and C1 restored |
| C4 | `20261025T202555626912Z` | 20/20, completed and C1 restored |

V5 challenge `20261025T182405798871Z` passed 3/5: collection fixes resolved
17/19/102, while 21/24 reached the tick limit. Replay found those two repeatedly
aiming at stone although a log face was visible. V6 (`20261025T184100551585Z`)
also passed 3/5: clear-cell planning alone still alternated aim and movement.
V7 checks the line from the actual current eyes before entering mining mode,
allowing repositioning to finish, while trusting a positive current native log
ray. Tests cover the two-heading oscillation and unknown-air handling.

The completed v4/default-GC baseline was **75/80**: C2 20/20, C3 20/20, C4 19/20,
C5 16/20. All raw recordings audited cleanly, but **G5 did not pass**, because
C5 missed 18/20. The complete baseline is retained in
`G5_DEFAULT_GC_BASELINE.json`; the new 80/80 cohort is in
`G5_NAVIGATION_PROOF.json`. Earlier partial proofs remain in Git history.
Old successes cannot be pooled into the new cohort.

Replay isolated a collection failure: after a legitimate log break, an occluded
drop floor caused global exploration away from the item. V5 approaches the
closest reachable known support toward the observed drop, respects compatible
item/body height, and stays at a valid pickup goal. Cyclic GC is disabled from
begin admission through terminal motor handling; reference counting remains on,
the caller's GC state is restored on every exit, and explicit collection/metrics
run between episodes. Late-admission errors are now tagged timing rather than
decision. No physics, action-age/duration, episode limit or native source changed.

G5 passed on one source-matched new cohort with 80/80 successes. All four courses
have 20 distinct layouts, complete policy ticks, causal chains and matching ledger
outcomes; all applied controls have observation age 3 and duration 5.
C4 discarded and regenerated one stale action within the existing recovery bound;
no timing, physics, episode or success criterion was relaxed.
Latest checks: **255 tests pass**, Ruff lint/format pass. A loaded full run
had one backend mock HTTP-404 assertion exceed its 0.1-second deadline;
the complete backend module passed 27/27 in isolation, then the full suite passed.
No provider or Atom calls were made during resumed navigation work. Keep holdouts
closed.

## Drive-move recovery and controller changes

- Fixed qualification field `resets` to `reset_checks_passed` after verifying
  four saved hashes, 20 matching resets per course and C1 cleanup.
- Preserved the historical 14 GB ledger. New navigation runs use per-run SQLite
  files; reports aggregate both formats and startup recovers interrupted work.
- New ledgers use WAL. The writer commits up to 128 records per batch with a
  bounded 2304-record burst queue. Payload JSON is encoded once for both stores.
  Write/flush/commit failures still invalidate work before finalization.
- Flush before releasing per-episode event/sample buffers. The old batch grew
  from 1.3 to 2.5 GB; the repaired runner was near 235 MB after ten attempts.
  Failed flushes preserve the old identity and transient history.
- Navigation commits routes, rechecks fresh support and distinguishes unknown
  support, observed void and one-block descents. V4 looks ahead up to three
  collinear level nodes and uses qualified normal forward speed 1. Corners and
  jumps retain short waypoints; stale distant support falls back to nearer nodes.
  Physics, action-age/duration and episode budgets are unchanged.
- Native visibility is 360-degree with occlusion, not a view cone. The speculative
  heading-only reacquisition change was removed. Replay instead exposed slow
  perimeter traversal: a log could be discovered with too few ticks left to mine.
- `python -m mclab.navigationproof ... --output ...` audits source snapshots,
  native reset/body state, causal chains, timing, per-tick recordings, SQLite
  counts/outcomes and physical layout diversity. It never updates gates itself.

## Retained development history

These are separate source/harness configurations, not pooled into the v4 result.
Invalid/stopped batches remain in the all-attempt report and raw recordings.

| Configuration / run | Reported outcome |
| --- | --- |
| Original G: disk exhaustion | C2 7/9, C3 14/15, C4 0/1; invalid batches, no qualification |
| Initial D: retries | Recorder queue/SQLite failures; invalid and retained |
| `20261025T132833793890Z` | 8 successes, 5 native failures, 1 invalid during deliberate stop |
| `20261025T134038807731Z` | C2 12/20; complete recording and cleanup |
| V2 `20261025T140057076305Z` | Challenge 1/2; seed 14 stale admissions |
| V2 `20261025T140707121590Z` | Challenge 3/5; seeds 14/16 timeout |
| V3 `20261025T141601682611Z` | Challenge 3/5; perimeter traversal too slow |
| V4 `20261025T142434699785Z` | Challenge 5/5 in 588–723 ticks |
| V4 `20261025T142953045663Z` | C2 20/20; raw audit passed |
| V4 `20261025T144644506665Z` | C3 smoke 5/5 |
| V4 `20261025T144911174072Z` | C3 20/20; raw audit passed |
| V4 `20261025T150245045278Z` | C4 smoke 4/5; seed 105 controller timing failure |
| V4 `20261025T151009629160Z` | C4 19/20; raw audit passed; seed 24 watchdog failure |
| V4 `20261025T153322545700Z` | C5 smoke 4/5; seed 102 controller timing failure |
| V4 `20261025T154037476012Z` | C5 16/20; seeds 17/19/21/24 late-admission failures |

The native foundation predates the move: mechanics 39/39, protocol 24/24,
reference fixtures 12/12, C1 reset 20/20, adversarial/sensor 29/29 and C1
execution 10/10. C2–C5 seed-0 reset qualification passed 20/20 each.
The original C1 specification/profile stay frozen; earlier smoke configurations
and all failures remain diagnostic history.

## Completed checkpoint and remaining work

1. V8 completed seeds 5–24 for C5/C2/C3/C4 on one frozen stack, with fresh launches
   between courses and verified C1 cleanup. All 80 native episodes and raw audits
   passed. G5 is passed only for deterministic development navigation.
2. C1 passed its ten-attempt regression and raw audit. Final reset, all-attempt
   reports and doctor are complete. The server is stopped. Java source is
   unchanged; native mechanics/reset suites need renewal if source/pins change.
3. G4 remains partial: the Atom screen awaits a quiet window and the separate
   local SystemOne candidate lacks an endpoint. Preserve all earlier provider
   outcomes, budgets and shared-load limitations.
4. Baritone still needs its dedicated Fabric client bridge, an authenticated
   client session and live qualification. Synchronized human demonstrations,
   learned-policy training/evaluation, construction and holdouts remain later
   work. Native navigation does not need another manual inspection.

For repeat navigation batches, never exceed 32 retained body identities. A fresh
20-attempt batch uses 21 including cleanup; a five-attempt smoke plus a full batch
uses 27. Wait for real protocol readiness after a restart, and admit only one
arena command at a time. Do not pool smoke/historical configurations into a
qualified denominator or consume fresh holdouts during development.

## Standing constraints

- G0–G3 evidence is historical and scoped; source/profile drift blocks scoring.
  Do not widen the initial profile or mark a later gate passed by preparation.
- Atom is heavily shared with Hermes agents per Marc. Do not benchmark it as an
  isolated service, retry an overloaded batch repeatedly, unload its model or
  change its service. Finish the partial screen in a quiet window later.
- OpenRouter has a verified $1 key/wallet cap. Last settled usage: $0.004482366.
  Keep explicit provider/request/cost limits and record every charged request.
- gpu-docker Qwen3 failed its existing tactical profile; preserve that baseline.
  Jev image/Box work remains a later, separate camera experiment.
- The original handoff is unchanged. Local commits are authorized; no deployment
  or publishing is part of this run. Preserve user environment/configuration edits.

The final report must distinguish passed, failed, incomplete and externally
blocked work. An authenticated baseline client session or synchronized human
demonstrations may require user input if no usable existing source is available;
do all independent work before returning such a blocker. Never substitute
synthetic/agent data for required human demonstrations without saying so.
