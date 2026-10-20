"""Apply a patch set (JSON) to a stock firmware .syx and write the patched .bin, .syx, and metadata manifest.

    python3 -I tools/patch.py patches/hello-version.json build/patched

Safety rules enforced here, not left to the patch author:
* the base stream must be structurally valid, with matching CRC and device identity;
* the base image must match the SHA-256 in the patch file and have a word-aligned length;
* extended images are padded with FF to a complete word before computing header size/CRC;
* every patch states the original bytes it replaces (FF for extensions); a mismatch aborts;
* patches cannot overlap; versions contain decimal digits, as the firmware parser requires;
* patches stay inside the application image, outside the vector table, unless "allow_vectors" is set;
* the update header type is 0 (application). Other types are other flash targets and are refused.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import syx  # noqa: E402

BASE = 0x08010000
VECTORS_END = 0x080101C8
FLASH_END = 0x08080000


def apply(spec_path, out_prefix):
    spec_bytes = Path(spec_path).read_bytes()
    spec = json.loads(spec_bytes)
    source = Path(spec["base_syx"]).read_bytes()
    ref = syx.parse(source)
    if not ref["crc_ok"]:
        sys.exit("base stream CRC mismatch")
    image = bytearray(ref["image"])
    if hashlib.sha256(image).hexdigest() != spec["base_sha256"]:
        sys.exit("base image SHA-256 does not match the patch file")
    if ref["header_prefix"][0] != 0:
        sys.exit("base header type is not 0 (application)")

    if not VECTORS_END - BASE + 6 <= len(image) <= FLASH_END - BASE:
        sys.exit("base image size is outside application bounds")
    if len(image) % 4:
        sys.exit("base application image length must be word-aligned")
    # This identity is covered by base_sha256, unlike transport metadata.
    if bytes(image[0x1CC:0x1CE]) != ref["init"][:2]:
        sys.exit("INIT family/device does not match the pinned application build-info")
    version_values = spec["version_nibbles"]
    if (not isinstance(version_values, list) or len(version_values) != 6
            or any(type(v) is not int or not 0 <= v <= 9 for v in version_values)):
        sys.exit("version_nibbles must be six decimal digits 0..9")
    version = bytes(version_values)
    intervals = []
    for p in spec["patches"]:
        addr, old, new = int(p["addr"], 16), bytes.fromhex(p["expect"]), bytes.fromhex(p["new"])
        if not new:
            sys.exit(f"{p['addr']}: empty patch")
        if len(old) != len(new):
            sys.exit(f"{p['addr']}: expect and new differ in length")
        if addr < BASE or addr + len(new) > FLASH_END:
            sys.exit(f"{p['addr']}: outside the application flash")
        if addr < VECTORS_END and not p.get("allow_vectors"):
            sys.exit(f"{p['addr']}: inside the vector table")
        if any(addr < end and start < addr + len(new) for start, end in intervals):
            sys.exit(f"{p['addr']}: overlaps another patch")
        intervals.append((addr, addr + len(new)))
        off = addr - BASE
        if off + len(new) > len(image):
            if off < len(image):
                sys.exit(f"{p['addr']}: straddles the image end")
            image.extend(b"\xff" * (off + len(new) - len(image)))
            old_now = b"\xff" * len(new)
        else:
            old_now = bytes(image[off:off + len(new)])
        if old_now != old:
            sys.exit(f"{p['addr']}: found {old_now.hex()}, expected {old.hex()}")
        image[off:off + len(new)] = new
        print(f"patched {p['addr']} {old.hex()} -> {new.hex()}  {p.get('note', '')}")

    # The application CRC feeder reads whole 32-bit words. Its zero-filled
    # scratch buffer would add implicit zero bytes for a partial final word.
    # Make padding explicit image bytes so host and device CRCs cover the same data.
    image.extend(b"\xff" * (-len(image) % 4))
    init = ref["init"][:2] + version + ref["init"][8:]
    header_prefix = ref["header_prefix"][:1] + version
    out = syx.build(bytes(image), init, header_prefix)

    check = syx.parse(out)  # round trip: what we wrote must parse back to exactly our image
    assert check["crc_ok"] and check["image"] == bytes(image) and check["header_prefix"][0] == 0
    ranges = []
    for offset, value in enumerate(image):
        if offset < len(ref["image"]) and value == ref["image"][offset]:
            continue
        address = BASE + offset
        if ranges and ranges[-1]["end_exclusive"] == address:
            ranges[-1]["end_exclusive"] += 1
        else:
            ranges.append({"start": address, "end_exclusive": address + 1})
    manifest = {
        "format_version": 1,
        "patch_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "source": {"syx_sha256": hashlib.sha256(source).hexdigest(),
                   "image_sha256": hashlib.sha256(ref["image"]).hexdigest(),
                   "syx_size": len(source), "image_size": len(ref["image"]),
                   "crc32_mpeg2": f"{ref['crc']:08x}", "version_digits": list(ref["init"][2:])},
        "output": {"syx_sha256": hashlib.sha256(out).hexdigest(),
                   "image_sha256": hashlib.sha256(image).hexdigest(),
                   "syx_size": len(out), "image_size": len(image),
                   "crc32_mpeg2": f"{check['crc']:08x}", "version_digits": list(version)},
        "target": {"header_type": 0, "device_id": ref["device"],
                   "device": syx.DEVICES[ref["device"]], "image_base": BASE},
        "changed_address_ranges": ranges,
        "changed_bytes": sum(item["end_exclusive"] - item["start"] for item in ranges),
        "range_semantics": "absolute byte addresses; end exclusive; appended bytes count as changed",
    }
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    Path(out_prefix + ".bin").write_bytes(image)
    Path(out_prefix + ".syx").write_bytes(out)
    Path(out_prefix + ".manifest.json").write_text(manifest_text)
    print(f"version  {ref['init'][2:8].hex(' ')} -> {version.hex(' ')}")
    print(f"wrote    {out_prefix}.bin ({len(image)} bytes) and {out_prefix}.syx + .manifest.json, crc 0x{check['crc']:08x}")
    print(f"sha256   {hashlib.sha256(out).hexdigest()}  {out_prefix}.syx")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    apply(sys.argv[1], sys.argv[2])
