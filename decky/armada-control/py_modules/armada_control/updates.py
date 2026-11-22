import hashlib
import json
import os
import pathlib
import re
import shutil
import ssl
import importlib.util
import tarfile
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile


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


class UpdateError(RuntimeError):
    pass


def _safe_id(value):
    return isinstance(value, str) and value not in (".", "..") and bool(value) and len(value) <= 96 and all(
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
    return urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT)


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
    if kind == "turnip":
        return (TURNIP_DIR / item_id / "libvulkan_freedreno.so").is_file()
    return any(
        (root / item_id / marker).is_file()
        for root in (COMPAT_DIR, SYSTEM_COMPAT_DIR)
        for marker in ("compatibilitytool.vdf", "toolmanifest.vdf")
    )


def _local_item(kind, item_id, label, version, removable):
    return {
        "id": item_id,
        "kind": kind,
        "label": label,
        "version": version,
        "removable": removable,
    }


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
                items.append(_local_item("turnip", item_id, item_id, "", item_id != "stable"))
    compatibility_items = {}
    for compat_root in (SYSTEM_COMPAT_DIR, COMPAT_DIR):
      if compat_root.is_dir():
        for marker in sorted(compat_root.glob("*/*.vdf")):
            if marker.name not in ("compatibilitytool.vdf", "toolmanifest.vdf"):
                continue
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
            compatibility_items[item_id] = _local_item("compatibility", item_id, label, version, compat_root == COMPAT_DIR)
    items.extend(compatibility_items.values())
    return items


def delete_local(kind, item_id):
    if kind == "turnip" and isinstance(item_id, str) and item_id.startswith(USER_PREFIX):
        name = item_id[len(USER_PREFIX):]
        if not _safe_id(name):
            raise UpdateError("invalid driver id")
        target = TURNIP_DIR / name
    elif kind == "compatibility" and _safe_id(item_id):
        target = COMPAT_DIR / item_id
    else:
        raise UpdateError("the built-in driver cannot be removed")
    if not target.is_dir():
        raise UpdateError("driver is not installed")
    shutil.rmtree(target)
    return {"kind": kind, "id": item_id, "removed": True}


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
            if not str(target).startswith(str(root) + os.sep):
                raise UpdateError("update package contains an unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                link = bundle.read(entry).decode("utf-8", "strict")
                link_target = (target.parent / link).resolve()
                if not str(link_target).startswith(str(root) + os.sep):
                    raise UpdateError("update package contains an unsafe link")
                os.symlink(link, target)
                continue
            with bundle.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


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
            if not str(target).startswith(str(root) + os.sep):
                raise UpdateError("update package contains an unsafe path")
            if entry.isfile():
                count += 1
                total += entry.size
                if count > MAX_FILES or total > MAX_PACKAGE_BYTES:
                    raise UpdateError("update package is too large")
        for entry in entries:
            target = destination / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                source = bundle.extractfile(entry)
                if source is None:
                    raise UpdateError("invalid compatibility archive")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        for entry in entries:
            if not (entry.issym() or entry.islnk()):
                continue
            target = destination / entry.name
            link_value = pathlib.PurePosixPath(entry.linkname)
            link_target = (target.parent / pathlib.Path(*link_value.parts)).resolve() if entry.issym() else (destination / pathlib.Path(*link_value.parts)).resolve()
            if not str(link_target).startswith(str(root) + os.sep):
                raise UpdateError("update package contains an unsafe link")
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.issym():
                os.symlink(entry.linkname, target)
            else:
                if not link_target.is_file():
                    raise UpdateError("compatibility package contains an invalid hard link")
                os.link(link_target, target)


def _package_root(directory, kind):
    markers = ("libvulkan_freedreno.so",) if kind == "turnip" else ("compatibilitytool.vdf", "toolmanifest.vdf")
    for root, directories, files in os.walk(directory):
        if any(marker in files for marker in markers):
            return pathlib.Path(root)
    raise UpdateError("update package does not contain its required runtime manifest")


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
        _set_progress(item_id, "extracting", 92, total, expected)
        unpacked = pathlib.Path(work) / "unpacked"
        unpacked.mkdir()
        if item["kind"] == "compatibility" and tarfile.is_tarfile(archive):
            _safe_extract_tar(archive, unpacked)
        else:
            _safe_extract(archive, unpacked)
        source = _package_root(unpacked, item["kind"])
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
        destination_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        _set_progress(item_id, "installing", 97, total, expected)
        staging = destination_root / ("." + item["id"] + ".tmp")
        destination = destination_root / item["id"]
        package_name = source.name
        if item["kind"] == "compatibility":
            destination = destination_root / package_name
            if destination.exists() and not (destination / "toolmanifest.vdf").is_file() and not (destination / "compatibilitytool.vdf").is_file():
                raise UpdateError("refusing to replace an unrelated compatibility tool directory")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging, symlinks=True)
        backup = destination_root / ("." + item["id"] + ".old")
        shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            destination.rename(backup)
        staging.rename(destination)
        shutil.rmtree(backup, ignore_errors=True)
    _set_progress(item_id, "complete", 100, total, expected)
    return {"id": item["id"], "kind": item["kind"], "label": item["label"], "version": item["version"], "installed": True}
