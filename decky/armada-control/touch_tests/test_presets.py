"""Tests for the built-in preset catalog and v2 settings validation."""

import copy
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import layout  # noqa: E402
from py_modules.freedom_touch.presets import (  # noqa: E402
    BUILTIN_PRESET_IDS,
    BUILTIN_PRESET_NAMES,
    DEFAULT_PRESET_ID,
    builtin_presets,
    find_preset,
    preset_catalog,
)
from py_modules.freedom_touch.runtime_policy import validate_settings  # noqa: E402


def custom_preset(
    preset_id="custom-one",
    name="我的布局",
    layout_copy=None,
    scale=1.0,
    dpad_mode="four_way",
    base_preset_id="tablet_standard",
):
    return {
        "id": preset_id,
        "name": name,
        "layout": copy.deepcopy(layout_copy or layout.default_layout()),
        "scale": scale,
        "dpad_mode": dpad_mode,
        "base_preset_id": base_preset_id,
    }


def test_catalog_has_three_ordered_builtin_presets():
    presets = builtin_presets()
    assert [preset["id"] for preset in presets] == list(BUILTIN_PRESET_IDS)
    assert [preset["name"] for preset in presets] == list(BUILTIN_PRESET_NAMES)
    assert [preset["id"] for preset in presets] == [
        "tablet_standard",
        "ps",
        "switch",
    ]
    assert all(preset["builtin"] is True for preset in presets)
    assert all(len(preset["layout"]) == 19 for preset in presets)
    assert all(preset["dpad_mode"] == "four_way" for preset in presets)
    assert [preset["base_preset_id"] for preset in presets] == [
        "tablet_standard",
        "ps",
        "switch",
    ]


def test_catalog_entries_are_fresh_independent_copies():
    first = builtin_presets()
    first[0]["layout"][0]["x"] = 0.999
    first[0]["name"] = "mutated"

    second = builtin_presets()
    assert second[0]["layout"][0]["x"] == pytest.approx(0.052)
    assert second[0]["name"] == "平板默认（Xbox）"

    default = layout.default_layout()
    default[0]["x"] = 0.777
    assert layout.default_layout()[0]["x"] == pytest.approx(0.052)


def test_preset_catalog_appends_custom_copies_and_find_returns_fresh_copy():
    custom = custom_preset()
    catalog = preset_catalog([custom])
    assert [preset["id"] for preset in catalog] == list(BUILTIN_PRESET_IDS) + [
        "custom-one"
    ]
    assert catalog[-1]["builtin"] is False

    found = find_preset("custom-one", [custom])
    assert found["name"] == "我的布局"
    found["layout"][0]["x"] = 0.5
    assert custom["layout"][0]["x"] == pytest.approx(0.052)
    assert find_preset("missing", [custom]) is None


def test_validate_v2_accepts_custom_presets_and_trims_names():
    raw = {
        "version": 2,
        "active_preset_id": "custom-one",
        "custom_presets": [custom_preset(name="  我的布局  ")],
        "layout": layout.default_layout(),
        "scale": 1.0,
    }
    settings = validate_settings(raw)
    assert settings["active_preset_id"] == "custom-one"
    assert settings["custom_presets"][0]["name"] == "我的布局"
    assert settings["custom_presets"][0]["id"] == "custom-one"
    assert settings["version"] == 2


@pytest.mark.parametrize(
    "preset",
    [
        custom_preset(preset_id="tablet_standard"),
        custom_preset(preset_id=""),
        custom_preset(preset_id="   "),
        custom_preset(preset_id=5),
        custom_preset(name=""),
        custom_preset(name="   "),
        custom_preset(name="x" * 49),
        custom_preset(name=5),
    ],
)
def test_validate_v2_rejects_invalid_custom_id_or_name(preset):
    raw = {"version": 2, "custom_presets": [preset]}
    with pytest.raises(ValueError):
        validate_settings(raw)


