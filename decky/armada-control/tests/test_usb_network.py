import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control import system


class UsbNetworkTest(unittest.TestCase):
    def test_state_comes_from_the_privileged_helper(self):
        with mock.patch.object(system, "call", return_value={"enabled": True, "supported": True}) as call:
            self.assertEqual(system.usb_network_state(), {"enabled": True, "supported": True})
        call.assert_called_once_with("get_usb_network_enabled")

    def test_an_image_without_the_action_hides_the_switch(self):
        with mock.patch.object(system, "call", side_effect=RuntimeError("unknown action")):
            self.assertEqual(system.usb_network_state(), {"enabled": False, "supported": False})

    def test_only_the_unknown_action_runtime_error_is_swallowed(self):
        errors = [
            TimeoutError("helper timed out"),
            OSError("transport failure"),
            ValueError("could not parse helper response"),
            RuntimeError("helper crashed"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(system, "call", side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        system.usb_network_state()
                self.assertIs(caught.exception, error)

    def test_set_passes_a_bool_and_returns_the_applied_state(self):
        with mock.patch.object(system, "call", return_value={"enabled": False, "supported": True}) as call:
            self.assertIs(system.set_usb_network_enabled(0), False)
        call.assert_called_once_with("set_usb_network_enabled", enabled=False)


if __name__ == "__main__":
    unittest.main()
