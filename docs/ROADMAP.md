# MCB roadmap and document index

**Java migration handoff (Nov 3):** implemented, independently reviewed and
installed; live action smoke passed 14/14. Remaining qualification and paired
baselines are running in the background. See
[current migration status](JAVA_MIGRATION_STATUS_2026-11-03.md) for evidence,
limitations, and resume paths. No final live qualification is claimed yet.

**Start here.** This file is the single list of what is done, what is next, and
where each plan or ledger lives. It holds no decisions of its own: decisions and
the phase plan are in [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md)
(authoritative; it wins any conflict). Results live in the ledgers listed below.
Update this file whenever an item here is finished or a new one is found.

Last updated: November 6, 2026 (companion track: "Open after Nov 6" below — the
follow_step 413 and the plan-only stance probe are fixed and live-verified; what is
left of the gather stalls is frontier optimism; iron baseline regression attributed
— not the climb change; dashboard gate sessions landed).
Earlier: November 3 (companion track: M5 state; Java migration baselines).
Earlier: November 1 (cohorts 16–29 and the frozen calm comparison on
`main`). Previously qualified mod: `c414e408…` (the Nov 3 migration build; the seven
regression stages passed for it on Nov 3, `results/awareness/20261103T190042815691Z-regression`;
no ledger promotion recorded). Later body changes require source-bound requalification.
Cohorts 27–28 ran on `2eed1251`, cohort 29 on `0129b057`.

## Where things stand

| Area | State | Detail |
|---|---|---|
| Mod split, D9 (step A) | Done (development evidence) | Direction doc §0 step A |
| In-mod route follower, D10 (step B) | Done for development. Its mod is requalified (item 11) and passed the frozen calm comparison (item 12); no separate step B sign-off is recorded | [STEP_B_PROGRESS](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN](STEP_B_RERUN_2026-10-29.md) |
| Planner core, D11 (step C) | Java-only segmented production implementation is ready for independent review and live requalification. Historical cohort 28 was 10/10; cohort 31's opt-in Java core was 3% faster than the Python planner in total | [JAVA_MIGRATION_INVENTORY](JAVA_MIGRATION_INVENTORY.md), [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md) |
| Baritone parity, D12 | Weights and moves reimplemented | [BARITONE_WEIGHTS_AND_MOVES](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md), `config/weights/baritone-v1.20.0.json` |
| Awareness parity, D8 | `sections` preset with delivery fixed (Oct 31) | [AWARENESS_MODES](AWARENESS_MODES.md) |
| Swimming (§5 of the direction doc) | Planner and body done; live water and ladder fixtures 10/10 each (item 16) | Direction doc §5 status lines |
| Qualification | Historical for mod `68b3e8f6` (Nov 1); not current for later body changes | [NAVIGATION_QUALIFICATION.json](NAVIGATION_QUALIFICATION.json), [gates.json](gates.json) |
| Gates | G0–G3, G5 passed; G4 in progress; G6–G8 not run | [gates.json](gates.json) |

Latest numbers against Baritone (ticks, calm, seed 1337, Baritone run
`20261029T205639210651Z`: R1 581, R2 531, R3 935, R4 801, R5 848):

- Cohort 14 (`20261031T003318288661Z`): R1 1062/1023, R2 947/fail,
  R3 952/1024, R4 913/1034, R5 1748/2180.
- Cohort 20 (`20261031T035652070241Z`): R1 609/619, R2 558/559, R3 916/937,
  R4 831/828, R5 2896/3052. 10/10; R5 swam through a lake Baritone walks around.
- Cohort 22 (`20261031T144601883497Z`): R1 602/607, R2 558/558, R3 807/824,
  R4 829/838, R5 1430/1334. 10/10.
- Cohort 24 (`20261031T151251308104Z`, mod `51e5089f`): R1 618/612, R2
  567/578, R3 701/700, R4 866/865, R5 921/909. 10/10; medians 1.06×, 1.08×,
  0.75×, 1.08×, 1.08×.
- Cohort 25 (`20261031T200524257733Z`, mod `51e5089f`): R1 611/607, R2
  563/563, R3 703/772, R4 862/839, R5 899/902. 10/10; medians 1.05×, 1.06×,
  0.79×, 1.06×, 1.06×.
- Cohort 26 (`20261031T214556225885Z`, mod `2eed1251`): R1 608/606, R2 563/564,
  R3 732/735, R4 836/830, R5 924/904. 10/10, no motor stops.
- **Cohort 28 (`20261101T005033322463Z`, current code): R1 623/619, R2 576/568,
  R3 736/738, R4 841/848, R5 905/940.** 10/10; medians 1.07×, 1.08×, 0.79×, 1.05×,
  1.09×. Cohorts 21–27 and the fixes between are in the ledger.

## Oct 31 findings that change earlier assumptions

Details and evidence: [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md), "Oct 31".

1. **Section batches were lost before the controller started reading.** The body
   sends each map section once. Batches produced before Python's first `/policy`
   read (40–70 ticks after the course starts) were never delivered, and they are
   the ones covering the start area. *Fixed:* unread sections are carried into the
   next batch (mod `fdc9e533`). Earlier analyses that read the body's
   `policy_sample` log as "what MCB knew" overstated its knowledge.
2. **Reading `/policy` consumes terrain.** A served batch counts as delivered.
   Never poll `/policy` and discard the result; use `/state` to wait for a state
   (`natural.py` fixed).
