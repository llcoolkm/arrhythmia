# SPDX-License-Identifier: MIT
"""Rebuild fixed VERSION 206 DSP streams from own source and local official input.

Combined outputs contain the user's firmware material and must remain local.
The optional join reproduces only the public 7306 candidate; this is no flasher.
"""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from dspvoice import adapter as a
from dspvoice.assemble import assemble_text, encode
from dspvoice206.assembler import assemble
from dspvoice206.tables import table_data
import syx

CORE0_SHA = 'cbbeb9b1185fb52cd1ebee536d04d3589f716c94b55589153bd36f57bf7bf8c8'
CORE1_SHA = 'b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948'
ARM_SHA = 'ee39d911082fed98a4e5dd6e89a29ad0477fa4fb7ace96c323e2f71a2bd1dc6f'
IMAGE_SHA = 'ab8b07a2930e57620902b0fa45f3f0d7870475ca99b01c1c295f5a0610b1fe78'
SYX_SHA = 'd72fd952b3375df8b45582576f2ce67619fe16437d8d75c20b2d359a06615e72'
CORE0_AT, DATA_AT = 0x080768C0, 0x2DA0
COMPONENTS = {
    'instrument': (0x2200, 2922, '93d632a2594ed36ce9587bc221ecd571fbd3deab89e5d0c254ae45783c2773bd'),
    'startup': (0x2D80, 11, '706e629b145327f453fa7b630014b7398988859b80693a3b2ed09f0289af02de'),
    'suppression': (0xD32, 37, '2a377ee37cdc378b835055a87ce2cafbc463eb4c2415a5da64bb08367b51ea6a'),
}
HOOKS = {0x2DA: ('clr a', 'boot'), 0x4F4: ('clr a', 'note_on'),
         0x509: ('clr a', 'note_off'), 0x2FD: ('move y1,n3', 'pending')}
SOURCE_FILES = (
    'tests/test_dspvoice206_source.py',
    *(f'tools/dspvoice206/{name}' for name in (
        'README.md', 'NOTICE.md', 'assembler.py', 'build.py', 'tables.py',
        'instrument.asm', 'startup.asm', 'suppression.asm')),
    'tools/syx.py', 'tools/dspvoice/LICENSE', 'tools/dspvoice/adapter.py', 'tools/dspvoice/assemble.py',
    *(f'tools/dspvoice/toolchain/{name}' for name in (
        'CMakeLists.txt', 'dspasm.cpp', 'README.md', 'UPSTREAM.md', 'NOTICES.md')),
)


def verify_sources(root=None):
    root = ROOT if root is None else Path(root)
    manifest = json.loads((root / 'tools/dspvoice206/source-manifest.json').read_text())
    if manifest.get('format') != 'arrhythmia-dsp206-own-source/1' or set(manifest.get('files', {})) != set(SOURCE_FILES):
        raise ValueError('source manifest has an unexpected file closure')
    for name, digest in manifest['files'].items():
        if a.sha((root / name).read_bytes()) != digest:
            raise ValueError(f'source manifest mismatch: {name}')
    if (manifest.get('core0_sha256'), manifest.get('core1_sha256'), manifest.get('image_sha256'),
            manifest.get('syx_sha256')) != (CORE0_SHA, CORE1_SHA, IMAGE_SHA, SYX_SHA):
        raise ValueError('source manifest output pins differ')
    return manifest


def packed(words):
    return b''.join(a.w24(word) for word in words)


def own_components(executable):
    components, labels = {}, {}
    for name, (origin, count, digest) in COMPONENTS.items():
        assembler = assemble_text if name == 'suppression' else assemble
        words, _, labs = assembler(executable, (HERE / f'{name}.asm').read_text(), origin)
        if len(words) != count or a.sha(packed(words)) != digest:
            raise ValueError(f'{name} differs from the supported component pin')
        components[name], labels[name] = words, labs
    return components, labels


