# SPDX-License-Identifier: MIT
"""Build the fixed -4 dB VERSION207 DSP component; no ARM join or installer.

The complete new instrument source is assembled. Historical207rel supplies its
unchanged, source-built integration. Only the assembled gain immediate differs.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from dspvoice207rel import build as rel

a = rel.a
CORE0_SHA = '21ac14ffea93a6c8775153a3b1bae4ffe006d6717e5009e238d4bdb8706f167a'
CORE1_SHA = rel.CORE1_SHA
COMPONENT_SHA = '7daf8f9b371d55c0999fd20247df1e1c7dc062369457a56a36a53d5202a27be8'
ASSEMBLER_SHA = 'e1dc1fadf1b21bece7237a95d18416f215cca5289e84d08fb714edfb376ca769'
ORIGIN, WORDS, GAIN_P = 0x2000, 3428, 0x21EB
OLD_GAIN, GAIN = 0x392CEE, 0x50C336
SOURCE_FILES = tuple(sorted({*rel.SOURCE_FILES, 'tools/dspvoice207rel/source-manifest.json',
                            'tests/test_dspvoice207m4_source.py',
                            *(f'tools/dspvoice207m4/{name}' for name in
                              ('build.py', 'instrument.asm', 'README.md', 'NOTICE.md'))}))


def verify_sources(root=None):
    root = ROOT if root is None else Path(root)
    manifest = json.loads((root / 'tools/dspvoice207m4/source-manifest.json').read_text())
    if manifest.get('format') != 'arrhythmia-dsp207m4-own-source/1' or set(manifest.get('files', {})) != set(SOURCE_FILES):
        raise ValueError('source manifest has an unexpected file closure')
    expected = dict(core0_sha256=CORE0_SHA, core1_sha256=CORE1_SHA, instrument_sha256=COMPONENT_SHA,
                    reference_core0_sha256=rel.CORE0_SHA, assembler_sha256=ASSEMBLER_SHA,
                    version=0x207, origin=ORIGIN, instrument_words=WORDS, gain_p=GAIN_P, gain_q23=GAIN,
                    feedback_shift=1, produces_firmware_images=False)
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError('source manifest component bindings differ')
    for name, digest in manifest['files'].items():
        if a.sha((root / name).read_bytes()) != digest:
            raise ValueError(f'source manifest mismatch: {name}')
    return manifest


def own_instrument(executable):
    """Assemble full source, materialize unchanged cosine table, enforce component pin."""
    if a.sha(Path(executable).read_bytes()) != ASSEMBLER_SHA:
        raise ValueError('assembler differs from the supported pin')
    words, listing, labels = rel.assemble(executable, (HERE / 'instrument.asm').read_text(), ORIGIN)
    if len(words) != WORDS or labels.get('entry') != rel.ENTRY:
        raise ValueError('instrument layout or entry moved')
    at = labels['release_cosine_weights'] - ORIGIN
    if words[at:at + 32] != [0] * 32:
        raise ValueError('terminal-weight placeholders differ')
    words[at:at + 32] = rel.cosine32()
    if a.sha(rel.packed(words)) != COMPONENT_SHA:
        raise ValueError('instrument differs from the supported component pin')
    return words, labels


def build_streams(image, executable):
    manifest = verify_sources()
    if a.sha(Path(executable).read_bytes()) != ASSEMBLER_SHA:
        raise ValueError('assembler differs from the supported pin')
    # This historical builder remains unchanged and validates its own entire source closure.
    old0, core1, reference_metadata = rel.build_streams(image, executable)
    new_words, labels = own_instrument(executable)
    # Compare complete assembled label maps, not only the entry or program length.
    _, _, old_labels = rel.assemble(executable, (rel.HERE / 'instrument.asm').read_text(), ORIGIN)
    if labels != old_labels:
        raise ValueError('instrument label map differs from historical207rel')
    offset = 6 + 3 * ORIGIN
    old_component = old0[offset:offset + 3 * WORDS]
    old_words = [a.u24(old_component, i) for i in range(WORDS)]
    delta = {ORIGIN + i: (before, after) for i, (before, after) in enumerate(zip(old_words, new_words))
             if before != after}
    if delta != {GAIN_P: (OLD_GAIN, GAIN)}:
        raise ValueError('assembled delta differs from the single supported gain immediate')
    component = rel.packed(new_words)
    core0 = old0[:offset] + component + old0[offset + len(component):]
    if len(core0) != len(old0) or a.sha(core0) != CORE0_SHA or a.sha(core1) != CORE1_SHA:
        raise ValueError('output streams differ from the supported pins')
    # Full stream comparison independently excludes changes outside the assembled overlay.
    changed = {i: (a.u24(old0, i), a.u24(core0, i)) for i in range(len(core0) // 3)
               if a.u24(old0, i) != a.u24(core0, i)}
    if changed != {GAIN_P + 2: (OLD_GAIN, GAIN)}:
        raise ValueError('unexpected full-stream word mutation')
    metadata = dict(format='arrhythmia-dsp207m4-component/1', version=0x207, component_only=True,
                    core0_sha256=CORE0_SHA, core1_sha256=CORE1_SHA, instrument_sha256=COMPONENT_SHA,
                    reference_core0_sha256=a.sha(old0), reference_core1_sha256=a.sha(core1),
                    gain_p=GAIN_P, gain_q23=GAIN, previous_gain_q23=OLD_GAIN, feedback_shift=1,
                    origin=ORIGIN, instrument_words=WORDS, flat_core0_words=a.u24(core0, 0),
                    entry=labels['entry'], label_map_sha256=a.sha(json.dumps(labels, sort_keys=True).encode()),
                    label_map_identical=True, instruction_lengths_and_allocations_identical=True,
                    gain_helper_instructions_and_cycles_identical=True,
                    source_manifest_sha256=a.sha((HERE / 'source-manifest.json').read_bytes()),
                    assembler_sha256=ASSEMBLER_SHA,
                    program_delta=[dict(space='P', address=GAIN_P, before=OLD_GAIN, after=GAIN)],
                    reference_own_components=reference_metadata['own_components'],
                    qualification='Source identity only; offline and hardware qualification remain separate.')
    return core0, core1, component, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    core0, core1, component, metadata = build_streams(ref['image'], args.assembler.resolve())
    files = {'core0-instrument.bin': core0, 'core1-suppression.bin': core1,
             'instrument-component.bin': component,
             'build.json': (json.dumps(metadata, indent=2, sort_keys=True) + '\n').encode()}
    files['SHA256SUMS'] = ''.join(f'{a.sha(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dspvoice207m4-', dir=args.out.parent) as directory:
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        rel.rename_noreplace(Path(directory), args.out)
    print(files['SHA256SUMS'].decode(), end='')


if __name__ == '__main__':
    main()
