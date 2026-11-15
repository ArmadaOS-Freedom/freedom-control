"""Behavioral contract tests for the freedom_touch runtime policy module."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_modules.freedom_touch.layout import default_layout, validate_layout
from py_modules.freedom_touch.runtime_policy import (
    FrameAccumulator,
    choose_composite,
    effective_layout,
    should_capture,
    validate_settings,
)

COMPOSITE_IFACE = "org.shadowblip.Input.CompositeDevice"
FOREIGN_COMPOSITE_IFACE = "org.shadowblip.Input.Gamepad"

# Raw evdev constants, deliberately numeric so these tests do not depend on
# python-evdev being installed.
EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
SYN_DROPPED = 3
MT_SLOT = 47
MT_TRACKING_ID = 57
MT_POSITION_X = 53
MT_POSITION_Y = 54


def composite(name, sources):
    return {
        COMPOSITE_IFACE: {
            "Name": name,
            "SourceDevicePaths": list(sources),
        }
    }


class ShouldCaptureTests(unittest.TestCase):
    def test_matching_game_session_is_captured(self):
        self.assertTrue(should_capture(True, 12345, 12345, 12345, 1.5, True))
        self.assertTrue(should_capture(True, 12345, 12345, 12345, 0.0, True))
        self.assertTrue(should_capture(True, 12345, 12345, 12345, 3.0, True))

    def test_disabled_or_not_ready_is_never_captured(self):
        self.assertFalse(should_capture(False, 12345, 12345, 12345, 1.0, True))
        self.assertFalse(should_capture(True, 12345, 12345, 12345, 1.0, False))

    def test_heartbeat_must_be_finite_and_within_three_seconds(self):
        for age in (-0.001, 3.001, None, float("nan"), float("inf")):
            with self.subTest(age=age):
                self.assertFalse(
                    should_capture(True, 12345, 12345, 12345, age, True)
                )

    def test_steam_ids_zero_negative_and_qam_are_rejected(self):
        for running in (0, -1, 769):
            with self.subTest(running=running):
                self.assertFalse(
                    should_capture(True, running, running, running, 1.0, True)
                )
        # QAM's input appid is 769 even while a real game owns graphics.
        self.assertFalse(should_capture(True, 12345, 769, 12345, 1.0, True))

    def test_running_input_and_gfx_must_be_the_same_app(self):
        self.assertFalse(should_capture(True, 12345, 54321, 12345, 1.0, True))
        self.assertFalse(should_capture(True, 12345, 12345, 54321, 1.0, True))

    def test_numeric_strings_wide_ids_and_unknown_apps(self):
        self.assertTrue(should_capture(True, "12345", "12345", "12345", 1.0, True))
        shortcut_id = (1 << 40) + 424242
        self.assertTrue(
            should_capture(
                True, shortcut_id, shortcut_id, shortcut_id, 1.0, True
            )
        )
        self.assertTrue(
            should_capture(
                True, str(shortcut_id), str(shortcut_id), str(shortcut_id), 1.0, True
            )
        )
        for value in (None, "", "not-an-app", "12.5"):
            with self.subTest(value=value):
                self.assertFalse(should_capture(True, value, value, value, 1.0, True))


class ChooseCompositeTests(unittest.TestCase):
    OWN = "/dev/input/event12"
    FOREIGN = "/dev/input/event7"

    def test_selects_only_the_composite_with_our_exact_source(self):
        objects = {
            "/org/shadowblip/InputPlumber/devices/1": composite(
                "G9 Pro", [self.FOREIGN]
            ),
            "/org/shadowblip/InputPlumber/devices/2": composite(
                "Freedom Touch Controller", ["/dev/input/event13", self.OWN]
            ),
        }
        self.assertEqual(
            choose_composite(objects, self.OWN),
            "/org/shadowblip/InputPlumber/devices/2",
        )

    def test_foreign_composites_non_composites_and_prefix_paths_yield_none(self):
        objects = {
            "/org/shadowblip/InputPlumber/devices/1": composite(
                "G9 Pro", [self.OWN]
            ),
            "/org/shadowblip/InputPlumber/devices/2": {
                FOREIGN_COMPOSITE_IFACE: {"Name": "Freedom Touch Controller"}
            },
        }
        self.assertIsNone(choose_composite(objects, self.OWN))
        self.assertIsNone(choose_composite({}, self.OWN))
        prefix_only = {
            "/org/shadowblip/InputPlumber/devices/1": composite(
                "Freedom Touch Controller", ["/dev/input/event120"]
            )
        }
        self.assertIsNone(choose_composite(prefix_only, self.OWN))

    def test_wrong_name_or_ambiguous_match_yields_none(self):
        wrong_name = {
            "/org/shadowblip/InputPlumber/devices/1": composite(
                "Freedom Touch Controller (copy)", [self.OWN]
            )
        }
        ambiguous = {
            "/org/shadowblip/InputPlumber/devices/1": composite(
                "Freedom Touch Controller", [self.OWN]
            ),
            "/org/shadowblip/InputPlumber/devices/2": composite(
                "Freedom Touch Controller", [self.OWN]
            ),
        }
        self.assertIsNone(choose_composite(wrong_name, self.OWN))
        self.assertIsNone(choose_composite(ambiguous, self.OWN))


class ValidateSettingsTests(unittest.TestCase):
    def test_defaults_match_the_canonical_settings(self):
        settings = validate_settings({})
        self.assertEqual(
            set(settings),
            {
                "enabled",
                "opacity",
                "scale",
                "deadzone",
                "edge_swipes",
                "haptics_enabled",
                "haptics_strength",
                "layout",
                "active_preset_id",
                "custom_presets",
                "version",
            },
        )
        self.assertIs(settings["enabled"], False)
        self.assertEqual(settings["opacity"], 0.45)
        self.assertEqual(settings["scale"], 1.0)
        self.assertEqual(settings["deadzone"], 0.1)
        self.assertIs(settings["edge_swipes"], False)
        self.assertIs(settings["haptics_enabled"], True)
        self.assertEqual(settings["haptics_strength"], 25)
        self.assertEqual(settings["version"], 2)
        self.assertEqual(settings["active_preset_id"], "tablet_standard")
        self.assertEqual(settings["custom_presets"], [])
        self.assertEqual(settings["layout"], validate_layout(default_layout()))

    def test_explicit_valid_settings_are_canonicalized(self):
        layout = validate_layout(
            [
                {
                    "id": "btn_a",
                    "kind": "button",
                    "x": 0.1,
                    "y": 0.2,
                    "w": 0.1,
                    "h": 0.1,
                    "output": "a",
                }
            ]
        )
        settings = validate_settings(
            {
                "enabled": True,
                "opacity": 1,
                "scale": 0.6,
                "deadzone": 0,
                "layout": layout,
            }
        )
        self.assertIs(settings["enabled"], True)
        self.assertEqual(settings["opacity"], 1.0)
        self.assertIsInstance(settings["opacity"], float)
        self.assertEqual(settings["scale"], 0.6)
        self.assertIsInstance(settings["scale"], float)
        self.assertEqual(settings["deadzone"], 0.0)
        self.assertIsInstance(settings["deadzone"], float)
        self.assertEqual(settings["layout"], layout)
        self.assertEqual(settings["version"], 2)

    def test_wrong_types_nonfinite_and_out_of_range_values_raise(self):
        invalid = [
            {"enabled": "false"},
            {"opacity": True},
            {"opacity": "0.5"},
            {"opacity": float("nan")},
            {"scale": float("inf")},
            {"opacity": 0.14},
            {"opacity": 1.01},
            {"scale": 0.59},
            {"scale": 1.41},
            {"deadzone": -0.01},
            {"deadzone": 0.51},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_settings(value)

    def test_haptics_flag_is_boolean_and_defaults_on(self):
        self.assertIs(validate_settings({"haptics_enabled": False})[
            "haptics_enabled"
        ], False)
        self.assertIs(validate_settings({"haptics_enabled": True})[
            "haptics_enabled"
        ], True)
        for value in (0, 1, "false", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_settings({"haptics_enabled": value})

    def test_invalid_explicit_layout_raises_instead_of_falling_back(self):
        with self.assertRaises(ValueError):
            validate_settings({"layout": [{"id": "broken"}]})
        with self.assertRaises(ValueError):
            validate_settings({"layout": "not-a-layout"})

    def test_v1_migration_preserves_raw_layout_and_activates_standard(self):
        legacy_layout = [
            {
                "id": "old_a",
                "kind": "button",
                "x": 0.1,
                "y": 0.2,
                "w": 0.1,
                "h": 0.1,
                "output": "a",
            }
        ]
        raw = {
            "enabled": True,
            "opacity": 0.7,
            "scale": 1.25,
            "deadzone": 0.3,
            "layout": legacy_layout,
            "version": 1,
            "haptics_enabled": False,
        }

        settings = validate_settings(raw)

        self.assertIs(settings["enabled"], True)
        self.assertEqual(settings["opacity"], 0.7)
        self.assertEqual(settings["deadzone"], 0.3)
        self.assertEqual(settings["version"], 2)
        self.assertIs(settings["edge_swipes"], False)
        self.assertIs(settings["haptics_enabled"], False)
        self.assertEqual(settings["active_preset_id"], "tablet_standard")
        self.assertEqual(settings["scale"], 1.0)
        self.assertEqual(settings["layout"], validate_layout(default_layout()))
        self.assertEqual(len(settings["custom_presets"]), 1)
        legacy = settings["custom_presets"][0]
        self.assertEqual(legacy["id"], "custom_legacy")
        self.assertEqual(legacy["name"], "原布局")
        self.assertEqual(legacy["scale"], 1.25)
        self.assertEqual(legacy["layout"], validate_layout(legacy_layout))

    def test_new_or_empty_settings_have_no_legacy_preset(self):
        for raw in ({}, {"version": 2}, {"enabled": False}):
            with self.subTest(raw=raw):
                settings = validate_settings(raw)
                self.assertEqual(settings["custom_presets"], [])
                self.assertEqual(settings["active_preset_id"], "tablet_standard")

    def test_v2_requires_active_preset_to_exist(self):
        with self.assertRaises(ValueError):
            validate_settings({"active_preset_id": "missing"})
        with self.assertRaises(ValueError):
            validate_settings({"active_preset_id": ""})
        with self.assertRaises(ValueError):
            validate_settings({"active_preset_id": 5})


class EffectiveLayoutTests(unittest.TestCase):
    def layout(self):
        return [
            {
                "id": "pad_r",
                "kind": "pad",
                "x": 0.4,
                "y": 0.4,
                "w": 0.2,
                "h": 0.2,
                "output": "right",
            }
        ]

    def test_scale_about_center_and_input_is_not_modified(self):
        layout = self.layout()
        original = copy.deepcopy(layout)
        scaled = effective_layout(layout, 1.5)
        self.assertEqual(layout, original)
        self.assertEqual(len(scaled), 1)
        rect = scaled[0]
        self.assertEqual(rect["id"], "pad_r")
        self.assertAlmostEqual(rect["w"], 0.3)
        self.assertAlmostEqual(rect["h"], 0.3)
        self.assertAlmostEqual(rect["x"], 0.35)
        self.assertAlmostEqual(rect["y"], 0.35)

    def test_scaling_preserves_an_offcenter_controls_center(self):
        layout = [
            {
                "id": "offcenter",
                "kind": "pad",
                "x": 0.1,
                "y": 0.2,
                "w": 0.1,
                "h": 0.2,
                "output": "left",
            }
        ]
        rect = effective_layout(layout, 1.2)[0]
        self.assertAlmostEqual(rect["x"], 0.09)
        self.assertAlmostEqual(rect["y"], 0.18)
        self.assertAlmostEqual(rect["w"], 0.12)
        self.assertAlmostEqual(rect["h"], 0.24)

    def test_edge_clamping_shifts_without_resizing(self):
        layout = [
            {
                "id": "edge",
                "kind": "pad",
                "x": 0.6,
                "y": 0.4,
                "w": 0.4,
                "h": 0.2,
                "output": "right",
            }
        ]
        rect = effective_layout(layout, 1.2)[0]
        self.assertAlmostEqual(rect["w"], 0.48)
        self.assertAlmostEqual(rect["h"], 0.24)
        self.assertAlmostEqual(rect["x"], 0.52)
        self.assertAlmostEqual(rect["y"], 0.38)
        self.assertLessEqual(rect["x"] + rect["w"], 1.0)
        self.assertLessEqual(rect["y"] + rect["h"], 1.0)

    def test_oversized_result_is_capped_to_unit_bounds(self):
        layout = [
            {
                "id": "huge",
                "kind": "pad",
                "x": 0.0,
                "y": 0.0,
                "w": 0.9,
                "h": 0.9,
                "output": "left",
            }
        ]
        rect = effective_layout(layout, 1.4)[0]
        self.assertLessEqual(rect["w"], 1.0)
        self.assertLessEqual(rect["h"], 1.0)
        self.assertAlmostEqual(rect["w"], rect["h"])
        self.assertGreaterEqual(rect["x"], 0.0)
        self.assertGreaterEqual(rect["y"], 0.0)
        self.assertLessEqual(rect["x"] + rect["w"], 1.0)
        self.assertLessEqual(rect["y"] + rect["h"], 1.0)


class FrameAccumulatorTests(unittest.TestCase):
    def setUp(self):
        self.accumulator = FrameAccumulator()

    def feed(self, *events):
        result = []
        for event_type, code, value in events:
            result.extend(self.accumulator.consume(event_type, code, value))
        return result

    def begin(self, slot, tracking_id, x, y):
        return (
            (EV_ABS, MT_SLOT, slot),
            (EV_ABS, MT_TRACKING_ID, tracking_id),
            (EV_ABS, MT_POSITION_X, x),
            (EV_ABS, MT_POSITION_Y, y),
            (EV_SYN, SYN_REPORT, 0),
        )

    def test_begin_waits_for_syn_report_and_both_coordinates(self):
        self.assertEqual(self.feed((EV_ABS, MT_TRACKING_ID, 100)), [])
        self.assertEqual(self.feed((EV_ABS, MT_POSITION_X, 10)), [])
        self.assertEqual(self.feed((EV_SYN, SYN_REPORT, 0)), [])
        self.assertEqual(self.feed((EV_ABS, MT_POSITION_Y, 20)), [])
        self.assertEqual(
            self.feed((EV_SYN, SYN_REPORT, 0)),
            [("begin", 100, 10, 20)],
        )

    def test_multiple_contacts_emit_once_per_frame_and_track_moves(self):
        events = self.begin(0, 5, 10, 11) + self.begin(1, 6, 20, 21)
        started = self.feed(*events)
        self.assertEqual(
            sorted(started),
            [("begin", 5, 10, 11), ("begin", 6, 20, 21)],
        )
        for event in started:
            self.assertIsInstance(event[1], int)
            self.assertIsInstance(event[2], int)
            self.assertIsInstance(event[3], int)

        moved = self.feed(
            (EV_ABS, MT_SLOT, 0),
            (EV_ABS, MT_POSITION_X, 12),
            (EV_ABS, MT_POSITION_Y, 13),
            (EV_SYN, SYN_REPORT, 0),
        )
        self.assertEqual(moved, [("move", 5, 12, 13)])

    def test_release_old_then_begin_new_tracking_id_in_same_slot(self):
        self.feed(*self.begin(0, 5, 10, 11))
        events = self.feed(
            (EV_ABS, MT_SLOT, 0),
            (EV_ABS, MT_TRACKING_ID, -1),
            (EV_ABS, MT_TRACKING_ID, 7),
            (EV_ABS, MT_POSITION_X, 30),
            (EV_ABS, MT_POSITION_Y, 31),
            (EV_SYN, SYN_REPORT, 0),
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "end")
        self.assertEqual(events[0][1], 5)
        self.assertEqual(events[1], ("begin", 7, 30, 31))

    def test_syn_dropped_emits_one_cancel_and_clears_state(self):
        self.feed(*self.begin(0, 5, 10, 11))
        self.assertEqual(
            self.feed((EV_SYN, SYN_DROPPED, 0)),
            [("cancel", None, None, None)],
        )
        self.assertEqual(self.feed((EV_SYN, SYN_REPORT, 0)), [])
        self.assertEqual(
            self.feed(*self.begin(0, 9, 1, 2)),
            [("begin", 9, 1, 2)],
        )


if __name__ == "__main__":
    unittest.main()
