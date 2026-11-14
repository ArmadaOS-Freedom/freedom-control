"""Behaviour tests for the root ``armada_control.tf_library.tf_worker``.

Everything the worker would do to a real card is injected through the runner, so
the exact argv list is pinned and no device, systemd unit, udev or Steam process
is ever touched.  The tests cover the destructive-path contract: only ``p1`` is
retyped, only the reserve partition is formatted (never the whole card or the
Android side), no zap/wipe command is ever issued, a failure before the write
halts, a formatted checkpoint resumes without a second ``mkfs``, connected cards
are never formatted, and an absent Steam leaves the card mounted and pending.
"""

import errno
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control.tf_library import service, tf_card, tf_inspection, tf_state, tf_worker  # noqa: E402

MIB = 1024 * 1024
DISK, RESERVE, META, PRIVATE = "/dev/mmcblk0", "/dev/mmcblk0p1", "/dev/mmcblk0p2", "/dev/mmcblk0p3"
CID = "1b2c3d4e5f60718293a4b5c6d7e8f901"
CID_OTHER = "0011223344556677889900aabbccddee"
DISK_BYTES = 512 * MIB
SECTOR = 512
FIRST_LBA, LAST_LBA = 2048, DISK_BYTES // SECTOR - 34
DISK_GUID = "01234567-89ab-cdef-0123-456789abcdef"
RESERVE_PARTUUID = "11111111-2222-3333-4444-555555555555"
META_PARTUUID = "22222222-3333-4444-5555-666666666666"
PRIVATE_PARTUUID = "99999999-8888-7777-6666-555555555555"
RESERVE_FS_UUID = "1A2B-3C4D"
PRIVATE_FS_UUID = "abcdef01-2345-6789-abcd-ef0123456789"
LINUX_FS_UUID = "00112233-4455-6677-8899-aabbccddeeff"
NEW_FS_UUID = "ffeeddcc-bbaa-9988-7766-554433221100"
DISK_MAJMIN = (179, 0)
_REAL_STAT = os.stat


def native_parts(reserve_type):
    reserve = 256 * MIB // SECTOR
    meta_start = FIRST_LBA + reserve
    private_start = meta_start + 16 * MIB // SECTOR
    private = LAST_LBA - private_start + 1
    return [
        {"node": RESERVE, "start": FIRST_LBA, "size": reserve, "type": reserve_type,
         "uuid": RESERVE_PARTUUID, "name": "linux-reserve"},
        {"node": META, "start": meta_start, "size": 16 * MIB // SECTOR, "type": tf_card.ANDROID_META,
         "uuid": META_PARTUUID, "name": "android-meta"},
        {"node": PRIVATE, "start": private_start, "size": private, "type": tf_card.ANDROID_PRIVATE,
         "uuid": PRIVATE_PARTUUID, "name": "android-private"},
    ]


def table(reserve_type=tf_card.RESERVE_PENDING):
    return {"label": "gpt", "sectorsize": SECTOR, "firstlba": FIRST_LBA, "lastlba": LAST_LBA,
            "id": DISK_GUID, "device": DISK, "partitions": native_parts(reserve_type)}


def handoff(**overrides):
    base = {"magic": "armada-tf-handoff", "schema_version": 1, "card_cid": CID,
            "disk_bytes": DISK_BYTES, "linux_ratio": 50, "public_fs_uuid": RESERVE_FS_UUID,
            "public_partition_guid": RESERVE_PARTUUID, "private_partition_guid": PRIVATE_PARTUUID,
            "private_fs_uuid": PRIVATE_FS_UUID}
    base.update(overrides)
    return base


