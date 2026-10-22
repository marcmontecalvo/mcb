# Minecraft AI Test Lab — Coding Agent Handoff

Date: October 22, 2026  
Owner: Marc Montecalvo  
Status: Ready for incremental implementation. Source-reviewed; not runtime-tested.  
Target: Minecraft Java 26.3, Fabric, Carpet, Windows desktop / VS Code.

## 1. Assignment

Build a reproducible Minecraft agent laboratory: generate short courses, reset them exactly, run existing baselines and our own controller, and collect evidence that identifies which approaches deserve further investment.

The eventual goal is a locally hosted, trainable embodied player with spatial awareness, learned navigation/mining/building/combat, slower strategic reasoning, fast tactical decisions, and optional deterministic tools. A model choosing scripts is a useful baseline, but does not prove learned motor competence.

Implement the milestones in order. Continue when a gate passes. If it fails, preserve evidence and diagnose the failing layer before adding complexity. Timebox each external baseline integration to one working day.

Scope: implementation, local builds, generated courses, local tests, and configured smoke experiments. Paid batches require an explicitly configured provider, episode/request limits and cost ceiling; do not start unlimited cloud sweeps. Missing credentials skip that backend while local work continues. Do not download the entire Jev model registry.

## 2. Fixed decisions and environment

| Area | Decision |
|---|---|
| Main version | Minecraft Java Edition 26.3 |
| Initial location | Dedicated server and harness on Marc's desktop; VS Code; no dependency on Crafty |
| Server body | Carpet fake player behind our small Fabric adapter |
| Course building | Declarative JSON/YAML generates datapack functions; no manual building |
| Runner | Python 3.12 + uv; Java mod; optional Node Mineflayer worker |
| Model integration | Reuse selected interfaces/implementations from marcmontecalvo/jevtests |
| Primary external baseline | Baritone on a controlled 26.3 client |
| Other baselines | Mineflayer only if compatibility probe passes; mc_aiplayer separately on 1.21.3 |
| Results | SQLite summaries + JSONL event streams; generated reports |
| Later migration | Server/harness move through configuration and launch profiles |
| Later inference choices | RTX 5060 Ti 16 GB, DGX Spark, 24 GB MacBook Pro |

The desktop screenshot shows 64 GB RAM, about 25.7 GB available at capture, and an RTX 3070 8 GB. Resource inventory is not a blocker. Start with a configurable 4 GB server heap and tune from MSPT/TPS. Keep one scored episode active at a time. Label shared-hardware timing separately from isolated inference timing.

Read-only checks found Git, uv, Node v26.10.0, and Corretto Java 21.0.12.1 on PATH. Python 3.12.13 runs at:
C:\Users\marcm\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe

The WindowsApps Python alias failed in this session. uv discovery encountered sandbox cache permissions; Python is present. Use a writable project cache and actual interpreter. Install/select a project-specific JDK 25; retain JDK 21 for older baseline environments. Do not globally replace working Java installations.

## 3. Verified dependency starting points

These are source/documentation findings, not passing runtime tests.

| Component | Pin / action |
|---|---|
| Minecraft | 26.3 |
| Fabric Loader | 0.19.5 |
| JDK | 25; Carpet metadata requires >=25 |
| Loom / Gradle | Fabric recommends 1.17 line / 9.6.0; resolve an actual fixed Loom artifact and commit Gradle wrapper |
| Fabric API | Resolve and pin exact 26.3-compatible artifact at bootstrap; do not invent a version |
| Carpet | v26.3; commit c6d48ab48551f28d367cfcce8a948414c2ca5f74 |
| Baritone | v1.20.0 explicitly targets Minecraft 26.3 |
| jevtests | ec2d602ab4ac03f88b8fac886b7c173d0485dcb4, default branch master |
| mc_aiplayer | a029fa6a3760fd0f83834c104051b041d986da60; documented MC 1.21.3 / Java 21 |
| Mineflayer | Lock a tested package revision; advertised support through 26.1, not established 26.3 |

Carpet asset: fabric-carpet-26.3+v260915.jar  
SHA256: 7c40b93e807ed914064176898037117097446c6cc76e6a80a856c8bf7f7659f9

