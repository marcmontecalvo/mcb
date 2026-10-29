# R2 steering fix and control-profile comparison

October 28, 2026. Development-only evidence on natural seed 1337, calm R2.
This is a diagnostic comparison of individual attempts, not a reliability
cohort, source qualification or a general speed claim.

## Failure and fix

The original lockstep attempt
[`20261028T225406643170Z`](../results/natural/20261028T225406643170Z/summary.json)
timed out after 6,000 ticks. The final position stayed at approximately
`[-99.1985, 56, -92.7114]`. Its last commands alternated yaw between 5.3636 and
13.9769 degrees without forward input. The final planner reason, `background
search budget`, did not explain this motor stall.

`SegmentedController._follow_route` tried distant straight-route waypoints first.
It accepted a turn toward a distant waypoint before that shortcut's swept motion
could be checked. Once aligned, the fresh safety check rejected the shortcut and
the nearer waypoint requested an opposing turn. The next decision selected the
distant heading again, so the body never moved.

The fix lets only the immediate waypoint request a turn. Farther waypoints can
still provide forward motion when already aligned and freshly safe. Freshness,
support, collision, edit permissions and command timing limits are unchanged.

`test_unsafe_lookahead_cannot_turn_body_away_from_safe_near_waypoint` recreates the
fractional position and headings beside a missing-support column. It fails on
the original code, passes after the fix, and verifies that the farther shortcut
is still rejected even when aligned toward it.

## Attempt ledger

All failed attempts are retained; none were rescored or excluded from this ledger.

| Run | Code | Control | Outcome | Ticks | Notes |
| --- | --- | --- | --- | ---: | --- |
| [225406643170Z](../results/natural/20261028T225406643170Z/summary.json) | Before fix | Lockstep | Failed | 6,000 | Steering oscillation; tick limit |
| [230524691908Z](../results/natural/20261028T230524691908Z/summary.json) | Before fix | Lockstep | Invalid | 1,395 | Late-command limit; did not reproduce the same stalled trajectory |
| [230707159321Z](../results/natural/20261028T230707159321Z/summary.json) | After fix | Lockstep | Success | 1,787 | 2 discarded late commands |
| [230917200471Z](../results/natural/20261028T230917200471Z/summary.json) | After fix | Pipelined | Invalid | 1,375 | 10 discarded late commands exceeded limit 9; about 10 blocks short |
| [231058310383Z](../results/natural/20261028T231058310383Z/summary.json) | After fix | Pipelined | Success | 1,431 | 2 discarded late commands |

The successful post-fix pair uses identical recorded route, seed, mod build,
Python source hashes, awareness, movement/memory/terrain policies, goal radius,
tick limit and view distance. The selected control profile differs. World
snapshots are restored and the server restarted for each attempt by the natural
runner. Asynchronous acquisition/planning and motor trajectories can vary.

- Mod source hash: `3c46b074fc1f3963274eb2bb2488664a0c3412b7e0cc4575108fe3517f982a0a`.
- Segmented controller hash: `2153e84d881ba3f0fd1a1e84e30be74c663f9bb5d0963f4c199bd7dd662b1e87`.
- Lockstep: 89.35 game seconds; pipelined: 71.55 game seconds.
- Difference: 356 ticks / 17.8 game seconds, or **19.9% fewer ticks** for pipelined
  in this successful pair. Both had zero damage, breaks or confirmed placements.

This does not establish an average improvement: the failed pipelined attempt is
part of the result. Late-command reliability remains open under Step B/D10 in
[the navigation direction](NAVIGATION_DIRECTION_2026-10-28.md).

## Reproduce

Use a separate JSON containing only R2 from `results/natural/routes-1337.json`;
the manual runs above used `runtime/routes-cli-R2.json`. From the repo root:

```powershell
$profileDirectory = 'docs/evidence/2026-10-28-phase0'
rtk proxy uv run --locked python -m mclab.cli natural run --seed 1337 --routes runtime/routes-cli-R2.json --entrant segmented_goal --condition calm --repetitions 1 --view-distance 4 --awareness-profile "$profileDirectory/awareness-chunk-64.json" --movement-profile "$profileDirectory/movement-passing-families.json" --terrain-profile "$profileDirectory/terrain-passing-families.json" --control-profile lockstep-three-tick-v1
```

For the other control profile, change only the last value to `dev-pipelined-v1`.
The runner restores the original lab properties and stops the server afterward.
