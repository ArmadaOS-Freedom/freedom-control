import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager

try:
    import fcntl
except ImportError:
    fcntl = None


CATALOG_URL = os.environ.get("ARMADA_UPDATE_CATALOG_URL", "")
RELEASE_SOURCES = (
    ("Banners-Turnip", "The412Banner", "Banners-Turnip"),
    ("WinNative", "WinNative-Emu", "Drivers"),
)
PROTON_SOURCES = (
    ("GE-Proton ARM64", "GloriousEggroll", "proton-ge-custom", re.compile(r"aarch64\.tar\.(?:gz|xz)$", re.I)),
    ("CachyOS Proton ARM64", "CachyOS", "proton-cachyos", re.compile(r"arm64\.tar\.(?:gz|xz)$", re.I)),
    ("DroidDeck Proton ARM64", "Droid-Deck", "DroidDeck-Proton", re.compile(r"aarch64\.tar\.(?:gz|xz)$", re.I)),
)
HOME = pathlib.Path("/var/home/armada")
SYSTEM_TURNIP_DIR = pathlib.Path("/usr/share/armada/turnip")
TURNIP_DIR = HOME / ".local/share/armada/turnip"
SYSTEM_COMPAT_DIR = pathlib.Path("/usr/share/steam/compatibilitytools.d")
COMPAT_DIR = HOME / ".local/share/Steam/compatibilitytools.d"
USER_PREFIX = "user:"
MAX_CATALOG_BYTES = 512 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024 * 1024
MAX_FILES = 100000
_SSL_CONTEXT = None
_PROGRESS_LOCK = threading.Lock()
_PROGRESS = {}
_INSTALL_LOCKS_LOCK = threading.Lock()
_INSTALL_LOCKS = {}


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme.lower() != "https":
            raise urllib.error.HTTPError(new_url, code, "redirect to a non-HTTPS URL refused", headers, file_pointer)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


class UpdateError(RuntimeError):
    pass


def _safe_id(value):
    return isinstance(value, str) and value not in (".", "..") and not value.startswith(".__armada_control_update__") and bool(value) and len(value) <= 96 and all(
        c.isalnum() or c in "._-" for c in value
    )


def _urlopen(request, timeout):
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        cafile = None
        try:
            certifi = importlib.util.find_spec("certifi")
            if certifi and certifi.origin:
                bundle = pathlib.Path(certifi.origin).with_name("cacert.pem")
                if bundle.is_file():
                    cafile = str(bundle)
        except (ImportError, ValueError):
            pass
        _SSL_CONTEXT = ssl.create_default_context(cafile=cafile)
    opener = urllib.request.build_opener(
        _HttpsOnlyRedirectHandler,
        urllib.request.HTTPSHandler(context=_SSL_CONTEXT),
    )
    return opener.open(request, timeout=timeout)


def _ensure_safe_directory(path, create=False):
    path = pathlib.Path(os.path.abspath(path))
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                return False
            try:
                current.mkdir(mode=0o755)
            except FileExistsError:
                pass
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise UpdateError("update path contains a symlink or non-directory")
    return True


def _safe_store(path, create=False):
    home = pathlib.Path(os.path.abspath(HOME))
    path = pathlib.Path(os.path.abspath(path))
    if path == home or not path.is_relative_to(home):
        raise UpdateError("update store resolves outside the Armada home directory")
    _ensure_safe_directory(home, create=create)
    user_data = home / ".local/share"
    if path.is_relative_to(user_data):
        _ensure_safe_directory(home / ".local", create=create)
        _ensure_safe_directory(user_data, create=create)
    _ensure_safe_directory(path, create=create)
    return path


