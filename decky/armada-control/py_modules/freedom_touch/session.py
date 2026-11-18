"""Gamescope session, touchscreen, orientation, and focus helpers.

The daemon validates the Gamescope session once and reuses the returned
environment.  Nothing here imports Gtk or grabs an input device.
"""

import ctypes
import os
import shlex
import subprocess
from array import array
from contextlib import contextmanager
from pathlib import Path

from py_modules.freedom_touch.runtime_policy import (
    MT_POSITION_X,
    MT_POSITION_Y,
    MT_SLOT,
)

try:
    import pwd
except ImportError:  # Deployed Armada systems are Linux; Windows is authoring only.
    pwd = None


DEVICE_ENV_HELPER = "/usr/libexec/armada/device-env"
PRIMARY_TOUCHSCREEN_KEY = "ARMADA_PRIMARY_TOUCHSCREEN"
PANEL_ORIENTATION_KEY = "ARMADA_PANEL_ORIENTATION"

XA_CARDINAL = 6
SERVER_ID_ATOM = b"GAMESCOPE_XWAYLAND_SERVER_ID"
FOCUS_ATOM = b"GAMESCOPE_FOCUSED_APP"
FOCUS_GFX_ATOM = b"GAMESCOPE_FOCUSED_APP_GFX"
OVERLAY_ATOM = b"ARMADA_VIRTUAL_TRACKPADS_OVERLAY"

# include/uapi/linux/input.h: EVIOCGMTSLOTS(len) is
# _IOC(_IOC_READ, 'E', 0x0a, len) and its buffer is one 32-bit ABS code
# followed by num_slots signed 32-bit values.  The asm-generic packing below
# is what arm64 and x86 both use.
_IOC_READ = 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = 8
_IOC_SIZESHIFT = 16
_IOC_DIRSHIFT = 30
EVIOCGMTSLOTS_NR = 0x0A
# A snapshot table is (num_slots + 1) ints; anything near 16 KiB is already
# far past a real panel and must never be allocated speculatively.
MAX_SLOT_SNAPSHOT_BYTES = 1 << 14

_SESSION_KEYS = frozenset(
    {"DISPLAY", "XAUTHORITY", "GAMESCOPE_WAYLAND_DISPLAY"}
)
_STEAM_PROCESS_NAMES = frozenset({"steam", "launch-steam"})


