from mclab import gather_runner as gr
from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_fake import FakeGatherBody
from mclab.gather_tasks import Gather

K = load_vanilla_pack()
M = "minecraft:"
LOG = M + "oak_log"
GROUND = {(x, -1, z): M + "dirt" for x in range(-8, 40) for z in range(-8, 8)}


class Logged(FakeGatherBody):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.broke: list = []

    def break_block(self, pos, tool=None, drops_optional=False):
        if self.w.get(pos) == LOG:
            self.broke.append(pos)
        return super().break_block(pos, tool, drops_optional)


def oak(x0: int, z0: int = 0) -> dict:
    """Seven trunk logs, two branch logs, a leaf canopy round the top."""
    w = {(x0, y, z0): LOG for y in range(7)}
    w |= {(x0 + 1, 5, z0): LOG, (x0 + 2, 6, z0): LOG}
    leaves = {
        (x0 + dx, y, z0 + dz): M + "oak_leaves"
        for dx in range(-2, 4)
        for dz in range(-2, 3)
        for y in (6, 7)
    }
    return {p: s for p, s in leaves.items() if p not in w} | w


def logs(body) -> list:
    return [p for p, s in body.w.items() if s == LOG]


def run(world, need, pos=(3, 0, 3)):
    body = Logged(K, GROUND | world, {}, pos=pos, can_dig=True)
    t = Gather(LOG, need, None, "hand", LOG, need, alternatives=((LOG, LOG),))
    gr._Exec(body, K, gr.GatherState(), 64).gather(t)
    return body


def test_one_tree_is_felled_whole_though_three_logs_were_needed():
    body = run(oak(0), 3)
    assert logs(body) == []
    assert body.inventory().get(LOG) == 9


def test_the_first_tree_is_cleared_before_the_second_is_touched():
    body = run(oak(0) | oak(12), 12)
    assert logs(body) == []
    assert body.inventory().get(LOG) == 18
    side = [p[0] < 6 for p in body.broke]
    assert side == sorted(side, reverse=side[0])  # one tree's logs, then the other's
    assert len(set(side[:9])) == 1


def test_a_2x2_trunk_is_cut_only_to_the_need():
    world = {(x, y, z): LOG for x in (0, 1) for z in (0, 1) for y in range(20)}
    body = run(world, 4)
    assert 4 <= body.inventory().get(LOG, 0) <= 8
    assert len(logs(body)) >= 72
