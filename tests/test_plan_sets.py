"""Build-wide edit sets travel once per change, not inside every plan_route.

House-large run 20261119T133618892664Z: every plan_route carried the whole build
`protected` list (20.8-31 KB), so each one was framed into 3+ synchronous round
trips and blocked navigation ticks (~300 s over the run).
"""

import base64
import hashlib
import json
import zlib
from dataclasses import replace

import pytest
from test_body_search import PROD, controller

from mclab import body_search, plan_sets
from mclab.planner_session import submit_plan
from mclab.terrain_steps import BUILD_BREAK_COST

FRAME = 12_000


def _house(origin=(1184, 71, -823), size=(25, 12, 19)):
    """A house-large-sized shell: walls, floor and roof (thousands of cells)."""
    ox, oy, oz = origin
    sx, sy, sz = size
    cells = []
    for x in range(sx):
        for y in range(sy):
            for z in range(sz):
                if x in (0, sx - 1) or y in (0, sy - 1) or z in (0, sz - 1):
                    cells.append((ox + x, oy + y, oz + z))
                elif y == 5:  # an upper floor
                    cells.append((ox + x, oy + y, oz + z))
    return tuple(cells)


class Body:
    """The mod's side of the wire, modelled independently of mclab.plan_sets."""

    def __init__(self):
        self.calls = []
        self.sets = {}
        self.upload = None
        self.accepted = []
        self.reject_unknown = True

    def send(self, operation, **fields):
        self.calls.append((operation, fields))
        return {"sequence": len(self.calls), "op": operation, "fields": fields}

    def acknowledge(self, request, kind):
        op, fields = request["op"], request["fields"]
        if op == "planner_set":
            assert kind == "planner_set_accepted"
            if fields["kind"] == "begin":
                assert self.upload is None
                self.upload = dict(fields, data="")
            elif fields["kind"] == "part":
                assert fields["offset"] == len(self.upload["data"])
                self.upload["data"] += fields["data"]
            elif fields["kind"] == "commit":
                up, self.upload = self.upload, None
                raw = zlib.decompress(base64.b64decode(up["data"]))
                assert len(raw) == up["raw_length"]
                digest = hashlib.sha256(raw).hexdigest()
                assert digest == up["sha256"] == up["set_id"] == fields["set_id"]
                self.sets[digest] = json.loads(raw)
            elif fields["kind"] == "abort":
                self.upload = None
            return {"type": kind, "data": {}}
        if op == "plan_route":
            assert kind == "plan_accepted"
            plan = self._resolve(fields, request["sequence"])
            for part in ("edits", "policy"):
                if part in plan:
                    plan[part] = self._resolve(plan[part], request["sequence"])
            self.accepted.append(plan)
            return {"type": kind, "data": {}}
        assert op == "cancel_plan"
        return {"type": kind, "data": {}}

    def _resolve(self, obj, sequence):
        obj = dict(obj)
        for key in [k for k in obj if k.endswith("_set")]:
            field, ref = key.removesuffix("_set"), obj.pop(key)
            if ref not in self.sets:
                raise RuntimeError(
                    f"Bridge rejected request {sequence}: plan_set_unknown"
                )
            stored = self.sets[ref]
            assert stored["field"] == field
            obj[field] = stored["entries"] + obj.get(field, [])
        return obj

    def ops(self):
        return [op for op, _ in self.calls]


def _client(body):
    body.run_id, body.episode_id, body.body_id = "run", "episode", "body"
    return body


def _house_payload(extra_footholds=()):
    house = _house()
    roof = [p for p in house if p[1] == 71 + 11]
    c, sent = controller()
    policy = replace(PROD, protected_positions=house, costly_positions=tuple(roof))
    c.terrain_policy = policy
    c._remaining_policy = lambda: policy
    c.own_footholds = {(3, 1, 0), *extra_footholds}
    body_search.BodySearch(c, (0, 1, 0), 3)
    return sent[0], house


LISTS = ("protected", "costly", "protected_positions", "costly_positions")


def _same_plan(resolved, original):
    """The body resolves exactly the request Python built (order of cells aside)."""

    def norm(obj):
        obj = json.loads(json.dumps(obj))
        for part in (obj, obj.get("edits", {}), obj.get("policy", {})):
            for field in LISTS:
                if field in part:
                    part[field] = sorted(map(tuple, part[field]))
        return obj

    assert norm(resolved) == norm(original)


def _terrain_payload(extra_footholds=()):
    house = _house()
    c, sent = controller()
    policy = replace(PROD, protected_positions=house, costly_positions=house[:600])
    c.own_footholds = {(3, 1, 0), *extra_footholds}
    body_search.BodyTerrainSearch(c, (0, 1, 0), 3, None, policy, c.inventory)
    return sent[0], house


def _size(fields):
    return len(json.dumps(fields, separators=(",", ":")).encode("utf-8"))


