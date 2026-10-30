"""The far blob: a second code blob in the old core-0 window (0.2.1, issue #3; flash-space REPORT option 1).

The C is compiled with -ffunction-sections -fdata-sections. From the root functions, every section reached through a
relocation is kept (the rest of the translation unit is dropped), laid out after a header at a fixed address, and
its relocations are resolved here; no linker. Accepted: Thumb BL / B.W (R_ARM_THM_CALL, R_ARM_THM_JUMP24), R_ARM_ABS32
and MOVW / MOVT (R_ARM_THM_MOVW_ABS_NC, R_ARM_THM_MOVT_ABS) against kept .text / .rodata sections. Refused: undefined
symbols, writable sections (.data / .bss: the blob owns no RAM), any other relocation type.

Header (16 + 4 n bytes): u32 magic 'CRF1', u32 key, u32 n, u32 0, then n Thumb addresses (the table functions, in
order). key = CRC-32 of the blob with the key word zero. The main blob is built after it with -DFAR_KEY=key and calls
table function i only if the magic, the key and n match (patches/src/notetap.c far_call).
"""
import struct
import zlib

MAGIC = 0x31465243                      # 'CRF1'
HEAD = 16
SHT_SYMTAB, SHT_REL, SHT_NOBITS = 2, 9, 8
SHF_WRITE, SHF_ALLOC = 1, 2
ABS32, THM_CALL, THM_JUMP24, MOVW, MOVT = 2, 10, 30, 47, 48


class Refused(Exception):
    pass


def need(ok, msg):
    if not ok:
        raise Refused(msg)


