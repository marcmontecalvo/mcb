import csv

import pytest

from mclab import evidence
from mclab.paths import write_json
from mclab.recorder import EpisodeRecorder


@pytest.fixture
def fixture_world(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "SERVER", tmp_path)
    state = tmp_path / "supervisor.json"
    monkeypatch.setattr(evidence.server, "STATE", state)
    monkeypatch.setattr(evidence.server, "status", lambda: {"state": "running"})
    write_json(state, {"launch_id": "new-launch"})
    write_json(
        tmp_path / "world/datapacks/mclab/evaluator/active.json",
        {"spec_hash": "current-spec"},
    )
    return tmp_path


def record(path, **overrides):
    value = {
        "launch_id": "new-launch",
        "spec_hash": "current-spec",
        "checked_blocks": 49 * 41 * 49,
        "mismatches": 0,
    }
    value.update(overrides)
    write_json(path / "mclab-bootstrap-evidence.json", value)


def test_matching_geometry_does_not_claim_human_inspection(fixture_world):
    record(fixture_world)
    assert not evidence.verify_bootstrap()["human_inspection_confirmed"]


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"launch_id": "old-launch"}, "Stale"),
        ({"spec_hash": "old-spec"}, "active specification"),
        ({"checked_blocks": 100}, "incomplete"),
        ({"mismatches": 1}, "mismatch"),
    ],
)
def test_stale_or_incomplete_evidence_is_rejected(fixture_world, overrides, reason):
    record(fixture_world, **overrides)
    with pytest.raises(ValueError, match=reason):
        evidence.verify_bootstrap()


def test_report_keeps_failed_attempts_and_distinguishes_suites(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(
        evidence.gates, "statuses", lambda: {"G0": "passed", "G1": "in_progress"}
    )
    monkeypatch.setattr(evidence.server, "status", lambda: {"state": "running"})
    monkeypatch.setattr(evidence, "verify_bootstrap", lambda: {"status": "verified"})
    write_json(
        tmp_path / "results/qualification/first/summary.json",
        {
            "run_id": "mechanics-failed",
            "status": "failed",
            "checks": [{"passed": True}, {"passed": False}],
            "error": "mechanics discrepancy",
        },
    )
    write_json(
        tmp_path / "results/qualification/second/summary.json",
        {
            "run_id": "protocol-passed",
            "suite": "supplementary_protocol_v1",
            "status": "passed_development_subset",
            "checks": [{"passed": True}],
        },
    )
    report = evidence.report()["summary"]
    attempts = report["development_qualification_attempts"]
    assert [(a["suite"], a["status"]) for a in attempts] == [
        ("body_mechanics_v1", "failed"),
        ("supplementary_protocol_v1", "passed_development_subset"),
    ]
    assert attempts[0]["checks_passed"] == 1
    assert attempts[0]["checks_total"] == 2
    assert report["episodes_attempted"] == 0
    assert report["gates"]["G1"] == "in_progress"
    with (tmp_path / "results/reports/qualification_attempts.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["error"] == "mechanics discrepancy"
    assert rows[1]["suite"] == "supplementary_protocol_v1"


def test_report_counts_every_scored_attempt_and_separates_fault_injection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(
        evidence.gates, "statuses", lambda: {"G0": "passed", "G1": "passed"}
    )
    monkeypatch.setattr(evidence.server, "status", lambda: {"state": "running"})
    monkeypatch.setattr(evidence, "verify_bootstrap", lambda: {"status": "verified"})
    recorder = EpisodeRecorder(tmp_path / "results/episodes.sqlite3")
    for name, scored in (("scored", True), ("adversarial", False)):
        recorder.begin_run(name, {"scored": scored})
        for attempt, status in enumerate(("success", "failed", "invalid"), 1):
            identity = f"{name}-{attempt}"
            recorder.begin(name, attempt, identity, "body", "events.jsonl")
            recorder.finish(identity, status, "test", {})
    recorder.close()
    probe = {"run_id": "probe-1", "backends": [{"provenance": {"requests": 2}}]}
    write_json(tmp_path / "results/provider_probes/first.json", probe)
    write_json(tmp_path / "results/provider_probes/latest.json", probe)
    summary = evidence.report()["summary"]
    assert summary["episodes_attempted"] == 3
    assert summary["episodes_succeeded"] == 1
    assert summary["success_rate"] == 1 / 3
    assert summary["unscored_development_attempts"] == 3
    assert summary["provider_requests"] == 2
    assert len(summary["episodes_by_configuration"]) == 1
    assert summary["episodes_by_configuration"][0]["attempted"] == 3
    assert summary["episodes_by_configuration"][0]["succeeded"] == 1
