# SPDX-License-Identifier: MIT
"""Build the offline x8/live-feedback FM component and CRE2; never join ARM or install."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from dspvoice207m4 import build as m4
import engine_file
import crbc

rel, a = m4.rel, m4.a
ORIGIN, HELPER, ENTRY = 0x2000, 0x3300, 0x2200
ENGINE_VERSION = "1.0.0"
BASE_ID, FILE_BUILD, QUEUE_ABI = 0xFE80B6B5, engine_file.encode_semver(1, 0, 0), 0x207
MAX_FILE = 15360
CORE0_SHA = '766b8583e0eeb9f91b8f0c8731502349e1f12cb73d426233929903db72460571'
BODY_SHA = '4ae18997b9fed97ac89b486d8c243ffddb7c7d7c81b9a9e2f218c120869da51a'
ENGINE_SHA = '236b20874ed999c46a8575cd3336b6ac26df9770d11f9f9dfdd6f711cd0029ad'
SOURCE_FILES = tuple(sorted({*m4.SOURCE_FILES, 'tools/dspvoice207m4/source-manifest.json',
                            'tools/engine_file.py', 'tools/crbc.py', 'patches/src/crbc.h',
                            'tests/test_dspvoice207live_source.py', 'tests/test_engine_semver.py',
                            *(f'tools/dspvoice207live/{n}' for n in
                              ('build.py', 'instrument.asm', 'live.asm', 'README.md', 'NOTICE.md'))}))


def verify_sources(root=None):
    root = ROOT if root is None else Path(root)
    data = json.loads((root / 'tools/dspvoice207live/source-manifest.json').read_text())
    expected = dict(format='arrhythmia-dsp207live-own-source/1', core0_sha256=CORE0_SHA,
                    body_sha256=BODY_SHA, engine_sha256=ENGINE_SHA, base_id=BASE_ID,
                    assembler_sha256=m4.ASSEMBLER_SHA, feedback_shift=0, queue_abi=QUEUE_ABI,
                    origin=ORIGIN, helper_origin=HELPER, file_build=FILE_BUILD, engine_version=ENGINE_VERSION,
                    produces_firmware_images=False)
    if any(data.get(k) != v for k, v in expected.items()) or set(data.get('files', {})) != set(SOURCE_FILES):
        raise ValueError('source manifest bindings or file closure differ')
    for name, digest in data['files'].items():
        if a.sha((root / name).read_bytes()) != digest:
            raise ValueError(f'source manifest mismatch: {name}')
    return data


def own_instrument(executable):
    if a.sha(Path(executable).read_bytes()) != m4.ASSEMBLER_SHA:
        raise ValueError('assembler differs from the supported pin')
    primary = (HERE / 'instrument.asm').read_text()
    # A first pass resolves external helper calls to entry solely for sizing.
    sizing = primary
    for name in ('tone', 'init', 'smooth', 'reset'):
        sizing = sizing.replace('@live_' + name + '_vector', '@entry')
    first, _, _ = rel.assemble(executable, sizing, ORIGIN)
    if ORIGIN + len(first) >= 0x2D80:
        raise ValueError('instrument overlaps the fixed startup')
    combined = primary + '\n' + 'nop\n' * (HELPER - ORIGIN - len(first)) + (HERE / 'live.asm').read_text()
    words, listing, labels = rel.assemble(executable, combined, ORIGIN)
    if labels.get('entry') != ENTRY or labels.get('instrument_end') != ORIGIN + len(first):
        raise ValueError('instrument entry or primary extent moved')
    if any(labels.get('live_' + name + '_vector') != HELPER + 2 * i
           for i, name in enumerate(('tone', 'init', 'smooth', 'reset'))):
        raise ValueError('helper vectors moved')
    at = labels['release_cosine_weights'] - ORIGIN
    if words[at:at + 32] != [0] * 32:
        raise ValueError('cosine table placeholders differ')
    words[at:at + 32] = rel.cosine32()
    # Padding was used only for label resolution; it must not overlay startup/tables.
    return words[:len(first)], words[HELPER - ORIGIN:], labels, listing


def own_body(executable):
    """Reconstruct P:2000 onward from own source and zeros, without stock input."""
    primary, helper, _, _ = own_instrument(executable)
    components, _ = rel.own_components(executable)
    startup = components['startup']
    startup_at = rel.COMPONENTS['startup'][0]
    data = rel.table_data()
    overlays = ((ORIGIN, primary), (startup_at, startup),
                (rel.DATA_AT, data), (HELPER, helper))
    end = max(origin + len(words) for origin, words in overlays)
    words = [0] * (end - ORIGIN)
    previous_end = ORIGIN
    for origin, component in overlays:
        if origin < previous_end:
            raise ValueError('own body components overlap or precede origin')
        words[origin - ORIGIN:origin - ORIGIN + len(component)] = component
        previous_end = origin + len(component)
    body = rel.packed(words)
    if a.sha(body) != BODY_SHA:
        raise ValueError('assembled output differs from supported body pin')
    return body


def build_streams(image, executable):
    verify_sources()
    old0, core1, _, _ = m4.build_streams(image, executable)
    primary, helper, labels, listing = own_instrument(executable)
    # The base contract is independently recomputed from the supplied official image.
    # crbc.parts accepts the whole flash image bytes and validates its pinned streams.
    base = crbc.base_id(crbc.preimage(crbc.parts(image)))
    if base != BASE_ID:
        raise ValueError('official image does not match the supported CRBC')
    words = [a.u24(old0, i + 2) for i in range(a.u24(old0, 0))]
    if any(words[HELPER:]):
        raise ValueError('helper region is occupied')
    words.extend([0] * max(0, HELPER + len(helper) - len(words)))
    words[ORIGIN:ORIGIN + len(primary)] = primary
    words[HELPER:HELPER + len(helper)] = helper
    core0 = a.w24(len(words)) + a.w24(0) + rel.packed(words)
    body = rel.packed(words[ORIGIN:])
    if body != own_body(executable):
        raise ValueError('mixed stream body differs from independent own-source body')
    engine = engine_file.make(body, 1, FILE_BUILD, QUEUE_ABI, BASE_ID)
    if len(engine) > MAX_FILE:
        raise ValueError('fm.dse exceeds the provisioner cap')
    if (a.sha(core0), a.sha(body), a.sha(engine)) != (CORE0_SHA, BODY_SHA, ENGINE_SHA):
        raise ValueError('assembled output differs from supported pins')
    metadata = dict(format='arrhythmia-dsp207live-component/1', component_only=True,
                    core0_sha256=a.sha(core0), core1_sha256=a.sha(core1), body_sha256=a.sha(body),
                    engine_sha256=a.sha(engine), engine_bytes=len(engine), body_words=len(body)//3,
                    primary_words=len(primary), helper_words=len(helper), helper_origin=HELPER,
                    helper_end=HELPER+len(helper), origin=ORIGIN, entry=ENTRY, queue_abi=QUEUE_ABI,
                    file_build=FILE_BUILD, engine_version=ENGINE_VERSION, base_id=BASE_ID, feedback_shift=0, gain_q23=m4.GAIN,
                    reference_core0_sha256=m4.CORE0_SHA, assembler_sha256=m4.ASSEMBLER_SHA,
                    labels=labels, maximum_file_bytes=MAX_FILE,
                    qualification='Offline source identity only; rendering and hardware validation are separate.')
    return core0, core1, body, engine, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.is_symlink():
        parser.error('output directory already exists')
    raw = args.stock.read_bytes()
    if a.sha(raw) != a.STOCK_SYX_SHA256:
        parser.error('official input SysEx differs from the supported pin')
    ref = rel.syx.parse(raw)
    if not ref['crc_ok'] or ref['device'] != 0x63 or ref['header_prefix'][0] != 0:
        parser.error('official input CRC, device or target differs')
    c0, c1, body, engine, meta = build_streams(ref['image'], args.assembler.resolve())
    files = {'core0-instrument.bin': c0, 'core0.u24be': c0[6:],
             'core1-suppression.bin': c1, 'engine-body.bin': body, 'fm.dse': engine,
             'build.json': (json.dumps(meta, indent=2, sort_keys=True)+'\n').encode()}
    files['SHA256SUMS'] = ''.join(f'{a.sha(data)}  {n}\n' for n, data in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice207live-', dir=args.out.parent) as directory:
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        rel.rename_noreplace(Path(directory), args.out)
    print(files['SHA256SUMS'].decode(), end='')


if __name__ == '__main__':
    main()