Do not select Carpet through releases/latest: it currently returns a 26.4 beta even though its prerelease flag is false. Use the exact tag and checksum. Carpet's own v26.3 source uses Loom 1.17-SNAPSHOT; do not leave an unresolved moving snapshot in our experiment manifest.
Baritone release assets include baritone-api-fabric-1.20.0.jar and a standalone Fabric jar. Verify checksums and exact API signatures at v1.20.0. Baritone's version is not the Minecraft version.

## 4. Body implementation decision

Reuse Carpet's fake player; do not write a new fake-player implementation or port all of mc_aiplayer.

Add a small mclab Fabric mod for observations, tick scheduling, episode control and scoring. Encapsulate all Carpet imports in CarpetBodyAdapter.

Reviewed Carpet v26.3 files:

- carpet.patches.EntityPlayerMPFake: ServerPlayer subclass, spawn, tick, death and dimension behavior.
- carpet.helpers.EntityPlayerActionPack: movement, aiming, sneak/sprint, jump, attack/use, hotbar and stop.
- carpet.fakes.ServerPlayerInterface: access to action pack.
- carpet.commands.PlayerCommand: command reference.

First prove command-level spawn/move/look/stop/jump/mine/place. Then use the Java action pack for motor control. Do not send RCON commands every motor tick.

### Source findings that affect correctness

1. createFake returns before asynchronous profile resolution/player placement completes. Wait for actual registered, stable player state; a true return is not readiness. Timeout is infrastructure failure.
2. Spawn loads persisted player data. Reset and verify inventory/equipment, game mode, health/food/effects, attributes, abilities, pose, velocity and position after readiness.
3. Console spawn defaults to Creative unless a mode is supplied. Explicitly request Survival and assert it before starting an episode.
4. Death invokes ordinary death logic and disconnects the fake player. End the episode on death and spawn a fresh body for the next one.
5. Continuous ATTACK mines blocks, but the entity branch only calls player.attack for noncontinuous actions. Combat requires discrete attack pulses.
6. Target tracing uses fixed 4.5-block Survival reach. Measure block and entity reach against a real player. Enforce applicable real-player interaction attributes where Carpet would exceed them, and record the compatibility guard.
7. stopAll releases inputs; it must not erase falling, inertia or knockback. Qualify input cancellation separately from instant position freezing.
8. Carpet internals are not a stable cross-version API. Keep the integration small and pinned.
9. Gameplay must use normal movement/interaction semantics. Admin teleports, inventory replacement and world edits are permitted only for setup/reset, never to accomplish a scored task.

### C0 qualification

Repeat checks against a real-player reference where relevant: walk/strafe distance, release/stop, yaw/pitch, jump height, collision, step height, ledge sneaking, sprint, fall damage, mining duration/interruption, pickup, placement face/reach/inventory decrement, discrete entity attack/cooldown, knockback, death/replacement. Confirm spawn protection does not invalidate the arena.

Record expected/observed values and tolerances before scoring. Correct discrepancies or mark the capability unqualified. Dimension travel is a later qualification before Nether work; do not infer full-game fidelity from basic courses.

Timebox initial body proof to one working day. If essential mechanics cannot match, preserve the runner/protocol and use a minimal Fabric client-control adapter as fallback. Do not spend weeks rebuilding physics.

## 5. Architecture and contracts

Python owns orchestration/model calls/results. The server mod owns tick-bound controls, authoritative scoring and filtered observations. Use localhost HTTP JSON commands and a persistent WebSocket event stream, or one equivalently simple bounded transport; document the choice.

All world access and control application run on the server thread. Network handlers enqueue requests only. Capture immutable snapshots on-thread and serialize/write off-thread. Never block a server tick on inference, network or disk. Bound queues, coalesce telemetry if necessary, and record gaps. Missing critical scoring events invalidates a run.

| Interface | Operations |
|---|---|
| CourseService | validate, build, reset, verify, begin, end |
| BodyAdapter | spawn, wait_ready, observe, apply_controls, release_controls, despawn, capabilities |
| AgentAdapter | prepare, start(goal), status, cancel, teardown, provenance |
| DecisionBackend | health, decide(request), close, provenance |
| EpisodeRecorder | append_event, record_request/result, finalize |
Every request/event: schema_version, run_id, episode_id, body_id, sequence. Controls also carry action_id, observation_tick, intended_apply_tick, expires_tick and duration. Reject stale/wrong-episode/wrong-body/duplicate/out-of-order actions. Revalidate legality at execution time. Log receipt and actual applied tick.

