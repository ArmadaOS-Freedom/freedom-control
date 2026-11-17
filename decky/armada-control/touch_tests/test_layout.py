"""Behavioral tests for the normalized touch layout contract."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_modules.freedom_touch import layout
from py_modules.freedom_touch.engine import TouchEngine
from py_modules.freedom_touch.runtime_policy import effective_layout

BUTTON_OUTPUTS = {
    "a", "b", "x", "y", "lb", "rb", "lt", "rt",
    "start", "back", "guide", "qam", "l3", "r3",
}
CONTROL_KEYS = {"id", "kind", "x", "y", "w", "h", "output"}


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


def overlaps(first, second):
    return (
        first["x"] < second["x"] + second["w"]
        and second["x"] < first["x"] + first["w"]
        and first["y"] < second["y"] + second["h"]
        and second["y"] < first["y"] + first["h"]
    )


BUILTIN_LAYOUT_FACTORIES = {
    "standard": layout.tablet_standard_layout,
    "compact": layout.tablet_compact_layout,
    "trackpads": layout.tablet_trackpads_layout,
}
SCALES = (0.6, 1.0, 1.4)


def center(rect):
    return (
        rect["x"] + rect["w"] / 2.0,
        rect["y"] + rect["h"] / 2.0,
    )


class DefaultLayoutTests(unittest.TestCase):
    def test_default_layout_has_every_button_output_once(self):
        controls = layout.default_layout()
        buttons = [c for c in controls if c["kind"] == "button"]
        self.assertEqual({c["output"] for c in buttons}, BUTTON_OUTPUTS)
        self.assertEqual(len(buttons), len(BUTTON_OUTPUTS))
        ids = [c["id"] for c in controls]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_layout_covers_all_kinds_and_pad_sides(self):
        controls = layout.default_layout()
        kinds = {c["kind"] for c in controls}
        self.assertEqual(kinds, {"button", "stick", "dpad", "pad"})
        sided = [c for c in controls if c["kind"] in ("stick", "pad")]
        self.assertEqual({c["output"] for c in sided}, {"left", "right"})

    def test_default_abxy_positions_match_semantics(self):
        controls = {c["id"]: c for c in layout.default_layout()}

        def center_x(item):
            return item["x"] + item["w"] / 2.0

        def center_y(item):
            return item["y"] + item["h"] / 2.0

        y = controls["btn_y"]
        x = controls["btn_x"]
        b = controls["btn_b"]
        a = controls["btn_a"]

        # Y sits above the rest of the diamond.
        self.assertLess(center_y(y), center_y(x))
        self.assertLess(center_y(y), center_y(a))
        self.assertLess(center_y(y), center_y(b))

        # X is the left-hand button, B the right-hand one.
        self.assertLess(center_x(x), center_x(b))
        self.assertLess(center_x(x), center_x(a))
        self.assertLess(center_x(x), center_x(y))
        self.assertGreater(center_x(b), center_x(a))
        self.assertGreater(center_x(b), center_x(y))

        # A sits below X and B.
        self.assertGreater(center_y(a), center_y(y))
        self.assertGreater(center_y(a), center_y(x))
        self.assertGreater(center_y(a), center_y(b))

    def test_default_layout_is_valid_with_normalized_rects(self):
        controls = layout.validate_layout(layout.default_layout())
        self.assertIsInstance(controls, list)
        for item in controls:
            self.assertTrue(CONTROL_KEYS.issubset(item))
            self.assertGreater(item["w"], 0.0)
            self.assertGreater(item["h"], 0.0)
            self.assertGreaterEqual(item["x"], 0.0)
            self.assertGreaterEqual(item["y"], 0.0)
            self.assertLessEqual(item["x"] + item["w"], 1.0)
            self.assertLessEqual(item["y"] + item["h"], 1.0)

    def test_tablet_standard_matches_the_requested_geometry(self):
        controls = {c["id"]: c for c in layout.default_layout()}
        expected = {
            "stick_l": (0.052, 0.682, 0.136, 0.216),
            "stick_r": (0.812, 0.682, 0.136, 0.216),
            "dpad_l": (0.038, 0.406, 0.164, 0.248),
            "btn_y": (0.854, 0.406, 0.052, 0.08),
            "btn_x": (0.798, 0.490, 0.052, 0.08),
            "btn_b": (0.910, 0.490, 0.052, 0.08),
            "btn_a": (0.854, 0.574, 0.052, 0.08),
            "pad_l": (0.299, 0.670, 0.152, 0.24),
            "pad_r": (0.549, 0.670, 0.152, 0.24),
            "btn_lb": (0.03, 0.29, 0.08, 0.06),
            "btn_lt": (0.14, 0.29, 0.08, 0.06),
            "btn_rt": (0.78, 0.29, 0.08, 0.06),
            "btn_rb": (0.89, 0.29, 0.08, 0.06),
            "btn_guide": (0.010, 0.0275, 0.072, 0.060),
            "btn_qam": (0.918, 0.0275, 0.072, 0.060),
            "btn_back": (0.404, 0.0275, 0.072, 0.060),
            "btn_start": (0.524, 0.0275, 0.072, 0.060),
            "btn_l3": (0.092, 0.936, 0.056, 0.048),
            "btn_r3": (0.852, 0.936, 0.056, 0.048),
        }
        for control_id, rect in expected.items():
            with self.subTest(control=control_id):
                actual = controls[control_id]
                for field, value in zip(("x", "y", "w", "h"), rect):
                    self.assertAlmostEqual(actual[field], value)

    def test_system_keys_use_top_corners_and_upper_center(self):
        controls = {c["id"]: c for c in layout.default_layout()}
        guide = controls["btn_guide"]
        qam = controls["btn_qam"]
        back = controls["btn_back"]
        start = controls["btn_start"]

        # Correct utility labels (guide/qam corner menus, back/start middle).
        self.assertEqual(guide["label"], "左侧栏")
        self.assertEqual(qam["label"], "右侧栏")
        self.assertEqual(back["label"], "返回")
        self.assertEqual(start["label"], "开始")

        # All four share the same top centre line cy=.0575, mirroring about
        # x=.5 (guide<->qam, back<->start).
        for c in (guide, qam, back, start):
            self.assertAlmostEqual(c["y"] + c["h"] / 2.0, 0.0575)
        for left, right in ((guide, qam), (back, start)):
            self.assertAlmostEqual(
                left["x"], 1.0 - (right["x"] + right["w"])
            )

        # All utility keys stay within the top extent.
        for c in (guide, qam, back, start):
            self.assertLessEqual(c["y"] + c["h"], 0.1)

        # Guide/QAM occupy opposite extreme top corners.
        self.assertLess(guide["x"] + guide["w"], 0.1)
        self.assertGreater(qam["x"], 0.9)

        # Back/Start sit at the upper middle with centres .44/.56.
        self.assertAlmostEqual(back["x"] + back["w"] / 2.0, 0.44)
        self.assertAlmostEqual(start["x"] + start["w"] / 2.0, 0.56)

    def test_catalog_layouts_keep_nineteen_controls_without_overlap(self):
        catalogs = {
            "standard": layout.tablet_standard_layout(),
            "compact": layout.tablet_compact_layout(),
            "trackpads": layout.tablet_trackpads_layout(),
        }
        expected_ids = [item["id"] for item in layout.default_layout()]
        for name, controls in catalogs.items():
            with self.subTest(layout=name):
                self.assertEqual(len(controls), 19)
                self.assertEqual([item["id"] for item in controls], expected_ids)
                validated = layout.validate_layout(controls)
                self.assertEqual(len(validated), 19)
                for position, first in enumerate(validated):
                    for second in validated[position + 1:]:
                        self.assertFalse(
                            overlaps(first, second),
                            "%s overlaps %s in %s"
                            % (first["id"], second["id"], name),
                        )

    def test_sticks_pads_and_system_keys_use_chinese_labels(self):
        controls = {c["id"]: c for c in layout.default_layout()}
        chinese = (
            "stick_l", "stick_r", "dpad_l", "pad_l", "pad_r",
            "btn_back", "btn_guide", "btn_start", "btn_qam",
        )
        for control_id in chinese:
            with self.subTest(control=control_id):
                label = controls[control_id]["label"]
                self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in label))

        for control_id in ("btn_a", "btn_b", "btn_x", "btn_y",
                           "btn_lb", "btn_rb", "btn_lt", "btn_rt",
                           "btn_l3", "btn_r3"):
            with self.subTest(control=control_id):
                label = controls[control_id]["label"]
                self.assertTrue(label.isascii())


class EffectiveLayoutHitTests(unittest.TestCase):
    def _assert_intended_control(self, expected, events):
        kind = expected["kind"]
        output = expected["output"]
        if kind == "button":
            self.assertIn(("button", output, True), events)
        elif kind == "stick":
            axis = "rx" if output == "right" else "lx"
            self.assertEqual(events[0][:2], ("axis", axis))
        elif kind == "dpad":
            self.assertEqual([event[1] for event in events], ["dx", "dy"])
        else:
            self.assertEqual(events[0][:2], ("pad", output))

    def test_every_control_center_hits_its_own_control_at_each_scale(self):
        for name, factory in BUILTIN_LAYOUT_FACTORIES.items():
            for scale in SCALES:
                with self.subTest(layout=name, scale=scale):
                    controls = layout.validate_layout(
                        effective_layout(factory(), scale)
                    )
                    engine = TouchEngine(controls)
                    engine.set_active(True)

                    for index, expected in enumerate(controls):
                        x, y = center(expected)
                        events = engine.begin(1000 + index, x, y, 0.0)
                        self.assertTrue(
                            events,
                            "%s produced no event at scale %s"
                            % (expected["id"], scale),
                        )
                        self._assert_intended_control(expected, events)
                        self.assertTrue(
                            engine.snapshot()["controls"][expected["id"]][
                                "pressed"
                            ],
                            "%s lost its center to another control"
                            % (expected["id"],),
                        )
                        engine.end(1000 + index, 0.1)

    def test_l3_and_r3_centers_are_reachable_at_max_trackpad_scale(self):
        controls = layout.validate_layout(
            effective_layout(layout.tablet_trackpads_layout(), 1.4)
        )
        by_id = {item["id"]: item for item in controls}
        engine = TouchEngine(controls)
        engine.set_active(True)

        for pointer, control_id, output in (
            (1, "btn_l3", "l3"),
            (2, "btn_r3", "r3"),
        ):
            x, y = center(by_id[control_id])
            with self.subTest(control=control_id):
                self.assertEqual(
                    engine.begin(pointer, x, y, 0.0),
                    [("button", output, True)],
                )
                self.assertTrue(
                    engine.snapshot()["controls"][control_id]["pressed"]
                )
                engine.end(pointer, 0.1)

    def test_l3_and_r3_sit_below_their_own_sticks(self):
        controls = {c["id"]: c for c in layout.default_layout()}
        self.assertGreaterEqual(
            controls["btn_l3"]["y"],
            controls["stick_l"]["y"] + controls["stick_l"]["h"],
        )
        self.assertGreaterEqual(
            controls["btn_r3"]["y"],
            controls["stick_r"]["y"] + controls["stick_r"]["h"],
        )

    def test_effective_layout_at_base_scale_preserves_bounded_defaults(self):
        for name, factory in BUILTIN_LAYOUT_FACTORIES.items():
            source = factory()
            effective = effective_layout(source, 1.0)
            self.assertEqual(len(effective), len(source))
            for original, scaled in zip(source, effective):
                with self.subTest(layout=name, control=original["id"]):
                    self.assertAlmostEqual(scaled["x"], original["x"])
                    self.assertAlmostEqual(scaled["y"], original["y"])
                    self.assertAlmostEqual(scaled["w"], original["w"])
                    self.assertAlmostEqual(scaled["h"], original["h"])
                    self.assertGreaterEqual(scaled["x"], 0.0)
                    self.assertGreaterEqual(scaled["y"], 0.0)
                    self.assertLessEqual(scaled["x"] + scaled["w"], 1.0)
                    self.assertLessEqual(scaled["y"] + scaled["h"], 1.0)


class ValidateLayoutTests(unittest.TestCase):
    def test_validate_returns_canonical_list(self):
        raw = [
            control("b1", "button", 0.0, 0.0, 0.1, 0.1, "a"),
            control("s1", "stick", 0.8, 0.8, 0.2, 0.2, "right"),
        ]
        result = layout.validate_layout(raw)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual({c["id"] for c in result}, {"b1", "s1"})
        for item in result:
            self.assertTrue(CONTROL_KEYS.issubset(item))

    def test_validate_rejects_duplicate_ids(self):
        raw = [
            control("same", "button", 0.0, 0.0, 0.1, 0.1, "a"),
            control("same", "button", 0.2, 0.0, 0.1, 0.1, "b"),
        ]
        with self.assertRaises(ValueError):
            layout.validate_layout(raw)

    def test_validate_rejects_duplicate_analog_outputs_and_dpad_right(self):
        cases = {
            "two sticks on left": [
                control("s1", "stick", 0.0, 0.0, 0.2, 0.2, "left"),
                control("s2", "stick", 0.3, 0.0, 0.2, 0.2, "left"),
            ],
            "two pads on right": [
                control("p1", "pad", 0.0, 0.3, 0.2, 0.2, "right"),
                control("p2", "pad", 0.3, 0.3, 0.2, 0.2, "right"),
            ],
            "two dpads on left": [
                control("d1", "dpad", 0.0, 0.6, 0.2, 0.2, "left"),
                control("d2", "dpad", 0.3, 0.6, 0.2, 0.2, "left"),
            ],
            "single dpad on right": [
                control("d1", "dpad", 0.6, 0.0, 0.2, 0.2, "right"),
            ],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    layout.validate_layout(raw)

        # Shared button outputs are still legal; the engine reference counts
        # them, so validation must not reject them.
        layout.validate_layout(
            [
                control("b1", "button", 0.0, 0.0, 0.1, 0.1, "a"),
                control("b2", "button", 0.2, 0.0, 0.1, 0.1, "a"),
            ]
        )

    def test_validate_rejects_nonfinite_rect(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            raw = [control("b1", "button", bad, 0.0, 0.1, 0.1, "a")]
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    layout.validate_layout(raw)

    def test_validate_rejects_out_of_range_rect(self):
        cases = [
            control("b1", "button", -0.01, 0.0, 0.1, 0.1, "a"),
            control("b2", "button", 0.0, 0.95, 0.1, 0.1, "b"),
            control("b3", "button", 0.0, 0.0, 1.1, 0.1, "x"),
        ]
        for raw in cases:
            with self.subTest(control=raw["id"]):
                with self.assertRaises(ValueError):
                    layout.validate_layout([raw])

    def test_validate_rejects_unknown_kind_and_output(self):
        with self.assertRaises(ValueError):
            layout.validate_layout(
                [control("b1", "trigger", 0.0, 0.0, 0.1, 0.1, "a")]
            )
        with self.assertRaises(ValueError):
            layout.validate_layout(
                [control("b2", "button", 0.0, 0.0, 0.1, 0.1, "turbo")]
            )


class TransformPointTests(unittest.TestCase):
    def test_identity_and_clamp(self):
        x, y = layout.transform_point(0.25, 0.75, 0.0, 1.0, 0.0, 1.0, 0)
        self.assertAlmostEqual(x, 0.25)
        self.assertAlmostEqual(y, 0.75)
        x, y = layout.transform_point(-0.5, 1.5, 0.0, 1.0, 0.0, 1.0, 0)
        self.assertEqual((x, y), (0.0, 1.0))

    def test_four_rotations(self):
        cases = {
            0: (0.25, 0.75),
            90: (0.75, 0.75),
            180: (0.75, 0.25),
            270: (0.25, 0.25),
        }
        for rotation, expected in cases.items():
            with self.subTest(rotation=rotation):
                x, y = layout.transform_point(
                    0.25, 0.75, 0.0, 1.0, 0.0, 1.0, rotation
                )
                self.assertAlmostEqual(x, expected[0])
                self.assertAlmostEqual(y, expected[1])

        corners = {90: (0.0, 1.0), 180: (1.0, 1.0), 270: (1.0, 0.0)}
        for rotation, expected in corners.items():
            with self.subTest(rotation=rotation, corner=(0.0, 0.0)):
                self.assertEqual(
                    layout.transform_point(0.0, 0.0, 0.0, 1.0, 0.0, 1.0, rotation),
                    expected,
                )

    def test_nonzero_min_maps_into_unit_range(self):
        x, y = layout.transform_point(0.3, 0.4, 0.2, 1.0, 0.1, 0.5, 0)
        self.assertAlmostEqual(x, 0.125)
        self.assertAlmostEqual(y, 0.75)
        rx, ry = layout.transform_point(0.3, 0.4, 0.2, 1.0, 0.1, 0.5, 90)
        self.assertAlmostEqual(rx, 0.75)
        self.assertAlmostEqual(ry, 0.875)

    def test_zero_range_is_rejected(self):
        with self.assertRaises(ValueError):
            layout.transform_point(0.5, 0.5, 0.5, 0.5, 0.0, 1.0, 0)
        with self.assertRaises(ValueError):
            layout.transform_point(0.5, 0.5, 0.0, 1.0, 0.5, 0.5, 90)


if __name__ == "__main__":
    unittest.main()
