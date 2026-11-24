# Local lab UI

For the short startup, interaction and shutdown workflow, see
[LAB_QUICKSTART.md](LAB_QUICKSTART.md).

## Current React interface

The current interface is a React/TypeScript app built with Vite and Tailwind,
using the pinned portable `@homenet/ui` package. The existing Python dashboard
serves the static `ui/dist` build and `/assets`; it remains the API and worker
owner. Build it before starting the dashboard:

```powershell
cd ui
npm ci
npm run build
cd ..
uv run --locked python -m mclab.dashboard
```

The dashboard launcher remains available from the repository root:

```powershell
powershell -NoProfile -File scripts/lab-ui.ps1
```

The launcher loads the ignored `.env` when present and opens
`http://127.0.0.1:8770/`. Use `--no-browser` to print the URL or `--port 8771` to
select a port. The direct entry point is `uv run --locked python -m
mclab.dashboard`; pass `--env-file .env` when launching directly with model
credentials. Without a built `ui/dist`, `/` falls back to the previous page;
`/legacy` explicitly serves that page when the React app is built. See
[ui/README.md](../ui/README.md) for build, test, and UI contribution instructions.

### Choosing a mission (Run something)

**Run something** goes in three steps, with a breadcrumb back: **What do you
want to test?** (one card per catalog section, with its line from
`dashboard_suites.SECTION_TEXT`; the developer sections show after **Show
developer tools**), then the section's missions, then the chosen mission's
settings. Its main fields (`basic_fields`) come first; the rest sit under **More
settings**, with the preset picker only when a preset applies. Choosing a mission
adds it to **Your run list** on the right; a row opens its settings again, ✕
removes it, **Start** runs the list top to bottom and **Check run list** shows
each run's exact settings. **Saved sets** load and save a list.