@contextmanager
def _installation_lock(path):
    store = pathlib.Path(path)
    process_lock = _INSTALL_LOCKS.setdefault(str(store), threading.Lock())
    with process_lock:
        if fcntl is None:
            yield
            return
        lock_path = store / ".armada-control-update.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _run_as_armada(code, *arguments):
    result = subprocess.run(
        ["/usr/bin/runuser", "-u", "armada", "--", "/usr/bin/python3", "-c", code, *map(str, arguments)],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        raise UpdateError(result.stderr.strip() or "package filesystem operation failed")
    return result.stdout.strip()


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Freedom-Control"})
    with _urlopen(request, timeout=20) as response:
        data = response.read(MAX_CATALOG_BYTES + 1)
    if len(data) > MAX_CATALOG_BYTES:
        raise UpdateError("update catalog is too large")
    return data


def _catalog(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise UpdateError("invalid update catalog") from error
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise UpdateError("unsupported update catalog")
    entries = value.get("updates")
    if not isinstance(entries, list):
        raise UpdateError("update catalog has no updates")
    clean = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind not in ("turnip", "compatibility") or not _safe_id(entry.get("id")):
            continue
        url = entry.get("url")
        sha256 = entry.get("sha256", "")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        if not isinstance(sha256, str) or (sha256 and (len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256))):
            continue
        clean.append({
            "id": entry["id"], "kind": kind,
            "label": str(entry.get("label") or entry["id"]),
            "version": str(entry.get("version") or ""),
            "description": str(entry.get("description") or ""),
            "url": url, "sha256": sha256.lower(), "verified": bool(sha256),
        })
    return {"schema": 1, "generatedAt": str(value.get("generatedAt") or ""), "updates": clean}


def _installed(kind, item_id):
    if kind == "turnip" and isinstance(item_id, str) and item_id.startswith(USER_PREFIX):
        item_id = item_id[len(USER_PREFIX):]
    if kind == "turnip":
        if not _safe_id(item_id):
            return False
        try:
            store = _safe_store(TURNIP_DIR)
        except (OSError, UpdateError):
            return False
        return (store / item_id / "libvulkan_freedreno.so").is_file()
    if not isinstance(item_id, str) or not _safe_id(item_id):
        return False
    for root in (COMPAT_DIR, SYSTEM_COMPAT_DIR):
        direct = root / item_id
        if any((direct / marker).is_file() for marker in ("compatibilitytool.vdf", "toolmanifest.vdf")):
            return True
        if root == COMPAT_DIR and root.is_dir():
            for package_dir in root.iterdir():
                try:
                    if _valid_install(package_dir, "compatibility") and _compat_catalog_id(package_dir) == item_id:
                        return True
                except (OSError, ValueError):
                    continue
    return False


def _local_item(kind, item_id, label, version, removable):
    return {
        "id": item_id,
        "kind": kind,
        "label": label,
        "version": version,
        "removable": removable,
    }


def _compat_catalog_id(directory):
    try:
        value = json.loads((directory / ".armada-control-update.json").read_text(encoding="utf-8"))
        catalog_id = value.get("catalogId") if isinstance(value, dict) else None
        return catalog_id if _safe_id(catalog_id) else directory.name
    except (OSError, ValueError):
        return directory.name


def _compat_tool_id(directory):
    for filename in ("compatibilitytool.vdf", "toolmanifest.vdf"):
        try:
            text = (directory / filename).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r'"compat_tools"\s*\{\s*"([^"\r\n]+)"\s*\{', text, re.I)
        if match and _safe_id(match.group(1)):
            return match.group(1)
    return directory.name


