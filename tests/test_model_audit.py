import json
import runpy
import sqlite3

from mclab.bootstrap import sha256
from mclab.paths import ROOT, write_json


def test_tandem_audit_separates_roles_and_detects_controller_source_drift(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts/audit-model-smokes.py"))
    audit = namespace["audit"]
    audit.__globals__["ROOT"] = tmp_path
    destination = tmp_path / "results/model_execution/test"
    source = destination / "sources/mclab"
    source.mkdir(parents=True)
    summary = {
        "run_id": "test-run",
        "backend": {"name": "tactical"},
        "planner_backend": {"name": "planner"},
        "experiment_mode": "tandem",
        "model_requests": 5,
        "tactical_model_requests": 3,
        "planner_model_requests": 2,
        "episodes": [],
        "scored": False,
    }
    for filename, field in (
        ("model_execution.py", "controller_sha256"),
        ("tactical.py", "tactical_sha256"),
        ("backends.py", "backend_sha256"),
        ("tandem.py", "tandem_sha256"),
    ):
        (source / filename).write_text(filename, encoding="utf-8")
        summary[field] = sha256(source / filename)
    write_json(destination / "summary.json", summary)
    events = [
        {
            "type": "tactical_response",
            "payload": {"decision": {"choice": "mine", "latency_ms": 1}},
        },
        {"type": "planner_response", "payload": {"decision": {"choice": "strategy"}}},
        {
            "type": "planner_response_discarded_on_close",
            "payload": {"error": "cancelled"},
        },
    ]
    (destination / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    with sqlite3.connect(tmp_path / "results/episodes.sqlite3") as db:
        db.execute("CREATE TABLE episodes (episode_id TEXT,status TEXT,run_id TEXT)")
    result = audit(destination / "summary.json")
    assert result["source_snapshots_match"] and result["sqlite_outcomes_match"]
    assert result["experiment_mode"] == "tandem"
    assert result["planner_backend"] == "planner"
    assert result["model_requests"] == 5
    assert result["tactical_model_requests"] == 3
    assert result["planner_model_requests"] == 2
    assert result["choices"] == {"mine": 1}
    assert len(result["planner_responses"]) == 2
    (source / "tandem.py").write_text("changed controller", encoding="utf-8")
    assert not audit(destination / "summary.json")["source_snapshots_match"]
