"""Back up the Circuit Rhythm's external 32 MB NOR, read-only.

    sudo python3 -I tools/norbackup.py usb auto [OUTDIR]
    sudo python3 -I tools/norbackup.py usb /dev/sdX [OUTDIR]
        Raw block reads of the USB mass-storage disk (NOR 0x94000..end), two passes that must agree, SHA-256 manifest.
        Refuses unless the device is a whole-disk block device of a USB device 1235:0138 with the stock geometry
        (0x2000 blocks of 0x1000 bytes) and none of it is mounted read-write. Opens the device O_RDONLY only.
        "auto" picks the one whole disk whose USB device is 1235:0138 (refuses if there are none or several), then
        runs the same checks. OUTDIR defaults to ./rhythm-backup-YYYYMMDD-HHMMSS. Under sudo, the files this run
        wrote and the directories it created are given back to the invoking user afterwards; nothing else is chowned.

    python3 -I tools/norbackup.py extract OUTDIR
        Verify OUTDIR/usb-disk.img against SHA256SUMS, then copy the files of its FAT16 content volume (disk offset
        0x40000) into OUTDIR/files/. Pure parsing of the image file: nothing is mounted, the device is not touched.

    python3 -I tools/norbackup.py verify OUTDIR
        Re-hash every file listed in OUTDIR/SHA256SUMS.

    python3 -I tools/norbackup.py assemble OUTDIR
        Join nor-0x000000-0x094000.bin (from `sysex`) and nor-0x094000-0x2000000.bin (from `usb`) into nor-full.bin.

    EXPERIMENTAL, needs a devkit build that has SysEx command 7B. No such build exists
    yet; without one this subcommand must not be run:
    python3 -I tools/norbackup.py sysex --expect-build N /dev/snd/midiC3D0 OUTDIR [--start 0x0 --size 0x94000]
        Reads raw NOR ranges through the stock NOR media object. First asks the stock version query (SysEx 0x70) and
        refuses unless the application build is exactly N.
"""
import argparse
import hashlib
import json
import mmap
import os
import select
import stat
import sys
import time

VID, PID = "1235", "0138"           # Focusrite/Novation, Circuit Rhythm
NOR_SIZE = 0x2000000                # MX25L256, 32 MB
BLOCK = 0x1000                      # MSC block size: the disk reports 0x2000 x 0x1000
DISK_BLOCKS = 0x2000
DISK_SIZE = BLOCK * DISK_BLOCKS
USB_BASE = 0x94000                  # NOR offset of disk block 0
VISIBLE = NOR_SIZE - USB_BASE       # disk bytes that map to real NOR; the rest of the disk reads as zeros
PLACEHOLDER = b"<SERIALUPN123>"     # the firmware replaces it with the unit serial in every block it serves

# NOR layout (NOR offsets).
REGIONS = (
    ("staging", 0x000000, 0x080000),     # update target type 0: application staging area
    ("header", 0x080000, 0x082000),      # update header copy at 0x80000 (23 bytes), rest of the sector unknown
    ("type2", 0x082000, 0x094000),       # update target type 2, unknown content
    ("welcome", 0x094000, 0x0D4000),     # update target type 3: FAT12 "Getting Started" drive (disk 0x0)
    ("content", 0x0D4000, 0x2000000),    # update target type 1: FAT16 content filesystem (disk 0x40000)
)
HIDDEN_END = USB_BASE

SYSEX_PRE = bytes.fromhex("F0 00 20 29 00 60")
CMD_NOR = 0x7B
NOR_CHUNK = 64
NOR_REQ_LEN = 13                    # F0 00 20 29 00 60 7B a4..a0 F7
NOR_REPLY_OK = 6 + 1 + 5 + 1 + 2 * NOR_CHUNK + 1      # 142
NOR_REPLY_ERR = 6 + 1 + 5 + 1 + 1                     # 14
ST_OK, ST_RANGE, ST_BUSY, ST_BAD, ST_NOMEDIA = 0, 1, 2, 3, 4
VERSION_REQ = bytes.fromhex("F0 00 20 29 00 70 F7")


