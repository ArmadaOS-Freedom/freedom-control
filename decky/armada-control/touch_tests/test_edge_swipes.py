"""Behavioral tests for optional in-game edge menu swipes.

The engine tests use literal rectangles.  The daemon tests reuse the hardware
fakes from ``test_daemon_lifecycle`` and inject the not-yet-canonical
``edge_swipes`` key directly, because runtime settings validation is owned by
the parallel layout/settings ticket.
"""

import sys
import types
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch.engine import TouchEngine  # noqa: E402
from py_modules.freedom_touch.runtime_policy import validate_settings  # noqa: E402

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

MENU_KIND = "menu"


def control(control_id, kind, x, y, w, h, output):
    return {
        "id": control_id,
        "kind": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "output": output,
    }


# The button is deliberately away from both 7% edge bands.
EDGE_LAYOUT = [
    control("btn_mid", "button", 0.40, 0.40, 0.10, 0.10, "a"),
]

# This control starts exactly on the left edge, at mid height.
EDGE_CONTROL_LAYOUT = [
    control("btn_edge", "button", 0.0, 0.40, 0.15, 0.20, "a"),
]


def active_engine(layout=EDGE_LAYOUT, **kwargs):
    engine = TouchEngine([dict(item) for item in layout], **kwargs)
    engine.set_active(True)
    return engine


