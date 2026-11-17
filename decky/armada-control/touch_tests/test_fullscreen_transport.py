import cairo
import pytest

from test_daemon_lifecycle import Harness
from py_modules.freedom_touch.draw import draw_controls
from py_modules.freedom_touch.overlay import parse_message


def _emit_move(h):
    h.activate_with({"right_stick_fullscreen": True, "deadzone": 0.0})
    h.daemon._emit_batch([("begin", 91, *h.raw_point(0.5, 0.45))], h.now)
    h.now += 0.02
    h.daemon._emit_batch([("move", 91, *h.raw_point(0.53, 0.45))], h.now)


def _rx_ry(batch):
    axes = {name: value for kind, name, value in batch if kind == "axis"}
    return axes.get("rx"), axes.get("ry")


def test_option_forwarding_and_recreate():
    h = Harness()
    try:
        h.activate_with({"right_stick_fullscreen": True, "deadzone": 0.0})
        assert h.daemon.engine.right_stick_fullscreen is True
        old = h.daemon.engine
        h.daemon.handle_message({"type": "settings", "settings": {**h.daemon.settings, "right_stick_fullscreen": False}})
        assert h.daemon.engine is not old
        assert h.daemon.engine.right_stick_fullscreen is False
    finally:
        h.daemon.shutdown()


def test_actual_daemon_idle():
    h = Harness()
    try:
        _emit_move(h)
        rx, _ = _rx_ry(h.sink.batches[-1])
        assert rx > 0
        h.now += 0.06
        h.daemon.tick(h.now)
        rx, ry = _rx_ry(h.sink.batches[-1])
        assert rx == 0 and ry == 0
        n = len(h.sink.batches)
        h.now += 0.02
        h.daemon.tick(h.now)
        assert len(h.sink.batches) == n
        assert h.daemon.engine.active is True
    finally:
        h.daemon.shutdown()


def test_actual_daemon_tick_sink_emit_failure_recovers():
    h = Harness()
    try:
        _emit_move(h)
        assert h.daemon.capturing is True
        assert h.sink.closed == 0

        real_emit = h.sink.emit
        failing = {"armed": True}

        def flaky_emit(events):
            if failing["armed"]:
                failing["armed"] = False
                raise RuntimeError("idle tick emit failed")
            return real_emit(events)

        h.sink.emit = flaky_emit
        h.now += 0.06
        h.daemon.tick(h.now)

        assert h.daemon.capturing is False
        assert h.daemon.sink is None
        assert h.sink.closed >= 1
        assert getattr(h.source, "closed", False) is True
        assert h.daemon._stop is False
        assert "touch" in h.daemon._failures
        assert "idle tick emit failed" in h.daemon._failures["touch"]
    finally:
        h.daemon.shutdown()


def test_menu_release():
    h = Harness()
    try:
        _emit_move(h)
        h.focus.apps = (769, 12345)
        h.now += 0.1
        h.daemon.tick(h.now)
        assert h.daemon.capturing is False
        assert h.daemon.engine.active is False
        rx, ry = _rx_ry(h.sink.batches[-1])
        assert rx == 0 and ry == 0
    finally:
        h.daemon.shutdown()


def test_visual_field():
    h = Harness()
    try:
        h.activate_with({"right_stick_fullscreen": True, "deadzone": 0.0})
        assert h.overlay.sent[-1]["right_stick_fullscreen"] is True
        msg = dict(h.overlay.sent[-1])
        assert parse_message(msg)["right_stick_fullscreen"] is True
        legacy = dict(msg)
        legacy.pop("right_stick_fullscreen")
        assert parse_message(legacy)["right_stick_fullscreen"] is False
        with pytest.raises(ValueError):
            parse_message({**msg, "right_stick_fullscreen": "yes"})
    finally:
        h.daemon.shutdown()


def test_cairo_draw_skips_only_right_stick():
    right = {"id": "r", "kind": "stick", "output": "right", "x": 0.7, "y": 0.2, "w": 0.15, "h": 0.24}
    left = {"id": "l", "kind": "stick", "output": "left", "x": 0.1, "y": 0.2, "w": 0.15, "h": 0.24}
    base = {"visible": True, "opacity": 1, "mode": "expanded", "chrome": False, "snapshot": {"controls": {}}}

    def render(controls, msg):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 500)
        ctx = cairo.Context(s)
        draw_controls(ctx, 800, 500, {**msg, "layout": controls})
        s.flush()
        return bytes(s.get_data())

    off_msg = {**base, "right_stick_fullscreen": False}
    on_msg = {**base, "right_stick_fullscreen": True}
    assert any(render([right], off_msg))
    assert not any(render([right], on_msg))
    assert any(render([right, left], on_msg))