3. **R1's gap was missing data, not planner throughput.** The Oct 30–31 reading
   "the binding limit is planner throughput" is withdrawn for R1. Throughput is
   still low (about 3,000 nodes/s live, against Baritone's 25–30k nodes per
   decision) and still matters for partial routes and pit escapes.
4. **Faster awareness is not better.** Ingesting every 2 ticks took Python time
   from the planner. The `-fast` awareness profiles are rejected; use
   `awareness-sections-64.json`.
5. **The saved `map.sqlite3` files are accurate.** Missing sections were never
   delivered, not lost on save.
6. **One planner worker lane serves both search and prefetch.** Cancelling must
   name its job (`cancel(token)`), and a superseded search resubmits itself.
   Before this, searches could hang "pending" until the stall cutoff.

## Oct 31 (later) findings, cohorts 16–20

Details: [STEP_C_PROGRESS](STEP_C_PROGRESS_2026-10-29.md), cohorts 16–20.

1. **The delivery fix carried over to every route** (cohort 16): R1, R2 and R4
   came within 4–14% of Baritone with no other change.
2. **`replay_delivered.py` under-reported the map after mod `fdc9e533`.** Carried
   sections are logged only when scanned; the script kept only read batches. Fixed
   (`OLD_MOD=1` for older attempts). One diagnosis ("drops through unknown space")
   was wrong because of it.
3. **Partial-route ping-pong was the common failure** (cohort 14 R2, cohort 17
   R5): partials from neighbouring starts end in opposite directions. A new
   partial now replaces the current plan's tail only if it ends ≥1 block closer to
   the goal; favouring holds the whole plan.
4. Fixed body/planner mismatches: a sunk start under a surface stance; a lake drop
   off a ledge overhanging the water (`swimSafe`); a lake bed more than 8 blocks
   under its surface; identical splices that reset the motor's step deadline.
5. The controller is built before the course starts: the first `/policy` read is
   on the start tick, and following starts 7–9 ticks in (was 11–41).

## Next work, in order

Each item names where its detail or evidence lives. Numbers are priority, not
dependencies, except where noted.

**Open after Nov 6 (companion track, M5), in the order to do them:**
(1) frontier optimism — the stall class left after the plan-only stance probe (now
in, see closed (e)): the planner answers *routed* for ways through chunks nothing
has sensed, and the walk pays for that. At the hill site the explore rings
(`explore()` → `walk_to`, so no stance probe applies) ping-pong the known edge in
2-3-node segments with 300-400 tick freezes between them
(`20261106T215445111262Z`, ticks 24.5k-27.2k: a 16-block ring walk needs >1,000
ticks, so StuckGuard ends 13 of them); at the pond 6 of 61 candidates were walked on
an optimistic routed verdict and stuck (`20261106T222704810114Z`);
(2) hole-escape quality: digging down to stone/iron and walking back fails about 1 in
3 attempts at the seed 20261026 site, with climbing on or off — three `--no-climb`
reps on Nov 6: `20261106T174543920887Z` failed after arriving at (43, 51, −81) (14
stuck gotos to surface stances, then `Craft…no_stance`), `20261106T180108898761Z` and
`20261106T181234719671Z` passed. The climb change is kept (scaffolding and ladders
need `allow_climb`); it was not the 9/9 → 7/9 cause. This also threatens the
large-house iron phase;
(3) promote the Nov 3 seven-stage regression for `c414e408…` into the ledgers (the
finalize helper is not present under `runtime/`);
(4) station placement economics: `PlaceStation` failures with `no_support_face` end
the task without another cell being tried — `place_free` re-picks the same
`free_spot` on every retry and `no_support_face` is not in `RETRY_REASONS`
(`20261106T182245230212Z`, hill site). Fix: add it to the stance-retry reasons or
rotate the `free_spot` offsets.

**Closed from Nov 3:** (a) the large-house driver crash — root-caused Nov 6 to the
`follow_step` protection echo crossing the 16 KB POST limit and fixed (`follow_step`
now carries protection beside the plan's cells only, `native_driver.STEP_PROTECT_RADIUS`
= 2; live: 65,361 B → 756 B per request, run `20261106T185239143471Z`, starter
completed 16 tasks);
(b) the seed 20261026 climb suspicion — disproved, see (2); (c) body requalification
for scored runs — already evidenced for the current install `c414e408…` by
`20261103T190042815691Z-regression` (all seven stages), so scored navigation may
proceed on this build; (d) dashboard gate sessions: `body-qualify` 39/39 and `body
protocol-check` 24/24 passed live through it today (sessions
`20261106T170523-2dfe718d`, `20261106T165607-5c17fa92`); (e) candidate economics at
known-unreachable stances — the plan-only probe in `goto_stance` (a motor-parked
verdict drive): the pond rerun `20261106T222704810114Z` rejected 55 of 61 candidates
with zero walking, where the same site had burned its whole 120 s watchdog on 12
stuck walks before. What remains is frontier optimism, open item (1).

**Open after Nov 1, in the order to do them:** (1) item 8: stage D and continuous
re-planning done (cohorts 30-31); review and qualify the Nov 3 Java-only migration;
(2) night diagnostics for item 12; (3) Marc's call on night survival (item 13);
(4) the paired ~10% gap to Baritone (items 8, 9); (5) a live multi-edit chain
(item 10).

**Confirm and close gaps**

1. Falling blocks over a cut: settle cost added (cohort 22); watch for repeats.
2. **Route choice from partial plans** varies rep to rep (R3: ~30 ticks between
   the x=-77 and x=-84 lines). Low priority; it falls under partial-route quality
   (item 9). Done: the R3 grounded swept-unsafe stop was a bend-sweep heading bug,
   fixed in cohort 26.
