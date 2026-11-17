"""Rapid-tap regressions for the real accumulator, engine, and sink.

The first classes exercise production code end to end: raw evdev events enter
``FrameAccumulator``, contact events enter ``TouchEngine``, and the engine's
output enters the real ``InputSink``.  Only the uinput device, the InputPlumber
bridge, and (in the daemon class) the evdev device are fakes.

These tests pin the suspected losses, including the trigger equivalents and
the sink's duplicate/coalescing boundaries.  No sleeps, threads, hardware, or
real device paths.
"""

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_PLUGIN = _TESTS.parent
sys.path.insert(0, str(_PLUGIN))
sys.path.insert(0, str(_TESTS))

from py_modules.freedom_touch.engine import TouchEngine  # noqa: E402
from py_modules.freedom_touch.input_adapter import InputSink  # noqa: E402
from py_modules.freedom_touch.runtime_policy import (  # noqa: E402
    FrameAccumulator,
)

EV_SYN = 0
EV_ABS = 3
EV_KEY = 1
SYN_REPORT = 0
SYN_DROPPED = 3
MT_SLOT = 47
MT_TRACKING_ID = 57
MT_POSITION_X = 53
MT_POSITION_Y = 54

BTN_SOUTH = 304
BTN_EAST = 305
ABS_X = 0
ABS_Y = 1
ABS_Z = 2
ABS_RX = 3
ABS_RY = 4
ABS_HAT0X = 16
ABS_HAT0Y = 17

ABS_MAX = 1000

# One button and two sticks, all in disjoint, comfortably centred regions.
PIPELINE_LAYOUT = [
    {
        "id": "btn_a",
        "kind": "button",
        "x": 0.10,
        "y": 0.10,
        "w": 0.20,
        "h": 0.20,
        "output": "a",
    },
    {
        "id": "stick_l",
        "kind": "stick",
        "x": 0.60,
        "y": 0.10,
        "w": 0.20,
        "h": 0.20,
        "output": "left",
    },
    {
        "id": "stick_r",
        "kind": "stick",
        "x": 0.60,
        "y": 0.60,
        "w": 0.20,
        "h": 0.20,
        "output": "right",
    },
]

# One digital trigger output, represented as a 0/255 axis by the sink.
TRIGGER_LAYOUT = [
    {
        "id": "btn_lt",
        "kind": "button",
        "x": 0.10,
        "y": 0.10,
        "w": 0.20,
        "h": 0.20,
        "output": "lt",
    },
]


def _slot(slot):
    return (EV_ABS, MT_SLOT, slot)


def _tid(tracking_id):
    return (EV_ABS, MT_TRACKING_ID, tracking_id)


def _syn():
    return (EV_SYN, SYN_REPORT, 0)


def _contact(slot, tracking_id, x=None, y=None):
    """One complete contact frame; axes are omitted when unchanged."""

    events = [_slot(slot), _tid(tracking_id)]
    if x is not None:
        events.append((EV_ABS, MT_POSITION_X, x))
    if y is not None:
        events.append((EV_ABS, MT_POSITION_Y, y))
    events.append(_syn())
    return events


def _release(slot):
    return [_slot(slot), _tid(-1), _syn()]


def _replace(slot, tracking_id, x=None, y=None):
    """Release and re-begin one slot inside a single SYN frame."""

    events = [_slot(slot), _tid(-1), _tid(tracking_id)]
    if x is not None:
        events.append((EV_ABS, MT_POSITION_X, x))
    if y is not None:
        events.append((EV_ABS, MT_POSITION_Y, y))
    events.append(_syn())
    return events


def _frame(*parts):
    """Join complete event frames into one SYN frame."""

    events = []
    for part in parts:
        if part and part[-1] == _syn():
            part = part[:-1]
        events.extend(part)
    events.append(_syn())
    return events


def _feed(accumulator, events):
    result = []
    for event_type, code, value in events:
        result.extend(accumulator.consume(event_type, code, value))
    return result


def _unit(raw):
    return min(1.0, max(0.0, float(raw) / ABS_MAX))