class Refused(Exception):
    """A safety check failed; nothing was read."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- identification (no device access beyond sysfs)

def _read(path):
    with open(path) as f:
        return f.read().strip()


def usb_identity(name, sysfs="/sys"):
    """Walk from /sys/class/block/<name> up to the USB device node; return its attributes."""
    node = os.path.realpath(os.path.join(sysfs, "class", "block", name))
    if not os.path.isdir(node):
        raise Refused(f"{name}: no sysfs node")
    if os.path.exists(os.path.join(node, "partition")):
        raise Refused(f"{name} is a partition; give the whole disk")
    d = node
    root = os.path.realpath(sysfs)
    while d.startswith(root) and d != root:
        if os.path.exists(os.path.join(d, "idVendor")) and os.path.exists(os.path.join(d, "idProduct")):
            ident = {"vid": _read(os.path.join(d, "idVendor")).lower(),
                     "pid": _read(os.path.join(d, "idProduct")).lower(), "usb_path": d, "block_path": node}
            for k in ("serial", "product", "manufacturer"):
                p = os.path.join(d, k)
                ident[k] = _read(p) if os.path.exists(p) else None
            if (ident["vid"], ident["pid"]) != (VID, PID):
                raise Refused(f"{name} is USB {ident['vid']}:{ident['pid']}, not the Circuit Rhythm {VID}:{PID}")
            return ident
        d = os.path.dirname(d)
    raise Refused(f"{name} is not a USB device")


def find_unit_disk(sysfs="/sys"):
    """Name of the one whole disk under /sys/block whose USB device is VID:PID. Refused if there are none or several."""
    found = []
    blockdir = os.path.join(sysfs, "block")
    for name in sorted(os.listdir(blockdir)) if os.path.isdir(blockdir) else ():
        dev = os.path.join(blockdir, name, "device")
        if not os.path.exists(dev):
            continue                                          # loop, dm, zram: no device behind them
        d, root = os.path.realpath(dev), os.path.realpath(sysfs)
        while d.startswith(root) and d != root:
            v, p = os.path.join(d, "idVendor"), os.path.join(d, "idProduct")
            if os.path.exists(v) and os.path.exists(p):
                if (_read(v).lower(), _read(p).lower()) == (VID, PID):
                    found.append(name)
                break
            d = os.path.dirname(d)
    if not found:
        raise Refused(f"no disk of a USB device {VID}:{PID} found; is the unit connected by USB and switched on?")
    if len(found) > 1:
        raise Refused(f"several disks of USB {VID}:{PID} ({', '.join(found)}); connect one unit only, or name the disk")
    return found[0]


def default_outdir(now=None):
    return "rhythm-backup-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def give_back(paths):
    """Under sudo, chown to the invoking user exactly the paths this run created or wrote (its own files and the
    directories it made), so they can read and move the backup. Anything else in OUTDIR is left alone."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or not uid or not gid:
        return
    for n in paths:
        os.chown(n, int(uid), int(gid), follow_symlinks=False)


def makedirs_tracked(path, written):
    """os.makedirs(path, exist_ok=True), appending every directory it actually created (outermost first) to written."""
    missing, p = [], os.path.abspath(path)
    while not os.path.lexists(p):
        missing.append(p)
        p = os.path.dirname(p)
    os.makedirs(path, exist_ok=True)
    written.extend(reversed(missing))


def geometry(name, sysfs="/sys"):
    base = os.path.join(sysfs, "class", "block", name)
    sectors = int(_read(os.path.join(base, "size")))          # always in 512-byte units
    lbs = int(_read(os.path.join(base, "queue", "logical_block_size")))
    ro = _read(os.path.join(base, "ro"))
    if sectors * 512 != DISK_SIZE or lbs != BLOCK:
        raise Refused(f"{name}: {sectors} x 512 B, logical block {lbs}; expected {DISK_SIZE // 512} x 512, block {BLOCK}")
    return {"bytes": sectors * 512, "logical_block_size": lbs, "sysfs_ro": ro}


def dev_numbers(name, sysfs="/sys"):
    """major:minor of the disk and of every partition under it."""
    base = os.path.join(sysfs, "class", "block", name)
    nums = {_read(os.path.join(base, "dev"))}
    for entry in os.listdir(base):
        p = os.path.join(base, entry)
        if os.path.exists(os.path.join(p, "partition")) and os.path.exists(os.path.join(p, "dev")):
            nums.add(_read(os.path.join(p, "dev")))
    return nums


