# Setup status — October 25, 2026

**G0–G3 passed for the frozen initial C1 scope.** The final deterministic
perception-goal configuration completed **10/10 scored attempts**, each with a
verified causal break/drop/pickup chain and complete per-tick policy recording.
Completion took 128–141 ticks (median 132). Three stale controls were discarded
and regenerated from fresh observations within the declared recovery limit.
Earlier failed/invalid scored configurations remain in the all-attempt report.
G5 passed for deterministic C2–C5 development navigation. G4 remains in progress;
G6–G8 are unqualified. Inference counts are retained per run and configuration;
probe, synthetic-screen, live-gameplay and startup-warmup counts are distinct.
The final compact-JSON tactical configuration completed **3/3 C1 seed0 development
attempts**; all Atom Qwen live configurations total **3/5**. Directory authentication and
chat readiness passed. See [live integration](G4_LIVE_INTEGRATION.md) and
[hash-bound live proof](G4_LIVE_PROOF.json).
Hosted Jev's initial C1 attempt passed in 212 ticks. A separately recorded
post-move C1 attempt passed in 151 ticks with 13 requests; source/ledger, causal
pickup and policy-tick audits passed. The initial and later source configurations
are retained separately. Across all historical live model configurations there
are eight attempts, five successes and 143 gameplay requests; these totals are
not a controlled comparison or qualification cohort. See
[initial Jev evidence](G4_JEV_PROOF.json) and
[follow-up evidence](G4_JEV_CURRENT_STACK_PROOF.json).
The original `Minecraft_AI_Technical_Handoff.md` remains unchanged.

The unauthenticated gpu-docker Ollama service was tested: Qwen3 14B Q4_K_M
returned fast valid live choices (468–593 ms) but failed C1 by repeatedly aiming
at a log that was not visible. Its 0/1 attempt and all44 calls are retained in
[comparison evidence](G4_GPU_DOCKER_PROOF.json). The Jev image/Box experiment
remains deferred in the [experiment queue](EXPERIMENT_QUEUE.md).

The broader [synthetic tactical screen](G4_SCREENING.md) froze 48 independently
reviewed observations under three option orders. Jev completed 141/144 correct,
with one wrong choice and two probability sums rejected. Atom's corrected runner
stopped at 52/54 correct after a deadline; Marc reports heavy concurrent Hermes
usage. Those timings are shared-load measurements, not an isolated ranking.
After the later Jev readiness/gameplay calls, an authenticated follow-up reconciled
settled OpenRouter usage to **$0.004860996**, with **$0.995139004** remaining under
the unchanged $1 key cap. The later readiness/gameplay increment was $0.000378630.

The additional local candidate screens are complete: OpenJev 4B v5 scored 78/144;
Laya English scored 18/144 accepted and correct, with 68 probability-sum rejections
and 29/144 raw choices correct; Qwen3.5-4B BF16 SemIf direct scoring scored 28/144
in each of two separate prompt configurations. All used the one-second deadline;
none were promoted to live gameplay under the tested representation. See
[current screen details](G4_SCREENING.md).

The C1 harness now supports optional tandem operation through separate planner
and tactical backend instances, asynchronous request lanes, versioned strategy
context, role budgets and provenance. The existing single-model path remains
available. A local HTTP test verifies tactical progress while a fake planner route
is held pending; **274 Python tests** and Ruff lint/format pass. The first planner
adapter selects configured strategy choices; unrestricted strategic planning,
later-course tandem gameplay and combat remain pending. Tandem is a hypothesis
to compare, not the chosen winner. See [harness evidence](TANDEM_HARNESS_PROOF.json)
and [candidate architecture](HYBRID_ARCHITECTURE.md).

## Runtime and qualification