def read_device_environment():
    """Return every ARMADA_* value printed by Armada's device-env helper.

    The helper is executed directly (no shell) and its output is parsed with
    shlex, so quoted values keep their spaces without evaluating shell text.
    """
    try:
        completed = subprocess.run(
            [DEVICE_ENV_HELPER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("armada device-env timed out after 3 seconds") from exc
    except OSError as exc:
        raise RuntimeError("cannot run armada device-env: %s" % exc) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            "armada device-env exited with status %d" % completed.returncode
        )
    return _parse_device_environment(os.fsdecode(completed.stdout or b""))


def _parse_device_environment(output):
    values = {}
    for number, raw in enumerate(output.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            words = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeError(
                "armada device-env has malformed quoting on line %d" % number
            ) from exc
        if len(words) != 1:
            continue
        key, separator, value = words[0].partition("=")
        if separator and key.startswith("ARMADA_"):
            values[key] = value
    return values


_ORIENTATION_ROTATIONS = {
    "normal": 0,
    "right": 90,
    "upside_down": 180,
    "upsidedown": 180,
    "left": 270,
}
_NUMERIC_ROTATIONS = {"0": 0, "90": 90, "180": 180, "270": 270}


def rotation_for(values):
    """Return the clockwise rotation for ARMADA_PANEL_ORIENTATION.

    Gamescope's touchscreen transform is the inverse of its scanout rotation,
    so a right-rotated panel uses our 90-degree transform.  Missing values mean
    a normal panel; an unknown value is an error rather than a guess.
    """
    value = values.get(PANEL_ORIENTATION_KEY) if values else None
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("unknown ARMADA_PANEL_ORIENTATION: %r" % (value,))
    if isinstance(value, int):
        if value in (0, 90, 180, 270):
            return value
        raise ValueError("unknown ARMADA_PANEL_ORIENTATION: %r" % (value,))
    if not isinstance(value, str):
        raise ValueError("unknown ARMADA_PANEL_ORIENTATION: %r" % (value,))

    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    rotation = _NUMERIC_ROTATIONS.get(key)
    if rotation is None:
        rotation = _ORIENTATION_ROTATIONS.get(key)
    if rotation is None:
        raise ValueError("unknown ARMADA_PANEL_ORIENTATION: %r" % (value,))
    return rotation


def open_touchscreen(values):
    """Open the exact primary touchscreen event device without grabbing it.

    Every rejected device is closed.  Missing names, zero matches, and
    ambiguous matches are errors: there is no arbitrary touchscreen fallback.
    """
    name = values.get(PRIMARY_TOUCHSCREEN_KEY) if isinstance(values, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "ARMADA_PRIMARY_TOUCHSCREEN must name the primary touchscreen"
        )

    try:
        import evdev
    except ImportError as exc:
        raise RuntimeError("python-evdev is required to open the touchscreen") from exc

    required = {
        evdev.ecodes.ABS_MT_POSITION_X,
        evdev.ecodes.ABS_MT_POSITION_Y,
        evdev.ecodes.ABS_MT_TRACKING_ID,
    }
    try:
        paths = evdev.list_devices()
    except OSError as exc:
        raise RuntimeError("cannot enumerate evdev devices: %s" % exc) from exc

    matches = []
    for path in paths:
        device = None
        try:
            device = evdev.InputDevice(path)
            if device.name != name:
                device.close()
                continue
            capabilities = device.capabilities(absinfo=False)
            codes = set(capabilities.get(evdev.ecodes.EV_ABS, ()))
            if not required.issubset(codes):
                device.close()
                continue
        except OSError:
            if device is not None:
                try:
                    device.close()
                except OSError:
                    pass
            continue
        matches.append(device)

    if not matches:
        raise RuntimeError(
            "no event device named %r has multi-touch position and tracking" % name
        )
    if len(matches) > 1:
        for device in matches:
            try:
                device.close()
            except OSError:
                pass
        raise RuntimeError(
            "multiple event devices match touchscreen %r; refusing to guess" % name
        )
    return matches[0]


def _eviocgmtslots(length):
    """Return EVIOCGMTSLOTS for a table of *length* bytes."""

    return (
        (_IOC_READ << _IOC_DIRSHIFT)
        | (length << _IOC_SIZESHIFT)
        | (ord("E") << _IOC_TYPESHIFT)
        | (EVIOCGMTSLOTS_NR << _IOC_NRSHIFT)
    )


def _read_slot_axis(fcntl, fd, code, slots):
    """Read one ABS axis for every slot through EVIOCGMTSLOTS."""

    values = array("i", [0] * (slots + 1))
    values[0] = code
    fcntl.ioctl(fd, _eviocgmtslots(len(values) * 4), values, True)
    return values[1:]


def touch_slot_snapshot(touch):
    """Return ({slot: {"x": int, "y": int}}, current_slot) or None.

    This is a read-only peek at the kernel's multitouch slot state: it never
    grabs the device, replays events, or changes any setting.  Opening a
    device mid-sequence therefore knows where each slot already is without
    pretending a contact exists.

    Everything unsupported or implausible -- no fcntl, no ABS_MT_SLOT, a
    non-zero minimum, an oversized slot table, an ioctl the driver rejects --
    simply yields None, so the caller falls back to waiting for complete
    coordinate events instead of failing to start.
    """

    try:
        import fcntl
    except ImportError:  # Authoring on Windows; deployed systems are Linux.
        return None

    try:
        fd = touch.fileno()
        info = touch.absinfo(MT_SLOT)
        minimum = info.min
        maximum = info.max
        current = info.value
    except (OSError, AttributeError, TypeError, ValueError):
        return None

    for number in (minimum, maximum, current):
        if isinstance(number, bool) or not isinstance(number, int):
            return None
    if minimum != 0 or maximum < 0 or current < 0 or current > maximum:
        return None

    slots = maximum + 1
    if (slots + 1) * 4 >= MAX_SLOT_SNAPSHOT_BYTES:
        return None

    try:
        xs = _read_slot_axis(fcntl, fd, MT_POSITION_X, slots)
        ys = _read_slot_axis(fcntl, fd, MT_POSITION_Y, slots)
    except (OSError, ValueError, OverflowError):
        return None

    positions = {
        slot: {"x": xs[slot], "y": ys[slot]} for slot in range(slots)
    }
    return positions, current


@contextmanager
def _temporary_x11_environment(display, authority):
    """Set DISPLAY/XAUTHORITY only while Xlib opens the connection."""
    missing = object()
    previous_display = os.environ.get("DISPLAY", missing)
    previous_authority = os.environ.get("XAUTHORITY", missing)

    os.environ["DISPLAY"] = display
    if authority:
        os.environ["XAUTHORITY"] = authority
    else:
        os.environ.pop("XAUTHORITY", None)
    try:
        yield
    finally:
        if previous_display is missing:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = previous_display
        if previous_authority is missing:
            os.environ.pop("XAUTHORITY", None)
        else:
            os.environ["XAUTHORITY"] = previous_authority


def _open_display(x11, display, authority, uid=None):
    """Open a display inside the temporary environment, optionally as uid.

    Gamescope's Xwayland only accepts the desktop-local user.  A root daemon
    therefore switches its effective uid around the single XOpenDisplay call
    and always restores it.  A non-root process never switches to another
    uid, and the caller keeps ownership of the returned connection.
    """
    if uid is not None and (
        isinstance(uid, bool) or not isinstance(uid, int) or uid < 0
    ):
        raise ValueError("uid must be a non-negative integer")

    with _temporary_x11_environment(display, authority):
        original = os.geteuid()
        switch = uid is not None and uid != original
        if switch and original != 0:
            raise PermissionError(
                "cannot open the X display as uid %d while running as uid %d"
                % (uid, original)
            )
        if not switch:
            return x11.XOpenDisplay(display.encode())
        os.seteuid(uid)
        try:
            return x11.XOpenDisplay(display.encode())
        finally:
            os.seteuid(original)


def _load_x11():
    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError as exc:
        raise RuntimeError("libX11.so.6 is required for Gamescope discovery") from exc

    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.restype = ctypes.c_int
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XGetWindowProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
    ]
    x11.XGetWindowProperty.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XFree.restype = ctypes.c_int
    return x11


def _read_cardinal(x11, connection, root, atom):
    """Read one 32-bit CARDINAL property, freeing Xlib data every time."""
    data = ctypes.POINTER(ctypes.c_ubyte)()
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    count = ctypes.c_ulong()
    remaining = ctypes.c_ulong()

    status = x11.XGetWindowProperty(
        connection,
        root,
        atom,
        0,
        1,
        False,
        XA_CARDINAL,
        ctypes.byref(actual_type),
        ctypes.byref(actual_format),
        ctypes.byref(count),
        ctypes.byref(remaining),
        ctypes.byref(data),
    )
    try:
        if (
            status != 0
            or actual_type.value != XA_CARDINAL
            or actual_format.value != 32
            or count.value != 1
            or not data
        ):
            return None
        # Xlib hands format-32 data back as C long, sign-extended on LP64;
        # non-Steam shortcut app ids have bit 31 set, so keep the low 32 bits.
        return int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0]) & 0xFFFFFFFF
    finally:
        if data:
            x11.XFree(ctypes.cast(data, ctypes.c_void_p))


