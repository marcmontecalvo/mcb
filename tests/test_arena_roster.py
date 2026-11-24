"""The saved arena roster: the names the arena wizard offers so nobody retypes them.

A small JSON file under ``runtime/`` (``mclab.arena_roster``): player names with an
optional label and pin, plus the last match's setup. It must survive restarts and
code updates, never half-write, recover from a damaged file and hold names only.
"""

import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from mclab import arena_roster as ar

T0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock that moves a minute on every read, so use order is visible."""

    def __init__(self):
        self.now = T0

    def __call__(self):
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def path(tmp_path):
    return tmp_path / "runtime" / "roster.json"


@pytest.fixture
def store(path):
    return ar.RosterStore(path, clock=Clock())


def names(store):
    return [p["name"] for p in store.players()]


def slot(name, team=None, op=False):
    return {"name": name, "team": team, "op": op}


# --- an empty or missing file ----------------------------------------------------


def test_a_missing_file_is_an_empty_roster_and_is_not_created_by_reading(store, path):
    assert store.players() == []
    assert store.last_match() is None
    assert not path.exists()


def test_the_default_file_is_the_module_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "ROSTER_FILE", tmp_path / "elsewhere.json")
    store = ar.RosterStore()
    store.upsert("Ann")
    assert (tmp_path / "elsewhere.json").is_file()


# --- adding, labelling, pinning, removing ------------------------------------------


def test_a_saved_name_starts_plain(store):
    entry = store.upsert("Ann")
    assert entry == {
        "name": "Ann",
        "label": "",
        "pinned": False,
        "uses": 0,
        "last_used": None,
    }
    assert names(store) == ["Ann"]


def test_a_label_and_a_pin_can_be_set_and_changed_apart(store):
    store.upsert("Ann", label="Dad")
    assert store.upsert("Ann", pinned=True)["label"] == "Dad"  # untouched
    entry = store.upsert("Ann", label="Father")
    assert (entry["label"], entry["pinned"]) == ("Father", True)
    entry = store.upsert("Ann", pinned=False)
    assert (entry["label"], entry["pinned"]) == ("Father", False)


def test_a_label_is_trimmed_and_an_empty_or_null_one_clears_it(store):
    assert store.upsert("Ann", label="  Dad  ")["label"] == "Dad"
    assert store.upsert("Ann", label="")["label"] == ""
    store.upsert("Ann", label="Dad")
    assert store.upsert("Ann", label=None)["label"] == "Dad"  # None: not given


def test_the_same_name_in_another_case_is_one_entry_that_keeps_its_spelling(store):
    store.upsert("SonOfDad", label="Son")
    entry = store.upsert("sonofdad", pinned=True)
    assert entry["name"] == "SonOfDad"
    assert entry["label"] == "Son" and entry["pinned"] is True
    assert names(store) == ["SonOfDad"]


def test_removing_ignores_case_and_says_whether_anything_was_removed(store):
    store.upsert("Ann")
    store.upsert("Bo")
    assert store.remove("ANN") is True
    assert store.remove("ann") is False
    assert names(store) == ["Bo"]


@pytest.mark.parametrize(
    "bad", ["", " ", "a b", "x" * 17, "Ann\n", "Ann.", "Ann-1", "é", None, 5, ["Ann"]]
)
def test_only_minecraft_names_are_saved(store, bad):
    with pytest.raises(ValueError, match="Minecraft name"):
        store.upsert(bad)
    assert store.players() == []


def test_a_name_may_be_sixteen_letters_digits_and_underscores(store):
    store.upsert("A_b_9" + "x" * 11)
    assert len(names(store)[0]) == 16


@pytest.mark.parametrize(
    "fields",
    [{"label": 7}, {"label": "x" * 33}, {"label": "a\nb"}, {"pinned": "yes"}],
)
def test_a_bad_label_or_pin_is_refused_and_changes_nothing(store, fields):
    store.upsert("Ann", label="Dad")
    with pytest.raises(ValueError):
        store.upsert("Ann", **fields)
    assert store.players()[0]["label"] == "Dad"


def test_removing_a_bad_name_is_refused(store):
    with pytest.raises(ValueError, match="Minecraft name"):
        store.remove("not a name")


# --- order -----------------------------------------------------------------------


def test_pinned_names_come_first_then_the_most_recently_used_then_by_name(store):
    for name in ("delta", "Charlie", "alpha", "Bravo"):
        store.upsert(name)
    store.record_match([slot("Charlie")])
    store.record_match([slot("Bravo")])
    store.upsert("delta", pinned=True)
    assert names(store) == ["delta", "Bravo", "Charlie", "alpha"]


def test_names_never_used_sort_by_name_ignoring_case(store):
    for name in ("bob", "Anna", "cy"):
        store.upsert(name)
    assert names(store) == ["Anna", "bob", "cy"]


# --- a started match saves its names ----------------------------------------------


def test_every_name_in_a_match_is_saved_and_counted(store):
    store.record_match([slot("Ann", "red"), slot("Bo", "blue", op=True)])
    store.record_match([slot("Ann", "blue")])
    by_name = {p["name"]: p for p in store.players()}
    assert by_name["Ann"]["uses"] == 2 and by_name["Bo"]["uses"] == 1
    assert by_name["Ann"]["last_used"] > by_name["Bo"]["last_used"]
    assert by_name["Ann"]["last_used"].endswith("Z")


def test_a_name_twice_in_one_match_is_counted_once(store):
    store.record_match([slot("Ann", "red"), slot("ann", "blue")])
    (entry,) = store.players()
    assert entry["uses"] == 1


def test_the_bot_is_not_saved_as_a_player(store):
    store.record_match([slot("MCBot", "red"), slot("Ann", "blue")], bot="mcbot")
    assert names(store) == ["Ann"]
    assert [p["name"] for p in store.last_match()["roster"]] == ["MCBot", "Ann"]


def test_a_match_keeps_the_label_and_pin_of_a_name_it_uses(store):
    store.upsert("ann", label="Dad", pinned=True)
    store.record_match([slot("Ann")])
    (entry,) = store.players()
    assert entry["label"] == "Dad" and entry["pinned"] is True
    assert entry["name"] == "Ann"  # the spelling the match worked with


def test_a_manual_add_does_not_respell_a_name_a_match_used(store):
    store.record_match([slot("Ann")])
    store.upsert("ANN")
    assert names(store) == ["Ann"]


def test_a_match_skips_a_name_that_cannot_be_saved(store):
    store.record_match([slot("Ann"), slot("not a name"), {"team": "red"}, "junk"])
    assert names(store) == ["Ann"]


def test_the_last_match_remembers_the_setup_and_the_teams(store):
    store.record_match(
        [slot("Dad", "red", op=True), slot("Son", "blue"), slot("MCBot", "blue")],
        arena="crash_site",
        game="teams",
        waves="mixed-10",
        custom_waves=None,
        bot="MCBot",
    )
    last = store.last_match()
    assert last["arena"] == "crash_site" and last["game"] == "teams"
    assert last["waves"] == "mixed-10" and last["custom_waves"] is None
    assert last["roster"] == [
        slot("Dad", "red", op=True),
        slot("Son", "blue"),
        slot("MCBot", "blue"),
    ]
    assert last["at"].endswith("Z")


def test_the_next_match_replaces_the_last_one(store):
    store.record_match([slot("Ann", "red")], arena="vault", game="waves")
    store.record_match([slot("Bo")], arena="hall", game="waves")
    last = store.last_match()
    assert last["arena"] == "hall" and [p["name"] for p in last["roster"]] == ["Bo"]


def test_a_match_with_no_one_in_it_leaves_the_last_match_alone(store):
    store.record_match([slot("Ann")], arena="vault")
    store.record_match([], arena="hall")
    assert store.last_match()["arena"] == "vault"


def test_a_removed_name_stays_out_of_the_roster_but_not_out_of_the_last_match(store):
    store.record_match([slot("Ann", "red")])
    store.remove("Ann")
    assert store.players() == []
    assert store.last_match()["roster"][0]["name"] == "Ann"


# --- the file ---------------------------------------------------------------------


def test_it_survives_a_new_store_on_the_same_file(path):
    ar.RosterStore(path).upsert("Ann", label="Dad", pinned=True)
    ar.RosterStore(path).record_match([slot("Bo", "red")], arena="vault")
    again = ar.RosterStore(path)
    assert sorted(names(again)) == ["Ann", "Bo"]
    assert again.last_match()["arena"] == "vault"


def test_the_file_holds_names_labels_and_counts_and_nothing_else(store, path):
    store.upsert("Ann", label="Dad", pinned=True)
    store.record_match([slot("Ann", "red", op=True)], arena="vault", game="waves")
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == 1
    assert set(data) == {"version", "players", "last_match"}
    assert set(data["players"][0]) == {"name", "label", "pinned", "uses", "last_used"}
    assert set(data["last_match"]) == {
        "at",
        "arena",
        "game",
        "waves",
        "custom_waves",
        "roster",
    }
    assert set(data["last_match"]["roster"][0]) == {"name", "team", "op"}


def test_a_write_leaves_no_temporary_file_behind(store, path):
    store.upsert("Ann")
    store.upsert("Bo")
    assert [p.name for p in path.parent.iterdir()] == ["roster.json"]


def test_a_failed_write_keeps_the_old_file_whole(store, path, monkeypatch):
    store.upsert("Ann")
    before = path.read_bytes()

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        store.upsert("Bo")
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["roster.json"]  # no temp file
    assert names(store) == ["Ann"]


def test_the_file_is_replaced_through_a_temporary_file_in_its_own_folder(
    store, path, monkeypatch
):
    seen = []
    real = os.replace
    monkeypatch.setattr(
        os, "replace", lambda src, dst: (seen.append((src, dst)), real(src, dst))[1]
    )
    store.upsert("Ann")
    ((src, dst),) = seen
    assert dst == path
    assert os.path.dirname(src) == str(path.parent) and src != str(path)


def test_the_folder_is_made_when_it_is_missing(tmp_path):
    deep = tmp_path / "a" / "b" / "roster.json"
    ar.RosterStore(deep).upsert("Ann")
    assert deep.is_file()


def test_threads_saving_at_once_lose_nothing(path):
    store = ar.RosterStore(path)
    threads = [
        threading.Thread(target=store.upsert, args=(f"p{i}",)) for i in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(ar.RosterStore(path).players()) == 12


# --- a damaged file ---------------------------------------------------------------


@pytest.mark.parametrize(
    "junk", ["{not json", "", "[]", '"text"', '{"players": 5}', '{"players": {}}']
)
def test_a_damaged_file_is_kept_aside_and_the_roster_starts_again(store, path, junk):
    path.parent.mkdir(parents=True)
    path.write_text(junk, encoding="utf-8")
    assert store.players() == [] and store.last_match() is None
    aside = path.with_name("roster.json.corrupt")
    assert aside.read_text("utf-8") == junk
    assert not path.exists()
    store.upsert("Ann")  # and it works again
    assert names(store) == ["Ann"]
    assert aside.read_text("utf-8") == junk


def test_a_binary_file_is_a_damaged_file(store, path):
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00bad")
    assert store.players() == []
    assert path.with_name("roster.json.corrupt").is_file()


def test_bad_entries_are_dropped_and_the_good_ones_kept(store, path):
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "players": [
                    {"name": "Ann", "label": "Dad", "pinned": True, "uses": 3},
                    {"name": "bad name"},
                    "junk",
                    {"name": "ANN", "label": "dup"},
                    {"name": "Bo", "label": 5, "pinned": "yes", "uses": -2},
                ],
                "last_match": {"roster": "nope"},
            }
        ),
        encoding="utf-8",
    )
    assert [p["name"] for p in store.players()] == ["Ann", "Bo"]
    ann, bo = store.players()
    assert (ann["label"], ann["pinned"], ann["uses"]) == ("Dad", True, 3)
    assert (bo["label"], bo["pinned"], bo["uses"]) == ("", False, 0)
    assert store.last_match() is None
    assert not path.with_name("roster.json.corrupt").exists()  # not damage


def test_a_newer_file_version_is_still_read_for_the_names_it_holds(store, path):
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 9, "players": [{"name": "Ann"}], "extra": 1}),
        encoding="utf-8",
    )
    assert names(store) == ["Ann"]


# --- the cap -----------------------------------------------------------------------


def test_the_oldest_plain_names_go_first_when_the_roster_is_full(store, monkeypatch):
    monkeypatch.setattr(ar, "MAX_SAVED", 3)
    store.upsert("keepPin", pinned=True)
    store.upsert("keepLabel", label="Mum")
    for name in ("old1", "old2", "new1"):
        store.record_match([slot(name)])
    assert sorted(names(store)) == ["keepLabel", "keepPin", "new1"]
