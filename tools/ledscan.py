"""One-byte-at-a-time LED scan used to decode docs/led-map.json (film it; marker = all on 2 s, off 1 s, then byte i
lit alone for 0.5 s at level 16). Needs devkit (6003) or lightshow v4 (6002).

    uv run python -I tools/ledscan.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledmap import send
LEVEL, HOLD = 16, 0.5
fd = os.open("/dev/snd/midiC3D0", os.O_RDWR | os.O_NONBLOCK)
send(fd, bytes([0x03, 0, 0]) + bytes([LEVEL] * 224)); time.sleep(2)     # marker: all on 2 s
send(fd, bytes([0x03, 0, 0]) + bytes(224)); time.sleep(1)               # all off 1 s
for i in range(224):
    send(fd, bytes([0x03, 0, 0]) + bytes(LEVEL if j == i else 0 for j in range(224)))
    time.sleep(HOLD)
send(fd, b"\x00"); os.close(fd)
