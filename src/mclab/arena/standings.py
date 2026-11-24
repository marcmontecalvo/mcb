"""A series' standings: what the lobby's sidebar shows between matches and the
chat recap at the end of a series (pure; arena_hunt sends them with the
``arena_board`` and ``arena_say`` ops).

The live match board and the per-match recap are the mod's (ArenaLogic
matchBoard and recap); this module mirrors their colours, their fit into the
sidebar's 15 lines and their recap line, so the two read alike.
Docs: docs/ARENA_RUNTIME.md "Boards and recaps".
"""

from __future__ import annotations

from collections.abc import Iterable

BOARD_LINES = 15
COLORS = frozenset({
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple",
    "gold", "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple",
    "yellow", "white",
})  # fmt: skip
TEAM_PALETTE = (
    "red",
    "blue",
    "green",
    "yellow",
    "light_purple",
    "aqua",
    "gold",
    "white",
)
SIDED = frozenset({"teams", "race"})
STATS = (
    "mob_kills",
    "player_kills",
    "deaths",
    "damage_dealt",
    "damage_taken",
    "healing",
)


def team_color(team: str, index: int) -> str:
    """A team named for a colour wears it; any other takes the palette's colour
    for its place in the plan (ArenaLogic.teamColor)."""
    lower = team.lower()
    return lower if lower in COLORS else TEAM_PALETTE[index % len(TEAM_PALETTE)]


def fmt(value: float) -> str:
    """Health points to a tenth, without a trailing ".0" (ArenaLogic.fmt)."""
    tenth = round(value * 10) / 10
    return str(int(tenth)) if tenth == int(tenth) else str(tenth)


