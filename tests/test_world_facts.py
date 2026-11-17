from test_spatial_memory import identity

from mclab import build_plan as bp
from mclab import build_runner as br
from mclab.place_graph import Knowledge
from mclab.spatial_memory import MapArchive

DOOR = "minecraft:oak_door[facing=north,half=lower,hinge=left,open=false]"


def knowledge(tmp_path):
    return Knowledge(MapArchive(tmp_path / "map.db", identity()))


def house():
    cells = {
        (0, 64, 0): "minecraft:stone",
        (2, 66, 2): "minecraft:stone",
        (1, 65, 0): DOOR,
        (1, 66, 0): DOOR.replace("lower", "upper"),
        (2, 65, 0): "minecraft:iron_door[facing=north,half=lower]",
        (3, 65, 0): "minecraft:air",
    }
    return bp.Template(cells, (3, 3, 3), (10, 64, 10), "hut")


def test_put_supersedes_and_get(tmp_path):
    k = knowledge(tmp_path)
    k.put("structure", "a", {"phase": "build"}, region=[[0, 0, 0], [1, 1, 1]])
    k.put("structure", "a", {"phase": "done"}, region=[[0, 0, 0], [1, 1, 1]])
    assert [f["value"] for f in k.all("structure")] == [{"phase": "done"}]
    assert k.get("structure", "a")["value"] == {"phase": "done"}
    assert k.get("entrance", "a") is None
    k.drop("structure", "a")
    assert k.get("structure", "a") is None


def test_containing_and_near_use_the_box(tmp_path):
    k = knowledge(tmp_path)
    k.put("structure", "a", {}, region=[[0, 0, 0], [4, 4, 4]])
    assert [f["subject"] for f in k.containing("structure", (2, 2, 2))] == ["a"]
    assert k.containing("structure", (5, 2, 2)) == []
    assert k.near("structure", (7, 2, 2), 3)
    assert not k.near("structure", (9, 2, 2), 3)


def test_build_records_structure_and_entrance(tmp_path):
    k = knowledge(tmp_path)
    br.record_build(k, house(), "build")
    br.record_build(k, house(), "done")
    (s,) = k.all("structure")
    assert s["value"] == {"template": "hut", "phase": "done"}
    assert s["region"] == [[0, 64, 0], [2, 66, 2]]
    (e,) = k.all("entrance")  # upper half, iron door, air are not entrances
    assert e["subject"] == "1,65,0"
    assert e["value"] == {
        "door": [1, 65, 0],
        "structure": s["subject"],
        "outside": [1, 65, 1],
        "inside": [1, 65, -1],
    }


def test_recheck_drops_a_removed_door(tmp_path):
    k = knowledge(tmp_path)
    br.record_build(k, house(), "build")
    br.recheck(k, {(1, 65, 0): {"block": "minecraft:oak_door[open=true]"}})
    assert k.get("entrance", "1,65,0")
    br.recheck(k, {(5, 5, 5): {"block": "minecraft:air"}})
    assert k.get("entrance", "1,65,0")
    br.recheck(k, {(1, 65, 0): {"block": "minecraft:stone"}})
    assert k.get("entrance", "1,65,0") is None