class WorkerRunner:
    """A card/Steam model that changes state only in response to worker actions."""

    def __init__(self):
        self.calls = []
        self.table = table()
        self.children = self._children(tf_card.RESERVE_PENDING)
        self.disk_size = DISK_BYTES
        self.blkid_values = {"UUID": RESERVE_FS_UUID, "TYPE": "vfat"}
        self.mounted = False
        self.mount_target = None
        self.mount_options = "rw,relatime"
        self.steam_running = False
        self.ancestors = ["259:0"]
        self.ancestors_by_source = {}
        self.inverse_rc = 0
        self.sgdisk_rc = 0
        self.mkfs_rc = 0
        self.steam_rc = 0
        self.vdf_path = None
        self.default_mount = None

    @staticmethod
    def _children(reserve_type):
        return [{"name": part["node"].rsplit("/", 1)[-1], "path": part["node"], "type": "part",
                 "size": part["size"] * SECTOR, "partuuid": part["uuid"], "pkname": "mmcblk0"}
                for part in native_parts(reserve_type)]

    def _done(self, argv, code=0, out="", err=""):
        return subprocess.CompletedProcess(argv, code, out, err)

    def __call__(self, argv, **kwargs):
        if not isinstance(argv, list) or not argv:
            raise AssertionError("blocking calls must pass an argv list: {!r}".format(argv))
        if kwargs.get("shell"):
            raise AssertionError("shell execution is never allowed")
        self.calls.append(list(argv))
        command = argv[0]
        if command == "pgrep":
            return self._done(argv, 0 if self.steam_running else 1, "")
        if command == "lsblk":
            if "--inverse" in argv:
                if self.inverse_rc:
                    return self._done(argv, 1, "", "lsblk failed")
                return self._done(argv, 0, "\n".join(
                    self.ancestors_by_source.get(argv[-1], self.ancestors)) + "\n")
            if DISK in argv:
                top = {"name": DISK, "path": DISK, "type": "disk", "size": self.disk_size,
                       "partuuid": None, "pkname": None, "children": self.children}
                return self._done(argv, 0, json.dumps({"blockdevices": [top]}))
            return self._done(argv, 0, json.dumps({"blockdevices": [
                {"name": DISK, "path": DISK, "type": "disk", "size": self.disk_size,
                 "partuuid": None, "pkname": None}]}))
        if command == "sfdisk":
            return self._done(argv, 0, json.dumps({"partitiontable": self.table}))
        if command == "blkid":
            return self._done(argv, 0, "".join("%s=%s\n" % item for item in self.blkid_values.items()))
        if command == "findmnt":
            if "--json" in argv:
                entries = ([{"source": RESERVE, "target": str(self.mount_target), "fstype": "ext4",
                             "options": self.mount_options}] if self.mounted else [])
                return self._done(argv, 0, json.dumps({"filesystems": entries}))
            return self._done(argv, 0, "/dev/nvme0n1p2\n")
        if command == "mount":
            # Only the inspector's own read-only, argv-driven inspection mount of
            # the reserve is modelled; its options are reflected back by findmnt.
            options = argv[argv.index("-o") + 1] if "-o" in argv else ""
            if argv[-2] != RESERVE or "ro" not in options.split(","):
                raise AssertionError("unexpected mount: {}".format(argv))
            self.mounted, self.mount_target = True, argv[-1]
            self.mount_options = options
            return self._done(argv, 0)
        if command == "umount":
            if not self.mounted or str(self.mount_target) != argv[-1]:
                return self._done(argv, 32, "", "not mounted")
            self.mounted, self.mount_target = False, None
            self.mount_options = "rw,relatime"
            return self._done(argv, 0)
        if command == tf_worker.AUTOMOUNT_HELPER:
            # The upstream automount helper mounts read-write, unlike the
            # inspector's temporary read-only mount.
            self.mounted, self.mount_target = True, self.mount_target or self.default_mount
            self.mount_options = "rw,relatime"
            return self._done(argv, 0)
        if command == "sgdisk":
            if self.sgdisk_rc == 0 and "--typecode=1:8300" in argv:
                # A native retype really does change the live GPT, so the model
                # mutates its table too; the guards still have to hold.
                self.table["partitions"][0]["type"] = tf_card.RESERVE_CONNECTED
            return self._done(argv, self.sgdisk_rc, "", "sgdisk failed" if self.sgdisk_rc else "")
        if command == "partprobe":
            return self._done(argv, 0)
        if command == "udevadm":
            return self._done(argv, 0)
        if command == "mkfs.ext4":
            if self.mkfs_rc:
                return self._done(argv, self.mkfs_rc, "", "mkfs failed")
            # A real mkfs.ext4 honours ``-U``; model exactly that so a test can
            # prove the worker formatted with the UUID it had already persisted.
            wanted = argv[argv.index("-U") + 1] if "-U" in argv else NEW_FS_UUID
            self.blkid_values = {"UUID": wanted, "TYPE": "ext4"}
            return self._done(argv, 0)
        if command == "systemd-run":
            if self.steam_rc == 0 and self.vdf_path is not None:
                self._write_vdf()
            return self._done(argv, self.steam_rc, "", "steam request failed" if self.steam_rc else "")
        raise AssertionError("unexpected command: {}".format(command))

    def _write_vdf(self):
        path = self.vdf_path / "libraryfolders.vdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        library = self.default_mount / "SteamLibrary"
        path.write_text('"libraryfolders"\n{\n  "0"\n  {\n    "path" "%s"\n  }\n}\n' % library,
                        encoding="utf-8")

    def ran(self, *commands):
        return [call for call in self.calls if call[0] in commands]


