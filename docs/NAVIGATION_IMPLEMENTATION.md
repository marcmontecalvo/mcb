# Navigation implementation boundary

Generator preparation is complete; native C2–C5 execution is not yet qualified.
See `G5_GENERATOR_PREPARATION.json` for source hashes and the versioned layout plan.
The frozen C1 specification and active world were not changed.

The next implementation must address these dependencies together:

1. Add a separately named navigation body/reset profile. `CourseReset` currently
   accepts C1 with zero rotation; `BridgeRuntime` admits only the initial C1
   profile and verifies zero rotation. Keep the existing profile available.
2. Apply specification yaw/pitch through native `CarpetBodyAdapter` creation and
   reset, which currently initialize both to zero. Verify generated initial
   headings, fresh identities, terrain and body fingerprints per profile.
3. Extend Python admission explicitly. `gates.require_initial_profile`,
   deterministic execution and model execution bind C1, 600 ticks and its reviewed
   specification. Introduce profile-scoped qualification rather than widening the
   passed C1 gate silently.
4. Implement a navigation motor with local map memory, obstacle handling and
   qualified jumping/elevation. The current tactical candidate set has no jump,
   and its conservative flat-ground guard intentionally stops at crests.
5. Rebuild and regress the native mod after any Java change. Its source hash is
   bound to G2; rerun mechanics/protocol/sensor/rule/reset evidence before scored
   navigation. `CourseEpisode` already parameterizes bounds, targets and deadlines
   and can reuse native oak-log/drop/pickup scoring.
6. Run five distinct smoke layouts, then 20 paired development layouts per viable
   course/configuration toward G5. Record every outcome and timing profile. Freeze
   fresh holdouts only after development configurations and budgets are fixed.

The Baritone reference still needs a dedicated Fabric client bridge, an
authenticated client session and live tests. Its pinned artifact alone is not
an implemented baseline. Camera/vision experiments and synchronized learned-policy
demonstrations remain separate later work in `EXPERIMENT_QUEUE.md`.
