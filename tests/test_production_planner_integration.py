import time

import pytest
from test_background_navigation import observation
from test_route_search import floor_map
from test_spatial_memory import identity

from mclab import control_loop, planner_session
from mclab.navigation_segmented import SegmentedController
from mclab.spatial_memory import MapArchive, SpatialMemory
from mclab.terrain_steps import TerrainPolicy


class BodyFixture:
    # Stages map frames and publishes reports explicitly.

    run_id, episode_id, body_id = "run", "episode", "body"

    def __init__(self):
        self.generation = 0
        self.staging = None
        self.sent = []
        self.plans = []
        self.reports = []

    def send(self, op, **fields):
        self.sent.append((op, fields))
        if op == "planner_map":
            kind = fields["kind"]
            if kind == "begin":
                assert self.staging is None
                self.staging = fields["generation"]
                assert self.staging == self.generation + 1
            elif kind in ("palette", "section", "batch"):
                assert self.staging is not None
            elif kind == "commit":
                assert fields["generation"] == self.staging
                self.generation, self.staging = self.staging, None
            elif kind == "abort":
                self.staging = None
            elif kind == "reset":
                self.generation, self.staging = 0, None
            else:
                assert kind == "status"
        elif op == "plan_route":
            assert self.staging is None
            assert fields["map_generation"] == self.generation > 0
            self.plans.append(fields)
        return {"op": op, "fields": fields}

    def acknowledge(self, request, expected):
        kinds = {
            "planner_map": "planner_map_accepted",
            "plan_route": "plan_accepted",
            "cancel_plan": "plan_cancelled",
            "route_policy": "route_policy_accepted",
        }
        assert kinds[request["op"]] == expected
        return {"data": {"generation": self.generation}}

    def report(self, plan, status, route, steps=()):
        self.reports = [
            {
                "plan_id": plan["plan_id"],
                "state": "done",
                "status": status,
                "route": route,
                "steps": list(steps),
                "expanded": 21,
            }
        ]


@pytest.fixture
def setup_controller(tmp_path):
    objects = []

    def make(goal=(6, 1, 0), **kwargs):
        cells = floor_map([(x, z) for x in range(-1, 43) for z in (-1, 0, 1)])
        memory = SpatialMemory(
            MapArchive(tmp_path / f"{len(objects)}.db", identity()), "session"
        )
        controller = SegmentedController(goal, memory, radius=0, **kwargs)
        body = BodyFixture()
        planner_session.attach(body, controller)
        objects.append((controller, memory, body))
        return controller, body, cells

    yield make
    for controller, memory, body in objects:
        planner_session.detach(controller)
        controller.close()
        body.planner_map_coordinator.close()
        memory.close()


def poll(controller, body, cells, predicate, *, x=0.5, native=False):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        obs = observation(cells, x=x, tick=1)
        obs["route_planner"] = list(body.reports)
        controller.decide(obs, native=native)
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError(
        f"controller timeout: {controller.mode}, {controller.reason}, {body.sent[-4:]}"
    )


@pytest.mark.parametrize("native", [False, True])
def test_map_ack_report_and_route_commit(setup_controller, native):
    controller, body, cells = setup_controller()
    poll(controller, body, cells, lambda: len(body.plans) == 1, native=native)
    commit = next(
        i
        for i, (op, f) in enumerate(body.sent)
        if op == "planner_map" and f["kind"] == "commit"
    )
    submit = next(i for i, (op, _) in enumerate(body.sent) if op == "plan_route")
    assert commit < submit
    body.report(body.plans[0], "reached", [[x, 1, 0] for x in range(7)])
    poll(controller, body, cells, lambda: controller.mode == "following", native=native)
    assert controller.route == [(x, 1, 0) for x in range(7)]
    assert controller.last_result.expanded == 21


def test_prefetch_adopted_after_movement(setup_controller):
    controller, body, cells = setup_controller(
        goal=(40, 1, 0), background_planning=True
    )
    poll(controller, body, cells, lambda: len(body.plans) == 1, native=True)
    body.report(body.plans[0], "reached", [[x, 1, 0] for x in range(41)])
    poll(controller, body, cells, lambda: controller.prefetch is not None, native=True)
    assert controller.prefetch.start == (15, 1, 0)
    # Native driver advances the prefetch while its current route is moving.
    deadline = time.monotonic() + 5
    while len(body.plans) < 2 and time.monotonic() < deadline:
        controller.prefetch.advance()
        time.sleep(0.002)
    assert len(body.plans) == 2
    body.report(body.plans[1], "reached", [[x, 1, 0] for x in range(15, 41)])
    poll(
        controller,
        body,
        cells,
        lambda: controller.prefetch_adopted == 1 and controller.mode == "following",
        x=15.5,
        native=True,
    )
    assert controller.route[0] == (15, 1, 0)
    assert any(
        kind == "route_prefetch_adopted" for kind, _ in controller.drain_events()
    )


