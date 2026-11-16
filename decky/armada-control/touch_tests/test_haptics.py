"""Behavioral tests for the native tablet haptic adapter."""

import os
import sys
import unittest

from evdev import ff

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_modules.freedom_touch.haptics import (
    EV_FF,
    FF_RUMBLE,
    Haptics,
)

RIGHT_PATH = "/dev/input/event4"
LEFT_PATH = "/dev/input/event5"
VIRTUAL_PATH = "/dev/input/event6"
XBOX_PATH = "/dev/input/event7"
NO_RUMBLE_PATH = "/dev/input/event8"

RIGHT_REAL = "/sys/devices/platform/soc/i2c-2/2-005a/input/input8"
LEFT_REAL = "/sys/devices/platform/soc/i2c-2/2-005b/input/input9"
VIRTUAL_REAL = "/sys/devices/virtual/input/input10"
XBOX_REAL = "/sys/devices/platform/soc/i2c-1/1-005b/input/input11"
NO_RUMBLE_REAL = "/sys/devices/platform/soc/i2c-2/2-005a/input/input12"

PHYSICAL_NAME = "aw86927-haptics"


def sysfs_link(path):
    return "/sys/class/input/%s/device" % (os.path.basename(path),)


class FakeDevice:
    """Fake InputDevice exposing exactly the real FF method signatures."""

    def __init__(self, name, path, effect_id=40, codes=(FF_RUMBLE,)):
        self.name = name
        self.path = path
        self.effect_id = effect_id
        self.codes = tuple(codes)
        self.uploads = []
        self.writes = []
        self.erases = []
        self.closed = False
        self.fail_write = False

    def capabilities(self):
        return {EV_FF: list(self.codes)}

    def upload_effect(self, effect):
        # No fake evdev construction: the adapter must hand over a real
        # python-evdev ctypes ff.Effect, not a dict or stand-in.
        if not isinstance(effect, ff.Effect):
            raise AssertionError("upload_effect requires a real ff.Effect")
        self.uploads.append(effect)
        return self.effect_id

    def write(self, type_, code, value):
        if self.fail_write:
            raise OSError("device disconnected")
        self.writes.append((type_, code, value))

    def erase_effect(self, effect_id):
        self.erases.append(effect_id)

    def close(self):
        self.closed = True


class Harness:
    def __init__(self, devices):
        self.devices = dict(devices)
        self.opened = []
        self.realpaths = {}

    def list_devices(self):
        return list(self.devices)

    def factory(self, path):
        self.opened.append(path)
        device = self.devices.get(path)
        if device is None:
            raise OSError("no such device")
        return device

    def realpath(self, path):
        return self.realpaths.get(path, path)

    def add_link(self, path, target):
        self.realpaths[sysfs_link(path)] = target


def standard_harness(enabled=True):
    right = FakeDevice(PHYSICAL_NAME, RIGHT_PATH, effect_id=41)
    left = FakeDevice(PHYSICAL_NAME, LEFT_PATH, effect_id=42)
    harness = Harness({RIGHT_PATH: right, LEFT_PATH: left})
    harness.add_link(RIGHT_PATH, RIGHT_REAL)
    harness.add_link(LEFT_PATH, LEFT_REAL)
    haptics = Haptics(
        enabled,
        device_factory=harness.factory,
        list_devices=harness.list_devices,
        realpath=harness.realpath,
    )
    haptics.refresh(0.0)
    return harness, haptics, right, left


def pad_event(side, x, y, touching=True):
    return (
        "pad",
        side,
        {"touching": bool(touching), "x": x, "y": y, "pressure": 1.0},
    )


