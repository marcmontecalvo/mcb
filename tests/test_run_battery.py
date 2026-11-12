"""Verdict tests for scripts/run-battery.py: what a reader sees as good or bad."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "run_battery", Path(__file__).resolve().parents[1] / "scripts" / "run-battery.py"
)
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

META = {"kind": "META", "incomplete": []}


def gap(seconds: float, kind: str = "VIOLATION") -> dict:
    return {
        "kind": kind,
        "unjustified_s": seconds,
        "start": 59.4,
        "pos": [-19.5, 65.0, 59.6],
        "last_op": ["req:plan_route", ""],
    }


def test_a_clean_recording_is_none():
    assert rb.standstill({"r1": [META, gap(9.0, "EXEMPT")]}, 0) == ("NONE", [])


def test_a_standstill_is_a_violation_with_the_longest_first():
    verdict, detail = rb.standstill({"r1": [gap(6.0), gap(11.2), META]}, 1)
    assert verdict == "VIOLATION 2 standstills, longest 11.2 s"
    assert detail[0].startswith("stood still 11.2 s at 59.4 s into run r1")


def test_a_recording_with_holes_is_unsure_and_says_why():
    holes = {"kind": "META", "incomplete": ["sparse sample gaps"]}
    assert rb.standstill({"r1": [holes]}, 2) == ("UNSURE (sparse sample gaps)", [])
    assert rb.standstill({}, 2)[0] == "UNSURE (a run had no usable recording)"


def test_a_failed_check_quotes_its_fail_lines(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("PASS a\nFAIL build stop  blocked steps=106\nx: 2/4 passed\n")
    verdict, why = rb.check_verdict(1, log, 900)
    assert verdict == "FAIL (exit code 1)"
    assert why == ["FAIL build stop  blocked steps=106", "PASS a", "x: 2/4 passed"]
    assert rb.check_verdict(0, log, 900) == ("PASS", [])
    assert rb.check_verdict(None, log, 900)[0].startswith("FAIL (TIMEOUT")
