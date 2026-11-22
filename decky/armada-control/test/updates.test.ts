import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { platform } from "node:os";
import { fileURLToPath } from "node:url";

const source = readFileSync(new URL("../py_modules/armada_control/updates.py", import.meta.url), "utf8");
const python = String.raw`
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("updates", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
`;
const runPython = (code, args = []) => {
  const executable = process.platform === "win32" ? "python" : "python3";
  const result = spawnSync(executable, ["-c", python + code, fileURLToPath(new URL("../py_modules/armada_control/updates.py", import.meta.url)), ...args], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result.stdout;
};

test("update contract exposes only Armada Linux package kinds", () => {
  assert.match(source, /kind not in \("turnip", "compatibility"\)/);
  assert.doesNotMatch(source, /android/i);
});

test("update downloads require HTTPS and reject traversal IDs", () => {
  assert.match(source, /value not in \("\."\, "\.\."\)/);
  assert.match(source, /url\.startswith\("https:\/\/"\)/);
});

test("Proton catalog uses DroidDeck ARM64 release sources and archive formats", () => {
  assert.match(source, /\("GE-Proton ARM64", "GloriousEggroll", "proton-ge-custom"/);
  assert.match(source, /\("CachyOS Proton ARM64", "CachyOS", "proton-cachyos"/);
  assert.match(source, /\("DroidDeck Proton ARM64", "Droid-Deck", "DroidDeck-Proton"/);
  assert.match(source, /aarch64\\\.tar/);
  assert.match(source, /arm64\\\.tar/);
  assert.match(source, /_safe_extract_tar/);
  assert.match(source, /toolmanifest\.vdf/);
  assert.match(source, /releases\.atom/);
  assert.match(source, /expanded_assets/);
  assert.match(source, /_release_page_assets/);
});

test("local update management follows Armada system and user component paths", () => {
  assert.match(source, /\/usr\/share\/armada\/turnip/);
  assert.match(source, /\.local\/share\/armada\/turnip/);
  assert.match(source, /\/usr\/share\/steam\/compatibilitytools\.d/);
  assert.match(source, /\.local\/share\/Steam\/compatibilitytools\.d/);
  assert.match(source, /USER_PREFIX = "user:"/);
});

test("HTTPS redirect handler rejects downgrade redirects", () => {
  const result = runPython(`
import urllib.error, urllib.request
handler = module._HttpsOnlyRedirectHandler()
request = urllib.request.Request("https://origin.test/file")
try:
    handler.redirect_request(request, None, 302, "Found", {}, "http://example.test/file")
except urllib.error.HTTPError:
    print("blocked")
else:
    raise AssertionError("HTTP redirect was allowed")
assert handler.redirect_request(request, None, 302, "Found", {}, "https://example.test/file") is not None
`);
  assert.match(result, /blocked/);
});

test("tar extraction accepts root directories and preserves safe executable bits", { skip: platform() === "win32" }, () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    runPython(`
import io, tarfile
archive, destination = sys.argv[2:]
with tarfile.open(archive, "w:gz") as bundle:
    root = tarfile.TarInfo("./")
    root.type = tarfile.DIRTYPE
    bundle.addfile(root)
    content = b"#!/bin/sh\\nexit 0\\n"
    entry = tarfile.TarInfo("Proton/proton")
    entry.size = len(content)
    entry.mode = 0o4755
    bundle.addfile(entry, io.BytesIO(content))
module._safe_extract_tar(archive, pathlib.Path(destination))
mode = (pathlib.Path(destination) / "Proton/proton").stat().st_mode & 0o7777
assert mode == 0o755, oct(mode)
`, [join(temp, "archive.tar.gz"), join(temp, "out")]);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("tar extraction rejects files at the extraction root", () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    const executable = process.platform === "win32" ? "python" : "python3";
    const result = spawnSync(executable, ["-c", python + `
import io, tarfile
archive, destination = sys.argv[2:]
with tarfile.open(archive, "w") as bundle:
    entry = tarfile.TarInfo(".")
    entry.size = 1
    bundle.addfile(entry, io.BytesIO(b"x"))
try:
    module._safe_extract_tar(archive, pathlib.Path(destination))
except module.UpdateError:
    pass
else:
    raise AssertionError("root file accepted")
`, fileURLToPath(new URL("../py_modules/armada_control/updates.py", import.meta.url)), join(temp, "archive.tar"), join(temp, "out")], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("zip extraction rejects targets that resolve to the extraction root", () => {
  const result = runPython(`
import pathlib, sys, zipfile
archive, destination = sys.argv[2:]
with zipfile.ZipFile(archive, "w") as bundle:
    bundle.writestr(".", "root")
try:
    module._safe_extract(archive, pathlib.Path(destination))
except module.UpdateError:
    pass
else:
    raise AssertionError("zip extraction root accepted as a file")
`, [join(tmpdir(), `armada-root-zip-${process.pid}.zip`), join(tmpdir(), `armada-root-zip-out-${process.pid}`)]);
  assert.equal(result, "");
});

test("user driver IDs map to removable filesystem IDs", { skip: platform() === "win32" }, () => {
  const result = runPython(`
import pathlib, sys, tempfile
module.HOME = pathlib.Path(sys.argv[2])
module.TURNIP_DIR = module.HOME / "turnip"
module.SYSTEM_TURNIP_DIR = module.HOME / "system-turnip"
module.HOME.mkdir(parents=True)
module.TURNIP_DIR.mkdir(parents=True)
module.os.geteuid = lambda: 1000
module.SYSTEM_TURNIP_DIR = pathlib.Path(sys.argv[2]) / "missing-system-turnip"
module._installed = lambda kind, item_id: (module.TURNIP_DIR / item_id.removeprefix(module.USER_PREFIX) / "libvulkan_freedreno.so").is_file()
driver = module.TURNIP_DIR / "custom-driver"
driver.mkdir()
(driver / "libvulkan_freedreno.so").write_text("driver")
items = module.local()
item = next(item for item in items if item["id"] == "user:custom-driver")
assert item["removable"]
assert module._installed("turnip", item["id"])
module.delete_local("turnip", item["id"])
assert not driver.exists()
`, [join(tmpdir(), `armada-user-driver-test-${process.pid}`)]);
  assert.equal(result, "");
});

test("safe store rejects symlinked parents", { skip: platform() === "win32" }, () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    runPython(`
import pathlib, sys
module.HOME = pathlib.Path(sys.argv[2]) / "home"
module.HOME.mkdir()
outside = pathlib.Path(sys.argv[2]) / "outside"
outside.mkdir()
(module.HOME / ".local").symlink_to(outside, target_is_directory=True)
try:
    module._safe_store(module.HOME / ".local/share/armada")
except module.UpdateError:
    pass
else:
    raise AssertionError("symlinked store accepted")
`, [temp]);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("publication restores an existing install when staging publication fails", { skip: platform() === "win32" }, () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    const result = runPython(`
import pathlib, unittest.mock
source, store = map(pathlib.Path, sys.argv[2:])
source.mkdir()
(source / "libvulkan_freedreno.so").write_text("new")
store.mkdir()
destination = store / "driver"
destination.mkdir()
(destination / "libvulkan_freedreno.so").write_text("old")
item = {"kind": "turnip"}
real_rename = pathlib.Path.rename
def fail_staging(self, target):
    if self.name == ".driver.tmp":
        raise OSError("simulated publication failure")
    return real_rename(self, target)
try:
    with unittest.mock.patch.object(pathlib.Path, "rename", fail_staging):
        module._publish_package(source, store, "driver", item)
except OSError:
    pass
else:
    raise AssertionError("publication failure not raised")
assert (destination / "libvulkan_freedreno.so").read_text() == "old"
`, [join(temp, "source"), join(temp, "store")]);
    assert.equal(result, "");
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});