Raw controls: forward/strafe [-1,1], bounded yaw/pitch deltas, jump/sneak/sprint, mining hold, discrete attack pulse, use hold/pulse, hotbar slot and duration. Document coordinate axes, degrees, slot numbering and button semantics. Start with 1–5 tick durations; automatic expiry releases controls. Disconnect/watchdog also releases controls. No admin commands in the policy vocabulary.

Capture observation after simulation tick t; apply accepted controls at the next documented pre-simulation boundary. Verify ordering relative to Carpet update hooks with traces, rather than assuming Fabric callback order. Log actual apply phase.

Initial rates: body state every tick; local deterministic/learned motor controller up to 20 Hz; tactical selector event-driven, maximum 2 Hz initially. At most one tactical request/body. Coalesce new state and discard expired responses. The game continues while the model thinks.

Use distinct named timing profiles. Initial tactical request deadline: 1 second; decisions translate into controls only after revalidating current state, with those controls referencing an observation no more than 5 ticks old. Primitive-selector mode may need a declared slower profile; never silently relax timing per provider. Bound behavior commitments and allow meaningful events to interrupt them to prevent oscillation.

## 6. Observation rules

Maintain a tick-indexed 3D map, not only an LLM summary. Initial local neighborhood: 17x9x17 cells (8 horizontal / 4 vertical radius), configurable and versioned. Store namespaced block IDs/properties, collision/affordance data, observed tick and knowledge state. Unknown is not air. Separate current observation from potentially stale memory.

Initial sensor profile: 360-degree local geometric visibility, occlusion-tested from the player's eye. This is an augmented sensor, not exact human screen vision. Only reveal a block's identity when a relevant surface is visible or it was previously observed. Test walls, corners, floors, foliage and fluids; loaded chunks do not imply visibility. Keep an explicit camera-FOV profile for later.

Record body state, known inventory, visible entities and recent action outcomes. Update geometry incrementally and store periodic full snapshots. Derive tactical JSON solely from permitted observations. Evaluator knows the full course; policy must not receive its manifest, hidden coordinates, safe route, labels or expected answers.

Separate coordinate_goal tracks (destination explicitly supplied to everyone) from perception_goal tracks (discover the described target). Report separately. Native baselines with broader chunk access remain whole-system references, not equal-sensor model trials.

## 7. Generated course world

Dedicated superflat server, fixed daylight/weather, no ambient mobs, ordinary Survival physics. Use one active arena at configurable origin, initially near x=1000,z=1000 with standing surface y=80. Optional preview plots are for humans; scored runs reconstruct the isolated active arena.

Course JSON/YAML defines origin/local geometry, spawn/facing, target, initial inventory, allowed region, break/place whitelist, success/fail conditions, tick limit, seed and course/generator version. Compile absolute-coordinate build/reset functions and version-specific datapack metadata. No executor-relative ambiguity.

| Course | Geometry / task | Tick limit |
|---|---|---:|
| C0 | Isolated mechanics checks | Per-check |
| C1 | One oak log 12 blocks straight ahead | 600 |
| C2 | Vary initial yaw and log direction | 900 |
| C3 | Solid obstacles requiring left/right detours, 2+ blocks tall | 1200 |
| C4 | Full-block rise, short crest and descent requiring ordinary jumps | 1200 |
| C5 | Combine turns, obstacles and elevation | 1800 |
| C6 | Five-block gap, supplied dirt, terminal log | 2400 |
| C7 | Eight-block gap, supplied dirt, terminal log | 3600 |
| C8 | Pit plus longer walkable detour, varied dirt supply | 3600 |
C1–C5: empty inventory/equipment, full health/food, no effects, empty offhand; break only target log, no placement. C6–C8: same plus supplied dirt, initially 16 for C6 / 24 for C7; place only in construction region. Finalize buffer and budgets through human development proof, then freeze. Server enforces restrictions and logs attempts as well as actual violations.

The required bridge starts at five blocks because a three-block gap can be sprint-jumped. A three-block gap may remain an optional placement diagnostic, not proof of bridging. Pits span the permitted width and cannot be jumped or bypassed. Prevent mining terrain into a route. Verify support depth, headroom and bounds; surrounding superflat terrain must not provide an unscored shortcut. End on a defined pit plane several blocks below walkway, out-of-bounds, death, timeout or prohibited mutation.

