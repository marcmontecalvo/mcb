from mclab.markers import Markers, file_switch, run_switch


class Client:
    def __init__(self):
        self.sent = []
        self.show_path = False

    def send(self, op, **fields):
        self.sent.append((op, fields))


def test_markers_stay_quiet_while_switched_off():
    client = Client()
    markers = Markers(client, lambda: False)
    markers.aim(goal=(1, 2, 3), target=(4, 5, 6))
    assert client.sent == [] and not client.show_path


def test_switching_on_shows_them_and_turns_the_path_on():
    client, on = Client(), [True]
    markers = Markers(client, lambda: on[0])
    markers.aim(goal=(1, 2, 3))
    assert client.sent == [
        ("natural_markers", {"show": True}),
        ("natural_markers", {"goal": [1, 2, 3]}),
    ]
    assert client.show_path  # native_driver.publish_path sends the route
    on[0] = False
    markers.aim(target=(4, 5, 6))
    assert client.sent[-1] == ("natural_markers", {"show": False})
    assert not client.show_path


def test_an_unchanged_aim_is_not_sent_again_and_a_cleared_one_is_null():
    client = Client()
    markers = Markers(client, lambda: True)
    markers.aim(goal=(1, 2, 3), target=(4, 5, 6))
    markers.aim(goal=(1, 2, 3), target=(4, 5, 6))
    markers.aim(target=(4, 5, 6))  # the goal kept
    markers.aim(target=None)
    assert client.sent[1:] == [
        ("natural_markers", {"goal": [1, 2, 3], "target": [4, 5, 6]}),
        ("natural_markers", {"target": None}),
    ]


def test_switching_back_on_sends_what_changed_while_off():
    # the lab keeps the goal and target while the markers are hidden
    client, on = Client(), [True]
    markers = Markers(client, lambda: on[0])
    markers.aim(goal=(1, 2, 3), target=(4, 5, 6))
    on[0] = False
    markers.aim(goal=(7, 8, 9))
    on[0] = True
    markers.aim()
    assert client.sent[-2:] == [
        ("natural_markers", {"show": True}),
        ("natural_markers", {"goal": [7, 8, 9]}),
    ]


def test_a_mark_cleared_while_off_is_cleared_when_back_on():
    # review of 5bb4812: forgetting what was sent on the switch made a cleared
    # target look like "nothing yet", so the lab kept showing the old one
    client, on = Client(), [True]
    markers = Markers(client, lambda: on[0])
    markers.aim(goal=(1, 2, 3), target=(4, 5, 6))
    on[0] = False
    markers.aim(goal=None, target=None)
    on[0] = True
    markers.aim()
    assert client.sent[-2:] == [
        ("natural_markers", {"show": True}),
        ("natural_markers", {"goal": None, "target": None}),
    ]


def test_a_run_switch_starts_as_asked_and_follows_its_file(tmp_path):
    switch = run_switch(tmp_path, on=True)
    assert switch() and (tmp_path / "MARKERS").exists()
    (tmp_path / "MARKERS").unlink()
    assert not switch()
    assert not run_switch(tmp_path, on=False)()


def test_the_file_switch_follows_the_file(tmp_path):
    flag = tmp_path / "MARKERS"
    switch = file_switch(flag)
    assert not switch()
    flag.write_text("")
    assert switch()
