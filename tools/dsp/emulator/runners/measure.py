# SPDX-License-Identifier: GPL-3.0-only
"""Render one fixture through an engine file in the emulator harness and measure it.

    python3 -I measure.py --harness BIN --engine FILE.dse --out NEWDIR [options]

The fixture is the one used for the level references: a first block, the opening (epoch, HELLO,
sources), the notes in one block, 1,502 held blocks, RELEASE of every lane, 2,253 release blocks.
Audio is the output of the blocks from the onset block on. The level is the RMS over 0.05 s to
0.6 s after the start of that audio, in dB relative to full scale (2^23). Cycles are interpreter
cycles between the engine entry and its return to the shim: a model, not silicon timing.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rig  # noqa: E402
from abi3 import FS  # noqa: E402

FM_DEFAULT_SOURCES = '0,0,64,64,0,0,127,0,0,0,64,127'
FULL_SCALE = 2 ** 23


def engine_body(path):
    """The P words of a CRE3 file, as 24-bit big-endian bytes from P:0x2000."""
    data = Path(path).read_bytes()
    if data[:4] != b'CRE3':
        raise ValueError('not a CRE3 engine file')
    header = int.from_bytes(data[4:6], 'little')
    words = int.from_bytes(data[8:12], 'little')
    body = data[header:]
    if len(body) != 3 * words:
        raise ValueError('body length differs from the word count in the header')
    if zlib.crc32(body) != int.from_bytes(data[12:16], 'little'):
        raise ValueError('body CRC differs from the header')
    return body


def level(samples, start_s, end_s):
    window = [s / FULL_SCALE for s in samples[int(start_s * FS):int(end_s * FS)]]
    rms = math.sqrt(sum(x * x for x in window) / len(window))
    return 20 * math.log10(rms + 1e-30)


def scene_for(a):
    sources = [int(x) for x in a.sources.split(',')]
    if len(sources) != 12:
        raise ValueError('--sources takes exactly 12 values (sources 0..11)')
    extra = [tuple(int(y) for y in p.split('=')) for p in a.param]
    notes = [int(n) for n in a.notes.split(',')]
    scene = rig.Scene('fixture')
    scene.block()
    scene.opening(sources, extra)
    for lane, note in enumerate(notes):
        scene.note(lane, 440 * 2 ** ((note - 69) / 12), a.velocity)
    onset_block = len(scene.blocks)
    scene.block(a.held)
    scene.release_all(range(len(notes)))
    scene.block(a.release)
    return scene, onset_block


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--harness', required=True, help='the built dsp-harness binary')
    ap.add_argument('--engine', required=True, help='a CRE3 engine file')
    ap.add_argument('--out', required=True, help='new directory for stimulus, trace and result')
    ap.add_argument('--sources', default=FM_DEFAULT_SOURCES, help='raw sources 0..11, comma separated')
    ap.add_argument('--param', action='append', default=[], metavar='SOURCE=VALUE',
                    help='extra PARAM singles in the opening, for sources 16 and up; repeatable')
    ap.add_argument('--notes', default='60', help='MIDI notes, one per lane, comma separated')
    ap.add_argument('--velocity', type=int, default=100)
    ap.add_argument('--held', type=int, default=1502, help='blocks between onset and release')
    ap.add_argument('--release', type=int, default=2253, help='blocks after the release')
    ap.add_argument('--window', type=float, nargs=2, default=(0.05, 0.6), metavar=('START', 'END'))
    ap.add_argument('--watch', action='append', default=[], metavar='HEXPC',
                    help='P address: record x0, x1, y0, y1 extremes whenever the PC passes it; repeatable')
    ap.add_argument('--wav', action='store_true', help='also write the audio as a 24-bit mono wav')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    body = out / 'body.u24be'
    body.write_bytes(engine_body(a.engine))
    scene, first = scene_for(a)
    watch = [int(w, 16) for w in a.watch]
    run = rig.execute(Path(a.harness), out, 'fixture', rig.stimulus(scene, body, watch))
    samples = rig.audio(run, scene, first)
    cycles = sorted(b['engine_cycles'] for b in run['blocks'][first:])
    result = dict(
        engine=Path(a.engine).name, notes=a.notes, velocity=a.velocity, blocks=len(scene.blocks),
        all_blocks_completed=all(b['done'] and b['returned'] and b['mixed'] for b in run['blocks']),
        rms_dbfs=level(samples, *a.window), window_s=list(a.window),
        peak=max(abs(s) for s in samples) / FULL_SCALE, rails=sum(abs(s) >= FULL_SCALE - 1 for s in samples),
        cycles_max=cycles[-1], cycles_median=cycles[len(cycles) // 2], cycles_init=run['blocks'][0]['engine_cycles'],
        watch={f'{pc:x}': dict(hits=w[0], x0=w[1:3], x1=w[3:5], y0=w[5:7], y1=w[7:9]) for pc, w in run['watch'].items()})
    if a.wav:
        rig.write_wav(out / 'fixture.wav', samples)
    (out / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps(result, indent=1))
    return 0 if result['all_blocks_completed'] else 1


if __name__ == '__main__':
    sys.exit(main())
