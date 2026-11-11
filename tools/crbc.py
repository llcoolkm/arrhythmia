# SPDX-License-Identifier: MIT
"""Engine base-contract generator and validator.

    python3 -I tools/crbc.py header
    python3 -I tools/crbc.py check
    python3 -I tools/crbc.py golden OUTDIR

An engine header binds its body to this composition contract. Version 1 hashes
b"CRBC", a one-byte version, and four length-prefixed byte strings in order:
the user's core-0 input stream, our core-0 integration records, the user's
core-1 input stream, and our companion edit records. Lengths are u32 little
endian; DSP words inside a part are three-byte big endian. The identifier is
the first four SHA-256 digest bytes interpreted as a little-endian u32.

The generated header supplies both this identifier and a CRC-32 of the four
concatenated parts for the loader's input check. Changing any part requires
new engine headers and validation of the resulting composition. Header
rewriting alone does not establish compatibility. Golden outputs contain
input firmware and remain local; they are not release assets.
"""
import hashlib
import json
import os
import struct
import sys
import zlib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STOCK = os.path.join(ROOT, "build", "circuit-rhythm-5706.bin")
HEADER = os.path.join(ROOT, "patches", "src", "crbc.h")
B = 0x08010000
MAGIC, VERSION = b"CRBC", 1

# Input stream bindings for the supported official application image.
C0_PTR_AT, C0_AT, C0_BYTES = 0x08031658, 0x0801A838, 24525
C1_PTR_AT, C1_AT, C1_BYTES = 0x08031660, 0x080101F0, 42567
P_REGION = 0x2000                             # the engine region starts at P:0x2000
# Part 2: the core-0 integration set.
SITES = [(0x100, 0x0AF080), (0x101, 0x002D80),   # jmp P:0x2D80: the engine's startup
         (0x224, 0x0BF080), (0x225, 0x002200)]   # jsr P:0x2200: the engine's render entry
PAD_FIRST, PAD_COUNT = 0x1FED, 19             # zero words P:0x1FED..0x1FFF
# Our companion program edit records, encoded as (space, address, words).
C1_EDITS = [
    (0, 0x004A, [0x0C0FF8]),                  # fail-stop vector
    (0, 0x02DA, [0x0C0D32]),                  # boot suppression hook
    (0, 0x02FD, [0x0C0D46]),                  # pending suppression hook
    (0, 0x04F4, [0x0C0D38]),                  # note-on suppression hook
    (0, 0x0509, [0x0C0D3F]),                  # note-off suppression hook
    (0, 0x0D32, [0x200013, 0x567000, 0x0000B4, 0x567000, 0x0000BA, 0x0C02DB, 0x56F400, 0x000007, 0x200076,
                 0x014785, 0x0EAD4D, 0x200013, 0x0C04F5, 0x56F400, 0x000007, 0x200076, 0x014785, 0x0EAD4D,
                 0x200013, 0x0C050A, 0x56F400, 0x000007, 0x200076, 0x014785, 0x0EAD4D, 0x20FB00, 0x0C02FE,
                 0x200013, 0x567000, 0x0000B4, 0x567000, 0x0000BA, 0x5E7000, 0x0307C5, 0x5E7000, 0x0307C6,
                 0x00000C]),                  # suppression body
    (0, 0x0FF8, [0x0003F8, 0x0C0FF9]),        # fail-stop body
]


def w24(v):
    assert 0 <= v < 1 << 24, v
    return v.to_bytes(3, "big")


def u24(b, i):
    return int.from_bytes(b[i:i + 3], "big")


def stock_image(path=STOCK):
    return open(path, "rb").read()


def stream(image, ptr_at, at, size):
    """A stock stream as stored, checked against the select-0 table and its own header."""
    assert struct.unpack_from("<I", image, ptr_at - B)[0] == at, f"select-0 pointer {ptr_at:#x}"
    s = image[at - B:at - B + size]
    assert len(s) == size and u24(s, 3) == 0 and 6 + 3 * u24(s, 0) == size, f"stream at {at:#x}"
    return s


def part2(sites=SITES, pad_first=PAD_FIRST, pad_count=PAD_COUNT):
    assert [a for a, _ in sites] == sorted({a for a, _ in sites}), "site addresses ascending, distinct"
    return (w24(len(sites)) + b"".join(w24(a) + w24(v) for a, v in sites) + w24(pad_first) + w24(pad_count)
            + bytes(3 * pad_count))


def part4(edits=C1_EDITS):
    return b"".join(w24(sp) + w24(len(ws)) + w24(a) + b"".join(w24(v) for v in ws) for sp, a, ws in edits)


def c1_prefix_words(c1):
    """Payload words before the stock terminator suffix: the secondary-loader records (space <= 2) walked in order."""
    n, k = u24(c1, 0), 0
    while k < n and u24(c1, 6 + 3 * k) <= 2:
        k += 3 + u24(c1, 6 + 3 * k + 3)
    assert 0 < k < n, k
    return k


