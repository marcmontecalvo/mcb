"""Body-planner adapter (ROADMAP item 8, stage E)."""

from dataclasses import replace
from types import SimpleNamespace

from mclab import body_search
from mclab.movement_policy import MovementPolicy
from mclab.terrain_steps import StepPlan, TerrainPolicy

PROD = TerrainPolicy(
    planner_tick_costs=True,
    planner_unknown=True,
    heuristic_weight=1,
    baritone_search=True,
    baritone_diagonals=True,
    baritone_moves=True,
    planner_swim=True,
    planner_edits=True,
    allow_bridge=True,
    allow_pillar=True,
    max_support_stack=3,
)
KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 2},
)
MOVES = MovementPolicy(True, True, 3, True, 2, True, True)


def controller():
    sent = []
    c = SimpleNamespace(
        goal=(40, 1, 0),
        radius=2.5,
        interact_reach=None,
        threats=(),
        search_nodes=128,
        movement_policy=MOVES,
        terrain_policy=PROD,
        previous_route=((0, 1, 0), (1, 1, 0)),
        failed_edges={((5, 1, 0), (6, 1, 0))},
        plan_reports={},
        inventory=KIT,
        own_footholds={(3, 1, 0)},
        submit_plan=sent.append,
        _remaining_policy=lambda: PROD,
    )
    return c, sent


def test_payload_carries_policy_history_and_limits():
    c, sent = controller()
    search = body_search.BodySearch(c, (0, 1, 0), 3)
    payload = sent[0]
    assert payload["plan_id"] == search.plan_id
    assert payload["swim"] and payload["extra"] and payload["max_gap"] == 2
    assert payload["unknown_penalty"] == PROD.unknown_penalty
    assert payload["favored"] == [[0, 1, 0], [1, 1, 0]]
    assert payload["excluded"] == [[[5, 1, 0], [6, 1, 0]]]
    assert search.advance().status == "searching"


def test_companion_planning_radius_can_reserve_motor_arrival_tolerance():
    c, sent = controller()
    c.planner_radius = 2.3
    body_search.BodySearch(c, (0, 1, 0), 3)
    assert sent[0]["radius"] == 2.3
    assert c.radius == 2.5


def test_body_search_submits_only_after_snapshot_is_committed():
    c, sent = controller()
    events = []

    class Transfer:
        def begin(self, owner, cells):
            events.append(("begin", owner, cells))

        def advance(self, owner):
            events.append(("advance", owner))
            return 7 if len(events) > 2 else None

        def cancel(self, owner):
            events.append(("cancel", owner))

    c.planner_map = Transfer()
    snapshot = {(0, 0, 0): {"block": "minecraft:stone"}}
    search = body_search.BodySearch(c, (0, 1, 0), 3, snapshot)
    assert not sent
    assert search.advance().reason == "planner map transfer"
    assert not sent
    assert search.advance().reason == "body plan pending"
    assert sent[0]["map_generation"] == 7
    assert events[0] == ("begin", search, snapshot)


def test_done_route_is_segmented_like_the_worker():
    c, _ = controller()
    search = body_search.BodySearch(c, (0, 1, 0), 3)
    route = [[x, 1.0, 0] for x in range(40)]
    c.plan_reports[search.plan_id] = {
        "plan_id": search.plan_id,
        "state": "done",
        "status": "reached",
        "route": route,
        "expanded": 812,
    }
    result = search.advance(node_limit=128, budget_ms=3)
    assert result.status == "segment" and len(result.route) == body_search.SEGMENT
    assert len(result.full_route) == 40 and result.expanded == 812


def test_edit_payload_follows_tools_supply_and_protection():
    c, sent = controller()
    body_search.BodySearch(c, (0, 1, 0), 3)
    edits = sent[0]["edits"]
    assert edits["step"] and edits["break_costs"]["minecraft:stone"] == 16
    # no axe on the hotbar: the log is still dug, at bare-hand speed (2 x 30 ticks)
    assert edits["break_costs"]["minecraft:oak_log"] == 60 + 2 + 6
    assert (
        edits["fall_costs"]["minecraft:sand"] == 3 + 2 + 17
    )  # 3 ticks with an iron shovel
    assert edits["place_supply"] == 2  # two cobblestone in the hotbar
    assert edits["protected"] == [[3, 1, 0]]
    no_break = body_search.edit_payload(replace(PROD, allow_break=False), KIT, ())
    # every tool broken: still a way out, by hand (stone x 100: no drop without a pick)
    bare = body_search.edit_payload(PROD, [], ())["break_costs"]
    assert bare["minecraft:dirt"] == 15 + 8 and bare["minecraft:stone"] == 150 + 8
    assert no_break["break_costs"] == {}