The header shows both servers with their state, the address to join (with
**Copy**) and **Start** when stopped. **Live activity** opens with **Where to
watch**: the server running the current job, its address and, for an arena, the
`/tp` into its spectator box (from `GET /api/where`, polled every ten seconds).
A server opened to the LAN (`"lan": true`) also shows the address another PC on
the network joins at. A match that names people says to type `/mcb ready` once
in the game: it waits for everyone named (see ARENA_RUNTIME.md "Waiting for
players"). The lab is a flat test world on its own port, so joining it to watch an arena
shows an empty world. **History & builds** lists results (when, what, who,
outcome, median time; a row opens its `summary.json`) and house builds.

Fields read hints from `/api/schema` (`mclab.play_runs.decorate_schema`), so a
new choice is a catalog entry, not UI work:

| Hint | Shown as |
|---|---|
| `enum` + `enum_help` | A list with readable names and the chosen item's description under it. |
| `placeholder` | The default, named in the list ("Iron (default)") or the empty box. |
| `by_test` | Label, help and placeholder for one family (`count` is Matches, Rounds or Episodes). |
| `format: "choices"` + `choices` | Grouped checkboxes with one line of help each; `exclusive` options stand alone, `other` adds a box for names off the list, `empty` is the value when nothing is ticked. |
| `custom` (on `waves`) | A last list item that opens the field it names (`custom_waves`, `format: "waves"`) as a wave builder: waves of mob × count rows from `mobs`. |
| `format: "position"` | X Y Z boxes; an optional one may stay blank. |

`ui/src/forms.ts` holds the pure helpers (joining checkbox values, the wave text,
history rows, where to watch), unit-tested in `forms.test.ts`.
`effects.test.ts` fails the build of any `useEffect(() => expr)`: React calls the
returned value as the cleanup, and `window.scrollTo` returns a Promise in current
Chrome, which unmounted the whole app on the first tab switch (the blank Live
activity and History pages).

### Catalog, configuration, and queue

The dynamic catalog exposes nine families: gather, build, natural routes,
courses, models, battery, companion checks, and the play-server families `arena`
and `combat` (below). Forms come
from the server's `/api/schema`; preset choices use partial config files in
`config/presets/`. Saved suites in `config/suites/` contain references and
explicit overrides, not copies of the underlying configuration. Configure
override matrices, seeds, and repetitions, preview the resolved run settings,
and enqueue up to 60 jobs in a batch.

The app uses the existing detached, durable session worker and exclusive arena
ownership. Live status uses authenticated server-sent events; each connection
receives an `event: snapshot` with the full durable state on initial connect and
reconnect. The server bounds active listeners to eight. Refreshing the page or
reconnecting resynchronizes from backend state; closing a browser tab does not
own or stop a worker.

**Stop after batch** lets the active batch finish cleanup, then skips pending
jobs. **Cancel current run** requests cancellation of the active job. **Shut down
dashboard** stops the HTTP dashboard process; it does not stop Minecraft or
implicitly cancel the worker. Use the explicit queue/job controls for those
actions. Existing loopback binding, Host/Origin checks, UI-token authentication,
arena locking, and POST authorization remain in force.

UI tests and the production build do not launch Minecraft and do not qualify bot
behavior. Current full-suite and browser E2E verification status is tracked in
[IMPLEMENTED.md](IMPLEMENTED.md).

### Arenas and combat (the arena and play servers)

The **Arenas and combat** section drives two game servers, not the lab, through
the same scripts and CLI an operator uses, each in a child process bound to its
instance (`MCLAB_INSTANCE=arena` or `play`). `mclab.play_runs` owns these rows
(`FAMILY_SERVER`: arena runs go to the arena server, combat trials to the play
server):

| Row | Server | What it runs |
|---|---|---|
| Arena | arena | `scripts/arena-hunt.py --world --mode GAME`: a match, `count` in a series. The arena server builds the lobby and this arena first when they are missing (see [ARENA_RUNTIME.md](ARENA_RUNTIME.md#the-arena-server)). |
| Combat trials | play | `scripts/combat-trial.py`: each weapon against each mob, `count` times round. |
| Stop an arena's match *(Arena tools)* | arena | `mcarena arena stop <arena>` |
| Build an arena *(Arena tools)* | arena | `mcarena arena build <arena> --at X Y Z --wait` |
| Remove an arena *(Arena tools)* | arena | `mcarena arena remove <arena>` (Restore clears it to air) |

**Arena tools** is an advanced section (hidden until lab tools are shown): a match
builds what it needs, so these are for by-hand work and, later, random worlds.

The **Arena** row is a short wizard: **Arena**, **Game**, **Players**, **Waves**,
**Matches**. **Game** is `run.game` (`play_runs.GAMES`): **PvE** (`waves`:
everyone against the mob waves), **PvP** (`teams`: team against team, waves
optional: **Waves** then offers **No mob waves**) or **Kill race** (`race`: teams
race to kill the most wave mobs). **Players** is `run.roster`, a list of
`{name, team, op}` (`run_config.RosterEntry`): the bot's row, a row per person
added by Minecraft name, a team select in PvP and Kill race (only the arena's
own teams, read from its plan: `run.arena.enum_meta` gives `teams`, `per_team`
and `players`), and an **Op** box that makes that person an operator on the
arena server when the series starts (`--op`; never the bot). A team game puts
every player on a team, the smallest first; switching to PvE takes the teams
off. The form refuses, before Start, what the launch would refuse
(`play_runs.require`): a duplicate or invalid name, more than 16 players, a team
game on one team, a team over the arena's `per_team`, or a PvE match over the
arena's player room. For 8v8 use `hall` or `crossroads` (10 a team).

The choices come from the files, every time the page loads: **Arena** lists the
plans in `arenas/*.json` (not the arena server's `lobby`), **Waves** the presets
in `arenas/waves/*.json`, and the weapons and mobs are whatever `combat_trial`
accepts. A new arena plan, wave preset or weapon appears without a dashboard
change. **Make my own waves** opens the wave builder; it stores one wave per
comma (`pillager*2,pillager+vindicator`) in `custom_waves` instead of a preset.

The **Kit** is a checkbox per part of `combat_trial.KIT_PARTS` (weapons,
protection, supplies), handed to every player, or **Keep what they carry** alone; nothing ticked is bare
hands. **Weapon material** (sword, axe, spear: `WEAPON_TIERS`, wooden to
netherite) and **Armor material** (`ARMOR_TIERS`, leather to netherite) both
default to iron and apply to arena hunts and combat trials alike (`--tier`,
`--armor`). A new kit part is one `KIT_PARTS` entry and its items in `kit()`; a
weapon such as the mace needs the mod's `CombatLogic` to fight with it first. A new
kind of play run is one `PlayRun` entry in `play_runs.RUNS` plus its command;
each row locks the family fields it does not use, so they are hidden and refused.

A session that holds an arena or combat run starts with **Start arena server**
and/or **Start play server** (setup operations `arena-start` / `play-start`,
`play_runs.ensure_server`): each does nothing when that server's bot is already
ready, and otherwise starts the server (`mcarena` / `mcplay server start`) and
waits up to five minutes for the bot. The header shows a badge, the join address
(and the LAN address) and a **Start** button for each of the lab, play and arena
servers; `/api/status` and the live snapshot carry `state` (lab), `play` and
`arena` (each companion host's `GET /health`, probed at most every five seconds).
The dashboard must run with the model credentials the companion hosts need:
`uv run --env-file <.env> --locked python -m mclab.dashboard`.

Matches and trials write the script's own record plus a `summary.json` (a
history row: one episode per match or fight, a match's with its `winner`,
`bot_won`, per-player `kills` and `team_kills`) to `results/arena/<stamp>/` and
`results/combat/<stamp>/`; CLI runs keep writing `results/companion/arena/` and
`results/companion/trials/`. **Stop** / **Cancel** on a running match interrupts
the script and then stops its match on the arena server (`mcarena arena stop`),
so no mobs are left running and the players go back to the lobby. Positions (`corner`, `spot`, like `start`) are fields with
`"format": "position"` in `/api/schema` and show as X Y Z boxes.

## Legacy dashboard page

The rest of this document preserves the previous dashboard's operation,
configuration, comparisons, companion jobs, and evidence references. Those
controls are available at `/legacy`; use the React interface instructions above
for the current root page. Historical proof links and run references remain here
for audit and prune retention.

Running the launcher again opens the existing dashboard and exits successfully.
It does not start another UI server or interrupt an active session. `--no-browser`
prints the existing URL instead. The running UI's port is recorded in
`runtime/dashboard-ui.json` so reopening also works with a custom port.

### Legacy dashboard: stopping or restarting

Click **Shut down dashboard** to stop the dashboard's HTTP server and release its
process/lock. The page displays a stopped message. Run `scripts/lab-ui.ps1` again
and reload the page to use newly changed Python code. Refresh configuration only
rereads input files; it does not reload Python modules. Closing a browser tab or
rerunning the launcher without a restart opens the existing process.

To restart an older dashboard that does not have the shutdown button, or replace
an unresponsive dashboard, run:

```powershell
powershell -NoProfile -File scripts/lab-ui.ps1 -Restart
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

## Legacy dashboard: run operation

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

## Legacy dashboard: configuration and evidence

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

## Legacy dashboard: companion jobs

Run type **Companion** starts one of the standalone scripts that drive the lab
server through `mclab.bridge.Bridge`: the live checks (`scripts/check-crafting.py`,
`check-placement.py`, `check-animals.py`, `check-containers.py`; only scripts that
exist are offered), **Gather** (`run-gather.py`, optional iron pickaxe) or
**Build** (`run-build.py`).

- **Starting.** Pick the job. Gather and Build ask for start X Y Z (a feet cell on
  open ground; integers within ±30,000,000). Build also takes a build file (a
  dropdown of `tests/fixtures/schematics/*.build.json`, `builds/*.build.json` and
  `results/companion/*/*.build.json`) and a **world**: a saved natural seed
  (`runtime/natural/natural-<seed>/`, passed as `--seed`) or the flat lab world
  (no `--seed`; nothing to gather there, so tick **Stock supply chests**). The
  form shows the last run of the chosen build with a **Use** button that copies
  its start and seed. **Stock supply chests** is `--supply`, with the coal form
  (`--coal item|block|ore`) beside it. Under *Placement and inspection*: offsets
  dx/dz, rotation 0/90/180/270, `--margin` (0-16, default 1) and `--inspect`
  (the body stays until a file named `STOP` appears in the run folder, or Kill
  now). `--resume`, `--empty`, `--stage`, `--preview`, `--scaffolding` and
  `--no-show-markers` (the in-game red route, green stance and yellow block markers
  are on by default for builds; this starts them off; create or delete
  `<run folder>/MARKERS` to flip them while it runs) are command-line only. The worker writes what it started the run with to
  `launch.json` in the run folder.
  Every input is validated server-side; build and schematic paths must resolve
  inside those folders. There is no "Use my position" button: the lab state does
  not expose player positions to the dashboard, so read X Y Z from F3.
- **Convert schematic.** Choose a `.litematic` from `tests/fixtures/schematics/`
  or `builds/`; the dashboard runs the `mclab schematic convert` code in process,
  writes `builds/<name>.build.json` and shows the material list and flags. The
  new file is selected in the build dropdown.
- **Rules.** Companion jobs use the same session worker as course runs: one
  active session, Stop after batch, Kill now. If the lab server is stopped when
  a session starts, the session boots it first (a `server-start` setup job named
  "Start lab server"); a failed start stops the queue before any run. Every run
  type gets this except a battery that restarts the server itself. The React
  UI also shows **Start lab** next to the server badge when it is not running.
  The scripts take
  the arena lock (`recorder.exclusive_arena`) themselves, so the worker does not;
  if another runner holds it the script fails and the job is an error. Kill sends
  Ctrl+Break (SIGINT off Windows) so the script's `finally` despawns its body,
  and forces the process after 120 s.
- **Live view.** The script's stdout is the session console log (child runs with
  `-u`). For the running job the dashboard follows the newest
  `results/companion/<stamp>/` created after the job started and shows: the
  optional `progress.json` stage/count bar, `build.json` totals (stop, steps,
  counts, skipped) once written, the case tally from `summary.json`, and the last
  12 op results from the event log (`op done/failed/rejected`, reason).
  Anything else writing into `results/companion/` at the same time can be
  mistaken for the job's folder.
- **History.** Rows show the job as the stage (`crafting`, `gather`, `build`, ...)
  with passed/failed counts. The job is read from `job.json` (written by the
  worker when the script exits), else `progress.json`'s `job`, else
  `build.json` / `gather-*.json`; older folders read "companion check".

### Legacy dashboard: house-build stats and comparison

The **House builds** section lists every `results/companion/<run>/` that has a
`build.json`, checkpoints or a `progress.json` with job `build`, whether or not
it wrote `summary.json` (a killed run does not). Each row is compared with the
previous run of the same build file; clicking a row opens the full comparison,
and the baseline can be changed to any other build run. A red banner or cell
means worse by 10% or more: result, total time, building time, blocks per
minute, longest wait without a successful edit and stalls. Incomplete plans can
also reflect insufficient known terrain, so they are not labeled search-limit failures.

`src/mclab/build_stats.py` computes the numbers (also
`python -m mclab.build_stats <run folder>...`):

- From `build.json`: result (`stop`, else `job_stop`), house cells correct /
  missing / wrong / unknown, site cleared / filled / blocked / trees, stalls and
  recoveries, cells given up by reason, refill trips, shortfall. A run with no
  `build.json` reads `progress.json` (stalls, site counts), a `KILLED*.txt` /
  `ENDED*.txt` note, or `error.txt`.
- From one streamed pass over the event log (plain or `.gz`; only the first
  bytes of each line are examined, the large sensing lines are never parsed):
  total time (first request to last work request or selected result), estimated
  house blocks placed and blocks per minute while building, placements and breaks
  the body refused by reason, route plans by status, longest wait without a
  successful edit. Site materials and stations are excluded from that estimate,
  including if they are part of the actual template.
- Time per phase is **estimated** from the log, which has no phase markers:
  gathering ends at the last craft/furnace/container result before building;
  tree clearing ends at the last log broken after that; site preparation ends
  and building starts at the first placed block that is not dirt, scaffolding
  or a station. Refill trips during the build count as building.
- The build file and seed come from `launch.json`, else the first checkpoint;
  runs with neither show "not recorded" and have no automatic baseline.

Results are cached in `<run>/build_stats.json` (keyed by the log's size and
time, and kept after `prune` removes the log). The dashboard computes missing
ones in a background thread, newest first, and skips a log written in the last
60 s. Log file names live in one place, `build_stats.LOG_NAMES`, read through
`event_logs()` / `iter_events()`: add `timeline.jsonl` and `blocks.jsonl` there
when the log is split (`seen.jsonl` holds nothing the stats read).
Log activity is not proof that a build is still working: an inspection may keep
the log active after the result is recorded. The dashboard has no reliable
inspection-wait marker or graceful finish control. Cache versions that cannot be
recomputed after log pruning are shown as unavailable rather than current stats.
The metric inventory and audit limitations are in [DASHBOARD_BUILD_REVIEW.md](DASHBOARD_BUILD_REVIEW.md).

### Legacy dashboard: progress hook

`src/mclab/companion_progress.py`: `write_progress(folder, job=None, stage=None,
done=None, total=None, detail=None)` atomically replaces `<folder>/progress.json`
as `{"job", "stage", "done", "total", "detail", "updated_at"}`; `write_job(folder,
job)` writes `job.json` as `{"job": name}`. Both ignore I/O errors and never
raise. The dashboard polls them every 3 s; scripts need not call either.

## Legacy dashboard: extending

`src/mclab/dashboard_runs.py` discovers original inputs, validates selections and
adapts them to runner calls. Add a body or stage there and expose its supported
options in `dashboard.html`. `dashboard.py` owns the local HTTP server and worker
session lifecycle. `dashboard_metrics.py` derives comparison measurements and
coverage from those original summaries. Keep new body configuration in the body's existing config
system, and keep evaluator outcomes in the existing lab evidence format.

The service binds only `127.0.0.1`, validates the Host and mutation Origin, and
uses a per-process mutation token. It has no arbitrary shell-command endpoint or
general file browser. It is intended for this desktop, not network hosting.
