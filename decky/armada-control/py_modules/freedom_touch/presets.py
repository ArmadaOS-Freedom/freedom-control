"""Built-in preset catalog for the Freedom touch controller.

The catalog is defined once in Python; the frontend receives it through the
plugin state and must never keep a second copy of these layouts.  Every getter
returns freshly copied objects so callers cannot mutate the catalog.
"""

import copy

from py_modules.freedom_touch.layout import (
    tablet_compact_layout,
    tablet_standard_layout,
    tablet_trackpads_layout,
)

DEFAULT_PRESET_ID = "tablet_standard"
LEGACY_PRESET_ID = "custom_legacy"
LEGACY_PRESET_NAME = "原布局"
CUSTOM_NAME_MAX_LENGTH = 48

BUILTIN_PRESET_IDS = (
    "tablet_standard",
    "tablet_compact",
    "tablet_trackpads",
)

BUILTIN_PRESET_NAMES = (
    "平板标准",
    "平板紧凑",
    "触摸板优先",
)

_BUILTIN_SPECS = (
    ("tablet_standard", "平板标准", tablet_standard_layout, 1.0),
    ("tablet_compact", "平板紧凑", tablet_compact_layout, 0.95),
    ("tablet_trackpads", "触摸板优先", tablet_trackpads_layout, 1.0),
)


def builtin_presets():
    """Return fresh copies of every built-in preset, in catalog order."""

    return [
        {
            "id": preset_id,
            "name": name,
            "builtin": True,
            "layout": factory(),
            "scale": scale,
        }
        for preset_id, name, factory, scale in _BUILTIN_SPECS
    ]


def preset_catalog(custom_presets=()):
    """Return built-ins followed by fresh copies of *custom_presets*."""

    catalog = builtin_presets()
    for preset in custom_presets:
        catalog.append(
            {
                "id": preset["id"],
                "name": preset["name"],
                "builtin": False,
                "layout": copy.deepcopy(preset["layout"]),
                "scale": preset["scale"],
            }
        )
    return catalog


def find_preset(preset_id, custom_presets=()):
    """Return a fresh catalog entry for *preset_id*, or None."""

    for preset in preset_catalog(custom_presets):
        if preset["id"] == preset_id:
            return preset
    return None


def builtin_preset_names():
    """Return the immutable tuple of built-in display names."""

    return BUILTIN_PRESET_NAMES
