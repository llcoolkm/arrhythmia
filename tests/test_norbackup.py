"""tools/norbackup.py against synthetic images, a fake sysfs tree and a fake unit on a socketpair. No device access.

    uv run python -I tests/test_norbackup.py
"""
import os
import random
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import norbackup as nb  # noqa: E402

SERIAL = "TEST0SERIAL123"          # 14 bytes, like the unit's
assert len(SERIAL) == len(nb.PLACEHOLDER)


def boot_sector(fs, sectors):
    s = bytearray(512)
    s[0:3] = b"\xEB\x3C\x90"
    s[3:11] = b"mkfs.fat"
    s[11:13] = (4096).to_bytes(2, "little")
    s[19:21] = sectors.to_bytes(2, "little")
    s[43:54] = b"RHYTHM     "
    s[54:62] = fs.ljust(8).encode()
    s[510:512] = b"\x55\xAA"
    return bytes(s)


def make_nor():
    nor = bytearray(random.Random(1).randbytes(nb.NOR_SIZE))
    nor[0x094000:0x094000 + 512] = boot_sector("FAT12", 64)
    nor[0x0D4000:0x0D4000 + 512] = boot_sector("FAT16", 7980)
    blk = 0x094000 + 0xB000                      # like the welcome page: placeholder in text, before any NUL
    text = b"method=readme&upn=" + nb.PLACEHOLDER + b'"><font>' + nb.PLACEHOLDER + b"."
    nor[blk:blk + 0x1000] = (text + b"x" * 0x1000)[:0x1000]
    blk2 = 0x094000 + 0xC000                     # placeholder after a NUL: the firmware leaves it alone
    nor[blk2:blk2 + 0x1000] = (b"abc\x00" + nb.PLACEHOLDER + b"y" * 0x1000)[:0x1000]
    return bytes(nor)


def firmware_disk(nor, serial=SERIAL):
    """What the stock USB disk read serves: NOR from 0x94000 in 4 KB blocks, serial substituted up to the
    first NUL of each block, zeros past the chip end."""
    out = bytearray(nb.DISK_SIZE)
    for d in range(0, nb.VISIBLE, nb.BLOCK):
        b = bytearray(nor[nb.USB_BASE + d:nb.USB_BASE + d + nb.BLOCK])
        nul = b.find(0)
        limit = len(b) if nul < 0 else nul
        i = b.find(nb.PLACEHOLDER)
        while 0 <= i < limit:
            b[i:i + len(nb.PLACEHOLDER)] = serial.encode()
            i = b.find(nb.PLACEHOLDER, i + 1)
        out[d:d + nb.BLOCK] = b
    return bytes(out)


def fake_sysfs(root, name="sdz", vid="1235", pid="0138", sectors=65536, lbs=4096, partition=False):
    usb = os.path.join(root, "devices", "pci0000:00", "usb3", "3-2")
    disk = os.path.join(usb, "3-2:1.2", "host6", "target6:0:0", "6:0:0:0", "block", name)
    os.makedirs(os.path.join(disk, "queue"))
    for f, v in (("idVendor", vid), ("idProduct", pid), ("serial", SERIAL), ("product", "Circuit Rhythm")):
        open(os.path.join(usb, f), "w").write(v + "\n")
    for f, v in (("size", str(sectors)), ("ro", "1"), ("dev", "8:208"), ("queue/logical_block_size", str(lbs))):
        open(os.path.join(disk, f), "w").write(v + "\n")
    part = os.path.join(disk, name + "1")
    os.makedirs(part)
    open(os.path.join(part, "partition"), "w").write("1\n")
    open(os.path.join(part, "dev"), "w").write("8:209\n")
    os.makedirs(os.path.join(root, "class", "block"))
    os.symlink(disk, os.path.join(root, "class", "block", name))
    os.symlink(part, os.path.join(root, "class", "block", name + "1"))


def mountinfo(path, lines):
    open(path, "w").write("".join(lines))


RO_MOUNT = "36 25 8:208 / /media/km/RHYTHM ro,nosuid,nodev relatime - vfat /dev/sdz ro,fmask=0022\n"
RW_MOUNT = "36 25 8:209 / /mnt/x rw,relatime - vfat /dev/sdz1 rw\n"
OTHER = "22 1 259:2 / / rw,relatime - ext4 /dev/nvme0n1p2 rw\n"

