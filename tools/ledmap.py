"""LED-map session: show the 8 bit-plane patterns on the unit while master films the pads.

    uv run python -I tools/ledmap.py /dev/snd/midiC3D0 [hold_seconds] [level]

Needs lightshow v4 (label 6002) installed. Sequence: all LEDs on (reference), then for k = 7..0 every LED byte whose
index has bit k set, each held hold_seconds (default 3), then off. Decoding the film: a pad channel that is lit in
the frames for bits {k} has byte index sum(1 << k).

With level (1..127) the patterns are written byte by byte through command 03 at value level << 1 instead of full
brightness, so a camera does not saturate on the pads (works on lightshow v4 and devkit).
"""
import os
import sys
import time

PRE = bytes.fromhex("F0 00 20 29 00 60")


def send(fd, body):
    msg = PRE + body + b"\xF7"
    while msg:
        try:
            msg = msg[os.write(fd, msg):]
        except BlockingIOError:
            time.sleep(0.01)


LEDS = 224


def pattern(k, level):
    if level is None:
        return bytes([0x02, k])
    return bytes([0x03, 0, 0]) + bytes(level if k == 0x7F or (i >> k) & 1 else 0 for i in range(LEDS))


def main(dev, hold="3", level=None):
    hold = float(hold)
    level = None if level is None else int(level, 0)
    if level is not None and not 1 <= level <= 127:
        sys.exit("level must be 1..127")
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        print("all LEDs on (reference)", flush=True)
        send(fd, pattern(0x7F, level))
        time.sleep(hold)
        for k in range(7, -1, -1):
            print(f"bit {k}", flush=True)
            send(fd, pattern(k, level))
            time.sleep(hold)
        print("off", flush=True)
        send(fd, b"\x00")
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