def _gamescope_server_id(display, authority, uid=None):
    """Return GAMESCOPE_XWAYLAND_SERVER_ID for a display, or None."""
    x11 = _load_x11()
    connection = _open_display(x11, display, authority, uid)
    if not connection:
        return None
    try:
        root = x11.XDefaultRootWindow(connection)
        atom = x11.XInternAtom(connection, SERVER_ID_ATOM, True)
        if not atom:
            return None
        return _read_cardinal(x11, connection, root, atom)
    finally:
        x11.XCloseDisplay(connection)


def _read_process_environment(process, keys):
    values = {}
    for entry in (process / "environ").read_bytes().split(b"\0"):
        key, separator, value = entry.partition(b"=")
        if not separator:
            continue
        name = os.fsdecode(key)
        if name in keys:
            values[name] = os.fsdecode(value)
    return values


def _steam_environments(uid, proc_root=Path("/proc")):
    """Return environments of this uid's Steam launcher processes only."""
    result = []
    try:
        processes = list(proc_root.glob("[0-9]*"))
    except OSError:
        return result

    for process in processes:
        try:
            if process.stat().st_uid != uid:
                continue
            command = (process / "cmdline").read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue
        if not command:
            continue
        if Path(os.fsdecode(command)).name not in _STEAM_PROCESS_NAMES:
            continue
        try:
            environment = _read_process_environment(process, _SESSION_KEYS)
        except OSError:
            continue
        if environment.get("DISPLAY") and environment.get("GAMESCOPE_WAYLAND_DISPLAY"):
            result.append(environment)
    return result


