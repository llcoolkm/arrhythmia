# SPDX-License-Identifier: MIT
"""Rebuild fixed VERSION 207 DSP streams from own source and local official input.

Combined outputs contain the user's firmware material and must remain local.
The optional join reproduces only the listed public images (JOINS): the 0.2 release image 7323 and the 0.2.1
image 7329, each from its exact ARM part, with the same DSP streams (-7 dB output gain, FM feedback x4);
this is no flasher.
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
from dspvoice207.tables import table_data, cosine32
import syx

CORE0_SHA = '56c8363d9a68c74fffde5099ed26819da9392d11193136f2242c417c422c4880'
CORE1_SHA = 'b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948'
# Exact source-reproduction bindings; this builder is not an installer.
ARM_SHA = '4cff8b24b1b985b50a594e096522483476682a3ffce0efc27d5a29937483142e'
IMAGE_SHA = 'ee083547dc850a627a987ec15dbbc592d932b3254f8c84f657f1c70410ab73e5'
SYX_SHA = 'f5f77f2902aa7934c0c5dd22bbb890ce4c5aaa8f683d4f55e10897df95178cd5'
RELEASE_LABEL = 7323
RELEASE_VERSION = (0, 0, 0, 8, 2, 3)
# Every supported join: ARM part SHA-256 -> (image label, version, image SHA-256, .syx SHA-256, far blob). The ARM
# part selects the image; any other ARM part is refused. 7329: the 0.2.1 ARM part (arrhythmia-firmware-arm-021,
# label 7304), whose far blob [0x0801B000, 0x0801C718) lies in the old core-0 stream after core 1's end (the only
# ARM bytes allowed there).
FAR_CAP = 0x0801B000
JOINS = {
    ARM_SHA: (RELEASE_LABEL, RELEASE_VERSION, IMAGE_SHA, SYX_SHA, None),
    'd3ab9200c857eb6abb070ebf9424c26611ddd117c70beba77325cfd84166eb24': (
        7329, (0, 0, 0, 8, 2, 9), '10939096414b70a25d2e5bdc6d593df4e56563b9abdc9d9870c88d3542fb3619',
        'f756c03a71039ba3edd248d53232f9bac24367a4cf800fe6ffd9b8d00fb15251', (0x0801B000, 0x0801C718)),
}
ENTRY = 0x2200
CORE0_AT, DATA_AT = 0x080768C0, 0x2DA0
COMPONENTS = {
    'instrument': (0x2000, 3428, 'd726f231f41def47a734c33fe425a5246c355f1addb9fbf235f74474e6a34c63'),
    'startup': (0x2D80, 11, '706e629b145327f453fa7b630014b7398988859b80693a3b2ed09f0289af02de'),
    'suppression': (0xD32, 37, '2a377ee37cdc378b835055a87ce2cafbc463eb4c2415a5da64bb08367b51ea6a'),
}
HOOKS = {0x2DA: ('clr a', 'boot'), 0x4F4: ('clr a', 'note_on'),
         0x509: ('clr a', 'note_off'), 0x2FD: ('move y1,n3', 'pending')}
SOURCE_FILES = (
    'tests/test_dspvoice207rel_source.py',
    *(f'tools/dspvoice207rel/{name}' for name in (
        'README.md', 'NOTICE.md', 'build.py', 'instrument.asm')),
    *(f'tools/dspvoice206/{name}' for name in (
        'assembler.py', 'tables.py', 'startup.asm', 'suppression.asm', 'NOTICE.md')),
    'tools/dspvoice207/tables.py', 'tools/dspvoice207/instrument.asm',
    'tools/syx.py', 'tools/dspvoice/LICENSE', 'tools/dspvoice/adapter.py', 'tools/dspvoice/assemble.py',
    *(f'tools/dspvoice/toolchain/{name}' for name in (
        'CMakeLists.txt', 'dspasm.cpp', 'README.md', 'UPSTREAM.md', 'NOTICES.md')),
)


def verify_sources(root=None):
    root = ROOT if root is None else Path(root)
    manifest = json.loads((root / 'tools/dspvoice207rel/source-manifest.json').read_text())
    if manifest.get('format') != 'arrhythmia-dsp207rel-own-source/2' or set(manifest.get('files', {})) != set(SOURCE_FILES):
        raise ValueError('source manifest has an unexpected file closure')
    for name, digest in manifest['files'].items():
        if a.sha((root / name).read_bytes()) != digest:
            raise ValueError(f'source manifest mismatch: {name}')
    if (manifest.get('core0_sha256'), manifest.get('core1_sha256'), manifest.get('image_sha256'),
            manifest.get('syx_sha256')) != (CORE0_SHA, CORE1_SHA, IMAGE_SHA, SYX_SHA):
        raise ValueError('source manifest output pins differ')
    if (manifest.get('arm_sha256'), manifest.get('release_label'), manifest.get('release_version')) != (
            ARM_SHA, RELEASE_LABEL, None if RELEASE_VERSION is None else list(RELEASE_VERSION)):
        raise ValueError('source manifest release binding differs')
    if manifest.get('joins') != {k: [v[0], list(v[1]), v[2], v[3], v[4] and list(v[4])] for k, v in JOINS.items()}:
        raise ValueError('source manifest join bindings differ')
    return manifest


def packed(words):
    return b''.join(a.w24(word) for word in words)


def own_components(executable):
    components, labels = {}, {}
    for name, (origin, count, digest) in COMPONENTS.items():
        assembler = assemble_text if name == 'suppression' else assemble
        source = HERE / 'instrument.asm' if name == 'instrument' else HERE.parent/'dspvoice206'/f'{name}.asm'
        words, _, labs = assembler(executable, source.read_text(), origin)
        if name == 'instrument':
            if labs.get('entry') != ENTRY:
                raise ValueError('instrument entry moved')
            at = labs['release_cosine_weights']-origin
            if words[at:at+32] != [0]*32:
                raise ValueError('terminal-weight placeholders differ')
            words[at:at+32] = cosine32()
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
                (0x224, [0x0BF080, ENTRY]),
                (COMPONENTS['instrument'][0], components['instrument']),
                (COMPONENTS['startup'][0], components['startup']), (DATA_AT, data)]
    touched = set()
    for at, words in overlays:
        addresses = set(range(at, at + len(words)))
        if addresses & touched or not 0 <= at <= at + len(words) <= 0x4000:
            raise ValueError('overlapping or out-of-bounds program overlay')
        if at >= len(memory) and at < 0x2200 and (at, len(words)) != (0x2000, 3428):
            raise ValueError('unapproved program-gap overlay')
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
    metadata = dict(format='arrhythmia-dsp207rel-local-build/1', version=0x207,
                    core0_sha256=a.sha(core0), core1_sha256=a.sha(core1),
                    core0_pointer=CORE0_AT, core1_pointer=a.STREAM1,
                    own_components={n: dict(origin=v[0], words=v[1], sha256=v[2]) for n, v in COMPONENTS.items()},
                    assembler_sha256=a.sha(Path(executable).read_bytes()))
    return core0, core1, metadata


def final_firmware(arm, core0, core1, stock_ref):
    if not all((ARM_SHA, IMAGE_SHA, SYX_SHA, RELEASE_VERSION)):
        raise ValueError('public 7323 join pins are not bound')
    if a.sha(arm) not in JOINS or a.sha(core0) != CORE0_SHA or a.sha(core1) != CORE1_SHA:
        raise ValueError('join requires the exact public ARM and supported DSP streams')
    stock = stock_ref['image']
    label, release_version, image_sha, syx_sha, far = JOINS[a.sha(arm)]
    old0 = a.pinned_streams(stock)[0]
    if far and not (a.STREAM1 + len(core1) <= FAR_CAP <= far[0] < far[1] <= a.STREAM0 + len(old0)):
        raise ValueError('far blob outside the old core-0 stream after the core-1 cap')
    for address, data in ((a.STREAM0, old0), (a.STREAM1, a.pinned_streams(stock)[1])):
        off = address - a.BASE
        got = bytearray(arm[off:off + len(data)])
        if far and address == a.STREAM0:      # the far blob's bytes are the ARM's own; the rest stays the input's
            lo, hi = far[0] - address, far[1] - address
            if got[lo:hi] == data[lo:hi]:
                raise ValueError('far blob missing from the ARM input')
            got[lo:hi] = data[lo:hi]
        if bytes(got) != data:
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
    struct.pack_into('<H', image, 0x1CA, label)
    image.extend(b'\xff' * (-len(image) % 4))
    version = bytes(release_version)
    stream = syx.build(bytes(image), stock_ref['init'][:2] + version + stock_ref['init'][8:],
                       stock_ref['header_prefix'][:1] + version)
    if a.sha(image) != image_sha or a.sha(stream) != syx_sha:
        raise ValueError('source-built final image differs from the supported pin')
    return bytes(image), stream


def rename_noreplace(source, destination):
    """Linux atomic directory publication without replacing a racing target."""
    import ctypes
    import os
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise OSError('atomic no-replace rename is unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--arm-image', type=Path)
    args = parser.parse_args()
    if args.out.exists() or args.out.is_symlink():
        parser.error('output directory already exists')
    if args.arm_image and not all((ARM_SHA, IMAGE_SHA, SYX_SHA, RELEASE_VERSION)):
        parser.error('public 7323 join pins are not bound')
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
        label = JOINS[a.sha(args.arm_image.read_bytes())][0]
        files.update({f'arrhythmia-firmware-{label}.bin': image, f'arrhythmia-firmware-{label}.syx': stream})
    files['SHA256SUMS'] = ''.join(f'{a.sha(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice207rel-', dir=args.out.parent) as directory:
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        rename_noreplace(Path(directory), args.out)
    print(files['SHA256SUMS'].decode(), end='')


if __name__ == '__main__':
    main()