@pytest.mark.parametrize("name", ["平板默认（Xbox）", "PS", "Switch"])
def test_validate_v2_loads_custom_named_like_a_builtin(name):
    # Before the built-in catalog changed, a user could legitimately save a
    # custom preset named "PS" or "Switch"; those files must keep loading.
    old = custom_preset(
        preset_id="custom-old",
        name=name,
        scale=1.1,
        dpad_mode="four_way",
        base_preset_id="tablet_standard",
    )
    raw = {
        "version": 2,
        "active_preset_id": "custom-old",
        "custom_presets": [old],
        "layout": copy.deepcopy(layout.default_layout()),
        "scale": 1.1,
    }

    first = validate_settings(raw)
    second = validate_settings(copy.deepcopy(first))

    assert second == first
    assert first["active_preset_id"] == "custom-old"
    presets = first["custom_presets"]
    assert len(presets) == 1
    preset = presets[0]
    assert preset["id"] == "custom-old"
    assert preset["name"] == name
    assert preset["scale"] == 1.1
    assert preset["layout"] == layout.default_layout()
    assert preset["dpad_mode"] == "four_way"
    assert preset["base_preset_id"] == "tablet_standard"


def test_validate_v2_rejects_duplicate_custom_ids_and_names():
    first = custom_preset("one", "A")
    second = custom_preset("two", "B")
    second["id"] = "one"
    with pytest.raises(ValueError):
        validate_settings({"version": 2, "custom_presets": [first, second]})

    second = custom_preset("two", "A")
    with pytest.raises(ValueError):
        validate_settings({"version": 2, "custom_presets": [first, second]})


def test_validate_v2_rejects_invalid_custom_layout_and_scale():
    bad_layout = custom_preset()
    bad_layout["layout"] = [{"id": "broken"}]
    with pytest.raises(ValueError):
        validate_settings({"version": 2, "custom_presets": [bad_layout]})

    for scale in (0.59, 1.41, float("nan"), "1.0", True):
        bad_scale = custom_preset(scale=scale)
        with pytest.raises(ValueError):
            validate_settings({"version": 2, "custom_presets": [bad_scale]})


def test_validate_v2_active_custom_preset_must_exist():
    raw = {
        "version": 2,
        "active_preset_id": "custom-one",
        "custom_presets": [custom_preset()],
        "layout": layout.default_layout(),
        "scale": 1.0,
    }
    assert validate_settings(raw)["active_preset_id"] == "custom-one"

    raw["active_preset_id"] = "unknown"
    with pytest.raises(ValueError):
        validate_settings(raw)


def test_validate_v2_round_trips_old_custom_ps_and_switch_names():
    # An old v2 document written before the catalog contained PS and Switch:
    # such a custom preset has no dpad_mode/base_preset_id yet, but its name is
    # not an error and must survive repeated validation byte-for-byte.
    old_ps = {
        "id": "custom-ps",
        "name": "PS",
        "layout": copy.deepcopy(layout.default_layout()),
        "scale": 0.95,
    }
    old_switch = {
        "id": "custom-switch",
        "name": "Switch",
        "layout": copy.deepcopy(layout.default_layout()),
        "scale": 1.1,
    }
    raw = {
        "version": 2,
        "active_preset_id": "custom-ps",
        "custom_presets": [old_ps, old_switch],
        "layout": copy.deepcopy(layout.default_layout()),
        "scale": 0.95,
    }

    first = validate_settings(raw)
    second = validate_settings(copy.deepcopy(first))

    assert second == first
    names = {p["name"]: p for p in first["custom_presets"]}
    assert set(names) == {"PS", "Switch"}
    for preset in names.values():
        assert preset["dpad_mode"] == "four_way"
        assert preset["base_preset_id"] == "tablet_standard"
    assert first["active_preset_id"] == "custom-ps"
    assert first["scale"] == 0.95


def test_active_builtin_layout_is_used_when_layout_is_absent():
    settings = validate_settings({"version": 2, "active_preset_id": "ps"})
    assert settings["active_preset_id"] == "ps"
    assert settings["layout"] == layout.validate_layout(layout.ps_layout())
    assert settings["scale"] == 1.0
    assert settings["dpad_mode"] == "four_way"