NOR = make_nor()
DISK = firmware_disk(NOR)


class Env:
    def __init__(self, **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.sysfs = os.path.join(self.root, "sys")
        fake_sysfs(self.sysfs, **kw)
        self.mi = os.path.join(self.root, "mountinfo")
        mountinfo(self.mi, [OTHER, RO_MOUNT])
        self.img = os.path.join(self.root, "sdz.img")
        open(self.img, "wb").write(DISK)
        self.out = os.path.join(self.root, "out")

    def run(self, name="sdz", **kw):
        return nb.backup_usb(self.img, self.out, sysfs=self.sysfs, mountinfo=self.mi, test_block_name=name,
                             log=lambda *_: None, **kw)


def refused(fn):
    try:
        fn()
    except nb.Refused:
        return True
    return False


def test_usb_backup_roundtrip():
    e = Env()
    m, problems = e.run()
    assert problems == [], problems
    part = m["parts"]["usb"]
    assert part["serial_used"] == SERIAL
    assert part["serial_offsets_disk"] == ["0xb012", "0xb028"], part["serial_offsets_disk"]
    assert part["checks"]["tail_zero"] and part["checks"]["content_fat"]["sectors"] == 7980
    assert open(os.path.join(e.out, "usb-disk.img"), "rb").read() == DISK
    nor = open(os.path.join(e.out, "nor-0x094000-0x2000000.bin"), "rb").read()
    assert nor == NOR[nb.USB_BASE:], "NOR-equivalent image differs from the synthetic NOR"
    assert part["regions_sha256"]["content"] == nb.sha(NOR[0xD4000:])
    assert nb.verify(e.out, log=lambda *_: None)
    blocks = open(os.path.join(e.out, "usb-blocks.sha256")).read().splitlines()
    assert len(blocks) == nb.DISK_BLOCKS and blocks[1].endswith("disk+0x0001000 nor+0x0095000")
    with open(os.path.join(e.out, "usb-disk.img"), "r+b") as f:
        f.seek(0x123456)
        f.write(b"\x00")
    assert not nb.verify(e.out, log=lambda *_: None)


def test_refusals():
    assert refused(lambda: Env(vid="0781").run())                 # another USB stick
    assert refused(lambda: Env(sectors=65536 * 2).run())          # geometry not the stock one
    assert refused(lambda: Env(lbs=512).run())
    assert refused(lambda: Env().run(name="sdz1"))                # a partition, not the disk
    e = Env()
    mountinfo(e.mi, [OTHER, RO_MOUNT, RW_MOUNT])                  # partition mounted rw
    assert refused(e.run)
    e = Env()
    assert refused(lambda: nb.backup_usb(e.img, e.out, sysfs=e.sysfs, mountinfo=e.mi, log=lambda *_: None))
    assert refused(lambda: e.run(passes=1))
    assert not os.path.exists(e.out)


def test_unstable_reads():
    e = Env()
    real, calls = nb.read_whole, []

    def flaky(path, size, direct):
        data = bytearray(real(path, size, direct))
        calls.append(1)
        if len(calls) == 2:
            data[0x50000] ^= 1                                    # a block that came back different
        return bytes(data)
    nb.read_whole = flaky
    try:
        e.run()
    except IOError as x:
        assert "0x50000" in str(x), x
    else:
        raise AssertionError("differing passes accepted")
    finally:
        nb.read_whole = real
    assert not os.path.exists(e.out)


def test_nor_equivalent_limits():
    blk = bytearray(b"z" * nb.BLOCK)
    blk[nb.BLOCK - 7:] = SERIAL.encode()[:7]                     # cut by the block end: never substituted
    disk = bytes(blk) + bytes(nb.DISK_SIZE - nb.BLOCK)
    nor, hits = nb.nor_equivalent(disk, SERIAL)
    assert hits == [] and nor[:nb.BLOCK] == bytes(blk)
    try:
        nb.nor_equivalent(disk, "short")
    except ValueError:
        pass
    else:
        raise AssertionError("bad serial length accepted")


# ---------------------------------------------------------------- SysEx 7B reader

def reply_7b(addr, status, data=None):
    body = b"".join(bytes((b >> 4, b & 0x0F)) for b in data) if status == nb.ST_OK else b""
    return nb.SYSEX_PRE + bytes([nb.CMD_NOR]) + nb.a7(addr) + bytes([status]) + body + b"\xF7"


def version_reply(app):
    return (bytes.fromhex("F0 00 20 29 00 70 00 03 00 25 3F 01 01") + nb.a7(app, 3) + bytes.fromhex("40 02 F7"))


def fake_unit(sock, nor, app, mode):
    buf, busy = b"", {}
    while True:
        try:
            chunk = sock.recv(4096)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while True:
            i, j = buf.find(b"\xF0"), buf.find(b"\xF7")
            if i < 0 or j < 0:
                break
            req, buf = buf[i:j + 1], buf[j + 1:]
            if req == nb.VERSION_REQ:
                sock.sendall(b"\xF8" + version_reply(app))
                continue
            assert len(req) == nb.NOR_REQ_LEN and req[:7] == nb.SYSEX_PRE + bytes([nb.CMD_NOR]), req.hex(" ")
            addr = nb.u7(req[7:12])
            if mode == "busy" and addr < 0x200 and busy.get(addr, 0) < 2:              # NOR held by the Storage task: try again
                busy[addr] = busy.get(addr, 0) + 1
                sock.sendall(reply_7b(addr, nb.ST_BUSY))
                continue
            if mode == "wrong-addr":
                sock.sendall(reply_7b(addr + 64, nb.ST_OK, nor[addr:addr + 64]))
                continue
            msg = reply_7b(addr, nb.ST_OK, nor[addr:addr + 64])
            sock.sendall(msg[:20])                                    # split, with a clock byte in between
            sock.sendall(b"\xF8" + msg[20:])


def with_unit(app, mode, fn):
    a, b = socket.socketpair()
    a.setblocking(False)
    t = threading.Thread(target=fake_unit, args=(b, NOR, app, mode), daemon=True)
    t.start()
    try:
        return fn(nb.Port(a.fileno()))
    finally:
        a.close()
        b.close()


def test_parse_replies():
    data = bytes(range(64))
    assert nb.parse_nor_reply(reply_7b(0x40, 0, data), 0x40) == (0, data)
    assert nb.parse_nor_reply(reply_7b(0x40, nb.ST_BUSY), 0x40) == (nb.ST_BUSY, None)
    assert len(reply_7b(0, 0, data)) == nb.NOR_REPLY_OK == 142 and len(reply_7b(0, 1)) == nb.NOR_REPLY_ERR
    for bad in (reply_7b(0x80, 0, data), reply_7b(0x40, 0, data)[:-2] + b"\xF7",
                reply_7b(0x40, 0, data).replace(b"\x00\x01", b"\x10\x01", 1), reply_7b(0x40, 9)):
        try:
            nb.parse_nor_reply(bad, 0x40)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad.hex(' ')}")
    assert nb.parse_version_reply(bytes.fromhex("F0 00 20 29 00 70 00 03 00 25 3F 01 01 00 2C 4A 40 02 F7")) \
        == (4799, 5706)
    assert nb.nor_request(0x93FC0) == bytes.fromhex("F0 00 20 29 00 60 7B 00 00 24 7F 40 F7")


