"""Hardware boundaries for the Freedom touch controller.

This module stays importable without python-evdev or PyGObject.  ``evdev`` is
imported only by :func:`create_source` and ``gi`` only when the real
InputPlumber bridge is used.  Tests and the pure engine can therefore run on
any machine.
"""

import math
import xml.etree.ElementTree as _ElementTree

# Mirrors runtime_policy.COMPOSITE_IFACE.  Kept local so importing this module
# does not depend on how the plugin package root is placed on sys.path.
COMPOSITE_IFACE = "org.shadowblip.Input.CompositeDevice"

# Linux input constants.  Keeping the used values local avoids importing
# python-evdev at module import time.
EV_KEY = 1
EV_ABS = 3

ABS_X = 0
ABS_Y = 1
ABS_Z = 2
ABS_RX = 3
ABS_RY = 4
ABS_RZ = 5
ABS_HAT0X = 16
ABS_HAT0Y = 17

BTN_SOUTH = 304
BTN_EAST = 305
BTN_NORTH = 307
BTN_WEST = 308
BTN_TL = 310
BTN_TR = 311
BTN_SELECT = 314
BTN_START = 315
BTN_MODE = 316
BTN_THUMBL = 317
BTN_THUMBR = 318

SOURCE_NAME = "Freedom Touch Controller Source"
SOURCE_BUS_USB = 3
SOURCE_VENDOR = 0x0000
SOURCE_PRODUCT = 0x4654
SOURCE_VERSION = 1

QAM_CHORD = "Gamepad:Button:QuickAccess"
GUIDE_CHORD = "Gamepad:Button:Guide"

PAD_MOTION_CAPS = {
    "left": "Touchpad:LeftPad:Motion",
    "right": "Touchpad:RightPad:Motion",
}
PAD_PRESS_CHORDS = {
    "left": "Touchpad:LeftPad:Button:Press",
    "right": "Touchpad:RightPad:Button:Press",
}

# ABXY follow the physical layout, not the Deck label order.  The evdev codes
# name positions (BTN_SOUTH/EAST/NORTH/WEST), and InputPlumber maps them one
# to one (evdev.rs: BTN_NORTH => North, BTN_WEST => West).  On the default
# layout X sits on the west and Y on the north, so X is 308 and Y is 307.
_BUTTON_CODES = {
    "a": BTN_SOUTH,
    "b": BTN_EAST,
    "x": BTN_WEST,
    "y": BTN_NORTH,
    "lb": BTN_TL,
    "rb": BTN_TR,
    "back": BTN_SELECT,
    "start": BTN_START,
    "guide": BTN_MODE,
    "l3": BTN_THUMBL,
    "r3": BTN_THUMBR,
}
_TRIGGER_AXES = {"lt": ABS_Z, "rt": ABS_RZ}
_STICK_AXES = {"lx": ABS_X, "ly": ABS_Y, "rx": ABS_RX, "ry": ABS_RY}
_DPAD_AXES = {"dx": ABS_HAT0X, "dy": ABS_HAT0Y}

_STICK_MAX = 32767


def _clamp(value, low, high):
    return min(high, max(low, value))


def _float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


