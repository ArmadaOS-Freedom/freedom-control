"""Non-interactive X11 game overlay for the Freedom touch controller.

The daemon owns session discovery and validates DISPLAY and GDK_BACKEND=x11
before starting this process.  The validated environment may already carry an
authorized X connection, so an explicit Xauthority file is not required.  This
module never looks for a root window, a Gamescope server id, or another
session.  GTK is imported only by the GUI branch, and only after that
environment check.

stdin carries newline-delimited JSON messages.  stdout carries exactly one
handshake line, emitted after the overlay window is realized and marked, so the
daemon can activate capture.  Diagnostics go to stderr.

The overlay has no interactive chrome.  The in-game collapse pill is drawn as
a visual and hit-tested by the daemon against the captured touchscreen; a
collapsed/hidden payload always unmaps the whole window and leaves it
click-through.  Legacy expand/bubble payloads are normalized to this hidden
state instead of drawing a bubble.
"""

import argparse
import json
import math
import os
import re
import sys

import cairo

from . import collapse
from .draw import draw_controls
from .layout import default_layout, validate_layout
from .runtime_policy import effective_layout

ATOM_NAME = "ARMADA_VIRTUAL_TRACKPADS_OVERLAY"
ROUTING_ATOM_NAME = "FREEDOM_TOUCH_ROUTING"
REGION_ATOM_NAME = "FREEDOM_TOUCH_REGION"
EDGE_ATOM_NAME = "FREEDOM_TOUCH_EDGE_SWIPES"
MAX_LINE_BYTES = 4 * 1024 * 1024
READ_CHUNK = 65536
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def _log(text):
    print("freedom-touch-overlay: %s" % (text,), file=sys.stderr, flush=True)


def _backdrop_color(value):
    if _HEX_COLOR.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "backdrop must be an opaque #RRGGBB color"
        )
    return value


def _backdrop_rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric" % field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % field)
    return number


def _ratio(value, field):
    number = _finite_number(value, field)
    if number < 0.0 or number > 1.0:
        raise ValueError("%s must be in [0, 1]" % field)
    return number


def parse_message(payload):
    """Return a canonical message, or raise ValueError.

    Layout rectangles are already effective (scaled) controls from the daemon,
    so they are validated but never scaled again here.
    """

    if not isinstance(payload, dict):
        raise ValueError("message must be an object")

    visible = payload.get("visible")
    if not isinstance(visible, bool):
        raise ValueError("visible must be a boolean")

    if "opacity" not in payload:
        raise ValueError("opacity is missing")
    opacity = _ratio(payload["opacity"], "opacity")

    mode = payload.get("mode", "expanded")
    if not isinstance(mode, str) or mode not in collapse.MODES:
        raise ValueError("mode must be expanded or collapsed")

    chrome = payload.get("chrome", False)
    if not isinstance(chrome, bool):
        raise ValueError("chrome must be a boolean")

    edge_swipes = payload.get("edge_swipes", False)
    if not isinstance(edge_swipes, bool):
        raise ValueError("edge_swipes must be a boolean")

    bubble_raw = payload.get("bubble", collapse.default_bubble())
    if not isinstance(bubble_raw, dict):
        raise ValueError("bubble must be an object")
    bubble = {}
    for key in ("x", "y"):
        if key not in bubble_raw:
            raise ValueError("bubble.%s is missing" % key)
        bubble[key] = _ratio(bubble_raw[key], "bubble.%s" % key)

    layout_raw = payload.get("layout")
    if not isinstance(layout_raw, (list, tuple)):
        raise ValueError("layout must be a list")
    layout = validate_layout(layout_raw)

    snapshot_raw = payload.get("snapshot")
    if not isinstance(snapshot_raw, dict):
        raise ValueError("snapshot must be an object")
    active = snapshot_raw.get("active")
    if not isinstance(active, bool):
        raise ValueError("snapshot.active must be a boolean")

    controls_raw = snapshot_raw.get("controls")
    if not isinstance(controls_raw, dict):
        raise ValueError("snapshot.controls must be an object")
    controls = {}
    for control_id, raw in controls_raw.items():
        if not isinstance(control_id, str):
            raise ValueError("snapshot control id must be a string")
        if not isinstance(raw, dict):
            raise ValueError("snapshot control %r must be an object" % control_id)
        pressed = raw.get("pressed")
        if not isinstance(pressed, bool):
            raise ValueError(
                "snapshot control %r pressed must be boolean" % control_id
            )
        if "x" not in raw or "y" not in raw:
            raise ValueError("snapshot control %r is missing coordinates" % control_id)
        controls[control_id] = {
            "pressed": pressed,
            "x": _ratio(raw["x"], "snapshot.x"),
            "y": _ratio(raw["y"], "snapshot.y"),
        }

    return {
        "visible": visible,
        "opacity": opacity,
        "mode": mode,
        "chrome": chrome,
        "edge_swipes": edge_swipes,
        "bubble": bubble,
        "layout": layout,
        "snapshot": {"active": active, "controls": controls},
    }