def test_iron_ore_with_only_a_wooden_pickaxe_is_priced_at_what_the_motor_swings():
    # The motor swings the wooden pickaxe (it cannot harvest iron ore): 3.0 x 100 / 2
    # = 150 ticks, not the 23 ticks of the iron-speed table price.
    held = (
        {"slot": 0, "item": "minecraft:wooden_pickaxe", "count": 1},
        {"slot": 1, "item": "minecraft:wooden_shovel", "count": 1},
    )
    edits = body_search.edit_payload(PROD, held, ())
    costs = edits["break_costs"]
    assert costs["minecraft:iron_ore"] == 150 + 8
    assert costs["minecraft:stone"] == 23 + 8  # the wooden pickaxe harvests stone
    assert costs["minecraft:dirt"] < 23 and edits["tools"]["minecraft:dirt"].endswith(
        "wooden_shovel"
    )


def test_place_supply_is_the_true_stock_of_every_allowed_material():
    held = (
        {"slot": 0, "item": "minecraft:wooden_pickaxe", "count": 1},
        {"slot": 12, "item": "minecraft:cobblestone", "count": 40},  # main inventory
        {"slot": 13, "item": "minecraft:cobblestone", "count": 20},
        {"slot": 4, "item": "minecraft:dirt", "count": 5},
        {"slot": 5, "item": "minecraft:andesite", "count": 99},  # not allowed here
    )
    allowed = ("minecraft:dirt", "minecraft:cobblestone")
    policy = replace(PROD, material_allowlist=allowed)
    edits = body_search.edit_payload(policy, held, ())
    assert edits["place_supply"] == 65 - policy.reserve_blocks
    # the allowlist's preference order (mclab.temporary_blocks), not the most held
    assert edits["materials"] == ["minecraft:dirt", "minecraft:cobblestone"]
    assert edits["material"] == "minecraft:dirt"


def test_body_edit_steps_become_step_plans():
    c, _ = controller()
    search = body_search.BodySearch(c, (0, 1, 0), 3)
    c.plan_reports[search.plan_id] = {
        "plan_id": search.plan_id,
        "state": "done",
        "status": "reached",
        "route": [[0, 1.0, 0], [1, 1.0, 0], [2, 1.0, 0]],
        "steps": [
            {
                "source": [1, 1.0, 0],
                "target": [2, 1.0, 0],
                "breaks": [[2, 1, 0], [2, 2, 0]],
                "places": [],
                "kind": "step",
            }
        ],
        "expanded": 30,
    }
    result = search.advance()
    assert result.steps == (
        StepPlan((1, 1.0, 0), (2, 1.0, 0), ((2, 1, 0), (2, 2, 0)), (), "step"),
    )
    # The controller looks steps up by the route's own (source, target) pairs.
    assert (result.route[1], result.route[2]) == (
        result.steps[0].source,
        result.steps[0].target,
    )


def test_terrain_forecast_transports_policy_stock_and_pillar_exit():
    c, sent = controller()
    search = body_search.BodyTerrainSearch(c, (0, 1, 0), 3, {}, PROD, KIT)
    payload = sent[0]
    assert payload["mode"] == "terrain"
    assert payload["policy"]["break_budget"] == PROD.break_budget
    assert payload["inventory"] == list(KIT)
    assert payload["protected"] == [[3, 1, 0]]
    c.plan_reports[search.plan_id] = {
        "state": "done",
        "status": "segment",
        "expanded": 12,
        "route": [[0, 1.0, 0], [0, 2.0, 0]],
        "steps": [
            {
                "source": [0, 1.0, 0],
                "target": [0, 2.0, 0],
                "breaks": [],
                "places": [[0, 1.0, 0]],
                "kind": "pillar",
                "exit": [1, 3.0, 0],
            }
        ],
    }
    step = search.advance().steps[0]
    assert step.exit == (1, 3, 0) and type(step.exit[1]) is int


