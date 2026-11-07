"""Offline integration checks. Run with python3 -I tools/rel030release/test_release.py."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'tools/rel030_release.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock_path = ROOT / 'firmware/circuit-rhythm-5706.syx'
        if not cls.stock_path.exists() or not (ROOT / 'build/circuit-rhythm-5706.bin').exists():
            raise unittest.SkipTest('official local stock setup required for offline integration checks')
        cls.raw, cls.stock = r.stock_input(cls.stock_path)
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / 'one'
        r.build_one(cls.stock_path, cls.out)
        cls.release = json.loads((cls.out / 'release/v0.3.0.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_reproducible(self):
        second = Path(self.temp.name) / 'two'
        r.build_one(self.stock_path, second)
        self.assertEqual(r.files(self.out), r.files(second))

    def test_stockcopy_and_no_stock_literals(self):
        self.assertEqual(r.crpatch.scan(self.release, self.stock), [])
        patches = self.release['builds'][0]['patches']
        self.assertTrue(all('expect' not in p and 'new' not in p for p in patches))
        relocation = next(p for p in patches if int(p['addr'], 16) == r.C0)
        self.assertEqual(relocation['pieces'], [dict(copy=f'0x{r.C0_SOURCE:08X}', len=r.C0_SIZE,
                                                    sha256=r.sha(self.stock[r.C0_SOURCE-r.BASE:r.C0_SOURCE-r.BASE+r.C0_SIZE]))])
        stream, image = r.crpatch.apply(self.release, self.raw)
        self.assertEqual(stream, (self.out / 'arrhythmia-7358.syx').read_bytes())
        self.assertEqual(image, (self.out / 'arrhythmia-7358.bin').read_bytes())

    def test_devkit_carried_unchanged(self):
        previous = json.loads((ROOT / "release/v0.2.2.json").read_text())
        self.assertEqual(self.release["builds"][1], next(b for b in previous["builds"] if b["name"] == "devkit"))

    def test_no_gate_or_join(self):
        image = (self.out / 'arrhythmia-7358.bin').read_bytes()
        r.check_image(image, self.stock)
        broken = bytearray(image)
        broken[r.INIT_VT-r.BASE] ^= 1
        with self.assertRaisesRegex(ValueError, 'INIT gate'):
            r.check_image(broken, self.stock)
        broken = bytearray(image)
        broken[r.C0-r.BASE] ^= 1
        with self.assertRaisesRegex(ValueError, 'core 0'):
            r.check_image(broken, self.stock)

    def test_invalid_input_pins(self):
        bad = Path(self.temp.name) / 'bad.syx'
        bad.write_bytes(self.raw[:-1])
        with self.assertRaisesRegex(ValueError, 'stock SysEx'):
            r.stock_input(bad)
        with self.assertRaisesRegex(ValueError, 'fm.dse'):
            r.fm_input(bad)
        damaged = json.loads(json.dumps(self.release))
        copy = next(pc for p in damaged['builds'][0]['patches'] for pc in p['pieces'] if 'copy' in pc)
        copy['sha256'] = '0' * 64
        with self.assertRaises(r.crpatch.Refused):
            r.crpatch.apply(damaged, self.raw)

    def test_no_clobber(self):
        # Public entry refuses before writing existing outputs, even with valid inputs.
        before = r.files(self.out)
        with self.assertRaises(FileExistsError):
            r.build_one(self.stock_path, self.out)
        self.assertEqual(before, r.files(self.out))


if __name__ == '__main__':
    unittest.main()