class HapticsDiscoveryTests(unittest.TestCase):
    def test_selects_only_physical_rumble_nodes(self):
        virtual = FakeDevice(PHYSICAL_NAME, VIRTUAL_PATH, effect_id=51)
        xbox = FakeDevice("Microsoft X-Box 360 pad", XBOX_PATH, effect_id=52)
        no_rumble = FakeDevice(
            PHYSICAL_NAME, NO_RUMBLE_PATH, effect_id=53, codes=()
        )
        right = FakeDevice(PHYSICAL_NAME, RIGHT_PATH, effect_id=54)
        left = FakeDevice(PHYSICAL_NAME, LEFT_PATH, effect_id=55)

        harness = Harness(
            {
                VIRTUAL_PATH: virtual,
                XBOX_PATH: xbox,
                NO_RUMBLE_PATH: no_rumble,
                RIGHT_PATH: right,
                LEFT_PATH: left,
            }
        )
        harness.add_link(VIRTUAL_PATH, VIRTUAL_REAL)
        harness.add_link(XBOX_PATH, XBOX_REAL)
        harness.add_link(NO_RUMBLE_PATH, NO_RUMBLE_REAL)
        harness.add_link(RIGHT_PATH, RIGHT_REAL)
        harness.add_link(LEFT_PATH, LEFT_REAL)

        haptics = Haptics(
            True,
            device_factory=harness.factory,
            list_devices=harness.list_devices,
            realpath=harness.realpath,
        )
        haptics.refresh(0.0)

        self.assertTrue(haptics.available)
        self.assertTrue(virtual.closed)
        self.assertTrue(xbox.closed)
        self.assertTrue(no_rumble.closed)

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 54, 1)])
        self.assertEqual(left.writes, [(EV_FF, 55, 1)])
        self.assertEqual(virtual.writes, [])
        self.assertEqual(xbox.writes, [])
        self.assertEqual(no_rumble.writes, [])

    def test_physical_path_maps_5a_right_and_5b_left(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([pad_event("right", 0.5, 0.5)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [])

        haptics.consume([pad_event("left", 0.5, 0.5)], 1.1)
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])

    def test_missing_or_failing_discovery_is_safe(self):
        empty = Harness({})
        haptics = Haptics(
            True,
            device_factory=empty.factory,
            list_devices=empty.list_devices,
            realpath=empty.realpath,
        )
        haptics.refresh(0.0)
        self.assertFalse(haptics.available)
        self.assertEqual(haptics.detail, "机身振动暂不可用")
        haptics.consume([("button", "a", True)], 1.0)
        haptics.stop()
        haptics.close()

        def broken_list():
            raise OSError("cannot enumerate")

        broken = Haptics(
            True,
            device_factory=empty.factory,
            list_devices=broken_list,
            realpath=empty.realpath,
        )
        broken.refresh(0.0)
        self.assertFalse(broken.available)
        self.assertEqual(broken.detail, "机身振动暂不可用")


