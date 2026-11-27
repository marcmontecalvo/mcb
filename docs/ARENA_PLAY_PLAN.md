# Arena game night and experiment plan

## Outcome

Two friends on stock Java Edition 26.3 can join the running arena server, create a game, recruit and name bots, play, watch and rematch with the dashboard closed. The lab can run the same match definition repeatedly and compare competing lineups. Crossroads is the four-team acceptance map. Vault remains a co-op map.

## Delivery order

1. Make fresh PvP mob-free and visibly confirm the choice. Preserve explicit optional hazards and saved matches. Co-op and kill race require waves. Derive team controls and capacities from the chosen map. Offer only compatible matches and report which capacity was exceeded.
2. Build the private game lobby. Use the server mod's vanilla interactions to expose Play, Watch and Play Again, backed by the existing arena instance and bot host. Show available maps, modes, objectives, open sides, named participants, spectators and capacity. Create or join any available side. The first host opens the game and can transfer control. Each player controls their own seat. Only the host changes rules or adds, names, moves or removes bots and mob groups. Provision through the existing fleet. Track connected bots independently from requested slots and surface failures. Support a balanced default and an explicit uneven challenge, including ten against one where capacity allows.
3. Keep one authoritative lobby revision across game and dashboard. Every participant sees and approves the exact roster, map, hazards, kits and rules. Material edits invalidate human readiness with a reason. Bots must be connected. Start an atomic countdown, freeze changes until reopened, cancel on departure or unready, and let late arrivals spectate and queue. Return to the lobby on completion, show the recap and offer a fresh ready check for a rematch. Specify host loss and restart recovery. Automated lab matches can run without human participants.
4. Expand team membership to include counted allied mob groups. Neutral wave hazards remain independent. Start with mobs for which targeting, friendly damage, kill credit, spawn capacity and victory can be verified. Support team kits and individual overrides, mixed and mob-only teams, elimination and draws. Reuse the existing match runtime, board and artifact pipeline.
5. Add PvP elimination (default), per-participant extra respawns and equal per-team respawn pools. Apply identical rules to humans and bots. Reserve exactly once on death, re-enter on a team pad after the countdown, resume bot combat, and expose alive, returning and remaining counts. Resolve disconnects and forfeits without indefinite waits. Victory accounts for pending returns. Record configuration, death, reservation and return evidence.
6. Save named, versioned scenarios with exact kits, profiles, rosters, rules and map. Duplicate, edit one factor, repeat and swap spawn sides. Compare wins/completed rounds, duration and damage, show failed and abandoned starts separately, and link individual runs.
7. After gameplay qualification, prototype four solid-color uniforms and optionally hidden helmets with armor, enchantments and durability preserved. Keep the colored square and readable name. Verify visibility, team changes, respawns and late joins on two stock clients.

## Verification

- Write and run failing tests before implementation. Aim for 90% coverage with an 80% minimum. Check old saved setups, map limits and concurrent edits through real entry points.
- With the dashboard closed, two vanilla clients create teams on Crossroads, add bots on all four sides, ready and play zero-hazard PvP. A spectator can watch and join the next round. Switch teams and play a three-team rematch. Exercise host transfer, stale readiness and an intentional uneven match.
- A human, a bot and two skeletons can oppose two bots and three zombies. Repeat with mobs alone. Verify no allied targeting, correct elimination, accurate recap and cleanup.
- Under a one-respawn pool, down a human and bot together. Only one returns. Check elimination, pooled and per-player rules with a bot that resumes its job. Test simultaneous wipes, reconnect, rematch and inventory handling.
- Compare side-swapped baseline and variant series and inspect a failed start in the result view. Keep live evidence distinct from unit or integration tests. Run only one live lab session at a time.
