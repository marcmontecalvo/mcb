# Companion ability inventory

Snapshot: November 3, 2026. This is a tracking inventory, not a claim that every Minecraft block, recipe, entity, mod or game version has been tested. It covers player ability categories and important sub-abilities; version-specific block states, items, recipes and entity interactions need their own compatibility tests. The target here is the shared Java `mcb-body` and Python companion, not a separate creative-mode bot or the lab's privileged fixture operations. The [navigation direction](NAVIGATION_DIRECTION_2026-10-28.md) remains authoritative; current priorities and results are in [ROADMAP](ROADMAP.md).

Minecraft's own guides describe [survival, creative and combat](https://www.minecraft.net/en-us/article/what-minecraft), [controls and interactions](https://www.minecraft.net/en-us/article/minecraft-controls), [crafting](https://www.minecraft.net/en-us/article/how-craft), and [redstone](https://www.minecraft.net/en-us/creator/article/redstone). These establish the game categories, not proof that MCB implements them.

## Reading this inventory

**Implemented** means the named narrow capability exists; **partial** means an executor or primitive exists but the general capability, automation, or qualification is incomplete; **missing** means no supported companion capability was identified. Evidence in a row is the strongest relevant evidence, not a blanket guarantee. Live fixture, historical navigation qualification, and current end-to-end qualification are different. In particular the body has changed since the last scored qualification; rerun the roadmap's qualification before scored navigation.

**Toggle** identifies a real setting, not a proposed switch. `Op` means a caller can invoke an operation but no independent enable/disable permission was found. `None` means no ability-specific switch was found. Policy objects can be passed to `CompanionBody` / `start_session`; that is a Python configuration interface, not a claim that a user-facing settings screen or CLI switch exists. Defaults below refer to companion sessions, not every benchmark profile. Follow, gather and build share the body and motor, but an operation being available does not mean a task plans to use it.

**Permission propagation, development implementation:**

- **CAP-01 — climbing:** `allow_climb` now reaches both the Java planner and motor independently of `baritone_moves`; the offline reference also respects it. The Java ladder fixture checks both settings. Live disabled/enabled qualification on this changed mod remains open. [Body search](../src/mclab/body_search.py), [Java planner](../mcb-body/src/main/java/com/vulpine/mcb/body/RoutePlanner.java).
- **CAP-02 — doors:** `use_doors` and `climb_vines` are captured separately for each plan and motor objective. Native routes, edit steps and gaps carry their own policy; lockstep/pipelined development controls latch policy for the session and open doors only during movement. Tests cover consecutive settings and planner-reader isolation. Live passage and policy-transition checks remain open. [Runtime](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyRuntime.java), [BuildingMoves](../mcb-body/src/main/java/com/vulpine/mcb/body/BuildingMoves.java).

| Companion policy | Actual default and scope |
|---|---|
| Movement | `NATURAL_MOVEMENT`: diagonals, sprint, shapes, climb on; `max_drop=3`, `max_gap=2`, swimming off. Fields are `allow_diagonals`, `allow_sprint`, `allow_shapes`, `allow_climb`, `allow_swim`, `max_drop`, `max_gap`. [Source](../src/mclab/companion_body.py), [schema](../src/mclab/movement_policy.py). |
| Walking | `WALK_TERRAIN` derives from `DIG_TERRAIN`, but disables `allow_break`, `allow_place`, `allow_bridge`, `allow_pillar` and sets both budgets to zero. Default session uses this unless a policy or `dig=True` is supplied. |
| Digging | `start_session(dig=True)` selects `DIG_TERRAIN` unless explicitly overridden: planner edits/break/place, bridges and pillars enabled, 64 break and 64 place budget, dirt support, max stack 3; `use_doors=True`, `climb_vines=False`, `allow_falling_neighbours=False`, `planner_swim=False`. Build and gather live scripts request digging. `allow_break` and `allow_place` govern route edits, not a universal ban on explicit action ops. [Policy](../src/mclab/terrain_steps.py), [body](../src/mclab/companion_body.py), [build](../scripts/run-build.py), [gather](../scripts/run-gather.py). |
| Overrides | `start_session` accepts explicit `movement_policy` and `terrain_policy`; tasks may modify policy (build protects template positions). Planner switches include `planner_edits`, `planner_swim`, `baritone_moves`, `use_doors`, `climb_vines`, budgets, support material, protected cells and hazard costs. These govern route planning/execution and do not independently authorize all world actions. [Body search](../src/mclab/body_search.py), [jobs](../src/mclab/companion_jobs.py). |

