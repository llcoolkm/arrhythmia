"""Build the released Arrhythmia 0.1.1 DSP from source and user-supplied stock.

Generated combined streams contain the user's stock material. Keep outputs
local; distribute source and the release patch, not these stream binaries.
"""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import adapter as a
from assemble import assemble_text, encode
import parameters
import tables
import syx

CORE1_SHA = 'b792d02b12c5526cc538a805244a6456439d65efe6c90c397f3ee3867e5a9b44'
CORE0_SHA = '4a8618bb189d5ea05374431d62f39331604221ea5f17219d1d6267d25495b814'
FIRMWARE_SHA = '920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b'
IMAGE_SHA = 'fef8e4072696e177104aa2d31d7fcc725ba4dc746754e18d1f8a97a33deb5aaf'


def own_code(executable):
    symbols = json.loads((HERE / 'symbols.json').read_text())
    overlay = {}
    for name, info in symbols['components'].items():
        words, _, labels = assemble_text(executable, (HERE / f'{name}.asm').read_text(), info['origin'])
        if (len(words) != info['words'] or labels != info['labels'] or
                a.sha(b''.join(map(a.w24, words))) != info['u24be_sha256']):
            raise ValueError(f'assembled {name} differs from the released source pin')
        for offset, word in enumerate(words):
            address = info['origin'] + offset
            if address in overlay:
                raise ValueError('own code components overlap')
            overlay[address] = word
    records = [(info['address'], info['instruction']) for info in symbols['hooks'].values()]
    fault = symbols['fault']
    records += [(fault['vector'], f"jmp ${fault['origin']:x}")]
    records += [(fault['origin'] + i, instruction)
                for i, instruction in enumerate(fault['instructions'])]
    for address, words, _ in encode(executable, records):
        if len(words) != 1 or address in overlay:
            raise ValueError('integration instruction overlaps or changes size')
        overlay[address] = words[0]
    x = {address: 0 for address in range(0x2E40, 0x2E6C)}
    x.update({address: 0 for address in range(0x2FD0, 0x2FE8)})
    x[0x2E70] = 0
    x.update(parameters.initial_words())
    y = tables.saw_words()
    if (len(x), len(y)) != (327, 1024):
        raise ValueError('initialization sizes changed')
    return overlay, x, y, symbols