class EdgeSwipeEngineTests(unittest.TestCase):
    def test_disabled_edge_swipes_ignore_empty_edge_gestures(self):
        engine = active_engine()
        self.assertFalse(engine.edge_swipes)
        self.assertEqual(engine.begin(1, 0.02, 0.15, 0.0), [])
        self.assertEqual(engine.move(1, 0.20, 0.15, 0.05), [])
        self.assertEqual(engine.end(1, 0.1), [])

    def test_inactive_engine_ignores_edge_gestures(self):
        engine = TouchEngine([dict(item) for item in EDGE_LAYOUT],
                             edge_swipes=True)
        self.assertEqual(engine.begin(1, 0.02, 0.15, 0.0), [])
        self.assertEqual(engine.move(1, 0.20, 0.15, 0.05), [])
        self.assertEqual(engine.end(1, 0.1), [])

    def test_both_edges_fire_once_per_pointer_and_allow_id_reuse(self):
        engine = active_engine(edge_swipes=True)
        self.assertEqual(engine.begin(1, 0.02, 0.15, 0.0), [])
        self.assertEqual(engine.begin(2, 0.98, 0.15, 0.0), [])

        self.assertEqual(
            engine.move(1, 0.15, 0.15, 0.05), [("menu", "guide")]
        )
        self.assertEqual(engine.move(2, 0.85, 0.15, 0.05), [("menu", "qam")])

        # A fired pointer cannot trigger a second menu.
        self.assertEqual(engine.move(1, 0.40, 0.15, 0.1), [])
        self.assertEqual(engine.move(2, 0.60, 0.15, 0.1), [])
        self.assertEqual(engine.end(1, 0.2), [])
        self.assertEqual(engine.end(2, 0.2), [])

        self.assertEqual(engine.begin(1, 0.98, 0.15, 1.0), [])
        self.assertEqual(
            engine.move(1, 0.85, 0.15, 1.05), [("menu", "qam")]
        )

    def test_short_outward_and_vertical_gestures_do_not_fire(self):
        engine = active_engine(edge_swipes=True)

        engine.begin(1, 0.02, 0.15, 0.0)
        self.assertEqual(engine.move(1, 0.07, 0.15, 0.05), [])
        self.assertEqual(engine.end(1, 0.1), [])

        engine.begin(2, 0.02, 0.15, 0.2)
        self.assertEqual(engine.move(2, 0.00, 0.15, 0.25), [])
        # Once it moved outward, a later inward move is still rejected.
        self.assertEqual(engine.move(2, 0.20, 0.15, 0.3), [])
        self.assertEqual(engine.end(2, 0.35), [])

        engine.begin(3, 0.02, 0.10, 0.4)
        self.assertEqual(engine.move(3, 0.10, 0.80, 0.45), [])
        # A vertical gesture stays rejected even if it later turns sideways.
        self.assertEqual(engine.move(3, 0.30, 0.10, 0.5), [])
        self.assertEqual(engine.end(3, 0.55), [])

        # A short edge tap never becomes a pad tap or a menu.
        engine.begin(4, 0.02, 0.15, 0.6)
        self.assertEqual(engine.end(4, 0.61), [])

    def test_control_hit_wins_and_edge_candidates_never_cross_activate(self):
        engine = active_engine(edge_swipes=True, layout=EDGE_CONTROL_LAYOUT)

        self.assertEqual(
            engine.begin(1, 0.05, 0.50, 0.0), [("button", "a", True)]
        )
        # The owned button keeps the finger; moving toward the centre does not
        # create a menu gesture.
        self.assertEqual(engine.move(1, 0.30, 0.50, 0.05), [])
        self.assertEqual(engine.end(1, 0.1), [("button", "a", False)])

        # A candidate that later passes over the control must not press it.
        self.assertEqual(engine.begin(2, 0.02, 0.10, 0.2), [])
        self.assertEqual(engine.move(2, 0.05, 0.50, 0.25), [])
        self.assertEqual(engine.end(2, 0.3), [])

    def test_concurrent_candidates_cancel_cleanly_and_ids_are_reusable(self):
        engine = active_engine(edge_swipes=True)
        engine.begin(1, 0.02, 0.15, 0.0)
        engine.begin(2, 0.98, 0.15, 0.0)

        self.assertEqual(engine.cancel_all(), [])
        self.assertEqual(engine.move(1, 0.20, 0.15, 0.05), [])
        self.assertEqual(engine.move(2, 0.80, 0.15, 0.05), [])
        self.assertEqual(engine.end(1, 0.1), [])
        self.assertEqual(engine.end(2, 0.1), [])

        self.assertEqual(engine.begin(1, 0.02, 0.15, 0.2), [])
        self.assertEqual(
            engine.move(1, 0.20, 0.15, 0.25), [("menu", "guide")]
        )

    def test_pure_vertical_gestures_on_both_edges_never_open_the_menu(self):
        engine = active_engine(edge_swipes=True)

        for side, edge_x, inward_x in (
            ("left", 0.02, 0.30),
            ("right", 0.98, 0.70),
        ):
            with self.subTest(side=side, direction="down"):
                self.assertEqual(engine.begin(1, edge_x, 0.10, 0.0), [])
                # The substantial vertical travel rejects the candidate
                # before any inward distance is considered.
                self.assertEqual(engine.move(1, edge_x, 0.80, 0.05), [])
                # A later sideways move cannot revive the same finger.
                self.assertEqual(engine.move(1, inward_x, 0.10, 0.10), [])
                self.assertEqual(engine.end(1, 0.15), [])

            with self.subTest(side=side, direction="up"):
                self.assertEqual(engine.begin(2, edge_x, 0.80, 0.20), [])
                self.assertEqual(engine.move(2, edge_x, 0.10, 0.25), [])
                self.assertEqual(engine.move(2, inward_x, 0.80, 0.30), [])
                self.assertEqual(engine.end(2, 0.35), [])

    def test_small_vertical_jitter_still_allows_a_horizontal_swipe(self):
        engine = active_engine(edge_swipes=True)

        self.assertEqual(engine.begin(1, 0.02, 0.50, 0.0), [])
        # 1% down is jitter, not a scroll.
        self.assertEqual(engine.move(1, 0.03, 0.51, 0.02), [])
        self.assertEqual(
            engine.move(1, 0.20, 0.50, 0.04), [("menu", "guide")]
        )
        self.assertEqual(engine.move(1, 0.40, 0.50, 0.06), [])
        self.assertEqual(engine.end(1, 0.08), [])

    def test_aspect_ratio_is_applied_and_can_be_updated(self):
        # 9% across and 5% down: square pixels fail the strict 2x test,
        # wide pixels pass it.
        square = active_engine(edge_swipes=True)
        square.begin(1, 0.02, 0.50, 0.0)
        self.assertEqual(square.move(1, 0.11, 0.55, 0.05), [])

        wide = active_engine(edge_swipes=True, aspect_ratio=2.0)
        wide.begin(1, 0.02, 0.50, 0.0)
        self.assertEqual(
            wide.move(1, 0.11, 0.55, 0.05), [("menu", "guide")]
        )

        updated = active_engine(edge_swipes=True)
        self.assertEqual(updated.set_aspect_ratio(2.0), 2.0)
        updated.begin(1, 0.02, 0.50, 1.0)
        self.assertEqual(
            updated.move(1, 0.11, 0.55, 1.05), [("menu", "guide")]
        )


