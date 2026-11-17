"""Root runtime for the Freedom touch controller.

The daemon is started by the Decky backend with ``--user <desktop user>``.  It
reads newline-delimited JSON commands (settings/heartbeat/preview_haptics/
controls_visibility/stop) from stdin and writes only one kind of line to
stdout::

    {"type": "status", "status": {state, detail, capturing, source_path,
                                  composite_path, device, rotation}}

Everything else goes to stderr.  The overlay process is the only child this
module owns; it is spawned unprivileged with the desktop account's uid/gid.

Captured touch input is translated exclusively into the virtual controller.
It is deliberately never replayed to Gamescope's physical touchscreen: during
a captured game the controller is touch-exclusive (first version).
"""

import argparse
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import pwd
except ImportError:  # Windows is authoring-only; deployed systems are Linux.
    pwd = None

from . import collapse, session
from .engine import TouchEngine
from .input_adapter import (
    GUIDE_CHORD,
    QAM_CHORD,
    InputPlumberBridge,
    InputSink,
    create_source,
)
from .layout import transform_point
from .runtime_policy import (
    FrameAccumulator,
    MAX_HEARTBEAT_AGE,
    MT_POSITION_X,
    MT_POSITION_Y,
    QAM_APP_ID,
    effective_layout,
    should_capture,
    validate_settings,
)

TICK_SECONDS = 0.02
DISCOVERY_SECONDS = 1.0
FOCUS_SECONDS = 0.1
OVERLAY_RETRY_SECONDS = 2.0
VISUAL_SECONDS = 1.0 / 60.0
MENU_RECAPTURE_SECONDS = 0.5
PASSIVE_MENU_GRACE_SECONDS = 0.1
HAPTICS_REFRESH_SECONDS = 5.0
PREVIEW_COOLDOWN_SECONDS = 0.15
HAPTICS_UNAVAILABLE_DETAIL = "机身振动暂不可用"

MENU_CHORDS = {
    "guide": GUIDE_CHORD,
    "qam": QAM_CHORD,
}

MAX_LINE_BYTES = 4 * 1024 * 1024
READ_CHUNK = 65536
PLUGIN_PYTHON = "/usr/bin/python3"

# Linux BTN_TOUCH.  Used only for the physical-contact idle check.
BTN_TOUCH = 330


def _default_haptics_factory(*, enabled=True):
    """Import the hardware adapter only when the daemon actually needs it."""

    from .haptics import Haptics

    return Haptics(enabled=enabled)


