"""Tests for reading Gamescope's 32-bit focus properties through Xlib.

Xlib returns format-32 property data as C long, so on LP64 a CARDINAL with
bit 31 set (every non-Steam shortcut app id) arrives sign-extended.  These
tests fake only XGetWindowProperty/XFree; the real _read_cardinal runs.
"""

import ctypes
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import session  # noqa: E402

# A real non-Steam shortcut app id (0xD12B3146) and the QAM app id.
SHORTCUT_APP_ID = 3509268806
QAM_APP_ID = 769


class CardinalX11:
    """Returns one format-32 CARDINAL the way Xlib stores it: as C long."""

    def __init__(self, value):
        # c_ulong wraps a negative long to the platform width, which is
        # exactly the sign-extended bit pattern Xlib leaves in the buffer.
        signed = value - (1 << 32) if value & 0x80000000 else value
        self.buffer = (ctypes.c_ulong * 1)(ctypes.c_ulong(signed).value)
        self.freed = 0

    def XGetWindowProperty(self, connection, root, atom, offset, length,
                           delete, req_type, actual_type, actual_format,
                           count, remaining, data):
        actual_type._obj.value = session.XA_CARDINAL
        actual_format._obj.value = 32
        count._obj.value = 1
        remaining._obj.value = 0
        data._obj.contents = ctypes.c_ubyte.from_buffer(self.buffer)
        return 0

    def XFree(self, pointer):
        self.freed += 1
        return 0


class ReadCardinalTests(unittest.TestCase):
    def test_shortcut_app_id_with_bit_31_is_not_sign_extended(self):
        x11 = CardinalX11(SHORTCUT_APP_ID)

        value = session._read_cardinal(x11, 1, 2, 3)

        self.assertEqual(value, SHORTCUT_APP_ID)
        self.assertEqual(x11.freed, 1)

    def test_small_app_id_is_unchanged(self):
        x11 = CardinalX11(QAM_APP_ID)

        self.assertEqual(session._read_cardinal(x11, 1, 2, 3), QAM_APP_ID)
        self.assertEqual(x11.freed, 1)


if __name__ == "__main__":
    unittest.main()
