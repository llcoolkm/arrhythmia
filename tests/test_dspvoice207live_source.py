# SPDX-License-Identifier: MIT
"""Offline FM live component identity, composition and refusal checks; no device."""
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
from dspvoice207live import build as b

ARGS = None


class SourceClosureTests(unittest.TestCase):
    def test_source_closure_and_binding_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in (*b.SOURCE_FILES, 'tools/dspvoice207live/source-manifest.json'):
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, dest)
            b.verify_sources(root)
            source = root / 'tools/dspvoice207live/live.asm'
            source.write_text(source.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'source manifest mismatch'):
                b.verify_sources(root)
            manifest = root / 'tools/dspvoice207live/source-manifest.json'
            data = json.loads(manifest.read_text()); data['queue_abi'] = 0x208
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'source manifest bindings'):
                b.verify_sources(root)


class ComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ARGS is None or ARGS.stock is None:
            raise unittest.SkipTest('supply --stock and --assembler for local reproduction')
        cls.ref = b.rel.syx.parse(ARGS.stock.read_bytes())
        cls.c0, cls.c1, cls.body, cls.engine, cls.meta = b.build_streams(cls.ref['image'], ARGS.assembler)
        cls.old0, cls.old1, _, _ = b.m4.build_streams(cls.ref['image'], ARGS.assembler)

    def test_composition_and_bounded_placement(self):
        h = b.engine_file.parse(self.engine)
        self.assertEqual((h['magic'], h['kind'], h['abi'], h['base'], h['build']),
                         ('CRE2', 1, 0x207, 0xFE80B6B5, 0x80010000))
        self.assertEqual(h['version'], '1.0.0')
        self.assertEqual(h['crc'], 0x5A7949BD)
        self.assertEqual(h['body'], self.body)
        self.assertEqual(self.body, self.c0[6+3*b.ORIGIN:])
        self.assertEqual(len(self.engine), 14906)
        self.assertLessEqual(len(self.engine), b.MAX_FILE)
        self.assertEqual(self.c1, self.old1)
        end = self.meta['labels']['instrument_end']
        self.assertEqual((self.meta['entry'], end, self.meta['helper_origin'], self.meta['helper_end']),
                         (0x2200, 0x2D75, 0x3300, 0x335E))
        # This independently preserves every original stock/integration/table word
        # outside the two declared own program intervals.
        for address in range(b.a.u24(self.old0, 0)):
            if not b.ORIGIN <= address < end:
                self.assertEqual(b.a.u24(self.c0, address+2), b.a.u24(self.old0, address+2), hex(address))
        self.assertEqual(self.c0[6+3*0x2D80:6+3*0x2D8B], self.old0[6+3*0x2D80:6+3*0x2D8B])
        self.assertEqual(b.crbc.base_id(b.crbc.preimage(b.crbc.parts(self.ref['image']))), b.BASE_ID)

    def test_independent_version_changes_only_informational_header(self):
        old = b.engine_file.make(self.body, 1, 0x300, b.QUEUE_ABI, b.BASE_ID)
        self.assertEqual(b.a.sha(old), '987279813143818290947d4da9c6686b746e4f5463467993940261ccb5dcbfbc')
        self.assertEqual(self.engine[:24], old[:24])
        self.assertEqual(self.engine[28:], old[28:])
        self.assertEqual(b.engine_file.decode_semver(b.FILE_BUILD), b.ENGINE_VERSION)
        self.assertEqual(self.meta['engine_version'], '1.0.0')
        self.assertEqual(b.engine_file.zlib.crc32(self.engine), 0x2EB146FE)

    def test_own_body_without_stock_readers(self):
        # Reconstruction receives no image; all mixed-stream and stock readers fail.
        forbidden = AssertionError('own body attempted to read stock firmware')
        with patch.object(b.a, 'pinned_streams', side_effect=forbidden), \
             patch.object(b.crbc, 'parts', side_effect=forbidden), \
             patch.object(b.rel.syx, 'parse', side_effect=forbidden), \
             patch.object(b.m4, 'build_streams', side_effect=forbidden), \
             patch.object(b.rel, 'build_streams', side_effect=forbidden):
            body = b.own_body(ARGS.assembler)
        self.assertEqual(body, self.body)
        self.assertEqual(b.a.sha(body), b.BODY_SHA)
        engine = b.engine_file.make(body, 1, b.FILE_BUILD, b.QUEUE_ABI, b.BASE_ID)
        self.assertEqual(engine, self.engine)
        self.assertEqual(b.a.sha(engine), b.ENGINE_SHA)
        self.assertEqual(b.a.STOCK_CORE0_WORDS, 0x1FED)
        self.assertLess(b.a.STOCK_CORE0_WORDS, b.ORIGIN)
        for start, end in ((0x2D75, 0x2D80), (0x2D8B, 0x2DA0), (0x3261, 0x3300)):
            self.assertEqual(body[3*(start-b.ORIGIN):3*(end-b.ORIGIN)], bytes(3*(end-start)))

    def test_altered_source_output_refused(self):
        original = Path.read_text
        for file, before, after in [('live.asm', 'asr #6,a,a', 'asr #5,a,a'),
                                    ('instrument.asm', 'asr #0,a,a', 'asr #1,a,a')]:
            path = b.HERE / file
            mutated = path.read_text().replace(before, after, 1)
            def read(p, *args, **kwargs):
                return mutated if p == path else original(p, *args, **kwargs)
            with patch.object(Path, 'read_text', read), patch.object(b, 'verify_sources'):
                with self.assertRaisesRegex(ValueError, 'assembled output differs'):
                    b.build_streams(self.ref['image'], ARGS.assembler)

    def test_wrong_assembler_and_official_input_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); fake = root / 'assembler'; fake.write_bytes(b'bad assembler')
            with self.assertRaisesRegex(ValueError, 'assembler differs'):
                b.own_instrument(fake)
            stock = root / 'wrong.syx'; stock.write_bytes(ARGS.stock.read_bytes()[:-1] + b'\0')
            result = subprocess.run([sys.executable, '-I', '-B', str(b.HERE/'build.py'),
                                     '--stock', str(stock), '--assembler', str(ARGS.assembler),
                                     '--out', str(root/'out')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('official input SysEx differs', result.stderr)
            self.assertFalse((root/'out').exists())

    def test_cli_atomic_output_and_existing_directory_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); out = root/'out'
            command = [sys.executable, '-I', '-B', str(b.HERE/'build.py'), '--stock', str(ARGS.stock),
                       '--assembler', str(ARGS.assembler), '--out', str(out)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((out/'fm.dse').read_bytes(), self.engine)
            before = {p.name:p.read_bytes() for p in out.iterdir()}
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual({p.name:p.read_bytes() for p in out.iterdir()}, before)
            self.assertFalse(any(p.name.startswith('.dspvoice207live-') for p in root.iterdir()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stock', type=Path)
    parser.add_argument('--assembler', type=Path)
    ARGS, rest = parser.parse_known_args()
    if (ARGS.stock is None) != (ARGS.assembler is None):
        parser.error('--stock and --assembler must be supplied together')
    unittest.main(argv=[sys.argv[0], *rest], verbosity=2)
