"""Tests for circular stick projection shared by layout, engine and draw."""

import unittest

import cairo

import sys

from py_modules.freedom_touch import draw as _draw
from py_modules.freedom_touch import layout as _layout
from py_modules.freedom_touch.engine import TouchEngine


ASPECTS = ((1920, 1080), (3040, 1904), (600, 960))


def _default_layout():
    return _layout.default_layout()


def _find(layout, control_id):
    for control in layout:
        if control["id"] == control_id:
            return control
    raise KeyError(control_id)


def _alpha_bbox(surface):
    """Return the alpha>8 bounding box of a cairo ImageSurface.

    Threshold 8 is required to include the translucent outer ring of a
    stick; the opaque inner thumb alone would hide projection ellipses.
    """

    surface.flush()
    width = surface.get_width()
    height = surface.get_height()
    stride = surface.get_stride()
    data = surface.get_data()
    # cairo ARGB32 is native-endian premultiplied; alpha is the top byte of
    # each 32-bit pixel on every supported little- or big-endian platform
    # only when read in native order.  Use a Cairo buffer accessor instead:
    # re-create the surface via bytearray view in device order.
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    pixels = bytes(data)
    if surface.get_format() != cairo.FORMAT_ARGB32:
        raise AssertionError("unexpected surface format")
    little = _is_little_endian()
    for row in range(height):
        base = row * stride
        for col in range(width):
            offset = base + col * 4
            alpha = pixels[offset + 3] if little else pixels[offset]
            if alpha > 8:
                if col < min_x:
                    min_x = col
                if col > max_x:
                    max_x = col
                if row < min_y:
                    min_y = row
                if row > max_y:
                    max_y = row
    if max_x < 0:
        return None
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _is_little_endian():
    return sys.byteorder == "little"


def _render_isolated(width, height, control):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    message = {
        "visible": True,
        "opacity": 1.0,
        "mode": "full",
        "chrome": False,
        "layout": [control],
        "snapshot": {"controls": {control["id"]: {"pressed": False}}},
    }
    _draw.draw_controls(context, width, height, message)
    return surface


class CircularStickRectTest(unittest.TestCase):
    def test_non_sticks_return_unchanged_copy(self):
        control = {
            "id": "btn_a",
            "kind": "button",
            "x": 0.1,
            "y": 0.2,
            "w": 0.3,
            "h": 0.4,
            "output": "a",
        }
        result = _layout.circular_stick_rect(control, 1.5)
        self.assertEqual(result, control)
        self.assertIsNot(result, control)

    def test_non_mutation(self):
        layout = _default_layout()
        control = _find(layout, "stick_l")
        snapshot = dict(control)
        _layout.circular_stick_rect(control, 1920.0 / 1080.0)
        self.assertEqual(control, snapshot)

    def test_default_left_right_equal_pixels(self):
        for width, height in ASPECTS:
            aspect = width / height
            layout = _default_layout()
            left = _layout.circular_stick_rect(
                _find(layout, "stick_l"), aspect
            )
            right = _layout.circular_stick_rect(
                _find(layout, "stick_r"), aspect
            )
            left_diameter = left["w"] * width
            right_diameter = right["w"] * width
            self.assertAlmostEqual(
                left_diameter, right_diameter, places=6, msg=(width, height)
            )
            self.assertAlmostEqual(
                left_diameter, 0.136 * width, places=6, msg=(width, height)
            )

    def test_projection_is_square_in_device_space(self):
        for width, height in ASPECTS:
            aspect = width / height
            layout = _default_layout()
            for control_id in ("stick_l", "stick_r"):
                projected = _layout.circular_stick_rect(
                    _find(layout, control_id), aspect
                )
                pixel_w = projected["w"] * width
                pixel_h = projected["h"] * height
                self.assertAlmostEqual(pixel_w, pixel_h, places=6)

    def test_projection_preserves_center(self):
        aspect = 16.0 / 9.0
        control = {
            "id": "stick_l",
            "kind": "stick",
            "x": 0.3,
            "y": 0.3,
            "w": 0.17,
            "h": 0.28,
            "output": "left",
        }
        projected = _layout.circular_stick_rect(control, aspect)
        self.assertAlmostEqual(
            projected["x"] + projected["w"] / 2.0,
            control["x"] + control["w"] / 2.0,
        )
        self.assertAlmostEqual(
            projected["y"] + projected["h"] / 2.0,
            control["y"] + control["h"] / 2.0,
        )

    def test_projection_clamped_at_edges(self):
        aspect = 16.0 / 9.0
        control = {
            "id": "stick_l",
            "kind": "stick",
            "x": 0.0,
            "y": 0.9,
            "w": 0.17,
            "h": 0.28,
            "output": "left",
        }
        projected = _layout.circular_stick_rect(control, aspect)
        self.assertGreaterEqual(projected["x"], 0.0)
        self.assertGreaterEqual(projected["y"], 0.0)
        self.assertLessEqual(projected["x"] + projected["w"], 1.0)
        self.assertLessEqual(projected["y"] + projected["h"], 1.0)

    def test_invalid_aspect_returns_unchanged_stick_copy(self):
        control = _find(_default_layout(), "stick_l")
        for bad in (0, -1, float("nan"), float("inf"), "x"):
            result = _layout.circular_stick_rect(control, bad)
            self.assertEqual(result, control)
            self.assertIsNot(result, control)


