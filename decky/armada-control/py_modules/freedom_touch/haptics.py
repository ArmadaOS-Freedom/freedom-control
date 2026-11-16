"""Native tablet haptics for the Freedom touch controller.

The TB323FU exposes two AW86937 drivers on I2C addresses 0x5a (right) and
0x5b (left).  The kernel input nodes are named ``aw86927-haptics`` and accept
FF_RUMBLE effects even though the module identifies the chip as a 9370.

This adapter is deliberately independent of the controller path: it only
consumes the engine's already-produced events, never raises into the caller,
and never grabs or configures the device beyond uploading its own effect.

``evdev`` is imported lazily so this module can be imported on a machine
without the library or without any haptic hardware.
"""

import math
import os
import posixpath
import re

EV_FF = 21
FF_RUMBLE = 0x50

DEVICE_NAME = "aw86927-haptics"
SIDE_ADDRESSES = {"5a": "right", "5b": "left"}
SIDES = ("left", "right")

EFFECT_LENGTH_MS = 16
EFFECT_DELAY_MS = 0
# 0x4000 is 25% of the 0xffff FF magnitude scale: a short, modest tick.
EFFECT_MAGNITUDE = 0x4000
DEFAULT_STRENGTH_PERCENT = 25
MAX_MAGNITUDE = 0xFFFF

PAD_TICK_DISTANCE = 0.025
PAD_TICK_INTERVAL = 0.045

AVAILABLE_DETAIL = "机身振动已就绪"
UNAVAILABLE_DETAIL = "机身振动暂不可用"

_EPSILON = 1e-9
_ADDRESS_PATTERN = re.compile(r"(?:^|[/-])0*(5[ab])(?:$|[/-])", re.IGNORECASE)


def _default_list_devices():
    import evdev

    return list(evdev.list_devices())


def _default_device_factory(path):
    from evdev import InputDevice

    return InputDevice(path)


def _magnitude(percent):
    """Return the FF magnitude for an integer 0..100 percent."""

    return int(round(MAX_MAGNITUDE * percent / 100.0))


def _build_effect(magnitude):
    """Build a real ``ff.Effect`` for one short FF_RUMBLE tick.

    ``python-evdev`` expects the kernel ``struct ff_effect`` layout:
    ``type`` is the effect kind (FF_RUMBLE, not EV_FF), ``id`` must be -1 so
    the kernel assigns one on upload, and the rumble magnitudes live in the
    ``u.ff_rumble_effect`` union member.
    """

    from evdev import ff

    return ff.Effect(
        FF_RUMBLE,
        -1,
        0,
        ff.Trigger(0, 0),
        ff.Replay(EFFECT_LENGTH_MS, EFFECT_DELAY_MS),
        ff.EffectType(
            ff_rumble_effect=ff.Rumble(magnitude, magnitude)
        ),
    )


