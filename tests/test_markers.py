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


def test_switching_back_on_resends_where_they_point():
    client, on = Client(), [True]
    markers = Markers(client, lambda: on[0])
    markers.aim(goal=(1, 2, 3))
    on[0] = False
    markers.aim(goal=(1, 2, 3))
    on[0] = True
    markers.aim(goal=(1, 2, 3))
    assert client.sent[-2:] == [
        ("natural_markers", {"show": True}),
        ("natural_markers", {"goal": [1, 2, 3]}),
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
