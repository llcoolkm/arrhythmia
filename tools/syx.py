"""Novation Circuit firmware .syx <-> raw binary.

Format (observed on Circuit Rhythm 5706 / Tracks 4486):
  F0 00 20 29 00 71 <init: 01 <device id> ...> F7      device 0x63 = Rhythm, 0x64 = Tracks
  F0 00 20 29 00 7C <7 bytes ?> <8 nibbles size> <8 nibbles crc32> F7
  F0 00 20 29 00 72 <37 x 7-bit data> F7                (repeated)
  F0 00 20 29 00 73 <37 x 7-bit data> F7                (sent last, but holds the FIRST 32 bytes:
                                                         the vector table, so an interrupted update never boots)
Each data block: 37 bytes x 7 bits = 259 bits -> 32 bytes, last 3 bits padding.
CRC: CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xFFFFFFFF, no reflection, xorout 0).
"""
import argparse
import hashlib
import sys

PREFIX = bytes([0xF0, 0x00, 0x20, 0x29, 0x00])
INIT, HEADER, WRITE, FINISH = 0x71, 0x7C, 0x72, 0x73
DEVICES = {0x63: "circuit-rhythm", 0x64: "circuit-tracks"}
BLOCK = 32
BLOCK_7BIT = 37


def _crc_table():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        t.append(c & 0xFFFFFFFF)
    return t


_TABLE = _crc_table()


def crc32_mpeg2(data: bytes) -> int:
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _TABLE[(c >> 24) ^ b]
    return c


def nibbles_to_int(b: bytes) -> int:
    if any(n > 15 for n in b):
        raise ValueError("invalid hexadecimal nibble")
    v = 0
    for n in b:
        v = (v << 4) | n
    return v


def int_to_nibbles(v: int, n: int = 8) -> bytes:
    if n <= 0 or not 0 <= v < 1 << (4 * n):
        raise ValueError("integer does not fit nibble field")
    return bytes((v >> (4 * (n - 1 - i))) & 0xF for i in range(n))


def unpack7(b: bytes) -> bytes:
    if any(x > 127 for x in b):
        raise ValueError("non-7-bit data")
    if not b:
        return b""
    bits = "".join(f"{x:07b}" for x in b)
    bits = bits[: len(bits) // 8 * 8]
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


def pack7(b: bytes) -> bytes:
    bits = "".join(f"{x:08b}" for x in b)
    bits += "0" * (-len(bits) % 7)
    return bytes(int(bits[i:i + 7], 2) for i in range(0, len(bits), 7))


def split(syx: bytes) -> list[bytes]:
    msgs, i = [], 0
    while i < len(syx):
        j = syx.index(0xF7, i)
        m = syx[i:j + 1]
        if not m.startswith(PREFIX):
            raise ValueError(f"non-Novation message at offset {i}")
        if len(m) < 7 or any(x > 127 for x in m[1:-1]):
            raise ValueError(f"invalid SysEx framing/data at offset {i}")
        msgs.append(m)
        i = j + 1
    return msgs


def _validate_metadata(init: bytes, header_prefix: bytes):
    if len(init) != 8 or init[0] != 1 or init[1] not in DEVICES:
        raise ValueError("unsupported INIT length, family, or device")
    if len(header_prefix) != 7 or header_prefix[0] not in range(4):
        raise ValueError("unsupported HEADER target or length")
    if any(v > 9 for v in init[2:]) or any(v > 9 for v in header_prefix[1:]):
        raise ValueError("version must contain six decimal digits (0..9)")
    if init[2:] != header_prefix[1:]:
        raise ValueError("INIT and HEADER versions differ")


def parse(syx: bytes) -> dict:
    msgs = split(syx)
    if (len(msgs) < 3 or msgs[0][5] != INIT or msgs[1][5] != HEADER
            or msgs[-1][5] != FINISH or any(m[5] != WRITE for m in msgs[2:-1])):
        raise ValueError("expected INIT, HEADER, WRITE*, FINISH exactly once in order")
    init, hb = msgs[0][6:-1], msgs[1][6:-1]
    if len(hb) != 23:
        raise ValueError(f"unexpected header length {len(hb)}")
    _validate_metadata(init, hb[:7])
    size, crc = nibbles_to_int(hb[7:15]), nibbles_to_int(hb[15:23])
    if not size or len(msgs) - 2 != (size + BLOCK - 1) // BLOCK:
        raise ValueError("data block count does not match nonzero header size")
    blocks = []
    for m in [msgs[-1], *msgs[2:-1]]:
        payload = m[6:-1]
        if len(payload) != BLOCK_7BIT or payload[-1] & 7:
            raise ValueError("invalid data block length or nonzero packing padding")
        blocks.append(unpack7(payload))
    data = b"".join(blocks)
    if data[size:] != b"\xff" * (len(data) - size):
        raise ValueError("non-FF image padding")
    image = data[:size]
    return {
        "device": init[1], "init": init, "header_prefix": hb[:7],
        "size": size, "crc": crc, "image": image, "crc_ok": crc32_mpeg2(image) == crc,
    }


def build(image: bytes, init: bytes, header_prefix: bytes) -> bytes:
    _validate_metadata(init, header_prefix)
    if not image:
        raise ValueError("image must not be empty")
    crc = crc32_mpeg2(image)
    out = [PREFIX + bytes([INIT]) + init + b"\xF7",
           PREFIX + bytes([HEADER]) + header_prefix + int_to_nibbles(len(image)) + int_to_nibbles(crc) + b"\xF7"]
    padded = image + b"\xFF" * (-len(image) % BLOCK)
    blocks = [padded[i:i + BLOCK] for i in range(0, len(padded), BLOCK)]
    for blk in blocks[1:]:
        out.append(PREFIX + bytes([WRITE]) + pack7(blk) + b"\xF7")
    out.append(PREFIX + bytes([FINISH]) + pack7(blocks[0]) + b"\xF7")
    return b"".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack", help="syx -> bin")
    u.add_argument("syx")
    u.add_argument("bin")
    i = sub.add_parser("info", help="print header fields")
    i.add_argument("syx")
    k = sub.add_parser("pack", help="bin -> syx, reusing init/header fields of a reference syx")
    k.add_argument("bin")
    k.add_argument("ref_syx")
    k.add_argument("out_syx")
    a = ap.parse_args()

    if a.cmd == "pack":
        ref = parse(open(a.ref_syx, "rb").read())
        if not ref["crc_ok"]:
            sys.exit("reference CRC mismatch")
        result = build(open(a.bin, "rb").read(), ref["init"], ref["header_prefix"])
        open(a.out_syx, "wb").write(result)
        a.syx = a.out_syx

    p = parse(open(a.syx, "rb").read())
    dev = DEVICES.get(p["device"], f"unknown 0x{p['device']:02x}")
    print(f"device   {dev}\ninit     {p['init'].hex(' ')}\nhdr pfx  {p['header_prefix'].hex(' ')}\n"
          f"size     {p['size']} (0x{p['size']:x})\ncrc      0x{p['crc']:08x} {'OK' if p['crc_ok'] else 'MISMATCH'}\n"
          f"sha256   {hashlib.sha256(p['image']).hexdigest()}")
    if not p["crc_ok"]:
        sys.exit(1)
    if a.cmd == "unpack":
        open(a.bin, "wb").write(p["image"])


if __name__ == "__main__":
    main()
