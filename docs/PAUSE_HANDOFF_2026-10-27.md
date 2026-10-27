# Awareness and movement sprint: pause handoff

Paused at Marc's request on October 27, 2026 because the current five-hour usage
block was nearly exhausted. Marc reports that usage resets at **11:56 a.m.
Eastern**. Resume only when requested. The full sprint is **unfinished**.

## Objective and decisions

Continue developing MCB's server-side Carpet body rather than switching its
movement to Baritone without testing the investment. Baritone remains the
reference. The old natural-terrain result was Baritone 10/10, MCB 0/10; these
new development fixtures do not supersede that comparison or prove parity.

The governing scope is [AWARENESS_MOVEMENT_SPRINT.md](AWARENESS_MOVEMENT_SPRINT.md),
with item status in [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json). Completing
the earlier awareness/hill milestone does not complete this sprint.

Marc explicitly requires a rolling plan for the next **three or four moves**,
including future blocks to break/place, rather than one-block decisions. The
implemented terrain horizon is four moves. Later steps stay queued and are
revalidated against fresh observations; changes trigger replanning. Shorter plans
are allowed when the goal lies inside the horizon. Persistent map storage uses
spatial indexing and compressed sections; an embedding model is not required for
coordinate/path recall. Semantic place search remains future work.

## Implemented

- Legacy, extended-visible and chunk geometry profiles; adjustable horizontal and
  vertical reach, independent entity reach/occlusion, bounded extra acquisition,
  partial-coverage metadata and exact access partitions. Legacy contracts remain
  frozen. Client-equivalent chunk admission/calibration is still missing.
- SQLite schema-two compressed palette sections, transactional migration from
  schema-one cells, explicit air/unknown handling, world/generation/dimension/
  awareness isolation, bounded asynchronous writes and fail-closed gaps.
- Current/session/persistent memory modes, historical access control, hot-cell
  cap and accounted hot/persistence byte budgets. Accounting bounds retained cell
  data, not total process RSS. Recall is still explicit, not automatic.
- Bounded segmented walking search, a cancellable background search lane,
  next-segment prefetch/adoption, and fresh local motor safety checks.
- Background map admission, copying and byte accounting, publishing owned
  versioned planning views. Driver observations are borrowed immutably until
  admission finishes. The driver does not mutate them.
- Four-move mixed terrain forecasts with predicted edits, stock/reserve tracking,
  protected footholds and queued-step revalidation. Ordinary Survival hotbar,
  ray/reach, mining/use inputs and observed edit confirmations execute the plan.
- Straight-route motor lookahead across up to three collinear, level nodes,
  stopping at turns/height changes and retaining fresh safety checks.
- Sparse section-palette transport for development profiles. Immutable facts are
  captured on the server thread; lazy cached palette construction/serialization
  uses HTTP threads. The driver validates and expands samples, while the recorder
  retains compact wire payloads. This is not yet byte-budgeted delta delivery.
- Native lethal-hit damage reporting and per-material pickup counters. Placement
  confirmation accounts for mined items refilling inventory between snapshots.
  Issued placements remain protected even if confirmation fails.

Primary implementation files are `awareness.py`, `spatial_memory.py`,
`spatial_archive.py`, `section_codec.py`, `memory_policy.py`,
`navigation_segmented.py`, `planning_worker.py`, `observation_worker.py`,
`route_search.py`, `terrain_route.py`, `terrain_steps.py`, `terrain_transport.py`,
and the development sensor/terrain classes under `mod/src`.

## Evidence and performance

Earlier walking/cut/placement cohorts passed ten layouts each. Four-move mixed
forecasts passed ten foreground layouts and later ten background layouts. Those
are development proofs, not new scored legacy runs.

Latest substantial cohorts before pausing:

| Cohort | Result | Aggregate motor p95 | Other findings |
| --- | --- | --- | --- |
| `132148920054Z`, mixed with transfer accounting | 10/10; restored | 22.03 ms | Native sampling p95 1.55 ms; two discarded stale actions; zero invalid attempts |
| `132526447497Z`, longer prefetch routes | 10/10; restored; all adopted prefetch | 3.30 ms | Native sampling p95 0.97 ms; zero discarded stale actions/invalid attempts |
| `134248294564Z`, compact transport mixed | 10/10; restored | 22.12 ms | Decode + motor p95 **28.58 ms**; native sampling p95 1.43 ms; response-body p95 about 67 KB; one discarded stale action; zero invalid attempts |

Full result paths begin `results/hills/20261027T.../`. Tracked copies of these
summaries and metrics are in [the evidence directory](evidence/2026-10-27-awareness-navigation).
Large raw event logs, SQLite archives, installed binaries and runtime state remain
local under ignored `results/` and `runtime/`; they are not pushed. Source hashes
and result hashes preserve the connection to those raw artifacts.

The 20 ms target is **not met** on mixed terrain. Compact transport reduced map
payloads roughly elevenfold, but did not eliminate control latency. Motor timings
use `perf_counter`; HTTP/decode measurements now use `perf_counter_ns` as well.
The earlier Windows uptime clock quantized short HTTP/decode durations. The first
compact prototype at `133958464312Z` has that limitation; do not use its decoder
timings for acceptance. The later ten-case compact cohort has high-resolution
timings, including decoding rather than hiding it outside the reported work.

Native `sample_ms` covers terrain sampling, not every snapshot/serialization
operation. Reported MSPT values are rolling server averages, not individual tick
costs or measured TPS. `policy_wire_bytes` measures HTTP response-body bytes;
canonical recorded JSON sizes are a separate metric.

## Last experiment and clean checkpoint

