#!/usr/bin/env python3
"""LowPowerMode1 download-mode lease tests for the armada-powerd companion.

Run under a private session bus:

    dbus-run-session -- python3 tests/powerd-low-power-test.py

The optional --powerd argument runs the same suite against another checkout,
for example the system repository daemon.

The helper daemon never builds the real ArmadaPower(): it allocates an instance
with __new__, gives it an empty download_handles table and publishes only the
LowPowerMode1 interface taken from the daemon's own XML. No profile, fan, CPU
or GPU state is read or written, so the suite cannot touch hardware.
"""

import importlib.machinery
import importlib.util
import os
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError) as exc:  # pragma: no cover - environment guard
    Gio = None
    GLib = None
    GI_IMPORT_ERROR = exc
else:
    GI_IMPORT_ERROR = None

# Wire contract of the LowPowerMode1 relay; the daemon XML declares it.
BUS_NAME = "org.armada.Power"
OBJECT_PATH = "/org/armada/Power"
IFACE_LOW_POWER = "com.steampowered.SteamOSManager1.LowPowerMode1"

CALL_TIMEOUT_MS = 5000
READY_TIMEOUT_S = 20.0
LEASE_TIMEOUT_S = 10.0

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POWERD = REPO_ROOT / "system_files" / "usr" / "libexec" / "armada" / "armada-powerd"
POWERD_PATH = DEFAULT_POWERD


