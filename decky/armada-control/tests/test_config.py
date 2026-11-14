import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

# tweaks.py imports two libraries that ship with the system image and are not
# part of the standalone control repo. They are only referenced inside tweak
# helpers that every test stubs, so empty stand-ins cover the import without
# touching real host services, files, or network.
EXTERNAL_MODULE_STUBS = {
    name: types.ModuleType(name) for name in ("armada_game_tweaks", "armada_turnip")
}

# Remove the stand-ins again once the import finishes: the helpers keep their
# own module references, and sys.modules stays clean for the rest of the run.
with mock.patch.dict(sys.modules, EXTERNAL_MODULE_STUBS):
    from armada_control import config, privileged

CONTROLLER_FIELDS = ("controllerType", "controllerTypes")
SENTINEL_TYPE = "sentinel-controller-type"


class ConfigTestCase(unittest.TestCase):
    """Run the real build_config with every unrelated system read stubbed."""

    def setUp(self):
        self.stubs = {
            "load_fex_contract": {"profiles": {"default": {"config": {}}}},
            "device_env": {
                "ARMADA_DEVICE_ID": "test-device",
                "ARMADA_SOC_CLASS": "test-soc",
                "ARMADA_PROTON_DEFAULTS": "one: two",
                "ARMADA_SUSPEND_MODE": "s2idle",
                "ARMADA_SECONDARY_CONNECTOR": "connector",
                "ARMADA_SECONDARY_TOUCHSCREEN": "touchscreen",
            },
            "bottom_screen_brightness": 42,
            "usb_network_state": {"enabled": True, "supported": True},
            "parse_power": {
                "general": {"default_profile": "balanced"},
                "profiles": {},
                "fan_curves": {},
                "fan": {"charging_pwm": "77"},
                "underclocks": {},
            },
            "factory_power_defaults": {"fan": {}},
            "load_tweaks": {},
            "installed_games": [{"appid": "1"}],
            "fex_profile_labels": {},
            "turnip_drivers": [],
            "load_env_presets": [],
            "perf_info": {
                "governors": [],
                "schedulers": [],
                "corePresets": [],
                "cpuCount": 8,
            },
            "rgb_supported": False,
            "os_version": "test-os",
            "bottom_screen_enabled": True,
            "bottom_screen_active": True,
            "fan_supported": True,
            "ssh_enabled": True,
            "swipe_gestures_enabled": True,
            "swipe_gestures_in_game_enabled": False,
            "mtp_enabled": True,
            "desktop_mode": "desktop",
            "desktop_modes": [],
            "sleep_modes": [],
        }
        self.mocks = {
            name: self.patch_config(name, mock.Mock(return_value=value))
            for name, value in self.stubs.items()
        }

        # Controller lookups may no longer be imported by config after the fix,
        # so these attributes are patched with create=True. A sentinel return
        # value makes any surviving call visible in the built config too.
        self.config_controller_type = mock.Mock(return_value=SENTINEL_TYPE)
        self.config_inputplumber_targets = mock.Mock(return_value=[SENTINEL_TYPE])
        self.config_controller_types = {SENTINEL_TYPE: "Sentinel"}
        self.patch_config("controller_type", self.config_controller_type, create=True)
        self.patch_config(
            "inputplumber_targets", self.config_inputplumber_targets, create=True
        )
        self.patch_config("CONTROLLER_TYPES", self.config_controller_types, create=True)

        self.privileged_call = mock.Mock(
            side_effect=AssertionError("privileged helper must not run while building config")
        )
        self.patch_module(privileged, "call", self.privileged_call)

        self.subprocess_run = mock.Mock(
            side_effect=AssertionError("subprocess must not run while building config")
        )
        self.subprocess_check_output = mock.Mock(
            side_effect=AssertionError("subprocess must not run while building config")
        )
        self.patch_module(subprocess, "run", self.subprocess_run)
        self.patch_module(subprocess, "check_output", self.subprocess_check_output)

    def patch_config(self, name, value, create=False):
        patcher = mock.patch.object(config, name, value, create=create)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def patch_module(self, module, name, value):
        patcher = mock.patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def build(self):
        return config.build_config(include_games=False)


class ControllerRemovalTests(ConfigTestCase):
    def test_config_has_no_controller_fields(self):
        result = self.build()

        for field in CONTROLLER_FIELDS:
            self.assertNotIn(field, result)
        self.assertEqual([key for key in result if "controller" in key.lower()], [])

    def test_config_does_not_query_controller_state(self):
        self.build()

        self.config_controller_type.assert_not_called()
        self.config_inputplumber_targets.assert_not_called()
        self.privileged_call.assert_not_called()
        self.subprocess_run.assert_not_called()
        self.subprocess_check_output.assert_not_called()

    def test_config_does_not_call_the_privileged_controller_helper(self):
        self.build()

        self.privileged_call.assert_not_called()
        self.subprocess_check_output.assert_not_called()


class UnrelatedConfigTests(ConfigTestCase):
    def test_non_controller_fields_remain(self):
        result = self.build()

        self.assertEqual(result["deviceId"], "test-device")
        self.assertEqual(result["cpuDeviceClass"], "test-soc")
        self.assertEqual(result["power"]["general"]["default_profile"], "balanced")
        self.assertEqual(result["chargingFanPwm"], 77)
        self.assertTrue(result["usbNetworkEnabled"])
        self.assertTrue(result["usbNetworkSupported"])
        self.assertEqual(result["osVersion"], "test-os")
        self.assertEqual(result["protonDefaults"], ["one", "two"])
        self.assertEqual(result["sleepMode"], "s2idle")
        self.assertTrue(result["bottomScreenSupported"])
        self.assertEqual(result["bottomScreenBrightness"], 42)

    def test_include_games_false_does_not_enumerate_games(self):
        result = self.build()

        self.assertEqual(result["installedGames"], [])
        self.mocks["installed_games"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
