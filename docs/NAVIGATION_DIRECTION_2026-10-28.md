# Navigation direction and handoff — October 28, 2026

**Status: authoritative.** This document sets the current direction for land
navigation, route planning, model roles and the Baritone comparison. Where an
older document disagrees with it, this one wins. See
[Superseded and legacy documents](#superseded-and-legacy-documents). When you touch
an older document during normal work, bring it into line with this one (or mark
the conflicting section legacy) rather than leaving two answers in the repo.

Prepared from a review of the docs and `route_search.py`, `terrain_route.py`,
`terrain_steps.py`, `navigation_segmented.py` and `water_movement.py` at the
October 27 checkpoint (`55d160c` plus uncommitted work). Nothing below has been
run live yet; every claim about performance is a hypothesis until Phase 0 measures it.

## 1. Product goal (why the choices below)

The goal is an AI-controlled body that plays alongside Marc, his son and their
friends, eventually several bots at once. Navigation must therefore be fast,
survivable, cheap to run per bot and able to fight when the situation calls for it.

- **Body:** server-side Carpet bodies stay the primary target. No client, no
  Microsoft account per bot, lower resource cost.
- **Baritone:** reference opponent, not the plan. Pivot rule: if, after both
  entrants have *equivalent terrain awareness*, the MCB planner still clearly loses
  on the frozen natural comparison and the gap does not close with the phases
  below, reconsider Baritone as motor (see `BARITONE_MOTOR_DESIGN.md`).
- **Combat is not out of scope.** Mob avoidance is a *cost weight*, not a policy.
  For head-to-head Baritone tests both entrants avoid mobs; for real play the weight
  can be zero or inverted ("go fight"). Combat execution (aim, strafe, shield,
  retreat) is separate later work.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | Route choice is **deterministic code** (A* family) over observed + remembered geometry. It must return in milliseconds for local routes and replan continuously as terrain is revealed. |
| D2 | **Edge costs are game ticks**, plus explicit risk and resource terms. No unitless costs. Walk ≈ 4.63 ticks/block (20/4.317), sprint ≈ 3.56 (20/5.612), swim ≈ 9.1 (20/2.2); break time from block hardness × actual tool × enchantments/effects, including bare hand and wood/stone tools. |
| D3 | **Unknown space is planned through optimistically at a penalty** (start ~1.5–2× the walking cost; tune in Phase 2) and replanned as observed (D* Lite or repeated A*). The *motor* still never steps into, onto or through an unobserved cell. Existing safety invariants are unchanged. |
| D4 | **Walking, breaking, placing and the macros compete in one search.** Cut-step, foothold, staircase, pillar, bridge, gap jump and drop become search edges with preconditions, tick costs and resource effects. The four-move rolling forecast stays as the *execution sequencer and resource reserver*, not the route chooser. |
| D5 | **Goals are sets, not single cells.** Add an interaction goal: any safe standing cell from which the eye is within 4.5 blocks of the target with a clear ray (crafting tables, chests, ores, logs, mobs). Keep the existing position/radius goal for "go to here". |
| D6 | **Jev sets objectives and weights; it does not pick waypoints.** Jev (and the larger planner) choose the goal type, a cost profile (speed, safety, block spending, mob avoidance or engagement, terrain preservation) and handle tactical events (threat, pickup opportunity, stuck/abandon). The deterministic planner turns that into the single best route. Jev may also rank a handful of *qualitatively different* routes (e.g. "40 s detour, no edits" vs "12 s, dig 2"). Model latency must never sit inside the movement loop. |
| D7 | **Survival is in the cost function.** Hazard terms: known hostiles (radius/strength), spawnable darkness, cliff-edge knockback exposure, adjacent lava/fire/powder snow, drop height relative to current health. Plus gates: don't take a lethal-risk drop at low health, eat before long routes. Immediate safety reflexes remain local and never wait for a model. |
| D8 | **Fair comparison means equivalent awareness.** MCB uses the `chunk` awareness preset with radius matched to what the Baritone client can actually have loaded (see §4). Results without matched awareness are diagnostics only. |

## 3. Layers

| Layer | Owner | Cadence | Output |
|---|---|---|---|
| Strategic planner | larger LLM | seconds, event-driven | task/subgoal, cost profile defaults |
| Tactical | Jev | ≤1 s, event-driven, never blocking motion | goal choice, cost-profile adjustments, interrupt/resume, route-class choice |
| Route planner | deterministic Python (this document) | continuous, ms | best route under current profile; edits and macros included |
| Executors and motor | existing `StepExecutor`, pillar/bridge/gap/staircase executors, segmented motor | every tick | controls with fresh-geometry checks |

The existing executors, fresh-geometry checks, edit/stock confirmation, failed-edge
exclusion, protected footholds, disabled-permission refusals and evidence discipline
are **kept**. This direction replaces how routes are *chosen*, not how steps are
*executed*.

## 4. Phased plan

Each phase ends with unit tests passing, its acceptance met, and a short results
note appended to this document (or a linked proof file). Keep failed cohorts in the
evidence ledger as before.

### Phase 0 — Baseline at matched awareness (do first; no planner changes)

Purpose: find out what actually breaks on real terrain before changing anything.

1. Match awareness. `runtime/server/server.properties` has `view-distance=6`
   (≈96-block radius available to a client), but the `chunk` preset's horizontal
   radius caps at 64. For the parity run, set `view-distance=4` (64 blocks) and
   use `chunk` with horizontal radius 64, full height. Record both values in the
   manifest. (Alternative: raise the awareness cap to 96; only if cheap.)
   Confirm the Baritone client's own render distance is ≥ the server view distance.
2. Run `natural run --entrant comparison` on the five existing seed-1337 routes
   (`results/natural/routes-1337.json`), calm and night, one repetition each first.
   Segmented MCB uses its development movement/terrain profiles with all
   currently-passing families enabled.
3. Deliverable: a table like `NATURAL_TERRAIN_COMPARISON.md` plus a failure
   classification per MCB attempt: planner blind spot (unknown treated as wall),
   edit-vs-detour misjudgment, motor stall/stop time, survival, other.
   This classification decides priority among Phases 1–5.

**Phase 0 result (Oct 28):** Baritone 10/10, MCB segmented 0/10 (4 invalid),
V8 goal 2/10 at matched awareness (view distance 4, chunk radius 64, Baritone
cache cleared). MCB failures: survival 4 (all night), harness/infrastructure 4,
edit-vs-detour 1, planner stall 1. Before Phase 1, the 64-block awareness
pipeline needs fixing: the 20,000-cell hot-map cap, persistence backlog and
occasional late actions. See [PHASE0_NATURAL_BASELINE_2026-10-28.md](PHASE0_NATURAL_BASELINE_2026-10-28.md).

### Phase 1 — Time-based cost model

- Replace `1 / 1.4 / 1+0.4|dy| / width+3.5` and flat `break_penalty=3.0`,
  `placement_penalty=4.0` with tick costs (D2) in both `route_search.py` and
  `terrain_route.py`.
- Break time = vanilla formula from block hardness, the best hotbar tool, harvestability,
  efficiency, haste/fatigue, on-ground/in-water penalties. Hand and wood/stone tools
  must work; widen the breakable set beyond stone/cobble/dirt/grass with an explicit
  unsafe list (falling blocks above, fluids adjacent, protected blocks).
- Mining a block that drops a placeable block is a resource credit, not just a cost.
- Acceptance: unit fixtures where the cheaper choice flips correctly
  (hand vs pickaxe on stone; 1-block dirt cut vs 10-block detour; 3-block stone
  tunnel by hand vs 25-block detour).

### Phase 2 — Optimistic planning through unknown space

- Unknown cells become traversable *for planning only*, at a configurable penalty
  multiplier. Known-solid stays solid; known-hazard stays forbidden.
- Replan when admitted geometry changes cells on or near the current route.
- Motor contract unchanged: never commit to an unobserved cell.
- Remove the "walk to closest known node, then start terrain edits" fallback;
  edits are chosen because they are cheapest, not because known walking ran out.
- Acceptance: fixture with a ridge hiding a walkable ramp beyond visibility —
  bot detours instead of digging; Phase 0 "blind spot" failures re-run.

### Phase 3 — Interaction goals

- Goal type `interact(block_pos, reach=4.5)`: success set = safe standable cells
  with eye-to-target distance ≤ reach and an unobstructed ray. Heuristic = distance
  to the nearest point of that set.
- Acceptance: crafting table on a ledge 3–4 blocks above approach terrain is
  reached from below without climbing when reachable; table behind a wall is not
  "reached" through the wall.

### Phase 4 — Unified search with macro edges

- Express cut-step, foothold, staircase, pillar, bridge, gap jump and drop as
  edges in the main search with preconditions, tick cost, material use and
  predicted cell changes (private overlay per search, as today).
- Bound edits per route and per edge; keep budgets/reserves/protected cells.
- The four-move forecast consumes the chosen route's next moves for reservation
  and sequencing only.
- Acceptance: all existing enabled/disabled family cohorts still pass; new mixed
  fixtures where a detour beats an edit and vice versa.

### Phase 5 — Movement speed

- Keep following the current best (optimistic) route while search continues;
  remove "stand still while planning" except where an edit precondition is unknown.
- Do not clear the route on every non-forward tick; distinguish "turning" from
  "blocked".
- Sprint on by default when food allows; sprint-jump one-block rises; carry
  momentum through corners; string-pull/smooth paths across known safe cells.
- Acceptance: flat and rolling fixtures within 1.25× ideal sprint time;
  Phase 0 routes re-run.

### Phase 6 — Survival weights and Jev profiles

- Implement D7 hazard terms and health/food gates with per-profile weights.
- Named cost profiles: `race` (Baritone parity), `safe`, `frugal` (min blocks),
  `hunt` (mob avoidance inverted toward a target class), `escort` (stay near a
  player).
- Jev contract (replaces `bounded-local-waypoint-selector-v1`): input = compact
  situation (task, health/food, time of day, visible threats, inventory summary,
  optional 2–6 route-class summaries); output = goal + profile + optional route-class
  choice + interrupt/resume decision. Deterministic fallback profile when Jev is
  late or invalid; record which produced each commitment.
- Acceptance: night routes with `safe` show lower exposure/damage than `race` at
  a measured time cost; Jev screened on recorded situations before live use.

### Phase 7 — Frozen natural comparison

Unchanged target from the sprint: 10 routes × ≥3 seeds × 3 repetitions per entrant,
MCB ≥ 27/30 and median per-route paired time ratio ≤ 2× Baritone, at matched
awareness, plus night diagnostics with damage/death/exposure. This is the input to
the pivot rule in §1.

### Later (not in this plan)

Combat execution, multi-bot coordination (shared map memory, not colliding,
following/escorting players), crafting/inventory, Waystones/teleport edges,
underwater navigation.

## 5. Swimming work in progress — review notes

Swimming is being built in parallel. Notes from the review of
`water_movement.py`, to fold in as convenient:

1. Air recovery returns `{}` when it cannot surface straight up (overhang, ice,
   lily pad, ceiling) — the bot waits while drowning. Recovery should route to the
   nearest breathable cell within remaining air. `can_surface` checks only 2.4
   blocks up, not to open air.
2. `still_water()` accepts `minecraft:bubble_column` (water fluid, no collision, no
   `level` → defaults to `"0"`). A magma-driven down column is a drowning trap.
   Add it to `HAZARDS` or reject it in `still_water()`.
3. Telemetry sends `isInWater()` but drowning depends on eye submersion. Add
   `eye_in_water` (`isUnderWater()`) and end air recovery when the head is out
   rather than at 90 % air; air refills while surface-swimming.
4. Entry/exit only at equal height. Add surface → land +1 (jump out) and
   land → water drops (water cancels fall damage).
5. Flowing water is all-or-nothing (`level != 0` rejected); give it a higher cost
   instead, and model push direction later.
6. Swim cost should be ticks (≈2× walk) per D2.
7. Decide whether "get head above water" is allowed when `allow_swim` is false
   (survival vs the no-recovery-fallback rule) and record it in `DECISIONS.md`.
8. Fixtures: all ten variants are the same 1-wide flush channel. Add raised banks,
   overhang during recovery, bubble column, flowing water, lily pad/ice, lava
   adjacency, deep water, knockback with swimming disabled.

## Superseded and legacy documents

Banners have been added to these. Treat the listed parts as legacy; the rest of
each document remains valid history or reference.

| Document | Superseded part | Still valid |
|---|---|---|
| `JEV_NAVIGATION_PLAN.md` | Jev as a bounded local-waypoint selector (`bounded-local-waypoint-selector-v1`) | Evidence discipline, deadlines, stale-response handling, no hidden-state access |
| `HYBRID_ARCHITECTURE.md` | Jev choosing local waypoints/behaviors inside the motor loop | Plan/commitment separation, priorities, interrupt/resume, observation rules |
| `BARITONE_MOTOR_DESIGN.md` | "Baritone as the long-range motor" as the default direction | Override/handoff mechanism if the pivot rule fires |
| `ROLLING_TERRAIN_PLANNING.md`, `HILL_NAVIGATION.md` | "Known walking search first; terrain beam only when walking fails" as the route chooser | Four-move forecasts as execution sequencing; executors, confirmations, evidence |
| `PAUSE_HANDOFF_2026-10-27.md` | "Remaining work" ordering | Runtime state, source hashes, latency findings |
| `NATURAL_TERRAIN_COMPARISON.md` | Conclusion favoring Baritone-as-motor (V8 ran with ±20-block local memory vs Baritone's full client world) | Raw results and setup as history |

`Minecraft_AI_Technical_Handoff.md` is the original specification and is not edited.
`SPRINT_ACCEPTANCE.json` remains the acceptance tracker; its walking/breaking/placing
"compete in the cost model" requirement is consistent with D4.

## Open questions for Marc

- Phase 0 awareness match: lower server `view-distance` to 4 for parity runs, or
  raise the awareness cap to cover 6? (Default here: lower to 4.)
- Should the `race` profile allow Baritone-equivalent block breaking/placing
  limits, or keep MCB's current budgets for the first baseline? (Default: current
  budgets, recorded in the manifest.)
