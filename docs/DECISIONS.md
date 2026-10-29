# Implementation decisions

## Bootstrap boundary

The handoff orders milestones and requires G0 (pinned server starts, human joins,
generated C1 loads) before body qualification. This increment establishes the
toolchain, desktop lifecycle, course compiler, and authoritative bootstrap geometry
evidence. Human inspection has passed; no later gate is inferred from a build.

## Dependencies

The 26.3 server was resolved through Mojang's version manifest, checked against its
published SHA-1 and additionally pinned by SHA-256. Datapack format 121.0 comes from
the downloaded server's `version.json`. Fabric API and Loom fixed artifacts were
resolved from Fabric Maven metadata; the handoff's Loader and Carpet pins were kept.
Carpet's published asset SHA-256 matches the handoff. A Windows x64 Temurin JDK 25
ZIP is installed under `runtime/toolchains`; no machine-wide PATH changes are made.

The Fabric example-mod 26.3 build was consulted for the unobfuscated dependency
configuration. The Java verifier is original code using public Fabric tick/lifecycle
events and Minecraft APIs. No Carpet internals, jevtests code, or other baseline
source has been copied. Downloaded runtime jars are ignored, not redistributed.
Gradle wrapper files retain their Apache license headers.

Primary references:

- https://fabricmc.net/2026/09/15/263.html
- https://github.com/FabricMC/fabric-example-mod/tree/26.3
- https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3
- https://github.com/gnembon/fabric-carpet/releases/tag/v26.3
- https://meta.fabricmc.net/
- https://piston-meta.mojang.com/mc/game/version_manifest_v2.json
- https://api.adoptium.net/

## Process and evidence

Python starts a background supervisor with a hidden Windows console. It owns Java's
stdin and provides only status/stop over a private bearer-token loopback HTTP
endpoint. Tokens stay in ignored runtime files and never appear in reports. This
is process management, separate from the implemented authenticated gameplay
bridge on port 8765. It must not become a
per-motor-tick RCON or console control path.

The Java verifier reads its specification before simulation, checks 2048 blocks
per END_SERVER_TICK after warmup, and sends immutable value records to a bounded
off-thread writer. The verifier checks block identity across the complete generated
clear envelope. It does not qualify fluids, scheduled ticks, block entities, body
state, restrictions, drops, or scored reset determinism. Launch IDs reject stale
evidence from earlier server processes. Human presence and human inspection are
separate facts.

Course manifests remain evaluator-only. The G2-qualified local-visibility-v1
pipeline exposes occlusion-tested observed geometry and body state; unknown cells
remain unknown. Tactical inputs derive solely from this permitted policy stream.
Do not expose evaluator manifests to decision backends.

## Qualified scope and next gates

G1 is passed for `initial-c1-survival-v1`, frozen in
`config/initial_body_profile.json` and reviewed in `G1_CAPABILITY_REVIEW.json`.
The handoff permits leaving capabilities unqualified. The initial C1 profile
covers movement, aiming, mining and pickup, supported by completed human
walk/jump, strafe/release, terrain, sprint and view references plus native
reach predicates and the live cancellation/death/replacement suite. Entity
attacks, combat knockback, construction and dimension travel are excluded from
this initial policy profile; their development evidence does not imply scored
qualification. Callers must use `gates.require_initial_profile`, which rejects
profile/runtime-pin drift and unsupported courses or capabilities.

Incomplete/operator-mistake human captures were removed at Marc's request.
Implementation failures remain diagnostic development history. The completed
references retain their recording hashes and client/server timing limitations.

The G2 reset foundation now reconstructs and verifies the full C1 envelope,
creates fresh generations/identities/bodies, warms the body and checks a second
terrain scan with a stable physical fingerprint. Twenty consecutive resets
matched. JSONL plus SQLite record each attempt. Native clock/weather setters
make environment normalization idempotent in 26.3; `time set noon` is not an
idempotent command when the clock is already at that marker.

G2 passed for the frozen initial C1 scope: twenty matching authoritative resets,
29 adversarial/sensor checks, native causal item scoring and durable episode
recording. Policy admission opens only after explicit begin revalidation. G2
qualification binds the evaluator source hash and sensor profile; drift blocks
scored execution. Native pickup observation runs after Inventory.add but before
Player.take because native playerTouch restores the discarded stack count before
returning. Early implementation failures remain recorded.

G3 passed: the final deterministic perception-goal C1 profile completed ten of ten
attempts. Its causal chains, complete policy ticks and source/configuration hashes
were audited; earlier failed configurations remain in the report. Provider Choice/chat
adapters have bounded requests/deadlines, zero retries and sanitized exchanges;
Atom Qwen chat and hosted OpenRouter Jev have passed real probes and C1 live
development attempts (see G4_LIVE_INTEGRATION.md). Local SystemOne and direct-scoring
candidates have now been screened and retained as failed tactical baselines.
G4 remains open for broader actual-observation and controlled native model trials.
Upstream jevtests files
are reference-only (no root license found); no implementation was copied.
Baritone v1.20.0 artifact/API is pinned, without a client bridge or live trial.

