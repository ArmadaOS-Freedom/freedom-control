"""Freedom Touch runtime owned by the Freedom Control plugin.

The daemon, overlay and pure policy modules stay in ``py_modules.freedom_touch``
under their original module paths; this component only owns the settings file,
the child process and the state that the Controller exposes.  Startup follows
the saved configuration: an enabled configuration is resumed and its daemon
started, while a disabled or invalid configuration stays lazy -- nothing is
started until an explicit enable.  An explicit disable stops this component's
own child again.

Edge swipes are not a second persisted preference.  The Controller already
owns the canonical system switches, so the runtime value pushed to the daemon
is derived from them through :meth:`TouchController.sync_swipe_policy`.
"""

import asyncio
import copy
import json
import os
import sys
import tempfile
import uuid

import decky

# The plugin root, not the armada_control package: the daemon and its pure
# modules are imported as ``py_modules.freedom_touch.*`` from this directory.
PLUGIN_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from py_modules.freedom_touch.presets import (  # noqa: E402
    BUILTIN_PRESET_IDS,
    DEFAULT_PRESET_ID,
    find_preset,
    preset_catalog,
)
from py_modules.freedom_touch.runtime_policy import (  # noqa: E402
    is_legacy_settings,
    validate_settings,
)

CHILD_PYTHON = "/usr/bin/python3"
SETTINGS_FILENAME = "touch-controller.json"

OPTION_KEYS = frozenset(
    {
        "enabled",
        "opacity",
        "scale",
        "deadzone",
        "edge_swipes",
        "haptics_enabled",
        "haptics_strength",
    }
)

STOP_WAIT_SECONDS = 3.0
TERMINATE_WAIT_SECONDS = 2.0
# A daemon that stopped reading its pipe must not hold the controller lock
# forever: the write and the stream close are both bounded.
WRITE_TIMEOUT_SECONDS = 2.0
STREAM_CLOSE_TIMEOUT_SECONDS = 1.0
CONTROLS_VISIBLE_TIMEOUT = 2.0

MAX_STDOUT_BUFFER = 1024 * 1024
MAX_STDERR_BUFFER = 8 * 1024
MAX_LOG_LINE = 500


