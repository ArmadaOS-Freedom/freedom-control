"""Normalized screen layout for the Freedom touch controller.

All rectangles are in landscape-oriented normalized screen coordinates; y grows
downward as it does for evdev touch events.
"""

import math

BUTTON_OUTPUTS = frozenset(
    {
        "a", "b", "x", "y",
        "lb", "rb", "lt", "rt",
        "start", "back", "guide", "qam",
        "l3", "r3",
    }
)
SIDE_OUTPUTS = frozenset({"left", "right"})
KINDS = frozenset({"button", "stick", "dpad", "pad"})

_REQUIRED_KEYS = ("id", "kind", "x", "y", "w", "h", "output")


def _entry(control_id, kind, x, y, w, h, output, label):
    return {
        "id": control_id,
        "kind": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "output": output,
        "label": label,
    }


def _tablet_standard():
    return [
        # Analog sticks: left lower-middle, right above the ABXY cluster.
        _entry("stick_l", "stick", 0.035, 0.580, 0.170, 0.300, "left", "左摇杆"),
        _entry("dpad_l", "dpad", 0.040, 0.240, 0.140, 0.230, "left", "十字键"),
        _entry("stick_r", "stick", 0.790, 0.240, 0.170, 0.280, "right", "右摇杆"),

        # ABXY diamond in the lower-right (Deck/Xbox arrangement):
        # Y top, X left, B right, A bottom.
        _entry("btn_y", "button", 0.855, 0.590, 0.065, 0.100, "y", "Y"),
        _entry("btn_x", "button", 0.785, 0.700, 0.065, 0.100, "x", "X"),
        _entry("btn_b", "button", 0.925, 0.700, 0.065, 0.100, "b", "B"),
        _entry("btn_a", "button", 0.855, 0.810, 0.065, 0.100, "a", "A"),

        # Native pads in the lower middle.
        _entry("pad_l", "pad", 0.280, 0.610, 0.190, 0.300, "left", "左触摸板"),
        _entry("pad_r", "pad", 0.530, 0.610, 0.190, 0.300, "right", "右触摸板"),

        # Shoulders across the top outer edges.  LB/RB stay outermost while
        # LT/RT sit inward, immediately beside the sidebars.
        _entry("btn_lb", "button", 0.020, 0.020, 0.100, 0.075, "lb", "LB"),
        _entry("btn_lt", "button", 0.130, 0.020, 0.100, 0.075, "lt", "LT"),
        _entry("btn_rt", "button", 0.770, 0.020, 0.100, 0.075, "rt", "RT"),
        _entry("btn_rb", "button", 0.880, 0.020, 0.100, 0.075, "rb", "RB"),

        # Sidebars sit immediately beside their shoulder buttons; the centre
        # pair stays between them.
        _entry("btn_guide", "button", 0.240, 0.020, 0.100, 0.075, "guide", "左侧栏"),
        _entry("btn_qam", "button", 0.660, 0.020, 0.100, 0.075, "qam", "右侧栏"),
        _entry("btn_back", "button", 0.440, 0.040, 0.050, 0.070, "back", "返回"),
        _entry("btn_start", "button", 0.510, 0.040, 0.050, 0.070, "start", "开始"),

        # Stick clicks sit below the analog/pad clusters so their centres stay
        # hittable even at the maximum UI scale.
        _entry("btn_l3", "button", 0.090, 0.930, 0.070, 0.060, "l3", "L3"),
        _entry("btn_r3", "button", 0.770, 0.930, 0.070, 0.060, "r3", "R3"),
    ]


def _affine(layout, factor):
    """Scale every rectangle about the screen centre without mutating input."""

    result = []
    for rect in layout:
        item = dict(rect)
        item["x"] = 0.5 + (float(rect["x"]) - 0.5) * factor
        item["y"] = 0.5 + (float(rect["y"]) - 0.5) * factor
        item["w"] = float(rect["w"]) * factor
        item["h"] = float(rect["h"]) * factor
        result.append(item)
    return result


def _tablet_compact():
    # A uniform affine shrink keeps every relative gap, so the compact layout
    # is genuinely tighter rather than merely having smaller controls.
    return _affine(_tablet_standard(), 0.94)