def load_module(name, path):
    """Import *path* under *name* and return the loaded module."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def find_armada_perf(powerd_path):
    """Return the armada_perf.py file shipped at or above *powerd_path*."""
    directory = Path(powerd_path).resolve().parent
    for _ in range(6):
        for candidate in (directory / "armada_perf.py",
                          directory / "lib" / "armada" / "armada_perf.py"):
            if candidate.is_file():
                return candidate
        if directory.parent == directory:
            break
        directory = directory.parent
    raise FileNotFoundError(f"armada_perf.py not found next to {powerd_path}")


def drain(stream, sink):
    """Append each line from *stream* to *sink* until EOF or the stream closes."""
    try:
        for line in iter(stream.readline, ""):
            sink.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass


def close_quietly(fd):
    """Close *fd*, ignoring an already-closed or invalid descriptor."""
    try:
        os.close(fd)
    except OSError:
        pass


class EnterDownloadModeFailureTest(unittest.TestCase):
    """Failure-path tests that call the daemon method without a bus."""

    @classmethod
    def setUpClass(cls):
        """Load armada_perf and the daemon under test once for this class."""
        if not POWERD_PATH.is_file():
            raise unittest.SkipTest(f"powerd not found: {POWERD_PATH}")
        if "armada_perf" not in sys.modules:
            load_module("armada_perf", find_armada_perf(POWERD_PATH))
        cls.module = load_module("armada_powerd_failure_test", POWERD_PATH)

    def new_power(self):
        """Return an ArmadaPower instance with an empty lease table."""
        power = self.module.ArmadaPower.__new__(self.module.ArmadaPower)
        power.download_handles = {}
        return power

    def assert_closed(self, fd):
        """Assert that *fd* is no longer an open descriptor."""
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_append_failure_replies_once_and_closes_both_descriptors(self):
        """A failing UnixFDList append replies once and closes both pipe ends."""
        module = self.module
        rfd, wfd = os.pipe()
        failing_list = mock.Mock()
        failing_list.append.side_effect = OSError("append failed")
        fake_gio = mock.Mock()
        fake_gio.UnixFDList.new.return_value = failing_list
        fake_glib = mock.Mock()
        invocation = mock.Mock()
        power = self.new_power()
        try:
            with mock.patch.object(module, "Gio", fake_gio, create=True):
                with mock.patch.object(module, "GLib", fake_glib, create=True):
                    with mock.patch.object(module.os, "pipe", return_value=(rfd, wfd)):
                        power.enter_download_mode("steam-app", invocation)
            self.assertEqual(power.download_handles, {})
            fake_glib.unix_fd_add_full.assert_not_called()
            invocation.return_value_with_unix_fd_list.assert_not_called()
            invocation.return_dbus_error.assert_called_once()
            name, message = invocation.return_dbus_error.call_args.args
            self.assertEqual(name, f"{module.BUS_NAME}.Error.DownloadModeFailed")
            self.assertIn("append failed", message)
            self.assert_closed(rfd)
            self.assert_closed(wfd)
        finally:
            close_quietly(rfd)
            close_quietly(wfd)

    def test_pipe_failure_replies_once_without_registration(self):
        """A failing os.pipe replies once and registers no watch."""
        module = self.module
        fake_glib = mock.Mock()
        invocation = mock.Mock()
        power = self.new_power()
        with mock.patch.object(module, "GLib", fake_glib, create=True):
            with mock.patch.object(module.os, "pipe", side_effect=OSError("pipe failed")):
                power.enter_download_mode("steam-app", invocation)
        self.assertEqual(power.download_handles, {})
        fake_glib.unix_fd_add_full.assert_not_called()
        invocation.return_value_with_unix_fd_list.assert_not_called()
        invocation.return_dbus_error.assert_called_once()
        name, message = invocation.return_dbus_error.call_args.args
        self.assertEqual(name, f"{module.BUS_NAME}.Error.DownloadModeFailed")
        self.assertIn("pipe failed", message)

    def test_limit_rejection_happens_before_pipe_allocation(self):
        """At the cap the daemon rejects without allocating a pipe or a lease."""
        module = self.module
        power = self.new_power()
        power.download_handles = {"steam-app": module.MAX_DOWNLOAD_HANDLES}
        invocation = mock.Mock()
        with mock.patch.object(module.os, "pipe") as pipe_mock:
            power.enter_download_mode("steam-app-two", invocation)
        pipe_mock.assert_not_called()
        self.assertEqual(power.download_handles,
                         {"steam-app": module.MAX_DOWNLOAD_HANDLES})
        invocation.return_value_with_unix_fd_list.assert_not_called()
        invocation.return_dbus_error.assert_called_once()
        name, message = invocation.return_dbus_error.call_args.args
        self.assertEqual(name, f"{module.BUS_NAME}.Error.DownloadModeLimitReached")
        self.assertIn(str(module.MAX_DOWNLOAD_HANDLES), message)

    def test_limit_sums_handles_across_identifiers(self):
        """The cap counts every identifier together, not each one separately."""
        module = self.module
        cap = module.MAX_DOWNLOAD_HANDLES
        power = self.new_power()
        power.download_handles = {
            "steam-app-one": cap // 2,
            "steam-app-two": cap - cap // 2,
        }
        invocation = mock.Mock()
        with mock.patch.object(module.os, "pipe") as pipe_mock:
            power.enter_download_mode("steam-app-three", invocation)
        pipe_mock.assert_not_called()
        invocation.return_dbus_error.assert_called_once()
        name, _message = invocation.return_dbus_error.call_args.args
        self.assertEqual(name, f"{module.BUS_NAME}.Error.DownloadModeLimitReached")
        self.assertEqual(power.download_handles,
                         {"steam-app-one": cap // 2,
                          "steam-app-two": cap - cap // 2})

    def test_one_below_limit_still_allocates_a_handle(self):
        """A table one below the cap still accepts and registers a new lease."""
        module = self.module
        rfd, wfd = os.pipe()
        fds = mock.Mock()
        fake_gio = mock.Mock()
        fake_gio.UnixFDList.new.return_value = fds
        # Real GI flag enum so the daemon can OR HUP and ERR together; every
        # other GLib attribute, including the watch, stays mocked.
        fake_glib = mock.Mock()
        fake_glib.IOCondition = GLib.IOCondition
        invocation = mock.Mock()
        power = self.new_power()
        power.download_handles = {"steam-app": module.MAX_DOWNLOAD_HANDLES - 1}
        try:
            with mock.patch.object(module, "Gio", fake_gio, create=True):
                with mock.patch.object(module, "GLib", fake_glib, create=True):
                    with mock.patch.object(module.os, "pipe",
                                           return_value=(rfd, wfd)):
                        power.enter_download_mode("steam-app-two", invocation)
            invocation.return_dbus_error.assert_not_called()
            invocation.return_value_with_unix_fd_list.assert_called_once()
            fake_glib.unix_fd_add_full.assert_called_once()
            self.assertEqual(power.download_handles,
                             {"steam-app": module.MAX_DOWNLOAD_HANDLES - 1,
                              "steam-app-two": 1})
            self.assert_closed(wfd)
        finally:
            close_quietly(rfd)
            close_quietly(wfd)


class DownloadModeServer:
    """Publish only the daemon's LowPowerMode1 object on the session bus."""

    def __init__(self, powerd_path):
        """Store the daemon path and clear every server attribute."""
        self.powerd_path = Path(powerd_path)
        self.module = None
        self.power = None
        self.interface = None
        self.loop = None
        self.owner_id = None

    def load(self):
        """Load the daemon and expose only its LowPowerMode1 interface."""
        perf_path = find_armada_perf(self.powerd_path)
        # The daemon imports armada_perf at module scope; provide the repo copy
        # first so the installed /usr tree is never used.
        load_module("armada_perf", perf_path)
        module = load_module("armada_powerd_under_test", self.powerd_path)
        module.GLib = GLib
        module.Gio = Gio
        node = Gio.DBusNodeInfo.new_for_xml(module.XML)
        interfaces = {getattr(iface, "name", ""): iface for iface in node.interfaces}
        self.interface = interfaces.get(module.IFACE_LOW_POWER)
        if self.interface is None:
            raise RuntimeError("daemon XML does not declare LowPowerMode1")
        power = module.ArmadaPower.__new__(module.ArmadaPower)
        power.download_handles = {}
        self.module = module
        self.power = power

    def run(self):
        """Publish the interface on the session bus until signalled, then exit 0."""
        self.load()
        self.loop = GLib.MainLoop()

        def bus_acquired(connection, _name):
            """Register the LowPowerMode1 object and announce READY on stdout."""
            connection.register_object(
                self.module.OBJECT_PATH,
                self.interface,
                self.power.handle_method_call,
                None,
                None,
            )
            sys.stdout.write("READY\n")
            sys.stdout.flush()

        def name_lost(_connection, _name):
            """Stop the loop when the bus name is taken away."""
            self.loop.quit()

        self.owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            self.module.BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            bus_acquired,
            None,
            name_lost,
        )

        def stop(_signum, _frame):
            """Stop the loop on SIGINT or SIGTERM."""
            self.loop.quit()

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, stop)
        try:
            self.loop.run()
        finally:
            if self.owner_id is not None:
                Gio.bus_unown_name(self.owner_id)
                self.owner_id = None
        return 0