def mounts_of(nums, mountinfo="/proc/self/mountinfo"):
    """[(mountpoint, per-mount options, super options)] for every mount of a device in nums (proc(5) mountinfo)."""
    found = []
    with open(mountinfo) as f:
        for line in f:
            left, _, right = line.partition(" - ")
            a, b = left.split(), right.split()
            if len(a) >= 6 and a[2] in nums:
                found.append((a[4], a[5], b[2] if len(b) > 2 else ""))
    return found


def check_not_rw(nums, mountinfo="/proc/self/mountinfo"):
    ms = mounts_of(nums, mountinfo)
    for mp, opts, sopts in ms:
        if "rw" in opts.split(",") or "rw" in sopts.split(","):
            raise Refused(f"mounted read-write at {mp} ({opts}; {sopts}); unmount it first")
    return ms


# ---------------------------------------------------------------- reading

def read_whole(path, size, direct):
    """Read exactly size bytes from offset 0. direct: O_DIRECT with an aligned buffer, so no page-cache copy."""
    flags = os.O_RDONLY | (os.O_DIRECT if direct else 0)
    fd = os.open(path, flags)
    try:
        step = 1 << 20
        buf = mmap.mmap(-1, step)                  # page-aligned, as O_DIRECT needs
        out = bytearray()
        while len(out) < size:
            n = os.preadv(fd, [buf], len(out))
            if n <= 0:
                raise IOError(f"short read at {len(out):#x}")
            out += buf[:min(n, size - len(out))]
        return bytes(out)
    finally:
        os.close(fd)


def differing_blocks(a, b, block=BLOCK):
    return [i for i in range(0, min(len(a), len(b)), block) if a[i:i + block] != b[i:i + block]]


def nor_equivalent(disk, serial):
    """Undo the firmware's serial substitution: the NOR bytes behind disk[0:VISIBLE].

    The firmware scans each 4 KB block it serves from its start up to the first NUL byte and replaces
    every "<SERIALUPN123>" that fits in the block with the 14-byte serial. The inverse is applied with the same
    limits. Returns (bytes, [disk offsets replaced])."""
    data = bytearray(disk[:VISIBLE])
    hits = []
    if serial:
        s = serial.encode()
        if len(s) != len(PLACEHOLDER):
            raise ValueError(f"serial {serial!r} is not {len(PLACEHOLDER)} bytes")
        for blk in range(0, VISIBLE, BLOCK):
            b = data[blk:blk + BLOCK]
            nul = b.find(0)
            limit = len(b) if nul < 0 else nul
            i = b.find(s)
            while 0 <= i and i < limit and i + len(s) <= len(b):
                data[blk + i:blk + i + len(s)] = PLACEHOLDER
                hits.append(blk + i)
                i = b.find(s, i + len(s))
    return bytes(data), hits


def fat_boot(sector):
    """Summary of a FAT boot sector (no partition table: the disk is a superfloppy)."""
    if len(sector) < 512 or sector[510:512] != b"\x55\xAA":
        return None
    return {"oem": sector[3:11].decode("latin-1"),
            "bytes_per_sector": int.from_bytes(sector[11:13], "little"),
            "sectors": int.from_bytes(sector[19:21], "little") or int.from_bytes(sector[32:36], "little"),
            "label": sector[43:54].decode("latin-1").rstrip(), "fs": sector[54:62].decode("latin-1").rstrip()}


def write_file(outdir, name, data, sums, written=None):
    with open(os.path.join(outdir, name), "wb") as f:
        f.write(data)
    sums[name] = sha(data)
    if written is not None:
        written.append(os.path.join(outdir, name))


def write_sums(outdir, sums, written=None):
    with open(os.path.join(outdir, "SHA256SUMS"), "w") as f:
        for name in sorted(sums):
            f.write(f"{sums[name]}  {name}\n")
    if written is not None:
        written.append(os.path.join(outdir, "SHA256SUMS"))


def load_sums(outdir):
    p = os.path.join(outdir, "SHA256SUMS")
    sums = {}
    if os.path.exists(p):
        for line in open(p):
            h, name = line.rstrip("\n").split("  ", 1)
            sums[name] = h
    return sums


def load_manifest(outdir):
    p = os.path.join(outdir, "manifest.json")
    return json.load(open(p)) if os.path.exists(p) else {"tool": "tools/norbackup.py", "parts": {}}


