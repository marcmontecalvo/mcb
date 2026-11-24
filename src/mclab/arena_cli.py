"""``mcplay arena plan``: write validated arena plans and their previews."""

from pathlib import Path

from mclab.arena import designs, preview
from mclab.arena import plan as ap
from mclab.paths import ROOT


def plan(name: str, out: Path | None = None, previews: bool = True) -> dict:
    """Build, validate and write one arena or ``all``; invalid plans are not written."""
    if name != "all" and name not in designs.DESIGNS:
        raise ValueError(
            f"unknown arena {name!r}; choose from {', '.join(designs.NAMES)} or all"
        )
    plans_dir = out if out is not None else ROOT / "arenas"
    previews_dir = (
        out / "previews" if out is not None else ROOT / "results" / "arena-previews"
    )
    names = designs.NAMES if name == "all" else (name,)
    report = []
    for arena in names:
        built = designs.build(arena)
        problems = ap.validate(built)
        entry = {
            "name": arena,
            "size": list(built.size),
            "capacity": {
                "teams": built.capacity.teams,
                "per_team": built.capacity.per_team,
                "mobs": built.capacity.mobs,
            },
            "fills": len(built.fills),
            "problems": problems,
        }
        if not problems:
            plans_dir.mkdir(parents=True, exist_ok=True)
            path = plans_dir / f"{arena}.json"
            path.write_text(ap.to_json(built), encoding="utf-8", newline="\n")
            entry["plan"] = str(path)
            if previews:
                top, side = preview.render(built, previews_dir)
                entry["previews"] = [str(top), str(side)]
        report.append(entry)
    return {"arenas": report}