def test_cancel_old_goal_cannot_adopt_old_report(setup_controller):
    controller, body, cells = setup_controller()
    poll(controller, body, cells, lambda: len(body.plans) == 1, native=True)
    old = body.plans[0]
    controller.replace_goal((3, 1, 0))
    assert ("cancel_plan", {"plan_id": old["plan_id"]}) in body.sent
    body.report(old, "reached", [[x, 1, 0] for x in range(7)])
    poll(controller, body, cells, lambda: len(body.plans) == 2, native=True)
    assert controller.mode != "following"
    body.report(body.plans[1], "reached", [[x, 1, 0] for x in range(4)])
    poll(controller, body, cells, lambda: controller.mode == "following", native=True)
    assert controller.route[-1] == (3, 1, 0)


def test_unreachable_starts_terrain_forecast_with_inventory_and_protection(
    setup_controller,
):
    controller, body, cells = setup_controller(terrain_policy=TerrainPolicy())
    controller.own_footholds.add((2, 0, 0))
    inventory = [{"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1}]

    def run_until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            obs = observation(cells)
            obs["body"]["inventory"] = inventory
            obs["route_planner"] = list(body.reports)
            controller.decide(obs, native=True)
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError((controller.mode, controller.reason))

    run_until(lambda: len(body.plans) == 1)
    body.report(body.plans[0], "unreachable", [[0, 1, 0]])
    run_until(lambda: len(body.plans) == 2)
    terrain = body.plans[1]
    assert terrain["mode"] == "terrain"
    assert terrain["inventory"] == inventory
    assert terrain["protected"] == [[2, 0, 0]]
    step = {
        "source": [0, 1, 0],
        "target": [1, 1, 0],
        "breaks": [],
        "places": [],
        "kind": "step",
    }
    body.report(terrain, "segment", [[0, 1, 0], [1, 1, 0]], [step])
    run_until(lambda: bool(controller.terrain_forecast))
    assert controller.terrain_forecast[0].target == (1, 1, 0)


@pytest.mark.parametrize(
    "profile", [control_loop.LOCKSTEP, control_loop.PIPELINED, control_loop.NATIVE]
)
def test_control_profile_drives_production_planner(
    setup_controller, monkeypatch, profile
):
    from mclab import native_driver

    controller, body, cells = setup_controller()
    planner_session.detach(controller)
    body.events = []
    body.tick = 0
    body.records = []
    original_send = body.send
    original_ack = body.acknowledge

    def send(op, **fields):
        if op in ("controls", "follow_route", "cancel_route"):
            body.sent.append((op, fields))
            sequence = len(body.sent)
            if op == "controls":
                body.events.append(
                    {"type": "applied", "data": {"request_sequence": sequence}}
                )
            return {"op": op, "fields": fields, "sequence": sequence}
        request = original_send(op, **fields)
        if op == "plan_route":
            body.report(body.plans[-1], "reached", [[x, 1, 0] for x in range(7)])
        return request

    def acknowledge(request, expected):
        if request["op"] in ("follow_route", "cancel_route"):
            assert (
                expected
                == {
                    "follow_route": "route_accepted",
                    "cancel_route": "route_cancelled",
                }[request["op"]]
            )
            return {}
        return original_ack(request, expected)

    def state():
        body.tick += 1
        return {"tick": body.tick}

    def policy(_client):
        if profile != control_loop.PIPELINED:
            body.tick += 1
        obs = observation(cells, tick=body.tick)
        obs["route_planner"] = list(body.reports)
        return obs

    def running(_client, _observation):
        complete = (
            any(op == "follow_route" for op, _ in body.sent)
            if profile == control_loop.NATIVE
            else controller.mode == "following"
        )
        return {"type": "test_route_committed"} if complete else None

    body.send = send
    body.acknowledge = acknowledge
    body.state = state
    body.record = lambda kind, data: body.records.append((kind, data))
    body.wait = lambda predicate, **kwargs: None
    monkeypatch.setattr(control_loop, "policy_state", policy)
    monkeypatch.setattr(native_driver, "policy_state", policy)
    monkeypatch.setattr(control_loop, "_running", running)
    result = control_loop.drive(body, controller, wall_timeout=5, profile=profile)
    assert result["type"] == "test_route_committed"
    assert body.plans and body.plans[0]["map_generation"] == 1
    assert controller.route[-1] == (6, 1, 0)
    if profile == control_loop.NATIVE:
        assert any(op == "follow_route" for op, _ in body.sent)
    else:
        assert any(op == "controls" for op, _ in body.sent)
    assert controller.planner_map is None
