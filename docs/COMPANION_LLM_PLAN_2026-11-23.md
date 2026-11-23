# Companion LLM wiring (Nov 23, 2026)

Status: **live verification in progress** on `feat/companion-llm` (section 7).
Verified on the play server (Nov 23): spawn at the world spawn and idle without
release, `status`/`list_places` over the real MCP stdio server, `goto` 30 blocks
(6.5 s), `stop` mid-route (cancelled in about 0.3 s), a failed start at a bad saved
spot falling back to the world spawn. Still to do with a player in the world:
`follow`, chat "come here", a deliberate death (`/kill MCBot`) and respawn, stop with
the model unreachable, and a daytime gather. This is phase 4 of
[LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) ("Companion
v0 and MCP: one bot"), done with two decisions from Nov 23:

- **Both LLM paths at once:** an MCP stdio server for external agents (Claude
  Code, Hermes, ...) *and* in-game chat answered by a local model (Atom Qwen).
  Both go through one intent/job path.
- **A separate play server now** (the Nov 1 decision, never built): its own
  world and ports, so playing with the bot and lab runs can happen at the same
  time.

D6 holds throughout: a model picks tasks, never motor actions, and model
latency never sits in the motor loop. `stop` is handled in code before any
model sees the message.

## Why the lab path is not enough

Today `/mcb build` starts `run-build.py` through a dashboard session. Each job is
a lab episode: it restores a seed world, `natural_start` freezes the clock at
noon, turns mob spawning off, clears mobs and items and replaces the inventory
with a kit, and the arena is wiped when the run ends. Nothing persists between
commands, commands cannot chain, and `stop` kills the worker process. A
companion needs one body that lives in a kept world, keeps its inventory and
takes task after task.

## Architecture

```
 Minecraft play server (mcb-body + Carpet + Fabric API; no mcb-lab)
   │  body bridge 127.0.0.1:<bridge_port>: /admin /controls /policy /state /events /tell /say
   │  events: player_command, player_chat (with sender position), op results
   ▼
 companion host process (one per play instance; started by the play supervisor)
   ├─ session: one Carpet body by a stable name, heartbeat, idle observe loop
   ├─ worker thread: runs one job at a time on CompanionBody (goto/follow/gather/build)
   ├─ cancel token: checked in Bridge.state(), so stop ends any wait at once
   ├─ ingress thread: own /events cursor; player_command → intents,
   │                  player_chat → talker; "stop" handled in code
   ├─ talker thread: OpenAI-compatible tool calling (Atom Qwen), same tools
   └─ control API 127.0.0.1:<companion_port> (bearer token): POST /call, GET /events
        ▲
 mcb MCP server (stdio, spawned by the external agent) ── calls the control API
```

Everything that can change the world goes through `Host.call(intent, args,
actor)`. MCP, the talker, `/mcb` and the HTTP API are thin adapters.

## 1. Play instance

`MCLAB_INSTANCE` selects a server instance; unset means `lab` and nothing about
the lab changes. A named instance reads `config/instances/<name>.json` and keeps
its runtime files under `runtime/instances/<name>/` (`server/`,
`supervisor.json`, `launch.lock`, `supervisor.log`, `server-console.log`,
`companion-host.json`). Toolchains and downloads stay shared in `runtime/`.
The arena lock is per instance. A `mcplay` console script sets the instance to
`play` and runs the normal CLI. Start it with
`uv run --env-file .env --locked mcplay server start`: the supervisor and the
companion host inherit that environment, which is where the talker finds
`ATOM_API_KEY` (without it the bot answers chat with "I can't think right now";
jobs, MCP and stop still work).

The play instance config has its own ports (game 25565, bridge 8775,
supervisor 8776, companion 8777), `"lab_mod": false`, a normal (not flat) world,
and `"companion": {"bot": "default"}`. Bootstrap writes play-world
`server.properties` (default world type, its own seed, normal difficulty,
view distance 10, spawn protection 0, `pause-when-empty-seconds=-1`).
`mod-install` installs only mcb-body when `lab_mod` is false. The supervisor of
an instance with a `companion` section starts and restarts the companion host
process instead of the lab's `companion_service` thread.

The game port binds to loopback like the lab. Opening it to the LAN (for a
second player on another PC) is a later, explicit switch.

## 2. Companion host (`companion_host.py`, `companion_session.py`)

- **Session:** spawn the body by its stable name (`bots/<id>/bot.json`, `name`)
  with `stream`, `extended_observation`, the sections awareness profile and
  `sections-v1` terrain, with **no** `natural_start`. The spawn position is the
  last saved position (host state file), else the world spawn from the server's
  `level.dat` (never a guessed column: a body in water never settles, so the
  session start fails and the host retries with backoff). The body keeps
  its saved inventory between sessions (Carpet player data). World memory and
  places are persistent (`session_archive` with the real level).
- **Bridge:** body-only when the instance has no lab mod (no `/lab/...` calls,
  `/state` and `/policy` unmerged). Long-lived: no request budget, bounded
  in-memory `events`/`samples`, a cancel hook in `Bridge.state()`.
- **Heartbeat:** the body releases itself after 2 s without contact, so the
  worker's idle loop observes about every 250 ms (heartbeat, fresh snapshot).
- **Jobs:** one runs at a time. `when: "now"` (default) cancels the running job
  and the queue first; `when: "next"` appends. Job states: `queued`, `running`,
  `succeeded`, `failed`, `cancelled`. Every job has an id, progress
  (`stage`, `done`, `total`, `detail`), a result or error, and the actor.
  An idempotency key returns the existing job instead of a second one.
- **Cancel:** `stop` sets the token. The next `Bridge.state()` raises
  `JobCancelled`; the worker sends `cancel_route` and `release`, clears the
  token and goes idle. The session is never torn down by a stop.
- **Death:** a dead body ends the job as failed (`died`); the host respawns it
  (vanilla rules: items are dropped where it died) and says so in chat.
- **Snapshot:** the worker publishes a thread-safe snapshot after every
  observation: position, health, food, held inventory, visible players, current
  job, queue, last failure. `status` and the talker read only the snapshot.

## 3. Intents (`companion_intents.py`, the contract)

One JSON Schema per intent, shared by MCP tools and the talker's tool list.

| Intent | Args | Kind |
|---|---|---|
| `status` | — | instant: snapshot + one-line explanation |
| `say` | `text` (1–256), `to?` player | instant |
| `list_places` | — | instant |
| `remember_place` | `name`, `x?,y?,z?` (default: where the bot stands) | instant |
| `job_status` | `job_id` | instant |
| `stop` | — | instant: cancel running job and queue |
| `goto` | exactly one of `{x,y,z}`, `place`, `player`; `radius?` 1–8 | job |
| `follow` | `player`, `distance?` 2–8 | job, ends only when stopped or replaced |
| `gather` | `item`, `count` 1–2304 | job |
| `build` | `template`, `x?,y?,z?`, `rotation?` 0/90/180/270 | job |

Job intents also take `when?: "now" | "next"`. Validation errors are
`ValueError`s with a message a model can act on.

**Actor and authority:** `Actor(source, name, uuid, position)`. `mcp` and `api`
actors are the local operator (the control API is token-protected loopback).
`chat` and `command` actors pass the owner/allowlist check of
`config/companion.json` (`companion_service.allowed`). Other players' text is
untrusted: it may get a chat reply, never a job.

## 4. Control API (`companion_api.py`)

Loopback HTTP on `companion_port`; the bearer token is written to
`runtime/instances/<name>/companion-host.json` (port, token, pid).

- `POST /call` `{intent, args, key?}` → `{ok, result}` or `{ok: false, error}`
- `GET /jobs/<id>?wait=<s>` → the job, long-polled until it is terminal or the
  wait ends (≤ 60 s)
- `GET /events?after=<n>&wait=<s>` → host events (job started/progress/finished,
  chat in and out)
- `POST /shutdown` → 202; the host halts and despawns the body and exits. The
  supervisor stops the host this way first, because on Windows `terminate()` is a
  hard kill that would skip that cleanup.

## 5. MCP server (`mcb_mcp.py`)

Stdio JSON-RPC 2.0, no extra dependency: `initialize`, `tools/list`,
`tools/call`, `ping`. Tools are the intents plus `wait_job {job_id, timeout_s}`;
job tools also take an idempotency `key`. Tool calls are answered as they finish
(a pool off the read loop), so a `stop` never waits behind a long `wait_job`.
It reads the control API address and token from the instance runtime file.
Example client config `.mcp.json.example`, and a bespoke skill
`.claude/skills/mcb-companion/SKILL.md` that teaches: check `status` first, follow
job ids to the end, never resubmit a running job (use `key`), `stop` is always
safe.

## 6. Chat and the talker (`companion_talker.py`, `llm_chat.py`)

- **Java:** `player_chat` events (sender uuid, name, text, position) from Fabric
  `ServerMessageEvents.CHAT_MESSAGE`; the sender's position is also added to
  `player_command`. `POST /say {text, to?}` broadcasts `<BotName> text` (or
  tells one player). Visible player entities carry their `name`.
- **Addressing:** the talker answers a chat line that names the bot (or starts
  with `@`+name), and every `/mcb ask <text>`.
- **Stop in code:** an authorized line whose words are only a stop word ("stop",
  "halt", "cancel", "wait", "freeze") cancels before the model sees anything.
- **Model:** OpenAI-compatible `/chat/completions` with `tools`, backend chosen
  by `bots/<id>/bot.json` `roles.talker` from `config/backends.json`, with the
  talker's own deadline (20 s) and a per-turn tool-call limit. Persona:
  `bots/<id>/SOUL.md`; instructions: `prompts/companion-talker-v1.md`. Per-player
  history of the last few turns. Every turn is logged with latency, tool calls
  and results.
- **Untrusted senders** get conversation only: the tool list offered to the model
  for an unauthorized sender holds `status` and `list_places`.

## 7. Verification

TDD per module with fakes (fake bridge/body, fake HTTP model server). Then live,
in order: play server bootstrap and start; host session spawns the named bot and
idles without the watchdog releasing it; `status` over MCP; `goto` a coordinate;
`follow` the operator; `stop` mid-route; `gather` an item; chat "come here" via
the talker; stop while the model is unreachable. Live evidence is recorded under
`results/companion-host/<stamp>/`; a mocked model or MCP fixture never counts as
live qualification.

## Open items

- LAN access to the play server (second PC) needs a non-loopback game bind.
- Disk growth of a long host session's `events.sqlite3`: rotate per session day.
- Night shelter and death recovery (B5) before leaving the bot alone for hours.