def local():
    items = []
    if SYSTEM_TURNIP_DIR.is_dir():
        for variant in sorted(SYSTEM_TURNIP_DIR.glob("*/variant.json")):
            item_id = variant.parent.name
            try:
                info = json.loads(variant.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            if item_id == "stable" or (variant.parent / "icd.aarch64.json").is_file():
                items.append(_local_item("turnip", item_id, str(info.get("label") or item_id), str(info.get("version") or ""), False))
    for library in sorted(TURNIP_DIR.glob("*/libvulkan_freedreno.so")):
        item_id = library.parent.name
        if not any(item["id"] == item_id for item in items) and _installed("turnip", item_id):
            items.append(_local_item("turnip", USER_PREFIX + item_id, item_id, "", item_id != "stable"))
    seen_compat_installations = set()
    for compat_root in (SYSTEM_COMPAT_DIR, COMPAT_DIR):
        if compat_root.is_dir():
            for marker in sorted(compat_root.glob("*/*.vdf")):
                if marker.name not in ("compatibilitytool.vdf", "toolmanifest.vdf"):
                    continue
                installation_identity = (compat_root, marker.parent.name)
                if installation_identity in seen_compat_installations:
                    continue
                seen_compat_installations.add(installation_identity)
                item_id = marker.parent.name
                if not _safe_id(item_id):
                    continue
                label = item_id
                version = ""
                try:
                    text = marker.read_text(encoding="utf-8", errors="replace")
                    for line in text.splitlines():
                        if '"display_name"' in line:
                            label = line.split('"')[-2] or label
                            break
                except OSError:
                    pass
                catalog_id = _compat_catalog_id(marker.parent) if compat_root == COMPAT_DIR else item_id
                item = _local_item("compatibility", catalog_id, label, version, compat_root == COMPAT_DIR)
                item["installedId"] = item_id
                item["selectionId"] = _compat_tool_id(marker.parent)
                item["storeId"] = "user" if compat_root == COMPAT_DIR else "system"
                items.append(item)
    return items


def delete_local(kind, item_id, installed_id=None):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        code = "import importlib.util,json,sys; s=importlib.util.spec_from_file_location('updates',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m.delete_local(sys.argv[2],sys.argv[3],sys.argv[4] or None)))"
        return json.loads(_run_as_armada(code, __file__, kind, item_id, installed_id or ""))
    selected_id = item_id
    if kind == "turnip" and isinstance(item_id, str) and item_id.startswith(USER_PREFIX):
        name = item_id[len(USER_PREFIX):]
        if not _safe_id(name):
            raise UpdateError("invalid driver id")
        store_path = TURNIP_DIR
    elif kind == "compatibility" and _safe_id(installed_id or item_id):
        name = installed_id or item_id
        store_path = COMPAT_DIR
    else:
        raise UpdateError("the built-in driver cannot be removed")
    store = _safe_store(store_path)
    target = store / name
    if kind == "compatibility" and installed_id is None and not target.exists():
        target = next((candidate for candidate in store.iterdir() if _compat_catalog_id(candidate) == item_id), target)
        name = target.name
    if target.is_symlink() or not target.is_dir() or not target.resolve().is_relative_to(store):
        raise UpdateError("package is not installed in its expected store")
    if not _safe_id(name):
        raise UpdateError("invalid installed package directory name")
    if kind == "compatibility" and _compat_catalog_id(target) != item_id:
        raise UpdateError("installed compatibility package does not match the selected catalog entry")
    with _installation_lock(store):
        if kind == "compatibility":
            try:
                selected_id = _compat_catalog_id(target)
            except (OSError, ValueError):
                selected_id = item_id
        shutil.rmtree(target)
    return {"kind": kind, "id": item_id, "selectionId": selected_id, "installedId": name, "removed": True}


def local_selection_id(kind, item_id, installed_id=None):
    if kind == "turnip" and isinstance(item_id, str) and item_id.startswith(USER_PREFIX):
        return item_id[len(USER_PREFIX):]
    if kind == "compatibility" and _safe_id(item_id):
        store = _safe_store(COMPAT_DIR)
        if installed_id and _safe_id(installed_id):
            target = store / installed_id
            if target.is_dir() and not target.is_symlink() and target.resolve().is_relative_to(store):
                return _compat_tool_id(target)
        direct = store / item_id
        if direct.is_dir() and not direct.is_symlink():
            return _compat_tool_id(direct)
        for candidate in store.iterdir():
            if candidate.is_dir() and not candidate.is_symlink() and _compat_catalog_id(candidate) == item_id:
                return _compat_tool_id(candidate)
    return item_id


def _state(catalog):
    return [dict(item, installed=_installed(item["kind"], item["id"])) for item in catalog["updates"]]


def _dedupe_updates(updates):
    unique = []
    seen = set()
    for item in updates:
        digest = str(item.get("sha256") or "").lower()
        url = item.get("url", "")
        identity = (item["kind"], "sha256", digest) if digest else (item["kind"], "url", url)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def progress(item_id):
    with _PROGRESS_LOCK:
        return dict(_PROGRESS.get(item_id, {"id": item_id, "phase": "idle", "percent": 0, "downloaded": 0, "total": 0}))


def _set_progress(item_id, phase, percent, downloaded=0, total=0):
    with _PROGRESS_LOCK:
        _PROGRESS[item_id] = {
            "id": item_id, "phase": phase, "percent": percent,
            "downloaded": downloaded, "total": total,
        }


def refresh():
    if CATALOG_URL:
        catalog = _catalog(_fetch(CATALOG_URL))
    else:
        catalog = _release_catalog()
    return {"ok": True, "source": CATALOG_URL, "generatedAt": catalog["generatedAt"], "updates": _state(catalog)}


def current():
    try:
        return refresh()
    except Exception as error:
        return {"ok": False, "source": CATALOG_URL, "error": str(error), "updates": []}


def _release_catalog():
    updates = []
    failures = []
    for source, owner, repo in RELEASE_SOURCES:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=10"
        try:
            releases = json.loads(_fetch_url(url, 2 * 1024 * 1024))
            for release in releases:
                if release.get("draft"):
                    continue
                tag = str(release.get("tag_name") or "")
                for asset in release.get("assets") or []:
                    name = str(asset.get("name") or "")
                    if not name.lower().endswith(".zip") or "-linux" not in name.lower():
                        continue
                    if "-wayland" in name.lower():
                        continue
                    sha = str(asset.get("digest") or "")
                    if sha.startswith("sha256:"):
                        sha = sha[7:]
                    item_id = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{source}-{name[:-4]}").strip("-")
                    updates.append({
                        "id": item_id, "kind": "turnip",
                        "label": f"{source} · {name[:-4]}", "version": tag,
                        "description": "Linux Vulkan driver from the DroidDeck-compatible release source.",
                        "url": str(asset.get("browser_download_url") or ""),
                        "sha256": sha, "verified": bool(sha),
                    })
            if updates:
                break
        except Exception as error:
            failures.append(f"{source}: {error}")
            try:
                assets = _release_page_assets(owner, repo, lambda name: name.lower().endswith(".zip") and "-linux" in name.lower() and "-wayland" not in name.lower())
                for tag, name, asset_url in assets:
                    item_id = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{source}-{name[:-4]}").strip("-")
                    updates.append({
                        "id": item_id, "kind": "turnip", "label": f"{source} · {name[:-4]}",
                        "version": tag, "description": "Linux Vulkan driver from the DroidDeck-compatible release source.",
                        "url": asset_url, "sha256": "", "verified": False,
                    })
                if assets:
                    break
            except Exception as fallback_error:
                failures.append(f"{source} HTML: {fallback_error}")
    proton_updates, proton_failures = _proton_release_catalog()
    updates.extend(proton_updates)
    failures.extend(proton_failures)
    if not updates and failures:
        raise UpdateError("; ".join(failures))
    return {"schema": 1, "generatedAt": "", "updates": _dedupe_updates(updates)}


def _proton_release_catalog():
    updates = []
    failures = []
    for label, owner, repo, asset_pattern in PROTON_SOURCES:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=15"
        try:
            releases = json.loads(_fetch_url(url, 2 * 1024 * 1024))
            for release in releases:
                if release.get("draft") or release.get("prerelease"):
                    continue
                tag = str(release.get("tag_name") or "")
                for asset in release.get("assets") or []:
                    name = str(asset.get("name") or "")
                    if not asset_pattern.search(name):
                        continue
                    sha = str(asset.get("digest") or "")
                    if sha.startswith("sha256:"):
                        sha = sha[7:]
                    package_id = re.sub(r"[^A-Za-z0-9._-]+", "-", re.sub(r"\.tar\.(?:gz|xz)$", "", name, flags=re.I)).strip("-")
                    updates.append({
                        "id": package_id, "kind": "compatibility",
                        "label": f"{label} · {name.rsplit('.tar', 1)[0]}", "version": tag,
                        "description": f"ARM64 Proton compatibility tool from {owner}/{repo}.",
                        "url": str(asset.get("browser_download_url") or ""),
                        "sha256": sha, "verified": bool(sha),
                    })
        except Exception as error:
            try:
                assets = _release_page_assets(owner, repo, asset_pattern.search)
                for tag, name, asset_url in assets:
                    package_id = re.sub(r"[^A-Za-z0-9._-]+", "-", re.sub(r"\.tar\.(?:gz|xz)$", "", name, flags=re.I)).strip("-")
                    updates.append({
                        "id": package_id, "kind": "compatibility",
                        "label": f"{label} · {name.rsplit('.tar', 1)[0]}", "version": tag,
                        "description": f"ARM64 Proton compatibility tool from {owner}/{repo}.",
                        "url": asset_url, "sha256": "", "verified": False,
                    })
                if not assets:
                    failures.append(f"{label}: API failed ({error}); release page had no ARM64 archives")
            except Exception as fallback_error:
                failures.append(f"{label}: API failed ({error}); release page failed ({fallback_error})")
    return updates, failures


def _release_page_assets(owner, repo, matches):
    feed = _fetch_url(f"https://github.com/{owner}/{repo}/releases.atom", 2 * 1024 * 1024).decode("utf-8", "replace")
    results = []
    tags = re.findall(r"<id>tag:github\.com,2008:Repository/[^<]+?/releases/([^<]+)</id>", feed)[:3]
    if not tags:
        tags = re.findall(r"<link[^>]+href=[\"']https://github\.com/" + re.escape(owner) + "/" + re.escape(repo) + r"/releases/tag/([^\"']+)[\"']", feed)[:3]
    for tag in tags:
        assets_page = _fetch_url(f"https://github.com/{owner}/{repo}/releases/expanded_assets/{tag}", 2 * 1024 * 1024).decode("utf-8", "replace")
        for asset_url in re.findall(r'href=["\']([^"\']+/releases/download/[^"\']+)["\']', assets_page):
            name = urllib.parse.unquote(asset_url.rsplit("/", 1)[-1])
            if matches(name):
                url = "https://github.com" + asset_url if asset_url.startswith("/") else asset_url
                results.append((tag, name, url))
    return results


def _fetch_url(url, limit):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Freedom-Control",
        "Accept": "application/vnd.github+json",
    })
    with _urlopen(request, timeout=10) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("remote update response is too large")
    return data


