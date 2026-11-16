"""Focused regression tests for shortcut entries in ``armada_control.steam``.

Upstream a84dceeb18a2ce62f8ddaf235bba282749e63531 normalises the keys inside
each binary-VDF shortcut record, so a shortcut Steam writes with a lowercase
``appname`` or a mixed-case ``ApPnAmE``/``ApPiD`` pair still reaches the
non-Steam list.  ``installed_games`` must also keep installed Steam games
intact and drop shortcuts whose appid is already listed.

The fixtures are real binary ``shortcuts.vdf`` files read by the production
parser; only the module paths are patched, plus a temporary directory for the
Steam tree.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control import steam  # noqa: E402

MANIFEST = """\
"AppState"
{{
\t"appid"\t\t"{appid}"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"4"
}}
"""


def _cstring(text):
    return text.encode("utf-8") + b"\0"


def _binary_vdf(values):
    """Encode ``values`` as the binary VDF object shortcuts.vdf uses."""
    data = bytearray()
    for key, value in values.items():
        if isinstance(value, dict):
            data += b"\x00" + _cstring(key) + _binary_vdf(value)
        elif isinstance(value, int):
            data += b"\x02" + _cstring(key) + value.to_bytes(4, "little")
        else:
            data += b"\x01" + _cstring(key) + _cstring(value)
    data += b"\x08"
    return bytes(data)


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Steam"
        self.apps = self.root / "steamapps"
        self.apps.mkdir(parents=True)
        self.config = self.root / "config" / "config.vdf"
        self.config.parent.mkdir(parents=True)
        for name, value in (
            ("STEAM_ROOT", self.root),
            ("STEAM_APPS_DIR", self.apps),
            ("CONFIG_VDF", self.config),
        ):
            patcher = mock.patch.object(steam, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_shortcuts(self, shortcuts, user="12345"):
        path = self.root / "userdata" / user / "config" / "shortcuts.vdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_binary_vdf({"shortcuts": shortcuts}))

    def _write_manifest(self, appid, name):
        (self.apps / f"appmanifest_{appid}.acf").write_text(
            MANIFEST.format(appid=appid, name=name), encoding="utf-8"
        )

    def test_a_lowercase_appname_is_still_a_shortcut(self):
        self._write_shortcuts({"0": {"appid": 777, "appname": "Lower Case Game"}})

        self.assertEqual(
            steam.installed_games(),
            [{"appid": "777", "name": "Lower Case Game", "nonSteam": True}],
        )

    def test_mixed_case_appid_and_appname_keys_are_read(self):
        self._write_shortcuts({"0": {"ApPiD": 2001, "ApPnAmE": "Mixed Case Game"}})

        self.assertEqual(
            steam.installed_games(),
            [{"appid": "2001", "name": "Mixed Case Game", "nonSteam": True}],
        )

    def test_the_original_capitalised_appname_still_works(self):
        self._write_shortcuts({"0": {"appid": 3001, "AppName": "Existing AppName"}})

        self.assertEqual(
            steam.installed_games(),
            [{"appid": "3001", "name": "Existing AppName", "nonSteam": True}],
        )

    def test_entries_without_a_usable_appid_or_name_are_skipped(self):
        self._write_shortcuts({
            # A shortcut slot that is a plain string instead of an object.
            "0": "not a shortcut record",
            # Steam writes 0 for shortcuts it could not assign an id to.
            "1": {"appid": 0, "AppName": "Zero Appid"},
            "2": {"appid": 4001, "AppName": ""},
            "3": {"appid": 4002, "AppName": "Kept"},
        })

        self.assertEqual(
            steam.installed_games(),
            [{"appid": "4002", "name": "Kept", "nonSteam": True}],
        )

    def test_a_non_table_shortcuts_value_yields_no_games(self):
        path = self.root / "userdata" / "12345" / "config" / "shortcuts.vdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_binary_vdf({"shortcuts": "not a table"}))

        self.assertEqual(steam.installed_games(), [])

    def test_installed_games_are_kept_and_shortcut_duplicates_dropped(self):
        self._write_manifest(123, "Installed Game")
        self._write_shortcuts({
            "0": {"appid": 123, "AppName": "Duplicate Shortcut"},
            "1": {"appid": 456, "AppName": "Shortcut Game"},
        })

        self.assertEqual(
            steam.installed_games(),
            [
                {"appid": "123", "name": "Installed Game"},
                {"appid": "456", "name": "Shortcut Game", "nonSteam": True},
            ],
        )


if __name__ == "__main__":
    unittest.main()
