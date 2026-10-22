"""Reviewed gate evidence is separate from volatile server presence snapshots."""

from mclab.paths import ROOT, read_json


def statuses() -> dict:
    ledger = read_json(ROOT / "docs/gates.json")
    return {
        name: value["status"]
        for name, value in ledger.items()
        if name.startswith("G") and isinstance(value, dict)
    }


def inspected(spec_hash: str) -> bool:
    gate = read_json(ROOT / "docs/gates.json")["G0"]
    return (
        gate["status"] == "passed"
        and gate["spec_hash"] == spec_hash
        and gate["human_join_observed"]
        and bool(gate["human_inspection_quote"])
    )