def _xwayland_authorities(uid, proc_root=Path("/proc")):
    """Map this uid's Xwayland displays to their -auth files."""
    result = {}
    try:
        processes = list(proc_root.glob("[0-9]*"))
    except OSError:
        return result

    for process in processes:
        try:
            if process.stat().st_uid != uid:
                continue
            arguments = [
                os.fsdecode(item)
                for item in (process / "cmdline").read_bytes().split(b"\0")
                if item
            ]
        except OSError:
            continue
        if not arguments:
            continue
        if "xwayland" not in Path(arguments[0]).name.lower():
            continue
        display = next((item for item in arguments[1:] if item.startswith(":")), None)
        try:
            authority = arguments[arguments.index("-auth") + 1]
        except (ValueError, IndexError):
            continue
        if display and authority:
            result.setdefault(display, authority)
    return result


def discover_session(uid, proc_root=Path("/proc")):
    """Return the primary Gamescope X11 environment for uid, or None.

    Only Steam/launch-steam environments are considered, and an X connection
    is accepted only when the root property identifies server 0, never a
    game-isolated server 1.  There is no polling here; the caller retries.
    """
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise ValueError("uid must be a non-negative integer")
    if pwd is None:
        raise RuntimeError("session discovery requires the pwd module")
    try:
        account = pwd.getpwuid(uid)
    except KeyError as exc:
        raise RuntimeError("no local account for uid %d" % uid) from exc
    except OSError as exc:
        raise RuntimeError("cannot read the account for uid %d: %s" % (uid, exc)) from exc

    candidates = _steam_environments(uid, proc_root)
    if not candidates:
        return None
    authorities = _xwayland_authorities(uid, proc_root)

    def preference(environment):
        display = environment["DISPLAY"]
        expected = authorities.get(display)
        authority = environment.get("XAUTHORITY")
        return (
            bool(authority and expected and authority == expected),
            bool(authority),
            bool(expected),
        )

    for environment in sorted(candidates, key=preference, reverse=True):
        display = environment["DISPLAY"]
        matched = authorities.get(display)
        options = []
        for authority in (matched, environment.get("XAUTHORITY"), None):
            if authority not in options:
                options.append(authority)
        for authority in options:
            if _gamescope_server_id(display, authority, uid=uid) != 0:
                continue
            session = {
                "DISPLAY": display,
                "GAMESCOPE_WAYLAND_DISPLAY": environment["GAMESCOPE_WAYLAND_DISPLAY"],
                "GDK_BACKEND": "x11",
                "XDG_RUNTIME_DIR": "/run/user/%d" % uid,
                "HOME": account.pw_dir,
                "USER": account.pw_name,
                "LOGNAME": account.pw_name,
            }
            if authority:
                session["XAUTHORITY"] = authority
            orientation = running_panel_orientation(uid, proc_root)
            if orientation is not None:
                session[PANEL_ORIENTATION_KEY] = orientation
            return session
    return None