def save_manifest(outdir, m, written=None):
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)
        f.write("\n")
    if written is not None:
        written.append(os.path.join(outdir, "manifest.json"))


def backup_usb(dev, outdir, passes=2, serial=None, sysfs="/sys", mountinfo="/proc/self/mountinfo",
               test_block_name=None, log=print, written=None):
    """Back up the USB-visible NOR. test_block_name lets the tests point the sysfs checks at a fake tree while dev
    is a regular image file; on a real run dev must be a block device. written, if given, receives every path this
    run created or wrote (directories it made, then its files), for give_back()."""
    written = [] if written is None else written
    if passes < 2:
        raise Refused("at least two passes are needed to tell a stable read from a flaky one")
    st = os.stat(dev)
    if test_block_name is None:
        if not stat.S_ISBLK(st.st_mode):
            raise Refused(f"{dev} is not a block device")
        name = os.path.basename(os.path.realpath(dev))
    else:
        if not stat.S_ISREG(st.st_mode):
            raise Refused("test_block_name is only for image files")
        name = test_block_name
    ident = usb_identity(name, sysfs)
    geo = geometry(name, sysfs)
    ms = check_not_rw(dev_numbers(name, sysfs), mountinfo)
    serial = serial or ident.get("serial")
    log(f"{dev}: USB {ident['vid']}:{ident['pid']} {ident.get('product')!r} serial {serial!r}, "
        f"{geo['bytes']} bytes, sysfs ro={geo['sysfs_ro']}, mounts {ms or 'none'}")

    direct = test_block_name is None
    images, t0 = [], time.time()
    for p in range(passes):
        img = read_whole(dev, DISK_SIZE, direct)
        log(f"pass {p + 1}: sha256 {sha(img)}")
        images.append(img)
    bad = sorted({b for img in images[1:] for b in differing_blocks(images[0], img)})
    if bad:
        raise IOError(f"passes differ in {len(bad)} block(s), first at disk {bad[0]:#x}; is the unit idle?")
    disk = images[0]

    nor, hits = nor_equivalent(disk, serial)
    checks = {
        "tail_zero": disk[VISIBLE:] == bytes(DISK_SIZE - VISIBLE),
        "welcome_fat": fat_boot(disk[0:512]),
        "content_fat": fat_boot(disk[0x40000:0x40000 + 512]),
        "serial_hits": len(hits),
    }
    makedirs_tracked(outdir, written)
    sums = load_sums(outdir)
    write_file(outdir, "usb-disk.img", disk, sums, written)
    write_file(outdir, nor_file(USB_BASE, NOR_SIZE), nor, sums, written)
    blocks = "".join(f"{sha(disk[i:i + BLOCK])}  disk+{i:#09x} nor+{USB_BASE + i:#09x}\n"
                     for i in range(0, DISK_SIZE, BLOCK))
    with open(os.path.join(outdir, "usb-blocks.sha256"), "w") as f:
        f.write(blocks)
    written.append(os.path.join(outdir, "usb-blocks.sha256"))
    sums["usb-blocks.sha256"] = sha(blocks.encode())
    write_sums(outdir, sums, written)

    m = load_manifest(outdir)
    m["parts"]["usb"] = {
        "device": dev, "identity": ident, "geometry": geo, "mounts": ms, "serial_used": serial,
        "passes": passes, "pass_sha256": [sha(i) for i in images], "started": t0, "finished": time.time(),
        "disk_to_nor": f"nor = disk + {USB_BASE:#x} for disk < {VISIBLE:#x}; disk >= {VISIBLE:#x} reads as zeros",
        "serial_offsets_disk": [f"{h:#x}" for h in hits], "checks": checks,
        "regions_sha256": {r: sha(nor[max(a, USB_BASE) - USB_BASE:e - USB_BASE])
                           for r, a, e in REGIONS if e > USB_BASE},
    }
    save_manifest(outdir, m, written)
    problems = [k for k in ("tail_zero", "welcome_fat", "content_fat") if not checks[k]]
    if problems:
        log(f"WARNING: unexpected content: {', '.join(problems)} (files kept)")
    log(f"wrote {outdir}: usb-disk.img, nor-0x094000-0x2000000.bin, usb-blocks.sha256, SHA256SUMS, manifest.json")
    return m, problems


