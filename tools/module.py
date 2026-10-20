"""Upload an ITCM effect module to a unit running the devkit patch, verify it, and start it.

    uv run python -I tools/module.py /dev/snd/midiC3D0 build/stars.mod

Writes the module 32 bytes per SysEx (cmd 04), reads ITCM back through the dump command (7D) and compares, then
sends start (05) and prints the status reply. The read-back covers 0x400 up to at most 0x3FFF, inside what 7D serves
on Arrhythmia 0.1 (ITCM 0x00000100-0x00003FFF only, docs/user/developers.md). Starting is refused by the unit unless
magic, size and CRC check out.
"""
import os
import select
import sys
import time

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
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