class InputSink:
    """Translate engine output tuples into uinput and InputPlumber calls.

    ``device`` exposes ``write(type, code, value)``, ``syn()`` and ``close()``.
    ``bridge`` exposes ``send_touch``, ``send_event`` and ``send_chord``.  The
    two objects are intentionally injected so this class can be tested with
    plain fakes.
    """

    def __init__(self, device, bridge):
        self.device = device
        self.bridge = bridge
        self._buttons = set()
        self._axes = {}
        self._pads = {}

    def emit(self, events):
        """Apply one engine frame and emit at most one uinput SYN.

        Writes are coalesced by ``(event_type, code)`` so repeated changes in a
        frame collapse to the final value.  Values identical to the last
        flushed state are not written.
        """

        pending = {}
        for event in events or ():
            try:
                kind = event[0]
            except (TypeError, IndexError):
                continue

            if kind == "button":
                if len(event) < 3:
                    continue
                self._button_event(event[1], bool(event[2]), pending)
            elif kind == "axis":
                if len(event) < 3:
                    continue
                self._axis_event(event[1], event[2], pending)
            elif kind == "pad":
                if len(event) < 3:
                    continue
                self._pad_event(event[1], event[2])
            elif kind == "pad_press":
                if len(event) < 3 or not event[2]:
                    # The native chord owns its release; never send a second
                    # "release" event.
                    continue
                chord = PAD_PRESS_CHORDS.get(event[1])
                if chord is not None:
                    self.bridge.send_chord([chord])

        if pending:
            self._flush(pending)

    def _button_event(self, output, pressed, pending):
        if output == "qam":
            if pressed:
                self.bridge.send_chord([QAM_CHORD])
            return

        trigger = _TRIGGER_AXES.get(output)
        if trigger is not None:
            pending[(EV_ABS, trigger)] = 255 if pressed else 0
            return

        code = _BUTTON_CODES.get(output)
        if code is not None:
            pending[(EV_KEY, code)] = 1 if pressed else 0

    def _axis_event(self, name, value, pending):
        number = _float(value)
        if number is None:
            return
        code = _STICK_AXES.get(name)
        if code is not None:
            scaled = int(round(_clamp(number, -1.0, 1.0) * _STICK_MAX))
        else:
            code = _DPAD_AXES.get(name)
            if code is None:
                return
            scaled = int(round(_clamp(number, -1.0, 1.0)))
        pending[(EV_ABS, code)] = scaled

    def _pad_event(self, side, payload):
        cap = PAD_MOTION_CAPS.get(side)
        if cap is None or not isinstance(payload, dict):
            return

        touching = bool(payload.get("touching", False))
        pressure = _float(payload.get("pressure", 0.0))
        x = _float(payload.get("x"))
        y = _float(payload.get("y"))
        if pressure is None or x is None or y is None:
            return
        pressure = _clamp(pressure, 0.0, 1.0)
        x = _clamp(x, 0.0, 1.0)
        y = _clamp(y, 0.0, 1.0)

        if touching:
            self._pads[side] = (x, y)
        self._send_touch(cap, touching, pressure, x, y)
        if not touching:
            # Only forget the contact once the bridge accepted the release;
            # a failed release stays recorded so neutralize() can retry it.
            self._pads.pop(side, None)

    def _send_touch(self, cap, touching, pressure, x, y):
        """Send one contact and treat an explicit ``False`` reply as failure.

        ``None`` keeps the older test-double contract and counts as success.
        A raised exception propagates unchanged.  Either way the runtime sees
        a failure and takes its existing device-recovery path.
        """

        if self.bridge.send_touch(cap, 0, touching, pressure, x, y) is False:
            raise RuntimeError("touch send failed for %s" % (cap,))

    def _flush(self, pending):
        changed = False
        for (event_type, code), value in pending.items():
            if event_type == EV_KEY:
                current = 1 if code in self._buttons else 0
            else:
                current = self._axes.get(code, 0)
            if current == value:
                continue

            self.device.write(event_type, code, value)
            changed = True
            if event_type == EV_KEY:
                if value:
                    self._buttons.add(code)
                else:
                    self._buttons.discard(code)
            elif value:
                self._axes[code] = value
            else:
                self._axes.pop(code, None)

        if changed:
            self.device.syn()

    def neutralize(self):
        """Release every held control and return to a neutral state.

        The method is idempotent: once state is clear it performs no writes,
        bridge calls, or SYN.  A contact is only forgotten after the bridge
        accepted its release, so a failed release is retried by the next
        call.  Buttons and axes are released even when a pad release fails;
        the pad failure is reported afterwards, as ``RuntimeError`` for an
        explicit ``False`` or unchanged for a raised bridge exception, so the
        runtime can recover.
        """

        failure = None
        for side in ("left", "right"):
            position = self._pads.get(side)
            if position is None:
                continue
            try:
                self._send_touch(
                    PAD_MOTION_CAPS[side],
                    False,
                    0.0,
                    position[0],
                    position[1],
                )
            except Exception as exc:
                if failure is None:
                    failure = exc
            else:
                self._pads.pop(side, None)

        pending = {}
        for code in sorted(self._buttons):
            pending[(EV_KEY, code)] = 0
        for code in sorted(self._axes):
            pending[(EV_ABS, code)] = 0

        if pending:
            self._flush(pending)
        self._buttons.clear()
        self._axes.clear()

        if failure is not None:
            # An explicit False already surfaced as RuntimeError; a bridge
            # exception is re-raised unchanged.  Either one reaches the
            # runtime's existing device-recovery path.
            raise failure

    def close(self):
        try:
            self.neutralize()
        finally:
            self.device.close()


