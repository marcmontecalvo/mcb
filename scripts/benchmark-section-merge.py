"""Replay recorded observations to compare section rewrite and sparse merge costs.

Codec timing only: no server, disk-write or live-control performance claim.
Run separately from live cohorts. Retains both algorithms and verifies final
decoded facts agree, including provenance, explicit air and unknown omissions.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.navigation_metrics import percentiles
from mclab.paths import ROOT, write_json
from mclab.section_codec import (
    decode_section,
    encode_section,
    merge_section,
    section_key,
)
from mclab.terrain_transport import expand_terrain


def replay(batches, merge):
    archive, durations = {}, []
    for batch in batches:
        started = time.perf_counter_ns()
        for key, cells in batch.items():
            previous = archive.get(key)
            if merge:
                archive[key] = merge_section(previous, cells)
            else:
                expanded = (
                    {tuple(c["position"]): c for c in decode_section(key, previous)}
                    if previous
                    else {}
                )
                expanded.update((tuple(c["position"]), c) for c in cells)
                archive[key] = encode_section(expanded.values())
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return archive, durations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("--samples", type=int, choices=range(1, 101), default=12)
    parser.add_argument("--repetitions", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    batches, seen = [], set()
    with args.events.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event["type"] != "policy_observation":
                continue
            observation = expand_terrain(event["payload"])
            sample = (
                event.get("episode_id"),
                observation.get("awareness", {}).get("revision"),
                observation.get("awareness", {}).get("coverage", {}).get("sample_tick"),
            )
            if sample in seen:
                continue
            seen.add(sample)
            grouped = defaultdict(list)
            for cell in observation.get("visible_blocks", []):
                grouped[section_key(cell["position"])].append(cell)
            batches.append(grouped)
            if len(batches) == args.samples:
                break
    if len(batches) != args.samples:
        raise ValueError("Recording has fewer distinct observations than requested")
    totals = {"rewrite": [], "merge": []}
    runs = []
    for repetition in range(args.repetitions):
        order = (False, True) if repetition % 2 == 0 else (True, False)
        outputs = {}
        for merge in order:
            name = "merge" if merge else "rewrite"
            outputs[name], durations = replay(batches, merge)
            totals[name].append(sum(durations))
            runs.append(
                {
                    "repetition": repetition,
                    "algorithm": name,
                    "total_ms": sum(durations),
                    "batch_ms": percentiles(durations),
                }
            )
        decoded = [
            {key: decode_section(key, payload) for key, payload in archive.items()}
            for archive in outputs.values()
        ]
        if decoded[0] != decoded[1]:
            raise RuntimeError("Sparse merge lost or changed recorded facts")
    result = {
        "development_only": True,
        "measurement": "in-process codec replay; excludes disk and control work",
        "clock": "perf_counter_ns",
        "events": str(args.events),
        "events_sha256": sha256(args.events),
        "runner_sha256": sha256(Path(__file__)),
        "codec_sha256": sha256(ROOT / "src/mclab/section_codec.py"),
        "distinct_observations": len(batches),
        "facts_equal": True,
        "runs": runs,
        "total_ms": {name: percentiles(values) for name, values in totals.items()},
    }
    write_json(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
