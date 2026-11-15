"""Rendering tests against a real Cairo image surface (no Cairo mocks).

The remote preview failure was an AttributeError on a non-existent
``cairo.Context.ellipse``.  These tests execute the real drawing code so the
same defect cannot hide behind a fake context.
"""

import sys
from pathlib import Path

import cairo

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch.draw import (  # noqa: E402
    _ellipse_path,
    _fit_label_layout,
    _label,
    draw_controls,
)
from py_modules.freedom_touch.layout import default_layout  # noqa: E402

WIDTH = 1920
HEIGHT = 1080
CHINESE_LABELS = ("指南", "快捷菜单", "返回", "开始", "手柄 自定义 A/B")


def _message(visible=True):
    layout = default_layout()
    controls = {
        control["id"]: {"pressed": True, "x": 0.2, "y": 0.8}
        for control in layout
    }
    return {
        "visible": visible,
        "opacity": 0.9,
        "layout": layout,
        "snapshot": {"active": True, "controls": controls},
    }


def _surface():
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)


def _opaque_pixel_count(surface):
    surface.flush()
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    total = 0
    for y in range(surface.get_height()):
        row = data[y * stride:(y + 1) * stride]
        total += sum(1 for alpha in row[3::4] if alpha)
    return total


def _alpha_bounds(surface):
    surface.flush()
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    left = top = None
    right = bottom = -1
    for y in range(surface.get_height()):
        row = data[y * stride:(y + 1) * stride]
        for x in range(surface.get_width()):
            if row[x * 4 + 3]:
                if left is None or x < left:
                    left = x
                if top is None:
                    top = y
                if x > right:
                    right = x
                bottom = y
    return left, top, right, bottom


def test_production_label_layout_resolves_all_chinese_glyphs():
    context = cairo.Context(_surface())
    for text in CHINESE_LABELS:
        layout, ink = _fit_label_layout(context, text, 42.0, 400.0, 80.0)
        assert layout is not None, text
        assert layout.get_unknown_glyphs_count() == 0, text
        assert ink.width > 0 and ink.height > 0, text


def test_production_label_layout_shrinks_to_the_label_box():
    context = cairo.Context(_surface())
    layout, ink = _fit_label_layout(context, "快捷菜单", 200.0, 120.0, 48.0)

    assert layout is not None
    # Pango pixel ink extents round outwards, hence the one-pixel tolerance.
    assert ink.width <= 121
    assert ink.height <= 49


def test_label_centers_ink_extents_in_the_control():
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 200)
    _label(cairo.Context(surface), "指南", 200.0, 100.0, 60.0, 300.0, 80.0, 1.0)

    left, top, right, bottom = _alpha_bounds(surface)
    assert left is not None
    assert abs((left + right) / 2.0 - 200.0) <= 2.0
    assert abs((top + bottom) / 2.0 - 100.0) <= 2.0


def test_default_layout_renders_all_controls_to_an_image_surface():
    layout = default_layout()
    assert len(layout) == 19

    surface = _surface()
    draw_controls(cairo.Context(surface), WIDTH, HEIGHT, _message())

    assert surface.get_width() == WIDTH
    assert surface.get_height() == HEIGHT
    assert len(surface.get_data()) == surface.get_stride() * HEIGHT
    assert _opaque_pixel_count(surface) > 1000


def test_invisible_message_clears_previous_frame():
    surface = _surface()
    context = cairo.Context(surface)

    draw_controls(context, WIDTH, HEIGHT, _message(visible=True))
    assert _opaque_pixel_count(surface) > 0

    draw_controls(context, WIDTH, HEIGHT, _message(visible=False))
    assert _opaque_pixel_count(surface) == 0


def test_zero_sized_ellipse_is_a_safe_noop():
    surface = _surface()
    context = cairo.Context(surface)

    _ellipse_path(context, 10.0, 10.0, 0.0, 20.0)
    _ellipse_path(context, 10.0, 10.0, 20.0, 0.0)
    _ellipse_path(context, 10.0, 10.0, 0.0, 0.0)

    # A zero radius declines to add a sub-path instead of raising.
    assert not context.has_current_point()