Success: designated log legitimately broken and its resulting item collected into the contestant inventory. Track causal break/drop/pickup, not just a generic inventory count. Record target observed, approached, broken, collected and pit crossed as milestones. Never accept a contestant's success text as ground truth.

Reset sequence:
stop admission -> cancel agent -> release controls -> invalidate old episode -> remove old body and arena-scoped drops/projectiles -> reconstruct terrain -> spawn/reset body -> overwrite state -> verify -> warm chunks -> begin.

Generation-check asynchronous spawn completions too. Restore block entities/scheduled effects or recreate the disposable arena/world when necessary. No world-wide cleanup commands. Remove previous agent memory, cached plans, held inputs and jobs unless an explicitly named transfer-learning experiment permits a specific artifact.

Twenty consecutive resets must match the authoritative arena/body state digest. Development starts with five known smoke layouts, then 20 seeded variants. Freeze separate holdouts; inspected holdout failures consume that set. A course generator seed is necessary; world seed alone does not reproduce episode timing.

## 8. Jev and LLM integration from jevtests

Pin the source revision above; do not turn jevtests itself into the Minecraft project. Check its license before copying and preserve attribution/hashes. If distribution rights are absent, use a private integration/reference boundary and document before distributing copied code.

Useful files:

- adapters/base.py: DecisionModel load/evaluate/unload/health.
- adapters/http_systemone.py: HTTP request building/parsing.
- adapters/jev_cloud.py: TypeSafe/OpenRouter routing and auth.
- adapters/chat_llm.py: OpenAI-compatible schema/tool output, LiteLLM option.
- harness/schema.py: Case, Result, normalize_answer.
- config/models.yaml: current registry and exclusions.
- config/upstreams.lock.json: upstream revisions.

Vendor a minimal permitted subset or use a clean versioned package; avoid generic top-level adapters/harness import collisions. Our runtime owns deadlines, retries and episode storage.

Wire contract: POST /v1/systemone with model, state and questions.q containing type, instructions, criteria. For Choice, criteria maps stable candidate IDs to descriptions. Candidates derive from the declared action profile, never the privileged course solution. Do not include expected labels on the wire. If adapting Case, expected=None is internal only.

| Hosted route | Root URL | Key |
|---|---|---|
| TypeSafe direct | https://api.typesafe.ai | JEV_API_KEY or TYPESAFE_API_KEY |
| OpenRouter Jev | https://openrouter.ai/api | OPENROUTER_API_KEY |

The adapter appends /v1/systemone; chat appends /v1/chat/completions by default. Normalize roots to avoid /v1/v1. Record provider route, requested and served model, revision, precision, quantization and host. A real authenticated Choice probe establishes readiness; HTTP 401/404 is not a healthy backend.

Required adaptations:

1. Old defaults are 180-second requests and five retries with exponential backoff. Live experiments use explicit deadlines/cancellation and normally zero retries. Prevent hidden SDK retries. Offline replay has a separately declared retry profile.
2. Validate legal prediction IDs and finite/ranged/normalized probabilities. Invalid outputs are errors, never silently guessed actions. Use a declared stop/release fallback; count deadline/errors in live outcomes.
3. Chat adapter creates a probability map from stated confidence (or one-hot values). Label verbalized/synthetic probabilities and never call them measured model likelihoods. Mark calibration metrics unavailable when invalid.
4. SETUP_STATUS.md is a historical September 26 report; current models.yaml disables additional models for option token limits, positional bias and null probabilities. Use current exclusions plus fresh probes.
5. The old benchmark requires 77 choices for Banking77. Minecraft initially has <=12 concise candidates; do not inherit that requirement. Option-count-only exclusions can be reconsidered explicitly, but positional-bias and broken-probability failures require retesting.
6. Linux/GB10 launch templates include shell substitutions and CUDA-specific builds. Never run them verbatim in PowerShell. Use configured HTTP endpoints first or platform-specific launcher profiles; keep incompatible torch stacks separate.
7. Spark localhost addresses are not desktop localhost. Discover/configure remote endpoints and don't assume model aliases remain valid.

