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
        movement_policy=MOVES,
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


def test_only_policies_the_java_core_implements_use_it():
    assert body_search.supported(PROD, MOVES)
    assert not body_search.supported(replace(PROD, hazard_penalty=40.0), MOVES)
    assert not body_search.supported(PROD, MOVES, interact_reach=4.5)
    assert not body_search.supported(replace(PROD, baritone_search=False), MOVES)
    assert not body_search.supported(None, MOVES)