class OrderedRunner(WorkerRunner):
    """A runner that notes the mkfs call inside a caller-supplied event list."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    def __call__(self, argv, **kwargs):
        if argv and argv[0] == "mkfs.ext4":
            self.events.append(("mkfs", list(argv)))
        return super().__call__(argv, **kwargs)


class OwnershipRecorder:
    """A meaningful ``os.fchown`` stand-in that records the *pinned* node.

    It changes no real owner.  Instead it records the descriptor's own link and
    ``(st_dev, st_ino)`` so a test can prove the ownership transfer addressed
    the directory that was pinned, not whatever a path resolves to by then.
    ``fail_next`` makes the next transfer fail like an unusable card.
    """

    def __init__(self):
        self.calls = []
        self.fail_next = False

    def __call__(self, descriptor, uid, gid):
        link = os.readlink("/proc/self/fd/%d" % descriptor)
        info = os.fstat(descriptor)
        if self.fail_next:
            self.fail_next = False
            raise OSError(errno.EPERM, "the ownership change failed")
        self.calls.append({"link": link, "inode": (info.st_dev, info.st_ino),
                           "uid": uid, "gid": gid})

    def inodes(self):
        return [call["inode"] for call in self.calls]


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.sysfs = self.root / "sys"
        self.runtime = self.root / "run" / "armada-installer-tf"
        self.checkpoint = self.root / "var" / "tf"
        self.mount_dir = self.root / "mnt"
        self.mount_dir.mkdir()
        self.status_path = self.runtime / tf_state.STATUS_NAME
        self.log_path = self.runtime / tf_state.LOG_NAME
        self.home = self.root / "home"
        self.home.mkdir()
        self._write_sysfs("SD", CID)
        self.runner = WorkerRunner()
        self.runner.default_mount = self.mount_dir
        self.runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        self._patch(pathlib.Path, "is_block_device", lambda self: True)
        self._patch(pathlib.Path, "is_mount", lambda self: False)
        self._patch(service.os, "stat", self._stat)
        # The fake devices carry no readable superblock, so the real ext4
        # journal probe is stubbed; its byte handling has its own tests.
        self._patch(tf_inspection, "_ext4_needs_recovery", lambda device: False)
        self.fchown = OwnershipRecorder()
        self._patch(tf_worker.os, "fchown", self.fchown)
        self._patch(tf_worker.uuid, "uuid4", lambda: uuid.UUID(NEW_FS_UUID))
        self._patch(tf_worker.pwd, "getpwuid", self._getpwuid)

    def _patch(self, obj, name, value):
        started = patch.object(obj, name, value)
        started.start()
        self.addCleanup(started.stop)

    def _stat(self, path, *args, **kwargs):
        if str(path) == DISK:
            return SimpleNamespace(st_rdev=os.makedev(*DISK_MAJMIN))
        return _REAL_STAT(path, *args, **kwargs)

    def _getpwuid(self, uid):
        return SimpleNamespace(pw_uid=uid, pw_gid=uid, pw_dir=str(self.home))

    def _write_sysfs(self, kind, cid):
        device = self.sysfs / "class" / "block" / "mmcblk0" / "device"
        device.mkdir(parents=True, exist_ok=True)
        (device / "type").write_text(kind + "\n", encoding="utf-8")
        (device / "cid").write_text(cid + "\n", encoding="utf-8")

    def _plan(self, mode="format"):
        reserve_type = tf_card.RESERVE_PENDING if mode == "format" else tf_card.RESERVE_CONNECTED
        filesystem_uuid = RESERVE_FS_UUID if mode == "format" else LINUX_FS_UUID
        return {"device": DISK, "reserve_device": RESERVE, "card_cid": CID, "size_bytes": DISK_BYTES,
                "mode": mode, "handoff": handoff(), "table": table(reserve_type),
                "filesystem_uuid": filesystem_uuid,
                "android_bytes": native_parts(reserve_type)[2]["size"] * SECTOR,
                "linux_bytes": native_parts(reserve_type)[0]["size"] * SECTOR}

    def _worker(self, plan, runner=None):
        return tf_worker.TfWorker(plan, status_path=self.status_path, log_path=self.log_path,
                                  runner=runner or self.runner, sysfs_dir=self.sysfs,
                                  runtime_dir=self.runtime, checkpoint_dir=self.checkpoint)

    def _status(self):
        return tf_state.read_json(self.status_path) or {}

    @staticmethod
    def _inode(path):
        info = os.stat(path)
        return (info.st_dev, info.st_ino)

    def _card_runner(self, runner=None):
        runner = runner or WorkerRunner()
        runner.default_mount = self.mount_dir
        runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        return runner


class BusyRunner(WorkerRunner):
    """A runner whose reserve is mounted and refuses to unmount."""

    def __call__(self, argv, **kwargs):
        if argv and argv[0] == "umount":
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "target is busy")
        return super().__call__(argv, **kwargs)


class FailingRequestRunner(WorkerRunner):
    """A request that reports failure while Steam nonetheless registers."""

    def __call__(self, argv, **kwargs):
        if argv and argv[0] == "systemd-run":
            self.calls.append(list(argv))
            self._write_vdf()
            return self._done(argv, 1, "", "steam request failed")
        return super().__call__(argv, **kwargs)


class FormatTests(WorkerTestCase):
    def test_format_retypes_only_p1_and_formats_only_the_reserve(self):
        self.runner.mounted, self.runner.mount_target = True, self.mount_dir
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 0)
        grans = self.runner.ran("sgdisk")
        self.assertEqual(grans, [["sgdisk", "--typecode=1:8300", DISK]])
        mkfs = self.runner.ran("mkfs.ext4")
        self.assertEqual(len(mkfs), 1)
        self.assertEqual(mkfs[0][-1], RESERVE)
        self.assertNotIn(DISK, mkfs[0])
        self.assertIn("nodiscard", mkfs[0])
        self.assertIn("ARMADA_TF_%s" % CID[-6:], mkfs[0])
        self.assertEqual(self.runner.ran("umount")[0][-1], str(self.mount_dir))
        # The upstream helper mounted the reserve exactly once.
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:],
                         ["add", os.path.basename(RESERVE)])
        self.assertEqual(self._status()["state"], "succeeded")

    def test_no_zap_or_wipe_commands_are_ever_issued(self):
        self._worker(self._plan("format")).run()
        executables = {call[0] for call in self.runner.calls}
        for forbidden in ("wipefs", "blkdiscard", "dd"):
            self.assertNotIn(forbidden, executables)
        for call in self.runner.calls:
            if call[0] == "sgdisk":
                for option in call[1:]:
                    self.assertNotIn(option, ("-Z", "--zap", "--zap-all"))

    def test_a_sgdisk_failure_halts_before_mkfs(self):
        self.runner.sgdisk_rc = 1
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("mkfs.ext4"), [])
        self.assertEqual(self._status()["state"], "failed")

    def test_the_validated_checkpoint_is_written_before_any_format(self):
        self._worker(self._plan("format")).run()
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual(record["stage"], "formatted")
        self.assertEqual(record["size_bytes"], DISK_BYTES)
        self.assertEqual(record["android_parts"][0]["uuid"], META_PARTUUID)
        self.assertEqual(record["android_parts"][1]["uuid"], PRIVATE_PARTUUID)
        self.assertEqual(record["linux_fs_uuid"], NEW_FS_UUID)


class ResumeTests(WorkerTestCase):
    def _preformatted(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": NEW_FS_UUID, "TYPE": "ext4"}
        plan = self._plan("format")
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "formatted", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:], "linux_fs_uuid": NEW_FS_UUID})
        return plan

    def test_a_formatted_checkpoint_resumes_without_a_second_mkfs(self):
        code = self._worker(self._preformatted()).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:], ["add", "mmcblk0p1"])
        self.assertEqual(self._status()["state"], "succeeded")

    def test_a_validated_checkpoint_resumes_the_unformatted_reserve(self):
        runner = WorkerRunner()
        runner.default_mount = self.mount_dir
        runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        runner.table = table(tf_card.RESERVE_CONNECTED)
        runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        runner.blkid_values = {}  # retyped to Linux but not yet formatted
        plan = self._plan("format")
        plan["filesystem_uuid"] = None
        plan["recovery"] = True
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:], "linux_fs_uuid": None})
        code = self._worker(plan, runner=runner).run()
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.ran("mkfs.ext4")), 1)
        self.assertEqual(self._status()["state"], "succeeded")


class RecoveryRetryTests(WorkerTestCase):
    """An inspector recovery plan must pass the worker's unchanged-target guard."""

    def _recovery_plan(self, fs_type="vfat", fs_uuid=RESERVE_FS_UUID):
        # p1 was retyped to Linux but the original FAT volume is still there.
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": fs_uuid, "TYPE": fs_type}
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:],
            "linux_fs_uuid": fs_uuid})
        inspector = tf_inspection.TfInspector(runner=self.runner, sysfs_dir=self.sysfs,
                                              runtime_dir=self.runtime,
                                              checkpoint_dir=self.checkpoint)
        card = inspector.inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("recovery", ""))
        return inspector.snapshot(DISK)

    def test_a_retyped_reserve_with_its_original_fat_uuid_is_converted_once(self):
        plan = self._recovery_plan()
        self.assertEqual((plan["filesystem_uuid"], plan["filesystem_type"]),
                         (RESERVE_FS_UUID, "vfat"))
        code = self._worker(plan).run()
        self.assertEqual(code, 0)
        self.assertEqual(self._status()["state"], "succeeded")
        mkfs = self.runner.ran("mkfs.ext4")
        self.assertEqual(len(mkfs), 1)
        self.assertEqual(mkfs[0][-1], RESERVE)
        self.assertEqual(self.runner.ran("sgdisk"), [["sgdisk", "--typecode=1:8300", DISK]])
        for call in self.runner.calls:
            self.assertNotIn(META, call)
            self.assertNotIn(PRIVATE, call)
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual((record["stage"], record["linux_fs_uuid"]), ("formatted", NEW_FS_UUID))

    def test_a_changed_fat_uuid_is_still_refused_before_any_write(self):
        plan = self._recovery_plan()
        self.runner.blkid_values = {"UUID": "1234-5678", "TYPE": "vfat"}
        code = self._worker(plan).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])


