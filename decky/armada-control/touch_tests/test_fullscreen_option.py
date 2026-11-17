import copy

import pytest

from py_modules.freedom_touch import runtime_policy
from py_modules.freedom_touch.runtime_policy import validate_settings


def _base_settings():
    # A minimal, valid v2 document produced through the public API itself:
    # start from a legacy document that exercises every field we care about,
    # migrate it, then round-trip once more so we hold canonical settings.
    legacy = {
        "version": 1,
        "enabled": True,
        "opacity": 0.6,
        "scale": 1.1,
        "deadzone": 0.2,
        "dpad_mode": "eight_way",
        "button_color": "black",
        "edge_swipes": True,
        "haptics_enabled": False,
        "haptics_strength": 40,
        "layout": [
            {
                "id": "btn_a",
                "kind": "button",
                "output": "a",
                "x": 0.1,
                "y": 0.1,
                "w": 0.1,
                "h": 0.1,
            }
        ],
    }
    migrated = validate_settings(legacy)
    return validate_settings(migrated)


def test_default_false_when_missing():
    settings = _base_settings()
    assert "right_stick_fullscreen" in settings
    assert settings["right_stick_fullscreen"] is False


def test_explicit_true_roundtrip():
    settings = _base_settings()
    settings["right_stick_fullscreen"] = True
    canonical = validate_settings(settings)
    assert canonical["right_stick_fullscreen"] is True
    again = validate_settings(canonical)
    assert again["right_stick_fullscreen"] is True


def test_explicit_false_roundtrip():
    settings = _base_settings()
    settings["right_stick_fullscreen"] = False
    canonical = validate_settings(settings)
    assert canonical["right_stick_fullscreen"] is False
    again = validate_settings(canonical)
    assert again["right_stick_fullscreen"] is False


@pytest.mark.parametrize("bad", [1, 0, "true", "false", None, [], {}])
def test_non_bool_rejected(bad):
    settings = _base_settings()
    settings["right_stick_fullscreen"] = bad
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_unrelated_fields_preserved_across_mode_changes():
    settings = _base_settings()
    baseline = copy.deepcopy(settings)

    settings["right_stick_fullscreen"] = True
    canonical = validate_settings(settings)

    for key in (
        "enabled",
        "opacity",
        "scale",
        "deadzone",
        "dpad_mode",
        "button_color",
        "edge_swipes",
        "haptics_enabled",
        "haptics_strength",
        "active_preset_id",
        "layout",
        "custom_presets",
        "version",
    ):
        assert canonical[key] == baseline[key], key

    # And the layout survives a second round-trip via the canonical object.
    canonical["right_stick_fullscreen"] = False
    back = validate_settings(canonical)
    assert back["layout"] == baseline["layout"]
    assert back["custom_presets"] == baseline["custom_presets"]
    assert back["right_stick_fullscreen"] is False


def test_legacy_missing_defaults_false():
    legacy = {
        "version": 1,
        "enabled": True,
        "opacity": 0.5,
        "scale": 1.0,
        "deadzone": 0.1,
        "dpad_mode": "four_way",
        "button_color": "white",
    }
    canonical = validate_settings(legacy)
    assert canonical["right_stick_fullscreen"] is False
    # Migrated document still validates as v2.
    assert validate_settings(canonical)["right_stick_fullscreen"] is False


def test_legacy_explicit_true_survives_migration():
    legacy = {
        "version": 1,
        "enabled": True,
        "opacity": 0.5,
        "scale": 1.0,
        "deadzone": 0.1,
        "dpad_mode": "four_way",
        "right_stick_fullscreen": True,
    }
    canonical = validate_settings(legacy)
    assert canonical["right_stick_fullscreen"] is True