The last completed live experiment was `20261027T135011571239Z`: one mixed case
with frequent `sleep(0)` yields in admission and section compression. It reached
the goal and restored the lab, but motor p95 worsened to **40.78 ms** and
decode + motor p95 to **46.84 ms**. Its receipts are preserved in the evidence
directory. **The yield experiment was reverted before this checkpoint.**

Retained small fixes include a single admission timestamp per sensor batch,
combined copying/accounting, and checking durable-writer health when polling the
admission lane, including repeated samples. These final Python changes have unit
coverage but have not received a fresh ten-case live freeze after the revert.

The leading next investigation is CPU/GIL contention between admission,
compression, recording and control work. Mode-agnostic latency spikes support
investigating this, but the cause is not conclusively profiled. A separate process
owning map admission, persistence and search is a possible solution. **No process
worker implementation was started.** Avoid shipping full hot-map snapshots over
IPC every observation; that could recreate the same copying/serialization cost.
Prefer compact observations in and bounded route/terrain plans plus status out.

## Remaining work

1. Resolve control-processing latency with meaningful measurements; retain failed
   trials. Finish byte-budgeted transport deltas, backlog/gap resynchronization,
   client-style chunk admission and actual Baritone coverage calibration.
2. Add automatic bounded historical recall, coarse places/connections, and the
   Waystone/teleport edge schema. No Waystone integration is implemented yet.
3. Complete safe diagonals/sprint controls, collision shapes/slabs/stairs,
   configurable drops, surface water/air recovery, ladders/dismount, one/two-block
   gaps, displacement/interruption/changed-geometry recovery and failed-edge tests.
4. Add staircases, short bridges and pillaring; complete material/protected-area/
   tool/wear/time/risk knobs and forbidden-action cases. Mixed hills are working,
   but they do not establish these other movement families.
5. Run ten generated cases per required family and corresponding adversarial
   configurations. Preserve failure evidence and honest source identities.
6. Run the five-route same-controller legacy/visible/chunk comparison, holding
   the old goal mover fixed. Then freeze ten new natural routes across at least
   three new seeds: three calm attempts per route per entrant, MCB and Baritone.
   Required MCB target is at least 27/30 and median per-route paired-success time
   ratio at most 2x Baritone. Include failures/timeouts. Run night diagnostics on
   three routes, three attempts each per entrant, with damage/death/exposure data.
7. Measure acquisition, search, decode/control, MSPT/TPS, actual transport,
   memory/backlog and starvation invalids. Final source-bound legacy qualification:
   G2 20 resets/29 adversarial checks, C2–C5 20 resets each, sensor checks and lab
   restoration. Publish a final result without promoting old-source qualification.

## Runtime and resumption

At the checkpoint, no live cohort, build, background command or test was left in
flight. The local lab server remains running, idle, with zero connected players,
on the restored `world` lab. Last checked server PID: `91112`; Minecraft 26.3,
protocol 777; game port 25575. Recheck live status rather than trusting the PID.
No action was taken to close the previously running Baritone client; its current
connection/process state has not been reverified during this pause.

Installed mod source hash:
`c23b9f84d8ba00591e6606a2b7250a85455f8c4fc98c43c8cfa2b3c1afb7f75b`.
Installed jar SHA-256:
`4ac4d8bf533a3f178fa4cd82b2f22847332832343d58851e4360f71189db4929`.
Java build and its geometry/awareness/palette verification tasks passed. This mod
differs from the last legacy-qualified source: **legacy scored admission must
remain closed until requalification**. Earlier gate proofs remain historical.

Final checkpoint checks: **405 Python tests passed** in
`results/background/pause-python-tests.xml`; Ruff lint and formatting checks
passed, and `git diff --check` passed. The Java build verification above passed
for the installed mod source. No tests or commands remain in flight.

All shell commands must start with `rtk` (see `C:\Users\marcm\.codex\RTK.md`).
There is no `.codegraph` index. Do not create one without the user's decision.
Do not spawn agents without explicit applicable authorization. Preserve all
existing work and do not reconfigure shared inference providers.

Useful commands, after checking that no other runner owns the arena:

```powershell
rtk proxy uv run --locked python -m mclab.cli server status
rtk proxy uv run --locked ruff check src tests scripts
rtk proxy uv run --locked pytest -q
rtk proxy uv run --locked python scripts/check-hill-navigation.py --kind mixed --variants 10 --background-planning
rtk proxy uv run --locked python scripts/check-hill-navigation.py --kind prefetch --variants 10 --background-planning
rtk proxy uv run --locked python scripts/summarize-navigation-performance.py results/hills/<timestamp>/summary.json
```

Use `--terrain-encoding cells` for a transport comparison; default is
`sections-v1`. Memory knobs are documented in [SPATIAL_NAVIGATION.md](SPATIAL_NAVIGATION.md).
Run performance cohorts without concurrent builds, tests or heavy log analysis.
The server admits at most 32 retired qualification bodies per launch; restart
between bounded cohorts when needed. An explicit body-cap rejection is terminal;
a mere slow call is not permission to restart an active attempt. Verify actual
state first. Install a rebuilt mod only while the server is stopped, and wait
for actual `running`/protocol readiness after starting it.

Local ignored helpers `runtime/requal-awareness.py`, `runtime/publish-rolling.py`
and `runtime/finalize-hills.py` contain historical workflows. Do not blindly run
the latter two: they bind older evidence/smaller milestones. The requalification
helper runs each reset family after a fresh server start; inspect it before use.

Resume from this handoff and the acceptance tracker; do not repeat completed
setup or call the full sprint complete based on these fixture results.