Initial slate:
- Official Jev direct if configured; otherwise explicitly named OpenRouter Jev route. Probe both if keys exist, but don't treat routes as different architectures.
- laya-typed-421m through its openjev backend.
- kev-0.6b-qwen3 as another small candidate.
- Optional next: open-jev-2b-qwen3.5.
- One small ordinary local LLM and one stronger local LLM from discovered endpoints.
- One fast and one stronger cloud LLM, explicitly chosen and capped.

Start end-to-end integration with one local decision model, hosted Jev and one ordinary LLM. Model names are candidates, not evidence of Minecraft skill. Large-model/MLX serving comes later. Missing cloud keys cannot block local build.

## 9. Baseline integration surfaces

### Baritone: primary 26.3 reference

Baritone controls a real client; installing it server-side does not drive a Carpet fake player. Run a dedicated Fabric client with a small bridge using supported baritone.api classes, not internals.

Use custom goal process setGoalAndPath(Goal...), mine process, status and cancellation through the pinned release's pathing behavior. Verify exact signatures at v1.20.0. Expected cancellation surface includes cancelEverything or its pinned equivalent. Programmatic bridge is preferred to chat scraping.

Documented manual controls include #goto, #mine and #stop. Automation needs acknowledgments and server scoring. A loaded mod/accepted command is not a completed baseline.

Use coordinate navigation and collect-log as separate tasks. Set permitted break/place policy, clear previous-world caches, and enforce rules on the server. Record broader chunk access and native recovery. Such trials belong in whole-system tables, not equal-observation model rankings.

### Mineflayer: optional compatibility experiment

Separate locked Node worker. Surface: connection/spawn events, pathfinder goal/cancel, look/control states, dig/placeBlock, inventory/events, clearControlStates and teardown. goal_reached is a navigation milestone, not log collection.

One-hour native 26.3 probe: join; stand/move >=2 minutes; teleport/reset; mine/pickup/place; cancel/reconnect. Record packages, kick reason and trace. Recent issue reports describe 26.3 movement kicks. If broken, park it or use an explicitly separate 1.21.11 environment. Do not insert ViaVersion/ViaBackwards into the main lab to hide mechanics/protocol differences.

### mc_aiplayer: separate 1.21.3 whole-system reference

Pin reviewed source; Java 21; strict_survival; no hidden scans, emergency teleports, forced pickup or remote world mutation. Generate equivalent layouts through a 1.21.3 compiler profile. Keep scores separate by version.

Reviewed command surfaces:
- /aibot spawn <name> ; console execution position/facing must be explicit.
- /aibot task assign <name> move <x> <y> <z>
- /aibot task assign <name> mine minecraft:oak_log 1
- /aibot task assign <name> gather minecraft:oak_log 1
- /aibot task status <name>
- /aibot task pause|resume|abort <name>
- /aibot profile <name>, replay diagnostics and despawn <name>

Direct task assignment tests its deterministic executor. A separate LLM trial must submit through its verified brain/message path; inspect exact pinned syntax rather than inventing a command. Production /aibot test and verify are GameTest-only, not usable production controls.
Server evaluator remains authoritative; add a small observation shim if status text is insufficient. Verify abort stops owned work before reset. Don't claim its native perception equals ours.

## 10. Experiment tracks and learned policy

Two report families:
1. Whole systems: same course specification, declared version/sensor/tool differences.
2. Controlled components: identical 26.3 body, observations, action profile, timing and motor controller; change only selector.

Our primitive-selector profile uses short forward/strafe/turn/jump/sneak, mining/use and stop. No complete_course or full-map goto_log primitive. Publish candidate generation so it cannot solve the course before model selection. A later tactical profile selects bounded waypoints/behaviors over a fixed motor controller; keep profiles separate.

Offline screen: 40–60 development states including facing errors, obstacles, ledges, impossible placement and insufficient dirt. Label valid action sets with geometry/human review (multiple choices can be valid). Jev is never ground truth. Test original/reversed/seeded option orders, repeated decisions, invalid outputs and latency. Frozen-state accuracy is screening evidence; live trajectories are still required.

Live: five smoke variants, then 20 paired seeded development variants for viable candidates. Freeze configurations before fresh holdouts. One model at a time for isolated timing; separately label shared-resource tests. No unlimited model x harness x course factorial sweep.

After recording/navigation work, train one compact policy on synchronized human demonstrations:
local voxel features + body state + short history -> motor controls.