class RefusalTests(WorkerTestCase):
    def test_a_stale_card_cid_is_refused_at_the_worker(self):
        plan = self._plan("format")
        plan["card_cid"] = CID_OTHER
        code = self._worker(plan).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        self.assertEqual(self._status()["state"], "failed")

    def test_a_boot_backed_card_is_refused_at_the_worker(self):
        self.runner.ancestors = ["179:0"]
        code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])

    def test_a_busy_reserve_is_refused_before_mkfs(self):
        runner = BusyRunner()
        runner.default_mount = self.mount_dir
        runner.mounted, runner.mount_target = True, self.mount_dir
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("mkfs.ext4"), [])

    def _extra_runner(self):
        runner = WorkerRunner()
        runner.default_mount = self.mount_dir
        runner.vdf_path = self.home / ".local" / "share" / "Steam" / "steamapps"
        return runner

    def test_a_changed_whole_capacity_is_refused_before_any_write(self):
        runner = self._extra_runner()
        # Same CID and same GPT, but the whole card now reports fewer bytes.
        runner.disk_size = DISK_BYTES - 4096
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])

    def test_a_changed_reserve_filesystem_uuid_is_refused_before_any_write(self):
        runner = self._extra_runner()
        # The reserve now carries a different filesystem than the prepared one.
        runner.blkid_values = {"UUID": "1234-5678", "TYPE": "vfat"}
        code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 1)
        self.assertEqual(runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])


