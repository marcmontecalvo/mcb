"""One bounded background search lane; obsolete goals cannot publish results."""

import threading
import time
from collections import deque
from dataclasses import dataclass, replace

from mclab.pathing_grid import GridView
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
        self.parked = None  # (token, search, job) of a wall-cut search
        self.stopping = False
        self.running = False
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
        # Grid views are frozen copy-on-write snapshots; plain dicts get an index
        # copy. Nested collision data stays owned by the map either way.
        view = cells.frozen_view() if isinstance(cells, GridView) else dict(cells)
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
                None,
            )
            self.latest = self.error = self.parked = None
            self.condition.notify()
            return self.serial

    def poll(self, token):
        with self.condition:
            if token != self.serial or self.stopping:
                return None
            if self.error is not None:
                raise RuntimeError("Background route search failed") from self.error
            return self.latest

    def resume(self, token):
        """Continue a wall-cut search on its own snapshot; None if not resumable.

        Restarting from zero on each new snapshot never finished a dead-end
        search that needed more than one wall budget, so it never reported
        "unreachable" and the terrain fallback never ran (natural run
        20261029T190159944470Z R5). The expansion cap still bounds total work.
        """
        with self.condition:
            if self.stopping or self.parked is None or self.parked[0] != token:
                return None
            _, search, job = self.parked
            self.serial += 1
            self.pending = (self.serial, *job[1:-1], search)
            self.latest = self.error = self.parked = None
            self.condition.notify()
            return self.serial

    def cancel(self, token=None):
        """Stop the current job; with a token, only if that job is still current.

        One lane serves both the main search and the prefetch. Discarding an
        already-superseded prefetch used to cancel the main search that had
        replaced it, leaving it "pending" forever (run 20261031T002351544397Z R4).
        """
        with self.condition:
            if token is not None and token != self.serial:
                return
            self.serial += 1
            self.pending = self.latest = self.error = self.parked = None
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
                self.running = True
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
                search,
            ) = job
            began = time.perf_counter()
            terminal_published = False
            try:
                if search is None:
                    search = factory(
                        cells,
                        start,
                        goal,
                        map_revision=revision,
                        goal_radius=radius,
                        owned_snapshot=True,
                    )
                wall_cut = False
                while self._current(token):
                    result = search.advance(
                        node_limit=min(nodes, cap - search.expanded),
                        budget_ms=slice_ms,
                        segment_length=length,
                    )
                    elapsed = (time.perf_counter() - began) * 1000
                    final = result.status != "searching"
                    if not final and (result.expanded >= cap or elapsed >= wall_ms):
                        wall_cut = result.expanded < cap
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
                            if wall_cut:
                                self.parked = (token, search, job)
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
            finally:
                with self.condition:
                    self.running = False

    def idle(self):
        with self.condition:
            return self.pending is None and not self.running

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


EDIT_PATIENCE_MS = 1000  # search time spent before following an edit-first partial


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
        self._job = (
            (cells, start, goal),
            dict(
                map_revision=map_revision,
                goal_revision=goal_revision,
                goal_radius=goal_radius,
                node_limit=node_limit,
                slice_ms=slice_ms,
                segment_length=16,
                search_factory=search_factory,
            ),
        )
        self.token = worker.submit(*self._job[0], **self._job[1])

    def advance(self, **_):
        envelope = self.worker.poll(self.token)
        if (
            envelope is None
            and not self.worker._current(self.token)
            and self.worker.idle()
        ):
            # Another submission took the shared lane: this job will never
            # publish. Once the lane is free, resubmit instead of reporting
            # "pending" forever (waiting avoids two searches evicting each other).
            self.token = self.worker.submit(*self._job[0], **self._job[1])
        if envelope is None:
            return RouteResult(
                "searching",
                (self.start,),
                self.revision,
                self.expanded,
                "background search pending",
            )
        self.expanded = envelope.result.expanded
        if envelope.token != getattr(self, "_counted", None):
            self._counted = envelope.token
            self.searched_ms = getattr(self, "searched_ms", 0) + envelope.elapsed_ms
        self.elapsed_ms = envelope.elapsed_ms
        result = envelope.result
        edit_first = len(result.route) >= 2 and any(
            tuple(s.source) == tuple(result.route[0])
            and tuple(s.target) == tuple(result.route[1])
            for s in getattr(result, "steps", ())
        )
        # Never start digging/building from a wall-clock-cut search either: a
        # ~600-node partial tunnelled 30 blocks where the finished search walks
        # around (run 20261030T200723012823Z R2). Past the expansion cap the
        # partial is used as before, and so is one after EDIT_PATIENCE_MS of
        # search: waiting for the full cap stalled R1 5 s (run 20261030T203732413689Z).
        edit_first = edit_first and self.searched_ms < EDIT_PATIENCE_MS
        if result.status == "incomplete" and (len(result.route) < 2 or edit_first):
            # No move to follow yet: keep searching rather than idle on a dead result.
            # ponytail: still ends incomplete past the expansion cap; Step C (D3)
            # planning through unknown space replaces this known-only fallback.
            token = self.worker.resume(self.token)
            if token is not None:
                self.token = token
                return RouteResult(
                    "searching",
                    (self.start,),
                    self.revision,
                    self.expanded,
                    "background search resumed",
                )
        return envelope.result
