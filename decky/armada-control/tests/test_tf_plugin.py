"""RPC integration tests for the TF-library page in the Controller plugin.

``main.Plugin`` must build the migrated TF backend itself: no ``armada_installer``
plugin, no Decky runtime and no real hardware.  Every sibling ``armada_control``
module that ``main`` imports for the existing Controller features is replaced by
an inert stub before ``main.py`` is loaded by path, so only the real
``armada_control.tf_library`` package and the real RPC envelope are exercised.
"""

import asyncio
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

from armada_control.tf_library import service as tf_service  # noqa: E402
from armada_control.tf_library.service import InstallerError, TfLibraryService  # noqa: E402

# The Controller modules ``main`` imports for everything except the TF page.
STUBBED = ("calibration", "config", "controller", "power", "rgb", "steam", "system",
           "tweaks", "fan_curves", "fan_sensors", "home_power")
MAIN_NAME = "armada_control_plugin_main"


def _noop(*args, **kwargs):
    return None


class _StubHomePower:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _noop


class _StubModule(types.ModuleType):
    """A module whose every attribute is an inert callable."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _StubHomePower if name == "HomePower" else _noop


def _install_stubs():
    saved = {}
    for name in STUBBED:
        key = "armada_control." + name
        saved[key] = sys.modules.get(key)
        sys.modules[key] = _StubModule(key)
    return saved


def _restore_stubs(saved):
    for key, module in saved.items():
        if module is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = module


def _load_main():
    spec = importlib.util.spec_from_file_location(MAIN_NAME, ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTf:
    """A TF service double that records its callers instead of touching a card."""

    def __init__(self, payload=None, error=None):
        self.payload = {"cards": []} if payload is None else payload
        self.error = error
        self.calls = []
        self.threads = []

    def _run(self, *args):
        self.calls.append(args)
        self.threads.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return self.payload

    def inspect(self):
        return self._run()

    def prepare(self, device):
        return self._run(device)

    def start(self, token, confirmed=False):
        return self._run(token, confirmed)

    def status(self):
        return self._run()


class StubRunner:
    """Answers only the read-only systemctl/lsblk probes of the real service."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(
                argv, 0, "LoadState=not-found\nActiveState=inactive\nSubState=dead\n", "")
        if argv[0] == "lsblk":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"blockdevices": []}), "")
        raise AssertionError("unexpected command: {}".format(argv))


class TfPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = _install_stubs()
        cls.addClassCleanup(_restore_stubs, cls._saved)
        cls.main = _load_main()

    def setUp(self):
        self.plugin = self.main.Plugin()

    @staticmethod
    def call(method, *args):
        return asyncio.run(method(*args))

    def test_plugin_builds_the_local_tf_service_without_the_old_installer(self):
        self.assertIsInstance(self.plugin._tf, TfLibraryService)
        # The original installer plugin is neither imported nor bundled.
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("armada_installer", source)
        self.assertIn("armada_control.tf_library", source)
        # No internal-install operation is exposed through the plugin.
        for hidden in ("_service", "get_inspection", "prepare_install", "start_install", "power_off"):
            self.assertFalse(hasattr(self.plugin, hidden), hidden)

    def test_fixed_unit_and_shared_paths_are_unchanged(self):
        self.assertEqual(tf_service.TF_UNIT, "armada-tf-library.service")
        self.assertEqual(tf_service.DEFAULT_TF_RUNTIME, pathlib.Path("/run/armada-installer-tf"))
        self.assertEqual(tf_service.DEFAULT_TF_CHECKPOINT, pathlib.Path("/var/lib/armada-installer/tf"))

    def test_every_rpc_returns_its_payload_in_the_ok_envelope(self):
        fake = FakeTf(payload={"cards": [{"device": "/dev/mmcblk0"}]})
        self.plugin._tf = fake
        token = "1" * 32
        self.assertEqual(self.call(self.plugin.get_tf_inspection),
                         {"ok": True, "data": {"cards": [{"device": "/dev/mmcblk0"}]}})
        self.assertEqual(self.call(self.plugin.prepare_tf_library, "/dev/mmcblk0"),
                         {"ok": True, "data": fake.payload})
        self.assertEqual(self.call(self.plugin.start_tf_library, token, True),
                         {"ok": True, "data": fake.payload})
        self.assertEqual(self.call(self.plugin.get_tf_status), {"ok": True, "data": fake.payload})
        self.assertEqual(fake.calls, [(), ("/dev/mmcblk0",), (token, True), ()])

    def test_start_defaults_to_unconfirmed_so_the_service_refuses_it(self):
        fake = FakeTf()
        self.plugin._tf = fake
        self.call(self.plugin.start_tf_library, "2" * 32)
        self.assertEqual(fake.calls, [("2" * 32, False)])

    def test_every_rpc_runs_in_a_worker_thread(self):
        fake = FakeTf()
        self.plugin._tf = fake
        caller = threading.get_ident()
        self.call(self.plugin.get_tf_inspection)
        self.call(self.plugin.prepare_tf_library, "/dev/mmcblk0")
        self.call(self.plugin.start_tf_library, "3" * 32, True)
        self.call(self.plugin.get_tf_status)
        self.assertEqual(len(fake.threads), 4)
        for ident in fake.threads:
            self.assertNotEqual(ident, caller)

    def test_every_rpc_maps_an_installer_error_to_its_code(self):
        methods = ((self.plugin.get_tf_inspection, ()),
                   (self.plugin.prepare_tf_library, ("/dev/mmcblk0",)),
                   (self.plugin.start_tf_library, ("4" * 32, True)),
                   (self.plugin.get_tf_status, ()))
        for method, args in methods:
            with self.subTest(method=method.__name__):
                self.plugin._tf = FakeTf(error=InstallerError("card_busy", "the card is busy"))
                self.assertEqual(self.call(method, *args),
                                 {"ok": False, "error": "card_busy", "detail": "the card is busy"})

    def test_every_rpc_hides_an_unexpected_exception(self):
        methods = ((self.plugin.get_tf_inspection, ()),
                   (self.plugin.prepare_tf_library, ("/dev/mmcblk0",)),
                   (self.plugin.start_tf_library, ("5" * 32, True)),
                   (self.plugin.get_tf_status, ()))
        for method, args in methods:
            with self.subTest(method=method.__name__):
                self.plugin._tf = FakeTf(error=RuntimeError("/dev/mmcblk0: internal detail"))
                result = self.call(method, *args)
                self.assertEqual(result, {"ok": False, "error": "internal_error", "detail": ""})
                self.assertNotIn("internal detail", json.dumps(result))

    def test_real_service_reports_idle_and_an_empty_card_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = StubRunner()
            self.plugin._tf = TfLibraryService(runner=runner, sysfs_dir=pathlib.Path(tmp) / "sys",
                                               runtime_dir=pathlib.Path(tmp) / "run",
                                               checkpoint_dir=pathlib.Path(tmp) / "checkpoints")
            self.assertEqual(self.call(self.plugin.get_tf_status),
                             {"ok": True, "data": {"state": "idle", "phase": "", "detail": "",
                                                   "log_tail": "", "library_requested": False,
                                                   "library_registered": False,
                                                   "library_pending": False}})
            self.assertEqual(self.call(self.plugin.get_tf_inspection), {"ok": True, "data": {"cards": []}})
        # Only read-only probes: no mount, no sgdisk, no mkfs, no launch.
        ran = {call[0] for call in runner.calls}
        self.assertEqual(ran, {"systemctl", "lsblk"})


if __name__ == "__main__":
    unittest.main()
