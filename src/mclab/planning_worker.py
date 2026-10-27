"""One bounded background search lane; obsolete goals cannot publish results."""

import threading
import time
from collections import deque
from dataclasses import dataclass, replace

from mclab.route_search import RouteResult, RouteSearch


@dataclass(frozen=True)
class PlanningResult:
    token: int
    goal_revision: int
    start: tuple
    result: object
    elapsed_ms: float


class PlanningWorker:
    """At most one running job, one replaceable pending job and one result.

    Cells must be an owned immutable-record view from SpatialMemory. The index is
    copied on submission; later live ingestion replaces records rather than
    mutating them. No database or Minecraft world object enters the search lane.
    """

    def __init__(self, *, search_factory=RouteSearch):
        self.condition = threading.Condition()
        self.search_factory = search_factory
        self.serial = 0
        self.pending = None
        self.latest = None
        self.error = None
        self.stopping = False
        self.durations = deque(maxlen=1024)
        self.completed = self.cancelled = 0
        self.thread = threading.Thread(
            target=self._run, name="mcb-route-search", daemon=True
        )
        self.thread.start()

    def submit(
        self,
        cells,
        start,
        goal,
        *,
        goal_revision,
        map_revision,
        goal_radius=0,
        node_limit=128,
        slice_ms=2,
        max_expansions=8192,
        wall_budget_ms=250,
        segment_length=128,
        search_factory=None,
    ):
        if (
            type(node_limit) is not int
            or node_limit < 1
            or type(max_expansions) is not int
            or max_expansions < node_limit
            or not 0 < slice_ms <= 20
            or not slice_ms <= wall_budget_ms <= 5000
            or type(segment_length) is not int
            or segment_length < 2
        ):
            raise ValueError("Invalid background planning budgets")
        # This bounded index copy is cheap; nested collision data stays owned.
        view = dict(cells)
        with self.condition:
            if self.stopping:
                raise RuntimeError("Planning worker is closed")
            self.serial += 1
            self.pending = (
                self.serial,
                goal_revision,
                tuple(start),
                tuple(goal),
                map_revision,
                goal_radius,
                view,
                node_limit,
                slice_ms,
                max_expansions,
                wall_budget_ms,
                segment_length,
                search_factory or self.search_factory,
            )
            self.latest = self.error = None
            self.condition.notify()
            return self.serial

    def poll(self, token):
        with self.condition:
            if token != self.serial or self.stopping:
                return None
            if self.error is not None:
                raise RuntimeError("Background route search failed") from self.error
            return self.latest

    def cancel(self):
        with self.condition:
            self.serial += 1
            self.pending = self.latest = self.error = None
            self.condition.notify()

    def _current(self, token):
        with self.condition:
            return not self.stopping and self.serial == token

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: self.stopping or self.pending is not None
                )
                if self.stopping:
                    return
                job, self.pending = self.pending, None
            (
                token,
                goal_revision,
                start,
                goal,
                revision,
                radius,
                cells,
                nodes,
                slice_ms,
                cap,
                wall_ms,
                length,
                factory,
            ) = job
            began = time.perf_counter()
            terminal_published = False
            try:
                search = factory(
                    cells,
                    start,
                    goal,
                    map_revision=revision,
                    goal_radius=radius,
                    owned_snapshot=True,
                )
                while self._current(token):
                    result = search.advance(
                        node_limit=min(nodes, cap - search.expanded),
                        budget_ms=slice_ms,
                        segment_length=length,
                    )
                    elapsed = (time.perf_counter() - began) * 1000
                    final = result.status != "searching"
                    if not final and (result.expanded >= cap or elapsed >= wall_ms):
                        result = replace(
                            result,
                            status="incomplete",
                            reason="background search budget",
                        )
                        final = True
                    with self.condition:
                        if self.serial != token or self.stopping:
                            break
                        self.latest = PlanningResult(
                            token, goal_revision, start, result, elapsed
                        )
                        if final:
                            self.durations.append(elapsed)
                            self.completed += 1
                            terminal_published = True
                    if final:
                        break
                    time.sleep(0)  # Yield the GIL between bounded slices.
                if not terminal_published and not self._current(token):
                    with self.condition:
                        self.cancelled += 1
            except Exception as error:
                with self.condition:
                    if self.serial == token and not self.stopping:
                        self.error = error

    def status(self):
        with self.condition:
            samples = sorted(self.durations)
            return {
                "completed": self.completed,
                "cancelled": self.cancelled,
                "pending": self.pending is not None,
                "latency_ms": {
                    name: samples[min(len(samples) - 1, int((len(samples) - 1) * q))]
                    if samples
                    else None
                    for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                },
            }

    def close(self):
        with self.condition:
            self.stopping = True
            self.serial += 1
            self.pending = self.latest = None
            self.condition.notify()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Route search worker did not stop")


class BackgroundSearch:
    """Nonblocking search adapter sharing the controller's bounded worker."""

    def __init__(
        self,
        worker,
        cells,
        start,
        goal,
        *,
        map_revision,
        goal_revision,
        goal_radius=0,
        node_limit=128,
        slice_ms=3,
        search_factory=None,
    ):
        self.worker = worker
        self.start = tuple(start)
        self.revision = map_revision
        self.expanded = 0
        self.elapsed_ms = 0
        self.token = worker.submit(
            cells,
            start,
            goal,
            map_revision=map_revision,
            goal_revision=goal_revision,
            goal_radius=goal_radius,
            node_limit=node_limit,
            slice_ms=slice_ms,
            segment_length=16,
            search_factory=search_factory,
        )

    def advance(self, **_):
        envelope = self.worker.poll(self.token)
        if envelope is None:
            return RouteResult(
                "searching",
                (self.start,),
                self.revision,
                self.expanded,
                "background search pending",
            )
        self.expanded = envelope.result.expanded
        self.elapsed_ms = envelope.elapsed_ms
        return envelope.result
