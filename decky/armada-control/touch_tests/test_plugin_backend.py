import asyncio
import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(PLUGIN_ROOT), str(PLUGIN_ROOT / "py_modules")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _install_decky_stub():
    stub = types.ModuleType("decky")
    stub.logger = _Logger()
    stub.DECKY_USER = "decky"
    stub.DECKY_PLUGIN_SETTINGS_DIR = str(
        Path(__file__).resolve().parent / "_unused_settings"
    )
    sys.modules.setdefault("decky", stub)
    return sys.modules["decky"]


decky_stub = _install_decky_stub()

from armada_control import touch_controller as controller_module  # noqa: E402
from py_modules.freedom_touch.layout import default_layout  # noqa: E402


async def _start(instance, edge_swipes=False):
    """Load the saved state with an explicit canonical swipe policy."""

    return await instance.startup(bool(edge_swipes), bool(edge_swipes))


def test_plugin_root_is_the_plugin_directory_not_the_package():
    assert controller_module.PLUGIN_ROOT == str(PLUGIN_ROOT)
    daemon = Path(controller_module.PLUGIN_ROOT, "py_modules", "freedom_touch", "daemon.py")
    assert daemon.is_file()


class _FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _FakeChild:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None

    async def wait(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    settings_dir = tmp_path / "settings"
    monkeypatch.setattr(
        controller_module.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(settings_dir)
    )
    instance = controller_module.TouchController()

    async def no_start():
        instance._child = None
        return True

    instance._start_child = no_start
    return instance


def _settings_payload(**overrides):
    payload = {
        "enabled": True,
        "opacity": 0.8,
        "scale": 1.2,
        "deadzone": 0.2,
        "layout": default_layout(),
    }
    payload.update(overrides)
    return payload


def test_set_settings_validates_persists_and_returns_canonical(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        return await plugin.set_settings(_settings_payload())

    state = asyncio.run(scenario())
    expected = controller_module.validate_settings(_settings_payload())
    expected_from_disk = json.loads(json.dumps(expected))

    assert state["settings"] == expected
    assert set(state["status"]) == {
        "state",
        "detail",
        "capturing",
        "controls_visible",
    }
    assert state["status"]["capturing"] is False
    assert state["status"]["controls_visible"] is True

    path = tmp_path / "settings" / "touch-controller.json"
    with path.open("r", encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted == expected_from_disk
    assert not list((tmp_path / "settings").glob(".touch-controller-*.tmp"))


def test_set_settings_rejects_invalid_without_touching_file(plugin, tmp_path):
    path = tmp_path / "settings" / "touch-controller.json"

    with pytest.raises(ValueError):
        asyncio.run(plugin.set_settings({"opacity": 2.0}))

    assert not path.exists()


def test_invalid_existing_config_reports_error_and_is_not_overwritten(
    plugin, tmp_path
):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    path = settings_dir / "touch-controller.json"
    original = "{not valid json"
    path.write_text(original, encoding="utf-8")

    async def scenario():
        await _start(plugin)
        return await plugin.get_state()

    state = asyncio.run(scenario())

    assert state["status"]["state"] == "error"
    assert "无效" in state["status"]["detail"]
    assert state["status"]["capturing"] is False
    assert path.read_text(encoding="utf-8") == original


def test_reset_layout_selects_tablet_standard_and_keeps_other_options(plugin):
    default = default_layout()
    custom = list(reversed(default))
    canonical_default = controller_module.validate_settings({"layout": default})

    async def scenario():
        await _start(plugin)
        await plugin.set_settings(
            _settings_payload(
                enabled=True,
                opacity=0.75,
                scale=1.25,
                deadzone=0.3,
                layout=custom,
            )
        )
        return await plugin.reset_layout()

    state = asyncio.run(scenario())

    assert state["settings"]["enabled"] is True
    assert state["settings"]["opacity"] == 0.75
    assert state["settings"]["deadzone"] == 0.3
    assert state["settings"]["scale"] == 1.0
    assert state["settings"]["layout"] == canonical_default["layout"]
    assert state["settings"]["active_preset_id"] == "tablet_standard"
    assert state["presets"][0]["id"] == "tablet_standard"
    assert state["presets"][0]["builtin"] is True


def _new_plugin():
    instance = controller_module.TouchController()

    async def no_start():
        instance._child = None
        return True

    instance._start_child = no_start
    return instance


def test_save_preset_returns_fresh_state_and_reload_preserves_it(
    plugin, tmp_path
):
    layout = default_layout()

    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset("  我的布局  ", layout, 1.0)
        reloaded = _new_plugin()
        await _start(reloaded)
        return saved, await reloaded.get_state()

    saved, reloaded = asyncio.run(scenario())

    preset = saved["presets"][-1]
    assert preset["builtin"] is False
    assert preset["name"] == "我的布局"
    assert preset["id"] != "tablet_standard"
    assert preset["scale"] == 1.0
    assert saved["settings"]["active_preset_id"] == preset["id"]
    assert saved["settings"]["custom_presets"] == [
        {
            "id": preset["id"],
            "name": preset["name"],
            "layout": controller_module.validate_settings(
                {"layout": layout}
            )["layout"],
            "scale": 1.0,
        }
    ]
    assert reloaded["settings"] == saved["settings"]
    assert reloaded["presets"] == saved["presets"]

    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted == json.loads(json.dumps(saved["settings"]))
    assert persisted["version"] == 2


def test_save_preset_state_and_catalog_are_independent_copies(plugin):
    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset("副本", default_layout(), 1.0)
        saved["settings"]["layout"][0]["x"] = 0.9
        saved["presets"][-1]["layout"][0]["x"] = 0.8
        saved["settings"]["custom_presets"][0]["layout"][0]["x"] = 0.7
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["settings"]["layout"][0]["x"] == 0.035
    assert state["presets"][-1]["layout"][0]["x"] == 0.035
    assert state["settings"]["custom_presets"][0]["layout"][0]["x"] == 0.035


def test_select_preset_copies_layout_and_scale_but_preserves_options(plugin):
    base = _settings_payload(
        enabled=True,
        opacity=0.75,
        scale=1.25,
        deadzone=0.3,
        edge_swipes=True,
    )

    async def scenario():
        await _start(plugin, edge_swipes=True)
        await plugin.set_settings(base)
        return await plugin.select_preset("tablet_compact")

    state = asyncio.run(scenario())
    assert state["settings"]["enabled"] is True
    assert state["settings"]["opacity"] == 0.75
    assert state["settings"]["deadzone"] == 0.3
    assert state["settings"]["edge_swipes"] is True
    assert state["settings"]["scale"] == 0.95
    assert state["settings"]["active_preset_id"] == "tablet_compact"
    assert state["settings"]["layout"] == controller_module.validate_settings(
        {"active_preset_id": "tablet_compact"}
    )["layout"]


def test_select_unknown_preset_is_rejected(plugin):
    async def scenario():
        await _start(plugin)
        await plugin.select_preset("does-not-exist")

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_legacy_v1_settings_are_backed_up_without_loss(plugin, tmp_path):
    legacy_layout = list(reversed(default_layout()))
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    legacy = {
        "enabled": True,
        "opacity": 0.65,
        "scale": 1.15,
        "deadzone": 0.25,
        "layout": legacy_layout,
        "version": 1,
    }
    (settings_dir / "touch-controller.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    async def scenario():
        await _start(plugin)
        migrated = await plugin.get_state()
        reloaded = _new_plugin()
        await _start(reloaded)
        return migrated, await reloaded.get_state()

    migrated, reloaded = asyncio.run(scenario())

    legacy_preset = migrated["settings"]["custom_presets"][0]
    assert legacy_preset["id"] == "custom_legacy"
    assert legacy_preset["name"] == "原布局"
    assert legacy_preset["scale"] == 1.15
    assert legacy_preset["layout"] == controller_module.validate_settings(
        {"layout": legacy_layout}
    )["layout"]
    assert migrated["settings"]["active_preset_id"] == "tablet_standard"
    assert migrated["settings"]["layout"] == controller_module.validate_settings(
        {"layout": default_layout()}
    )["layout"]
    assert migrated["settings"]["enabled"] is True
    assert migrated["settings"]["opacity"] == 0.65
    assert migrated["settings"]["deadzone"] == 0.25
    assert migrated["settings"]["haptics_enabled"] is True
    assert reloaded["settings"] == migrated["settings"]

    with (settings_dir / "touch-controller.json").open("r", encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted["version"] == 2
    assert persisted["custom_presets"] == migrated["settings"][
        "custom_presets"
    ]


def test_save_preset_rejects_invalid_and_duplicate_names(plugin):
    layout = default_layout()

    async def scenario():
        await _start(plugin)
        first = await plugin.save_preset("同名", layout, 1.0)
        first_id = first["settings"]["active_preset_id"]
        with pytest.raises(ValueError):
            await plugin.save_preset("同名", layout, 1.0)
        for bad in ("", "   ", "x" * 49):
            with pytest.raises(ValueError):
                await plugin.save_preset(bad, layout, 1.0)
        return await plugin.save_preset(
            "同名", layout, 1.0, overwrite_id=first_id
        )

    state = asyncio.run(scenario())
    assert len(state["settings"]["custom_presets"]) == 1
    assert state["settings"]["custom_presets"][0]["name"] == "同名"


def test_builtin_presets_are_read_only(plugin):
    layout = default_layout()

    async def scenario():
        await _start(plugin)
        with pytest.raises(ValueError):
            await plugin.save_preset(
                "覆盖标准", layout, 1.0, overwrite_id="tablet_standard"
            )
        with pytest.raises(ValueError):
            await plugin.save_preset("平板标准", layout, 1.0)
        with pytest.raises(ValueError):
            await plugin.save_preset(
                "自定义", layout, 1.0, overwrite_id="not-a-preset"
            )

    asyncio.run(scenario())


def test_update_options_preserves_selected_preset_and_custom_catalog(plugin):
    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset("保留", default_layout(), 1.0)
        preset_id = saved["settings"]["active_preset_id"]
        state = await plugin.update_options({"opacity": 0.9, "deadzone": 0.2})
        return preset_id, state

    preset_id, state = asyncio.run(scenario())
    assert state["settings"]["opacity"] == 0.9
    assert state["settings"]["deadzone"] == 0.2
    assert state["settings"]["active_preset_id"] == preset_id
    assert state["settings"]["custom_presets"][0]["id"] == preset_id
    assert [preset["id"] for preset in state["presets"]][-1] == preset_id


def test_haptics_option_roundtrips_and_persists(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        off = await plugin.update_options({"haptics_enabled": False})
        reloaded = _new_plugin()
        await _start(reloaded)
        return off, await reloaded.get_state()

    off, reloaded = asyncio.run(scenario())

    assert off["settings"]["haptics_enabled"] is False
    assert off["settings"]["enabled"] is False
    assert reloaded["settings"]["haptics_enabled"] is False
    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted["haptics_enabled"] is False


def test_haptics_strength_roundtrips_and_persists(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        await plugin.update_options({"haptics_strength": 70})
        result = await plugin.update_options({"haptics_enabled": False})
        reloaded = _new_plugin()
        await _start(reloaded)
        return result, await reloaded.get_state()

    result, reloaded = asyncio.run(scenario())

    assert result["settings"]["haptics_strength"] == 70
    assert result["settings"]["haptics_enabled"] is False
    assert reloaded["settings"]["haptics_strength"] == 70
    assert reloaded["settings"]["haptics_enabled"] is False
    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted["haptics_strength"] == 70
    assert persisted["version"] == 2


def test_strength_update_preserves_custom_preset(plugin):
    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset("强度", default_layout(), 1.0)
        preset_id = saved["settings"]["active_preset_id"]
        state = await plugin.update_options({"haptics_strength": 60})
        return preset_id, state

    preset_id, state = asyncio.run(scenario())

    assert state["settings"]["haptics_strength"] == 60
    assert state["settings"]["active_preset_id"] == preset_id
    assert state["settings"]["custom_presets"][0]["id"] == preset_id
    assert [preset["id"] for preset in state["presets"]][-1] == preset_id
    assert state["settings"]["layout"] == state["settings"]["custom_presets"][0][
        "layout"
    ]


def test_update_options_rejects_invalid_haptics_strength(plugin):
    async def scenario():
        await _start(plugin)
        for value in (True, "50", None, -1, 101, 25.5, float("nan")):
            with pytest.raises(ValueError):
                await plugin.update_options({"haptics_strength": value})

    asyncio.run(scenario())


def _preview_ready(plugin):
    child = _FakeChild(returncode=None)
    plugin._child = child
    plugin._settings = controller_module.validate_settings(
        {
            "enabled": True,
            "haptics_enabled": True,
            "haptics_strength": 50,
        }
    )
    plugin._status = {
        "state": "ready",
        "detail": "ok",
        "capturing": False,
        "haptics_available": True,
        "haptics_detail": "机身振动已就绪",
    }
    return child


def test_preview_haptics_sends_only_the_preview_message(plugin):
    child = _preview_ready(plugin)

    result = asyncio.run(plugin.preview_haptics())

    assert result == {"queued": True}
    lines = child.stdin.data.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"type": "preview_haptics"}


def test_preview_haptics_is_gated_without_sending(plugin):
    cases = (
        {"enabled": False, "haptics_enabled": True, "haptics_strength": 50},
        {"enabled": True, "haptics_enabled": False, "haptics_strength": 50},
        {"enabled": True, "haptics_enabled": True, "haptics_strength": 0},
    )

    async def scenario():
        await _start(plugin)
        for settings in cases:
            child = _preview_ready(plugin)
            plugin._settings = controller_module.validate_settings(settings)
            assert await plugin.preview_haptics() == {"queued": False}
            assert child.stdin.data == b""

        child = _preview_ready(plugin)
        plugin._status["haptics_available"] = False
        assert await plugin.preview_haptics() == {"queued": False}
        assert child.stdin.data == b""

        child = _preview_ready(plugin)
        child.returncode = 7
        assert await plugin.preview_haptics() == {"queued": False}
        assert child.stdin.data == b""

    asyncio.run(scenario())


def test_update_options_rejects_non_option_fields(plugin):
    async def scenario():
        await _start(plugin)
        with pytest.raises(ValueError):
            await plugin.update_options({"layout": default_layout()})
        with pytest.raises(ValueError):
            await plugin.update_options({"active_preset_id": "tablet_compact"})
        with pytest.raises(ValueError):
            await plugin.update_options("not-a-dict")

    asyncio.run(scenario())


def test_persist_failure_leaves_last_state_unchanged(plugin, tmp_path, monkeypatch):
    async def scenario():
        await _start(plugin)
        await plugin.update_options({"opacity": 0.6})
        before = await plugin.get_state()

        def fail(settings):
            raise OSError("disk full")

        monkeypatch.setattr(plugin, "_persist_settings", fail)
        with pytest.raises(OSError):
            await plugin.update_options({"opacity": 0.9})
        return before, await plugin.get_state()

    before, after = asyncio.run(scenario())
    assert after == before
    assert after["settings"]["opacity"] == 0.6

    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted["opacity"] == 0.6


def test_set_settings_pushes_canonical_settings(plugin):
    child = _FakeChild(returncode=None)
    plugin._child = child

    state = asyncio.run(plugin.set_settings(_settings_payload(enabled=True)))

    message = json.loads(child.stdin.data.decode("utf-8"))
    assert message == {
        "type": "settings",
        "settings": json.loads(json.dumps(state["settings"])),
    }


def test_heartbeat_rejects_non_string_appid(plugin):
    with pytest.raises(ValueError):
        asyncio.run(plugin.heartbeat(123))


def test_heartbeat_sends_protocol_message_without_persisting(plugin, tmp_path):
    child = _FakeChild(returncode=None)
    # A heartbeat only belongs to an enabled, running controller; the fixture
    # starts disabled, so mark the ownership it is meant to exercise.
    plugin._child = child
    plugin._settings = controller_module.validate_settings({"enabled": True})
    plugin._status = {
        "state": "ready",
        "detail": "ok",
        "capturing": False,
        "controls_visible": True,
    }

    state = asyncio.run(plugin.heartbeat("12345"))

    assert json.loads(child.stdin.data.decode("utf-8")) == {
        "type": "heartbeat",
        "appid": "12345",
    }
    assert state["state"] == "ready"
    assert "settings" not in state
    assert set(state) <= {
        "state",
        "detail",
        "capturing",
        "controls_visible",
        "source_path",
        "composite_path",
        "device",
        "rotation",
    }
    assert not (tmp_path / "settings" / "touch-controller.json").exists()


class _GatedStdin(_FakeStdin):
    """Fake stdin that blocks inside drain until the test releases it."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def drain(self):
        self.entered.set()
        await self.release.wait()


class _GatedChild(_FakeChild):
    def __init__(self):
        super().__init__(returncode=None)
        self.stdin = _GatedStdin()


def test_heartbeat_queued_behind_disable_does_not_report_unavailable(plugin):
    async def scenario():
        await _start(plugin)
        child = _GatedChild()
        plugin._settings = controller_module.validate_settings({"enabled": True})
        plugin._status = {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "controls_visible": True,
        }
        plugin._child = child

        disable = asyncio.create_task(plugin.update_options({"enabled": False}))
        # The disable is already inside the controller lock: its stop command
        # reached the child and the drain gate keeps it from completing, so the
        # lock is held for the whole window the heartbeat arrives in.
        await asyncio.wait_for(child.stdin.entered.wait(), 1.0)

        heartbeat = asyncio.create_task(plugin.heartbeat("12345"))
        # One scheduling turn lets the heartbeat reach the held lock and queue
        # behind the disable; this is ordering, not a timing wait.
        await asyncio.sleep(0)

        child.stdin.release.set()
        disabled = await asyncio.wait_for(disable, 1.0)
        state = await asyncio.wait_for(heartbeat, 1.0)
        return child, disabled, state, await plugin.get_state()

    child, disabled, state, final = asyncio.run(scenario())

    assert [
        json.loads(line)
        for line in child.stdin.data.decode("utf-8").splitlines()
    ] == [{"type": "stop"}]
    assert disabled["settings"]["enabled"] is False
    assert disabled["status"]["state"] == "disabled"
    # The heartbeat observed the disabled ownership the disable left behind.
    assert state["state"] == "disabled"
    assert "心跳" not in state["detail"]
    assert state["capturing"] is False
    assert final["status"] == state
    assert final["settings"]["enabled"] is False


def _emit_status(plugin, child, controls_visible, **overrides):
    status = {
        "state": "ready",
        "detail": "ok",
        "capturing": False,
        "controls_visible": controls_visible,
    }
    status.update(overrides)
    line = json.dumps({"type": "status", "status": status}).encode("utf-8")
    plugin._handle_stdout_line(child, line)


def test_controls_visible_defaults_true_and_survives_lifecycle_status(plugin):
    async def scenario():
        await _start(plugin)
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["status"]["controls_visible"] is True


def test_set_controls_visible_is_strict_boolean(plugin):
    async def scenario():
        await _start(plugin)
        plugin._settings = controller_module.validate_settings({"enabled": True})
        for value in (0, 1, "false", None, [], {}):
            with pytest.raises(ValueError):
                await plugin.set_controls_visible(value)

    asyncio.run(scenario())


def test_set_controls_visible_rejects_disabled_or_missing_child(plugin):
    async def scenario():
        await _start(plugin)
        with pytest.raises(RuntimeError):
            await plugin.set_controls_visible(False)

        plugin._settings = controller_module.validate_settings({"enabled": True})
        with pytest.raises(RuntimeError):
            await plugin.set_controls_visible(False)

        child = _FakeChild(returncode=3)
        plugin._child = child
        with pytest.raises(RuntimeError):
            await plugin.set_controls_visible(False)
        assert child.stdin.data == b""

    asyncio.run(scenario())


def test_set_controls_visible_sends_and_waits_for_matching_status(plugin):
    child = _FakeChild(returncode=None)

    async def scenario():
        await _start(plugin)
        plugin._settings = controller_module.validate_settings({"enabled": True})
        plugin._child = child

        task = asyncio.create_task(plugin.set_controls_visible(False))
        for _ in range(20):
            await asyncio.sleep(0)
            if child.stdin.data:
                break
        assert json.loads(child.stdin.data.decode("utf-8")) == {
            "type": "controls_visibility",
            "visible": False,
        }

        # A different value is not an acknowledgement; a matching status is.
        _emit_status(plugin, child, True)
        await asyncio.sleep(0)
        assert not task.done()
        _emit_status(plugin, child, False)
        return await asyncio.wait_for(task, 1.0)

    state = asyncio.run(scenario())
    assert state["status"]["controls_visible"] is False
    assert state["status"]["capturing"] is False


def test_set_controls_visible_timeout_does_not_claim_applied(plugin, monkeypatch):
    child = _FakeChild(returncode=None)
    monkeypatch.setattr(controller_module, "CONTROLS_VISIBLE_TIMEOUT", 0.05)

    async def scenario():
        await _start(plugin)
        plugin._settings = controller_module.validate_settings({"enabled": True})
        plugin._child = child
        plugin._status = {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "controls_visible": True,
        }

        task = asyncio.create_task(plugin.set_controls_visible(False))
        for _ in range(20):
            await asyncio.sleep(0)
            if child.stdin.data:
                break
        _emit_status(plugin, child, True)
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, 1.0)
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["status"]["controls_visible"] is True


def test_set_controls_visible_command_error_does_not_claim_applied(
    plugin, monkeypatch
):
    child = _FakeChild(returncode=None)

    async def fail(_message):
        raise RuntimeError("pipe closed")

    async def scenario():
        await _start(plugin)
        plugin._settings = controller_module.validate_settings({"enabled": True})
        plugin._child = child
        monkeypatch.setattr(plugin, "_send_unlocked", fail)
        with pytest.raises(RuntimeError):
            await plugin.set_controls_visible(False)
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["status"]["controls_visible"] is True
    assert child.stdin.data == b""


def test_set_controls_visible_never_writes_settings(plugin, tmp_path):
    child = _FakeChild(returncode=None)

    async def scenario():
        await _start(plugin)
        plugin._settings = controller_module.validate_settings({"enabled": True})
        plugin._child = child
        task = asyncio.create_task(plugin.set_controls_visible(False))
        for _ in range(20):
            await asyncio.sleep(0)
            if child.stdin.data:
                break
        _emit_status(plugin, child, False)
        await asyncio.wait_for(task, 1.0)
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["status"]["controls_visible"] is False
    assert not (tmp_path / "settings" / "touch-controller.json").exists()


def test_parse_status_preserves_controls_visible():
    status = controller_module.TouchController._parse_status(
        {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "controls_visible": False,
        }
    )
    assert status["controls_visible"] is False
    assert controller_module.TouchController._parse_status(
        {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "controls_visible": "no",
        }
    )["controls_visible"] is True


def test_child_exit_reports_unavailable(plugin):
    child = _FakeChild(returncode=7)
    plugin._child = child

    asyncio.run(plugin._wait_child(child))

    state = plugin._snapshot()
    assert state["status"]["state"] == "unavailable"
    assert state["status"]["capturing"] is False
    assert "7" in state["status"]["detail"]


def test_stop_cancels_tasks_and_closes_stdin(plugin):
    child = _FakeChild(returncode=0)

    async def scenario():
        plugin._child = child
        plugin._stdout_task = asyncio.create_task(asyncio.sleep(60))
        plugin._stderr_task = asyncio.create_task(asyncio.sleep(60))
        plugin._wait_task = asyncio.create_task(asyncio.sleep(60))
        stdout_task = plugin._stdout_task
        await plugin._stop_child()
        return stdout_task

    stdout_task = asyncio.run(scenario())

    assert stdout_task.cancelled()
    assert child.stdin.closed is True
    assert plugin._child is None
    assert plugin._stdout_task is None
    assert plugin._stderr_task is None
    assert plugin._wait_task is None


class _HangingStdin(_FakeStdin):
    """Fake stdin whose drain never completes: the pipe is never readable."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()

    async def drain(self):
        self.entered.set()
        await asyncio.Event().wait()


class _StuckChild(_FakeChild):
    """Process that only exits after the controller escalates the signal."""

    def __init__(self):
        super().__init__(returncode=None)
        self.stdin = _HangingStdin()
        self.exited = asyncio.Event()
        self.terminated = False
        self.killed = False

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def terminate(self):
        super().terminate()
        self.terminated = True
        self.exited.set()

    def kill(self):
        super().kill()
        self.killed = True
        self.exited.set()


class _UnkillableChild(_StuckChild):
    """Process that ignores both SIGTERM and SIGKILL."""

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _patch_child_timeouts(monkeypatch):
    # The write and stream-close bounds are the behaviour under test, so they
    # stay optional: an implementation without them must fail on the real
    # endless wait (caught by the outer timeout), not on a missing attribute.
    monkeypatch.setattr(
        controller_module, "WRITE_TIMEOUT_SECONDS", 0.01, raising=False
    )
    monkeypatch.setattr(controller_module, "STOP_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(controller_module, "TERMINATE_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(
        controller_module, "STREAM_CLOSE_TIMEOUT_SECONDS", 0.01, raising=False
    )


@pytest.mark.parametrize("entry", ["disable", "shutdown"])
def test_stuck_write_is_bounded_and_reclaims_the_owned_child(
    plugin, monkeypatch, entry
):
    _patch_child_timeouts(monkeypatch)

    async def scenario():
        await _start(plugin)
        child = _StuckChild()
        plugin._child = child
        plugin._settings = controller_module.validate_settings({"enabled": True})
        if entry == "disable":
            return child, await plugin.update_options({"enabled": False})

        await plugin.shutdown()
        return child, None

    child, state = asyncio.run(asyncio.wait_for(scenario(), 1.0))

    # The stop write was attempted, timed out, and the child was reclaimed
    # through terminate instead of an endless write or an endless close.
    assert json.loads(child.stdin.data.decode("utf-8")) == {"type": "stop"}
    assert child.terminated is True
    assert plugin._child is None
    if entry == "disable":
        assert state["settings"]["enabled"] is False
        assert state["status"]["state"] == "disabled"


def test_surviving_child_keeps_its_handle_and_is_not_reported_stopped(
    plugin, monkeypatch
):
    _patch_child_timeouts(monkeypatch)

    async def scenario():
        await _start(plugin)
        child = _UnkillableChild()
        plugin._child = child
        plugin._settings = controller_module.validate_settings({"enabled": True})
        return child, await plugin.update_options({"enabled": False})

    child, state = asyncio.run(asyncio.wait_for(scenario(), 1.0))

    assert child.terminated is True
    assert child.killed is True
    # The only reaper stays reachable for a later retry.
    assert plugin._child is child
    assert state["status"]["state"] == "unavailable"
    assert "未退出" in state["status"]["detail"]


class _StuckCloseStdin(_FakeStdin):
    """Fake stdin whose close handshake never finishes."""

    def __init__(self):
        super().__init__()
        self.close_entered = asyncio.Event()

    async def wait_closed(self):
        self.close_entered.set()
        await asyncio.Event().wait()


def test_stuck_close_still_releases_the_child(plugin, monkeypatch):
    _patch_child_timeouts(monkeypatch)

    async def stopped():
        await _start(plugin)
        child = _FakeChild(returncode=0)
        child.stdin = _StuckCloseStdin()
        plugin._child = child
        await plugin._stop_child()
        return child

    stopped_child = asyncio.run(asyncio.wait_for(stopped(), 1.0))

    assert stopped_child.stdin.close_entered.is_set()
    assert stopped_child.stdin.closed is True
    assert plugin._child is None

    async def disposed():
        child = _FakeChild(returncode=0)
        child.stdin = _StuckCloseStdin()
        plugin._child = child
        await plugin._dispose_child()
        return child

    disposed_child = asyncio.run(asyncio.wait_for(disposed(), 1.0))

    assert disposed_child.stdin.close_entered.is_set()
    assert disposed_child.stdin.closed is True
    assert plugin._child is None


def test_startup_pushes_persisted_enabled_settings_exactly_once(
    plugin, tmp_path, monkeypatch
):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    layout = list(reversed(default_layout()))
    persisted = controller_module.validate_settings(
        _settings_payload(enabled=True, opacity=0.7, layout=layout)
    )
    (settings_dir / "touch-controller.json").write_text(
        json.dumps(persisted), encoding="utf-8"
    )

    child = _FakeChild(returncode=None)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return child

    monkeypatch.setattr(
        controller_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    # The shared fixture stubs _start_child out; restore the real lifecycle
    # method so startup starts and configures a fake process.
    plugin.__dict__.pop("_start_child", None)

    asyncio.run(_start(plugin))

    lines = child.stdin.data.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "type": "settings",
        "settings": persisted,
    }
    assert persisted["enabled"] is True
    assert persisted["layout"] == controller_module.validate_settings(
        {"layout": layout}
    )["layout"]


def test_parse_status_preserves_optional_fields():
    status = controller_module.TouchController._parse_status(
        {
            "state": "ready",
            "detail": "ok",
            "capturing": True,
            "source_path": "/dev/input/event3",
            "composite_path": "/org/shadowblip/InputPlumber/devices/1",
            "device": "/dev/input/event7",
            "rotation": 90,
        }
    )

    assert status == {
        "state": "ready",
        "detail": "ok",
        "capturing": True,
        "controls_visible": True,
        "source_path": "/dev/input/event3",
        "composite_path": "/org/shadowblip/InputPlumber/devices/1",
        "device": "/dev/input/event7",
        "rotation": 90,
    }


def test_parse_status_drops_invalid_optional_fields():
    status = controller_module.TouchController._parse_status(
        {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "source_path": 5,
            "device": None,
            "rotation": True,
            "haptics_available": 1,
            "haptics_detail": 5,
            "controls_visible": 1,
        }
    )

    assert status == {
        "state": "ready",
        "detail": "ok",
        "capturing": False,
        "controls_visible": True,
    }


def test_parse_status_preserves_haptics_fields():
    status = controller_module.TouchController._parse_status(
        {
            "state": "ready",
            "detail": "ok",
            "capturing": False,
            "haptics_available": False,
            "haptics_detail": "机身振动暂不可用",
        }
    )

    assert status["haptics_available"] is False
    assert status["haptics_detail"] == "机身振动暂不可用"


def test_child_environment_drops_inherited_pythonhome(plugin, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/decky/embedded-python")

    env = plugin._child_environment()

    assert "PYTHONHOME" not in env
    assert env["PYTHONPATH"] == plugin._plugin_root


def test_child_environment_drops_bundle_library_path_without_original(
    plugin, monkeypatch
):
    bundle_path = "/run/decky-loader/_MEI123456"
    monkeypatch.setenv("LD_LIBRARY_PATH", bundle_path)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    env = plugin._child_environment()

    assert "LD_LIBRARY_PATH" not in env
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert os.environ["LD_LIBRARY_PATH"] == bundle_path
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ


def test_child_environment_restores_original_library_path(plugin, monkeypatch):
    original = "/usr/lib:/usr/local/lib"
    monkeypatch.setenv("LD_LIBRARY_PATH", "/run/decky-loader/_MEI123456")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)

    env = plugin._child_environment()

    assert env["LD_LIBRARY_PATH"] == original
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert os.environ["LD_LIBRARY_PATH"] == "/run/decky-loader/_MEI123456"
    assert os.environ["LD_LIBRARY_PATH_ORIG"] == original


def test_child_environment_treats_empty_original_as_absent(plugin, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/run/decky-loader/_MEI123456")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")

    env = plugin._child_environment()

    assert "LD_LIBRARY_PATH" not in env
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert os.environ["LD_LIBRARY_PATH"] == "/run/decky-loader/_MEI123456"
    assert os.environ["LD_LIBRARY_PATH_ORIG"] == ""


def test_child_environment_does_not_mutate_parent_environment(plugin, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/decky/embedded-python")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/run/decky-loader/_MEI123456")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    parent_before = dict(os.environ)

    env = plugin._child_environment()

    assert dict(os.environ) == parent_before
    assert env is not os.environ
    assert env["PYTHONPATH"] == plugin._plugin_root
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_disabled_startup_never_starts_a_child(plugin, tmp_path, monkeypatch):
    started = []

    async def refuse(*args, **kwargs):
        started.append(args)
        raise AssertionError("a disabled controller must not start a daemon")

    monkeypatch.setattr(controller_module.asyncio, "create_subprocess_exec", refuse)
    plugin.__dict__.pop("_start_child", None)

    state = asyncio.run(_start(plugin, edge_swipes=True))

    assert started == []
    assert plugin._child is None
    assert state["settings"]["enabled"] is False
    assert state["status"]["state"] == "disabled"
    assert not (tmp_path / "settings" / "touch-controller.json").exists()


def test_invalid_config_never_starts_a_child(plugin, tmp_path, monkeypatch):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    path = settings_dir / "touch-controller.json"
    original = "{not valid json"
    path.write_text(original, encoding="utf-8")

    started = []

    async def refuse(*args, **kwargs):
        started.append(args)
        raise AssertionError("an invalid configuration must not start a daemon")

    monkeypatch.setattr(controller_module.asyncio, "create_subprocess_exec", refuse)
    plugin.__dict__.pop("_start_child", None)

    state = asyncio.run(_start(plugin, edge_swipes=True))

    assert started == []
    assert plugin._child is None
    assert state["status"]["state"] == "error"
    assert path.read_text(encoding="utf-8") == original


def test_explicit_disable_stops_the_owned_child(plugin):
    async def scenario():
        await _start(plugin, edge_swipes=True)
        child = _FakeChild(returncode=None)
        plugin._child = child
        state = await plugin.update_options({"enabled": False})
        return child, state

    child, state = asyncio.run(scenario())

    assert json.loads(child.stdin.data.decode("utf-8")) == {"type": "stop"}
    assert child.stdin.closed is True
    assert plugin._child is None
    assert state["settings"]["enabled"] is False
    assert state["status"]["state"] == "disabled"


def test_sync_swipe_policy_applies_and_pushes_once(plugin):
    child = _FakeChild(returncode=None)

    async def scenario():
        await _start(plugin)
        plugin._child = child
        first = await plugin.sync_swipe_policy(True, True)
        return first, await plugin.sync_swipe_policy(True, True)

    first, second = asyncio.run(scenario())

    assert first["settings"]["edge_swipes"] is True
    assert second["settings"]["edge_swipes"] is True
    lines = child.stdin.data.decode("utf-8").splitlines()
    assert len(lines) == 1
    message = json.loads(lines[0])
    assert message["type"] == "settings"
    assert message["settings"]["edge_swipes"] is True


def test_saved_edge_swipes_follow_the_canonical_policy(plugin, tmp_path):
    layout = default_layout()

    async def scenario():
        await _start(plugin, edge_swipes=True)
        await plugin.set_settings(_settings_payload(edge_swipes=False))
        saved = await plugin.save_preset("策略", layout, 1.0)
        patched = await plugin.update_options(
            {"edge_swipes": False, "opacity": 0.9}
        )
        off = await plugin.sync_swipe_policy(True, False)
        return saved, patched, off, await plugin.update_options({"opacity": 0.85})

    saved, patched, off, after = asyncio.run(scenario())

    # The persisted value never wins over the Controller's switches.
    assert saved["settings"]["edge_swipes"] is True
    assert patched["settings"]["edge_swipes"] is True
    assert off["settings"]["edge_swipes"] is False
    assert after["settings"]["edge_swipes"] is False

    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted["edge_swipes"] is False
    assert after["settings"]["opacity"] == 0.85


# ---------------------------------------------------------------------------
# Unexpected child exit: bounded automatic restart
# ---------------------------------------------------------------------------


class _WaitableChild(_FakeChild):
    """Fake child whose exit is driven by a real asyncio wait event."""

    def __init__(self):
        super().__init__(returncode=None)
        self.exit_event = asyncio.Event()

    async def wait(self):
        await self.exit_event.wait()
        return self.returncode

    def crash(self, returncode=7):
        self.returncode = returncode
        self.exit_event.set()


async def _until(predicate, timeout=2.0):
    """Await a real event, never a guessed sleep, with a bounded deadline."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.005)


def _install_children(plugin, monkeypatch, count):
    """Spawn fake children in order and return (children, started)."""

    children = [_WaitableChild() for _ in range(count)]
    started = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        if len(started) >= len(children):
            raise AssertionError("the controller spawned more children than planned")
        child = children[len(started)]
        started.append(child)
        return child

    monkeypatch.setattr(
        controller_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    # The shared fixture stubs _start_child out; restore the real lifecycle.
    plugin.__dict__.pop("_start_child", None)
    return children, started


def _messages(child):
    return [
        json.loads(line) for line in child.stdin.data.decode("utf-8").splitlines()
    ]


class _StopAwareStdin(_FakeStdin):
    """Fake stdin: the parent's stop command ends the child with code 0."""

    def __init__(self, child):
        super().__init__()
        self._child = child

    def write(self, data):
        super().write(data)
        if b'"stop"' in data:
            self._child.crash(0)

    async def drain(self):
        # Yield once so the exit report really runs while the parent's stop is
        # latched: that latch, not the exit code, must suppress recovery.
        await asyncio.sleep(0)


def test_unexpected_exit_restarts_and_resends_settings_once(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        assert plugin._child is children[0]
        children[0].crash(7)
        await _until(
            lambda: len(started) == 2
            and plugin._child is children[1]
            and plugin._restart_task is None
        )
        return plugin._snapshot()

    state = asyncio.run(scenario())

    # The exit code is reported while the restart is pending, then the fresh
    # process gets exactly the persisted settings once.
    assert len(started) == 2
    assert plugin._restart_attempts == 1
    assert state["status"]["state"] == "starting"
    expected = json.loads(json.dumps(plugin._settings))
    for child in started:
        assert _messages(child) == [{"type": "settings", "settings": expected}]


def test_disable_cancels_a_pending_restart(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.3,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        children[0].crash(7)
        await _until(lambda: plugin._restart_task is not None)
        state = await plugin.update_options({"enabled": False})
        # Long past the backoff: the cancelled restart must not fire.
        await asyncio.sleep(0.4)
        return state

    state = asyncio.run(scenario())

    assert len(started) == 1
    assert plugin._child is None
    assert plugin._restart_task is None
    assert state["status"]["state"] == "disabled"


def test_shutdown_cancels_a_pending_restart(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.3,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        children[0].crash(7)
        await _until(lambda: plugin._restart_task is not None)
        await plugin.shutdown()
        await asyncio.sleep(0.4)

    asyncio.run(scenario())

    assert len(started) == 1
    assert plugin._child is None
    assert plugin._restart_task is None
    assert plugin._shutdown is True


def test_a_replaced_child_reporting_late_is_ignored(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        old = children[0]
        old.crash(7)
        await _until(
            lambda: len(started) == 2
            and plugin._child is children[1]
            and plugin._restart_task is None
        )
        plugin._status = {
            "state": "ready",
            "detail": "ok",
            "capturing": True,
            "controls_visible": True,
        }
        # The replaced child's own wait event fires late; the parent must not
        # report it or schedule a restart for the current instance.
        await plugin._wait_child(old)
        await asyncio.sleep(0.05)
        return plugin._snapshot(), plugin._restart_task

    state, restart = asyncio.run(scenario())

    assert len(started) == 2
    assert restart is None
    assert state["status"]["state"] == "ready"
    assert plugin._child is started[1]


def test_repeated_quick_exits_stop_at_the_restart_budget(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 6)
    monkeypatch.setattr(
        controller_module, "RESTART_BACKOFF_SECONDS", (0.0, 0.0, 0.0)
    )

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        for index in (1, 2, 3):
            started[-1].crash(7)
            await _until(
                lambda: plugin._child is children[index]
                and plugin._restart_task is None
            )
        started[-1].crash(7)
        await _until(lambda: "停止自动重启" in plugin._status["detail"])
        await asyncio.sleep(0.05)
        return plugin._snapshot()

    state = asyncio.run(scenario())

    # The initial start plus exactly three restarts, then the budget is spent.
    assert len(started) == 4
    assert plugin._restart_attempts == 3
    assert plugin._child is children[3]
    assert plugin._child_running() is False
    assert plugin._restart_task is None
    assert state["status"]["state"] == "unavailable"


def test_explicit_enable_after_exhaustion_opens_a_fresh_budget(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 6)
    monkeypatch.setattr(
        controller_module, "RESTART_BACKOFF_SECONDS", (0.0, 0.0, 0.0)
    )

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        for index in (1, 2, 3):
            started[-1].crash(7)
            await _until(
                lambda: plugin._child is children[index]
                and plugin._restart_task is None
            )
        started[-1].crash(7)
        await _until(lambda: "停止自动重启" in plugin._status["detail"])
        return await plugin.update_options({"enabled": True})

    state = asyncio.run(scenario())

    assert len(started) == 5
    assert plugin._restart_attempts == 0
    assert plugin._child is started[-1]
    assert state["status"]["state"] == "starting"
    assert len(_messages(started[-1])) == 1


def test_a_child_that_ran_stably_opens_a_fresh_budget(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        plugin._restart_attempts = 2
        plugin._child_started_at = (
            asyncio.get_running_loop().time()
            - controller_module.STABLE_CHILD_UPTIME_SECONDS
            - 1.0
        )
        children[0].crash(7)
        await _until(lambda: len(started) == 2)
        return plugin._restart_attempts

    assert asyncio.run(scenario()) == 1


def test_restart_spawn_failure_keeps_the_error_and_a_manual_retry_works(
    plugin, monkeypatch
):
    first = _WaitableChild()
    second = _WaitableChild()
    outcomes = [first, OSError("python3 missing"), second]
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        controller_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))
    plugin.__dict__.pop("_start_child", None)

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        first.crash(7)
        await _until(lambda: len(calls) == 2 and plugin._restart_task is None)
        await asyncio.sleep(0.05)
        failed = plugin._snapshot()
        recovered = await plugin.update_options({"enabled": True})
        return failed, recovered

    failed, recovered = asyncio.run(scenario())

    # The failed spawn is reported, not hidden, and no background loop keeps
    # hammering: only an explicit enable retries.
    assert len(calls) == 3
    assert failed["status"]["state"] == "error"
    assert "无法启动触摸控制器" in failed["status"]["detail"]
    assert plugin._child is second
    assert recovered["status"]["state"] == "starting"
    assert len(_messages(second)) == 1


# ---------------------------------------------------------------------------
# Lifecycle gaps: an exit during the replacement's start, the late enable after
# shutdown, and a zero exit that the parent did not ask for
# ---------------------------------------------------------------------------


def test_replacement_exiting_during_its_start_is_still_retried(
    plugin, monkeypatch
):
    children, started = _install_children(plugin, monkeypatch, 3)
    monkeypatch.setattr(
        controller_module, "RESTART_BACKOFF_SECONDS", (0.0, 0.0, 0.0)
    )
    replacement = children[1]
    # The replacement dies before _start_child has finished the initial
    # settings write, i.e. while the restart still owns _restart_task.
    replacement.stdin = _GatedStdin()

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        children[0].crash(7)
        await asyncio.wait_for(replacement.stdin.entered.wait(), 1.0)

        # Crash the replacement while its drain is still blocked: the test
        # must not wait for the restart task to finish and crash it afterwards,
        # which would hide the race instead of reproducing it.
        replacement.crash(7)
        # The exit report runs while the restart task is in flight, which is
        # exactly the window in which it used to be dropped.
        await _until(lambda: plugin._status["state"] == "unavailable")
        assert plugin._restart_task is not None
        assert not plugin._restart_task.done()

        replacement.stdin.release.set()
        await _until(
            lambda: plugin._child is children[2]
            and plugin._restart_task is None
        )
        return plugin._restart_attempts, plugin._snapshot()

    attempts, state = asyncio.run(scenario())

    # Initial start, the restart that lost its child, and the retry that
    # followed it: the exit was neither dropped nor started twice.
    assert len(started) == 3
    assert attempts == 2
    assert state["status"]["state"] == "starting"
    expected = json.loads(json.dumps(plugin._settings))
    for child in started:
        assert _messages(child) == [{"type": "settings", "settings": expected}]


def test_late_enable_after_shutdown_never_starts_a_child(
    plugin, monkeypatch, tmp_path
):
    children, started = _install_children(plugin, monkeypatch, 2)
    children[0].stdin = _StopAwareStdin(children[0])

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        await plugin.shutdown()
        state = await plugin.update_options({"enabled": True})
        await asyncio.sleep(0.05)
        return state, await plugin.get_state()

    state, final = asyncio.run(scenario())

    assert len(started) == 1
    assert plugin._child is None
    assert plugin._restart_task is None
    assert plugin._shutdown is True
    # The settings are still saved for the next startup, but this instance
    # never claims that something is running.
    assert state["settings"]["enabled"] is True
    assert final["settings"]["enabled"] is True
    assert final["status"]["state"] not in ("starting", "ready")
    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        persisted = json.load(handle)
    assert persisted["enabled"] is True


def test_an_unexpected_zero_exit_is_restarted(plugin, monkeypatch):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        children[0].crash(0)
        await _until(
            lambda: len(started) == 2
            and plugin._child is children[1]
            and plugin._restart_task is None
        )
        return plugin._snapshot()

    state = asyncio.run(scenario())

    # A clean exit status is not evidence that the parent asked for the stop:
    # it must produce exactly one live replacement, which receives the
    # persisted settings once, while the controller reports the fresh start.
    # The old child's exit text is transient and already replaced by the
    # replacement's own start status, so the behaviour is asserted instead.
    assert len(started) == 2
    assert started == children
    assert plugin._child is children[1]
    assert plugin._child_running() is True
    assert plugin._restart_attempts == 1
    assert plugin._restart_task is None
    assert state["status"]["state"] == "starting"
    assert state["status"]["capturing"] is False
    assert _messages(children[1]) == [
        {
            "type": "settings",
            "settings": json.loads(json.dumps(plugin._settings)),
        }
    ]


def test_parent_requested_stop_with_zero_exit_is_not_recovered(
    plugin, monkeypatch
):
    children, started = _install_children(plugin, monkeypatch, 2)
    monkeypatch.setattr(controller_module, "RESTART_BACKOFF_SECONDS", (0.0,))
    children[0].stdin = _StopAwareStdin(children[0])

    async def scenario():
        await _start(plugin)
        await plugin.update_options({"enabled": True})
        state = await plugin.update_options({"enabled": False})
        await asyncio.sleep(0.05)
        return state, await plugin.get_state()

    disabled, final = asyncio.run(scenario())

    # The parent's own stop is the authority, so the same zero exit that is
    # recovered above must not bring a child back here.
    assert len(started) == 1
    assert plugin._child is None
    assert plugin._restart_task is None
    assert plugin._restart_handled_child is None
    assert disabled["status"]["state"] == "disabled"
    assert final["status"] == disabled["status"]
    assert "退出" not in final["status"]["detail"]


# ---------------------------------------------------------------------------
# Persistence off the event loop
# ---------------------------------------------------------------------------

def test_blocking_persist_keeps_the_event_loop_responsive(plugin, monkeypatch):
    real_persist = plugin._persist_settings
    entered = threading.Event()
    release = threading.Event()

    def gated(settings):
        entered.set()
        if not release.wait(5.0):
            raise AssertionError("the persist gate was never released")
        real_persist(settings)

    monkeypatch.setattr(plugin, "_persist_settings", gated)

    async def scenario():
        await _start(plugin)
        save = asyncio.create_task(plugin.update_options({"opacity": 0.6}))
        await _until(entered.is_set)
        assert not save.done()

        # The fsync is blocked in a worker thread; timers and reads still run.
        fired = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, fired.set)
        await asyncio.wait_for(fired.wait(), 1.0)
        pending = await asyncio.wait_for(plugin.get_state(), 1.0)
        assert not save.done()
        assert pending["settings"]["opacity"] != 0.6

        release.set()
        return await asyncio.wait_for(save, 5.0)

    state = asyncio.run(scenario())

    assert state["settings"]["opacity"] == 0.6


def test_two_saves_persist_in_order(plugin, tmp_path, monkeypatch):
    real_persist = plugin._persist_settings
    first_entered = threading.Event()
    release_first = threading.Event()
    persisted = []

    def gated(settings):
        persisted.append(settings["opacity"])
        if len(persisted) == 1:
            first_entered.set()
            if not release_first.wait(5.0):
                raise AssertionError("the persist gate was never released")
        real_persist(settings)

    monkeypatch.setattr(plugin, "_persist_settings", gated)

    async def scenario():
        await _start(plugin)
        first = asyncio.create_task(plugin.update_options({"opacity": 0.55}))
        await _until(first_entered.is_set)
        second = asyncio.create_task(plugin.update_options({"opacity": 0.65}))
        await asyncio.sleep(0.05)
        # The second save waits for the first one's fsync to finish.
        assert persisted == [0.55]
        assert not second.done()
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5.0)
        return await plugin.get_state()

    state = asyncio.run(scenario())

    assert persisted == [0.55, 0.65]
    assert state["settings"]["opacity"] == 0.65
    with (tmp_path / "settings" / "touch-controller.json").open(
        "r", encoding="utf-8"
    ) as handle:
        stored = json.load(handle)
    assert stored["opacity"] == 0.65