def nor_file(start, end):
    return f"nor-{start:#08x}-{end:#08x}.bin"


def verify(outdir, log=print):
    sums = load_sums(outdir)
    if not sums:
        raise IOError(f"{outdir}: no SHA256SUMS")
    bad = [n for n, h in sums.items() if sha(open(os.path.join(outdir, n), "rb").read()) != h]
    for n in sorted(sums):
        log(f"{'BAD' if n in bad else 'ok '} {n}")
    return not bad


def assemble(outdir, log=print):
    lo = open(os.path.join(outdir, nor_file(0, HIDDEN_END)), "rb").read()
    hi = open(os.path.join(outdir, nor_file(USB_BASE, NOR_SIZE)), "rb").read()
    if len(lo) != HIDDEN_END or len(hi) != VISIBLE:
        raise ValueError("part sizes do not match the layout")
    full = lo + hi
    sums = load_sums(outdir)
    write_file(outdir, "nor-full.bin", full, sums)
    write_sums(outdir, sums)
    m = load_manifest(outdir)
    m["parts"]["full"] = {"sha256": sha(full), "regions_sha256": {r: sha(full[a:e]) for r, a, e in REGIONS}}
    save_manifest(outdir, m)
    log(f"nor-full.bin sha256 {sha(full)}")
    return full


# ---------------------------------------------------------------- FAT16 extraction from a backup image

CONTENT_OFFSET = 0x40000            # disk offset of the FAT16 content volume


class FatError(Exception):
    """The image is not a FAT16 volume this reader understands."""


def _safe_name(name):
    name = name.rstrip(" .")
    if not name or name in (".", "..") or any(c in name for c in '/\\\0') or any(ord(c) < 32 for c in name):
        raise FatError(f"unsafe file name {name!r}")
    return name


def sfn_checksum(short11):
    """The checksum a long-name entry carries for the 11-byte short name that follows it."""
    c = 0
    for b in short11:
        c = (((c & 1) << 7) + (c >> 1) + b) & 0xFF
    return c


def _warn(msg):
    print(f"extract: {msg}", file=sys.stderr)


