import pytest

from mclab import control_loop
from mclab.control_loop import drive, predicted
from mclab.terrain_transport import ExpandedTerrain


class FakeServer:
    """Applies scheduled controls at their apply tick, like BridgeRuntime."""

    run_id, episode_id, body_id = "r", "e", "b"

    def __init__(self, ticks, *, late=(), reject=None, drop=False, dies=None):
        self.ticks = list(ticks)
        self.index = -1
        self.events, self.sent, self.records = [], [], []
        self.sequence = 0
        self.scheduled = {}
        self.yaw = self.pitch = 0.0
        self.yaws = []
        self.late, self.reject, self.drop = set(late), reject or {}, drop
        self.dies = dies
        self.last_controller_error = None

    def _event(self, kind, **data):
        self.events.append(
            {
                "type": kind,
                "run_id": "r",
                "episode_id": "e",
                "body_id": "b",
                "data": data,
            }
        )

    def state(self):
        """/state: advance one poll; apply due commands like the mod."""
        self.index += 1
        self.polls = getattr(self, "polls", 0) + 1
        if self.index >= len(self.ticks):
            if self.index == len(self.ticks):
                self._event("course_episode_complete", status="success")
            return {"tick": self.ticks[-1]}
        tick = self.ticks[self.index]
        if self.dies is not None and tick == self.dies + 1:
            # The terminal event lands one poll after the rejection.
            self._event("course_episode_complete", status="failed", reason="death")
            self.ticks = self.ticks[: self.index + 1]
            return {"tick": tick}
        if self.dies is not None and tick == self.dies:
            for sequence in sorted(self.scheduled):
                self._event(
                    "rejected_at_execution",
                    request_sequence=sequence,
                    reason="body_not_registered",
                )
            self.scheduled.clear()
            return {"tick": tick}
        for sequence, c in sorted(self.scheduled.items()):
            if c["intended_apply_tick"] > tick:
                continue
            del self.scheduled[sequence]
            if c["intended_apply_tick"] != tick:
                self._event(
                    "rejected_at_execution",
                    request_sequence=sequence,
                    reason="stale_or_mistimed_action",
                )
                continue
            self.yaw += c["yaw_delta"]
            self.pitch += c["pitch_delta"]
            self._event("applied", request_sequence=sequence, action_id=c["action_id"])
        self.yaws.append(self.yaw)
        return {"tick": tick}

    def policy(self):
        """/policy: the current observation; never advances."""
        self.policy_reads = getattr(self, "policy_reads", 0) + 1
        if self.index >= len(self.ticks):
            return {"state": "episode_terminal"}
        return {
            "state": "running",
            "tick": self.ticks[self.index],
            "body": {"yaw": self.yaw, "pitch": self.pitch},
        }

    def send(self, op, motor=False, **fields):
        self.sequence += 1
        self.sent.append((op, fields))
        if op == "controls":
            c = fields["controls"]
            assert c["expires_tick"] == c["intended_apply_tick"] + c["duration"]
            assert all(
                s["intended_apply_tick"] != c["intended_apply_tick"]
                for s in self.scheduled.values()
            ), "one_action_per_apply_tick"
            if c["action_id"] in self.late:
                self._event(
                    "rejected",
                    request_sequence=self.sequence,
                    reason="late_or_distant_action",
                )
            elif c["action_id"] in self.reject:
                self._event(
                    "rejected",
                    request_sequence=self.sequence,
                    reason=self.reject[c["action_id"]],
                )
            elif not self.drop:
                self.scheduled[self.sequence] = c
        elif op == "release":
            self.scheduled.clear()
        elif op == "course_end":
            self._event("course_episode_complete", status="failed")
        return {"sequence": self.sequence}

    def record(self, kind, payload):
        self.records.append((kind, payload))

    def acknowledge(self, *_args, **_kwargs):
        pass

    def wait(self, predicate, **_kwargs):
        for _ in range(50):  # Like Bridge.wait: poll /state until it holds.
            if predicate({}):
                return
            self.state()
        raise TimeoutError("fake wait")

    def controls(self):
        return [f["controls"] for op, f in self.sent if op == "controls"]

    def summary(self):
        return next(p for k, p in self.records if k == "control_loop_summary")


