"""Behavioral tests for the hardware adapter, using fake boundaries."""

import os
import sys
import types
import unittest
from unittest import mock

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN = _ROOT
# runtime_policy imports py_modules.freedom_touch.*, while the plugin itself
# imports freedom_touch.*; keep both roots available for either style.
sys.path.insert(0, _PLUGIN)
sys.path.insert(0, os.path.join(_PLUGIN, "py_modules"))

from freedom_touch.input_adapter import InputSink, create_source


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.syns = 0
        self.closed = False

    def write(self, event_type, code, value):
        self.writes.append((event_type, code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        self.closed = True


class FakeBridge:
    def __init__(self):
        self.touches = []
        self.events = []
        self.chords = []

    def send_touch(self, cap, index, touching, pressure, x, y):
        self.touches.append(
            (cap, index, touching, pressure, x, y)
        )

    def send_event(self, cap, pressed):
        self.events.append((cap, pressed))

    def send_chord(self, caps):
        self.chords.append(list(caps))


class InputSinkTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.bridge = FakeBridge()
        self.sink = InputSink(self.device, self.bridge)

    def test_abxy_uses_linux_north_west_constants(self):
        # A=south 304, B=east 305, X=west 308, Y=north 307 on the default
        # layout.  This test previously pinned the swapped X/Y pair.
        self.sink.emit(
            [("button", name, True) for name in ("a", "b", "x", "y")]
        )
        self.assertEqual(
            self.device.writes,
            [(1, 304, 1), (1, 305, 1), (1, 308, 1), (1, 307, 1)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_face_buttons_write_their_own_code_on_press_and_release(self):
        # Codes stay literal on purpose: importing the adapter's table would
        # only restate it and keep a swapped pair green.
        for label, code in [("a", 304), ("b", 305), ("x", 308), ("y", 307)]:
            with self.subTest(label=label):
                device = FakeDevice()
                sink = InputSink(device, FakeBridge())

                sink.emit([("button", label, True)])
                self.assertEqual(device.writes, [(1, code, 1)])

                sink.emit([("button", label, False)])
                self.assertEqual(
                    device.writes, [(1, code, 1), (1, code, 0)]
                )
                self.assertEqual(device.syns, 2)

    def test_shoulder_system_and_stick_buttons(self):
        outputs = (
            "lb",
            "rb",
            "back",
            "start",
            "guide",
            "l3",
            "r3",
        )
        self.sink.emit([("button", name, True) for name in outputs])
        self.assertEqual(
            self.device.writes,
            [
                (1, 310, 1),
                (1, 311, 1),
                (1, 314, 1),
                (1, 315, 1),
                (1, 316, 1),
                (1, 317, 1),
                (1, 318, 1),
            ],
        )
        self.assertEqual(self.device.syns, 1)

        self.sink.emit([("button", name, False) for name in outputs])
        self.assertEqual(
            self.device.writes[-len(outputs):],
            [
                (1, 310, 0),
                (1, 311, 0),
                (1, 314, 0),
                (1, 315, 0),
                (1, 316, 0),
                (1, 317, 0),
                (1, 318, 0),
            ],
        )
        self.assertEqual(self.device.syns, 2)

    def test_triggers_are_full_range_axes(self):
        self.sink.emit([("button", "lt", True), ("button", "rt", True)])
        self.assertEqual(
            self.device.writes, [(3, 2, 255), (3, 5, 255)]
        )
        self.assertEqual(self.device.syns, 1)

        self.sink.emit([("button", "lt", False), ("button", "rt", False)])
        self.assertEqual(
            self.device.writes[2:], [(3, 2, 0), (3, 5, 0)]
        )
        self.assertEqual(self.device.syns, 2)

    def test_sticks_and_diagonal_dpad_axes(self):
        self.sink.emit(
            [
                ("axis", "lx", 0.5),
                ("axis", "ly", -0.5),
                ("axis", "rx", 1.0),
                ("axis", "ry", -1.0),
            ]
        )
        self.assertEqual(
            self.device.writes,
            [
                (3, 0, 16384),
                (3, 1, -16384),
                (3, 3, 32767),
                (3, 4, -32767),
            ],
        )
        self.assertEqual(self.device.syns, 1)

        self.sink.emit([("axis", "dx", 1), ("axis", "dy", -1)])
        self.assertEqual(
            self.device.writes[4:], [(3, 16, 1), (3, 17, -1)]
        )
        self.assertEqual(self.device.syns, 2)

    def test_frame_writes_are_coalesced_and_one_syn(self):
        self.sink.emit(
            [
                ("button", "a", True),
                ("axis", "lx", 0.5),
                ("axis", "lx", 0.25),
                ("button", "a", True),
            ]
        )
        self.assertEqual(
            self.device.writes, [(1, 304, 1), (3, 0, 8192)]
        )
        self.assertEqual(self.device.syns, 1)

        # Identical state must not be written again.
        self.sink.emit([("button", "a", True), ("axis", "lx", 0.25)])
        self.assertEqual(
            self.device.writes, [(1, 304, 1), (3, 0, 8192)]
        )
        self.assertEqual(self.device.syns, 1)

    def test_frame_that_returns_to_original_writes_nothing(self):
        self.sink.emit([("axis", "lx", 0.5), ("axis", "lx", 0.0)])
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.syns, 0)

    def test_pads_use_native_motion_not_a_mouse(self):
        payload = {
            "touching": True,
            "pressure": 0.7,
            "x": 0.2,
            "y": 0.8,
        }
        self.sink.emit([("pad", "left", payload)])
        self.assertEqual(
            self.bridge.touches,
            [("Touchpad:LeftPad:Motion", 0, True, 0.7, 0.2, 0.8)],
        )
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.syns, 0)

        payload = dict(payload, touching=False, pressure=0.0)
        self.sink.emit([("pad", "right", payload)])
        self.assertEqual(
            self.bridge.touches[-1],
            ("Touchpad:RightPad:Motion", 0, False, 0.0, 0.2, 0.8),
        )
        self.assertEqual(self.device.writes, [])

    def test_pad_press_uses_one_native_chord_and_ignores_release(self):
        self.sink.emit(
            [
                ("pad_press", "left", True),
                ("pad_press", "left", False),
                ("pad_press", "right", True),
            ]
        )
        self.assertEqual(
            self.bridge.chords,
            [
                ["Touchpad:LeftPad:Button:Press"],
                ["Touchpad:RightPad:Button:Press"],
            ],
        )
        self.assertEqual(self.device.writes, [])

    def test_qam_only_sends_on_press(self):
        self.sink.emit([("button", "qam", True)])
        self.assertEqual(
            self.bridge.chords, [["Gamepad:Button:QuickAccess"]]
        )
        self.sink.emit([("button", "qam", False)])
        self.assertEqual(
            self.bridge.chords, [["Gamepad:Button:QuickAccess"]]
        )
        self.assertEqual(self.device.writes, [])
        self.assertEqual(self.device.syns, 0)

    def test_neutralize_releases_all_once_and_is_idempotent(self):
        self.sink.emit(
            [
                ("button", "a", True),
                ("button", "lb", True),
                ("button", "lt", True),
                ("axis", "lx", 0.5),
                ("axis", "dy", -1),
                ("pad", "left", {
                    "touching": True,
                    "pressure": 1.0,
                    "x": 0.25,
                    "y": 0.75,
                }),
            ]
        )
        before = len(self.device.writes)

        self.sink.neutralize()
        released = self.device.writes[before:]
        self.assertEqual(len(released), 5)
        self.assertEqual(
            set(released),
            {
                (1, 304, 0),
                (1, 310, 0),
                (3, 0, 0),
                (3, 2, 0),
                (3, 17, 0),
            },
        )
        self.assertEqual(self.device.syns, 2)
        self.assertEqual(
            self.bridge.touches[-1],
            ("Touchpad:LeftPad:Motion", 0, False, 0.0, 0.25, 0.75),
        )

        writes_after = len(self.device.writes)
        touches_after = len(self.bridge.touches)
        self.sink.neutralize()
        self.assertEqual(len(self.device.writes), writes_after)
        self.assertEqual(self.device.syns, 2)
        self.assertEqual(len(self.bridge.touches), touches_after)

    def test_close_neutralizes_then_closes_device(self):
        self.sink.emit([("button", "a", True)])
        self.sink.close()
        self.assertTrue(self.device.closed)
        self.assertEqual(self.device.writes[-1], (1, 304, 0))
        self.assertEqual(self.device.syns, 2)


class _FakeAbsInfo:
    def __init__(self, value=0, min=0, max=0, fuzz=0, flat=0,
                 resolution=0):
        self.value = value
        self.min = min
        self.max = max
        self.fuzz = fuzz
        self.flat = flat
        self.resolution = resolution


def _fake_evdev(uinput_cls):
    ecodes = types.SimpleNamespace(
        EV_KEY=1,
        EV_ABS=3,
        BUS_USB=3,
        BTN_SOUTH=304,
        BTN_EAST=305,
        BTN_NORTH=307,
        BTN_WEST=308,
        BTN_TL=310,
        BTN_TR=311,
        BTN_SELECT=314,
        BTN_START=315,
        BTN_MODE=316,
        BTN_THUMBL=317,
        BTN_THUMBR=318,
        ABS_X=0,
        ABS_Y=1,
        ABS_Z=2,
        ABS_RX=3,
        ABS_RY=4,
        ABS_RZ=5,
        ABS_HAT0X=16,
        ABS_HAT0Y=17,
    )
    module = types.ModuleType("evdev")
    module.ecodes = ecodes
    module.AbsInfo = _FakeAbsInfo
    module.UInput = uinput_cls
    return module


class CreateSourceTests(unittest.TestCase):
    def test_uses_actual_device_path_and_exact_identity(self):
        class FakeAbsInfo:
            def __init__(self, value=0, min=0, max=0, fuzz=0, flat=0,
                         resolution=0):
                self.value = value
                self.min = min
                self.max = max
                self.fuzz = fuzz
                self.flat = flat
                self.resolution = resolution

        class FakeUInput:
            instance = None

            def __init__(self, events, **kwargs):
                self.events = events
                self.kwargs = kwargs
                self.device = types.SimpleNamespace(
                    path="/dev/input/event123"
                )
                self.closed = False
                FakeUInput.instance = self

            def close(self):
                self.closed = True

        ecodes = types.SimpleNamespace(
            EV_KEY=1,
            EV_ABS=3,
            BUS_USB=3,
            BTN_SOUTH=304,
            BTN_EAST=305,
            BTN_NORTH=307,
            BTN_WEST=308,
            BTN_TL=310,
            BTN_TR=311,
            BTN_SELECT=314,
            BTN_START=315,
            BTN_MODE=316,
            BTN_THUMBL=317,
            BTN_THUMBR=318,
            ABS_X=0,
            ABS_Y=1,
            ABS_Z=2,
            ABS_RX=3,
            ABS_RY=4,
            ABS_RZ=5,
            ABS_HAT0X=16,
            ABS_HAT0Y=17,
        )
        fake_evdev = types.ModuleType("evdev")
        fake_evdev.ecodes = ecodes
        fake_evdev.AbsInfo = FakeAbsInfo
        fake_evdev.UInput = FakeUInput

        with mock.patch.dict(sys.modules, {"evdev": fake_evdev}):
            ui, path = create_source()

        self.assertEqual(path, "/dev/input/event123")
        self.assertIs(ui.device.path, path)
        self.assertEqual(
            ui.kwargs["name"], "Freedom Touch Controller Source"
        )
        self.assertEqual(ui.kwargs["bustype"], 3)
        self.assertEqual(ui.kwargs["vendor"], 0x0000)
        self.assertEqual(ui.kwargs["product"], 0x4654)
        self.assertEqual(ui.kwargs["version"], 1)

        key_caps = ui.events[1]
        for code in (304, 305, 307, 308, 310, 311, 314, 315, 316,
                     317, 318):
            self.assertIn(code, key_caps)

        abs_caps = dict(ui.events[3])
        self.assertEqual((abs_caps[0].min, abs_caps[0].max),
                         (-32767, 32767))
        self.assertEqual((abs_caps[2].min, abs_caps[2].max), (0, 255))
        self.assertEqual((abs_caps[16].min, abs_caps[16].max), (-1, 1))
        self.assertEqual((abs_caps[17].min, abs_caps[17].max), (-1, 1))

        # A successful source stays open for InputSink.close() to own.
        self.assertFalse(ui.closed)

    def test_missing_device_closes_ui_once_and_reports_unavailable_node(self):
        class NoDeviceUInput:
            instance = None

            def __init__(self, events, **kwargs):
                self.closed = 0
                self.device = None
                NoDeviceUInput.instance = self

            def close(self):
                self.closed += 1

        with mock.patch.dict(
            sys.modules, {"evdev": _fake_evdev(NoDeviceUInput)}
        ):
            with self.assertRaises(RuntimeError) as caught:
                create_source()

        self.assertIn(
            "source event node unavailable", str(caught.exception)
        )
        self.assertEqual(NoDeviceUInput.instance.closed, 1)

    def test_path_access_failure_closes_ui_and_preserves_exception(self):
        class BrokenPath:
            @property
            def path(self):
                raise OSError("path unavailable")

        class BrokenPathUInput:
            instance = None

            def __init__(self, events, **kwargs):
                self.closed = 0
                self.device = BrokenPath()
                BrokenPathUInput.instance = self

            def close(self):
                self.closed += 1

        with mock.patch.dict(
            sys.modules, {"evdev": _fake_evdev(BrokenPathUInput)}
        ):
            with self.assertRaises(OSError) as caught:
                create_source()

        self.assertEqual(str(caught.exception), "path unavailable")
        self.assertEqual(BrokenPathUInput.instance.closed, 1)


if __name__ == "__main__":
    unittest.main()
