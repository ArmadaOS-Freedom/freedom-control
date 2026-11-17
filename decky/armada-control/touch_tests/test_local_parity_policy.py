"""Tests for local Touch 0.1.6 parity in the Freedom touch controller.

Covers console catalog labels/face outputs, catalog copy independence, custom
preset dpad_mode/base_preset_id persistence (incl. legacy geometry), retired
selection retention, white/black button_color validation, and four-way vs
eight-way D-pad behaviour with deadzone.

These tests target public pure-module APIs only (presets, layout, runtime_policy,
engine). Missing functionality is expected to surface as failures against the
current baseline; they intentionally avoid source-text assertions, mocks, and
non-existent imports at module import time.
"""

import copy
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import layout  # noqa: E402
from py_modules.freedom_touch import engine  # noqa: E402
from py_modules.freedom_touch import presets  # noqa: E402
from py_modules.freedom_touch import runtime_policy  # noqa: E402


# ---------------------------------------------------------------------------
# Console catalog: exact ids, labels, and physical face output contract.
# ---------------------------------------------------------------------------


def _face_by_id(layout_copy):
    return {c["id"]: c for c in layout_copy}


def test_builtin_catalog_has_exact_console_ids():
    builtins = presets.builtin_presets()
    assert [p["id"] for p in builtins] == [
        "tablet_standard",
        "ps",
        "switch",
    ]
    assert all(p["builtin"] is True for p in builtins)


def test_builtin_catalog_omits_retired_presets():
    ids = {p["id"] for p in presets.builtin_presets()}
    for retired in getattr(presets, "RETIRED_PRESET_IDS", (
        "tablet_compact",
        "tablet_trackpads",
    )):
        assert retired not in ids


def test_console_layouts_preserve_face_label_and_slot_contract():
    # Physical positions: top/left/right/bottom carry y/x/b/a labels per
    # family, while the emitted output slot stays y/x/b/a.
    expectations = {
        "tablet_standard": {"top": "Y", "left": "X", "right": "B", "bottom": "A"},
        "ps": {"top": "\u25b3", "left": "\u25a1", "right": "\u25cb", "bottom": "\u00d7"},
        "switch": {"top": "X", "left": "Y", "right": "A", "bottom": "B"},
    }
    for preset in presets.builtin_presets():
        expected = expectations[preset["id"]]
        faces = _face_by_id(preset["layout"])
        assert faces["btn_y"]["label"] == expected["top"]
        assert faces["btn_x"]["label"] == expected["left"]
        assert faces["btn_b"]["label"] == expected["right"]
        assert faces["btn_a"]["label"] == expected["bottom"]
        # Output slot codes must remain the semantic y/x/b/a contract.
        assert faces["btn_y"]["output"] == "y"
        assert faces["btn_x"]["output"] == "x"
        assert faces["btn_b"]["output"] == "b"
        assert faces["btn_a"]["output"] == "a"


def test_console_shoulder_labels_match_family():
    shoulder_labels = {
        "tablet_standard": ("LB", "LT", "RT", "RB"),
        "ps": ("L1", "L2", "R2", "R1"),
        "switch": ("L", "ZL", "ZR", "R"),
    }
    for preset in presets.builtin_presets():
        faces = _face_by_id(preset["layout"])
        lb, lt, rt, rb = shoulder_labels[preset["id"]]
        assert faces["btn_lb"]["label"] == lb
        assert faces["btn_lt"]["label"] == lt
        assert faces["btn_rt"]["label"] == rt
        assert faces["btn_rb"]["label"] == rb
        # Shoulder slot codes must not change across families.
        assert faces["btn_lb"]["output"] == "lb"
        assert faces["btn_lt"]["output"] == "lt"
        assert faces["btn_rt"]["output"] == "rt"
        assert faces["btn_rb"]["output"] == "rb"


def test_builtin_and_default_layouts_are_independent_copies():
    first = presets.builtin_presets()
    first[0]["layout"][0]["x"] = 0.999
    first[1]["name"] = "mutated"

    second = presets.builtin_presets()
    assert second[0]["layout"][0]["x"] != 0.999
    assert second[1]["name"] != "mutated"

    default = layout.default_layout()
    default[0]["x"] = 0.777
    assert layout.default_layout()[0]["x"] != 0.777


