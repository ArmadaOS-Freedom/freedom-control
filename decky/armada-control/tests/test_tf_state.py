"""Tests for ``armada_control.tf_library.tf_state`` durability.

``atomic_write_json`` must only report success when the new name is durable, so
a caller that gates destructive work on it (the TF worker's pre-mkfs
checkpoint) can trust a returned call.  These tests exercise the real function
with only the directory open/fd fsync primitives replaced, and prove that:

* a normal write still fsyncs the temporary file, replaces it and fsyncs the
  parent directory;
* a parent-directory open failure propagates as ``OSError`` and leaks no
  descriptor (none was opened);
* a parent-directory fsync failure propagates as ``OSError`` *and* the opened
  directory descriptor is still closed;
* a temporary-file fsync failure keeps its original behaviour: the error
  propagates and the temporary file is removed, so no ``.tmp-`` file is left.

No real device, mount or subprocess is involved.
"""

import errno
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control.tf_library import tf_state  # noqa: E402

PAYLOAD = {"stage": "validated", "intended_fs_uuid": "00112233-4455-6677-8899-aabbccddeeff"}


def _temporaries(directory):
    return sorted(name for name in os.listdir(directory) if name.startswith(".tmp-"))


class AtomicWriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = pathlib.Path(self.tmp.name) / "tf"
        self.directory.mkdir()
        self.path = self.directory / "checkpoint.json"

    def _spy_fsync(self):
        """Record ``(fd, is_directory)`` for every fsync, then delegate."""
        calls = []
        real = os.fsync

        def spy(fd):
            calls.append((fd, stat.S_ISDIR(os.fstat(fd).st_mode)))
            return real(fd)

        started = patch.object(tf_state.os, "fsync", spy)
        started.start()
        self.addCleanup(started.stop)
        return calls

    def test_a_normal_write_persists_the_file_and_the_directory_entry(self):
        self._spy_fsync()
        replace_calls = []
        real_replace = os.replace

        def replace_spy(source, target):
            replace_calls.append((os.fspath(source), os.fspath(target)))
            return real_replace(source, target)

        with patch.object(tf_state.os, "replace", replace_spy):
            tf_state.atomic_write_json(self.path, PAYLOAD)

        self.assertEqual(len(replace_calls), 1)
        source, target = replace_calls[0]
        source_path = pathlib.Path(source)
        self.assertEqual(pathlib.Path(target), self.path)
        self.assertEqual(source_path.parent, self.directory)
        self.assertNotEqual(source_path, self.path)
        self.assertRegex(source_path.name, r"^\.tmp-.+\.json$")
        self.assertFalse(source_path.exists(), "the temporary source was not consumed")
        self.assertEqual(tf_state.read_json(self.path), PAYLOAD)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(_temporaries(self.directory), [])

    def test_the_temporary_file_and_the_directory_are_both_fsynced(self):
        calls = self._spy_fsync()
        tf_state.atomic_write_json(self.path, PAYLOAD)
        self.assertTrue(any(not is_directory for _fd, is_directory in calls),
                        "the temporary file was not fsynced")
        self.assertTrue(any(is_directory for _fd, is_directory in calls),
                        "the parent directory was not fsynced")

    def test_a_failed_directory_open_propagates(self):
        real_open = os.open
        opened = []

        def failing_open(path, flags, *args, **kwargs):
            if os.fspath(path) == str(self.directory):
                raise OSError(errno.EACCES, "the directory could not be opened")
            descriptor = real_open(path, flags, *args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with patch.object(tf_state.os, "open", failing_open):
            with self.assertRaises(OSError) as caught:
                tf_state.atomic_write_json(self.path, PAYLOAD)

        self.assertEqual(caught.exception.errno, errno.EACCES)
        # Only the temporary file was opened, and it was consumed by the
        # stream; nothing was leaked.
        self.assertEqual(len(opened), 1)
        self.assertEqual(_temporaries(self.directory), [])

    @staticmethod
    def _failing_directory_fsync(error):
        """Fail only directory fsyncs, recording each descriptor while open."""
        directory_fds = []
        real = os.fsync

        def failing(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_fds.append(fd)
                raise OSError(error, "the directory fsync failed")
            return real(fd)

        return failing, directory_fds

    def test_a_failed_directory_fsync_propagates_and_closes_the_descriptor(self):
        failing, directory_fds = self._failing_directory_fsync(errno.EIO)
        with patch.object(tf_state.os, "fsync", failing):
            with self.assertRaises(OSError) as caught:
                tf_state.atomic_write_json(self.path, PAYLOAD)

        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(len(directory_fds), 1)
        # The directory descriptor opened for the fsync was still closed.
        with self.assertRaises(OSError) as closed:
            os.fstat(directory_fds[0])
        self.assertEqual(closed.exception.errno, errno.EBADF)
        self.assertEqual(_temporaries(self.directory), [])

    def test_a_failed_file_fsync_propagates_and_removes_the_temporary_file(self):
        real_fsync = os.fsync

        def failing_fsync(fd):
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.ENOSPC, "the file fsync failed")
            return real_fsync(fd)

        with patch.object(tf_state.os, "fsync", failing_fsync):
            with self.assertRaises(OSError) as caught:
                tf_state.atomic_write_json(self.path, PAYLOAD)

        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertEqual(_temporaries(self.directory), [])
        self.assertFalse(self.path.exists())

    def test_write_checkpoint_passes_the_durability_failure_through(self):
        cid = "1b2c3d4e5f60718293a4b5c6d7e8f901"
        failing, _fds = self._failing_directory_fsync(errno.EIO)
        with patch.object(tf_state.os, "fsync", failing):
            with self.assertRaises(OSError):
                tf_state.write_checkpoint(self.directory, cid, PAYLOAD)


if __name__ == "__main__":
    unittest.main()
