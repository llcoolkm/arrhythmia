"""Print input events live from devkit-input (label 6006) command 09, for mapping the controls.

    uv run python -I tools/inputlog.py /dev/snd/midiC3D0 [--out events.jsonl] [--interval 0.2]

Polls both log pages, prints every new event once (kind, index, value) with a host timestamp, and warns when events
were lost between polls (more than 32 since the last one). Ctrl-C to stop.
"""
import argparse
import json
import os
import select
import sys
import time

PRE = bytes.fromhex("F0 00 20 29 00 60")
KINDS = {1: "pad", 2: "button", 3: "pot", 4: "pressure", 5: "encoder"}


def parse(frame, page):
    if len(frame) != 142 or frame[:8] != PRE + bytes([0x09, page]) or frame[-1] != 0xF7:
        raise ValueError("bad cmd 09 reply")
    seq = 0
    for b in frame[8:13]:
        seq = seq << 7 | b
    return seq, [int("".join(f"{x:x}" for x in frame[13 + 8 * k:21 + 8 * k]), 16) for k in range(16)]


def read_page(fd, page, timeout=1.0):
    msg = PRE + bytes([0x09, page]) + b"\xF7"
    deadline = time.monotonic() + timeout
    while msg:
        if select.select([], [fd], [], 0.1)[1]:
            try:
                msg = msg[os.write(fd, msg):]
            except BlockingIOError:
                pass
        if time.monotonic() > deadline:
            raise TimeoutError("write timed out")
    buf = b""
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)
            except BlockingIOError:
                pass
        i = buf.find(PRE + bytes([0x09, page]))
        j = buf.find(b"\xF7", i) if i >= 0 else -1
        if i >= 0 and j >= 0:
            return parse(buf[i:j + 1], page)
    raise TimeoutError("no reply (is devkit-input installed?)")


def decode(e):
    kind, index, value = e & 0xFF, (e >> 8) & 0xFF, e >> 16
    d = {"kind": KINDS.get(kind, kind), "index": index}
    if kind == 1:
        d.update(velocity=value & 0xFF, on=bool(value >> 8))
    elif kind == 5:
        d["delta"] = value - 0x10000 if value & 0x8000 else value
    else:
        d["value"] = value
    return d


def snapshot(fd, tries=10):
    """Both pages with the same seq, or None if the log kept changing (pressure floods); the caller polls again."""
    for _ in range(tries):
        s0, e0 = read_page(fd, 0)
        s1, e1 = read_page(fd, 1)
        if s0 == s1:
            return s0, e0 + e1
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dev")
    ap.add_argument("--out")
    ap.add_argument("--interval", type=float, default=0.2)
    a = ap.parse_args()
    out = open(a.out, "a") if a.out else None
    fd = os.open(a.dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        snap = None
        while snap is None:
            snap = snapshot(fd)
        last = snap[0]
        print(f"listening; {last} events logged before start", flush=True)
        while True:
            time.sleep(a.interval)
            snap = snapshot(fd)
            if snap is None:
                continue
            seq, ring = snap
            if seq - last > 32:
                print(f"!! {seq - last - 32} events lost", flush=True)
                last = seq - 32
            for i in range(last, seq):
                d = decode(ring[i % 32])
                d.update(seq=i, t=round(time.time(), 3))
                print(json.dumps(d), flush=True)
                if out:
                    out.write(json.dumps(d) + "\n")
                    out.flush()
            last = seq
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
        if out:
            out.close()


if __name__ == "__main__":
    main()