class Turner:
    """Turns toward yaw 90 from whatever orientation it is shown."""

    pipeline_safe = True

    def __init__(self, extra=None):
        self.seen = []
        self.extra = extra or {}

    def decide(self, observation):
        self.seen.append((observation["tick"], observation["body"]["yaw"]))
        return {
            "yaw_delta": max(-45, min(45, 90 - observation["body"]["yaw"])),
            **self.extra,
        }

    def drain_events(self):
        return []


class Interactor(Turner):
    pipeline_safe = False


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    monkeypatch.setattr(control_loop, "policy_state", lambda client: client.policy())
    monkeypatch.setattr(control_loop.time, "sleep", lambda _: None)


def pipelined(server, controller, **kwargs):
    return drive(server, controller, 30, profile=control_loop.PIPELINED, **kwargs)


def test_decides_every_new_tick_with_bounded_in_flight_commands():
    server = FakeServer([10, 10, *range(11, 30)])
    assert pipelined(server, Turner())["data"]["status"] == "success"
    sent = server.controls()
    assert [c["observation_tick"] for c in sent] == list(range(10, 30))
    assert [c["intended_apply_tick"] for c in sent] == list(range(13, 33))
    assert all(c["duration"] == 5 for c in sent)
    assert len({c["action_id"] for c in sent}) == len(sent)
    assert sent[0]["action_id"] == "action-1"
    assert server.summary()["peak_in_flight"] <= 3
    assert server.summary()["decisions"] == 20


def test_unapplied_turns_are_predicted_so_they_never_compound():
    server = FakeServer(range(10, 30))
    pipelined(server, Turner())
    # Without prediction three in-flight 45-degree turns would reach 135+.
    assert max(server.yaws) == 90 and server.yaws[-1] == 90


def test_in_flight_bound_limits_outstanding_commands():
    server = FakeServer(range(10, 40))
    drive_kwargs = {"max_in_flight": 1}
    control_loop.drive_pipelined(server, Turner(), 30, **drive_kwargs)
    ticks = [c["observation_tick"] for c in server.controls()]
    assert all(b - a >= 3 for a, b in zip(ticks, ticks[1:], strict=False))
    with pytest.raises(ValueError):
        control_loop.drive_pipelined(server, Turner(), 30, max_in_flight=4)


def test_unsafe_controller_sees_each_result_before_deciding_again():
    server = FakeServer(range(10, 40))
    controller = Interactor()
    pipelined(server, controller)
    sent = server.controls()
    seen_ticks = [tick for tick, _ in controller.seen]
    for command, tick in zip(sent, seen_ticks[1:], strict=False):
        assert tick >= command["intended_apply_tick"]
    assert server.summary()["barrier_decisions"] == len(sent)


def test_late_commands_are_discarded_not_retried_or_released():
    server = FakeServer(range(10, 30), late={"action-2", "action-5"})
    assert pipelined(server, Turner())["data"]["status"] == "success"
    discarded = [p for k, p in server.records if k == "discarded_stale_action"]
    assert [p["count"] for p in discarded] == [1, 2]
    assert all(p["retried"] is False for p in discarded)
    assert "release" not in [op for op, _ in server.sent]
    assert len({c["action_id"] for c in server.controls()}) == len(server.controls())


@pytest.mark.parametrize(
    "late,reject",
    [
        ({f"action-{i}" for i in range(1, 11)}, None),  # limit 3 x 3 in flight
        ((), {"action-3": "invalid_controls"}),
    ],
)
def test_failure_releases_and_no_command_follows_the_release(late, reject):
    server = FakeServer(range(10, 60), late=late, reject=reject)
    assert pipelined(server, Turner())["data"]["status"] == "failed"
    ops = [op for op, _ in server.sent]
    assert ops[-2:] == ["release", "course_end"]
    assert "controls" not in ops[ops.index("release") :]
    assert not server.scheduled
    assert server.last_controller_error


def test_missing_acknowledgement_times_out_and_releases(monkeypatch):
    monkeypatch.setattr(control_loop, "ACK_TIMEOUT_S", -1)
    server = FakeServer(range(10, 30), drop=True)
    assert pipelined(server, Turner())["data"]["status"] == "failed"
    assert "acknowledgement" in server.last_controller_error
    assert [op for op, _ in server.sent][-2:] == ["release", "course_end"]


