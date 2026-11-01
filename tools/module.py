"""Upload an ITCM effect module to a unit running the devkit patch, verify it, and start it; or manage the module
files on the unit's microSD card (images with SysEx cmd 10, CARD_WRITE).

    uv run python -I tools/module.py /dev/snd/midiC3D0 build/stars.mod
    uv run python -I tools/module.py card write  /dev/snd/midiC3D0 build/stars.mod [NAME]
    uv run python -I tools/module.py card delete /dev/snd/midiC3D0 NAME
    uv run python -I tools/module.py card verify /dev/snd/midiC3D0 NAME
    uv run python -I tools/module.py card list   /dev/snd/midiC3D0

Writes the module 32 bytes per SysEx (cmd 04), reads ITCM back through the dump command (7D) and compares, then
sends start (05) and prints the status reply. card write stages the file in ITCM with cmd 10 (one acknowledged
32-byte piece per message), reads the staged bytes back through 7D, commits (the unit writes 1:/MODULES/NAME.MOD
through NAME.TMP and checks it; the job runs between the unit's Main-loop passes and waits while the
sequencer plays), then reads the card file back (cmd 10 07: size and CRC-32) and compares.
NAME is 1..8 characters A-Z 0-9 _ - (default: the file name, upper-cased); the unit adds .MOD. A failed step stops
with its result code; nothing is retried. Starting a card command stops a running module on the unit. The read-back covers 0x400 up to at most 0x3FFF, inside what 7D serves
on Arrhythmia 0.1 (ITCM 0x00000100-0x00003FFF only, docs/user/developers.md). Starting is refused by the unit unless
magic, size and CRC check out.
"""
import os
import select
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_module  # noqa: E402

PRE = bytes.fromhex("F0 00 20 29 00 60")
MOD_BASE = 0x400
PER_MSG = 32
ITCM_END = 0x4000                                    # read-back covers the module area in ITCM only
CHUNK = 32
DUMP_PRE = PRE + b"\x7D"
DUMP_REPLY = len(DUMP_PRE) + 5 + 2 * CHUNK + 1       # 77


def a7(addr):
    return bytes((addr >> (7 * (4 - i))) & 0x7F for i in range(5))


def write_all(fd, data, deadline):
    while data:
        if time.monotonic() > deadline:
            raise TimeoutError("write timed out")
        if not select.select([], [fd], [], 0.1)[1]:
            continue
        try:
            data = data[os.write(fd, data):]
        except BlockingIOError:
            pass


def read_itcm(fd, start, size, timeout=2.0):
    """Read ITCM back through the dump command (7D), 32 bytes per request; only 0x0000-0x3FFF."""
    assert start % CHUNK == 0 and size % CHUNK == 0 and size > 0 and start + size <= ITCM_END
    end = time.monotonic() + 0.3
    while time.monotonic() < end:                      # drop anything already queued
        if select.select([fd], [], [], 0.05)[0]:
            os.read(fd, 4096)
    data, buf = bytearray(), b""
    for addr in range(start, start + size, CHUNK):
        deadline = time.monotonic() + timeout
        write_all(fd, DUMP_PRE + a7(addr) + b"\xF7", deadline)
        while True:
            i = buf.find(DUMP_PRE)
            j = buf.find(b"\xF7", i) if i >= 0 else -1
            if i >= 0 and j >= 0:
                msg, buf = buf[i:j + 1], buf[j + 1:]
                break
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no reply for {addr:#06x}")
            if select.select([fd], [], [], left)[0]:
                try:
                    buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)   # drop realtime bytes
                except BlockingIOError:
                    pass
        got = sum(x << (7 * (4 - k)) for k, x in enumerate(msg[7:12]))
        if len(msg) != DUMP_REPLY or got != addr or any(b > 0x0F for b in msg[12:-1]):
            raise ValueError(f"bad reply for {addr:#06x}: {msg.hex(' ')}")
        data += bytes((msg[12 + 2 * k] << 4) | msg[13 + 2 * k] for k in range(CHUNK))
    return bytes(data)


def upload_msgs(blob):
    for off in range(0, len(blob), PER_MSG):
        part = blob[off:off + PER_MSG]
        yield PRE + bytes([0x04, (off >> 14) & 0x7F, (off >> 7) & 0x7F, off & 0x7F]) + \
            b"".join(bytes((b >> 4, b & 0x0F)) for b in part) + b"\xF7"


def read_reply(fd, cmd, timeout=2.0):
    buf, deadline = b"", time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)
            except BlockingIOError:
                pass
        i = buf.find(PRE + bytes([cmd]))
        j = buf.find(b"\xF7", i) if i >= 0 else -1
        if i >= 0 and j >= 0:
            return buf[i:j + 1]
    return None