class FocusMonitor:
    """Persistent Xlib connection for Gamescope's focused-app properties."""

    def __init__(self, env):
        if not isinstance(env, dict):
            raise ValueError("FocusMonitor requires a session environment mapping")
        display_name = env.get("DISPLAY")
        if not isinstance(display_name, str) or not display_name:
            raise ValueError("FocusMonitor requires DISPLAY")
        authority = env.get("XAUTHORITY")
        if authority is not None and not isinstance(authority, str):
            raise ValueError("XAUTHORITY must be a string")

        uid = None
        user = env.get("USER")
        if user is not None:
            if not isinstance(user, str) or not user:
                raise ValueError("USER must be a non-empty string")
            if pwd is None:
                raise RuntimeError("focus monitoring requires the pwd module")
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError as exc:
                raise RuntimeError("no local account for user %r" % (user,)) from exc
            except OSError as exc:
                raise RuntimeError(
                    "cannot read the account for user %r: %s" % (user, exc)
                ) from exc

        x11 = _load_x11()
        connection = _open_display(x11, display_name, authority, uid)
        if not connection:
            raise RuntimeError("cannot open the Gamescope X11 display")

        try:
            root = x11.XDefaultRootWindow(connection)
            atom = x11.XInternAtom(connection, SERVER_ID_ATOM, True)
            server_id = _read_cardinal(x11, connection, root, atom) if atom else None
            if server_id != 0:
                raise RuntimeError(
                    "X display is not the primary Gamescope Xwayland "
                    "(GAMESCOPE_XWAYLAND_SERVER_ID is not 0)"
                )
        except Exception:
            x11.XCloseDisplay(connection)
            raise

        self._x11 = x11
        self._connection = connection
        self._root = root
        self._atoms = {}

    def get_focus(self):
        """Return (input_app, gfx_app); missing properties become None."""
        if self._connection is None:
            raise RuntimeError("FocusMonitor is closed")
        return (self._read_atom(FOCUS_ATOM), self._read_atom(FOCUS_GFX_ATOM))

    def _read_atom(self, name):
        atom = self._atoms.get(name)
        if not atom:
            atom = self._x11.XInternAtom(self._connection, name, True)
            if atom:
                self._atoms[name] = atom
        if not atom:
            return None
        return _read_cardinal(self._x11, self._connection, self._root, atom)

    def close(self):
        """Close the owned Xlib connection; safe to call more than once."""
        connection = self._connection
        self._connection = None
        self._root = None
        self._atoms = {}
        if connection is not None:
            self._x11.XCloseDisplay(connection)
            self._x11 = None


def _running_gamescope_binaries(uid=None, proc_root=Path("/proc")):
    """Find the live Steam compositor images, including custom runtime paths.

    Keep /proc/PID/exe handles rather than resolving them to a disk pathname:
    replacing an installed file must not change what a running process reports.
    """
    binaries = []
    try:
        processes = list(proc_root.glob("[0-9]*"))
    except OSError:
        return binaries
    for process in processes:
        try:
            if uid is not None and process.stat().st_uid != uid:
                continue
            executable = process / "exe"
            name = Path(os.readlink(executable).removesuffix(" (deleted)")).name
            if name not in ("gamescope", "gamescope-wl"):
                continue
            arguments = (process / "cmdline").read_bytes().split(b"\0")
            if b"--steam" not in arguments and b"-e" not in arguments:
                continue
            binaries.append(executable)
        except OSError:
            continue
    return binaries


def running_panel_orientation(uid, proc_root=Path("/proc")):
    """Read the orientation actually used to start this Steam compositor.

    Armada's rotation setting is applied when the session starts. Reading its
    saved file would apply a pending choice too early; the model profile can
    also disagree with a user's choice. The live command line is authoritative.
    """
    binaries = _running_gamescope_binaries(uid, proc_root)
    if len(binaries) != 1:
        return None
    try:
        arguments = [os.fsdecode(item) for item in
                     (binaries[0].parent / "cmdline").read_bytes().split(b"\0") if item]
    except OSError:
        return None
    orientation = None
    for index, argument in enumerate(arguments):
        if argument == "--force-orientation" and index + 1 < len(arguments):
            orientation = arguments[index + 1]
        elif argument.startswith("--force-orientation="):
            orientation = argument.partition("=")[2]
    if orientation is None or orientation == "auto":
        return None
    # Validate rather than silently substituting a direction for bad metadata.
    rotation_for({PANEL_ORIENTATION_KEY: orientation})
    return orientation


def overlay_supported(binary=None, *, uid=None, proc_root=Path("/proc")):
    """Check the loaded Steam compositor, or an explicitly supplied offline image."""
    if binary is None:
        binaries = _running_gamescope_binaries(uid, proc_root)
        if len(binaries) != 1:
            return False
        binary = binaries[0]
    try:
        with open(binary, "rb") as handle:
            tail = b""
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                window = tail + chunk
                if OVERLAY_ATOM in window:
                    return True
                tail = window[-(len(OVERLAY_ATOM) - 1):]
    except OSError:
        return False
