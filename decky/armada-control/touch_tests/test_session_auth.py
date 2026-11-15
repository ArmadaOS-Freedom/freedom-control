"""Authentication tests for the root daemon's Gamescope X11 opens.

Gamescope's Xwayland accepts only the desktop-local user, so a root daemon
must switch effective uid for the single XOpenDisplay call.  These tests fake
only that process boundary and the passwd lookup; the real helper logic runs.
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import session  # noqa: E402

try:
    import fcntl
except ImportError:  # The deployed systems are Linux; authoring may not be.
    fcntl = None


class EffectiveUid:
    """Mutable effective uid with a record of every switch."""

    def __init__(self, uid):
        self.uid = uid
        self.switches = []
        self.fail_on = None

    def get(self):
        return self.uid

    def set(self, uid):
        if uid == self.fail_on:
            raise OSError("cannot restore effective uid")
        self.switches.append(uid)
        self.uid = uid


class FakeX11:
    """Only the calls session.py makes around XOpenDisplay."""

    def __init__(self, connection=1, error=None):
        self.connection = connection
        self.error = error
        self.display_names = []
        self.effective_uids = []
        self.environments = []
        self.closed = []

    def XOpenDisplay(self, name):
        self.display_names.append(name)
        self.effective_uids.append(os.geteuid())
        self.environments.append(
            (os.environ.get("DISPLAY"), os.environ.get("XAUTHORITY"))
        )
        if self.error is not None:
            raise self.error
        return self.connection

    def XDefaultRootWindow(self, _connection):
        return 1

    def XInternAtom(self, _connection, _name, _only_if_exists):
        return 2

    def XCloseDisplay(self, connection):
        self.closed.append(connection)


def _patch_uid(state):
    return mock.patch.multiple(
        session.os, geteuid=state.get, seteuid=state.set
    )


class OpenDisplayTests(unittest.TestCase):
    def test_root_open_uses_desktop_euid_and_restores_after_success(self):
        state = EffectiveUid(0)
        x11 = FakeX11(connection=7)

        with _patch_uid(state):
            connection = session._open_display(
                x11, ":0", "/tmp/xauth", 1000
            )

        self.assertEqual(connection, 7)
        self.assertEqual(x11.effective_uids, [1000])
        self.assertEqual(x11.environments, [(":0", "/tmp/xauth")])
        self.assertEqual(state.switches, [1000, 0])
        self.assertEqual(state.uid, 0)

    def test_root_open_restores_after_null_connection(self):
        state = EffectiveUid(0)
        x11 = FakeX11(connection=None)

        with _patch_uid(state):
            connection = session._open_display(x11, ":0", None, 1000)

        self.assertIsNone(connection)
        self.assertEqual(x11.effective_uids, [1000])
        self.assertEqual(state.uid, 0)

    def test_root_open_restores_after_raised_exception(self):
        state = EffectiveUid(0)
        x11 = FakeX11(error=RuntimeError("XOpenDisplay failed"))

        with _patch_uid(state):
            with self.assertRaises(RuntimeError):
                session._open_display(x11, ":0", None, 1000)

        self.assertEqual(x11.effective_uids, [1000])
        self.assertEqual(state.uid, 0)
        self.assertEqual(state.switches, [1000, 0])

    def test_same_uid_does_not_switch(self):
        state = EffectiveUid(1000)
        x11 = FakeX11()

        with _patch_uid(state):
            session._open_display(x11, ":0", None, 1000)

        self.assertEqual(x11.effective_uids, [1000])
        self.assertEqual(state.switches, [])

    def test_missing_uid_does_not_switch(self):
        state = EffectiveUid(0)
        x11 = FakeX11()

        with _patch_uid(state):
            session._open_display(x11, ":0", None)

        self.assertEqual(x11.effective_uids, [0])
        self.assertEqual(state.switches, [])

    def test_non_root_other_uid_fails_without_opening(self):
        state = EffectiveUid(1000)
        x11 = FakeX11()

        with _patch_uid(state):
            with self.assertRaises(PermissionError) as caught:
                session._open_display(x11, ":0", None, 2000)

        self.assertIn("2000", str(caught.exception))
        self.assertEqual(x11.display_names, [])
        self.assertEqual(state.switches, [])

    def test_restore_failure_is_not_swallowed(self):
        state = EffectiveUid(0)
        state.fail_on = 0
        x11 = FakeX11()

        with _patch_uid(state):
            with self.assertRaises(OSError) as caught:
                session._open_display(x11, ":0", None, 1000)

        self.assertIn("cannot restore", str(caught.exception))


class DiscoverSessionTests(unittest.TestCase):
    def test_discover_session_passes_the_verified_desktop_uid(self):
        account = types.SimpleNamespace(
            pw_name="deck", pw_uid=1000, pw_dir="/home/deck"
        )
        fake_pwd = types.SimpleNamespace(
            getpwuid=mock.Mock(return_value=account)
        )
        opened = []

        def server_id(display, authority, uid=None):
            opened.append((display, authority, uid))
            return 0

        with mock.patch.object(session, "pwd", fake_pwd), mock.patch.object(
            session,
            "_steam_environments",
            return_value=[
                {
                    "DISPLAY": ":0",
                    "XAUTHORITY": "/run/user/1000/xauth",
                    "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
                }
            ],
        ), mock.patch.object(
            session,
            "_xwayland_authorities",
            return_value={":0": "/run/user/1000/xauth"},
        ), mock.patch.object(
            session, "_gamescope_server_id", side_effect=server_id
        ):
            result = session.discover_session(1000)

        fake_pwd.getpwuid.assert_called_once_with(1000)
        self.assertEqual(opened, [(":0", "/run/user/1000/xauth", 1000)])
        self.assertEqual(result["USER"], "deck")
        self.assertEqual(result["LOGNAME"], "deck")
        self.assertTrue(all(isinstance(value, str) for value in result.values()))


class FocusMonitorAuthTests(unittest.TestCase):
    def _account(self):
        return types.SimpleNamespace(
            pw_name="deck", pw_uid=1000, pw_dir="/home/deck"
        )

    def test_focus_monitor_resolves_user_and_opens_as_desktop_uid(self):
        state = EffectiveUid(0)
        x11 = FakeX11()
        fake_pwd = types.SimpleNamespace(
            getpwnam=mock.Mock(return_value=self._account())
        )

        with _patch_uid(state), mock.patch.object(
            session, "pwd", fake_pwd
        ), mock.patch.object(
            session, "_load_x11", return_value=x11
        ), mock.patch.object(
            session, "_read_cardinal", return_value=0
        ):
            monitor = session.FocusMonitor(
                {"DISPLAY": ":0", "USER": "deck"}
            )
            monitor.close()

        fake_pwd.getpwnam.assert_called_once_with("deck")
        self.assertEqual(x11.effective_uids, [1000])
        self.assertEqual(state.uid, 0)
        self.assertEqual(x11.closed, [1])

    def test_focus_monitor_without_user_does_not_switch(self):
        state = EffectiveUid(0)
        x11 = FakeX11()
        fake_pwd = types.SimpleNamespace(
            getpwnam=mock.Mock(side_effect=AssertionError("unexpected lookup"))
        )

        with _patch_uid(state), mock.patch.object(
            session, "pwd", fake_pwd
        ), mock.patch.object(
            session, "_load_x11", return_value=x11
        ), mock.patch.object(
            session, "_read_cardinal", return_value=0
        ):
            monitor = session.FocusMonitor({"DISPLAY": ":0"})
            monitor.close()

        self.assertEqual(x11.effective_uids, [0])
        self.assertEqual(state.switches, [])


class SnapshotTouch:
    """Only the calls a slot snapshot makes on a real evdev device."""

    def __init__(self, slots=3, current=2, minimum=0, fd=5):
        self.slots = slots
        self.current = current
        self.minimum = minimum
        self.fd = fd
        self.codes = []

    def fileno(self):
        return self.fd

    def absinfo(self, code):
        self.codes.append(code)
        return types.SimpleNamespace(
            min=self.minimum, max=self.slots - 1, value=self.current
        )


MT_SLOT = 47
MT_POSITION_X = 53
MT_POSITION_Y = 54


@unittest.skipUnless(fcntl is not None, "fcntl is Linux-only")
class TouchSlotSnapshotTests(unittest.TestCase):
    def test_reads_both_axes_for_every_slot(self):
        calls = []

        def fake_ioctl(fd, request, buffer, mutate=True):
            calls.append((fd, request, buffer[0], len(buffer)))
            base = 100 if buffer[0] == MT_POSITION_X else 200
            for index in range(1, len(buffer)):
                buffer[index] = base + index
            return 0

        touch = SnapshotTouch(slots=3, current=2)
        with mock.patch.object(fcntl, "ioctl", side_effect=fake_ioctl):
            result = session.touch_slot_snapshot(touch)

        self.assertIsNotNone(result)
        positions, current = result
        self.assertEqual(current, 2)
        self.assertEqual(
            positions,
            {
                0: {"x": 101, "y": 201},
                1: {"x": 102, "y": 202},
                2: {"x": 103, "y": 203},
            },
        )
        # _IOC(_IOC_READ, 'E', 0x0a, (num_slots + 1) * 4) with one code word.
        length = (3 + 1) * 4
        request = (2 << 30) | (length << 16) | (ord("E") << 8) | 0x0A
        self.assertEqual(
            [call[1:] for call in calls],
            [
                (request, MT_POSITION_X, length // 4),
                (request, MT_POSITION_Y, length // 4),
            ],
        )
        self.assertEqual([call[0] for call in calls], [touch.fd, touch.fd])
        self.assertEqual(touch.codes, [MT_SLOT])

    def test_a_rejected_ioctl_yields_no_snapshot(self):
        with mock.patch.object(
            fcntl, "ioctl", side_effect=OSError(25, "inappropriate ioctl")
        ):
            self.assertIsNone(session.touch_slot_snapshot(SnapshotTouch()))

    def test_implausible_slot_tables_are_refused_without_an_ioctl(self):
        cases = [
            SnapshotTouch(slots=0, current=0),
            SnapshotTouch(slots=3, current=3),
            SnapshotTouch(slots=3, minimum=1),
            SnapshotTouch(slots=4096, current=0),
        ]
        ioctl = mock.Mock()
        with mock.patch.object(fcntl, "ioctl", ioctl):
            for touch in cases:
                with self.subTest(touch=touch):
                    self.assertIsNone(session.touch_slot_snapshot(touch))
        ioctl.assert_not_called()

    def test_missing_slot_absinfo_yields_no_snapshot(self):
        class NoSlotInfo(SnapshotTouch):
            def absinfo(self, _code):
                raise OSError(22, "invalid argument")

        ioctl = mock.Mock()
        with mock.patch.object(fcntl, "ioctl", ioctl):
            self.assertIsNone(session.touch_slot_snapshot(NoSlotInfo()))
        ioctl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
