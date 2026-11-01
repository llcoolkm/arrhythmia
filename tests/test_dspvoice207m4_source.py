# SPDX-License-Identifier: MIT
"""Focused offline component identity, source closure and refusal checks; no device."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from dspvoice207m4 import build as b

ARGS = None


class ComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = b.rel.syx.parse(ARGS.stock.read_bytes())
        cls.c0, cls.c1, cls.component, cls.meta = b.build_streams(cls.ref['image'], ARGS.assembler)

    def test_exact_streams_and_one_source_line(self):
        self.assertEqual(b.a.sha(self.c0), b.CORE0_SHA)
        self.assertEqual(b.a.sha(self.c1), b.CORE1_SHA)
        self.assertEqual(b.a.sha(self.component), b.COMPONENT_SHA)
        old = (b.rel.HERE / 'instrument.asm').read_text()
        self.assertEqual(old.count('move #>$392cee,x0'), 1)
        self.assertEqual((b.HERE / 'instrument.asm').read_text(),
                         old.replace('move #>$392cee,x0', 'move #>$50c336,x0'))
        self.assertEqual(self.meta['program_delta'],
                         [dict(space='P', address=0x21EB, before=0x392CEE, after=0x50C336)])
        self.assertEqual(self.meta['flat_core0_words'], 12897)
        self.assertEqual(self.meta['entry'], 0x2200)
        self.assertTrue(self.meta['label_map_identical'])
        if ARGS.bench_stream:
            self.assertEqual(self.c0, ARGS.bench_stream.read_bytes())
        if ARGS.reference_stream:
            old_stream = ARGS.reference_stream.read_bytes()
            self.assertEqual(b.a.sha(old_stream), b.rel.CORE0_SHA)
            diff = {i: (b.a.u24(old_stream, i), b.a.u24(self.c0, i))
                    for i in range(len(self.c0) // 3) if b.a.u24(old_stream, i) != b.a.u24(self.c0, i)}
            self.assertEqual(diff, {0x21EB + 2: (0x392CEE, 0x50C336)})

    def test_assembled_gain_feedback_and_old_source_refused(self):
        source = (b.HERE / 'instrument.asm').read_text()
        original = Path.read_text
        for text in (source.replace('#>$50c336,x0', '#>$50c335,x0', 1),
                     source.replace('asr #1,a,a', 'asr #2,a,a', 1),
                     (b.rel.HERE / 'instrument.asm').read_text()):
            def read(path, *args, **kwargs):
                return text if path == b.HERE / 'instrument.asm' else original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                with self.assertRaisesRegex(ValueError, 'supported component pin'):
                    b.own_instrument(ARGS.assembler)

    def test_source_closure_and_binding_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in (*b.SOURCE_FILES, 'tools/dspvoice207m4/source-manifest.json'):
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, dest)
            self.assertEqual(b.verify_sources(root)['core0_sha256'], b.CORE0_SHA)
            dep = root / 'tools/dspvoice207rel/instrument.asm'
            dep.write_text(dep.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'source manifest mismatch'):
                b.verify_sources(root)
            manifest = root / 'tools/dspvoice207m4/source-manifest.json'
            data = json.loads(manifest.read_text())
            data['gain_q23'] = 0x392CEE
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'component bindings differ'):
                b.verify_sources(root)

    def test_wrong_stock_and_assembler_refused(self):
        image = bytearray(self.ref['image'])
        image[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'pinned stock firmware'):
            b.build_streams(bytes(image), ARGS.assembler)
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / 'assembler'
            fake.write_bytes(b'not an executable')
            with self.assertRaisesRegex(ValueError, 'assembler differs'):
                b.own_instrument(fake)

    def test_cli_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / 'result'
            command = [sys.executable, '-I', '-B', str(b.HERE / 'build.py'), '--stock', str(ARGS.stock),
                       '--assembler', str(ARGS.assembler), '--out', str(out)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name for p in out.iterdir()},
                             {'core0-instrument.bin', 'core1-suppression.bin', 'instrument-component.bin',
                              'build.json', 'SHA256SUMS'})
            self.assertEqual((out / 'core0-instrument.bin').read_bytes(), self.c0)
            before = {p.name: p.read_bytes() for p in out.iterdir()}
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)
            self.assertFalse(any(p.name.startswith('.dspvoice207m4-') for p in root.iterdir()))
            for option in ('--arm-image', '--gain', '--label'):
                refused = subprocess.run(command[:-1] + [str(root / 'unsupported'), option, '1'],
                                         capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0)
                self.assertFalse((root / 'unsupported').exists())

    def test_wrong_official_input_fails_before_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wrong = root / 'wrong.syx'
            wrong.write_bytes(ARGS.stock.read_bytes()[:-1] + b'\0')
            result = subprocess.run([sys.executable, '-I', '-B', str(b.HERE / 'build.py'), '--stock', str(wrong),
                                     '--assembler', str(ARGS.assembler), '--out', str(root / 'out')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('official input SysEx differs', result.stderr)
            self.assertFalse((root / 'out').exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--reference-stream', type=Path)
    parser.add_argument('--bench-stream', type=Path)
    ARGS = parser.parse_args()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
