"""The running compositor overrides the model's original panel direction."""
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
PLUGIN_ROOT = TESTS_ROOT.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from py_modules.freedom_touch import session  # noqa: E402
from py_modules.freedom_touch.layout import transform_point  # noqa: E402
from test_daemon_lifecycle import Harness  # noqa: E402


@pytest.mark.parametrize("orientation,rotation", [
    ("normal", 0), ("right", 90), ("upsidedown", 180), ("left", 270),
])
@pytest.mark.parametrize("equals", [False, True])
def test_actual_forced_orientation_overrides_device_default(tmp_path, orientation, rotation, equals):
    process = tmp_path / "42"
    process.mkdir()
    args = ["/custom/gamescope", "--steam"]
    args += ["--force-orientation=" + orientation] if equals else ["--force-orientation", orientation]
    (process / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
    with mock.patch.object(session, "_running_gamescope_binaries", return_value=[process / "exe"]):
        actual = session.running_panel_orientation(os.getuid(), tmp_path)
    assert session.rotation_for({session.PANEL_ORIENTATION_KEY: actual}) == rotation


def test_absent_runtime_override_leaves_device_fallback(tmp_path):
    process = tmp_path / "42"
    process.mkdir()
    (process / "cmdline").write_bytes(b"/custom/gamescope\0--steam\0")
    with mock.patch.object(session, "_running_gamescope_binaries", return_value=[process / "exe"]):
        assert session.running_panel_orientation(os.getuid(), tmp_path) is None


@pytest.mark.parametrize("orientation", ["normal", "right", "upsidedown", "left"])
def test_daemon_uses_runtime_direction_for_all_button_centres(orientation):
    harness = Harness()
    env = {"DISPLAY": ":0", "GDK_BACKEND": "x11", session.PANEL_ORIENTATION_KEY: orientation}
    harness.daemon._session_finder = lambda _uid: env
    harness.daemon._rotation_resolver = session.rotation_for
    harness.activate()
    expected = {"normal": 0, "right": 90, "upsidedown": 180, "left": 270}[orientation]
    assert harness.daemon.rotation == expected
    for control in harness.daemon.effective_controls:
        x, y = control["x"] + control["w"] / 2, control["y"] + control["h"] / 2
        raw_x, raw_y = harness.raw_point(x, y)
        ax, ay = harness.daemon._absinfo
        actual = transform_point(raw_x, raw_y, ax.min, ax.max, ay.min, ay.max, expected)
        assert actual == pytest.approx((x, y), abs=0.003)


def test_direction_change_releases_contacts_and_updates_aspect():
    harness = Harness()
    harness.touch.absinfo = lambda code: types.SimpleNamespace(
        min=0, max=2000 if code == 54 else 1000
    )
    current = {"orientation": "left"}
    harness.daemon._session_finder = lambda _uid: {
        "DISPLAY": ":0", "GDK_BACKEND": "x11",
        session.PANEL_ORIENTATION_KEY: current["orientation"],
    }
    harness.daemon._rotation_resolver = session.rotation_for
    harness.activate()
    assert harness.daemon.rotation == 270
    landscape = harness.daemon.aspect_ratio
    current["orientation"] = "normal"
    harness.now += 2
    harness.daemon.tick(harness.now)
    assert harness.daemon.rotation == 0
    assert harness.daemon.aspect_ratio == pytest.approx(1 / landscape)


# ----------------------------------------------------------------------
# runtime probes: the loaded compositor image is the authority
# ----------------------------------------------------------------------

def _compositor(proc_root, pid, executable, arguments):
    """Build one fake /proc/<pid> with an exe link and a command line."""
    process = proc_root / pid
    process.mkdir()
    (process / "cmdline").write_bytes(
        b"\0".join(item.encode() for item in arguments) + b"\0"
    )
    os.symlink(os.fspath(executable), os.fspath(process / "exe"))
    return process


@pytest.mark.parametrize("arguments", [
    ["/custom/runtime/gamescope", "--steam", "--force-orientation=left"],
    ["/custom/runtime/gamescope", "-e", "--force-orientation", "left"],
])
def test_probe_uses_the_loaded_image_of_this_uid(tmp_path, arguments):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    image = tmp_path / "custom" / "gamescope (deleted)"
    image.parent.mkdir()
    image.write_bytes(b"..." + session.OVERLAY_ATOM + b"...")
    process = _compositor(
        proc_root, "4100", image, arguments
    )

    # A replaced copy on disk without the marker must not be consulted.
    replaced = tmp_path / "usr" / "bin" / "gamescope"
    replaced.parent.mkdir(parents=True)
    replaced.write_bytes(b"no marker here")

    uid = os.getuid()
    assert session._running_gamescope_binaries(uid, proc_root) == [
        process / "exe"
    ]
    assert session.running_panel_orientation(uid, proc_root) == "left"
    assert session.overlay_supported(uid=uid, proc_root=proc_root) is True
    assert session.overlay_supported(binary=replaced) is False


def test_explicit_offline_binary_is_still_honoured(tmp_path):
    marked = tmp_path / "marked"
    unmarked = tmp_path / "unmarked"
    marked.write_bytes(session.OVERLAY_ATOM)
    unmarked.write_bytes(b"no marker here")

    assert session.overlay_supported(binary=marked) is True
    assert session.overlay_supported(binary=unmarked) is False
    assert session.overlay_supported(binary=str(marked / "missing")) is False


def test_missing_or_ambiguous_compositor_falls_back_to_default(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    assert session._running_gamescope_binaries(os.getuid(), proc_root) == []
    assert session.running_panel_orientation(os.getuid(), proc_root) is None
    assert session.overlay_supported(uid=os.getuid(), proc_root=proc_root) is False

    image = tmp_path / "custom" / "gamescope"
    image.parent.mkdir()
    image.write_bytes(session.OVERLAY_ATOM)
    _compositor(proc_root, "4100", image, ["/custom/gamescope", "--steam"])
    _compositor(proc_root, "4101", image, ["/custom/gamescope", "--steam"])

    assert session.running_panel_orientation(os.getuid(), proc_root) is None
    assert session.overlay_supported(uid=os.getuid(), proc_root=proc_root) is False


def test_other_uid_and_non_compositor_processes_are_ignored(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    image = tmp_path / "custom" / "gamescope"
    image.parent.mkdir()
    image.write_bytes(session.OVERLAY_ATOM)
    _compositor(
        proc_root, "4100", image,
        ["/custom/gamescope", "--steam", "--force-orientation", "left"],
    )
    launcher = tmp_path / "custom" / "steam"
    launcher.write_bytes(b"not the compositor")
    _compositor(proc_root, "4101", launcher, ["/usr/bin/steam", "--steam"])

    uid = os.getuid()
    assert session.running_panel_orientation(uid + 1, proc_root) is None
    assert session.overlay_supported(uid=uid + 1, proc_root=proc_root) is False
    assert session.running_panel_orientation(uid, proc_root) == "left"