class LowPowerMode1LeaseTest(unittest.TestCase):
    server_proc = None
    daemon_log = None

    @classmethod
    def setUpClass(cls):
        """Start a private daemon process and wait until it reports READY."""
        if Gio is None:
            raise unittest.SkipTest(f"PyGObject Gio unavailable: {GI_IMPORT_ERROR}")
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            raise unittest.SkipTest("no private session bus; run under dbus-run-session")
        if not POWERD_PATH.is_file():
            raise unittest.SkipTest(f"powerd not found: {POWERD_PATH}")
        if "armada_perf" not in sys.modules:
            load_module("armada_perf", find_armada_perf(POWERD_PATH))
        # Read the cap from the daemon under test so both checkouts agree.
        cls.module = load_module("armada_powerd_cap_test", POWERD_PATH)
        cls.daemon_log = []
        cls.server_proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--serve", "--powerd", str(POWERD_PATH)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        cls.log_thread = threading.Thread(
            target=drain, args=(cls.server_proc.stderr, cls.daemon_log), daemon=True)
        cls.log_thread.start()
        try:
            cls._wait_ready()
        except Exception:
            cls._stop_server()
            raise

    @classmethod
    def tearDownClass(cls):
        """Stop the daemon process started for this class."""
        cls._stop_server()

    @classmethod
    def _stop_server(cls):
        """Terminate the daemon, close its pipes and join the log drain thread."""
        proc = cls.server_proc
        cls.server_proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        if getattr(cls, "log_thread", None) is not None:
            cls.log_thread.join(timeout=5)

    @classmethod
    def log_tail(cls, count=20):
        """Return the last *count* daemon stderr lines for failure messages."""
        if not cls.daemon_log:
            return "(no daemon output)"
        return "\n".join(cls.daemon_log[-count:])

    @classmethod
    def _wait_ready(cls):
        """Wait until the daemon prints READY, failing on exit or timeout."""
        stream = cls.server_proc.stdout
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("daemon did not report READY\n" + cls.log_tail())
            if cls.server_proc.poll() is not None:
                raise RuntimeError(
                    f"daemon exited before READY: {cls.server_proc.returncode}\n"
                    + cls.log_tail())
            readable, _, _ = select.select([stream], [], [], min(remaining, 0.5))
            if not readable:
                continue
            line = stream.readline()
            if not line:
                raise RuntimeError("daemon closed stdout before READY\n" + cls.log_tail())
            if line.strip() == "READY":
                return

    def setUp(self):
        """Open the session-bus connection and start tracking leased descriptors."""
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.open_fds = set()

    def tearDown(self):
        """Close descriptors this test kept and require no leaked leases."""
        for fd in list(self.open_fds):
            close_quietly(fd)
        self.open_fds.clear()
        self._wait_handles(lambda handles: not handles,
                           f"leases leaked by {self.id()}")

    def handles_raw(self):
        """Return the daemon's identifier-to-lease-count table."""
        reply = self.connection.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            IFACE_LOW_POWER,
            "ListDownloadModeHandles",
            None,
            GLib.VariantType("(a{su})"),
            Gio.DBusCallFlags.NONE,
            CALL_TIMEOUT_MS,
            None,
        )
        return reply.unpack()[0]

    def lease(self, identifier):
        """Lease *identifier* and return the descriptor this test now owns.

        The returned descriptor is the write end whose close releases the
        lease; dropping the fd list removes only the list's own reference.
        """
        reply, fd_list = self.connection.call_with_unix_fd_list_sync(
            BUS_NAME,
            OBJECT_PATH,
            IFACE_LOW_POWER,
            "EnterDownloadMode",
            GLib.Variant("(s)", (identifier,)),
            GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE,
            CALL_TIMEOUT_MS,
            None,
            None,
        )
        index = reply.unpack()[0]
        self.assertGreaterEqual(index, 0)
        fd = fd_list.get(index)
        # Dropping the list closes the descriptor it owns, so the lease then
        # depends only on the copies this test keeps.
        del fd_list
        self.assertGreaterEqual(fd, 0, "EnterDownloadMode returned no descriptor")
        self.assertTrue(
            stat.S_ISFIFO(os.fstat(fd).st_mode),
            "EnterDownloadMode did not transfer a pipe descriptor",
        )
        self.open_fds.add(fd)
        return fd

    def lease_error_name(self, identifier):
        """Attempt a lease and return the remote error name that rejected it."""
        try:
            self.connection.call_with_unix_fd_list_sync(
                BUS_NAME,
                OBJECT_PATH,
                IFACE_LOW_POWER,
                "EnterDownloadMode",
                GLib.Variant("(s)", (identifier,)),
                GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE,
                CALL_TIMEOUT_MS,
                None,
                None,
            )
        except GLib.Error as exc:
            try:
                return Gio.DBusError.get_remote_error(exc) or exc.message
            except Exception:
                return exc.message
        self.fail(f"EnterDownloadMode for {identifier!r} unexpectedly succeeded")

    def close_lease(self, fd):
        """Close a leased descriptor while this test still owns it."""
        if fd in self.open_fds:
            os.close(fd)
            self.open_fds.discard(fd)

    def _wait_handles(self, predicate, description):
        """Poll the handle table until *predicate* holds or the deadline expires."""
        deadline = time.monotonic() + LEASE_TIMEOUT_S
        handles = {}
        while time.monotonic() < deadline:
            handles = self.handles_raw()
            if predicate(handles):
                return handles
            time.sleep(0.02)
        self.fail(f"{description}; last ListDownloadModeHandles={handles!r}\n"
                  + self.log_tail())

    def wait_count(self, identifier, expected):
        """Wait until *identifier* has *expected* leases; zero means absent."""
        if expected:
            return self._wait_handles(
                lambda handles: handles.get(identifier) == expected,
                f"expected {expected} lease(s) for {identifier!r}")
        return self._wait_handles(
            lambda handles: identifier not in handles,
            f"expected no lease for {identifier!r}")

    def assert_stays(self, identifier, expected, duration=0.3):
        """Assert *identifier* keeps *expected* leases for *duration* seconds."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            handles = self.handles_raw()
            self.assertEqual(
                handles.get(identifier, 0), expected,
                f"{identifier!r} changed early: {handles!r}\n" + self.log_tail())
            time.sleep(0.02)

    def test_two_leases_of_same_identifier_are_counted(self):
        """Two leases of one identifier are counted separately."""
        self.lease("steam-app")
        self.lease("steam-app")
        self.wait_count("steam-app", 2)

    def test_separate_identifiers_are_tracked_independently(self):
        """Each identifier keeps its own lease count."""
        self.lease("steam-app-one")
        self.lease("steam-app-two")
        handles = self.wait_count("steam-app-two", 1)
        self.assertEqual(handles, {"steam-app-one": 1, "steam-app-two": 1})

    def test_closing_one_lease_decrements(self):
        """Closing one of two leases decrements the count."""
        first = self.lease("steam-app")
        self.lease("steam-app")
        self.wait_count("steam-app", 2)
        self.close_lease(first)
        handles = self.wait_count("steam-app", 1)
        self.assertEqual(handles, {"steam-app": 1})

    def test_duplicated_descriptor_holds_lease_until_last_copy_closes(self):
        """A dup of the descriptor keeps the lease until the last copy closes."""
        fd = self.lease("steam-app")
        duplicate = os.dup(fd)
        self.open_fds.add(duplicate)
        self.close_lease(fd)
        self.assert_stays("steam-app", 1)
        self.close_lease(duplicate)
        self.wait_count("steam-app", 0)

    def test_closing_every_lease_removes_the_identifier(self):
        """Closing every lease removes the identifier from the table."""
        leases = [self.lease("steam-app") for _ in range(3)]
        self.wait_count("steam-app", 3)
        for fd in leases:
            self.close_lease(fd)
        handles = self.wait_count("steam-app", 0)
        self.assertEqual(handles, {})

    def test_disconnected_client_drops_its_lease(self):
        """A client that exits without closing loses its lease."""
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--client-lease", "steam-app"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEASED 1", result.stdout,
                      f"client never held a lease: {result.stdout}{result.stderr}")
        handles = self.wait_count("steam-app", 0)
        self.assertEqual(handles, {})

    def fill_to_cap(self):
        """Lease one distinct identifier at a time until the daemon hits the cap."""
        cap = self.module.MAX_DOWNLOAD_HANDLES
        leases = [self.lease(f"steam-app-{index}") for index in range(cap)]
        self._wait_handles(
            lambda current: sum(current.values()) == cap,
            f"expected {cap} leases before the cap test")
        return leases

    def test_cap_rejects_new_identifier_and_keeps_existing_leases(self):
        """A full handle table rejects a new identifier while earlier leases stay live."""
        leases = self.fill_to_cap()
        before = self.handles_raw()
        message = self.lease_error_name("steam-app-overflow")
        self.assertIn(f"{BUS_NAME}.Error.DownloadModeLimitReached", message)
        self.assertEqual(self.handles_raw(), before,
                         "a rejected lease must not change the handle table")
        self.assert_stays("steam-app-overflow", 0)
        # The handle accepted at the cap is still a working write end.
        self.assertEqual(os.write(leases[-1], b"x"), 1)
        self.close_lease(leases[-1])
        self.wait_count(f"steam-app-{len(leases) - 1}", 0)
        self.lease("steam-app-overflow")
        self.wait_count("steam-app-overflow", 1)

    def test_cap_rejects_repeated_identifier(self):
        """Repeated leases of one identifier count against the same global cap."""
        cap = self.module.MAX_DOWNLOAD_HANDLES
        for _ in range(cap):
            self.lease("steam-app")
        self.wait_count("steam-app", cap)
        message = self.lease_error_name("steam-app")
        self.assertIn(f"{BUS_NAME}.Error.DownloadModeLimitReached", message)
        self.assertEqual(self.handles_raw(), {"steam-app": cap})


def run_client(identifier):
    """Lease *identifier*, print the observed count, and exit without closing."""
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply, fd_list = connection.call_with_unix_fd_list_sync(
        BUS_NAME,
        OBJECT_PATH,
        IFACE_LOW_POWER,
        "EnterDownloadMode",
        GLib.Variant("(s)", (identifier,)),
        GLib.VariantType("(h)"),
        Gio.DBusCallFlags.NONE,
        CALL_TIMEOUT_MS,
        None,
        None,
    )
    fd = fd_list.get(reply.unpack()[0])
    if fd < 0:
        print("LEASED 0")
        sys.stdout.flush()
        return 1
    reply = connection.call_sync(
        BUS_NAME,
        OBJECT_PATH,
        IFACE_LOW_POWER,
        "ListDownloadModeHandles",
        None,
        GLib.VariantType("(a{su})"),
        Gio.DBusCallFlags.NONE,
        CALL_TIMEOUT_MS,
        None,
    )
    count = reply.unpack()[0].get(identifier, 0)
    print(f"LEASED {count}")
    sys.stdout.flush()
    # Leave without closing: the kernel releases every descriptor at process
    # exit, which is the client-disconnect path this suite has to cover.
    os._exit(0)


def parse_args(argv):
    """Split the daemon options from the unittest arguments."""
    powerd = DEFAULT_POWERD
    serve = False
    client_lease = None
    rest = [argv[0]]
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--serve":
            serve = True
            index += 1
        elif argument == "--powerd" and index + 1 < len(argv):
            powerd = Path(argv[index + 1])
            index += 2
        elif argument.startswith("--powerd="):
            powerd = Path(argument.split("=", 1)[1])
            index += 1
        elif argument == "--client-lease" and index + 1 < len(argv):
            client_lease = argv[index + 1]
            index += 2
        elif argument.startswith("--client-lease="):
            client_lease = argument.split("=", 1)[1]
            index += 1
        else:
            rest.append(argument)
            index += 1
    return powerd, serve, client_lease, rest


def main(argv):
    """Dispatch serve, client-lease or unittest mode."""
    global POWERD_PATH
    powerd, serve, client_lease, rest = parse_args(argv)
    POWERD_PATH = Path(powerd).resolve()
    if Gio is None:
        sys.stderr.write(f"PyGObject Gio is required: {GI_IMPORT_ERROR}\n")
        return 2
    if serve:
        return DownloadModeServer(POWERD_PATH).run()
    if client_lease is not None:
        return run_client(client_lease)
    unittest.main(argv=rest, verbosity=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
