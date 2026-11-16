"""Wiring tests for the Freedom Touch backend inside the Controller plugin.

``main.Plugin`` must own exactly one touch component, expose the prefixed RPC
surface, and keep every existing Controller feature untouched.  The touch
component itself is replaced per test here; its own behaviour is covered by
``test_plugin_backend.py``.
"""

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(ROOT), str(ROOT / "py_modules")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

MAIN_NAME = "armada_control_plugin_main"


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _noop(*args, **kwargs):
    return None


class _StubHomePower:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _noop


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _StubHomePower if name == "HomePower" else _noop


class _DeckyModule(types.ModuleType):
    pass


def _install_decky_stub():
    module = sys.modules.get("decky")
    if module is None:
        module = _DeckyModule("decky")
        module.DECKY_USER = "decky"
        module.DECKY_PLUGIN_SETTINGS_DIR = tempfile.mkdtemp(
            prefix="touch-controller-settings-"
        )
        sys.modules["decky"] = module
    if not hasattr(module, "logger"):
        module.logger = _Logger()
    return module


_install_decky_stub()


class FakeSystem(types.ModuleType):
    """Only the canonical swipe switches of ``armada_control.system``."""

    def __init__(self):
        super().__init__("armada_control.system")
        self.values = {"overall": True, "in_game": False}
        self.get_calls = []
        self.set_calls = []

    def reset(self):
        self.values = {"overall": True, "in_game": False}
        self.get_calls = []
        self.set_calls = []

    def swipe_gestures_enabled(self):
        self.get_calls.append("overall")
        return self.values["overall"]

    def swipe_gestures_in_game_enabled(self):
        self.get_calls.append("in_game")
        return self.values["in_game"]

    def set_swipe_gestures_enabled(self, enabled):
        self.set_calls.append(("set_swipe_gestures_enabled", enabled))
        self.values["overall"] = bool(enabled)
        return bool(enabled)

    def set_swipe_gestures_in_game_enabled(self, enabled):
        self.set_calls.append(("set_swipe_gestures_in_game_enabled", enabled))
        self.values["in_game"] = bool(enabled)
        return bool(enabled)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _noop


class FakeTfService:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def inspect(self):
        self.calls.append(())
        return {"cards": []}

    def prepare(self, device):
        self.calls.append((device,))
        return {"cards": []}

    def start(self, token, confirmed=False):
        self.calls.append((token, confirmed))
        return {"cards": []}

    def status(self):
        self.calls.append(())
        return {"state": "idle"}


class FakeInstallerError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


STUBBED = (
    "calibration",
    "config",
    "controller",
    "power",
    "rgb",
    "steam",
    "tweaks",
    "fan_curves",
    "fan_sensors",
    "home_power",
)


def _install_stubs():
    saved = {}
    for name in STUBBED:
        key = "armada_control." + name
        saved[key] = sys.modules.get(key)
        sys.modules[key] = _StubModule(key)

    saved["armada_control.system"] = sys.modules.get("armada_control.system")
    system = FakeSystem()
    sys.modules["armada_control.system"] = system

    package = types.ModuleType("armada_control.tf_library")
    package.__path__ = []
    service = types.ModuleType("armada_control.tf_library.service")
    service.InstallerError = FakeInstallerError
    service.TfLibraryService = FakeTfService
    package.service = service
    saved["armada_control.tf_library"] = sys.modules.get("armada_control.tf_library")
    saved["armada_control.tf_library.service"] = sys.modules.get(
        "armada_control.tf_library.service"
    )
    sys.modules["armada_control.tf_library"] = package
    sys.modules["armada_control.tf_library.service"] = service
    return saved, system


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


class FakeTouch:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def record(*args):
            self.calls.append((name, args))
            return {"rpc": name, "args": list(args)}

        return record


class GatedTouch:
    """Records applied swipe policies and parks the first gated call.

    The first call in the mode's series only finishes once the test releases its
    event, which makes the interleaving of two overlapping calls explicit
    instead of timing based. Later calls apply immediately, so a lock-less
    implementation lets the newer policy finish first and keeps the stale one
    last instead of parking it too.
    """

    def __init__(self, mode):
        self._mode = mode
        self._gate_used = False
        self.startup_policies = []
        self.applied = []
        self.startup_started = asyncio.Event()
        self.release_startup = asyncio.Event()
        self.apply_started = asyncio.Event()
        self.release_apply = asyncio.Event()

    async def startup(self, enabled, in_game):
        if self._mode == "startup":
            self.startup_started.set()
            await self.release_startup.wait()
        policy = bool(enabled) and bool(in_game)
        self.startup_policies.append(policy)
        return {"settings": {"edge_swipes": policy}}

    async def sync_swipe_policy(self, enabled, in_game):
        if self._mode == "sync" and not self._gate_used:
            self._gate_used = True
            self.apply_started.set()
            await self.release_apply.wait()
        policy = bool(enabled) and bool(in_game)
        self.applied.append(policy)
        return {"settings": {"edge_swipes": policy}}


class TouchRpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved, cls.system = _install_stubs()
        cls.addClassCleanup(_restore_stubs, cls._saved)
        cls.main = _load_main()

    def setUp(self):
        self.system.reset()
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        decky = _install_decky_stub()
        self._saved_directory = decky.DECKY_PLUGIN_SETTINGS_DIR
        decky.DECKY_PLUGIN_SETTINGS_DIR = self._directory.name
        self.addCleanup(
            setattr, decky, "DECKY_PLUGIN_SETTINGS_DIR", self._saved_directory
        )
        self.plugin = self.main.Plugin()

    @staticmethod
    def call(method, *args):
        return asyncio.run(method(*args))

    def test_prefixed_rpcs_forward_arguments_and_returns(self):
        fake = FakeTouch()
        self.plugin._touch = fake

        cases = (
            ("get_touch_state", (), ("get_state", ()), ()),
            ("set_touch_settings", ({"enabled": False},),
             ("set_settings", ({"enabled": False},)), ({"enabled": False},)),
            ("update_touch_options", ({"opacity": 0.6},),
             ("update_options", ({"opacity": 0.6},)), ({"opacity": 0.6},)),
            ("select_touch_preset", ("tablet_compact",),
             ("select_preset", ("tablet_compact",)), ("tablet_compact",)),
            ("save_touch_preset", ("我的布局", [], 1.0),
             ("save_preset", ("我的布局", [], 1.0, None)),
             ("我的布局", [], 1.0, None)),
            ("save_touch_preset", ("我的布局", [], 1.0, "custom-one"),
             ("save_preset", ("我的布局", [], 1.0, "custom-one")),
             ("我的布局", [], 1.0, "custom-one")),
            ("reset_touch_layout", (), ("reset_layout", ()), ()),
            ("touch_heartbeat", (), ("heartbeat", (None,)), (None,)),
            ("touch_heartbeat", ("12345",), ("heartbeat", ("12345",)), ("12345",)),
            ("preview_touch_haptics", (), ("preview_haptics", ()), ()),
            ("set_touch_controls_visible", (False,),
             ("set_controls_visible", (False,)), (False,)),
        )
        for name, args, target, forwarded in cases:
            with self.subTest(rpc=name, args=args):
                result = self.call(getattr(self.plugin, name), *args)
                self.assertEqual(
                    result, {"rpc": target[0], "args": list(forwarded)}
                )

        self.assertEqual(fake.calls, [case[2] for case in cases])

    def test_existing_controller_features_remain_available(self):
        for name in (
            "get_home_power",
            "set_home_power",
            "get_config",
            "save_power_config",
            "save_tweaks",
            "get_rgb",
            "set_rgb",
            "get_fans_state",
            "save_fan_curves",
            "get_current_temp",
            "set_swipe_gestures_enabled",
            "set_swipe_gestures_in_game_enabled",
            "suspend_swipe_gestures",
            "set_ssh_enabled",
            "get_tf_inspection",
            "prepare_tf_library",
            "start_tf_library",
            "get_tf_status",
        ):
            self.assertTrue(hasattr(self.plugin, name), name)
        self.assertIsInstance(self.plugin._tf, FakeTfService)
        self.assertEqual(
            self.call(self.plugin.get_tf_status),
            {"ok": True, "data": {"state": "idle"}},
        )

    def test_touch_rpcs_do_not_touch_system_switches(self):
        self.call(self.plugin.get_touch_state)
        self.call(self.plugin.update_touch_options, {"opacity": 0.5})
        self.call(self.plugin.touch_heartbeat, "12345")

        self.assertEqual(self.system.set_calls, [])
        self.assertEqual(self.system.get_calls, [])

    def test_startup_reads_the_canonical_switches_once_and_stays_disabled(self):
        self.call(self.plugin._main)
        state = self.call(self.plugin.get_touch_state)

        self.assertEqual(self.system.get_calls, ["overall", "in_game"])
        self.assertIsNone(self.plugin._touch._child)
        self.assertEqual(state["status"]["state"], "disabled")
        self.assertFalse(state["settings"]["enabled"])
        self.assertFalse(state["settings"]["edge_swipes"])

    def test_swipe_rpc_resyncs_the_policy_once(self):
        self.system.values["in_game"] = True
        self.call(self.plugin.set_swipe_gestures_enabled, True)

        state = self.call(self.plugin.get_touch_state)
        self.assertEqual(
            self.system.set_calls, [("set_swipe_gestures_enabled", True)]
        )
        self.assertEqual(self.system.get_calls, ["overall", "in_game"])
        self.assertTrue(state["settings"]["edge_swipes"])

        before = list(self.system.get_calls)
        self.call(self.plugin.touch_heartbeat, "12345")
        self.assertEqual(self.system.get_calls, before)

    def test_in_game_switch_alone_keeps_edge_swipes_off(self):
        self.call(self.plugin.set_swipe_gestures_in_game_enabled, False)

        state = self.call(self.plugin.get_touch_state)
        self.assertFalse(state["settings"]["edge_swipes"])

        self.system.values["in_game"] = True
        self.call(self.plugin.set_swipe_gestures_enabled, True)
        state = self.call(self.plugin.get_touch_state)
        self.assertTrue(state["settings"]["edge_swipes"])

    def test_unload_stops_only_the_owned_components(self):
        order = []

        class Recorder:
            def __init__(self, name):
                self.name = name

            async def shutdown(self):
                order.append(self.name)

            def restore(self):
                order.append(self.name)

        self.plugin._touch = Recorder("touch")
        self.plugin._home_power = Recorder("home-power")

        self.call(self.plugin._unload)

        self.assertEqual(order, ["touch", "home-power"])

    def test_touch_component_is_created_once_by_the_plugin(self):
        self.assertIsNotNone(self.plugin._touch)
        first = self.plugin._touch
        self.call(self.plugin.get_touch_state)
        self.assertIs(self.plugin._touch, first)

    def test_concurrent_sync_cannot_apply_a_stale_policy(self):
        async def scenario():
            touch = GatedTouch("sync")
            self.plugin._touch = touch
            self.system.values["in_game"] = False

            stale = asyncio.create_task(
                self.plugin._sync_touch_swipe_policy()
            )
            # The first sync read the switches and is inside its apply.
            await asyncio.wait_for(touch.apply_started.wait(), 1.0)

            # The canonical switches moved on before the first sync finished.
            self.system.values["in_game"] = True
            fresh = asyncio.create_task(
                self.plugin._sync_touch_swipe_policy()
            )
            _, pending = await asyncio.wait({fresh}, timeout=0.1)
            parked = fresh in pending
            touch.release_apply.set()
            await asyncio.wait_for(asyncio.gather(stale, fresh), 1.0)
            return touch.applied, parked

        applied, parked = asyncio.run(scenario())

        # The newer read waits for the critical section and is applied last;
        # the older policy can never be the final value.
        self.assertTrue(parked)
        self.assertEqual(applied, [False, True])
        self.assertEqual(self.system.get_calls, ["overall", "in_game"] * 2)

    def test_startup_and_policy_sync_share_the_policy_lock(self):
        async def scenario():
            touch = GatedTouch("startup")
            self.plugin._touch = touch
            self.system.values["in_game"] = False

            booting = asyncio.create_task(self.plugin._main())
            await asyncio.wait_for(touch.startup_started.wait(), 1.0)

            # A newer preference arrives while startup still holds the lock.
            self.system.values["in_game"] = True
            syncing = asyncio.create_task(
                self.plugin._sync_touch_swipe_policy()
            )
            _, pending = await asyncio.wait({syncing}, timeout=0.1)
            parked = syncing in pending
            touch.release_startup.set()
            await asyncio.wait_for(asyncio.gather(booting, syncing), 1.0)
            return touch.startup_policies, touch.applied, parked

        startup_policies, applied, parked = asyncio.run(scenario())

        # Startup kept the saved (older) policy, the queued sync applied the
        # newer one afterwards -- the older initialization never wins.
        self.assertTrue(parked)
        self.assertEqual(startup_policies, [False])
        self.assertEqual(applied, [True])


if __name__ == "__main__":
    unittest.main()