Start with behavior cloning, a small MLP/recurrent model and a bounded collection budget (e.g. 50 short development episodes, two training iterations). Capture actual client buttons/view inputs through a lightweight recorder and align them to server ticks. Do not infer input labels from player displacement. Record client/server alignment and uncertainty even on localhost.

Split by layouts, not adjacent frames. Evaluate closed-loop completion on unseen layouts; training loss isn't competence. Add targeted corrective demonstrations if coverage fails before escalating to RL. Save checkpoint hash and provenance.

Compare deterministic motor controller vs learned controller; then the same learned controller with/without tactical decision layer. That separates learned gameplay from selector value. Bridging follows navigation/placement. PvP, farms, Nether and Dragon completion are out of the initial implementation.

## 11. Acceptance gates, timing and metrics

States: PREPARING -> READY -> RUNNING -> SUCCEEDED / FAILED / INVALID / CANCELLED.

A model/controller timeout during RUNNING is a failed attempt. Reset/evaluator corruption is INVALID. Report every attempted episode and denominator. Human rescue marks assisted; preserve the record.

Tick and monotonic wall time are separate. Never pause ticks while waiting for a model in scored real-time runs. Initial wall watchdog: twice nominal course duration +30 seconds. Flag excessive server lag, use a predeclared rerun policy, and preserve the original attempt.

| Gate | Pass evidence | On failure |
|---|---|---|
| G0 Bootstrap | Pinned 26.3 starts, human joins, generated C1 loads | Fix toolchain |
| G1 Body | C0 fidelity/cancel tests pass | Thin repair or client fallback |
| G2 Reset | 20 consecutive matching state checks | No model scoring until fixed |
| G3 Execution | >=9/10 deterministic C1 | Diagnose sensing/input/mining/pickup |
| G4 Providers | Valid real probes and deadline/error tests | Skip missing keys; reject broken backend |
| G5 Navigation | Target >=18/20 per C2–C5 development course | Isolate failure layer |
| G6 Learned skill | Held-out closed-loop learned policy, target >=18/20 | Bounded data/training iterations |
| G7 Construction | >=16/20 C6 before C7 | Separate ledge/aim/place/strategy |
| G8 Commitment | Fresh holdouts, acceptable costs/latency and reproducibility | Commit only where evidence supports it |

Calibrate budgets/tolerances on human/deterministic development trials and freeze them before scored comparisons. Twenty episodes are screening evidence, not proof that small score differences are significant. Include uncertainty; pair layouts/start states across candidates.
Metrics: success and milestones; ticks/wall time; stuck/collision time; falls/deaths; illegal/rejected actions; dirt used/wasted; parsing/provider/deadline failures; decision switching; p50/p95 observation-to-action latency; MSPT/TPS; model load and warm timing; local memory/VRAM where measurable; actual cloud usage/cost or unknown (not assumed zero).

Failure categories: perception, decision, execution, timing, infrastructure, rule_violation, unknown. Heuristics can suggest causes but must retain evidence and uncertainty.

## 12. Storage and replay

SQLite: runs, configurations, episodes, requests/results, actions, milestones, resource samples and errors. Large observations/events go to per-episode JSONL with full snapshots + deltas. Record schema version and migrations.

Capture candidate order, model-visible observation, request/response times, actual apply tick, duration, raw response, validated choice, termination, prompts/config hashes, source commits, course/generator revision, checkpoint, provider route, precision/hardware and sensor profile. Redact secrets/auth headers.

Replay modes:
- Reconstruct recorded events/maps for inspection.
- Send identical frozen decisions to alternative backends.

Neither promises bit-exact Minecraft simulation replay. Resume complete benchmark episodes; after a crash mark the interrupted attempt and reset under a new attempt ID, rather than continuing a half-modified world.

## 13. Suggested project layout and local operation

| Path | Purpose |
|---|---|
| mod/ | Fabric bridge, Carpet adapter, evaluator, tick hooks |
| client-bridge/ | Input recorder and Baritone integration |
| src/mclab/ | Python orchestration, schemas, backends, reports |
| courses/ | Declarative courses and variant generator |
| config/ | Servers, models, sensors/actions, budgets |
| scripts/ | PowerShell setup/start/stop |
| tests/ | Meaningful contracts, resets, stale actions, scoring |
| vendor/ | Attributed pinned adapter subset if permitted |
| runtime/, results/ | Ignored worlds/jars/logs/checkpoints |
| docs/ | Setup status, evidence and decisions |

