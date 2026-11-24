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
const pythonExecutable = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const runProcess = (args) => {
  const result = spawnSync(pythonExecutable, args, { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.signal) throw new Error(`Python process terminated by ${result.signal}`);
  return result;
};
const runPython = (code, args = []) => {
  const result = runProcess(["-c", python + code, fileURLToPath(new URL("../py_modules/armada_control/updates.py", import.meta.url)), ...args]);
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

test("compatibility identity and driver-store discovery cover installed edge cases", () => {
  assert.match(source, /def _compat_tool_id\(/);
  assert.match(source, /isinstance\(value, dict\)/);
  assert.match(source, /for library in sorted\(TURNIP_DIR\.glob/);
  assert.match(source, /installed_id=None/);
  assert.match(source, /__armada_control_update__/);
  assert.match(source, /def _validate_package_links\(/);
});

test("compatibility metadata falls back safely and user drivers list without system drivers", () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    runPython(`
import json, pathlib, sys
root = pathlib.Path(sys.argv[2])
module.HOME = root / "home"
module.SYSTEM_TURNIP_DIR = root / "missing-system-turnip"
module.TURNIP_DIR = module.HOME / ".local/share/armada/turnip"
module.SYSTEM_COMPAT_DIR = root / "missing-system-compat"
module.COMPAT_DIR = module.HOME / ".local/share/Steam/compatibilitytools.d"
package = module.TURNIP_DIR / "custom-driver"
package.mkdir(parents=True)
(package / "libvulkan_freedreno.so").write_text("driver")
items = module.local()
assert any(item["id"] == "user:custom-driver" for item in items), items
compat = module.COMPAT_DIR / "Proton-A"
compat.mkdir(parents=True)
(compat / "compatibilitytool.vdf").write_text('"compatibilitytools" { "compat_tools" { "Steam-Key" { "display_name" "Custom" } } }')
metadata = compat / ".armada-control-update.json"
metadata.write_text("[]")
assert module._compat_catalog_id(compat) == "Proton-A"
assert module._compat_tool_id(compat) == "Steam-Key"
rows = [item for item in module.local() if item["kind"] == "compatibility"]
assert rows == [{"id": "Proton-A", "kind": "compatibility", "label": "Custom", "version": "", "removable": True, "installedId": "Proton-A", "selectionId": "Steam-Key"}], rows
`, [temp]);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("compatibility listing preserves duplicate catalog entries by installation", () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    runPython(`
import json, pathlib, sys
root = pathlib.Path(sys.argv[2])
module.HOME = root / "home"
module.TURNIP_DIR = module.HOME / "missing-turnip"
module.SYSTEM_TURNIP_DIR = root / "missing-system-turnip"
module.SYSTEM_COMPAT_DIR = root / "missing-system-compat"
module.COMPAT_DIR = module.HOME / ".local/share/Steam/compatibilitytools.d"
for name in ("Proton-A", "Proton-B"):
    package = module.COMPAT_DIR / name
    package.mkdir(parents=True)
    (package / "compatibilitytool.vdf").write_text('"compat_tools" { "Shared-Steam-Key" { "display_name" "Shared" } }')
    (package / ".armada-control-update.json").write_text(json.dumps({"catalogId": "shared-catalog"}))
items = [item for item in module.local() if item["kind"] == "compatibility"]
assert [item["installedId"] for item in items] == ["Proton-A", "Proton-B"], items
`, [temp]);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("compatibility publication rejects links outside its installed subtree", { skip: platform() === "win32" }, () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
    runPython(`
import pathlib, sys
root = pathlib.Path(sys.argv[2])
package = root / "unpacked" / "Proton"
package.mkdir(parents=True)
(package / "compatibilitytool.vdf").write_text("manifest")
(root / "unpacked" / "Sibling").mkdir()
(root / "unpacked" / "Sibling" / "outside").write_text("outside")
(package / "escape").symlink_to(root / "unpacked" / "Sibling" / "outside")
try:
    module._validate_package_links(package)
except module.UpdateError:
    pass
else:
    raise AssertionError("link escaping the installed package was accepted")
`, [temp]);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
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
    root.mode = 0o755
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
    const result = runProcess(["-c", python + `
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
`, fileURLToPath(new URL("../py_modules/armada_control/updates.py", import.meta.url)), join(temp, "archive.tar"), join(temp, "out")]);
    assert.equal(result.status, 0, result.stderr);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});

test("zip extraction rejects targets that resolve to the extraction root", () => {
  const temp = mkdtempSync(join(tmpdir(), "armada-update-test-"));
  try {
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
    `, [join(temp, "archive.zip"), join(temp, "out")]);
    assert.equal(result, "");
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
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
module.HOME = store.parent
module.os.geteuid = lambda: 1000
source.mkdir()
(source / "libvulkan_freedreno.so").write_text("new")
store.mkdir()
destination = store / "driver"
destination.mkdir()
(destination / "libvulkan_freedreno.so").write_text("old")
item = {"kind": "turnip"}
real_rename = pathlib.Path.rename
def fail_staging(self, target):
    if self.name.endswith(".tmp"):
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