def begin_frame(slot, tracking_id, x, y):
    return [
        FakeEvent(EV_ABS, MT_SLOT, slot),
        FakeEvent(EV_ABS, MT_TRACKING_ID, tracking_id),
        FakeEvent(EV_ABS, MT_POSITION_X, x),
        FakeEvent(EV_ABS, MT_POSITION_Y, y),
        FakeEvent(EV_SYN, SYN_REPORT, 0),
    ]


def move_frame(slot, x, y):
    return [
        FakeEvent(EV_ABS, MT_SLOT, slot),
        FakeEvent(EV_ABS, MT_POSITION_X, x),
        FakeEvent(EV_ABS, MT_POSITION_Y, y),
        FakeEvent(EV_SYN, SYN_REPORT, 0),
    ]


def moves_frame(*moves):
    """Return one frame that moves several slots before a single SYN_REPORT.

    Concatenating ``move_frame`` results would deliver one frame per move,
    which hides ordering bugs between two pointers of the same frame.
    """

    events = []
    for slot, x, y in moves:
        events.extend(
            [
                FakeEvent(EV_ABS, MT_SLOT, slot),
                FakeEvent(EV_ABS, MT_POSITION_X, x),
                FakeEvent(EV_ABS, MT_POSITION_Y, y),
            ]
        )
    events.append(FakeEvent(EV_SYN, SYN_REPORT, 0))
    return events


def advance(harness, events, step=0.02):
    harness.push_frame(events)
    harness.now += step
    harness.daemon.tick(harness.now)


def enable_edge_swipes(harness):
    # Settings validation is owned by T20; inject the future canonical key
    # after validating the rest of the settings.
    settings = validate_settings({"enabled": True})
    settings["edge_swipes"] = True
    harness.daemon.handle_message({"type": "heartbeat", "appid": "12345"})
    harness.daemon._apply_settings(settings)
    harness.focus.apps = (12345, 12345)
    harness.daemon.tick(harness.now)


def record_chords(harness):
    harness.bridge.chords = []
    harness.bridge.neutralized_at_chord = None

    def send_chord(caps):
        harness.bridge.chords.append(list(caps))
        harness.bridge.neutralized_at_chord = harness.sink.neutralized

    harness.bridge.send_chord = send_chord