class TouchController:
    def __init__(self):
        self._plugin_root = PLUGIN_ROOT
        self._settings = validate_settings({"enabled": False})
        self._status = {
            "state": "disabled",
            "detail": "触摸控制已关闭",
            "capturing": False,
            "controls_visible": True,
        }
        self._settings_error = None
        self._lock = None
        self._status_event = None
        # Effective edge-swipe policy, derived from the Controller's system
        # switches.  Persisted settings never win over this value.
        self._swipe_policy = False
        self._swipe_policy_known = False

        self._child = None
        self._stdout_task = None
        self._stderr_task = None
        self._wait_task = None
        self._stopping = False

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------

    async def get_state(self):
        return self._snapshot()

    async def set_settings(self, settings):
        canonical = validate_settings(settings)
        async with self._ensure_lock():
            return await self._commit_locked(canonical, allow_restart=True)

    async def update_options(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("options must be a dict")
        unknown = set(patch) - OPTION_KEYS
        if unknown:
            raise ValueError(
                "unsupported option: %s" % (", ".join(sorted(unknown)),)
            )

        async with self._ensure_lock():
            current = copy.deepcopy(self._settings)
            current.update(patch)
            canonical = validate_settings(current)
            return await self._commit_locked(canonical, allow_restart=True)

    async def select_preset(self, preset_id):
        async with self._ensure_lock():
            canonical = self._select_locked(preset_id)
            return await self._commit_locked(canonical, allow_restart=False)

    async def save_preset(self, name, layout, scale, overwrite_id=None):
        async with self._ensure_lock():
            current = copy.deepcopy(self._settings)
            custom = current["custom_presets"]

            if overwrite_id is not None:
                if not isinstance(overwrite_id, str):
                    raise ValueError("overwrite_id must be a string")
                if overwrite_id in BUILTIN_PRESET_IDS:
                    raise ValueError("built-in presets are read-only")
                index = None
                for position, preset in enumerate(custom):
                    if preset["id"] == overwrite_id:
                        index = position
                        break
                if index is None:
                    raise ValueError("unknown preset to overwrite: %r" % (
                        overwrite_id,
                    ))
                preset_id = overwrite_id
            else:
                preset_id = str(uuid.uuid4())
                while any(item["id"] == preset_id for item in custom):
                    preset_id = str(uuid.uuid4())
                index = None

            item = {
                "id": preset_id,
                "name": name,
                "layout": layout,
                "scale": scale,
            }
            if index is None:
                custom.append(item)
            else:
                custom[index] = item

            current["active_preset_id"] = preset_id
            current["layout"] = copy.deepcopy(layout)
            current["scale"] = scale
            canonical = validate_settings(current)
            return await self._commit_locked(canonical, allow_restart=False)

    async def reset_layout(self):
        async with self._ensure_lock():
            canonical = self._select_locked(DEFAULT_PRESET_ID)
            return await self._commit_locked(canonical, allow_restart=False)

    async def heartbeat(self, appid=None):
        if appid is not None and not isinstance(appid, str):
            raise ValueError("appid must be a string or None")

        # Settings, ownership and the child are re-read under the same lock
        # that guards the write and the status update.  A ping that queued
        # behind an explicit disable (or shutdown, or a child replacement)
        # therefore sees the state that disable left behind instead of
        # sending to a child that is already gone and reporting its absence
        # as a fresh failure.  Only a live, enabled child can be reported
        # unavailable.
        async with self._ensure_lock():
            if not self._settings["enabled"] or not self._child_running():
                return dict(self._status)
            try:
                await self._send_unlocked(
                    {"type": "heartbeat", "appid": appid}
                )
            except Exception as exc:
                decky.logger.error("Freedom Touch heartbeat failed: %s", exc)
                self._set_status(
                    "unavailable",
                    "无法向触摸控制器发送心跳：%s" % exc,
                    False,
                )
        return dict(self._status)

    async def preview_haptics(self):
        """Ask the daemon for one direct preview tick; never asserts success."""

        settings = self._settings
        if (
            not settings["enabled"]
            or not settings.get("haptics_enabled", True)
            or settings.get("haptics_strength", 0) <= 0
            or not self._child_running()
            or self._status.get("haptics_available") is not True
        ):
            return {"queued": False}

        try:
            async with self._ensure_lock():
                await self._send_unlocked({"type": "preview_haptics"})
        except Exception as exc:
            decky.logger.error(
                "Freedom Touch haptics preview failed: %s", exc
            )
            return {"queued": False}
        return {"queued": True}

    async def set_controls_visible(self, visible):
        """Request a session-only overlay visibility change.

        The daemon is authoritative: the call only succeeds after a status
        packet confirms the requested value.  It never restarts the daemon or
        session and never writes the settings file.
        """

        if not isinstance(visible, bool):
            raise ValueError("visible must be a boolean")
        if not self._settings["enabled"] or not self._child_running():
            raise RuntimeError("触摸控制器未启用或未运行")

        async with self._ensure_lock():
            if not self._settings["enabled"] or not self._child_running():
                raise RuntimeError("触摸控制器未启用或未运行")

            event = asyncio.Event()
            self._status_event = event
            try:
                try:
                    await self._send_unlocked(
                        {"type": "controls_visibility", "visible": visible}
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "无法向触摸控制器发送可见性请求：%s" % exc
                    ) from exc

                deadline = asyncio.get_running_loop().time() + (
                    CONTROLS_VISIBLE_TIMEOUT
                )
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0.0:
                        raise RuntimeError("触摸控制器未确认可见性切换")
                    try:
                        await asyncio.wait_for(event.wait(), remaining)
                    except asyncio.TimeoutError:
                        raise RuntimeError(
                            "触摸控制器未确认可见性切换"
                        ) from None
                    event.clear()
                    if self._status.get("controls_visible") is visible:
                        return self._snapshot()
            finally:
                if self._status_event is event:
                    self._status_event = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self, swipe_enabled, swipe_in_game):
        """Load the saved state and start the daemon only if it was enabled.

        The effective edge-swipe policy is known before the configuration is
        applied, so a persisted stale value can never reach the daemon.
        """

        self._swipe_policy = bool(swipe_enabled) and bool(swipe_in_game)
        self._swipe_policy_known = True

        async with self._ensure_lock():
            settings, error = self._load_settings()
            self._settings = self._apply_swipe_policy(settings)
            if error is not None:
                self._settings_error = error
                self._status = {
                    "state": "error",
                    "detail": error,
                    "capturing": False,
                    "controls_visible": True,
                }
            else:
                self._settings_error = None
                self._status = self._status_for_settings()

            # A missing dependency leaves the plugin usable: get_state reports
            # it.  An invalid configuration never starts a daemon.
            if error is None and self._settings["enabled"]:
                await self._start_child()
        return self._snapshot()

    async def sync_swipe_policy(self, enabled, in_game):
        """Apply the Controller's canonical edge-swipe switches."""

        policy = bool(enabled) and bool(in_game)

        async with self._ensure_lock():
            changed = policy != self._swipe_policy or not self._swipe_policy_known
            self._swipe_policy = policy
            self._swipe_policy_known = True
            if changed:
                self._settings = self._apply_swipe_policy(self._settings)
                if self._child_running():
                    try:
                        await self._send_unlocked(
                            {"type": "settings", "settings": self._settings}
                        )
                    except Exception as exc:
                        # The policy change must not break the Controller's own
                        # swipe switches; the failure stays visible in state.
                        decky.logger.error(
                            "Freedom Touch swipe policy push failed: %s", exc
                        )
                        self._set_status(
                            "unavailable",
                            "无法向触摸控制器发送设置：%s" % exc,
                            False,
                        )
        return self._snapshot()

    async def shutdown(self):
        stopped = await self._stop_child()
        if stopped:
            decky.logger.info("Freedom Touch backend stopped")
        else:
            # The child survived SIGKILL; _stop_child kept its handle.  Never
            # claim a stop that did not happen.
            decky.logger.warning(
                "Freedom Touch backend still running after shutdown"
            )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _settings_path(self):
        directory = os.fspath(decky.DECKY_PLUGIN_SETTINGS_DIR)
        return os.path.join(directory, SETTINGS_FILENAME)

    def _load_settings(self):
        path = self._settings_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return validate_settings({"enabled": False}), None
        except (OSError, ValueError) as exc:
            return (
                validate_settings({"enabled": False}),
                "现有设置文件无效，未覆盖：%s" % exc,
            )

        try:
            settings = validate_settings(raw)
        except (ValueError, TypeError) as exc:
            return (
                validate_settings({"enabled": False}),
                "现有设置文件无效，未覆盖：%s" % exc,
            )

        # A v1 file is upgraded in memory; write the v2 document immediately so
        # the preserved legacy layout survives a crash before the first edit.
        if is_legacy_settings(raw):
            try:
                self._persist_settings(self._apply_swipe_policy(settings))
            except Exception as exc:
                decky.logger.warning(
                    "Freedom Touch could not persist settings migration: %s",
                    exc,
                )
        return settings, None

    def _persist_settings(self, settings):
        directory = os.fspath(decky.DECKY_PLUGIN_SETTINGS_DIR)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, SETTINGS_FILENAME)

        descriptor, temp_path = tempfile.mkstemp(
            prefix=".touch-controller-", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    settings,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temp_path, path)

            # Persist the rename itself where the filesystem supports it.
            try:
                directory_fd = os.open(directory, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    pass
                finally:
                    os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    async def _commit_locked(self, canonical, allow_restart):
        canonical = self._apply_swipe_policy(canonical)
        had_error = self._settings_error is not None

        self._persist_settings(canonical)
        self._settings = canonical
        self._settings_error = None

        if had_error:
            self._set_status(
                "starting" if canonical["enabled"] else "disabled",
                "正在启动触摸控制器…" if canonical["enabled"] else "触摸控制已关闭",
                False,
            )

        # An explicit disable releases input and stops the daemon this plugin
        # owns; nothing else may keep the touch device grabbed.
        if not canonical["enabled"]:
            exited = True
            if self._child_running():
                exited = await self._stop_child_locked()
            if exited:
                self._set_status("disabled", "触摸控制已关闭", False)
            else:
                # The handle is kept for a later retry; claiming "disabled"
                # here would report an exit that never happened.
                self._set_status(
                    "unavailable", "无法停止触摸控制器：进程未退出", False
                )
            return self._snapshot()

        started = False
        if allow_restart and not self._child_running():
            # One attempt per explicit enable request; no background retry loop.
            # A fresh child receives this canonical settings object itself, so
            # do not push it a second time below.
            started = await self._start_child()

        if self._child_running() and not started:
            try:
                await self._send_unlocked(
                    {"type": "settings", "settings": canonical}
                )
            except Exception as exc:
                decky.logger.error("Freedom Touch settings push failed: %s", exc)
                self._set_status(
                    "unavailable",
                    "无法向触摸控制器发送设置：%s" % exc,
                    False,
                )
                raise

        return self._snapshot()

    def _apply_swipe_policy(self, settings):
        canonical = copy.deepcopy(settings)
        canonical["edge_swipes"] = self._swipe_policy
        return canonical

    # ------------------------------------------------------------------
    # Child process
    # ------------------------------------------------------------------

    def _child_environment(self):
        env = dict(os.environ)
        # The daemon must use the system Python's evdev/GI, never Decky's venv.
        # An inherited PYTHONHOME from Decky's embedded interpreter makes the
        # system python3 look for its standard library in the wrong prefix.
        env.pop("PYTHONHOME", None)
        # Decky's loader is a PyInstaller bundle: it exports its unpack dir as
        # LD_LIBRARY_PATH (keeping the previous value in LD_LIBRARY_PATH_ORIG).
        # Left in place, /usr/bin/python3 loads the bundled libpython instead of
        # the system one and fails before it can import encodings.
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
        env["PYTHONPATH"] = self._plugin_root
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    async def _start_child(self):
        if self._child_running():
            return True

        await self._dispose_child()

        try:
            child = await asyncio.create_subprocess_exec(
                CHILD_PYTHON,
                "-m",
                "py_modules.freedom_touch.daemon",
                "--user",
                str(decky.DECKY_USER),
                cwd=self._plugin_root,
                env=self._child_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            decky.logger.error("Freedom Touch child failed to start: %s", exc)
            self._set_status(
                "error",
                "无法启动触摸控制器：%s" % exc,
                False,
            )
            return False

        self._stopping = False
        self._child = child
        self._stdout_task = asyncio.create_task(self._read_stdout(child))
        self._stderr_task = asyncio.create_task(self._read_stderr(child))
        self._wait_task = asyncio.create_task(self._wait_child(child))

        self._set_status(*self._starting_status())

        # A daemon started by a previous plugin instance learned settings over
        # stdin; a new process has none.  Push the persisted canonical settings
        # exactly once, now that the reader tasks exist.  This is called with
        # the lock already held by _commit_locked on restart, so use the
        # unlocked writer directly.
        try:
            await self._send_unlocked(
                {"type": "settings", "settings": self._settings}
            )
        except Exception as exc:
            decky.logger.error(
                "Freedom Touch initial settings push failed: %s", exc
            )
            self._set_status(
                "unavailable",
                "无法向触摸控制器发送设置：%s" % exc,
                False,
            )
            return False
        return True

    async def _stop_child(self):
        async with self._ensure_lock():
            return await self._stop_child_locked()

    async def _stop_child_locked(self):
        """Stop the owned child; return True once the process is gone.

        Every wait is bounded, so a full stdin pipe or a process that ignores
        SIGKILL cannot hold the controller lock forever.  A child that survives
        the bounded kill wait keeps its handle: dropping it would leave the only
        reaper behind and would claim an exit that never happened.
        """

        child = self._child
        self._stopping = True
        exited = child is None or child.returncode is not None
        try:
            if not exited:
                try:
                    await self._send_unlocked({"type": "stop"})
                except Exception:
                    # A failed stop write is not a reason to keep writing; fall
                    # through to the existing wait/terminate/kill recovery.
                    pass

                exited = await self._reap_child(child)
        finally:
            await self._cancel_child_tasks()
            if child is not None and child.stdin is not None:
                try:
                    child.stdin.close()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(
                        child.stdin.wait_closed(), STREAM_CLOSE_TIMEOUT_SECONDS
                    )
                except Exception:
                    pass
            if exited:
                self._child = None
            else:
                decky.logger.error(
                    "Freedom Touch child survived SIGKILL; keeping its handle"
                )
            self._stopping = False
        return exited

    @staticmethod
    async def _reap_child(child):
        """Escalate stop → terminate → kill, keeping every wait bounded."""

        try:
            await asyncio.wait_for(child.wait(), STOP_WAIT_SECONDS)
            return True
        except asyncio.TimeoutError:
            pass

        for escalate in (child.terminate, child.kill):
            try:
                escalate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(child.wait(), TERMINATE_WAIT_SECONDS)
                return True
            except asyncio.TimeoutError:
                continue
        return False

    async def _dispose_child(self):
        child = self._child
        await self._cancel_child_tasks()
        if child is not None and child.stdin is not None:
            try:
                child.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    child.stdin.wait_closed(), STREAM_CLOSE_TIMEOUT_SECONDS
                )
            except Exception:
                pass
        self._child = None

    async def _cancel_child_tasks(self):
        tasks = [
            task
            for task in (
                self._stdout_task,
                self._stderr_task,
                self._wait_task,
            )
            if task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._stdout_task = None
        self._stderr_task = None
        self._wait_task = None

    async def _send_unlocked(self, message):
        child = self._child
        if (
            child is None
            or child.returncode is not None
            or child.stdin is None
        ):
            raise RuntimeError("触摸控制器未运行")

        data = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        child.stdin.write(data)
        try:
            await asyncio.wait_for(
                child.stdin.drain(), WRITE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            # The pipe stayed full: report it like any other write failure so
            # the caller's existing error path runs instead of blocking the
            # controller lock on a daemon that stopped reading.
            raise RuntimeError(
                "触摸控制器写入超时（%s 秒）" % WRITE_TIMEOUT_SECONDS
            ) from None

    async def _wait_child(self, child):
        try:
            returncode = await child.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            returncode = None

        if self._child is not child or self._stopping:
            return

        if returncode is None:
            detail = "触摸控制器已退出"
        elif returncode == 0:
            detail = "触摸控制器已停止"
        else:
            detail = "触摸控制器已退出（退出码 %s）" % returncode
        self._set_status("unavailable", detail, False)

    async def _read_stdout(self, child):
        stream = child.stdout
        if stream is None:
            return

        buffer = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                line = buffer[:index]
                buffer = buffer[index + 1:]
                self._handle_stdout_line(child, line)
            if len(buffer) > MAX_STDOUT_BUFFER:
                decky.logger.warning("Freedom Touch child wrote an oversized line")
                buffer = b""

        if buffer:
            self._handle_stdout_line(child, buffer)

    def _handle_stdout_line(self, child, line):
        if (
            self._child is not child
            or self._stopping
            or child.returncode is not None
        ):
            return

        text = line.decode("utf-8", "replace").strip("\r\n")
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            decky.logger.warning("Freedom Touch child sent invalid JSON")
            return
        if not isinstance(message, dict) or message.get("type") != "status":
            return

        status = self._parse_status(message.get("status"))
        if status is None:
            decky.logger.warning("Freedom Touch child sent invalid status")
            return
        if self._settings_error is not None:
            return
        self._status = status
        event = self._status_event
        if event is not None:
            event.set()

    @staticmethod
    def _parse_status(value):
        if not isinstance(value, dict):
            return None
        state = value.get("state")
        detail = value.get("detail", "")
        capturing = value.get("capturing", False)
        controls_visible = value.get("controls_visible", True)
        if not isinstance(state, str) or not state:
            return None
        if not isinstance(detail, str):
            return None
        if not isinstance(capturing, bool):
            return None
        if not isinstance(controls_visible, bool):
            controls_visible = True
        status = {
            "state": state,
            "detail": detail,
            "capturing": capturing,
            "controls_visible": controls_visible,
        }
        for key in ("source_path", "composite_path", "device"):
            if key not in value:
                continue
            item = value[key]
            if isinstance(item, str):
                status[key] = item
        rotation = value.get("rotation")
        if isinstance(rotation, int) and not isinstance(rotation, bool):
            status["rotation"] = rotation
        haptics_available = value.get("haptics_available")
        if isinstance(haptics_available, bool):
            status["haptics_available"] = haptics_available
        haptics_detail = value.get("haptics_detail")
        if isinstance(haptics_detail, str):
            status["haptics_detail"] = haptics_detail
        return status

    async def _read_stderr(self, child):
        stream = child.stderr
        if stream is None:
            return

        buffer = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                index = buffer.find(b"\n")
                if index < 0:
                    break
                line = buffer[:index]
                buffer = buffer[index + 1:]
                self._log_stderr(line)
            if len(buffer) > MAX_STDERR_BUFFER:
                self._log_stderr(buffer[:MAX_STDERR_BUFFER])
                buffer = b""

        if buffer:
            self._log_stderr(buffer)

    @staticmethod
    def _log_stderr(line):
        text = line.decode("utf-8", "replace").strip("\r\n")
        if not text:
            return
        if len(text) > MAX_LOG_LINE:
            text = text[:MAX_LOG_LINE] + "…"
        decky.logger.warning("[freedom-touch] %s", text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _select_locked(self, preset_id):
        if not isinstance(preset_id, str) or not preset_id:
            raise ValueError("preset id must be a non-empty string")
        preset = find_preset(preset_id, self._settings["custom_presets"])
        if preset is None:
            raise ValueError("unknown preset: %r" % (preset_id,))

        current = copy.deepcopy(self._settings)
        current["layout"] = copy.deepcopy(preset["layout"])
        current["scale"] = preset["scale"]
        current["active_preset_id"] = preset["id"]
        return validate_settings(current)

    def _child_running(self):
        return self._child is not None and self._child.returncode is None

    def _status_for_settings(self):
        if self._settings["enabled"]:
            return {
                "state": "starting",
                "detail": "正在启动触摸控制器…",
                "capturing": False,
                "controls_visible": True,
            }
        return {
            "state": "disabled",
            "detail": "触摸控制已关闭",
            "capturing": False,
            "controls_visible": True,
        }

    def _starting_status(self):
        if self._settings["enabled"]:
            return ("starting", "正在启动触摸控制器…", False)
        return ("disabled", "触摸控制已关闭", False)

    def _set_status(self, state, detail, capturing):
        if self._settings_error is not None:
            return
        self._status = {
            "state": state,
            "detail": detail,
            "capturing": bool(capturing),
            "controls_visible": bool(
                self._status.get("controls_visible", True)
            ),
        }

    def _snapshot(self):
        return {
            "settings": copy.deepcopy(self._settings),
            "status": dict(self._status),
            "presets": preset_catalog(self._settings["custom_presets"]),
        }
