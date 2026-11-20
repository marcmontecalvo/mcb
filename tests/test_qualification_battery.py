"""Retention, selection, summary and exit code of the one-command qualification run.

No live runs: the battery subprocess is replaced by a fake runner.
"""

import importlib.util
import json
from datetime import UTC
from pathlib import Path

import pytest

from mclab import qualification_battery as qb

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20261119T101112000000Z"


def load_script():
    spec = importlib.util.spec_from_file_location(
        "run_battery_qb", ROOT / "scripts" / "run-battery.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- keep / discard ---------------------------------------------------------


def reasons(**over):
    args = {
        "verdict": "PASS",
        "standstill": "NONE",
        "search_cap": "NONE",
        "duration_s": 30.0,
        "timeout_s": 300,
    }
    return qb.keep_reasons(**{**args, **over})


def test_a_clean_fast_pass_is_discarded():
    assert reasons() == []


@pytest.mark.parametrize("verdict", ["FAIL", "UNSURE"])
def test_fail_and_unsure_are_kept(verdict):
    assert reasons(verdict=verdict) == [verdict]


def test_a_pass_with_a_standstill_or_search_cap_finding_is_kept():
    assert reasons(standstill="UNSURE (holes)") == ["standstill: UNSURE (holes)"]
    assert reasons(search_cap="far cap hits") == ["search cap: far cap hits"]


def test_a_pass_over_75_percent_of_its_timeout_is_kept():
    assert reasons(duration_s=225.0) == []  # exactly 75 % is still fine
    slow = reasons(duration_s=225.1)
    assert len(slow) == 1 and slow[0].startswith("slow")


def test_every_reason_is_listed():
    got = reasons(verdict="FAIL", standstill="VIOLATION x", duration_s=299)
    assert len(got) == 3


# ---- deleting the outputs ---------------------------------------------------


def make_check(tmp_path, name="wading"):
    out = tmp_path / "battery"
    out.mkdir(exist_ok=True)
    log = out / f"{name}.log"
    log.write_text("COMMAND: x\n")
    idle = out / f"{name}.idle.txt"
    idle.write_text("idle\n")
    run = tmp_path / "results" / "wading" / RUN_ID
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text("{}\n")
    return log, idle, run


def test_discard_removes_log_idle_file_and_run_folder(tmp_path):
    log, idle, run = make_check(tmp_path)
    done = qb.discard_outputs(tmp_path, log, [run])
    assert not log.exists() and not idle.exists() and not run.exists()
    assert done == {"deleted": [run], "cited": []}


def test_discard_keeps_a_run_folder_cited_in_the_docs(tmp_path):
    log, _idle, run = make_check(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "X.md").write_text(f"run {RUN_ID} proved it\n")
    done = qb.discard_outputs(tmp_path, log, [run])
    assert run.exists() and not log.exists()
    assert done == {"deleted": [], "cited": [run]}


def test_discard_never_deletes_outside_the_results_folder(tmp_path):
    log, _idle, _run = make_check(tmp_path)
    stray = tmp_path / "keepme"
    stray.mkdir()
    family = tmp_path / "results" / "wading"
    done = qb.discard_outputs(tmp_path, log, [stray, family])
    assert stray.exists() and family.exists()
    assert done["deleted"] == []


# ---- selection --------------------------------------------------------------

ALL = [
    "wading",
    "iron-xray",
    "iron-mine",
    "iron-raw",
    "small-house",
    "scaffold-course",
    "animals",
]


def test_default_selection_is_every_check_in_order():
    assert qb.select_checks(ALL, None, False) == ALL


def test_fast_drops_the_five_slow_checks():
    assert qb.select_checks(ALL, None, True) == ["wading", "animals"]


def test_only_wins_and_keeps_the_given_order():
    assert qb.select_checks(ALL, ["animals", "wading"], False) == ["animals", "wading"]
    assert qb.select_checks(ALL, ["iron-xray", "wading"], True) == [
        "iron-xray",
        "wading",
    ]


def test_the_real_checks_contain_the_fast_exclusions_and_vine_escape():
    checks = load_script().CHECKS
    assert set(qb.FAST_EXCLUDE) <= set(checks)
    names = list(checks)
    assert names[names.index("wading-trapped") + 1] == "vine-escape"
    assert checks["vine-escape"] == (["scripts/check-companion-vine-escape.py"], 300)
    assert "vine-escape" in qb.select_checks(names, None, True)


def test_list_does_not_need_the_vine_escape_script(capsys, monkeypatch):
    module = load_script()
    monkeypatch.setattr("sys.argv", ["run-battery.py", "--list"])
    assert module.main() == 0
    assert "vine-escape" in capsys.readouterr().out


def test_default_out_is_a_utc_stamp_under_results_battery():
    from datetime import datetime

    when = datetime(2026, 11, 19, 10, 11, 12, tzinfo=UTC)
    assert qb.default_out(when) == Path("results/battery/20261119T101112Z")


# ---- summary.json and the table --------------------------------------------


def record(name, verdict="PASS", **over):
    base = {
        "name": name,
        "verdict": verdict,
        "check": "PASS" if verdict == "PASS" else "FAIL (exit code 1)",
        "standstill": "NONE",
        "search_cap": "NONE",
        "duration_s": 12.3,
        "timeout_s": 300,
        "kept": verdict != "PASS",
        "why": [] if verdict == "PASS" else [verdict],
        "log": None if verdict == "PASS" else f"results/battery/b/{name}.log",
        "folders": [],
    }
    return base | over


def test_summary_payload_has_tally_checks_and_returncode(tmp_path):
    records = [record("a"), record("b", "FAIL"), record("c", "UNSURE")]
    payload = qb.summary_payload(tmp_path / "b", records, keep_all=False)
    assert payload["tally"] == {"PASS": 1, "FAIL": 1, "UNSURE": 1}
    assert payload["returncode"] == 1 and payload["status"] == "failed"
    assert payload["checks"] == ["a", "b", "c"]
    assert payload["keep_all"] is False
    assert payload["results"][1]["name"] == "b"
    assert payload["results"][1]["kept"] is True
    json.dumps(payload, allow_nan=False)


def test_all_pass_is_return_code_zero(tmp_path):
    payload = qb.summary_payload(tmp_path / "b", [record("a")], keep_all=False)
    assert payload["returncode"] == 0 and payload["status"] == "completed_battery"
    assert qb.exit_code([record("a")]) == 0
    assert qb.exit_code([record("a"), record("b", "UNSURE")]) == 1
    assert qb.exit_code([]) == 1  # nothing ran is not a pass


def test_table_lists_logs_only_for_kept_checks_and_ends_with_the_qual_line():
    lines = qb.render_table([record("a"), record("b", "FAIL"), record("c", "UNSURE")])
    text = "\n".join(lines)
    assert "a" in lines[2] and "results/battery/b/b.log" in text
    assert text.count(".log") == 2
    assert lines[-1] == "QUAL: 1 PASS, 1 FAIL, 1 UNSURE"


def test_summary_markdown_has_one_retention_line_per_check():
    md = qb.retention_line(record("b", "FAIL"))
    assert md.startswith("    retention: kept") and "FAIL" in md
    assert "discarded" in qb.retention_line(record("a"))


def test_write_summary_files(tmp_path):
    out = tmp_path / "battery"
    out.mkdir()
    records = [record("a"), record("b", "FAIL")]
    qb.write_summary(out, records, keep_all=False, header=["# Battery x"])
    data = json.loads((out / "summary.json").read_text())
    assert data["returncode"] == 1 and data["results"][0]["duration_s"] == 12.3
    md = (out / "SUMMARY.md").read_text()
    assert "BATTERY: 1 PASS, 1 FAIL, 0 UNSURE" in md and "retention" in md


# ---- the mclab qual command -------------------------------------------------


class FakeRunner:
    def __init__(self, records, code=1):
        self.records = records
        self.code = code
        self.cmd = None

    def __call__(self, cmd, **_kw):
        self.cmd = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        qb.write_summary(out, self.records, keep_all="--keep-all" in cmd, header=[])
        return self.code


def test_qual_passes_flags_to_the_battery_and_prints_the_table(tmp_path, capsys):
    runner = FakeRunner([record("a")], code=0)
    code = qb.qual(
        ["--fast", "--keep-all", "--out", str(tmp_path / "q")], runner=runner
    )
    assert code == 0
    assert "--fast" in runner.cmd and "--keep-all" in runner.cmd
    assert "scripts/run-battery.py" in runner.cmd
    assert capsys.readouterr().out.strip().endswith("QUAL: 1 PASS, 0 FAIL, 0 UNSURE")


def test_qual_only_is_forwarded(tmp_path):
    runner = FakeRunner([record("a")], code=0)
    qb.qual(
        ["--only", "wading", "animals", "--out", str(tmp_path / "q")], runner=runner
    )
    i = runner.cmd.index("--only")
    assert runner.cmd[i + 1 : i + 3] == ["wading", "animals"]


def test_qual_exit_code_is_nonzero_with_a_failure(tmp_path, capsys):
    runner = FakeRunner([record("a"), record("b", "FAIL")], code=1)
    assert qb.qual(["--out", str(tmp_path / "q")], runner=runner) == 1
    assert "QUAL: 1 PASS, 1 FAIL, 0 UNSURE" in capsys.readouterr().out


def test_qual_with_no_summary_reports_not_run(tmp_path, capsys):
    code = qb.qual(["--out", str(tmp_path / "q")], runner=lambda *_a, **_k: 2)
    assert code == 2
    assert "QUAL: NOT RUN" in capsys.readouterr().out


def test_qual_default_out_is_under_results_battery(tmp_path):
    runner = FakeRunner([record("a")], code=0)
    qb.qual([], runner=runner, root=tmp_path)
    out = Path(runner.cmd[runner.cmd.index("--out") + 1])
    assert out.parent.as_posix().endswith("results/battery")


def test_cli_registers_qual(monkeypatch):
    import sys

    from mclab import cli

    seen = {}
    monkeypatch.setattr(qb, "qual", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["mclab", "qual", "--fast"])
    assert cli.main() == 0
    assert seen["argv"] == ["--fast"]


# ---- the script's retention loop -------------------------------------------


def test_script_accepts_keep_all_and_fast_flags(monkeypatch, tmp_path):
    from mclab import run_launch

    module = load_script()
    import argparse

    monkeypatch.setattr(
        "sys.argv",
        ["run-battery.py", "--out", str(tmp_path / "o"), "--keep-all", "--fast"],
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--keep-all", action="store_true")
    ap.add_argument("--fast", action="store_true")
    a = run_launch.parse_battery_args(ap)
    assert a.keep_all is True and a.fast is True
    assert module.CHECKS  # loaded without the vine-escape script present


def test_script_loop_deletes_clean_passes_and_keeps_failures(tmp_path, monkeypatch):
    import argparse

    module = load_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(
        module, "CHECKS", {"good": (["g.py"], 300), "bad": (["b.py"], 300)}
    )
    created = {}

    def fake_run(cmd, log, timeout):
        name = "good" if "g.py" in cmd else "bad"
        log.write_text("FAIL boom\n" if name == "bad" else "ok\n")
        folder = tmp_path / "results" / name / RUN_ID
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text("{}\n")
        created[name] = folder
        return 1 if name == "bad" else 0

    def fake_folders():
        return {p.parent for p in (tmp_path / "results").glob("*/*/events.jsonl")}

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(module, "run_folders", fake_folders)
    monkeypatch.setattr(module, "restart_server", lambda: True)
    monkeypatch.setattr(module, "server", lambda *_w: '"state": "running"')
    monkeypatch.setattr(module, "standstill_verdict", lambda *_a: ("NONE", []))
    monkeypatch.setattr(module, "search_cap_verdict", lambda _f: ("NONE", []))
    out = tmp_path / "results" / "battery" / "b1"
    out.mkdir(parents=True)
    args = argparse.Namespace(
        only=["good", "bad"], timeout=None, keep_all=False, no_restart=False
    )
    assert module.run_battery(args, out) == 1
    assert not created["good"].exists() and not (out / "good.log").exists()
    assert created["bad"].exists() and (out / "bad.log").exists()
    data = json.loads((out / "summary.json").read_text())
    good, bad = data["results"]
    assert (good["kept"], bad["kept"]) == (False, True)
    assert good["log"] is None and bad["log"].endswith("battery/b1/bad.log")
    assert data["tally"] == {"PASS": 1, "FAIL": 1, "UNSURE": 0}
    assert "retention: discarded" in (out / "SUMMARY.md").read_text()

    out2 = tmp_path / "results" / "battery" / "b2"
    out2.mkdir()
    args.only, args.keep_all = ["good"], True
    module.run_battery(args, out2)
    assert (out2 / "good.log").exists() and created["good"].exists()