class EdgeSwipeDaemonTests(unittest.TestCase):
    def test_disabled_setting_leaves_edges_inert(self):
        harness = Harness()
        harness.activate()
        record_chords(harness)
        self.assertFalse(harness.daemon.engine.edge_swipes)

        advance(harness, begin_frame(2, 3, 20, 150))
        advance(harness, move_frame(2, 170, 150))

        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.bridge.chords, [])
        self.assertEqual(harness.touch.ungrabs, 0)

    def test_menu_releases_held_controls_before_one_guide_chord(self):
        harness = Harness()
        enable_edge_swipes(harness)
        harness.press_button_and_stick()
        self.assertIn(("button", "a", True), harness.sink.batches[-1])
        record_chords(harness)

        advance(harness, begin_frame(2, 3, 20, 150))
        self.assertEqual(harness.bridge.chords, [])

        # One frame carries both the stick move and the menu swipe; the stale
        # axis must not be sent to the game.
        batches = len(harness.sink.batches)
        advance(harness, moves_frame((1, 240, 485), (2, 170, 150)))
        frame_batches = harness.sink.batches[batches:]

        self.assertEqual(
            harness.bridge.chords, [["Gamepad:Button:Guide"]]
        )
        self.assertIsNotNone(harness.bridge.neutralized_at_chord)
        self.assertGreaterEqual(harness.bridge.neutralized_at_chord, 1)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertGreaterEqual(harness.sink.neutralized, 1)

        # The frame produced the release neutralisation only: the menu wins
        # over the stick move, so no axis value and no menu event may leave.
        self.assertEqual(len(frame_batches), 1)
        released = frame_batches[0]
        self.assertIn(("button", "a", False), released)
        for batch in frame_batches:
            self.assertFalse(
                any(event[0] == MENU_KIND for event in batch)
            )
            self.assertFalse(
                any(
                    event[0] == "axis" and event[2] != 0
                    for event in batch
                )
            )

    def test_right_edge_sends_the_quick_access_chord(self):
        harness = Harness()
        enable_edge_swipes(harness)
        record_chords(harness)

        advance(harness, begin_frame(1, 1, 980, 150))
        advance(harness, move_frame(1, 830, 150))

        self.assertEqual(
            harness.bridge.chords, [["Gamepad:Button:QuickAccess"]]
        )
        self.assertFalse(harness.daemon.capturing)

    def test_two_menus_in_one_frame_send_only_the_first_chord(self):
        harness = Harness()
        enable_edge_swipes(harness)
        record_chords(harness)

        advance(harness, begin_frame(1, 1, 20, 150))
        advance(harness, begin_frame(2, 2, 980, 150))
        advance(
            harness,
            moves_frame((1, 170, 150), (2, 830, 150)),
        )

        self.assertEqual(
            harness.bridge.chords, [["Gamepad:Button:Guide"]]
        )

    def test_menu_blocks_recapture_until_the_cooldown_expires(self):
        harness = Harness()
        enable_edge_swipes(harness)
        record_chords(harness)

        advance(harness, begin_frame(1, 1, 20, 150))
        advance(harness, move_frame(1, 170, 150))
        menu_time = harness.now
        self.assertEqual(harness.touch.grabs, 1)
        self.assertFalse(harness.daemon.capturing)

        harness.focus.apps = (12345, 12345)
        harness.now = menu_time + 0.01
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

        harness.now = menu_time + 0.6
        harness.daemon.tick(harness.now)
        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 2)

    def test_toggling_edge_swipes_rebuilds_only_after_release(self):
        harness = Harness()
        enable_edge_swipes(harness)
        self.assertTrue(harness.daemon.capturing)
        self.assertTrue(harness.daemon.engine.edge_swipes)

        disabled = validate_settings({"enabled": True})
        disabled["edge_swipes"] = False
        harness.daemon._apply_settings(disabled)

        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertFalse(harness.daemon.engine.edge_swipes)
        self.assertIs(harness.daemon.settings, disabled)

        harness.now += 0.02
        harness.daemon.tick(harness.now)
        self.assertTrue(harness.daemon.capturing)

        enabled = validate_settings({"enabled": True})
        enabled["edge_swipes"] = True
        harness.daemon._apply_settings(enabled)
        self.assertFalse(harness.daemon.capturing)
        self.assertTrue(harness.daemon.engine.edge_swipes)

    def test_touch_extents_feed_the_engine_aspect_after_rotation(self):
        harness = Harness()
        harness.touch.absinfo = lambda code: types.SimpleNamespace(
            min=0,
            max=2000 if code == MT_POSITION_X else 1000,
        )
        harness.daemon._rotation_resolver = lambda _values: 90

        enable_edge_swipes(harness)

        # 2000x1000 raw extents become 1000x2000 after the 90-degree swap.
        self.assertAlmostEqual(harness.daemon.aspect_ratio, 0.5)
        self.assertAlmostEqual(harness.daemon.engine.aspect_ratio, 0.5)


if __name__ == "__main__":
    unittest.main()
