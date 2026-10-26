# Minecraft AI laboratory

This repository implements the local laboratory foundation from
[the technical handoff](Minecraft_AI_Technical_Handoff.md). It runs an isolated,
local Minecraft Java 26.3 server and generates development courses C0–C5.
The bootstrap Fabric mod verifies arena geometry against the generated specification.

The initial C1 body, reset/evaluator, filtered sensor, episode recorder and
deterministic controller are implemented. The gate ledger records reviewed live
qualification separately from code availability. Provider adapters are implemented;
real provider probes and scoped C1 model trials are recorded. G4 remains open;
no learned policy or external baseline has been live-qualified.
**G0–G3 passed for initial C1; the final configuration completed 10/10 scored attempts.**
The frozen deterministic C2–C5 navigation cohort passed **80/80**. Optional tandem
model coordination is locally tested, with no live tandem performance claim.
Earlier failed configurations remain in the report, grouped by timing/source profile.
See [setup status](docs/SETUP_STATUS.md) and
[implementation decisions](docs/DECISIONS.md).

## Local operation

Run from this directory in PowerShell with Git and uv installed:

```powershell
rtk proxy powershell -NoProfile -File scripts/setup.ps1
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server start
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-bootstrap
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 report
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server stop
```

The initial start requires accepting Minecraft's EULA. After reading
[the EULA](https://aka.ms/MinecraftEULA), an accepting operator can use
`server start --accept-eula`. Acceptance persists only in ignored local runtime files.
Marc authorized acceptance for the current local installation on October 22, 2026.

Join **127.0.0.1:25575** from an authenticated Java 26.3 client on this desktop.
The game interface binds loopback, so this address is not accessible from another
computer. The initial spawn is near **1000,80,1000**. C1 has an oak log at
**1000,80,1012**. Face south (+Z; yaw 0). Inspect the platform, empty hands,
Survival movement, and log. The client needs no mods for this check.
Do not start scored comparisons until body qualification and subsequent gates pass.

Empty-server pausing is disabled so queued setup and reset requests keep running.
Bootstrap verification starts
after 160 simulation ticks and checks 2048 cells per tick. Allow approximately
11 seconds of simulation after startup. `server start` acknowledges supervisor
startup; `server status` reports `running` only after a Minecraft protocol response.

## Configuration and generated courses

[config/lab.json](config/lab.json) controls origin, memory, ports, and launch settings.
Settings initialize `server.properties` on first bootstrap. Existing properties are
preserved; startup rejects host/port drift. To change those fields, edit both files
with the lab stopped. No existing public server configuration is touched.

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server stop
# Wait for server status to say stopped before replacing files.
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course build --course C3 --seed 4
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-generated
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server start
```

All C0–C5 functions are generated. Only the selected course occupies the active
arena. Building changes files while stopped. The datapack compares its specification
hash at load and reconstructs a newly selected specification after warming chunks.
Rebuilding the same specification does **not** reset a modified world. The new
`course reset-check` operation explicitly reconstructs the active C1 arena and
checks fresh body state. `course execution-check` resets each attempt before
starting the authoritative C1 evaluator and deterministic controller.

Courses clear a bounded 49×41×49 volume, build a four-block-deep isolated platform,
and use absolute coordinates. Vanilla fill limits are respected. The source
specifications are development fixtures: C0 is an empty mechanics arena with a log,
not a completed qualification suite. C3 has a two-block-tall wall; C4 has a
full-width one-block rise and descent. C1 enforces target-only breaking and no
placement/attacks, and scores causal break/drop-UUID/normal-pickup chains.
C2–C5 qualification applies only to the declared navigation profile and frozen
cohort; repeated seeds alone do not establish distinct layouts.

## Builds and verification

```powershell
rtk proxy powershell -NoProfile -File scripts/build-mod.ps1
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 mod-install
rtk proxy uv run --locked pytest -q
rtk proxy uv run --locked ruff check src tests scripts
rtk proxy uv run --locked ruff format --check src tests scripts
```

The preserved direct-scoring container wrapper under
`experiments/qwen35-direct/` is checked separately with Ruff. Its Dockerfile and
README reproduce the tested SemIf service without committing weights or caches.

Install the built mod only with the server stopped. The build script sets JDK and
Gradle cache paths for its own process; Java 21 remains installed and unchanged.
Dependency versions/hashes are in `config/dependencies.lock.json`, `uv.lock`,
`mod/gradle.lockfile`, `mod/buildscript-gradle.lockfile`, and Gradle verification
metadata. The wrapper pins and verifies Gradle 9.6.0. `-UpdateLocks` is an explicit
maintainer operation, not the normal build. Keep generated wrapper files in version
control. The repository initially had no commits.

`scripts/discover.ps1` and `scripts/resolve-dependencies.py` are dependency-update
tools. Normal setup never resolves latest versions. Updating the lock needs review
and complete requalification.

## Artifacts and boundaries

- `runtime/server/`: disposable lab world, dependencies, properties and logs.
- `runtime/server/mclab-bootstrap-evidence.json`: server-thread geometry snapshot,
  serialized off-thread; includes launch ID, specification hash and mismatch counts.
- `results/generated/`: reproduction specifications and functions.
- `results/doctor.json`: local environment checks.
- `results/episodes.sqlite3`: shared C1/model and historical navigation ledger.
- `results/navigation/<run>/episodes.sqlite3`: isolated new navigation ledgers,
  including every attempted outcome and full event payloads. Reports aggregate
  these with the shared ledger; pending attempts are recovered as interrupted.
- `results/execution/`: controller provenance, JSONL and terminal summaries.
- `results/reports/initial.{md,csv,json}` and `episodes.csv`: all scored denominators
  plus separately labeled development/adversarial attempts.

Runtime and results are ignored. Keep the handoff intact. The supervisor exposes
only token-authenticated status/stop on loopback port 8766; port 8765 hosts the
qualification bridge. No model service or paid request runs during setup.
The evaluator and sensor run on the server thread. Model network calls never
pause simulation; the bounded transport detects event gaps and stale actions.

## Body qualification and human reference

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body qualify
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body protocol-check
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body reference --player Osadar932 --seconds 60
```

These are bounded development checks, not scored task episodes. Qualification
uses a disposable mechanics fixture near 990,80,988.5 and rejects setup/cleanup
when a human is inside it. Keep human players near the platform center.
The suite first proves Carpet commands and then uses the Java action pack.
It preserves all attempts, checks, actual apply ticks, observations and errors
in `results/qualification/`. Human reference captures go in `results/references/`.
Do not run these commands concurrently.

The supplementary protocol check has 24 live admission/cancellation checks,
reported separately from the 39 mechanics checks. The October 24 human reference
confirmed basic mining, pickup, placement and single-attack behavior; see
[the comparison evidence](docs/G1_HUMAN_INTERACTION_PROOF.json). The
[capability review](docs/G1_CAPABILITY_REVIEW.json) qualifies the frozen initial
C1 movement/mining profile. Combat knockback, entity attacks, construction and
dimension travel remain outside that profile.

For the repeat cow test, use `body reference --player Osadar932 --seconds 45
--combat`. This prepares a stone backstop behind the cow; approach from the log
side, near x=1002.5,z=1006.5, facing north. The wall remains in the development
arena after capture. The recorder includes direct sprint state and attack-strength
telemetry for new captures. Guide one step per short recording, allowing the human
to reconnect and get ready before starting.

Completed terrain references have separate, prepared G1 fixtures:

```powershell
rtk proxy uv run --locked mclab body reference-plan --mechanic collision
rtk proxy uv run --locked mclab body reference --player Osadar932 --seconds 30 --mechanic collision
rtk proxy uv run --locked mclab body reference-fixture-check
```

Named mechanics are `collision`, `step`, `ledge`, and `fall`. Review the plan and
wait for the human to be ready before capture. Named setup rebuilds the bounded
C0 area (default x=987..993, y=75..85, z=986..996), restores health/food to 20,
and relocates that player facing south. Inventory is preserved; armor, effects,
non-Survival state and another human in the fixture block preparation. Geometry
remains after capture. The ledge catch layer is at y=75 (landing surface y=76).
The `fall` check starts a five-block drop immediately, so release all keys before setup.
These are G1 development fixtures, not the G2 episode reset implementation.

The summary retains the server's setup acknowledgement, grounded height, collision
samples, sneak/forward input, first-landing health and tick continuity. Reports
require review and never advance G1 automatically. `reference-fixture-check` uses
a fake player to verify geometry and mechanics without manipulating a human;
it complements the recorded real-player comparisons.

## Reset foundation

```powershell
rtk proxy uv run --locked mclab course reset-check --count 20
```

Keep human players disconnected or outside the arena. This bounded G2 development
check reconstructs C1, removes arena-scoped drops/projectiles, creates a fresh body,
warms it and compares authoritative terrain/body digests. Each attempt is recorded
in JSONL and `results/reset_checks.sqlite3`. The live 20-reset run matched one state
digest throughout. Policy admission stays closed until an explicit `course_begin`
revalidates terrain/body state. This command itself runs no scored episodes.

## Navigation development

The separate `navigation-c2-c5-survival-v1` profile inherits the qualified C1
physics. Its reset qualification is in `docs/NAVIGATION_QUALIFICATION.json`;
execution results and raw audits are in `docs/G5_NAVIGATION_PROOF.json`.
The frozen v8 baseline passed 20/20 distinct development layouts per course
(80/80 total), with complete raw audits. This is deterministic navigation evidence;
model comparisons, learned policies and holdouts remain separate.
The motor uses local observed-map memory, committed routes, fresh support checks
and level-path lookahead. It plans clear mining viewpoints and approaches
observed drops over known support. It is a deterministic baseline. Cyclic garbage
collection is scheduled between episodes, with the policy recorded per run.
The bridge reuses an authenticated HTTP connection during polling; transport
failures invalidate attempts without retrying potentially admitted commands.

With the server reporting `running`, run one development batch at a time:

```powershell
rtk proxy uv run --locked python -c "from mclab.navigation_runner import execute; r=execute('C2',list(range(5,25)),scored=True); print(r['status'],r['successes'],r['attempted_episodes'],r['recording'])"
rtk proxy uv run --locked python -m mclab.navigationproof --help
```

Use C2, C3, C4 or C5. Seeds 5–24 are the paired development set, not holdouts.
Smoke seeds are 101–105. Restart before exceeding 32 retained body identities;
a five-attempt smoke plus a twenty-attempt batch uses 27 including cleanup.
Each batch restores C1 and saves a `summary.json`, JSONL and SQLite ledger.
Pass completed summary paths to the audit command with `--output`; invalid
evidence exits nonzero. A valid smoke is not a gate pass. G5 requires at least
18/20 per course under one frozen controller, execution stack and course inputs.
No audit command changes the gate ledger automatically.

## C1 execution and later experiments

```powershell
rtk proxy uv run --locked mclab course episode-check
rtk proxy uv run --locked mclab course execution-check --count 10
rtk proxy uv run --locked mclab models probe
rtk proxy uv run --locked mclab experiments plan --output results/experiments/development_plan_navigation_v2.json
```

`episode-check` deliberately attempts forbidden actions, planted items, altered
terrain, cancellation, deadlines and controller loss. Its failed/invalid episodes
are expected unscored fault injection, not hidden task successes. It also verifies
native sensor occlusion against walls/corners/floors/foliage/fluids.

Scored execution requires reviewed G1/G2 and the installed evaluator source hash.
The C1 perception-goal controller discovers the oak log through permitted local
visibility, approaches, mines, then walks into normal pickup range. It receives
no evaluator target coordinates or drop UUID. Each attempt has a fixed 600-tick
limit and 90-second wall watchdog. `--development` explicitly runs unscored work.
Do not run multiple qualification/execution commands concurrently. The server
requires restart after 32 retained body identities, including development checks.

`config/backends.json` lists explicitly configured endpoints. Probes use one bounded
request at a time, zero retries, a deadline and validated legal Choice output.
Remote calls additionally require a configured cost ceiling and request-cost cap.
Missing credentials skip local progress without implying provider readiness.

Atom is configured as the first private-LAN chat route. Its bearer token belongs
in the Git-ignored `.env` as `ATOM_API_KEY` (see `.env.example`). Load it explicitly:

```powershell
rtk proxy uv run --env-file .env --locked mclab models probe
```

The configured private address was resolved from `atom`; recheck discovery before
changing it. Private HTTP requires an explicit LAN scope and private IP literal.
Inference must also be declared local to use that scope without a remote cost
budget. Other remote endpoints retain the default HTTPS/cost requirements.
The Atom short-choice profile explicitly disables thinking through the
[documented vLLM template option](https://docs.vllm.ai/en/stable/features/reasoning_outputs/).
That option is recorded in provenance; no reasoning text is interpreted as a
legal action. See [the Atom probe evidence](docs/G4_ATOM_PROBE.json) for live
readiness, the bounded choice smoke checks and retained failed attempts.

Live local tactical integration is available as a separately recorded development
experiment:

```powershell
rtk proxy uv run --env-file .env --locked mclab models live-smoke --backend atom-qwen-chat --count 1 --request-limit 40
```

The final compact-JSON configuration completed three consecutive C1 seed0 runs;
all five historical live attempts remain in the report. Inference is asynchronous
and every motor action is revalidated against current observations. This visible
target behavior baseline does not establish learned control or later-course skill.
See [live integration and results](docs/G4_LIVE_INTEGRATION.md). G4 remains open.

OpenRouter Jev uses `OPENROUTER_API_KEY` in the same ignored `.env`. The pinned
`typesafe/jev-1.14` SystemOne route passed its probes and one live C1 attempt.
Probe only that route with `mclab models probe --backend openrouter-jev` under the
same `uv run --env-file .env --locked` prefix. Its configured 40-call cap and $1
cost ceiling are checked before any live batch. See [Jev evidence](docs/G4_JEV_PROOF.json)
for the 23-call initial experiment and provider-reported charges.

Upcoming model comparisons and deferred vision work are tracked in the
[experiment queue](docs/EXPERIMENT_QUEUE.md). The gpu-docker Ollama endpoint is
`http://10.10.40.20:11434/v1` and requires no API key. Its discovered `qwen3:14b`
model passed three recorded visible-log choices but failed its initial live C1
attempt by repeatedly selecting an impossible aim behavior. See
[the comparison evidence](docs/G4_GPU_DOCKER_PROOF.json). It is not qualified for
broader trials under the current prompt.

`experiments plan --output <new-path>` freezes a development layout inventory
without changing the active world. C3–C5 now have expanded generators. Offline
states can be exported with `experiments export-screening --recording <JSONL>
--output <JSON>`. Their valid-action labels require independent review, and they
are not synchronized human demonstrations or training-ready data.

The Baritone 26.3 artifact/API is pinned in `config/baselines.lock.json`; a client
bridge (`client-bridge/`, `mclab baritone prepare|launch|status|goto|cancel`) runs
Baritone in a Prism-authenticated client; it joined the lab and passed a live
goto/cancel probe. Scored baseline trials remain pending.

The broader [synthetic tactical screen](docs/G4_SCREENING.md) contains 48 reviewed
observations under three candidate orders. Jev scored 141/144; Atom's shared-load
comparison stopped after a timeout and remains partial. Its performance is not an
isolated model speed comparison. Use `experiments screen-build`, `screen-run` and
`screen-report` to freeze, run bounded batches and inspect all outcomes.
The additional local screens scored OpenJev 78/144, Laya 18/144 accepted and
correct (29/144 raw choices), and Qwen3.5-4B SemIf direct scoring 28/144 in each
of two separately retained prompt configurations. These candidates were not
promoted to live trials. See the [screening evidence](docs/G4_SCREENING.md).
The tested SemIf container build sources are preserved under
[experiments/qwen35-direct](experiments/qwen35-direct/README.md); raw recordings
and model caches remain ignored local artifacts.

C2–C5 now each have 25 distinct development layouts; C3–C5 each have 25 different
physical geometries. C2 variation also includes initial facing. The qualified C1
output is unchanged; twenty seed-0 resets per navigation course and the frozen
80/80 deterministic navigation cohort are recorded. Later-course model selection
and tandem gameplay remain unqualified. Preserve the earlier frozen plan and
reproduce the development inventory with:

```powershell
rtk proxy uv run --locked mclab experiments plan --output results/experiments/development_plan_navigation_v2.json
```

The reference recorder reads the actual latest client button state received by
the server, plus view and body state. It records client/server timing uncertainty;
these are not training-ready, precisely aligned client-timestamped demonstrations.
See [bridge protocol](docs/BRIDGE_PROTOCOL.md) for lifecycle and observation details.

## Optional tandem model experiments

Tandem planning/tactical models are a hypothesis to compare with single-model
execution, not an assumed winning architecture. The existing C1 live-smoke path
stays single-model unless a planner is explicitly supplied. Either role can use
any compatible configured backend; no model is automatically assigned a role.

The first tandem adapter uses Choice to select a declared strategy and passes
versioned strategy context to the tactical model. It is not an unrestricted
structured-plan brain. Save an explicit experimental profile, for example at
`results/experiments/c1_planner_profile.json`:

```json
{
  "instructions": "Choose a collection strategy from the permitted observations.",
  "candidates": {
    "search": "Search safely for an oak log, then work toward collecting it.",
    "observed_item": "Prioritize collecting a currently observed oak log item."
  },
  "planner_interval_s": 5.0,
  "plan_ttl_ticks": 200,
  "planner_failure": "fail_attempt"
}
```

Replace the uppercase backend names with entries from `config/backends.json`.
These commands send new provider requests and require the existing native server
and source-bound C1 readiness checks:

```powershell
# Single model, retaining the existing motor/action profile.
rtk proxy uv run --env-file .env --locked mclab models live-smoke --backend TACTICAL_BACKEND --count 1 --request-limit 30
# Optional second model, with independently bounded requests.
rtk proxy uv run --env-file .env --locked mclab models live-smoke --backend TACTICAL_BACKEND --planner-backend PLANNER_BACKEND --planner-profile results/experiments/c1_planner_profile.json --planner-request-limit 4 --count 1 --request-limit 30
```

The two asynchronous lanes have separate backend instances, deadlines and budgets;
slow planning does not block tactical calls or the motor. Plan changes, replanning
and expiry invalidate obsolete tactical replies. Both roles are closed and their
requests/provenance retained during teardown failures. Remote per-role caps and
their combined conservative cost budget are validated before touching the arena.
`continue_without_plan` is an optional, explicitly recorded degraded failure policy;
the default fails the attempt when the planner fails. Reports distinguish the
roles, single/tandem mode and degraded outcomes.

Fake-provider integration tests verify these contracts. Two-model live gameplay,
general structured plans, interruption/resumption policies and combat still need
their own tests; see [candidate architecture](docs/HYBRID_ARCHITECTURE.md).
