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

## 0. Update — October 28 midday (Marc). Overrides §3–§4 where they differ.

After Phase 0 and the [Phase 0 fixes](PHASE0_FIXES_RESULTS_2026-10-28.md), Marc set
four further decisions. Work in this order: **A → B → C**, with D as the method for B and C.

| # | Decision |
|---|---|
| D9 | **Split the mod in two, fully independent.** `mcb-body` (the nervous system): sensing, applying controls, the per-tick route follower, actions (mine/place/attack/eat), reflexes, events to Python. `mcb-lab` (the test lab): world/course generation, resets, scoring, recording/observing *any* player. Neither depends on the other or shares code paths. The lab must score Baritone, Mineflayer or any other body exactly as it scores MCB, so it can outlive MCB if Marc pivots. |
| D10 | **Per-tick movement runs in `mcb-body`, not Python.** Python sends a route (with planned breaks/places) and objectives; the mod drives the Carpet body every server tick from live position/velocity and reports progress, blocked, done or needs-replan. Reflexes (e.g. dodging a zombie/creeper, retreat) also run in the mod. This removes the HTTP/Python round trip and the 3-tick lead from the movement loop, and is the real fix for late commands. A separate Python map/search process (shared-memory grid) is still worthwhile but secondary. |
| D11 | **Planner core is one phase with flags.** Phases 1, 2 and 4 (tick costs, optimistic unknown space, unified edit/macro edges) are built together, each behind its own flag, and evaluated with ablations (all off / each on / all on) on the five calm routes plus fixtures. |
| D12 | **Baritone as the apples-to-apples baseline, reimplemented, not copied.** Read Baritone's source (LGPL-3.0) and reimplement its move logic and cost values in MCB's own code, with a reference note to the Baritone class/constant each derives from. Do not paste Baritone code. Goal: same costs, same view range (D8), tick-for-tick execution, so the remaining difference is architecture. That comparison, plus licensing and per-bot cost, decides MCB vs Baritone. |

### Revised order

**A. Mod split (D9).** Move lab-only classes (courses, resets, natural runs,
development fixtures, reference geometry, causal scoring) into `mcb-lab`; keep
sensing, body adapter and control bridge in `mcb-body`. Each mod builds, loads and
passes its own checks alone and together. Lab scoring must work with a human or
Baritone client and no `mcb-body` installed. Re-establish the legacy and natural
harness on the split before moving on. Today's single mod is ~3.2k lines in 18
files, so this is a bounded refactor; requalify source-bound evidence afterwards.

> **Step A status (Oct 28, 2026): done, development evidence only.** `mod/` is now
> `mcb-body/` and `mcb-lab/`, two Gradle projects with separate packages, mixins and
> ports (8765 body, 8768 lab). `mcb-lab` has no Carpet dependency and scores any named
> player through one reset/scoring path. The wire changes are listed at the top of
> [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md). The Python client keeps the old interface
> and fans out compound ops, so harness callers barely changed.
>
> Live, both mods together, final source hash `3c46b074…` (`results/qualification/`
> unless noted): body mechanics 39/39 (`20261028T171014058999Z`), protocol 24/24
> (`…171103610861Z`), C1 reset ×3 with one state digest (`…171107813505Z`),
> adversarial episodes 29/29 (`…171138484284Z`), natural lethal-hit reporting
> (`results/natural_reporting/20261028T171408460232Z`), driven natural walk, success
> in 106 ticks (`results/spatial/20261028T171426790821Z`). Each mod also booted
> **alone**: the lab served and failed a reset for an absent player with
> `subject_not_present`; the body spawned and settled a Carpet body.
>
> Failed cohorts kept: protocol `20261028T165532032455Z` (hash `0789a582…`, the lab
> checked identity before its op allowlist) and adversarial `20261028T165714107133Z`
> (hash `1b855235…`, no lab controller watchdog). Both were fixed in the mods; the
> checks were not relaxed.
>
> Independent-client check completed later on Oct 28: Baritone finished natural R1
> in 579 ticks with `mcb-body` physically absent and its port unbound. The lab alone
> reset and scored the real client; both mods were restored afterward. Evidence:
> `results/lab_only_client/20261028T232805064827Z/summary.json` (development only).
>
> Not yet done: formal requalification. The mod source hash
> changed, so `docs/NAVIGATION_QUALIFICATION.json` and G1/G2 entries bound to the old
> hash are stale, and scored navigation refuses to run until the 20-reset
> qualification is repeated per course. One proof changed meaning:
> `policy_closed_until_begin` is now `motion_before_begin_cannot_score` (see
> BRIDGE_PROTOCOL.md).

