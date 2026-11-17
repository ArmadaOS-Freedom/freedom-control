"""Regression tests for retired built-in selection conversion.

A saved active selection of a retired built-in must round-trip through
validate_settings without raising, preserving the user's name, geometry,
scale and dpad mode as a legacy custom preset.  The preserved preset must
anchor its base at the canonical tablet default, never at the retired id.
"""

import unittest

from py_modules.freedom_touch import layout as layout_mod
from py_modules.freedom_touch.presets import (
    DEFAULT_DPAD_MODE,
    DEFAULT_PRESET_ID,
    LEGACY_PRESET_ID,
    LEGACY_PRESET_NAME,
    RETIRED_PRESET_IDS,
)
from py_modules.freedom_touch.runtime_policy import validate_settings


_RETIRED_FACTORIES = {
    "tablet_compact": layout_mod.tablet_compact_layout,
    "tablet_trackpads": layout_mod.tablet_trackpads_layout,
}


def _base_raw(active_id, **overrides):
    raw = {
        "version": 2,
        "enabled": True,
        "opacity": 0.5,
        "scale": 1.0,
        "deadzone": 0.1,
        "button_color": "white",
        "edge_swipes": False,
        "haptics_enabled": True,
        "haptics_strength": 25,
        "dpad_mode": DEFAULT_DPAD_MODE,
        "active_preset_id": active_id,
        "custom_presets": [],
    }
    raw.update(overrides)
    return raw