## Movement, survival and following

| Ability | State; task and actual control | Evidence, limit and remaining work |
|---|---|---|
| Walk, turn, diagonals, sprint, stairs/slabs | Partial; shared route motor; `allow_diagonals`, `allow_sprint`, `allow_shapes` | [Route planner](../mcb-body/src/main/java/com/vulpine/mcb/body/RoutePlanner.java), [motor](../mcb-body/src/main/java/com/vulpine/mcb/body/RouteMotor.java). Frozen calm navigation 90/90 on prior qualified body; requalify current mod. |
| Jump gaps, climb rises, drop | Partial; shared route; `max_gap` 0–2 and `max_drop` 0–3 | Same sources; drops beyond the supported range and arbitrary parkour remain unproven. |
| Ladders/scaffolding | Partial; companion `allow_climb=True`; **CAP-01** propagated in development | Live ladder fixture 10/10 on earlier mod; rerun on the changed body with climbing on and off. [Roadmap](ROADMAP.md). |
| Vines | Partial; climb route controlled by `allow_climb` and `climb_vines`; companion vines off | Disabled after a drowning. [BuildingMoves](../mcb-body/src/main/java/com/vulpine/mcb/body/BuildingMoves.java). Do not infer safety from merely turning it on. |
| Surface swimming and water landings | Partial; `allow_swim` permits still-water edges; `planner_swim` enables the extended swim model. Both are off in companion defaults | Earlier live water fixture 10/10. Flowing water push is not modeled and native routes cut before current; test lakes, exits and recovery on current body. [Direction §5](NAVIGATION_DIRECTION_2026-10-28.md). |
| Submerged navigation and drowning recovery | Partial; Python controller has air recovery; no companion-specific selectable body drowning reflex | Companion drownings occurred with swimming disabled and vines interfering; do not equate earlier water fixture with reliable recovery. Implement and test reachable air under overhangs, low air and interruption with swimming both on/off. [Direction §5](NAVIGATION_DIRECTION_2026-10-28.md), [roadmap](ROADMAP.md). |
| Follow a player | Partial; follow task; no ability switch identified | [follow.py](../src/mclab/follow.py) uses visible named player, retargets at 2 blocks and continues toward last seen position. No guarantee of reacquisition across dimensions/unloaded terrain or reliable escort under threats. |
| Open wooden doors/fence gates on route | Partial; `use_doors` on in companion terrain; **CAP-02** propagated in development | [BuildingMoves](../mcb-body/src/main/java/com/vulpine/mcb/body/BuildingMoves.java) opens nearby under the active objective's permission. Confirm live follow and build journeys and consecutive on/off routes. Explicit `use_block` also operates hand-usable doors. |
| Close a door/gate explicitly | Partial; `use_block` op, not a close toggle | A second normal use may close a hand-operated door; task must issue it and verify state. [Action contract](BODY_ACTION_OPS.md). |
| Automatically close behind body | Missing; no switch identified | Route logic opens but has no recorded close-behind behavior. Design safe timing with player/animal passage and test. |
| Iron doors and redstone-operated entrances | Missing as automatic routing; no switch | Iron doors remain route walls; explicit use cannot substitute for powering them. Add switch discovery, control and safe traversal. [BuildingMoves](../mcb-body/src/main/java/com/vulpine/mcb/body/BuildingMoves.java). |
| Hostile avoidance and emergency retreat | Partial; cost profile fields `mob_coefficient`, `mob_radius`, `hazard_penalty`; `follow_route` `flee` | Reflex is limited; tested safe and race night runs failed acceptance, and longer retreat was rejected. This is not combat or a reliable survival system. [Roadmap item 13](ROADMAP.md), [RouteReflex](../mcb-body/src/main/java/com/vulpine/mcb/body/RouteReflex.java). |
| Food, hunger and healing | Partial; `eat` op, gather executor can call it; no global auto-eat toggle found | Real use consumes food over ticks; hunger and inventory gate it. Prove continuous task survival, food acquisition, starvation and interruption. [Ops](BODY_ACTION_OPS.md), [gather runner](../src/mclab/gather_runner.py). |
| Damage, fire, lava, suffocation and fall safety | Partial; route hazard costs, drop limits and `allow_falling_neighbours` | Avoidance is not immunity or full survival. Known liquid and falling-block restrictions were added after deaths; unknown terrain and other damage sources remain. [Roadmap](ROADMAP.md). |
| Sleep, respawn, death recovery, status effects | Missing as end-to-end tasks; no switches found | Generic use is not proof of bed/sleep orchestration or inventory recovery. Add health/effect decisions and tests. |
| Creative flight, elytra, rockets | Missing; no flight switch | Add separate game-mode and elytra controls, physics, safety, equipment and landing tests. |
| Boats, minecarts, horses and other mounts | Missing as navigation tasks; no switch | Generic entity interaction does not establish mounting, steering, dismounting or transport routing. |

