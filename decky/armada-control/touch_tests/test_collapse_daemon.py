"""Daemon-level visibility and collapse tests.

Only the hardware boundaries are fake.  The real daemon policy, engine, sink
translation, and overlay message construction run, so a hide that leaves a
button pressed, steals a live game sequence, or keeps a grab is visible here.

There is no floating bubble in this contract: the in-game pill requests a
complete overlay hide, and the Decky Common tab switch requests visibility.
"""

import sys
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

PLUGIN_ROOT = TESTS_ROOT.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import collapse  # noqa: E402

from test_daemon_lifecycle import (  # noqa: E402
    EV_ABS,
    EV_SYN,
    MT_POSITION_X,
    MT_POSITION_Y,
    MT_SLOT,
    MT_TRACKING_ID,
    SYN_REPORT,
    FakeEvent,
    Harness,
)


def _control_point(harness, control_id, fx=0.5, fy=0.5):
    controls = {
        item["id"]: item for item in harness.daemon.effective_controls
    }
    control = controls[control_id]
    return harness.raw_point(
        control["x"] + control["w"] * fx,
        control["y"] + control["h"] * fy,
    )


def _begin(harness, pointer, slot, x, y):
    harness.push_frame(
        [
            FakeEvent(EV_ABS, MT_SLOT, slot),
            FakeEvent(EV_ABS, MT_TRACKING_ID, pointer),
            FakeEvent(EV_ABS, MT_POSITION_X, x),
            FakeEvent(EV_ABS, MT_POSITION_Y, y),
            FakeEvent(EV_SYN, SYN_REPORT, 0),
        ]
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)


def _move(harness, pointer, slot, x, y):
    harness.push_frame(
        [
            FakeEvent(EV_ABS, MT_SLOT, slot),
            FakeEvent(EV_ABS, MT_TRACKING_ID, pointer),
            FakeEvent(EV_ABS, MT_POSITION_X, x),
            FakeEvent(EV_ABS, MT_POSITION_Y, y),
            FakeEvent(EV_SYN, SYN_REPORT, 0),
        ]
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)


def _end(harness, pointer, slot):
    harness.push_frame(
        [
            FakeEvent(EV_ABS, MT_SLOT, slot),
            FakeEvent(EV_ABS, MT_TRACKING_ID, -1),
            FakeEvent(EV_SYN, SYN_REPORT, 0),
        ]
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)


def _pill_point(harness):
    x, y, width, height = collapse.pill_normalized()
    return harness.raw_point(x + width / 2.0, y + height / 2.0)


def _tap_pill(harness, pointer=9, slot=8):
    x, y = _pill_point(harness)
    _begin(harness, pointer, slot, x, y)
    _end(harness, pointer, slot)


def _assert_hidden(testcase, harness):
    daemon = harness.daemon
    testcase.assertTrue(daemon.collapsed)
    testcase.assertFalse(daemon.controls_visible)
    testcase.assertFalse(daemon.capturing)
    message = harness.overlay.sent[-1]
    testcase.assertFalse(message["visible"])
    testcase.assertFalse(message["chrome"])
    testcase.assertEqual(message["mode"], "expanded")


def _hidden(harness):
    harness.activate()
    _tap_pill(harness)
    assert harness.daemon.collapsed is True
    assert harness.daemon.controls_visible is False
    assert harness.daemon.capturing is False


