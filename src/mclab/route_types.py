"""Shared route reports; independent of any search implementation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteResult:
    status: str
    route: tuple
    map_revision: int
    expanded: int
    reason: str
    steps: tuple = ()
    full_route: tuple = ()


def segment(result, length):
    route = result.route[:length]
    status = (
        "segment"
        if result.status == "reached" and len(route) < len(result.route)
        else result.status
    )
    targets = set(route[1:])
    return RouteResult(
        status,
        route,
        result.map_revision,
        result.expanded,
        result.reason,
        tuple(s for s in result.steps if s.target in targets),
        result.route if len(route) < len(result.route) else (),
    )