Development planning hashes geometry, spawn, facing, target and inventory rather
than treating seed metadata as diversity. The versioned navigation plan now has
25 variants per C2–C5; C3–C5 each have 25 distinct physical geometries. The earlier
frozen plan is retained. C2–C5 reset qualification and the frozen V8 deterministic
development cohort passed; model/learned-policy qualification remains separate.
Offline exports contain actual filtered observations
with pending independent action labels. They are not synchronized demonstrations;
training and fresh holdout evaluation remain pending. The authorized hosted Jev
experiment made 23 paid calls with settled usage of $0.000637602; the verified
wallet/key cap is $1. Those are historical initial-call costs. Later screening and
a follow-up C1 trial brought authenticated settled usage to $0.004860996, as
recorded in G4_JEV_CURRENT_STACK_PROOF.json. See EXPERIMENT_QUEUE.md for current work.

The broader synthetic useful-action screen contains 48 independently reviewed
states in three option orders. Jev completed 144 cases with 141 correct, one wrong
choice and two rejected probability sums. Atom was stopped after a shared-load
deadline; it remains a partial comparison. Marc reports concurrent Hermes agents
heavily use that service. Preserve the initial runner's admission failures as
history; the corrected runner stops rather than consuming unadmitted cases.
No native sensing, construction, calibration or full G4 pass follows from this
screen. See G4_SCREENING.md and G4_SCREENING_PROOF.json.

Synchronous Python audit serialization interrupted motor renewal and exposed
stale-action failures. Those scored batches are preserved. The event writer now
uses a bounded queue and its own SQLite connection; critical write/flush failure
invalidates the attempt before success finalization. A start event is not the
publication of the running snapshot, so the runner waits for both. Stale controls
are discarded, followed by release and a fresh observation/decision, at most three
times per episode. No action age, duration, physics or course budget was relaxed.

## Optional tandem experiment boundary

Tandem planner/tactical models are a hypothesis to test against single-model
alternatives. `tandem.py` adds independent asynchronous roles, bounded
Choice-planner strategy context, expiry/replan revision checks and stale tactical
reply rejection. `models live-smoke` remains single-model unless a second backend
and planner profile are explicitly supplied. Per-role request caps and combined
conservative remote cost are checked before world access; both roles' provenance
and request totals survive shutdown failures. A configured degraded planner policy
is recorded separately from default fail-on-planner-error behavior.

The local HTTP concurrency check uses fake model responses, and the full Python
suite passes 274 tests. No real two-model Minecraft run or winning architecture
is claimed. Free-form strategic plans, pickup interruption/resumption, later-course
model navigation and combat require their own work and evidence. The original
technical handoff and native body/physics profiles remain unchanged. See
TANDEM_HARNESS_PROOF.json and HYBRID_ARCHITECTURE.md.

## Navigation direction (October 28, 2026)

Marc approved a new navigation direction after a code and docs review:
deterministic, tick-costed route search over observed and remembered geometry;
optimistic planning through unknown space at a penalty, with the motor still
refusing unobserved cells; walking, edits and movement macros competing in one
search; interaction goals as reachable-cell sets; survival hazards as cost terms;
and Jev setting goals, cost profiles and tactical interrupts instead of choosing
waypoints. Server-side Carpet bodies remain primary. Baritone is the reference,
and becomes a candidate motor only if MCB still loses at matched terrain awareness.
Mob avoidance is a weight, not a policy: the bot is meant to play and fight
alongside human players. Details, phases and the list of superseded documents
are in NAVIGATION_DIRECTION_2026-10-28.md.

## Mod split, in-mod movement, Baritone-derived baseline (October 28, 2026)

Marc decided: split the mod into independent `mcb-body` and `mcb-lab` mods so
the lab can evaluate any body; move per-tick movement and reflexes into
`mcb-body`; build the planner core (tick costs, optimistic unknown space, unified
edges) as one flagged phase with ablations. Baritone (LGPL-3.0) may be read and
its move logic and cost values reimplemented as MCB's own code with reference
notes; Baritone source is not copied. Earlier wording that no baseline source was
consulted described the bootstrap increment, not a standing rule. See section 0
of NAVIGATION_DIRECTION_2026-10-28.md.


## Mod split implementation (October 28, 2026)

Step A of the direction document. The lab never spawns or drives a body: every
subject is a named player that may join after the request (600-tick window), so a
Carpet body and a Baritone client go through the same reset, fingerprint and scoring
code. Rules the single mod enforced via shared state were moved to their owner, not
dropped. Course control limits are a body spawn option. Motion before `course_begin`
fails begin verification in the lab. The controller watchdog is split: the body
releases controls, and the lab fails the episode only for courses that opt in
(bridge-driven bodies), because Baritone and human clients are not heartbeated. The
Python bridge keeps the single-bridge interface and fans compound ops out to both mods
so the existing harness did not need rewriting; step B can replace it. Duplicated
small helpers (player reset, interaction observers) are intentional: D9 forbids
shared code paths. Legacy qualified evidence is bound to the old source hash and must
be requalified before scored use.
