# Baritone reference baseline

Created October 26, 2026. This is unscored development evidence, not gate evidence.

## Setup

- Baritone API v1.20.0 for Minecraft 26.3, pinned in `config/baselines.lock.json`.
- It runs in a real Fabric client launched by Prism Launcher under the signed-in
  account (`mclab baritone prepare` / `launch`). The Microsoft login never leaves Prism.
- `client-bridge/` is a separate client mod (its own Gradle project) exposing a
  loopback, bearer-token HTTP bridge: `status`, `goto`, `mine`, `cancel`, `policy`.
  The token lives in `runtime/client-bridge.token` and is passed to the game by file
  path, because Prism logs JVM arguments.
- `src/mclab/baritone_runner.py` runs attempts. Bridge acknowledgments are not
  outcomes; the server evaluator is.

## Server-side scoring of a human client

The server mod gained a human-subject path (`HumanCourse.java`, admin ops
`human_course_reset`, `human_course_begin`, `human_course_end`, `human_course_stop`):

1. The named player is put in spectator 30 blocks above the course spawn (outside
   the reset envelope) while the shared `CourseReset` builds and verifies the arena.
2. The player is reset to ordinary survival state (shared `CarpetBodyAdapter.resetPlayer`)
   at the course spawn and must settle there for 20 ticks with an unchanged pose.
3. `CourseEpisode` scores the player with the same causal break/drop/pickup ledger,
   bounds, pit, tick limit and mutation rules used for Carpet bodies.

Mouse input on the client during reset changes the heading and fails the reset
(`human_changed_since_reset:heading`). Leave the game on the pause menu during runs;
multiplayer does not pause and Baritone still drives.

This path is not part of the G2 or navigation qualification scope. Changing the mod
changed its source hash, so G2 and the C2–C5 reset checks were re-run (see
`gates.json` and `NAVIGATION_QUALIFICATION.json`).

## Results (seeds 5–24, same layouts as the scored V8 runs)

| Course | Baritone success | Baritone mean ticks | V8 success | V8 mean ticks |
|---|---|---|---|---|
| C2 | 20/20 | 112.3 | 20/20 | 447.0 |
| C3 | 20/20 | 120.3 | 20/20 | 207.6 |
| C4 | 20/20 | 115.3 | 20/20 | 482.0 |
| C5 | 20/20 | 123.2 | 20/20 | 378.6 |

Runs are in `results/baritone/`. The first C4 batch aborted before any attempt on a
dropped client-bridge connection; it was re-run in full. The client now reconnects once.

## Limits of the comparison

- Baritone reads the full loaded client world; V8 uses the filtered local-visibility
  policy stream and observed-cell memory.
- Baritone moves and sprints continuously; V8 issues bounded 5-tick commands with a
  3-tick apply offset.
- Equal reliability with a 2–4× tick gap points at motor efficiency (turning, the
  C4 rise/descent) rather than decision quality. It is not a model comparison.