def test_sysex_backup_and_assemble():
    e = Env()
    e.run()
    with_unit(6099, "busy", lambda p: nb.backup_sysex(p, e.out, 6099, log=lambda *_: None))
    assert open(os.path.join(e.out, "nor-0x000000-0x094000.bin"), "rb").read() == NOR[:nb.HIDDEN_END]
    full = nb.assemble(e.out, log=lambda *_: None)
    assert full == NOR
    assert nb.verify(e.out, log=lambda *_: None)
    m = nb.load_manifest(e.out)
    assert m["parts"]["full"]["regions_sha256"]["staging"] == nb.sha(NOR[:0x80000])
    assert refused(lambda: with_unit(6011, "ok", lambda p: nb.backup_sysex(p, e.out, 6099, log=lambda *_: None)))
    try:
        with_unit(6099, "wrong-addr", lambda p: nb.sysex_read(p, 0, 0x100, timeout=0.5))
    except ValueError:
        pass
    else:
        raise AssertionError("reply for another address accepted")


# ---------------------------------------------------------------- usb auto: finding the unit in a fake sysfs

def block_tree(root, disks):
    """A fake /sys with /sys/block/<name>/device links. disks: [(name, vid, pid) or (name, None, None)]."""
    for n, (name, vid, pid) in enumerate(disks):
        if vid is None:                                       # loop/dm style: no device link
            os.makedirs(os.path.join(root, "devices", "virtual", "block", name))
            dev = None
        else:
            usb = os.path.join(root, "devices", "pci0000:00", "usb3", f"3-{n}")
            dev = os.path.join(usb, f"3-{n}:1.2", f"host{n}", f"target{n}:0:0", f"{n}:0:0:0")
            os.makedirs(os.path.join(dev, "block", name))
            open(os.path.join(usb, "idVendor"), "w").write(vid + "\n")
            open(os.path.join(usb, "idProduct"), "w").write(pid + "\n")
            os.symlink(dev, os.path.join(dev, "block", name, "device"))
        os.makedirs(os.path.join(root, "block"), exist_ok=True)
        target = (os.path.join(dev, "block", name) if dev
                  else os.path.join(root, "devices", "virtual", "block", name))
        os.symlink(target, os.path.join(root, "block", name))
    os.makedirs(os.path.join(root, "block"), exist_ok=True)