3. Done (Oct 31): `stall_seconds = 0` also disables `goal_no_progress`
   (`controller.goal_progress_ticks = None`; `test_native_driver.py`).
4. Closed Nov 1 (water exit onto a +1 bank): not possible in vanilla, so nothing
   to build. `LivingEntity.jumpOutOfFluid` (26.3 bytecode) sets vertical speed 0.3
   only while in water, against a wall, with the box 0.6 higher free; the boost
   ends when the feet leave the water. Cohort 19 R5 peaked at 63.92 about 350 times
   under a bank top at 64.00. Baritone's ascend from water is our level exit; it
   has no +2. The planner has not offered this move since cohort 21.
5. Done Nov 1 (more reps and more seeds): covered by the frozen calm comparison
   (item 12), 3 seeds × 10 routes × 3 reps, MCB 90/90.

**Speed**

6. Done Oct 31 (planner speed on the grid view). Separated on the cohort 25 R3
   map, an 8,202-node search from the start, best of 7: grid with swimming 3,836
   nodes/s, grid without 4,475, plain dict with 4,512, dict without 4,563. Both
   costs were 10–20%, not 3×. Fixed: `GridView.get` converts whole-number float
   heights inline (a third of lookups went through `_locate`), and swim checks
   rule out land with one lookup (`surface_water`, `_landings`). Now 4,184 / 5,244
   (grid with / without swimming), +9% / +17%; replayed routes unchanged. What is
   left is per-node interpreter cost: items 7 and 8.
7. Superseded Nov 1 by item 8 (search outside the interpreter lock): the Java
   core runs in the body, outside Python's lock, at 5–10× Python's offline speed;
   a separate Python process would copy map snapshots for about 2×.
8. **Compiled planner core** (Java in the body). Started Oct 31 at Marc's request.
   Stage A done: `mcb-body/.../RoutePlanner.java` is the land subset of
   `route_search.py` (tick costs, Baritone search and diagonals, shapes).
   Stage B done: `scripts/analysis/planner_parity.py` exports a delivered map and
   runs both. Cohort 25 R3 start, 8,000 cap: same 6,010 expansions, route and
   cost (608.08); Java 125,608 nodes/s against Python's 12,603 (10×). Cohort 27 R5
   start at 1,500 / 3,000 / exhaustion: same partial routes, Java 5–8× faster.
   Stage C started: unknown-space exits (D3 frontier, exit pricing, buried
   dig-out rise, walk-rate partial estimate) match Python on both maps
   (`UNKNOWN=1`; R3 8,000 cap: same route and cost 511.61, 84k against 13k
   nodes/s). Stage C done Nov 1: swimming, water landings, ladders and gaps
   match Python exactly on the R3 and R5 maps (9–13× faster). Stage E done as
   opt-in: body `plan_route` op (10 ms per tick), `BodySearch`, `--body-planner`.
   Cohort 29 with it: 10/10 but 15% slower (R5 skips a 2-block cut it cannot
   price; R2/R3 7–9% slower, likely because the body reads the live world in
   range while Python plans on delivered cells). Stage D done Nov 1: every edit
   kind in Java (dig/place steps, dig down, descend with breaks, pillar), same
   routes, costs and edit plans as Python at all 20 frozen-comparison edit
   decisions and on built maps; the edit-edge fallback for that supported Java
   subset was removed. Unsupported interaction, survival and other policies
   still used Python at that time. Cohort 30
   (`20261101T135049338315Z`, mod `68b3e8f6`): 10/10, 7,488 ticks against
   Python's 7,394; R5 digs the cut again, only R3 is still ~8% slower. R3's
   cause was early commitment, not the live-world reader: plans came back at
   once and each piece was locked in ~30 blocks ahead on the map of that moment.
   Fixed with continuous re-planning from 6 nodes ahead whenever the map changes
   (body planner only; the driven route is favoured, so it switches only to a
   clearly cheaper way). Cohort 31 (`20261101T142832710853Z`, R2 rerun
   `20261101T143807855604Z`): 9/9 valid (one harness `invalid`), 7,182 ticks,
   3% under Python; R3 653/649. The Nov 3 migration makes the Java planner the
   production segmented path for all supported policies and retains Python
   search only as an offline reference. Candidate live qualification remains open.
   A Java search runs outside Python's interpreter lock, so it also answers item 7.
9. **Partial-route quality.** ~1,000-node partials can still lead into pits and
   lakes (R5). Commitment to a plan's tail is done (cohort 18). Oct 31: the first
   search of an attempt uses the full 3,000-node floor (cohort 27 R5 rep 2 went
   into the lake from a 1,500-node start partial; 3,000 picks the ridge in
   replay). Cohort 28: 10/10, no lake, R1/R2 5–15 ticks slower (accepted).
10. **Several edits per plan.** Oct 31: `_begin_route_edit` queues the whole run
   of consecutive edit edges from one search (each re-checked on fresh geometry
   before it starts), instead of one search per edit. Unit test in
   `test_planner_core.py`; cohorts 27–28 had only single edits, so no live
   multi-edit chain yet.

**Scored comparison**

11. Done Nov 1 (requalify the mod): `results/awareness/20261101T020439115161Z-regression`
    passed all seven checks (awareness 15/15, 20 initial resets, 29 adversarial,
    C2–C5 20 resets each) on mod `a94dcdef`; promoted by `runtime/finalize-movement.py`
    to `docs/MOVEMENT_PARITY_PROOF.json`, G2 and `NAVIGATION_QUALIFICATION.json`.
    Rerun Nov 1 for mod `68b3e8f6` (Java edit edges):
    `results/awareness/20261101T144040539892Z-regression` passed all seven checks
    (same counts) and was promoted the same way.
    Any later body change needs this rerun.
