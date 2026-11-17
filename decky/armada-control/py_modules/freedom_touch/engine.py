"""Stateful, hardware-free multi-touch controller engine."""

import math

from . import layout as _layout

TAP_MAX_TIME = 0.25
TAP_MAX_MOVEMENT = 0.04
EDGE_ZONE = 0.07
EDGE_MIN_TRAVEL = 0.08
EDGE_HORIZONTAL_FACTOR = 2.0
_EPSILON = 1e-9
DPAD_MODES = ("four_way", "eight_way")
DEFAULT_DPAD_MODE = "four_way"
_DPAD_SECTORS = {
    0: (1, 0),
    1: (1, 1),
    2: (0, 1),
    3: (-1, 1),
    4: (-1, 0),
    -1: (1, -1),
    -2: (0, -1),
    -3: (-1, -1),
    -4: (-1, 0),
}


def _clamp01(value):
    return min(1.0, max(0.0, value))


class TouchEngine:
    """Translate touch points into controller events.

    The engine does not know about X11, evdev, or uinput.  Callers feed touch
    coordinates and forward the returned tuples to their adapter.
    """

    def __init__(
        self,
        layout,
        deadzone=0.1,
        edge_swipes=False,
        aspect_ratio=1.0,
        dpad_mode=DEFAULT_DPAD_MODE,
        right_stick_fullscreen=False,
    ):
        self.controls = _layout.validate_layout(layout)
        try:
            deadzone = float(deadzone)
        except (TypeError, ValueError) as exc:
            raise ValueError("deadzone must be numeric") from exc
        if not math.isfinite(deadzone) or deadzone < 0.0 or deadzone >= 1.0:
            raise ValueError("deadzone must be in [0, 1)")
        if not isinstance(dpad_mode, str) or dpad_mode not in DPAD_MODES:
            raise ValueError(
                "dpad_mode must be one of: %s" % (", ".join(DPAD_MODES),)
            )
        if not isinstance(right_stick_fullscreen, bool):
            raise ValueError("right_stick_fullscreen must be a boolean")

        self.deadzone = deadzone
        self.edge_swipes = bool(edge_swipes)
        self.aspect_ratio = self._valid_aspect(aspect_ratio)
        self.dpad_mode = dpad_mode
        self.right_stick_fullscreen = right_stick_fullscreen
        self._look_owner = None
        self.active = False
        self._pointers = {}
        self._owners = {}
        self._refs = {}

    # -- input -----------------------------------------------------------------

    def begin(self, pointer_id, x, y, now):
        if not self.active:
            return []
        try:
            duplicate = pointer_id in self._pointers
        except TypeError:
            return []

        events = []
        if duplicate:
            # A pointer can only own one control.  Drop the old ownership
            # without generating a tap before starting a new gesture.
            events.extend(self._release(pointer_id, allow_tap=False))

        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            return events
        if not (math.isfinite(x) and math.isfinite(y)):
            return events

        control = self._hit(x, y)
        if control is None:
            if self.edge_swipes:
                side = self._edge_side(x)
                if side is not None:
                    self._pointers[pointer_id] = {
                        "kind": "edge",
                        "side": side,
                        "start_x": x,
                        "start_y": y,
                        "fired": False,
                        "rejected": False,
                    }
                    return events
            if self.right_stick_fullscreen and self._look_owner is None:
                try:
                    now = float(now)
                except (TypeError, ValueError):
                    return events
                if not math.isfinite(now):
                    return events
                self._look_owner = {
                    "pointer_id": pointer_id,
                    "x": x,
                    "y": y,
                    "time": now,
                    "last_motion": now,
                    "moving": False,
                }
                self._pointers[pointer_id] = {"kind": "look"}
            return events

        kind = control["kind"]
        if kind == "button":
            self._pointers[pointer_id] = {"kind": kind, "control": control}
            output = control["output"]
            count = self._refs.get(output, 0)
            self._refs[output] = count + 1
            if count == 0:
                events.append(("button", output, True))
            return events

        control_id = control["id"]
        if control_id in self._owners:
            # Analog controls already have an owner; a second finger must not
            # steal or reset the gesture.
            return events

        local = self._local(control, x, y)
        record = {
            "kind": kind,
            "control": control,
            "local": local,
            "start_local": local,
            "start_time": float(now),
            "max_move": 0.0,
        }
        if kind == "pad":
            # Movement is measured in unclamped gesture coordinates; the
            # emitted payload stays inside 0..1.
            raw = self._local_raw(control, x, y)
            record["raw"] = raw
            record["start_raw"] = raw
            record["outside"] = False
        self._owners[control_id] = pointer_id
        self._pointers[pointer_id] = record

        if kind == "stick":
            first, second = self._stick_axis_names(control)
            ax, ay = self._stick_axes(control, x, y)
            events.extend((("axis", first, ax), ("axis", second, ay)))
        elif kind == "dpad":
            dx, dy = self._dpad_axes(control, x, y)
            record["dpad"] = (dx, dy)
            events.extend((("axis", "dx", dx), ("axis", "dy", dy)))
        else:
            events.append(
                ("pad", control["output"], self._pad_payload(local, True))
            )
        return events

    def move(self, pointer_id, x, y, now):
        if not self.active:
            return []
        record = self._pointers.get(pointer_id)
        if record is None:
            return []

        kind = record["kind"]
        if kind == "button":
            # Hit testing happens only on begin; moving off a button does not
            # release it.
            return []
        if kind == "look":
            owner = self._look_owner
            if owner is None or owner["pointer_id"] != pointer_id:
                return []
            try:
                x = float(x)
                y = float(y)
                now = float(now)
            except (TypeError, ValueError):
                return []
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(now)):
                return []
            if now < owner["time"]:
                return []
            dt = max(now - owner["time"], 1.0 / 120.0)
            nx = (x - owner["x"]) / dt / 2.0
            ny = (y - owner["y"]) / self.aspect_ratio / dt / 2.0
            mag = math.hypot(nx, ny)
            if mag <= self.deadzone:
                rx = 0.0
                ry = 0.0
            else:
                scale = min(1.0, (mag - self.deadzone) / (1.0 - self.deadzone))
                rx = nx / mag * scale
                ry = ny / mag * scale
            owner["x"] = x
            owner["y"] = y
            owner["time"] = now
            owner["last_motion"] = now
            owner["moving"] = (rx != 0.0 or ry != 0.0)
            return [("axis", "rx", rx), ("axis", "ry", ry)]
        if kind == "edge":
            # Menu gestures are recognized from movement only; a begin inside
            # an otherwise empty edge zone never emits anything by itself.
            try:
                x = float(x)
                y = float(y)
            except (TypeError, ValueError):
                return []
            if not (math.isfinite(x) and math.isfinite(y)):
                return []
            return self._edge_move(record, x, y)

        control = record["control"]

        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            return []
        if not (math.isfinite(x) and math.isfinite(y)):
            return []

        local = self._local(control, x, y)
        record["local"] = local

        if kind == "stick":
            first, second = self._stick_axis_names(control)
            ax, ay = self._stick_axes(control, x, y)
            return [("axis", first, ax), ("axis", second, ay)]

        if kind == "dpad":
            dx, dy = self._dpad_axes(control, x, y)
            record["dpad"] = (dx, dy)
            return [("axis", "dx", dx), ("axis", "dy", dy)]

        raw = self._local_raw(control, x, y)
        record["raw"] = raw
        if not self._inside(control, x, y):
            # Once a pad gesture leaves its rectangle it is a drag, even if it
            # comes back before release.
            record["outside"] = True
        start = record["start_raw"]
        distance = math.hypot(raw[0] - start[0], raw[1] - start[1])
        record["max_move"] = max(record["max_move"], distance)
        return [("pad", control["output"], self._pad_payload(raw, True))]

    def tick(self, now):
        if not self.active or not self.right_stick_fullscreen:
            return []
        try:
            now = float(now)
        except (TypeError, ValueError):
            return []
        if math.isnan(now) or math.isinf(now):
            return []
        owner = self._look_owner
        if owner is not None and owner.get('moving'):
            last = owner.get('last_motion')
            try:
                last = float(last)
            except (TypeError, ValueError):
                return []
            if now - last >= 0.05:
                owner['moving'] = False
                return [('axis', 'rx', 0.0), ('axis', 'ry', 0.0)]
        return []

    def end(self, pointer_id, now):
        if not self.active:
            return []
        if pointer_id not in self._pointers:
            return []
        return self._release(pointer_id, allow_tap=True, now=now)

    def cancel_all(self):
        if not self.active:
            return []
        events = []
        for pointer_id in list(self._pointers):
            events.extend(self._release(pointer_id, allow_tap=False))
        # These should already be empty; clearing them keeps a bad adapter from
        # leaving a stuck control behind.
        for output in list(self._refs):
            events.append(("button", output, False))
        self._refs.clear()
        self._owners.clear()
        if self._look_owner is not None:
            self._look_owner = None
            events.extend((("axis", "rx", 0.0), ("axis", "ry", 0.0)))
        return events

    def set_active(self, active):
        active = bool(active)
        if active == self.active:
            return []
        if not active:
            events = self.cancel_all()
            self.active = False
            return events
        self.active = True
        return []

    def set_aspect_ratio(self, aspect_ratio):
        """Update the screen aspect used by edge-swipe recognition."""

        self.aspect_ratio = self._valid_aspect(aspect_ratio)
        return self.aspect_ratio

    # -- rendering state -------------------------------------------------------

    def snapshot(self):
        """Return JSON-friendly current state for a renderer."""

        controls = {}
        for control in self.controls:
            control_id = control["id"]
            kind = control["kind"]
            pressed = False
            x = 0.5
            y = 0.5

            if kind == "button":
                pressed = self._refs.get(control["output"], 0) > 0
            else:
                pointer_id = self._owners.get(control_id)
                if pointer_id is not None:
                    pressed = True
                    record = self._pointers.get(pointer_id)
                    if record is not None and kind in ("stick", "pad"):
                        x, y = record["local"]
                    elif record is not None and kind == "dpad":
                        # Highlight the direction actually emitted, so the
                        # renderer never guesses from the finger position.
                        dx, dy = record.get("dpad", (0, 0))
                        x = 0.5 + 0.5 * dx
                        y = 0.5 + 0.5 * dy

            controls[control_id] = {
                "pressed": bool(pressed),
                "x": float(x),
                "y": float(y),
            }
        return {"active": bool(self.active), "controls": controls}

    # -- internals -------------------------------------------------------------

    def _release(self, pointer_id, allow_tap, now=None):
        record = self._pointers.pop(pointer_id, None)
        if record is None:
            return []

        kind = record["kind"]
        if kind == "look":
            owner = self._look_owner
            if owner is not None and owner["pointer_id"] == pointer_id:
                self._look_owner = None
                self._pointers.pop(pointer_id, None)
                return [("axis", "rx", 0.0), ("axis", "ry", 0.0)]
            return []
        if kind == "edge":
            # A menu gesture owns no control and therefore produces no pad
            # tap, release event, or neutralisation work.
            return []

        control = record["control"]
        control_id = control["id"]

        if kind == "button":
            output = control["output"]
            count = self._refs.get(output, 0) - 1
            if count > 0:
                self._refs[output] = count
                return []
            self._refs.pop(output, None)
            return [("button", output, False)]

        if self._owners.get(control_id) == pointer_id:
            self._owners.pop(control_id, None)

        if kind == "stick":
            first, second = self._stick_axis_names(control)
            return [("axis", first, 0.0), ("axis", second, 0.0)]

        if kind == "dpad":
            return [("axis", "dx", 0), ("axis", "dy", 0)]

        events = [
            ("pad", control["output"], self._pad_payload(record["local"], False))
        ]
        if allow_tap and now is not None:
            try:
                elapsed = float(now) - record["start_time"]
            except (TypeError, ValueError):
                elapsed = -1.0
            if (
                0.0 <= elapsed <= TAP_MAX_TIME + _EPSILON
                and record["max_move"] <= TAP_MAX_MOVEMENT + _EPSILON
                and not record.get("outside", False)
            ):
                events.append(("pad_press", control["output"], True))
                events.append(("pad_press", control["output"], False))
        return events

    @staticmethod
    def _valid_aspect(aspect_ratio):
        try:
            number = float(aspect_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError("aspect_ratio must be numeric") from exc
        if not math.isfinite(number) or number <= 0.0:
            raise ValueError("aspect_ratio must be positive")
        return number

    @staticmethod
    def _edge_side(x):
        if x <= EDGE_ZONE:
            return "left"
        if x >= 1.0 - EDGE_ZONE:
            return "right"
        return None

    def _edge_move(self, record, x, y):
        if record["fired"] or record["rejected"]:
            return []

        dx = x - record["start_x"]
        dy = y - record["start_y"]
        inward = dx if record["side"] == "left" else -dx

        # A substantially vertical move is a scroll.  Decide that from the
        # whole offset before the short-gesture early return, otherwise a pure
        # vertical drag would stay a candidate and a later sideways move would
        # open the menu.  The threshold is 8% of physical screen width; small
        # sideways jitter does not make the gesture horizontal.
        if (
            abs(dy) >= EDGE_MIN_TRAVEL * self.aspect_ratio
            and abs(dx) * self.aspect_ratio
            <= EDGE_HORIZONTAL_FACTOR * abs(dy)
        ):
            record["rejected"] = True
            return []

        if inward < 0.0:
            # Moving back toward (or beyond) the edge is never a menu gesture.
            record["rejected"] = True
            return []

        if inward + _EPSILON < EDGE_MIN_TRAVEL:
            return []

        if inward * self.aspect_ratio > EDGE_HORIZONTAL_FACTOR * abs(dy):
            record["fired"] = True
            side = "guide" if record["side"] == "left" else "qam"
            return [("menu", side)]

        if abs(dy) >= EDGE_MIN_TRAVEL:
            # A long vertical gesture is a scroll, not a menu swipe.  Reject it
            # for this pointer even if the finger later turns sideways.
            record["rejected"] = True
        return []

    def _project(self, control):
        if control.get("kind") != "stick":
            return control
        return _layout.circular_stick_rect(control, self.aspect_ratio)

    def _hit(self, x, y):
        # draw_controls paints every pad first and every other control after
        # it, each group in layout order.  Hit testing walks that stacking
        # order backwards, so a button drawn above a pad wins no matter where
        # either sits in the layout list.
        # Sticks are hit-tested against the same circular projection that is
        # drawn, but acceptance stays within the projected bounding rectangle.
        ordered = [c for c in self.controls if c["kind"] == "pad"]
        ordered.extend(c for c in self.controls if c["kind"] != "pad")

        for control in reversed(ordered):
            if (
                self.right_stick_fullscreen
                and control["kind"] == "stick"
                and control["output"] == "right"
            ):
                continue
            projected = self._project(control)
            if (
                projected["x"] <= x <= projected["x"] + projected["w"]
                and projected["y"] <= y <= projected["y"] + projected["h"]
            ):
                return projected
        return None

    @staticmethod
    def _local(control, x, y):
        return (
            _clamp01((x - control["x"]) / control["w"]),
            _clamp01((y - control["y"]) / control["h"]),
        )

    @staticmethod
    def _local_raw(control, x, y):
        return (
            (x - control["x"]) / control["w"],
            (y - control["y"]) / control["h"],
        )

    @staticmethod
    def _inside(control, x, y):
        return (
            control["x"] <= x <= control["x"] + control["w"]
            and control["y"] <= y <= control["y"] + control["h"]
        )

    @staticmethod
    def _stick_axis_names(control):
        if control["output"] == "right":
            return "rx", "ry"
        return "lx", "ly"

    def _stick_axes(self, control, x, y):
        # Normalize each axis by its own half-extent, then apply a radial
        # deadzone/clamp so diagonal magnitude stays bounded.  Projection is
        # idempotent so a control previously projected by _hit is unchanged.
        control = self._project(control)
        nx = (x - (control["x"] + control["w"] / 2.0)) / (control["w"] / 2.0)
        ny = (y - (control["y"] + control["h"] / 2.0)) / (control["h"] / 2.0)
        magnitude = math.hypot(nx, ny)
        if magnitude <= self.deadzone:
            return 0.0, 0.0
        scaled = min(
            1.0, (magnitude - self.deadzone) / (1.0 - self.deadzone)
        )
        return (nx / magnitude) * scaled, (ny / magnitude) * scaled

    def _dpad_axes(self, control, x, y):
        nx = (x - (control["x"] + control["w"] / 2.0)) / (control["w"] / 2.0)
        ny = (y - (control["y"] + control["h"] / 2.0)) / (control["h"] / 2.0)
        threshold = max(self.deadzone, 0.05)
        if math.hypot(nx, ny) <= threshold:
            return 0, 0

        if self.dpad_mode == "eight_way":
            # The eight sectors are centred on the cardinals and diagonals
            # (22.5 degrees half-width), so the radius never changes the mode.
            angle = math.atan2(ny, nx)
            sector = math.floor(angle / (math.pi / 4.0) + 0.5)
            return _DPAD_SECTORS[sector]

        # Four-way: the dominant normalized axis wins, with a tie going
        # vertical so a 45-degree corner still reports exactly one axis.
        if abs(ny) + _EPSILON >= abs(nx):
            return 0, 1 if ny > 0.0 else -1
        return (1 if nx > 0.0 else -1), 0

    @staticmethod
    def _pad_payload(local, touching):
        return {
            "touching": bool(touching),
            "x": _clamp01(float(local[0])),
            "y": _clamp01(float(local[1])),
            "pressure": 1.0 if touching else 0.0,
        }