class HapticsEventTests(unittest.TestCase):
    def test_button_press_pulses_both_sides_but_release_is_silent(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])

        haptics.consume([("button", "a", False)], 1.1)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])

    def test_pad_press_and_first_contact_match_side(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([("pad_press", "right", True)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [])

        haptics.consume([pad_event("left", 0.4, 0.4)], 1.1)
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])

        haptics.consume([("pad_press", "right", False)], 1.2)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])

    def test_pad_motion_threshold_and_rate_limit(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([pad_event("right", 0.50, 0.50)], 1.0)
        self.assertEqual(len(right.writes), 1)

        # Stationary and sub-threshold movement never buzz continuously.
        haptics.consume([pad_event("right", 0.50, 0.50)], 1.1)
        haptics.consume([pad_event("right", 0.51, 0.50)], 1.2)
        self.assertEqual(len(right.writes), 1)

        # 0.04 exceeds the 0.025 distance threshold.
        haptics.consume([pad_event("right", 0.54, 0.50)], 1.3)
        self.assertEqual(len(right.writes), 2)

        # A large jump inside the 0.045 s window is coalesced.
        haptics.consume([pad_event("right", 0.55, 0.50)], 1.31)
        haptics.consume([pad_event("right", 0.58, 0.50)], 1.32)
        self.assertEqual(len(right.writes), 2)

        # Once the window passes the accumulated move ticks exactly once.
        haptics.consume([pad_event("right", 0.58, 0.50)], 1.36)
        self.assertEqual(len(right.writes), 3)

        # Multifinger duplicates and repeated batches stay silent.
        haptics.consume([pad_event("right", 0.58, 0.50)], 1.5)
        haptics.consume(
            [pad_event("right", 0.58, 0.50), pad_event("right", 0.58, 0.50)],
            1.6,
        )
        self.assertEqual(len(right.writes), 3)
        self.assertEqual(left.writes, [])

    def test_batch_coalescing_and_touch_release(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume(
            [("button", "a", True), ("button", "b", True)], 1.0
        )
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])

        haptics.consume(
            [
                pad_event("right", 0.2, 0.2, touching=False),
                pad_event("right", 0.2, 0.2),
                ("pad_press", "right", True),
            ],
            2.0,
        )
        self.assertEqual(right.writes, [(EV_FF, 41, 1), (EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])

    def test_malformed_events_do_not_raise(self):
        _harness, haptics, _right, _left = standard_harness()

        haptics.consume(None, 1.0)
        haptics.consume("not-a-list", 1.1)
        haptics.consume([None, (), ("pad", "right", None)], 1.2)
        haptics.consume([pad_event("right", "bad", 0.5)], 1.3)

    def test_effect_is_a_valid_short_ff_rumble(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(len(right.uploads), 1)
        self.assertEqual(len(left.uploads), 1)

        effect = right.uploads[0]
        self.assertIsInstance(effect, ff.Effect)
        self.assertEqual(effect.type, FF_RUMBLE)
        self.assertEqual(effect.type, 0x50)
        self.assertEqual(effect.id, -1)
        self.assertEqual(effect.direction, 0)
        self.assertEqual(effect.ff_trigger.button, 0)
        self.assertEqual(effect.ff_trigger.interval, 0)
        self.assertEqual(effect.ff_replay.length, 16)
        self.assertEqual(effect.ff_replay.delay, 0)
        self.assertEqual(effect.u.ff_rumble_effect.strong_magnitude, 0x4000)
        self.assertEqual(effect.u.ff_rumble_effect.weak_magnitude, 0x4000)

        haptics.consume([("button", "b", True)], 2.0)
        self.assertEqual(len(right.uploads), 1)


class HapticsStrengthTests(unittest.TestCase):
    def test_default_strength_keeps_the_0x4000_tick(self):
        _harness, haptics, right, _left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)

        effect = right.uploads[0]
        self.assertEqual(effect.u.ff_rumble_effect.strong_magnitude, 0x4000)
        self.assertEqual(effect.u.ff_rumble_effect.weak_magnitude, 0x4000)
        self.assertEqual(effect.ff_replay.length, 16)

    def test_strength_percent_maps_to_evdev_magnitudes(self):
        for percent, expected in (
            (0, 0),
            (25, 0x4000),
            (75, 49151),
            (100, 0xFFFF),
        ):
            _harness, haptics, right, left = standard_harness()
            haptics.set_strength(percent)

            haptics.consume([("button", "a", True)], 1.0)

            if percent == 0:
                self.assertEqual(right.uploads, [])
                self.assertEqual(right.writes, [])
                self.assertEqual(left.uploads, [])
                self.assertEqual(left.writes, [])
                continue

            effect = right.uploads[0]
            self.assertIsInstance(effect, ff.Effect)
            self.assertEqual(
                effect.u.ff_rumble_effect.strong_magnitude, expected
            )
            self.assertEqual(
                effect.u.ff_rumble_effect.weak_magnitude, expected
            )
            self.assertEqual(effect.ff_replay.length, 16)
            self.assertEqual(right.writes, [(EV_FF, 41, 1)])
            self.assertEqual(left.writes, [(EV_FF, 42, 1)])

    def test_changing_strength_erases_owned_effects_and_reuploads(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(len(right.uploads), 1)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])

        self.assertTrue(haptics.set_strength(75))
        # The old effect is stopped and erased, but the device stays open.
        self.assertEqual(right.writes[-1], (EV_FF, 41, 0))
        self.assertEqual(left.writes[-1], (EV_FF, 42, 0))
        self.assertEqual(right.erases, [41])
        self.assertEqual(left.erases, [42])
        self.assertFalse(right.closed)
        self.assertFalse(left.closed)
        self.assertTrue(haptics.available)

        haptics.consume([("button", "b", True)], 2.0)

        self.assertEqual(len(right.uploads), 2)
        effect = right.uploads[-1]
        self.assertEqual(
            effect.u.ff_rumble_effect.strong_magnitude, 49151
        )
        self.assertEqual(right.writes[-1], (EV_FF, 41, 1))
        self.assertEqual(left.writes[-1], (EV_FF, 42, 1))

        # Re-applying the same value must not disturb a live effect.
        self.assertFalse(haptics.set_strength(75))
        self.assertEqual(right.erases, [41])

    def test_disabled_keeps_strength_without_pulsing(self):
        _harness, haptics, right, left = standard_harness()

        self.assertTrue(haptics.set_strength(75))
        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(
            right.uploads[0].u.ff_rumble_effect.strong_magnitude, 49151
        )

        haptics.set_enabled(False)
        writes = list(right.writes)
        haptics.consume([("button", "b", True)], 2.0)
        self.assertEqual(right.writes, writes)
        self.assertEqual(len(right.uploads), 1)
        self.assertEqual(left.writes, [(EV_FF, 42, 1), (EV_FF, 42, 0)])

        haptics.set_enabled(True)
        haptics.consume([("button", "a", True)], 3.0)
        self.assertEqual(right.writes[-1], (EV_FF, 41, 1))
        self.assertEqual(len(right.uploads), 1)

    def test_invalid_strength_values_are_rejected(self):
        _harness, haptics, _right, _left = standard_harness()

        for value in (
            True,
            False,
            "50",
            None,
            -1,
            101,
            50.5,
            float("nan"),
            float("inf"),
        ):
            self.assertFalse(haptics.set_strength(value))

    def test_preview_ticks_both_sides_without_engine_events(self):
        _harness, haptics, right, left = standard_harness()

        haptics.preview(2.0)

        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])
        self.assertEqual(len(right.uploads), 1)
        self.assertEqual(len(left.uploads), 1)

        haptics.preview(2.1)
        self.assertEqual(right.writes, [(EV_FF, 41, 1), (EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1), (EV_FF, 42, 1)])
        self.assertEqual(len(right.uploads), 1)

    def test_preview_respects_disabled_and_zero_strength(self):
        _harness, haptics, right, left = standard_harness(enabled=False)
        haptics.preview(2.0)
        self.assertEqual(right.writes, [])
        self.assertEqual(left.writes, [])

        _harness, haptics, right, left = standard_harness()
        haptics.set_strength(0)
        haptics.preview(2.0)
        self.assertEqual(right.uploads, [])
        self.assertEqual(right.writes, [])
        self.assertEqual(left.uploads, [])
        self.assertEqual(left.writes, [])