class LineBuffer:
    """Split arbitrary stdin chunks into complete lines without losing tails."""

    def __init__(self, limit=MAX_LINE_BYTES):
        self._buffer = b""
        self._limit = limit
        self._discarding = False

    @property
    def pending(self):
        return bool(self._buffer)

    def feed(self, data):
        self._buffer += data
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()

        if self._discarding:
            # The first fragment is the tail of an overlong frame that has
            # already been reported; drop it and keep any following line.
            if lines:
                self._discarding = False
                lines = lines[1:]
            else:
                self._buffer = b""
                return []

        if len(self._buffer) > self._limit:
            self._buffer = b""
            self._discarding = True
            return []
        return lines


def _default_message():
    layout = validate_layout(effective_layout(default_layout(), 1.0))
    controls = {
        control["id"]: {"pressed": False, "x": 0.5, "y": 0.5}
        for control in layout
    }
    return {
        "visible": True,
        "opacity": 1.0,
        "mode": "expanded",
        "chrome": False,
        "edge_swipes": False,
        "bubble": collapse.default_bubble(),
        "layout": layout,
        "snapshot": {"active": False, "controls": controls},
    }


def _load_state(path):
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read())
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read state %s: %s" % (path, exc)) from None
    message = parse_message(payload)
    # Preview always shows the composition even if the captured state was
    # hidden; the state's layout, opacity and snapshot are preserved.  A
    # collapsed payload is normalized to the expanded composition so preview
    # can never draw the retired bubble.
    if message["mode"] == "collapsed":
        message["mode"] = "expanded"
        message["chrome"] = False
    message["visible"] = True
    return message


def render_preview(path, width, height, state_path=None, backdrop=None):
    """Write a preview PNG; never creates a GUI.

    The default surface stays transparent.  An explicit ``backdrop`` is an
    opaque #RRGGBB color composited underneath the existing drawing so the
    controls' own alpha keeps blending against it.
    """

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    message = _load_state(state_path) if state_path else _default_message()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(width), int(height))
    context = cairo.Context(surface)
    draw_controls(context, width, height, message)
    if backdrop is not None:
        context.save()
        context.set_operator(cairo.OPERATOR_DEST_OVER)
        context.set_source_rgb(*_backdrop_rgb(backdrop))
        context.paint()
        context.restore()
    surface.write_to_png(path)


def _gui_environment_problem(environ=None):
    environ = os.environ if environ is None else environ
    if not environ.get("DISPLAY"):
        return "DISPLAY is not set"
    backend = environ.get("GDK_BACKEND", "")
    backends = [item.strip() for item in backend.split(",") if item.strip()]
    if "x11" not in backends:
        return "GDK_BACKEND must select x11"
    return None


