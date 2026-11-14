"""Root worker that converts one TF card's reserve partition into a Steam library.

The plugin never touches a card itself.  ``TfLibraryService.start`` writes a
fixed plan record and hands ``armada-tf-library.service`` to ``systemd-run``;
that unit runs *this* file with the running interpreter and fixed argv, so the
conversion survives a Decky panel close or a plugin reload.  The worker:

* re-proves the physical card (SD/boot/critical guards, CID, GPT, filesystem
  UUID) immediately before every write, while holding the upstream
  ``/run/jupiter-automount-<reserve>.lock`` exactly once;
* unmounts only the reserve partition and refuses to format a busy one;
* retypes only ``p1`` to the Linux-filesystem GPT type (``sgdisk --typecode=1:8300``)
  and re-reads the GPT to prove the Android ``p2``/``p3`` are untouched;
* formats only the reserve partition (``mkfs.ext4 -F -E nodiscard -U <intended>
  -L ARMADA_TF_<cid6>``), where the intended UUID is generated and persisted in
  the validated checkpoint *before* anything is retyped or formatted;
* persists a durable checkpoint before formatting and after formatting so an
  interrupted run can resume without ever reformatting a verified ext4 reserve;
* mounts through the upstream automount helper, verifies the mount is the exact
  reserve ext4 mounted read-write, writes the Linux marker and creates the
  ``SteamLibrary/steamapps`` tree owned by UID 1000;
* asks a running Steam to add the library folder, and reports registration
  separately from the request: membership in ``libraryfolders.vdf`` is read
  first, so an already listed library is reported registered with no request at
  all, and a CLI ``0`` is never treated as registration.

The module imports the package absolutely, adding the ``py_modules`` ancestor of
its own file (three directories up: ``tf_library`` -> ``armada_control`` ->
``py_modules``) to ``sys.path`` only when it is run as a direct script from an
arbitrary working directory, so it never depends on a relative import or on an
installed ``armada_installer`` distribution.
"""

import errno
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

if __package__ in (None, ""):  # run as a direct script by the fixed systemd unit
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from armada_control.tf_library import service, tf_card, tf_inspection, tf_state  # noqa: E402

AUTOMOUNT_HELPER = "/usr/lib/hwsupport/steamos-automount.sh"
STEAM_BIN = "/usr/bin/steam"
STEAM_UID = 1000
MKFS_TIMEOUT = 600
HELPER_TIMEOUT = 120
STEAM_TIMEOUT = 120
MOUNT_POLLS = 40
MOUNT_POLL_DELAY = 0.5
LIBRARY_NAME = "SteamLibrary"
STEAMAPPS_NAME = "steamapps"
MARKER_NAME = tf_inspection.LINUX_MARKER_NAME

_VDF_PATH = re.compile(r'"path"\s*"([^"]*)"')
_UUID_DASH = re.compile(r"-")
_UUID32 = re.compile(r"[0-9a-f]{32}\Z")


class WorkerError(service.InstallerError):
    """A worker failure with a stable machine-readable code."""


def _norm(value):
    return str(value or "").replace("-", "").lower()


def _same_source(left, right):
    if not left or not right:
        return False
    left, right = str(left).split("[", 1)[0], str(right).split("[", 1)[0]
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return left == right


def _is_symlink_at(parent_fd, name):
    """True when ``name`` inside ``parent_fd`` is a symlink; never follows it.

    Linux may report either ``ELOOP`` or ``ENOTDIR`` when ``openat`` combines
    ``O_DIRECTORY`` with ``O_NOFOLLOW`` and the entry is a symlink to a
    directory, so the failed open is classified with a descriptor-relative
    no-follow stat instead of the errno alone.
    """
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (OSError, NotImplementedError):
        return False
    return stat.S_ISLNK(info.st_mode)


