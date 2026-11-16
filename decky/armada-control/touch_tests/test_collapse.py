"""Geometry, gesture, and message tests for the collapsible overlay chrome.

The drag/tap decisions must be testable without a display, so collapse.py is
pure and the overlay uses the same helper for hit testing and clamping.
"""

import copy
import math
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import collapse  # noqa: E402
from py_modules.freedom_touch.overlay import (  # noqa: E402
    _default_message,
    parse_message,
)

WIDTH = 1920
HEIGHT = 1080


def _center(rect):
    return rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0


def test_bubble_is_a_physical_circle_at_eight_percent_of_screen_height():
    for width, height in ((1920, 1080), (1280, 1024), (2560, 1080)):
        _x, _y, bubble_width, bubble_height = collapse.bubble_rect(
            {"x": 0.30, "y": 0.20}, width, height
        )
        assert bubble_width == pytest.approx(bubble_height, abs=1e-9)
        assert bubble_height == pytest.approx(height * 0.08, abs=1e-9)


def test_default_bubble_is_clamped_inside_the_viewport():
    x, y, width, height = collapse.bubble_normalized(
        {"x": 1.0, "y": 1.0}, WIDTH, HEIGHT
    )
    assert x + width == pytest.approx(1.0)
    assert y + height == pytest.approx(1.0)

    x, y, _width, _height = collapse.bubble_normalized(
        {"x": -0.5, "y": -0.5}, WIDTH, HEIGHT
    )
    assert x == 0.0
    assert y == 0.0


def test_begin_only_hits_the_bubble_rectangle():
    drag = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, WIDTH, HEIGHT)
    rect = collapse.bubble_rect(drag.bubble, WIDTH, HEIGHT)
    center_x, center_y = _center(rect)

    assert drag.begin(rect[0] - 2.0, center_y) is False
    assert drag.begin(center_x, rect[1] - 2.0) is False
    assert drag.begin(center_x, center_y) is True


def test_movement_below_the_scaled_threshold_stays_a_tap():
    drag = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, WIDTH, HEIGHT)
    rect = collapse.bubble_rect(drag.bubble, WIDTH, HEIGHT)
    center_x, center_y = _center(rect)
    start = drag.bubble

    assert drag.begin(center_x, center_y) is True
    assert drag.update(center_x + 7.9, center_y) is False
    assert drag.dragging is False
    assert drag.bubble == start
    assert drag.end() == "tap"


def test_drag_keeps_the_nonzero_grab_anchor():
    drag = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, WIDTH, HEIGHT)
    rect = collapse.bubble_rect(drag.bubble, WIDTH, HEIGHT)
    grab_x = rect[0] + 10.0
    grab_y = rect[1] + 12.0

    assert drag.begin(grab_x, grab_y) is True
    assert drag.update(grab_x + 100.0, grab_y + 60.0) is True
    assert drag.dragging is True

    # The finger keeps the same offset inside the bubble.
    assert drag.bubble["x"] * WIDTH == pytest.approx(rect[0] + 100.0)
    assert drag.bubble["y"] * HEIGHT == pytest.approx(rect[1] + 60.0)
    assert drag.end() == "drag"


def test_drag_continues_outside_the_original_rectangle_and_clamps():
    drag = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, WIDTH, HEIGHT)
    rect = collapse.bubble_rect(drag.bubble, WIDTH, HEIGHT)
    center_x, center_y = _center(rect)

    assert drag.begin(center_x, center_y) is True
    assert drag.update(rect[0] + rect[2] + 40.0, center_y) is True
    assert drag.dragging is True
    assert drag.bubble["x"] > 0.60

    assert drag.update(WIDTH + 300.0, HEIGHT + 300.0) is True
    x, y, bubble_width, bubble_height = collapse.bubble_normalized(
        drag.bubble, WIDTH, HEIGHT
    )
    assert x + bubble_width == pytest.approx(1.0)
    assert y + bubble_height == pytest.approx(1.0)
    assert drag.end() == "drag"