**B. In-mod route follower and reflexes (D10).** Start with the ordinary moves
(walk, ascend, descend, diagonal, fall) reimplemented from Baritone's movement
logic (D12), then sprint/momentum, then the existing macros (cut, place, bridge,
pillar, staircase, gap). Python keeps planning and edit confirmation policy; the
follower enforces the same invariants (never step into unknown, confirm edits).
Acceptance: existing fixture families pass; zero late-command invalids on the
five calm routes; ticks compared to Baritone per route.

**C. Planner core (D11)** using Baritone-derived tick costs (D12), then the
frozen comparison (Phase 7) and survival/Jev profiles (Phase 6).

Phase 5 (movement speed) is absorbed into B. Phase 3 (interaction goals) can be
done any time; it is small.

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
   **Status:** Done in `water_movement.air_recovery_command` (BFS within air/`SWIM_TICKS` blocks); `can_surface` now scans to open air. The controller still calls `can_surface` and must switch to `air_recovery_command`.
2. Run `natural run --entrant comparison` on the five existing seed-1337 routes
   (`results/natural/routes-1337.json`), calm and night, one repetition each first.
   Segmented MCB uses its development movement/terrain profiles with all
   currently-passing families enabled.
   **Status:** Done (HAZARDS + `still_water`).
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

**Phase 0 fixes result (Oct 28):** a compact section grid now retains the whole
64-block view, with zero map-gap invalids. An optional pipelined control loop
(`dev-pipelined-v1`) is available. Calm natural routes: MCB 3/5 reached the goal
(Phase 0: 0/5), at about 2.4–2.7× Baritone's time. Late actions at 64-block
awareness remain the main harness issue. See
[PHASE0_FIXES_RESULTS_2026-10-28.md](PHASE0_FIXES_RESULTS_2026-10-28.md).

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
   **Status:** Done in `water_movement.air_recovery_command` (BFS within air/`SWIM_TICKS` blocks); `can_surface` now scans to open air. The controller still calls `can_surface` and must switch to `air_recovery_command`.
2. `still_water()` accepts `minecraft:bubble_column` (water fluid, no collision, no
   `level` → defaults to `"0"`). A magma-driven down column is a drowning trap.
   Add it to `HAZARDS` or reject it in `still_water()`.
   **Status:** Done (HAZARDS + `still_water`).
3. Telemetry sends `isInWater()` but drowning depends on eye submersion. Add
   `eye_in_water` (`isUnderWater()`) and end air recovery when the head is out
   rather than at 90 % air; air refills while surface-swimming.
   **Status:** Partial: `head_out_of_water(survival)` uses `eye_in_water`, falling back to `in_water`. Controller must use it instead of the 90 % air test.
4. Entry/exit only at equal height. Add surface → land +1 (jump out) and
   land → water drops (water cancels fall damage).
   **Status:** Partial: predicates `water_exit_up` and `water_landing` exist; `route_search.py` does not call them yet.
5. Flowing water is all-or-nothing (`level != 0` rejected); give it a higher cost
   instead, and model push direction later.
   **Status:** Partial: `water_cost_multiplier` (1.5x flowing); routing still rejects flowing; push direction not modelled.
6. Swim cost should be ticks (≈2× walk) per D2.
   **Status:** Done: `SWIM_TICKS`, `WALK_ON_WATER_PENALTY`; not yet used by `route_search.py`.
7. Decide whether "get head above water" is allowed when `allow_swim` is false
   (survival vs the no-recovery-fallback rule) and record it in `DECISIONS.md`.
   **Status:** Done: allowed as a reflex; see `DECISIONS.md`.
8. Fixtures: all ten variants are the same 1-wide flush channel. Add raised banks,
   overhang during recovery, bubble column, flowing water, lily pad/ice, lava
   adjacency, deep water, knockback with swimming disabled.
   **Status:** Done in `tests/test_water_movement.py`.

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
