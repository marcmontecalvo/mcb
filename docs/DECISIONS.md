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
is process management, not the future gameplay transport. It must not become a
per-motor-tick RCON or console control path.

The Java verifier reads its specification before simulation, checks 2048 blocks
per END_SERVER_TICK after warmup, and sends immutable value records to a bounded
off-thread writer. The verifier checks block identity across the complete generated
clear envelope. It does not qualify fluids, scheduled ticks, block entities, body
state, restrictions, drops, or scored reset determinism. Launch IDs reject stale
evidence from earlier server processes. Human presence and human inspection are
separate facts.

Course manifests remain evaluator-only. There is no policy input pipeline yet.
Unknown cells, ray casting and permitted observation construction are future body
milestones. Do not expose these manifests to decision backends.

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

The deterministic perception-goal C1 controller is implemented; G3 requires a
reviewed ten-attempt run with at least nine successes. Provider Choice/chat
adapters have bounded requests/deadlines, zero retries and sanitized exchanges;
real readiness is unproven without configured endpoints. Upstream jevtests files
are reference-only (no root license found); no implementation was copied.
Baritone v1.20.0 artifact/API is pinned, without a client bridge or live trial.

Development planning counts physical layouts rather than seeds: C2 has at least
25 unique variants, C3 only three and C4 one; C5 is not generated. Those later
courses remain unqualified. Offline exports contain actual filtered observations
with pending independent action labels. They are not synchronized demonstrations;
training and fresh holdout evaluation remain pending. No paid traffic has run.
