import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control import display_orientation
from armada_control.display_orientation import get_display_orientation, set_display_orientation


def state(orientation="", supported=True, default="normal"):
    return {"supported": supported, "orientation": orientation, "defaultOrientation": default}


class FakeHelper:
    """Stands in for the privileged socket; records the exact request payload."""

    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    def __call__(self, action, **payload):
        self.calls.append((action, payload))
        if self.error is not None:
            raise self.error
        if not self.results:
            raise AssertionError(f"unexpected call: {action} {payload}")
        return self.results.pop(0)


class DisplayOrientationTest(unittest.TestCase):
    def build(self, results=None, error=None):
        helper = FakeHelper(results=results, error=error)
        patcher = mock.patch.object(display_orientation, "call", helper)
        patcher.start()
        self.addCleanup(patcher.stop)
        return helper


class GetTests(DisplayOrientationTest):
    def test_unset_orientation_is_reported_as_off(self):
        helper = self.build([state("")])

        result = get_display_orientation()

        self.assertEqual(result, state(""))
        self.assertEqual(helper.calls, [("get_display_orientation", {})])

    def test_each_accepted_orientation_passes_through(self):
        for value in ("normal", "left", "right", "upsidedown"):
            with self.subTest(orientation=value):
                helper = self.build([state(value)])

                result = get_display_orientation()

                self.assertEqual(result, state(value))
                self.assertEqual(helper.calls, [("get_display_orientation", {})])

    def test_unknown_orientation_from_helper_is_refused(self):
        for value in ("landscape", "Normal", "left 90", None, 1, ["left"]):
            with self.subTest(orientation=value):
                self.build([state(value)])

                with self.assertRaises(ValueError):
                    get_display_orientation()

    def test_invalid_default_is_refused(self):
        for value in ("sideways", "", None, 90):
            with self.subTest(default=value):
                self.build([state("", default=value)])

                with self.assertRaises(ValueError):
                    get_display_orientation()

    def test_valid_default_is_kept(self):
        self.build([state("", default="upsidedown")])

        self.assertEqual(get_display_orientation()["defaultOrientation"], "upsidedown")

    def test_supported_must_be_a_real_boolean(self):
        for payload in (
            {"orientation": "", "defaultOrientation": "normal"},
            state("", supported=1),
            state("", supported="yes"),
        ):
            with self.subTest(payload=payload):
                self.build([payload])

                with self.assertRaises(ValueError):
                    get_display_orientation()

    def test_supported_false_is_kept(self):
        self.build([state("", supported=False)])

        self.assertEqual(get_display_orientation(), state("", supported=False))

    def test_helper_failure_propagates(self):
        helper = self.build(error=RuntimeError("helper unavailable"))

        with self.assertRaises(RuntimeError) as raised:
            get_display_orientation()

        self.assertEqual(str(raised.exception), "helper unavailable")
        self.assertEqual(helper.calls, [("get_display_orientation", {})])

    def test_non_mapping_result_is_refused(self):
        for payload in (None, [], "ok", 3):
            with self.subTest(payload=payload):
                self.build([payload])

                with self.assertRaises(ValueError):
                    get_display_orientation()

    def test_incomplete_mapping_is_refused(self):
        for payload in ({}, {"supported": True}, {"supported": True, "orientation": ""}):
            with self.subTest(payload=payload):
                self.build([payload])

                with self.assertRaises(ValueError):
                    get_display_orientation()


class SetTests(DisplayOrientationTest):
    def test_each_accepted_value_is_forwarded_and_returned(self):
        for value in ("normal", "left", "right", "upsidedown", ""):
            with self.subTest(orientation=value):
                helper = self.build([state(value)])

                result = set_display_orientation(value)

                self.assertEqual(helper.calls, [("set_display_orientation", {"orientation": value})])
                self.assertEqual(result, state(value))

    def test_unsupported_strings_are_refused_without_calling(self):
        for value in ("landscape", "Normal", " normal", "normal ", "left90", "up-side-down"):
            with self.subTest(orientation=value):
                helper = self.build()

                with self.assertRaises(ValueError):
                    set_display_orientation(value)

                self.assertEqual(helper.calls, [])

    def test_non_string_values_are_refused_without_calling(self):
        for value in (None, 90, True, ["normal"], {"orientation": "normal"}, object()):
            with self.subTest(orientation=value):
                helper = self.build()

                with self.assertRaises(ValueError):
                    set_display_orientation(value)

                self.assertEqual(helper.calls, [])

    def test_helper_failure_propagates(self):
        helper = self.build(error=RuntimeError("helper unavailable"))

        with self.assertRaises(RuntimeError) as raised:
            set_display_orientation("left")

        self.assertEqual(str(raised.exception), "helper unavailable")
        self.assertEqual(helper.calls, [("set_display_orientation", {"orientation": "left"})])

    def test_malformed_result_is_refused(self):
        for payload in (None, {}, [], "left"):
            with self.subTest(payload=payload):
                self.build([payload])

                with self.assertRaises(ValueError):
                    set_display_orientation("left")

    def test_unexpected_orientation_in_result_is_refused(self):
        self.build([state("landscape")])

        with self.assertRaises(ValueError):
            set_display_orientation("left")

    def test_normal_is_not_mapped_to_another_axis(self):
        helper = self.build([state("normal")])

        self.assertEqual(set_display_orientation("normal")["orientation"], "normal")
        self.assertEqual(helper.calls, [("set_display_orientation", {"orientation": "normal"})])


if __name__ == "__main__":
    unittest.main()