def create_source():
    """Create the dedicated uinput gamepad and return ``(ui, device_path)``.

    The path comes from ``ui.device.path`` and is never guessed.  Imports are
    local so this module stays hardware-free.  If the event node cannot be
    resolved, the just-created uinput is closed before the original exception
    propagates so failed attempts do not leak virtual devices.
    """

    import evdev
    from evdev import AbsInfo, UInput

    ecodes = evdev.ecodes

    def axis_range(low, high):
        return AbsInfo(
            value=0, min=low, max=high, fuzz=0, flat=0, resolution=0
        )

    capabilities = {
        ecodes.EV_KEY: [
            ecodes.BTN_SOUTH,
            ecodes.BTN_EAST,
            ecodes.BTN_NORTH,
            ecodes.BTN_WEST,
            ecodes.BTN_TL,
            ecodes.BTN_TR,
            ecodes.BTN_SELECT,
            ecodes.BTN_START,
            ecodes.BTN_MODE,
            ecodes.BTN_THUMBL,
            ecodes.BTN_THUMBR,
        ],
        ecodes.EV_ABS: [
            (ecodes.ABS_X, axis_range(-_STICK_MAX, _STICK_MAX)),
            (ecodes.ABS_Y, axis_range(-_STICK_MAX, _STICK_MAX)),
            (ecodes.ABS_RX, axis_range(-_STICK_MAX, _STICK_MAX)),
            (ecodes.ABS_RY, axis_range(-_STICK_MAX, _STICK_MAX)),
            (ecodes.ABS_Z, axis_range(0, 255)),
            (ecodes.ABS_RZ, axis_range(0, 255)),
            (ecodes.ABS_HAT0X, axis_range(-1, 1)),
            (ecodes.ABS_HAT0Y, axis_range(-1, 1)),
        ],
    }

    ui = UInput(
        capabilities,
        name=SOURCE_NAME,
        bustype=SOURCE_BUS_USB,
        vendor=SOURCE_VENDOR,
        product=SOURCE_PRODUCT,
        version=SOURCE_VERSION,
    )
    try:
        device = ui.device
        if device is None:
            raise RuntimeError(
                "source event node unavailable: uinput device was not created"
            )
        return ui, device.path
    except Exception:
        try:
            ui.close()
        except Exception:
            pass
        raise


