"""Read-only inspection and controlled start of one TF-card library conversion.

This is the TF backend migrated, behaviour unchanged, from the
mydanyi/armada-installer plugin (commit
1ac40b959085e6b55c2aef1467080b07560a2431), which is distributed under
GPL-3.0-or-later.  The copyright stays with the original author; the migrated
code is distributed here as part of this GPL-2.0 project with the copyright
holder's permission.  The fixed runtime and checkpoint paths and the
``armada-tf-library.service`` unit name are retained so both plugins share one
job state and a running conversion can be recovered after a restart.  Overlap
between two copies of *this* module is prevented by one shared lifecycle
``flock`` plus the unit-state check below; that is not an absolute guarantee,
because a legacy plugin build that never takes the lifecycle lock cannot be
forced to obey it.

The Decky process never touches a card itself.  ``inspect``/``prepare`` only
read state; the single mutation is a fixed ``armada-tf-library.service`` unit
handed to ``systemd-run`` after the caller confirms a previously prepared plan.
"""

import fcntl
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from . import tf_state

LOG_BYTES = 65536
LOG_LINES = 40


class InstallerError(Exception):
    """An expected failure with a stable machine-readable code."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _capture(runner, argv, timeout=30):
    # A frozen PyInstaller bundle points LD_LIBRARY_PATH at its own runtime
    # libraries.  The system programs started here must load the system
    # libraries instead, so use the pre-bundle value in the child environment
    # only; the parent environment is never modified.
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                  timeout=timeout, env=env)


def _check_boot_disk(device, runner):
    """Refuse to touch the whole disk the running system booted from."""
    try:
        mount = "/sysroot" if Path("/sysroot").is_mount() else "/"
        result = _capture(runner, ["findmnt", "--noheadings", "--output", "SOURCE", "--target", mount])
        if result.returncode:
            raise InstallerError("boot_disk_unknown", "could not determine the running system's boot source")
        source = (result.stdout or "").strip().split("[", 1)[0]
        if not source or not Path(source).is_block_device():
            raise InstallerError("boot_disk_unknown", f"{source or 'the boot source'} is not a block device")
        result = _capture(runner, ["lsblk", "--inverse", "--noheadings", "--raw", "--output", "MAJ:MIN", source])
        if result.returncode:
            raise InstallerError("boot_disk_unknown", "could not trace the boot disk ancestry")
        ancestors = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        ident = os.stat(device).st_rdev
        if not ancestors or f"{os.major(ident)}:{os.minor(ident)}" in ancestors:
            raise InstallerError("boot_disk_forbidden", str(device))
    except InstallerError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallerError("boot_disk_unknown", str(error)) from error


def _parse_show(text):
    fields = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def _state_of(fields):
    load, active = fields.get("LoadState", ""), fields.get("ActiveState", "")
    sub, result, code = fields.get("SubState", ""), fields.get("Result", ""), fields.get("ExecMainStatus", "")
    if load == "not-found":
        return "idle"
    if active in ("activating", "reloading", "deactivating"):
        return "running"
    if active == "active":
        if sub == "running":
            return "running"
        if sub == "exited":
            return "succeeded" if result == "success" and code == "0" else "failed"
        raise InstallerError("status_unavailable", f"unexpected active unit state: {sub!r}")
    if active == "failed":
        return "failed"
    if active == "inactive":
        return "failed" if (result not in ("", "success") or code not in ("", "0")) else "idle"
    raise InstallerError("status_unavailable", "could not read the installer unit state")


def _plan_identity(record):
    """The card identity of a persisted plan, or ``None`` when it is unusable.

    Only a record that names a real ``/dev`` node and a valid 32-character card
    CID yields an identity.  A missing or malformed record never produces a
    guessed one: the caller must not attribute an operation to an arbitrary
    card the user then "retries" by accident.
    """
    if not isinstance(record, dict):
        return None
    device = record.get("device")
    if not isinstance(device, str) or not device.startswith("/dev/"):
        return None
    try:
        cid = tf_state.safe_cid(record.get("card_cid"))
    except ValueError:
        return None
    return {"card_cid": cid, "device": device}


# ---------------------------------------------------------------------------
# TF library: read-only inspection and prepared plans
# ---------------------------------------------------------------------------

DEFAULT_TF_SYSFS = Path("/sys")
DEFAULT_TF_RUNTIME = Path("/run/armada-installer-tf")
DEFAULT_TF_CHECKPOINT = Path(tf_state.CHECKPOINT_DIR)
TF_UNIT = "armada-tf-library.service"
# One cross-instance guard file inside the (root-owned) runtime directory.
LOCK_NAME = "lifecycle.lock"
CRITICAL_MOUNTS = ("/var", "/boot", "/home")


def _check_critical_mounts(device, runner):
    """Refuse a card that backs /var, /boot or /home; fail closed if unknown."""
    for target in CRITICAL_MOUNTS:
        if not Path(target).is_mount():
            continue
        try:
            result = _capture(runner, ["findmnt", "--noheadings", "--output", "SOURCE", "--target", target])
            if result.returncode:
                raise InstallerError("boot_disk_unknown", "could not read a critical mount source")
            source = (result.stdout or "").strip().split("[", 1)[0]
            if not source or not Path(source).is_block_device():
                raise InstallerError("boot_disk_unknown", "a critical mount source is not a block device")
            result = _capture(runner, ["lsblk", "--inverse", "--noheadings", "--raw",
                                       "--output", "MAJ:MIN", source])
            if result.returncode:
                raise InstallerError("boot_disk_unknown", "could not trace a critical mount ancestry")
            ancestors = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            ident = os.stat(device).st_rdev
            if not ancestors or "%d:%d" % (os.major(ident), os.minor(ident)) in ancestors:
                raise InstallerError("boot_disk_forbidden", str(device))
        except InstallerError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("boot_disk_unknown", str(error)) from error


class _ReserveLock:
    """Serialise against the upstream Armada automount/format lock.

    The lock is only ever taken non-blocking: a card already held by the
    automount/format path fails fast with ``card_busy`` instead of hanging the
    caller, and the descriptor is closed on failure so a later re-entrant
    attempt by the worker cannot deadlock on a leaked descriptor.
    """

    def __init__(self, path):
        self._path = Path(path)
        self._handle = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise InstallerError("card_busy", "the card is busy; retry once it is idle") from error
        self._handle = handle
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class _LifecycleLock:
    """Cross-instance guard for one injected TF runtime directory.

    A ``TfLibraryService`` only owns a process-local ``RLock``; two instances
    (a second plugin load, a second panel) share the same unit/plan/status
    files and each could read the same terminal unit state, so one would stop
    the run the other had just relaunched.  This takes a non-blocking ``flock``
    on one shared, root-owned file under that runtime directory: it is held for
    the whole start decision and for the status aggregation, so unit, plan and
    status identity always describe one operation.  Contention fails fast with
    the caller's stable busy code (``unit_running``/``status_unavailable``)
    before any snapshot, cleanup or write is attempted.
    """

    def __init__(self, path, shared=False, busy_code="unit_running", error_code="launch_failed"):
        self._path = Path(path)
        self._shared = shared
        self._busy_code = busy_code
        self._error_code = error_code
        self._handle = None

    def __enter__(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = self._path.open("a+")
        except OSError as error:
            raise InstallerError(self._error_code, str(error)) from error
        try:
            mode = fcntl.LOCK_SH if self._shared else fcntl.LOCK_EX
            fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise InstallerError(self._busy_code,
                                 "another TF library operation is in progress") from error
        self._handle = handle
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class TfLibraryService:
    """Public facade for the Armada TF-card library conversion.

    ``inspect``/``prepare`` are thin, read-only wrappers over the one
    :class:`tf_inspection.TfInspector`.  ``start`` writes a fixed plan/status
    record atomically and hands the *fixed* ``armada-tf-library.service`` unit
    to ``systemd-run``; the unit runs this package's own ``tf_worker`` under the
    Python interpreter with no client-supplied command, so a panel close or a
    plugin reload cannot cancel the running conversion.  ``status`` merges the
    transient worker record with the real systemd unit state.
    """

    def __init__(self, runner=subprocess.run, sysfs_dir=DEFAULT_TF_SYSFS,
                 runtime_dir=DEFAULT_TF_RUNTIME, checkpoint_dir=DEFAULT_TF_CHECKPOINT):
        # Imported locally: ``tf_inspection`` imports ``InstallerError`` and the
        # boot/critical guards from this module, so a module-level import here
        # would be circular.
        from .tf_inspection import TfInspector
        self._runner = runner
        self._runtime = Path(runtime_dir)
        self._checkpoint_dir = Path(checkpoint_dir)
        self._inspector = TfInspector(runner=runner, sysfs_dir=sysfs_dir, runtime_dir=runtime_dir,
                                      checkpoint_dir=self._checkpoint_dir)
        self._lock = threading.RLock()
        self._token = None
        self._plan = None

    # -- paths --------------------------------------------------------------

    @property
    def _plan_path(self):
        return self._runtime / tf_state.PLAN_NAME

    @property
    def _status_path(self):
        return self._runtime / tf_state.STATUS_NAME

    @property
    def _log_path(self):
        return self._runtime / tf_state.LOG_NAME

    @property
    def _lock_path(self):
        return self._runtime / LOCK_NAME

    # -- public API ---------------------------------------------------------

    def inspect(self):
        with self._lock:
            return self._inspector.inspect_cards()

    def prepare(self, device):
        with self._lock:
            snapshot = self._inspector.snapshot(device)
            token = secrets.token_hex(16)
            self._token, self._plan = token, snapshot
            return {"token": token, "mode": snapshot["mode"], "device": snapshot["device"],
                    "reserve_device": snapshot["reserve_device"], "card_cid": snapshot["card_cid"],
                    "android_bytes": snapshot["android_bytes"], "linux_bytes": snapshot["linux_bytes"],
                    "recovery": bool(snapshot.get("recovery")),
                    "preserves": _preserves(snapshot["table"]),
                    "erase_scope": "reserve_only_format" if snapshot["mode"] == "format" else "none"}

    def start(self, token, confirmed=False):
        """Launch the fixed TF library worker once for one prepared plan.

        The plan is single use and confirmation is mandatory.  The card is
        re-inspected; the CID, GPT geometry, handoff and reserve filesystem
        UUID must still match the prepared snapshot, otherwise the launch is
        refused with ``target_changed``.  A running conversion is refused with
        ``unit_running``.  Before the launch the plan and an initial status
        record are written atomically (``0600``) to fixed runtime paths.  The
        whole decision runs under the shared lifecycle lock, so a concurrent
        start from another service instance over the same runtime directory is
        refused with ``unit_running`` before it reads, cleans up or writes.
        """
        if confirmed is not True:
            raise InstallerError("confirmation_required", "explicit confirmation is required")
        with self._lock:
            if not token or self._token is None or token != self._token or not isinstance(self._plan, dict):
                raise InstallerError("unknown_token", "prepare the library conversion again")
            plan = self._plan
            # The instance ``RLock`` above only covers this object.  A second
            # service instance shares the same unit/plan/status files, so the
            # whole read-decide-write-launch sequence runs under one shared
            # non-blocking flock: a concurrent start fails with ``unit_running``
            # before it can re-read a terminal unit state and stop the run this
            # one is launching.
            with _LifecycleLock(self._lock_path):
                # Read the existing unit state once; a running conversion is
                # refused untouched, and the same reading decides the terminal
                # cleanup below.
                state = self._tf_state()
                if state == "running":
                    raise InstallerError("unit_running", "a TF library conversion is already running")
                # Re-inspect the physical card before anything is written.  This
                # briefly takes the automount lock via the inspector's read-only
                # mount, and the worker's longer lock is only taken once it runs.
                seen = self._inspector.snapshot(plan["device"])
                self._ensure_unchanged(plan, seen)
                # A stale terminal unit must be cleared before the new run is
                # prepared: a failed transient unit is cleared with reset-failed
                # and a succeeded one with stop.  This runs before the token is
                # consumed and before the plan/status/log are written, so a
                # cleanup failure leaves the previous record and log intact and
                # never launches.
                self._clear_unit(state)
                # The token is single use: consume it before the launch.
                self._token, self._plan = None, None
                self._write_plan(plan, seen)
                self._write_status({"state": "running", "phase": "preparing", "detail": ""})
                try:
                    self._truncate_log()
                except InstallerError as error:
                    # No worker was launched, so the provisional running record
                    # is wrong and must not survive: record the known launch
                    # failure before re-raising.  The token stays consumed, and
                    # a retry therefore starts from a fresh prepare.
                    self._write_status({"state": "failed", "phase": "preparing",
                                        "detail": error.detail or "launch_failed"})
                    raise
                try:
                    result = _capture(self._runner, self._tf_argv())
                except (OSError, subprocess.SubprocessError) as error:
                    self._write_status({"state": "failed", "phase": "preparing", "detail": "launch_failed"})
                    raise InstallerError("launch_failed", str(error)) from error
                if result.returncode:
                    detail = (result.stderr or "").strip() or "systemd-run could not start the TF library worker"
                    self._write_status({"state": "failed", "phase": "preparing", "detail": detail})
                    raise InstallerError("launch_failed", detail)
                # The launch has not requested or registered a library yet, so
                # the running status it returns carries those flags as false.
                started = {"state": "running", "phase": "preparing", "detail": "", "log_tail": "",
                           "library_requested": False, "library_registered": False,
                           "library_pending": False}
                # The freshly written plan names the card this run is bound to,
                # so a retry after reopening can find that same card again.
                identity = _plan_identity({"device": plan["device"], "card_cid": plan["card_cid"]})
                if identity is not None:
                    started.update(identity)
                return started

    def status(self):
        """Merge the transient worker record with the real systemd unit state.

        A failed unit always reports ``failed`` even when a stale status record
        still claims success; a running or successful unit likewise overrides
        an out-of-date record.  An idle unit with a saved ``running`` record
        means no worker exists (the launch died, or it exited before writing a
        terminal record): that run is reported as ``failed``/interrupted so it
        cannot look busy forever and block the user's retry.  Saved terminal
        records are kept, and an activating/running unit is never called
        failed.  ``phase`` is one of ``preparing``, ``formatting``,
        ``mounting`` or ``registering`` (or empty when idle).

        A non-idle operation also reports the ``card_cid``/``device`` of the
        persisted plan that launched it, when that record is valid, so a
        reopened tab can retry *that* card without guessing.  The shared
        lifecycle lock is taken in shared, non-blocking mode: a launch that is
        in progress reports ``status_unavailable`` instead of mixing a unit, a
        plan and a status record from two different operations.
        """
        with self._lock:
            with _LifecycleLock(self._lock_path, shared=True,
                                busy_code="status_unavailable", error_code="status_unavailable"):
                unit = _state_of(self._tf_show())
                record = tf_state.read_json(self._status_path) or {}
                state, phase = record.get("state"), record.get("phase") or ""
                detail = str(record.get("detail") or "")
                if unit in ("failed", "running", "succeeded"):
                    state = unit
                    if unit == "running" and phase not in tf_state.PHASES:
                        phase = "preparing"
                elif state == "running":
                    # The unit is idle while the record still claims a run: the
                    # launch never produced a worker, or the worker vanished
                    # before writing a terminal record.  Report that run as an
                    # interrupted failure instead of leaving it running forever,
                    # so the card can be prepared and confirmed again.  Only a
                    # saved ``running`` record is reinterpreted; a saved
                    # terminal record is retained, and ``_state_of`` maps an
                    # activating/running unit to ``running`` above, so a live
                    # worker is never labelled failed.
                    state = "failed"
                    if phase not in tf_state.PHASES:
                        phase = "preparing"
                    if not detail:
                        detail = "interrupted"
                if state not in tf_state.STATES:
                    state = "idle"
                    phase = ""
                payload = {"state": state, "phase": phase, "detail": detail,
                           "log_tail": self._log_tail(),
                           "library_requested": bool(record.get("library_requested")),
                           "library_registered": bool(record.get("library_registered")),
                           "library_pending": bool(record.get("library_pending"))}
                if state != "idle":
                    identity = _plan_identity(tf_state.read_json(self._plan_path))
                    if identity is not None:
                        payload.update(identity)
                return payload

    # -- helpers ------------------------------------------------------------

    def _ensure_unchanged(self, plan, seen):
        from . import tf_inspection
        if tf_inspection.fingerprint(seen) != tf_inspection.fingerprint(plan):
            raise InstallerError("target_changed", "the card changed; prepare the conversion again")

    def _clear_unit(self, state):
        """Clear this plugin's own terminal TF unit before a fresh launch.

        A failed transient unit needs ``reset-failed``; a succeeded one needs
        ``stop``.  Any failure is surfaced as ``launch_failed`` so the caller
        aborts before the plan/status/log are written.
        """
        if state not in ("failed", "succeeded"):
            return
        argv = (["systemctl", "reset-failed", TF_UNIT] if state == "failed"
                else ["systemctl", "stop", TF_UNIT])
        try:
            result = _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("launch_failed", str(error)) from error
        if result.returncode:
            action = "clear the failed" if state == "failed" else "stop the previous"
            detail = (result.stderr or "").strip() or "systemctl could not %s TF worker unit" % action
            raise InstallerError("launch_failed", detail)

    def _write_plan(self, plan, seen):
        from . import tf_inspection
        table = seen.get("table") if isinstance(seen.get("table"), dict) else {}
        parts = table.get("partitions") if isinstance(table.get("partitions"), list) else []
        record = {"device": plan["device"], "reserve_device": plan["reserve_device"],
                  "card_cid": plan["card_cid"], "size_bytes": plan.get("size_bytes"),
                  "mode": plan["mode"], "recovery": bool(plan.get("recovery")),
                  "reserve_type": str((parts[0] if parts else {}).get("type", "")).lower(),
                  "handoff": plan.get("handoff"), "table": table,
                  "filesystem_uuid": plan.get("filesystem_uuid"),
                  "android_bytes": plan.get("android_bytes"), "linux_bytes": plan.get("linux_bytes"),
                  # A readable, comparable fingerprint so a retry can prove the
                  # plan still describes the same card.
                  "fingerprint": list(tf_inspection.fingerprint(seen))}
        try:
            tf_state.atomic_write_json(self._plan_path, record, mode=0o600)
        except OSError as error:
            raise InstallerError("launch_failed", "could not write the TF plan: %s" % error) from error

    def _write_status(self, record):
        try:
            tf_state.atomic_write_json(self._status_path, record, mode=0o600)
        except OSError:
            # A status write must never take down a launch that already started.
            pass

    def _truncate_log(self):
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("wb"):
                pass
        except OSError as error:
            raise InstallerError("launch_failed", "could not reset the TF worker log: %s" % error) from error

    def _tf_argv(self):
        worker = Path(__file__).resolve().with_name("tf_worker.py")
        return ["systemd-run", "--unit", TF_UNIT,
                "--property", "Type=exec",
                "--property", "RemainAfterExit=yes",
                "--property", "StandardOutput=append:%s" % self._log_path,
                "--property", "StandardError=append:%s" % self._log_path,
                "--", "/usr/bin/python3", str(worker), str(self._plan_path), str(self._status_path),
                str(self._log_path)]

    def _tf_show(self):
        return _parse_show(self._show_unit(TF_UNIT))

    def _show_unit(self, unit):
        argv = ["systemctl", "show", unit,
                "--property=LoadState,ActiveState,SubState,ExecMainStatus,Result", "--no-pager"]
        try:
            result = _capture(self._runner, argv)
        except (OSError, subprocess.SubprocessError) as error:
            raise InstallerError("status_unavailable", str(error)) from error
        if result.returncode:
            raise InstallerError("status_unavailable", "systemctl could not report the TF worker unit")
        return result.stdout

    def _tf_state(self):
        return _state_of(self._tf_show())

    def _log_tail(self):
        return _tail(self._log_path)


def _tail(log_path):
    try:
        with Path(log_path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - LOG_BYTES))
            data = stream.read()
    except OSError:
        return ""
    text = tf_state.sanitize_log_text(data.decode("utf-8", errors="replace"))
    lines = text.splitlines()[-LOG_LINES:]
    text = "\n".join(lines)
    encoded = text.encode("utf-8")
    return encoded[-LOG_BYTES:].decode("utf-8", errors="ignore") if len(encoded) > LOG_BYTES else text


def _preserves(table):
    parts = table.get("partitions", []) if isinstance(table, dict) else []

    def entry(index):
        if index < len(parts):
            part = parts[index]
            return {"name": part.get("name"), "type": str(part.get("type", "")).lower(),
                    "uuid": str(part.get("uuid") or "").replace("-", "").lower()}
        return {}

    return {"android_p2": entry(1), "android_p3": entry(2)}
