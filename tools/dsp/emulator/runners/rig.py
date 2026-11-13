# SPDX-License-Identifier: GPL-3.0-only
"""Host side of the emulator harness: scenes, stimulus text, trace parsing, audio extraction.

A scene is a list of blocks of 32 samples. Each block carries the memory writes the host makes
before the engine call, in the order the host makes them, and a random but repeatable register
set for the caller of the engine.
"""
import hashlib
from pathlib import Path
import random
import struct
import subprocess
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
import abi3  # noqa: E402

MASK = 0xFFFFFF
OUT_A, OUT_B = 0x306B0, 0x306D0            # the two output halves in Y; blocks alternate
SR_NATIVE = 0xE000D4
AUDIO_DUMP = (('Y', 0x306B0, 0x306EF),)    # both halves, after every block


class Scene:
    def __init__(self, name, sr=SR_NATIVE, seed=1):
        self.name, self.sr = name, sr
        self.rng = random.Random(seed)
        self.blocks, self.pokes = [], []
        self.req = self.prod = 0
        self.gen = [0] * abi3.LANES

    def poke(self, area, addr, value):
        self.pokes.append((area, addr, value & MASK))

    def epoch(self):
        self.req = self.req + 1 & MASK
        self.poke('X', abi3.REQ, self.req)

    def publish(self, *entries):
        """Write each entry in the order epoch, word0, word1, commit; then PROD once."""
        for w0, w1 in entries:
            seq = self.prod + 1 & MASK
            slot = abi3.RING + 4 * (self.prod & 15)
            self.poke('X', slot, self.req)
            self.poke('X', slot + 1, w0)
            self.poke('X', slot + 2, w1)
            self.poke('X', slot + 3, seq)
            self.prod = seq
        self.poke('X', abi3.PROD, self.prod)

    def block(self, n=1):
        for _ in range(n):
            index = len(self.blocks)
            self.blocks.append(dict(pokes=self.pokes, half=index & 1, caller=self.caller_registers()))
            self.pokes = []

    def caller_registers(self):
        r = self.rng
        values = [r.randrange(256), r.randrange(1 << 24), r.randrange(1 << 24),     # a2 a1 a0
                  r.randrange(256), r.randrange(1 << 24), r.randrange(1 << 24)]     # b2 b1 b0
        values += [r.randrange(1 << 24) for _ in range(4)]                          # x0 x1 y0 y1
        values += [r.randrange(1 << 24) for _ in range(16)]                         # r0..r7 n0..n7
        values += [MASK] * 8                                                        # m0..m7 linear
        values += [r.randrange(1 << 24), r.randrange(1, 1 << 16)]                   # la lc
        return values

    def note(self, lane, freq, velocity=100):
        self.gen[lane] = self.gen[lane] + 1 & 15
        self.publish(abi3.onset_freq(lane, self.gen[lane], freq, velocity))

    def release_all(self, lanes):
        self.publish(*[abi3.release(lane, self.gen[lane]) for lane in lanes])

    def opening(self, values, extra=()):
        """Epoch, HELLO 0..3, then sources 0..11 and any extra (source, value) singles."""
        self.epoch()
        self.block()
        self.publish(*abi3.hello_words())
        self.block()
        self.publish(*abi3.opening_sources(list(values)), *[abi3.param(s, v) for s, v in extra])
        self.block()


def stimulus(scene, body, watch=(), dumps=AUDIO_DUMP):
    rows = [f'LOAD 2000 {body}', 'MODE SHIM', f'SR {scene.sr:x}']
    rows += [f'WATCH {pc:x}' for pc in watch]
    rows.append('STARTUP')
    for index, block in enumerate(scene.blocks):
        active = OUT_A if block['half'] == 0 else OUT_B
        inactive = OUT_B if active == OUT_A else OUT_A
        rows.append(f'D0 {block["half"]:x}')
        for i in range(32):                       # poison both halves so unwritten output shows
            rows.append(f'Y {active + i:x} {0x5A0000 | (index & 0xFF) << 8 | i:x}')
            rows.append(f'Y {inactive + i:x} {0xC0DE00 | i:x}')
        rows += [f'Y {0x306AF:x} badbad', f'Y {0x306F0:x} badbad']
        rows += [f'{a} {addr:x} {value:x}' for a, addr, value in block['pokes']]
        rows.append('CALLER ' + ' '.join(f'{v:x}' for v in block['caller']))
        rows.append('BLOCK')
        rows += [f'DUMP {a} {lo:x} {hi:x}' for a, lo, hi in dumps]
    return '\n'.join(rows) + '\n'


def execute(harness, directory, name, text, dumps=AUDIO_DUMP):
    directory.mkdir(parents=True, exist_ok=True)
    stim = directory / f'{name}.stimulus'
    stim.write_text(text)
    trace = directory / f'{name}.trace'
    run = subprocess.run([str(harness), str(stim), str(trace)], capture_output=True, text=True, timeout=3600)
    if run.returncode:
        raise RuntimeError(f'harness failed for {name}: {run.stderr.strip()}')
    return parse(trace, dumps)


def parse(trace, dumps=AUDIO_DUMP):
    blocks, layout, watch = [], [], {}
    for row in trace.read_text().splitlines():
        f = row.split()
        if f[0] == 'BLOCK':
            fields = dict(x.split('=') for x in f[2:])
            blocks.append({k: int(fields[k]) for k in ('done', 'entered', 'returned', 'mixed', 'instructions',
                                                      'engine_cycles', 'to_mixer_cycles', 'min_sp', 'max_sp')})
        elif f[0] == 'DUMP':
            layout.append((f[1], int(f[2], 16), int(f[3], 16)))
        elif f[0] == 'WATCH':
            watch[int(f[1], 16)] = [int(x) for x in f[2:]]
    raw = Path(str(trace) + '.dump').read_bytes()
    words = [int.from_bytes(raw[i:i + 3], 'big') for i in range(0, len(raw), 3)]
    regions, at = [], 0
    for area, lo, hi in layout:
        regions.append((area, lo, words[at:at + hi - lo + 1]))
        at += hi - lo + 1
    per = len(dumps)
    snapshots = [regions[i:i + per] for i in range(0, len(blocks) * per, per)]
    return dict(blocks=blocks, snapshots=snapshots, watch=watch,
                trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest())


def s24(v):
    return v - (1 << 24) if v & 0x800000 else v


def output_of(run, index, half):
    base = OUT_A if half == 0 else OUT_B
    area, lo, words = run['snapshots'][index][0]
    return [s24(w) for w in words[base - lo:base - lo + 32]]


def audio(run, scene, first=0):
    """Signed output samples of the blocks first.., in order."""
    out = []
    for i in range(first, len(scene.blocks)):
        out += output_of(run, i, scene.blocks[i]['half'])
    return out


def write_wav(path, samples, rate=48000):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<i', s << 8)[1:] for s in samples))
