"""Lifecycle tests for the root touch daemon.

Only hardware boundaries are faked: the touch device, uinput source, sink,
InputPlumber bridge, focus monitor, and overlay process.  The real engine and
policy code run, so a lifecycle bug that leaves a pressed control or a grab
behind is visible.
"""

import io
import json
import os
import sys
import types
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch.daemon import Daemon  # noqa: E402
from py_modules.freedom_touch.engine import TouchEngine  # noqa: E402

EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
MT_SLOT = 47
MT_POSITION_X = 53
MT_POSITION_Y = 54
MT_TRACKING_ID = 57

# One deliberately off-centre control: scaling moves its edges away from the
# raw rectangle, so a point near either edge distinguishes the two geometries.
OFFCENTER_LAYOUT = [
    {
        "id": "btn_a",
        "kind": "button",
        "x": 0.10,
        "y": 0.10,
        "w": 0.20,
        "h": 0.20,
        "output": "a",
        "label": "A",
    }
]


class FakeEvent:
    def __init__(self, event_type, code, value):
        self.type = event_type
        self.code = code
        self.value = value


class FakeTouch:
    def __init__(self):
        self.path = "/dev/input/event7"
        self.events = []
        self.grabs = 0
        self.ungrabs = 0
        self.closed = False
        # Real evdev exposes currently pressed keys.  Tests that need a
        # BTN_TOUCH boundary inject 330 here; contact tracking covers the
        # multi-touch stream itself.
        self.keys = set()
        self._read_fd, self._write_fd = os.pipe()
        os.set_blocking(self._read_fd, False)

    def fileno(self):
        return self._read_fd

    def absinfo(self, _code):
        return types.SimpleNamespace(min=0, max=1000)

    def active_keys(self):
        return sorted(self.keys)

    def grab(self):
        self.grabs += 1

    def ungrab(self):
        self.ungrabs += 1

    def read(self):
        batch = self.events
        self.events = []
        return iter(batch)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in (self._read_fd, self._write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeSource:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeSink:
    def __init__(self):
        self.batches = []
        self.neutralized = 0
        self.closed = 0
        self.fail = False

    def emit(self, events):
        self.batches.append(list(events))
        if self.fail:
            raise RuntimeError("sink emit failed")

    def neutralize(self):
        self.neutralized += 1

    def close(self):
        self.closed += 1


class FakeBridge:
    def __init__(self):
        self._own_path = "/org/shadowblip/InputPlumber/composite/freedom"
        self.discovered = []

    @property
    def own_path(self):
        return self._own_path

    def discover(self, source_path):
        self.discovered.append(source_path)
        return True, ""


class FakeFocus:
    def __init__(self):
        self.apps = (12345, 12345)
        self.closed = False

    def get_focus(self):
        return self.apps

    def close(self):
        self.closed = True


class FakeHaptics:
    """Injected motor adapter; never opens a real device."""

    def __init__(self, enabled=True):
        self.available = True
        self.detail = ""
        self.enabled = bool(enabled)
        self.refreshed = []
        self.consumed = []
        self.strengths = []
        self.previews = []
        self.stops = 0
        self.closed = 0

    def set_enabled(self, value):
        self.enabled = bool(value)

    def set_strength(self, percent):
        self.strengths.append(percent)

    def preview(self, now):
        self.previews.append(now)

    def refresh(self, now):
        self.refreshed.append(now)
        return self.available

    def consume(self, events, now):
        if self.enabled:
            self.consumed.append((list(events), now))

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed += 1


class FakeOverlay:
    def __init__(self, argv, cwd, env, uid, gid, groups):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.uid = uid
        self.gid = gid
        self.groups = groups
        self.ready = True
        self.touch_routing = True
        self.actions = []
        self.alive_flag = True
        self.sent = []
        self.closed = False

    def poll_ready(self):
        return None

    def drain_actions(self):
        actions = self.actions
        self.actions = []
        return actions

    def alive(self):
        return self.alive_flag and not self.closed

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


class Harness:
    def __init__(self, stdin_fd=None, install_signals=False):
        self.now = 1000.0
        self.stdin_fd = stdin_fd
        self.install_signals = install_signals
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.touch = FakeTouch()
        self.source = FakeSource()
        self.sink = FakeSink()
        self.bridge = FakeBridge()
        self.focus = FakeFocus()
        self.overlay = FakeOverlay([], "", {}, 1000, 1000, [1000])
        self.haptics = FakeHaptics()
        self.overlay_args = None
        self.daemon = self._build()

    def _build(self):
        def make_overlay(argv, cwd, env, uid, gid, groups):
            self.overlay_args = (argv, cwd, env, uid, gid, groups)
            return self.overlay

        def make_haptics(*, enabled=True):
            self.haptics.enabled = bool(enabled)
            return self.haptics

        return Daemon(
            "armada",
            1000,
            1000,
            "/home/armada",
            str(PLUGIN_ROOT),
            source_factory=lambda: (self.source, "/dev/input/event9"),
            bridge_factory=lambda: self.bridge,
            sink_factory=lambda _ui, _bridge: self.sink,
            environment_reader=lambda: {
                "ARMADA_PRIMARY_TOUCHSCREEN": "Touch",
                "ARMADA_PANEL_ORIENTATION": "normal",
            },
            touch_factory=lambda _values: self.touch,
            rotation_resolver=lambda _values: 0,
            session_finder=lambda _uid: {
                "DISPLAY": ":0",
                "XAUTHORITY": "/tmp/xauth",
                "GDK_BACKEND": "x11",
            },
            focus_factory=lambda _env: self.focus,
            overlay_feature=lambda: True,
            overlay_factory=make_overlay,
            haptics_factory=make_haptics,
            stdin_fd=self.stdin_fd,
            stdout=self.stdout,
            stderr=self.stderr,
            clock=lambda: self.now,
            install_signals=self.install_signals,
        )

    def activate(self):
        self.activate_with({})

    def activate_with(self, settings):
        payload = {"enabled": True}
        payload.update(settings)
        self.daemon.handle_message(
            {"type": "settings", "settings": payload}
        )
        self.daemon.handle_message({"type": "heartbeat", "appid": "12345"})
        self.focus.apps = (12345, 12345)
        self.daemon.tick(self.now)

    def push_frame(self, events):
        self.touch.events.extend(events)

    def raw_point(self, x, y):
        """Invert transform_point for the fake panel's absinfo and rotation."""

        abs_x = self.touch.absinfo(MT_POSITION_X)
        abs_y = self.touch.absinfo(MT_POSITION_Y)
        rotation = int(self.daemon.rotation)
        if rotation == 90:
            x, y = 1.0 - y, x
        elif rotation == 180:
            x, y = 1.0 - x, 1.0 - y
        elif rotation == 270:
            x, y = y, 1.0 - x
        raw_x = abs_x.min + x * (abs_x.max - abs_x.min)
        raw_y = abs_y.min + y * (abs_y.max - abs_y.min)
        return int(round(raw_x)), int(round(raw_y))

    def begin_touch_at(self, x, y):
        self.push_frame(
            [
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, 1),
                FakeEvent(EV_ABS, MT_POSITION_X, x),
                FakeEvent(EV_ABS, MT_POSITION_Y, y),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
            ]
        )
        self.now += 0.02
        self.daemon.tick(self.now)

    def press_button_and_stick(self):
        controls = {
            item["id"]: item for item in self.daemon.effective_controls
        }
        button = controls["btn_a"]
        stick = controls["stick_l"]
        button_x, button_y = self.raw_point(
            button["x"] + button["w"] / 2.0,
            button["y"] + button["h"] / 2.0,
        )
        stick_x, stick_y = self.raw_point(
            stick["x"] + stick["w"] * 0.75,
            stick["y"] + stick["h"] * 0.75,
        )
        self.push_frame(
            [
                FakeEvent(EV_ABS, MT_SLOT, 0),
                FakeEvent(EV_ABS, MT_TRACKING_ID, 1),
                FakeEvent(EV_ABS, MT_POSITION_X, button_x),
                FakeEvent(EV_ABS, MT_POSITION_Y, button_y),
                FakeEvent(EV_ABS, MT_SLOT, 1),
                FakeEvent(EV_ABS, MT_TRACKING_ID, 2),
                FakeEvent(EV_ABS, MT_POSITION_X, stick_x),
                FakeEvent(EV_ABS, MT_POSITION_Y, stick_y),
                FakeEvent(EV_SYN, SYN_REPORT, 0),
            ]
        )
        self.now += 0.02
        self.daemon.tick(self.now)

    def status_lines(self):
        lines = []
        for line in self.stdout.getvalue().splitlines():
            if line.strip():
                lines.append(json.loads(line))
        return lines


