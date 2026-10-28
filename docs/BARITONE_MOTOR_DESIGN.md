# Design sketch: Baritone as the motor under mcb's planner and reflexes

> **Partly superseded (Oct 28, 2026):** Baritone-as-motor is no longer the default direction. Server-side Carpet bodies with MCB's planner are primary; this design is kept for the pivot rule if MCB still loses at matched awareness. See [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).

Created October 26, 2026. Proposal only; nothing here is built or qualified.
Context: Baritone went 80/80 on C2–C5 at 2–4× fewer ticks than V8
([Baritone reference](BARITONE_REFERENCE.md)). If that holds on harder layouts, mcb
stops building its own long-range movement and focuses on planning, reflexes,
construction and combat on top of Baritone.

## Layers

| Layer | Owner | Rate | Examples |
|---|---|---|---|
| Strategic planner | larger LLM | seconds | "get 3 logs, then build shelter" |
| Tactical / reflex | Jev or a fast policy | ≤ 1 s, bounded commitments | dodge, grab a nearby drop, retreat, strike |
| Motor (long-range) | Baritone | every tick | `goto`, `mine`, follow, explore |
| Motor (direct) | mcb bridge | every tick while overriding | strafe, jump, attack, look |

Only one motor holds the controls at a time. The planner never steers directly.

## Control handoff (the key mechanism)

Verified against the pinned API jar with javap: `IPathingControlManager.registerProcess`,
`IBaritoneProcess` (`isActive`, `onTick`, `priority`, `onLostControl`) and
`PathingCommandType.REQUEST_PAUSE`. Baritone's `Input` enum covers forward/back/
left/right, both clicks, jump, sneak and sprint.

1. The client bridge registers one `mclab-override` process with priority above
   every built-in process.
2. **Take over** (`POST /override {on: true, reason}`): the process becomes active and
   returns `REQUEST_PAUSE` each tick. Baritone keeps its goal but stops moving. The
   bridge clears Baritone's forced inputs and applies mcb's bounded direct controls
   (same validation as the server bridge: ≤5-tick duration, yaw/pitch caps).
3. **Hand back** (`{on: false}`): the process goes inactive; Baritone resumes and
   replans from wherever the player now is. A goal made stale by the diversion
   (e.g. the target was already collected) is replaced by the planner, not resumed.
4. **Fail-safe**: the override expires after a bounded number of ticks with no fresh
   command, releasing controls and returning to Baritone (or to idle if the planner
   cancelled the goal). A dead model can never freeze or spin the player.

Open to verify before building: how forced inputs are applied (Baritone's
`IInputOverrideHandler`) and whether direct look changes during a pause disturb
Baritone's rotation smoothing on resume.

## What the bridge needs next

- `POST /override` as above, with `status` reporting the controller in charge
  (`baritone:<process>`, `mclab-override`, idle) and the tick it changed.
- `POST /controls` for direct inputs while overriding, timed by client tick.
- Observation for the reflex layer comes from the **server** policy stream for the
  human subject (filtered, same sensor profile as Carpet bodies), not Baritone's
  world cache. This keeps mcb's claims about what its policies saw honest.
- Events: path started/finished/failed, goal changed, override begin/end, so the
  planner can react without polling.

## Constraints to accept or design around

- **One account per Baritone bot.** Each needs a real authenticated client. Carpet
  bodies need neither. Multi-bot plans need accounts or a hybrid (Baritone for the
  lead bot, Carpet + mcb motor for helpers).
- **Privileged map knowledge.** Baritone plans with the full loaded world. Report it
  as such; it is not a perception result.
- **Physics parity.** Baritone drives a vanilla client (client-side prediction, real
  network); mcb's Carpet body is server-side. Combat timing measured on one does not
  transfer to the other without re-measurement.

## Proposed harder comparison before deciding (needs approval)

Same server scoring for both subjects; V8 and Baritone on identical seeds.

1. **C6 maze/corridor**: 1-wide corridors with dead ends; tests planning under walls.
2. **C7 gap and ledge**: one-block gaps and 2–3 block drops; tests parkour/fall
   choices (Baritone parkour allowed vs. not, reported separately).
3. **C8 moving target**: the target log is replaced at a second location mid-run;
   tests replanning and goal switching latency.
4. **C9 interrupt**: a scripted item drop appears off-route; measures diversion +
   resumption cost through the override handoff above.

C6–C7 need new `courses/profiles.json` geometry and reset qualification; C8–C9 need
new evaluator events. The G2 `extensions_pending` list already includes C2–C8.
