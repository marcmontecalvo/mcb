import json
from collections import Counter
from pathlib import Path


def main():
    events_file = "results/companion/20261109T023323003795Z/events.jsonl"
    base_cp_file = (
        "results/companion/20261109T015728547275Z/checkpoints/0040_recovered.json"
    )
    cp = json.load(open(base_cp_file, encoding="utf-8"))

    last_obs = None
    ops = Counter()
    breaks = []
    places = {}

    with open(events_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"observation"' in line:
                try:
                    last_obs = json.loads(line)["payload"]
                except Exception:
                    pass
            elif '"type":"request"' in line:
                try:
                    data = json.loads(line)
                    if len(ops) == 0:
                        print("Sample request keys:", list(data.keys()))
                        print("Sample payload:", data.get("payload"))
                    pl = data.get("payload") or {}
                    op = data.get("op") or (
                        pl.get("op") if isinstance(pl, dict) else None
                    )
                    ops[op] += 1
                    if op == "break_block":
                        p = pl.get("pos")
                        if p:
                            breaks.append(tuple(p))
                    elif op == "place_block":
                        pos = tuple(pl["pos"])
                        state = pl.get("state")
                        if state:
                            places[pos] = state
                except Exception:
                    pass

    print("Ops summary:", ops)
    print("Breaks found:", len(breaks))
    print("Places found:", len(places))

    if last_obs:
        body = last_obs.get("body", {})
        inv = {}
        for st in body.get("inventory", []):
            inv[st["item"]] = inv.get(st["item"], 0) + st["count"]
        print("Found inventory items:", len(inv))
        print("Position:", body.get("position"))
        cp["inventory"] = inv

    # Merge into cells
    cells_dict = {tuple(c[:3]): c[3] for c in cp["cells"]}
    # Apply breaks
    for bpos in breaks:
        if bpos in cells_dict:
            print("Break at:", bpos, "was:", cells_dict[bpos])
            cells_dict[bpos] = "minecraft:air"

    # Apply places
    for pos, state in places.items():
        cells_dict[pos] = state

    print("Total merged cells:", len(cells_dict))
    non_air = [p for p, s in cells_dict.items() if "air" not in s]
    print("Total non-air cells:", len(non_air))

    new_cells = [[p[0], p[1], p[2], s] for p, s in cells_dict.items()]
    cp["cells"] = new_cells

    out_dir = Path("results/companion/20261109T023323003795Z/checkpoints")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "0045_recovered.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(cp, f, indent=1)
    print("Saved:", out_file)


if __name__ == "__main__":
    main()
