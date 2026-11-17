"""Runtime D-pad mode + overlay palette transport on the current daemon.

These tests pin the *local* parity contract:

* ``TouchEngine`` accepts a trailing ``dpad_mode`` keyword, validates it,
  snapshots the emitted direction for the renderer, and leaves every other
  behaviour (hit stacking, refcounts, safety, aspect, recovery) untouched.
* The daemon rebuilds the engine only when the mode changes, never on a
  palette-only change, and forwards both fields to the overlay message.
* The overlay parser defaults to ``"white"`` and strictly rejects anything
  that is not ``"white"`` / ``"black"``.
* Real Cairo pixels prove white and black palettes differ while the layout,
  hit geometry, and zero-opacity transparency stay identical.

The renderer section executes the production ``draw_controls`` against a
``cairo.ImageSurface`` and skips only when cairo/Pango are missing; the
manager runs it remotely inside the build image.
"""

import sys
import unittest
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_PLUGIN = _TESTS.parent
for _path in (str(_PLUGIN), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from py_modules.freedom_touch import collapse  # noqa: E402
from py_modules.freedom_touch.engine import TouchEngine  # noqa: E402
from py_modules.freedom_touch.layout import default_layout  # noqa: E402
from py_modules.freedom_touch.overlay import (  # noqa: E402
    _default_message,
    parse_message,
)

# Reuse the *current* lifecycle harness so daemon fakes are the production
# doubles, not a parallel copy.
from test_daemon_lifecycle import Harness  # noqa: E402

try:
    import cairo
except ImportError:  # pragma: no cover - depends on the runner image
    cairo = None

try:
    from py_modules.freedom_touch.draw import draw_controls
except (ImportError, ValueError):  # pragma: no cover - GI/Pango missing
    draw_controls = None

WIDTH = 1920
HEIGHT = 1080
BRIGHT_BACKDROP = (0.93, 0.95, 0.97)
SAMPLE_IDS = ("btn_b", "stick_l", "dpad_l", "pad_l")

requires_renderer = pytest.mark.skipif(
    cairo is None or draw_controls is None,
    reason=(
        "cairo + PyGObject/Pango are unavailable in this interpreter; the "
        "manager runs the renderer tests remotely in the build image"
    ),
)


# ---------------------------------------------------------------------------
# engine: trailing dpad_mode argument
# ---------------------------------------------------------------------------


def _dpad_only():
    return [
        {
            "id": "dpad_l",
            "kind": "dpad",
            "x": 0.05,
            "y": 0.60,
            "w": 0.20,
            "h": 0.20,
            "output": "left",
        }
    ]


def _axis(events, name):
    values = [e[2] for e in events if e[0] == "axis" and e[1] == name]
    return values[-1] if values else None


def test_engine_dpad_mode_defaults_to_four_way():
    engine = TouchEngine(_dpad_only())
    engine.set_active(True)
    assert engine.dpad_mode == "four_way"
    # Diagonal leg collapses onto the dominant axis in four-way.
    engine.begin(1, 0.15, 0.70, 0.0)
    events = engine.move(1, 0.23, 0.62, 0.1)
    assert (_axis(events, "dx"), _axis(events, "dy")) in {(1, 0), (0, -1)}


def test_engine_eight_way_keeps_diagonal_axes():
    engine = TouchEngine(_dpad_only(), dpad_mode="eight_way")
    engine.set_active(True)
    engine.begin(1, 0.15, 0.70, 0.0)
    events = engine.move(1, 0.23, 0.62, 0.1)
    assert _axis(events, "dx") == 1
    assert _axis(events, "dy") == -1


def test_engine_rejects_unknown_dpad_mode():
    for bad in ("eight", "FOUR_WAY", "", None, 4, True):
        with pytest.raises(ValueError):
            TouchEngine(_dpad_only(), dpad_mode=bad)


def test_engine_snapshot_reflects_emitted_dpad_direction():
    engine = TouchEngine(_dpad_only(), dpad_mode="eight_way")
    engine.set_active(True)
    engine.begin(1, 0.15, 0.70, 0.0)
    engine.move(1, 0.23, 0.62, 0.1)
    state = engine.snapshot()["controls"]["dpad_l"]
    assert state["pressed"] is True
    # Direction came from the emitted tuple, not the raw finger position.
    assert state["x"] == pytest.approx(1.0)
    assert state["y"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# daemon: engine rebuilds on mode, not on colour
# ---------------------------------------------------------------------------


def _activate_engine(harness, settings):
    harness.activate_with(settings)
    return harness.daemon.engine


def test_daemon_recreates_engine_on_dpad_mode_change():
    harness = Harness()
    first = _activate_engine(harness, {"dpad_mode": "four_way"})
    assert isinstance(first, TouchEngine)

    harness.daemon.handle_message(
        {
            "type": "settings",
            "settings": {"enabled": True, "dpad_mode": "eight_way"},
        }
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)

    second = harness.daemon.engine
    assert second is not first
    assert second.dpad_mode == "eight_way"


def test_daemon_palette_change_never_recreates_engine():
    harness = Harness()
    engine = _activate_engine(harness, {"button_color": "black"})
    sent = len(harness.overlay.sent)

    harness.daemon.handle_message(
        {
            "type": "settings",
            "settings": {"enabled": True, "button_color": "white"},
        }
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)

    assert harness.daemon.engine is engine
    # Exactly one extra visual frame, carrying the new palette only.
    assert len(harness.overlay.sent) >= sent + 1
    latest = harness.overlay.sent[-1]
    assert latest["button_color"] == "white"
    assert latest["button_color"] != "black"


def test_daemon_visual_defaults_palette_white():
    harness = Harness()
    _activate_engine(harness, {})
    assert harness.overlay.sent[-1]["button_color"] == "white"


def test_daemon_make_engine_passes_dpad_mode_by_keyword():
    harness = Harness()
    captured = {}

    def spy(layout, deadzone, edge_swipes, aspect_ratio, **kwargs):
        captured.update(
            deadzone=deadzone,
            edge_swipes=edge_swipes,
            aspect_ratio=aspect_ratio,
            kwargs=dict(kwargs),
        )
        return TouchEngine(
            layout,
            deadzone,
            edge_swipes,
            aspect_ratio,
            **kwargs,
        )

    harness.daemon._engine_factory = spy
    harness.activate_with({"dpad_mode": "eight_way"})

    # The daemon must pass dpad_mode as a *keyword*, never a 5th positional
    # argument, so older engine callers stay intact.
    assert captured["kwargs"].get("dpad_mode") == "eight_way"
    assert harness.daemon.engine.dpad_mode == "eight_way"


def test_daemon_recreated_engine_carries_aspect_and_edge_flags():
    harness = Harness()
    harness.activate_with({"dpad_mode": "four_way", "edge_swipes": True})
    aspect = harness.daemon.engine.aspect_ratio
    harness.daemon.handle_message(
        {
            "type": "settings",
            "settings": {
                "enabled": True,
                "dpad_mode": "eight_way",
                "edge_swipes": True,
            },
        }
    )
    harness.now += 0.02
    harness.daemon.tick(harness.now)
    rebuilt = harness.daemon.engine
    assert rebuilt.dpad_mode == "eight_way"
    assert rebuilt.aspect_ratio == aspect
    assert rebuilt.edge_swipes is True


# ---------------------------------------------------------------------------
# overlay parser: palette default and strict validation
# ---------------------------------------------------------------------------


def _message(button_color="white", opacity=1.0):
    message = _default_message()
    message["opacity"] = opacity
    if button_color is None:
        message.pop("button_color", None)
    else:
        message["button_color"] = button_color
    return message


def test_overlay_default_message_includes_white_palette():
    assert _default_message()["button_color"] == "white"


def test_overlay_parse_defaults_missing_palette_to_white():
    legacy = _message(None)
    assert "button_color" not in legacy
    assert parse_message(legacy)["button_color"] == "white"


def test_overlay_parse_preserves_known_palettes():
    assert parse_message(_message("white"))["button_color"] == "white"
    assert parse_message(_message("black"))["button_color"] == "black"


@pytest.mark.parametrize(
    "value",
    ("White", "BLACK", " black", "black ", "", None, 1, True, [], {}),
)
def test_overlay_parse_rejects_unknown_palettes(value):
    broken = _message("black")
    broken["button_color"] = value
    with pytest.raises(ValueError):
        parse_message(broken)


# ---------------------------------------------------------------------------
# real Cairo pixels
# ---------------------------------------------------------------------------


def _control(message, control_id):
    for control in message["layout"]:
        if control["id"] == control_id:
            return control
    raise KeyError(control_id)


def _rect(message, control_id):
    control = _control(message, control_id)
    return (
        control["x"] * WIDTH,
        control["y"] * HEIGHT,
        control["w"] * WIDTH,
        control["h"] * HEIGHT,
    )


def _inset(rect, fraction):
    left, top, width, height = rect
    return (left + width * fraction, top + height * fraction)


def _pixel(surface, x, y):
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    index = int(y) * stride + int(x) * 4
    blue, green, red, alpha = data[index:index + 4]
    return red / 255.0, green / 255.0, blue / 255.0, alpha / 255.0


def _surface(message):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    draw_controls(cairo.Context(surface), WIDTH, HEIGHT, message)
    surface.flush()
    return surface


def _composited(message, rgb):
    surface = _surface(message)
    context = cairo.Context(surface)
    context.save()
    context.set_operator(cairo.OPERATOR_DEST_OVER)
    context.set_source_rgb(*rgb)
    context.paint()
    context.restore()
    surface.flush()
    return surface


def _opaque_pixels(surface):
    data = bytes(surface.get_data())
    stride = surface.get_stride()
    total = 0
    for y in range(surface.get_height()):
        row = data[y * stride:(y + 1) * stride]
        total += sum(1 for alpha in row[3::4] if alpha)
    return total


def _midpoints(message, control_ids):
    return {
        cid: _inset(_rect(message, cid), 0.5)
        for cid in control_ids
    }


@requires_renderer
def test_white_and_black_palettes_differ_on_the_same_layout():
    white = parse_message(_message("white"))
    black = parse_message(_message("black"))

    # The palette must not touch geometry.
    assert [dict(control) for control in white["layout"]] == [
        dict(control) for control in black["layout"]
    ]

    white_surface = _composited(white, BRIGHT_BACKDROP)
    black_surface = _composited(black, BRIGHT_BACKDROP)

    for cid, (x, y) in _midpoints(white, SAMPLE_IDS).items():
        wr = _pixel(white_surface, x, y)[:3]
        br = _pixel(black_surface, x, y)[:3]
        assert wr != br, cid
        # Black is measurably darker than white on a bright backdrop.
        assert sum(br) < sum(wr), cid


@requires_renderer
def test_palette_change_does_not_move_hit_geometry():
    white = parse_message(_message("white"))
    black = parse_message(_message("black"))

    for cid in SAMPLE_IDS:
        wc, bc = _control(white, cid), _control(black, cid)
        for key in ("x", "y", "w", "h", "kind", "output", "id"):
            assert wc[key] == bc[key], (cid, key)


@requires_renderer
@pytest.mark.parametrize("color", ("white", "black"))
def test_zero_opacity_stays_fully_transparent(color):
    surface = _surface(_message(color, opacity=0.0))
    assert _opaque_pixels(surface) == 0
    assert _pixel(surface, 1, 1)[3] == 0.0
    assert _pixel(surface, WIDTH - 2, HEIGHT - 2)[3] == 0.0


@requires_renderer
@pytest.mark.parametrize("color", ("white", "black"))
def test_palette_transparency_outside_controls_is_unchanged(color):
    surface = _surface(_message(color))
    assert _pixel(surface, 2, 2)[3] == 0.0
    assert _pixel(surface, WIDTH - 3, HEIGHT - 3)[3] == 0.0
    # Overlay plane is not full-screen opaque.
    assert _opaque_pixels(surface) < WIDTH * HEIGHT // 2