class EngineCircularStickTest(unittest.TestCase):
    def _engine(self, width, height):
        engine = TouchEngine(
            _default_layout(), aspect_ratio=width / height, deadzone=0.0
        )
        engine.set_active(True)
        return engine

    def test_cardinal_deflections_equal_magnitude(self):
        for width, height in ASPECTS:
            for control_id in ("stick_l", "stick_r"):
                layout = _default_layout()
                control = _layout.circular_stick_rect(
                    _find(layout, control_id), width / height
                )
                center_x = control["x"] + control["w"] / 2.0
                center_y = control["y"] + control["h"] / 2.0
                offsets = (
                    (control["w"] * 0.4, 0.0),
                    (-control["w"] * 0.4, 0.0),
                    (0.0, control["h"] * 0.4),
                    (0.0, -control["h"] * 0.4),
                )
                magnitudes = []
                for dx, dy in offsets:
                    engine = self._engine(width, height)
                    events = engine.begin(
                        "p", center_x + dx, center_y + dy, 0.0
                    )
                    axes = {name: value for kind, name, value in events
                            if kind == "axis"}
                    magnitudes.append(
                        abs(axes.get("lx", axes.get("rx", 0.0)))
                        + abs(axes.get("ly", axes.get("ry", 0.0)))
                    )
                self.assertTrue(
                    all(abs(m - magnitudes[0]) < 1e-6 for m in magnitudes),
                    (width, height, magnitudes),
                )

    def test_hit_uses_projected_circle_bounds(self):
        width, height = 1920, 1080
        aspect = width / height
        layout = _default_layout()
        original = _find(layout, "stick_l")
        projected = _layout.circular_stick_rect(original, aspect)
        engine = self._engine(width, height)
        center_x = projected["x"] + projected["w"] / 2.0
        center_y = projected["y"] + projected["h"] / 2.0
        # A point just below the projected bottom but inside the original
        # (taller) rectangle must not hit the stick.
        below_y = projected["y"] + projected["h"] + 0.001
        if below_y < original["y"] + original["h"]:
            events = engine.begin("p", center_x, below_y, 0.0)
            self.assertEqual(events, [])
        # A point at the projected bottom edge hits and uses the circle.
        events = engine.begin("p2", center_x, center_y + projected["h"] * 0.4, 0.0)
        self.assertTrue(any(kind == "axis" for kind, _, _ in events))

    def test_direct_stick_axes_matches_begin(self):
        engine = self._engine(1920, 1080)
        control = _find(engine.controls, "stick_l")
        center_x = control["x"] + control["w"] / 2.0
        center_y = control["y"] + control["h"] / 2.0
        expected = engine._stick_axes(control, center_x, center_y + 0.05)
        events = engine.begin("p", center_x, center_y + 0.05, 0.0)
        axes = {name: value for kind, name, value in events if kind == "axis"}
        self.assertAlmostEqual(axes["ly"], expected[1], places=9)


class CairoCircularStickTest(unittest.TestCase):
    def test_rendered_stick_bounding_box_square(self):
        width, height = 1920, 1080
        # Pass the RAW original stick: production draw_controls must perform
        # the circular projection itself.  Pre-projecting here would mask a
        # regression where draw paints the unprojected rectangle.
        original = _find(_default_layout(), "stick_l")
        surface = _render_isolated(width, height, original)
        bbox = _alpha_bbox(surface)
        self.assertIsNotNone(bbox)
        _, _, pixel_w, pixel_h = bbox
        self.assertLessEqual(abs(pixel_w - pixel_h), 4)

    def test_rendered_left_right_same_diameter(self):
        width, height = 1920, 1080
        diameters = []
        for control_id in ("stick_l", "stick_r"):
            control = _find(_default_layout(), control_id)
            surface = _render_isolated(width, height, control)
            bbox = _alpha_bbox(surface)
            self.assertIsNotNone(bbox)
            diameters.append(max(bbox[2], bbox[3]))
        self.assertLessEqual(abs(diameters[0] - diameters[1]), 4)


if __name__ == "__main__":
    unittest.main()