class ControlsVisibilityDaemonTests(unittest.TestCase):
    def test_hide_neutralizes_held_button_and_keeps_grab_until_last_lift(self):
        harness = Harness()
        harness.activate()

        button_x, button_y = _control_point(harness, "btn_a")
        _begin(harness, 1, 0, button_x, button_y)
        self.assertIn(("button", "a", True), harness.sink.batches[-1])
        self.assertEqual(harness.touch.grabs, 1)

        _tap_pill(harness, pointer=2, slot=1)

        daemon = harness.daemon
        self.assertEqual(harness.touch.grabs, 1)
        self.assertEqual(harness.touch.ungrabs, 0)
        self.assertFalse(daemon.engine.active)
        # The held button is neutralised immediately...
        self.assertIn(("button", "a", False), harness.sink.batches[-1])
        # ...but the device stays grabbed while the other finger is down.
        _assert_hidden(self, harness)
        self.assertFalse(harness.source.closed)
        self.assertIsNotNone(daemon.ui)
        self.assertIsNotNone(daemon.engine)
        self.assertEqual(daemon.source_path, "/dev/input/event9")

        _end(harness, 1, 0)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertEqual(harness.touch.grabs, 1)

        # No grab/ungrab churn after the hide settled.
        harness.now += 0.02
        daemon.tick(harness.now)
        self.assertEqual(harness.touch.ungrabs, 1)

    def test_hidden_controls_ignore_game_touches(self):
        harness = Harness()
        _hidden(harness)
        self.assertEqual(harness.touch.ungrabs, 1)
        baseline = list(harness.sink.batches)

        x, y = _control_point(harness, "btn_a")
        _begin(harness, 1, 0, x, y)
        _move(harness, 1, 0, x + 40, y + 40)
        _end(harness, 1, 0)

        self.assertEqual(harness.sink.batches, baseline)
        self.assertFalse(harness.daemon.engine.active)
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

    def test_show_waits_for_a_held_game_finger_then_captures(self):
        harness = Harness()
        _hidden(harness)
        self.assertEqual(harness.touch.ungrabs, 1)

        x, y = _control_point(harness, "btn_a")
        _begin(harness, 1, 0, x, y)

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": True}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertFalse(harness.daemon.collapsed)
        self.assertTrue(harness.daemon.controls_visible)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.overlay.sent[-1]["visible"])

        _end(harness, 1, 0)
        # The end is read on this tick; capture happens on the next one.
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 2)
        message = harness.overlay.sent[-1]
        self.assertTrue(message["visible"])
        self.assertEqual(message["mode"], "expanded")
        self.assertTrue(message["chrome"])

    def test_hide_and_show_from_qam_restore_only_after_game_focus(self):
        harness = Harness()
        harness.activate()

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": False}
        )
        _assert_hidden(self, harness)

        harness.focus.apps = (769, 12345)
        harness.now += 0.2
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.overlay.sent[-1]["visible"])

        # The Common tab switch is honoured while QAM is frontmost, but it
        # must not authorise a grab there.
        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": True}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)
        self.assertTrue(harness.daemon.controls_visible)
        self.assertFalse(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.overlay.sent[-1]["visible"])

        harness.focus.apps = (12345, 12345)
        harness.now += 0.2
        harness.daemon.tick(harness.now)

        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 2)
        message = harness.overlay.sent[-1]
        self.assertTrue(message["visible"])
        self.assertTrue(message["chrome"])

    def test_old_expand_and_bubble_actions_are_ignored(self):
        harness = Harness()
        _hidden(harness)
        bubble_before = dict(harness.daemon.bubble)

        harness.overlay.actions.extend(
            [
                {"action": "expand"},
                {"action": "bubble", "x": 0.25, "y": 0.35},
            ]
        )
        harness.now += 0.2
        harness.daemon.tick(harness.now)

        _assert_hidden(self, harness)
        self.assertEqual(harness.daemon.bubble, bubble_before)
        self.assertEqual(harness.touch.ungrabs, 1)

    def test_hide_survives_overlay_restart_on_old_compositor(self):
        harness = Harness()
        harness.overlay.touch_routing = False
        harness.activate()

        self.assertTrue(harness.daemon.capturing)
        message = harness.overlay.sent[-1]
        self.assertTrue(message["visible"])
        self.assertTrue(message["chrome"])
        self.assertEqual(message["mode"], "expanded")

        _tap_pill(harness)
        _assert_hidden(self, harness)

        # Losing and restarting the overlay process is not a user request to
        # reveal the controls again.
        harness.daemon._stop_overlay()
        self.assertFalse(harness.daemon.controls_visible)
        self.assertTrue(harness.daemon.collapsed)

        harness.overlay.closed = False
        harness.overlay.alive_flag = True
        harness.overlay.ready = True
        harness.daemon._start_overlay(harness.now)
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertFalse(harness.daemon.controls_visible)
        self.assertTrue(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.overlay.sent[-1]["visible"])
        self.assertFalse(harness.overlay.sent[-1]["chrome"])

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": True}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)
        self.assertTrue(harness.daemon.capturing)
        self.assertTrue(harness.overlay.sent[-1]["visible"])

    def test_pill_dragout_or_cancel_does_not_hide(self):
        harness = Harness()
        harness.activate()

        x, y = _pill_point(harness)
        _begin(harness, 1, 0, x, y)
        outside_x, outside_y = harness.raw_point(0.70, 0.18)
        _move(harness, 1, 0, outside_x, outside_y)
        _end(harness, 1, 0)
        self.assertFalse(harness.daemon.collapsed)
        self.assertTrue(harness.daemon.controls_visible)
        self.assertTrue(harness.daemon.capturing)

        _begin(harness, 2, 1, x, y)
        harness.push_frame([FakeEvent(EV_SYN, 3, 0)])
        harness.now += 0.02
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.daemon.collapsed)
        self.assertTrue(harness.daemon.capturing)
        self.assertIsNone(harness.daemon._pill_pointer)

    def test_menu_focus_and_stale_heartbeat_keep_hidden_request(self):
        harness = Harness()
        _hidden(harness)
        self.assertFalse(harness.overlay.sent[-1]["visible"])

        harness.focus.apps = (769, 12345)
        harness.now += 0.2
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.overlay.sent[-1]["visible"])
        self.assertTrue(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.controls_visible)

        # Returning to the game keeps the hidden request; there is no bubble.
        harness.focus.apps = (12345, 12345)
        harness.now += 0.2
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.overlay.sent[-1]["visible"])
        self.assertFalse(harness.overlay.sent[-1]["chrome"])

        harness.daemon.heartbeat_time = harness.now - 10.0
        harness.now += 0.2
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.overlay.sent[-1]["visible"])

        harness.daemon.handle_message({"type": "heartbeat", "appid": None})
        self.assertFalse(harness.overlay.sent[-1]["visible"])
        self.assertTrue(harness.daemon.collapsed)

    def test_lost_overlay_keeps_hidden_request_and_releases(self):
        harness = Harness()
        _hidden(harness)

        harness.overlay.alive_flag = False
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertIsNone(harness.daemon.overlay)
        self.assertFalse(harness.daemon.controls_visible)
        self.assertTrue(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.capturing)
        self.assertGreaterEqual(harness.touch.ungrabs, 1)
        self.assertFalse(harness.daemon.engine.active)

    def test_disable_from_hidden_cleans_resources_but_keeps_intent(self):
        harness = Harness()
        _hidden(harness)

        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": False}}
        )

        self.assertIsNone(harness.daemon.engine)
        self.assertFalse(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.controls_visible)
        self.assertTrue(harness.touch.closed)
        self.assertTrue(harness.overlay.closed)
        self.assertTrue(harness.source.closed)
        self.assertEqual(harness.sink.closed, 1)

    def test_visibility_status_defaults_true_and_rejects_non_boolean(self):
        harness = Harness()
        harness.activate()
        status = harness.status_lines()[-1]["status"]
        self.assertIs(status["controls_visible"], True)
        self.assertIs(status["capturing"], True)

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": 0}
        )
        self.assertTrue(harness.daemon.controls_visible)
        self.assertTrue(harness.daemon.capturing)
        self.assertIs(harness.status_lines()[-1]["status"]["controls_visible"], True)

    def test_edge_swipes_option_rides_expanded_frames_and_survives_hide(self):
        harness = Harness()
        harness.activate_with({"edge_swipes": True})
        self.assertTrue(harness.overlay.sent[-1]["edge_swipes"])

        _tap_pill(harness)
        _assert_hidden(self, harness)
        self.assertTrue(harness.overlay.sent[-1]["edge_swipes"])

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": True}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        message = harness.overlay.sent[-1]
        self.assertTrue(message["visible"])
        self.assertTrue(message["edge_swipes"])

        other = Harness()
        other.activate()
        self.assertFalse(other.overlay.sent[-1]["edge_swipes"])


if __name__ == "__main__":
    unittest.main()