# --- card files (SysEx cmd 10, patches/src/cardwrite.c) ------------------------------------------------------------

CARD = 0x10
STATE = ["none", "staging", "job", "done"]
JOB = ["none", "write", "delete", "list", "verify"]
RESULT = ["none", "ok", "no card", "busy", "bad name", "bad size", "bad offset", "bad CRC", "bad module",
          "file system error", "read-back mismatch", "not found", "no Content task", "not now", "ITCM unavailable",
          "recovery of NAME.TMP / NAME.OLD failed"]
W_OK = 1
CW_DONE = 3


class CardError(Exception):
    pass


def card_name(name):
    """The 8 request bytes for NAME (1..8 of A-Z 0-9 _ -); ValueError otherwise."""
    if not 1 <= len(name) <= 8 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
        raise ValueError(f"name {name!r}: 1..8 characters A-Z 0-9 _ - (the unit adds .MOD)")
    return name.encode().ljust(8, b"\0")


def default_name(path):
    return os.path.splitext(os.path.basename(path))[0].upper()


def p7(v, n):
    return bytes((v >> (7 * (n - 1 - i))) & 0x7F for i in range(n))


def g7(b):
    return sum(x << (7 * (len(b) - 1 - i)) for i, x in enumerate(b))


def parse_status(r):
    """The 26-byte cmd 10 status reply as a dict."""
    if not r or len(r) != 26 or r[:7] != PRE + bytes([CARD]):
        raise CardError(f"bad status reply: {r.hex(' ') if r else None}")
    return dict(sub=r[7], answer=r[8], state=r[9], job=r[10], result=r[11], fr=r[12], got=g7(r[13:16]),
                size=g7(r[16:19]), info=g7(r[19:24]), count=r[24])


def describe(st):
    return (f"answer {RESULT[st['answer']] if st['answer'] < len(RESULT) else st['answer']}, state "
            f"{STATE[st['state']] if st['state'] < 4 else st['state']}, job {JOB[st['job']] if st['job'] < 5 else st['job']}"
            f", result {RESULT[st['result']] if st['result'] < len(RESULT) else st['result']}"
            + (f" (FatFs {st['fr']})" if st["fr"] else ""))