def test_interaction_and_survival_policy_are_transmitted():
    c, sent = controller()
    c.interact_reach = 4.5
    c.threats = ((5, 1, 3),)
    c._remaining_policy = lambda: replace(
        PROD, hazard_penalty=40.0, mob_coefficient=3.0
    )
    body_search.BodySearch(c, (0, 1, 0), 3)
    assert sent[0]["interact_reach"] == 4.5
    assert sent[0]["hazard_penalty"] == 40.0
    assert sent[0]["mob_coefficient"] == 3.0
    assert sent[0]["threats"] == [[5, 1, 3]]


def live_memory(preset="sections", mode="persistent", historical=True):
    from mclab.awareness import Awareness
    from mclab.memory_policy import MemoryPolicy

    return SimpleNamespace(
        policy=MemoryPolicy(memory_mode=mode, use_historical_geometry=historical),
        archive=SimpleNamespace(
            identity=SimpleNamespace(awareness=Awareness.resolve(preset))
        ),
    )


class CountingTransfer:
    def __init__(self):
        self.frames = []

    def begin(self, owner, cells):
        self.frames.append(("begin", owner))

    def advance(self, owner):
        self.frames.append(("advance", owner))
        return 1

    def cancel(self, owner):
        self.frames.append(("cancel", owner))


def test_sections_walks_plan_on_live_terrain_with_no_map_frames():
    c, sent = controller()
    c.memory = live_memory()
    c.planner_map = transfer = CountingTransfer()
    for start in ((0, 1, 0), (5, 1, 0)):
        search = body_search.BodySearch(c, start, 3, {(0, 0, 0): {"block": "x"}})
        assert search.advance().reason == "body plan pending"
        search.cancel()
    assert transfer.frames == []
    assert [p["terrain"] for p in sent] == ["live", "live"]
    assert sent[0]["terrain_radius"] == [64, 48]
    assert "map_generation" not in sent[0]


def test_live_radius_is_a_plain_setting(monkeypatch):
    c, sent = controller()
    c.memory = live_memory()
    c.planner_map = CountingTransfer()
    monkeypatch.setattr(body_search, "LIVE_TERRAIN_RADIUS", (80, 32))
    body_search.BodySearch(c, (0, 1, 0), 3, {})
    assert sent[0]["terrain_radius"] == [80, 32]


def test_occluded_current_and_historyless_sessions_keep_the_upload():
    for memory in (
        live_memory(preset="visible"),
        live_memory(mode="current"),
        live_memory(historical=False),
    ):
        c, sent = controller()
        c.memory = memory
        c.planner_map = transfer = CountingTransfer()
        search = body_search.BodySearch(c, (0, 1, 0), 3, {})
        search.advance()
        assert transfer.frames[0] == ("begin", search)
        assert "terrain" not in sent[0] and sent[0]["map_generation"] == 1


def test_edit_payload_picks_tool_by_tool_choice():
    held = [
        {"slot": i, "item": f"minecraft:{t}_pickaxe", "count": 1}
        for i, t in enumerate(("wooden", "stone", "iron"))
    ]
    low = body_search.edit_payload(PROD, held, ())
    best = body_search.edit_payload(PROD, held, (), "best")
    assert low["tools"]["minecraft:stone"] == "minecraft:wooden_pickaxe"
    assert low["tools"]["minecraft:iron_ore"] == "minecraft:stone_pickaxe"
    assert best["tools"]["minecraft:stone"] == "minecraft:iron_pickaxe"
    # priced at the chosen tier: wood is slower than iron
    assert (
        low["break_costs"]["minecraft:stone"] > best["break_costs"]["minecraft:stone"]
    )
    assert "tools" not in body_search.edit_payload(PROD, [], ())


