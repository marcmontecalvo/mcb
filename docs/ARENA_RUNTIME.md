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

### Waiting for players

A start refuses a named player who is offline (`participant_offline`). People join after the
server comes up, so the dashboard's starts and every `arena-hunt.py` series first wait
(`ArenaClient.wait_ready`, `mcplay arena start --ready-timeout S`; the dashboard waits 900 s,
`arena-hunt.py` 900 s by default, the bare CLI not at all). The wait holds until every named player
is online (`GET /players`) and every human among them has typed `/mcb ready` (or `ready` alone in
chat) since the wait began. Fake players (the bot) need only be online. Each human is whispered
how to start when they join; one who leaves must be ready again. A ready player hears who is still
awaited, and everyone hears "Everyone is ready: the match starts." The wait's progress lines go to
the session log; after the timeout the start fails with `players_not_ready`, naming who did not
join or did not type ready. The mod answers `/mcb ready` with "Ready."; the companion ignores it.

A human on the death screen (`GET /players` `alive: false`) is not ready: the wait says "Waiting for
Ann to respawn." and gives up with "Ann has not respawned". A ready typed while dead counts once they
are back. A series waits like this **before every match**, not only the first (`run_series`
`ready_timeout`, `arena-hunt.py --ready-timeout`); later matches greet with "Arena duel, match 2 of
5, is waiting for you". Before, a player killed in match 1 was still dead when match 2 started: their
team counted as out at once (matches 2-5 "won" in 0.2 s), and the start's kit healed the dead player,
so the game took them for alive and ignored their respawn click. The mod now also refuses a dead
participant at `start` and `arena_kit` (`participant_dead`); a dead player is never healed or moved.

Joining players start at the world spawn exactly when it lies inside a built arena (the arena
server's lobby; `SpawnFinderMixin`, `ArenaLogic.keepsSpawnExact`). Outside adventure mode the game
otherwise searches the spawn's column from the top down and stands them on the lobby's roof.

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
`participant_dead`, `no_participants`, `duplicate_participant`, `busy` (at most 4 jobs at once), `internal`.

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
           "wave_mobs": [{"uuid": "..", "type": "minecraft:zombie", "position": [..], "health": 20.0,
                          "velocity": [vx, vy, vz]}],
           "participants": [{"uuid": "..", "name": "Ann", "alive": true, "team": "red"}],
           "teams_alive": {"red": 1}}}
