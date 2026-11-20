# MCB roadmap

**Phase 2 lab UI delivered (Nov 15, 2026). As of Nov 16 the order puts bot capability first: baseline battery, build reach at height, underground iron, then in-game commands.** The shared run contract is documented in
[RUN_CONFIG.md](RUN_CONFIG.md), with completed implementation and verification
in [IMPLEMENTED.md](IMPLEMENTED.md).

This is the short list of upcoming work. Product and architecture decisions live
in [DECISIONS.md](DECISIONS.md) and the authoritative
[navigation direction](NAVIGATION_DIRECTION_2026-10-28.md). Implementation
evidence and limits are summarized in [IMPLEMENTED.md](IMPLEMENTED.md). The
[index](INDEX.md) routes readers to plans, proof, reference material, and history.
The original 998-line roadmap snapshot is preserved verbatim in
[ROADMAP_HISTORY_2026-11-15.md](ROADMAP_HISTORY_2026-11-15.md); it is archive
material, not current work ordering.

## Work order

Phase 2 passed Python, UI, desktop and narrow-screen checks; see
[IMPLEMENTED.md](IMPLEMENTED.md) for verification and scope. The Nov 16, 2026
review found the lab tooling ahead of the bot, so bot capability now comes first
and durable comparison moves down. Preserve single-world/body ownership and
qualification gates throughout. A UI, mock, or local test does not qualify
Minecraft behavior.

| Phase | Track | Status and next deliverable | Authority / evidence |
|---|---|---|---|
| B1 | Baseline battery | **In progress (Nov 16).** Run one full battery (`scripts/run-battery.py`) on the current commit before changing behavior; the smelt_wait livelock fix (d4710b3) and recursion fix (ad41dc0) are unproven live. Output: `results/battery/20261116-review-baseline`. | [IMPLEMENTED.md](IMPLEMENTED.md) (record the result there once run) |
| B2 | Build reach at height | **Next.** Fix `no_stance` on upper floors and roofs: stand on placed blocks and the template's own floors/stairs; put scaffold columns outside the template. Rerun the 1,227-block large house until it finishes (best so far 666/1227, run 20261110T095528994372Z) and the small house without staging (stalls at 57/84). | [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md) |
| B2a | House-large speed | **In progress (Nov 20, branch fix/build-travel).** House-speed fixes merged (PR #4; 1227/1227 in 59:15). Build-travel fixes (see IMPLEMENTED "Build travel"): server-equivalent placement predicate, cover-ranked stances, click-to-state stances, plan-set deltas, cost-ranked scaffold reclaim only when short. Run 20261120T113050800552Z (adb77e1, 59:32, 1225/1230): structure 28:16 → 20:07, but details rose to 24:27 because every dirt pillar of two or more blocks was refused by Python's pre-check (733 silent refusals, a 311 s up-and-down on a scaffolding column). Fixed: `prepare_pillar` follows StepMotor's exit contract; refusals are logged as `terrain_step_refused`. Run 20261120T141244368734Z (a55dfa1, `--inspect`): 1227/1227, job 51:28 (was 58:46); details 1467 → 442 s, structure 1207 → 1725 s. Since then (not yet live; the Java needs build-mod + mod-install + Q2): scaffold bridges straight/L/U ([BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md)); a tower top needs room for the head (it towered into a solid ceiling under a hopper, bookshelf in hand, instead of taking the stairs); glass goes in after the details it is the way to (the balcony panes went in from a tree after the window closed on the flower pots); in-game markers toggle (`run-build.py --show-markers`, `<run>/MARKERS`); scaffold challenges for the bot or a person (`check-scaffold-challenges.py`, battery `scaffold-challenges`). After the Gemini review of 5bb4812: held glazing no longer holds the layer, column or structure rule (a detail a layer up was blocked by the pane's layer and the pane went first); the bridge courses are roofed and deeper than their scaffolding, pits are walled in laid stone, the kit is scaffolding only, every changed course cell fails an attempt (lab kit `probe`), the kit's `home` keeps restores whole, human mode always stops and restores; markers resend a clear made while hidden; a dropped bridge waits for the fall and collects its scaffolding; a dirt tower needs the last jump's cell clear (as `RoutePlanner.pillar`); the Java planner no longer plans a solid place with no solid block held (`EditPolicy.solidSupply`). Installed and run (e029b74): Q2 fast 10/10. Challenges, first live run 3/7: the bridge roof at y=3 was feet+3 of the pad, so the bot never left it (roof now y=4). With that, 6/7, each bridge at its shortest (6, 5, 6). After the second Gemini review: held panes no longer trigger a fetch; a bridge course passes only with the bridge standing (read back: a person could sprint-jump to the bridge-l island or jump bridge-u's last gap); the kit's clear empties the start cells too; bodiless waits (person, Baritone) send no heartbeat (each was rejected); scaffold acts go through `can_place_here` before a direct click; the place reserve comes out of solid blocks only. **Open, measured in the challenges:** `descend` needs scaffolding dropped down a shaft to stack from the floor (no planner edit for it); climb and reach-out each hit the 4000-node cap before reaching (the `pillar_to` cap issue below, now reproducible in 5 s). Next: house-large `--inspect`. Open, measured in that run: stances ranked by straight-line distance send the bot round walls (147 slow `goto_stance`, about 1318 s; proposal: rank targets and stances by walking distance over the sensed map); cleanup ordered by `(-y, x, z)` over the whole house (326.7 s; proposal: sweep by zone and proximity); 66 near-goal search-cap hits, 59 of them `pillar_to` (`riseEstimate` and unreachable stance candidates); 29 `goal_no_progress` (261 s); the same balcony→tree `follow_gap` retried 18 times in 26 s; 1866 prefetches started vs 309 adopted; site prep 463 s (34 water leaks); slow scaffold descents (17 idle gaps of 5–8 s); leftover temporaries inside (dirt, scaffolding) crowd inside stances; Python build decisions are not logged (no `build_note`); the supply gives one stack of scaffolding (reclaim churn). Not a course yet: a 13-wide pit crossed by dropping scaffolding onto its floor to stack a middle tower (the walk down and up cannot be ruled out; the planner does not drop columns). Target 10–15 min. | [IMPLEMENTED.md](IMPLEMENTED.md) |
| B3 | Underground iron | Fix `descent_failed:walk_failed`; mine at the knowledge-pack ore depth instead of surface ring walks; strip legs that reach ore. | [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md) |
| 4 | One-bot companion and MCP (slim, pulled forward) | **In progress (branch feat/mcb-command).** In-game `/mcb status\|goto\|gather\|build\|stop` with an owner allowlist and a deterministic stop; a "why are you stalled?" answer from real state. MCP and web chat reuse the same job path later. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md), [DECISIONS.md](DECISIONS.md) |
| B4 | Project layer | **Planned.** Saved project record (site, template, materials remaining, progress); saved chest contents in the existing place graph; base chest cluster with capacity; resource-sightings table; resume after restart. | [IMPLEMENTED.md](IMPLEMENTED.md) (current place graph and save state) |
| B5 | Night shelter and death recovery | **Planned.** Required before any multi-hour job. | None yet; needs a plan before work starts. |
| B6 | Lab UI | **Planned.** Readable live view (progress, stage, actions, log instead of JSON dumps); port /legacy features (history, compare, gather/build forms, schematic convert, delete) so /legacy can retire; position/inventory/map panel (needs a backend position feed). | [LAB_UI.md](LAB_UI.md) |
| 3 | Durable comparison | **Deferred** until enough live runs are worth comparing. Persist normalized run and per-case summaries before pruning; compare only matching tasks and show configuration/provenance differences before metric deltas. Preserve partial and killed runs and distinguish zero from missing. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| 5 | Web voice | **Later.** Add browser push-to-talk, local transcription, and speech playback through the same chat and intent path, with cancellation and text fallback. Select engines after measuring latency. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| 6 | Skins and multiple bots | **Later, after one-bot jobs.** Investigate skin options first. Then make runtime, bridge, sessions, permissions, memory, and jobs body-ID aware; verify isolation before concurrent work or a coordinator role. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |

