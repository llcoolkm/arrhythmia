"""Rebuild the FM-only 0.3.0 public image offline, from the official local stock input.

python3 -I tools/rel030_release.py --stock firmware/circuit-rhythm-5706.syx --fm fm.dse --out NEW_DIRECTORY
The existing build/circuit-rhythm-5706.bin setup must match --stock. No device access.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import build_lightshow as compiler
import crpatch
import syx

LABEL, VERSION = 7358, [0, 0, 0, 8, 5, 8]
PROFILE = 'arrhythmia-firmware-arm-engopt'
MAIN_END = 0x08079F94
BASE, MAIN, C0, C0_SOURCE, C0_SIZE = 0x08010000, 0x0806A570, 0x0807A01C, 0x0801A838, 24525
C0_PTR, C1_PTR, C1, C1_SIZE, INIT_VT = 0x08031658, 0x08031660, 0x080101F0, 42567, 0x080692A8
STOCK_SYX = '11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf'
STOCK_BIN = 'd5b9ff5dadf2e284f80d69ecb7087486cf0df315a697bed8272af9de0e90df15'
FM_SHA = '236b20874ed999c46a8575cd3336b6ac26df9770d11f9f9dfdd6f711cd0029ad'
IMAGE_SHA = 'a45b7df13361c8a0e341be1082d66b8e5f4a439972996fe999126adf7cf77f39'
SYX_SHA = 'adfd566c1c28d3f9f1484ac44e343d08b3fc7c1638e91b07b2265540af8a852d'
DESCRIPTION = ('Circus Arrhythmia FM 0.3.0: card-loaded FM with 8x feedback range and live feedback; stock sample playback '
               'when no engine is selected. Arpeggiator, scales, modules and a card loader with pack and engine inventory. '
               'Hardware runs for build 7358 and final Euclid waived; voice count and DSP headroom not measured.')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def remote_disabled(defines):
    """Undefined uses the reviewed C default; explicit values must be literal zero."""
    for item in defines:
        key, separator, value = item.partition('=')
        if key == 'REMOTE_PACK_LOAD' and (not separator or not re.fullmatch(r'(?:0|0[xX]0+)[uUlL]*', value)):
            return False
    return True


def verify_sources():
    manifest = json.loads((ROOT / 'tools/rel030release/source-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        need(sha((ROOT / name).read_bytes()) == digest, f'source pin differs: {name}')
    return manifest


def stock_input(path):
    raw = Path(path).read_bytes()
    need(sha(raw) == STOCK_SYX, 'official stock SysEx SHA-256 differs')
    ref = syx.parse(raw)
    stock = ref['image']
    need(ref['crc_ok'] and ref['device'] == 0x63 and ref['header_prefix'][0] == 0,
         'stock CRC/device/application target differs')
    need(sha(stock) == STOCK_BIN and len(stock) == 370032, 'official stock image differs')
    need((ROOT / 'build/circuit-rhythm-5706.bin').read_bytes() == stock,
         'local build/circuit-rhythm-5706.bin differs from --stock; complete the documented stock setup')
    return raw, stock


def fm_input(path):
    blob = Path(path).read_bytes()
    need(sha(blob) == FM_SHA and len(blob) == 14906, 'fm.dse SHA-256/size differs')
    need(zlib.crc32(blob) == 0x2EB146FE, 'fm.dse whole-file CRC differs')
    # SHA-256 binds the complete CRE2 header and body, including ABI207/base/kind/build and body CRC.
    return blob


def public_spec(arm, stock):
    cfg = compiler.TARGETS[PROFILE]
    need(remote_disabled(cfg['defines']), 'public image forbids remote pack loading')
    need('ENGINE_FLASH=0u' in cfg['defines'] and cfg.get('main_cap') == C0,
         'public build requires ENGINE_FLASH=0 and the stock relocation cap')
    need(arm.get('profile', {}).get('engine_pack'), 'not an ENGINE_PACK spec')
    spec = dict(arm)
    patches = [dict(p) for p in arm['patches']]
    labels = [p for p in patches if int(p['addr'], 16) == BASE + 0x1CA]
    need(len(labels) == 1, 'build label mutation missing or ambiguous')
    labels[0].update(new=struct.pack('<H', LABEL).hex(), note='public 0.3.0 build label')
    def add(at, data, note):
        off = at - BASE
        old = stock[off:off + len(data)] if off < len(stock) else b'\xff' * len(data)
        patches.append(dict(addr=f'0x{at:08X}', expect=old.hex(), new=data.hex(), note=note))
    add(C0, stock[C0_SOURCE-BASE:C0_SOURCE-BASE+C0_SIZE], 'exact stock core 0 copied locally')
    add(C0_PTR, struct.pack('<I', C0), 'select-0 pointer to relocated stock core 0')
    spans = sorted((int(p['addr'], 16), len(bytes.fromhex(p['new']))) for p in patches)
    need(all(BASE <= at < at+n <= BASE+0x70000 and n > 0 for at, n in spans), 'patch outside flash')
    need(all(a+n <= b for (a, n), (b, _) in zip(spans, spans[1:])), 'overlapping patches')
    need(all(len(bytes.fromhex(p['new'])) == len(bytes.fromhex(p['expect'])) for p in patches), 'patch length differs')
    need(not any(at <= INIT_VT < at+n for at, n in spans), 'INIT vtable must remain stock')
    main_end = max(at+n for at, n in spans if MAIN <= at < C0)
    need(main_end == MAIN_END, 'ARM body differs from the reviewed ARM body')
    spec.update(patches=patches, version_nibbles=VERSION, description=DESCRIPTION)
    return spec


def check_image(image, stock):
    need(image[C0-BASE:C0-BASE+C0_SIZE] == stock[C0_SOURCE-BASE:C0_SOURCE-BASE+C0_SIZE], 'core 0 differs')
    need(image[C1-BASE:C1-BASE+C1_SIZE] == stock[C1-BASE:C1-BASE+C1_SIZE], 'core 1 differs')
    need(image[C1_PTR-BASE:C1_PTR-BASE+4] == stock[C1_PTR-BASE:C1_PTR-BASE+4], 'core 1 pointer differs')
    need(image[INIT_VT-BASE:INIT_VT-BASE+4] == stock[INIT_VT-BASE:INIT_VT-BASE+4], 'INIT gate present')
    need(image[MAIN_END-BASE:C0-BASE] == b'\xff' * (C0-MAIN_END), 'gate landing is not erased')
    need(int.from_bytes(image[0x1CA:0x1CC], 'little') == LABEL, 'label differs')



def canonical_spec(stock, image, version, gap=32):
    """Describe changed spans deterministically, merging short gaps and splitting at the input end."""
    need(len(image) >= len(stock), 'output is shorter than the input')
    extended = stock + b'\xff' * (len(image) - len(stock))
    spans = []
    for k in range(len(image)):
        if image[k] != extended[k]:
            if spans and k - spans[-1][1] < gap and not (spans[-1][1] <= len(stock) <= k):
                spans[-1][1] = k + 1
            else:
                spans.append([k, k + 1])
    split = []
    for first, last in spans:
        split.extend([[first, len(stock)], [len(stock), last]] if first < len(stock) < last else [[first, last]])
    patches = []
    for first, last in split:
        patch = dict(addr=hex(BASE + first), expect=extended[first:last].hex(), new=image[first:last].hex())
        if BASE + first < crpatch.VECTORS_END:
            patch['allow_vectors'] = True
        patches.append(patch)
    return dict(base_sha256=sha(stock), version_nibbles=list(version), patches=patches, description='')


def build_one(stock_path, out):
    verify_sources()
    raw, stock = stock_input(stock_path)
    out.mkdir(parents=True)
    # Intermediate patch.py specs contain the user's stock bytes and stay in a temporary local directory.
    with tempfile.TemporaryDirectory(prefix='rel030-spec-') as temporary:
        compiler.main(PROFILE, temporary)
        arm = json.loads((Path(temporary) / f'{PROFILE}.json').read_text())
        intermediate = crpatch.build(public_spec(arm, stock), raw, 'firmware', DESCRIPTION, release_name='Circus Arrhythmia FM 0.3.0')
    stream, image = crpatch.apply(intermediate, raw)
    release = crpatch.build(canonical_spec(stock, image, VERSION), raw, 'firmware', DESCRIPTION,
                            release_name='Circus Arrhythmia FM 0.3.0')
    need(crpatch.apply(release, raw) == (stream, image), 'canonical patch spans differ')
    previous = json.loads((ROOT / 'release/v0.2.2.json').read_text())
    devkit = next(b for b in previous['builds'] if b['name'] == 'devkit')
    need(devkit['output']['label'] == 7228, 'unexpected carried devkit identity')
    crpatch.add_build(release, devkit, raw)
    need(not crpatch.scan(release, stock), 'published literal stock match')
    check_image(image, stock)
    need(sha(image) == IMAGE_SHA and sha(stream) == SYX_SHA, 'output differs from the reproducible 7358 byte pins')
    parsed = syx.parse(stream)
    need(parsed['crc_ok'] and parsed['image'] == image and parsed['init'][2:8] == bytes(VERSION), 'output roundtrip differs')
    (out / 'release').mkdir()
    (out / 'release/v0.3.0.json').write_text(crpatch.dump(release))
    (out / 'arrhythmia-7358.bin').write_bytes(image)
    (out / 'arrhythmia-7358.syx').write_bytes(stream)
    return release


def files(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def build(stock_path, fm_path, out):
    verify_sources()
    stock_input(stock_path)
    fm = fm_input(fm_path)
    need(not out.exists() and not out.is_symlink(), 'output directory already exists')
    out.mkdir(parents=True)
    for number in (1, 2):
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--stock', str(stock_path),
                                 '--build-once', str(out / f'build-{number}')], cwd=ROOT, capture_output=True, text=True)
        need(result.returncode == 0, 'fresh build failed: ' + result.stderr[-2000:])
    first = files(out / 'build-1')
    need(first == files(out / 'build-2'), 'two fresh builds differ')
    for name, blob in first.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    (out / 'fm.dse').write_bytes(fm)
    release = json.loads(first['release/v0.3.0.json'])
    report = dict(format='rel030-public-build/1', status='UNPINNED; independent review required',
                  hardware_tested=False, publication_authorized=False, label=LABEL, version=''.join(map(str, VERSION)), app_bytes=' '.join(f'{(LABEL >> shift) & 0x7F:02X}' for shift in (14, 7, 0)),
                  profile=PROFILE, ENGINE_FLASH=0, REMOTE_PACK_LOAD=False, CARD_INVENTORY=True, gate=False, reproducible_fresh_processes=True,
                  output=release['builds'][0]['output'], fm_sha256=FM_SHA,
                  sources=verify_sources()['files'])
    (out / 'BUILD.json').write_text(json.dumps(report, indent=1)+'\n')
    (out / 'SHA256SUMS').write_text(''.join(f'{sha(blob)}  {name}\n' for name, blob in sorted(files(out).items())))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--fm', type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out', type=Path)
    group.add_argument('--build-once', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.build_once:
            build_one(args.stock.resolve(), args.build_once.resolve())
        else:
            need(args.fm is not None, '--fm is required (pinned fm.dse; see source rebuild instructions)')
            print(json.dumps(build(args.stock.resolve(), args.fm.resolve(), args.out.absolute()), indent=1))
    except (ValueError, OSError, crpatch.Refused) as exc:
        parser.exit(1, f'refused: {exc}\n')


if __name__ == '__main__':
    main()
