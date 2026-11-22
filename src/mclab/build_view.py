"""A view of a live build, for a watcher: the template's layers drawn from the run's
newest checkpoint, with the bot's position, as a PNG; and a verdict per look on
whether the bot is placing, walking or sitting there.

    uv run --locked python -m mclab.build_view results/companion/<run> [--every 300]

Each look writes ``<run>/views/view-NNN.png`` (``--out`` elsewhere) and prints one
line: stage, cells done, counts, the bot's position, how far it moved since the last
look and the verdict. ``--every`` seconds between looks (0: one look). The watch ends
when the run reaches done or inspection, or ``progress.json`` stops updating for
``--stale`` seconds.

The panels are the template's layers bottom up, left to right then down; one pixel
block per cell, x across and z down. Colours: COLOURS.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import time
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mclab import build_plan as bp
from mclab.paths import ROOT

Pos = tuple[int, int, int]
RGB = tuple[int, int, int]

COLOURS: dict[str, RGB] = {
    "correct": (60, 160, 70),
    "missing": (205, 205, 205),
    "wrong": (210, 50, 50),
    "temporary": (230, 190, 40),  # scaffolding, a dirt step: in a cell kept empty
    "empty": (250, 250, 250),
    "outside": (120, 120, 120),  # template box cells drawn as the panel's rim
    "body": (20, 20, 20),
    "progress": (40, 110, 200),
    "background": (255, 255, 255),
}
SCALE = 6  # pixels per cell
GAP = 6  # pixels between panels
BAR = 6  # the progress bar's height
END_STAGES = {"done", "inspection"}
STILL = 2.0  # blocks: less than this between two looks is standing still


def classify(template: bp.Template, world: dict[Pos, str]) -> dict[Pos, str]:
    """correct / missing / wrong for every block the template wants, and temporary for
    a block standing in a cell the template keeps empty (cells unseen are missing)."""
    kinds: dict[Pos, str] = {}
    for pos, want in template.cells.items():
        got = world.get(pos, "minecraft:air")
        want_air = bp.parse_state(want)[0] in bp.AIR
        got_air = bp.parse_state(got)[0] in bp.AIR
        if want_air:
            if not got_air:
                kinds[pos] = "temporary"
        elif got_air:
            kinds[pos] = "missing"
        else:
            same = bp.parse_state(got)[0] == bp.parse_state(want)[0]
            kinds[pos] = "correct" if same else "wrong"
    return kinds


def _png(width: int, height: int, pixels: list[list[RGB]]) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def render(
    template: bp.Template,
    world: dict[Pos, str],
    body: tuple | None = None,
    done: int | None = None,
    total: int | None = None,
    scale: int = SCALE,
) -> bytes:
    """The layers of ``template`` as panels (see the module docstring), a progress bar
    along the top (``done`` of ``total``) and the body as a dark block on its layer."""
    kinds = classify(template, world)
    ox, oy, oz = template.origin
    sx, _, sz = template.size
    layers = sorted(
        {p[1] for p, s in template.cells.items() if bp.parse_state(s)[0] not in bp.AIR}
        | {p[1] for p, k in kinds.items() if k == "temporary"}
    )
    cols = max(1, math.ceil(math.sqrt(len(layers) * sz / max(1, sx))))
    cols = min(cols, max(1, len(layers)))
    rows = max(1, math.ceil(len(layers) / cols))
    pw, ph = sx * scale, sz * scale
    width = cols * pw + (cols + 1) * GAP
    height = BAR + rows * ph + (rows + 1) * GAP
    px = [[COLOURS["background"]] * width for _ in range(height)]

    def fill(x0, y0, w, h, colour):
        for y in range(max(0, y0), min(height, y0 + h)):
            row = px[y]
            for x in range(max(0, x0), min(width, x0 + w)):
                row[x] = colour

    if total:
        fill(
            0, 0, round(width * min(1.0, (done or 0) / total)), BAR, COLOURS["progress"]
        )
    feet = None
    if body is not None:
        feet = (math.floor(body[0]), math.floor(body[1]), math.floor(body[2]))
    for i, y in enumerate(layers):
        left = GAP + (i % cols) * (pw + GAP)
        top = BAR + GAP + (i // cols) * (ph + GAP)
        fill(left - 1, top - 1, pw + 2, ph + 2, COLOURS["outside"])
        for dx in range(sx):
            for dz in range(sz):
                pos = (ox + dx, y, oz + dz)
                kind = kinds.get(pos, "empty")
                fill(left + dx * scale, top + dz * scale, scale, scale, COLOURS[kind])
        if feet is not None and feet[1] == y:
            bx, bz = feet[0] - ox, feet[2] - oz
            if 0 <= bx < sx and 0 <= bz < sz:
                inset = max(1, scale // 4)
                fill(
                    left + bx * scale + inset,
                    top + bz * scale + inset,
                    scale - 2 * inset,
                    scale - 2 * inset,
                    COLOURS["body"],
                )
    return _png(width, height, px)


def latest_checkpoint(run: Path) -> Path | None:
    found = sorted(
        (p for p in (run / "checkpoints").glob("[0-9]*.json") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    return found[-1] if found else None


def body_position(run: Path) -> tuple | None:
    """The newest observed body position (the tail of the event log only)."""
    from mclab import event_log

    try:
        rows = event_log.tail(run, 400)
    except (FileNotFoundError, OSError, ValueError):
        return None
    for r in reversed(rows):
        p = r.get("payload")
        if r.get("type") == "observation" and isinstance(p, dict):
            pos = (p.get("body") or {}).get("position")
            if pos:
                return tuple(float(v) for v in pos)
    return None


@dataclass
class Snapshot:
    stage: str = ""
    done: int = 0
    total: int = 0
    updated_at: float | None = None
    checkpoint: Path | None = None
    body: tuple | None = None
    counts: dict = field(default_factory=dict)
    template: bp.Template | None = None
    world: dict = field(default_factory=dict)


def _read_json(path: Path) -> dict:
    for _ in range(3):  # a file being rewritten: try again
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)
    return {}


def snapshot(
    run: Path, position: Callable[[Path], tuple | None] = body_position
) -> Snapshot:
    progress = _read_json(run / "progress.json")
    snap = Snapshot(
        stage=str(progress.get("stage", "")),
        done=int(progress.get("done") or 0),
        total=int(progress.get("total") or 0),
        updated_at=progress.get("updated_at"),
        body=position(run),
    )
    snap.checkpoint = latest_checkpoint(run)
    if snap.checkpoint is None:
        return snap
    ck = _read_json(snap.checkpoint)
    if not ck.get("build"):
        return snap
    path = Path(ck["build"])
    path = path if path.is_absolute() else ROOT / path
    build = json.loads(path.read_text(encoding="utf-8"))
    snap.template = bp.Template.from_build(
        build, tuple(ck["origin"]), int(ck.get("rotation") or 0)
    )
    snap.world = {(c[0], c[1], c[2]): c[3] for c in ck.get("cells") or ()}
    counts = Counter(classify(snap.template, snap.world).values())
    snap.counts = {k: counts.get(k, 0) for k in ("correct", "missing", "wrong")}
    snap.counts["temporary"] = counts.get("temporary", 0)
    return snap


def verdict(prev: Snapshot | None, cur: Snapshot) -> str:
    """progressing (more cells in), moving (none in, but it walked) or sitting."""
    if prev is None:
        return "progressing"
    placed = cur.counts.get("correct", cur.done) - prev.counts.get("correct", prev.done)
    if cur.done > prev.done or placed > 0 or cur.stage != prev.stage:
        return "progressing"
    if prev.body is None or cur.body is None:
        return "unknown"
    return "moving" if math.dist(prev.body, cur.body) >= STILL else "sitting"


def _line(n: int, started: float, prev: Snapshot | None, cur: Snapshot, png) -> str:
    moved = (
        f"{math.dist(prev.body, cur.body):.1f}"
        if prev and prev.body and cur.body
        else "-"
    )
    pos = (
        "-" if cur.body is None else "(" + ",".join(f"{v:.0f}" for v in cur.body) + ")"
    )
    mins, secs = divmod(int(time.monotonic() - started), 60)
    return (
        f"look {n} +{mins:02d}:{secs:02d} stage={cur.stage} "
        f"done={cur.done}/{cur.total} counts={cur.counts} pos={pos} moved={moved} "
        f"verdict={verdict(prev, cur)} view={png}"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--every", type=float, default=300, help="seconds between looks")
    ap.add_argument("--out", type=Path, help="default <run>/views")
    ap.add_argument("--stale", type=float, default=900)
    args = ap.parse_args(argv)
    out = args.out or args.run / "views"
    out.mkdir(parents=True, exist_ok=True)
    started, prev, n = time.monotonic(), None, 0
    while True:
        cur = snapshot(args.run)
        png = None
        if cur.template is not None:
            png = out / f"view-{n:03d}.png"
            png.write_bytes(
                render(cur.template, cur.world, cur.body, cur.done, cur.total)
            )
        print(_line(n, started, prev, cur, png), flush=True)
        stale = (
            cur.stage not in END_STAGES
            and cur.updated_at is not None
            and time.time() - cur.updated_at > args.stale
        )
        if args.every <= 0 or cur.stage in END_STAGES or stale:
            if stale:
                print(f"watch ended: progress.json not updated for {args.stale:.0f}s")
            return 0
        prev, n = cur, n + 1
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
