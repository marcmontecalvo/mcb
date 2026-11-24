# Arena runtime

A fixed, protected fighting space the lab builds from a plan, then runs wave or team matches in.
Java: `mcb-body/.../arena/` (a second entrypoint, `ArenaMod`, and its own mixin config
`mcb-arena.mixins.json`, in the same jar). Python: `src/mclab/arena/client.py` (plans, waves, a standalone
bridge client), `src/mclab/arena_cli.py` (`mcplay arena ...`), and the `CompanionBody.arena_*`
wrappers. Op contracts: [BODY_ACTION_OPS.md](BODY_ACTION_OPS.md#arena-ops).

Arena ops are admin ops that need **no body, run or episode**: the bridge dispatches them at the top
of `admit`, before identity and sequence checks, so `mcplay arena` works beside a running session.
The registry is `mcb_arenas.json` in the world root (saved atomically).

## Plan

```json
{"schema": 1, "name": "vault", "size": [sx, sy, sz],
 "fills": [{"from": [0,0,0], "to": [sx-1,sy-1,sz-1], "block": "minecraft:air"}],
 "markers": {"teams": {"red": {"pads": [[x,y,z]], "entry": [x,y,z]}},
             "mob_spawns": [[x,y,z]], "spectator": [x,y,z]},
 "capacity": {"teams": 2, "per_team": 4, "mobs": 15}}
```

Coordinates are local to the min corner (`origin`). Fills apply in order. **The first fill clears
the whole bounds to air**: the arena owns its bounds, and a build overwrites whatever stood there
(only a bounds already held by another arena is refused). Unknown top-level keys are ignored. The
plan is validated entirely (blocks, markers inside the bounds, capacity) before a single block is
placed. Files live in `arenas/<name>.json`; wave presets in `arenas/waves/` (`vault-raid`,
`mixed-10`).

States: `building` -> `built`; `clearing` (restore on remove); `incomplete` (a build or clear was
interrupted by a restart: remove it, then build again). Only a `built` arena can start a match.

## Protection (always on, every player, fake players included)

Inside an arena's bounds:

- no block is broken or placed; no bucket, flint and steel, fire charge or bone meal; no entity
  items (boats, minecarts, armor stands, spawn eggs) are used;
- explosions destroy no arena block; fire does not spread or burn (it is frozen), and fire outside
  within 4 blocks is put out; mobs break no blocks (endermen, silverfish, door breakers, `Mob`
  breakers);
- the body's own `place_block`, `use_block`, `break_block` and scaffold ops fail with
  `arena_protected`;
- no natural spawns. Allowed: wave mobs (tag `mcb_arena:<name>`), mobs made by a wave mob (adopted
  by spawn reason, parent type and a 32-block range, so riders, splits and summons count), tamed
  pets and `mcb_trial` mobs. Everything else is discarded and counted in `blocked_spawns`.
  Stale wave-tagged mobs are removed at startup and when loaded while no match runs.

Opening doors, chests and other plain `use` of blocks stays allowed; only placing and the items
above are refused.

Not covered: pistons and dispensers, water or lava flowing in from outside, lightning, farmland and
turtle-egg trampling, and `/` commands run by an operator.

## Matches

`mode: "waves"`: the participants all fight on one side. Each starts on a pad (every team marker's
pads in plan order, so the sealed arenas need no door) facing the centre; more players than pads, or
than `capacity.teams * capacity.per_team`, is refused (`not_enough_pads`, `over_capacity`). Code:
`ArenaLogic.wavePads`. A wave is a `{mobs: {type: count}, delay_ticks}`; mobs spawn round-robin over the
`mob_spawns` markers (room-checked, `COMMAND` spawn reason), are persistent, and are retargeted to
the nearest live participant every 10 ticks. `delay_ticks` counts from the match start for wave 0
and from the previous wave being cleared afterwards. Won when the last wave is cleared; lost when no
participant is live (alive, online, same level, inside the bounds). Mobs are discarded when the
match ends.

`mode: "teams"`: players go to their team's pads, a scoreboard team `mcb_<8 hex>` per arena team is
made with friendly fire off, and it is removed afterwards. Won by the last team with a live member;
`draw` is a loss with winner none.

Chunks are forceloaded while a build or match needs them (refcounted; chunks someone else forced
are never released). Each arena persists the chunks it forced, so a crash is cleaned up at the next
start.

## Events (`/events`, source `fabric_start`)

| Event | Data |
|---|---|
| `arena_build_progress` | `name, kind (build/restore), percent, placed, total` (about every 20 ticks) |
| `arena_built` | `name, ticks, cells, bounds` |
| `arena_build_aborted` | `name, ticks, reason` |
| `arena_removed` | `name, restored` |
| `arena_match_started` | `name, mode, participants, waves` or `teams` |
| `arena_wave_started` | `name, wave, waves, mobs, failed` |
| `arena_wave_cleared` | `name, wave, waves` |
| `arena_match_won` / `arena_match_lost` / `arena_match_stopped` | `name, mode, winner?, reason?, waves_cleared, waves, duration_ticks` |

Acks are `<op>_done` / `<op>_failed` (`reason`, `detail`) carrying `request_sequence` and
`request_id`. Failure reasons: `bad_request` (a malformed request or waves), `not_allowed` (a
forbidden mob such as the ender dragon), `bad_plan`, `bad_block`, `overlap`,
`unknown_arena`, `unknown_dimension`, `arena_not_ready`, `match_running`, `no_match`,
`no_mob_spawns`, `over_capacity`, `unknown_entity`, `not_a_mob`, `participant_offline`,
`no_participants`, `duplicate_participant`, `busy` (at most 4 jobs at once), `internal`.

## Status shape (`arena_status`)

```json
{"name": "vault", "dimension": "minecraft:overworld", "state": "built",
 "origin": [..], "size": [..], "bounds": {"min": [..], "max": [..]},
 "capacity": {"teams": 2, "per_team": 4, "mobs": 15},
 "teams": {"red": 2}, "mob_spawns": 4, "spectator": [..], "match_state": "running",
 "build": {"name": "vault", "kind": "build", "percent": 40, "placed": 1, "total": 3},
 "blocked_spawns": 12,
 "match": {"name": "vault", "mode": "waves", "state": "running", "ticks": 340,
           "wave_index": 1, "waves": 3, "next_wave_in_ticks": 0, "spawned": 9, "killed": 4,
           "wave_mobs": [{"uuid": "..", "type": "minecraft:zombie", "position": [..], "health": 20.0}],
           "participants": [{"uuid": "..", "name": "Ann", "alive": true, "team": "red"}],
           "teams_alive": {"red": 1}}}
```

`build` appears only while a job runs; `match` only after a match has started (it keeps the result
after the end). A finished match reports `state` won, lost or stopped with `reason` and `winner`.
Waves mode carries `wave_index`, `waves`, `next_wave_in_ticks`, `spawned`, `killed`, `wave_mobs`;
teams mode carries `teams_alive`.

## The bot's hunt

`attack {arena: NAME}` (or `arena: true`, the arena the bot stands in; in chat `/mcb attack arena`)
is the bot's hunt mode. Each look reads `arena_status` and takes the nearest live wave mob, wherever
it is in the bounds, keeping the one already chosen while it lives. If the body does not see it,
the bot walks to it; otherwise one `fight` op runs against it with the usual tactic. Between waves
the bot eats if it needs to and waits. The job ends only with the match (`won`, `lost`, `stopped`),
with the job's kills, hits and damage plus `waves_cleared`. Inside a hunt there is no threat
radius, and the guard stands down: the hunt is the defence. The fight has no health floor, because
the arena has no shelter and a match is lost only by dying. The bot must be a participant (the
default is everyone inside when the match starts). A wave mob within 24 blocks is fought even when
the body's short entity sensor (8 blocks) misses it: the status places it and the fight op tracks
it by uuid. A mob the guard would avoid (a ravager with only a sword) is fought with the best melee
weapon held (`combat.ordered_tactic`). Each fight lands as a host `job_step` event (weapon, target
type, `hurt_by`, `killed_by`). Code: `companion_attack.hunt` / `arena_here`, `Session.attack`.

Food and potions (`companion_consumables`): before every fight the hunt asks what the moment
calls for, with every wave mob as a foe and the players in sight as friends no harmful splash may
reach. A use is a step of its own (`action` eat / drink / throw, `purpose`, `potion` or `item`).
While a heal is held (a healing or regeneration potion, a golden apple) the fight breaks off at
`HEAL_AT` (10 health) so the bot heals and fights on; the "no floor" rule applies once nothing heals.
Between waves it eats to a full bar, which regenerates fastest. A use the body refuses waits
`USE_RETRY_S` before it is tried again. The rules are in COMPANION_COMBAT_PLAN section 15.

`scripts/arena-hunt.py` (`mclab.arena_hunt`) measures hunts: each run hands the kit out afresh
(default `all+armor+shield+trident+food+potions`), heals, starts the match and samples it once a
second; the file under `results/companion/arena/` keeps every run's samples, steps, damage by
source, the food and potions used by purpose (`uses`) and the series summary.

## CLI

```
mcplay arena build <plan> --at X Y Z [--as NAME] [--dimension ID] [--wait] [--timeout S]
mcplay arena start NAME [--mode waves|teams] [--waves PRESET|FILE] [--players A B] [--team red=A,B]
mcplay arena stop NAME | status NAME | list | remove NAME [--restore]
```

Each command prints one JSON object (`ok` first) and exits 0, or prints `reason` and `detail` and
exits 1. The CLI reads the bridge token from the server state file; it needs a running server.
The Python side checks waves with the mod's limits before sending and reports `bad_waves`; the mod
itself answers `bad_request` / `not_allowed`.
