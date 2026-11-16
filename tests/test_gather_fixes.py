from types import SimpleNamespace

from mclab import gather_runner as gr
from mclab.build_runner import Result
from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_dig import Digger
from mclab.gather_fake import FakeGatherBody
from mclab.gather_options import GatherOptions
from mclab.gather_tasks import Gather

K = load_vanilla_pack()
M = "minecraft:"
STONE = M + "stone"
BOX = [(x, z) for x in range(-6, 7) for z in range(-6, 7)]


def column_world(**cells):
    return {(x, y, z): STONE for x, z in BOX for y in range(-20, 0)} | cells


def task(block="stone"):
    return Gather(M + block, 1, "pickaxe", "wood", M + "cobblestone", 1)


def test_gravel_and_water_do_not_stop_a_stair_step_but_lava_behind_a_block_does():
    body = FakeGatherBody(K, column_world(), {}, pos=(0, 0, 0))
    digger = Digger(gr._Exec(body, K, gr.GatherState(), 64))
    new, dig = digger._step_cells((0, 0, 0), (1, 0), False)
    assert digger._check(new, dig) is None
    body.w[(1, new[1] - 1, 0)] = M + "gravel"  # the floor under the new stair
    body.w[dig[0]] = M + "sand"
    body.w[(new[0] + 1, new[1], 0)] = M + "water"  # beside a dug cell
    assert digger._check(new, dig) is None
    body.w[(new[0] + 1, new[1], 0)] = M + "lava"
    assert digger._check(new, dig) == "lava"
    assert not gr._sealed(body, (new[0], new[1], 0))
    body.w[(new[0] + 1, new[1], 0)] = M + "water"
    assert not gr._sealed(body, (new[0], new[1], 0))  # surface mining refuses water


def test_only_a_missing_tool_runs_the_escape(monkeypatch):
    body = FakeGatherBody(K, column_world(), {}, pos=(0, -12, 0))
    for why, escapes in (("missing_tool", True), ("walk_failed", False)):
        run = lambda self, t, why=why: Result(False, why)  # noqa: E731
        monkeypatch.setattr(gr._Exec, "run", run)
        report = gr.run_gather(SimpleNamespace(tasks=[task()]), body, K, max_failures=1)
        assert any(f[0] == "escape" for f in report.failures) is escapes
    failed = ("walk_failed", "leg_limit", "descent_failed:x", "break_failed:x")
    assert not any(gr._buried(r) for r in failed)


def test_explore_fails_when_the_walk_does_not_arrive():
    body = FakeGatherBody(K, column_world(), {}, pos=(0, -1, 0))
    body.walk_to = lambda pos, radius=1.5: False
    ex = gr._Exec(body, K, gr.GatherState(), 16)
    assert ex.explore(lambda: True) is False


def test_kit_is_planned_at_every_depth(monkeypatch):
    body = FakeGatherBody(K, column_world(), {}, pos=(0, 0, 0))
    kits = []

    def fake_plan(targets, inv, k, stations, **kw):
        kits.append(kw["kit"])
        return SimpleNamespace(tasks=[] if kits[1:] else [task()], missing=[])

    def run(self, t):
        body.pos = (0, -10, 0) if kits else (0, 0, 0)  # the second replan is deep
        return Result(True)

    monkeypatch.setattr(gr.gp, "plan", fake_plan)
    monkeypatch.setattr(gr._Exec, "run", run)
    opts = GatherOptions()
    gr.run_gather(SimpleNamespace(tasks=[task()]), body, K, {M + "x": 1}, opts=opts)
    assert kits == [opts, opts]


def test_only_consecutive_failures_of_one_task_stop_the_run(monkeypatch):
    body = FakeGatherBody(K, column_world(), {}, pos=(0, 0, 0))
    monkeypatch.setattr(gr._Exec, "run", lambda self, t: Result(False, "nothing_mined"))
    mixed = [task("stone"), task("dirt")] * 3
    report = gr.run_gather(SimpleNamespace(tasks=mixed), body, K, max_failures=2)
    assert report.stop == "done"
    same = gr.run_gather(SimpleNamespace(tasks=[task()] * 3), body, K, max_failures=2)
    assert same.stop == "failed"


def _grove(log, x0, n=2):
    return {(x0, y, 0): M + log for y in range(n)}


def test_any_log_counts_for_planks_so_the_near_acacia_is_cut_not_the_far_oak():
    from mclab.gather_plan import plan

    world = {(x, -1, z): M + "dirt" for x in range(-6, 50) for z in range(-6, 6)}
    world |= _grove("acacia_log", 3) | _grove("oak_log", 40)
    body = FakeGatherBody(K, world, {}, pos=(0, 0, 0))
    goal = {M + "crafting_table": 1}
    first = plan(goal, {}, K).tasks[0]
    assert (M + "acacia_log", M + "acacia_log") in first.alternatives
    report = gr.run_gather(plan(goal, {}, K), body, K, goal, starter_tools=False)
    assert report.stop == "done", report
    assert body.inventory().get(M + "crafting_table") == 1
    assert not any(c[0] == "walk" and c[1][0] > 20 for c in body.log)


def test_candidates_rank_by_yield_per_distance_capped_at_the_need():
    lone = (3, 0, 0)
    cluster = [(12, y, 0) for y in range(6)]
    known = [lone, *cluster]
    assert gr._by_yield(known, (0, 0, 0), 5)[0] in cluster
    assert gr._by_yield(known, (0, 0, 0), 1)[0] == lone  # one needed: nearest
    assert gr._by_yield(known, (0, 0, 0), 2)[0] == lone  # cap 2: 3/1 vs 12/2 (6/1)


def test_sealed_refuses_water_flowing_water_and_waterlogged_neighbours():
    body = FakeGatherBody(K, column_world(), {}, pos=(0, 0, 0))
    pos = (0, -5, 0)
    assert gr._sealed(body, pos)
    for state in (M + "water", M + "water[level=3]", M + "slab[waterlogged=true]"):
        for face in gr.FACES:
            at = (pos[0] + face[0], pos[1] + face[1], pos[2] + face[2])
            old = body.w[at]
            body.w[at] = state
            assert not gr._sealed(body, pos), (state, face)
            body.w[at] = old