class RetiredPresetRoundtripTest(unittest.TestCase):
    def test_retired_ids_are_exactly_the_two_factories(self):
        self.assertEqual(set(RETIRED_PRESET_IDS), set(_RETIRED_FACTORIES))

    def test_defaults(self):
        self.assertEqual(DEFAULT_PRESET_ID, "tablet_standard")
        self.assertEqual(DEFAULT_DPAD_MODE, "four_way")
        self.assertEqual(LEGACY_PRESET_ID, "custom_legacy")
        self.assertEqual(LEGACY_PRESET_NAME, "原布局")

    def test_each_retired_missing_layout_roundtrips(self):
        for retired_id in RETIRED_PRESET_IDS:
            with self.subTest(retired_id=retired_id):
                raw = _base_raw(retired_id)
                once = validate_settings(raw)
                twice = validate_settings(once)

                self.assertEqual(once, twice)
                self.assertEqual(once["active_preset_id"], DEFAULT_PRESET_ID)
                self.assertEqual(once["version"], 2)

                presets = once["custom_presets"]
                self.assertEqual(len(presets), 1)
                preserved = presets[0]
                self.assertEqual(preserved["id"], LEGACY_PRESET_ID)
                self.assertEqual(preserved["name"], LEGACY_PRESET_NAME)
                self.assertEqual(preserved["base_preset_id"], DEFAULT_PRESET_ID)
                self.assertEqual(preserved["scale"], 1.0)
                self.assertEqual(preserved["dpad_mode"], DEFAULT_DPAD_MODE)
                # Geometry comes from the retired factory, not standard.
                self.assertEqual(
                    preserved["layout"],
                    layout_mod.validate_layout(_RETIRED_FACTORIES[retired_id]()),
                )
                self.assertEqual(once["layout"], preserved["layout"])

    def test_missing_layout_is_not_silently_standard(self):
        factory_layout = layout_mod.validate_layout(
            layout_mod.tablet_compact_layout()
        )
        standard_layout = layout_mod.validate_layout(
            layout_mod.tablet_standard_layout()
        )
        self.assertNotEqual(factory_layout, standard_layout)
        once = validate_settings(_base_raw("tablet_compact"))
        self.assertEqual(once["layout"], factory_layout)

    def test_each_retired_explicit_empty_layout_roundtrips(self):
        for retired_id in RETIRED_PRESET_IDS:
            with self.subTest(retired_id=retired_id):
                raw = _base_raw(retired_id, layout=[])
                once = validate_settings(raw)
                twice = validate_settings(once)

                self.assertEqual(once, twice)
                self.assertEqual(once["layout"], [])
                preserved = once["custom_presets"][0]
                self.assertEqual(preserved["layout"], [])
                self.assertEqual(preserved["base_preset_id"], DEFAULT_PRESET_ID)

    def test_saved_geometry_name_scale_mode_retained(self):
        saved = layout_mod.validate_layout(layout_mod.ps_layout())
        raw = _base_raw(
            "tablet_trackpads",
            layout=saved,
            scale=1.25,
            dpad_mode="eight_way",
        )
        once = validate_settings(raw)
        twice = validate_settings(once)
        self.assertEqual(once, twice)

        self.assertEqual(once["layout"], saved)
        self.assertAlmostEqual(once["scale"], 1.25)
        self.assertEqual(once["dpad_mode"], "eight_way")
        self.assertEqual(once["active_preset_id"], DEFAULT_PRESET_ID)

        preserved = once["custom_presets"][0]
        self.assertEqual(preserved["layout"], saved)
        self.assertAlmostEqual(preserved["scale"], 1.25)
        self.assertEqual(preserved["dpad_mode"], "eight_way")
        self.assertEqual(preserved["base_preset_id"], DEFAULT_PRESET_ID)

    def test_collision_with_existing_legacy_id_and_name(self):
        existing_layout = layout_mod.validate_layout(layout_mod.ps_layout())
        existing = {
            "id": LEGACY_PRESET_ID,
            "name": LEGACY_PRESET_NAME,
            "layout": existing_layout,
            "scale": 1.0,
            "dpad_mode": "four_way",
            "base_preset_id": DEFAULT_PRESET_ID,
        }
        raw = _base_raw(
            "tablet_compact",
            layout=layout_mod.validate_layout(layout_mod.tablet_compact_layout()),
            custom_presets=[existing],
        )
        once = validate_settings(raw)
        twice = validate_settings(once)
        self.assertEqual(once, twice)

        ids = [p["id"] for p in once["custom_presets"]]
        names = [p["name"] for p in once["custom_presets"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(len(set(names)), 2)
        self.assertIn(LEGACY_PRESET_ID, ids)
        # A genuine collision must uniquify the new preset while keeping the
        # existing one untouched; any positive unique id is acceptable.
        self.assertTrue(
            any(
                id_.startswith(LEGACY_PRESET_ID + "_") and id_ != LEGACY_PRESET_ID
                for id_ in ids
            )
            and LEGACY_PRESET_ID in ids
        )
        preserved_existing = next(
            p for p in once["custom_presets"] if p["id"] == LEGACY_PRESET_ID
        )
        self.assertEqual(preserved_existing["layout"], existing_layout)
        for preset in once["custom_presets"]:
            self.assertEqual(preset["base_preset_id"], DEFAULT_PRESET_ID)

    def test_collision_with_multiple_existing_legacy_names(self):
        existing_a = {
            "id": "legacy_a",
            "name": LEGACY_PRESET_NAME,
            "layout": layout_mod.validate_layout(layout_mod.ps_layout()),
            "scale": 1.0,
            "dpad_mode": "four_way",
            "base_preset_id": DEFAULT_PRESET_ID,
        }
        existing_b = {
            "id": "legacy_b",
            "name": LEGACY_PRESET_NAME + " 1",
            "layout": layout_mod.validate_layout(layout_mod.switch_layout()),
            "scale": 1.0,
            "dpad_mode": "four_way",
            "base_preset_id": DEFAULT_PRESET_ID,
        }
        raw = _base_raw(
            "tablet_trackpads",
            layout=[],
            custom_presets=[existing_a, existing_b],
        )
        once = validate_settings(raw)
        twice = validate_settings(once)
        self.assertEqual(once, twice)
        names = [p["name"] for p in once["custom_presets"]]
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3)

    def test_v1_migration_still_preserves_layout(self):
        v1_layout = layout_mod.validate_layout(layout_mod.tablet_compact_layout())
        raw = {
            "version": 1,
            "enabled": True,
            "opacity": 0.4,
            "scale": 1.1,
            "deadzone": 0.1,
            "button_color": "black",
            "edge_swipes": True,
            "haptics_enabled": True,
            "haptics_strength": 30,
            "dpad_mode": "eight_way",
            "layout": v1_layout,
        }
        once = validate_settings(raw)
        twice = validate_settings(once)
        self.assertEqual(once, twice)
        self.assertEqual(once["version"], 2)
        self.assertEqual(once["active_preset_id"], DEFAULT_PRESET_ID)
        self.assertEqual(len(once["custom_presets"]), 1)
        migrated = once["custom_presets"][0]
        self.assertEqual(migrated["layout"], v1_layout)
        self.assertAlmostEqual(migrated["scale"], 1.1)
        self.assertEqual(migrated["dpad_mode"], "eight_way")
        self.assertEqual(migrated["base_preset_id"], DEFAULT_PRESET_ID)

    def test_retired_ids_reused_as_custom_active_survive(self):
        for retired_id in RETIRED_PRESET_IDS:
            with self.subTest(retired_id=retired_id):
                custom_layout = layout_mod.validate_layout(
                    layout_mod.tablet_standard_layout()
                )
                custom = {
                    "id": retired_id,
                    "name": "My %s" % retired_id,
                    "layout": custom_layout,
                    "scale": 1.3,
                    "dpad_mode": "eight_way",
                    "base_preset_id": DEFAULT_PRESET_ID,
                }
                raw = _base_raw(
                    retired_id,
                    layout=custom_layout,
                    scale=1.3,
                    dpad_mode="eight_way",
                    custom_presets=[custom],
                )
                once = validate_settings(raw)
                twice = validate_settings(once)
                third = validate_settings(twice)

                self.assertEqual(once, twice)
                self.assertEqual(twice, third)
                self.assertEqual(once["active_preset_id"], retired_id)
                self.assertEqual(once["layout"], custom_layout)
                self.assertAlmostEqual(once["scale"], 1.3)
                self.assertEqual(once["dpad_mode"], "eight_way")

                ids = [p["id"] for p in once["custom_presets"]]
                self.assertEqual(ids, [retired_id])
                self.assertNotIn(LEGACY_PRESET_ID, ids)
                self.assertFalse(
                    any(id_.startswith(LEGACY_PRESET_ID) for id_ in ids)
                )

                preserved = once["custom_presets"][0]
                self.assertEqual(preserved["id"], retired_id)
                self.assertEqual(preserved["name"], "My %s" % retired_id)
                self.assertEqual(preserved["layout"], custom_layout)
                self.assertAlmostEqual(preserved["scale"], 1.3)
                self.assertEqual(preserved["dpad_mode"], "eight_way")
                self.assertEqual(preserved["base_preset_id"], DEFAULT_PRESET_ID)

    def test_retired_ids_not_in_catalog(self):
        from py_modules.freedom_touch.presets import preset_catalog

        ids = [p["id"] for p in preset_catalog()]
        for retired_id in RETIRED_PRESET_IDS:
            self.assertNotIn(retired_id, ids)


if __name__ == "__main__":
    unittest.main()
