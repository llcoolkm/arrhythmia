# SPDX-License-Identifier: MIT
"""Focused public7339 source, exact-image, mutation and atomic-output checks; no device."""
import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from dspvoice207m4 import release as r

ARGS = None


class SourceTests(unittest.TestCase):
    def test_source_closure(self):
        r.verify_sources()

    def test_unbound_or_wrong_inputs_refuse(self):
        with self.assertRaisesRegex(ValueError, 'not bound' if r.ARM_SHA is None else 'exact public7339'):
            r.final_firmware(b'', b'', b'', {})

    def test_unbound_cli_refuses_before_read_or_assembly(self):
        if r.ARM_SHA is not None:
            self.skipTest('release pins are bound')
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'output'
            result = subprocess.run([sys.executable, '-I', '-B', str(r.HERE/'release.py'),
                                     '--stock', '/nonexistent/stock', '--assembler', '/nonexistent/assembler',
                                     '--arm-image', '/nonexistent/arm', '--out', str(out)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('release pins are not bound', result.stderr)
            self.assertFalse(out.exists())

    def test_atomic_no_replace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, destination = root / 'source', root / 'destination'
            source.mkdir(); destination.mkdir()
            (source / 'new').write_bytes(b'new')
            (destination / 'keep').write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                r.rel.rename_noreplace(source, destination)
            self.assertEqual((destination / 'keep').read_bytes(), b'keep')
            self.assertTrue((source / 'new').exists())
            link = root / 'link'
            link.symlink_to(root / 'missing')
            with self.assertRaises(FileExistsError):
                r.rel.rename_noreplace(source, link)
            self.assertTrue(link.is_symlink())


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ARGS.stock:
            raise unittest.SkipTest('supply --stock, --assembler and --arm-image for source rebuild checks')
        r.require_bound()
        raw = ARGS.stock.read_bytes()
        if r.a.sha(raw) != r.a.STOCK_SYX_SHA256:
            raise ValueError('test requires pinned official stock')
        cls.ref = r.syx.parse(raw)
        cls.c0, cls.c1, _, _ = r.component.build_streams(cls.ref['image'], ARGS.assembler)
        cls.arm = ARGS.arm_image.read_bytes()
        cls.image, cls.update = r.final_firmware(cls.arm, cls.c0, cls.c1, cls.ref)

    def test_exact_image_streams_version_crc_and_far(self):
        self.assertEqual(r.a.sha(self.image), r.IMAGE_SHA)
        self.assertEqual(r.a.sha(self.update), r.SYX_SHA)
        at = r.rel.CORE0_AT-r.a.BASE
        self.assertEqual(self.image[at:at+len(self.c0)], self.c0)
        at = r.a.STREAM1-r.a.BASE
        self.assertEqual(self.image[at:at+len(self.c1)], self.c1)
        lo, hi, _ = r.FAR
        self.assertEqual(self.image[lo-r.a.BASE:hi-r.a.BASE], self.arm[lo-r.a.BASE:hi-r.a.BASE])
        self.assertEqual(struct.unpack_from('<H', self.image, 0x1CA)[0], 7339)
        decoded = r.syx.parse(self.update)
        self.assertTrue(decoded['crc_ok'])
        self.assertEqual(decoded['image'], self.image)
        self.assertEqual(decoded['init'][2:8], r.VERSION)
        self.assertEqual(decoded['header_prefix'][1:], r.VERSION)

    def test_mutated_gain_feedback_core1_arm_and_gate_refused(self):
        variants = []
        for word in (0x21EB, 0x278A):
            changed = bytearray(self.c0); changed[6+word*3] ^= 1
            variants.append((self.arm, bytes(changed), self.c1))
        changed = bytearray(self.c1); changed[-1] ^= 1
        variants.append((self.arm, self.c0, bytes(changed)))
        for address in (0x080692A8, 0x080101CA, r.FAR[0]):
            changed = bytearray(self.arm); changed[address-r.a.BASE] ^= 1
            variants.append((bytes(changed), self.c0, self.c1))
        for arm, c0, c1 in variants:
            with self.assertRaisesRegex(ValueError, 'exact public7339'):
                r.final_firmware(arm, c0, c1, self.ref)

    def test_cli_rebuild_and_refusals_leave_output_intact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); out = root / 'output'
            command = [sys.executable, '-I', '-B', str(r.HERE/'release.py'), '--stock', str(ARGS.stock),
                       '--assembler', str(ARGS.assembler), '--arm-image', str(ARGS.arm_image), '--out', str(out)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual((out/'arrhythmia-firmware-7339.bin').read_bytes(), self.image)
            self.assertEqual((out/'arrhythmia-firmware-7339.syx').read_bytes(), self.update)
            before = {p.name:p.read_bytes() for p in out.iterdir()}
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual({p.name:p.read_bytes() for p in out.iterdir()}, before)
            for option in ('--label', '--gain', '--public', '--version'):
                refused = subprocess.run(command[:-1]+[str(root/'refused'), option, '1'], capture_output=True)
                self.assertNotEqual(refused.returncode, 0)
                self.assertFalse((root/'refused').exists())
            for option, original in (('--stock', ARGS.stock), ('--arm-image', ARGS.arm_image),
                                     ('--assembler', ARGS.assembler)):
                bad = root / 'bad-input'
                bad.write_bytes(original.read_bytes()+b'\0')
                args = command[:-1]+[str(root/'refused')]
                args[args.index(option)+1] = str(bad)
                self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
                self.assertFalse((root/'refused').exists())
            self.assertFalse(any(p.name.startswith('.dspvoice207m4-release-') for p in root.iterdir()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stock', type=Path)
    parser.add_argument('--assembler', type=Path)
    parser.add_argument('--arm-image', type=Path)
    ARGS = parser.parse_args()
    if any((ARGS.stock, ARGS.assembler, ARGS.arm_image)) and not all((ARGS.stock, ARGS.assembler, ARGS.arm_image)):
        parser.error('supply all three integration inputs')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