def test_console_layouts_have_independent_layout_objects():
    # Each preset must own a distinct layout list independent from the others.
    builtins = presets.builtin_presets()
    seen_ids = set()
    for preset in builtins:
        obj_id = id(preset["layout"])
        assert obj_id not in seen_ids
        seen_ids.add(obj_id)
    # Mutating one preset's layout must not leak into another preset copy.
    builtins[0]["layout"][0]["x"] = 0.123
    assert builtins[1]["layout"][0]["x"] != 0.123


# ---------------------------------------------------------------------------
# Custom preset metadata persistence: dpad_mode / base_preset_id and legacy
# geometry, plus retired selection retention.
# ---------------------------------------------------------------------------


def _custom(name="我的布局", preset_id="custom-one", **extra):
    base = {
        "id": preset_id,
        "name": name,
        "layout": copy.deepcopy(layout.default_layout()),
        "scale": 1.0,
    }
    base.update(extra)
    return base


def test_custom_preset_round_trips_dpad_mode_and_base_preset_id():
    custom = _custom(dpad_mode="eight_way", base_preset_id="ps")
    raw = {
        "version": runtime_policy.SETTINGS_VERSION,
        "enabled": False,
        "opacity": runtime_policy.DEFAULT_OPACITY,
        "scale": 1.0,
        "deadzone": runtime_policy.DEFAULT_DEADZONE,
        "edge_swipes": False,
        "haptics_enabled": False,
        "haptics_strength": 0,
        "dpad_mode": "eight_way",
        "button_color": "white",
        "active_preset_id": runtime_policy.DEFAULT_PRESET_ID,
        "custom_presets": [custom],
    }
    validated = runtime_policy.validate_settings(raw)
    saved = validated["custom_presets"][0]
    assert saved["id"] == "custom-one"
    assert saved["name"] == "我的布局"
    assert saved["dpad_mode"] == "eight_way"
    assert saved["base_preset_id"] == "ps"
    # Geometry survives the round trip unchanged.
    assert saved["layout"] == copy.deepcopy(layout.default_layout())


def test_legacy_custom_without_metadata_defaults_base_and_inherits_mode():
    legacy_custom = _custom()  # no dpad_mode / base_preset_id
    raw = {
        "version": runtime_policy.SETTINGS_VERSION,
        "enabled": False,
        "opacity": runtime_policy.DEFAULT_OPACITY,
        "scale": 1.0,
        "deadzone": runtime_policy.DEFAULT_DEADZONE,
        "edge_swipes": False,
        "haptics_enabled": False,
        "haptics_strength": 0,
        "dpad_mode": "eight_way",
        "button_color": "white",
        "active_preset_id": runtime_policy.DEFAULT_PRESET_ID,
        "custom_presets": [legacy_custom],
    }
    validated = runtime_policy.validate_settings(raw)
    saved = validated["custom_presets"][0]
    # Old custom presets default base to the tablet standard template.
    assert saved["base_preset_id"] == "tablet_standard"
    # Missing mode inherits the document's own top-level dpad_mode.
    assert saved["dpad_mode"] == "eight_way"


def test_retired_selection_retains_saved_geometry_as_legacy_custom():
    # A v2 document that still selects a retired built-in must keep its saved
    # geometry rather than resetting the user's layout.
    saved_layout = copy.deepcopy(layout.default_layout())
    saved_layout[0]["x"] = 0.11
    saved_layout[0]["y"] = 0.22
    raw = {
        "version": runtime_policy.SETTINGS_VERSION,
        "enabled": False,
        "opacity": runtime_policy.DEFAULT_OPACITY,
        "scale": 1.0,
        "deadzone": runtime_policy.DEFAULT_DEADZONE,
        "edge_swipes": False,
        "haptics_enabled": False,
        "haptics_strength": 0,
        "dpad_mode": "four_way",
        "button_color": "white",
        "active_preset_id": "tablet_trackpads",
        "layout": copy.deepcopy(saved_layout),
        "custom_presets": [],
    }
    validated = runtime_policy.validate_settings(raw)
    assert validated["active_preset_id"] == runtime_policy.DEFAULT_PRESET_ID
    # The user's saved geometry is preserved rather than reset.
    assert validated["layout"] == copy.deepcopy(saved_layout)


