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

## G1 completion and next gates

1. Finish the remaining C0 real-player comparisons. The command/API development
   suite passes 39 checks, and human walk/jump and October 24 mining, pickup,
   placement, strafe/release, sprint input, interrupted mining and wall-backed
   attack references are saved. Direct cooldown reset/recovery is recorded. G1
   stays open until remaining reference/capability evidence is reviewed; see
   `G1_GUIDED_MECHANICS_PROOF.json` and `G1_COMBAT_WALL_PROOF.json`.
2. Freeze the qualified initial capability and timing profiles. The adapter,
   bounded tick scheduling and transport are implemented and callback order traced.
   Prepared human collision/step/ledge/fall checks and a 12-check live fake-body
   fixture probe now support the remaining review. Their world edits are bounded
   G1 setup, not an implementation or qualification of scored episode reset.
3. Implement reset generations, authoritative state digests, restrictions,
   causal break/drop/pickup scoring and SQLite/JSONL recording. Pass 20 resets.
4. Deterministic C1, then interchangeable decision backends and baseline clients.

Follow the handoff for remaining operations. `body qualify` and `body reference`
now run actual bounded development experiments. `body protocol-check` adds a
separate 24-check live cancellation/admission proof while the human interaction
reference now covers basic interactions but not all C0 mechanics. It cannot alone
advance G1. Scored `run`, provider probes,
replay, model training and external baselines remain unimplemented.
