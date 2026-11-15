# MCB roadmap

**Phase 2 delivered locally (November 15, 2026): lab frontend, catalog, queue,
and live status. Durable comparison is next.** The shared run contract is documented in
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
[IMPLEMENTED.md](IMPLEMENTED.md) for verification and scope. The next work is
ordered by dependency. Preserve single-world/body ownership and
qualification gates throughout. A UI, mock, or local test does not qualify
Minecraft behavior.

| Phase | Track | Status and next deliverable | Authority / evidence |
|---|---|---|---|
| 3 | Durable comparison | **Next.** Persist normalized run and per-case summaries before pruning; compare only matching tasks and show configuration/provenance differences before metric deltas. Preserve partial and killed runs and distinguish zero from missing. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md) |
| 4 | One-bot companion and MCP | **Planned.** Route web chat, in-game whispers, `/mcb`, and local-stdio MCP through validated jobs. Start with `say`, `status`, `goto`, `gather`, `build`, and deterministic `stop`; apply the same authority checks and protected areas at every ingress. Model work must not block movement. | [LAB_COMPANION_PLAN_2026-11-15.md](LAB_COMPANION_PLAN_2026-11-15.md), [DECISIONS.md](DECISIONS.md) |
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
2. Deliver the lab track in the numbered order above. Keep the server-side Python
   API, exclusive arena ownership, durable sessions, and deterministic stop path.
3. At every phase, record focused verification, remaining live checks, and any
   source or configuration hashes. Preserve failed attempts and don't merge
   denominators across different tasks or configurations.
4. Update [IMPLEMENTED.md](IMPLEMENTED.md) only for behavior supported by current
   code and cited proof; update this roadmap when a deliverable changes status.
5. Keep the original specification
   [Minecraft_AI_Technical_Handoff.md](../Minecraft_AI_Technical_Handoff.md)
   unchanged.