class InputPlumberBridge:
    """Minimal synchronous client for our own InputPlumber composite.

    ``bus`` may be injected for tests.  Real use discovers the composite that
    InputPlumber created for our exact source path; there is deliberately no
    fallback to the first target or to composite creation.
    """

    SERVICE = "org.shadowblip.InputPlumber"
    ROOT_PATH = "/org/shadowblip/InputPlumber"
    OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
    PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
    TARGET_IFACE = "org.shadowblip.Input.Target"
    TARGET_DEVICE_TYPE = "deck-uhid"
    MAX_TIMEOUT_MS = 1000

    def __init__(self, bus=None, timeout_ms=MAX_TIMEOUT_MS):
        try:
            timeout = int(timeout_ms)
        except (TypeError, ValueError):
            timeout = self.MAX_TIMEOUT_MS
        if timeout <= 0 or timeout > self.MAX_TIMEOUT_MS:
            timeout = self.MAX_TIMEOUT_MS

        self._bus = bus
        self._timeout = timeout
        self._own_path = None

    @property
    def own_path(self):
        return self._own_path

    @staticmethod
    def _gio():
        # Lazy so importing this module never requires PyGObject.
        from gi.repository import Gio, GLib

        return Gio, GLib

    def _bus_handle(self):
        if self._bus is None:
            Gio, _ = self._gio()
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._bus

    def discover(self, source_path):
        """Find and validate our composite.  Return ``(ok, detail)``.

        On any failure the cached own path is cleared, so later sends cannot
        hit a stale or unrelated composite.
        """

        self._own_path = None
        if not isinstance(source_path, str) or not source_path:
            return False, "source path must be a non-empty string"

        Gio, GLib = self._gio()
        try:
            reply = self._bus_handle().call_sync(
                self.SERVICE,
                self.ROOT_PATH,
                self.OBJECT_MANAGER_IFACE,
                "GetManagedObjects",
                None,
                GLib.VariantType.new("(a{oa{sa{sv}}})"),
                Gio.DBusCallFlags.NONE,
                self._timeout,
                None,
            )
            objects = reply.unpack()[0]
        except (GLib.Error, AttributeError, IndexError, TypeError) as exc:
            return False, "GetManagedObjects failed: %s" % (exc,)

        try:
            from .runtime_policy import choose_composite
        except ImportError as exc:
            return False, "composite selector unavailable: %s" % (exc,)

        path = choose_composite(objects, source_path)
        if path is None:
            return False, "no unique composite for %s" % (source_path,)

        interfaces = objects.get(path, {})
        if not isinstance(interfaces, dict):
            return False, "composite properties unavailable"
        composite = interfaces.get(COMPOSITE_IFACE)
        if not isinstance(composite, dict):
            return False, "composite interface unavailable"

        targets = composite.get("TargetDevices")
        if not isinstance(targets, (list, tuple)) or not targets:
            return False, "composite has no target devices"
        if not self._has_target_type(targets):
            return False, "composite has no %s target" % (
                self.TARGET_DEVICE_TYPE,
            )

        xml = self._introspect(path)
        if not _has_method(xml, "SendTouchEvent"):
            return False, "composite lacks SendTouchEvent"

        self._own_path = path
        return True, ""

    def _has_target_type(self, targets):
        for target in targets:
            if not isinstance(target, str):
                continue
            value = self._get_property(
                target, self.TARGET_IFACE, "DeviceType"
            )
            if value == self.TARGET_DEVICE_TYPE:
                return True
        return False

    def _get_property(self, path, interface, name):
        Gio, GLib = self._gio()
        try:
            reply = self._bus_handle().call_sync(
                self.SERVICE,
                path,
                self.PROPERTIES_IFACE,
                "Get",
                GLib.Variant("(ss)", (interface, name)),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                self._timeout,
                None,
            )
            return reply.unpack()[0]
        except (GLib.Error, AttributeError, IndexError, TypeError):
            return None

    def _introspect(self, path):
        Gio, GLib = self._gio()
        try:
            reply = self._bus_handle().call_sync(
                self.SERVICE,
                path,
                "org.freedesktop.DBus.Introspectable",
                "Introspect",
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                self._timeout,
                None,
            )
            return reply.unpack()[0]
        except (GLib.Error, AttributeError, IndexError, TypeError):
            return None

    def send_touch(self, cap, index, touching, pressure, x, y):
        if self._own_path is None:
            return False
        _, GLib = self._gio()
        parameters = GLib.Variant(
            "(subddd)",
            (
                str(cap),
                int(index),
                bool(touching),
                float(pressure),
                float(x),
                float(y),
            ),
        )
        return self._call("SendTouchEvent", parameters)

    def send_event(self, cap, pressed):
        if self._own_path is None:
            return False
        _, GLib = self._gio()
        parameters = GLib.Variant(
            "(sv)", (str(cap), GLib.Variant("b", bool(pressed)))
        )
        return self._call("SendEvent", parameters)

    def send_chord(self, caps):
        if self._own_path is None:
            return False
        _, GLib = self._gio()
        parameters = GLib.Variant("(as)", ([str(cap) for cap in caps],))
        return self._call("SendButtonChord", parameters)

    def _call(self, method, parameters):
        path = self._own_path
        if path is None:
            return False

        Gio, GLib = self._gio()
        try:
            self._bus_handle().call_sync(
                self.SERVICE,
                path,
                COMPOSITE_IFACE,
                method,
                parameters,
                None,
                Gio.DBusCallFlags.NONE,
                self._timeout,
                None,
            )
            return True
        except (GLib.Error, AttributeError, TypeError):
            return False


def _has_method(xml, name):
    """Return True when introspection XML exposes *name* on the composite."""

    if not isinstance(xml, str):
        return False
    try:
        root = _ElementTree.fromstring(xml)
    except _ElementTree.ParseError:
        return False

    for interface in root.findall("interface"):
        if interface.get("name") != COMPOSITE_IFACE:
            continue
        for method in interface.findall("method"):
            if method.get("name") == name:
                return True
    return False