def core1_stream(stock, components, labels, executable):
    records, _, suffix = a.parse_payload(stock[6:])
    before = a.memory_map(records)
    if len(records) != 20:
        raise ValueError('input scatter record count differs')
    code = {COMPONENTS['suppression'][0] + i: w for i, w in enumerate(components['suppression'])}
    hooks = {}
    for at, (displaced, label) in HOOKS.items():
        old = encode(executable, [(at, displaced)])[0][1]
        new = encode(executable, [(at, f"jmp ${labels['suppression'][label]:x}")])[0][1]
        if len(old) != 1 or len(new) != 1 or before.get(('P', at)) != old[0]:
            raise ValueError('unsupported suppression integration entry')
        hooks[at] = new[0]
    for at, instruction in ((0x4A, 'jmp $ff8'), (0xFF8, 'ori #$03,mr'), (0xFF9, 'jmp $ff9')):
        words = encode(executable, [(at, instruction)])[0][1]
        if len(words) != 1 or at in code:
            raise ValueError('invalid fail-stop integration instruction')
        code[at] = words[0]
    if any(('P', at) in before for at in code):
        raise ValueError('own suppression program overlaps input')
    patched = [(space, start, tuple(hooks.get(start + i, w) if space == 'P' else w
                for i, w in enumerate(words))) for space, start, words in records]
    payload = a.encode_records(patched + a.contiguous_records('P', code)) + suffix
    decoded, _, tail = a.parse_payload(payload)
    expected = dict(before)
    expected.update({('P', at): w for at, w in {**hooks, **code}.items()})
    if a.memory_map(decoded) != expected or tail != suffix or len(decoded) != 23:
        raise ValueError('unexpected scatter mutation or suffix change')
    stream = a.w24(len(payload) // 3) + a.w24(0) + payload
    if a.sha(stream) != CORE1_SHA:
        raise ValueError('suppression stream differs from the supported pin')
    return stream


def build_streams(image, executable):
    verify_sources()
    stock0, stock1 = a.pinned_streams(image)
    components, labels = own_components(executable)
    data = table_data()
    memory = [a.u24(stock0[6:], i) for i in range(a.STOCK_CORE0_WORDS)]
    overlays = [(0x100, [0x0AF080, COMPONENTS['startup'][0]]),
                (0x224, [0x0BF080, COMPONENTS['instrument'][0]]),
                (COMPONENTS['instrument'][0], components['instrument']),
                (COMPONENTS['startup'][0], components['startup']), (DATA_AT, data)]
    touched = set()
    for at, words in overlays:
        addresses = set(range(at, at + len(words)))
        if addresses & touched or not 0 <= at <= at + len(words) <= 0x4000:
            raise ValueError('overlapping or out-of-bounds program overlay')
        if at >= len(memory) and at < 0x2200:
            raise ValueError('reserved program gap is occupied')
        touched |= addresses
    original = list(memory)
    memory.extend([0] * (DATA_AT + len(data) - len(memory)))
    for at, words in overlays:
        memory[at:at + len(words)] = words
    if any(memory[i] != w for i, w in enumerate(original) if i not in {0x100, 0x101, 0x224, 0x225}):
        raise ValueError('unexpected input program mutation')
    core0 = a.w24(len(memory)) + a.w24(0) + packed(memory)
    core1 = core1_stream(stock1, components, labels, executable)
    if len(memory) != 12897 or a.sha(core0) != CORE0_SHA:
        raise ValueError('instrument stream differs from the supported pin')
    if a.STREAM1 + len(core1) > CORE0_AT or CORE0_AT + len(core0) > a.FLASH_END:
        raise ValueError('stream placement overlaps or exceeds flash')
    if any(b != 0xFF for b in image[CORE0_AT - a.BASE:CORE0_AT - a.BASE + len(core0)]):
        raise ValueError('instrument flash placement is occupied')
    metadata = dict(format='arrhythmia-dsp206-local-build/1', version=0x206,
                    core0_sha256=a.sha(core0), core1_sha256=a.sha(core1),
                    core0_pointer=CORE0_AT, core1_pointer=a.STREAM1,
                    own_components={n: dict(origin=v[0], words=v[1], sha256=v[2]) for n, v in COMPONENTS.items()},
                    assembler_sha256=a.sha(Path(executable).read_bytes()))
    return core0, core1, metadata


def final_firmware(arm, core0, core1, stock_ref):
    if a.sha(arm) != ARM_SHA or a.sha(core0) != CORE0_SHA or a.sha(core1) != CORE1_SHA:
        raise ValueError('join requires the exact public ARM and supported DSP streams')
    stock = stock_ref['image']
    for address, data in ((a.STREAM0, a.pinned_streams(stock)[0]), (a.STREAM1, a.pinned_streams(stock)[1])):
        off = address - a.BASE
        if arm[off:off + len(data)] != data:
            raise ValueError('ARM input changed a local input stream')
    for pointer in (a.PTR_CORE0, a.PTR_CORE1):
        off = pointer - a.BASE
        if arm[off:off + 4] != stock[off:off + 4]:
            raise ValueError('ARM input already changes a stream pointer')
    at = CORE0_AT - a.BASE
    if any(b != 0xFF for b in arm[at:at + len(core0)]):
        raise ValueError('ARM input overlaps the instrument placement')
    image = bytearray(arm)
    image.extend(b'\xff' * (at + len(core0) - len(image)))
    image[a.STREAM1 - a.BASE:a.STREAM1 - a.BASE + len(core1)] = core1
    image[at:at + len(core0)] = core0
    struct.pack_into('<I', image, a.PTR_CORE0 - a.BASE, CORE0_AT)
    struct.pack_into('<H', image, 0x1CA, 7306)
    image.extend(b'\xff' * (-len(image) % 4))
    version = bytes([0, 0, 0, 8, 0, 6])
    stream = syx.build(bytes(image), stock_ref['init'][:2] + version + stock_ref['init'][8:],
                       stock_ref['header_prefix'][:1] + version)
    if a.sha(image) != IMAGE_SHA or a.sha(stream) != SYX_SHA:
        raise ValueError('source-built final image differs from the supported pin')
    return bytes(image), stream


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--arm-image', type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output directory already exists')
    raw = args.stock.read_bytes()
    if a.sha(raw) != a.STOCK_SYX_SHA256:
        parser.error('official input SysEx differs from the supported pin')
    ref = syx.parse(raw)
    if not ref['crc_ok'] or ref['device'] != 0x63 or ref['header_prefix'][0] != 0:
        parser.error('official input CRC, device or target differs')
    core0, core1, metadata = build_streams(ref['image'], args.assembler.resolve())
    files = {'core0-instrument.bin': core0, 'core1-suppression.bin': core1,
             'build.json': (json.dumps(metadata, indent=2, sort_keys=True) + '\n').encode()}
    if args.arm_image:
        image, stream = final_firmware(args.arm_image.read_bytes(), core0, core1, ref)
        files.update({'arrhythmia-firmware-7306.bin': image, 'arrhythmia-firmware-7306.syx': stream})
    files['SHA256SUMS'] = ''.join(f'{a.sha(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice206-', dir=args.out.parent) as directory:
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        Path(directory).rename(args.out)
    print(files['SHA256SUMS'].decode(), end='')


if __name__ == '__main__':
    main()