def parts(image=None):
    image = stock_image() if image is None else image
    p1 = stream(image, C0_PTR_AT, C0_AT, C0_BYTES)
    p3 = stream(image, C1_PTR_AT, C1_AT, C1_BYTES)
    assert PAD_FIRST == u24(p1, 0) and PAD_FIRST + PAD_COUNT == P_REGION, "the pad closes the stock P to P:0x2000"
    assert all(a < PAD_FIRST for a, _ in SITES), "sites inside the stock words"
    return [p1, part2(), p3, part4()]


def preimage(ps, version=VERSION):
    return MAGIC + bytes([version]) + b"".join(struct.pack("<I", len(p)) + p for p in ps)


def base_id(pre):
    """The header's base id: SHA-256(preimage)[0:4] as a little-endian u32."""
    return struct.unpack("<I", hashlib.sha256(pre).digest()[:4])[0]


def flash_crc(ps):
    """CRC-32 (zlib) of the four parts as stored, concatenated in order: what the firmware rechecks per load."""
    return zlib.crc32(b"".join(ps))


def _bytes_asm(name, data):
    rows = "".join("        \".byte " + ", ".join(f"0x{x:02X}" for x in data[i:i + 15]) + "\\n\\t\"\n"
                   for i in range(0, len(data), 15))
    return (f"__attribute__((naked, used, noinline)) static uint32_t {name}(void)\n{{\n    __asm__ volatile(\n"
            "        \"adr r0, 1f\\n\\t\"\n        \"bx lr\\n\\t\"\n        \".p2align 2\\n\"\n        \"1:\\n\\t\"\n"
            + rows + "        \".p2align 1\");\n}\n")


def header_text(image=None):
    ps = parts(image)
    pre = preimage(ps)
    p1, p2, p3, p4 = ps
    return f"""/* GENERATED by tools/crbc.py from its declared parts: do not edit (python3 -I tools/crbc.py header). The build
 * regenerates it and refuses an engine-pack profile while this file differs. Base contract "CRBC" version {VERSION}
 * The id is SHA-256 over "CRBC" | version | four length-prefixed parts (1 the user-supplied
 * core-0 stream as stored, 2 the core-0 integration set, 3 the stock core-1 stream as stored, 4 the core-1
 * suppression records), its first four bytes as a little-endian u32. Preimage {len(pre):,d} B, SHA-256
 * {hashlib.sha256(pre).hexdigest()}.
 * Parts 2 and 4 below are the bytes the composers send, reached by adr (.text: no data section, no relocation). */
#ifndef CRBC_H
#define CRBC_H
#define CRBC_VERSION    {VERSION}u
#define CRBC_ID         0x{base_id(pre):08X}u     /* header +16 of every admissible engine file */
#define CRBC_CRC        0x{flash_crc(ps):08X}u     /* CRC-32 of parts 1..4 as stored, concatenated */
#define CRBC_C0_W       0x{u24(p1, 0):04X}u         /* part 1: the stock core-0 stream's count (P:0..0x{u24(p1, 0) - 1:04X}) */
#define CRBC_C0_BYTES   {len(p1)}u
#define CRBC_SET_BYTES  {len(p2)}u           /* part 2: {len(SITES)} sites, pad P:0x{PAD_FIRST:04X}..0x{PAD_FIRST + PAD_COUNT - 1:04X} */
#define CRBC_SET_N      {len(SITES)}u
#define CRBC_C1_W       {u24(p3, 0)}u        /* part 3: the stock core-1 stream's count */
#define CRBC_C1_BYTES   {len(p3)}u
#define CRBC_C1_PFX_W   {c1_prefix_words(p3)}u        /* its payload words before the terminator suffix */
#define CRBC_EDIT_BYTES {len(p4)}u          /* part 4: {len(C1_EDITS)} records, {len(p4) // 3} words */

{_bytes_asm("crbc_set", p2)}
{_bytes_asm("crbc_edits", p4)}#endif
"""


def golden(out, image=None):
    ps = parts(image)
    pre = preimage(ps)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "crbc-preimage.bin"), "wb").write(pre)
    rec = {"format": "crbc-golden/1", "version": VERSION, "preimage": "crbc-preimage.bin", "bytes": len(pre),
           "sha256": hashlib.sha256(pre).hexdigest(), "id": f"0x{base_id(pre):08X}",
           "id_header_bytes": hashlib.sha256(pre).digest()[:4].hex(), "flash_crc32": f"0x{flash_crc(ps):08X}",
           "parts": [{"n": i + 1, "bytes": len(p), "sha256": hashlib.sha256(p).hexdigest()} for i, p in enumerate(ps)],
           "stock_image_sha256": hashlib.sha256(image if image else stock_image()).hexdigest(),
           "note": "parts 1 and 3 are stock 5706 streams. Build them from a local firmware image."}
    json.dump(rec, open(os.path.join(out, "crbc-golden.json"), "w"), indent=1)
    return rec


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "header":
        open(HEADER, "w").write(header_text())
    elif cmd == "check":
        sys.exit(0 if os.path.exists(HEADER) and open(HEADER).read() == header_text() else 1)
    elif cmd == "golden" and len(sys.argv) == 3:
        print(json.dumps(golden(sys.argv[2]), indent=1))
    else:
        sys.exit(__doc__)
