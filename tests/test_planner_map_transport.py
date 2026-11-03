import base64
import threading
import zlib
from types import SimpleNamespace

import pytest

from mclab.pathing_grid import PathingGrid
from mclab.planner_map_transport import (
    PlannerMapCoordinator,
    PlannerMapEncoder,
    PlannerMapTransfer,
)


class Wire:
    def __init__(self):
        self.frames = []
        self.generation = 0

    def send(self, frame):
        self.frames.append(frame)
        if frame["kind"] == "commit":
            self.generation = frame["generation"]
        elif frame["kind"] == "reset":
            self.generation = 0
        return self.generation


def test_snapshot_deltas_keep_unknown_distinct_and_publish_only_after_ack():
    encoder = PlannerMapEncoder()
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    first = encoder.prepare({(-1, 0, 0): air})
    assert first[0] == {"kind": "begin", "base": 0, "generation": 1}
    section = next(
        frame
        for batch in first
        if batch["kind"] == "batch"
        for frame in batch["frames"]
        if frame["kind"] == "section"
    )
    states = zlib.decompress(base64.b64decode(section["states"]))
    assert (
        len(states) == 8192 and states[(15 << 8) * 2 : (15 << 8) * 2 + 2] == b"\x01\x00"
    )
    assert states[(15 << 8) * 2 + 2 : (15 << 8) * 2 + 4] == b"\x00\x00"
    with pytest.raises(RuntimeError):
        encoder.prepare({})
    encoder.acknowledge(1)
    assert [f["kind"] for f in encoder.prepare({(-1, 0, 0): air})] == [
        "begin",
        "commit",
    ]
    encoder.abort()
    removed = encoder.prepare({})
    assert [f["kind"] for f in removed] == ["begin", "batch", "commit"]
    assert removed[1]["frames"][0]["kind"] == "remove"
    assert removed[0]["base"] == 1


def test_frozen_grid_reuses_unchanged_sections_and_sends_only_changed_section():
    grid = PathingGrid()
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    stone = {
        "block": "minecraft:stone",
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }
    air_id, stone_id = grid.state_id(air), grid.state_id(stone)
    grid.put((0, 0, 0), air_id, 0)
    grid.put((16, 0, 0), air_id, 0)
    encoder = PlannerMapEncoder()
    encoder.prepare(grid.snapshot())
    encoder.acknowledge(1)
    grid.put((16, 0, 1), stone_id, 0)
    frames = encoder.prepare(grid.snapshot())
    delta = [f for batch in frames if batch["kind"] == "batch" for f in batch["frames"]]
    assert [f["section"] for f in delta if f["kind"] == "section"] == [[1, 0, 0]]


def test_transfer_acknowledges_commit_and_cancel_aborts_partial_generation():
    wire = Wire()
    frames = wire.frames
    transfer = PlannerMapTransfer(wire.send)
    air = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
    try:
        transfer.start({(0, 0, 0): air})
        transfer.future.result(timeout=2)
        assert transfer.advance() is None
        assert frames[-1]["kind"] == "begin"
        transfer.cancel()
        assert frames[-1] == {"kind": "abort"}
        assert transfer.encoder.generation == 0
        transfer.start({(0, 0, 0): air})
        transfer.future.result(timeout=2)
        result = None
        while result is None:
            result = transfer.advance()
        assert result == 1 and transfer.encoder.generation == 1
        assert frames[-1] == {"kind": "commit", "generation": 1}
    finally:
        transfer.close()


def test_transfer_exposes_failed_acknowledgement_and_aborts():
    frames = []

    def send(frame):
        frames.append(frame)
        if frame["kind"] == "begin":
            raise TimeoutError("acknowledgement lost")
        return 0

    transfer = PlannerMapTransfer(send)
    try:
        transfer.start({})
        transfer.future.result(timeout=2)
        with pytest.raises(TimeoutError, match="acknowledgement lost"):
            transfer.advance()
        transfer.cancel()
        assert frames[-1] == {"kind": "abort"}
        assert transfer.encoder.generation == 0
    finally:
        transfer.close()