def count(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def _won(run: dict, team: str | None) -> bool:
    if team:
        return run.get("winner") == team
    return run.get("outcome") == "won"


def player_totals(runs: Iterable[dict]) -> dict[str, dict]:
    """Each player's series: matches played, wins (their team's, or the co-op
    match won), kills, downs and health dealt, taken and healed, summed."""
    out: dict[str, dict] = {}
    for run in runs:
        for name, p in (run.get("players") or {}).items():
            total = out.setdefault(name, {
                **({"team": p["team"]} if p.get("team") else {}),
                "matches": 0, "wins": 0, "kills": 0,
                **{k: 0 for k in STATS},
            })  # fmt: skip
            total["matches"] += 1
            total["wins"] += int(_won(run, p.get("team")))
            for k in STATS:
                total[k] = round(total[k] + (p.get(k) or 0), 1)
            total["kills"] = total["mob_kills"] + total["player_kills"]
    for total in out.values():
        for k in ("mob_kills", "player_kills", "deaths", "kills"):
            total[k] = int(total[k])
    return out


def _roster(record: dict) -> list[str]:
    teams = record.get("teams") or {}
    if teams:
        return [n for names in teams.values() for n in names]
    return list(record.get("participants") or ())


def _played(record: dict) -> tuple[int, int]:
    played = len(record.get("runs") or ())
    return played, max(int(record.get("repeat") or played), played)


def _blank() -> dict:
    return {"kills": 0, "matches": 0, "wins": 0, **{k: 0 for k in STATS}}


def fit(
    sections: list[tuple[dict | None, list[dict]]], limit: int = BOARD_LINES
) -> list[dict]:
    """Sections (a head row that always shows, member rows that fold) into
    ``limit`` rows: members share what is left round robin, and a section that
    cannot show them all ends with "+n more" (ArenaLogic.fit)."""
    heads = sum(1 for head, _ in sections if head is not None)
    sizes = [len(members) for _, members in sections]
    quota = list(sizes)
    if heads + sum(sizes) > limit:
        folded = 0
        for _ in range(len(sections) + 1):
            quota = _deal(sizes, max(0, limit - heads - folded))
            now = sum(1 for q, s in zip(quota, sizes, strict=True) if q < s)
            if now == folded:
                break
            folded = now
    out: list[dict] = []
    for (head, members), q in zip(sections, quota, strict=True):
        if head is not None:
            out.append(head)
        out.extend(members[:q])
        if len(members) > q:
            out.append({"text": f"  +{len(members) - q} more", "color": "gray"})
    return out[:limit]


def _deal(sizes: list[int], free: int) -> list[int]:
    quota = [0] * len(sizes)
    while free > 0 and any(q < s for q, s in zip(quota, sizes, strict=True)):
        for i, size in enumerate(sizes):
            if free and quota[i] < size:
                quota[i] += 1
                free -= 1
    return quota


def series_board(record: dict) -> tuple[str, list[dict]]:
    """The lobby's sidebar between matches: each team's wins with its players'
    series kills (draws counted), or in co-op the matches won and everyone's
    kills. Titled with the arena and how far the series has gone."""
    played, repeat = _played(record)
    title = f"{record['arena']} · " + (
        "final" if played >= repeat else f"{played} of {repeat} played"
    )
    runs = record.get("runs") or []
    totals = player_totals(runs)
    teams = record.get("teams") or {}
    sections: list[tuple[dict | None, list[dict]]] = []
    if record.get("mode") in SIDED and teams:
        for i, (team, names) in enumerate(teams.items()):
            color = team_color(team, i)
            wins = sum(1 for r in runs if r.get("winner") == team)
            head = {
                "text": team.capitalize(),
                "value": count(wins, "win"),
                "color": color,
                "bold": True,
            }
            sections.append((head, _kill_rows(names, totals, color, " ")))
        draws = sum(1 for r in runs if r.get("reason") == "draw")
        if draws:
            sections.append(
                ({"text": "Draws", "value": str(draws), "color": "gray"}, [])
            )
    else:
        won = sum(1 for r in runs if r.get("outcome") == "won")
        head = {
            "text": "Won",
            "value": f"{won} of {played}",
            "color": "gold",
            "bold": True,
        }
        sections.append((head, []))
        sections.append((None, _kill_rows(_roster(record), totals, "white", "")))
    return title, fit(sections)


def _kill_rows(names: list[str], totals: dict, color: str, indent: str) -> list[dict]:
    ranked = sorted(names, key=lambda n: -totals.get(n, _blank())["kills"])
    return [
        {
            "text": indent + n,
            "value": count(totals.get(n, _blank())["kills"], "kill"),
            "color": color,
        }
        for n in ranked
    ]


def _seg(text: str, color: str = "white", bold: bool = False) -> dict:
    return {"text": text, "color": color, **({"bold": True} if bold else {})}


def series_recap(record: dict) -> list[list[dict]]:
    """The chat recap when a series ends: how far it went, the score (team wins
    and draws, or the co-op matches won), then every player by damage dealt over
    the series (kills, downs, health dealt, taken and healed), the top one
    starred."""
    played, repeat = _played(record)
    runs = record.get("runs") or []
    head = (
        "final standings" if played >= repeat else f"stopped after {played} of {repeat}"
    )
    lines = [[_seg(f"{record['arena']} · {head}", "gold", bold=True)]]
    teams = record.get("teams") or {}
    color_of: dict[str, str] = {}
    if record.get("mode") in SIDED and teams:
        score: list[dict] = []
        for i, team in enumerate(teams):
            color = team_color(team, i)
            for n in teams[team]:
                color_of[n] = color
            wins = sum(1 for r in runs if r.get("winner") == team)
            if score:
                score.append(_seg(" · ", "gray"))
            score.append(_seg(f"{team.capitalize()} {wins}", color, bold=True))
        draws = sum(1 for r in runs if r.get("reason") == "draw")
        if draws:
            score.append(_seg(f" · {count(draws, 'draw')}", "gray"))
        lines.append(score)
    else:
        won = sum(1 for r in runs if r.get("outcome") == "won")
        lines.append([_seg(f"Won {won} of {played}", "gold")])
    totals = player_totals(runs)
    names = _roster(record) or list(totals)
    ranked = sorted(names, key=lambda n: -totals.get(n, _blank())["damage_dealt"])
    top = (
        ranked[0]
        if ranked and totals.get(ranked[0], _blank())["damage_dealt"] > 0
        else None
    )
    for name in ranked:
        t = totals.get(name, _blank())
        line = [_seg(" ")]
        if name == top:
            line.append(_seg("★ ", "gold", bold=True))
        line.append(_seg(name, color_of.get(name, "white")))
        line.append(_seg(_stat_text(t), "gray"))
        lines.append(line)
    return lines


def _stat_text(t: dict) -> str:
    """ ": 2 kills (1 player), 1 down · dealt 70 · took 32 · healed 18"
    (ArenaLogic's recap line)."""
    out = ": " + count(t["kills"], "kill")
    if t["mob_kills"] > 0 and t["player_kills"] > 0:
        out += f" ({count(t['player_kills'], 'player')})"
    if t["deaths"] > 0:
        out += ", " + count(t["deaths"], "down")
    return out + (
        f" · dealt {fmt(t['damage_dealt'])} · took {fmt(t['damage_taken'])}"
        f" · healed {fmt(t['healing'])}"
    )