class ConnectedTests(WorkerTestCase):
    def test_a_connected_card_is_mounted_but_never_formatted(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        code = self._worker(self._plan("mount")).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4", "partprobe"), [])
        self.assertEqual(self.runner.ran(tf_worker.AUTOMOUNT_HELPER)[0][1:], ["add", "mmcblk0p1"])
        self.assertTrue((self.mount_dir / tf_worker.MARKER_NAME).is_file())
        self.assertTrue((self.mount_dir / "SteamLibrary" / "steamapps").is_dir())


class SteamTests(WorkerTestCase):
    def _mount_plan(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        return self._plan("mount")

    def _write_membership(self):
        """Record ``SteamLibrary`` in the account's own libraryfolders.vdf."""
        path = self.home / ".local" / "share" / "Steam" / "config" / "libraryfolders.vdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        library = self.mount_dir / "SteamLibrary"
        path.write_text('"libraryfolders"\n{\n  "0"\n  {\n    "path" "%s"\n  }\n}\n' % library,
                        encoding="utf-8")

    def test_an_absent_steam_leaves_the_card_mounted_and_pending(self):
        self.runner.steam_running = False
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertEqual(status["state"], "succeeded")
        self.assertFalse(status["library_requested"])
        self.assertFalse(status["library_registered"])
        self.assertTrue(status["library_pending"])
        self.assertEqual(self.runner.ran("systemd-run"), [])

    def test_a_cli_success_without_membership_is_not_a_registration(self):
        self.runner.steam_running = True
        self.runner.vdf_path = None  # no VDF will be written, so membership is unconfirmed
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertTrue(status["library_requested"])
        self.assertFalse(status["library_registered"])
        self.assertTrue(status["library_pending"])
        argv = self.runner.ran("systemd-run")[0]
        self.assertIn("--uid=1000", argv)
        self.assertIn("--collect", argv)
        self.assertIn("--setenv=XDG_RUNTIME_DIR=/run/user/1000", argv)
        self.assertTrue(argv[-1].startswith("steam://addlibraryfolder/"))

    def test_a_confirmed_membership_reports_registered(self):
        self.runner.steam_running = True
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertTrue(status["library_requested"])
        self.assertTrue(status["library_registered"])
        self.assertFalse(status["library_pending"])

    def test_a_listed_library_is_registered_even_while_steam_is_stopped(self):
        self._write_membership()
        self.runner.steam_running = False
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue(status["library_registered"])
        self.assertFalse(status["library_pending"])
        self.assertFalse(status["library_requested"])
        self.assertEqual(self.runner.ran("systemd-run"), [])

    def test_a_running_steam_is_not_asked_to_add_a_listed_library(self):
        self._write_membership()
        self.runner.steam_running = True
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertTrue(status["library_registered"])
        self.assertFalse(status["library_pending"])
        self.assertFalse(status["library_requested"])
        self.assertEqual(self.runner.ran("systemd-run"), [])

    def test_a_failed_request_with_confirmed_membership_is_registered(self):
        runner = self._card_runner(FailingRequestRunner())
        runner.steam_running = True
        runner.table = table(tf_card.RESERVE_CONNECTED)
        runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        code = self._worker(self._plan("mount"), runner=runner).run()
        self.assertEqual(code, 0)
        status = self._status()
        self.assertFalse(status["library_requested"])
        self.assertTrue(status["library_registered"])
        self.assertFalse(status["library_pending"])
        self.assertEqual(len(runner.ran("systemd-run")), 1)


class IntendedUuidTests(WorkerTestCase):
    """The mkfs UUID is persisted before any write and resumes a crash as ext4."""

    def test_the_mkfs_uuid_was_persisted_before_any_write(self):
        events = []
        runner = self._card_runner(OrderedRunner(events))
        real = tf_state.write_checkpoint

        def spy(directory, cid, payload):
            events.append(("checkpoint", payload.get("stage"), payload.get("intended_fs_uuid")))
            return real(directory, cid, payload)

        with patch.object(tf_state, "write_checkpoint", spy):
            code = self._worker(self._plan("format"), runner=runner).run()
        self.assertEqual(code, 0)
        mkfs_index = [index for index, event in enumerate(events) if event[0] == "mkfs"][0]
        mkfs = events[mkfs_index][1]
        intended = mkfs[mkfs.index("-U") + 1]
        self.assertEqual(intended, NEW_FS_UUID)
        self.assertIn("ARMADA_TF_%s" % CID[-6:], mkfs)
        # Nothing but the intended record was persisted before mkfs ran.
        self.assertEqual(events[:mkfs_index],
                         [("checkpoint", "validated", intended)])
        self.assertEqual(events[mkfs_index + 1:], [("checkpoint", "formatted", intended)])

    def test_a_failed_intended_checkpoint_prevents_every_write(self):
        with patch.object(tf_state, "write_checkpoint", side_effect=OSError("disk full")):
            code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("umount", "sgdisk", "mkfs.ext4"), [])
        self.assertEqual(self._status()["state"], "failed")

    def test_a_crash_after_mkfs_resumes_without_a_second_format(self):
        real = tf_state.write_checkpoint
        crashed = []

        def flaky(directory, cid, payload):
            if payload.get("stage") == "formatted" and not crashed:
                crashed.append(True)
                raise OSError("simulated crash before the formatted checkpoint")
            return real(directory, cid, payload)

        with patch.object(tf_state, "write_checkpoint", flaky):
            code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.runner.ran("mkfs.ext4")), 1)
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual(record["stage"], "validated")
        self.assertEqual(record["intended_fs_uuid"], NEW_FS_UUID)
        self.assertEqual(record["linux_fs_uuid"], RESERVE_FS_UUID)

        # The next scan recognises the ext4 the worker created out of the
        # validated record, and the retry only mounts, marks and registers.
        inspector = tf_inspection.TfInspector(runner=self.runner, sysfs_dir=self.sysfs,
                                              runtime_dir=self.runtime,
                                              checkpoint_dir=self.checkpoint)
        card = inspector.inspect_cards()["cards"][0]
        self.assertEqual((card["state"], card["reason"]), ("recovery", ""))
        plan = inspector.snapshot(DISK)
        self.assertEqual((plan["mode"], plan["recovery"]), ("mount", True))
        code = self._worker(plan).run()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.runner.ran("mkfs.ext4")), 1)
        self.assertEqual(len(self.runner.ran("sgdisk")), 1)
        self.assertEqual(self._status()["state"], "succeeded")
        self.assertTrue((self.mount_dir / tf_worker.MARKER_NAME).is_file())

    def test_a_validated_intended_checkpoint_lets_a_format_retry_skip_mkfs(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": NEW_FS_UUID, "TYPE": "ext4"}
        plan = self._plan("format")
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:],
            "linux_fs_uuid": RESERVE_FS_UUID, "intended_fs_uuid": NEW_FS_UUID})
        code = self._worker(plan).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual((record["stage"], record["linux_fs_uuid"], record["intended_fs_uuid"]),
                         ("formatted", NEW_FS_UUID, NEW_FS_UUID))

    def test_a_mismatched_intended_uuid_is_refused_before_any_write(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": NEW_FS_UUID, "TYPE": "ext4"}
        plan = self._plan("format")
        plan["table"] = table(tf_card.RESERVE_CONNECTED)
        tf_state.write_checkpoint(self.checkpoint, CID, {
            "stage": "validated", "card_cid": CID, "size_bytes": DISK_BYTES, "handoff": handoff(),
            "reserve_device": RESERVE, "reserve_type": tf_card.RESERVE_CONNECTED,
            "android_parts": native_parts(tf_card.RESERVE_CONNECTED)[1:],
            "linux_fs_uuid": RESERVE_FS_UUID,
            "intended_fs_uuid": "11111111-2222-3333-4444-555555555555"})
        code = self._worker(plan).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("sgdisk", "mkfs.ext4"), [])
        self.assertIn("target_changed", self._status()["detail"])