def test_superseded_transfer_aborts_without_publishing_partial_map():
    wire = Wire()
    frames = wire.frames
    coordinator = PlannerMapCoordinator(wire.send)
    first, second = SimpleNamespace(superseded=False), SimpleNamespace(superseded=False)
    try:
        coordinator.begin(first, {})
        coordinator.transfer.future.result(timeout=2)
        assert coordinator.advance(first) is None
        coordinator.begin(second, {})
        assert first.superseded and frames[-1] == {"kind": "abort"}
        coordinator.transfer.future.result(timeout=2)
        assert coordinator.advance(second) is None
        assert coordinator.advance(second) == 1
        assert coordinator.transfer.encoder.generation == 1
    finally:
        coordinator.close()


def test_encoding_runs_off_thread_but_all_wire_frames_are_sent_by_caller(monkeypatch):
    caller = threading.get_ident()
    encoder_threads, wire_threads = [], []
    original = PlannerMapEncoder.prepare

    def prepare(self, cells):
        encoder_threads.append(threading.get_ident())
        return original(self, cells)

    monkeypatch.setattr(PlannerMapEncoder, "prepare", prepare)
    wire = Wire()

    def send(frame):
        wire_threads.append(threading.get_ident())
        return wire.send(frame)

    transfer = PlannerMapTransfer(send)
    try:
        transfer.start({})
        transfer.future.result(timeout=2)
        while transfer.advance() is None:
            pass
        assert encoder_threads and encoder_threads[0] != caller
        assert wire_threads and set(wire_threads) == {caller}
    finally:
        transfer.close()


def test_lost_commit_ack_is_reconciled_against_body_generation():
    frames = []
    generation = 0

    def send(frame):
        nonlocal generation
        frames.append(frame)
        if frame["kind"] == "commit":
            generation = frame["generation"]
            raise TimeoutError("lost commit ack")
        return generation

    transfer = PlannerMapTransfer(send)
    try:
        transfer.start({})
        transfer.future.result(timeout=2)
        assert transfer.advance() is None
        assert transfer.advance() == 1
        assert transfer.recovered and transfer.encoder.generation == 1
        assert frames[-1] == {"kind": "status"}
    finally:
        transfer.close()


def test_lost_batch_ack_aborts_uncommitted_generation():
    frames = []

    def send(frame):
        frames.append(frame)
        if frame["kind"] == "batch":
            raise TimeoutError("lost batch ack")
        return 0

    transfer = PlannerMapTransfer(send)
    try:
        transfer.start({(0, 0, 0): {"block": "minecraft:air", "collision_boxes": []}})
        transfer.future.result(timeout=2)
        assert transfer.advance() is None
        with pytest.raises(TimeoutError, match="lost batch ack"):
            transfer.advance()
        assert frames[-2:] == [{"kind": "status"}, {"kind": "abort"}]
        assert transfer.encoder.generation == 0 and transfer.future is None
    finally:
        transfer.close()


def test_unavailable_status_requires_explicit_reset_before_retry():
    frames = []
    failures = {"begin", "status"}

    def send(frame):
        frames.append(frame)
        if frame["kind"] in failures:
            raise TimeoutError(frame["kind"])
        return frame["generation"] if frame["kind"] == "commit" else 0

    coordinator = PlannerMapCoordinator(send)
    first = SimpleNamespace(superseded=False)
    second = SimpleNamespace(superseded=False)
    try:
        coordinator.begin(first, {})
        coordinator.transfer.future.result(timeout=2)
        with pytest.raises(TimeoutError, match="begin"):
            coordinator.advance(first)
        failures.clear()
        coordinator.cancel(first)
        assert coordinator.transfer.uncertain
        coordinator.begin(second, {})
        assert frames[-1] == {"kind": "reset"}
        coordinator.transfer.future.result(timeout=2)
        assert coordinator.advance(second) is None
        assert coordinator.advance(second) == 1
    finally:
        coordinator.close()


def test_wrong_acknowledgement_generation_fails_closed():
    frames = []

    def send(frame):
        frames.append(frame)
        return 99

    transfer = PlannerMapTransfer(send)
    try:
        transfer.start({})
        transfer.future.result(timeout=2)
        with pytest.raises(RuntimeError, match="generation mismatch"):
            transfer.advance()
        assert transfer.uncertain and transfer.encoder.generation == 0
        with pytest.raises(RuntimeError, match="abort generation mismatch"):
            transfer.cancel()
        assert frames[-1] == {"kind": "abort"}
    finally:
        transfer.close()
