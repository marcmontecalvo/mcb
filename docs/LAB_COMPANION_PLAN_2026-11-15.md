# Lab UI and companion plan

Status: phase 1 implemented and locally verified, November 15, 2026. The shared resolver,
typed schema, run provenance, and policy plumbing now cover all seven run
families; the full Python suite passed **1,349 tests**, with Ruff and documentation
checks clean. Phase 1 automated acceptance is complete. No live Minecraft
qualification was run for this configuration work. See [RUN_CONFIG.md](RUN_CONFIG.md)
for the canonical contract. Phase 2 is also implemented and locally verified:
1,386 Python tests (after review fixes), 6 UI tests, and 4 desktop/mobile browser checks passed.
See [LAB_UI.md](LAB_UI.md) for operation and [IMPLEMENTED.md](IMPLEMENTED.md)
for verification limits. Phases 3–6 remain planned.
This records Marc's decisions and the implementation order.
Navigation decisions and qualification remain governed by
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).
Existing dashboard operation is documented in [LAB_UI.md](LAB_UI.md).

## Decisions

- Keep the Python dashboard backend and replace its single-file frontend.
- Use React, TypeScript and Vite, with Tailwind and the existing HomeNet UI
  component package as the baseline. Build static assets and serve them from the
  Python server. A build step is accepted; production needs no Node server.
- Keep the interface minimal. Colors, type, spacing, radii and surfaces come from
  shared theme tokens. Use ready-made components for forms, dialogs and tables.
- Bring-your-own agents connect through MCP. SOUL.md import is useful but is not
  the integration endpoint.
- Gather and build must accept planner profile overrides and record the settings
  actually used.
- Add this as a roadmap track, linked to chat item 18 and the bot-profile spike.

## Framework and component reuse

Use `G:/projects/homenet/packages/ui` (`@homenet/ui`) as the reusable component
source. Its current package supports React 18.3/19, uses Tailwind 4 and Radix
Dialog, and exports buttons, cards, badges, alerts, form controls, navigation,
tables and dialogs. Its compiled stylesheet includes theme tokens. It also has
the AOS `space-glass` theme; the source design export is in
`G:/projects/homenet/ui_design_system`.

Start with a quiet neutral theme. Keep the AOS styling available without requiring
the star field, glass effects or agent-specific colors throughout the lab. Slate
(`G:/projects/vulpine-slate-site`) is an Astro site: reuse visual ideas where useful,
while HomeNet supplies the React components.

Before building, choose a pinned package artifact or a source snapshot with its
license and source revision retained. Do not require a sibling checkout at runtime.
Expose components through one MCB UI module; add missing shadcn-style components
there using the same tokens, rather than introducing a second visual system.
Check inherited contrast and keyboard behavior before adoption.

Keep frontend code in a small `ui/` project: shared UI, API client and types,
schema forms, and feature folders for runs, comparison and companion. Document
build, typecheck and verification commands and one example of adding a field/page
so another agent can extend the app without inventing new patterns.

