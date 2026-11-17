"""Cairo/Pango rendering for the Freedom touch controller overlay.

This module deliberately has no GTK imports.  Pango and PangoCairo are used
only for text layout, so it can be reused by the offline preview renderer and
by unit tests on a machine without a display.  All geometry is expressed in
normalized screen coordinates inside *message* and is converted to device
pixels here.
"""

import math

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")
from gi.repository import Pango, PangoCairo  # noqa: E402

from . import collapse  # noqa: E402
from . import layout as _layout  # noqa: E402

WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
CYAN = (0.28, 0.86, 1.0)
ABXY_OUTPUTS = frozenset({"a", "b", "x", "y"})
MAX_OPACITY = 1.0
MIN_OPACITY = 0.0

# Two flat palettes selected by the message's ``button_color``.  "white" is
# the unchanged historical appearance; "black" fills each control body so it
# stays readable over a bright game while keeping light, crisp ink.  Every
# alpha below is multiplied by the message opacity at draw time, and pressed
# feedback stays CYAN in both palettes.
PALETTES = {
    "white": {
        "body": WHITE,
        "ink": WHITE,
        "button": {"fill": 0.055, "stroke": 0.82, "text": 0.90},
        "stick": {
            "surface_fill": 0.045,
            "surface_stroke": 0.72,
            "thumb_fill": 0.10,
            "thumb_stroke": 0.88,
            "dot": 0.55,
        },
        "dpad": {"fill": 0.055, "stroke": 0.78},
        "pad": {"fill": 0.04, "stroke": 0.62, "label": 0.34},
        "pill": {"fill": 0.075, "stroke": 0.82, "label": 1.0},
    },
    "black": {
        "body": BLACK,
        "ink": WHITE,
        "button": {"fill": 0.65, "stroke": 0.92, "text": 0.96},
        "stick": {
            "surface_fill": 0.45,
            "surface_stroke": 0.82,
            "thumb_fill": 0.65,
            "thumb_stroke": 0.92,
            "dot": 0.72,
        },
        "dpad": {"fill": 0.60, "stroke": 0.86},
        "pad": {"fill": 0.45, "stroke": 0.72, "label": 0.55},
        "pill": {"fill": 0.60, "stroke": 0.90, "label": 1.0},
    },
}
DEFAULT_PALETTE = "white"


def _palette(message):
    """Return the palette named by *message*; a legacy message means white."""

    color = message.get("button_color")
    if isinstance(color, str) and color in PALETTES:
        return PALETTES[color]
    return PALETTES[DEFAULT_PALETTE]


def _clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def _number(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not math.isfinite(number):
        return default
    return number


def _line_width(width, height):
    return max(1.0, 2.0 * min(width, height) / 1080.0)


def rounded_rectangle(context, x, y, width, height, radius):
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2.0, 0.0)
    context.arc(
        x + width - radius, y + height - radius, radius, 0.0, math.pi / 2.0
    )
    context.arc(x + radius, y + height - radius, radius, math.pi / 2.0, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, math.pi * 1.5)
    context.close_path()


def _ellipse_path(context, x, y, width, height):
    radius_x = max(0.0, width / 2.0)
    radius_y = max(0.0, height / 2.0)
    if radius_x <= 0.0 or radius_y <= 0.0:
        context.new_sub_path()
        return
    # cairo.Context has no ellipse primitive.  Draw a unit circle under a
    # temporary scale; restoring the graphics state keeps the path, which is
    # recorded in device space, so callers can still fill/stroke it normally.
    context.save()
    context.translate(x + radius_x, y + radius_y)
    context.scale(radius_x, radius_y)
    context.new_sub_path()
    context.arc(0.0, 0.0, 1.0, 0.0, math.tau)
    context.restore()


def _fill_and_stroke(context, fill, stroke, line_width):
    context.set_source_rgba(*fill)
    context.fill_preserve()
    context.set_source_rgba(*stroke)
    context.set_line_width(line_width)
    context.stroke()