class Card:
    """cmd 10 over a transport: xfer(msg, cmd) sends one SysEx and returns the reply starting with PRE + cmd (or
    None). The fd transport below talks to a unit; the emulator tests pass their own."""
    def __init__(self, xfer, poll=lambda: None, polls=400):
        self.xfer, self.poll, self.polls = xfer, poll, polls

    def req(self, body):
        st = parse_status(self.xfer(PRE + bytes([CARD]) + body + b"\xF7", CARD))
        if st["sub"] != body[0]:
            raise CardError(f"reply for sub {st['sub']:#04x}, expected {body[0]:#04x}")
        return st

    def must(self, body, what):
        st = self.req(body)
        if st["answer"] != W_OK:
            raise CardError(f"{what} refused: {describe(st)}")
        return st

    def wait(self, what):
        """Poll the status until the job is done; its result must be ok."""
        for _ in range(self.polls):
            st = self.req(b"\x00")
            if st["state"] == CW_DONE:
                if st["result"] != W_OK:
                    raise CardError(f"{what} failed: {describe(st)}")
                return st
            if st["state"] != 2:
                raise CardError(f"{what}: no job any more: {describe(st)}")
            self.poll()
        raise CardError(f"{what}: no result after {self.polls} polls (the unit's job waits while the sequencer "
                        "plays: stop playback and the job finishes on its own)")

    def stage(self, blob, name):
        crc = zlib.crc32(blob)
        self.must(b"\x01" + card_name(name) + p7(len(blob), 3) + p7(crc, 5), "begin")
        for off in range(0, len(blob), PER_MSG):
            part = blob[off:off + PER_MSG]
            st = self.must(b"\x02" + p7(off, 3) + b"".join(bytes((b >> 4, b & 0x0F)) for b in part),
                           f"piece at {off}")
            if st["got"] != off + len(part):
                raise CardError(f"piece at {off}: unit has {st['got']} bytes")

    def readback(self, n):
        """The staged bytes, through 7D (32 bytes per request)."""
        data = b""
        for addr in range(MOD_BASE, MOD_BASE + (n + CHUNK - 1) // CHUNK * CHUNK, CHUNK):
            r = self.xfer(DUMP_PRE + a7(addr) + b"\xF7", 0x7D)
            if not r or len(r) != DUMP_REPLY or g7(r[7:12]) != addr:
                raise CardError(f"no read-back for {addr:#06x}: {r.hex(' ') if r else None}")
            data += bytes((r[12 + 2 * k] << 4) | r[13 + 2 * k] for k in range(CHUNK))
        return data[:n]

    def verify(self, name):
        self.must(b"\x07" + card_name(name), "verify")
        st = self.wait("verify")
        return st["size"], st["info"]

    def write(self, blob, name):
        build_module.check(blob)
        self.stage(blob, name)
        back = self.readback(len(blob))
        if back != blob:
            bad = next(i for i in range(len(blob)) if back[i] != blob[i])
            self.req(b"\x7F")
            raise CardError(f"staged copy differs at offset {bad}; nothing was written to the card")
        self.must(b"\x03", "commit")
        self.wait("write")
        size, crc = self.verify(name)
        if (size, crc) != (len(blob), zlib.crc32(blob)):
            raise CardError(f"card file {name}.MOD: {size} bytes CRC {crc:08x}, expected {len(blob)} bytes CRC "
                            f"{zlib.crc32(blob):08x}")
        self.req(b"\x7F")
        return size, crc

    def delete(self, name):
        self.must(b"\x04" + card_name(name), "delete")
        self.wait("delete")
        self.req(b"\x7F")

    def list(self):
        self.must(b"\x05", "list")
        st = self.wait("list")
        out = []
        for i in range(st["count"]):
            r = self.xfer(PRE + bytes([CARD, 0x06, i]) + b"\xF7", CARD)
            if not r or len(r) != 25 or r[7] != 0x06 or r[8] != i:
                raise CardError(f"no list entry {i}: {r.hex(' ') if r else None}")
            out.append((r[9:21].rstrip(b"\0").decode("ascii", "replace"), g7(r[21:24])))
        self.req(b"\x7F")
        return out


def fd_xfer(fd):
    def xfer(msg, cmd):
        write_all(fd, msg, time.monotonic() + 2.0)
        return read_reply(fd, cmd)
    return xfer


def card_main(args):
    if len(args) < 2 or args[0] not in ("write", "delete", "verify", "list"):
        sys.exit(__doc__)
    op, dev, rest = args[0], args[1], args[2:]
    if op == "write":
        if not 1 <= len(rest) <= 2:
            sys.exit(__doc__)
        blob = open(rest[0], "rb").read()
        name = rest[1] if len(rest) == 2 else default_name(rest[0])
        try:
            build_module.check(blob)
            card_name(name)
        except ValueError as e:
            sys.exit(f"{rest[0]}: {e}")
    elif op in ("delete", "verify"):
        if len(rest) != 1:
            sys.exit(__doc__)
        name = rest[0]
        try:
            card_name(name)
        except ValueError as e:
            sys.exit(str(e))
    elif rest:
        sys.exit(__doc__)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        card = Card(fd_xfer(fd), poll=lambda: time.sleep(0.05), polls=600)
        if op == "write":
            size, crc = card.write(blob, name)
            print(f"wrote 1:/MODULES/{name}.MOD: {size} bytes, CRC {crc:08x}, read back from the card and matched")
        elif op == "delete":
            card.delete(name)
            print(f"deleted 1:/MODULES/{name}.MOD")
        elif op == "verify":
            size, crc = card.verify(name)
            card.req(b"\x7F")
            print(f"1:/MODULES/{name}.MOD: {size} bytes, CRC {crc:08x}")
        else:
            for n, size in card.list():
                print(f"{n:12s} {size:6d}")
    except CardError as e:
        sys.exit(f"card {op}: {e}")
    finally:
        os.close(fd)


def main(dev, path):
    blob = open(path, "rb").read()
    try:
        build_module.check(blob)                   # refuse before touching the unit
    except ValueError as e:
        sys.exit(f"{path}: {e}")
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        for msg in upload_msgs(blob):
            write_all(fd, msg, time.monotonic() + 2.0)
            time.sleep(0.005)
        size = (len(blob) + 31) // 32 * 32
        back = read_itcm(fd, MOD_BASE, size)[:len(blob)]
        if back != blob:
            bad = next(i for i in range(len(blob)) if back[i] != blob[i])
            sys.exit(f"read-back mismatch at offset {bad}: wrote {blob[bad]:#04x}, read {back[bad]:#04x}")
        print(f"uploaded and verified {len(blob)} bytes at ITCM {MOD_BASE:#x}")
        write_all(fd, PRE + b"\x05\xF7", time.monotonic() + 2.0)
        st = read_reply(fd, 0x7F)
        if not st or len(st) != 20:
            sys.exit(f"no status reply after start: {st.hex(' ') if st else None}")
        print(f"status: mode {st[7]}, module {['none', 'valid', 'BAD', 'ITCM unavailable'][st[18]] if st[18] < 4 else st[18]}")
        if st[7] != 3:
            sys.exit("module did not start")
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "card":
        card_main(sys.argv[2:])
        sys.exit(0)
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