def find_in(disks):
    with tempfile.TemporaryDirectory() as t:
        block_tree(t, disks)
        return nb.find_unit_disk(t)


def test_find_unit_disk():
    assert find_in([("loop0", None, None), ("sda", "0781", "5583"), ("sdc", "1235", "0138")]) == "sdc"
    assert find_in([("sdb", "1235", "0138")]) == "sdb"
    assert refused(lambda: find_in([("loop0", None, None), ("sda", "0781", "5583")]))   # none
    assert refused(lambda: find_in([]))
    assert refused(lambda: find_in([("sdb", "1235", "0138"), ("sdc", "1235", "0138")]))  # two units
    assert refused(lambda: find_in([("sdb", "1235", "0139")]))                           # another Novation product


def test_usb_auto_and_default_outdir():
    assert nb.default_outdir(0).startswith("rhythm-backup-") and len(nb.default_outdir(0)) == len("rhythm-backup-") + 15
    calls = []
    real_find, real_backup = nb.find_unit_disk, nb.backup_usb
    nb.find_unit_disk = lambda sysfs="/sys": "sdz"
    nb.backup_usb = lambda dev, outdir, passes, serial, written: calls.append((dev, outdir, passes)) or ({}, [])
    try:
        with tempfile.TemporaryDirectory() as t:
            assert nb.main(["usb", "auto", os.path.join(t, "o")]) == 0
            cwd = os.getcwd()
            os.chdir(t)
            try:
                assert nb.main(["usb", "auto"]) == 0
            finally:
                os.chdir(cwd)
        assert calls[0] == ("/dev/sdz", os.path.join(t, "o"), 2), calls
        assert calls[1][0] == "/dev/sdz" and calls[1][1].startswith("rhythm-backup-"), calls
        nb.find_unit_disk = lambda sysfs="/sys": (_ for _ in ()).throw(nb.Refused("none"))
        assert nb.main(["usb", "auto", "x"]) == 2 and len(calls) == 2
    finally:
        nb.find_unit_disk, nb.backup_usb = real_find, real_backup


# ---------------------------------------------------------------- extract: a synthetic FAT16 volume

BPS, RSV, NFATS, SPF, NROOT, NCLUST = 512, 1, 2, 17, 512, 4200


def dirent(name8, ext3, attr, first, size, case=0):
    e = bytearray(32)
    e[0:8], e[8:11] = name8.ljust(8).encode(), ext3.ljust(3).encode()
    e[11], e[12] = attr, case
    e[26:28], e[28:32] = first.to_bytes(2, "little"), size.to_bytes(4, "little")
    return bytes(e)