def fat16_files(vol, warn=_warn):
    """Yield (path parts, bytes) for every file of the FAT16 volume vol (bytes). Read-only; raises FatError on any
    inconsistency, so a damaged volume never yields a partial listing that looks complete."""
    if len(vol) < 512 or vol[510:512] != b"\x55\xAA":
        raise FatError("no boot sector signature")
    le = lambda a, n: int.from_bytes(vol[a:a + n], "little")
    bps, spc, rsv, nfats, nroot = le(11, 2), vol[13], le(14, 2), vol[16], le(17, 2)
    total16, total32 = le(19, 2), le(32, 4)
    total = total16 or total32
    spf = le(22, 2)
    if bps not in (512, 1024, 2048, 4096):
        raise FatError(f"BPB: {bps} bytes per sector")
    if spc == 0 or spc & (spc - 1) or spc * bps > 0x10000:
        raise FatError(f"BPB: {spc} sectors per cluster")
    if rsv < 1 or nfats < 1 or spf < 1:
        raise FatError(f"BPB: {rsv} reserved sectors, {nfats} FATs, {spf} sectors per FAT")
    if nroot == 0 or nroot * 32 % bps:
        raise FatError(f"BPB: {nroot} root entries is not a FAT16 root directory of whole sectors")
    if not total or (total16 and total32 and total16 != total32):
        raise FatError(f"BPB: total sectors {total16} / {total32}")
    root_start = (rsv + nfats * spf) * bps
    data_start = root_start + nroot * 32
    if data_start >= total * bps:
        raise FatError("BPB: no data area")
    csize = spc * bps
    nclusters = (total * bps - data_start) // csize
    if not 4085 <= nclusters < 65525:
        raise FatError(f"{nclusters} clusters: not FAT16")
    if spf * bps < 2 * (nclusters + 2):
        raise FatError(f"BPB: {spf} sectors per FAT cannot map {nclusters} clusters")
    if total * bps > len(vol):
        raise FatError("volume larger than the image")
    fat = vol[rsv * bps:(rsv + spf) * bps]

    def chain(first, what):
        """Clusters of a chain. Only an end-of-chain mark (0xFFF8..0xFFFF) ends it; a free, reserved or bad entry,
        a link outside the volume and a loop are errors."""
        out, c, seen = [], first, set()
        while True:
            if not 2 <= c < nclusters + 2:
                raise FatError(f"{what}: cluster chain reaches {c:#06x} after {len(out)} cluster(s)"
                               + (" (free)" if c == 0 else " (reserved)" if c == 1 else
                                  " (bad cluster mark)" if c == 0xFFF7 else " (outside the volume)"))
            if c in seen:
                raise FatError(f"{what}: cluster chain loops at {c:#06x}")
            seen.add(c)
            out.append(c)
            c = int.from_bytes(fat[2 * c:2 * c + 2], "little")
            if c >= 0xFFF8:
                return out

    def read_chain(first, what):
        return b"".join(vol[data_start + (c - 2) * csize:data_start + (c - 1) * csize] for c in chain(first, what))

    def walk(raw, parts, depth):
        if depth > 32:
            raise FatError("directories nested too deep")
        lfn, broken = [], False                                 # pending long-name pieces: (seq, checksum, text)
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn, broken = [], False
                continue
            if e[11] & 0x3F == 0x0F:                             # long-name piece
                seq, chk = e[0] & 0x1F, e[13]
                text = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace")
                if e[0] & 0x40:                                  # first stored piece = last of the name
                    if lfn:
                        warn(f"{'/'.join(parts) or '/'}: long-name pieces without a short entry ignored")
                    lfn, broken = [(seq, chk, text)], not 1 <= seq <= 20 or e[26:28] != b"\0\0"
                elif lfn and not broken and seq == lfn[-1][0] - 1 and chk == lfn[0][1] and e[26:28] == b"\0\0":
                    lfn.append((seq, chk, text))
                else:
                    lfn, broken = lfn or [(seq, chk, text)], True
                continue
            attr = e[11]
            if attr & 0x08:                                      # volume label
                lfn, broken = [], False
                continue
            base, ext = e[0:8].decode("latin-1").rstrip(), e[8:11].decode("latin-1").rstrip()
            short = (base.lower() if e[12] & 0x08 else base), (ext.lower() if e[12] & 0x10 else ext)  # NT case bits
            if short[0] in (".", ".."):
                lfn, broken = [], False
                continue
            short_name = short[0] + ("." + short[1] if short[1] else "")
            long = ""
            if lfn:
                if broken or lfn[-1][0] != 1 or lfn[0][1] != sfn_checksum(e[0:11]):
                    warn(f"{'/'.join(parts + [short_name])}: long name ignored (broken sequence or checksum "
                         f"mismatch), using the short name")
                else:
                    long = "".join(t for _, _, t in reversed(lfn)).split("\0")[0]
            lfn, broken = [], False
            name = _safe_name(long or short_name)
            path = "/".join(parts + [name])
            first, size = int.from_bytes(e[26:28], "little"), int.from_bytes(e[28:32], "little")
            if attr & 0x10:
                yield from walk(read_chain(first, path), parts + [name], depth + 1)
            else:
                data = read_chain(first, path) if first else b""
                if len(data) < size:
                    raise FatError(f"{path}: chain shorter than the size")
                yield parts + [name], data[:size]

    yield from walk(vol[root_start:root_start + nroot * 32], [], 0)