class FrameAccumulatorSlotStateTests(unittest.TestCase):
    """Type-B slots retain their last ABS_MT positions across a release."""

    def test_released_slot_retains_coordinates_for_recontact(self):
        accumulator = FrameAccumulator()

        self.assertEqual(
            _feed(accumulator, _contact(0, 100, 400, 500)),
            [("begin", 100, 400, 500)],
        )
        self.assertEqual(
            _feed(accumulator, _release(0)),
            [("end", 100, None, None)],
        )

        # The driver suppresses unchanged ABS values, so the new contact
        # reports only TRACKING_ID and must still begin at the last position.
        self.assertEqual(
            _feed(accumulator, _contact(0, 101)),
            [("begin", 101, 400, 500)],
        )
        self.assertEqual(
            _feed(accumulator, _release(0)),
            [("end", 101, None, None)],
        )

        self.assertEqual(
            _feed(accumulator, _contact(0, 102)),
            [("begin", 102, 400, 500)],
        )
        self.assertEqual(
            _feed(accumulator, _release(0)),
            [("end", 102, None, None)],
        )

    def test_missing_y_uses_cached_axis_when_x_changes(self):
        accumulator = FrameAccumulator()
        _feed(accumulator, _contact(0, 200, 400, 500))
        _feed(accumulator, _release(0))

        self.assertEqual(
            _feed(accumulator, _contact(0, 201, x=650)),
            [("begin", 201, 650, 500)],
        )

    def test_each_slot_keeps_its_own_last_coordinates(self):
        accumulator = FrameAccumulator()
        _feed(accumulator, _contact(0, 300, 400, 500))
        _feed(accumulator, _contact(1, 301, 600, 700))
        _feed(accumulator, _release(0))
        _feed(accumulator, _release(1))

        self.assertEqual(
            _feed(accumulator, _contact(0, 302, x=450)),
            [("begin", 302, 450, 500)],
        )
        self.assertEqual(
            _feed(accumulator, _contact(1, 303, y=720)),
            [("begin", 303, 600, 720)],
        )

    def test_unseen_axis_is_never_defaulted_to_zero(self):
        for event in (
            _contact(5, 900, x=321),
            _contact(6, 901, y=654),
        ):
            with self.subTest(event=event):
                accumulator = FrameAccumulator()
                self.assertEqual(_feed(accumulator, event), [])

    def test_syn_dropped_invalidates_cached_coordinates(self):
        accumulator = FrameAccumulator()
        _feed(accumulator, _contact(0, 400, 400, 500))

        self.assertEqual(
            _feed(accumulator, [(EV_SYN, SYN_DROPPED, 0)]),
            [("cancel", None, None, None)],
        )
        # Leave the dropped state; a partial-frame cache must not survive it.
        self.assertEqual(_feed(accumulator, [_syn()]), [])

        self.assertEqual(_feed(accumulator, _contact(0, 401, x=410)), [])
        self.assertEqual(
            _feed(accumulator, _contact(0, 402, 410, 510)),
            [("begin", 402, 410, 510)],
        )