def _run_gui():
    problem = _gui_environment_problem()
    if problem is not None:
        _log(problem)
        return 2

    # The daemon already proved this environment points at Gamescope's
    # server 0.  Importing Gtk initializes it, so it must happen only after
    # the environment check above.
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkX11", "4.0")
    gi.require_foreign("cairo")
    from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk

    import ctypes

    class XRectangle(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_short),
            ("y", ctypes.c_short),
            ("width", ctypes.c_ushort),
            ("height", ctypes.c_ushort),
        ]

    class X11Overlay:
        """Apply the marker atom and keep the overlay plane click-through."""

        XA_CARDINAL = 6
        PROP_MODE_REPLACE = 0
        SHAPE_INPUT = 2

        def __init__(self):
            self.x11 = ctypes.CDLL("libX11.so.6")
            self.xfixes = ctypes.CDLL("libXfixes.so.3")
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XInternAtom.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
            ]
            self.x11.XInternAtom.restype = ctypes.c_ulong
            self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
            self.x11.XGetWindowProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ]
            self.x11.XGetWindowProperty.restype = ctypes.c_int
            self.x11.XFree.argtypes = [ctypes.c_void_p]
            self.x11.XFree.restype = ctypes.c_int
            self.x11.XMoveResizeWindow.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint, ctypes.c_uint,
            ]
            self.x11.XChangeProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
            ]
            self.x11.XChangeProperty.restype = ctypes.c_int
            self.x11.XDeleteProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong
            ]
            self.x11.XDeleteProperty.restype = ctypes.c_int
            self.x11.XFlush.argtypes = [ctypes.c_void_p]
            self.x11.XFlush.restype = ctypes.c_int
            self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self.x11.XSync.restype = ctypes.c_int
            self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            self.x11.XCloseDisplay.restype = ctypes.c_int
            self.xfixes.XFixesCreateRegion.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int
            ]
            self.xfixes.XFixesCreateRegion.restype = ctypes.c_ulong
            self.xfixes.XFixesSetWindowShapeRegion.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
            ]
            self.xfixes.XFixesDestroyRegion.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong
            ]
            self.display = self.x11.XOpenDisplay(None)
            if not self.display:
                raise RuntimeError("cannot open the prevalidated X11 display")
            self.window = 0
            self.atom = 0
            self.region_atom = 0
            self.edge_atom = 0
            self.touch_routing = False
            self._region = "unset"

        def _set_u32(self, window, atom, values):
            array = (ctypes.c_ulong * len(values))(*values)
            return self.x11.XChangeProperty(
                self.display,
                window,
                atom,
                self.XA_CARDINAL,
                32,
                self.PROP_MODE_REPLACE,
                ctypes.cast(array, ctypes.POINTER(ctypes.c_ubyte)),
                len(values),
            )

        def _root_capability(self):
            """Return True only for an existing root CARDINAL 32 scalar 1.

            Touch routing is a *server* capability.  The overlay is only a
            client, so it reads the property and never creates, replaces, or
            deletes it.  Advertising a capability the compositor does not
            have would make the daemon send touches it cannot receive.
            """

            atom = self.x11.XInternAtom(
                self.display, ROUTING_ATOM_NAME.encode("ascii"), True
            )
            if not atom:
                return False
            root = self.x11.XDefaultRootWindow(self.display)
            if not root:
                return False

            actual_type = ctypes.c_ulong()
            actual_format = ctypes.c_int()
            nitems = ctypes.c_ulong()
            bytes_after = ctypes.c_ulong()
            data = ctypes.POINTER(ctypes.c_ubyte)()
            status = self.x11.XGetWindowProperty(
                self.display,
                root,
                atom,
                0,
                1,
                False,
                self.XA_CARDINAL,
                ctypes.byref(actual_type),
                ctypes.byref(actual_format),
                ctypes.byref(nitems),
                ctypes.byref(bytes_after),
                ctypes.byref(data),
            )
            try:
                if status != 0:
                    return False
                if (
                    actual_type.value != self.XA_CARDINAL
                    or actual_format.value != 32
                    or nitems.value != 1
                    or bytes_after.value != 0
                    or not data
                ):
                    return False
                # Xlib returns format-32 items as an array of longs.
                value = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0]
                return value == 1
            finally:
                if data:
                    self.x11.XFree(ctypes.cast(data, ctypes.c_void_p))

        def attach(self, surface, width, height):
            # GDK created the window on a different X connection; sync so the
            # xid is real before touching it through our owned connection.
            surface.get_display().sync()
            self.window = int(GdkX11.X11Surface.get_xid(surface))
            if not self.window:
                raise RuntimeError("overlay did not create an X11 window")
            self.x11.XMoveResizeWindow(
                self.display, self.window, 0, 0, int(width), int(height)
            )
            self.atom = self.x11.XInternAtom(
                self.display, ATOM_NAME.encode("ascii"), False
            )
            self._set_u32(self.window, self.atom, (1,))

            self.region_atom = self.x11.XInternAtom(
                self.display, REGION_ATOM_NAME.encode("ascii"), False
            )
            self.edge_atom = self.x11.XInternAtom(
                self.display, EDGE_ATOM_NAME.encode("ascii"), False
            )
            # Read-only: the compositor owns FREEDOM_TOUCH_ROUTING.
            self.touch_routing = bool(
                self.region_atom and self._root_capability()
            )

            # Empty input region: the full-screen surface is always
            # click-through.  There is no collapsed bubble target.
            self.set_region(None)
            # Round-trip so the server has processed the atom, capability,
            # and input-shape requests before the caller emits ready.
            self.x11.XSync(self.display, False)

        def set_region(self, rect, edge_swipes=False):
            """Publish FREEDOM_TOUCH_REGION, its input shape, and edge swipes.

            This contract never supplies a rectangle; the method is retained
            so shutdown and defensive hides can explicitly clear any older
            region/input-shape state.  A None *rect* deletes the properties
            and installs an empty ShapeInput.
            """

            if not self.display or not self.window:
                return
            if not self.region_atom:
                rect = None
            rect = tuple(rect) if rect is not None else None
            edge = bool(edge_swipes) and rect is not None and bool(
                self.edge_atom
            )
            state = (rect, edge)
            if state == self._region:
                # Applying the same state at every visual frame would add a
                # needless synchronous X round-trip to the 60 Hz path.
                return
            previous_rect = (
                self._region[0]
                if isinstance(self._region, tuple)
                else "unset"
            )
            self._region = state

            if self.edge_atom:
                if edge:
                    self._set_u32(self.window, self.edge_atom, (1,))
                else:
                    self.x11.XDeleteProperty(
                        self.display, self.window, self.edge_atom
                    )

            if rect != previous_rect:
                if rect is None:
                    if self.region_atom:
                        self.x11.XDeleteProperty(
                            self.display, self.window, self.region_atom
                        )
                    region = self.xfixes.XFixesCreateRegion(
                        self.display, None, 0
                    )
                else:
                    x, y, width, height = (int(value) for value in rect)
                    self._set_u32(
                        self.window,
                        self.region_atom,
                        (x, y, width, height),
                    )
                    rectangle = XRectangle(x, y, width, height)
                    region = self.xfixes.XFixesCreateRegion(
                        self.display,
                        ctypes.cast(
                            ctypes.pointer(rectangle), ctypes.c_void_p
                        ),
                        1,
                    )

                self.xfixes.XFixesSetWindowShapeRegion(
                    self.display, self.window, self.SHAPE_INPUT, 0, 0, region
                )
                self.xfixes.XFixesDestroyRegion(self.display, region)
            self.x11.XSync(self.display, False)

        def close(self):
            if self.display:
                if self.window and self.atom:
                    self.x11.XDeleteProperty(
                        self.display, self.window, self.atom
                    )
                if self.window and self.region_atom:
                    self.x11.XDeleteProperty(
                        self.display, self.window, self.region_atom
                    )
                if self.window and self.edge_atom:
                    self.x11.XDeleteProperty(
                        self.display, self.window, self.edge_atom
                    )
                if (
                    self.window
                    or self.atom
                    or self.region_atom
                    or self.edge_atom
                ):
                    self.x11.XFlush(self.display)
                self.x11.XCloseDisplay(self.display)
            self.display = None
            self.window = 0
            self.atom = 0
            self.region_atom = 0
            self.edge_atom = 0
            self.touch_routing = False
            self._region = "unset"

    class OverlayApplication(Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id="dev.hsteam.FreedomTouchOverlay",
                flags=Gio.ApplicationFlags.NON_UNIQUE,
            )
            self.window = None
            self.area = None
            self.xoverlay = X11Overlay()
            self.width = DEFAULT_WIDTH
            self.height = DEFAULT_HEIGHT
            self.message = None
            self.visible = False
            self.watch_id = 0
            self.lines = LineBuffer()
            self.stdin_fd = -1
            self.exit_code = 0

        def _fail(self, text):
            _log(text)
            self.exit_code = 2
            self.quit()

        def _announce_ready(self):
            print(
                json.dumps(
                    {
                        "ready": True,
                        "touch_routing": bool(self.xoverlay.touch_routing),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )

        def do_activate(self):
            if self.window is not None:
                return
            display = Gdk.Display.get_default()
            if display is None:
                self._fail("GTK could not open the prevalidated display")
                return

            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
            if monitor is not None:
                geometry = monitor.get_geometry()
                if geometry.width > 0 and geometry.height > 0:
                    self.width = geometry.width
                    self.height = geometry.height

            provider = Gtk.CssProvider()
            provider.load_from_data(
                b"window, drawingarea { background-color: transparent;"
                b" background-image: none; box-shadow: none; }"
            )
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            self.window = Gtk.ApplicationWindow(application=self)
            self.window.set_title("Freedom Touch Overlay")
            self.window.set_decorated(False)
            self.window.set_resizable(False)
            self.window.set_focusable(False)
            self.window.set_can_focus(False)
            self.window.set_default_size(self.width, self.height)

            self.area = Gtk.DrawingArea()
            self.area.set_content_width(self.width)
            self.area.set_content_height(self.height)
            self.area.set_draw_func(self._draw)
            self.window.set_child(self.area)
            # Stay hidden through realize so the marker atom and input shape
            # exist before the first map; the daemon only reveals the overlay
            # after the handshake below.
            self.window.set_visible(False)
            try:
                self.window.realize()
                surface = self.window.get_surface()
                if surface is None:
                    raise RuntimeError("overlay did not realize an X11 surface")
                self.xoverlay.attach(surface, self.width, self.height)
            except (OSError, RuntimeError, GLib.Error) as exc:
                self._fail("could not realize overlay: %s" % (exc,))
                return
            self._announce_ready()
            self._start_stdin()

        def _draw(self, _area, context, width, height):
            message = self.message
            if message is None:
                message = {
                    "visible": False,
                    "opacity": 0.0,
                    "layout": [],
                    "snapshot": {"active": False, "controls": {}},
                }
            draw_controls(context, width, height, message)

        def _start_stdin(self):
            self.stdin_fd = sys.stdin.fileno()
            channel = GLib.IOChannel.unix_new(self.stdin_fd)
            try:
                channel.set_encoding(None)
            except (AttributeError, TypeError):
                pass
            self.watch_id = GLib.io_add_watch(
                channel,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN
                | GLib.IOCondition.HUP
                | GLib.IOCondition.ERR,
                self._on_stdin,
            )

        def _on_stdin(self, _channel, _condition):
            try:
                data = os.read(self.stdin_fd, READ_CHUNK)
            except BlockingIOError:
                return True
            except OSError as exc:
                _log("stdin read failed: %s" % (exc,))
                self.watch_id = 0
                self.quit()
                return False
            if not data:
                if self.lines.pending:
                    _log("ignoring incomplete final frame at EOF")
                self.watch_id = 0
                self.quit()
                return False
            for line in self.lines.feed(data):
                self._handle_line(line)
            return True

        def _handle_line(self, line):
            text = line.strip()
            if not text:
                return
            try:
                payload = json.loads(text.decode("utf-8"))
                message = parse_message(payload)
            except (UnicodeDecodeError, ValueError) as exc:
                _log("ignoring malformed frame: %s" % (exc,))
                return
            self.apply_message(message)

        def apply_message(self, message):
            # A collapsed payload is a legacy request for the retired bubble
            # and is always a runtime hide.  Hidden visuals never carry chrome
            # and never publish an input region.
            message = dict(message)
            if message["mode"] == "collapsed" or not message["visible"]:
                message["visible"] = False
                message["mode"] = "expanded"
                message["chrome"] = False

            if message == self.message and message["visible"] == self.visible:
                return
            was_visible = self.visible
            self.message = message
            self.visible = message["visible"]

            # There is no interactive region in either state: the window is a
            # pure overlay plane, so hide/show only changes mapping/drawing.
            self.xoverlay.set_region(None)

            if self.visible:
                if not was_visible:
                    self.window.present()
                self.area.queue_draw()
                return

            if was_visible:
                # Paint one transparent frame before unmapping so the overlay
                # plane does not retain the previous image.
                self.area.queue_draw()
                GLib.timeout_add(100, self._hide_after_clear)
            elif self.window.get_visible():
                self.window.set_visible(False)

        def _hide_after_clear(self):
            if not self.visible and self.window is not None:
                self.window.set_visible(False)
            return GLib.SOURCE_REMOVE

        def _shutdown(self, _application):
            if self.watch_id:
                GLib.source_remove(self.watch_id)
                self.watch_id = 0
            self.xoverlay.close()

    application = OverlayApplication()
    application.connect("shutdown", application._shutdown)
    try:
        status = application.run([sys.argv[0]])
    finally:
        application._shutdown(application)
    if application.exit_code:
        return application.exit_code
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m py_modules.freedom_touch.overlay",
        description="Freedom Touch controller overlay",
    )
    parser.add_argument("--render", metavar="PATH", help="write a preview PNG")
    parser.add_argument("--state", metavar="PATH", help="message JSON for preview")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument(
        "--backdrop",
        metavar="HEX",
        type=_backdrop_color,
        help="opaque #RRGGBB background for --render (default: transparent)",
    )
    args = parser.parse_args(argv)

    if args.width <= 0 or args.height <= 0:
        parser.error("width and height must be positive")
    if args.state and not args.render:
        parser.error("--state requires --render")
    if args.backdrop is not None and not args.render:
        parser.error("--backdrop requires --render")

    if args.render:
        try:
            render_preview(
                args.render,
                args.width,
                args.height,
                args.state,
                backdrop=args.backdrop,
            )
        except (OSError, ValueError, cairo.Error) as exc:
            _log("preview failed: %s" % (exc,))
            return 2
        return 0
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
