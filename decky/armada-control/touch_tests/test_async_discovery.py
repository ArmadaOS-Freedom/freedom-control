"""Async InputPlumber discovery tests for the production bridge path.

Only the system bus, the uinput device, the touch panel, the focus monitor and
the overlay are doubled.  The daemon, its policy, :class:`InputPlumberBridge`
and :class:`InputSink` are the production objects, so these tests exercise the
real background discovery protocol rather than a fallback double.  The fake
bus holds its GetManagedObjects reply behind an event, which lets a test keep
one query open while it drives the input loop and observes that nothing on the
input thread waits for it.
"""

import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch.daemon import Daemon  # noqa: E402
from py_modules.freedom_touch.input_adapter import (  # noqa: E402
    COMPOSITE_IFACE,
    InputPlumberBridge,
    InputSink,
)

from test_daemon_lifecycle import (  # noqa: E402
    FakeSource,
    Harness,
    RecordingDevice,
)

SOURCE_PATH = "/dev/input/event9"
NEXT_SOURCE_PATH = "/dev/input/event10"
COMPOSITE_PATH = "/org/shadowblip/InputPlumber/composite/freedom"
TARGET_PATH = COMPOSITE_PATH + "/target0"
TARGET_IFACE = InputPlumberBridge.TARGET_IFACE
DEVICE_TYPE = InputPlumberBridge.TARGET_DEVICE_TYPE
COMPOSITE_NAME = "Freedom Touch Controller"

# Only SendTouchEvent is required by the bridge's validation; it is the one
# method this test module actually calls on the composite.
INTROSPECTION_XML = """<?xml version="1.0"?>
<node>
  <interface name="org.shadowblip.Input.CompositeDevice">
    <method name="SendTouchEvent"/>
  </interface>
</node>
"""


class FakeGError(Exception):
    """Stands in for GLib.Error, the type the bridge already handles."""


class FakeCancellable:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def is_cancelled(self):
        with self._lock:
            return self._cancelled


