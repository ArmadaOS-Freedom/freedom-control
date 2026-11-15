"""Behavioral tests for the multi-touch controller engine."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_modules.freedom_touch.engine import TouchEngine

EVENT_KINDS = {"button", "axis", "pad", "pad_press"}


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


def button_values(events, name):
    return [e[2] for e in events if e[0] == "button" and e[1] == name]


def axis_value(events, name):
    values = [e[2] for e in events if e[0] == "axis" and e[1] == name]
    return values[-1] if values else None


def pad_payloads(events, name):
    return [e[2] for e in events if e[0] == "pad" and e[1] == name]


def pad_press_values(events, name):
    return [e[2] for e in events if e[0] == "pad_press" and e[1] == name]


class TouchEngineTests(unittest.TestCase):
    # Literal rects, deliberately independent of default_layout().
    CONTROLS = [
        control("btn_a", "button", 0.05, 0.05, 0.10, 0.10, "a"),
        control("btn_b", "button", 0.20, 0.05, 0.10, 0.10, "b"),
        control("stick_l", "stick", 0.05, 0.30, 0.20, 0.20, "left"),
        control("dpad_l", "dpad", 0.05, 0.60, 0.20, 0.20, "left"),
        control("pad_r", "pad", 0.70, 0.60, 0.25, 0.25, "right"),
    ]

    A = (0.10, 0.10)
    B = (0.25, 0.10)
    STICK = (0.15, 0.40)
    DPAD = (0.15, 0.70)
    PAD = (0.825, 0.725)
    EMPTY = (0.50, 0.50)

    def make_engine(self):
        return TouchEngine([dict(c) for c in self.CONTROLS])

    def active_engine(self):
        engine = self.make_engine()
        engine.set_active(True)
        return engine

    def test_engine_starts_inactive_and_ignores_all_input(self):
        engine = self.make_engine()
        self.assertEqual(engine.begin(1, self.A[0], self.A[1], 0.0), [])
        self.assertEqual(engine.move(1, self.STICK[0], self.STICK[1], 0.1), [])
        self.assertEqual(engine.end(1, 0.2), [])
        self.assertIsInstance(engine.set_active(True), list)
        pressed = engine.begin(1, self.A[0], self.A[1], 0.3)
        self.assertIn(("button", "a", True), pressed)
        for event in pressed:
            self.assertIn(event[0], EVENT_KINDS)

    def test_button_press_and_release_emit_no_mouse_events(self):
        engine = self.active_engine()
        self.assertEqual(engine.begin(1, self.A[0], self.A[1], 0.0),
                         [("button", "a", True)])
        self.assertEqual(engine.end(1, 0.1), [("button", "a", False)])
        events = engine.begin(2, self.PAD[0], self.PAD[1], 0.2)
        events += engine.end(2, 0.3)
        events += engine.begin(3, self.STICK[0], self.STICK[1], 0.4)
        for event in events:
            self.assertIn(event[0], EVENT_KINDS)

    def test_hit_testing_only_happens_on_begin(self):
        engine = self.active_engine()
        self.assertEqual(engine.begin(1, self.EMPTY[0], self.EMPTY[1], 0.0), [])
        self.assertEqual(engine.move(1, self.A[0], self.A[1], 0.1), [])
        self.assertEqual(engine.move(1, self.PAD[0], self.PAD[1], 0.2), [])
        self.assertEqual(engine.begin(2, self.A[0], self.A[1], 0.3),
                         [("button", "a", True)])
        self.assertEqual(engine.move(2, self.EMPTY[0], self.EMPTY[1], 0.4), [])
        self.assertEqual(engine.end(2, 0.5), [("button", "a", False)])

    def test_duplicate_and_unknown_pointers(self):
        engine = self.active_engine()
        engine.begin(1, self.A[0], self.A[1], 0.0)
        events = engine.begin(1, self.B[0], self.B[1], 0.1)
        self.assertIn(("button", "a", False), events)
        self.assertIn(("button", "b", True), events)
        self.assertNotIn(("button", "b", False), events)

        self.assertEqual(engine.move(99, self.A[0], self.A[1], 0.2), [])
        self.assertEqual(engine.end(99, 0.3), [])
        self.assertEqual(button_values(engine.end(1, 0.4), "b"), [False])

    def test_shared_button_uses_reference_counting(self):
        engine = self.active_engine()
        self.assertEqual(engine.begin(1, self.A[0], self.A[1], 0.0),
                         [("button", "a", True)])
        self.assertEqual(button_values(engine.begin(2, self.A[0], self.A[1], 0.1), "a"), [])
        self.assertEqual(button_values(engine.end(1, 0.2), "a"), [])
        self.assertEqual(button_values(engine.end(2, 0.3), "a"), [False])

    def test_stick_deadzone_and_axis_mapping(self):
        engine = self.active_engine()
        engine.begin(1, self.STICK[0], self.STICK[1], 0.0)
        jitter = engine.move(1, self.STICK[0] + 0.005, self.STICK[1] + 0.005, 0.1)
        for event in jitter:
            if event[0] == "axis":
                self.assertEqual(event[2], 0.0)
        moved = engine.move(1, 0.24, self.STICK[1], 0.2)
        self.assertGreater(axis_value(moved, "lx"), 0.8)
        self.assertAlmostEqual(axis_value(moved, "ly"), 0.0)

    def test_stick_clamps_and_retains_pointer_owner(self):
        engine = self.active_engine()
        engine.begin(1, self.STICK[0], self.STICK[1], 0.0)
        clamped = engine.move(1, 5.0, self.STICK[1], 0.1)
        self.assertAlmostEqual(abs(axis_value(clamped, "lx")), 1.0)
        self.assertAlmostEqual(axis_value(clamped, "ly"), 0.0)
        back = engine.move(1, -5.0, self.STICK[1], 0.2)
        self.assertAlmostEqual(axis_value(back, "lx"), -1.0)
        self.assertNotIn(("button", "a", True), engine.move(1, self.A[0], self.A[1], 0.3))

    def test_dpad_diagonal_reports_both_axes(self):
        engine = self.active_engine()
        engine.begin(1, self.DPAD[0], self.DPAD[1], 0.0)
        events = engine.move(1, 0.23, 0.62, 0.1)
        self.assertEqual(axis_value(events, "dx"), 1)
        self.assertEqual(axis_value(events, "dy"), -1)
        events = engine.move(1, 0.23, 0.78, 0.2)
        self.assertEqual(axis_value(events, "dx"), 1)
        self.assertEqual(axis_value(events, "dy"), 1)

    def test_pad_normalizes_coordinates_and_clamps(self):
        engine = self.active_engine()
        events = list(engine.begin(1, self.PAD[0], self.PAD[1], 0.0))
        events += engine.move(1, self.PAD[0], self.PAD[1], 0.02)
        payload = pad_payloads(events, "right")[-1]
        self.assertTrue(payload["touching"])
        self.assertAlmostEqual(payload["x"], 0.5)
        self.assertAlmostEqual(payload["y"], 0.5)
        self.assertGreater(payload["pressure"], 0.0)
        self.assertLessEqual(payload["pressure"], 1.0)

        moved = engine.move(1, 0.95, self.PAD[1], 0.1)
        self.assertAlmostEqual(pad_payloads(moved, "right")[-1]["x"], 1.0)
        clamped = engine.move(1, 2.0, 2.0, 0.2)
        payload = pad_payloads(clamped, "right")[-1]
        self.assertAlmostEqual(payload["x"], 1.0)
        self.assertAlmostEqual(payload["y"], 1.0)

    def test_rectangular_stick_scales_each_axis_by_its_own_half(self):
        engine = TouchEngine(
            [control("stick_l", "stick", 0.1, 0.2, 0.2, 0.4, "left")],
            deadzone=0.0,
        )
        engine.set_active(True)
        engine.begin(1, 0.2, 0.4, 0.0)

        # A tenth of the screen down is half of the .2 half-height.
        vertical = engine.move(1, 0.2, 0.5, 0.1)
        self.assertAlmostEqual(axis_value(vertical, "lx"), 0.0)
        self.assertAlmostEqual(axis_value(vertical, "ly"), 0.5)

        half_height = engine.move(1, 0.2, 0.6, 0.2)
        self.assertAlmostEqual(axis_value(half_height, "ly"), 1.0)

        half_width = engine.move(1, 0.3, 0.4, 0.3)
        self.assertAlmostEqual(axis_value(half_width, "lx"), 1.0)

    def test_pad_leaving_rect_suppresses_tap_even_when_clamped(self):
        engine = self.active_engine()

        events = engine.begin(1, 0.949, 0.725, 0.0)
        events += engine.move(1, 1.2, 0.725, 0.02)
        payload = pad_payloads(events, "right")[-1]
        self.assertAlmostEqual(payload["x"], 1.0)

        events += engine.end(1, 0.1)
        self.assertNotIn(True, pad_press_values(events, "right"))

        # Leaving the rect and returning quickly must not turn into a tap.
        engine.begin(2, 0.949, 0.725, 10.0)
        engine.move(2, 1.2, 0.725, 10.02)
        back = engine.move(2, 0.949, 0.725, 10.04)
        back += engine.end(2, 10.1)
        self.assertNotIn(True, pad_press_values(back, "right"))

    def test_pad_short_low_motion_tap(self):
        engine = self.active_engine()
        engine.begin(1, self.PAD[0], self.PAD[1], 100.0)
        events = engine.move(1, self.PAD[0] + 0.002, self.PAD[1], 100.02)
        events += engine.end(1, 100.05)
        self.assertEqual(pad_press_values(events, "right"), [True, False])

    def test_pad_drag_does_not_generate_tap(self):
        engine = self.active_engine()
        engine.begin(1, self.PAD[0], self.PAD[1], 200.0)
        engine.move(1, 0.95, 0.65, 200.1)
        events = engine.move(1, 0.72, 0.62, 200.4)
        events += engine.end(1, 200.45)
        self.assertNotIn(True, pad_press_values(events, "right"))

    def test_concurrent_stick_button_and_pad(self):
        engine = self.active_engine()
        stick_events = engine.begin(10, self.STICK[0], self.STICK[1], 0.0)
        stick_events += engine.move(10, 0.24, self.STICK[1], 0.1)
        self.assertGreater(axis_value(stick_events, "lx"), 0.8)
        self.assertEqual(button_values(engine.begin(11, self.A[0], self.A[1], 0.2), "a"), [True])
        pad_events = engine.begin(12, self.PAD[0], self.PAD[1], 0.3)
        pad_events += engine.move(12, self.PAD[0], self.PAD[1], 0.35)
        self.assertTrue(pad_payloads(pad_events, "right")[-1]["touching"])

        released = engine.end(11, 0.4)
        self.assertEqual(button_values(released, "a"), [False])
        self.assertNotIn(False, [p["touching"] for p in pad_payloads(released, "right")])
        self.assertGreater(axis_value(engine.move(10, 0.24, self.STICK[1], 0.5), "lx"), 0.8)
        moved_pad = pad_payloads(engine.move(12, 0.90, 0.70, 0.6), "right")
        self.assertTrue(moved_pad)
        self.assertGreater(moved_pad[-1]["x"], 0.5)

    def test_overlapping_pad_and_button_hit_the_button_in_either_order(self):
        # draw_controls paints pads first and every other control afterwards,
        # so a button sits on top of a pad regardless of layout position.
        pad = control("pad_l", "pad", 0.10, 0.10, 0.40, 0.40, "left")
        btn = control("btn_a", "button", 0.20, 0.20, 0.10, 0.10, "a")
        for layout in ([pad, btn], [btn, pad]):
            with self.subTest(order=[item["id"] for item in layout]):
                engine = TouchEngine([dict(item) for item in layout])
                engine.set_active(True)
                self.assertEqual(
                    engine.begin(1, 0.25, 0.25, 0.0),
                    [("button", "a", True)],
                )

    def test_same_kind_overlap_hits_the_control_drawn_last(self):
        first = control("btn_a", "button", 0.10, 0.10, 0.20, 0.20, "a")
        last = control("btn_b", "button", 0.15, 0.15, 0.20, 0.20, "b")
        for layout, expected in (([first, last], "b"), ([last, first], "a")):
            with self.subTest(expected=expected):
                engine = TouchEngine([dict(item) for item in layout])
                engine.set_active(True)
                self.assertEqual(
                    engine.begin(1, 0.18, 0.18, 0.0),
                    [("button", expected, True)],
                )

        left = control("pad_l", "pad", 0.30, 0.30, 0.30, 0.30, "left")
        right = control("pad_r", "pad", 0.35, 0.35, 0.30, 0.30, "right")
        for layout, expected in (
            ([left, right], "right"),
            ([right, left], "left"),
        ):
            with self.subTest(expected=expected):
                engine = TouchEngine([dict(item) for item in layout])
                engine.set_active(True)
                self.assertEqual(
                    [event[1] for event in engine.begin(1, 0.40, 0.40, 0.0)],
                    [expected],
                )

    def test_cancel_and_deactivate_release_everything(self):
        engine = self.active_engine()
        engine.begin(1, self.A[0], self.A[1], 0.0)
        engine.begin(2, self.STICK[0], self.STICK[1], 0.1)
        engine.move(2, 0.24, 0.40, 0.2)
        engine.begin(3, self.DPAD[0], self.DPAD[1], 0.3)
        engine.move(3, 0.23, 0.62, 0.4)
        engine.begin(4, self.PAD[0], self.PAD[1], 0.5)
        engine.move(4, 0.95, 0.65, 0.55)

        events = engine.cancel_all()
        self.assertIn(("button", "a", False), events)
        self.assertAlmostEqual(axis_value(events, "lx"), 0.0)
        self.assertAlmostEqual(axis_value(events, "ly"), 0.0)
        self.assertEqual(axis_value(events, "dx"), 0)
        self.assertEqual(axis_value(events, "dy"), 0)
        self.assertIn(False, [p["touching"] for p in pad_payloads(events, "right")])
        self.assertNotIn(True, pad_press_values(events, "right"))

        self.assertEqual(engine.begin(1, self.A[0], self.A[1], 1.0),
                         [("button", "a", True)])

        fresh = self.active_engine()
        fresh.begin(1, self.A[0], self.A[1], 2.0)
        release = fresh.set_active(False)
        self.assertEqual(button_values(release, "a"), [False])
        self.assertEqual(fresh.begin(1, self.A[0], self.A[1], 2.1), [])
        self.assertEqual(fresh.move(1, self.STICK[0], self.STICK[1], 2.2), [])


if __name__ == "__main__":
    unittest.main()
