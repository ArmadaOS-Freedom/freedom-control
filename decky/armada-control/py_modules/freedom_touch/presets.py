"""Built-in preset catalog for the Freedom touch controller.

The catalog is defined once in Python; the frontend receives it through the
plugin state and must never keep a second copy of these layouts.  Every getter
returns freshly copied objects so callers cannot mutate the catalog.

The built-in presets are the console families the overlay ships with.  Each
one names itself as its own base, so a saved custom preset can always be
traced back to the template it was edited from.
"""

import copy

from py_modules.freedom_touch.layout import (
    ps_layout,
    switch_layout,
    tablet_standard_layout,
)

DEFAULT_PRESET_ID = "tablet_standard"
LEGACY_PRESET_ID = "custom_legacy"
LEGACY_PRESET_NAME = "原布局"
CUSTOM_NAME_MAX_LENGTH = 48

DPAD_MODES = ("four_way", "eight_way")
DEFAULT_DPAD_MODE = "four_way"

BUILTIN_PRESET_IDS = (
    "tablet_standard",
    "ps",
    "switch",
)

BUILTIN_PRESET_NAMES = (
    "平板默认（Xbox）",
    "PS",
    "Switch",
)

# Earlier releases shipped these two ids.  They are no longer catalog entries,
# but a saved selection of one is migrated to a preserved legacy custom preset
# rather than resetting the user's geometry.
RETIRED_PRESET_IDS = (
    "tablet_compact",
    "tablet_trackpads",
)

_BUILTIN_SPECS = (
    ("tablet_standard", "平板默认（Xbox）", tablet_standard_layout, 1.0),
    ("ps", "PS", ps_layout, 1.0),
    ("switch", "Switch", switch_layout, 1.0),
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
            "dpad_mode": DEFAULT_DPAD_MODE,
            "base_preset_id": preset_id,
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
                "dpad_mode": preset["dpad_mode"],
                "base_preset_id": preset["base_preset_id"],
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