class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeBus:
    """System bus double whose discovery answer waits for an event."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.source_paths = [SOURCE_PATH]
        self.discovery_calls = 0
        self.completed = 0
        self.cancelled = 0
        self.fail_touches = False
        self.discovery_thread = None
        self.connect_thread = None
        self.touches = []
        self.chords = []
        # The first system-bus connection is held behind its own gate, so a
        # test can cancel a probe while that connect is still open.
        self.connect_gate = threading.Event()
        self.connect_gate.set()
        self.connect_cancellable = None
        self.connects = 0
        self.connect_cancelled = 0
        # (method, cancellable) for every call: the live send path must never
        # carry one, whatever the probe did.
        self.calls = []

    def connected(self, thread):
        self.connect_thread = thread
        self.connects += 1
        return self

    def hold_connect(self, cancellable):
        while not self.connect_gate.is_set():
            if cancellable is not None and cancellable.is_cancelled():
                self.connect_cancelled += 1
                raise FakeGError("cancelled")
            self.connect_gate.wait(0.005)

    def call_sync(self, service, path, interface, method, params,
                  reply_type, flags, timeout, cancellable):
        self.calls.append((method, cancellable))
        if method == "GetManagedObjects":
            self.discovery_thread = threading.current_thread()
            self.discovery_calls += 1
            self._hold(cancellable)
            self.completed += 1
            return _Reply((self._objects(),))
        if method == "Get":
            return _Reply((DEVICE_TYPE,))
        if method == "Introspect":
            return _Reply((INTROSPECTION_XML,))
        if method == "SendTouchEvent":
            if self.fail_touches:
                raise FakeGError("touch channel down")
            self.touches.append(params[2])
            return _Reply(None)
        if method == "SendButtonChord":
            self.chords.append(params[2][0])
        return _Reply(None)

    def _hold(self, cancellable):
        while not self.gate.is_set():
            if cancellable is not None and cancellable.is_cancelled():
                self.cancelled += 1
                raise FakeGError("cancelled")
            self.gate.wait(0.005)

    def _objects(self):
        return {
            COMPOSITE_PATH: {
                COMPOSITE_IFACE: {
                    "Name": COMPOSITE_NAME,
                    "SourceDevicePaths": list(self.source_paths),
                    "TargetDevices": [TARGET_PATH],
                },
            },
            TARGET_PATH: {TARGET_IFACE: {"DeviceType": DEVICE_TYPE}},
        }


def _fake_gi_modules(bus):
    """Return sys.modules entries serving ``gi.repository`` from *bus*."""

    class Gio:
        BusType = types.SimpleNamespace(SYSTEM=1)
        DBusCallFlags = types.SimpleNamespace(NONE=0)
        Cancellable = FakeCancellable

        @staticmethod
        def bus_get_sync(_bus_type, cancellable):
            bus.connect_cancellable = cancellable
            bus.connected(threading.current_thread())
            bus.hold_connect(cancellable)
            return bus

    glib = types.SimpleNamespace(
        Error=FakeGError,
        VariantType=types.SimpleNamespace(new=lambda _signature: "type"),
        Variant=lambda *args: ("variant",) + args,
    )
    repository = types.ModuleType("gi.repository")
    repository.Gio = Gio
    repository.GLib = glib
    package = types.ModuleType("gi")
    package.repository = repository
    return {"gi": package, "gi.repository": repository}


class JumpingClock:
    """Injected clock: the tick's own work consumes ``jump`` seconds.

    ``settle`` samples are returned before the jump, because the tick's first
    sample is its own checkpoint: the block has to happen *between* two
    samples to be measurable.  ``offset`` puts the clock in a different
    absolute domain from the frame time the test passes to ``tick``.
    """

    def __init__(self, now, jump=5.0, offset=0.0, settle=1):
        self.now = float(now) + float(offset)
        self.jump = float(jump)
        self.settle = int(settle)
        self.samples = 0
        self.jumps = 1

    def __call__(self):
        self.samples += 1
        if self.samples > self.settle and self.jumps > 0:
            self.jumps -= 1
            self.now += self.jump
        return self.now


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class DiscoveryHarness(Harness):
    """Lifecycle harness wiring the real bridge and sink to the fake bus.

    The controller source is created fresh on every request so a test can
    replace it, and each bridge is a real :class:`InputPlumberBridge` that
    connects lazily on its own first use.
    """

    def __init__(self, bus):
        self.bus = bus
        self.paths = [SOURCE_PATH]
        self.sources = []
        self.bridges = []
        self.sinks = []
        self.device = RecordingDevice()
        super().__init__()
        self.daemon._source_factory = self._next_source
        self.daemon._bridge_factory = self._next_bridge
        self.daemon._sink_factory = self._make_sink

    def _next_source(self):
        source = FakeSource()
        self.sources.append(source)
        index = min(len(self.sources) - 1, len(self.paths) - 1)
        return source, self.paths[index]

    def _next_bridge(self):
        bridge = InputPlumberBridge()
        self.bridges.append(bridge)
        return bridge

    def _make_sink(self, _ui, bridge):
        sink = InputSink(self.device, bridge)
        self.sinks.append(sink)
        return sink

    def pump(self, predicate, ticks=250):
        """Tick until *predicate* holds, giving the worker thread real time."""

        for _ in range(ticks):
            if predicate():
                return True
            self.now += 0.02
            self.daemon.tick(self.now)
            time.sleep(0.002)
        return predicate()

    def ready(self):
        """Finish the first discovery round and capture the panel."""

        self.bus.gate.set()
        if not self.pump(lambda: self.daemon.capturing):
            raise AssertionError("daemon never captured")
        return self


class ProbeProtocolTests(unittest.TestCase):
    """The two bridge methods the daemon uses, without any daemon."""

    def setUp(self):
        self.bus = FakeBus()
        patcher = mock.patch.dict(sys.modules, _fake_gi_modules(self.bus))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_probe_discovers_without_touching_the_live_bridge(self):
        live = InputPlumberBridge(bus=self.bus)
        probe = live.make_probe()
        self.assertIsNot(probe, live)
        self.assertIsNone(live.own_path)
        self.assertIsNone(probe.own_path)

        ok, detail = probe.discover(SOURCE_PATH)

        self.assertTrue(ok, detail)
        self.assertEqual(probe.own_path, COMPOSITE_PATH)
        # The worker's object changed; the live one still owes its verdict
        # to the input thread.
        self.assertIsNone(live.own_path)

        live.adopt_probe(probe)
        self.assertEqual(live.own_path, COMPOSITE_PATH)

    def test_adopting_a_failed_probe_clears_the_live_identity(self):
        live = InputPlumberBridge(bus=self.bus)
        probe = live.make_probe()
        self.assertTrue(probe.discover(SOURCE_PATH)[0])
        live.adopt_probe(probe)
        self.assertEqual(live.own_path, COMPOSITE_PATH)

        # The composite disappears: its round fails and clears the identity.
        self.bus.source_paths = []
        failed = live.make_probe()
        ok, _detail = failed.discover(SOURCE_PATH)
        self.assertFalse(ok)
        live.adopt_probe(failed)
        self.assertIsNone(live.own_path)

    def test_cancelling_a_probe_ends_its_query(self):
        live = InputPlumberBridge(bus=self.bus)
        probe = live.make_probe()
        self.bus.gate.clear()
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(probe.discover(SOURCE_PATH))
        )
        worker.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not outcome:
                if self.bus.discovery_calls:
                    probe.cancel()
                time.sleep(0.005)
            worker.join(2.0)
        finally:
            self.bus.gate.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.bus.discovery_calls, 1)
        self.assertEqual(self.bus.cancelled, 1)
        self.assertFalse(outcome[0][0])
        self.assertIsNone(probe.own_path)

    def test_run_probe_returns_the_probe_with_its_verdict(self):
        live = InputPlumberBridge(bus=self.bus)
        probe = live.make_probe()

        finished, ok, detail = Daemon._run_probe(probe, SOURCE_PATH)

        # The worker's shape is exactly what the input thread unpacks.
        self.assertIs(finished, probe)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, "")
        self.assertEqual(probe.own_path, COMPOSITE_PATH)

        self.bus.source_paths = []
        failed = live.make_probe()

        finished, ok, detail = Daemon._run_probe(failed, SOURCE_PATH)

        self.assertIs(finished, failed)
        self.assertFalse(ok)
        self.assertIn("no unique composite", detail)
        self.assertIsNone(failed.own_path)

    def test_cancelling_a_first_connection_ends_only_the_probe(self):
        live = InputPlumberBridge()
        probe = live.make_probe()
        self.bus.connect_gate.clear()
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(probe.discover(SOURCE_PATH))
        )
        worker.start()
        try:
            deadline = time.monotonic() + 2.0
            while (
                time.monotonic() < deadline
                and self.bus.connect_cancellable is None
            ):
                time.sleep(0.005)
            self.assertIsNotNone(self.bus.connect_cancellable)
            # The first connection carries the probe's own cancellable.
            self.assertIs(self.bus.connect_cancellable, probe._cancellable)
            probe.cancel()
            worker.join(2.0)
        finally:
            self.bus.connect_gate.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.bus.connect_cancelled, 1)
        self.assertFalse(outcome[0][0])
        # The abandoned connect left no handle on either object.
        self.assertIsNone(probe._bus)
        self.assertIsNone(live._bus)
        self.assertIsNone(live.own_path)

    def test_the_live_bridge_never_carries_a_cancellable(self):
        live = InputPlumberBridge(bus=self.bus)
        probe = live.make_probe()
        probe.cancel()  # Nothing in flight: only this probe's flag flips.

        self.assertTrue(live.discover(SOURCE_PATH)[0])
        self.assertTrue(
            live.send_touch(
                "Touchpad:LeftPad:Motion", 0, True, 0.5, 0.5, 0.5
            )
        )

        self.assertEqual(live.own_path, COMPOSITE_PATH)
        self.assertEqual(self.bus.calls[-1][0], "SendTouchEvent")
        # No live call, discovery or send, ever carries a cancellable.
        self.assertEqual(
            [call[1] for call in self.bus.calls],
            [None] * len(self.bus.calls),
        )


class AsyncDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        patcher = mock.patch.dict(sys.modules, _fake_gi_modules(self.bus))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.harness = DiscoveryHarness(self.bus)
        # The first round is held open until a test lets it answer.
        self.bus.gate.clear()
        # This activation must return while that round is still blocked.  If a
        # regression ever discovered on the input thread instead of the
        # worker, activate() would wait inside the fake bus for this very gate
        # and the method body would never get to start its own watchdog.  A
        # short, generous margin opens the bus so such an implementation fails
        # an assertion instead of wedging the whole run.  A correct one
        # returns immediately and the timer is disarmed.
        watchdog = threading.Timer(5.0, self.bus.gate.set)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        try:
            self.harness.activate()
        finally:
            watchdog.cancel()
        if not self.wait_for(lambda: self.bus.discovery_thread is not None):
            raise AssertionError("background discovery never started")

    def tearDown(self):
        # A held round owns a non-daemon worker: never leave one behind.
        self.bus.gate.set()
        self.harness.daemon.shutdown()

    def wait_for(self, predicate, timeout=2.0):
        return _wait_for(predicate, timeout)

    def test_a_gated_query_never_holds_the_input_tick(self):
        harness = self.harness
        watchdog = threading.Timer(5.0, self.bus.gate.set)
        watchdog.start()
        self.addCleanup(watchdog.cancel)

        started = time.monotonic()
        harness.now += 1.0
        harness.daemon.tick(harness.now)
        spent = time.monotonic() - started

        self.assertLess(spent, 1.0)
        self.assertTrue(self.wait_for(lambda: self.bus.discovery_calls == 1))
        self.assertIsNotNone(harness.daemon._probe_future)
        self.assertFalse(harness.daemon._probe_future.done())
        self.assertFalse(harness.daemon.capturing)
        self.assertIsNone(harness.daemon.bridge.own_path)
        # Both the query and its lazy bus connection belong to the worker.
        self.assertIsNot(self.bus.discovery_thread, threading.current_thread())
        self.assertIs(self.bus.connect_thread, self.bus.discovery_thread)
        self.assertTrue(harness.status_lines())

    def test_a_finished_round_applies_only_on_the_next_input_tick(self):
        harness = self.harness
        future = harness.daemon._probe_future
        self.bus.gate.set()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not future.done():
            time.sleep(0.005)

        self.assertTrue(future.done())
        self.assertIsNone(harness.daemon.bridge.own_path)
        self.assertFalse(harness.daemon.capturing)

        harness.now += 0.02
        harness.daemon.tick(harness.now)

        # The successful verdict is really adopted: identity, sink and status
        # all come from the round that finished on the worker.
        self.assertEqual(harness.daemon.bridge.own_path, COMPOSITE_PATH)
        self.assertIsNotNone(harness.daemon.sink)
        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(
            harness.status_lines()[-1]["status"]["composite_path"],
            COMPOSITE_PATH,
        )

    def test_one_round_in_flight_never_queues_another(self):
        harness = self.harness
        harness.now += 1.0
        harness.daemon.tick(harness.now)
        executor = harness.daemon._executor
        self.assertIsNotNone(executor)

        for _ in range(6):
            harness.now += 1.0
            harness.daemon.tick(harness.now)

        self.assertEqual(self.bus.discovery_calls, 1)
        self.assertIsNotNone(harness.daemon._probe_future)
        self.assertIs(harness.daemon._executor, executor)

    def test_a_pending_round_keeps_the_live_channel_usable(self):
        harness = self.harness.ready()
        self.assertTrue(harness.daemon.capturing)

        # A second round starts while the composite is still live.
        self.bus.gate.clear()
        harness.now += 1.0
        harness.daemon.tick(harness.now)
        self.assertIsNotNone(harness.daemon._probe_future)
        self.assertFalse(harness.daemon._probe_future.done())

        point = harness.control_point("pad_l")
        harness.begin_touch_at(*point)
        harness.end_touch()

        # uinput forwarding is independent of the query as well.
        harness.begin_touch_at(*harness.control_point("btn_a"))
        self.assertIn((1, 304, 1), harness.device.writes)
        harness.end_touch()
        self.assertIn((1, 304, 0), harness.device.writes)

        # Press and release both reached InputPlumber while the query was
        # still open, and the live identity was never reset by the worker.
        self.assertEqual(
            [touch[2] for touch in self.bus.touches], [True, False]
        )
        self.assertEqual(harness.daemon.bridge.own_path, COMPOSITE_PATH)
        self.assertTrue(harness.status_lines())

    def test_a_pending_round_does_not_block_a_release_retry(self):
        harness = self.harness.ready()
        bridge = harness.daemon.bridge

        harness.begin_touch_at(*harness.control_point("pad_l"))
        self.assertEqual([touch[2] for touch in self.bus.touches], [True])

        # The menu takes focus while the touch channel is broken, so the
        # release cannot be delivered and stays pending in the sink.
        self.bus.fail_touches = True
        harness.focus.apps = (769, 12345)
        harness.now += 0.1
        harness.daemon.tick(harness.now)
        self.assertFalse(harness.daemon.capturing)
        self.assertTrue(harness.daemon._neutralize_pending)

        # A discovery round is now in flight; that must not hide the live
        # composite from the retry path.
        self.bus.gate.clear()
        harness.now += 1.0
        harness.daemon.tick(harness.now)
        self.assertIsNotNone(harness.daemon._probe_future)

        self.bus.fail_touches = False
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertFalse(harness.daemon._neutralize_pending)
        self.assertEqual(bridge.own_path, COMPOSITE_PATH)
        self.assertEqual(
            [touch[2] for touch in self.bus.touches], [True, False]
        )

    def test_failed_discovery_clears_identity_and_stops_capture(self):
        harness = self.harness.ready()
        bridge = harness.daemon.bridge
        self.assertTrue(harness.daemon.capturing)

        self.bus.source_paths = []
        applied = harness.pump(lambda: bridge.own_path is None)

        self.assertTrue(applied)
        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertIsNone(harness.status_lines()[-1]["status"]["composite_path"])

    def test_a_late_result_cannot_bind_the_next_source(self):
        # This case ends the round through the probe's own cancellation, so
        # its answer comes back failed.  The case below keeps the same answer
        # successful and proves it is dropped for being stale, not for being
        # a failure.
        harness = self.harness
        first_bridge = harness.daemon.bridge
        self.assertIsNotNone(harness.daemon._probe_future)

        # The controller source is destroyed and a different one appears.
        harness.paths.append(NEXT_SOURCE_PATH)
        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": False}}
        )
        self.assertIsNone(harness.daemon.bridge)
        self.assertIsNone(harness.daemon.ui)
        self.assertTrue(self.wait_for(lambda: self.bus.cancelled))
        self.assertEqual(self.bus.cancelled, 1)

        self.bus.gate.set()
        harness.daemon.handle_message(
            {"type": "settings", "settings": {"enabled": True}}
        )
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        next_bridge = harness.daemon.bridge
        self.assertIsNot(next_bridge, first_bridge)
        self.assertEqual(harness.daemon.source_path, NEXT_SOURCE_PATH)
        # The old round did succeed for its own path, but it may not bind that
        # composite to the new source or revive capture.
        self.assertIsNone(next_bridge.own_path)
        self.assertFalse(harness.daemon.capturing)

        harness.pump(lambda: False, ticks=60)
        self.assertEqual(harness.daemon.source_path, NEXT_SOURCE_PATH)
        self.assertIsNone(next_bridge.own_path)
        self.assertFalse(harness.daemon.capturing)

    def test_a_late_success_cannot_bind_the_next_source(self):
        harness = self.harness
        first_bridge = harness.daemon.bridge
        old_future = harness.daemon._probe_future
        old_probe = harness.daemon._probe
        self.assertIsNotNone(first_bridge)
        self.assertIsNotNone(old_future)
        self.assertIsNotNone(old_probe)
        self.assertFalse(old_future.done())

        # A query that cannot be ended: only this probe instance's cancel
        # entry point is replaced, so clearing the daemon cannot make the
        # answer disappear.  The round stays on the bus gate instead.
        old_probe.cancel = lambda: None

        harness.paths.append(NEXT_SOURCE_PATH)
        try:
            harness.daemon.handle_message(
                {"type": "settings", "settings": {"enabled": False}}
            )
            self.assertIsNone(harness.daemon.bridge)
            # Nothing ended the query in flight; it is held by the gate only.
            self.assertEqual(self.bus.cancelled, 0)
            self.assertFalse(old_future.done())

            # Re-enabling builds the NEXT_SOURCE bridge while the old round is
            # still open, so that bridge is in place before the answer lands.
            harness.daemon.handle_message(
                {"type": "settings", "settings": {"enabled": True}}
            )
            harness.now += 0.02
            harness.daemon.tick(harness.now)

            next_bridge = harness.daemon.bridge
            self.assertIsNot(next_bridge, first_bridge)
            self.assertEqual(harness.daemon.source_path, NEXT_SOURCE_PATH)
            self.assertIsNone(next_bridge.own_path)
            self.assertFalse(harness.daemon.capturing)
            self.assertFalse(old_future.done())

            # Let the old query answer: it really succeeds for its own source.
            self.bus.gate.set()
            self.assertTrue(self.wait_for(old_future.done))
            finished, ok, detail = old_future.result()
            self.assertIs(finished, old_probe)
            self.assertTrue(ok, detail)
            self.assertEqual(detail, "")
            self.assertEqual(old_probe.own_path, COMPOSITE_PATH)

            # No frame has run since it finished, so nothing adopted it yet.
            self.assertIsNone(next_bridge.own_path)
            self.assertIsNone(harness.daemon.sink)
            self.assertFalse(harness.daemon.capturing)

            harness.now += 0.02
            harness.daemon.tick(harness.now)

            # The success belonged to the closed source: the next bridge and
            # the frame that saw the answer both refuse it.
            self.assertIs(harness.daemon.bridge, next_bridge)
            self.assertEqual(harness.daemon.source_path, NEXT_SOURCE_PATH)
            self.assertIsNone(next_bridge.own_path)
            self.assertIsNone(harness.daemon.sink)
            self.assertFalse(harness.daemon.capturing)
            self.assertEqual(self.bus.touches, [])

            harness.pump(lambda: False, ticks=60)
            self.assertEqual(harness.daemon.source_path, NEXT_SOURCE_PATH)
            self.assertIs(harness.daemon.bridge, next_bridge)
            self.assertIsNone(next_bridge.own_path)
            self.assertIsNone(harness.daemon.sink)
            self.assertFalse(harness.daemon.capturing)
        finally:
            # Never leave the gated worker behind, even when an assertion
            # above fails: tearDown joins it after this release.
            self.bus.gate.set()

    def test_shutdown_cancels_the_round_and_releases_the_worker(self):
        harness = self.harness
        worker = self.bus.discovery_thread
        self.assertIsNotNone(worker)
        self.assertIsNotNone(harness.daemon._executor)

        harness.daemon.shutdown()

        self.assertIsNone(harness.daemon._executor)
        self.assertIsNone(harness.daemon._probe_future)
        self.assertIsNone(harness.daemon.bridge)
        self.assertTrue(harness.touch.closed)
        self.assertTrue(self.wait_for(lambda: self.bus.cancelled))

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and worker.is_alive():
            time.sleep(0.005)
        self.assertFalse(worker.is_alive())

    def test_a_blocked_tick_releases_capture_when_the_heartbeat_expires(self):
        harness = self.harness.ready()
        self.assertTrue(harness.daemon.capturing)

        # Five seconds pass inside this tick; the heartbeat cannot stay valid.
        # The jump lands after the tick's opening checkpoint, which is what a
        # blocked stage looks like from the checkpoint's point of view.
        harness.daemon._now = JumpingClock(harness.now, jump=5.0)
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertFalse(harness.daemon.capturing)
        self.assertFalse(harness.daemon.engine.active)
        self.assertEqual(harness.touch.ungrabs, 1)

    def test_an_offset_clock_never_moves_the_explicit_frame_time(self):
        harness = self.harness.ready()
        self.assertTrue(harness.daemon.capturing)

        # The injected clock is 4000 s ahead of the frame time.  Reading it
        # must not make the tick adopt that absolute value: an expired
        # heartbeat is decided by elapsed time only.
        harness.daemon._now = JumpingClock(
            harness.now, jump=0.0, offset=4000.0
        )
        harness.now += 0.1
        harness.daemon.tick(harness.now)

        self.assertTrue(harness.daemon.capturing)
        self.assertFalse(harness.daemon._neutralize_pending)
        self.assertEqual(harness.touch.ungrabs, 0)

    def test_one_blocked_stage_is_never_advanced_twice(self):
        harness = self.harness.ready()
        harness.daemon._now = JumpingClock(harness.now, jump=5.0)

        frame = harness.now
        checkpoint = harness.daemon._now()  # the sample tick() opens with
        after_polls, checkpoint = harness.daemon._advance(frame, checkpoint)
        after_read, checkpoint = harness.daemon._advance(
            after_polls, checkpoint
        )

        self.assertAlmostEqual(after_polls - frame, 5.0)
        # The clock stood still after the block, so the second stage measures
        # from the checkpoint the first one left and adds nothing.
        self.assertAlmostEqual(after_read, after_polls)


class _StaleProbe:
    """Probe double that can answer once and then raise with its identity.

    ``fail`` is explicit: a probe created before it is set returns a clear
    verdict, and a later one raises while still carrying a complete
    ``own_path``.  That partial state is exactly what a raised round must
    never leave behind on the live bridge.
    """

    def __init__(self, own_path, fail=False):
        self.own_path = own_path
        self.fail = bool(fail)
        self.cancelled = 0

    def discover(self, _source_path):
        if self.fail:
            raise RuntimeError("bus exploded")
        return True, ""

    def cancel(self):
        self.cancelled += 1


class StaleProbeBridge:
    """Bridge double with one clear round, then a round that raises.

    The fixture needs a live capture to lose, and capture only exists after a
    successful discovery has been adopted on the input thread.  The first
    round therefore succeeds; setting ``fail`` makes the next probe keep the
    same known identity but return no verdict at all.
    """

    def __init__(self):
        self._own_path = COMPOSITE_PATH
        self.adopted = []
        self.fail = False

    @property
    def own_path(self):
        return self._own_path

    def make_probe(self):
        return _StaleProbe(COMPOSITE_PATH, fail=self.fail)

    def adopt_probe(self, probe):
        self.adopted.append(getattr(probe, "own_path", None))
        self._own_path = getattr(probe, "own_path", None)


class RaisedRoundTests(unittest.TestCase):
    def test_a_raised_round_clears_identity_and_releases_capture(self):
        bridge = StaleProbeBridge()
        harness = Harness(bridge=bridge)
        self.addCleanup(harness.daemon.shutdown)
        harness.activate()

        # Discovery is asynchronous: the first round has to finish on the
        # worker and be adopted by a later input tick before a capture exists.
        first = harness.daemon._probe_future
        self.assertIsNotNone(first)
        self.assertTrue(_wait_for(first.done))
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        self.assertEqual(bridge.own_path, COMPOSITE_PATH)
        self.assertIsNotNone(harness.daemon.sink)
        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.grabs, 1)

        # The same bridge keeps its identity from here on, but its round now
        # raises instead of returning a verdict.
        bridge.adopted.clear()
        bridge.fail = True
        harness.now += 1.05
        harness.daemon.tick(harness.now)

        # The round is still on the worker, so nothing on the live object has
        # changed yet: only a finished round may touch identity or capture.
        second = harness.daemon._probe_future
        self.assertIsNotNone(second)
        self.assertEqual(bridge.own_path, COMPOSITE_PATH)
        self.assertTrue(harness.daemon.capturing)
        self.assertEqual(harness.touch.ungrabs, 0)

        self.assertTrue(_wait_for(second.done))
        harness.now += 0.02
        harness.daemon.tick(harness.now)

        # The probe still claimed an identity, but its round never returned a
        # verdict: the live object must be cleared, not bound, and the grab
        # released in this very frame.
        self.assertEqual(bridge.adopted, [None])
        self.assertIsNone(bridge.own_path)
        self.assertFalse(harness.daemon.capturing)
        self.assertEqual(harness.touch.ungrabs, 1)
        self.assertIn("bus exploded", harness.daemon._failures["bridge"])


if __name__ == "__main__":
    unittest.main()