Vite emits static assets; Python retains API ownership, authentication, detached
workers, session records, stop/kill behavior and arena locking. Development may
proxy `/api` to Python. Preserve loopback, host/origin and token checks, including
live streams. See [Vite static deployment](https://vite.dev/guide/static-deploy),
[shadcn Vite setup](https://ui.shadcn.com/docs/installation/vite) and
[theme tokens](https://ui.shadcn.com/docs/theming).

## Current contract and boundaries

The inventories remain references to actual consumers and protocol boundaries,
not instructions to redo phase 1. The field matrix has been applied to the shared
resolver across existing CLI and Python launch paths. `CompanionBody.__init__`
and `start_session` accept movement, terrain, and memory policies; the resolver
passes supported values through the existing hooks. Build gathering options are
consumed through planning, refill, and chest/storage paths.

The protocol still distinguishes route-local `use_doors`/`climb_vines` on
`follow_route`/`follow_gap` from raw-control defaults on `route_policy`. A setting
must reach the operation that consumes it. `body.view_distance` stays distinct
from awareness bounds and is recorded through its current control path. Typed
model settings cover tactical and tandem backends, request limits, and planner
profile overrides; prompt construction/extraction remains a companion-phase
concern. This track does not assume chat, skins, or multi-bot support already
exists.

## 1. Resolved run config, provenance and schema

Every shared CLI family and existing decorated runner resolves the same
versioned config contract with `run`, `body`, `planner` and `model` sections.
Dashboard companion launch also uses this resolver. Keep gathering options in a
named run subsection, memory policy with planner configuration, and references to
world/server configuration separate from per-bot preferences.

Resolution order is family defaults, preset, supplied run config, then explicit
launch overrides (including CLI flags). Distinguish an omitted flag from its
parser default. Reject unknown fields, invalid combinations, and settings
unsupported by the selected test before taking the arena. `--print-config` shows
the resolved config without launching.

Presets live in `config/presets/`; UI selections store references and overrides.
Each run nevertheless retains an immutable resolved snapshot and the hashes of
referenced inputs so editing a preset cannot rewrite history. Record before the
first world-changing action:

- resolved config, schema version, invocation and preset references;
- commit, dirty state and a deterministic hash of relevant tracked changes and
  untracked source/config inputs, excluding secrets and generated results;
- installed mod hashes and current source identity; claim a source-to-installed
  build match only when the evidence verifies it; runtime/world/seed/start and
  checkpoint identity;
- model/backend identifiers and declared prompt identity, without credentials.
  Record prompt-file hashes only when the prompt is a hashed input artifact.

CLI flags remain supported and feed this resolver. Record defaults too, and retain
failure/interruption records. An older run with missing provenance stays marked
unknown; do not infer it from today's defaults.

`/api/schema` is generated from `dataclasses.fields()` and resolved type hints on
`TerrainPolicy`, `MovementPolicy`, `MemoryPolicy`, `Awareness`, `GatherOptions`,
and typed run/model settings. It includes field metadata, defaults, constraints,
and per-family capabilities. Dataclass/server validation remains authoritative.
Unsupported types or settings are explicit rather than silently dropped.
Java constants remain fixed until deliberately added to the payload, validated,
and consumed on both sides.

Implementation is delivered across the shared CLI, direct runner APIs, dashboard
launches, battery, and fixed checks. The full suite passed **1,349 tests**;
Ruff, formatting, documentation links, and diff checks passed. Shared CLI
config-only resolution passed for all seven families. Phase 1 automated
acceptance is complete; these checks do not establish live body behavior.

## 2. Lab UI, catalog, queue and live status

One catalog covers battery checks, companion checks, gather, build, courses,
natural routes and model smokes. Inventory the current battery rather than freezing
the reported count of 13 into the design. Entries declare parameters, runner,
capabilities and result adapter. Battery checks can run individually or as a suite.

Provide checkboxes, saved suites, preset selection, per-test overrides, seeds and
repetitions, start/world, timeouts and resume checkpoint. Show the expanded queue
and resolved settings. Preserve the existing exclusive arena and worker lifecycle;
adding a queue does not permit concurrent use of the singleton body/world.

Use a server-sent-events endpoint for status changes, with bounded listeners,
disconnect cleanup and reconnect/resync. The backend remains the source of truth.
Reconnect, page refresh and a backend restart must recover durable session state.
Stop, kill, inspection waiting and terminal failure need distinct visible states.
Do not introduce another consumer that reads and discards `/policy` terrain batches.

Done when a saved mixed suite launches through existing runners, refresh preserves
the queue, stop/kill still work, and forms/live status work by keyboard and on a
narrow screen. Measure the shipped bundle and add dependencies only as needed.

## 3. Durable comparison

Write a small versioned normalized record containing config, provenance, metrics
and per-case results, including partial/killed runs. Compute log-derived metrics
before pruning. Prune must retain raw data if required summary generation fails;
compare must still work after raw logs are removed. Reuse
`dashboard_metrics.compare_data`, `build_stats` and `idle_gaps` semantics through
family-specific adapters rather than assuming every test has the same metrics.

Default to the previous run of the same test and seed; let the user change it.
Show config/provenance differences first, metric deltas second, pass/fail flips
third, then standstills. A zero is different from missing data. Mark differing
configs and never pool them. Only highlight improvement for matching tasks and
comparable metric definitions; task identity includes world/start, template or
route, checkpoint, supply mode and other test-specific inputs. Source changes are
visible, so matched experiments can compare implementations without hiding them.

Done when comparison survives pruning, missing old provenance is visible, and
mismatched tasks cannot produce a misleading improvement or pooled pass rate.

## 4. Companion v0 and MCP: one bot

Build the UI around a bot list from the start, initially with one available bot.
Each profile is `bots/<name>/SOUL.md` plus `bot.json`: stable ID, display name,
skin/voice references, role backends, settings preset and memory reference.
Profiles belong to bots and can be reused across servers. Home, chests and other
world facts must also carry world/server identity.

Roles map to configured backends: talker handles persona/chat, planner handles
longer tasks, coordinator is absent for one bot. Keep this LLM planner role distinct
from the Java route planner. One model may fill multiple roles. Persona can shape
preferences, never permissions, protected areas or motor safety.

Use one intent/job path for web chat, in-game whispers and `/mcb`. Register the
command in the server mod so vanilla clients work. Item 18's first case stays
"you appear to be stalled, what is going on?": answer from current task, queue,
position/inventory, navigator state, last failure and idle evidence. Chat/model
latency must not block control. Expose cancel and a deterministic stop handled in
code, independently of model availability.

Deliver an MCP server in this phase with `say`, `status`, `goto`, `gather`, `build`
and `stop`, using the same validated intents and jobs. Start with local stdio for
external agents such as Hermes or Muse; do not require a second HTTP service.
Long tasks return a job ID; status reports progress/result and stop cancels it.
Retries must not silently duplicate a running job. External agents can retain
their own persona/memory; an internal talker is optional for this connection.
No agent gets direct motor access through MCP.

Create bespoke, versioned agent skills alongside the MCP tools. Teach agents to
discover capabilities and the active bot/world, choose settings, submit intents,
follow job IDs through completion, inspect failures and stalls, and stop or
cancel safely. Include concrete gather/build/follow workflows, retries that do
not duplicate jobs, and the same authority and protected-area boundaries as the
tools. Keep skills aligned with the actual tool schemas and verify their
examples against the companion contract; a tool list alone is not sufficient
agent guidance.

Revisit agent-side hooks and plugin/tool packaging during phase 4. Evaluate
whether they improve capability discovery, job follow-through and stop handling
for the chosen agents before choosing an integration. These are deferred design
choices, not additional frameworks or permissions to introduce in phase 2.

Command authority is an owner and allowlist, applied consistently to every ingress.
Other players' text is untrusted input. Enforce autonomy limits and protected
areas before job execution. Record companion sessions with config, prompt
identity, actions, outcomes and per-role latency/cost (unknown where unreported).
Extract prompts to versioned files after locating their current construction.

Done when an external MCP client using the bespoke skills can inspect and control one bot through jobs,
web/in-game chat shares that path, unauthorized commands are refused, and stop
works while a model is slow or unavailable. Verify the stall explanation from
real state and keep live evidence separate from mocked backend tests.

## 5. Web voice

Browser push-to-talk captures audio for local speech-to-text, then uses the same
chat/intent path. Play text-to-speech replies in the tab. Show recording,
transcribing and speaking states; allow cancellation and text fallback. Choose
local engines and measure latency before committing to dependencies. Voice does
not bypass command authority. Positional in-game voice and a client mod remain
later research, including Minecraft-version compatibility.

## 6. Skin spike and multiple bots

Investigate Carpet name-based skins, borrowing a named account's skin, and custom
PNG signing. Document compatibility, caching, credentials and whether an outside
service uploads the image before selecting a solution.

Multi-bot support is a separate refactor: `BodyRuntime` becomes an ID-keyed map;
body endpoints route through `/bodies/<id>/...`; Python `Bridge`, `CompanionBody`,
events, cancellation, permissions, memory and jobs follow that identity. Plan
compatibility for current single-body callers. Verify isolation and shared-world
resource conflicts before enabling concurrent jobs. Only then add the coordinator
role and multi-bot MCP addressing. MCP itself does not wait for this refactor.

## Delivery order and evidence

Phase 1 is implemented and locally verified. Deliver phases 2 through 6 in order. The
frontend builds on the resolved contract; it does not redefine runner behavior.

At each phase, record changes, focused automated checks and the exact live smoke
evidence still needed. Frontend work needs typecheck/build and user-flow checks;
future contract changes need resolution, validation and runner propagation checks.
Preserve existing regression evidence and qualification gates. A new dashboard,
MCP fixture, or mocked model response does not qualify a changed Minecraft body.
