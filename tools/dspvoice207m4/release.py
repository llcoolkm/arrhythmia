# SPDX-License-Identifier: MIT
"""Rebuild only the pinned public7339 image from official stock, own DSP source and exact ARM input."""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from dspvoice207m4 import build as component

rel, a, syx = component.rel, component.a, component.rel.syx
LABEL, VERSION = 7339, bytes((0, 0, 0, 8, 3, 9))
# Approved public7339 inputs/outputs from the frozen b51d653 ARM build.
ARM_SHA = 'a1f5ed53d3d23c250736eede1c8fa27a13d320783d599b758bd9627db0b7c2ac'
IMAGE_SHA = '5f10a0ac33e1358ab9bf51eb22f05d1172e22f5c0c91cb5b86dec2e1d23458df'
SYX_SHA = '7aadbde0811a77b23c05a06fca34c36d27d98c98945f3f587bb8d7ccf63fc822'
ARM_SIZE, ARM_END = 419316, 0x080765F4
MAIN_AT = 0x0806A570
MAIN_SHA = '37388ef4e7b1aaf8bc4197dcceb79b12bdb747f4362e101898cecf7428521557'
IMAGE_SIZE, IMAGE_CRC = 458732, 0x6E5A4843
FAR = (0x0801B000, 0x0801E70C, 0x08020805)
FAR_SHA = '7e851c630b2421e57b02d9649ce5b30ce88d9fdbc64b33ee099da7c35e0cec73'
FAR_KEY, FAR_COUNT = 0x5B7B01D8, 6
RELEASE_FILES = ('tools/dspvoice207m4/release.py', 'tools/dspvoice207m4/RELEASE.md',
                 'tests/test_dspvoice207m4_release.py')


def verify_sources():
    component.verify_sources()
    manifest = json.loads((HERE / 'release-manifest.json').read_text())
    expected = dict(format='arrhythmia-dsp207m4-public-release/1', label=LABEL, version=list(VERSION),
                    arm_sha256=ARM_SHA, image_sha256=IMAGE_SHA, syx_sha256=SYX_SHA,
                    far=None if FAR is None else list(FAR), far_sha256=FAR_SHA, far_key=FAR_KEY, far_count=FAR_COUNT,
                    arm_size=ARM_SIZE, arm_end=ARM_END, main_at=MAIN_AT, main_sha256=MAIN_SHA,
                    image_size=IMAGE_SIZE, image_crc=IMAGE_CRC, core0_sha256=component.CORE0_SHA,
                    core1_sha256=component.CORE1_SHA,
                    component_manifest_sha256=a.sha((HERE / 'source-manifest.json').read_bytes()))
    if any(manifest.get(k) != v for k, v in expected.items()) or set(manifest.get('files', {})) != set(RELEASE_FILES):
        raise ValueError('release manifest bindings or file closure differ')
    for name, digest in manifest['files'].items():
        if a.sha((ROOT / name).read_bytes()) != digest:
            raise ValueError(f'release source mismatch: {name}')
    return manifest


def require_bound():
    if not all((ARM_SHA, IMAGE_SHA, SYX_SHA, ARM_SIZE, ARM_END, MAIN_SHA, IMAGE_SIZE, IMAGE_CRC, FAR, FAR_SHA, FAR_KEY, FAR_COUNT)):
        raise ValueError('public7339 release pins are not bound')


