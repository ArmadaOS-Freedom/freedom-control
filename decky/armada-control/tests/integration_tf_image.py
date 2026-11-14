#!/usr/bin/env python3
"""Standalone, image-only integration proof for the TF reserve conversion.

This script is meant to be run inside the remote test container.  It creates a
*regular* sparse image file (never ``/dev``, never a loop device, never a host
mount), lays down a native three-partition mixed GPT, writes known Android
sentinels, and then exercises the production contract end to end at the image
level:

* ``tf_card.validate_handoff`` / ``tf_card.validate_layout`` must accept the
  fabricated layout against the card identity we independently chose;
* the exact production typecode change ``sgdisk --typecode=1:8300 <disk>`` must
  retype only ``p1`` and leave every Android GUID, geometry and byte untouched;
* only the reserve region is formatted, with an explicit offset and an explicit
  block count plus ``nodiscard`` (so nothing outside ``p1`` is ever written);
* the GPT still verifies cleanly (``sgdisk --verify``), its disk GUID and
  geometry are unchanged, and the Android entries keep their exact metadata --
  only the ``p1`` type code legitimately differs;
* the reserve is a real ext4 filesystem with the ``ARMADA_TF_<cid6>`` label, and
  a streaming SHA256 over *every* byte of the Android ``p2``/``p3`` regions is
  byte-for-byte unchanged.

It prints a JSON proof document to stdout and exits non-zero on any difference.
It proves image-level invariants, not behaviour on a real SD card.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from armada_control.tf_library import tf_card  # noqa: E402

MIB = 1024 * 1024
SECTOR = 512
DISK_BYTES = 512 * MIB
FIRST_LBA = 2048
LAST_LBA = DISK_BYTES // SECTOR - 34
RESERVE_BYTES = 256 * MIB
META_BYTES = 16 * MIB
CID = "1b2c3d4e5f60718293a4b5c6d7e8f901"
RESERVE_PARTUUID = "11111111-2222-3333-4444-555555555555"
META_PARTUUID = "22222222-3333-4444-5555-666666666666"
PRIVATE_PARTUUID = "99999999-8888-7777-6666-555555555555"
PRIVATE_FS_UUID = "abcdef01-2345-6789-abcd-ef0123456789"
DIGEST_CHUNK = 1024 * 1024


def _run(argv, env=None):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _sectors():
    reserve = RESERVE_BYTES // SECTOR
    meta = META_BYTES // SECTOR
    meta_start = FIRST_LBA + reserve
    private_start = meta_start + meta
    private = LAST_LBA - private_start + 1
    return reserve, meta_start, meta, private_start, private


def _record_part(part):
    return {"start": part.get("start"), "size": part.get("size"),
            "type": str(part.get("type", "")).lower(),
            "uuid": str(part.get("uuid") or "").replace("-", "").lower()}


def _sfdisk(image):
    result = _run(["sfdisk", "--json", str(image)])
    if result.returncode:
        raise RuntimeError("sfdisk failed: %s" % (result.stderr or result.stdout))
    return json.loads(result.stdout)["partitiontable"]


def _region_digest(stream, offset, size):
    """SHA256 over every byte of ``[offset, offset + size)``.

    The whole region is streamed in bounded chunks, so a mutation anywhere
    inside it changes the digest; nothing is sampled.  A short read means the
    image is smaller than the layout claims, which is a hard failure instead of
    a digest silently covering fewer bytes than promised.
    """
    digest = hashlib.sha256()
    stream.seek(offset)
    remaining = size
    while remaining > 0:
        block = stream.read(min(DIGEST_CHUNK, remaining))
        if not block:
            raise RuntimeError("premature EOF hashing %d bytes at offset %d" % (size, offset))
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def _build_image(image):
    reserve, meta_start, meta, private_start, private = _sectors()
    with image.open("wb") as stream:
        stream.truncate(DISK_BYTES)
    steps = [
        ["sgdisk", "--clear", str(image)],
        ["sgdisk", "--new=1:%d:%d" % (FIRST_LBA, FIRST_LBA + reserve - 1),
         "--typecode=1:%s" % tf_card.RESERVE_PENDING,
         "--partition-guid=1:%s" % RESERVE_PARTUUID, str(image)],
        ["sgdisk", "--new=2:%d:%d" % (meta_start, meta_start + meta - 1),
         "--typecode=2:%s" % tf_card.ANDROID_META,
         "--partition-guid=2:%s" % META_PARTUUID, str(image)],
        ["sgdisk", "--new=3:%d:%d" % (private_start, LAST_LBA),
         "--typecode=3:%s" % tf_card.ANDROID_PRIVATE,
         "--partition-guid=3:%s" % PRIVATE_PARTUUID, str(image)],
    ]
    for argv in steps:
        result = _run(argv)
        if result.returncode:
            raise RuntimeError("sgdisk failed: %s\n%s" % (" ".join(argv), result.stderr or result.stdout))
    # Known Android sentinels inside the metadata and private regions.
    with image.open("r+b") as stream:
        for offset, size, tag in ((meta_start * SECTOR, META_BYTES, b"META"),
                                  (private_start * SECTOR, private * SECTOR, b"PRIV")):
            stream.seek(offset)
            stream.write(tag + b"-SENTINEL-" * 8)
            stream.seek(offset + size - 32)
            stream.write(b"-" * 16 + tag + b"-TAIL")
    return image


def _checks(image):
    reserve, meta_start, meta, private_start, private = _sectors()
    reserve_offset = FIRST_LBA * SECTOR
    proof = []
    with image.open("rb") as stream:
        meta_before = _region_digest(stream, meta_start * SECTOR, META_BYTES)
        private_before = _region_digest(stream, private_start * SECTOR, private * SECTOR)
    table_before = _sfdisk(image)
    parts_before = [_record_part(part) for part in table_before["partitions"]]

    # The production domain contract must accept the fabricated layout.
    handoff = tf_card.validate_handoff({
        "magic": "armada-tf-handoff", "schema_version": 1, "card_cid": CID,
        "disk_bytes": DISK_BYTES, "linux_ratio": 50, "public_fs_uuid": "1A2B-3C4D",
        "public_partition_guid": RESERVE_PARTUUID, "private_partition_guid": PRIVATE_PARTUUID,
        "private_fs_uuid": PRIVATE_FS_UUID})
    tf_card.validate_layout(table_before, handoff, CID, DISK_BYTES)
    proof.append({"name": "production_layout_accepted", "ok": True})

    # The exact production typecode change, on the image file.
    changed = _run(["sgdisk", "--typecode=1:8300", str(image)])
    proof.append({"name": "typecode_change_ok", "ok": changed.returncode == 0,
                  "detail": "" if changed.returncode == 0 else (changed.stderr or "").strip()})

    # Format only the reserve region: explicit offset, explicit block count, nodiscard.
    label = "ARMADA_TF_%s" % CID[-6:]
    blocks = RESERVE_BYTES // 4096
    fmt = _run(["mkfs.ext4", "-F", "-E", "nodiscard,offset=%d" % reserve_offset,
                "-b", "4096", "-L", label, str(image), str(blocks)])
    proof.append({"name": "format_reserve_ok", "ok": fmt.returncode == 0,
                  "detail": "" if fmt.returncode == 0 else (fmt.stderr or "").strip()})

    table_after = _sfdisk(image)
    parts_after = [_record_part(part) for part in table_after["partitions"]]

    # Only the p1 type code may differ, so the GPT headers and CRC change by
    # design; the table itself must still verify and keep its own identity.
    verify = _run(["sgdisk", "--verify", str(image)], env=dict(os.environ, LC_ALL="C"))
    verify_ok = verify.returncode == 0 and "No problems found" in (verify.stdout or "")
    proof.append({"name": "gpt_verify_clean", "ok": verify_ok,
                  "detail": "" if verify_ok else ((verify.stdout or "") + (verify.stderr or "")).strip()})
    geometry_keys = ("label", "sectorsize", "firstlba", "lastlba", "id")
    geometry_unchanged = all(table_before.get(key) == table_after.get(key) for key in geometry_keys)
    proof.append({"name": "disk_geometry_and_guid_unchanged", "ok": geometry_unchanged,
                  "before": {key: table_before.get(key) for key in geometry_keys},
                  "after": {key: table_after.get(key) for key in geometry_keys}})

    android_unchanged = parts_before[1:] == parts_after[1:]
    proof.append({"name": "android_guid_geometry_unchanged", "ok": android_unchanged,
                  "before": parts_before[1:], "after": parts_after[1:]})
    p1 = parts_after[0]
    p1_ok = (p1["start"] == FIRST_LBA and p1["size"] == RESERVE_BYTES // SECTOR
             and p1["type"] == tf_card.RESERVE_CONNECTED
             and p1["uuid"] == RESERVE_PARTUUID.replace("-", ""))
    proof.append({"name": "reserve_p1_retyped_and_preserved", "ok": p1_ok, "p1": p1})

    with image.open("rb") as stream:
        meta_after = _region_digest(stream, meta_start * SECTOR, META_BYTES)
        private_after = _region_digest(stream, private_start * SECTOR, private * SECTOR)
        stream.seek(reserve_offset + 0x438)          # ext4 superblock magic
        magic = stream.read(2)
        stream.seek(reserve_offset + 0x478)          # ext4 volume label
        written_label = stream.read(16).split(b"\x00", 1)[0].decode("ascii", "replace")
    proof.append({"name": "android_bytes_unchanged",
                  "ok": meta_before == meta_after and private_before == private_after,
                  "sha256": {"p2_before": meta_before, "p2_after": meta_after,
                             "p3_before": private_before, "p3_after": private_after}})
    proof.append({"name": "reserve_is_ext4", "ok": magic == b"\x53\xef", "magic": magic.hex()})
    proof.append({"name": "reserve_label", "ok": written_label == label, "label": written_label})
    return proof


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    missing = [tool for tool in ("sgdisk", "sfdisk", "mkfs.ext4") if shutil.which(tool) is None]
    proof = {"kind": "armada-tf-image-integration", "device_validation": False,
             "checks": [], "ok": False, "reason": ""}
    if missing:
        proof["reason"] = "missing tools: %s" % ", ".join(missing)
        print(json.dumps(proof, indent=2, sort_keys=True))
        return 1
    workdir = Path(argv[0]) if argv else Path(os.environ.get("ARMADA_TF_TEST_WORKDIR",
                                                            tempfile.mkdtemp(prefix="armada-tf-")))
    workdir.mkdir(parents=True, exist_ok=True)
    image = workdir / "tf-mixed-gpt.img"
    try:
        _build_image(image)
        checks = _checks(image)
    except Exception as error:  # a difference or a tool failure is a hard failure
        checks = [{"name": "integration", "ok": False, "detail": str(error)}]
    proof["checks"] = checks
    proof["ok"] = bool(checks) and all(check.get("ok") is True for check in checks)
    if not proof["ok"]:
        proof["reason"] = proof["reason"] or "one or more image invariants differ"
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