def test_boxed_in_dead_plan_lifts_the_bans_from_its_start_cell():
    # run 20261113T172130173999Z: a pit whose every exit was banned, expanded=2
    c, _ = controller()
    c.events = []
    c.failed_edges.add(((9, 1, 0), (10, 1, 0)))
    search = body_search.BodySearch(c, (5, 1, 0), 3)
    c.plan_reports[search.plan_id] = {
        "state": "done", "status": "unreachable", "route": [[5, 1, 0]], "expanded": 2,
    }
    search.advance()
    assert c.failed_edges == {((9, 1, 0), (10, 1, 0))}  # only the start cell's exits
    assert c.events[0][0] == "failed_edges_cleared"


def test_predicted_cells_apply_planned_breaks_and_places():
    from mclab.body_search import predicted_cells
    from mclab.terrain_steps import StepPlan

    base = {(1, 1, 0): {"block": "minecraft:stone"}}
    step = StepPlan((0, 1, 0), (1, 1, 0), breaks=((1, 1, 0),), places=((1, 0, 0),))
    cells = predicted_cells(base, [step])
    assert cells[(1, 1, 0)]["collision_boxes"] == []
    assert cells[(1, 0, 0)]["collision_boxes"] == [[0, 0, 0, 1, 1, 1]]
    assert base[(1, 1, 0)]["block"] == "minecraft:stone"  # the original is untouched


def test_zero_place_supply_sends_no_place_edges_but_keeps_breaks():
    edits = body_search.edit_payload(PROD, [], ())
    assert edits["place_supply"] == 0 and edits["materials"] == []
    assert not (edits["allow_place"] or edits["allow_bridge"] or edits["allow_pillar"])
    assert edits["break_costs"]  # a bot with nothing to place can still dig out
    held = body_search.edit_payload(PROD, KIT, ())
    assert held["allow_place"] and held["allow_pillar"] and held["allow_bridge"]


def test_template_cells_are_protected_in_the_payload():
    policy = replace(PROD, protected_positions=((4, 1, 4),))
    assert [4, 1, 4] in body_search.edit_payload(policy, KIT, ())["protected"]


def test_edit_payload_sends_costly_build_cells():
    from mclab.terrain_steps import BUILD_BREAK_COST

    edits = body_search.edit_payload(
        replace(PROD, costly_positions=((2, 1, 0), (1, 1, 0))), [], ()
    )
    assert edits["costly"] == [[1, 1, 0, BUILD_BREAK_COST], [2, 1, 0, BUILD_BREAK_COST]]
    assert body_search.edit_payload(PROD, [], ())["costly"] == []


def test_scaffolding_is_a_separate_supply_and_a_scaffold_plan_cell():
    inv = [{"item": "minecraft:scaffolding", "count": 5}]
    policy = replace(PROD, scaffold_materials=("minecraft:scaffolding",))
    sent = body_search.edit_payload(policy, inv, ())
    assert sent["scaffold_supply"] == 5 and sent["place_supply"] > 0
    assert "scaffold_supply" not in body_search.edit_payload(PROD, inv, ())
    plan = body_search.step_plan(
        {"source": [0, 1, 0], "target": [0, 2, 0], "breaks": [],
         "places": [[0, 1, 0]], "scaffold": [[0, 1, 0]], "kind": "pillar"}
    )
    assert plan.scaffold == ((0, 1, 0),)


def test_predicted_cells_marks_scaffold_cells_as_scaffolding():
    from mclab.body_search import predicted_cells
    from mclab.pathing_grid import PathingGrid
    from mclab.terrain_steps import StepPlan

    step = StepPlan(
        (0, 1, 0),
        (1, 2, 0),
        places=((1, 0, 0), (1, 1, 0)),
        kind="step",
        scaffold=((1, 1, 0),),
    )
    cells = predicted_cells({}, [step])
    assert cells[(1, 0, 0)]["block"] == "minecraft:cobblestone"
    assert cells[(1, 1, 0)]["block"] == "minecraft:scaffolding"
    assert cells[(1, 1, 0)]["collision_boxes"] == []
    grid = PathingGrid()
    grid_cells = predicted_cells(grid.snapshot(), [step])
    assert grid_cells[(1, 0, 0)]["block"] == "minecraft:cobblestone"
    assert grid_cells[(1, 1, 0)]["block"] == "minecraft:scaffolding"
