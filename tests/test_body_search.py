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
    assert "minecraft:oak_log" not in edits["break_costs"]  # no axe on the hotbar
    assert (
        edits["fall_costs"]["minecraft:sand"] == 3 + 2 + 17
    )  # 3 ticks with an iron shovel
    assert edits["place_supply"] == 2  # two cobblestone in the hotbar
    assert edits["protected"] == [[3, 1, 0]]
    no_break = body_search.edit_payload(replace(PROD, allow_break=False), KIT, ())
    assert no_break["break_costs"] == {}


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
