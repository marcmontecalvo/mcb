"""Body-planner adapter (ROADMAP item 8, stage E)."""

from dataclasses import replace
from types import SimpleNamespace

from mclab import body_search
from mclab.movement_policy import MovementPolicy
from mclab.route_search import RouteResult
from mclab.terrain_steps import TerrainPolicy

PROD = TerrainPolicy(
    planner_tick_costs=True,
    planner_unknown=True,
    heuristic_weight=1,
    baritone_search=True,
    baritone_diagonals=True,
    baritone_moves=True,
    planner_swim=True,
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
        submit_plan=sent.append,
        _remaining_policy=lambda: PROD,
    )
    return c, sent


def test_payload_carries_policy_history_and_limits():
    c, sent = controller()
    search = body_search.BodySearch(c, (0, 1, 0), 3, fallback=None)
    payload = sent[0]
    assert payload["plan_id"] == search.plan_id
    assert payload["swim"] and payload["extra"] and payload["max_gap"] == 2
    assert payload["unknown_penalty"] == PROD.unknown_penalty
    assert payload["favored"] == [[0, 1, 0], [1, 1, 0]]
    assert payload["excluded"] == [[[5, 1, 0], [6, 1, 0]]]
    assert search.advance().status == "searching"


def test_done_route_is_segmented_like_the_worker():
    c, _ = controller()
    search = body_search.BodySearch(c, (0, 1, 0), 3, fallback=None)
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


def test_unreachable_falls_back_to_the_python_search_with_edits():
    c, _ = controller()
    fallback = SimpleNamespace(
        token=7,
        advance=lambda **_: RouteResult("incomplete", ((0, 1, 0),), 3, 5, "py"),
    )
    search = body_search.BodySearch(c, (0, 1, 0), 3, fallback=lambda: fallback)
    c.plan_reports[search.plan_id] = {
        "plan_id": search.plan_id,
        "state": "done",
        "status": "unreachable",
        "route": [[0, 1, 0]],
        "expanded": 90,
    }
    assert search.advance().reason == "py"
    assert search.fallback is fallback and search.token == 7


def test_only_policies_the_java_core_implements_use_it():
    assert body_search.supported(PROD, MOVES)
    assert not body_search.supported(replace(PROD, hazard_penalty=40.0), MOVES)
    assert not body_search.supported(PROD, MOVES, interact_reach=4.5)
    assert not body_search.supported(replace(PROD, baritone_search=False), MOVES)
    assert not body_search.supported(None, MOVES)
