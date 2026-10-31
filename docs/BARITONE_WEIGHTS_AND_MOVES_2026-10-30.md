# Baritone weight set and movement comparison (Oct 30, 2026)

Reference for Phase 6 (Jev profiles) of
[NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md). Source:
Baritone v1.20.0, the pinned tag, read per D12 (values and rules only, no code
copied). The machine-readable base set is
[config/weights/baritone-v1.20.0.json](../config/weights/baritone-v1.20.0.json).
Nothing here changes planner behaviour yet.

## 1. Baritone's weight set

Baritone has one global cost context
(`CalculationContext`) built from `Settings`. Every movement reads it, so costs
stay consistent within a path. Cost-relevant values:

| Group | Values |
|---|---|
| Move costs (ticks) | walk 4.633, sprint 3.564, sneak 15.385, in water 9.091, soul sand 9.266, ladder up 8.511 / down 6.667, walk-off 3.706, centre after fall 0.927, jump 3.163, fall N blocks from vanilla gravity |
| Penalties | place 20, break +2 per block, jump +2 (hunger), walk on water +3 |
| Permissions | sprint, break, place, downward (dig down) on; parkour, parkour-place, diagonal ascend/descend off; max fall 3 (20 with water bucket) |
| Search | heuristic = octile XZ × 3.563 + Y term (up 3.163/block, down 3.894/block); best partial path chosen over 7 cost coefficients (1.5 to 10), at least 5 blocks out; nodes on the previous path cost ×0.5 when replanning |
| Timing | primary 500 ms, failure 2,000 ms, plan-ahead 4,000/5,000 ms; plan next segment when < 150 ticks remain; drop the last 10% of paths ≥ 30 moves; replan if a move's cost rises > 10 ticks; stop 5 moves before a blocked move |
| Avoidance (off by default) | ×1.5 cost within 8 blocks of hostile mobs, ×2.0 within 16 of spawners; below 1.0 attracts |

### Per-goal variants

Baritone changes the cost context for only three tasks. The rest change only the
goal shape.

| Task | Goal | Cost changes |
|---|---|---|
| goto | GoalBlock / Near / XZ / YLevel | none |
| mine | composite of ore positions (+ dropped items); legit mode strip-mines at y = -59 | needs break on |
| get to block | adjacent to any known instance; blacklist closest on failure | reachability scan ignores break permission |
| **build** | place/break/adjacent per schematic block | **jump penalty 12** (was 2), no previous-path favouring, placing a correct block **free**, wrong block ×2 (×3 if not in inventory), breaking a correct block **×10** |
| follow | GoalNear(entity, 3) | none |
| explore | nearest unloaded chunks, keep y = 64 | Y term added to heuristic |
| farm | crops | none |
| flee | GoalRunAway: away from threats (0.6) + keep Y (1.5) | none |
| **walk-off** (elytra) | GoalYLevel | **no break/place**, unlimited fall |
| hunt / fight | — | Baritone has no combat; its avoidance docs suggest coefficient < 1 to approach mobs |

Mapping to the direction doc's named profiles: `race` = goto; `frugal` = raise
the place/break penalties; `hunt` = avoidance coefficient < 1; `safe` = avoidance
on; `escort` = follow. Jev picks a goal, then adjusts these weights per D7.

## 2. Movement inventory: MCB vs Baritone

| Move | MCB today | Baritone | Gap |
|---|---|---|---|
| Flat walk | sprint 3.564 if allowed | sprint only when nothing to break and not in water | same |
| Ascend 1 | max(jump, walk) = 4.633; motor does **not** sprint uphill | + jump penalty 2 = 6.633; motor **sprint-jumps** ascends (`sprintAscends`) | cost parity; motor speed |
| Descend 1 | walk-off + max(fall, centre) | same | — |
| Fall 2–3 | same form | same; also **any height into still water**, bucket falls to 20 | water landings |
| Diagonal | both side columns must be **standable** | side cells need only be **passable** (can cut over a hole, not lava); can edge past one blocked side (cost ≈ 2 × walk, no sprint) | MCB rejects many corners Baritone takes |
| Diagonal ascend/descend | none | optional (off by default) | — |
| Gap jump | widths 1–2, cost walk × (w+1); landing must be full cube, gap ≥ 2 deep | parkour off by default; when on widths 1–3 (3 needs sprint, 4 × sprint), jump-up landings, place-landing variant; + jump penalty | MCB has parkour Baritone runs without (fairness note) |
| Water | edges exist, but the native motor **rejects swim**, so R2/R3 bridge over water | part of every move at 9.091/block (+3 to walk on) | **largest gap** |
| Break through (traverse) | edit edge: break ticks + 2 + 6 aim + 15 per edit step | break ticks + 2 | MCB overheads are measured; fine |
| Bridge | support stacks up to 3, side anchor; 20 + 15/bridge + 20/stack + 15 step | 20 with side face; sneak back-place (walk → sneak) otherwise | similar |
| Ascend with place | stack under target | place one block, needs a side face | similar |
| Descend with breaks | **none** (edits only level or +1) | breaks feet/head/above at target | missing |
| Dig down | **none** as a search edge | fall 1 + break time, on by default | missing |
| Pillar | legacy `terrain_route` only, not in `RouteSearch` | jump + 20 + 2 (+0.1 on air) | missing in planner |
| Ladders/vines | none | 8.511 up, 6.667 down | missing |
| Sand/gravel | refuse any cut next to a falling block | price the falling stack above; refuse only unsupported side neighbours | MCB too strict in sandy terrain |
| Unknown space | plans through it (×2 penalty, buried exits priced as dig-out) | treats unloaded chunks as impassable, cuts paths at load boundary | MCB-only |
| Slabs/shapes | collision-box standing heights | bottom-slab rules | similar |

## 3. Recommendations, ranked by expected effect on R1–R5

1. **Native water movement.** R2 and R3 bridge across water Baritone simply wades
   through at 9.1 ticks/block. Needs swim in the `mcb-body` motor (already listed
   as deferred).
2. **Baritone's diagonal rule** (passable sides, edging). Cheap planner change;
   straightens most routes.
3. **Sprint-jump ascents in `RouteMotor`.** Every climb currently drops to walk.
4. **Octile heuristic.** Euclidean under-estimates diagonal-capable movement, so
   the search expands more nodes than Baritone for the same answer.
5. **Partial-path coefficients and the 10% cutoff.** Picking the partial end by
   pure estimate (what we do) is what walked R3 to the very edge of knowledge;
   Baritone weighs cost too and trims the uncertain tail.
6. **Previous-path favouring ×0.5.** Damps replan flip-flops without the special
   cases we added.
7. **Jump penalty 2** on ascend, gap and pillar edges for cost parity.
8. Missing moves: descend-with-breaks, dig-down, pillar as a planner edge, water
   landings, falling-block pricing.

Items 2, 4–7 are planner-only and testable offline with the replay tools; 1 and 3
change `mcb-body` and need requalification.