def test_legacy_v1_migration_keeps_layout_as_custom_preset():
    legacy_layout = copy.deepcopy(layout.default_layout())
    legacy_layout[0]["x"] = 0.10
    raw = {
        "version": runtime_policy.LEGACY_SETTINGS_VERSION,
        "enabled": True,
        "opacity": 0.5,
        "scale": 1.1,
        "deadzone": 0.15,
        "edge_swipes": True,
        "layout": copy.deepcopy(legacy_layout),
    }
    validated = runtime_policy.validate_settings(raw)
    assert validated["version"] == runtime_policy.SETTINGS_VERSION
    assert validated["active_preset_id"] == runtime_policy.DEFAULT_PRESET_ID
    customs = validated["custom_presets"]
    assert len(customs) == 1
    assert customs[0]["id"] == "custom_legacy"
    assert customs[0]["layout"] == copy.deepcopy(legacy_layout)


# ---------------------------------------------------------------------------
# White/black button_color setting validation and default.
# ---------------------------------------------------------------------------


def _base_settings(**overrides):
    raw = {
        "version": runtime_policy.SETTINGS_VERSION,
        "enabled": False,
        "opacity": runtime_policy.DEFAULT_OPACITY,
        "scale": 1.0,
        "deadzone": runtime_policy.DEFAULT_DEADZONE,
        "edge_swipes": False,
        "haptics_enabled": False,
        "haptics_strength": 0,
        "dpad_mode": "four_way",
        "button_color": "white",
        "active_preset_id": runtime_policy.DEFAULT_PRESET_ID,
        "custom_presets": [],
    }
    raw.update(overrides)
    return raw


def test_button_color_defaults_to_white_when_omitted():
    raw = _base_settings()
    raw.pop("button_color")
    validated = runtime_policy.validate_settings(raw)
    assert validated["button_color"] == "white"


@pytest.mark.parametrize("color", ["white", "black"])
def test_button_color_accepts_white_and_black(color):
    validated = runtime_policy.validate_settings(_base_settings(button_color=color))
    assert validated["button_color"] == color


@pytest.mark.parametrize("color", ["red", "", None, 1, True])
def test_button_color_rejects_invalid_values(color):
    with pytest.raises(ValueError):
        runtime_policy.validate_settings(_base_settings(button_color=color))


# ---------------------------------------------------------------------------
# Four-way vs eight-way D-pad with deadzone.
# ---------------------------------------------------------------------------


def _dpad_layout():
    return [
        {
            "id": "dpad_l",
            "kind": "dpad",
            "x": 0.0,
            "y": 0.0,
            "w": 0.3,
            "h": 0.3,
            "output": "left",
            "label": "十字键",
        }
    ]


def _dpad_axes(engine_obj, x, y):
    events = engine_obj.begin(1, x, y, 0.0)
    axes = {e[1]: e[2] for e in events if e[0] == "axis"}
    return axes.get("dx"), axes.get("dy")


def test_four_way_dpad_never_diagonal_at_corner():
    eng = engine.TouchEngine(_dpad_layout(), deadzone=0.1, dpad_mode="four_way")
    eng.set_active(True)
    cx, cy = 0.15, 0.15
    # Upper-right corner of the dpad: both axes large but only one may fire.
    dx, dy = _dpad_axes(eng, cx + 0.14, cy - 0.14)
    assert (dx, dy) in ((1, 0), (-1, 0), (0, 1), (0, -1))


def test_eight_way_dpad_emits_diagonal_at_corner():
    eng = engine.TouchEngine(_dpad_layout(), deadzone=0.1, dpad_mode="eight_way")
    eng.set_active(True)
    cx, cy = 0.15, 0.15
    dx, dy = _dpad_axes(eng, cx + 0.14, cy - 0.14)
    assert dx == 1 and dy == -1


def test_dpad_deadzone_zero_input_near_center():
    for mode in ("four_way", "eight_way"):
        eng = engine.TouchEngine(_dpad_layout(), deadzone=0.1, dpad_mode=mode)
        eng.set_active(True)
        dx, dy = _dpad_axes(eng, 0.15, 0.15)
        assert dx == 0 and dy == 0


def test_four_way_dpad_accepted_by_engine():
    # Constructing without dpad_mode must default to four_way and not raise.
    eng = engine.TouchEngine(_dpad_layout(), deadzone=0.1)
    assert eng.dpad_mode == "four_way"