12. **Frozen comparison, Phase 7:** 10 routes × ≥3 seeds × 3 reps; MCB ≥ 27/30
    and median ≤ 2× Baritone at matched awareness, plus night diagnostics.
    **Calm part met Nov 1** (`scripts/frozen-comparison.ps1`, commit `3cf9426`,
    development runs, Baritone with parkour, view distance 4, awareness-sections-64):
    | Seed | MCB run | MCB ok | MCB median | Baritone run | Baritone ok | Baritone median | Ratio |
    |---|---|---|---|---|---|---|---|
    | 1337 | `20261101T023131287363Z` | 30/30 | 717.5 | `20261101T030328345130Z` | 30/30 | 773.5 | 0.93 |
    | 9001 | `20261101T034420070608Z` | 30/30 | 670 | `20261101T041609691919Z` | 29/30 | 747 | 0.90 |
    | 42424242 | `20261101T045505457355Z` | 30/30 | 674.5 | `20261101T052517944411Z` | 30/30 | 594.5 | 1.13 |
    Baritone's miss (9001 R9 rep 3) is `invalid`, a harness error ("Stop the lab
    server before replacing runtime/course files"), not a navigation failure.
    Paired by route and rep, Baritone is faster in 77 of 89 attempts both
    completed (total ticks 60,057 vs MCB 66,190, about 10% faster); the per-seed
    medians favour MCB on two seeds only because route lengths differ.
    MCB broke 12 blocks in total, Baritone 71. MCB took 1 damage on each 42424242 R8 attempt (3 total), no deaths.
    Still open: night diagnostics for this item, and the paired gap above (Baritone
    about 10% faster route by route); items 8 and 9 are the likely fixes.

**Features from the plan**

13. **Survival weights and Jev profiles, Phase 6.** Oct 31, code done:
    `TerrainPolicy` survival terms (Baritone mob avoidance `mob_coefficient` /
    `mob_radius`, `hazard_penalty` beside lava/fire); profiles in
    `config/profiles/` with `--cost-profile`; visible hostiles feed the search;
    `jev_contract.py` (situation, decision, validation, deterministic fallback,
    source recorded). No fall-damage gate: dry drops stop at 3 blocks, below
    vanilla's damage threshold. **Night acceptance run Nov 1 and not met.**
    `race` (`20261101T010410539420Z`): 6/10, 3 deaths, damage 94.2,
    exposure 3,736 hostile-ticks within 16. `safe` (`20261101T011522361531Z`):
    7/10, 3 deaths, damage 93.8, exposure 6,162 (one R5 attempt took 3,239 ticks).
    Every death was close-range: a creeper within 3 blocks, or a zombie villager
    and spider within 1.5, or three creepers and a husk within 2. Plan-time
    avoidance of mobs seen when the route was planned cannot escape an attacker
    already beside the body. A longer reactive flight was tried and rejected:
    `safe` with a 6-block sprinting retreat (creepers from 7 blocks;
    `20261101T015007016363Z`) went 6/10, 2 deaths, damage 118, exposure 7,240, two stalls:
    fleeing and re-planning kept the body among the mobs. The flight setting stays
    available (`follow_route` `flee`, profile `flee`) but no profile uses it; the
    reflex's hostile list now includes zombie villagers. **Open research:** night
    survival needs combat or shelter (direction doc "Later"), not route weights.
    Screening Jev waits for the model track. Needs Marc's decision (see open
    questions).
14. Done Oct 31 (interaction goals, Phase 3): `RouteSearch(interact_reach=4.5)`
    and `SegmentedController(interact_reach=...)` treat the goal as a block to
    reach with the eye (feet + 1.62, centre ray, every crossed cell known and
    clear). Acceptance fixtures in `tests/test_planner_core.py`: ledge table
    reached from below without climbing; never through a wall or unknown cell.
    Not yet exposed in the natural-run CLI (no route needs it).
15. Closed Oct 31 (movement leftovers), each on evidence:
    - 0.01 edging-bend margin: fixed by sweeping on the exact bearing (cohort 26,
      no motor stops in 10 attempts).
    - Causeway block cost: `placement_penalty` (4 per place) plus the escalating
      edit-chain cost (cohort 9) already price it; no long causeway since cohort 12.
    - Placement-aim stall from an off-centre stance: seen once (cohort 12a R2) and
      not in cohorts 13–26. Reopen with the attempt if it recurs.
    - Ladder facing: vanilla climbs any ladder while jump is held, and the motor
      holds jump; the wall side is blocked by the wall itself. No change.
    - Flowing-water push: native routes are cut before flowing water, so push
      cannot mislead the motor. Model it when a route needs to cross a current.
16. Done Nov 1 (live fixture cohorts): water `results/hills/20261101T014540110734Z` and ladder
    `results/hills/20261101T014706699506Z`, 10/10 each with every proof (mod `a94dcdef`). New
    ladder fixture in `DevelopmentHill`. The low-air proof also accepts a native
    surface swim (air never below the injected 90, refilled to at least 200).

Housekeeping (Nov 1): `main` is committed and pushed after each item. The `-fast`
awareness profiles stay: cohorts 15d–15f name them, and the ledger keeps every
cohort reproducible. `scripts/analysis/` now passes ruff (long lines and late
imports allowed there).