def test_cancel_ends_without_tap_or_drag_action():
    drag = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, WIDTH, HEIGHT)
    center_x, center_y = _center(
        collapse.bubble_rect(drag.bubble, WIDTH, HEIGHT)
    )

    assert drag.begin(center_x, center_y) is True
    assert drag.update(center_x + 60.0, center_y + 40.0) is True
    assert drag.cancel() is True
    assert drag.active is False
    assert drag.dragging is False
    assert drag.end() is None


def test_drag_threshold_scales_with_viewport_height():
    small = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, 1920, 1080)
    small_center = _center(collapse.bubble_rect(small.bubble, 1920, 1080))
    assert small.begin(*small_center) is True
    assert small.update(small_center[0] + 12.0, small_center[1]) is True

    tall = collapse.BubbleDrag({"x": 0.60, "y": 0.50}, 3840, 2160)
    tall_center = _center(collapse.bubble_rect(tall.bubble, 3840, 2160))
    assert tall.begin(*tall_center) is True
    assert tall.update(tall_center[0] + 12.0, tall_center[1]) is False


def test_default_message_uses_expanded_mode_and_default_bubble():
    message = _default_message()

    assert message["mode"] == "expanded"
    assert message["chrome"] is False
    assert message["bubble"] == {"x": 0.92, "y": 0.45}
    assert parse_message(message) == message


def test_visible_collapsed_message_parses_chrome_and_bubble():
    payload = copy.deepcopy(_default_message())
    payload["mode"] = "collapsed"
    payload["chrome"] = True
    payload["bubble"] = {"x": 0.71, "y": 0.24}

    message = parse_message(payload)

    assert message["visible"] is True
    assert message["mode"] == "collapsed"
    assert message["chrome"] is True
    assert message["bubble"] == {"x": 0.71, "y": 0.24}


@pytest.mark.parametrize(
    "field, value",
    [
        ("mode", "half"),
        ("mode", None),
        ("chrome", 1),
        ("chrome", "yes"),
        ("bubble", []),
        ("bubble", {"x": 0.2}),
        ("bubble", {"x": 1.2, "y": 0.2}),
        ("bubble", {"x": 0.2, "y": -0.1}),
        ("bubble", {"x": True, "y": 0.2}),
        ("bubble", {"x": "0.2", "y": 0.2}),
    ],
)
def test_malformed_chrome_fields_are_rejected(field, value):
    payload = copy.deepcopy(_default_message())
    payload[field] = value

    with pytest.raises(ValueError):
        parse_message(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("visible", "yes"),
        ("opacity", 1.5),
        ("layout", {}),
        ("snapshot", None),
    ],
)
def test_existing_message_validation_is_preserved(field, value):
    payload = copy.deepcopy(_default_message())
    payload[field] = value

    with pytest.raises(ValueError):
        parse_message(payload)


def test_collapsed_draw_renders_only_the_bubble():
    import cairo

    from py_modules.freedom_touch.draw import draw_controls

    message = copy.deepcopy(_default_message())
    message["mode"] = "collapsed"
    message["chrome"] = True
    message["bubble"] = {"x": 0.60, "y": 0.50}

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    draw_controls(cairo.Context(surface), WIDTH, HEIGHT, message)
    surface.flush()

    data = bytes(surface.get_data())
    stride = surface.get_stride()
    left = top = None
    right = bottom = -1
    for y in range(HEIGHT):
        row = data[y * stride:(y + 1) * stride]
        for x in range(WIDTH):
            if row[x * 4 + 3]:
                if left is None or x < left:
                    left = x
                if top is None:
                    top = y
                if x > right:
                    right = x
                bottom = y

    assert left is not None
    bubble = collapse.bubble_rect(message["bubble"], WIDTH, HEIGHT)
    assert left >= math.floor(bubble[0] - 3)
    assert top >= math.floor(bubble[1] - 3)
    assert right <= math.ceil(bubble[0] + bubble[2] + 3)
    assert bottom <= math.ceil(bubble[1] + bubble[3] + 3)