def build_streams(image, executable):
    core0, stock1 = a.pinned_streams(image)
    records, _, suffix = a.parse_payload(stock1[6:])
    stock_map = a.memory_map(records)
    overlay, x, y, symbols = own_code(executable)
    hooks = {info['address'] for info in symbols['hooks'].values()}
    tail = set(range(0xD32, 0xFE2)) | {0xFF8, 0xFF9}
    if set(overlay) != tail | hooks | {0x4A}:
        raise ValueError('own program addresses differ from release layout')
    if any(('P', address) in stock_map for address in tail | {0x4A}):
        raise ValueError('own program overlaps input program')
    for space, words in (('X', x), ('Y', y)):
        if any((space, address) in stock_map for address in words):
            raise ValueError('own initialization overlaps input data')
    patched, found = [], set()
    for space, address, words in records:
        replacement = list(words)
        if space == 'P':
            for offset in range(len(words)):
                at = address + offset
                if at in hooks:
                    replacement[offset] = overlay[at]
                    found.add(at)
        patched.append((space, address, tuple(replacement)))
    if found != hooks:
        raise ValueError('integration entry absent from supported input')
    additions = (a.contiguous_records('P', {at: word for at, word in overlay.items() if at not in hooks}) +
                 a.contiguous_records('X', x) + a.contiguous_records('Y', y))
    body = a.encode_records(patched + additions) + suffix
    core1 = a.w24(len(body) // 3) + a.w24(0) + body
    decoded, terminator, decoded_suffix = a.parse_payload(body)
    expected = dict(stock_map)
    for space, words in (('P', overlay), ('X', x), ('Y', y)):
        expected.update({(space, at): word for at, word in words.items()})
    if (a.memory_map(decoded) != expected or decoded_suffix != suffix or
            len(records) != 20 or len(additions) != 9 or len(body) // 3 != 16256):
        raise ValueError('rebuilt scatter records differ')
    if a.sha(core1) != CORE1_SHA or a.sha(core0) != CORE0_SHA:
        raise ValueError('rebuilt streams differ from the released hashes')
    at0 = (a.FLASH_END - len(core0)) & ~0x1F
    end1 = a.STREAM1 + len(core1)
    if end1 > a.STREAM0 + len(core0) or end1 > at0 or at0 + len(core0) > a.FLASH_END:
        raise ValueError('rebuilt streams exceed the reserved flash ranges')
    old_region = image[at0 - a.BASE:at0 - a.BASE + len(core0)]
    if any(byte != 0xFF for byte in old_region):
        raise ValueError('relocated stream region is occupied')
    manifest = dict(profile='Arrhythmia-firmware-0.1.1', wire_abi_frozen=True,
        firmware_image=False, stock_sha256=a.STOCK_SHA256,
        core0_stock_stream_sha256=a.sha(core0), core1_sha256=a.sha(core1),
        core0_new_pointer=at0, core0_stream_end=at0 + len(core0),
        core1_pointer=a.STREAM1, core1_stream_end=end1,
        core1_new_words=len(body) // 3, core1_new_records=len(decoded),
        core1_new_terminator=terminator, enable_initial_value=0,
        occupied_tail_words=690, remaining_words=28, x_init_words=len(x), y_saw_words=len(y),
        hook_addresses=sorted(hooks), new_record_layout=[(s, at, len(words)) for s, at, words in additions],
        parameter_layout=parameters.describe(), source_sha256={str(p.relative_to(HERE)): a.sha(p.read_bytes())
            for p in sorted(HERE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts
            and p.suffix != '.pyc'},
        assembler_sha256=a.sha(Path(executable).read_bytes()))
    return core0, core1, manifest


def final_firmware(arm, core0, core1, manifest, stock_ref):
    """Combine a locally source-built ARM base with the released DSP layer."""
    if len(arm) > a.FLASH_END - a.BASE or len(arm) < 0x21664:
        raise ValueError('ARM base size outside bounds')
    image = bytearray(arm)
    # A base must retain the input streams/pointers. No already-layered image.
    stock_image = stock_ref['image']
    for pointer in (a.PTR_CORE0, a.PTR_CORE1):
        off = pointer - a.BASE
        if image[off:off + 4] != stock_image[off:off + 4]:
            raise ValueError('ARM base stream pointer already modified')
    for address, original in ((a.STREAM0, a.pinned_streams(stock_image)[0]),
                              (a.STREAM1, a.pinned_streams(stock_image)[1])):
        off = address - a.BASE
        if image[off:off + len(original)] != original:
            raise ValueError('ARM base modified an input DSP stream')
    at0 = manifest['core0_new_pointer'] - a.BASE
    if any(byte != 0xFF for byte in image[at0:at0 + len(core0)]):
        raise ValueError('ARM base overlaps relocated DSP stream')
    size = at0 + len(core0)
    if len(image) < size:
        image.extend(b'\xFF' * (size - len(image)))
    image[a.STREAM1 - a.BASE:a.STREAM1 - a.BASE + len(core1)] = core1
    image[at0:at0 + len(core0)] = core0
    struct.pack_into('<I', image, a.PTR_CORE0 - a.BASE, manifest['core0_new_pointer'])
    struct.pack_into('<H', image, 0x1CA, 7261)
    image.extend(b'\xFF' * (-len(image) % 4))
    version = bytes([0, 0, 0, 7, 9, 2])
    stream = syx.build(bytes(image), stock_ref['init'][:2] + version + stock_ref['init'][8:],
                       stock_ref['header_prefix'][:1] + version)
    if a.sha(image) != IMAGE_SHA or a.sha(stream) != FIRMWARE_SHA:
        raise ValueError('source-built final firmware differs from the released 7261 pin')
    return bytes(image), stream


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--stock', type=Path, required=True, help="user's official pinned 5706 .syx")
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='new local output directory')
    parser.add_argument('--arm-image', type=Path, help='optional locally rebuilt 7251 ARM .bin, produces final 7261')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output directory already exists; choose a fresh directory')
    raw = args.stock.read_bytes()
    if a.sha(raw) != a.STOCK_SYX_SHA256:
        parser.error('stock .syx differs from pinned official input')
    ref = syx.parse(raw)
    if not ref['crc_ok']:
        parser.error('stock input CRC mismatch')
    core0, core1, manifest = build_streams(ref['image'], args.assembler.resolve())
    files = {'core0-stock-relocated.bin': core0, 'core1-voice.bin': core1,
             'manifest.json': (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()}
    if args.arm_image:
        image, stream = final_firmware(args.arm_image.read_bytes(), core0, core1, manifest, ref)
        files.update({'arrhythmia-firmware-7261.bin': image, 'arrhythmia-firmware-7261.syx': stream})
    files['SHA256SUMS'] = ''.join(f'{a.sha(blob)}  {name}\n' for name, blob in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice-', dir=args.out.parent) as temporary:
        staging = Path(temporary) / 'result'
        staging.mkdir()
        for name, blob in files.items():
            (staging / name).write_bytes(blob)
        staging.rename(args.out)
    print(f'Built released DSP streams from own source: {args.out}')
    print(f'core1 {a.sha(core1)}; core0 {a.sha(core0)}')
    if args.arm_image:
        print(f'firmware7261 {FIRMWARE_SHA}')


if __name__ == '__main__':
    main()