def parse(elf):
    need(elf[:4] == b"\x7fELF" and elf[4] == 1 and elf[5] == 1, "not a 32-bit little-endian ELF")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)
    secs = []
    for i in range(shnum):
        f = struct.unpack_from("<10I", elf, shoff + i * shentsize)
        secs.append(dict(name=f[0], type=f[1], flags=f[2], off=f[4], size=f[5], link=f[6], info=f[7], align=f[8]))
    strtab = secs[shstrndx]
    nm = lambda o, t: elf[t["off"] + o:elf.index(b"\0", t["off"] + o)].decode()          # noqa: E731
    for s in secs:
        s["name"] = nm(s["name"], strtab)
    sym = next(s for s in secs if s["type"] == SHT_SYMTAB)
    syms = []
    for k in range(sym["size"] // 16):
        n, v, sz, info, oth, shndx = struct.unpack_from("<IIIBBH", elf, sym["off"] + 16 * k)
        syms.append(dict(name=nm(n, secs[sym["link"]]), value=v, bind=info >> 4, type=info & 15, shndx=shndx))
    rels = {}
    for s in secs:
        if s["type"] == SHT_REL:
            rels[s["info"]] = [struct.unpack_from("<II", elf, s["off"] + 8 * k) for k in range(s["size"] // 8)]
    return secs, syms, rels


def _bl_get(b, o):
    hi, lo = struct.unpack_from("<HH", b, o)
    s = hi >> 10 & 1
    i1, i2 = 1 - ((lo >> 13 & 1) ^ s), 1 - ((lo >> 11 & 1) ^ s)
    off = s << 24 | i1 << 23 | i2 << 22 | (hi & 0x3FF) << 12 | (lo & 0x7FF) << 1
    return off - (1 << 25) if s else off


def _bl_put(b, o, off, call):
    need(-(1 << 24) <= off < (1 << 24) and off % 2 == 0, f"branch offset {off} out of range")
    hi0, lo0 = struct.unpack_from("<HH", b, o)
    s = off >> 24 & 1
    j1, j2 = (1 - (off >> 23 & 1)) ^ s, (1 - (off >> 22 & 1)) ^ s
    hi = 0xF000 | s << 10 | (off >> 12 & 0x3FF)
    lo = (lo0 & 0xD000) | j1 << 13 | j2 << 11 | (off >> 1 & 0x7FF)
    need((lo0 & 0x5000) == (0x5000 if call else 0x1000), "not a BL / B.W at a branch relocation")
    struct.pack_into("<HH", b, o, hi, lo)


def _mov_get(b, o):
    hi, lo = struct.unpack_from("<HH", b, o)
    return (hi & 0xF) << 12 | (hi >> 10 & 1) << 11 | (lo >> 12 & 7) << 8 | (lo & 0xFF)


def _mov_put(b, o, v):
    hi, lo = struct.unpack_from("<HH", b, o)
    hi = (hi & 0xFBF0) | (v >> 12 & 0xF) | (v >> 11 & 1) << 10
    lo = (lo & 0x8F00) | (v >> 8 & 7) << 12 | (v & 0xFF)
    struct.pack_into("<HH", b, o, hi, lo)


def link(elf, roots, table, at):
    """(blob, {root name: address}, key) for the functions `roots` (entries) and `table` (header table, in order)."""
    secs, syms, rels = parse(elf)
    by = {s["name"]: s for s in syms if s["type"] == 2 and s["shndx"]}           # FUNC
    for r in list(roots) + list(table):
        need(r in by and by[r]["bind"] == 1, f"far root {r} missing or not global")
    keep, todo = [], [by[r]["shndx"] for r in list(table) + list(roots)]
    while todo:
        i = todo.pop()
        if i in keep:
            continue
        s = secs[i]
        need(s["flags"] & SHF_ALLOC and not s["flags"] & SHF_WRITE and s["type"] != SHT_NOBITS
             and s["name"].startswith((".text", ".rodata")), f"far blob needs section {s['name']} (no RAM, no data)")
        keep.append(i)
        for _, info in rels.get(i, []):
            y = syms[info >> 8]
            need(y["shndx"] != 0, f"undefined symbol {y['name']} in the far blob")
            todo.append(y["shndx"])
    keep.sort()
    n = len(table)
    pos, addr = HEAD + 4 * n, {}
    for i in keep:
        al = max(secs[i]["align"], 2)
        pos += -pos % al
        addr[i] = at + pos
        pos += secs[i]["size"]
    blob = bytearray(b"\xff" * (pos + -pos % 4))
    for i in keep:
        o = addr[i] - at
        blob[o:o + secs[i]["size"]] = elf[secs[i]["off"]:secs[i]["off"] + secs[i]["size"]]
    for i in keep:
        for r_off, info in rels.get(i, []):
            y, t = syms[info >> 8], info & 0xFF
            S, P, o = addr[y["shndx"]] + y["value"], addr[i] + r_off, addr[i] - at + r_off
            if t in (THM_CALL, THM_JUMP24):
                _bl_put(blob, o, (S & ~1) + _bl_get(blob, o) - P, t == THM_CALL)
            elif t == ABS32:
                struct.pack_into("<I", blob, o, (S + struct.unpack_from("<I", blob, o)[0]) & 0xFFFFFFFF)
            elif t in (MOVW, MOVT):
                need(_mov_get(blob, o) == 0, f"MOVW / MOVT addend in {secs[i]['name']}")
                v = S & 0xFFFFFFFF
                _mov_put(blob, o, v & 0xFFFF if t == MOVW else v >> 16)
            else:
                raise Refused(f"relocation type {t} in {secs[i]['name']}")
    fa = lambda r: addr[by[r]["shndx"]] + (by[r]["value"] & ~1)                    # noqa: E731
    struct.pack_into("<4I", blob, 0, MAGIC, 0, n, 0)
    for k, r in enumerate(table):
        struct.pack_into("<I", blob, HEAD + 4 * k, fa(r) | 1)
    key = zlib.crc32(bytes(blob)) & 0xFFFFFFFF
    struct.pack_into("<I", blob, 4, key)
    return bytes(blob), {r: fa(r) for r in list(roots) + list(table)}, key
