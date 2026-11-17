import asyncio
import json

import pytest

from test_plugin_backend import (  # noqa: F401
    controller_module,
    plugin,
    _start,
    _settings_payload,
)
from py_modules.freedom_touch.layout import default_layout  # noqa: E402


def _new_plugin():
    instance = controller_module.TouchController()

    async def no_start():
        instance._child = None
        return True

    instance._start_child = no_start
    return instance


def _saved_path(tmp_path):
    return tmp_path / "settings" / "touch-controller.json"


def test_save_preset_four_arg_compat_defaults_mode_and_base(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        return await plugin.save_preset("旧式", default_layout(), 1.0)

    state = asyncio.run(scenario())
    custom = state["settings"]["custom_presets"][-1]
    assert custom["name"] == "旧式"
    assert custom["dpad_mode"] == "four_way"
    assert custom["base_preset_id"] == "tablet_standard"

    persisted = json.loads(_saved_path(tmp_path).read_text(encoding="utf-8"))
    assert persisted["custom_presets"][-1]["dpad_mode"] == "four_way"
    assert persisted["custom_presets"][-1]["base_preset_id"] == "tablet_standard"


def test_save_preset_optional_mode_and_base_roundtrip_reload(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset(
            "八向",
            default_layout(),
            1.0,
            None,
            "eight_way",
            "ps",
        )
        reloaded = _new_plugin()
        await _start(reloaded)
        return saved, await reloaded.get_state()

    saved, reloaded = asyncio.run(scenario())
    custom = saved["settings"]["custom_presets"][-1]
    assert custom["dpad_mode"] == "eight_way"
    assert custom["base_preset_id"] == "ps"
    assert saved["settings"]["dpad_mode"] == "eight_way"
    assert reloaded["settings"]["custom_presets"] == saved["settings"]["custom_presets"]


def test_select_preset_adopts_mode(plugin):
    async def scenario():
        await _start(plugin)
        saved = await plugin.save_preset(
            "八向", default_layout(), 1.0, None, "eight_way", "tablet_standard"
        )
        preset = saved["settings"]["custom_presets"][-1]
        await plugin.select_preset("tablet_standard")
        assert (await plugin.get_state())["settings"]["dpad_mode"] == "four_way"
        return await plugin.select_preset(preset["id"])

    state = asyncio.run(scenario())
    assert state["settings"]["dpad_mode"] == "eight_way"
    assert state["settings"]["active_preset_id"] == state["settings"]["custom_presets"][-1]["id"]


def test_delete_active_custom_falls_back_and_persists_remaining(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        first = await plugin.save_preset("第一", default_layout(), 1.0)
        first_id = first["settings"]["custom_presets"][-1]["id"]
        second = await plugin.save_preset("第二", default_layout(), 1.0)
        second_id = second["settings"]["custom_presets"][-1]["id"]
        assert second["settings"]["active_preset_id"] == second_id
        deleted = await plugin.delete_preset(second_id)
        reloaded = _new_plugin()
        await _start(reloaded)
        return first_id, deleted, await reloaded.get_state()

    first_id, deleted, reloaded = asyncio.run(scenario())
    assert [p["id"] for p in deleted["settings"]["custom_presets"]] == [first_id]
    assert deleted["settings"]["active_preset_id"] == "tablet_standard"
    assert deleted["settings"]["dpad_mode"] == "four_way"

    persisted = json.loads(_saved_path(tmp_path).read_text(encoding="utf-8"))
    assert [p["id"] for p in persisted["custom_presets"]] == [first_id]
    assert reloaded["settings"]["custom_presets"] == persisted["custom_presets"]
    assert reloaded["settings"]["active_preset_id"] == "tablet_standard"


def test_delete_preset_rejects_builtin_and_unknown(plugin):
    async def scenario():
        await _start(plugin)
        with pytest.raises(ValueError):
            await plugin.delete_preset("tablet_standard")
        with pytest.raises(ValueError):
            await plugin.delete_preset("no-such-preset")
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["settings"]["custom_presets"] == []


def test_update_options_accepts_button_color_and_dpad_mode(plugin):
    async def scenario():
        await _start(plugin)
        await plugin.update_options({"button_color": "black", "dpad_mode": "eight_way"})
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["settings"]["button_color"] == "black"
    assert state["settings"]["dpad_mode"] == "eight_way"


def test_update_options_rejects_invalid_values_atomically(plugin, tmp_path):
    async def scenario():
        await _start(plugin)
        await plugin.update_options({"button_color": "white", "dpad_mode": "four_way"})
        for patch in (
            {"button_color": "purple"},
            {"dpad_mode": "six_way"},
            {"button_color": "black", "dpad_mode": "six_way"},
        ):
            with pytest.raises((ValueError, TypeError)):
                await plugin.update_options(patch)
        return await plugin.get_state()

    state = asyncio.run(scenario())
    assert state["settings"]["button_color"] == "white"
    assert state["settings"]["dpad_mode"] == "four_way"

    persisted = json.loads(_saved_path(tmp_path).read_text(encoding="utf-8"))
    assert persisted["button_color"] == "white"
    assert persisted["dpad_mode"] == "four_way"
