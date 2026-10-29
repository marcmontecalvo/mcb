# Local lab UI

Run from the repo root:

```powershell
rtk proxy powershell -NoProfile -File scripts/lab-ui.ps1
```

The script loads the existing ignored `.env` when present, runs the project's
locked Python environment and opens `http://127.0.0.1:8770/`. Optional arguments:
`--no-browser` and `--port 8771`. The direct entry point is
`rtk proxy uv run --locked python -m mclab.dashboard`; add `--env-file .env` to
`uv run` when model credentials are needed. Python's standard library serves the
page; no packages, JavaScript framework or build step are added.

Running the launcher again opens the existing dashboard and exits successfully.
It does not start another UI server or interrupt an active session. `--no-browser`
prints the existing URL instead. The running UI's port is recorded in
`runtime/dashboard-ui.json` so reopening also works with a custom port.

### Stopping or restarting the dashboard

Click **Shut down dashboard** to stop the dashboard's HTTP server and release its
process/lock. The page displays a stopped message. Run `scripts/lab-ui.ps1` again
and reload the page to use newly changed Python code. Refresh configuration only
rereads input files; it does not reload Python modules. Closing a browser tab or
rerunning the launcher without a restart opens the existing process.

To restart an older dashboard that does not have the shutdown button, or replace
an unresponsive dashboard, run:

```powershell
rtk proxy powershell -NoProfile -File scripts/lab-ui.ps1 -Restart
```

The restart option stops only the verified dashboard Python process listening on
the recorded loopback port, then launches the current code. Both shutdown methods
leave Minecraft and any active test worker running. Use **Stop after batch** first
if you also want the test queue to stop after its current batch cleans up.

During server startup, both mods expose HTTP before publishing their first
tick snapshot. The bridge waits up to 30 seconds for those snapshots before
submitting commands. Initial `{}` or `{"state":"idle"}` responses mean the
snapshot is not ready; they are not evidence of a different server launch.
A real launch-ID mismatch still invalidates the attempt, with the endpoint and
expected/received IDs in the error. New sessions load the current runner code;
an already running test worker does not reload it mid-batch.

## Running

1. Use **Start lab** if the lab server is stopped. It waits for the existing
   server status to report protocol-ready `running`. Existing installation,
   properties and EULA checks apply.
2. For course comparisons with Baritone, use **Prepare Baritone**, then
   **Launch Baritone** with Prism already signed in. The client must have joined
   the lab. The natural runner manages its own per-attempt client relaunches.
3. Choose a run type, setups and stages, then **Start session**. Multiple setups
   run sequentially, using the same selected seeds or routes. The server evaluator
   remains responsible for outcomes.

Supported first-draft combinations:

| Run type | Setups | Selection |
| --- | --- | --- |
| Courses | MCB V8, Baritone | C2-C5, 1-20 distinct explicit seeds per batch |
| C1 deterministic | MCB | C1 only, seed 0, one development attempt |
| Natural terrain | Segmented MCB, V8, Baritone | Existing route JSON, seed, calm/night/both, 1-3 repetitions |
| Model smoke | Enabled entries in `config/backends.json` | One C1 attempt per backend using its existing request cap |

There is no Mineflayer adapter or simultaneous race mode yet. Existing scored
model-smoke prerequisites still apply; the UI does not bypass qualification.
Course MCB launches explicitly use development mode. Current source installation,
course reset, client readiness and arena locks are enforced by the original
runners. Natural runs require an existing snapshot for the selected world seed.
Do not exceed the server's retained-body limit; the UI does not silently restart
the lab to hide it.

Elapsed clocks run continuously. During an attempt, the UI reads the original
`events.jsonl` observation's evaluator deadline and tick to show game time left,
plus the sample age. This is recorded game time, not a wall-clock prediction:
lag can change the real duration. Preparation, reset and cleanup show their
recorded phase when available. Before a running observation exists, the UI says
the timeout clock is waiting. No observer heartbeat or extra policy requests are
sent by the UI.

**Stop after batch** lets the active batch finish its normal cleanup, then skips
the remaining batches. It does not interrupt the current attempt. Closing the
browser or stopping the UI with Ctrl+C leaves the worker running. Restart the UI
to reconnect. A worker that exited before completion is marked interrupted;
inspect its console log and lab state. A runner/cleanup error stops the queue;
ordinary task failures remain results and do not trigger automatic retries.

## Shared configuration and evidence

### Natural-terrain development setup

For the documented seed-1337 Phase 0 setup, choose **Natural terrain**, select
**MCB segmented** (and Baritone when comparing), and use:

| Field | Value |
| --- | --- |
| World seed | `1337` |
| Existing route file | `results/natural/routes-1337.json` |
| Conditions | Calm for an initial development run; Both for calm/night diagnostics |
| Repetitions | `1` |
| Awareness profile | `docs/evidence/2026-10-28-phase0/awareness-chunk-64.json` |
| Movement profile | `docs/evidence/2026-10-28-phase0/movement-passing-families.json` |
| Memory profile | Leave blank for runner defaults |
| Server view distance | `4` chunks |
| Terrain profile | `docs/evidence/2026-10-28-phase0/terrain-passing-families.json` |
| MCB segmented control profile | `dev-pipelined-v1` |

