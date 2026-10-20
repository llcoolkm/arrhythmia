"""Drive tools/flash.py against a simulated unit that follows the application's update handler.

    python3 -I tests/test_flash_mock.py build/hello-version.syx
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import flash  # noqa: E402
import syx  # noqa: E402


def ack(off):
    return bytes([0xF0, 0, 0x20, 0x29, 0, 0x72, (off >> 5) & 0x7F, (off >> 12) & 0x7F, (off >> 19) & 0x7F, 0xF7])


class FakeUnit:
    """Stages the image like the unit, checks the CRC on FINISH, answers WRITE and FINISH like the unit."""
    def __init__(self, path, corrupt_crc=False, drop_ack_at=None):
        self.out, self.stage, self.size, self.crc, self.offset = [], bytearray(), 0, 0, 0
        self.corrupt_crc, self.drop_ack_at, self.writes = corrupt_crc, drop_ack_at, 0
        self.out.append(bytes.fromhex("F0 00 20 29 01 63 04 02 00 00 F7"))  # unrelated chatter

    def send(self, m):
        cmd = m[5]
        if cmd == syx.HEADER:
            hb = m[6:-1]
            self.size, self.crc = syx.nibbles_to_int(hb[7:15]), syx.nibbles_to_int(hb[15:23])
            self.stage = bytearray(b"\xff" * (self.size + 32))
            self.offset = 0x20
            self.out.append(ack(0x20))
        elif cmd == syx.WRITE:
            self.writes += 1
            blk = syx.unpack7(m[6:-1])
            self.stage[self.offset:self.offset + 32] = blk
            self.offset += 0x20
            if self.drop_ack_at != self.writes:
                self.out.append(ack(0 if self.offset >= self.size else self.offset))
        elif cmd == syx.FINISH:
            self.stage[0:32] = syx.unpack7(m[6:-1])
            ok = syx.crc32_mpeg2(bytes(self.stage[:self.size])) == self.crc and not self.corrupt_crc
            self.out.append(bytes([0xF0, 0, 0x20, 0x29, 0, 0x73, 0x7F if ok else 0x7E, 0x7F, 0x7F, 0xF7]))

    def recv(self, timeout):
        return self.out.pop(0) if self.out else None


def run(path, **kw):
    unit = FakeUnit(path, **kw)
    flash.Port = lambda dev: unit
    try:
        flash.flash("fake", path, None, 1.0)
        return "ok", unit
    except SystemExit as e:
        return str(e), unit


def main(path):
    data = open(path, "rb").read()
    status, unit = run(path)
    assert status == "ok", status
    assert bytes(unit.stage[:unit.size]) == syx.parse(data)["image"]
    status, _ = run(path, corrupt_crc=True)
    assert "MISMATCH" in status, status
    status, _ = run(path, drop_ack_at=100)
    assert "WRITE 101" in status or "WRITE 100" in status, status
    # Malformed streams must be refused before the port is opened.
    msgs = syx.split(data)
    bad_cmd = b"".join(msgs[:5]) + bytes([0xF0, 0, 0x20, 0x29, 0, 0x77, 0, 0xF7]) + b"".join(msgs[5:])
    extra_hdr = b"".join(msgs[:2]) + msgs[1][:6] + bytes([2]) + msgs[1][7:] + b"".join(msgs[2:])
    for name, stream in (("inserted 0x77", bad_cmd), ("extra HEADER type 2", extra_hdr)):
        tmp = path + ".bad.syx"
        open(tmp, "wb").write(stream)
        def no_port(dev):
            raise AssertionError(f"{name}: port opened")
        flash.Port = no_port
        try:
            flash.flash("fake", tmp, None, 1.0)
            raise AssertionError(f"{name}: accepted")
        except SystemExit as e:
            assert "refusing" in str(e), (name, e)
        finally:
            os.remove(tmp)
    bad_ack = bytes([0xF0, 0, 0x20, 0x29, 0, 0x72, 0x80, 0, 0, 0xF7])
    assert flash.ack_offset(bad_ack) is None
    print("flash mock: success, CRC-failure and missing-ack paths behave")


if __name__ == "__main__":
    main(sys.argv[1])