### Parallel existing tracks

- **Navigation:** follow the phase order and acceptance rules in
  [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).
  Recheck current source hashes, gate records, and live state before resuming any
  dated item in the archived roadmap. Never infer current qualification from old
  cohorts or from a code change.
- **Gathering and building:** use
  [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md)
  and its current source-bound run records. Keep construction and gather claims
  separate from the initial C1 qualification profile.
- **Models:** G4/model experiments remain a separate track. Existing screening,
  provider probes, and tandem harness evidence do not amount to a learned-policy
  or full G4 qualification. See [IMPLEMENTED.md](IMPLEMENTED.md) and
  [INDEX.md](INDEX.md#model-work).

## Delivery rules

1. Build the frontend on the verified phase 1 config contract; keep runner
   behavior and source-bound qualification separate from UI work.
2. Deliver work in the table order above (bot items before deferred lab phases). Keep the server-side Python
   API, exclusive arena ownership, durable sessions, and deterministic stop path.
3. At every phase, record focused verification, remaining live checks, and any
   source or configuration hashes. Preserve failed attempts and don't merge
   denominators across different tasks or configurations.
4. Update [IMPLEMENTED.md](IMPLEMENTED.md) only for behavior supported by current
   code and cited proof; update this roadmap when a deliverable changes status.
5. Keep the original specification
   [Minecraft_AI_Technical_Handoff.md](../Minecraft_AI_Technical_Handoff.md)
   unchanged.