Commit wrapper and dependency locks. Use project JDK settings, no machine-wide PATH replacement. Keep ports configurable and avoid existing servers. Suggested game port 25575, bridge 8765; check conflicts. RCON is optional and must use a different port.

Bind control interfaces to loopback. Remote model endpoints are configuration. Keep credentials in environment variables; no secrets in reports. Don't alter existing public servers. Moving to a server later changes launch/configuration, not agent logic.

Required CLI operations (to implement, not existing): doctor; server start/stop; course build/reset/verify; body qualify; models probe; run; replay-decisions; report. VS Code tasks invoke the same commands. No dashboard first.

## 14. Implementation order / deliverables

1. Bootstrap, locks, project JDK and doctor; write docs/SETUP_STATUS.md with detected/source-reviewed/runtime-tested distinctions.
2. Generate C0–C4; launch 26.3; human inspect C1.
3. Prove Carpet commands, implement body/transport, pass G1.
4. Reset/evaluator/recorder, cancellation and stale-episode rejection, pass G2.
5. Deterministic C1 then navigation; pass G3.
6. Minimum jevtests adapters, real probes and deadlines; local decision model + official Jev + ordinary LLM; pass G4.
7. Baritone reference; optional bounded Mineflayer and mc_aiplayer integrations.
8. Offline screening and controlled live trials, C5; Markdown/CSV/JSON report.
9. Human input recorder, compact learned policy, G6; then construction C6–C8.

First reviewable deliverable: reproducible desktop launch, generated courses, body/reset/C1 evidence, an external baseline where compatible, interchangeable backends and an honest initial report. List proven capabilities, failed gates, exact reproduction commands and next experiments.

## 15. Upgrade strategy

Keep Minecraft/Carpet-specific Java types behind the body/world adapter and bridge protocol. Python/model/reporting layers consume our stable versioned schemas. Use namespaced identifiers, properties and capability descriptors, not persisted numeric registry IDs or Java objects.
Course source stays independent of Minecraft; command/datapack compilers are version-specific. Regenerate disposable worlds rather than downgrading saves. Re-run C0/reset/scoring qualification after every version change. New mechanics may require policy reevaluation/retraining even if software interfaces stay stable. This limits porting scope; it does not promise zero maintenance.

## 16. Primary sources

- [Minecraft 26.3](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3)
- [Fabric 26.3 guidance](https://fabricmc.net/2026/09/15/263.html)
- [Carpet release v26.3](https://github.com/gnembon/fabric-carpet/releases/tag/v26.3)
- [Carpet body](https://github.com/gnembon/fabric-carpet/blob/v26.3/src/main/java/carpet/patches/EntityPlayerMPFake.java)
- [Carpet controls](https://github.com/gnembon/fabric-carpet/blob/v26.3/src/main/java/carpet/helpers/EntityPlayerActionPack.java)
- [Carpet commands](https://github.com/gnembon/fabric-carpet/blob/v26.3/src/main/java/carpet/commands/PlayerCommand.java)
- [Carpet dependency metadata](https://github.com/gnembon/fabric-carpet/blob/v26.3/src/main/resources/fabric.mod.json)
- [Baritone v1.20.0](https://github.com/cabaletta/baritone/releases/tag/v1.20.0)
- [Mineflayer](https://github.com/PrismarineJS/mineflayer)
- [Reported Mineflayer 26.3 issue](https://github.com/PrismarineJS/mineflayer/issues/4189)
- [mc_aiplayer reviewed revision](https://github.com/zoyluoblue/mc_aiplayer/tree/a029fa6a3760fd0f83834c104051b041d986da60)
- [jevtests reviewed revision](https://github.com/marcmontecalvo/jevtests/tree/ec2d602ab4ac03f88b8fac886b7c173d0485dcb4)
- [Current registry at that revision](https://github.com/marcmontecalvo/jevtests/blob/ec2d602ab4ac03f88b8fac886b7c173d0485dcb4/config/models.yaml)
- [TypeSafe contract](https://docs.typesafe.ai/concepts/system-one)

Source inspection supports these choices. Live credentials, dependency builds, mechanics fidelity, actual model latency and course success remain acceptance tests. No server, agent, training run or paid provider batch was executed while preparing this handoff.