def _safe_extract(archive, destination):
    total = 0
    count = 0
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if entry.is_dir():
                continue
            count += 1
            total += entry.file_size
            if count > MAX_FILES or total > MAX_PACKAGE_BYTES:
                raise UpdateError("update package is too large")
            target = (destination / entry.filename).resolve()
            if target == root or not target.is_relative_to(root):
                raise UpdateError("update package contains an unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                link = bundle.read(entry).decode("utf-8", "strict")
                link_target = (target.parent / link).resolve()
                if not link_target.is_relative_to(root):
                    raise UpdateError("update package contains an unsafe link")
                os.symlink(link, target)
                continue
            with bundle.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            mode = (entry.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode & ~0o6000)


def _safe_extract_tar(archive, destination):
    total = 0
    count = 0
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as bundle:
        entries = bundle.getmembers()
        for entry in entries:
            if not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):
                raise UpdateError("compatibility package contains an unsafe link or special file")
            target = (destination / entry.name).resolve()
            if target == root:
                if not entry.isdir():
                    raise UpdateError("update package contains an unsafe path")
            elif not target.is_relative_to(root):
                raise UpdateError("update package contains an unsafe path")
            if entry.isfile():
                count += 1
                total += entry.size
                if count > MAX_FILES or total > MAX_PACKAGE_BYTES:
                    raise UpdateError("update package is too large")
        directory_modes = []
        for entry in entries:
            target = destination / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                if target != destination:
                    directory_modes.append((target, entry.mode & 0o777 & ~0o6000))
            elif entry.isfile():
                source = bundle.extractfile(entry)
                if source is None:
                    raise UpdateError("invalid compatibility archive")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod(entry.mode & 0o777 & ~0o6000)
        for entry in entries:
            if not (entry.issym() or entry.islnk()):
                continue
            target = destination / entry.name
            link_value = pathlib.PurePosixPath(entry.linkname)
            link_target = (target.parent / pathlib.Path(*link_value.parts)).resolve() if entry.issym() else (destination / pathlib.Path(*link_value.parts)).resolve()
            if not link_target.is_relative_to(root):
                raise UpdateError("update package contains an unsafe link")
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.issym():
                os.symlink(entry.linkname, target)
            else:
                if not link_target.is_file():
                    raise UpdateError("compatibility package contains an invalid hard link")
                os.link(link_target, target)
        for directory, mode in reversed(directory_modes):
            directory.chmod(mode)