class CheckpointDurabilityTests(WorkerTestCase):
    """An unprovable checkpoint must stop the destructive path before any write.

    The failure is injected at the real filesystem boundary: only the parent
    directory fsync of the *checkpoint* directory fails, exactly the point where
    the record's rename may not be durable yet.  The runtime status directory is
    left working, so the failure is scoped to the checkpoint and the worker's
    best-effort status policy is unchanged.
    """

    def _failing_checkpoint_directory_fsync(self):
        self.checkpoint.mkdir(parents=True, exist_ok=True)
        info = os.stat(self.checkpoint)
        wanted = (info.st_dev, info.st_ino)
        real = os.fsync

        def failing(fd):
            current = os.fstat(fd)
            if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == wanted:
                raise OSError(errno.EIO, "the checkpoint directory fsync failed")
            return real(fd)

        return failing

    def test_a_checkpoint_durability_failure_stops_before_every_destructive_step(self):
        # The reserve is mounted, so the only umount the worker would issue is
        # its own, after the validated checkpoint: it must never happen.
        self.runner.mounted, self.runner.mount_target = True, self.mount_dir
        with patch.object(tf_state.os, "fsync", self._failing_checkpoint_directory_fsync()):
            code = self._worker(self._plan("format")).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.ran("umount", "partprobe", "sgdisk", "mkfs.ext4",
                                         tf_worker.AUTOMOUNT_HELPER), [])
        for call in self.runner.calls:
            self.assertNotIn(META, call)
            self.assertNotIn(PRIVATE, call)
        self.assertFalse((self.mount_dir / tf_worker.MARKER_NAME).exists())
        self.assertFalse((self.mount_dir / tf_worker.LIBRARY_NAME).exists())
        self.assertEqual(self.runner.ran("systemd-run"), [])
        self.assertIn("internal_error", self._status()["detail"])
        # The intended record was never marked formatted, so a retry still
        # proves its own mkfs before touching anything.
        record = tf_state.read_checkpoint(self.checkpoint, CID)
        self.assertEqual(record["stage"], "validated")
        self.assertEqual(record["intended_fs_uuid"], NEW_FS_UUID)