class OverlayChild:
    """Owned unprivileged overlay process with a non-blocking writer.

    The root loop only ever replaces one pending visual state; a bounded writer
    thread performs the actual pipe writes so a slow or wedged overlay can
    never freeze input processing.  Closing writes an EOF first, waits, and
    only then terminates/kills the exact child pid.  The only post-ready line
    the overlay may send is the handshake; legacy chrome actions are ignored.
    """

    def __init__(self, argv, cwd, env, uid, gid, groups):
        self.ready = False
        self._buffer = b""
        self._latest = None
        self._closing = False
        self._condition = threading.Condition()
        self._stdin_closed = False

        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            user=uid,
            group=gid,
            extra_groups=groups,
        )
        self._stdout_fd = self.process.stdout.fileno()
        self._stdin_fd = self.process.stdin.fileno()
        try:
            os.set_blocking(self._stdout_fd, False)
        except OSError:
            pass

        self._writer = threading.Thread(
            target=self._write_loop,
            name="freedom-touch-overlay-writer",
            daemon=True,
        )
        self._writer.start()

    # -- writer ----------------------------------------------------------------

    def _write_loop(self):
        while True:
            with self._condition:
                while self._latest is None and not self._closing:
                    self._condition.wait()
                if self._latest is None:
                    return
                data, self._latest = self._latest, None
            try:
                view = memoryview(data)
                while view:
                    written = os.write(self._stdin_fd, view)
                    view = view[written:]
            except (OSError, ValueError):
                return

    def send(self, message):
        data = (
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        with self._condition:
            if self._closing:
                return
            # Only the latest visual state matters; an older frame is dropped.
            self._latest = data
            self._condition.notify()

    # -- readiness -------------------------------------------------------------

    def poll_ready(self):
        while True:
            try:
                chunk = os.read(self._stdout_fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                return
            if not chunk:
                return
            self._buffer += chunk
            lines = self._buffer.split(b"\n")
            self._buffer = lines.pop()
            if len(self._buffer) > MAX_LINE_BYTES:
                self._buffer = b""
            for line in lines:
                self._handle_line(line)

    def _handle_line(self, line):
        text = line.strip()
        if not text:
            return
        try:
            payload = json.loads(text.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("ready") is True:
            self.ready = True

    def alive(self):
        return self.process.poll() is None

    # -- shutdown --------------------------------------------------------------

    def close(self):
        with self._condition:
            # Let the writer flush the last queued state, then exit and EOF.
            self._closing = True
            self._condition.notify()

        # Let the writer flush its last frame, then give the overlay EOF.
        self._writer.join(1.0)
        if not self._writer.is_alive():
            self._close_stdin()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        if self._writer.is_alive():
            self._terminate()

        if self.process.poll() is None:
            self._terminate()

        self._close_stdin()
        self._writer.join(0.5)
        try:
            self.process.stdout.close()
        except (OSError, ValueError):
            pass

    def _close_stdin(self):
        if self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass

    def _terminate(self):
        try:
            self.process.terminate()
        except OSError:
            pass
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError:
                pass
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass


class Daemon:
    """One root runtime instance; hardware boundaries are injectable for tests."""

    def __init__(
        self,
        user,
        uid,
        gid,
        home,
        plugin_root,
        *,
        source_factory=create_source,
        bridge_factory=InputPlumberBridge,
        sink_factory=InputSink,
        engine_factory=TouchEngine,
        environment_reader=session.read_device_environment,
        touch_factory=session.open_touchscreen,
        rotation_resolver=session.rotation_for,
        session_finder=session.discover_session,
        focus_factory=session.FocusMonitor,
        overlay_feature=None,
        overlay_factory=None,
        haptics_factory=None,
        stdin_fd=None,
        stdout=None,
        stderr=None,
        clock=time.monotonic,
        install_signals=True,
    ):
        self.user = user
        self.uid = uid
        self.gid = gid
        self.home = home
        self.plugin_root = plugin_root

        self._source_factory = source_factory
        self._bridge_factory = bridge_factory
        self._sink_factory = sink_factory
        self._engine_factory = engine_factory
        self._environment_reader = environment_reader
        self._touch_factory = touch_factory
        self._rotation_resolver = rotation_resolver
        self._session_finder = session_finder
        self._focus_factory = focus_factory
        self._overlay_feature = overlay_feature or (
            lambda: session.overlay_supported(uid=self.uid)
        )
        self._overlay_factory = overlay_factory
        self._haptics_factory = haptics_factory or _default_haptics_factory
        self._stdin_fd = stdin_fd
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._now = clock
        self._install_signals = install_signals

        self.settings = validate_settings({"enabled": False})
        self.engine = None
        self.effective_controls = None
        self.ui = None
        self.source_path = None
        self.bridge = None
        self.sink = None
        self.touch = None
        self.session_env = None
        self.focus = None
        self.overlay = None
        self.overlay_ready = False
        self.haptics = None

        self.capturing = False
        self.heartbeat_appid = None
        self.heartbeat_time = None
        self.rotation = 0
        self.aspect_ratio = 1.0
        self.focus_apps = (None, None)
        self._capture_block_until = -1e9
        # A failed release neutralisation may leave a contact on the sink, so
        # it stays pending until one later attempt succeeds.
        self._neutralize_pending = False

        # Collapsed chrome is a mode of its own: the pill requests a complete
        # overlay hide, while controls_visible is the session-only request
        # that survives overlay restarts and is changed only by user action.
        self.controls_visible = True
        self.collapsed = False
        self._eligible = False
        self.bubble = collapse.default_bubble()
        self._expand_pending = False
        self._collapse_pending = False
        self._pill_pointer = None
        self._pill_dragout = False

        self.frame = FrameAccumulator()
        # Physical contact bookkeeping runs in every mode and is independent
        # of the engine frame, which is reset on every capture transition.
        self._contact_frame = FrameAccumulator()
        self._contacts = set()
        self._native_contacts = set()
        # A hidden overlay must not grab the panel, so it needs a separate,
        # empty-layout engine that can only recognize edge menu gestures from
        # the same passive evdev stream.  The main virtual engine stays inert.
        self._passive_engine = None
        self._passive_pending_side = None
        self._passive_deadline = None
        self._absinfo = None
        self._grabbed = False
        self._selector = None
        self._touch_registered = False
        self._stdin_buffer = b""
        self._stop = False
        self._shutdown_done = False

        self._last_discovery = -1e9
        self._last_focus = -1e9
        self._last_visual = -1e9
        # InputPlumber discovery can block for seconds on a slow system bus, so
        # it runs in this one lazily created worker.  Only one round may be in
        # flight; tick() adopts its result on the input thread, and the live
        # bridge is never written by the worker.
        self._executor = None
        self._probe_future = None
        self._probe = None
        self._probe_key = None
        self._probe_newly_created = False
        self._probe_generation = 0
        self._last_haptics_refresh = -1e9
        self._last_haptics_preview = -1e9
        self._overlay_retry_at = 0.0
        self._pending_visual = None
        self._sent_visual = None
        self._visual_force = False

        self._device_values = None
        self._supported = None
        self._failures = {}
        self._status = None
        self._override = None

    # ------------------------------------------------------------------
    # stdin protocol
    # ------------------------------------------------------------------

    def handle_line(self, line):
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                self._log("ignoring non-UTF-8 stdin line")
                return
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError as exc:
            self._log("ignoring malformed stdin message: %s" % (exc,))
            return
        self.handle_message(message)

    def handle_message(self, message):
        if not isinstance(message, dict):
            self._log("ignoring non-object stdin message")
            return
        kind = message.get("type")

        if kind == "settings":
            try:
                settings = validate_settings(message.get("settings"))
            except ValueError as exc:
                self._log("rejected settings: %s" % (exc,))
                self._override = (
                    "error",
                    "设置无效：%s" % (exc,),
                    self._now() + 3.0,
                )
                self._publish(self._now(), force=True)
                return
            self._apply_settings(settings)
            self._publish(self._now(), force=True)
            return

        if kind == "heartbeat":
            appid = message.get("appid")
            if appid is not None and not isinstance(appid, str):
                self._log("ignoring heartbeat with a non-string appid")
                return
            self.heartbeat_appid = appid
            self.heartbeat_time = self._now() if appid is not None else None
            if appid is None:
                self._reset_passive("heartbeat cleared")
                if self.capturing:
                    self._release_capture("heartbeat cleared")
                if self.collapsed:
                    # The game is gone; hide the bubble immediately instead
                    # of waiting for the next policy pass.
                    self._eligible = False
                    self._expand_pending = False
                    self._visual_force = True
                self._queue_visual()
                self._flush_visual(self._now())
            self._publish(self._now())
            return

        if kind == "controls_visibility":
            self._apply_controls_visibility(
                message.get("visible"), self._now()
            )
            return

        if kind == "preview_haptics":
            self._preview_haptics(self._now())
            return

        if kind == "stop":
            self._stop = True
            return

        self._log("ignoring unknown message type %r" % (kind,))

    def handle_eof(self):
        self._stop = True

    # ------------------------------------------------------------------
    # settings / hardware lifecycle
    # ------------------------------------------------------------------

    def _apply_settings(self, settings):
        self._reset_passive("settings changed")
        self._release_capture("settings changed")

        changed = (
            self.engine is None
            or settings["layout"] != self.settings["layout"]
            or settings["scale"] != self.settings["scale"]
            or settings["deadzone"] != self.settings["deadzone"]
            or bool(settings.get("edge_swipes", False))
            != bool(self.settings.get("edge_swipes", False))
            or settings.get("dpad_mode")
            != self.settings.get("dpad_mode")
            or bool(settings.get("right_stick_fullscreen", False))
            != bool(self.settings.get("right_stick_fullscreen", False))
        )
        if changed:
            # Input hit targets must be the exact rectangles the overlay
            # renders, so both sides share one effective (scaled) layout.
            self.effective_controls = effective_layout(
                settings["layout"], settings["scale"]
            )
            self.engine = self._make_engine(settings)
        self.settings = settings
        self._apply_haptics_settings(settings)

        if settings["enabled"]:
            self._override = None
            self._last_discovery = -1e9
            self._visual_force = True
        else:
            self._teardown_hardware()
            self._override = None

    def _make_engine(self, settings):
        """Build the engine with every input option and the current aspect."""

        return self._engine_factory(
            self.effective_controls,
            settings["deadzone"],
            bool(settings.get("edge_swipes", False)),
            self.aspect_ratio,
            dpad_mode=settings["dpad_mode"],
            right_stick_fullscreen=bool(
                settings.get("right_stick_fullscreen", False)
            ),
        )

    def _apply_controls_visibility(self, visible, now):
        """Apply the user's requested overlay visibility immediately.

        The request is independent of game/menu focus and overlay lifetime.
        Hiding neutralises every active control at once but deliberately keeps
        the touchscreen grab until all physical fingers lift; showing never
        grabs on its own, so the normal eligibility and native-idle guards
        decide when capture may resume.
        """

        if not isinstance(visible, bool):
            self._log("ignoring controls_visibility with a non-boolean value")
            self._publish(now, force=True)
            return

        # A pending deferred menu belongs to the previous visibility mode.
        self._reset_passive("controls visibility")
        self.controls_visible = visible
        self._reset_pill()
        if visible:
            self.collapsed = False
            self._expand_pending = False
            self._collapse_pending = False
        else:
            self.collapsed = True
            self._expand_pending = False
            self._stop_haptics()
            if (
                self.capturing
                or (self.engine is not None and self.engine.active)
            ):
                # Neutralise now, but keep the grab for fingers still down.
                self._collapse_pending = True
                self._suspend_capture("controls hidden")

        self._visual_force = True
        self._queue_visual()
        self._flush_visual(now)
        self._publish(now, force=True)

    # ------------------------------------------------------------------
    # haptics
    # ------------------------------------------------------------------

    def _apply_haptics_settings(self, settings):
        """Create/update the optional motor adapter without affecting input."""

        enabled = bool(
            settings["enabled"] and settings.get("haptics_enabled", True)
        )
        self._ensure_haptics(enabled)
        if self.haptics is None:
            return
        setter = getattr(self.haptics, "set_strength", None)
        if setter is not None:
            try:
                setter(settings["haptics_strength"])
            except Exception as exc:
                self._log("haptics set_strength failed: %s" % (exc,))
        try:
            self.haptics.set_enabled(enabled)
        except Exception as exc:
            self._log("haptics set_enabled failed: %s" % (exc,))
        if not enabled:
            self._stop_haptics()

    def _ensure_haptics(self, enabled):
        if self.haptics is not None:
            return
        try:
            self.haptics = self._haptics_factory(enabled=bool(enabled))
        except Exception as exc:
            # No motor controller must never prevent touch input.
            self.haptics = None
            self._log("cannot create haptics adapter: %s" % (exc,))
            return
        # A freshly created adapter owns no motor node at all: availability
        # is only decided by refresh().  Mark the next poll as already due so
        # the first discovery happens on this tick instead of five seconds
        # later, and only subsequent polls keep the regular cadence.
        self._last_haptics_refresh = -math.inf

    def _poll_haptics(self, now):
        haptics = self.haptics
        if haptics is None:
            return
        if now - self._last_haptics_refresh < HAPTICS_REFRESH_SECONDS:
            return
        self._last_haptics_refresh = now
        try:
            haptics.refresh(now)
        except Exception as exc:
            self._log("haptics refresh failed: %s" % (exc,))

    def _stop_haptics(self):
        haptics = self.haptics
        if haptics is None:
            return
        try:
            haptics.stop()
        except Exception as exc:
            self._log("haptics stop failed: %s" % (exc,))

    def _preview_haptics(self, now):
        """Play one direct tick on both sides, outside the input path."""

        settings = self.settings
        if not settings["enabled"]:
            return
        if not settings.get("haptics_enabled", True):
            return
        if settings.get("haptics_strength", 0) <= 0:
            return

        haptics = self.haptics
        preview = getattr(haptics, "preview", None)
        if preview is None:
            return

        try:
            now = float(now)
        except (TypeError, ValueError):
            return
        if not math.isfinite(now):
            return
        if now < self._last_haptics_preview + PREVIEW_COOLDOWN_SECONDS:
            return
        self._last_haptics_preview = now

        try:
            preview(now)
        except Exception as exc:
            self._log("haptics preview failed: %s" % (exc,))

    def _consume_haptics(self, events, now):
        haptics = self.haptics
        if haptics is None:
            return
        try:
            haptics.consume(events, now)
        except Exception as exc:
            self._log("haptics consume failed: %s" % (exc,))

    def _haptics_status(self):
        haptics = self.haptics
        if haptics is None:
            return False, HAPTICS_UNAVAILABLE_DETAIL
        try:
            available = bool(haptics.available)
            detail = haptics.detail
        except Exception:
            return False, HAPTICS_UNAVAILABLE_DETAIL
        if not isinstance(detail, str):
            detail = HAPTICS_UNAVAILABLE_DETAIL
        if not available and not detail:
            detail = HAPTICS_UNAVAILABLE_DETAIL
        return available, detail

    def _teardown_hardware(self):
        self._release_capture("disabled")
        self._reset_collapse()
        self._close_focus()
        self._stop_overlay()
        self._stop_haptics()

        sink = self.sink
        self.sink = None
        if sink is not None:
            try:
                sink.close()
            except Exception as exc:
                self._log("sink close failed: %s" % (exc,))

        self._close_ui()
        self._close_touch()
        self.bridge = None
        self.session_env = None
        self.focus_apps = (None, None)
        self.engine = None
        self.effective_controls = None
        self.frame = FrameAccumulator()
        self._pending_visual = None
        self._sent_visual = None
        self._visual_force = False
        self._device_values = None
        self._absinfo = None
        self._supported = None
        self._failures = {}
        self._last_discovery = -1e9
        self._override = None

    def _close_ui(self):
        # The controller source is going away: a query still running for it
        # must never bind its composite to whatever source comes next.
        self._invalidate_probe()
        ui = self.ui
        self.ui = None
        self.source_path = None
        if ui is not None:
            try:
                ui.close()
            except Exception as exc:
                self._log("source close failed: %s" % (exc,))

    def _close_focus(self):
        focus = self.focus
        self.focus = None
        self.focus_apps = (None, None)
        self._last_focus = -1e9
        if focus is not None:
            try:
                focus.close()
            except Exception as exc:
                self._log("focus close failed: %s" % (exc,))

    def _close_touch(self):
        self._reset_passive("touch closed")
        if self._grabbed:
            self._ungrab()
        touch = self.touch
        self.touch = None
        self._absinfo = None
        self._contacts.clear()
        self._native_contacts.clear()
        self._contact_frame = FrameAccumulator()
        self._reset_pill()
        if touch is not None:
            try:
                touch.close()
            except Exception as exc:
                self._log("touch close failed: %s" % (exc,))

    def _reset_pill(self):
        self._pill_pointer = None
        self._pill_dragout = False

    def _reset_collapse(self):
        self.collapsed = False
        self._eligible = False
        self._expand_pending = False
        self._collapse_pending = False
        self._reset_pill()

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------

    def _discover(self, now):
        self._last_discovery = now
        if not self.settings["enabled"]:
            return

        self._discover_session()
        if self.session_env is not None and self._supported is None:
            try:
                self._supported = bool(self._overlay_feature())
            except Exception as exc:
                self._supported = False
                self._log("overlay feature check failed: %s" % (exc,))

        self._discover_source()

    def _discover_session(self):
        environment = None
        try:
            environment = self._session_finder(self.uid)
        except Exception as exc:
            self._failures["session"] = str(exc)

        if environment is None:
            self._supported = None
            if self.session_env is not None:
                self._release_capture("session lost")
                self._stop_overlay()
                self._close_focus()
            self.session_env = None
            self._failures.setdefault(
                "session", "未发现 Steam 主 Gamescope X11 会话"
            )
            return

        if environment != self.session_env:
            self._supported = None
            self._release_capture("session changed")
            self._stop_overlay()
            self._close_focus()
            self.session_env = environment
            # A new Gamescope session may carry a different panel orientation;
            # re-read the device environment on this pass.
            self._device_values = None
        self._failures.pop("session", None)

        if self.focus is None:
            try:
                self.focus = self._focus_factory(environment)
                self._failures.pop("focus", None)
            except Exception as exc:
                self._failures["focus"] = str(exc)

        if self._device_values is None:
            try:
                self._device_values = self._environment_reader()
            except Exception as exc:
                self._failures["device"] = "读取设备环境失败：%s" % (exc,)

        if self._device_values is not None:
            try:
                # The running compositor's --force-orientation is the direction
                # the panel actually uses; the model profile can disagree.
                direction_values = dict(self._device_values)
                if session.PANEL_ORIENTATION_KEY in environment:
                    direction_values[session.PANEL_ORIENTATION_KEY] = environment[
                        session.PANEL_ORIENTATION_KEY
                    ]
                self.rotation = int(
                    self._rotation_resolver(direction_values)
                )
                self.aspect_ratio = self._compute_aspect_ratio()
                if self.engine is not None:
                    self.engine.set_aspect_ratio(self.aspect_ratio)
                self._failures.pop("rotation", None)
            except Exception as exc:
                self.rotation = 0
                self._failures["rotation"] = "面板方向无效：%s" % (exc,)

        if self.touch is None and self._device_values is not None:
            self._open_touch()

    def _open_touch(self):
        touch = None
        try:
            touch = self._touch_factory(self._device_values)
            abs_x = touch.absinfo(MT_POSITION_X)
            abs_y = touch.absinfo(MT_POSITION_Y)
            self.touch = touch
            self._absinfo = (abs_x, abs_y)
            self._contacts.clear()
            self._native_contacts.clear()
            self._contact_frame = self._opened_contact_frame(touch)
            self.aspect_ratio = self._compute_aspect_ratio()
            self._reset_passive("touch opened")
            if self.engine is not None:
                self.engine.set_aspect_ratio(self.aspect_ratio)
            self._failures.pop("touch", None)
            self._failures.pop("device", None)
        except Exception as exc:
            self._failures["touch"] = str(exc)
            self._log("cannot open primary touchscreen: %s" % (exc,))
            if touch is not None:
                try:
                    touch.close()
                except Exception:
                    pass

    def _opened_contact_frame(self, touch):
        """Return a passive frame seeded from a read-only slot snapshot.

        Opening the device is not a touch: the snapshot only fills the per-slot
        coordinates and the current slot, so a finger that is already on the
        panel keeps its point without becoming a contact.  The first gesture
        still needs its own TRACKING_ID.  When the ioctl is unsupported the
        frame stays empty and a begin simply waits for complete coordinates.
        """

        frame = FrameAccumulator()
        snapshot = session.touch_slot_snapshot(touch)
        if snapshot is not None:
            frame.seed_positions(*snapshot)
        return frame

    def _compute_aspect_ratio(self):
        """Return the rotated screen width/height ratio of the touch panel."""

        if self._absinfo is None:
            return 1.0
        abs_x, abs_y = self._absinfo
        try:
            width = float(abs_x.max) - float(abs_x.min)
            height = float(abs_y.max) - float(abs_y.min)
        except (AttributeError, TypeError, ValueError):
            return 1.0
        if self.rotation in (90, 270):
            width, height = height, width
        if (
            not (math.isfinite(width) and math.isfinite(height))
            or width <= 0.0
            or height <= 0.0
        ):
            return 1.0
        return width / height

    def _discover_source(self):
        newly_created = False
        if self.ui is None:
            try:
                self.ui, self.source_path = self._source_factory()
                newly_created = True
                self.bridge = None
                self.sink = None
                self._failures.pop("source", None)
            except Exception as exc:
                self._failures["source"] = str(exc)
                self._log("cannot create controller source: %s" % (exc,))
                return

        if not self.source_path:
            self._failures["source"] = "source path is empty"
            return

        if self.bridge is None:
            try:
                self.bridge = self._bridge_factory()
            except Exception as exc:
                self._failures["bridge"] = str(exc)
                self._log("cannot create InputPlumber bridge: %s" % (exc,))
                return

        if self._start_probe(newly_created):
            return
        self._discover_bridge_sync(newly_created)

    def _start_probe(self, newly_created):
        """Submit one background discovery.  True when the bridge has a probe.

        A bridge without ``make_probe``/``adopt_probe`` keeps the synchronous
        path below, which is what the existing device doubles rely on.  The
        real bridge always probes, so a slow bus never holds the input thread.
        """

        bridge = self.bridge
        make_probe = getattr(bridge, "make_probe", None)
        adopt_probe = getattr(bridge, "adopt_probe", None)
        if make_probe is None or adopt_probe is None:
            return False
        if self._probe_future is not None:
            # One query at a time.  A newer source must not queue behind the
            # round already running; its result is dropped by identity instead.
            return True

        try:
            probe = make_probe()
        except Exception as exc:
            self._failures["bridge"] = str(exc)
            self._log("cannot create discovery probe: %s" % (exc,))
            self._release_capture("composite unavailable")
            return True

        key = (self._probe_generation, self.source_path, id(bridge))
        try:
            future = self._ensure_executor().submit(
                self._run_probe, probe, self.source_path
            )
        except Exception as exc:
            self._failures["bridge"] = str(exc)
            self._log("cannot start discovery probe: %s" % (exc,))
            self._release_capture("composite unavailable")
            return True

        self._probe = probe
        self._probe_future = future
        self._probe_key = key
        self._probe_newly_created = newly_created
        return True

    @staticmethod
    def _run_probe(probe, source_path):
        """Worker body: only the detached probe belongs to this thread.

        The round's verdict is unpacked here, so the future always carries
        ``(probe, ok, detail)`` - the same shape :meth:`_poll_probe` reads.
        """

        ok, detail = probe.discover(source_path)
        return probe, ok, detail

    def _ensure_executor(self):
        executor = self._executor
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="freedom-touch-discovery",
            )
            self._executor = executor
        return executor

    def _probe_identity(self):
        return (self._probe_generation, self.source_path, id(self.bridge))

    def _invalidate_probe(self):
        """Cancel the running query and make its answer stale.

        The round stays in flight until its worker returns, so no second
        query is ever started alongside it; the next tick drops the result
        because its identity no longer matches.
        """

        self._probe_generation += 1
        cancel = getattr(self._probe, "cancel", None)
        if cancel is None:
            return
        try:
            cancel()
        except Exception as exc:
            self._log("cannot cancel discovery probe: %s" % (exc,))

    def _poll_probe(self, now):
        """Adopt a finished background discovery on the input thread."""

        future = self._probe_future
        if future is None or not future.done():
            return

        key = self._probe_key
        probe = self._probe
        newly_created = self._probe_newly_created
        self._probe_future = None
        self._probe_key = None
        self._probe = None
        self._probe_newly_created = False

        bridge = self.bridge
        adopt_probe = getattr(bridge, "adopt_probe", None)
        if (
            probe is None
            or adopt_probe is None
            or key != self._probe_identity()
        ):
            # The source, the bridge or the round itself changed while this
            # query ran: its answer belongs to hardware that is no longer
            # ours, so it may not bind or revive anything.
            self._last_discovery = -1e9
            return

        raised = False
        finished = None
        try:
            finished, ok, detail = future.result()
        except Exception as exc:
            # Cancelled proposals and a genuinely broken bridge land here.
            ok, detail, raised = False, str(exc), True

        try:
            if ok:
                adopt_probe(finished)
            else:
                # Only a complete, successful round may bind an identity.  A
                # failed or raised one must clear it: a probe that never
                # returned a verdict must not leave a half-known composite on
                # the live object, and reusing its connection is not worth a
                # wrong binding.
                adopt_probe(None)
        except Exception as exc:
            ok, detail = False, str(exc)

        # The next round is throttled from the moment this one finished, not
        # from the moment it was submitted.
        self._last_discovery = now

        if not ok:
            self._failures["bridge"] = detail or "composite 不可用"
            if raised and newly_created:
                # A broken bridge on a just-created source must not leave a
                # stray controller around; retry with a fresh one next time.
                self._close_ui()
                self.bridge = None
                self.sink = None
            self._release_capture("composite unavailable")
            return

        self._failures.pop("bridge", None)
        self._ensure_sink()

    def _discover_bridge_sync(self, newly_created):
        """Discover inside the input thread, for bridges without a probe."""

        try:
            ok, detail = self.bridge.discover(self.source_path)
        except Exception as exc:
            ok, detail = False, str(exc)
            # A broken bridge on a brand-new source must not leave a stray
            # controller around; clean it once and retry on the next pass.
            if newly_created:
                self._close_ui()
                self.bridge = None
                self.sink = None

        if not ok:
            self._failures["bridge"] = detail or "composite 不可用"
            self._release_capture("composite unavailable")
            return

        self._failures.pop("bridge", None)
        self._ensure_sink()

    def _ensure_sink(self):
        if self.sink is not None:
            return
        try:
            self.sink = self._sink_factory(self.ui, self.bridge)
            self._failures.pop("sink", None)
        except Exception as exc:
            self._failures["sink"] = str(exc)
            self._log("cannot create input sink: %s" % (exc,))

    def _stop_probe_executor(self):
        """Cancel background discovery and release its own worker.

        The call is deliberately not waited for: the probe's cancellable ends
        its D-Bus request, and the input thread must never block on a bus that
        may already be wedged.
        """

        self._invalidate_probe()
        executor = self._executor
        self._executor = None
        future = self._probe_future
        self._probe_future = None
        self._probe_key = None
        self._probe = None
        self._probe_newly_created = False
        if future is not None:
            future.cancel()
        if executor is None:
            return
        try:
            executor.shutdown(wait=False)
        except Exception as exc:
            self._log("discovery executor shutdown failed: %s" % (exc,))

    # ------------------------------------------------------------------
    # overlay process
    # ------------------------------------------------------------------

    def _poll_overlay(self, now):
        if self.overlay is not None:
            try:
                self.overlay.poll_ready()
            except Exception as exc:
                self._log("overlay stdout failed: %s" % (exc,))

            if not self.overlay.alive():
                self._release_capture("overlay exited")
                self._stop_overlay()
                self._overlay_retry_at = now + OVERLAY_RETRY_SECONDS
                self._failures["overlay"] = "叠加进程已退出"
                return

            if self.overlay.ready and not self.overlay_ready:
                self.overlay_ready = True
                self._visual_force = True
                self._failures.pop("overlay", None)
            return

        if (
            self.settings["enabled"]
            and self._supported
            and self.session_env is not None
            and now >= self._overlay_retry_at
        ):
            self._start_overlay(now)

    def _start_overlay(self, now):
        try:
            groups = os.getgrouplist(self.user, self.gid)
        except (KeyError, OSError, ValueError):
            groups = [self.gid]

        environment = dict(self.session_env)
        environment["PATH"] = os.environ.get(
            "PATH", "/usr/local/bin:/usr/bin:/bin"
        )
        environment["PYTHONPATH"] = self.plugin_root
        environment["PYTHONDONTWRITEBYTECODE"] = "1"

        argv = [
            PLUGIN_PYTHON,
            "-m",
            "py_modules.freedom_touch.overlay",
        ]
        factory = self._overlay_factory or OverlayChild
        try:
            self.overlay = factory(
                argv,
                self.plugin_root,
                environment,
                self.uid,
                self.gid,
                groups,
            )
            self.overlay_ready = False
        except Exception as exc:
            self.overlay = None
            self.overlay_ready = False
            self._overlay_retry_at = now + OVERLAY_RETRY_SECONDS
            self._failures["overlay"] = "无法启动叠加进程：%s" % (exc,)
            self._log("cannot start overlay: %s" % (exc,))
            return

        try:
            self.overlay.poll_ready()
        except Exception as exc:
            self._log("overlay stdout failed: %s" % (exc,))
        if self.overlay.ready:
            self.overlay_ready = True
            self._visual_force = True
            self._failures.pop("overlay", None)

    def _stop_overlay(self):
        overlay = self.overlay
        self.overlay = None
        self.overlay_ready = False
        # The overlay disappearing is a hardware/process event, not a user
        # request: controls_visible and the collapsed intent survive so a
        # restarted overlay cannot silently reveal the controls again.
        self._pending_visual = None
        self._sent_visual = None
        self._visual_force = False
        if overlay is not None:
            try:
                overlay.close()
            except Exception as exc:
                self._log("overlay close failed: %s" % (exc,))

    # ------------------------------------------------------------------
    # focus / capture decisions
    # ------------------------------------------------------------------

    def _poll_focus(self, now, force=False):
        if self.focus is None:
            return
        if not force and now - self._last_focus < FOCUS_SECONDS:
            return
        self._last_focus = now
        try:
            apps = self.focus.get_focus()
            self.focus_apps = (apps[0], apps[1])
        except Exception as exc:
            self.focus_apps = (None, None)
            self._failures["focus"] = str(exc)
            self._log("focus read failed: %s" % (exc,))
            self._release_capture("focus read failed")
            self._close_focus()

    def _heartbeat_age(self, now):
        if self.heartbeat_time is None:
            return None
        return now - self.heartbeat_time

    def _session_ready(self):
        return bool(
            self._supported
            and self.session_env is not None
            and self.focus is not None
            and "rotation" not in self._failures
            and self.touch is not None
            and self.ui is not None
            and self.source_path
            and self.bridge is not None
            and self.bridge.own_path
            and self.sink is not None
            and self.overlay is not None
            and self.overlay.ready
        )

    def _overlay_ready(self):
        """Return True when the overlay handshake completed.

        The collapse pill is drawn by, and its touches are read by, this own
        overlay/daemon pair; no compositor bubble routing is required.
        """

        overlay = self.overlay
        return bool(overlay is not None and getattr(overlay, "ready", False))

    def _touch_keys_idle(self):
        """Return True unless the real device still reports BTN_TOUCH."""

        if self.touch is None:
            return True
        getter = getattr(self.touch, "active_keys", None)
        if getter is None:
            return True
        try:
            keys = getter()
        except Exception:
            return True
        try:
            return BTN_TOUCH not in keys
        except TypeError:
            return True

    def _native_idle(self):
        """True when no finger currently belongs to the game's own sequence."""

        return not self._native_contacts and self._touch_keys_idle()

    def _all_idle(self):
        """True when every physical finger has been lifted."""

        return not self._contacts and self._touch_keys_idle()

    def _update_capture(self, now):
        ready = self._session_ready()
        age = self._heartbeat_age(now)
        in_cooldown = now < self._capture_block_until
        eligible = bool(
            not in_cooldown
            and ready
            and should_capture(
                True,
                self.heartbeat_appid,
                self.focus_apps[0],
                self.focus_apps[1],
                age,
                True,
            )
        )
        self._eligible = eligible

        if in_cooldown:
            # A menu chord was just sent; give the compositor time to take
            # focus before the game session can be captured again.
            if self.capturing:
                self._release_capture("menu recapture cooldown")
            return

        if not self.controls_visible:
            # A hidden request survives focus changes, menu focus, and an
            # overlay restart.  It never grabs; only a pending grab from the
            # hide transition is released after the last finger lifts.
            self.collapsed = True
            self._expand_pending = False
            if self._grabbed and self._all_idle():
                self._ungrab()
            return

        if self.capturing:
            if not ready:
                self._release_capture("session not ready")
                return
            if not eligible:
                self._release_capture("capture policy")
            return

        if self._grabbed:
            # A hide request keeps the grab until every physical finger is
            # lifted, even if visibility was requested again meanwhile.
            if self._all_idle():
                self._ungrab()
            return

        if not eligible:
            self._expand_pending = False
            return

        if self._neutralize_pending:
            # An earlier release never reached the bridge.  A new grab would
            # put our engine on top of that uncleared input, so capture waits
            # until _retry_neutralize() succeeds; physical contact tracking
            # and status publishing keep running on every frame.
            return

        # Never grab in the middle of a game-owned touch sequence.
        if self._native_idle():
            self._capture(now)

    def _capture(self, now):
        if self.touch is None or self.engine is None:
            return
        if self._grabbed:
            return
        try:
            self._drain_touch()
        except Exception as exc:
            self._device_error(exc)
            return
        if not self._native_idle():
            return
        try:
            self.touch.grab()
            self._grabbed = True
        except Exception as exc:
            self._device_error(exc)
            return

        # Ignore any contact that was already down at grab time; only a new
        # TRACKING_ID may start a gesture.  The passive frame's cached slot
        # coordinates are inherited, so the first press after a menu (or after
        # a release) still begins at the point the slot already holds even
        # when it reports a single axis or no coordinates at all.
        self.frame = self._contact_frame.clone_positions()
        self.engine.set_active(True)
        self.capturing = True
        self.collapsed = False
        self._expand_pending = False
        self._reset_pill()
        self._override = None
        self._register_touch()
        self._visual_force = True

    def _suspend_capture(self, reason):
        """Neutralise the controller but keep the touchscreen grab."""

        self.capturing = False
        try:
            if self.engine is not None and self.engine.active:
                events = self.engine.set_active(False)
                if self.sink is not None:
                    try:
                        self.sink.emit(events)
                    except Exception as exc:
                        self._log("sink emit failed: %s" % (exc,))
        finally:
            if self.sink is not None:
                try:
                    self.sink.neutralize()
                except Exception as exc:
                    # The sink may still hold the released contact; keep the
                    # failure pending so a later frame finishes the cleanup.
                    self._neutralize_pending = True
                    self._log("sink neutralize failed: %s" % (exc,))
                else:
                    self._neutralize_pending = False
            self.frame = FrameAccumulator()
            self._visual_force = True

    def _release_capture(self, reason):
        self._stop_haptics()
        had_capture = (
            self.capturing
            or self._grabbed
            or (self.engine is not None and self.engine.active)
        )
        if not had_capture:
            return

        self._suspend_capture(reason)
        self._ungrab()
        self._reset_pill()
        self._log("capture released: %s" % (reason,))

    def _retry_neutralize(self):
        """Finish a release neutralisation the sink rejected.

        The pending flag belongs to the capture release only and is cleared by
        the first successful call.  It is retried from the frame loop in every
        mode - released, cooldown, hidden controls, or no eligible session -
        because an unreleased pad contact survives all of those.  A failure is
        logged and left pending.

        A pending retry never blocks the physical passive read, contact
        tracking or status publishing; it only withholds the next grab, which
        _update_capture allows again once this call has succeeded.  While the
        bridge has no own composite, every touch call would be rejected before
        reaching InputPlumber, so the retry waits for discovery to find it
        again instead of sending doomed releases on every frame.
        """

        if not self._neutralize_pending:
            return
        sink = self.sink
        if sink is None:
            # Closing the sink dropped the channel itself; the flag survives
            # so the next sink is cleaned once before it is used.
            return
        bridge = self.bridge
        if bridge is None or getattr(bridge, "own_path", None) is None:
            return
        try:
            sink.neutralize()
        except Exception as exc:
            self._log("sink neutralize retry failed: %s" % (exc,))
            return
        self._neutralize_pending = False

    def _ungrab(self):
        self._unregister_touch()
        touch = self.touch
        if touch is not None and self._grabbed:
            self._grabbed = False
            self._collapse_pending = False
            try:
                touch.ungrab()
            except Exception as exc:
                self._log("touch ungrab failed: %s" % (exc,))

    def _register_touch(self):
        if self._selector is None or self.touch is None:
            return
        try:
            self._selector.register(
                self.touch.fileno(), selectors.EVENT_READ, "touch"
            )
            self._touch_registered = True
        except (KeyError, ValueError, OSError) as exc:
            self._log("cannot watch touch device: %s" % (exc,))

    def _unregister_touch(self):
        if self._selector is None or not self._touch_registered:
            return
        self._touch_registered = False
        try:
            self._selector.unregister(self.touch.fileno())
        except (KeyError, ValueError, OSError):
            pass

    def _device_error(self, exc):
        self._log("touch device error: %s" % (exc,))
        self._failures["touch"] = str(exc)
        try:
            self._release_capture("touch device error")
        except Exception as inner:
            self._log("release after device error failed: %s" % (inner,))

        self._close_touch()
        self._close_focus()
        self._stop_overlay()

        sink = self.sink
        self.sink = None
        if sink is not None:
            try:
                sink.close()
            except Exception as inner:
                self._log("sink close failed: %s" % (inner,))
        self._close_ui()
        self.bridge = None
        self._last_discovery = -1e9
        self._override = (
            "unavailable",
            "触摸设备错误：%s" % (exc,),
            self._now() + 3.0,
        )

    # ------------------------------------------------------------------
    # touch input
    # ------------------------------------------------------------------

    def _track_contact(self, event):
        """Update physical contact bookkeeping for one raw evdev event.

        This never dispatches to the engine or the compositor.  Contacts that
        begin while the overlay is capturing are "ours"; contacts that begin
        while the device is released belong to the game and hold back
        recapture until they end.
        """

        batch = self._contact_frame.consume(
            event.type, event.code, event.value
        )
        for entry in batch:
            kind = entry[0]
            if kind == "begin":
                pointer = entry[1]
                self._contacts.add(pointer)
                if not self.capturing:
                    self._native_contacts.add(pointer)
            elif kind == "end":
                pointer = entry[1]
                self._contacts.discard(pointer)
                self._native_contacts.discard(pointer)
            elif kind == "cancel":
                self._contacts.clear()
                self._native_contacts.clear()
        return batch

    def _drain_touch(self):
        if self.touch is None:
            return
        try:
            for event in self.touch.read():
                self._track_contact(event)
        except BlockingIOError:
            return

    def _read_touch(self, now):
        if self.touch is None:
            return
        try:
            for event in self.touch.read():
                batch = self._track_contact(event)
                if not self.capturing:
                    self._consume_passive(batch, now)
                    continue
                batch = self.frame.consume(event.type, event.code, event.value)
                if batch:
                    self._emit_batch(batch, now)
        except BlockingIOError:
            return
        except Exception as exc:
            self._device_error(exc)

    # -- passive hidden edge recognizer ---------------------------------

    def _passive_ready(self, now):
        """True when a hidden, native-touch session may recognize edges."""

        if not self.settings["enabled"]:
            return False
        if self.controls_visible or self.capturing or self._grabbed:
            return False
        if not bool(self.settings.get("edge_swipes", False)):
            return False
        if not self._session_ready():
            return False
        # A menu cooldown only delays recapture; it must not disable the
        # independent passive listener, because the panel is never grabbed.
        return bool(
            should_capture(
                True,
                self.heartbeat_appid,
                self.focus_apps[0],
                self.focus_apps[1],
                self._heartbeat_age(now),
                True,
            )
        )

    def _sync_passive(self, now):
        """Drop the passive recognizer before reads when eligibility is lost."""

        if not self._passive_ready(now):
            self._reset_passive()

    def _reset_passive(self, _reason=None):
        self._passive_pending_side = None
        self._passive_deadline = None
        engine = self._passive_engine
        self._passive_engine = None
        if engine is not None and engine.active:
            # Edge-only engines have no controls, so this emits nothing.
            engine.set_active(False)

    def _passive_begin(self, pointer, x, y, now):
        engine = self._passive_engine
        if engine is None:
            # An empty layout can never hit a control: only edge gestures
            # remain possible, and this engine is never fed to the sink.
            engine = TouchEngine(
                [], edge_swipes=True, aspect_ratio=self.aspect_ratio
            )
            self._passive_engine = engine
        else:
            engine.set_aspect_ratio(self.aspect_ratio)
        if not engine.active:
            engine.set_active(True)
        engine.begin(pointer, x, y, now)

    def _consume_passive(self, batch, now):
        if not batch:
            return
        if not self._passive_ready(now):
            self._reset_passive()
            return

        for entry in batch:
            kind = entry[0]
            if kind == "cancel":
                # SYN_DROPPED invalidates both the live gesture and any
                # deferred menu; nothing may leak from a partial frame.
                self._reset_passive()
                return

            if kind == "begin":
                if self._passive_pending_side is not None:
                    # A new physical sequence must not inherit the old
                    # sequence's already-running grace window.
                    self._passive_deadline = None
                x, y = self._map_point(entry[2], entry[3])
                self._passive_begin(entry[1], x, y, now)
                continue

            engine = self._passive_engine
            if engine is None or not engine.active:
                continue

            pointer = entry[1]
            if kind == "move":
                x, y = self._map_point(entry[2], entry[3])
                events = engine.move(pointer, x, y, now)
                for event in events:
                    if (
                        event[0] == "menu"
                        and self._passive_pending_side is None
                    ):
                        # Record only the first menu of this physical
                        # sequence; dispatch waits until every finger lifts.
                        self._passive_pending_side = event[1]
                        self._passive_deadline = None
            elif kind == "end":
                engine.end(pointer, now)

        if (
            self._passive_pending_side is not None
            and self._passive_deadline is None
            and self._all_idle()
        ):
            self._passive_deadline = now + PASSIVE_MENU_GRACE_SECONDS

    def _dispatch_passive(self, now):
        side = self._passive_pending_side
        if side is None or self._passive_deadline is None:
            return
        if now < self._passive_deadline or not self._all_idle():
            return

        # The grace may have outlived the normal focus poll.  Confirm the
        # current game session from fresh hardware state immediately before
        # the one deferred chord; a native menu opening in this window wins.
        self._poll_focus(now, force=True)
        self._update_capture(now)
        if not self._passive_ready(now):
            self._reset_passive()
            return

        self._reset_passive()
        self._handle_menu(side, now, passive=True)

    def _pill_begin(self, pointer, x, y):
        """Claim a pointer that starts inside the expanded collapse pill."""

        if not self.controls_visible or not self._overlay_ready():
            # The pill only exists while the expanded controls are shown.
            return False
        if self._pill_pointer is not None:
            return False
        if not collapse.contains(collapse.pill_normalized(), x, y):
            return False
        self._pill_pointer = pointer
        self._pill_dragout = False
        return True

    def _pill_move(self, x, y):
        if not collapse.contains(collapse.pill_normalized(), x, y):
            # Once the finger leaves the pill the gesture can no longer be a
            # tap, even if it returns before lift-off.
            self._pill_dragout = True

    def _request_collapse(self, now):
        """Hide every overlay control without stealing any live sequence."""

        self._reset_pill()
        self._apply_controls_visibility(False, now)
        self._log("capture suspended: collapsed")

    def _settle_collapse(self):
        if self._grabbed and not self.capturing and self._all_idle():
            self._ungrab()

    def _emit_batch(self, batch, now):
        if self.engine is None or not self.capturing:
            return

        events = []
        cancelled = False
        collapse_requested = False
        for entry in batch:
            kind = entry[0]
            if kind == "cancel":
                cancelled = True
                self._reset_pill()
                continue
            pointer = entry[1]
            if kind == "begin":
                x, y = self._map_point(entry[2], entry[3])
                if self._pill_begin(pointer, x, y):
                    continue
                events.extend(self.engine.begin(pointer, x, y, now))
            elif kind == "move":
                x, y = self._map_point(entry[2], entry[3])
                if pointer == self._pill_pointer:
                    self._pill_move(x, y)
                    continue
                events.extend(self.engine.move(pointer, x, y, now))
            elif kind == "end":
                if pointer == self._pill_pointer:
                    tap = not self._pill_dragout
                    self._reset_pill()
                    if tap:
                        # Only a completed tap inside the pill collapses.
                        collapse_requested = True
                    continue
                events.extend(self.engine.end(pointer, now))

        if cancelled:
            # A dropped SYN frame invalidates all contacts; release every
            # control but stay grabbed and active for the next TRACKING_ID.
            events.extend(self.engine.cancel_all())

        if collapse_requested:
            # Any other controls touched in this frame are neutralised with
            # the engine; no partial or DOWN-only sequence is sent.
            self._request_collapse(now)
            return

        menu = next((event for event in events if event[0] == "menu"), None)
        if menu is not None:
            # The menu gesture wins over anything else in this frame: ordinary
            # stale events must never reach the game after capture release.
            self._handle_menu(menu[1], now)
            return

        if events and self.sink is not None:
            self.sink.emit(events)
        if cancelled:
            # A dropped SYN frame is a neutralisation, not player input; the
            # motor adapter must clear any gesture without a new pulse.
            if self.sink is not None:
                self.sink.neutralize()
            self._stop_haptics()
            return
        if events and self.sink is not None:
            self._consume_haptics(events, now)

    def _handle_menu(self, side, now, passive=False):
        chord = MENU_CHORDS.get(side)
        if chord is None:
            return False

        # Release the grab and every held control before opening the menu, so
        # the compositor sees a clean controller and no stale touch arrives.
        self._capture_block_until = float(now) + MENU_RECAPTURE_SECONDS
        self._release_capture("edge menu")

        bridge = self.bridge
        if bridge is None or getattr(bridge, "own_path", None) is None:
            return False
        sent = False
        try:
            sent = bridge.send_chord([chord]) is not False
        except Exception as exc:
            self._log("menu chord failed: %s" % (exc,))
        if sent and passive:
            # One line for real-device verification; the hidden path never
            # grabs, replays, or injects the game's own touch stream.
            self._log("hidden edge menu dispatched: %s" % (side,))
        return sent

    def _map_point(self, x, y):
        if self._absinfo is None:
            raise RuntimeError("touch absinfo is unavailable")
        abs_x, abs_y = self._absinfo
        return transform_point(
            x,
            y,
            abs_x.min,
            abs_x.max,
            abs_y.min,
            abs_y.max,
            self.rotation,
        )

    # ------------------------------------------------------------------
    # overlay visuals
    # ------------------------------------------------------------------

    def _queue_visual(self):
        if self.overlay is None or not self.overlay.ready:
            return
        snapshot = (
            self.engine.snapshot()
            if self.engine is not None
            else {"active": False, "controls": {}}
        )
        if self.effective_controls is None:
            self.effective_controls = effective_layout(
                self.settings["layout"], self.settings["scale"]
            )

        overlay_ready = self._overlay_ready()
        if self.controls_visible and self.capturing:
            visible = True
            mode = "expanded"
            # The pill is drawn by the overlay and hit-tested here; it needs
            # only the ready handshake, never compositor bubble routing.
            chrome = overlay_ready
        else:
            # A hidden request, menu focus, heartbeat loss, or a disabled
            # session hides the entire overlay plane.  There is no bubble.
            visible = False
            mode = "expanded"
            chrome = False

        message = {
            "visible": visible,
            "opacity": self.settings["opacity"],
            "mode": mode,
            "chrome": chrome,
            "edge_swipes": bool(self.settings.get("edge_swipes", False)),
            "right_stick_fullscreen": bool(
                self.settings.get("right_stick_fullscreen", False)
            ),
            "button_color": self.settings["button_color"],
            "bubble": dict(self.bubble),
            "layout": self.effective_controls,
            "snapshot": snapshot,
        }
        if message != self._sent_visual:
            self._pending_visual = message
        else:
            # The current state is already on screen; drop any older pending
            # frame so a transient change cannot be replayed later.
            self._pending_visual = None

    def _flush_visual(self, now):
        if self._pending_visual is None or self.overlay is None:
            return
        if not self.overlay.ready:
            return
        if not self._visual_force and now - self._last_visual < VISUAL_SECONDS:
            return
        message = self._pending_visual
        try:
            self.overlay.send(message)
        except Exception as exc:
            self._log("overlay send failed: %s" % (exc,))
            return
        self._sent_visual = message
        self._pending_visual = None
        self._last_visual = now
        self._visual_force = False

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def _missing_components(self):
        missing = []
        if self._supported is False:
            missing.append("Gamescope 未包含触摸叠加补丁（ARMADA 标记）")
        if self.session_env is None:
            missing.append(
                self._failures.get("session", "Steam 主 X11 会话不可用")
            )
        if self.focus is None:
            missing.append(
                "焦点监视不可用：%s"
                % (self._failures.get("focus", "未知原因"),)
            )
        if "rotation" in self._failures:
            missing.append(self._failures["rotation"])
        if self.touch is None:
            missing.append(
                "主触摸设备不可用：%s"
                % (
                    self._failures.get("touch")
                    or self._failures.get("device")
                    or "未打开",
                )
            )
        if self.ui is None or not self.source_path:
            missing.append(
                "控制器 source 未创建：%s"
                % (self._failures.get("source", "未创建"),)
            )
        elif self.bridge is None or not self.bridge.own_path:
            missing.append(
                "InputPlumber composite 未就绪：%s"
                % (self._failures.get("bridge", "未发现自有 composite"),)
            )
        elif self.sink is None:
            missing.append(
                "InputPlumber 触摸通道未建立：%s"
                % (self._failures.get("sink", "未创建"),)
            )
        if self.overlay is None:
            missing.append(
                "叠加进程未启动：%s"
                % (self._failures.get("overlay", "未启动"),)
            )
        elif not self.overlay.ready:
            missing.append("叠加窗口尚未自报就绪")
        return missing

    def _describe(self, now):
        if not self.settings["enabled"]:
            return "disabled", "触摸控制已关闭"

        if self._override is not None and now < self._override[2]:
            return self._override[0], self._override[1]

        missing = self._missing_components()
        if missing:
            return "waiting", "缺少：" + "；".join(missing)

        if self.heartbeat_appid is None or self.heartbeat_time is None:
            return "waiting", "等待游戏心跳（由前端每秒发送）"
        age = self._heartbeat_age(now)
        if age is None or age > MAX_HEARTBEAT_AGE:
            return "waiting", "前端心跳已超时"

        input_app, gfx_app = self.focus_apps
        if input_app == QAM_APP_ID:
            return "waiting", "Steam 快捷菜单在前台，触摸控制暂停"
        if (
            input_app is None
            or gfx_app is None
            or str(input_app) != str(gfx_app)
            or str(input_app) != str(self.heartbeat_appid)
        ):
            return "waiting", "前台焦点与游戏心跳不一致"

        if self.capturing:
            return "capturing", "正在捕获触摸输入"
        return "ready", "就绪，等待游戏前台会话"

    def _publish(self, now, force=False):
        state, detail = self._describe(now)
        haptics_available, haptics_detail = self._haptics_status()
        status = {
            "state": state,
            "detail": detail,
            "capturing": bool(self.capturing),
            "controls_visible": bool(self.controls_visible),
            "source_path": self.source_path,
            "composite_path": (
                self.bridge.own_path if self.bridge is not None else None
            ),
            "device": (
                getattr(self.touch, "path", None)
                if self.touch is not None
                else None
            ),
            "rotation": int(self.rotation),
            "haptics_available": haptics_available,
            "haptics_detail": haptics_detail,
        }
        if not force and status == self._status:
            return
        self._status = status
        line = (
            json.dumps(
                {"type": "status", "status": status},
                separators=(",", ":"),
            )
            + "\n"
        )
        try:
            self._stdout.write(line)
            self._stdout.flush()
        except (OSError, ValueError) as exc:
            self._log("cannot write status: %s" % (exc,))
            self._stop = True

    # ------------------------------------------------------------------
    # loop
    # ------------------------------------------------------------------

    def tick(self, now=None):
        # One clock sample opens the tick.  Every later stage adds only the
        # non-negative time that really elapsed since the previous sample, so
        # the explicit frame time keeps its own domain: a clock that reads 1000
        # can never replace a frame time that reads 10.
        checkpoint = self._now()
        if now is None:
            now = checkpoint

        if not self.settings["enabled"]:
            self._publish(now)
            return

        if self.engine is None:
            if self.effective_controls is None:
                self.effective_controls = effective_layout(
                    self.settings["layout"], self.settings["scale"]
                )
            self.engine = self._make_engine(self.settings)

        # A finished background discovery is adopted here, on the input
        # thread, and only its verdict is allowed to change the live bridge.
        self._poll_probe(now)
        if now - self._last_discovery >= DISCOVERY_SECONDS:
            self._discover(now)

        self._poll_overlay(now)
        self._poll_focus(now)
        self._poll_haptics(now)
        # Retry an outstanding release neutralisation before deciding capture,
        # so a leftover contact is cleared ahead of any new grab; a failure is
        # contained inside the retry and cannot block the rest of the frame.
        self._retry_neutralize()
        # Discovery, controller writes, focus, haptics and a release retry may
        # each spend seconds inside a D-Bus reply.  Every policy decision below
        # must run on the time that really passed, or an expired heartbeat
        # would keep capturing.
        now, checkpoint = self._advance(now, checkpoint)
        self._update_capture(now)
        # Eligibility is evaluated before the passive stream is consumed, so
        # a lost menu/focus/heartbeat cancels even with no new touch frame.
        self._sync_passive(now)

        # Physical contacts are read in every mode.  While released this is
        # bookkeeping plus the independent hidden edge listener; nothing is
        # dispatched to the main controller engine or the game's touch owner.
        if self.touch is not None:
            self._read_touch(now)
        # The physical read can block as well.  It is measured from the
        # checkpoint the polls left behind, so one block is never counted
        # twice.
        now, checkpoint = self._advance(now, checkpoint)
        self._settle_collapse()
        self._dispatch_passive(now)
        if self.capturing and self.engine is not None and self.sink is not None:
            events = self.engine.tick(now)
            if events:
                try:
                    self.sink.emit(events)
                except Exception as exc:
                    self._device_error(exc)

        self._queue_visual()
        self._flush_visual(now)
        self._publish(now)

    def _advance(self, now, checkpoint):
        """Return *(now advanced by the elapsed clock time, new checkpoint)*.

        ``checkpoint`` is a sample this tick already took; the caller passes
        the returned one to the next stage.  Only the non-negative delta since
        that sample is added to *now*, so the frame time never adopts the
        clock's absolute domain and two stages cannot count the same elapsed
        time twice.
        """

        try:
            current = float(self._now())
        except (TypeError, ValueError):
            return now, checkpoint
        if not math.isfinite(current):
            return now, checkpoint
        try:
            spent = current - float(checkpoint)
        except (TypeError, ValueError):
            return now, current
        if spent <= 0.0:
            return now, current
        try:
            advanced = float(now) + spent
        except (TypeError, ValueError):
            return now, current
        return advanced, current

    def _read_stdin(self, fd):
        while True:
            try:
                chunk = os.read(fd, READ_CHUNK)
            except BlockingIOError:
                return
            except OSError as exc:
                self._log("stdin read failed: %s" % (exc,))
                self._stop = True
                return
            if not chunk:
                self.handle_eof()
                return

            self._stdin_buffer += chunk
            lines = self._stdin_buffer.split(b"\n")
            self._stdin_buffer = lines.pop()
            if len(self._stdin_buffer) > MAX_LINE_BYTES:
                self._log("dropping oversized stdin line")
                self._stdin_buffer = b""
            for line in lines:
                self.handle_line(line)

    def _handle_signal(self, _signum, _frame):
        self._stop = True

    def run(self):
        if self._install_signals:
            for number in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(number, self._handle_signal)
                except (ValueError, OSError):
                    pass

        fd = self._stdin_fd
        if fd is None:
            fd = sys.stdin.fileno()
        try:
            os.set_blocking(fd, False)
        except OSError as exc:
            self._log("cannot make stdin non-blocking: %s" % (exc,))

        selector = selectors.DefaultSelector()
        self._selector = selector
        try:
            selector.register(fd, selectors.EVENT_READ, "stdin")
        except (KeyError, ValueError, OSError) as exc:
            self._log("cannot watch stdin: %s" % (exc,))

        code = 0
        try:
            while not self._stop:
                events = selector.select(TICK_SECONDS)
                for key, _mask in events:
                    if key.data == "stdin":
                        self._read_stdin(fd)
                if self._stop:
                    break
                self.tick()
        except Exception as exc:
            self._log("runtime failed: %r" % (exc,))
            self._override = (
                "unavailable",
                "运行时错误：%s" % (exc,),
                self._now() + 3.0,
            )
            try:
                self._publish(self._now(), force=True)
            except Exception:
                pass
            code = 1
        finally:
            self.shutdown()
        return code

    def shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._stop = True
        # Stop background discovery first so no late answer can revive the
        # hardware this shutdown is about to close.
        self._stop_probe_executor()

        try:
            self._release_capture("shutdown")
        except Exception as exc:
            self._log("release during shutdown failed: %s" % (exc,))

        self._close_touch()
        self._close_focus()

        sink = self.sink
        self.sink = None
        if sink is not None:
            try:
                sink.close()
            except Exception as exc:
                self._log("sink close failed: %s" % (exc,))
        self._close_ui()
        self.bridge = None

        haptics = self.haptics
        self.haptics = None
        if haptics is not None:
            try:
                haptics.close()
            except Exception as exc:
                self._log("haptics close failed: %s" % (exc,))

        self._stop_overlay()

        selector = self._selector
        self._selector = None
        self._touch_registered = False
        if selector is not None:
            try:
                selector.close()
            except OSError:
                pass

    def _log(self, text):
        try:
            print(
                "freedom-touch-daemon: %s" % (text,),
                file=self._stderr,
                flush=True,
            )
        except (OSError, ValueError):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m py_modules.freedom_touch.daemon",
        description="Freedom Touch controller root runtime",
    )
    parser.add_argument(
        "--user",
        required=True,
        help="desktop account that owns the Gamescope session and overlay",
    )
    args = parser.parse_args(argv)

    if pwd is None:
        print(
            "freedom-touch-daemon: pwd is unavailable on this platform",
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        account = pwd.getpwnam(args.user)
    except KeyError:
        print(
            "freedom-touch-daemon: unknown user %r" % (args.user,),
            file=sys.stderr,
            flush=True,
        )
        return 2
    except OSError as exc:
        print(
            "freedom-touch-daemon: cannot read account %r: %s"
            % (args.user, exc),
            file=sys.stderr,
            flush=True,
        )
        return 2

    plugin_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    daemon = Daemon(
        account.pw_name,
        account.pw_uid,
        account.pw_gid,
        account.pw_dir,
        plugin_root,
    )
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