## Open questions for Marc

- Answered Oct 31: the frozen comparison runs Baritone with moves matched to
  MCB's (parkour on), which needs a new Baritone baseline; the compiled planner
  core (item 8) starts now; finished work is committed and pushed to `main`.
- Open Nov 1: night survival (item 13). Route weights and a reactive flight both
  failed the night acceptance; the choice is combat, shelter, or defer to "Later".
- Answered Nov 1: build M0–M4 of
  [BUILD_AND_GATHER_PLAN](BUILD_AND_GATHER_PLAN_2026-11-01.md) now, alongside
  follow and chat; M5–M6 later. Formats: [KNOWLEDGE_PACK_FORMAT](KNOWLEDGE_PACK_FORMAT.md).
  Defaults taken for §6 questions 2–4 (clear natural terrain only, anchor at the
  speaker's feet, no chests yet) until Marc says otherwise.
  Status Nov 1 (afternoon):
  - **M0 done live.** The vanilla pack `knowledge/26.3-f7dba04d8b5b` (1.7 MB,
    committed) took 0.42 s to dump. It has 2,042 recipes (361 of unhandled types,
    e.g. `crafting_dye`, `crafting_transmute`, `brewing`) and 0 drop errors. The
    first dump had the tier order scrambled (`Map.of` order); fixed.
  - **M1 done** on the real house (`tests/fixtures/schematics/house-154fc7cd.litematic`,
    1,227 blocks, matching the file).
  - **M2 Python logic done.** The body `place_state` op and the build loop are
    still open.
  - **M3 ops built.** Live crafting and furnace check still open (needs a lab
    fixture with items and stations).
  - **M4 planner done** on the real pack: order, fuel, surplus, tool upgrade and
    preference bugs fixed (wave 2); axe stripping, vines, moss and flowers added.
    The house plans in 76 tasks; only bed and bookshelf are unreachable (mob
    drops). The executor is not written yet (G0).
  - **M2 body ops and M3 done live** (wave 2): placement 46/46
    (`results/companion/20261101T172441585144Z`), crafting and furnace 8/8
    (`results/companion/20261101T172600197748Z`), via the lab `kit` fixture.
  - **M4 live, empty inventory → stone pickaxe, axe and shovel** (Nov 2, all
    runs no damage). Cohorts are listed by code version; none are pooled.
    - `8b20de6`: seed 20261026 from (68, 71, -64) 1/1 (`20261102T021647512245Z`).
    - `ad8102b` (shovel before stone, canopy drops skipped): seed 20261026 2/2
      (`024905580710Z`, `025142613958Z`); seed 42424242 from (32, 70, -112) 2/3
      (`025438690629Z`, `025904699306Z`; failed `030233328966Z`: one step
      confirmation deadline ended the whole session).
    - `b2ccf51` (a failed goto no longer ends the session; pillars with dirt):
      seed 42424242 1/1 (`031355605172Z`); seed 31415926 from (-48, 72, -52) 3/3
      (`031831471777Z`, `032102867061Z`, `032314503187Z`), 1,909–3,505 ticks.
    - Earlier failures: `20261102T005434368454Z` died in lava (stone beside a lava
      pool; now stone touching liquid or unknown cells is never mined);
      `20261102T020413428498Z` could not climb its 3-high staircase (the planner
      needs y+3 clear to step up; steps are now 4 high). Seed 1337 near spawn is
      wooded badlands (14–18 blocks of terracotta over stone), seed 9001 is ice:
      not used.
    - Method (Baritone-style): gather sessions plan with digging on
      (`start_session(dig=True)`, cohort 30 movement and terrain profile); the
      planner is offered buried stone tops (`dig_to_stone`), with the staircase
      (`gather_dig.py`) as fallback. Digging with any tool tier
      (`terrain_steps.hotbar`, `StepMotor.slot`; body mod changed).
  - **M4 done: empty inventory → iron pickaxe 9/9** (Nov 2, commit `90050ec`,
    seeds 42424242 / 31415926 / 20261026 × 3, no damage, 5,165–11,414 ticks;
    `20261102T053908375995Z` … `063009399753Z`). Each run also makes the stone
    tools. Failed iron cohorts, kept separate: `348704c` 4/5 (cave ratchet:
    reach now measured from the crafting table), `63ff2f2` 0/1 (drowned
    exploring across a lake: no swimming), `1e883e0` 0/1 (dug to iron 11 below
    and skipped it: one depth window), `7e764a9` 3/4 (suffocated under gravel:
    no cuts beside sand or gravel, body mod), `f269ff2` 1/2 (drowned via vines
    over a pond: no climbing). Also fixed: stale inventory made replans
    over-mine (102 cobblestone), and a failed goto no longer ends the session.
    - Open: no drowning reflex that works with swimming off (air recovery sees a
      vine as blocked); exploring far for ore is where both drownings started
      (Baritone branch-mines at depth instead); 2–4 goto "stuck" stalls per run;
      `run-build.py` still runs with digging off; body mod changed twice:
      requalify before scored navigation runs.
  - **M5 started** (Nov 2): `build_job` protects every template cell (air
    included) from the route planner (`protected_positions`), so build jobs
    now dig; `run-build.py --empty --seed N` is the M5 run (phase ticks in
    `build.json`).
    - Failed, kept: `beb62f5`+protection, seed 42424242 from (32, 70, -112),
      `20261102T120137588638Z`. It cut 35 oak logs by hand in 18,500 ticks
      (trees every ~30 blocks, first one 50 away) and never crafted, because
      `starter_tools` only applied to stone pickaxe targets. Then it explored a
      treeless shore at y=63 for ~100,000 ticks (6,820 route requests, nothing
      collected) until stopped by hand. Fixes: build jobs run the M4 starter
      phase first (stone pickaxe, axe, shovel; no wooden axe); a gather with no
      inventory change for 120 s stops `stalled` and ends the job; plain sand
      preferred over red sand. Seed 42424242 has at most 37 oak logs within
      40 blocks of any generated chunk, so M5 sites come from a snapshot scan
      (oak logs within 40, sand within 72, no surface water).
    - Failed, kept: those fixes, seed 20261026 from (-19, 65, -116),
      `20261102T135116924480Z`. The start was inside a grass block (the site
      scan read heights one too low): it cut 4 logs it could reach, but every
      route search expanded nothing, so no drop was collected; the watchdog
      ended the job at ~6,500 ticks (checks were only between log breaks).
      Fixes: `start_session` refuses a non-standable start; the watchdog also
      checks before each stance goto and drop walk; `run-build.py` reports a
      job that ends before building.
    - Failed, kept: those fixes, seed 20261026 from (-19, 66, -116),
      `20261102T140212509088Z`. Starter phase done: crafting table at tick
      757, stone pickaxe 1,749, stone axe and shovel 2,190; 28 oak logs cut
      with the stone axe by 4,494. Then sand: the nearest patch is a lake
      shore, and the bot bounced between two points on partial ("incomplete")
      routes for ~3,000 ticks (12 stuck gotos), while dry sand lay 20 blocks
      west. The watchdog ended the job. Fix: a candidate with no reachable
      stance skips every known candidate within 6 blocks (its patch).
    - Failed, kept, but **gathering done**: that fix, same site,
      `20261102T141238475772Z`. Empty inventory to every house material in
      7,388 ticks (starter and main gather, no stall). Build 2,948 ticks: 63 of
      84 blocks correct, then `blocked` with 21 missing (18 `state_unreachable`,
      1 `no_stance`): floor planks beside the bot's stance, two wall planks, a
      glass pane, and 9 roof stairs. Open (B8): diagnose `state_unreachable`
      from stances right beside the target (suspect: "arrived" within 0.6 of
      the stance centre, so the body may overlap the target cell) and stances
      for roof-edge stairs. No scaffolds were placed.
    - **Diagnosed and patched, not yet run live** (Nov 2): the stance arrival
      check is `hypot(p - cell centre) <= 0.6`, and the body box is 0.6 wide, so it
      could stand 0.4 into a neighbouring cell, including the one being placed
      into. `CompanionBody.precise_stance` (set by `build_job` around
      `run_build` only) arrives within 0.2 of the stance centre and re-centres
      when the body box overlaps the target cell. Also: `run-build.py --empty`
      now kits a chest 2 west of the start with 64 scaffolding and 2 shulker
      boxes; `build_job(chests=...)` peeks it before moving, takes the shulkers,
      and takes the scaffolding just before building (`minecraft:scaffolding` is
      first in `SCAFFOLD_ITEMS`).
    - **Scaffolding mechanics, live** (Nov 2, `mclab/scaffolding.py` holds the
      rules: `distance` 0-7 stability, roots, what drops with a broken root;
      fixture `tests/fixtures/schematics/floating-bridge.build.json`,
      `run-build.py --scaffolding 8`, last pass `20261102T184038033110Z` 5/5:
      two floating planks built, 4 scaffolds placed, 8 of 8 recovered). Found and
      fixed on the way: (1) the body skipped any neighbour with empty collision as
      a place target, which scaffolding has (Java `BodyBuild`); (2) a column grows
      upward only by clicking its side **not sneaking** (the body now tries both
      and requires the item's chosen cell to be the requested one); (3) the
      lattice outline only registers near its top edge (extra hit samples);
      (4) stances inside the target cell are excluded (the body stood in the
      tower); (5) a placing stance must see a face of a solid neighbour
      (`sees_support`; scaffolding columns count from any side); (6) a floating
      block gets a same-height scaffold column beside it, since the top face of a
      tower is above the eye. Cleanup breaks only roots and then collects drops
      (`CompanionBody.collect_drops`); out of scaffolds, standing ones are taken
      back before dirt is asked for. Body mod rebuilt: requalify before scored runs.
    - **House from empty with the supply chest** (`20261102T184245183502Z`, seed
      20261026): chest read, shulkers and scaffolding withdrawn (3 container ops,
      all ok); gather 11,473 ticks, build 5,870 ticks; **70 of 84 correct** (was
      63), 14 missing, no scaffolds needed. 12 cells skipped as `state_unreachable`,
      mostly plain planks (floor edge, wall corners) and two roof stairs, with the
      body standing inside the house at y 67. Cause not found: the failure event
      carries no target or stance; the next step is to log the target and the
      stance cell with each failed place before changing anything.
    - **Checkpoints, resume and prune** (Nov 2): every `run-build.py` run writes
      `checkpoints/NNNN.json` + `final.json` (`mclab/build_checkpoint.py`) and
      `build.json` now has `failures` (cell, reason, stance cell). `--resume FILE`
      restores the seed's world, the template cells and the inventory and runs only
      the build (checked live: 43 cells restored from `0042.json` of
      `20261102T201251437109Z`, 13 more placed). `mclab/prune.py` keeps `results/`
      small (CLAUDE.md has the rule). **Open (B8)**, first failure data, kitted
      house run `20261102T201251437109Z` (51/84): 30 distinct cells fail, all with
      the body standing *on* the y=66 floor at feet y=67: floor holes
      `(-16,66,-114)`, `(-14,66,-113)`, `(-14,66,-111)` and wall/roof cells.
      Reproduce with `--resume .../checkpoints/0042.json` rather than a full run
      (a hard kill of a run leaves the server on the seed world: `natural.restore_lab()`
      then `mclab server start`). `--resume FILE --stage` sets the world up and
      waits (join `127.0.0.1:25575`) until the run folder gets a `STOP` file.
    - **House placement fixed, replay from `0013.json`** (Nov 2, run
      `20261102T233918895522Z`): all 84 blocks correct in the delivered map (was
      51), build only, 12,188 ticks. Causes found with the diagnosis, in order:
      eye rays sampled only near face centres (edge samples added); stances chosen
      without a line-of-sight test (exact grid ray, overlay of own recent
      placements included, visible from the whole arrival radius); a wall torch
      needs a stance that sees its one support face; inside corners placed after
      the walls that close them (now first in their layer; the check compared a
      neighbour with itself); the door placed closed before the rest, which shut
      the body in (it cannot open doors: **open item**, teach routes to use doors);
      skipped cells now get two fresh rounds; the build waits for the sensor
      before calling itself blocked. Bottom up: nothing above a layer with a
      missing block. Not yet re-run: the whole job from an empty inventory.
    - **Build-debugging aids** (implemented Nov 3; unit and live staging/preview checks passed):
      1. `run-build.py --preview --inspect`: kit-place the finished template beside
         the completed attempt, then hold for in-game comparison until `STOP`.
         `--resume ... --stage --preview` inspects an existing checkpoint.
      2. `walkthrough.md` and `walkthrough.json`: each failed attempt's target,
         block, reason, actual stance, observer teleport command and diagnosis.
      3. Done: a failed `place_block` now carries `diagnosis` (eye, body, and per
         clickable neighbour: faces, points in reach / visible, and how many
         candidates the item refused, found obstructed or placed in the wrong
         orientation); `build.json` `failures` keeps it.
      Usage and limits: [BUILD_DEBUGGING](BUILD_DEBUGGING.md).
    - **Shared body review (Nov 3):** build/gather use `CompanionBody`, the same
      `SegmentedController` and Java body motor as navigation. Companion walking
      now shares movement/door/climbing settings with construction, with terrain
      edits disabled; `dig=True` enables those edits. Explicit movement and terrain
      overrides select permissions without replacing the body. Actual settings
      are recorded by policy ID per navigation and in build/gather artifacts.
      Preview/checkpoint staging passed live (2/2), run
      `20261103T123516731671Z`: restored 100 cells and placed a 100-cell reference
      at offset `[10, 0, 0]`; this is diagnostic proof, not construction qualification.
      Full capability inventory:
      [COMPANION_ABILITIES](COMPANION_ABILITIES.md).
      Review found two permission mismatches: **CAP-01**, planners proposed
      climbing with motor climbing disabled; and **CAP-02**, door settings were
      not reliably route-local. The Nov 3 migration implements propagation and
      unit coverage; enabled/disabled live qualification remains open. See
      [COMPANION_ABILITIES](COMPANION_ABILITIES.md).
    - **Nov 3 state** (uncommitted work from Nov 2 committed Nov 3):
      - Added: door and ladder use in routes (`BuildingMoves.java`), the site
        scanner (`scripts/analysis/site_scan.py`), the large-house fixture
        (`tests/fixtures/schematics/house-large.build.json`, 1,228 blocks; spruce
        → birch, mossy → plain), and crash recording in the live scripts.
      - Gathering fixes: refuse a block touching known water or lava (the bot
        drowned digging shoreline sand); surface blocks check only known water or
        lava, since the cells under sand are rarely sensed.
      - Small house from empty: 4/4 (`20261103T010332222541Z`). Scaffolding: 5/5
        (`20261103T030849936261Z`).
      - Iron-pickaxe baseline: **7/9, down from 9/9**. Seeds 42424242 and
        31415926 3/3. Seed 20261026 failed twice: rep 2 the iron gather stalled;
        rep 3 (`20261103T025858772778Z`) the bot was stuck in its own dig hole at
        (46.6, 57, −72.4), 10 blocks below the crafting table, every route up
        cancelled (`no_stance`). Suspect: climbing, now on in companion sessions.
        Earlier M4 runs on this seed also had stalls, so it may be an old flake.
      - Large house: crashed at start (`20261103T031040847065Z`), see "Open after
        Nov 3" at the top of "Next work".
  - **Body mod changed** (now `9e536f77…`): qualification (item 11 procedure)
    must be rerun before scored navigation runs.
  - Current status per milestone, the code map, and every known gap with its
    planned fix (ids K1–K7, C1–C3, B1–B6, A1–A4, G0–G9, in waves 3–5) are in
    [BUILD_AND_GATHER_PLAN](BUILD_AND_GATHER_PLAN_2026-11-01.md) §7–§9.
- Answered Nov 1 ([BUILD_AND_GATHER_PLAN](BUILD_AND_GATHER_PLAN_2026-11-01.md) §6):
  - The bot may shear or kill animals and must eat cooked meat.
  - It may craft and place chests; the operator may `/give` shulker boxes.
  - Test runs start at sites with the needed resources nearby.
  - Rule: real-world use, not artificial difficulty.
- Carried from the direction doc: `race` profile block budgets; server view
  distance for parity runs (currently 4).

## Document index

**Plan and decisions (current)**

| Document | Role |
|---|---|
| [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md) | Authoritative plan: decisions D1–D12, phases, step order, swimming notes |
| [DECISIONS.md](DECISIONS.md) | Decision log |
| [ROADMAP.md](ROADMAP.md) | This file: status, ordered work, index |
| [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md) | Companion track: schematic building, knowledge pack from the game, AltoClef-style gathering. Milestones M0–M6, status (§7), gap list with fixes (§8), M5/M6 detail (§9) |
| [KNOWLEDGE_PACK_FORMAT.md](KNOWLEDGE_PACK_FORMAT.md) | Contract: knowledge pack files and the `build.json` format |
| [BODY_ACTION_OPS.md](BODY_ACTION_OPS.md) | Contract: body action ops (craft, furnace, equip, place/use/break) and the lab `kit` fixture |
| [COMPANION_ABILITIES.md](COMPANION_ABILITIES.md) | Minecraft ability inventory: 79 tracked abilities, primitives, workflows, actual controls and missing support |
| [BUILD_DEBUGGING.md](BUILD_DEBUGGING.md) | Preview/reference copy, checkpoint inspection and failure walkthrough usage |
| [BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md](BARITONE_WEIGHTS_AND_MOVES_2026-10-30.md) | Baritone weights and the move-by-move comparison |
| [BARITONE_REFERENCE.md](BARITONE_REFERENCE.md) | Baritone baseline setup (pinned client, launcher, how it is run) |
| `Minecraft_AI_Technical_Handoff.md` (repo root) | Original specification; never edited |

**Ledgers and trackers (results; keep every cohort)**

| Document | Role |
|---|---|
| [STEP_C_PROGRESS_2026-10-29.md](STEP_C_PROGRESS_2026-10-29.md) | Planner-core cohorts 1–29 and causes; Oct 31 findings |
| [STEP_B_PROGRESS_2026-10-28.md](STEP_B_PROGRESS_2026-10-28.md), [STEP_B_RERUN_2026-10-29.md](STEP_B_RERUN_2026-10-29.md) | In-mod follower work and its live rerun |
| [PHASE0_NATURAL_BASELINE_2026-10-28.md](PHASE0_NATURAL_BASELINE_2026-10-28.md), [PHASE0_FIX_PLAN_2026-10-28.md](PHASE0_FIX_PLAN_2026-10-28.md), [PHASE0_FIXES_RESULTS_2026-10-28.md](PHASE0_FIXES_RESULTS_2026-10-28.md) | Phase 0 baseline and fixes (done) |
| [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json) | Acceptance tracker for the awareness/movement sprint |
| [gates.json](gates.json) | Gate ledger G0–G8 |
| [NAVIGATION_QUALIFICATION.json](NAVIGATION_QUALIFICATION.json) | Reset/navigation qualification (current for mod `68b3e8f6`; rerun after any body change) |
| `docs/*_PROOF.json`, `docs/G*_*.json` | Evidence files bound to source hashes |

**Reference (how things work)**

| Document | Role |
|---|---|
| [AWARENESS_MODES.md](AWARENESS_MODES.md) | Awareness presets, `sections` delivery semantics |
| [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md) | Body/lab HTTP protocol, `/policy` delivery rule |
| [SPATIAL_NAVIGATION.md](SPATIAL_NAVIGATION.md) | Map memory, recall, planner/motor boundary |
| [LAB_UI.md](LAB_UI.md) | Dashboard and how to run the lab |
| [SETUP_STATUS.md](SETUP_STATUS.md) | Runtime setup and G0–G3 history |
| [CONTROL_LATENCY_FOLLOWUP_2026-10-27.md](CONTROL_LATENCY_FOLLOWUP_2026-10-27.md), [CONTROL_PROFILE_R2_2026-10-28.md](CONTROL_PROFILE_R2_2026-10-28.md) | Control-loop latency findings |
| `scripts/analysis/` | Attempt analysis: planner trace, time budget, timeline, delivered-map replay |

**Model work (separate track, paused behind navigation)**

| Document | Role |
|---|---|
| [EXPERIMENT_QUEUE.md](EXPERIMENT_QUEUE.md) | Queued model experiments; its "next sprint" section is history |
| [G4_SCREENING.md](G4_SCREENING.md), [G4_LIVE_INTEGRATION.md](G4_LIVE_INTEGRATION.md) | Model screening and live integration |

**History or partly superseded** (banners in each; see the direction doc's
"Superseded and legacy documents")

[AWARENESS_MOVEMENT_SPRINT.md](AWARENESS_MOVEMENT_SPRINT.md),
[AWARENESS_MOVEMENT_RESULTS.md](AWARENESS_MOVEMENT_RESULTS.md),
[AUTONOMOUS_RUN.md](AUTONOMOUS_RUN.md),
[PAUSE_HANDOFF_2026-10-27.md](PAUSE_HANDOFF_2026-10-27.md),
[MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md](MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md),
[NATURAL_TERRAIN_COMPARISON.md](NATURAL_TERRAIN_COMPARISON.md),
[ROLLING_TERRAIN_PLANNING.md](ROLLING_TERRAIN_PLANNING.md),
[HILL_NAVIGATION.md](HILL_NAVIGATION.md),
[NAVIGATION_IMPLEMENTATION.md](NAVIGATION_IMPLEMENTATION.md),
[JEV_NAVIGATION_PLAN.md](JEV_NAVIGATION_PLAN.md),
[HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md),
[BARITONE_MOTOR_DESIGN.md](BARITONE_MOTOR_DESIGN.md).