class TfWorker:
    """Run one prepared TF-card conversion to completion.

    All external access goes through the injected ``runner`` so the tests can
    pin the exact argv list and never touch a real device, systemd or Steam.
    """

    def __init__(self, plan, status_path=None, log_path=None, runner=subprocess.run,
                 sysfs_dir=Path("/sys"), runtime_dir=service.DEFAULT_TF_RUNTIME,
                 checkpoint_dir=service.DEFAULT_TF_CHECKPOINT, helper=AUTOMOUNT_HELPER,
                 steam_bin=STEAM_BIN, steam_uid=STEAM_UID):
        self._plan = plan if isinstance(plan, dict) else {}
        self._status_path = status_path
        self._log_path = log_path
        self._runner = runner
        self._sysfs = Path(sysfs_dir)
        self._runtime = Path(runtime_dir)
        self._checkpoint = Path(checkpoint_dir)
        self._helper = helper
        self._steam_bin = steam_bin
        self._steam_uid = steam_uid
        self._status = {"state": "running", "phase": "preparing", "detail": ""}

    # -- entry point --------------------------------------------------------

    def run(self):
        try:
            self._execute()
            return 0
        except service.InstallerError as error:
            self._fail(error.code, error.detail)
            return 1
        except Exception as error:  # never leak a traceback through the unit
            self._fail("internal_error", str(error))
            return 1

    def _execute(self):
        plan = self._plan
        for key in ("device", "reserve_device", "card_cid", "mode", "handoff", "table"):
            if not plan.get(key):
                raise WorkerError("invalid_plan", "the plan record is incomplete")
        cid = str(plan["card_cid"]).lower()
        tf_card.validate_handoff(plan["handoff"])  # the descriptor must stay valid
        self._set_phase("preparing", "Validating the card %s" % plan["device"])
        inspector = tf_inspection.TfInspector(runner=self._runner, sysfs_dir=self._sysfs,
                                              runtime_dir=self._runtime,
                                              checkpoint_dir=self._checkpoint)
        lock = service._ReserveLock(self._runtime.parent /
                                    ("jupiter-automount-%s.lock" % os.path.basename(plan["reserve_device"])))
        with lock:
            # Re-prove the card identity under the same lock the automount path
            # uses, before anything at all is unmounted or written.
            got = inspector.recheck(plan["device"], plan)
            if plan["mode"] == "format":
                record = self._already_formatted(cid, plan, got)
                if record is None:
                    # The reserve must still carry the prepared filesystem
                    # before the first write, unless a durable checkpoint proves
                    # our own earlier mkfs produced a different UUID.
                    self._verify_snapshot_uuid(plan, got)
                    got = self._format(plan, got, inspector)
                else:
                    got = self._verify_formatted(plan, got)
                    self._promote_checkpoint(cid, record, plan, got)
            else:
                self._verify_snapshot_uuid(plan, got)
                got = self._verify_formatted(plan, got)
            mountpoint = self._mount(got)
            self._write_marker(mountpoint, plan, got)
            library = self._ensure_library(mountpoint)
        # Registration talks to a user session and never touches the card, so it
        # runs after the automount lock is released.
        self._register(library)
        # Keep the registration flags the worker set while marking success.
        self._update(state="succeeded", phase="registering")

    # -- format -------------------------------------------------------------

    def _already_formatted(self, cid, plan, got):
        """The durable checkpoint that proves the live ext4 reserve is ours.

        This is the resume path: an interrupted run that already formatted the
        reserve must never call ``sgdisk``/``mkfs`` again.  Two records qualify
        for the same CID, capacity and handoff:

        * ``formatted`` with its recorded ``linux_fs_uuid`` equal to the live
          UUID (the legacy resume path), or
        * ``validated`` with a valid non-empty ``intended_fs_uuid`` equal to the
          live UUID -- the UUID persisted *before* ``mkfs.ext4 -U``, which
          covers an interrupt between mkfs and the formatted checkpoint.

        Anything else (no checkpoint, another filesystem, a missing or
        mismatched intended UUID) returns ``None`` so the caller keeps the
        original unchanged-target protection.
        """
        record = tf_state.read_checkpoint(self._checkpoint, cid)
        if not isinstance(record, dict) or record.get("size_bytes") != plan.get("size_bytes"):
            return None
        if got.get("filesystem_type") != "ext4":
            return None
        stage = record.get("stage")
        live = _norm(got.get("filesystem_uuid"))
        if not live:
            return None
        if stage == "formatted":
            if _norm(record.get("linux_fs_uuid")) != live:
                return None
        elif stage == "validated":
            intended = _norm(record.get("intended_fs_uuid"))
            if not _UUID32.match(intended) or intended != live:
                return None
        else:
            return None
        try:
            if record.get("handoff") != plan.get("handoff"):
                return None
        except Exception:  # pragma: no cover - defensive
            return None
        return record

    def _promote_checkpoint(self, cid, record, plan, got):
        """Promote a validated resume record once the ext4 filesystem is proven.

        Only ever runs after ``_already_formatted`` matched the live ext4 UUID;
        a formatted record is left exactly as it is.
        """
        if record.get("stage") != "validated":
            return
        tf_state.write_checkpoint(self._checkpoint, cid,
                                  self._checkpoint_payload(plan, got, "formatted",
                                                           record.get("intended_fs_uuid")))

    def _verify_formatted(self, plan, got):
        """Confirm an existing reserve is usable ext4 without reformatting it."""
        if got.get("filesystem_type") != "ext4" or not got.get("filesystem_uuid"):
            raise WorkerError("format_required",
                              "the reserve partition is not a known ext4 filesystem")
        return got

    def _verify_snapshot_uuid(self, plan, got):
        """Prove the live reserve filesystem still matches the prepared snapshot.

        ``recheck`` returns the live reserve filesystem UUID; comparing it with
        the prepared snapshot UUID refuses a reserve that was reformatted or
        swapped since the plan was built, before anything is written.  The one
        legitimate divergence -- a resume whose new UUID was produced by our own
        ``mkfs`` -- is proven by a formatted or validated checkpoint before this
        check is reached, so this is never a false old-versus-new comparison.
        """
        expected = _norm(plan.get("filesystem_uuid"))
        live = _norm(got.get("filesystem_uuid"))
        if expected != live:
            raise WorkerError("target_changed",
                              "the reserve filesystem changed since it was prepared")

    def _format(self, plan, got, inspector):
        cid = str(plan["card_cid"]).lower()
        self._set_phase("formatting", "Preparing to format only the reserve partition")
        # The UUID the new ext4 filesystem is going to get.  It is generated and
        # persisted in the validated checkpoint *before* anything is retyped or
        # formatted, so an interrupt between a successful mkfs and the formatted
        # checkpoint is recognisable (and resumable) without reformatting.
        intended = str(uuid.uuid4())
        # Durable checkpoint BEFORE any format: the validated descriptor plus the
        # exact Android p2/p3 geometry, so a partial failure can recover without
        # ever re-reading (or touching) the Android side.
        tf_state.write_checkpoint(self._checkpoint, cid,
                                  self._checkpoint_payload(plan, got, "validated", intended))
        self._unmount_reserve(got["reserve_device"])
        # Re-prove SD/boot/GPT/CID *and* the live reserve filesystem UUID
        # immediately before the first write.
        got = inspector.recheck(plan["device"], plan)
        self._verify_snapshot_uuid(plan, got)
        self._log("Retyping the reserve partition (p1) to a Linux filesystem")
        self._checked(["sgdisk", "--typecode=1:8300", plan["device"]])
        self._checked(["partprobe", plan["device"]], optional=True)
        # Re-read the actual GPT and prove the Android p2/p3 are byte-identical,
        # and that the reserve still carries the prepared filesystem, before mkfs.
        got = inspector.recheck(plan["device"], plan)
        self._verify_snapshot_uuid(plan, got)
        self._checked(["udevadm", "settle"])
        label = "ARMADA_TF_%s" % cid[-6:]
        self._log("Formatting only %s" % got["reserve_device"])
        self._checked(["mkfs.ext4", "-F", "-E", "nodiscard", "-U", intended, "-L", label,
                       got["reserve_device"]], timeout=MKFS_TIMEOUT)
        self._checked(["udevadm", "settle"])
        uuid_value, fstype = self._blkid(got["reserve_device"])
        if fstype != "ext4" or _norm(uuid_value) != _norm(intended):
            raise WorkerError("format_failed",
                              "the reserve partition is not the new ext4 filesystem it should be")
        got = dict(got, filesystem_uuid=uuid_value, filesystem_type="ext4",
                   reserve_type=tf_card.RESERVE_CONNECTED)
        # Durable checkpoint BEFORE mounting so a crash while mounting still
        # resumes directly to mount without a second mkfs.  The intended UUID is
        # retained so both resume paths keep working.
        tf_state.write_checkpoint(self._checkpoint, cid,
                                  self._checkpoint_payload(plan, got, "formatted", intended))
        return got

    def _checkpoint_payload(self, plan, got, stage, intended=None):
        table = plan.get("table") if isinstance(plan.get("table"), dict) else {}
        parts = table.get("partitions") if isinstance(table.get("partitions"), list) else []
        android = parts[1:3] if len(parts) >= 3 else []
        payload = {"stage": stage, "card_cid": str(plan["card_cid"]).lower(),
                   "size_bytes": plan.get("size_bytes"), "handoff": plan.get("handoff"),
                   "reserve_device": got.get("reserve_device"),
                   "reserve_type": got.get("reserve_type"), "android_parts": android,
                   "linux_fs_uuid": got.get("filesystem_uuid")}
        if intended is not None:
            payload["intended_fs_uuid"] = intended
        return payload

    # -- mount --------------------------------------------------------------

    def _mount(self, got):
        reserve = got["reserve_device"]
        self._set_phase("mounting", "Mounting %s" % reserve)
        if self._mounted_target(reserve) is None:
            self._checked([self._helper, "add", os.path.basename(reserve)], timeout=HELPER_TIMEOUT,
                          optional=True)
        target = self._await_mount(reserve)
        if target is None:
            raise WorkerError("mount_failed", "the reserve partition did not mount")
        info = self._mount_info(reserve)
        if info is None or not _same_source(info.get("source"), reserve):
            raise WorkerError("mount_failed", "the mounted source is not the reserve partition")
        if str(info.get("fstype", "")).lower() != "ext4":
            raise WorkerError("mount_failed", "the reserve partition is not mounted as ext4")
        if "rw" not in str(info.get("options", "")).split(","):
            raise WorkerError("mount_failed", "the reserve partition is not mounted read-write")
        return Path(target)

    def _unmount_reserve(self, reserve):
        target = self._mounted_target(reserve)
        if target is None:
            return
        result = self._run(["umount", str(target)], check=False)
        if result is None or result.returncode:
            raise WorkerError("card_busy", "the reserve partition is busy; nothing was written")

    def _await_mount(self, reserve):
        for attempt in range(MOUNT_POLLS):
            target = self._mounted_target(reserve)
            if target is not None:
                return target
            if attempt + 1 < MOUNT_POLLS:
                time.sleep(MOUNT_POLL_DELAY)
        return None

    def _mount_entries(self):
        result = self._run(["findmnt", "--json", "--list", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"],
                           check=False)
        if result is None or result.returncode:
            return []
        try:
            data = json.loads(result.stdout or "{}")
        except ValueError:
            return []
        entries = data.get("filesystems") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _mounted_target(self, reserve):
        for entry in self._mount_entries():
            if isinstance(entry, dict) and _same_source(entry.get("source"), reserve):
                return entry.get("target")
        return None

    def _mount_info(self, reserve):
        for entry in self._mount_entries():
            if isinstance(entry, dict) and _same_source(entry.get("source"), reserve):
                return entry
        return None

    # -- marker and library -------------------------------------------------

    def _write_marker(self, mountroot, plan, got):
        root_real = Path(mountroot).resolve()
        marker = Path(mountroot) / MARKER_NAME
        if marker.is_symlink():
            raise WorkerError("library_unsafe", "the Linux marker is a symlink")
        if marker.exists():
            return
        resolved = marker.resolve()
        if root_real != resolved and root_real not in resolved.parents:
            raise WorkerError("library_unsafe", "the Linux marker escapes the mount")
        payload = dict(plan["handoff"])
        payload["linux_fs_uuid"] = got.get("filesystem_uuid")
        try:
            tf_state.atomic_write_json(marker, payload, mode=0o644)
        except OSError as error:
            raise WorkerError("marker_failed", "could not write the Linux marker: %s" % error) from error

    def _ensure_library(self, mountroot):
        """Create and own ``SteamLibrary/steamapps`` through pinned descriptors.

        The mount is opened ``O_DIRECTORY|O_NOFOLLOW`` and both managed
        directories are created and pinned relative to it (``dir_fd``) *before*
        any ownership transfer, then owned by ``fchown`` on those descriptors,
        leaf first.  A path that is swapped for a symlink between the creation
        and the ``chown`` therefore cannot redirect the ownership change outside
        the card, and a retry re-owns root-owned leftovers instead of skipping
        them.  Nothing else is ever walked, so game files keep their owner and
        data.
        """
        try:
            account = pwd.getpwuid(self._steam_uid)
        except KeyError as error:
            raise WorkerError("library_failed", "no UID %d account on this device" % self._steam_uid) from error
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = library_fd = steamapps_fd = None
        try:
            try:
                root_fd = os.open(os.fspath(mountroot), flags)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise WorkerError("library_unsafe", "the mount path is a symlink") from error
                raise WorkerError("library_failed",
                                  "could not open %s: %s" % (mountroot, error)) from error
            library_fd = self._pinned_directory(root_fd, LIBRARY_NAME, flags)
            steamapps_fd = self._pinned_directory(library_fd, STEAMAPPS_NAME, flags)
            for descriptor, name in ((steamapps_fd, STEAMAPPS_NAME), (library_fd, LIBRARY_NAME)):
                try:
                    os.fchown(descriptor, account.pw_uid, account.pw_gid)
                except OSError as error:
                    raise WorkerError("library_failed", "could not own %s: %s" % (name, error)) from error
        finally:
            for descriptor in (steamapps_fd, library_fd, root_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:  # pragma: no cover - defensive
                        pass
        return Path(mountroot) / LIBRARY_NAME

    @staticmethod
    def _pinned_directory(parent_fd, name, flags):
        """Create ``name`` inside ``parent_fd`` and return it pinned as a directory."""
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise WorkerError("library_failed", "could not create %s: %s" % (name, error)) from error
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            # A symlinked directory surfaces as ELOOP or ENOTDIR depending on
            # the kernel/filesystem; only a proven symlink is unsafe, so an
            # ordinary non-directory file still fails as library_failed.
            if error.errno in (errno.ELOOP, errno.ENOTDIR) and _is_symlink_at(parent_fd, name):
                raise WorkerError("library_unsafe", "the %s path is a symlink" % name) from error
            raise WorkerError("library_failed", "could not open %s: %s" % (name, error)) from error

    # -- steam registration -------------------------------------------------

    def _register(self, library):
        self._set_phase("registering", "Registering the Steam library")
        library_path = str(library)
        # Membership in the account's own libraryfolders.vdf is the only proof
        # of registration, so it is checked first and independently of Steam:
        # a library that is already listed needs no request at all, and an
        # exited Steam cannot make an already registered library pending.
        if self._library_member(library_path):
            self._update(library_requested=False, library_registered=True, library_pending=False,
                         detail="")
            return
        if not self._steam_running():
            self._update(library_requested=False, library_registered=False, library_pending=True,
                         detail="Steam is not running; the library is mounted and pending registration")
            return
        url = "steam://addlibraryfolder/" + urllib.parse.quote(library_path, safe="")
        argv = ["systemd-run", "--uid=%d" % self._steam_uid, "--collect", "--wait",
                "--setenv=XDG_RUNTIME_DIR=/run/user/%d" % self._steam_uid,
                self._steam_bin, "-ifrunning", url]
        result = self._run(argv, check=False, timeout=STEAM_TIMEOUT)
        requested = result is not None and result.returncode == 0
        # A CLI success only means Steam was asked; membership is re-read after
        # the attempt and is the sole determinant of ``library_registered``.
        registered = self._library_member(library_path)
        self._update(library_requested=requested, library_registered=registered,
                     library_pending=not registered,
                     detail="" if registered else "the library request could not be confirmed")

    def _steam_running(self):
        try:
            result = self._runner(["pgrep", "-x", "steam"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _library_member(self, library_path):
        account = pwd.getpwuid(self._steam_uid)
        home = Path(account.pw_dir)
        candidates = (home / ".steam" / "steam" / "config" / "libraryfolders.vdf",
                      home / ".steam" / "steam" / "steamapps" / "libraryfolders.vdf",
                      home / ".local" / "share" / "Steam" / "config" / "libraryfolders.vdf",
                      home / ".local" / "share" / "Steam" / "steamapps" / "libraryfolders.vdf")
        wanted = os.path.normpath(library_path)
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for found in _VDF_PATH.findall(text):
                if os.path.normpath(found) == wanted:
                    return True
        return False

    # -- plumbing -----------------------------------------------------------

    def _checked(self, argv, timeout=30, optional=False):
        result = self._run(argv, check=False, timeout=timeout)
        if result is None:  # command missing
            if optional:
                return None
            raise WorkerError("worker_failed", "%s is not available" % argv[0])
        if result.returncode:
            if optional:
                return result
            detail = (result.stderr or "").strip() or ("%s failed" % argv[0])
            raise WorkerError("worker_failed", detail)
        return result

    def _run(self, argv, check=False, timeout=30):
        try:
            return self._runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            if check:
                raise WorkerError("worker_failed", str(error)) from error
            return None

    def _blkid(self, device):
        result = self._run(["blkid", "-o", "export", device], check=False)
        if result is None or result.returncode:
            return None, None
        values = {}
        for line in (result.stdout or "").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
        return values.get("UUID"), (values.get("TYPE") or "").lower()

    def _log(self, message):
        if not self._log_path:
            return
        text = tf_state.sanitize_log_text(str(message)) + "\n"
        try:
            os.makedirs(os.path.dirname(os.fspath(self._log_path)) or ".", mode=0o700, exist_ok=True)
            with open(os.fspath(self._log_path), "a", encoding="utf-8") as stream:
                stream.write(text)
        except OSError:
            pass

    def _set_phase(self, phase, detail=""):
        self._log("%s: %s" % (phase, detail) if detail else phase)
        self._write_status({"state": "running", "phase": phase, "detail": detail})

    def _update(self, **fields):
        self._status.update(fields)
        self._write_status(self._status)

    def _write_status(self, record):
        self._status = dict(record)
        if not self._status_path:
            return
        try:
            tf_state.atomic_write_json(self._status_path, self._status, mode=0o600)
        except OSError:
            pass

    def _fail(self, code, detail):
        self._log("failed: %s (%s)" % (code, detail))
        record = {"state": "failed", "phase": self._status.get("phase") or "preparing",
                  "detail": "%s: %s" % (code, detail) if detail else code}
        for key in ("library_requested", "library_registered", "library_pending"):
            if key in self._status:
                record[key] = self._status[key]
        self._write_status(record)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        sys.stderr.write("usage: tf_worker.py <plan.json> <status.json> [log]\n")
        return 2
    plan = tf_state.read_json(argv[0])
    status_path, log_path = argv[1], (argv[2] if len(argv) > 2 else None)
    if not isinstance(plan, dict):
        tf_state.atomic_write_json(status_path, {"state": "failed", "phase": "preparing",
                                                 "detail": "the plan record is missing or invalid"})
        return 1
    return TfWorker(plan, status_path=status_path, log_path=log_path).run()


if __name__ == "__main__":
    sys.exit(main())
