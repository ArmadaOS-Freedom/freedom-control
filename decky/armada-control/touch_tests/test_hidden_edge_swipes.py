"""Regression tests for edge swipes while the expanded controls are hidden.

A hidden request means the game owns the physical touchscreen: the daemon
must not grab it, must keep the controller engine inert, and must still be
able to recognise an optional edge swipe from the same passive stream.  The
menu chord is deliberately deferred until the finger has lifted so the game's
own touch sequence is never interrupted by a controller chord under a finger.

The existing Harness, frame helpers, FrameAccumulator, and TouchEngine are
used unchanged.  On the r8 production build there is no hidden passive
listener, so the positive menu cases are expected to fail until the feature
is implemented.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(PLUGIN_ROOT))

from test_daemon_lifecycle import (  # noqa: E402
    EV_ABS,
    EV_SYN,
    MT_POSITION_X,
    MT_SLOT,
    MT_TRACKING_ID,
    SYN_REPORT,
    FakeEvent,
    Harness,
)
from test_edge_swipes import (  # noqa: E402
    advance,
    begin_frame,
    enable_edge_swipes,
    move_frame,
    record_chords,
)

SYN_DROPPED = 3
GUIDE_CHORD = ["Gamepad:Button:Guide"]
QAM_CHORD = ["Gamepad:Button:QuickAccess"]
CONTROLLER_KINDS = ("button", "axis", "pad", "pad_press")


def end_frame(slot=0):
    return [
        FakeEvent(EV_ABS, MT_SLOT, slot),
        FakeEvent(EV_ABS, MT_TRACKING_ID, -1),
        FakeEvent(EV_SYN, SYN_REPORT, 0),
    ]


def idle(harness, seconds=0.2):
    """Advance wall time without synthesising another touch frame."""

    harness.now += seconds
    harness.daemon.tick(harness.now)


def hide(harness):
    """Request a hidden overlay and let the existing policy settle it."""

    harness.daemon.handle_message(
        {"type": "controls_visibility", "visible": False}
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)


def hidden_harness(edge_swipes=True, rotation=None, absinfo=None):
    harness = Harness()
    if absinfo is not None:
        harness.touch.absinfo = absinfo
    if rotation is not None:
        harness.daemon._rotation_resolver = (
            lambda _values, value=rotation: value
        )

    if edge_swipes:
        enable_edge_swipes(harness)
    else:
        harness.activate()

    hide(harness)
    record_chords(harness)
    # Hide may neutralise a held control; isolate the passive stream.
    harness.sink.batches = []
    return harness


def arm_pending_swipe(harness, side="left"):
    """Perform a valid raw swipe and lift without waiting for its chord."""

    if side == "left":
        edge_x, inward_x = 0.02, 0.20
    else:
        edge_x, inward_x = 0.98, 0.80

    x, y = harness.raw_point(edge_x, 0.50)
    advance(harness, begin_frame(0, 11, x, y))
    x, y = harness.raw_point(inward_x, 0.50)
    advance(harness, move_frame(0, x, y))
    advance(harness, end_frame(0))


def swipe(harness, points, pointer=21, slot=0):
    x, y = harness.raw_point(*points[0])
    advance(harness, begin_frame(slot, pointer, x, y))
    for point in points[1:]:
        x, y = harness.raw_point(*point)
        advance(harness, move_frame(slot, x, y))
    advance(harness, end_frame(slot))


class HiddenEdgeSwipeDaemonTests(unittest.TestCase):
    def assert_overlay_hidden(self, harness):
        message = harness.overlay.sent[-1]
        self.assertFalse(message["visible"])
        self.assertFalse(message["chrome"])
        self.assertEqual(message["mode"], "expanded")

    def assert_controller_inert(self, harness):
        self.assertFalse(harness.daemon.capturing)
        self.assertTrue(harness.daemon.collapsed)
        self.assertFalse(harness.daemon.controls_visible)
        self.assertFalse(harness.daemon.engine.active)
        for batch in harness.sink.batches:
            for event in batch:
                self.assertNotIn(event[0], CONTROLLER_KINDS)

    def test_hidden_edge_swipes_open_menus_after_release_grace(self):
        cases = (
            ("left", 0.02, 0.20, GUIDE_CHORD),
            ("right", 0.98, 0.80, QAM_CHORD),
        )

        for side, edge_x, inward_x, chord in cases:
            with self.subTest(side=side):
                harness = hidden_harness()
                self.assert_controller_inert(harness)
                self.assert_overlay_hidden(harness)
                baseline_grabs = harness.touch.grabs
                baseline_ungrabs = harness.touch.ungrabs
                self.assertEqual(baseline_ungrabs, 1)

                x, y = harness.raw_point(edge_x, 0.50)
                advance(harness, begin_frame(0, 11, x, y))
                self.assertEqual(harness.bridge.chords, [])
                self.assertEqual(harness.touch.grabs, baseline_grabs)
                self.assertFalse(harness.daemon.engine.active)

                x, y = harness.raw_point(inward_x, 0.50)
                advance(harness, move_frame(0, x, y))
                # A menu may not pre-empt the game's live touch sequence.
                self.assertEqual(harness.bridge.chords, [])
                self.assertFalse(harness.daemon.engine.active)

                advance(harness, end_frame(0))
                self.assertEqual(harness.bridge.chords, [])

                # The bounded settle period runs after raw release, not under
                # the finger.  Fifty milliseconds is still inside the grace.
                idle(harness, 0.05)
                self.assertEqual(harness.bridge.chords, [])

                idle(harness, 0.15)
                self.assertEqual(harness.bridge.chords, [chord])

                self.assertEqual(harness.touch.grabs, baseline_grabs)
                self.assertEqual(harness.touch.ungrabs, baseline_ungrabs)
                self.assertEqual(harness.sink.batches, [])
                self.assert_controller_inert(harness)
                self.assert_overlay_hidden(harness)
                status = harness.status_lines()[-1]["status"]
                self.assertFalse(status["capturing"])
                self.assertFalse(status["controls_visible"])

                # The chord is one-shot, even after the recapture cooldown
                # would have expired if capture were visible.
                idle(harness, 0.3)
                self.assertEqual(harness.bridge.chords, [chord])

    def test_hidden_disabled_edges_leave_taps_drags_and_swipes_inert(self):
        harness = hidden_harness(edge_swipes=False)
        baseline_grabs = harness.touch.grabs
        baseline_ungrabs = harness.touch.ungrabs
        baseline_neutralized = harness.sink.neutralized
        self.assert_controller_inert(harness)

        gestures = (
            ("normal inward edge swipe", [(0.02, 0.50), (0.20, 0.50)]),
            ("normal right inward swipe", [(0.98, 0.50), (0.80, 0.50)]),
            ("central tap", [(0.50, 0.50)]),
            ("central drag", [(0.45, 0.55), (0.65, 0.60)]),
            ("vertical edge drag", [(0.02, 0.20), (0.02, 0.80)]),
            ("outward edge drag", [(0.02, 0.50), (0.00, 0.50)]),
            ("short edge drag", [(0.02, 0.50), (0.06, 0.50)]),
        )

        for index, (label, points) in enumerate(gestures):
            with self.subTest(gesture=label):
                swipe(harness, points, pointer=30 + index)
                idle(harness, 0.2)

                self.assertEqual(harness.bridge.chords, [])
                self.assertEqual(harness.sink.batches, [])
                self.assertEqual(
                    harness.sink.neutralized, baseline_neutralized
                )
                self.assertEqual(harness.touch.grabs, baseline_grabs)
                self.assertEqual(harness.touch.ungrabs, baseline_ungrabs)
                self.assert_controller_inert(harness)
                self.assert_overlay_hidden(harness)

    def test_pending_swipe_is_cleared_by_qam_or_focus_loss(self):
        cases = (
            ("qam", (769, 12345)),
            ("different game", (54321, 12345)),
        )

        for label, apps in cases:
            with self.subTest(reason=label):
                harness = hidden_harness()
                arm_pending_swipe(harness)
                self.assertEqual(harness.bridge.chords, [])

                harness.focus.apps = apps
                harness.now += 0.12
                harness.daemon.tick(harness.now)
                self.assertEqual(harness.bridge.chords, [])

                idle(harness, 0.2)
                self.assertEqual(harness.bridge.chords, [])

                # Returning to the game must not revive a cleared swipe.
                harness.focus.apps = (12345, 12345)
                idle(harness, 0.2)
                self.assertEqual(harness.bridge.chords, [])

    def test_pending_swipe_is_cleared_by_stale_heartbeat(self):
        harness = hidden_harness()
        arm_pending_swipe(harness)
        self.assertEqual(harness.bridge.chords, [])

        harness.daemon.heartbeat_time = harness.now - 4.0
        idle(harness, 0.02)
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])

        # A fresh heartbeat must not resurrect the expired swipe.
        harness.daemon.handle_message(
            {"type": "heartbeat", "appid": "12345"}
        )
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])

    def test_pending_swipe_is_cleared_by_settings_or_visibility(self):
        def disable_edge_option(item):
            item.daemon.handle_message(
                {
                    "type": "settings",
                    "settings": {"enabled": True, "edge_swipes": False},
                }
            )

        def disable_controls(item):
            item.daemon.handle_message(
                {"type": "settings", "settings": {"enabled": False}}
            )

        def show_controls(item):
            item.daemon.handle_message(
                {"type": "controls_visibility", "visible": True}
            )

        cases = (
            ("edge option off", disable_edge_option),
            ("enable off", disable_controls),
            ("visibility on", show_controls),
        )

        for label, change in cases:
            with self.subTest(change=label):
                harness = hidden_harness()
                arm_pending_swipe(harness)
                self.assertEqual(harness.bridge.chords, [])

                change(harness)
                idle(harness, 0.2)
                self.assertEqual(harness.bridge.chords, [])

    def test_pending_swipe_is_cleared_by_syn_dropped(self):
        # A dropped frame while the menu chord is still pending.
        harness = hidden_harness()
        arm_pending_swipe(harness)
        self.assertEqual(harness.bridge.chords, [])

        harness.push_frame([FakeEvent(EV_SYN, SYN_DROPPED, 0)])
        harness.now += 0.02
        harness.daemon.tick(harness.now)
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])

        # A dropped frame in the middle of the gesture must invalidate it
        # before release as well.
        other = hidden_harness()
        x, y = other.raw_point(0.02, 0.50)
        advance(other, begin_frame(0, 41, x, y))
        x, y = other.raw_point(0.20, 0.50)
        advance(other, move_frame(0, x, y))
        other.push_frame([FakeEvent(EV_SYN, SYN_DROPPED, 0)])
        other.now += 0.02
        other.daemon.tick(other.now)
        advance(other, end_frame(0))
        idle(other, 0.2)
        self.assertEqual(other.bridge.chords, [])

    def test_native_menu_during_grace_sends_no_duplicate_chord(self):
        harness = hidden_harness()
        arm_pending_swipe(harness)
        self.assertEqual(harness.bridge.chords, [])

        # Steam's own menu wins while the deferred chord is still pending.
        harness.focus.apps = (769, 12345)
        harness.now += 0.11
        harness.daemon.tick(harness.now)
        self.assertEqual(harness.bridge.chords, [])

        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])

        harness.focus.apps = (12345, 12345)
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])
        self.assert_controller_inert(harness)
        self.assert_overlay_hidden(harness)

    def test_collapse_keeps_grab_and_old_gesture_cannot_leak(self):
        harness = Harness()
        enable_edge_swipes(harness)
        record_chords(harness)
        self.assertTrue(harness.daemon.capturing)

        # Begin an edge candidate under the old capture grab.
        x, y = harness.raw_point(0.02, 0.50)
        advance(harness, begin_frame(0, 51, x, y))
        self.assertTrue(harness.daemon.capturing)
        self.assertTrue(harness.daemon.engine.active)

        hide(harness)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertEqual(harness.touch.grabs, 1)
        self.assertEqual(harness.touch.ungrabs, 0)
        harness.sink.batches = []

        # The same physical finger moves inward and lifts after the grab was
        # suspended.  It began under the old grab and must not become a menu.
        x, y = harness.raw_point(0.20, 0.50)
        advance(harness, move_frame(0, x, y))
        self.assertEqual(harness.bridge.chords, [])
        self.assertEqual(harness.touch.grabs, 1)
        self.assertEqual(harness.touch.ungrabs, 0)

        advance(harness, end_frame(0))
        self.assertEqual(harness.touch.ungrabs, 1)
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [])
        self.assert_overlay_hidden(harness)

        # Only after that lift may a new passive gesture open a menu without
        # taking a new grab.
        arm_pending_swipe(harness)
        self.assertEqual(harness.bridge.chords, [])
        idle(harness, 0.2)
        self.assertEqual(harness.bridge.chords, [GUIDE_CHORD])
        self.assertEqual(harness.touch.grabs, 1)
        self.assertEqual(harness.touch.ungrabs, 1)

    def test_show_waits_for_last_live_touch_then_captures(self):
        harness = Harness()
        harness.activate()
        record_chords(harness)
        hide(harness)
        self.assertEqual(harness.touch.ungrabs, 1)

        first = harness.raw_point(0.45, 0.50)
        second = harness.raw_point(0.55, 0.50)
        advance(harness, begin_frame(0, 61, *first))
        advance(harness, begin_frame(1, 62, *second))

        harness.daemon.handle_message(
            {"type": "controls_visibility", "visible": True}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

        advance(harness, end_frame(0))
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

        advance(harness, end_frame(1))
        # The second lift is read on this tick; capture may only begin on the
        # following policy pass.
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

        idle(harness, 0.02)
        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 2)
        self.assertTrue(harness.overlay.sent[-1]["visible"])
        self.assertEqual(harness.bridge.chords, [])

    def test_rotated_panels_keep_edge_rules_through_raw_point(self):
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                def absinfo(code):
                    return types.SimpleNamespace(
                        min=0,
                        max=2000 if code == MT_POSITION_X else 1000,
                    )

                harness = hidden_harness(
                    rotation=rotation, absinfo=absinfo
                )
                self.assertEqual(harness.daemon.rotation, rotation)
                self.assertAlmostEqual(harness.daemon.aspect_ratio, 0.5)
                self.assertAlmostEqual(
                    harness.daemon.engine.aspect_ratio, 0.5
                )

                # 7% inward is below the existing 8% threshold for this
                # independently rotated, portrait-aspect panel.
                x, y = harness.raw_point(0.02, 0.50)
                advance(harness, begin_frame(0, 71, x, y))
                x, y = harness.raw_point(0.09, 0.50)
                advance(harness, move_frame(0, x, y))
                advance(harness, end_frame(0))
                idle(harness, 0.2)
                self.assertEqual(harness.bridge.chords, [])

                # 9% inward with 1% jitter passes the same 2x aspect test.
                x, y = harness.raw_point(0.02, 0.50)
                advance(harness, begin_frame(0, 72, x, y))
                x, y = harness.raw_point(0.11, 0.51)
                advance(harness, move_frame(0, x, y))
                advance(harness, end_frame(0))
                idle(harness, 0.2)
                self.assertEqual(harness.bridge.chords, [GUIDE_CHORD])

                x, y = harness.raw_point(0.98, 0.50)
                advance(harness, begin_frame(0, 73, x, y))
                x, y = harness.raw_point(0.89, 0.49)
                advance(harness, move_frame(0, x, y))
                advance(harness, end_frame(0))
                idle(harness, 0.2)
                self.assertEqual(
                    harness.bridge.chords, [GUIDE_CHORD, QAM_CHORD]
                )


if __name__ == "__main__":
    unittest.main()