def extract(outdir, log=print):
    """Copy the files of the content volume in outdir/usb-disk.img to outdir/files/ (which must not exist yet)."""
    img = os.path.join(outdir, "usb-disk.img")
    sums = load_sums(outdir)
    disk = open(img, "rb").read()
    if sums.get("usb-disk.img") != sha(disk):
        raise IOError(f"{img} does not match SHA256SUMS; run verify")
    dest = os.path.join(outdir, "files")
    if os.path.lexists(dest):
        raise FileExistsError(f"{dest} already exists; move it away first")
    os.makedirs(dest)
    n = 0
    for parts, data in fat16_files(disk[CONTENT_OFFSET:]):
        path = os.path.join(dest, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as f:
            f.write(data)
        n += 1
    log(f"extracted {n} files to {dest}")
    return n


# ---------------------------------------------------------------- SysEx NOR read (needs a build with command 7B)

def a7(value, n=5):
    return bytes((value >> (7 * (n - 1 - i))) & 0x7F for i in range(n))


def u7(bs):
    v = 0
    for b in bs:
        if b > 0x7F:
            raise ValueError("not a 7-bit byte")
        v = (v << 7) | b
    return v


def nor_request(addr):
    if addr % NOR_CHUNK or not 0 <= addr < NOR_SIZE:
        raise ValueError(f"bad NOR address {addr:#x}")
    return SYSEX_PRE + bytes([CMD_NOR]) + a7(addr) + b"\xF7"


def parse_nor_reply(msg, addr):
    """(status, data or None) for a reply to nor_request(addr); ValueError if it is not one."""
    if not msg.startswith(SYSEX_PRE + bytes([CMD_NOR])) or msg[-1] != 0xF7:
        raise ValueError(f"not a 7B reply: {msg.hex(' ')}")
    if len(msg) not in (NOR_REPLY_OK, NOR_REPLY_ERR):
        raise ValueError(f"7B reply of {len(msg)} bytes")
    got = u7(msg[7:12])
    if got != addr:
        raise ValueError(f"7B reply for {got:#x}, asked {addr:#x}")
    status = msg[12]
    if status == ST_OK:
        if len(msg) != NOR_REPLY_OK:
            raise ValueError("status ok without data")
        nib = msg[13:-1]
        if any(b > 0x0F for b in nib):
            raise ValueError("data byte above 0x0F")
        return status, bytes((nib[2 * k] << 4) | nib[2 * k + 1] for k in range(NOR_CHUNK))
    if len(msg) != NOR_REPLY_ERR or status > ST_NOMEDIA:
        raise ValueError(f"bad 7B status {status} / length {len(msg)}")
    return status, None


def parse_version_reply(msg):
    """F0 00 20 29 00 70 <boot b0 b1 n2 n1 n0> <app b0 b1 n2 n1 n0> 40 02 F7 -> (boot, app)."""
    if len(msg) != 19 or not msg.startswith(bytes.fromhex("F0 00 20 29 00 70")) or msg[-1] != 0xF7:
        raise ValueError(f"not a version reply: {msg.hex(' ')}")
    return u7(msg[8:11]), u7(msg[13:16])


class Port:
    """Non-blocking MIDI fd (rawmidi device, or a socket in tests). Drops realtime bytes."""

    def __init__(self, fd):
        self.fd, self.buf = fd, b""

    def drain(self, quiet=0.3):
        end = time.monotonic() + quiet
        while time.monotonic() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    os.read(self.fd, 4096)
                except BlockingIOError:
                    pass
        self.buf = b""

    def send(self, msg, timeout=2.0):
        end, view = time.monotonic() + timeout, memoryview(msg)
        while view:
            if time.monotonic() > end:
                raise TimeoutError("write timed out")
            if select.select([], [self.fd], [], 0.1)[1]:
                try:
                    view = view[os.write(self.fd, view):]
                except BlockingIOError:
                    pass

    def recv(self, prefix, timeout):
        """Next complete SysEx message starting with prefix; others are dropped. None on timeout."""
        end = time.monotonic() + timeout
        while True:
            while True:
                i = self.buf.find(b"\xF0")
                j = self.buf.find(b"\xF7", i) if i >= 0 else -1
                if i < 0 or j < 0:
                    break
                msg, self.buf = self.buf[i:j + 1], self.buf[j + 1:]
                if msg.startswith(prefix):
                    return msg
            left = end - time.monotonic()
            if left <= 0:
                return None
            if select.select([self.fd], [], [], left)[0]:
                try:
                    self.buf += bytes(b for b in os.read(self.fd, 4096) if b < 0xF8)
                except BlockingIOError:
                    pass


def check_build(port, expect, timeout=1.0):
    port.drain()
    port.send(VERSION_REQ)
    msg = port.recv(bytes.fromhex("F0 00 20 29 00 70"), timeout)
    if msg is None:
        raise Refused("no reply to the version query")
    boot, app = parse_version_reply(msg)
    if app != expect:
        raise Refused(f"unit runs application build {app}, not {expect}; command 7B is not known to exist there")
    return boot, app


def sysex_read(port, start, size, timeout=1.0, busy_retries=50, progress=None):
    if start % NOR_CHUNK or size % NOR_CHUNK or size <= 0 or start + size > NOR_SIZE:
        raise ValueError("range must be 64-byte aligned and inside the NOR")
    out = bytearray()
    for addr in range(start, start + size, NOR_CHUNK):
        for attempt in range(busy_retries + 1):
            port.send(nor_request(addr))
            msg = port.recv(SYSEX_PRE + bytes([CMD_NOR]), timeout)
            if msg is None:
                raise TimeoutError(f"no 7B reply for {addr:#x}")
            status, data = parse_nor_reply(msg, addr)
            if status != ST_BUSY:
                break
            time.sleep(0.01)
        if status != ST_OK:
            raise IOError(f"7B status {status} at {addr:#x}")
        out += data
        if progress and (addr - start) % 0x10000 == 0:
            progress(addr)
    return bytes(out)


def backup_sysex(port, outdir, expect_build, start=0, size=HIDDEN_END, passes=2, log=print):
    if passes < 2:
        raise Refused("at least two passes")
    boot, app = check_build(port, expect_build)
    log(f"bootloader build {boot}, application build {app}")
    reads = []
    for p in range(passes):
        data = sysex_read(port, start, size, progress=lambda a: log(f"  pass {p + 1} {a:#08x}"))
        log(f"pass {p + 1}: sha256 {sha(data)}")
        reads.append(data)
    bad = sorted({b for r in reads[1:] for b in differing_blocks(reads[0], r, NOR_CHUNK)})
    if bad:
        raise IOError(f"passes differ in {len(bad)} chunk(s), first at NOR {start + bad[0]:#x}")
    os.makedirs(outdir, exist_ok=True)
    sums = load_sums(outdir)
    name = nor_file(start, start + size)
    write_file(outdir, name, reads[0], sums)
    write_sums(outdir, sums)
    m = load_manifest(outdir)
    m["parts"][name] = {"via": "SysEx 7B", "boot_build": boot, "app_build": app, "start": start, "size": size,
                        "passes": passes, "sha256": sha(reads[0]), "finished": time.time()}
    save_manifest(outdir, m)
    log(f"wrote {os.path.join(outdir, name)}")
    return reads[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("usb")
    u.add_argument("device", help='/dev/sdX, or "auto" to find the unit')
    u.add_argument("outdir", nargs="?", help="default: ./rhythm-backup-YYYYMMDD-HHMMSS")
    u.add_argument("--passes", type=int, default=2)
    u.add_argument("--serial", help="string the firmware substitutes for <SERIALUPN123> (default: USB serial)")
    v = sub.add_parser("verify")
    v.add_argument("outdir")
    a = sub.add_parser("assemble")
    a.add_argument("outdir")
    x = sub.add_parser("extract", help="copy the content volume's files from OUTDIR/usb-disk.img to OUTDIR/files/")
    x.add_argument("outdir")
    s = sub.add_parser("sysex", help="EXPERIMENTAL: needs a devkit build with command 7B")
    s.add_argument("--expect-build", type=int, required=True,
                   help="application build number that has command 7B (none exists yet)")
    s.add_argument("device")
    s.add_argument("outdir")
    s.add_argument("--start", type=lambda x: int(x, 0), default=0)
    s.add_argument("--size", type=lambda x: int(x, 0), default=HIDDEN_END)
    s.add_argument("--passes", type=int, default=2)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "usb":
            device = args.device
            if device == "auto":
                device = "/dev/" + find_unit_disk()
                print(f"auto: found the Circuit Rhythm at {device}")
            outdir = args.outdir or default_outdir()
            written = []
            try:
                _, problems = backup_usb(device, outdir, args.passes, args.serial, written=written)
            finally:
                give_back(written)
            print(f"backup in {os.path.abspath(outdir)}")
            return 3 if problems else 0
        if args.cmd == "extract":
            try:
                extract(args.outdir)
            except (FatError, OSError) as e:
                print(f"extract: {e}", file=sys.stderr)
                return 1
            return 0
        if args.cmd == "verify":
            return 0 if verify(args.outdir) else 1
        if args.cmd == "assemble":
            assemble(args.outdir)
            return 0
        fd = os.open(args.device, os.O_RDWR | os.O_NONBLOCK)
        try:
            backup_sysex(Port(fd), args.outdir, args.expect_build, args.start, args.size, args.passes)
        finally:
            os.close(fd)
        return 0
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