def test_prediction_keeps_the_observation_type_and_skips_applied_turns():
    observation = ExpandedTerrain(
        {"tick": 10, "body": {"yaw": 0.0, "pitch": 80.0}}, {(0, 0, 0): {}}
    )
    in_flight = {1: (10, 30, 5), 2: (12, 15, 20)}  # The first already applied.
    result = predicted(observation, in_flight)
    assert isinstance(result, ExpandedTerrain)
    assert (0, 0, 0) in result.cell_index
    assert result["body"]["yaw"] == 15 and result["body"]["pitch"] == 90
    assert observation["body"]["yaw"] == 0  # The original is not changed.
    assert predicted(observation, {}) is observation


def test_lockstep_profile_still_waits_for_each_ack():
    server = FakeServer(range(10, 20))

    def wait(predicate, **_kwargs):
        # Lockstep observes once per ack wait; emulate the tick passing.
        while not predicate({}):
            server.state()

    server.wait = wait
    drive(server, Interactor(), 30)
    sent = server.controls()
    assert sent and all(
        c["intended_apply_tick"] == c["observation_tick"] + 3 for c in sent
    )
    assert sent[0]["action_id"] == "action-1"
    with pytest.raises(ValueError):
        drive(server, Interactor(), 30, profile="unknown")


def test_segmented_controller_pipelines_only_plain_walking(tmp_path):
    from test_spatial_memory import identity

    from mclab.movement_policy import MovementPolicy
    from mclab.navigation_segmented import SegmentedController
    from mclab.spatial_memory import MapArchive, SpatialMemory

    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "s")
    controller = SegmentedController((3, 1, 0), memory, radius=0)
    try:
        assert controller.pipeline_safe
        controller.committed_step = (1, 1, 0)
        assert not controller.pipeline_safe
        controller.committed_step = None
        controller.air_waypoint = [1.5, 2, 0.5]  # Mid step-up: jump in progress.
        assert not controller.pipeline_safe
        controller.air_waypoint = None
        controller.terrain_queue = [object()]
        assert not controller.pipeline_safe
        controller.terrain_queue = []
        controller.mode = "gap_runway"
        assert not controller.pipeline_safe
        controller.mode = "following"
        controller.movement_policy = MovementPolicy(allow_swim=True)
        assert not controller.pipeline_safe
    finally:
        controller.close()
        memory.close()


def test_policy_is_fetched_once_per_new_tick():
    server = FakeServer([10, 10, 10, 11, 11, 12, 13])
    pipelined(server, Turner())
    assert server.policy_reads == len({10, 11, 12, 13})


def test_tick_regression_never_sends_a_duplicate_apply_tick():
    server = FakeServer([10, 11, 10, 12])
    pipelined(server, Turner())
    ticks = [c["observation_tick"] for c in server.controls()]
    assert ticks == [10, 11, 12]


@pytest.mark.parametrize("name", control_loop.INTERACTIONS)
def test_jumps_and_interactions_land_before_the_next_decision(name):
    server = FakeServer(range(10, 40))
    pipelined(server, Turner({name: True}))
    sent = server.controls()
    assert all(
        b["observation_tick"] >= a["intended_apply_tick"]
        for a, b in zip(sent, sent[1:], strict=False)
    )
    assert server.summary()["barrier_decisions"] == len(sent)


def test_summary_is_recorded_when_decide_raises():
    class Broken(Turner):
        def decide(self, observation):
            raise RuntimeError("controller bug")

    server = FakeServer(range(10, 20))
    with pytest.raises(RuntimeError, match="controller bug"):
        pipelined(server, Broken())
    assert server.summary()["decisions"] == 0


def test_processing_trace_is_rejected_in_pipelined_mode():
    with pytest.raises(ValueError, match="lockstep"):
        drive(
            FakeServer([10]),
            Turner(),
            30,
            trace_processing=True,
            profile=control_loop.PIPELINED,
        )


def test_death_race_returns_the_terminal_outcome_not_a_controller_error():
    server = FakeServer(range(10, 40), dies=20)
    terminal = pipelined(server, Turner())
    assert terminal["data"]["reason"] == "death"
    assert server.last_controller_error is None
    assert "release" not in [op for op, _ in server.sent]
    assert [k for k, _ in server.records].count("rejection_at_episode_end") >= 1