def _create_label_layout(context, text, size):
    """Create the Pango layout used for every in-game label.

    *size* is in device pixels; Pango works in 1/Pango.SCALE units, hence the
    absolute size.  A layout owns its font fallback, so Chinese labels are
    resolved by Pango/fontconfig instead of the cairo toy text API.
    """
    layout = PangoCairo.create_layout(context)
    description = Pango.FontDescription("Sans Bold")
    description.set_absolute_size(max(0.0, float(size)) * Pango.SCALE)
    layout.set_font_description(description)
    layout.set_text(text, -1)
    return layout


def _fit_label_layout(context, text, size, maximum_width, maximum_height):
    """Lay out *text* at *size*, shrinking it to the label box if needed.

    Mirrors the previous toy-text intent: labels are only ever scaled down,
    measured by ink pixel extents, and never enlarged.  Returns
    ``(layout, ink)`` or ``(None, None)`` when the fitted size is unusable.
    """
    layout = _create_label_layout(context, text, size)
    ink, _ = layout.get_pixel_extents()
    width = max(float(ink.width), 1.0)
    height = max(float(ink.height), 1.0)
    scale = min(
        1.0,
        maximum_width / width if maximum_width > 0.0 else 1.0,
        maximum_height / height if maximum_height > 0.0 else 1.0,
    )
    if scale < 1.0:
        size *= scale
        if size <= 0.5:
            return None, None
        layout = _create_label_layout(context, text, size)
        ink, _ = layout.get_pixel_extents()
    return layout, ink


def _label(
    context,
    text,
    center_x,
    center_y,
    size,
    maximum_width,
    maximum_height,
    alpha,
    color=WHITE,
):
    if not text or size <= 0.0 or alpha <= 0.0:
        return
    layout, ink = _fit_label_layout(
        context, text, size, maximum_width, maximum_height
    )
    if layout is None:
        return
    context.new_path()
    # Ink extents are relative to the layout origin, so subtract them to put
    # the visible glyph box exactly in the middle of the control.
    context.move_to(
        center_x - ink.width / 2.0 - ink.x,
        center_y - ink.height / 2.0 - ink.y,
    )
    context.set_source_rgba(color[0], color[1], color[2], alpha)
    PangoCairo.show_layout(context, layout)


def _chrome_bubble(message):
    bubble = message.get("bubble")
    if not isinstance(bubble, dict):
        return collapse.default_bubble()
    return bubble


def _draw_collapse_pill(context, width, height, opacity, palette):
    """Draw the single expanded-mode pill with the existing Pango label."""

    body = palette["body"]
    ink = palette["ink"]
    spec = palette["pill"]
    x, y, pill_width, pill_height = collapse.pill_rect(width, height)
    radius = min(pill_width, pill_height) / 2.0
    rounded_rectangle(context, x, y, pill_width, pill_height, radius)
    _fill_and_stroke(
        context,
        (body[0], body[1], body[2], spec["fill"] * opacity),
        (ink[0], ink[1], ink[2], spec["stroke"] * opacity),
        _line_width(width, height),
    )
    _label(
        context,
        collapse.PILL_LABEL,
        x + pill_width / 2.0,
        y + pill_height / 2.0,
        min(pill_height * 0.50, pill_width * 0.42),
        pill_width * 0.76,
        pill_height * 0.66,
        spec["label"] * opacity,
        ink,
    )


def _gamepad_path(context, center_x, center_y, diameter):
    """Lay out a small line-art gamepad without any icon font."""

    body_width = diameter * 0.58
    body_height = diameter * 0.30
    body_x = center_x - body_width / 2.0
    body_y = center_y - body_height / 2.0
    rounded_rectangle(
        context,
        body_x,
        body_y,
        body_width,
        body_height,
        min(body_width, body_height) * 0.42,
    )
    context.stroke()


def _draw_gamepad_icon(context, center_x, center_y, diameter, opacity):
    """Stroke the bubble's controller glyph and its two face buttons."""

    context.set_source_rgba(WHITE[0], WHITE[1], WHITE[2], 0.94 * opacity)
    context.set_line_width(max(1.5, diameter * 0.035))
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)

    _gamepad_path(context, center_x, center_y, diameter)

    # Cross on the left half of the pad.
    cross_x = center_x - diameter * 0.15
    cross_y = center_y
    cross_arm = diameter * 0.055
    context.new_path()
    context.move_to(cross_x - cross_arm, cross_y)
    context.line_to(cross_x + cross_arm, cross_y)
    context.move_to(cross_x, cross_y - cross_arm)
    context.line_to(cross_x, cross_y + cross_arm)
    context.stroke()

    # Two face buttons on the right half.
    button_radius = diameter * 0.026
    for offset in (-0.045, 0.045):
        context.new_sub_path()
        context.arc(
            center_x + diameter * 0.15,
            center_y + diameter * offset,
            button_radius,
            0.0,
            math.tau,
        )
        context.fill()


