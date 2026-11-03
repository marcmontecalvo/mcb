"""Operator-only build comparison and failure walkthrough artifacts."""

import json
from pathlib import Path

from mclab.build_checkpoint import KIT_PLACE_MAX


def preview_cells(template, offset):
    """Translate every template cell, including air, outside the attempt bounds."""
    if len(offset) != 3 or any(type(v) is not int for v in offset):
        raise ValueError("preview offset requires three integers")
    source = set(template.cells)
    moved = {
        tuple(p[i] + offset[i] for i in range(3)): state
        for p, state in template.cells.items()
    }
    if source & moved.keys():
        raise ValueError("preview overlaps the build template")
    return moved


def place_preview(cells, feet, kit):
    """Kit in batches without changing inventory; never overwrite the body."""
    own = {tuple(feet), (feet[0], feet[1] + 1, feet[2])}
    if own & cells.keys():
        raise ValueError("preview overlaps the body")
    places = [
        {"state": state, "offset": [p[i] - feet[i] for i in range(3)]}
        for p, state in sorted(cells.items())
    ]
    for i in range(0, len(places), KIT_PLACE_MAX):
        kit(place=places[i : i + KIT_PLACE_MAX])
    return len(places)


def write_walkthrough(folder: Path, template, failures):
    """Keep every failed attempt, with its actual stance and full diagnosis."""
    rows, lines = (
        [],
        [
            "# Build failure walkthrough",
            "",
            "Teleport commands are for the observing human (@s).",
            "",
        ],
    )
    for index, (pos, reason, stance, detail) in enumerate(failures, 1):
        target = template.cells.get(tuple(pos))
        command = f"/tp @s {stance[0] + 0.5} {stance[1]} {stance[2] + 0.5}"
        rows.append(
            dict(
                index=index,
                target=list(pos),
                state=target,
                reason=reason,
                stance=list(stance),
                teleport=command,
                diagnosis=detail,
            )
        )
        lines.extend(
            [
                f"## {index}. {list(pos)} — {target}",
                "",
                f"Reason: {reason}. Stance: {list(stance)}.",
                "",
                f"`{command}`",
                "",
                "```json",
                json.dumps(detail, indent=2),
                "```",
                "",
            ]
        )
    (folder / "walkthrough.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    (folder / "walkthrough.md").write_text("\n".join(lines), encoding="utf-8")
