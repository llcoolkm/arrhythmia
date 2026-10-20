"""Build an ITCM effect module for the devkit patch.

    uv run python -I tools/build_module.py modules/stars.c build/stars.mod

The module must define frame(leds, frame_no) and may define init(leds). It is compiled position-independent with
no relocations and no data sections; the 24-byte header (magic, size, CRC-32, entry offsets) is prepended. The
offsets are Thumb addresses relative to MOD_BASE (0x00000400). input(leds, kind, index, value) is optional.

Host services (devkit-play, label 6008): a module finds them through the host table at the fixed flash address
0x0806A570 (u32 magic 'CRH1' = 0x31485243, u32 abi, u32 note() Thumb address, u32 built-in module address). Check
magic and abi >= 1 before calling; other builds have code or erased flash there. note(track 0..7, engine note,
velocity 1..127, 0 = off) only queues the request; call it from init/input/frame. See modules/paint.c. Nothing
here changes: the table is read by absolute address, so it needs no relocation.

Clock (devkit-clock, label 6010): a module that defines clock(leds, event, pos, bpm) gets the 28-byte 'CRM2' header
(the six words above plus clock_off); every other module keeps the 24-byte 'CRM1' header, byte for byte as before.
6009 and older refuse 'CRM2' modules (bad magic). See docs/user/developers.md.

Module ABI 3 (devkit-abi3, label 6012): the host table has abi 3 and four more words, note_port(), param(), ui(),
midi(); check abi >= 3 before reading them (on older builds they are hook code). Headers do not change. See
docs/user/developers.md.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

MOD_MAGIC = 0x314D5243
MOD_MAGIC2 = 0x324D5243         # 'CRM2': header + u32 clock_off
HDR = 24
HDR2 = 28
MOD_MAX = 0x4000 - 0x400
CFLAGS = ["--target=thumbv7em-none-eabi", "-mcpu=cortex-m7", "-mthumb", "-Os", "-ffreestanding", "-fno-builtin",
          "-nostdlib", "-fno-jump-tables", "-fno-pic", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables"]


def build(src, out):
    with tempfile.TemporaryDirectory() as tmp:
        obj, text = os.path.join(tmp, "m.o"), os.path.join(tmp, "m.text")
        subprocess.run(["clang", *CFLAGS, "-c", src, "-o", obj], check=True)
        elf = json.loads(subprocess.run(["llvm-readelf-21", "--elf-output-style=JSON", "-S", "-s", "-r", obj],
                                        capture_output=True, text=True, check=True).stdout)[0]
        secs = [x["Section"] for x in elf["Sections"]]
        text_idx = None
        for i, sec in enumerate(secs):
            name, size = sec["Name"]["Name"], sec["Size"]
            flags = {f["Name"] for f in sec["Flags"]["Flags"]}
            if name == ".text":
                text_idx = i
            elif "SHF_ALLOC" in flags and size and not name.startswith(".ARM.exidx"):   # unwind index: unused
                sys.exit(f"module has a non-empty {name} section; only .text is loaded (state goes in MOD_RAM)")
            if sec["Type"]["Name"] in ("SHT_REL", "SHT_RELA") and size and not name.startswith(".rel.ARM.exidx"):
                sys.exit(f"module has relocations ({name}); it must be self-contained")
        if text_idx is None:
            sys.exit("module has no .text")
        subprocess.run(["llvm-objcopy-21", "-O", "binary", "-j", ".text", obj, text], check=True)
        code = open(text, "rb").read()
    entry = {}
    for x in elf["Symbols"]:
        sym = x["Symbol"]
        name = sym["Name"]["Name"]
        if name not in ("init", "frame", "input", "clock"):
            continue
        if sym["Type"]["Name"] != "Function" or sym["Section"]["Value"] != text_idx:
            sys.exit(f"{name} must be a function in .text")
        addr = sym["Value"]
        if not addr & 1 or (addr & ~1) + 2 > len(code):
            sys.exit(f"{name} is not a Thumb function inside the code")
        entry[name] = addr & ~1
    hdr = HDR2 if "clock" in entry else HDR
    entry = {k: hdr + v for k, v in entry.items()}
    if "frame" not in entry:
        sys.exit("module has no frame()")
    code += b"\x00" * (-len(code) % 4)
    size = hdr + len(code)
    if size > MOD_MAX:
        sys.exit(f"module too big: {size} > {MOD_MAX}")
    init_off = entry["init"] | 1 if "init" in entry else 0
    frame_off = entry["frame"] | 1
    input_off = entry["input"] | 1 if "input" in entry else 0      # header word 5: devkit-mode input entry
    if "clock" in entry:
        words = (MOD_MAGIC2, size, 0, init_off, frame_off, input_off, entry["clock"] | 1)
    else:
        words = (MOD_MAGIC, size, 0, init_off, frame_off, input_off)
    head = struct.pack(f"<{len(words)}I", *words)
    crc = zlib.crc32(head + code)
    blob = head[:8] + struct.pack("<I", crc) + head[12:] + code
    check(blob)
    open(out, "wb").write(blob)
    print(f"module {out}: {size} bytes, crc 0x{crc:08x}, init 0x{init_off:x}, frame 0x{frame_off:x}"
          + (f", input 0x{input_off:x}" if input_off else "") + (f", clock 0x{words[6]:x}" if len(words) > 6 else ""))
    return blob


def check(blob):
    """The unit's acceptance rules (mod_check in devkit_abi3.c), applied on the host before anything is sent."""
    if len(blob) < HDR + 2:
        raise ValueError("module shorter than its header")
    magic, size, crc, init_off, frame_off, input_off = struct.unpack("<6I", blob[:HDR])
    if magic not in (MOD_MAGIC, MOD_MAGIC2):
        raise ValueError("bad magic")
    hdr = HDR2 if magic == MOD_MAGIC2 else HDR
    if len(blob) < hdr + 2:
        raise ValueError("module shorter than its header")
    clock_off = struct.unpack_from("<I", blob, HDR)[0] if magic == MOD_MAGIC2 else 0
    if size != len(blob) or size > MOD_MAX:
        raise ValueError(f"size field {size} does not match the file ({len(blob)}) or exceeds {MOD_MAX}")
    for name, off in (("frame", frame_off), ("init", init_off), ("input", input_off), ("clock", clock_off)):
        if name != "frame" and off == 0:
            continue
        if not off & 1 or (off & ~1) < hdr or (off & ~1) + 2 > size:
            raise ValueError(f"{name} entry 0x{off:x} is not a Thumb address inside the code")
    if zlib.crc32(blob[:8] + b"\0\0\0\0" + blob[12:]) != crc:
        raise ValueError("CRC mismatch")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(*sys.argv[1:])