The project resumed on D:\mcb after the disk-space interruption. The moved
toolchain passed doctor; Python launchers and installed-mod path metadata were
refreshed. Navigation v4 completed **C2 20/20, C3 20/20, C4 19/20, C5 16/20**;
raw source, timing, causal UUID chains, policy ticks and SQLite outcomes were
audited. C5 missed the per-course criterion. V8's corrected collection/aiming
candidate then passed **80/80** on one frozen execution stack: 20/20 each for
C2, C3, C4 and C5, with persistent HTTP and between-episode GC. The raw audit
passed source/profile binding, three-tick applied ages, five-tick controls,
causal chains, resets/cleanup, complete policy ticks and SQLite agreement.
This qualifies deterministic development navigation, with no model or holdout
claim. See [current evidence](G5_NAVIGATION_PROOF.json),
[baseline evidence](G5_DEFAULT_GC_BASELINE.json)
and [the current autonomous handoff](AUTONOMOUS_RUN.md). The navigation cohort
itself made no provider requests; later model experiments are recorded separately.

The repaired recorder and persistent transport also passed **10/10** frozen C1
regression attempts (127–187 ticks, median 129.5), with complete causal chains,
policy ticks, timing and ledger/source checks. See
[post-move regression](C1_DRIVE_MOVE_REGRESSION.json); the original G3 proof is
retained. C1 was reset afterward, aggregate reports refreshed, and the server
stopped cleanly. Doctor passes with all three loopback ports available.

| Item | Current evidence |
| --- | --- |
| Python | Project-local CPython 3.12.13 and locked uv environment |
| Java | Project-local Temurin 25.0.4.1+1; system Java 21 retained |
| Build | Gradle 9.6.0, Loom 1.17.21; locked dependencies and verification metadata |
| Minecraft | Java 26.3, protocol 777, authenticated mode, Survival |
| Mods | Fabric Loader 0.19.5, API 0.164.0+26.3, pinned Carpet v26.3 |
| Network | Loopback game 127.0.0.1:25575; authenticated bridge 8765 and supervisor 8766 |
| Python checks | 274 tests pass; Ruff lint and formatting pass (53 files including the preserved experiment wrapper) |
| Java checks | Mod build and pure geometry verifier pass |
| G1 live suites | 39 body checks, 24 protocol checks, 12 reference-fixture checks |
| G2 reset foundation | 20 consecutive identical state digests; teardown confirmed |

The server stays active while empty (`pause-when-empty-seconds=-1`). A start
acknowledgement is not readiness: require a real protocol response from
`mclab server status`. Marc authorized this local installation's EULA acceptance
on October 22; no global Java/PATH or public server configuration was changed.

## G0

Marc joined and confirmed the stone platform and log. The initial C1 seed-0
server scan checked 98,441 cells with zero mismatches. Reviewed spec hash:
`8632e6132b76947bc406ac6c5bbee311d1fe569c9b07cd1fdaa8afae93ea9bdf`.
This historical bootstrap scan does not substitute for later reset verification.
Human reference fixtures intentionally modify the development arena.

## G1: qualified initial profile

[The capability review](G1_CAPABILITY_REVIEW.json) binds qualification to
[config/initial_body_profile.json](../config/initial_body_profile.json).
The scope is C1 ground locomotion, aiming, mining and pickup, with the tested
jump/sneak/sprint and terrain behaviors. The profile freezes runtime pins,
acceptance bounds and timing limits. `gates.require_initial_profile` rejects an
open gate, edited profile, changed physics pins, other courses or excluded
capabilities. This is scoped qualification, not full-game fidelity.

Entity attacks, combat knockback, construction and dimension travel remain
outside the initial policy profile. Their supplementary interaction checks remain
development evidence. Death/replacement qualification is the intentional Carpet
lifecycle: terminal disconnect and a fresh registered Survival body, rather than
imitation of a human respawn screen.

Completed human references:

- [Walk/jump and body proof](G1_BODY_PROOF.json): three jump peaks at
  1.252203340253729 blocks; steady 20-tick walking distances about 4.317 blocks.
- [Basic interactions](G1_HUMAN_INTERACTION_PROOF.json): mining 60 ticks versus
  fake 61–62, normal log collection, one dirt consumed per placement and a
  one-damage in-range punch. G2 subsequently qualified causal drop-UUID scoring.