def _tablet_trackpads():
    controls = _tablet_standard()
    by_id = {item["id"]: item for item in controls}
    # The two native trackpads grow vertically and slightly in width while
    # keeping clear margins around the sticks and the stick-click buttons.
    by_id["pad_l"].update({"x": 0.280, "y": 0.585, "w": 0.215, "h": 0.375})
    by_id["pad_r"].update({"x": 0.505, "y": 0.585, "w": 0.215, "h": 0.375})
    return controls


def tablet_standard_layout():
    """Return a fresh tablet-standard layout."""

    return _tablet_standard()


def tablet_compact_layout():
    """Return a fresh compact tablet layout."""

    return _tablet_compact()


def tablet_trackpads_layout():
    """Return a fresh trackpad-first tablet layout."""

    return _tablet_trackpads()


def default_layout():
    """Return the default landscape controller layout (tablet standard)."""

    return _tablet_standard()


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("control %s must be numeric" % field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("control %s must be finite" % field)
    return number


def validate_layout(controls):
    """Return a canonical copy of *controls*, or raise ValueError."""

    try:
        items = list(controls)
    except TypeError as exc:
        raise ValueError("layout must be iterable") from exc

    canonical = []
    seen = set()
    seen_analog = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each control must be a dict")
        for key in _REQUIRED_KEYS:
            if key not in raw:
                raise ValueError("control is missing %s" % key)

        control_id = raw["id"]
        try:
            duplicate = control_id in seen
        except TypeError as exc:
            raise ValueError("control id must be hashable") from exc
        if duplicate:
            raise ValueError("duplicate control id: %r" % (control_id,))
        seen.add(control_id)

        kind = raw["kind"]
        output = raw["output"]
        if kind not in KINDS:
            raise ValueError("unknown control kind: %r" % (kind,))
        allowed = BUTTON_OUTPUTS if kind == "button" else SIDE_OUTPUTS
        if output not in allowed:
            raise ValueError("unknown output for %s: %r" % (kind, output))

        # Buttons may share an output because the engine reference counts
        # them.  Analog controls drive a single axis pair / pad, so each
        # (kind, output) pair must be unique.
        if kind != "button":
            if kind == "dpad" and output != "left":
                raise ValueError("dpad must use the left output")
            analog_key = (kind, output)
            if analog_key in seen_analog:
                raise ValueError(
                    "duplicate %s output: %r" % (kind, output)
                )
            seen_analog.add(analog_key)

        x = _number(raw["x"], "x")
        y = _number(raw["y"], "y")
        w = _number(raw["w"], "w")
        h = _number(raw["h"], "h")
        if w <= 0.0 or h <= 0.0:
            raise ValueError("control size must be positive")
        if x < 0.0 or y < 0.0 or x + w > 1.0 or y + h > 1.0:
            raise ValueError("control rectangle is outside the screen")

        item = {
            "id": control_id,
            "kind": kind,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "output": output,
        }
        label = raw.get("label")
        if isinstance(label, str) and label:
            item["label"] = label
        canonical.append(item)

    return canonical


def transform_point(x, y, xmin, xmax, ymin, ymax, rotation):
    """Normalize a raw point and apply a 0/90/180/270 degree rotation."""

    values = (x, y, xmin, xmax, ymin, ymax)
    try:
        numeric = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("transform inputs must be numeric") from exc
    if not all(math.isfinite(value) for value in numeric):
        raise ValueError("transform inputs must be finite")
    x, y, xmin, xmax, ymin, ymax = numeric
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("transform ranges must be positive")
    if rotation not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180, or 270")

    nx = min(1.0, max(0.0, (x - xmin) / (xmax - xmin)))
    ny = min(1.0, max(0.0, (y - ymin) / (ymax - ymin)))

    if rotation == 90:
        nx, ny = ny, 1.0 - nx
    elif rotation == 180:
        nx, ny = 1.0 - nx, 1.0 - ny
    elif rotation == 270:
        nx, ny = 1.0 - ny, nx

    return (
        min(1.0, max(0.0, nx)),
        min(1.0, max(0.0, ny)),
    )