def lfn_entries(name, short11):
    """Long-name entries for name, carrying the checksum of the 11-byte short name that will follow them."""
    assert len(short11) == 11
    chk = nb.sfn_checksum(short11)
    u = name.encode("utf-16-le")
    if len(name) % 13:
        u = (u + b"\0\0").ljust(13 * 2 * (-(-len(name) // 13)), b"\xFF")
    pieces = [u[i:i + 26] for i in range(0, len(u), 26)]
    out = []
    for k, p in enumerate(pieces, 1):
        e = bytearray(32)
        e[0] = k | (0x40 if k == len(pieces) else 0)
        e[1:11], e[11], e[13], e[14:26], e[28:32] = p[0:10], 0x0F, chk, p[10:22], p[22:26]
        out.append(bytes(e))
    return b"".join(reversed(out))


class Fat16:
    def __init__(self):
        self.total = RSV + NFATS * SPF + NROOT * 32 // BPS + NCLUST
        self.vol = bytearray(self.total * BPS)
        b = self.vol
        b[0:3], b[3:11] = b"\xEB\x3C\x90", b"mkfs.fat"
        b[11:13], b[13], b[14:16], b[16] = BPS.to_bytes(2, "little"), 1, RSV.to_bytes(2, "little"), NFATS
        b[17:19], b[19:21], b[22:24] = NROOT.to_bytes(2, "little"), self.total.to_bytes(2, "little"), SPF.to_bytes(2, "little")
        b[510:512] = b"\x55\xAA"
        self.fat = {0: 0xFFF8, 1: 0xFFFF}
        self.root = RSV * BPS + NFATS * SPF * BPS
        self.data = self.root + NROOT * 32

    def put(self, clusters, data):
        for i, c in enumerate(clusters):
            self.fat[c] = clusters[i + 1] if i + 1 < len(clusters) else 0xFFFF
            chunk = data[i * BPS:(i + 1) * BPS]
            self.vol[self.data + (c - 2) * BPS:self.data + (c - 2) * BPS + len(chunk)] = chunk

    def image(self):
        for f in range(NFATS):
            for c, v in self.fat.items():
                o = (RSV + f * SPF) * BPS + 2 * c
                self.vol[o:o + 2] = v.to_bytes(2, "little")
        return bytes(self.vol)


GLOB = bytes(range(256)) * 5                        # 1280 bytes: three clusters, out of order
SESSION = b"session" * 100


def build_fat16():
    f = Fat16()
    root = (dirent("RHYTHM", "", 0x08, 0, 0) + dirent("README", "TXT", 0x20, 2, 5, case=0x18)
            + dirent("XOLD", "TXT", 0x20, 3, 3)
            + dirent("GLOBALS", "", 0x10, 10, 0) + dirent("EMPTY", "", 0x20, 0, 0))
    root = bytearray(root)
    root[64] = 0xE5                                          # the deleted entry
    f.vol[f.root:f.root + len(root)] = root
    f.put([2], b"hello")
    sub = (dirent(".", "", 0x10, 10, 0) + dirent("..", "", 0x10, 0, 0) + dirent("00_GLOB", "CG", 0x20, 20, len(GLOB))
           + lfn_entries("15_HD 2.ncs", b"15_HD2~1NCS") + dirent("15_HD2~1", "NCS", 0x20, 30, len(SESSION)))
    f.put([10], sub)
    f.put([20, 25, 21], GLOB)
    f.put([30, 31], SESSION)
    return f


def test_fat16_files():
    got = {"/".join(p): d for p, d in nb.fat16_files(build_fat16().image())}
    assert got == {"readme.txt": b"hello", "GLOBALS/00_GLOB.CG": GLOB, "GLOBALS/15_HD 2.ncs": SESSION,
                   "EMPTY": b""}, sorted(got)


def fat_error(img):
    try:
        list(nb.fat16_files(img))
    except nb.FatError:
        return True
    return False


def test_fat16_rejects_bad_volumes():
    f = build_fat16()
    f.fat[31] = 30                                           # a cluster loop
    assert fat_error(f.image())
    f = build_fat16()
    o = f.data + 8 * BPS + 96                                # the one LFN entry of 15_HD2~1, in cluster 10
    f.vol[o:o + 32] = lfn_entries("../evil", b"15_HD2~1NCS")         # valid checksum, unsafe name
    assert fat_error(f.image())
    assert fat_error(bytes(4096))                           # no boot sector
    small = bytearray(build_fat16().image())
    small[19:21] = (100).to_bytes(2, "little")              # too few clusters for FAT16
    assert fat_error(bytes(small))
    assert fat_error(build_fat16().image()[:100000])        # truncated image


def files_of(img, warnings=None):
    w = [] if warnings is None else warnings
    return {"/".join(p): d for p, d in nb.fat16_files(img, warn=w.append)}


def test_sfn_checksum():
    # Reference value from the Microsoft FAT specification's ChkSum() over "FOO     BAR".
    c = 0
    for b in b"FOO     BAR":
        c = ((0x80 if c & 1 else 0) + (c >> 1) + b) & 0xFF
    assert nb.sfn_checksum(b"FOO     BAR") == c


def test_fat16_chain_terminators():
    for bad in (0x0000, 0x0001, 0xFFF7, 0x5000, 4202):       # free, reserved, bad mark, outside, one past the end
        f = build_fat16()
        f.fat[2] = bad                                       # README's one-cluster file chain
        assert fat_error(f.image()), hex(bad)
        f = build_fat16()
        f.fat[10] = bad                                      # the GLOBALS directory chain
        assert fat_error(f.image()), hex(bad)
        f = build_fat16()
        f.fat[25] = bad                                      # the middle of a multi-cluster chain
        assert fat_error(f.image()), hex(bad)
    for eoc in range(0xFFF8, 0x10000):                       # every legal end-of-chain mark ends the chain
        f = build_fat16()
        f.fat[2] = eoc
        assert files_of(f.image())["readme.txt"] == b"hello"
    f = build_fat16()
    f.fat[10] = 10                                           # a directory chain that points at itself
    assert fat_error(f.image())
    f = build_fat16()
    root = bytearray(f.vol[f.root:f.root + 32 * 5])
    root[96 + 26:96 + 28] = (0).to_bytes(2, "little")        # GLOBALS directory with first cluster 0
    f.vol[f.root:f.root + len(root)] = root
    assert fat_error(f.image())


def bpb_patched(off, n, value):
    img = bytearray(build_fat16().image())
    img[off:off + n] = value.to_bytes(n, "little")
    return bytes(img)


def test_fat16_bpb_validation():
    assert files_of(build_fat16().image())                   # the fixture itself is valid
    for off, n, value, why in ((22, 2, 1, "FAT of 1 sector for 4200 clusters"),
                               (22, 2, 8, "FAT of 8 sectors for 4200 clusters"),
                               (22, 2, 0, "no FAT"),
                               (17, 2, 0, "no root directory entries"),
                               (17, 2, 513, "root directory not whole sectors"),
                               (11, 2, 300, "bytes per sector"),
                               (11, 2, 0, "bytes per sector zero"),
                               (13, 1, 3, "sectors per cluster not a power of two"),
                               (13, 1, 0, "zero sectors per cluster"),
                               (16, 1, 0, "no FATs"),
                               (14, 2, 0, "no reserved sectors"),
                               (19, 2, 100, "too few clusters for FAT16"),
                               (19, 2, 0, "no total sectors"),
                               (19, 2, 4300, "volume larger than the image")):
        assert fat_error(bpb_patched(off, n, value)), why
    assert fat_error(build_fat16().image()[:100000])        # truncated image


def lfn_root(entries):
    """A FAT16 volume whose root directory is entries, with README's 'hello' in cluster 2."""
    f = Fat16()
    f.vol[f.root:f.root + len(entries)] = entries
    f.put([2], b"hello")
    return f.image()


def test_fat16_long_names():
    w = []
    got = files_of(lfn_root(lfn_entries("LIE.TXT", b"LIE     TXT") + dirent("README", "TXT", 0x20, 2, 5)), w)
    assert got == {"README.TXT": b"hello"}, got               # wrong checksum: the verified short name, never LIE.TXT
    assert len(w) == 1 and "README.TXT" in w[0], w
    name, short = "a rather long file name.txt", b"AERATH~1TXT"
    assert files_of(lfn_root(lfn_entries(name, short) + dirent("AERATH~1", "TXT", 0x20, 2, 5))) == {name: b"hello"}
    exact = "thirteen.char"                                  # exactly one piece, no NUL terminator
    assert files_of(lfn_root(lfn_entries(exact, b"THIRTE~1CHA") + dirent("THIRTE~1", "CHA", 0x20, 2, 5))) \
        == {exact: b"hello"}
    for corrupt in ("seq", "missing", "first"):
        lfn = bytearray(lfn_entries(name, short))            # three pieces stored 0x43, 0x02, 0x01
        if corrupt == "seq":
            lfn[32] = 0x03                                   # second stored piece numbered 3 instead of 2
        elif corrupt == "missing":
            lfn = lfn[:32] + lfn[64:]                        # piece 2 lost
        else:
            lfn[0] &= ~0x40                                  # no piece marked as the last one
        w = []
        got = files_of(lfn_root(bytes(lfn) + dirent("AERATH~1", "TXT", 0x20, 2, 5)), w)
        assert got == {"AERATH~1.TXT": b"hello"}, (corrupt, got)
        assert w, corrupt
    lfn = bytearray(lfn_entries(name, short))
    lfn[64 + 13] ^= 1                                        # one piece with a different checksum
    assert files_of(lfn_root(bytes(lfn) + dirent("AERATH~1", "TXT", 0x20, 2, 5))) == {"AERATH~1.TXT": b"hello"}


def test_give_back_only_own_paths():
    e = Env()
    os.makedirs(e.out)
    unrelated = os.path.join(e.out, "unrelated.txt")
    open(unrelated, "w").write("not ours\n")
    os.makedirs(os.path.join(e.out, "olddir"))
    written = []
    e.run(written=written)
    chowned = []
    with mock.patch.dict(os.environ, {"SUDO_UID": "1000", "SUDO_GID": "1000"}), \
            mock.patch.object(nb.os, "geteuid", return_value=0), \
            mock.patch.object(nb.os, "chown", side_effect=lambda p, u, g, follow_symlinks=True:
                              chowned.append((p, u, g, follow_symlinks))):
        nb.give_back(written)
    paths = {p for p, _, _, _ in chowned}
    assert unrelated not in paths and e.out not in paths and os.path.join(e.out, "olddir") not in paths, paths
    assert paths == {os.path.join(e.out, n) for n in ("usb-disk.img", "nor-0x094000-0x2000000.bin",
                                                      "usb-blocks.sha256", "SHA256SUMS", "manifest.json")}, paths
    assert all(u == 1000 and g == 1000 and fs is False for _, u, g, fs in chowned)

    e = Env()
    e.out = os.path.join(e.root, "new", "deep")              # two directory levels created by the run
    written = []
    e.run(written=written)
    assert written[:2] == [os.path.join(e.root, "new"), e.out] and e.root not in written, written
    chowned = []
    with mock.patch.dict(os.environ, {"SUDO_UID": "1000", "SUDO_GID": "1000"}), \
            mock.patch.object(nb.os, "geteuid", return_value=1000), \
            mock.patch.object(nb.os, "chown", side_effect=lambda *a, **k: chowned.append(a)):
        nb.give_back(written)                                # not root: nothing is chowned
    assert chowned == []


def test_extract():
    with tempfile.TemporaryDirectory() as t:
        disk = bytes(nb.CONTENT_OFFSET) + build_fat16().image()
        open(os.path.join(t, "usb-disk.img"), "wb").write(disk)
        open(os.path.join(t, "SHA256SUMS"), "w").write(f"{nb.sha(disk)}  usb-disk.img\n")
        assert nb.extract(t, log=lambda *_: None) == 4
        assert open(os.path.join(t, "files", "GLOBALS", "15_HD 2.ncs"), "rb").read() == SESSION
        assert open(os.path.join(t, "files", "GLOBALS", "00_GLOB.CG"), "rb").read() == GLOB
        assert nb.main(["extract", t]) == 1                  # files/ exists already
        os.rename(os.path.join(t, "files"), os.path.join(t, "old"))
        open(os.path.join(t, "SHA256SUMS"), "w").write(f"{'0' * 64}  usb-disk.img\n")
        assert nb.main(["extract", t]) == 1                  # image does not match its hash
        assert not os.path.exists(os.path.join(t, "files"))


def load_tests(loader, tests, pattern):
    """Run the module's test_* functions under unittest too."""
    suite = unittest.TestSuite()
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            suite.addTest(unittest.FunctionTestCase(fn, description=name))
    return suite


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")


if __name__ == "__main__":
    main()
