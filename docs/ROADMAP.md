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
| B2a | House-large speed | **Implemented, not run live (Nov 19, branch fix/house-speed).** Fixes from the 48-min run 20261119T133618892664Z: RouteMotor accepts collision-shape stance heights (trapdoor trap), route refusals are remembered as failed edges with a stuck stop, `on_pass` skips the bot's own path, content-addressed plan sets (`planner_set`) shrink plan payloads ~9x/~30x, prefetch re-plans only when cells ahead change, no centring on unlevel/refused stances, scaffolding centre-only arrival, per-candidate goto budget and failed-stance memory, polled drop wait, reach-filtered pond fills, local 8-block build zones with deferred scaffold teardown. The first live rerun (20261119T202232979013Z) fell into a pond and stalled on a vine climb; fixed with the `wading-trapped` planner fix, the climb-centre and wet-climb motor fixes and session edge-failure memory (bans lifted when the edge's blocks change), plus the `vine-escape` battery check and `mclab qual`. Next: fast qualification with the new mod, rerun house-large with `--inspect`; target 10–15 min. More battery scenarios still wanted (water, caves, edits near hazards). | [IMPLEMENTED.md](IMPLEMENTED.md) |
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