class DaemonLifecycleTests(unittest.TestCase):
    def assert_only_status_stdout(self, harness):
        for message in harness.status_lines():
            self.assertEqual(message["type"], "status")
            self.assertEqual(
                set(message["status"]),
                {
                    "state",
                    "detail",
                    "capturing",
                    "controls_visible",
                    "source_path",
                    "composite_path",
                    "device",
                    "rotation",
                    "haptics_available",
                    "haptics_detail",
                },
            )

    def test_capture_releases_controls_and_ungrabs_on_qam_then_recaptures(self):
        harness = Harness()
        harness.activate()
        daemon = harness.daemon

        self.assertTrue(daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)
        self.assertEqual(harness.overlay.sent[-1]["visible"], True)

        harness.press_button_and_stick()
        batch = harness.sink.batches[-1]
        self.assertIn(("button", "a", True), batch)
        self.assertTrue(
            any(
                event[0] == "axis" and event[1] == "lx" and event[2] != 0
                for event in batch
            )
        )

        # Steam's Quick Access Menu is frontmost: the game id still matches
        # the heartbeat but QAM must never authorise capture.
        harness.focus.apps = (769, 12345)
        harness.now += 0.1
        daemon.tick(harness.now)

        self.assertFalse(daemon.capturing)
        self.assertFalse(daemon.engine.active)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertFalse(harness.touch.closed)
        self.assertGreaterEqual(harness.sink.neutralized, 1)
        released = harness.sink.batches[-1]
        self.assertIn(("button", "a", False), released)
        self.assertIn(("axis", "lx", 0.0), released)
        self.assertEqual(harness.overlay.sent[-1]["visible"], False)

        harness.focus.apps = (12345, 12345)
        harness.now += 0.1
        daemon.tick(harness.now)

        self.assertTrue(daemon.capturing)
        self.assertEqual(harness.touch.grabs, 2)
        self.assertEqual(harness.overlay.sent[-1]["visible"], True)
        self.assert_only_status_stdout(harness)

    def test_no_grab_before_overlay_reports_ready(self):
        harness = Harness()
        harness.overlay.ready = False
        harness.activate()

        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 0)

        harness.overlay.ready = True
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

    def test_overlay_is_spawned_unprivileged_with_expected_arguments(self):
        harness = Harness()
        harness.activate()
        argv, cwd, env, uid, gid, groups = harness.overlay_args

        self.assertEqual(
            argv, ["/usr/bin/python3", "-m", "py_modules.freedom_touch.overlay"]
        )
        self.assertEqual(cwd, str(PLUGIN_ROOT))
        self.assertEqual(uid, 1000)
        self.assertEqual(gid, 1000)
        self.assertIn(1000, groups)
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["PYTHONPATH"], str(PLUGIN_ROOT))
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertIn("PATH", env)

    def test_stdin_eof_closes_every_owned_resource(self):
        read_fd, write_fd = os.pipe()
        harness = Harness(stdin_fd=read_fd)
        harness.activate()
        os.close(write_fd)

        try:
            code = harness.daemon.run()
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

        self.assertEqual(code, 0)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertGreaterEqual(harness.touch.ungrabs, 1)
        self.assertTrue(harness.touch.closed)
        self.assertTrue(harness.focus.closed)
        self.assertTrue(harness.overlay.closed)
        self.assertTrue(harness.source.closed)
        self.assertEqual(harness.sink.closed, 1)
        self.assertGreaterEqual(harness.sink.neutralized, 1)

    def test_runtime_exception_releases_pressed_controls_and_grab(self):
        read_fd, write_fd = os.pipe()
        harness = Harness(stdin_fd=read_fd)
        harness.activate()
        harness.press_button_and_stick()

        def explode(*_args, **_kwargs):
            raise RuntimeError("unexpected loop failure")

        harness.daemon.tick = explode

        try:
            code = harness.daemon.run()
        finally:
            os.close(write_fd)
            try:
                os.close(read_fd)
            except OSError:
                pass

        self.assertEqual(code, 1)
        self.assertFalse(harness.daemon.engine.active)
        self.assertGreaterEqual(harness.touch.ungrabs, 1)
        self.assertTrue(harness.touch.closed)
        self.assertTrue(harness.overlay.closed)
        self.assertTrue(harness.source.closed)
        self.assertGreaterEqual(harness.sink.neutralized, 1)
        released = harness.sink.batches[-1]
        self.assertIn(("button", "a", False), released)
        self.assertIn(("axis", "lx", 0.0), released)
        states = [item["status"]["state"] for item in harness.status_lines()]
        self.assertIn("unavailable", states)

    def test_disable_releases_and_closes_all_hardware(self):
        harness = Harness()
        harness.activate()
        harness.press_button_and_stick()

        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": False}}
        )

        self.assertFalse(harness.daemon.capturing)
        self.assertIsNone(harness.daemon.engine)
        self.assertTrue(harness.touch.closed)
        self.assertTrue(harness.overlay.closed)
        self.assertTrue(harness.focus.closed)
        self.assertTrue(harness.source.closed)
        self.assertEqual(harness.sink.closed, 1)
        self.assertGreaterEqual(harness.sink.neutralized, 1)
        self.assertEqual(harness.status_lines()[-1]["status"]["state"], "disabled")

    def test_heartbeat_null_releases_before_next_frame(self):
        harness = Harness()
        harness.activate()
        harness.press_button_and_stick()

        harness.daemon.handle_message({"type": "heartbeat", "appid": None})

        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertGreaterEqual(harness.sink.neutralized, 1)
        self.assertEqual(harness.overlay.sent[-1]["visible"], False)

    def test_scale_0_6_shrinks_hits_to_the_overlay_geometry(self):
        harness = Harness()
        harness.activate_with({"layout": OFFCENTER_LAYOUT, "scale": 0.6})

        effective = harness.overlay.sent[-1]["layout"]
        self.assertEqual(harness.daemon.engine.controls, effective)
        self.assertAlmostEqual(effective[0]["x"], 0.14)
        self.assertAlmostEqual(effective[0]["w"], 0.12)

        # 0.12 lies inside the raw rectangle (0.10..0.30) but outside the
        # scaled one (0.14..0.26): the old raw hit must no longer press.
        harness.begin_touch_at(120, 120)
        self.assertEqual(harness.sink.batches, [])

        raw = TouchEngine(OFFCENTER_LAYOUT)
        raw.set_active(True)
        self.assertEqual(
            raw.begin(1, 0.12, 0.12, 0.0), [("button", "a", True)]
        )

    def test_scale_1_4_grows_hits_to_the_overlay_geometry(self):
        harness = Harness()
        harness.activate_with({"layout": OFFCENTER_LAYOUT, "scale": 1.4})

        effective = harness.overlay.sent[-1]["layout"]
        self.assertEqual(harness.daemon.engine.controls, effective)
        self.assertAlmostEqual(effective[0]["x"], 0.06)
        self.assertAlmostEqual(effective[0]["w"], 0.28)

        # 0.32 is outside the raw rectangle (0.10..0.30) but inside the
        # scaled one (0.06..0.34): only the effective geometry hits.
        harness.begin_touch_at(320, 320)
        self.assertIn(("button", "a", True), harness.sink.batches[-1])

        raw = TouchEngine(OFFCENTER_LAYOUT)
        raw.set_active(True)
        self.assertEqual(raw.begin(1, 0.32, 0.32, 0.0), [])


if __name__ == "__main__":
    unittest.main()