def _draw_collapse_bubble(context, width, height, message, opacity):
    """Draw the collapsed round bubble and nothing else."""

    x, y, bubble_width, bubble_height = collapse.bubble_rect(
        _chrome_bubble(message), width, height
    )
    diameter = min(bubble_width, bubble_height)
    center_x = x + bubble_width / 2.0
    center_y = y + bubble_height / 2.0

    context.new_sub_path()
    context.arc(center_x, center_y, diameter / 2.0, 0.0, math.tau)
    _fill_and_stroke(
        context,
        (WHITE[0], WHITE[1], WHITE[2], 0.10 * opacity),
        (WHITE[0], WHITE[1], WHITE[2], 0.88 * opacity),
        _line_width(width, height),
    )
    _draw_gamepad_icon(context, center_x, center_y, diameter, opacity)


def _button_colors(pressed, opacity, palette):
    if pressed:
        # Pressed feedback is always the same cyan highlight on either body.
        return (
            (CYAN[0], CYAN[1], CYAN[2], 0.34 * opacity),
            (CYAN[0], CYAN[1], CYAN[2], 0.95 * opacity),
            WHITE,
            0.98 * opacity,
        )
    body = palette["body"]
    ink = palette["ink"]
    spec = palette["button"]
    return (
        (body[0], body[1], body[2], spec["fill"] * opacity),
        (ink[0], ink[1], ink[2], spec["stroke"] * opacity),
        ink,
        spec["text"] * opacity,
    )


def _draw_button(context, rect, control, state, opacity, line_width, palette):
    x, y, width, height = rect
    pressed = bool(state.get("pressed"))
    fill, stroke, text_color, text_alpha = _button_colors(
        pressed, opacity, palette
    )
    label = control.get("label") or str(control.get("output", "")).upper()

    if control.get("output") in ABXY_OUTPUTS:
        radius = max(0.0, min(width, height) / 2.0 - line_width)
        context.new_sub_path()
        context.arc(
            x + width / 2.0, y + height / 2.0, radius, 0.0, math.tau
        )
        _fill_and_stroke(context, fill, stroke, line_width)
    else:
        radius = min(width, height) * 0.28
        rounded_rectangle(context, x, y, width, height, radius)
        _fill_and_stroke(context, fill, stroke, line_width)

    _label(
        context,
        label,
        x + width / 2.0,
        y + height / 2.0,
        min(height * 0.46, width * 0.55),
        width * 0.76,
        height * 0.62,
        text_alpha,
        text_color,
    )


def _draw_stick(context, rect, state, opacity, line_width, palette):
    x, y, width, height = rect
    pressed = bool(state.get("pressed"))
    body = palette["body"]
    ink = palette["ink"]
    spec = palette["stick"]
    center_x = x + width / 2.0
    center_y = y + height / 2.0

    _ellipse_path(context, x, y, width, height)
    _fill_and_stroke(
        context,
        (body[0], body[1], body[2], spec["surface_fill"] * opacity),
        (ink[0], ink[1], ink[2], spec["surface_stroke"] * opacity),
        line_width,
    )

    radius = max(0.0, min(width, height) * 0.27)
    travel_x = max(0.0, width / 2.0 - radius - line_width * 2.0)
    travel_y = max(0.0, height / 2.0 - radius - line_width * 2.0)
    local_x = _clamp(_number(state.get("x"), 0.5))
    local_y = _clamp(_number(state.get("y"), 0.5))
    thumb_x = center_x + (local_x * 2.0 - 1.0) * travel_x
    thumb_y = center_y + (local_y * 2.0 - 1.0) * travel_y

    if pressed:
        fill = (CYAN[0], CYAN[1], CYAN[2], 0.34 * opacity)
        stroke = (CYAN[0], CYAN[1], CYAN[2], 0.96 * opacity)
    else:
        fill = (body[0], body[1], body[2], spec["thumb_fill"] * opacity)
        stroke = (ink[0], ink[1], ink[2], spec["thumb_stroke"] * opacity)

    context.new_sub_path()
    context.arc(thumb_x, thumb_y, radius, 0.0, math.tau)
    _fill_and_stroke(context, fill, stroke, line_width)

    context.new_sub_path()
    context.arc(center_x, center_y, max(1.0, line_width * 0.9), 0.0, math.tau)
    context.set_source_rgba(ink[0], ink[1], ink[2], spec["dot"] * opacity)
    context.fill()


