# Shared run configuration

Phase 1 implementation now resolves a versioned run configuration for gather,
build, natural routes, courses, tactical/tandem model runs, battery checks, and
companion checks. Existing runners and dashboards retain their own launch
surfaces; each resolves or records the same typed contract. This describes local
configuration and provenance behavior, not live Minecraft qualification.

## Resolve and launch

The shared CLI accepts a test family, a JSON config, a preset, and a config-only
inspection mode:

```powershell
uv run --locked mclab run --test gather --preset conservative --print-config
uv run --locked mclab run --test natural --run-config natural.json --print-config
uv run --locked mclab run --test models --run-config model.json
```

`--print-config` resolves and validates without launching the runner or acquiring
the arena. To execute, omit that flag. The shared runner CLI supports all seven
families: `gather`, `build`, `natural`, `courses`, `models`, `battery`, and
`companion`. Existing direct scripts also accept `--run-config`, `--preset`, and
`--print-config` alongside their family-specific options where supported; explicit
CLI values override config values. Existing runner defaults are preserved when a
flag is omitted.

Resolution applies family defaults, then the selected preset, then the supplied
config, then explicit launch overrides. Presets are JSON files in
`config/presets/`. Partial config files are merged with defaults. Unknown fields,
wrong types, non-finite numbers, invalid family combinations, and unsupported
runner options fail validation before arena acquisition.

## Canonical fields

Configurations use `schema_version`, `run`, `body`, `planner`, and `model`.
`planner` may be null for families that do not consume planner policies. Fields
are typed and family capabilities constrain which sections are consumed. The
canonical names below match the resolver and API schema; see
[run_config.py](../src/mclab/run_config.py) and `GET /api/schema` for defaults,
constraints, and capability metadata.

```json
{
  "schema_version": 1,
  "run": {
    "test": "natural",
    "seed": 1337,
    "route_data": [
      {"route_id": "example", "start": [0, 70, 0], "goal": [8, 70, 0], "yaw": 0}
    ],
    "entrants": ["v8_goal"],
    "conditions": [false]
  },
  "body": {
    "control_profile": "lockstep-three-tick-v1",
    "view_distance": 4
  },
  "planner": {
    "terrain": {},
    "movement": {},
    "memory": {}
  },
  "model": {
    "backend": "example-backend",
    "model": "example-model",
    "request_limit": 20,
    "planner_profile": {
      "instructions": "Prefer safe, direct routes."
    }
  }
}
```

The snippet illustrates field names, not a ready-to-run configuration: populate
each policy object from schema defaults and choose a backend present in
`config/backends.json`. Natural-route configurations use `run.route_data` entries
with `route_id`, `start`, `goal`, and `yaw`, plus `run.entrants` and
`run.conditions`. Body-level settings include `body.awareness`,
`body.control_profile`, and `body.view_distance`. Planner policy objects are
`planner.terrain`, `planner.movement`, and `planner.memory`. Gathering controls
are nested under `run.gather`.

`model` is typed and consumed for tactical and tandem runs. Its fields include
backend/model selection, request limits, deadlines, prompt identities, and typed
`planner_profile` settings for tandem planning. Model configuration is not an
empty placeholder. Family validation still rejects model settings that a selected
runner cannot consume.

## Dashboard and direct-run behavior

Dashboard `POST /api/start` accepts `run_config` and/or a preset for companion
gather/build jobs. It retains saved `run_config` and references to the original
input files. Legacy natural-route and backend references are resolved again at
batch start; the actual effective config is then frozen with the job. Dashboard
gather/build and direct CLI launches use the same resolver. Host, Origin, UI-token,
arena-lock, stop, and worker boundaries are unchanged.

Existing gather/build/battery and check-script CLIs accept shared config and
preset options alongside their own options. Course, natural, and model APIs are
decorated at launch so their effective arguments are recorded even when invoked
through existing scripts or Python entry points. The common CLI exposes supported
family options such as `--start`, `--seed`, `--count`, backend and request limits,
in addition to course operation, natural route inputs, battery selections, and
companion check arguments.

## Schema and policy propagation

`GET /api/schema` is generated from typed settings and includes per-family
defaults, field metadata, constraints, and capability information. Server-side
validation remains authoritative. Memory policy is now consumed by companion
launches; build gathering overrides reach planning, refill, and chest/storage
paths. Awareness and other body policy settings are resolved and passed through
their supported bridge/session contracts.

## Run records and limits

Before arena/world work, a launch writes immutable resolved config and provenance
records under `results/launches/`. The same resolved snapshot is attached to the
runner's native result folder. Battery records are created before its restart
boundary. Status records preserve finished, failed, and interrupted outcomes,
including startup errors. Input references are retained so later edits to a
preset or source file cannot change what the run claims it used.

Provenance captures checkout/source identity, relevant dirty and untracked input
hashes, installed mod hashes, selected input references, and sanitized invocation
data. Unknown or unavailable evidence stays explicitly unknown; an installed jar
hash does not prove a match to the current source checkout.

Phase 1 completed local verification on November 15, 2026: **1,349 tests passed**,
Ruff checks and formatting passed, and local documentation links and
`git diff --check` passed. Config-only CLI resolution passed across all seven
families without launching Minecraft. No live Minecraft qualification was run
for this configuration work.
