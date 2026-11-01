# Phase 7 frozen comparison (ROADMAP item 12): 10 routes x 3 seeds x 3 reps,
# MCB (segmented_goal, default planner) and Baritone with parkour (moves matched).
# Run from the repo root with the server stopped. Each run prints its own JSON.
param([int[]]$Seeds = @(1337, 9001, 42424242))
$ErrorActionPreference = 'Stop'
$shared = @('--condition', 'calm', '--repetitions', '3', '--view-distance', '4',
    '--awareness-profile', 'config/stepc/awareness-sections-64.json', '--stall-seconds', '5')
# MCB-only settings; run() refuses them for the Baritone entrant.
$mcb = @('--movement-profile', 'config/stepc/movement-baritone.json',
    '--terrain-profile', 'config/stepc/terrain-stepc-baritone-moves.json',
    '--control-profile', 'dev-native-route-v1')
foreach ($seed in $Seeds) {
    $routes = "results/natural/routes-$seed-10.json"
    & rtk proxy uv run --locked python -m mclab.cli natural run --seed $seed --routes $routes --entrant segmented_goal @shared @mcb
    & rtk proxy uv run --locked python -m mclab.cli natural run --seed $seed --routes $routes --entrant baritone --baritone-parkour @shared
}
