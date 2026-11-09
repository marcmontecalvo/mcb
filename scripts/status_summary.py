"""Quick status inspection script for running build."""
import json
import pathlib
import sys

def main():
    companion_dirs = sorted(pathlib.Path("results/companion").glob("*Z"))
    if not companion_dirs:
        print("No companion dirs found.")
        return
    latest = companion_dirs[-1]
    print(f"Run dir: {latest.name}")

    prog_file = latest / "progress.json"
    if prog_file.exists():
        try:
            print("progress:", prog_file.read_text().strip())
        except Exception as e:
            print("progress err:", e)

    rep_file = latest / "build_report.json"
    if rep_file.exists():
        try:
            print("build_report:", rep_file.read_text().strip())
        except Exception as e:
            print("build_report err:", e)

    checkpoints = sorted((latest / "checkpoints").glob("*.json")) if (latest / "checkpoints").exists() else []
    print(f"Checkpoints: {len(checkpoints)}")
    if checkpoints:
        print(f"Latest checkpoint: {checkpoints[-1].name}")

    events_file = latest / "events.jsonl"
    if events_file.exists():
        try:
            reqs = []
            last_obs = None
            with open(events_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"request"' in line or '"type": "request"' in line:
                        try:
                            reqs.append(json.loads(line))
                        except Exception:
                            pass
                    elif '"type":"observation"' in line or '"type": "observation"' in line:
                        last_obs = line
            if last_obs:
                obs = json.loads(last_obs)
                body = obs.get("payload", {}).get("body", {})
                tick = obs.get("payload", {}).get("tick")
                pos = body.get("position")
                inv = [(i["item"].replace("minecraft:", ""), i["count"]) for i in body.get("inventory", [])]
                print(f"Tick: {tick}, Pos: {pos}")
                print(f"Held: {inv[:8]} ... ({len(inv)} items)")

            from collections import Counter
            print(f"Total requests: {len(reqs)}")
            counts = Counter(r.get("payload", {}).get("op") for r in reqs)
            print("Op counts:", dict(counts))
            block_ops = [r for r in reqs if r.get("payload", {}).get("op") in ("place_block", "break_block")]
            print(f"Block ops count: {len(block_ops)}")
            if block_ops:
                print("Latest 5 block ops:")
                for b in block_ops[-5:]:
                    p = b.get("payload", {})
                    print(" ", p.get("op"), p.get("pos"), p.get("item") or p.get("tool"))
        except Exception as e:
            print("events err:", e)

if __name__ == "__main__":
    main()