## Building and world interaction

| Ability | State; task and actual control | Evidence, limit and remaining work |
|---|---|---|
| Place a block with orientation | Partial; `place_block` op; build job | Real item, reachable visible support and vanilla placement; placement 46/46 in earlier fixture. Some state properties are ignored/recomputed; test relevant blocks and placement contexts individually. [Ops](BODY_ACTION_OPS.md), [BodyBuild](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyBuild.java). |
| Break blocks and tool selection | Partial; `break_block` op; gather/build; route edit `allow_break` is separate | Real attack over ticks, reach, line of sight and tool/drop checks; pickup requires movement. Expand block/tool and environment coverage. [Ops](BODY_ACTION_OPS.md). |
| Clear, excavate, mine ore/wood | Partial; gather and build; route edits `allow_break`, budgets and protections | Empty to iron pickaxe passed historical 9/9; newer baseline 7/9. Liquid/falling-block restrictions limit choices; arbitrary branch mining and large excavation are not established. [Roadmap](ROADMAP.md), [gather runner](../src/mclab/gather_runner.py). |
| Bridge, pillar and route terrain edits | Partial; `allow_place`, `allow_bridge`, `allow_pillar`, budgets and materials | Enabled by dig profile, disabled by walk profile. A live multi-edit chain remains open; prove supply, cleanup, safety and protected cells. [Roadmap](ROADMAP.md). |
| Schematic import, rotate/mirror, plan and build | Partial; build job parameters `rotation`, `mirror`, `survey`, `build_only` are job options, not ability permissions | Build protects all template cells, orders work, refills materials and checks the delivered map. Small house from empty 4/4; replay 84/84; large house 1,228 blocks crashed at start and needs rerun. [Jobs](../src/mclab/companion_jobs.py), [roadmap](ROADMAP.md). |
| Giant construction (large sites, long duration, complex shapes) | Partial; same build job, no scale toggle | Not qualified by small-house success or isolated placement tests. Establish large/giant representative builds, long-run recovery, resource logistics, throughput, world/chunk limits, verification and restart evidence. |
| Scaffolding erect, recover and reuse | Partial; build automatically plans supports; `--scaffolding` is a fixture supply option | Floating bridge fixture 5/5 and all eight recovered in cited run, not giant-build qualification. [scaffolding](../src/mclab/scaffolding.py), [roadmap](ROADMAP.md). |
| Checkpoint, resume and diagnostics | Partial; `run-build.py --resume`, `--stage`, `--preview`, `--preview-offset`, `--inspect` | Checkpoint restore tested for a build-only continuation; `build_debug` provides preview and failure walkthrough. `--inspect` holds the finished run for inspection. Preview is lab kit placement for human comparison, not autonomous building or proof. Route records refer to deduplicated `policy_id` entries in `build.json` `policies`. [Runner](../scripts/run-build.py), [debug](../src/mclab/build_debug.py). |
| Repair/demolish an existing structure | Partial; build reconciles template cells and has break/place primitives | Do not infer arbitrary protected-property preservation, selective salvage or autonomous repair from template construction. Add explicit target, consent, verification and recovery tests. [build runner](../src/mclab/build_runner.py). |
| Use blocks: switches, trapdoors, work surfaces, tool transformations | Partial; `use_block` op | Normal use with optional item; opens or closes usable blocks, strips logs, paths or tills where vanilla accepts. Individual states and downstream effects need testing. [Ops](BODY_ACTION_OPS.md). |
| Redstone circuits, machines and automation | Missing as a general design/build/operate task; `use_block`/`place_block` are primitives only | Requires circuit state, timing, dependencies, power verification and safety. [Mojang overview](https://www.minecraft.net/en-us/creator/article/redstone). |
| Water/lava bucket collection and placement | Missing as a supported general fluid task; no fluid toggle | `place_block` requires a block item and is not bucket placement. Generic block/entity use is not evidence of controlled fluid pickup, placement, source/flow reasoning or safe recovery. Test both fluids and dimension rules before claiming support. |
| Waterlogging, fluids and gravity blocks in builds | Partial; no per-ability toggle | Placement matching ignores `waterlogged`; powered/open and connection properties may be recomputed. Add explicit fluid/state verification and falling-block ordering. [Ops](BODY_ACTION_OPS.md). |
| Crop planting, farming, harvesting, fishing | Partial only at primitive level; `use_block`/break operations | Hoe use is supported; no general closed-loop agriculture or fishing capability established. Test seeds, growth, harvest, replanting and inventory. |

## Inventory, production and entities

| Ability | State; task and actual control | Evidence, limit and remaining work |
|---|---|---|
| Observe inventory, select/equip hotbar item, collect drops | Partial; `equip` op and companion pickup movement; no independent permission switch | [BodyActions](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyActions.java), [CompanionBody](../src/mclab/companion_body.py). Equip selects a held item, not proof of armor/offhand management. Test full inventory and inaccessible drops. |
| Chest/barrel/shulker peek, deposit, withdraw | Partial; `container` op; gather/build can use remembered storage | Live checks 18/18; partial moves may remain after failure. General logistics across large builds, sorting and durable recovery need testing. [Ops](BODY_ACTION_OPS.md). |
| Craft inventory and crafting-table recipes | Partial; `craft` op, gather planner/executor | 1–256 per request and real ingredients/station. Craft/furnace fixture 8/8; pack has unhandled recipe types, so not all recipes. [BodyActions](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyActions.java), [roadmap](ROADMAP.md). |
| Furnace, blast furnace, smoker | Partial; `furnace` load/collect op; gather | Fuel and input required; asynchronous cooking still requires task orchestration. Test station contention, fuel accounting and recovery at scale. [Ops](BODY_ACTION_OPS.md). |
| Brewing, enchanting, anvil, smithing, stonecutting, other special menus | Missing as supported complete production workflows; no switches identified | Knowledge dump lists unhandled types; generic station use is not a menu executor. Implement and verify each workflow. [Roadmap](ROADMAP.md). |
| Gather, craft and upgrade tools | Partial; gather job; no single global toggle | Planner handles material dependencies, fuel and tool progression; 9/9 historical iron-pick runs, later 7/9. Needs current repeatability, long jobs and supply coverage. [gather tasks](../src/mclab/gather_tasks.py), [roadmap](ROADMAP.md). |
| Passive-animal attack/hunt | Partial; `attack_entity` op; gather hunt | Only eligible passive `Animal` targets; refuses tamed animals, players, villagers and other targets. Cooldown, reach and line of sight apply; reacquire if it moves. [BodyEntity](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyEntity.java), [Ops](BODY_ACTION_OPS.md). |
| Hostile combat, ranged weapons, shields, armor, tactics | Missing as supported combat workflow; no combat toggle | Passive-animal attack is not hostile combat. Implement targeting, safety, equipment, defense, evasion and live night acceptance. |
| Shear, milk, other entity use | Partial; `use_entity` op; gather can shear | Live animal checks 16/16 with stationary lab animals; generic interaction is not proof of every entity/item interaction. Test moving entities, inventory effects and task recovery. [Ops](BODY_ACTION_OPS.md). |
| Breed, tame, lead, trade, manage villagers or pets | Missing as complete tasks; no switches found | Generic use may cause a vanilla interaction but does not establish selection, prerequisites, menus or a multi-step behavior. |
| Multi-agent cooperation and player escort | Missing as a coordinated capability; no switch | Follow is not cooperative planning, shared inventory, collision avoidance or formation. [Direction](NAVIGATION_DIRECTION_2026-10-28.md). |

## World and compatibility boundaries

| Ability | State; task and actual control | Evidence, limit and remaining work |
|---|---|---|
| Perception, map, explore and interaction stances | Partial; awareness profiles and route policies | Unknown is not air; sensor does not force-load chunks. Interaction reach uses eye/visibility; long-range search and discovered terrain remain bounded by observations. [PolicySensor](../mcb-body/src/main/java/com/vulpine/mcb/body/PolicySensor.java), [roadmap](ROADMAP.md). |
| Overworld long-range navigation | Partial; shared Java body planner | Frozen calm result covers measured seeds/routes on an earlier body; unknown space, stalls and night survival remain. Requalify this changed body. [Roadmap](ROADMAP.md). |
| Nether/End portals, dimension transitions and cross-dimension tasks | Missing as supported planned navigation; no switch identified | Add portal discovery, travel, dimension-aware memory and destination handling, hazards and return tests. Do not assume a goal in one coordinate space survives transition. |
| Creative, spectator, commands, teleportation | Missing as companion workflows; no switch | Lab `kit` is privileged test setup and cannot be counted as a survival-body ability. [Ops](BODY_ACTION_OPS.md). |
| Vanilla version, data packs and mods | Partial; game-generated knowledge pack, vanilla-oriented runtime | Snapshot pack is 26.3; recipes include unsupported types. Mods may add blocks, entities, dimensions, menus and rules the planner does not understand. No blanket mod compatibility: regenerate knowledge and qualify each integration. [Knowledge format](KNOWLEDGE_PACK_FORMAT.md), [KnowledgeDump](../mcb-body/src/main/java/com/vulpine/mcb/body/KnowledgeDump.java). |
| Permissions, protected areas and server limits | Partial; operation checks, no bypass toggle | Actions respect reach, sight, spawn protection and `mayInteract`; task should report refusal and recover. Chunk loading, world border and server-specific claims require integration tests. [Ops](BODY_ACTION_OPS.md). |

## Granular ability checklist

Stable IDs identify individual tracking items; the category tables above retain the fuller rationale and evidence. **Primitive**: `yes` means code exists for the stated narrow action; `limited` means context-dependent or unqualified; `no` means no dedicated supported primitive found. **Workflow**: `yes` means an existing task invokes it, `limited` means only a bounded task or fixture does, `no` means no complete autonomous workflow found. Neither column claims a new live test. **Control** names an actual field or operation; `none found` is not a proposed setting. A raw control or generic use does not imply that the route planner can reason about the mechanic. Test each item against the current body and supported game version before promoting it.

| ID | Ability | Primitive | Workflow | Control / evidence and gap |
|---|---|---|---|---|
| MOV-01 | Walk and steer | yes | yes | Route motor; arrival and obstacles remain bounded by sensed geometry. |
| MOV-02 | Sprint | yes | yes | `allow_sprint`; food and physics apply. |
| MOV-03 | Jump and sprint-jump | yes | limited | `max_gap`, `allow_sprint`; bounded gap/rise support, not arbitrary parkour. |
| MOV-04 | Crouch/sneak | yes | limited | Raw control `sneak`; placement uses sneak internally. No general crouch-navigation switch. [Adapter](../mcb-body/src/main/java/com/vulpine/mcb/body/CarpetBodyAdapter.java). |
| MOV-05 | Crawl in one-block spaces | no | no | None found; crouch input alone is not crawl path planning. |
| MOV-06 | Step up/down and safe drop | yes | yes | `allow_shapes`, `max_drop`; shape and drop bounds apply. |
| MOV-07 | Climb ladder | yes | limited | `allow_climb` controls planner and motor (**CAP-01**); earlier 10/10 fixture predates the current mod. |
| MOV-08 | Climb scaffolding | yes | limited | `allow_climb` controls planner and motor (**CAP-01**); expand live routing coverage. |
| MOV-09 | Climb vines | limited | limited | `allow_climb` plus `climb_vines`; off by default after drowning. |
| MOV-10 | Swim at surface | yes | limited | `allow_swim` and `planner_swim`; companion defaults off. |
| MOV-11 | Dive and navigate underwater | limited | no | Raw swim control is not full underwater routing or air-budget planning. |
| MOV-12 | Recover breathable air | limited | limited | Python recovery exists; companion drowning reflex is not reliably established. |
| MOV-13 | Boat/raft embark, steer, exit | no | no | None found; generic entity use does not establish steering. |
| MOV-14 | Minecart/rail transport | no | no | None found; requires track routing and mount control. |
| MOV-15 | Mount and steer animal | no | no | None found; distinguish entity interaction from riding. |
| MOV-16 | Creative flight | no | no | None found; requires explicit mode and flight controls. |
| MOV-17 | Elytra glide, rockets, landing | no | no | None found; equipment and flight safety needed. |
| MOV-18 | Follow named player | yes | limited | [FollowTarget](../src/mclab/follow.py); last-seen behavior, not full escort. |
| MOV-19 | Observe and avoid dynamic threats | yes | limited | `mob_coefficient`, `mob_radius`, `hazard_penalty`, `flee`; failed night acceptance. |
| INT-01 | Open wooden door while routing | yes | limited | `use_doors` is captured per route (**CAP-02**); live requalification remains. [BuildingMoves](../mcb-body/src/main/java/com/vulpine/mcb/body/BuildingMoves.java). |
| INT-02 | Close wooden door explicitly | yes | limited | `use_block`; caller verifies state. |
| INT-03 | Close door automatically behind passage | no | no | None found; add safe close timing. |
| INT-04 | Open/close fence gate | yes | limited | `use_doors` may open during permitted routes; `use_block` can explicitly toggle. No auto-close. |
| INT-05 | Open/close trapdoor | yes | limited | `use_block`; no route opening/closing behavior identified. |
| INT-06 | Power and traverse iron door/trapdoor | limited | no | Generic use/place is available, not a redstone control workflow. |
| INT-07 | Use button, lever, pressure plate | limited | no | Generic `use_block` or physical movement may interact; no circuit-aware task. |
| INT-08 | Place oriented blocks, slabs, stairs | yes | limited | `place_block`; 46/46 earlier placement fixture, not every block/state. |
| INT-09 | Break block using appropriate tool | yes | yes | `break_block`; tool and drop checks; route `allow_break` is separate. |
| INT-10 | Strip log, till soil, make path | yes | limited | `use_block` with item; effects depend on vanilla context. |
| INT-11 | Write or edit sign/hanging sign | no | no | None found; block placement alone does not supply text. |
| INT-12 | Read/write books, lecterns and text-bearing block entities | no | no | None found; requires content and menu protocol. |
| INT-13 | Open/use generic block | yes | limited | `use_block`; menu is closed and individual outcomes require verification. |
| INV-01 | Select held hotbar item | yes | yes | `equip` op; selects a held inventory item. |
| INV-02 | Swap inventory item into hotbar | yes | limited | `equip` performs a menu swap if necessary; not arbitrary rearrangement. [BodyActions](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyActions.java). |
| INV-03 | Split/transfer partial stacks | yes | limited | `container` op uses legal clicks for requested counts; not a general inventory editor. [BodyContainer](../mcb-body/src/main/java/com/vulpine/mcb/body/BodyContainer.java). |
| INV-04 | Drop selected item or stack intentionally | no | no | None found; walking over existing drops is pickup, not dropping. |
| INV-05 | Equip/remove armor | no | no | No dedicated armor-slot op found; hotbar equip is different. |
| INV-06 | Swap/use offhand or shield | no | no | No dedicated offhand management found. |
| INV-07 | Collect dropped item | yes | limited | Companion walks to drops; validate inventory-full and unreachable-item recovery. |
| INV-08 | Peek/deposit/withdraw chest, barrel, shulker | yes | limited | `container`; 18/18 lab checks. Failed transfers can leave partial moves. |
| INV-09 | Sort inventory and replenish supplies | limited | limited | Build refill and storage exist; no general sorting/loadout workflow. |
| PROD-01 | Craft 2×2/3×3 crafting recipe | yes | yes | `craft` op; ingredients and reachable table as needed; types limited. |
| PROD-02 | Smelt, blast, smoke | yes | limited | `furnace` load/collect; account for fuel and delayed output. |
| PROD-03 | Brew potions | no | no | No brewing workflow found; recipe knowledge alone is insufficient. |
| PROD-04 | Enchant, repair, smith and stonecut | no | no | No supported complete menu workflows found. |
| PROD-05 | Gather natural blocks and drops | yes | yes | Gather job; water/falling-block exclusions and reachable world constrain it. |
| PROD-06 | Grow crops, harvest and replant | limited | no | Hoe/use/break primitives exist; no autonomous farming cycle established. |
| PROD-07 | Fishing | no | no | No supported cast, bite detection and reel workflow found. |
| PROD-08 | Construct schematic | yes | limited | `build_job`; 4/4 small-house empty runs, large attempt crashed. |
| PROD-09 | Giant construction and supply chain | limited | no | Shared build primitives; no completed giant-scale qualification. |
| PROD-10 | Scaffold, recover support | yes | limited | Small floating-bridge live fixture 5/5; expand scale and materials. |
| PROD-11 | Checkpoint and resume build | yes | limited | `--resume`; tested build-only restore, not arbitrary full-job recovery. |
| PROD-12 | Build/operate redstone circuit | limited | no | Generic placement/use; requires signal and timing reasoning. |
| FLD-01 | Collect water source in bucket | limited | no | Generic use is not verified bucket handling; no dedicated operation. |
| FLD-02 | Place water with bucket | limited | no | `place_block` requires a block item; test item-use semantics and fluid behavior separately. |
| FLD-03 | Collect/place lava in bucket | limited | no | Same boundary; hazard and dimension tests required. |
| FLD-04 | Plan around flowing water and bubble columns | limited | no | Flow priced but push not modeled; native routes cut before current. |
| FLD-05 | Place/verify waterlogged target | limited | no | `place_block` matching ignores `waterlogged`; final fluid-state verification needed. |
| ENV-01 | Ice and slippery momentum | limited | no | Generic geometry/control exists; no dedicated traction model or qualified task found. |
| ENV-02 | Honey and slime surfaces | limited | no | No dedicated sticky/bounce behavior or qualification identified. |
| ENV-03 | Powder snow | limited | no | No qualified sink/freezing/escape reasoning found. |
| ENV-04 | Cobweb | limited | no | No qualified slowdown/escape planning found. |
| ENV-05 | Sand/gravel falling blocks | limited | limited | `allow_falling_neighbours` controls route cuts; disabled in companion after suffocation. Test collapse in construction and mining. |
| ENV-06 | Fire and lava avoidance/extinguishing | limited | limited | `hazard_penalty` and fluid-adjacency rules; no complete fire response. |
| ENV-07 | TNT/explosions and blast protection | no | no | No safe explosive planning or blast-response workflow found. |
| ENV-08 | Weather, time, light and hostile spawning | limited | no | Threat observations and costs exist; no complete weather/shelter/light management. |
| ENV-09 | Beds, sleep and spawn point | limited | no | Generic use exists; no verified sleep/spawn and dimension safety workflow. |
| ENV-10 | Respawn anchors and dimension-specific respawn | limited | no | No supported charge/use/safety workflow; generic use is not proof. |
| ENV-11 | Totem and emergency offhand survival | no | no | No dedicated offhand or totem management. |
| ENV-12 | Dimension portals and return | no | no | No dimension-aware travel planning or task continuity found. |
| ENV-13 | Chunk boundaries and unloaded space | limited | limited | Sensor never force-loads; unknown is not air. Long-range recovery remains bounded. |
| ENV-14 | World border, height, protection | limited | limited | `place_block` and other interactions check permissions/limits; test server-specific protection and refusals. |
| ENT-01 | Attack eligible passive animal | yes | limited | `attack_entity`; passive-only policy, reach and cooldown, live stationary-animal fixture. |
| ENT-02 | Attack hostile mob or defend player | no | no | Passive attack refuses other targets; no hostile-combat workflow. |
| ENT-03 | Shear sheep or milk cow | yes | limited | `use_entity` with item; stationary lab animals tested, moving cases needed. |
| ENT-04 | Breed, tame or lead animals | limited | no | Generic use may interact; prerequisites and multi-step orchestration unverified. |
| ENT-05 | Trade with villager | limited | no | Generic interaction is not a trading menu implementation. |
| ENT-06 | Bow, crossbow, trident or thrown items | no | no | No supported ranged-attack workflow found. |
| ENT-07 | Cooperate with another bot/player | limited | no | Follow exists; no coordinated task allocation or shared logistics. |

## Verified external reference scope

These four Mojang pages were opened on November 3, 2026 and returned article content. They provide broad game context; the repository sources and tests above establish MCB behavior.

- [What is Minecraft](https://www.minecraft.net/en-us/article/what-minecraft): describes survival and creative modes, resources, crafting, monsters, health and hunger.
- [Minecraft controls](https://www.minecraft.net/en-us/article/minecraft-controls): describes movement, mining and the use interaction.
- [How to craft](https://www.minecraft.net/en-us/article/how-craft): explains inventory and crafting-table recipes, ingredients and gathering.
- [Researching Redstone](https://www.minecraft.net/en-us/creator/article/redstone): describes components, power, circuits and automation examples.

## Priorities and evidence to add

1. Verify **CAP-01** and **CAP-02** live with on/off and consecutive-route cases. Rerun the large-house empty-inventory job and compare the iron-pick baseline with climbing disabled and enabled. Preserve failures and exact versions. Requalify the changed body before scored navigation.
2. Make survival dependable: prove drowning escape even when swimming is disabled, including overhangs and vines; then decide and test hostile combat or shelter against the documented night acceptance. Automatic door closing and iron-door traversal need explicit behaviors and tests.
3. Qualify construction at increasing scales: representative giant multi-material, vertical, interior, roof and scaffold builds; complete material supply, checkpoints/restart, cleanup and full final-world diff. Measure time and failure recovery, and distinguish lab-supplied materials from gathered ones.
4. Extend production and interaction deliberately: fluid bucket handling and state verification, missing recipe/station families, storage logistics, equipment, agriculture and entity workflows. Maintain per-version and per-block test matrices instead of assuming generic use covers every case.
5. Add dimension travel, transport and flight only with explicit policies, task-level orchestration and live safety tests. Keep all new controls distinct from existing route-edit permissions and op availability.