class HapticsLifecycleTests(unittest.TestCase):
    def test_disabled_shows_availability_without_effects(self):
        _harness, haptics, right, left = standard_harness(enabled=False)

        self.assertTrue(haptics.available)
        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(right.uploads, [])
        self.assertEqual(right.writes, [])
        self.assertEqual(left.uploads, [])
        self.assertEqual(left.writes, [])

    def test_disable_stop_and_close_clean_up_owned_effects(self):
        _harness, haptics, right, left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])
        self.assertEqual(len(right.uploads), 1)

        haptics.set_enabled(False)
        self.assertEqual(right.writes[-1], (EV_FF, 41, 0))
        self.assertEqual(left.writes[-1], (EV_FF, 42, 0))
        self.assertFalse(right.closed)

        # Re-enabling starts a fresh gesture instead of resuming old state.
        haptics.set_enabled(True)
        haptics.consume([pad_event("right", 0.3, 0.3)], 2.0)
        self.assertEqual(right.writes[-1], (EV_FF, 41, 1))
        self.assertEqual(len(right.uploads), 1)

        haptics.stop()
        self.assertEqual(right.writes[-1], (EV_FF, 41, 0))
        self.assertEqual(left.writes[-1], (EV_FF, 42, 0))
        self.assertFalse(right.closed)

        # stop() cleared the pad gesture, so contact ticks again.
        haptics.consume([pad_event("right", 0.3, 0.3)], 3.0)
        self.assertEqual(right.writes[-1], (EV_FF, 41, 1))

        haptics.close()
        # close() stops the active effect before erasing and closing.
        self.assertEqual(right.writes[-1], (EV_FF, 41, 0))
        self.assertEqual(left.writes[-1], (EV_FF, 42, 0))
        self.assertEqual(right.erases, [41])
        self.assertEqual(left.erases, [42])
        self.assertTrue(right.closed)
        self.assertTrue(left.closed)
        self.assertFalse(haptics.available)
        self.assertEqual(haptics.detail, "机身振动暂不可用")

    def test_disconnect_closes_one_node_and_retries_only_on_refresh(self):
        harness, haptics, right, left = standard_harness()

        haptics.consume([("button", "a", True)], 1.0)
        self.assertEqual(right.writes, [(EV_FF, 41, 1)])
        right.fail_write = True

        haptics.consume([pad_event("right", 0.4, 0.4)], 2.0)
        self.assertTrue(right.closed)
        self.assertTrue(haptics.available)
        self.assertEqual(left.writes, [(EV_FF, 42, 1)])
        self.assertEqual(right.erases, [41])

        opened_before = harness.opened.count(RIGHT_PATH)
        haptics.consume([("button", "b", True)], 3.0)
        self.assertEqual(harness.opened.count(RIGHT_PATH), opened_before)
        self.assertEqual(left.writes, [(EV_FF, 42, 1), (EV_FF, 42, 1)])

        replacement = FakeDevice(PHYSICAL_NAME, RIGHT_PATH, effect_id=43)
        harness.devices[RIGHT_PATH] = replacement
        haptics.refresh(4.0)
        self.assertEqual(harness.opened.count(RIGHT_PATH), opened_before + 1)

        haptics.consume([("button", "a", True)], 5.0)
        self.assertEqual(replacement.writes, [(EV_FF, 43, 1)])
        self.assertTrue(haptics.available)


if __name__ == "__main__":
    unittest.main()
