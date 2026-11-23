---
name: mcb-companion
description: Use when driving the MCB companion bot in the play world through the mcb MCP tools (status, goto, follow, gather, build, say, stop, wait_job) — "come here", "get me 20 logs", "build the cottage", "what is the bot doing", "stop".
---

# Driving the companion bot

One bot lives in the play world and does one job at a time. You pick tasks; the
body does the walking, mining and placing. Never try to steer it step by step.

## Setup (once)

Copy `.mcp.json.example` to `.mcp.json`, replace `<REPO_PATH>` with the repo
checkout (absolute path, forward slashes) and restart the client. The play server
must be running (`uv run --env-file .env --locked mcplay server start`; the
`.env` gives the in-game chat model its `ATOM_API_KEY`). If a tool says "companion
host is not running", start it and retry; do not retry in a loop.

## Tools

Instant: `status`, `say {text, to?}`, `list_places`, `remember_place {name, x?, y?, z?}`,
`job_status {job_id}`, `stop`.
Jobs (return `{ok, result: {job}}`): `goto {x,y,z | place | player, radius?}`,
`follow {player, distance?}`, `gather {item, count}`, `build {template, x?,y?,z?, rotation?}`.
`wait_job {job_id, timeout_s 1-60, default 30}` long-polls a job.
Every job tool also takes `when`: `now` (default; stops the current job and the
queue first) or `next` (queue behind what is already there).

## Rules

1. Call `status` first. It shows the current job, queue, position, health, food,
   inventory, nearby players and the last failure.
2. A job tool returns at once with a job id and state `queued`/`running`. Follow
   it: call `wait_job` until `state` is `succeeded`, `failed` or `cancelled`
   (`progress` has `stage`, `done`, `total`, `detail`). Tell the player progress
   between waits for long jobs.
3. Never resubmit a job that is still running: a second `when: now` cancels the
   first. Give each job you mean once a `key` (any short string): sending the
   same key again returns the existing job instead of starting another. If
   unsure whether a call went through, call `status` or `job_status`.
4. To add work behind a running job use `when: "next"`. To replace it, use the
   default `now`.
5. `stop` is always safe: it ends the job and drops the queue; the bot stands still.
   Use it whenever the player says stop, wait or no.
6. `follow` never finishes by itself; it ends on `stop` or the next `now` job.
   Do not `wait_job` on it for completion.

## Resolving requests

- "Come here" / "to me": `goto {player: "<their name>"}`. Use the name from chat or
  ask; do not guess coordinates.
- Named places: `list_places`, then `goto {place}`. Save one with
  `remember_place {name: "home"}` (where the bot stands) or with x/y/z.
- Items are Minecraft ids: "iron" is `iron_ingot`, "stone" `cobblestone`. When any
  kind will do, give a group tag: "wood"/"logs" is `#minecraft:logs` (any log, the
  nearest trees), "planks" `#minecraft:planks`; name one (`spruce_log`) only when
  the player does. `gather` counts what the bot holds in total (every member of a
  group) and crafts or smelts as needed.
- Templates are names of saved builds (`builds/`). If you do not know the name,
  ask the player which template; do not invent one.
- `build` without x/y/z builds beside the bot; give the origin corner to place it.

## Errors

`{ok: false, error}` (tool `isError`) means nothing started: the message says which
argument to fix (unknown place, bad item id, two goto targets). Fix and call again
once. A job that ends `failed` has `error` in the job (cannot reach, missing
materials, died); report it plainly and offer a different approach rather than
repeating the same job. "Not authorized" applies to in-game chat players; the MCP
caller is the operator and may do everything.

## Examples

Come here:
1. `status` -> idle.  2. `goto {player: "Marc"}` -> job `j7`.
3. `wait_job {job_id: "j7"}` -> succeeded. Say so.

Get logs, then follow:
1. `gather {item: "#minecraft:logs", count: 32}` -> `j8`.
2. `follow {player: "Marc", when: "next"}` queues behind it.
3. `wait_job {job_id: "j8", timeout_s: 60}` repeatedly, reporting `progress.detail`.

Player says stop mid-job: `stop`, then `status` to confirm idle. Do not resubmit
the interrupted job unless asked.