class LibraryOwnershipTests(WorkerTestCase):
    """``SteamLibrary/steamapps`` are owned through pinned descriptors only."""

    def _mount_plan(self):
        self.runner.table = table(tf_card.RESERVE_CONNECTED)
        self.runner.children = WorkerRunner._children(tf_card.RESERVE_CONNECTED)
        self.runner.blkid_values = {"UUID": LINUX_FS_UUID, "TYPE": "ext4"}
        return self._plan("mount")

    def _prepare_tree(self):
        library = self.mount_dir / "SteamLibrary"
        steamapps = library / "steamapps"
        game = steamapps / "common" / "game.bin"
        game.parent.mkdir(parents=True)
        game.write_bytes(b"game payload")
        return library, steamapps, game

    def _use_recorder(self, recorder):
        self._patch(tf_worker.os, "fchown", recorder)
        return recorder

    def test_both_managed_directories_are_owned_leaf_first(self):
        library, steamapps, _game = self._prepare_tree()
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.fchown.inodes(), [self._inode(steamapps), self._inode(library)])
        for call in self.fchown.calls:
            self.assertEqual((call["uid"], call["gid"]), (1000, 1000))

    def test_a_child_swapped_for_a_symlink_cannot_redirect_the_ownership(self):
        library, steamapps, game = self._prepare_tree()
        pinned = steamapps.with_name("steamapps-pinned")
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_bytes(b"outside data")
        outside_owner = os.stat(outside).st_uid
        game_owner, game_inode = os.stat(game).st_uid, self._inode(game)

        class Swapping(OwnershipRecorder):
            swapped = False

            def __call__(recorder, descriptor, uid, gid):
                if not recorder.swapped:
                    recorder.swapped = True
                    os.rename(steamapps, pinned)
                    os.symlink(outside, steamapps)
                return OwnershipRecorder.__call__(recorder, descriptor, uid, gid)

        recorder = self._use_recorder(Swapping())
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        self.assertTrue(recorder.swapped)
        self.assertTrue(os.path.islink(steamapps))
        # The ownership transfer addressed the pinned directories, never the
        # symlink target that replaced one of them.
        self.assertEqual(recorder.inodes(), [self._inode(pinned), self._inode(library)])
        self.assertEqual(os.stat(outside).st_uid, outside_owner)
        self.assertEqual(sentinel.read_bytes(), b"outside data")
        # The game file stayed exactly where it was, with its owner and data.
        moved = pinned / "common" / "game.bin"
        self.assertEqual(self._inode(moved), game_inode)
        self.assertEqual(os.stat(moved).st_uid, game_owner)
        self.assertEqual(moved.read_bytes(), b"game payload")

    def test_a_failed_ownership_change_is_repaired_by_a_retry(self):
        library, steamapps, _game = self._prepare_tree()
        self.fchown.fail_next = True
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 1)
        self.assertEqual(self.fchown.calls, [])
        self.assertIn("library_failed", self._status()["detail"])
        # The leftover directories must be re-owned, not skipped because they exist.
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        self.assertEqual(self.fchown.inodes(), [self._inode(steamapps), self._inode(library)])

    def test_game_files_keep_their_owner_and_data(self):
        _library, _steamapps, game = self._prepare_tree()
        before = os.stat(game)
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fchown.calls), 2)
        after = os.stat(game)
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(game.read_bytes(), b"game payload")

    def test_a_symlinked_steamapps_is_still_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        library = self.mount_dir / "SteamLibrary"
        library.mkdir()
        (library / "steamapps").symlink_to(outside)
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 1)
        self.assertIn("library_unsafe", self._status()["detail"])
        self.assertEqual(self.fchown.calls, [])

    def test_a_regular_file_steamapps_is_not_mistaken_for_a_symlink(self):
        library = self.mount_dir / "SteamLibrary"
        library.mkdir()
        (library / "steamapps").write_bytes(b"not a directory")
        code = self._worker(self._mount_plan()).run()
        self.assertEqual(code, 1)
        self.assertIn("library_failed", self._status()["detail"])
        self.assertEqual(self.fchown.calls, [])


if __name__ == "__main__":
    unittest.main()
