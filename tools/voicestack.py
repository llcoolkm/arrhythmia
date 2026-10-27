"""Stack the firmware-profile DSP bootstrap streams (no meter) onto an Arrhythmia ARM base.

    uv run python -I tools/voicestack.py --base patches/arrhythmia-firmware-arm.json \
        --streams build/voice-stream --label 7201 --version 000791 --out build/arrhythmia-firmware-7201

Inputs (local, ignored build/, never in git): the stream directory produced by the DSP voice build
(tools/dspvoice, see its README): core1-voice.bin, core0-stock-relocated.bin and manifest.json. Placement follows the manifest
only, after checking it:
* the stream hashes equal the manifest's; the base image is the pinned stock 5706 (manifest stock_sha256);
* core 1 goes in place at the select-1 pointer's target 0x080101F0 (pointer 0x08031660 unchanged); it may run into
  the old core-0 stream window but not past its end; it parses as a scatter stream with the manifest's word count,
  record count and terminator;
* core 0 is byte-identical to the stock select-0 stream and goes to the manifest's new address (erased flash, FF
  expected); the pointer word 0x08031658 is rewritten from the stock address to it;
* the ARM patch area of the base ends below the relocated core-0 stream; no patch overlaps another (tools/patch.py);
* the stream start addresses appear in the stock image only in the pointer table (aligned scan).
The base's label patch is replaced by --label (must differ from the base's: the bootloader skips an install with
the same label).
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import dspmeter as m  # noqa: E402
import patch as patchtool  # noqa: E402
import syx  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


def layer(image, streams):
    man = json.load(open(os.path.join(streams, "manifest.json")))
    s1 = open(os.path.join(streams, "core1-voice.bin"), "rb").read()
    s0 = open(os.path.join(streams, "core0-stock-relocated.bin"), "rb").read()
    if sha(image) != man["stock_sha256"] or sha(image) != m.STOCK_SHA256:
        sys.exit("base image is not the pinned stock 5706 the streams were built from")
    if sha(s1) != man["core1_sha256"] or sha(s0) != man["core0_stock_stream_sha256"]:
        sys.exit("stream hashes do not match the manifest")
    if man.get("firmware_image") is not False or not man.get("core0_stream_unchanged"):
        sys.exit("unexpected manifest flags")
    if (man["core1_pointer_address"], man["core1_pointer"]) != (m.PTR_CORE1, m.STREAM1):
        sys.exit("core 1 placement differs from the stock select-1 pointer")
    if (man["core0_pointer_address"], man["core0_old_pointer"]) != (m.PTR_CORE0, m.STREAM0):
        sys.exit("core 0 pointer address / old target differ from the stock pointer table")
    stock0, stock1 = m.stock_streams(image)
    old_end0 = m.STREAM0 + 6 + 3 * m.STOCK_CORE0_WORDS
    if s0 != m.w24(m.STOCK_CORE0_WORDS) + m.w24(0) + stock0:
        sys.exit("core 0 stream is not the stock select-0 stream")
    n1 = m.u24(s1, 0)
    if m.u24(s1, 1) != 0 or len(s1) != 6 + 3 * n1 or n1 != man["core1_new_words"]:
        sys.exit("core 1 header / length")
    recs, term = m.parse_scatter(s1[6:])
    if len(recs) != man["core1_new_records"] or term != man["core1_new_terminator"]:
        sys.exit(f"core 1 scatter: {len(recs)} records, terminator {term}")
    end1 = m.STREAM1 + len(s1)
    if end1 != man["core1_stream_end"] or end1 > old_end0:
        sys.exit(f"core 1 stream ends {end1:#x} (manifest {man['core1_stream_end']:#x}, old core 0 end {old_end0:#x})")
    at0 = man["core0_new_pointer"]
    if at0 + len(s0) != man["core0_stream_end"] or at0 + len(s0) > m.FLASH_END or at0 % 4:
        sys.exit("core 0 placement")
    # the two stream starts are referenced only by the pointer table (aligned literal scan of the whole image)
    for start, ptr in ((m.STREAM0, m.PTR_CORE0), (m.STREAM1, m.PTR_CORE1)):
        at = [m.BASE + i for i in range(0, len(image) - 3, 4) if struct.unpack_from("<I", image, i)[0] == start]
        if at != [ptr]:
            sys.exit(f"stream start {start:#x} referenced at {[hex(x) for x in at]}, expected only {ptr:#x}")
    old1 = image[m.STREAM1 - m.BASE:end1 - m.BASE]
    patches = []
    for a, b in m._runs([i for i in range(len(s1)) if s1[i] != old1[i]]):
        patches.append((m.STREAM1 + a, old1[a:b], s1[a:b], "core 1 voice stream (select 1; Arrhythmia DSP voice code, source not yet published)"))
    patches.append((m.PTR_CORE0, struct.pack("<I", m.STREAM0), struct.pack("<I", at0), "core 0 stream pointer"))
    patches.append((at0, b"\xff" * len(s0), s0, f"core 0 stream, stock {m.STOCK_CORE0_WORDS} words, relocated"))
    info = {"streams": os.path.relpath(streams, ROOT), "profile": man["profile"], "wire_abi_frozen": man["wire_abi_frozen"],
            "core1_sha256": man["core1_sha256"], "core0_sha256": man["core0_stock_stream_sha256"],
            "manifest_sha256": sha(open(os.path.join(streams, "manifest.json"), "rb").read()),
            "core1_stream": [m.STREAM1, end1], "core0_stream": [at0, at0 + len(s0)], "core1_records": len(recs),
            "enable_initial_value": man["enable_initial_value"], "meter": False}
    return patches, info


def build(base_spec, streams, label, out, version):
    spec = json.load(open(base_spec))
    image = bytes(syx.parse(open(os.path.join(ROOT, spec["base_syx"]), "rb").read())["image"])
    patches, info = layer(image, streams)
    arm_end = max(int(p["addr"], 16) + len(bytes.fromhex(p["new"])) for p in spec["patches"]
                  if int(p["addr"], 16) >= 0x0806A570)
    if arm_end > info["core0_stream"][0]:
        sys.exit(f"ARM patch area ends {arm_end:#x}, past the relocated core 0 stream {info['core0_stream'][0]:#x}")
    info["arm_end"] = arm_end
    base = [p for p in spec["patches"] if p["addr"] != "0x080101CA"]
    old = next(p for p in spec["patches"] if p["addr"] == "0x080101CA")
    if len(base) != len(spec["patches"]) - 1 or struct.unpack("<H", bytes.fromhex(old["new"]))[0] == label:
        sys.exit("base needs its own build-number patch, different from --label")
    if not 7000 <= label <= 7999 or version <= spec["version_nibbles"]:
        sys.exit("label 70xx..79xx and a version above the base's")
    voice = [{"addr": f"0x{a:08X}", "expect": e.hex(), "new": n.hex(), "note": note} for a, e, n, note in patches]
    lab = {"addr": "0x080101CA", "expect": "4a16", "new": struct.pack("<H", label).hex(),
           "note": f"build-info block: build number 5706 -> {label} (install marker)"}
    desc = (f"{spec['description']} DSP: firmware-profile streams ({info['profile']}, wire ABI "
            f"{'frozen' if info['wire_abi_frozen'] else 'not frozen'}): "
            "core 1 voice decoder (synth ENABLE X:2E70 = 0 at boot), core 0 stock relocated; no meter.")
    out_spec = dict(spec, description=desc, patches=base + voice + [lab], version_nibbles=version, dsp_voice=info)
    with open(out + ".json", "w") as f:
        json.dump(out_spec, f, indent=1)
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        patchtool.apply(os.path.abspath(out + ".json"), os.path.abspath(out))
    finally:
        os.chdir(cwd)
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--streams", required=True)
    ap.add_argument("--label", type=int, required=True)
    ap.add_argument("--version", required=True, help="six decimal digits")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9]{6}", a.version):
        sys.exit("--version takes six decimal digits")
    info = build(a.base, a.streams, a.label, a.out, [int(c) for c in a.version])
    print(json.dumps(info, default=lambda v: hex(v) if isinstance(v, int) else str(v)))


if __name__ == "__main__":
    main()