def final_firmware(arm, core0, core1, stock_ref):
    require_bound()
    if len(arm) != ARM_SIZE or a.sha(arm) != ARM_SHA or a.sha(core0) != component.CORE0_SHA or a.sha(core1) != component.CORE1_SHA:
        raise ValueError('join requires the exact public7339 ARM and supported DSP streams')
    if not a.BASE < ARM_END <= rel.CORE0_AT:
        raise ValueError('ARM end overlaps core-0 placement')
    if a.sha(arm[MAIN_AT-a.BASE:ARM_END-a.BASE]) != MAIN_SHA:
        raise ValueError('ARM main blob differs from its exact pin')
    stock = stock_ref['image']
    old0, old1 = a.pinned_streams(stock)
    start, end, limit = FAR
    if not (a.STREAM1 + len(core1) <= rel.FAR_CAP <= start < end <= limit <= a.STREAM0 + len(old0)):
        raise ValueError('far blob exceeds the core-1 cap or old core-0 bounds')
    far_blob = arm[start-a.BASE:end-a.BASE]
    if a.sha(far_blob) != FAR_SHA or struct.unpack_from('<4I', far_blob) != (0x31465243, FAR_KEY, FAR_COUNT, 0):
        raise ValueError('far blob hash or header differs')
    if zlib.crc32(far_blob[:4]+b'\0'*4+far_blob[8:]) & 0xFFFFFFFF != FAR_KEY:
        raise ValueError('far blob key CRC differs')
    for i in range(FAR_COUNT):
        pointer = struct.unpack_from('<I', far_blob, 16+4*i)[0]
        if not pointer & 1 or not start+16+4*FAR_COUNT <= (pointer & ~1) < end:
            raise ValueError('far table pointer outside its code span')
    if arm[0x080692A8-a.BASE:0x080692AC-a.BASE] != struct.pack('<I', 0x08043379):
        raise ValueError('public release refuses a firmware-update gate')
    for address, data in ((a.STREAM0, old0), (a.STREAM1, old1)):
        off = address - a.BASE
        got = bytearray(arm[off:off + len(data)])
        if address == a.STREAM0:
            lo, hi = start-address, end-address
            if got[lo:hi] == data[lo:hi]:
                raise ValueError('far blob missing from ARM input')
            got[lo:hi] = data[lo:hi]
        if bytes(got) != data:
            raise ValueError('ARM input changed a stock DSP stream outside the far blob')
    for pointer in (a.PTR_CORE0, a.PTR_CORE1):
        off = pointer-a.BASE
        if arm[off:off+4] != stock[off:off+4]:
            raise ValueError('ARM input already changes a stream pointer')
    at = rel.CORE0_AT-a.BASE
    if len(arm) > a.FLASH_END-a.BASE or rel.CORE0_AT+len(core0) > a.FLASH_END:
        raise ValueError('image exceeds flash')
    if any(v != 255 for v in arm[at:at+len(core0)]):
        raise ValueError('ARM overlaps core-0 placement')
    image = bytearray(arm)
    image.extend(b'\xff' * max(0, at+len(core0)-len(image)))
    image[a.STREAM1-a.BASE:a.STREAM1-a.BASE+len(core1)] = core1
    image[at:at+len(core0)] = core0
    struct.pack_into('<I', image, a.PTR_CORE0-a.BASE, rel.CORE0_AT)
    struct.pack_into('<H', image, 0x1CA, LABEL)
    image.extend(b'\xff' * (-len(image)%4))
    image = bytes(image)
    allowed = ((a.STREAM1-a.BASE, a.STREAM1-a.BASE+len(core1)),
               (at, at+len(core0)), (a.PTR_CORE0-a.BASE, a.PTR_CORE0-a.BASE+4), (0x1CA, 0x1CC))
    padded_arm = arm+b'\xff'*(len(image)-len(arm))
    if any(before != after and not any(lo <= i < hi for lo,hi in allowed)
           for i,(before,after) in enumerate(zip(padded_arm,image))):
        raise ValueError('join changed bytes outside allowed DSP slots, pointer and label')
    stream = syx.build(image, stock_ref['init'][:2]+VERSION+stock_ref['init'][8:], stock_ref['header_prefix'][:1]+VERSION)
    decoded = syx.parse(stream)
    if (not decoded['crc_ok'] or decoded['crc'] != IMAGE_CRC or len(image) != IMAGE_SIZE
            or decoded['image'] != image or decoded['init'][2:8] != VERSION):
        raise ValueError('generated update framing, version or CRC differs')
    if a.sha(image) != IMAGE_SHA or a.sha(stream) != SYX_SHA:
        raise ValueError('source-built final image differs from public7339 pins')
    return image, stream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--arm-image', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.is_symlink():
        parser.error('output directory already exists')
    require_bound()
    verify_sources()
    raw = args.stock.read_bytes()
    if a.sha(raw) != a.STOCK_SYX_SHA256:
        raise ValueError('official input SysEx differs from supported pin')
    ref = syx.parse(raw)
    if not ref['crc_ok'] or ref['device'] != 0x63 or ref['header_prefix'][0] != 0:
        raise ValueError('official input CRC, device or target differs')
    arm = args.arm_image.read_bytes()
    if a.sha(arm) != ARM_SHA:
        raise ValueError('ARM input differs from exact public7339 pin')
    core0, core1, overlay, metadata = component.build_streams(ref['image'], args.assembler.resolve())
    image, update = final_firmware(arm, core0, core1, ref)
    metadata = dict(metadata, release_label=LABEL, arm_sha256=ARM_SHA, image_sha256=IMAGE_SHA, syx_sha256=SYX_SHA,
                    release_manifest_sha256=a.sha((HERE/'release-manifest.json').read_bytes()))
    files = {'core0-instrument.bin':core0, 'core1-suppression.bin':core1, 'instrument-component.bin':overlay,
             f'arrhythmia-firmware-{LABEL}.bin':image, f'arrhythmia-firmware-{LABEL}.syx':update,
             'build.json':(json.dumps(metadata,indent=2,sort_keys=True)+'\n').encode()}
    files['SHA256SUMS'] = ''.join(f'{a.sha(data)}  {name}\n' for name,data in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice207m4-release-',dir=args.out.parent) as directory:
        for name,data in files.items():
            (Path(directory)/name).write_bytes(data)
        rel.rename_noreplace(Path(directory),args.out)
    print(files['SHA256SUMS'].decode(),end='')


if __name__ == '__main__':
    main()
