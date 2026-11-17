"""Normalized screen layout for the Freedom touch controller.

All rectangles are in landscape-oriented normalized screen coordinates; y grows
downward as it does for evdev touch events.

Face buttons are placed by physical position, never by their printed label.
The InputPlumber adapter owns the output -> evdev table, and the Deck/DualSense
semantic layers name those codes:

* ``y`` -> BTN_NORTH (307) -> physical top button
* ``x`` -> BTN_WEST (308) -> physical left button
* ``b`` -> BTN_EAST (305) -> physical right button
* ``a`` -> BTN_SOUTH (304) -> physical bottom button

The PlayStation diamond prints triangle above square, so the physical top
button must drive ``y`` and the physical left button ``x``.  Xbox and Switch
print X/Y on those same positions, and the emitted codes stay identical for
all three families.

The face control IDs (``btn_y`` top, ``btn_x`` left, ``btn_b`` right and
``btn_a`` bottom) are opaque, stable component identifiers shared by every
preset; the ``output`` field is the semantic slot that is actually emitted.
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


def _lower_pads():
    """Return the two native Steam trackpads in the lower middle.

    Both pads share one center line at y=.790 with the sticks, and their
    combined left/right extents mirror about x=.5.
    """

    return [
        _entry("pad_l", "pad", 0.280, 0.640, 0.190, 0.300, "left", "左触摸板"),
        _entry("pad_r", "pad", 0.530, 0.640, 0.190, 0.300, "right", "右触摸板"),
    ]


def _stick_clicks():
    # Centered directly below their own stick columns, sharing one baseline.
    # Both centres stay hittable at the maximum UI scale.
    return [
        _entry("btn_l3", "button", 0.085, 0.930, 0.070, 0.060, "l3", "L3"),
        _entry("btn_r3", "button", 0.845, 0.930, 0.070, 0.060, "r3", "R3"),
    ]


def _top_row(shoulders, back_label, start_label):
    """Return the shoulder pair plus the top system-button row.

    *shoulders* is the ``(lb, lt, rt, rb)`` label tuple, all sharing the
    upper shoulder line (centre Y .32 in the built-in family defaults).
    The four utility keys (guide/qam/back/start) sit at centre Y .0575:
    guide/QAM in the extreme top corners and back/start at the upper
    middle (Back/Start centres X .44/.56); the private name is retained to
    minimize unrelated edits.
    """

    lb_label, lt_label, rt_label, rb_label = shoulders
    return [
        _entry("btn_lb", "button", 0.020, 0.2825, 0.100, 0.075, "lb",
               lb_label),
        _entry("btn_lt", "button", 0.130, 0.2825, 0.100, 0.075, "lt",
               lt_label),
        _entry("btn_rt", "button", 0.770, 0.2825, 0.100, 0.075, "rt",
               rt_label),
        _entry("btn_rb", "button", 0.880, 0.2825, 0.100, 0.075, "rb",
               rb_label),
        _entry("btn_guide", "button", 0.001, 0.020, 0.09, 0.075, "guide",
               "左侧栏"),
        _entry("btn_qam", "button", 0.909, 0.020, 0.09, 0.075, "qam",
               "右侧栏"),
        _entry("btn_back", "button", 0.395, 0.020, 0.09, 0.075, "back",
               back_label),
        _entry("btn_start", "button", 0.515, 0.020, 0.09, 0.075, "start",
               start_label),
    ]


# Default shrink factor applied to every non-face control in the built-in
# family factory.  Small tablets (7/8in) get a tighter default.  Shrinking
# preserves each control's chosen centre, keeping the symmetric centre grid
# (D-pad/face union, stick/pad/L3 centres) exactly mirrored; primary groups
# stay low and the utility keys sit at the top (guide/QAM in the extreme
# corners, back/start at the upper middle).
_DEFAULT_SCALE = 0.8

# Union centre of the original face diamond (btn_y/x/b/a), used to shrink
# the four face buttons as one rigid group so their box stays mirrored to
# the D-pad.
_FACE_UNION_CENTER_X = 0.880
_FACE_UNION_CENTER_Y = 0.530


def _shrink_about(rect, cx, cy, factor=_DEFAULT_SCALE):
    """Return a copy of *rect* scaled by *factor* around (cx, cy)."""

    item = dict(rect)
    item["x"] = cx + (float(rect["x"]) - cx) * factor
    item["y"] = cy + (float(rect["y"]) - cy) * factor
    item["w"] = float(rect["w"]) * factor
    item["h"] = float(rect["h"]) * factor
    return item


def _family_layout(shoulders, back_label, start_label, face_labels):
    """Build one built-in layout using the shared default geometry.

    Every console family shares this exact geometry so sticks, pads, D-pad
    and the face diamond are mirrored/aligned across the screen.  Only the
    console-specific *labels* differ (shoulders, back/start text and the
    four face labels in ``(top, left, right, bottom)`` order).  The ``output``
    slots of the face diamond stay ``y``/``x``/``b``/``a`` for all families.

    All non-face controls are shrunk by ``_DEFAULT_SCALE`` about their own
    (unchanged) centres, so shrink preserves each chosen centre and the
    symmetric grid stays mirrored.  The four face buttons are shrunk as one
    rigid diamond group about ``(_FACE_UNION_CENTER_X, _FACE_UNION_CENTER_Y)``,
    keeping the D-pad/face-diamond union and the stick/pad/L3 centres exactly
    mirrored.  Primary groups sit low for a mature two-thumb grip and the
    utility keys (guide/qam/back/start) sit at centre Y .0575: guide/QAM in
    the extreme top corners and back/start at the upper middle.  This
    is a concrete factory default only: it does not touch user settings,
    custom presets or the runtime hit/render path.
    """

    top_label, left_label, right_label, bottom_label = face_labels
    face_ids = ("btn_y", "btn_x", "btn_b", "btn_a")
    raw = [
        # Symmetric sticks at mirrored x=.035/.795 and shared center line.
        _entry("stick_l", "stick", 0.035, 0.655, 0.170, 0.270,
               "left", "左摇杆"),
        # D-pad upper left; its union mirrors the face-diamond union.
        _entry("dpad_l", "dpad", 0.0175, 0.375, 0.205, 0.310,
               "left", "十字键"),
        _entry("stick_r", "stick", 0.795, 0.655, 0.170, 0.270,
               "right", "右摇杆"),

        # Face diamond union x=.7775..9825, y=.375..685; equal centre Y and
        # mirrored centre X to the D-pad.  Top button drives y (BTN_NORTH).
        _entry("btn_y", "button", 0.8475, 0.375, 0.065, 0.100,
               "y", top_label),
        _entry("btn_x", "button", 0.7775, 0.480, 0.065, 0.100,
               "x", left_label),
        _entry("btn_b", "button", 0.9175, 0.480, 0.065, 0.100,
               "b", right_label),
        _entry("btn_a", "button", 0.8475, 0.585, 0.065, 0.100,
               "a", bottom_label),

        *_lower_pads(),
        *_top_row(shoulders, back_label, start_label),
        *_stick_clicks(),
    ]

    result = []
    for item in raw:
        cx = float(item["x"]) + float(item["w"]) / 2.0
        cy = float(item["y"]) + float(item["h"]) / 2.0
        if item["id"] in face_ids:
            result.append(_shrink_about(
                item, _FACE_UNION_CENTER_X, _FACE_UNION_CENTER_Y
            ))
        else:
            result.append(_shrink_about(item, cx, cy))
    return result


def _tablet_standard():
    # Xbox family: standard tablet default using the shared geometry.
    return _family_layout(
        ("LB", "LT", "RT", "RB"), "返回", "开始",
        ("Y", "X", "B", "A"),
    )


def _playstation():
    # PlayStation family: triangle top, square left, circle right, cross
    # bottom.  Physical top drives ``y`` (BTN_NORTH), left drives ``x``.
    return _family_layout(
        ("L1", "L2", "R2", "R1"), "SHARE", "OPTIONS",
        ("△", "□", "○", "×"),
    )


def _switch():
    # Switch family: X top, Y left, A right, B bottom.  Printed labels differ
    # from PlayStation; emitted slots stay y/x/b/a per the shared geometry.
    return _family_layout(
        ("L", "ZL", "ZR", "R"), "−", "+",
        ("X", "Y", "A", "B"),
    )


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


def ps_layout():
    """Return a fresh PlayStation layout."""

    return _playstation()


def switch_layout():
    """Return a fresh Switch layout."""

    return _switch()


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


def circular_stick_rect(control, aspect_ratio):
    """Return a copy of *control* with sticks projected to a circle.

    The canonical normalized stick width is the circle's diameter as a
    fraction of screen width.  For a stick the returned rectangle is square
    in device pixels (``w`` normalized by width, ``h`` normalized by height)
    and keeps the original centre, clamped inside the unit screen rectangle.
    Non-stick controls, and sticks without a usable aspect ratio, are
    returned as an unmodified shallow copy.  The input is never mutated.
    """

    item = dict(control)
    if item.get("kind") != "stick":
        return item

    try:
        aspect = float(aspect_ratio)
    except (TypeError, ValueError):
        return item
    if not math.isfinite(aspect) or aspect <= 0.0:
        return item

    try:
        old_w = float(item["w"])
        old_h = float(item["h"])
        old_x = float(item["x"])
        old_y = float(item["y"])
    except (KeyError, TypeError, ValueError):
        return item

    width = min(old_w, 1.0, 1.0 / aspect)
    if width <= 0.0:
        return item
    height = width * aspect

    center_x = old_x + old_w / 2.0
    center_y = old_y + old_h / 2.0
    new_x = min(1.0 - width, max(0.0, center_x - width / 2.0))
    new_y = min(1.0 - height, max(0.0, center_y - height / 2.0))

    item["x"] = new_x
    item["y"] = new_y
    item["w"] = width
    item["h"] = height
    return item


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
