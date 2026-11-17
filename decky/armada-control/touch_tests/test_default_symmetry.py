"""Physical symmetry tests for circular touch sticks."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_modules.freedom_touch import layout
from py_modules.freedom_touch.runtime_policy import effective_layout

ASPECTS = (956.0 / 599.0, 1920.0 / 1080.0, 3040.0 / 1904.0)
SCALES = (0.6, 1.0, 1.4)
LAYOUTS = {
    "standard": layout.tablet_standard_layout,
    "ps": layout.ps_layout,
}


def _by_id(controls):
    return {item["id"]: item for item in controls}


def _circular_sticks(controls, aspect):
    result = []
    for item in controls:
        if item["kind"] != "stick":
            continue
        circle = layout.circular_stick_rect(item, aspect)
        result.append(circle)
    return _by_id(result)


def _center(rect):
    return (
        rect["x"] + rect["w"] / 2.0,
        rect["y"] + rect["h"] / 2.0,
    )


def _left_margin(rect):
    return rect["x"]


def _right_margin(rect):
    return 1.0 - (rect["x"] + rect["w"])


class DefaultSymmetryTests(unittest.TestCase):
    def test_fresh_factory_calls_are_independent(self):
        for name, factory in LAYOUTS.items():
            with self.subTest(layout=name):
                first = factory()
                second = factory()
                self.assertIsNot(first, second)
                self.assertEqual(first, second)
                for item in first:
                    self.assertIsInstance(item, dict)

                first[0]["x"] = first[0]["x"] + 12345.0
                self.assertNotEqual(first, second)
                third = factory()
                self.assertNotEqual(first, third)
                self.assertEqual(second, factory())

    def test_effective_layout_does_not_mutate_input(self):
        for name, factory in LAYOUTS.items():
            for scale in SCALES:
                with self.subTest(layout=name, scale=scale):
                    source = factory()
                    snapshot = copy.deepcopy(source)
                    effective_layout(source, scale)
                    self.assertEqual(source, snapshot)

    def test_circular_stick_rect_does_not_mutate_input(self):
        for name, factory in LAYOUTS.items():
            for aspect in ASPECTS:
                with self.subTest(layout=name, aspect=aspect):
                    source = factory()
                    snapshot = copy.deepcopy(source)
                    for item in source:
                        if item["kind"] != "stick":
                            continue
                        layout.circular_stick_rect(item, aspect)
                    self.assertEqual(source, snapshot)

    def test_both_sticks_match_physical_geometry(self):
        for name, factory in LAYOUTS.items():
            for aspect in ASPECTS:
                for scale in SCALES:
                    with self.subTest(
                        layout=name, aspect=aspect, scale=scale
                    ):
                        source = factory()
                        sticks = _circular_sticks(
                            effective_layout(source, scale), aspect
                        )
                        left = sticks["stick_l"]
                        right = sticks["stick_r"]

                        # Equal device-pixel diameter.
                        self.assertAlmostEqual(
                            left["w"], right["w"], places=9
                        )
                        self.assertAlmostEqual(
                            left["w"] * aspect,
                            left["h"],
                            places=9,
                        )
                        self.assertAlmostEqual(
                            right["w"] * aspect,
                            right["h"],
                            places=9,
                        )

                        # Same horizontal center line.
                        _, left_y = _center(left)
                        _, right_y = _center(right)
                        self.assertAlmostEqual(left_y, right_y, places=9)

                        # Mirrored left/right edge margins.
                        self.assertAlmostEqual(
                            _left_margin(left),
                            _right_margin(right),
                            places=9,
                        )

    def test_face_diamond_clear_of_right_stick_at_scale_one(self):
        factory = layout.tablet_standard_layout
        for aspect in ASPECTS:
            with self.subTest(aspect=aspect):
                sticks = _circular_sticks(
                    effective_layout(factory(), 1.0), aspect
                )
                stick_top = sticks["stick_r"]["y"]
                face_bottoms = {
                    item["id"]: item["y"] + item["h"]
                    for item in factory()
                    if item["kind"] == "button"
                    and item["id"] in ("btn_y", "btn_x", "btn_b", "btn_a")
                }
                # Every face button must sit above the stick's upper edge.
                for control_id, bottom in face_bottoms.items():
                    with self.subTest(control=control_id):
                        self.assertLess(bottom, stick_top)


if __name__ == "__main__":
    unittest.main()
