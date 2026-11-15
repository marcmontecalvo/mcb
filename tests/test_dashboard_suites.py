import json

import pytest

from mclab import dashboard_runs as runs
from mclab import dashboard_suites as suites
from mclab.run_config import resolve_config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(suites, "ROOT", tmp_path)
    monkeypatch.setattr(runs, "ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/run-gather.py").touch()
    presets = tmp_path / "config/presets"
    presets.mkdir(parents=True)
    (presets / "careful.json").write_text(
        json.dumps({"planner": {"movement": {"max_gap": 0}}})
    )
    rows = [
        {
            "id": "crafting",
            "label": "Crafting",
            "family": "companion",
            "default_config": resolve_config(
                "companion", overrides={"run": {"check": "crafting"}}
            ),
            "locked_fields": ["run.test", "run.check"],
        },
        {
            "id": "gather",
            "label": "Gather",
            "family": "gather",
            "default_config": resolve_config(
                "gather", overrides={"run": {"start": [0, 70, 0]}}
            ),
        },
        {
            "id": "course",
            "label": "C2",
            "family": "courses",
            "default_config": resolve_config(
                "courses", overrides={"run": {"course": "C2", "seeds": [0]}}
            ),
        },
    ]
    monkeypatch.setattr(suites, "test_catalog", lambda: rows)
    return tmp_path, rows


def test_mixed_queue_uses_canonical_jobs_and_expands_in_order(workspace):
    jobs = suites.resolve_entries(
        [
            {"test_id": "crafting"},
            {"test_id": "course", "seeds": [1, 2], "repetitions": 2},
        ]
    )
    assert [job["id"] for job in jobs] == [f"job-{i}" for i in range(1, 6)]
    assert jobs[0]["run_config"]["run"]["check"] == "crafting"
    assert [job["run_config"]["run"]["seeds"] for job in jobs[1:]] == [
        [1],
        [1],
        [2],
        [2],
    ]
    assert all(job["shared_launch"] for job in jobs)


def test_preset_beats_defaults_but_explicit_override_beats_preset(workspace):
    job = suites.resolve_entries([{"test_id": "gather", "preset": "careful"}])[0]
    assert job["run_config"]["planner"]["movement"]["max_gap"] == 0
    job = suites.resolve_entries(
        [
            {
                "test_id": "gather",
                "preset": "careful",
                "overrides": {"planner": {"movement": {"max_gap": 1}}},
            }
        ]
    )[0]
    assert job["run_config"]["planner"]["movement"]["max_gap"] == 1
    assert job["input_files"]["preset"] == "config/presets/careful.json"


def test_saved_suite_keeps_references_and_changes_resolve_next_time(workspace):
    root, rows = workspace
    value = suites.save_suite(
        {"name": "My routine", "entries": [{"test_id": "gather"}]}
    )
    assert value["id"] == "my-routine"
    assert value["entries"][0]["overrides"] == {}
    rows[1]["default_config"]["planner"]["movement"]["max_gap"] = 0
    loaded = suites.list_suites()[0]
    job = suites.resolve_entries(loaded["entries"])[0]
    assert job["run_config"]["planner"]["movement"]["max_gap"] == 0
    assert "run_config" not in (root / "config/suites/my-routine.json").read_text()
    assert not list((root / "config/suites").glob("*.tmp"))


@pytest.mark.parametrize(
    "entry",
    [
        {"test_id": "missing"},
        {"test_id": "crafting", "seeds": [1]},
        {"test_id": "crafting", "preset": "../private"},
        {"test_id": "crafting", "overrides": {"run": {"check": "animals"}}},
        {"test_id": "gather", "seeds": [True]},
        {"test_id": "gather", "seeds": [1, 1]},
        {"test_id": "gather", "repetitions": 0},
        {"test_id": "gather", "repetitions": True},
        {"test_id": "gather", "unknown": True},
    ],
)
def test_invalid_selection_is_rejected_before_worker_or_world(workspace, entry):
    with pytest.raises(ValueError):
        suites.resolve_entries([entry])
    assert not (workspace[0] / "results").exists()


def test_expansion_cap_rejects_whole_queue(workspace):
    with pytest.raises(ValueError, match="Expanded queue"):
        suites.resolve_entries(
            [
                {"test_id": "course", "seeds": list(range(20)), "repetitions": 3},
                {"test_id": "crafting"},
            ]
        )


def test_cap_counts_all_jobs_returned_by_planner(workspace, monkeypatch):
    monkeypatch.setattr(
        runs,
        "plan",
        lambda request: [
            {"kind": "companion", "run_config": request["run_config"]},
            {"kind": "companion", "run_config": request["run_config"]},
        ],
    )
    with pytest.raises(ValueError, match="Expanded queue"):
        suites.resolve_entries([{"test_id": "crafting"}] * 31)


def test_requested_preset_must_pass_safe_catalog_lookup(workspace, monkeypatch):
    monkeypatch.setattr(suites, "presets", lambda: [])
    with pytest.raises(ValueError, match="unsafe preset"):
        suites.resolve_entries([{"test_id": "gather", "preset": "careful"}])
    with pytest.raises(ValueError, match="unsafe preset"):
        suites.save_suite(
            {"name": "Outside", "entries": [{"test_id": "gather", "preset": "careful"}]}
        )


def test_preset_catalog_reports_consuming_families(workspace):
    [preset] = suites.presets()
    assert set(preset["supported_tests"]) == {"gather", "build", "natural"}


def test_symlinked_preset_is_not_resolved(workspace, tmp_path):
    root, _ = workspace
    outside = root.parent / (root.name + "-outside.json")
    outside.write_text('{"planner":{"movement":{"max_gap":0}}}')
    link = root / "config/presets/outside.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable for this Windows account")
    with pytest.raises(ValueError, match="unsafe preset"):
        suites.resolve_entries([{"test_id": "gather", "preset": "outside"}])


@pytest.mark.parametrize("suite_id", ["../other", "", "a/b", "A", "x" * 65])
def test_suite_ids_cannot_escape_storage(workspace, suite_id):
    with pytest.raises(ValueError):
        suites.save_suite(
            {"id": suite_id, "name": "Routine", "entries": [{"test_id": "crafting"}]}
        )


def test_unknown_override_is_not_saved(workspace):
    with pytest.raises(ValueError):
        suites.save_suite(
            {
                "name": "Invalid",
                "entries": [
                    {
                        "test_id": "crafting",
                        "overrides": {"model": {"api_key": "never-write-this"}},
                    }
                ],
            }
        )
    assert not (workspace[0] / "config/suites").exists()


def test_preset_listing_rejects_unrecognized_content(workspace):
    root, _ = workspace
    (root / "config/presets/private.json").write_text(
        '{"password":"never-return-this"}'
    )
    with pytest.raises(ValueError, match="no supported run family"):
        suites.presets()