```

`build` appears only while a job runs; `match` only after a match has started (it keeps the result
after the end). A finished match reports `state` won, lost or stopped with `reason` and `winner`.
Waves mode carries `wave_index`, `waves`, `next_wave_in_ticks`, `spawned`, `killed`, `wave_mobs`;
teams mode carries `teams_alive`. A wave mob's `velocity` is its motion this tick in blocks per
second (the bot leads a thrown splash with it).

## The bot's hunt

`attack {arena: NAME}` (or `arena: true`, the arena the bot stands in; in chat `/mcb attack arena`)
is the bot's hunt mode. Each look reads `arena_status` and takes the live wave mob with the least
distance / `THREAT` weight (evoker 3, witch 2.5, others 1: casters first, since what they do goes
on while they live), wherever it is in the bounds. The one already chosen is kept while it lives,
unless a mob of greater weight comes out ahead (`companion_attack._threat`). Vault raid
20261124T044539Z died three times in wave 4 sniping the nearest pillager while the witches'
splashes and poison did 100 of the damage. If the body does not see it,
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
`USE_RETRY_S` before it is tried again. A thrown splash marks its targets in flight for `FLIGHT_S`
(1.5 s), so no second potion follows it at a mob the first may already have killed; a heal marks
the bot itself the same way (from the op's end), so no second heal follows one its health does not
show yet. The rules are in COMPANION_COMBAT_PLAN section 15.

Every walk in a hunt (a chase past 24 blocks, or toward a mob the fight op found unreachable) is
one `walk_to` under `walk_wall(ARENA_WALK_S)` (4 s), never `walk_far`'s legs: a walk cannot heal or
fight back, and the look after it can. Run 20261124T040230Z died in a 29 s walk toward an
unreachable vindicator with five heals held. Damage the bot did to itself (its own splash) is
counted as `self:<id>` in `hurt_by` and `killed_by`.

A harmful splash is aimed where its targets will be when it lands (0.6 s on, by `velocity`; a
melee mob of unknown course is taken to be charging), and never along a line some foe would catch
it on nearer than 4.75 blocks, since the lowest arc flies 1 to 1.6 blocks up all the way and its
splash would take the bot. No harmful splash flies while a vex or phantom is within 8 blocks. The
vault raid 20261124T043026Z showed both: its own harming took it for 4 and 3. The body also clips the
predicted flight against the blocks and refuses the throw (`blocked`) when it would break on one within
4.75 blocks of its feet: raid 20261124T045928Z took 10 from a harming splash that broke on a block in
front (the policy sees mobs, not blocks; the body sees both). The same raid's
fight op refused a vex as a pet (a vex is `OwnableEntity`, its evoker the owner); a pet is now
owned and not hostile (`ActionLogic.pet`).

`arena-hunt.py` stops a match whose job ended while it still ran and records the run as
`abandoned` (before, the next run found it running and could not start).

`scripts/arena-hunt.py` (`mclab.arena_hunt`) measures hunts: each run hands the kit out afresh
(default `all+armor+shield+trident+food+potions`), heals, starts the match and samples it once a
second; the file under `results/companion/arena/` keeps every run's samples, steps, damage by
source, the food and potions used by purpose (`uses`) and the series summary.

### Team games: the bot against players

`arena-hunt.py --mode teams|race --team red=MCBot,Ann --team blue=Bob` plays team matches. The
bot's foes (`companion_attack.foes`) are the placed wave mobs and, in `teams` mode only, the live
participants of every team but its own (names matched case-insensitively): it hunts the nearest
weighted foe as above, fights a player with `allow_player`, and never splashes a harmful potion
where a teammate would be caught (the enemies are not "friends"). In `race` it hunts only wave
mobs; teammates are never targets. Each match row keeps the scoreboard (`winner`, per-player
`kills` with `mob_kills` / `player_kills`, `team_kills`), the bot's own job kills as `bot_kills`,
and `bot_won` when the bot played on a team; the summary counts `winners` and `bot_wins`. With no
bot in the roster the script needs no companion host: it starts the match and samples it until it
ends or times out. `--op NAME` / `--deop NAME` set operator status once before the first match
(`arena_op`; the bot is skipped), and every player gets the same kit (`arena_kit`).

### Several bots

A match can have several copies of the bot, on one team or on both (2v2, 3v3, bots and people
mixed, up to 8 a team). Built, **live check pending**.

- **Names.** The first bot is `MCBot` (fleet id `default`); the others are `MCBot2` … `MCBot99`
  (ids `mcbot2` …). `mclab.companion_names` holds the mapping (`bot_id_for`, `bot_name_for`,
  `bot_id_for_name`, `is_bot_name`); it imports nothing heavy, so the dashboard and `play_runs`
  can use it. Nothing else names a bot: a roster name matching `MCBot` or `MCBot<N>` is a bot.
- **Bodies.** The mod's body hub gives each bot its own body, routed and tagged by body id. The
  companion `Fleet` (`companion_fleet.py`, up to 16) keeps one `Member` (host, session, talker) per
  bot with its own files. `ApiClient` routes by `bot=` (`bots()`, `add_bot(id)`; adding an
  existing id is a no-op).
- **Hunt.** `arena-hunt.py --team red=MCBot,Ann --team blue=MCBot2,Bob --world` reads the roster,
  asks the host which bots are running (`resolve_bots`), and puts in the missing ones
  (`ensure_bots`, echoed `put in mcbot2`) before waiting for the humans. `--bot NAME` (repeatable)
  names a bot that is not a derived name; it must already be running. A bot that cannot be put in
  ends the script with exit 2 and a reason; bots are never waited for as humans and never made
  operators. Each bot gets the kit, is sent to hunt its own foes (the live participants of the
  other teams, so bots fight each other as well) and ends with the match; `_end` stops every bot
  and then the match. A bot whose attack is refused is recorded (`outcome: refused`,
  `refused_by`), not retried. When every bot's job is over (a bot died) but a human is still
  alive in the running match (`match.participants[].alive`), the hunt keeps sampling until the
  match ends or the timeout hits; it ends the match as `abandoned` only when no live human is
  left.
- **Results.** One bot keeps the flat row (`bot`, `bot_won`, `bot_kills`, …). Several give
  totals (`bot_kills`, `hits`, `damage`, `hurt_by`, `uses`) and a `bots` object keyed by name with
  each bot's `min_health`, `job_state`, `error`, `steps`, `hurt_by`, `killed_by`, `picks`,
  `uses`, `hits`, `damage`, `bot_kills`, `team`, `bot_won`. Samples carry `bot_health` per bot. The
  series summary adds `bots[name] = {runs, wins, bot_kills, deaths, min_health, hurt_by}`.
- **Dashboard.** The Arena roster has "+ Add a bot (MCBot2)", which names the next free bot and
  puts it on the smaller team; a bot cannot be an operator (`play_runs` refuses it too) and
  counts toward capacity. The "where to watch" card lists only the humans a match waits on.
- **Not done.** The MCP tools (`goto`, `attack`, …) drive the first bot only: there is no `bot`
  argument yet.

## The arena server

A third instance, `config/instances/arena.json` (`mcarena`, like `mcplay`; game `25566`, bridge
`8785`, supervisor `8786`, companion `8787`, LAN on, 20 players): an empty void world (flat, one air
layer, `the_void` biome, no structures), so people who join need no operator rights and see
nothing but the lobby and the arenas. The play world is untouched.

`mclab.arena.world.prepare` (`arena-hunt.py --world`, which the dashboard always passes) readies it
before the first match:

1. the **lobby** (`designs.lobby`, never offered as a match arena): a 25 × 25 lit quartz hall with
   glass windows and a spectator gallery, built at `LOBBY_ORIGIN` so its entry is `(0, 102, 0)`;
2. `arena_spawn` at the lobby's entry: everyone who joins (or dies outside a match) starts there;
3. the chosen arena, when it is not built, at the first free slot east of the lobby
   (`slot_origin`: `MAX_FOOTPRINT + 64` apart, never overlapping a registered arena). An arena
   already registered keeps its origin; one left half-built is removed and rebuilt; one still
   clearing refuses with `arena_busy`. Build progress is echoed (`vault: 40%`); a build that
   aborts stops the series.

The lobby and the spawn are not left to the first match: the bot joins as soon as the server is
up, and under the level's spawn there is only void (it fell out of the world at every retry). The
instance's `companion.spawn: "lobby"` makes the companion host's world spawn
`world.lobby_world_spawn`, which builds the lobby when it is missing, sets `arena_spawn` and returns
its entry; the session starts there and comes back there after a death. A lobby that cannot be
built fails the session start, and the host retries it. Other instances keep the level's spawn
(`companion.spawn` `"level"`, the default).

The match then takes the players from the lobby to their pads and, at its end, back to the lobby
(`start`'s `exit`). Matches wait for every named human to join and type `/mcb ready` as before.
Bootstrapping the instance needs the EULA accepted in `runtime/instances/arena/server/eula.txt`
(as for play) and the mod installed (`mcarena mod-install` after `scripts/build-mod.ps1`).

## CLI

```
mcplay arena build <plan> --at X Y Z [--as NAME] [--dimension ID] [--wait] [--timeout S]
mcplay arena start NAME [--mode waves|teams] [--waves PRESET|FILE] [--players A B] [--team red=A,B]
                        [--ready-timeout S]
mcplay arena stop NAME | status NAME | list | remove NAME [--restore]
```

Each command prints one JSON object (`ok` first) and exits 0, or prints `reason` and `detail` and
exits 1. The CLI reads the bridge token from the server state file; it needs a running server.
The Python side checks waves with the mod's limits before sending and reports `bad_waves`; the mod
itself answers `bad_request` / `not_allowed`.
