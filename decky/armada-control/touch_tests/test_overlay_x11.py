"""Smoke test for the native GTK/X11 overlay.

The real overlay module runs as a subprocess against the X server named by
``DISPLAY`` (the manager supplies Xvfb through ``xvfb-run -a``).  The Xvfb
server must be started with ``-noreset``: the routing tests seed a root
property using short-lived ``xprop`` clients, and a server that resets when
the last client disconnects drops that property before it can be read.  The
test covers only the X11 mechanics the daemon relies on: the ready
handshake, a real mapped GTK window, the marker atom, the routing
capability, click-through expanded visuals, complete unmapping for a hidden
or legacy collapsed payload, restoration, and clean shutdown at stdin EOF.

``FREEDOM_TOUCH_ROUTING`` is a compositor capability, not an overlay claim.
The negative tests prove it is absent by default and that the overlay never
creates it; the positive tests seed an exact ``CARDINAL`` 32-bit scalar 1 on
the root, verify routing is reported, and verify the overlay leaves that
property untouched.  Malformed properties (wrong type, format, length, or
value) must not count as support.

The retired pointer-bubble gesture tests are intentionally absent: this
contract has no interactive overlay chrome, no input region, and no GDK event
controller.  The in-game pill is drawn visually and hit-tested by the daemon.

Xvfb is not Gamescope.  This does not prove compositor integration, a real
Gamescope session, GPU rendering, or behavior on the target device.
"""

import ctypes
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

TITLE = "Freedom Touch Overlay"
ATOM = "ARMADA_VIRTUAL_TRACKPADS_OVERLAY"
REGION_ATOM = "FREEDOM_TOUCH_REGION"
ROUTING_ATOM = "FREEDOM_TOUCH_ROUTING"
EDGE_ATOM = "FREEDOM_TOUCH_EDGE_SWIPES"
SHAPE_INPUT = 2  # ShapeInput from Xext; ShapeBounding is 0, ShapeClip is 1.

READY_TIMEOUT = 8.0
WINDOW_TIMEOUT = 8.0
SHUTDOWN_TIMEOUT = 4.0
POLL_INTERVAL = 0.05

requires_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="needs an X11 display (manager runs this under xvfb-run -a)",
)


def _run(argv):
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5.0,
        check=False,
    )


def _normalized(text):
    """Compare titles without depending on the spaces GTK may strip."""

    return re.sub(r"\s+", "", text).casefold()


def _window_tree():
    result = _run(["xwininfo", "-root", "-tree"])
    if result.returncode == 0:
        return result.stdout.strip()
    return result.stderr.strip()


def _tree_entries():
    result = _run(["xwininfo", "-root", "-tree"])
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        match = re.search(r'(0x[0-9a-fA-F]+)\s+"([^"]*)"', line)
        if match:
            entries.append((int(match.group(1), 16), match.group(2)))
    return entries


def _window_names(window_id):
    result = _run(
        ["xprop", "-id", "0x%x" % window_id, "WM_NAME", "_NET_WM_NAME"]
    )
    if result.returncode != 0:
        return []
    return re.findall(r'=\s*"(.*)"', result.stdout)


def _find_overlay_window():
    entries = _tree_entries()
    expected = _normalized(TITLE)
    for window_id, name in entries:
        if _normalized(name) == expected:
            return window_id
    # xwininfo can print an empty WM_NAME when GTK only set the UTF-8
    # _NET_WM_NAME property, so confirm the actual title with xprop.
    for window_id, _name in entries:
        for name in _window_names(window_id):
            if _normalized(name) == expected:
                return window_id
    return None


def _map_state(window_id):
    result = _run(["xwininfo", "-id", "0x%x" % window_id])
    if result.returncode != 0:
        return None
    match = re.search(r"Map State:\s*(\S+)", result.stdout)
    return match.group(1) if match else None


