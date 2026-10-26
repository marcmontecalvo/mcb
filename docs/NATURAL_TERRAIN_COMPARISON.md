# Natural-terrain comparison: Baritone vs V8 goal mode

Created October 26, 2026. Development-only evidence; not part of any gate.

## Setup

- **World:** seed 1337, ordinary generation, a separate save beside the flat lab
  world (`runtime/server/natural-1337`). It was chosen over seeds 42424242, 9001
  and 20261026 for the most land (92%) and steep steps (675 grid steps of 5+ blocks)
  within 160 blocks of spawn. Every attempt starts from the same snapshot
  (`runtime/natural/natural-1337`), with a server restart.
- **Routes:** five straight-line routes, 117–141 blocks long, picked automatically
  as the most rugged with dry, level endpoints (`results/natural/routes-1337.json`).
  Height range is 35–56 blocks, and the largest 4-block step is 16–40 blocks, which
  means cliffs or ravines. Both bots start facing the goal.
- **Goal:** within 3 blocks horizontally and 4 vertically, in at most 6000 ticks
  (5 minutes).
- **Kit (both):** iron pickaxe, shovel and axe; 64 cobblestone; 64 dirt.
- **Conditions:**
  - calm: noon, mob spawning off, existing hostiles removed
  - mobs: midnight, mob spawning on, starting from an area cleared of hostiles
- **Entrants:**
  - **Baritone:** v1.20.0 in a real client (`goto`, breaking and placing allowed).
  - **V8 goal mode** (`src/mclab/navigation_goal.py`): V8's pathing pointed at the
    goal, with tunnelling or a staircase step up when progress stalls. It cannot
    place blocks or fight.
- **Scoring:** server-side `NaturalRun`; mod admin ops `natural_probe`,
  `natural_start`, `natural_stop`.

## Results

| Route | Calm: Baritone | Calm: V8 goal | Mobs: Baritone | Mobs: V8 goal |
|---|---|---|---|---|
| R1 (136 blocks) | 579 ticks | timeout, 80 short | 576 ticks | killed at 556, 113 short |
| R2 (117) | 532 | timeout, 78 short | 537 (took 7.8 dmg) | killed at 715, 104 short |
| R3 (141) | 601 | fell to death at 3333, 57 short | 603 | killed at 554, 119 short |
| R4 (133) | 777 | timeout, 76 short | 787 | died at 1339, 106 short |
| R5 (126) | 853 | timeout, 65 short | 851 | killed at 1402, 111 short |

- **Baritone:** 10/10. Mobs made no measurable difference to its time. It broke 0–5
  blocks per route and placed none; detours were enough.
- **V8 goal:** 0/10. Calm runs covered 37–68 blocks in five minutes while breaking
  39–110 blocks. With mobs it died every time, within 28–70 seconds.

Runs: `results/natural/20261026T153913593587Z` (calm; its mob half failed setup on
a gamerule bug since fixed) and `results/natural/20261026T161537236571Z` (mobs).

## Interpretation and limits

- On real terrain the gap is no longer speed but whether the bot finishes at all.
  V8's movement was built for small flat arenas: local observed-cell memory (here
  capped at ±20 blocks so decisions stay inside the 3-tick schedule), no placing,
  and no awareness of threats.
- V8 goal mode is a quick adaptation, not a tuned mover. A stronger mcb mover is
  possible, but it would mean rebuilding much of what Baritone already does.
- Baritone plans with the full loaded client world and runs at full client speed.
- One attempt per route and condition; mob spawns are random. The R4 mobs death
  shows 0 damage because damage is only counted while the subject is alive (a
  one-tick lethal hit is missed). This is a reporting gap, not a scoring error.

## Conclusion for the design decision

This supports using Baritone as the long-range motor and focusing mcb on planning,
reflexes, construction and combat ([motor design sketch](BARITONE_MOTOR_DESIGN.md)).
The mobs runs also show the first reflex job: Baritone survived night routes by
avoiding mobs, not by fighting them.
