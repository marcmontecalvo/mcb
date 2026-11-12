import json
import pathlib


def main():
    p = sorted(pathlib.Path("results/companion").glob("*Z"))[-1]
    print(f"Inspecting run: {p}")
    events_file = p / "events.jsonl"
    lines = []
    with open(events_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
                typ = r.get("type")
                payload = r.get("payload", {})
                op = payload.get("op")
                if typ == "error" or "error" in payload:
                    lines.append(("ERROR", r))
                elif typ == "request":
                    if op not in ("heartbeat", "planner_map", "planner_request"):
                        lines.append(("REQ", payload))
                elif typ == "response":
                    if op not in ("heartbeat", "planner_map", "planner_request"):
                        lines.append(("RESP", payload))
                elif "event" in r and r.get("event") not in ("tick", "bot_status"):
                    lines.append(("EVENT", r))
            except Exception:
                pass
    print(f"Total non-trivial events: {len(lines)}")
    for kind, item in lines[-40:]:
        if isinstance(item, dict):
            op = item.get("op") or item.get("event") or list(item.keys())
            details = {
                k: v
                for k, v in item.items()
                if k
                not in (
                    "schema_version",
                    "run_id",
                    "episode_id",
                    "body_id",
                    "sequence_id",
                )
            }
            print(f"[{kind}] {op} -> {str(details)[:120]}")
        else:
            print(f"[{kind}] {str(item)[:140]}")


if __name__ == "__main__":
    main()