def _coordinate(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _has_rumble(device):
    try:
        capabilities = device.capabilities()
    except Exception:
        return False
    if not isinstance(capabilities, dict):
        return False
    try:
        codes = capabilities.get(EV_FF) or ()
        return FF_RUMBLE in codes
    except TypeError:
        return False


def _safe_close(device):
    close = getattr(device, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        pass


def _upload_effect(device, magnitude):
    effect = _build_effect(magnitude)
    return device.upload_effect(effect)


def _send_ff(device, effect_id, value):
    device.write(EV_FF, effect_id, value)


def _erase_effect(device, effect_id):
    device.erase_effect(effect_id)


def _teardown(node):
    """Best-effort stop, erase and close for one owned node."""

    effect_id = node.effect_id
    node.effect_id = None

    if effect_id is not None:
        try:
            _send_ff(node.device, effect_id, 0)
        except Exception:
            pass
        try:
            _erase_effect(node.device, effect_id)
        except Exception:
            pass

    _safe_close(node.device)


class _Node:
    __slots__ = ("device", "path", "effect_id")

    def __init__(self, device, path):
        self.device = device
        self.path = path
        self.effect_id = None


class Haptics:
    """Best-effort native tablet rumble driven by controller events."""

    def __init__(
        self,
        enabled=True,
        *,
        device_factory=None,
        list_devices=None,
        realpath=None,
    ):
        self._enabled = bool(enabled)
        self._strength = DEFAULT_STRENGTH_PERCENT
        self._device_factory = device_factory or _default_device_factory
        self._list_devices = list_devices or _default_list_devices
        self._realpath = realpath or os.path.realpath

        self._nodes = {}
        self._pad = {}
        self._last_pulse = {}

    # -- public API ------------------------------------------------------------

    @property
    def available(self):
        return bool(self._nodes)

    @property
    def detail(self):
        return AVAILABLE_DETAIL if self._nodes else UNAVAILABLE_DETAIL

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop()

    def set_strength(self, percent):
        """Change the tick magnitude, invalidating any uploaded effect.

        The physical devices stay open; only this adapter's effects are
        stopped and erased, so the next pulse uploads at the new magnitude.
        """

        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            return False
        try:
            number = float(percent)
        except (OverflowError, TypeError, ValueError):
            return False
        if not math.isfinite(number) or number != int(number):
            return False
        value = int(number)
        if not 0 <= value <= 100:
            return False
        if value == self._strength:
            return False

        self._strength = value
        self._invalidate_effects()
        return True

    def preview(self, now):
        """Play one tick on both physical sides, outside the input stream."""

        if not self._enabled or self._strength <= 0:
            return
        try:
            now = float(now)
        except (TypeError, ValueError):
            return
        if not math.isfinite(now):
            return
        pulsed = set()
        self._pulse("left", now, pulsed)
        self._pulse("right", now, pulsed)

    def refresh(self, now=None):
        """Rediscover the two physical nodes; the only retry path."""

        try:
            self._discover()
        except Exception:
            # Discovery is advisory and must never disturb the controller.
            return

    def consume(self, events, now):
        """Play a short tick for the supplied engine events.

        Only ``button`` presses, first pad contact, pad taps, and sufficiently
        spacing pad motion produce a tick.  Releases are silent.
        """

        if not self._enabled:
            return
        try:
            now = float(now)
        except (TypeError, ValueError):
            return
        if not math.isfinite(now):
            return
        if events is None:
            return
        try:
            iterator = iter(events)
        except TypeError:
            return

        pulsed = set()
        try:
            for event in iterator:
                self._consume_event(event, now, pulsed)
        except Exception:
            # Haptics is best-effort; controller input must keep flowing.
            return

    def stop(self):
        """Silence every effect this adapter owns and clear gesture state."""

        for side, node in list(self._nodes.items()):
            if node.effect_id is None:
                continue
            try:
                _send_ff(node.device, node.effect_id, 0)
            except Exception:
                self._discard(side, node)

        self._pad.clear()
        self._last_pulse.clear()

    def _invalidate_effects(self):
        """Stop and erase owned effects while keeping every device open."""

        for node in self._nodes.values():
            effect_id = node.effect_id
            node.effect_id = None
            if effect_id is None:
                continue
            try:
                _send_ff(node.device, effect_id, 0)
            except Exception:
                pass
            try:
                _erase_effect(node.device, effect_id)
            except Exception:
                pass

        self._pad.clear()
        self._last_pulse.clear()

    def close(self):
        """Stop, erase and close every owned node, each step best-effort."""

        for side, node in list(self._nodes.items()):
            self._nodes.pop(side, None)
            _teardown(node)

        self._pad.clear()
        self._last_pulse.clear()

    # -- discovery -------------------------------------------------------------

    def _discover(self):
        if len(self._nodes) >= len(SIDES):
            return

        try:
            items = list(self._list_devices())
        except Exception:
            return

        seen = set()
        for item in items:
            if len(self._nodes) >= len(SIDES):
                break

            path = item if isinstance(item, str) else getattr(item, "path", None)
            if not isinstance(path, str) or not path or path in seen:
                continue
            seen.add(path)

            device = None
            try:
                device = self._device_factory(path)
                if getattr(device, "name", None) != DEVICE_NAME:
                    continue
                if not _has_rumble(device):
                    continue

                side = self._side_for(path)
                if side is None or side in self._nodes:
                    continue

                self._nodes[side] = _Node(device, path)
                device = None
            except Exception:
                continue
            finally:
                if device is not None:
                    _safe_close(device)

    def _side_for(self, path):
        basename = posixpath.basename(path)
        sysfs = "/sys/class/input/%s/device" % (basename,)
        try:
            resolved = self._realpath(sysfs)
        except Exception:
            return None
        if not isinstance(resolved, str) or not resolved:
            return None

        normalized = "/" + resolved.replace("\\", "/").strip("/") + "/"
        if "/devices/virtual/" in normalized:
            return None

        matches = _ADDRESS_PATTERN.findall(normalized)
        if not matches:
            return None
        return SIDE_ADDRESSES.get(matches[-1].lower())

    # -- playback --------------------------------------------------------------

    def _consume_event(self, event, now, pulsed):
        if not isinstance(event, (tuple, list)) or len(event) < 2:
            return

        kind = event[0]
        if kind == "button":
            if len(event) >= 3 and event[2] is True:
                self._pulse("left", now, pulsed)
                self._pulse("right", now, pulsed)
            return

        if kind == "pad":
            if len(event) < 3:
                return
            side = event[1]
            payload = event[2]
            if side not in SIDES or not isinstance(payload, dict):
                return

            if not payload.get("touching"):
                self._pad.pop(side, None)
                return

            x = _coordinate(payload.get("x"))
            y = _coordinate(payload.get("y"))
            if x is None or y is None:
                return

            anchor = self._pad.get(side)
            if anchor is None:
                self._pad[side] = (x, y)
                self._pulse(side, now, pulsed)
                return

            distance = math.hypot(x - anchor[0], y - anchor[1])
            if distance + _EPSILON < PAD_TICK_DISTANCE:
                return

            last = self._last_pulse.get(side)
            if last is not None and now - last + _EPSILON < PAD_TICK_INTERVAL:
                # Keep the old anchor so slow movement can accumulate.
                return

            self._pad[side] = (x, y)
            self._pulse(side, now, pulsed)
            return

        if kind == "pad_press":
            if (
                len(event) >= 3
                and event[2] is True
                and event[1] in SIDES
            ):
                self._pulse(event[1], now, pulsed)

    def _pulse(self, side, now, pulsed):
        if not self._enabled or self._strength <= 0:
            return
        if side in pulsed:
            return
        pulsed.add(side)
        self._last_pulse[side] = now

        node = self._nodes.get(side)
        if node is None:
            return

        try:
            effect_id = node.effect_id
            if effect_id is None:
                effect_id = _upload_effect(
                    node.device, _magnitude(self._strength)
                )
                if effect_id is None:
                    return
                node.effect_id = effect_id
            _send_ff(node.device, effect_id, 1)
        except Exception:
            self._discard(side, node)

    def _discard(self, side, node):
        if self._nodes.get(side) is not node:
            return
        self._nodes.pop(side, None)
        _teardown(node)
