"""Read-back test for tools/module.py (ITCM through SysEx 7D) against a fake unit on a socketpair.

    python3 -I tests/test_module_readback.py
"""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import module  # noqa: E402

ITCM = bytes((i * 13 + 5) & 0xFF for i in range(module.ITCM_END))


def fake_unit(sock, mode):
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 13:
            req, buf = buf[:13], buf[13:]
            assert req[:7] == module.DUMP_PRE and req[12] == 0xF7, req.hex(" ")
            addr = sum(x << (7 * (4 - k)) for k, x in enumerate(req[7:12]))
            body = b"".join(bytes((b >> 4, b & 0x0F)) for b in ITCM[addr:addr + 32])
            if mode == "wrong-addr":
                addr += 32
            if mode == "silent":
                continue
            reply = module.DUMP_PRE + module.a7(addr) + body + b"\xF7"
            if mode == "short":
                reply = reply[:-2] + b"\xF7"
            for k in range(0, len(reply), 10):
                sock.sendall(b"\xF8" + reply[k:k + 10])


def run(mode, start=module.MOD_BASE, size=0x400):
    a, b = socket.socketpair()
    a.setblocking(False)
    threading.Thread(target=fake_unit, args=(b, mode), daemon=True).start()
    try:
        return module.read_itcm(a.fileno(), start, size, timeout=0.5)
    finally:
        a.close()


def main():
    assert module.DUMP_REPLY == 77
    assert run("good") == ITCM[module.MOD_BASE:module.MOD_BASE + 0x400]
    for mode, exc in (("wrong-addr", ValueError), ("short", ValueError), ("silent", TimeoutError)):
        try:
            run(mode, size=0x40)
        except exc:
            pass
        else:
            raise AssertionError(f"{mode}: no {exc.__name__}")
    for start, size in ((0x08000000, 0x20), (0x3FE0, 0x40)):           # outside ITCM: refused before sending
        try:
            module.read_itcm(-1, start, size)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"read outside ITCM accepted: {start:#x}+{size:#x}")
    print("module read-back mock: good read, wrong address, short reply, silence and range checks behave")


if __name__ == "__main__":
    main()