class RecordingDevice:
    """uinput boundary: records writes/SYNs, never opens a device."""

    def __init__(self):
        self.writes = []
        self.syns = 0
        self.closed = 0

    def write(self, event_type, code, value):
        self.writes.append((event_type, code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        self.closed += 1


class RecordingBridge:
    """InputPlumber boundary: accepts calls, never talks to D-Bus."""

    def __init__(self):
        self._own_path = "/org/shadowblip/InputPlumber/composite/freedom"
        self.touches = []
        self.events = []
        self.chords = []

    @property
    def own_path(self):
        return self._own_path

    def discover(self, source_path):
        return True, ""

    def send_touch(self, cap, index, touching, pressure, x, y):
        self.touches.append((cap, index, touching, pressure, x, y))

    def send_event(self, cap, pressed):
        self.events.append((cap, pressed))

    def send_chord(self, caps):
        self.chords.append(list(caps))


class _Pipeline:
    """Real FrameAccumulator -> TouchEngine -> InputSink; fake hardware edges."""

    def __init__(self, layout=PIPELINE_LAYOUT):
        self.frame = FrameAccumulator()
        self.engine = TouchEngine(layout)
        self.engine.set_active(True)
        self.device = RecordingDevice()
        self.bridge = RecordingBridge()
        self.sink = InputSink(self.device, self.bridge)

    def feed(self, events, now=1000.0):
        """Feed raw events, forwarding each completed frame to the sink once."""

        batches = []
        for event_type, code, value in events:
            batch = self.frame.consume(event_type, code, value)
            if not batch:
                continue
            batches.append(batch)

            output = []
            for entry in batch:
                kind = entry[0]
                if kind == "begin":
                    output.extend(
                        self.engine.begin(
                            entry[1], _unit(entry[2]), _unit(entry[3]), now
                        )
                    )
                elif kind == "move":
                    output.extend(
                        self.engine.move(
                            entry[1], _unit(entry[2]), _unit(entry[3]), now
                        )
                    )
                elif kind == "end":
                    output.extend(self.engine.end(entry[1], now))
                elif kind == "cancel":
                    output.extend(self.engine.cancel_all())
            if output:
                self.sink.emit(output)
        return batches


class RapidTapPipelineTests(unittest.TestCase):
    def test_held_button_release_and_new_press_keep_both_edges(self):
        pipeline = _Pipeline()

        pipeline.feed(_contact(0, 1, 200, 200))
        self.assertEqual(pipeline.device.writes, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(pipeline.device.syns, 1)

        # The old contact ends and a new one starts on the same button in one
        # SYN frame.  Both edges are real inputs and need their own SYN.
        pipeline.feed(_replace(0, 2, 200, 200))

        self.assertEqual(
            pipeline.device.writes,
            [
                (EV_KEY, BTN_SOUTH, 1),
                (EV_KEY, BTN_SOUTH, 0),
                (EV_KEY, BTN_SOUTH, 1),
            ],
        )
        self.assertEqual(pipeline.device.syns, 3)

    def test_crossed_stick_axes_share_one_syn(self):
        pipeline = _Pipeline()

        pipeline.feed(
            _frame(_contact(0, 10, 780, 260), _contact(1, 11, 760, 660))
        )
        self.assertEqual(pipeline.device.syns, 1)
        self.assertEqual(
            [item[:2] for item in pipeline.device.writes],
            [(EV_ABS, ABS_X), (EV_ABS, ABS_Y), (EV_ABS, ABS_RX),
             (EV_ABS, ABS_RY)],
        )

        before = len(pipeline.device.writes)
        pipeline.feed(
            [_slot(0), (EV_ABS, MT_POSITION_X, 700),
             (EV_ABS, MT_POSITION_X, 720),
             (EV_ABS, MT_POSITION_Y, 240),
             _slot(1), (EV_ABS, MT_POSITION_X, 780),
             (EV_ABS, MT_POSITION_Y, 640), _syn()]
        )

        # Axis moves stay deliberately coalesced: one write per code and one
        # shared SYN, unlike the button release/press edges above.
        self.assertEqual(pipeline.device.syns, 2)
        self.assertEqual(
            [item[:2] for item in pipeline.device.writes[before:]],
            [(EV_ABS, ABS_X), (EV_ABS, ABS_Y), (EV_ABS, ABS_RX),
             (EV_ABS, ABS_RY)],
        )

    def test_releasing_every_contact_returns_the_sink_to_neutral(self):
        pipeline = _Pipeline()

        pipeline.feed(
            _frame(
                _contact(0, 20, 200, 200),
                _contact(1, 21, 780, 260),
                _contact(2, 22, 760, 660),
            )
        )
        pipeline.feed(_frame(_release(0), _release(1), _release(2)))

        self.assertEqual(pipeline.device.syns, 2)
        self.assertIn((EV_KEY, BTN_SOUTH, 0), pipeline.device.writes)
        for code in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
            self.assertIn((EV_ABS, code, 0), pipeline.device.writes)

        writes = len(pipeline.device.writes)
        pipeline.sink.emit([])
        self.assertEqual(len(pipeline.device.writes), writes)
        self.assertEqual(pipeline.device.syns, 2)

    def test_long_hold_presses_once(self):
        pipeline = _Pipeline()

        pipeline.feed(_contact(0, 30, 200, 200), now=1000.0)
        for step in range(5):
            pipeline.feed(
                [
                    _slot(0),
                    (EV_ABS, MT_POSITION_X, 200),
                    (EV_ABS, MT_POSITION_Y, 200),
                    _syn(),
                ],
                now=1001.0 + step,
            )
        pipeline.feed(_release(0), now=2000.0)

        self.assertEqual(
            pipeline.device.writes,
            [(EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_SOUTH, 0)],
        )
        self.assertEqual(pipeline.device.syns, 2)


class RapidTapTriggerPipelineTests(unittest.TestCase):
    """A trigger output keeps both edges through the same real pipeline."""

    def test_held_trigger_release_and_new_press_keep_both_edges(self):
        pipeline = _Pipeline(TRIGGER_LAYOUT)

        pipeline.feed(_contact(0, 70, 200, 200))
        self.assertEqual(pipeline.device.writes, [(EV_ABS, ABS_Z, 255)])
        self.assertEqual(pipeline.device.syns, 1)

        pipeline.feed(_replace(0, 71, 200, 200))

        self.assertEqual(
            pipeline.device.writes,
            [
                (EV_ABS, ABS_Z, 255),
                (EV_ABS, ABS_Z, 0),
                (EV_ABS, ABS_Z, 255),
            ],
        )
        self.assertEqual(pipeline.device.syns, 3)


class InputSinkDigitalTransitionTests(unittest.TestCase):
    """Digital edge retention in the real InputSink.

    Buttons, triggers and the digital D-pad hat axes all keep their edges.
    Only the uinput device and the InputPlumber bridge are recording fakes;
    every write and SYN below comes from ``InputSink.emit`` itself.
    """

    def setUp(self):
        self.device = RecordingDevice()
        self.bridge = RecordingBridge()
        self.sink = InputSink(self.device, self.bridge)

    def test_released_button_press_and_release_keep_both_edges(self):
        self.sink.emit([("button", "a", True), ("button", "a", False)])
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_SOUTH, 0)],
        )
        self.assertEqual(self.device.syns, 2)

    def test_held_button_release_and_repress_keep_both_edges(self):
        self.sink.emit([("button", "a", True)])
        self.sink.emit([("button", "a", False), ("button", "a", True)])
        self.assertEqual(
            self.device.writes,
            [
                (EV_KEY, BTN_SOUTH, 1),
                (EV_KEY, BTN_SOUTH, 0),
                (EV_KEY, BTN_SOUTH, 1),
            ],
        )
        self.assertEqual(self.device.syns, 3)

    def test_released_trigger_press_and_release_keep_both_edges(self):
        self.sink.emit([("button", "lt", True), ("button", "lt", False)])
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_Z, 255), (EV_ABS, ABS_Z, 0)],
        )
        self.assertEqual(self.device.syns, 2)

    def test_held_trigger_release_and_repress_keep_both_edges(self):
        self.sink.emit([("button", "lt", True)])
        self.sink.emit([("button", "lt", False), ("button", "lt", True)])
        self.assertEqual(
            self.device.writes,
            [
                (EV_ABS, ABS_Z, 255),
                (EV_ABS, ABS_Z, 0),
                (EV_ABS, ABS_Z, 255),
            ],
        )
        self.assertEqual(self.device.syns, 3)

    def test_held_dpad_release_and_repress_keep_both_edges(self):
        self.sink.emit([("axis", "dx", 1)])
        self.assertEqual(self.device.writes, [(EV_ABS, ABS_HAT0X, 1)])
        self.assertEqual(self.device.syns, 1)

        # A radial D-pad reports 0 between two taps on the same direction.
        # Both edges are real inputs, so the held direction cannot swallow
        # the neutral sample.
        self.sink.emit([("axis", "dx", 0), ("axis", "dx", 1)])
        self.assertEqual(
            self.device.writes,
            [
                (EV_ABS, ABS_HAT0X, 1),
                (EV_ABS, ABS_HAT0X, 0),
                (EV_ABS, ABS_HAT0X, 1),
            ],
        )
        self.assertEqual(self.device.syns, 3)

    def test_dpad_diagonal_pair_shares_one_syn(self):
        self.sink.emit([("axis", "dx", 1), ("axis", "dy", -1)])
        self.assertEqual(
            self.device.writes,
            [(EV_ABS, ABS_HAT0X, 1), (EV_ABS, ABS_HAT0Y, -1)],
        )
        self.assertEqual(self.device.syns, 1)

        # A consistent pair is one hat state; reversing both axes in one
        # frame writes the new pair once, in a single SYN.
        self.sink.emit([("axis", "dx", -1), ("axis", "dy", 1)])
        self.assertEqual(
            self.device.writes[2:],
            [(EV_ABS, ABS_HAT0X, -1), (EV_ABS, ABS_HAT0Y, 1)],
        )
        self.assertEqual(self.device.syns, 2)

    def test_dpad_change_only_flushes_the_changed_axis(self):
        self.sink.emit([("axis", "dx", 0), ("axis", "dy", -1)])
        self.assertEqual(self.device.writes, [(EV_ABS, ABS_HAT0Y, -1)])
        self.assertEqual(self.device.syns, 1)

        # The untouched direction is not rewritten and gets no SYN of its own.
        self.sink.emit([("axis", "dx", 1), ("axis", "dy", -1)])
        self.assertEqual(self.device.writes[1:], [(EV_ABS, ABS_HAT0X, 1)])
        self.assertEqual(self.device.syns, 2)

    def test_duplicate_same_button_is_suppressed(self):
        self.sink.emit(
            [
                ("button", "a", True),
                ("button", "a", True),
                ("button", "a", True),
            ]
        )
        self.assertEqual(self.device.writes, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(self.device.syns, 1)

        # The same value in a later frame is still a duplicate.
        self.sink.emit([("button", "a", True)])
        self.assertEqual(self.device.writes, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(self.device.syns, 1)

    def test_different_buttons_share_one_frame(self):
        self.sink.emit([("button", "a", True), ("button", "b", True)])
        self.assertEqual(
            self.device.writes,
            [(EV_KEY, BTN_SOUTH, 1), (EV_KEY, BTN_EAST, 1)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_stick_still_coalesces_while_dpad_edges_stay_digital(self):
        self.sink.emit(
            [
                ("axis", "lx", 0.5),
                ("axis", "lx", -0.25),
                ("axis", "lx", 0.25),
                ("axis", "dy", 0),
                ("axis", "dy", -1),
            ]
        )
        # The stick keeps only the frame's final value.  The D-pad takes the
        # digital path, so neutral is committed before the direction; it is
        # already the held state and is therefore not rewritten.  Both codes
        # share the frame's single SYN.
        self.assertEqual(
            [item for item in self.device.writes
             if item[:2] == (EV_ABS, ABS_X)],
            [(EV_ABS, ABS_X, 8192)],
        )
        self.assertEqual(
            [item for item in self.device.writes
             if item[:2] == (EV_ABS, ABS_HAT0Y)],
            [(EV_ABS, ABS_HAT0Y, -1)],
        )
        self.assertEqual(self.device.syns, 1)

    def test_button_reversal_keeps_axis_coalescing(self):
        self.sink.emit([("button", "a", True)])
        self.sink.emit(
            [
                ("button", "a", False),
                ("axis", "lx", 0.5),
                ("button", "a", True),
                ("axis", "lx", 0.25),
            ]
        )
        self.assertEqual(
            self.device.writes,
            [
                (EV_KEY, BTN_SOUTH, 1),
                (EV_KEY, BTN_SOUTH, 0),
                (EV_KEY, BTN_SOUTH, 1),
                (EV_ABS, ABS_X, 8192),
            ],
        )
        self.assertEqual(self.device.syns, 3)


class DaemonReadTouchTests(unittest.TestCase):
    """Complete SYN frames arriving in one daemon read keep every edge."""

    def test_two_press_release_frames_in_one_read_keep_all_edges(self):
        from test_daemon_lifecycle import FakeEvent, Harness

        harness = Harness()
        device = RecordingDevice()
        bridge = RecordingBridge()
        # Harness resolves its sink and bridge through the current attributes.
        harness.sink = InputSink(device, bridge)
        harness.bridge = bridge
        self.addCleanup(harness.touch.close)

        harness.activate()
        self.assertTrue(harness.daemon.capturing)

        controls = {
            item["id"]: item for item in harness.daemon.effective_controls
        }
        button = controls["btn_a"]
        raw_x, raw_y = harness.raw_point(
            button["x"] + button["w"] / 2.0,
            button["y"] + button["h"] / 2.0,
        )

        events = []
        events.extend(
            [
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, 500),
                FakeEvent(EV_ABS, MT_POSITION_X, raw_x),
                FakeEvent(EV_ABS, MT_POSITION_Y, raw_y),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, -1),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, 501),
                FakeEvent(EV_ABS, MT_POSITION_X, raw_x),
                FakeEvent(EV_ABS, MT_POSITION_Y, raw_y),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, -1),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
            ]
        )
        harness.push_frame(events)

        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertEqual(
            device.writes,
            [
                (EV_KEY, BTN_SOUTH, 1),
                (EV_KEY, BTN_SOUTH, 0),
                (EV_KEY, BTN_SOUTH, 1),
                (EV_KEY, BTN_SOUTH, 0),
            ],
        )
        self.assertEqual(device.syns, 4)


if __name__ == "__main__":
    unittest.main()