def _validate_package_links(source):
    root = source.resolve()
    for directory, directories, files in os.walk(source, followlinks=False):
        for name in directories + files:
            path = pathlib.Path(directory) / name
            if not path.is_symlink():
                continue
            if os.path.isabs(os.readlink(path)):
                raise UpdateError("compatibility package contains an absolute link")
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise UpdateError("compatibility package contains a link outside its installed directory")


def _package_root(directory, kind):
    markers = ("libvulkan_freedreno.so",) if kind == "turnip" else ("compatibilitytool.vdf", "toolmanifest.vdf")
    for root, directories, files in os.walk(directory):
        if any(marker in files for marker in markers):
            return pathlib.Path(root)
    raise UpdateError("update package does not contain its required runtime manifest")


def _publish_package(source, store_path, package_id, item):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        handoff = pathlib.Path(tempfile.mkdtemp(prefix="armada-update-handoff-"))
        os.chmod(handoff, 0o711)
        try:
            handoff_source = handoff / source.name
            shutil.copytree(source, handoff_source, symlinks=True)
            code = "import importlib.util,json,pathlib,sys; s=importlib.util.spec_from_file_location('updates',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m._publish_package(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),sys.argv[4],json.loads(sys.argv[5]))))"
            output = _run_as_armada(code, __file__, handoff_source, store_path, package_id, json.dumps(item))
            return json.loads(output)
        finally:
            shutil.rmtree(handoff, ignore_errors=True)
    store = _safe_store(store_path, create=True)
    package_name = source.name if item["kind"] == "compatibility" else package_id
    if not _safe_id(package_name):
        raise UpdateError("package directory name is invalid")
    with _installation_lock(store):
        destination = store / package_name
        transaction = ".__armada_control_update__" + hashlib.sha256(package_name.encode()).hexdigest()[:20]
        staging = store / (transaction + ".tmp")
        backup = store / (transaction + ".old")
        if destination.is_symlink():
            raise UpdateError("refusing to replace a symlinked package")
        if destination.exists() and item["kind"] == "compatibility" and not any(
            (destination / marker).is_file() for marker in ("toolmanifest.vdf", "compatibilitytool.vdf")
        ):
            raise UpdateError("refusing to replace an unrelated compatibility tool directory")
        if backup.exists() and not _valid_install(destination, item["kind"]):
            if destination.exists():
                shutil.rmtree(destination)
            backup.rename(destination)
        elif backup.exists():
            shutil.rmtree(backup)
        shutil.rmtree(staging, ignore_errors=True)
        if item["kind"] == "compatibility":
            _validate_package_links(source)
        shutil.copytree(source, staging, symlinks=True)
        if item["kind"] == "compatibility":
            (staging / ".armada-control-update.json").write_text(
                json.dumps({"catalogId": package_id}) + "\n", encoding="utf-8"
            )
        moved_old = False
        try:
            if destination.exists():
                destination.rename(backup)
                moved_old = True
            staging.rename(destination)
        except Exception:
            if moved_old and backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    return package_name