def test_house_sized_plan_route_fits_one_frame_and_sets_upload_once():
    payload, house = _house_payload()
    assert len(house) > 1500
    assert _size(payload) > 20_000  # the problem: the whole build rode along
    body = _client(Body())
    submit_plan(body, payload)
    assert "planner_request" not in body.ops()  # no framed plan request any more
    plans = [f for op, f in body.calls if op == "plan_route"]
    assert len(plans) == 1 and _size(plans[0]) < FRAME // 2
    assert all(len(f.get("data", "")) <= 10_000 for _, f in body.calls)
    _same_plan(body.accepted[0], payload)

    # The next plan, one new foothold later: one frame, no upload, still exact.
    body.calls.clear()
    second, _ = _house_payload(extra_footholds=[(9, 1, 2)])
    submit_plan(body, second)
    assert body.ops() == ["plan_route"]
    assert _size(body.calls[0][1]) < FRAME // 2
    _same_plan(body.accepted[1], second)


def test_protected_cells_reach_the_planner_through_the_reference():
    payload, house = _house_payload()
    body = _client(Body())
    submit_plan(body, payload)
    protected = set(map(tuple, body.accepted[0]["edits"]["protected"]))
    assert set(house) <= protected and (3, 1, 0) in protected
    costly = body.accepted[0]["edits"]["costly"]
    assert costly and all(c[3] == BUILD_BREAK_COST for c in costly)


def test_a_removed_cell_uploads_a_new_set_rather_than_keep_protecting_it():
    payload, house = _house_payload()
    body = _client(Body())
    submit_plan(body, payload)
    smaller = json.loads(json.dumps(payload))
    gone = smaller["edits"]["protected"].pop(5)
    smaller["plan_id"] = "plan-next"
    body.calls.clear()
    submit_plan(body, smaller)
    assert "planner_set" in body.ops()
    resolved = set(map(tuple, body.accepted[1]["edits"]["protected"]))
    assert tuple(gone) not in resolved
    _same_plan(body.accepted[1], smaller)


def test_a_body_that_lost_the_set_gets_it_again_before_planning():
    payload, _ = _house_payload()
    body = _client(Body())
    submit_plan(body, payload)
    body.sets.clear()  # e.g. the mod respawned the body and dropped its store
    body.calls.clear()
    second, _ = _house_payload(extra_footholds=[(9, 1, 2)])
    submit_plan(body, second)
    assert body.ops()[0] == "plan_route" and "planner_set" in body.ops()
    assert body.ops()[-1] == "plan_route"
    _same_plan(body.accepted[-1], second)


def test_a_reference_the_body_never_resolves_fails_loudly():
    payload, _ = _house_payload()

    class Forgetful(Body):
        def acknowledge(self, request, kind):
            result = super().acknowledge(request, kind)
            self.sets.clear()  # never keeps anything
            return result

    body = _client(Forgetful())
    with pytest.raises(RuntimeError, match="plan_set_unknown"):
        submit_plan(body, payload)
    assert not body.accepted  # never planned without its protection
    assert body.ops()[-1] == "cancel_plan"


def test_a_new_body_identity_uploads_its_own_sets():
    payload, _ = _house_payload()
    body = _client(Body())
    submit_plan(body, payload)
    body.body_id = "body-2"
    body.sets.clear()
    body.calls.clear()
    submit_plan(body, payload)
    assert body.ops()[0] == "planner_set"  # no wasted rejected plan first
    _same_plan(body.accepted[-1], payload)


def test_small_requests_are_sent_unchanged():
    c, sent = controller()
    body_search.BodySearch(c, (0, 1, 0), 3)
    body = _client(Body())
    submit_plan(body, sent[0])
    assert body.ops() == ["plan_route"]
    assert body.calls[0][1] == sent[0]


def test_house_sized_terrain_plan_fits_one_frame_and_keeps_protection():
    payload, house = _terrain_payload()
    assert payload["mode"] == "terrain" and _size(payload) > 20_000
    body = _client(Body())
    submit_plan(body, payload)
    assert "planner_request" not in body.ops()
    plans = [f for op, f in body.calls if op == "plan_route"]
    assert len(plans) == 1 and _size(plans[0]) < FRAME // 2
    _same_plan(body.accepted[0], payload)
    protected = set(map(tuple, body.accepted[0]["policy"]["protected_positions"]))
    assert protected == set(house)

    body.calls.clear()
    second, _ = _terrain_payload(extra_footholds=[(9, 1, 2)])
    submit_plan(body, second)
    assert body.ops() == ["plan_route"]  # the policy sets are already held
    _same_plan(body.accepted[1], second)


def test_route_and_terrain_plans_share_one_body_store():
    route, _ = _house_payload()
    terrain, _ = _terrain_payload()
    body = _client(Body())
    submit_plan(body, route)
    submit_plan(body, terrain)
    body.calls.clear()
    submit_plan(body, route)
    submit_plan(body, terrain)
    assert body.ops() == ["plan_route", "plan_route"]
    _same_plan(body.accepted[-2], route)
    _same_plan(body.accepted[-1], terrain)


def test_logged_uploads_rebuild_the_request_for_offline_replay():
    payload, _ = _house_payload()
    body = _client(Body())
    submit_plan(body, payload)
    uploads = [dict(f) for op, f in body.calls if op == "planner_set"]
    sent = next(f for op, f in body.calls if op == "plan_route")
    replay = plan_sets.resolve(sent, plan_sets.collect(uploads))
    _same_plan(replay, payload)
    with pytest.raises(KeyError, match="plan_set_unknown"):
        plan_sets.resolve(sent, {})
