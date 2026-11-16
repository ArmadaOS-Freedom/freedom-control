"""Integration tests for the optional haptics adapter in the daemon.

The motor driver itself is replaced by the fake from ``test_daemon_lifecycle``;
the real engine, policy, and dispatch path run so a pulse is only delivered
for ordinary successful player input.
"""

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

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
    FakeHaptics,
    Harness,
)
from test_collapse_daemon import (  # noqa: E402
    _control_point,
    _tap_pill,
)

SYN_DROPPED = 3


def _begin_at(harness, pointer, slot, x, y):
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


class ColdFakeHaptics(FakeHaptics):
    """Motor adapter that owns no node until a refresh discovers one.

    The real adapter starts with an empty node table, so ``available`` is
    False until ``refresh()`` opens the two physical devices.  The shared
    fake reports available from construction, which would hide a daemon that
    never refreshes at startup.
    """

    def __init__(self):
        super().__init__()
        self.available = False
        self.detail = "机身振动暂不可用"

    def refresh(self, now):
        super().refresh(now)
        self.available = True
        self.detail = "机身振动已就绪"
        return self.available


class HapticsSettingsTests(unittest.TestCase):
    def test_default_true_and_explicit_false_roundtrip(self):
        default = validate_settings({"enabled": True})
        self.assertIs(default["haptics_enabled"], True)

        off = validate_settings(
            {"enabled": True, "haptics_enabled": False}
        )
        self.assertIs(off["haptics_enabled"], False)
        self.assertEqual(
            validate_settings(off)["haptics_enabled"], False
        )

    def test_default_strength_is_25_integer_percent(self):
        settings = validate_settings({"enabled": True})
        self.assertEqual(settings["haptics_strength"], 25)
        self.assertIs(type(settings["haptics_strength"]), int)

    def test_strength_bounds_roundtrip(self):
        for value in (0, 1, 42, 99, 100):
            settings = validate_settings({"haptics_strength": value})
            self.assertEqual(settings["haptics_strength"], value)
            self.assertEqual(
                validate_settings(settings)["haptics_strength"], value
            )

    def test_strength_rejects_bool_non_numeric_fraction_and_range(self):
        for value in (
            True,
            False,
            "25",
            None,
            -1,
            101,
            25.5,
            float("nan"),
            float("inf"),
        ):
            with self.assertRaises(ValueError):
                validate_settings({"haptics_strength": value})

    def test_legacy_v1_strength_defaults_and_explicit_value_is_kept(self):
        migrated = validate_settings(
            {
                "version": 1,
                "enabled": True,
                "opacity": 0.5,
                "deadzone": 0.2,
            }
        )
        self.assertEqual(migrated["haptics_strength"], 25)

        explicit = validate_settings(
            {
                "version": 1,
                "enabled": True,
                "haptics_strength": 80,
                "haptics_enabled": False,
            }
        )
        self.assertEqual(explicit["haptics_strength"], 80)
        self.assertIs(explicit["haptics_enabled"], False)

    def test_legacy_v1_without_the_field_defaults_on(self):
        migrated = validate_settings(
            {
                "version": 1,
                "enabled": True,
                "opacity": 0.5,
                "deadzone": 0.2,
            }
        )
        self.assertIs(migrated["haptics_enabled"], True)

    def test_legacy_v1_explicit_false_is_preserved(self):
        migrated = validate_settings(
            {
                "version": 1,
                "enabled": True,
                "haptics_enabled": False,
            }
        )
        self.assertIs(migrated["haptics_enabled"], False)