def _cross_path(context, x, y, width, height, thickness):
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    half = max(0.0, min(thickness / 2.0, width / 2.0, height / 2.0))
    context.new_path()
    context.move_to(x, center_y - half)
    context.line_to(center_x - half, center_y - half)
    context.line_to(center_x - half, y)
    context.line_to(center_x + half, y)
    context.line_to(center_x + half, center_y - half)
    context.line_to(x + width, center_y - half)
    context.line_to(x + width, center_y + half)
    context.line_to(center_x + half, center_y + half)
    context.line_to(center_x + half, y + height)
    context.line_to(center_x - half, y + height)
    context.line_to(center_x - half, center_y + half)
    context.line_to(x, center_y + half)
    context.close_path()


def _draw_dpad(context, rect, state, opacity, line_width, palette):
    x, y, width, height = rect
    pressed = bool(state.get("pressed"))
    body = palette["body"]
    ink = palette["ink"]
    spec = palette["dpad"]
    thickness = min(width, height) * 0.36
    _cross_path(context, x, y, width, height, thickness)
    _fill_and_stroke(
        context,
        (body[0], body[1], body[2], spec["fill"] * opacity),
        (ink[0], ink[1], ink[2], spec["stroke"] * opacity),
        line_width,
    )

    if not pressed:
        return

    local_x = _number(state.get("x"), 0.5)
    local_y = _number(state.get("y"), 0.5)
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    half = thickness / 2.0
    context.save()
    _cross_path(context, x, y, width, height, thickness)
    context.clip()
    context.set_source_rgba(CYAN[0], CYAN[1], CYAN[2], 0.30 * opacity)
    if local_x <= 0.5 - 0.12:
        context.rectangle(x, center_y - half, center_x - x, thickness)
    elif local_x >= 0.5 + 0.12:
        context.rectangle(center_x, center_y - half, x + width - center_x, thickness)
    if local_y <= 0.5 - 0.12:
        context.rectangle(center_x - half, y, thickness, center_y - y)
    elif local_y >= 0.5 + 0.12:
        context.rectangle(center_x - half, center_y, thickness, y + height - center_y)
    context.fill()
    context.restore()


def _draw_pad(context, rect, control, state, opacity, line_width, palette):
    x, y, width, height = rect
    pressed = bool(state.get("pressed"))
    body = palette["body"]
    ink = palette["ink"]
    spec = palette["pad"]
    # Trackpads are large translucent surfaces, so their body stays lighter
    # than a small button even in the black palette.
    border = (
        (CYAN[0], CYAN[1], CYAN[2], 0.92 * opacity)
        if pressed
        else (ink[0], ink[1], ink[2], spec["stroke"] * opacity)
    )
    fill = (
        (CYAN[0], CYAN[1], CYAN[2], 0.075 * opacity)
        if pressed
        else (body[0], body[1], body[2], spec["fill"] * opacity)
    )
    radius = min(width, height) * 0.18
    rounded_rectangle(context, x, y, width, height, radius)
    _fill_and_stroke(context, fill, border, line_width)

    side = "L" if control.get("output") == "left" else "R"
    _label(
        context,
        side,
        x + width / 2.0,
        y + height / 2.0,
        min(width, height) * 0.16,
        width * 0.30,
        height * 0.24,
        spec["label"] * opacity,
        ink,
    )

    if not pressed:
        return

    dot_radius = max(3.0, min(width, height) * 0.028)
    dot_radius = min(dot_radius, width / 2.0, height / 2.0)
    dot_x = x + _clamp(_number(state.get("x"), 0.5)) * width
    dot_y = y + _clamp(_number(state.get("y"), 0.5)) * height
    dot_x = _clamp(dot_x, x + dot_radius, x + width - dot_radius)
    dot_y = _clamp(dot_y, y + dot_radius, y + height - dot_radius)

    context.new_sub_path()
    context.arc(dot_x, dot_y, dot_radius, 0.0, math.tau)
    context.set_source_rgba(CYAN[0], CYAN[1], CYAN[2], 0.92 * opacity)
    context.fill_preserve()
    context.set_source_rgba(WHITE[0], WHITE[1], WHITE[2], 0.95 * opacity)
    context.set_line_width(max(1.0, line_width * 0.8))
    context.stroke()