def _atom_value(window_id):
    result = _run(["xprop", "-id", "0x%x" % window_id, ATOM])
    if result.returncode != 0:
        return None
    match = re.search(r"=\s*(\d+)\s*$", result.stdout.strip())
    return int(match.group(1)) if match else None


def _cardinal_values(window_id, name):
    """Read a CARDINAL property, returning None when it is absent."""

    result = _run(["xprop", "-id", "0x%x" % window_id, name])
    if result.returncode != 0 or "not found" in result.stdout:
        return None
    match = re.search(r"=\s*(.+)$", result.stdout.strip())
    if match is None:
        return None
    numbers = re.findall(r"-?\d+", match.group(1))
    return tuple(int(number) for number in numbers) if numbers else None


def _root_property_text(name):
    """Return the raw xprop line for a root property, or None if absent.

    ``xprop`` exits 0 when the property is missing and prints a diagnostic
    such as ``not found`` or ``no such atom on any window`` instead of a
    value, so the diagnostic text is not a capability.  Only an actual
    ``NAME(TYPE) = value`` assignment counts as present.  The assignment is
    returned verbatim so malformed seeded values (wrong value, type, format,
    or length) still reach the strict capability checks unchanged.
    """

    result = _run(["xprop", "-root", name])
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if re.match(
        r"^%s\([^)]*\)\s*=\s*" % re.escape(name), output, re.DOTALL
    ) is None:
        return None
    return output