def _valid_install(path, kind):
    if path.is_symlink() or not path.is_dir():
        return False
    markers = ("libvulkan_freedreno.so",) if kind == "turnip" else ("compatibilitytool.vdf", "toolmanifest.vdf")
    return any((path / marker).is_file() for marker in markers)


def _install_archive(archive, item):
    with tempfile.TemporaryDirectory(prefix="armada-update-", dir=HOME) as work:
        unpacked = pathlib.Path(work) / "unpacked"
        unpacked.mkdir()
        if item["kind"] == "compatibility" and tarfile.is_tarfile(archive):
            _safe_extract_tar(archive, unpacked)
        else:
            _safe_extract(archive, unpacked)
        source = _package_root(unpacked, item["kind"])
        if item["kind"] == "compatibility":
            _validate_package_links(source)
        if item["kind"] == "turnip":
            if not (source / "libvulkan_freedreno.so").is_file():
                raise UpdateError("Turnip package is missing its runtime files")
            if not (source / "variant.json").is_file():
                meta = {}
                try:
                    meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                (source / "variant.json").write_text(json.dumps({
                    "label": item["label"],
                    "version": item["version"] or str(meta.get("version") or ""),
                }) + "\n", encoding="utf-8")
            destination_root = TURNIP_DIR
        else:
            if not ((source / "compatibilitytool.vdf").is_file() or (source / "toolmanifest.vdf").is_file()):
                raise UpdateError("compatibility package is missing its Steam tool manifest")
            destination_root = COMPAT_DIR
        package_name = _publish_package(source, destination_root, item["id"], item)
        return {"id": item["id"], "installedId": package_name, "selectionId": _compat_tool_id(destination_root / package_name) if item["kind"] == "compatibility" else package_name, "kind": item["kind"], "label": item["label"], "version": item["version"], "installed": True}


