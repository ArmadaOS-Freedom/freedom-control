"""Behavioral digital-edge preservation tests for the real InputSink.

Only the uinput device and InputPlumber bridge are recording fakes; every
write and SYN below comes from ``InputSink.emit`` itself.  Analog stick
changes remain coalesced; digital buttons, triggers and D-pad hat axes keep
their ordering with per-edge SYN boundaries, while unrelated codes may
share a SYN and duplicates are suppressed.
"""

import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "py_modules"))

from freedom_touch.input_adapter import (  # noqa: E402
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RX,
    ABS_X,
    ABS_Z,
    BTN_EAST,
    BTN_NORTH,
    BTN_SOUTH,
    BTN_WEST,
    EV_ABS,
    EV_KEY,
    InputSink,
)


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.syns = 0

    def write(self, event_type, code, value):
        self.writes.append((event_type, code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        pass


class FakeBridge:
    def __init__(self):
        self.touches = []
        self.events = []
        self.chords = []

    def send_touch(self, cap, index, touching, pressure, x, y):
        self.touches.append((cap, index, touching, pressure, x, y))
        return True

    def send_event(self, cap, pressed):
        self.events.append((cap, pressed))
        return True

    def send_chord(self, caps):
        self.chords.append(list(caps))
        return True


class LocalParityEdgeTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.bridge = FakeBridge()
        self.sink = InputSink(self.device, self.bridge)

    def test_rapid_press_release_press_keeps_all_edges(self):
        self.sink.emit(
            [
                ("button", "a", True),
                ("button", "a", False),
                ("button", "a", True),
            ]
        )
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_SOUTH, 0),
             (EV_KEY, BTN_SOUTH, 1)],
        )
        self.assertEqual(self.device.syns, 3)

    def test_rapid_release_press_from_held_keeps_both_edges(self):
        self.sink.emit([("button", "b", True)])
        self.sink.emit([("button", "b", False), ("button", "b", True)])
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_EAST, 1), (EV_KEY, BTN_EAST, 0),
             (EV_KEY, BTN_EAST, 1)],
        )
        self.assertEqual(self.device.syns, 3)

    def test_abxy_position_codes_unchanged(self):
        self.sink.emit(
            [("button", name, True) for name in ("x", "y", "a", "b")]
        )
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_WEST, 1), (EV_KEY, BTN_NORTH, 1),
             (EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_EAST, 1)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_trigger_edge_preserved_inside_one_emit(self):
        self.sink.emit([("button", "lt", True), ("button", "lt", False)])
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_Z, 255), (EV_ABS, ABS_Z, 0)],
        )
        self.assertEqual(self.device.syns, 2)

    def test_hat_up_neutral_up_keeps_order(self):
        self.sink.emit([("axis", "dy", 1)])
        self.sink.emit([("axis", "dy", 0), ("axis", "dy", 1)])
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_HAT0Y, 1), (EV_ABS, ABS_HAT0Y, 0),
             (EV_ABS, ABS_HAT0Y, 1)],
        )
        self.assertEqual(self.device.syns, 3)

    def test_dpad_diagonal_pair_shares_one_syn(self):
        self.sink.emit([("axis", "dx", 1), ("axis", "dy", -1)])
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_HAT0X, 1), (EV_ABS, ABS_HAT0Y, -1)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_duplicates_do_not_emit_extra_edges(self):
        self.sink.emit([("button", "a", True)] * 3)
        self.assertEqual(self.device.writes, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(self.device.syns, 1)
        self.sink.emit([("button", "a", True)])
        self.assertEqual(self.device.writes, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(self.device.syns, 1)

    def test_stick_changes_are_coalesced(self):
        self.sink.emit(
            [("axis", "lx", 0.5), ("axis", "lx", -0.25),
             ("axis", "lx", 0.25), ("axis", "rx", 1.0)]
        )
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_X, 8192), (EV_ABS, ABS_RX, 32767)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_button_reversal_keeps_analog_coalescing(self):
        self.sink.emit([("button", "a", True)])
        self.sink.emit(
            [("button", "a", False), ("axis", "lx", 0.5),
             ("button", "a", True), ("axis", "lx", 0.25)]
        )
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_SOUTH, 0),
             (EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_X, 8192)],
        )
        self.assertEqual(self.device.syns, 3)


if __name__ == "__main__":
    unittest.main()