That table preserves the original Phase 0 setup. For the new native per-tick
motor, select `dev-native-route-v1` (now the dashboard default for new sessions).
It hands route segments to `mcb-body` instead of scheduling short Python control
commands. It is a development profile, does not support swimming, and retains
the runner's installed-source checks. Existing sessions keep their recorded
control profile; reload the dashboard to see the new option.

The profile inputs offer matching existing files as suggestions when focused or
typed into. These are suggestions, not automatic selections. Use **Refresh
configuration** after adding files. Discovery checks JSON files at the root and
one directory level below `config`, `runtime`, `docs/evidence`, and the supported
result directories (`natural`, `spatial`, `hills`, `background`, `awareness`). It
validates profiles against their original schemas and does not recursively scan
archived source trees. Files elsewhere inside the checkout can still be entered
by their relative path.

This movement profile enables diagonals, sprinting, shaped-block traversal,
drops up to three blocks and gaps up to two blocks. Swimming stays disabled.
The terrain profile enables bounded breaking/placing, bridging, support stacks
and pillaring. These are development inputs retained with the Phase 0 evidence;
do not edit them to configure a different experiment. Create a separate profile
in `config` and refresh instead.

Blank fields use runner defaults: legacy 8-block awareness; movement without
sprinting, diagonals, shaped blocks, gaps or swimming and with a one-block drop
limit; persistent memory with historical geometry and 128 MB active-map and
persistence-queue budgets; and terrain edits with eight-break/eight-place budgets
but no bridging or pillaring. Blank view distance keeps the server setting.
Movement, memory, terrain and control selections apply to segmented MCB;
awareness applies to MCB entrants. Baritone uses its own client awareness.

For matched-awareness comparisons, also verify the Baritone client's render
distance is at least four chunks. Choosing a profile does not establish parity
or qualification. See [the navigation direction](NAVIGATION_DIRECTION_2026-10-28.md)
for the current phase, known limitations and outstanding source-bound checks.

### Original files and session records

The original files are authoritative:

- `config/lab.json`: server settings.
- `courses/profiles.json`: course definitions.
- `config/backends.json`: models, endpoint selection and budgets.
- `config/initial_body_profile.json`, `config/navigation_body_profile.json`:
  existing body admission profiles.
- `config/baselines.lock.json`: pinned Baritone artifact.
- Existing awareness/movement/memory/terrain JSON files selected by path.

Edit these originals in your editor. Refresh configuration to discover new
choices. Profile/route fields also accept a path to another existing JSON file
inside the checkout, so discovery does not restrict where agents store profiles.
The UI stores file references, not a separate copy of bot configuration. Each
batch rereads its inputs; editing a profile between batches changes the later
batch. Original runners retain their own resolved configuration and provenance
in evidence. The UI does not edit frozen historical artifacts.

`results/sessions/<id>/session.json` contains selected jobs, original input paths,
timestamps, queue status and links to original runner outputs. `console.log`
contains the worker's output. These are session records, not another configuration
store. The launch script passes credentials through the process environment.

History reads `results/<suite>/<run>/summary.json` without scanning archived
source trees. It retains unreadable, failed, invalid and incomplete records.
Historical natural summaries containing several entrants appear as separate
comparison rows, all linking to the unchanged original summary. Select two to
four rows for a human-readable table of completion/failure counts, successful
completion time, recorded HP lost, deaths, blocks broken, confirmed placements,
inventory use and environmental measurements. Partial measurements show their
coverage; unavailable values say `Not recorded`. Game seconds are ticks divided
by 20, while recorded real elapsed time is shown separately. Failure durations
are excluded from successful completion timing but retained in total attempt time.

Expand **Per-attempt results** for individual route/condition/repetition or seed
comparisons, and **Recorded setup** for readable configuration fields. Confirmed
placements, server counts and inventory-use estimates stay separate. A lower
time or health value is highlighted only when recorded tasks match, all attempts
succeeded and complete measurements are available. These are diagnostics, not a
declaration of equivalent awareness or qualification. Different configurations
stay separate; there is no automatic pooling of cohorts. Original JSON remains
available from each run's detail button, outside the comparison table.

## Extending

`src/mclab/dashboard_runs.py` discovers original inputs, validates selections and
adapts them to runner calls. Add a body or stage there and expose its supported
options in `dashboard.html`. `dashboard.py` owns the local HTTP server and worker
session lifecycle. `dashboard_metrics.py` derives comparison measurements and
coverage from those original summaries. Keep new body configuration in the body's existing config
system, and keep evaluator outcomes in the existing lab evidence format.

The service binds only `127.0.0.1`, validates the Host and mutation Origin, and
uses a per-process mutation token. It has no arbitrary shell-command endpoint or
general file browser. It is intended for this desktop, not network hosting.