- [Guided mechanics](G1_GUIDED_MECHANICS_PROOF.json): strafe/release and a mining
  abort after 21 ticks followed by a full 60-tick restart.
- [Terrain](G1_HUMAN_TERRAIN_PROOF.json): 1,800 reviewed samples. Collision stopped
  at z=989.6999999880791, step rise was 0.5 without jumping, ledge position settled
  at z=990.2975091220263, and a five-block fall first landed with health 18. These
  match the corresponding fake-player results. Human collision review uses
  position and received input; the human horizontal-collision flag stayed false.
- [Sprint](G1_HUMAN_SPRINT_PROOF.json): complete, flat, grounded 20-tick windows
  with native sprint state and matching inputs, within existing development
  bounds. Same-duration fake observations are included. Overlapping windows are
  not independent trials; starting conditions still require careful comparison.
- [View](G1_HUMAN_VIEW_PROOF.json): stationary body, native degree yaw/pitch and
  pitch extremes -90..90. Native rotation and guard APIs supplement the recorded
  comparisons; a matched mouse-input trajectory is not claimed.
- [Wall-backed combat](G1_COMBAT_WALL_PROOF.json): all three attacks reached the
  cow, damage 1/0/1, attack strength reset to zero and recovered to one. The
  six-tick second hit was fully charged; zero damage is consistent with target
  damage immunity. This is supplementary evidence outside the initial C1 policy.

Incomplete/operator-mistake human captures were removed at Marc's request.
Actual implementation failures remain in development qualification history.
Recordings use server-observed packets/ticks; synchronized client timestamps
and training-ready demonstration alignment are not claimed.

## G2: reset foundation

`mclab course reset-check --count 20` is a bounded development check gated by the
initial C1 profile. It closes policy admission, clears pending controls, retires
the old body, creates new episode/body identities and increments a generation.
Retired asynchronous spawns remain watched and are removed if they arrive late.

The server removes arena-scoped drops/projectiles, normalizes the native 26.3
Overworld clock and weather, and reconstructs the complete 49×41×49 envelope.
Building and each verification pass are limited to 2,048 cells per tick. After
the first terrain verification it spawns a fresh body, overwrites initial state,
waits for registration/settling, warms 20 ticks and verifies terrain again.
The physical fingerprint must remain stable through that second scan.

The fingerprint covers full block states, position/velocity, view, Survival mode,
health/food, saturation/exhaustion/hunger timer, inventory/equipment, effects,
abilities, pose, air/fire/freeze, reach, attack strength and environment state.
Ephemeral names/UUIDs/episode IDs/generations are excluded from the physical
digest and recorded separately. Twenty consecutive resets matched. This is
authoritative state checking, not bit-exact simulation replay.

Safety and scope:

- Humans in the arena, unloaded chunks and unexpected living entities reject the
  reset. A human entering during reset aborts it.
- The foundation supports ordinary generated C1 and a fluid/block-entity-free
  arena. Unsupported state invalidates the attempt; arbitrary scheduled-effect
  or block-entity restoration is not claimed.
- Policy controls stay closed after `reset_ready` until explicit `course_begin`
  revalidates terrain/body state; reset-check itself cannot report scored success.
- Requests/events remain launch/run/episode/body scoped. Runs declare a
  1,500-request reset-check budget; motor age/duration limits are unchanged.
- JSONL and summaries live in `results/qualification/`. SQLite schema v1 in
  `results/reset_checks.sqlite3` records pending/finalized attempts. Pending work
  is never treated as passed or resumed inside a modified world.

G2 passed for initial C1: [29 adversarial and sensor checks](G2_EPISODE_PROOF.json)
verify pre-begin admission, forbidden controls, illegal floor breaking, planted
items, altered target, cancellation, the 600-tick deadline, controller watchdog,
out-of-bounds termination and native visibility fixtures. Those intentionally
failed/invalid attempts remain unscored and visible in the episode ledger.

