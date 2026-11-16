"""Focused regression tests for ``armada_control.system.set_sleep_logs_enabled``.

Enabling or disabling native sleep logging finishes by reloading systemd, and
that reload must run with ``clean_env()``: Decky's PyInstaller loader exports
its unpack directory as ``LD_LIBRARY_PATH``, so the host ``systemctl`` picks up
bundled libraries and dies.  ``LD_LIBRARY_PATH_ORIG`` holds the pre-bundle value
and has to be restored; without it the variable must be removed.

Only the module's path constants are redirected into a temporary directory and
the two subprocess entry points are replaced, so the real function writes,
chmods and unlinks its files.
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control import proc, system  # noqa: E402

HOOK_SOURCE = b"#!/bin/sh\nexec /usr/bin/armada-sleep-debug hook \"$@\"\n"
RELOAD = ["/usr/bin/systemctl", "daemon-reload"]


class SleepLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.command = base / "usr/bin/armada-sleep-debug"
        self.module = base / "usr/lib/armada/armada_sleep_debug.py"
        self.source = base / "py_modules/armada_control/sleep_debug_hook.sh"
        self.hook = base / "etc/armada/sleep-debug-hook"
        self.dropin = (
            base / "etc/systemd/system/systemd-suspend.service.d/90-armada-sleep-debug.conf"
        )
        for path, content in (
            (self.command, b"#!/bin/sh\n"),
            (self.module, b"# module\n"),
            (self.source, HOOK_SOURCE),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for name, value in (
            ("SLEEP_DEBUG_COMMAND", self.command),
            ("SLEEP_DEBUG_MODULE", self.module),
            ("SLEEP_LOG_HOOK_SOURCE", self.source),
            ("SLEEP_LOG_HOOK", self.hook),
            ("SLEEP_LOG_DROPIN", self.dropin),
        ):
            patcher = mock.patch.object(system, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _reload_kwargs(self, run):
        self.assertEqual(run.call_count, 1)
        args, kwargs = run.call_args
        self.assertEqual(args[0], RELOAD)
        return kwargs

    def test_enabling_writes_the_hook_and_reloads_with_the_clean_environment(self):
        original = "/usr/lib/x86_64-linux-gnu"
        with mock.patch.dict(
            os.environ,
            {"LD_LIBRARY_PATH": "/tmp/_MEI123456", "LD_LIBRARY_PATH_ORIG": original},
            clear=True,
        ):
            expected = proc.clean_env()
            with mock.patch.object(system.subprocess, "run") as run:
                self.assertIs(system.set_sleep_logs_enabled(True), True)
            kwargs = self._reload_kwargs(run)

        self.assertEqual(expected["LD_LIBRARY_PATH"], original)
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", expected)
        self.assertEqual(kwargs.get("env"), expected)
        self.assertIs(kwargs["check"], True)
        self.assertEqual(kwargs["timeout"], 15)
        self.assertIs(kwargs["text"], True)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(self.dropin.read_text(encoding="utf-8"), system.sleep_log_dropin())
        self.assertEqual(self.hook.read_bytes(), HOOK_SOURCE)
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o755)

    def test_disabling_restores_libraries_and_never_leaks_the_bundle_path(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_text(system.sleep_log_dropin(), encoding="utf-8")
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEI123456"}, clear=True):
            expected = proc.clean_env()
            with mock.patch.object(system, "run_cmd") as run_cmd:
                with mock.patch.object(system.subprocess, "run") as run:
                    self.assertIs(system.set_sleep_logs_enabled(False), False)
            kwargs = self._reload_kwargs(run)

        # The bundle path was present in the environment but must not be forwarded.
        self.assertNotIn("LD_LIBRARY_PATH", expected)
        self.assertEqual(kwargs.get("env"), expected)
        self.assertFalse(self.dropin.exists())
        run_cmd.assert_called_once_with([str(self.command), "restore"])

    def test_a_failed_reload_reports_the_systemd_stderr(self):
        error = subprocess.CalledProcessError(1, RELOAD, stderr="Unit reload failed.\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(system.subprocess, "run", side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    system.set_sleep_logs_enabled(True)

        self.assertEqual(str(caught.exception), "Unit reload failed.")
        self.assertIs(caught.exception.__cause__, error)

    def test_a_timed_out_reload_keeps_the_generic_message(self):
        error = subprocess.TimeoutExpired(RELOAD, 15)
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(system, "run_cmd"):
                with mock.patch.object(system.subprocess, "run", side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        system.set_sleep_logs_enabled(False)

        self.assertEqual(str(caught.exception), "Could not reload systemd sleep logging.")
        self.assertIs(caught.exception.__cause__, error)

    def test_missing_support_files_fail_before_systemd_is_touched(self):
        self.command.unlink()
        with mock.patch.object(system.subprocess, "run") as run:
            with self.assertRaises(RuntimeError) as caught:
                system.set_sleep_logs_enabled(True)

        self.assertEqual(str(caught.exception), "Native sleep logging is unavailable.")
        run.assert_not_called()
        self.assertFalse(self.dropin.exists())


if __name__ == "__main__":
    unittest.main()