class DaemonHapticsTests(unittest.TestCase):
    def test_button_and_pad_input_dispatch_actual_events(self):
        harness = Harness()
        harness.activate()
        haptics = harness.haptics
        haptics.consumed.clear()

        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        self.assertTrue(
            any(
                event[0] == "button" and event[1] == "a" and event[2] is True
                for events, _now in haptics.consumed
                for event in events
            )
        )
        self.assertIn(("button", "a", True), harness.sink.batches[-1])

        haptics.consumed.clear()
        pad_x, pad_y = _control_point(harness, "pad_l")
        _begin_at(harness, 2, 1, pad_x, pad_y)

        pad_events = [
            event for events, _now in haptics.consumed for event in events
        ]
        self.assertTrue(
            any(
                event[0] == "pad" and event[1] == "left"
                for event in pad_events
            )
        )

    def test_disabled_haptics_keeps_input_but_never_consumes(self):
        harness = Harness()
        harness.activate_with({"haptics_enabled": False})

        haptics = harness.haptics
        self.assertFalse(haptics.enabled)
        haptics.consumed.clear()

        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        self.assertIn(("button", "a", True), harness.sink.batches[-1])
        self.assertEqual(haptics.consumed, [])

    def test_cancel_stops_without_a_new_pulse(self):
        harness = Harness()
        harness.activate()
        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        haptics = harness.haptics
        consumed = len(haptics.consumed)
        stops = haptics.stops
        harness.push_frame([FakeEvent(EV_SYN, SYN_DROPPED, 0)])
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertGreater(haptics.stops, stops)
        self.assertEqual(len(haptics.consumed), consumed)
        self.assertNotIn(
            ("button", "a", False),
            [event for events, _now in haptics.consumed for event in events],
        )

    def test_menu_focus_stops_without_a_new_pulse(self):
        harness = Harness()
        harness.activate()
        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        haptics = harness.haptics
        consumed = len(haptics.consumed)
        stops = haptics.stops
        harness.focus.apps = (769, 12345)
        harness.now += 0.1
        harness.daemon.tick(harness.now)

        self.assertFalse(harness.daemon.capturing)
        self.assertGreater(haptics.stops, stops)
        self.assertEqual(len(haptics.consumed), consumed)

    def test_collapse_stops_without_a_new_pulse(self):
        harness = Harness()
        harness.activate()
        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        haptics = harness.haptics
        consumed = len(haptics.consumed)
        stops = haptics.stops
        _tap_pill(harness)

        self.assertTrue(harness.daemon.collapsed)
        self.assertGreater(haptics.stops, stops)
        self.assertEqual(len(haptics.consumed), consumed)

    def test_disable_stops_and_shutdown_closes_the_adapter(self):
        harness = Harness()
        harness.activate()
        button_x, button_y = _control_point(harness, "btn_a")
        _begin_at(harness, 1, 0, button_x, button_y)

        haptics = harness.haptics
        consumed = len(haptics.consumed)
        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": False}}
        )

        self.assertFalse(haptics.enabled)
        self.assertGreater(haptics.stops, 0)
        self.assertEqual(len(haptics.consumed), consumed)

        harness.daemon.shutdown()
        self.assertEqual(haptics.closed, 1)

    def test_refresh_is_bounded_to_a_five_second_cadence(self):
        harness = Harness()
        harness.activate()
        haptics = harness.haptics
        baseline = len(haptics.refreshed)
        base_now = harness.now

        harness.now = base_now + 1.0
        harness.daemon.tick(harness.now)
        harness.now = base_now + 4.9
        harness.daemon.tick(harness.now)
        self.assertEqual(len(haptics.refreshed), baseline)

        harness.now = base_now + 5.01
        harness.daemon.tick(harness.now)
        self.assertEqual(len(haptics.refreshed), baseline + 1)

    def test_first_poll_discovers_before_the_first_five_second_window(self):
        harness = Harness()
        harness.haptics = ColdFakeHaptics()
        harness.activate()

        haptics = harness.haptics
        first = harness.now
        # The creating tick must already have discovered the nodes; otherwise
        # the UI reports "unavailable" for a full refresh interval.
        self.assertEqual(haptics.refreshed, [first])
        self.assertTrue(haptics.available)
        self.assertIs(
            harness.status_lines()[-1]["status"]["haptics_available"], True
        )

        harness.now = first + 4.9
        harness.daemon.tick(harness.now)
        self.assertEqual(haptics.refreshed, [first])

        harness.now = first + 5.0
        harness.daemon.tick(harness.now)
        self.assertEqual(haptics.refreshed, [first, first + 5.0])

    def test_unavailable_is_reported_but_never_blocks_capture(self):
        harness = Harness()
        harness.haptics.available = False
        harness.haptics.detail = "机身振动暂不可用"
        harness.activate()

        status = harness.status_lines()[-1]["status"]
        self.assertIs(status["haptics_available"], False)
        self.assertEqual(status["haptics_detail"], "机身振动暂不可用")
        self.assertTrue(harness.daemon.capturing)
        self.assertNotEqual(status["state"], "waiting")

    def test_empty_detail_falls_back_to_the_unavailable_message(self):
        harness = Harness()
        harness.haptics.available = False
        harness.haptics.detail = ""
        harness.activate()

        status = harness.status_lines()[-1]["status"]
        self.assertIs(status["haptics_available"], False)
        self.assertEqual(status["haptics_detail"], "机身振动暂不可用")

    def test_preview_message_ticks_without_input_or_sink_events(self):
        harness = Harness()
        harness.activate()
        haptics = harness.haptics
        haptics.previews.clear()
        batches = len(harness.sink.batches)
        neutralized = harness.sink.neutralized

        harness.daemon.handle_message({"type": "preview_haptics"})

        self.assertEqual(haptics.previews, [harness.now])
        self.assertEqual(len(harness.sink.batches), batches)
        self.assertEqual(harness.sink.neutralized, neutralized)
        self.assertEqual(harness.touch.events, [])

    def test_preview_is_bounded_to_150ms(self):
        harness = Harness()
        harness.activate()
        haptics = harness.haptics
        haptics.previews.clear()
        start = harness.now

        harness.daemon.handle_message({"type": "preview_haptics"})
        harness.now = start + 0.149
        harness.daemon.handle_message({"type": "preview_haptics"})
        self.assertEqual(len(haptics.previews), 1)

        harness.now = start + 0.15
        harness.daemon.handle_message({"type": "preview_haptics"})
        self.assertEqual(len(haptics.previews), 2)

    def test_preview_requires_enabled_haptics_and_nonzero_strength(self):
        harness = Harness()
        harness.activate_with({"haptics_enabled": False})
        harness.haptics.previews.clear()
        harness.daemon.handle_message({"type": "preview_haptics"})
        self.assertEqual(harness.haptics.previews, [])

        harness = Harness()
        harness.activate_with({"haptics_strength": 0})
        harness.haptics.previews.clear()
        harness.daemon.handle_message({"type": "preview_haptics"})
        self.assertEqual(harness.haptics.previews, [])

        harness = Harness()
        harness.activate()
        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": False}}
        )
        harness.haptics.previews.clear()
        harness.daemon.handle_message({"type": "preview_haptics"})
        self.assertEqual(harness.haptics.previews, [])

    def test_settings_updates_push_the_new_strength(self):
        harness = Harness()
        harness.activate_with({"haptics_strength": 80})
        self.assertEqual(harness.haptics.strengths[-1], 80)

        harness.daemon.handle_message(
            {
                "type": "settings",
                "settings": {
                    "enabled": True,
                    "haptics_enabled": True,
                    "haptics_strength": 40,
                },
            }
        )
        self.assertEqual(harness.haptics.strengths[-1], 40)


if __name__ == "__main__":
    unittest.main()
