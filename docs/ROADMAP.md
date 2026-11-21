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
| B2a | House-large speed | **In progress (Nov 20, branch fix/build-travel).** House-speed fixes merged (PR #4; 1227/1227 in 59:15). Build-travel fixes (see IMPLEMENTED "Build travel"): server-equivalent placement predicate, cover-ranked stances, click-to-state stances, plan-set deltas, cost-ranked scaffold reclaim only when short. Run 20261120T113050800552Z (adb77e1, 59:32, 1225/1230): structure 28:16 → 20:07, but details rose to 24:27 because every dirt pillar of two or more blocks was refused by Python's pre-check (733 silent refusals, a 311 s up-and-down on a scaffolding column). Fixed: `prepare_pillar` follows StepMotor's exit contract; refusals are logged as `terrain_step_refused`. Run 20261120T141244368734Z (a55dfa1, `--inspect`): 1227/1227, job 51:28 (was 58:46); details 1467 → 442 s, structure 1207 → 1725 s. Since then (not yet live; the Java needs build-mod + mod-install + Q2): scaffold bridges straight/L/U ([BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md)); a tower top needs room for the head (it towered into a solid ceiling under a hopper, bookshelf in hand, instead of taking the stairs); glass goes in after the details it is the way to (the balcony panes went in from a tree after the window closed on the flower pots); in-game markers toggle (`run-build.py --show-markers`, `<run>/MARKERS`); scaffold challenges for the bot or a person (`check-scaffold-challenges.py`, battery `scaffold-challenges`). After the Gemini review of 5bb4812: held glazing no longer holds the layer, column or structure rule (a detail a layer up was blocked by the pane's layer and the pane went first); the bridge courses are roofed and deeper than their scaffolding, pits are walled in laid stone, the kit is scaffolding only, every changed course cell fails an attempt (lab kit `probe`), the kit's `home` keeps restores whole, human mode always stops and restores; markers resend a clear made while hidden; a dropped bridge waits for the fall and collects its scaffolding; a dirt tower needs the last jump's cell clear (as `RoutePlanner.pillar`); the Java planner no longer plans a solid place with no solid block held (`EditPolicy.solidSupply`). Installed and run (e029b74): Q2 fast 10/10. Challenges, first live run 3/7: the bridge roof at y=3 was feet+3 of the pad, so the bot never left it (roof now y=4). With that, 6/7, each bridge at its shortest (6, 5, 6). After the second Gemini review: held panes no longer trigger a fetch; a bridge course passes only with the bridge standing (read back: a person could sprint-jump to the bridge-l island or jump bridge-u's last gap); the kit's clear empties the start cells too; bodiless waits (person, Baritone) send no heartbeat (each was rejected); scaffold acts go through `can_place_here` before a direct click; the place reserve comes out of solid blocks only. **Open, measured in the challenges:** ~~`descend` needs scaffolding dropped down a shaft to stack from the floor; climb and reach-out each hit the 4000-node cap before reaching.~~ **Fixed (Nov 21):** replaced `RoutePlanner`'s 2D column-scan `riseNeed`/`riseInit`/`gapInit` with a lazy 3D backward Dijkstra `riseField` (D-c) and added `scaffold_drop` (5-block overhang crane + `DOWN`-face drop stacking from the shaft floor, `ChainMap(fresh, known)` validation/confirmation for cells below the ±4 refresh box, and `RouteMotor` `CLIMB_CENTRE` rim clearance before sneaking into a column); live challenges **7/7 PASS** (`20261121T152315861130Z`, polished `descend` `20261121T152817456060Z`: 15 scaffolding, 17.8 s, 0 damage, `search cap: NONE` across all 7). Run 20261121T154048852669Z (7981436, `--inspect`): blocked 1225/1228 (1 wrong, 2 missing), job 44:16 (was 56–60 min), 11 watchdog stalls (was 20–30). Two root causes, fixed offline (not yet live): cobblestone ran out with two foundation cells left, the zone's lowest layer moved up a floor and the planks sealed them in (a structure cell short of its item now holds its column, not its layer); and nearest-first cleanup broke a stairwell step with footholds upstairs left, then re-placed it walking up, twice (planner-placed steps now come down after everything above them). Next: house-large `--inspect` on that fix (run 20261121T171016886377Z). That run (3db6ff0, `--inspect`, 43 min: site 7:20, structure 23:05, refill 2:06, details 5:23) ended `need_materials` at 1215/1242 with no cleanup. At 28 min the refill had stored into a shulker box placed on the crafting table beside the supply. That box blocked the view from the one stance the bot used to open the chest at (-27,64,56). Every later withdraw from that chest failed `no_line_of_sight`, so the last birch doors, birch trapdoors and campfire never came out, and the run stopped with its scaffolding standing. Fixed: container opens go through `reach_and_do`, so a blocked view or reach tries other stances, and a build that stops short of materials still runs its cleanup. Build runs now show the in-game markers by default (`--no-show-markers` to start them off); they had been off in every house run because the flag was never passed. Run 20261120T175636584118Z (49defd6, `--inspect`, 3371 s, blocked: 1223 correct, 4 wrong, 4 missing) exposed seven root causes now fixed and verified offline against the run's final map: upfront prop planning for click-states with no existing face (mutually facing hoppers), click-aware shortest sound tower height + rejecting unreachable roof lifts (hanging chains, wrong-side-of-wall hopper towers), connected-structure scaffolding reclaim (`sc.roots`/`sc.survivors` + adopting step-placed `stray_footholds`), waiting `COLLAPSE_TICKS` + `collect_drops` under `no_placing` on failed tower climbs / reclaims / cleanup, re-adopting cleanup-placed footholds every round (which caused 3 of the 4 wrong cells in `final_diff`), keeping sealed interior cavities (`bp.sealed_air`) out of `build_air` (attic dirt `(-12, 75, 63)`), and requiring `above` support for hanging lanterns. Next: rerun house-large `--inspect` once current inspection finishes. Open, measured in that run: stances ranked by straight-line distance send the bot round walls (147 slow `goto_stance`, about 1318 s; proposal: rank targets and stances by walking distance over the sensed map); cleanup ordered by `(-y, x, z)` over the whole house (326.7 s; proposal: sweep by zone and proximity); 66 near-goal search-cap hits, 59 of them `pillar_to` (`riseEstimate` and unreachable stance candidates); 29 `goal_no_progress` (261 s); the same balcony→tree `follow_gap` retried 18 times in 26 s; 1866 prefetches started vs 309 adopted; site prep 463 s (34 water leaks); slow scaffold descents (17 idle gaps of 5–8 s); leftover temporaries inside (dirt, scaffolding) crowd inside stances; Python build decisions are not logged (no `build_note`); the supply gives one stack of scaffolding (reclaim churn). Not a course yet: a 13-wide pit crossed by dropping scaffolding onto its floor to stack a middle tower (the walk down and up cannot be ruled out; the planner does not drop columns). Target 10–15 min. | [IMPLEMENTED.md](IMPLEMENTED.md) |
| B3 | Underground iron | Fix `descent_failed:walk_failed`; mine at the knowledge-pack ore depth instead of surface ring walks; strip legs that reach ore. | [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md) |
| 4 | One-bot companion and MCP (slim, pulled forward) | **In progress (feat/mcb-command merged; not yet used live).** In-game `/mcb status\|goto\|gather\|build\|stop` with an owner allowlist (`config/companion.json`: empty lists let every player in; ships with the operator as owner) and a deterministic stop; replies need `mclab companion-service` running, `goto` is not wired yet, and `status` reads dashboard sessions, not run-build.py runs; a "why are you stalled?" answer from real state. MCP and web chat reuse the same job path later. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md), [DECISIONS.md](DECISIONS.md) |
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