def draw_controls(context, width, height, message):
    """Draw *message* onto a cairo context of *width* x *height* pixels.

    The target is cleared to transparent first so a hidden frame cannot leave
    stale pixels in a dedicated overlay plane.  The optional ``button_color``
    field selects the "white" (default, legacy) or "black" control palette and
    only affects colours: geometry, hit rectangles and opacity are identical.
    """

    try:
        width = float(width)
        height = float(height)
    except (TypeError, ValueError):
        raise ValueError("width and height must be numeric") from None
    if not (math.isfinite(width) and math.isfinite(height)):
        raise ValueError("width and height must be finite")
    if width <= 0.0 or height <= 0.0:
        raise ValueError("width and height must be positive")

    context.save()
    context.set_operator(cairo.OPERATOR_SOURCE)
    context.set_source_rgba(0.0, 0.0, 0.0, 0.0)
    context.paint()
    context.restore()

    if not isinstance(message, dict) or not message.get("visible", False):
        return

    opacity = _clamp(
        _number(message.get("opacity"), MAX_OPACITY), MIN_OPACITY, MAX_OPACITY
    )
    if opacity <= 0.0:
        return

    palette = _palette(message)

    # Collapsed mode replaces the whole controller with the single bubble.
    if message.get("mode") == "collapsed":
        if message.get("chrome") is True:
            try:
                _draw_collapse_bubble(context, width, height, message, opacity)
            except ValueError:
                pass
        return

    line_width = _line_width(width, height)
    layout = message.get("layout")
    if not isinstance(layout, (list, tuple)):
        return
    snapshot = message.get("snapshot")
    controls = {}
    if isinstance(snapshot, dict) and isinstance(snapshot.get("controls"), dict):
        controls = snapshot["controls"]

    # The two trackpads are translucent surfaces; keep them below every
    # controller glyph so the contact dot is never obscured by a neighbour.
    ordered = [item for item in layout if item.get("kind") == "pad"]
    ordered.extend(item for item in layout if item.get("kind") != "pad")

    for control in ordered:
        if not isinstance(control, dict):
            continue
        if message.get("right_stick_fullscreen") is True and control.get("kind") == "stick" and control.get("output") == "right":
            continue
        try:
            x = float(control["x"]) * width
            y = float(control["y"]) * height
            control_width = float(control["w"]) * width
            control_height = float(control["h"]) * height
        except (KeyError, TypeError, ValueError):
            continue
        if control_width <= 0.0 or control_height <= 0.0:
            continue

        control_id = control.get("id")
        state = controls.get(control_id, {})
        if not isinstance(state, dict):
            state = {}
        kind = control.get("kind")
        if kind == "stick":
            try:
                projected = _layout.circular_stick_rect(
                    control, width / height
                )
                x = float(projected["x"]) * width
                y = float(projected["y"]) * height
                control_width = float(projected["w"]) * width
                control_height = float(projected["h"]) * height
            except (KeyError, TypeError, ValueError):
                continue
            if control_width <= 0.0 or control_height <= 0.0:
                continue
        rect = (x, y, control_width, control_height)

        context.save()
        if kind == "button":
            _draw_button(
                context, rect, control, state, opacity, line_width, palette
            )
        elif kind == "stick":
            _draw_stick(context, rect, state, opacity, line_width, palette)
        elif kind == "dpad":
            _draw_dpad(context, rect, state, opacity, line_width, palette)
        elif kind == "pad":
            _draw_pad(
                context, rect, control, state, opacity, line_width, palette
            )
        context.restore()

    if message.get("chrome") is True:
        _draw_collapse_pill(context, width, height, opacity, palette)
