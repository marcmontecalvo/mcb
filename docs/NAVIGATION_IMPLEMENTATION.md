# Navigation implementation boundary

Updated October 25, 2026. The native C2–C5 reset profile and deterministic
navigation runner are implemented. Reset qualification passed 20 matching
seed-0 resets per course; see `NAVIGATION_QUALIFICATION.json`. That evidence
does not qualify navigation performance or pass G5. The frozen C1 specification
and its separate admission scope remain in place.

Implemented boundaries:

1. `navigation-c2-c5-survival-v1` inherits the frozen C1 physics/capabilities.
   Generated specifications and headings enter the authenticated administrative
   reset interface; they never enter controller observations.
2. Native reset verifies fresh identities, terrain, body state and headings.
   Separate source/profile-bound admission checks require 20 matching resets
   before scored navigation. C1 admission remains unchanged.
3. `navigation.py` builds memory from permitted local visibility, searches known
   walkable cells, explores frontiers, jumps one-block rises, mines and collects.
   Immediate controls revalidate geometry against the five-tick freshness bound.
4. `navigation_runner.py` records specifications, source snapshots, controller
   hashes, every attempted episode and cleanup to the original C1 arena. Native
   causal break/drop/pickup scoring remains authoritative.
5. The installed native source `622e3361` passed the C1 mechanics, protocol,
   reference-fixture, reset, adversarial/sensor and 10/10 execution regressions.
   Any subsequent Java change still requires source-bound requalification.

The pre-move four-course smoke completed 19/20; C4/105 timed out. Subsequent
development batches hit disk exhaustion and cannot qualify G5. The repaired
recorder now has isolated navigation ledgers, bounded batched writes and flushed
per-episode transient history; all old outcomes remain in the aggregate report.

The motor commits routes and looks ahead on straight level paths, using qualified
walking speed 1. Corners/height changes retain short waypoints; fresh geometry
remains mandatory. It plans clear mining viewpoints from observed cells and checks
the actual eye-to-log ray before interrupting repositioning. Pickup routes reject
platforms above a falling item and approach known support to reveal occluded floors.
Cyclic GC runs between episodes; the authenticated HTTP transport reuses sockets.

The complete v4/default-GC baseline was C2 20/20, C3 20/20, C4 19/20, C5 16/20.
Its [raw audit](G5_DEFAULT_GC_BASELINE.json) passed, but C5 missed the per-course
criterion. V7's five-case challenge passed, then a full batch stopped on socket
exhaustion. With persistent HTTP, another V7 smoke exposed the falling-item height
bug (1/2). V8's corrected difficult-layout smoke passed 2/2. The subsequent frozen
matrix passed **80/80**, with 20 distinct development layouts per course:

| Course | Successes | Completion ticks, min / median / max |
| --- | --- | --- |
| C2 | 20/20 | 126 / 476 / 702 |
| C3 | 20/20 | 145 / 196.5 / 328 |
| C4 | 20/20 | 169 / 418 / 991 |
| C5 | 20/20 | 180 / 388 / 744 |

The [current raw audit](G5_NAVIGATION_PROOF.json) passed every check: native causal
chains and terminals, complete policy-tick coverage, three-tick applied observation
age and five-tick controls, fresh identities and verified resets/cleanup, source
snapshots and SQLite agreement. Controller, execution stack, generator, native
source, profiles and timing match across all four batches. G5's deterministic
development-navigation criterion is passed. Old successes are not pooled into
this cohort. No model, learned-policy or holdout result is implied.
C4 regenerated one discarded stale action within the existing bound; every
applied control still met the unchanged timing profile. The shared recorder and
transport also passed [10/10 C1 regression attempts](C1_DRIVE_MOVE_REGRESSION.json).
See `AUTONOMOUS_RUN.md` for the run ledger. Holdouts remain unopened.

The Baritone reference runs through a dedicated Fabric client bridge
(`client-bridge/`) in a Prism-authenticated session; a live join and goto/cancel
probe passed. Server-scored reference results are in BARITONE_REFERENCE.md. Camera/vision experiments and synchronized learned-policy
demonstrations remain separate later work in `EXPERIMENT_QUEUE.md`.
