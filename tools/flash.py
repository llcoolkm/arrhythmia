"""Send a firmware .syx to a Circuit over raw MIDI, one message at a time, waiting for the unit's acknowledgements.

    python3 -I tools/flash.py /dev/snd/midiC3D0 build/hello-version.syx [--expect-sha256 HEX]

Protocol, as observed from the unit's update handler:
* INIT (0x71) and HEADER (0x7C) start the transfer; the unit may want a button press to confirm on the device.
* After each WRITE (0x72) the unit replies `F0 00 20 29 00 72 b6 b7 b8 F7`.
* After HEADER, once the staging area is erased, it replies with ack offset 0x20.
* Ack offset = (b6 | b7<<7 | b8<<14) << 5 = the next offset it expects; 0 once the declared size is reached.
* After FINISH (0x73) it replies `F0 00 20 29 00 73 <status> 7F 7F F7`: 0x7F = CRC OK (it then resets and the
  bootloader installs), 0x7E = CRC mismatch. No reply means an unknown outcome.
Nothing is installed until FINISH passes the CRC check, so aborting earlier leaves the running firmware untouched.
"""
import argparse
import hashlib
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import syx  # noqa: E402


class Port:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.buf = b""

    def send(self, msg):
        view = memoryview(msg)
        while view:
            select.select([], [self.fd], [], 1.0)
            n = os.write(self.fd, view)
            view = view[n:]

    def recv(self, timeout):
        """Return the next complete SysEx message, or None on timeout."""
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\xF0")
            j = self.buf.find(b"\xF7", i) if i >= 0 else -1
            if i >= 0 and j >= 0:
                msg, self.buf = self.buf[i:j + 1], self.buf[j + 1:]
                return msg
            left = end - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if r:
                try:
                    self.buf += os.read(self.fd, 4096)
                except BlockingIOError:
                    pass


def ack_offset(msg):
    """Decode an update progress reply F0 00 20 29 00 72 b6 b7 b8 F7; None if malformed."""
    if (len(msg) != 10 or not msg.startswith(syx.PREFIX) or msg[5] != syx.WRITE or msg[9] != 0xF7
            or any(b > 0x7F for b in msg[6:9])):
        return None
    return (msg[6] | msg[7] << 7 | msg[8] << 14) << 5


def next_update_reply(port, timeout):
    """Next F0 00 20 29 00 message within one overall deadline, skipping unrelated traffic (e.g. 01 63 status)."""
    deadline = time.monotonic() + timeout
    while True:
        reply = port.recv(max(0.0, deadline - time.monotonic()))
        if reply is None or reply.startswith(syx.PREFIX):
            return reply
        print(f"  (other message: {reply.hex(' ')})")


def expect_ack(port, timeout, want, what):
    reply = next_update_reply(port, timeout)
    got = ack_offset(reply) if reply else None
    if got != want:
        sys.exit(f"{what}: expected ack offset 0x{want:x}, got {reply.hex(' ') if reply else 'timeout'}"
                 " - aborted before FINISH; the installed firmware is untouched. Power-cycle the unit.")


def flash(dev, path, expect_sha, confirm_timeout):
    data = open(path, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    if expect_sha and sha != expect_sha:
        sys.exit(f"sha256 {sha} does not match --expect-sha256")
    try:
        p = syx.parse(data)  # strict: exactly INIT, HEADER, WRITE*, FINISH in order, nothing else
    except ValueError as e:
        sys.exit(f"refusing malformed stream: {e}")
    if not p["crc_ok"] or p["header_prefix"][0] != 0:
        sys.exit("refusing: CRC not OK or header type is not 0 (application)")
    msgs = syx.split(data)
    size = p["size"]
    writes = sum(1 for m in msgs if m[5] == syx.WRITE)
    print(f"{path}: sha256 {sha}, {len(msgs)} messages, {writes} WRITE blocks, image {size} bytes")

    port = Port(dev)
    while port.recv(0.2):  # drain anything pending
        pass
    n = 0
    t0 = time.monotonic()
    for m in msgs:
        cmd = m[5]
        port.send(m)
        if cmd == syx.INIT:
            reply = port.recv(2.0)
            print(f"INIT sent, reply: {reply.hex(' ') if reply else 'none'}")
        elif cmd == syx.HEADER:
            print(f"HEADER sent; waiting up to {confirm_timeout:.0f} s for the unit (confirm on the device if asked)")
            expect_ack(port, confirm_timeout, 0x20, "HEADER")  # staging erased, ready at 0x20
            print("unit ready, sending blocks")
        elif cmd == syx.WRITE:
            n += 1
            want = 0x20 * (n + 1)
            expect_ack(port, 5.0, 0 if want >= size else want, f"WRITE {n}")
            if n % 500 == 0:
                print(f"  {n}/{writes} blocks, {time.monotonic() - t0:.0f} s")
        elif cmd == syx.FINISH:
            reply = next_update_reply(port, 30.0)
            print(f"FINISH sent after {time.monotonic() - t0:.0f} s, reply: {reply.hex(' ') if reply else 'none'}")
            status = reply[6] if reply and len(reply) == 10 and reply[5] == syx.FINISH and reply[7:9] == b"\x7f\x7f" else None
            if status == 0x7F:
                print("unit reports CRC OK; it now resets and the bootloader installs the image")
            elif status == 0x7E:
                sys.exit("unit reports CRC MISMATCH (0x7e) - nothing installed")
            else:
                sys.exit("UNKNOWN outcome: no valid status frame after FINISH. Wait, then query the version (SysEx 0x70).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("device")
    ap.add_argument("syx")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--confirm-timeout", type=float, default=60.0)
    a = ap.parse_args()
    flash(a.device, a.syx, a.expect_sha256, a.confirm_timeout)