The server scores native target break -> newly created item UUID -> normal
inventory transfer. `Inventory.add` is observed before `Player.take`, because
Minecraft restores a discarded stack count before `playerTouch` returns. A live
smoke exposed the original incorrect RETURN hook; its failed attempt was retained.
A second integration race between HTTP snapshots and terminal events was fixed
and covered by a regression test, with its invalid attempt also retained.

The recorder uses SQLite schema v1 plus JSONL, complete versioned event payloads,
per-tick body/policy events and periodic observed-map snapshots/deltas. A bounded
2304-record writer queue keeps serialization/disk I/O off the motor loop and
invalidates missing critical recordings before finalization. Pending
attempts after a crash become interrupted. One OS arena lock covers execution,
reset, reference and mechanics runners. Body profile, native mod source, sensor and course-specification drift block scored runs.

## G3: deterministic C1

[The execution proof](G3_EXECUTION_PROOF.json) binds the final ten-attempt run to
its controller hash, source commit, native mod, course specification, sensor and
`c1-three-tick-bounded-resense-v4` timing profile. Every applied action used a
three-tick-old observation, within the existing five-tick bound. All ten item
UUID chains and every running policy tick were audited against JSONL and SQLite.
This is repeatability on one development C1 layout, not unseen-course navigation,
learned control or model skill.

The implementation history includes a 3-success/1-invalid aborted scored batch,
a 4/10 synchronous-recorder batch and an 8/10 asynchronous batch that terminated
on isolated stale-control rejections. Each remains recorded under its exact
configuration/source hash. The final profile discards stale commands, releases
inputs and makes a fresh observation/decision, at most three times per episode.
No timestamp is rewritten and no physics, action-age/duration or course budget
was relaxed. Recording failures invalidate attempts before finalization.

## Later work prepared

Choice/SystemOne and chat backend adapters have deadline, single-outstanding-call,
zero-retry, output-validation, redaction and budget tests. Atom is configured through an ignored `.env` bearer token. Authenticated discovery
returned `qwen3.8-flash-next`, with a route to `host.docker.internal`. A temporary
VPN disconnection interrupted DNS/TCP access; those attempts remain recorded.
After connectivity returned, default thinking consumed the 64-token allowance
without a JSON answer. The explicitly configured nonthinking profile produced
valid readiness JSON in 750 ms, with no reasoning tokens. Two bounded offline
choice checks returned legal `forward` IDs under original/reversed option order
in 828–1000 ms. These are a narrow schema/deadline smoke check, not semantic
accuracy, calibration or Minecraft task performance. Chat probability maps remain
labelled synthetic one-hot values. [The Atom proof](G4_ATOM_PROBE.json) preserves
all eight attempted requests and four valid Choice responses. Live tactical integration subsequently completed three consecutive C1 seed0
attempts after compact JSON reduced output latency. Two earlier live configurations
failed on deadlines and remain recorded. All71 live calls are counted, including
three pending calls cancelled after native success. Hosted Jev subsequently passed
probes and two separately recorded C1 trials. Local SystemOne candidates have now
been screened; G4 remains open for actual-observation screening, controlled
later-course model trials and optional tandem live comparisons.
Upstream source references/hashes are pinned without vendoring unlicensed code.

Baritone v1.20.0 Fabric API artifact SHA-256 was verified against its release
metadata, and supported goal/mine/cancel signatures reviewed. The dedicated client
bridge, login and live scored baseline remain pending. C2 development planning
now has25 distinct layouts per C2–C5; C3–C5 each have25 physical geometries.
The original frozen plan is retained. Native navigation reset qualification and
the frozen V8 deterministic cohort are complete as described above. Broader
model/selector qualification is pending. Thirty-five actual permitted C1
observations were exported with original/reversed/seeded candidate orders and
pending independent labels. No synchronized training demonstrations, trained
checkpoint or fresh holdout result is claimed.
See [decisions](DECISIONS.md), [bridge protocol](BRIDGE_PROTOCOL.md) and
[the gate ledger](gates.json) for extension boundaries.