def install(item_id):
    _set_progress(item_id, "checking", 0)
    try:
        return _install(item_id)
    except Exception as error:
        _set_progress(item_id, "error", 0)
        raise error


def _install(item_id):
    catalog = _catalog(_fetch(CATALOG_URL)) if CATALOG_URL else _release_catalog()
    item = next((entry for entry in catalog["updates"] if entry["id"] == item_id), None)
    if item is None:
        raise UpdateError("update is not in the catalog")
    _set_progress(item_id, "downloading", 1)
    with tempfile.TemporaryDirectory(prefix="armada-update-") as work:
        archive = pathlib.Path(work) / "package.zip"
        request = urllib.request.Request(item["url"], headers={"User-Agent": "Freedom-Control"})
        with _urlopen(request, timeout=60) as response, archive.open("wb") as output:
            digest = hashlib.sha256()
            total = 0
            expected = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise UpdateError("update package is too large")
                digest.update(chunk)
                output.write(chunk)
                percent = min(89, int(total * 90 / expected)) if expected else 5
                _set_progress(item_id, "downloading", max(1, percent), total, expected)
        if item.get("sha256") and digest.hexdigest().lower() != item["sha256"]:
            raise UpdateError("update checksum mismatch")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            handoff = pathlib.Path(tempfile.mkdtemp(prefix="armada-update-handoff-"))
            os.chmod(handoff, 0o755)
            try:
                user_archive = handoff / "package.zip"
                shutil.copyfile(archive, user_archive)
                user_archive.chmod(0o644)
                code = "import importlib.util,json,pathlib,sys; s=importlib.util.spec_from_file_location('updates',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m._install_archive(pathlib.Path(sys.argv[2]),json.loads(sys.argv[3]))))"
                result = json.loads(_run_as_armada(code, __file__, user_archive, json.dumps(item)))
            finally:
                shutil.rmtree(handoff, ignore_errors=True)
            _set_progress(item_id, "complete", 100, total, expected)
            return result
        _set_progress(item_id, "extracting", 92, total, expected)
        _set_progress(item_id, "installing", 97, total, expected)
        result = _install_archive(archive, item)
    _set_progress(item_id, "complete", 100, total, expected)
    return result
