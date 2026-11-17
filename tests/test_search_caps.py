import json

from mclab.search_caps import NEAR_MANHATTAN, cap_hits, main, verdict


def events(tmp_path, plans):
    """plans: (plan_id, goal, status, expanded, node_limit)."""
    rows = [{"type": "hello", "monotonic_ns": 0}]
    for i, (pid, goal, status, expanded, limit) in enumerate(plans):
        rows.append(
            {
                "type": "request",
                "payload": {
                    "op": "plan_route",
                    "plan_id": pid,
                    "sequence": i,
                    "start": [0, 64, 0],
                    "goal": goal,
                    "node_limit": limit,
                    "edits": {"allow_place": False, "break_budget": 64},
                },
            }
        )
        rows.append(
            {
                "type": "server_event",
                "monotonic_ns": 2_000_000_000 * (i + 1),
                "payload": {
                    "type": "plan_finished",
                    "data": {
                        "plan_id": pid,
                        "status": status,
                        "expanded": expanded,
                        "length": 0,
                    },
                },
            }
        )
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    return f


def test_near_hit_is_violation(tmp_path):
    f = events(tmp_path, [("p1", [1, 64, 0], "incomplete", 10000, 10000)])
    (hit,) = cap_hits(f)
    assert hit.near and hit.seconds == 2.0 and hit.manhattan == 1
    assert hit.edits["break_budget"] == 64
    assert verdict([hit])[0].startswith("VIOLATION 1 near")
    assert main([str(f)]) == 1


def test_far_hit_listed_not_failed(tmp_path):
    far = [NEAR_MANHATTAN + 1, 64, 0]
    f = events(tmp_path, [("p1", far, "incomplete", 4000, 4000)])
    hits = cap_hits(f)
    text, detail = verdict(hits)
    assert not hits[0].near and text.startswith("NONE") and len(detail) == 1
    assert main([str(f)]) == 0


def test_found_and_non_cap_incomplete_ignored(tmp_path):
    f = events(
        tmp_path,
        [
            ("p1", [1, 64, 0], "reached", 10000, 10000),
            ("p2", [1, 64, 0], "found", 10000, 10000),
            ("p3", [1, 64, 0], "incomplete", 500, 10000),
        ],
    )
    assert cap_hits(f) == []
    assert verdict([]) == ("NONE", [])