def _root_atom_value(name):
    text = _root_property_text(name)
    if text is None:
        return None
    match = re.search(r"=\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def _seed_root_property(format_spec, value):
    """Create the test's exact root property; the compositor owns it."""

    result = _run(
        [
            "xprop", "-root", "-f", ROUTING_ATOM, format_spec,
            "-set", ROUTING_ATOM, value,
        ]
    )
    assert result.returncode == 0, (
        "xprop could not seed %s: %s"
        % (ROUTING_ATOM, result.stderr.strip())
    )


def _remove_root_property():
    """Remove only the exact property this test module seeded."""

    _run(["xprop", "-root", "-remove", ROUTING_ATOM])


def _window_size(window_id):
    result = _run(["xwininfo", "-id", "0x%x" % window_id])
    if result.returncode != 0:
        return None
    width = re.search(r"Width:\s*(\d+)", result.stdout)
    height = re.search(r"Height:\s*(\d+)", result.stdout)
    if width is None or height is None:
        return None
    return int(width.group(1)), int(height.group(1))


class _XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


def _input_rectangles(window_id):
    """Return the X ShapeInput rectangles for a real window."""

    x11 = ctypes.CDLL("libX11.so.6")
    xext = ctypes.CDLL("libXext.so.6")

    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XFree.restype = ctypes.c_int
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.restype = ctypes.c_int
    xext.XShapeGetRectangles.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    xext.XShapeGetRectangles.restype = ctypes.POINTER(_XRectangle)

    # None opens the DISPLAY inherited from the environment; the test never
    # hard-codes a display string.
    display = x11.XOpenDisplay(None)
    assert display, "cannot open the DISPLAY inherited from the environment"
    try:
        count = ctypes.c_int(0)
        ordering = ctypes.c_int(0)
        rectangles = xext.XShapeGetRectangles(
            display,
            ctypes.c_ulong(window_id),
            SHAPE_INPUT,
            ctypes.byref(count),
            ctypes.byref(ordering),
        )
        result = []
        if rectangles:
            for index in range(count.value):
                rectangle = rectangles[index]
                result.append(
                    (
                        rectangle.x,
                        rectangle.y,
                        rectangle.width,
                        rectangle.height,
                    )
                )
            x11.XFree(ctypes.cast(rectangles, ctypes.c_void_p))
        return result
    finally:
        x11.XCloseDisplay(display)


def _poll_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return None
        time.sleep(min(POLL_INTERVAL, remaining))


class OverlayProcess:
    """Own one overlay process and guarantee it is stopped and drained."""

    def __init__(self):
        env = os.environ.copy()
        env["GDK_BACKEND"] = "x11"
        env["PYTHONPATH"] = str(PLUGIN_ROOT)
        self.proc = subprocess.Popen(
            ["/usr/bin/python3", "-m", "py_modules.freedom_touch.overlay"],
            cwd=str(PLUGIN_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Unbuffered pipes: select() must agree with readline(), so a
            # queued line cannot hide in Python's read-ahead buffer.
            bufsize=0,
        )
        self._stderr = None
        self._reported = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        stderr = self._stop()
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
        if exc_type is not None and stderr.strip() and not self._reported:
            print("overlay stderr:\n%s" % stderr.rstrip(), file=sys.stderr)
        return False

    def _collect_stderr(self):
        if self._stderr is None:
            stream = self.proc.stderr
            data = b""
            if stream is not None:
                try:
                    data = stream.read()
                except (OSError, ValueError):
                    data = b""
                finally:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._stderr = data.decode("utf-8", "replace")
        return self._stderr

    def _stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
        return self._collect_stderr()

    def failure(self, message):
        """Stop the process, include its stderr, and build an assertion."""

        stderr = self._stop()
        self._reported = True
        if stderr.strip():
            message = "%s\n\noverlay stderr:\n%s" % (
                message,
                stderr.rstrip(),
            )
        return AssertionError(message)

    def wait_ready(self, touch_routing):
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise self.failure(
                    "overlay did not report ready within %.0fs"
                    % READY_TIMEOUT
                )
            readable, _, _ = select.select(
                [self.proc.stdout], [], [], min(remaining, POLL_INTERVAL)
            )
            if not readable:
                code = self.proc.poll()
                if code is not None:
                    raise self.failure(
                        "overlay exited with %r before the ready handshake"
                        % (code,)
                    )
                continue
            line = self.proc.stdout.readline()
            if not line:
                raise self.failure(
                    "overlay closed stdout before the ready handshake"
                )
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise self.failure(
                    "overlay handshake is not JSON: %r" % (line,)
                ) from None
            expected = {"ready": True, "touch_routing": touch_routing}
            if payload != expected:
                raise self.failure(
                    "unexpected overlay handshake: %r, expected %r"
                    % (payload, expected)
                )
            return

    def send(self, message):
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(payload + b"\n")
        self.proc.stdin.flush()

    def close_stdin(self):
        stream = self.proc.stdin
        if stream is not None and not stream.closed:
            stream.close()

    def wait_exit(self, timeout):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise self.failure(
                "overlay did not exit within %.0fs of stdin EOF" % timeout
            ) from None


def _legacy_collapsed_message():
    """An older daemon payload that asked for the retired visible bubble."""

    from py_modules.freedom_touch.overlay import _default_message

    message = _default_message()
    message["visible"] = True
    message["mode"] = "collapsed"
    message["chrome"] = True
    message["bubble"] = {"x": 0.60, "y": 0.50}
    return message


def _assert_click_through(overlay, window_id):
    region = _cardinal_values(window_id, REGION_ATOM)
    if region is not None:
        raise overlay.failure(
            "%s on 0x%x is %r, expected absent"
            % (REGION_ATOM, window_id, region)
        )
    edge = _cardinal_values(window_id, EDGE_ATOM)
    if edge is not None:
        raise overlay.failure(
            "%s on 0x%x is %r, expected absent"
            % (EDGE_ATOM, window_id, edge)
        )
    rectangles = _input_rectangles(window_id)
    if rectangles:
        raise overlay.failure(
            "ShapeInput on 0x%x is %r, expected click-through"
            % (window_id, rectangles)
        )


def _assert_unmapped(overlay, window_id):
    if not _poll_until(
        lambda: _map_state(window_id) == "IsUnMapped", WINDOW_TIMEOUT
    ):
        raise overlay.failure(
            "window 0x%x stayed mapped (last Map State: %r)"
            % (window_id, _map_state(window_id))
        )


def _find_mapped_window(overlay, window_id=None):
    if window_id is None:
        window_id = _poll_until(_find_overlay_window, WINDOW_TIMEOUT)
        if window_id is None:
            raise overlay.failure(
                "no X11 window titled %r appeared within %.0fs\n\n"
                "xwininfo -root -tree:\n%s"
                % (TITLE, WINDOW_TIMEOUT, _window_tree())
            )
    if not _poll_until(
        lambda: _map_state(window_id) == "IsViewable", WINDOW_TIMEOUT
    ):
        raise overlay.failure(
            "window 0x%x never became IsViewable (last Map State: %r)"
            % (window_id, _map_state(window_id))
        )
    return window_id


@requires_display
def test_overlay_without_root_capability_is_not_routing():
    from py_modules.freedom_touch.overlay import _default_message

    _remove_root_property()
    try:
        with OverlayProcess() as overlay:
            overlay.wait_ready(False)

            if _root_property_text(ROUTING_ATOM) is not None:
                raise overlay.failure(
                    "%s appeared on the root window without being seeded"
                    % ROUTING_ATOM
                )

            overlay.send(_default_message())
            window_id = _find_mapped_window(overlay)

            if _atom_value(window_id) != 1:
                raise overlay.failure(
                    "%s on 0x%x is %r, expected 1"
                    % (ATOM, window_id, _atom_value(window_id))
                )

            if _root_property_text(ROUTING_ATOM) is not None:
                raise overlay.failure(
                    "%s was created on the root window by the overlay"
                    % ROUTING_ATOM
                )

        if _root_property_text(ROUTING_ATOM) is not None:
            raise AssertionError(
                "%s appeared on the root window after overlay shutdown"
                % ROUTING_ATOM
            )
    finally:
        _remove_root_property()


@pytest.mark.parametrize(
    "format_spec, value",
    [
        ("32c", "0"),
        ("32c", "1, 1"),
        ("8c", "1"),
        ("8s", "1"),
    ],
    ids=["wrong-value", "wrong-length", "wrong-format", "wrong-type"],
)
@requires_display
def test_overlay_rejects_nonconforming_root_capability(format_spec, value):
    _remove_root_property()
    try:
        _seed_root_property(format_spec, value)
        seeded = _root_property_text(ROUTING_ATOM)
        assert seeded is not None

        with OverlayProcess() as overlay:
            overlay.wait_ready(False)
            if _root_property_text(ROUTING_ATOM) != seeded:
                raise overlay.failure(
                    "%s was modified while it did not match the capability "
                    "contract" % ROUTING_ATOM
                )
            overlay.close_stdin()
            code = overlay.wait_exit(SHUTDOWN_TIMEOUT)
            if code != 0:
                raise overlay.failure(
                    "overlay exited with %r, expected 0" % (code,)
                )

        if _root_property_text(ROUTING_ATOM) != seeded:
            raise AssertionError(
                "%s was modified after overlay shutdown" % ROUTING_ATOM
            )
    finally:
        _remove_root_property()


@requires_display
def test_overlay_maps_click_through_hides_and_restores_under_x11():
    from py_modules.freedom_touch.overlay import _default_message

    message = _default_message()
    assert message["visible"] is True

    _remove_root_property()
    try:
        # The compositor seeds the capability; the overlay only reads it.
        _seed_root_property("32c", "1")
        if _root_atom_value(ROUTING_ATOM) != 1:
            raise AssertionError("could not seed the root routing capability")

        with OverlayProcess() as overlay:
            overlay.wait_ready(True)

            if _root_atom_value(ROUTING_ATOM) != 1:
                raise overlay.failure(
                    "%s did not stay set to 1 during the handshake"
                    % ROUTING_ATOM
                )

            overlay.send(message)
            window_id = _find_mapped_window(overlay)

            names = _window_names(window_id)
            if not names:
                raise overlay.failure(
                    "xprop found no WM_NAME/_NET_WM_NAME on 0x%x" % window_id
                )
            if _normalized(names[0]) != _normalized(TITLE):
                raise overlay.failure(
                    "window 0x%x title is %r, expected %r"
                    % (window_id, names[0], TITLE)
                )

            if _atom_value(window_id) != 1:
                raise overlay.failure(
                    "%s on 0x%x is %r, expected 1"
                    % (ATOM, window_id, _atom_value(window_id))
                )

            if _root_atom_value(ROUTING_ATOM) != 1:
                raise overlay.failure(
                    "%s on the root window is %r, expected the seeded 1"
                    % (ROUTING_ATOM, _root_atom_value(ROUTING_ATOM))
                )

            _assert_click_through(overlay, window_id)

            # The marker atom is already 1 from the first mapped payload, so
            # polling it cannot order a later update.  Synchronise on real map
            # state instead: hide first and wait for the actual unmap, which
            # proves the overlay consumed the preceding queue.
            before_chrome = dict(message)
            before_chrome["visible"] = False
            overlay.send(before_chrome)
            _assert_unmapped(overlay, window_id)

            # Expanded chrome is visual only: the pill must not open an input
            # region or change the click-through shape.  Mapping again is the
            # barrier that proves this visible/chrome payload was processed.
            expanded_chrome = dict(message)
            expanded_chrome["chrome"] = True
            overlay.send(expanded_chrome)
            _find_mapped_window(overlay, window_id)
            if _atom_value(window_id) != 1:
                raise overlay.failure(
                    "overlay lost its marker after chrome was enabled"
                )
            _assert_click_through(overlay, window_id)

            # An older queued collapsed/visible=true payload must be
            # normalized to a complete runtime hide.
            overlay.send(_legacy_collapsed_message())
            _assert_unmapped(overlay, window_id)
            _assert_click_through(overlay, window_id)

            # Restoring visibility maps the expanded controls click-through.
            overlay.send(expanded_chrome)
            _find_mapped_window(overlay, window_id)
            _assert_click_through(overlay, window_id)

            # A normal hidden payload also unmaps and clears every region.
            hidden = dict(expanded_chrome)
            hidden["visible"] = False
            overlay.send(hidden)
            _assert_unmapped(overlay, window_id)
            _assert_click_through(overlay, window_id)

            overlay.close_stdin()
            code = overlay.wait_exit(SHUTDOWN_TIMEOUT)
            if code != 0:
                raise overlay.failure(
                    "overlay exited with %r after stdin EOF, expected 0"
                    % (code,)
                )

        if _root_atom_value(ROUTING_ATOM) != 1:
            raise AssertionError(
                "%s was deleted or changed by the overlay" % ROUTING_ATOM
            )
    finally:
        _remove_root_property()


@requires_display
def test_overlay_edge_swipes_option_never_creates_input_chrome():
    from py_modules.freedom_touch.overlay import _default_message

    _remove_root_property()
    try:
        _seed_root_property("32c", "1")

        with OverlayProcess() as overlay:
            overlay.wait_ready(True)

            expanded = _default_message()
            expanded["chrome"] = True
            expanded["edge_swipes"] = True
            overlay.send(expanded)
            window_id = _find_mapped_window(overlay)
            _assert_click_through(overlay, window_id)

            collapsed = _legacy_collapsed_message()
            collapsed["edge_swipes"] = True
            overlay.send(collapsed)
            _assert_unmapped(overlay, window_id)
            _assert_click_through(overlay, window_id)

            overlay.send(expanded)
            _find_mapped_window(overlay, window_id)
            _assert_click_through(overlay, window_id)

            overlay.close_stdin()
            code = overlay.wait_exit(SHUTDOWN_TIMEOUT)
            if code != 0:
                raise overlay.failure(
                    "overlay exited with %r after stdin EOF, expected 0"
                    % (code,)
                )

        if _root_atom_value(ROUTING_ATOM) != 1:
            raise AssertionError(
                "%s was deleted or changed by the overlay" % ROUTING_ATOM
            )
    finally:
        _remove_root_property()
