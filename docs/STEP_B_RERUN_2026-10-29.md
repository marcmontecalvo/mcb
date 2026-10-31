# Step B live rerun — October 29, 2026

Development comparison, not formal qualification. Both entrants ran the five
original seed-1337 calm routes, one repetition each, on an unchanged source.

Evidence: `results/natural/20261029T205639210651Z/summary.json`, with separate
per-attempt `events.jsonl` recordings and MCB map databases beside it. All ten
attempts completed. Each attempt restored the natural-world snapshot; the existing
runner cleared Baritone's disk cache and relaunched its client for each reference
attempt.

Source: `2ca44cfc6f5bf4686d6c080925d07e9c333e985393d892555c8a153e8a2d606b`.
Installed body/lab jar checksums match `runtime/mod-build.json`.

MCB used `dev-native-route-v1`, chunk awareness radius 64/full height, persistent
128 MB memory, diagonals/shapes/sprint enabled, max drop 3 and max gap 2, no swim.
Terrain permissions and budgets match the preceding MCB run. Server view distance
was explicitly four chunks for both entrants. The preceding October 29 comparison
recorded six chunks, so its timings are contextual observations, not a controlled
before/after speed experiment.

| Route | MCB ticks | Baritone ticks | MCB / Baritone | MCB route splices |
|---|---:|---:|---:|---:|
| R1 | 1,130 | 581 | 1.94 | 12 |
| R2 | 982 | 531 | 1.85 | 23 |
| R3 | 1,525 | 935 | 1.63 | 32 |
| R4 | 1,836 | 801 | 2.29 | 21 |
| R5 | stalled, 1,576 | 848 | — | 11 |

MCB reached 4/5 goals; Baritone reached 5/5. Every attempt recorded zero damage,
no death and no disconnect. All five MCB attempts recorded zero timing rejections;
none had a controller failure or accepted-identity error. Across all five MCB
recordings, all 112 `native_route_finished` events have empty rejection reasons.
There were 99 route splices in total. No damaging corner overshoot was observed;
this does not prove the absence of every harmless overshoot between samples.

## R5 and the Step C boundary

R5 still failed the goal test. Its terminal navigation summary says `planning` /
`background search resumed`, but the recording gives additional evidence: near
the end, navigation cycles through `terrain_planning` and `terrain_incomplete`
(ticks 1557/1558, 1588/1589 and 1618/1619). The fallback is therefore reached;
the former endless known-walking search is not the whole remaining failure.
No native edit step was submitted, and no block was broken or placed on R5.
The run does not prove that a digging plan was found or executed. The exact
remaining terrain-search/breakability limitation needs Step C investigation;
do not attribute the entire live failure to terracotta without replay evidence.

The motor rerun is clean on these routes, and route splicing is exercised live.
It does not establish an overall speed improvement: R2 improved relative to the
previous run, R3/R4 stayed similar, and R1 took longer and placed five blocks
where the preceding run placed none. Baritone R3 also varied substantially.

Retain R5 as the first planner regression case for Step C (tick costs, broader
safe excavation, bounded search progress, and eventually unified edit/detour
selection). Do not mark Step B's five-goal acceptance or formal source-bound
qualification complete. No Step C implementation was changed during this rerun.

## Checks and cleanup

- Full Python suite: **687 passed** (34.77 seconds).
- Both Gradle builds passed. Body verification: RouteMotor 40, StepMotor 11,
  GapMotor 9, RouteReflex 5, plus awareness and terrain serialization checks.
  Lab verification: causal ledger, navigation admission and reference geometry.
- The runner restored `level-name=world`, original `view-distance=6`, and left
  the server stopped. No mod replacement or source changes were made for the run.
