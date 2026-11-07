# SPDX-License-Identifier: MIT
"""Engine version metadata stays independent of firmware admission and DSP identity."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import engine_file as e


class EngineSemverTests(unittest.TestCase):
    def test_marked_versions_and_component_bounds(self):
        for version in ((0, 0, 0), (1, 0, 0), (2, 17, 255), (255, 255, 255)):
            packed = e.encode_semver(*version)
            self.assertEqual(e.decode_semver(packed), '.'.join(map(str, version)))
        self.assertEqual(e.encode_semver(1, 0, 0), 0x80010000)
        for invalid in ((-1, 0, 0), (1, 256, 0), (1, 0, 256), (True, 0, 0), ('1', 0, 0)):
            with self.assertRaises(ValueError):
                e.encode_semver(*invalid)

    def test_unknown_legacy_and_reserved_values_remain_parseable(self):
        for build in (0, 0x207, 0x300, 0x7FFFFFFF, 0x81010000, 0xFFFFFFFF):
            self.assertIsNone(e.decode_semver(build))
            raw = e.make(b'\x00\x03\x00', 1, build, 0x207, 0xFE80B6B5)
            parsed = e.parse(raw)
            self.assertEqual(parsed['build'], build)
            self.assertIsNone(parsed['version'])
            self.assertEqual(e.reheader(raw, 0xFE80B6B5), raw)
        for invalid in (-1, 1 << 32, None, '1.0.0', True):
            self.assertIsNone(e.decode_semver(invalid))

    def test_header_only_metadata_change_and_reserved_zero(self):
        body = b'\x00\x03\x00' * 5  # DSP identity does not follow file semver.
        old = e.make(body, 1, 0x300, 0x207, 0xFE80B6B5)
        new = e.make(body, 1, e.encode_semver(1, 0, 0), 0x207, 0xFE80B6B5)
        self.assertEqual(old[:24], new[:24])
        self.assertEqual(old[28:], new[28:])
        self.assertEqual(new[28:32], bytes(4))
        h = e.parse(new)
        self.assertEqual((h['version'], h['abi'], h['base'], h['kind']), ('1.0.0', 0x207, 0xFE80B6B5, 1))
        self.assertEqual(h['body'], body)
        self.assertEqual(h['crc'], zlib.crc32(body))

    def test_host_inventory_surfaces_only_valid_engine_versions(self):
        import module
        row = bytearray(96)
        row[0], row[7] = 2, 1
        struct.pack_into('<I', row, 68, e.encode_semver(1, 0, 0))
        self.assertEqual(module.decode_card_row(row)['version'], '1.0.0')
        row[7] = 3
        self.assertIsNone(module.decode_card_row(row)['version'])
        row[7] = 1
        struct.pack_into('<I', row, 68, 0x300)
        self.assertIsNone(module.decode_card_row(row)['version'])

    def test_cre1_conversion_remains_explicit_and_no_implicit_semver(self):
        body = b'\x00\x03\x00'
        legacy = struct.pack('<8I', e.CRE1, 1, zlib.crc32(body), 0x80010000, 0xFE80B6B5, 0, 0, 0) + body
        self.assertIsNone(e.parse(legacy)['version'])
        converted = e.parse(e.convert(legacy, 2, 0x209, base=0xFE80B6B5))
        self.assertEqual((converted['kind'], converted['abi'], converted['build']), (2, 0x209, 0x80010000))
        self.assertEqual(converted['body'], body)
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / 'in.dse', Path(td) / 'out.dse'
            src.write_bytes(legacy); dst.write_bytes(b'keep existing')
            cli = [sys.executable, '-I', str(ROOT / 'tools/engine_file.py')]
            self.assertEqual(subprocess.run(cli + ['convert', str(src), str(dst)], capture_output=True).returncode, 1)
            self.assertEqual(dst.read_bytes(), b'keep existing')
            src.write_bytes(e.make(body, 1, 0x80010000, 0x207, 0xFE80B6B5)[:-1])
            self.assertEqual(subprocess.run(cli + ['reheader', str(src), str(dst)], capture_output=True).returncode, 1)
            self.assertEqual(dst.read_bytes(), b'keep existing')

    def test_device_admission_does_not_bind_build_u32(self):
        # The pure header check and diagnostic wrapper keep file build informational.
        source = (ROOT / 'patches/src/carddsp.c').read_text()
        helper = source.split('static uint32_t cd_header(', 1)[1].split('static uint32_t cd_hdr(', 1)[0]
        wrapper = source.split('static uint32_t cd_hdr(', 1)[1].split('/* 1 if the open file', 1)[0]
        self.assertIn('cd_header(b)', wrapper)
        hdr = helper + wrapper
        self.assertIn('cd_u32(b + 12) == Q0_VERSION', hdr)
        self.assertIn('cd_u32(b + 16) != base', hdr)
        self.assertIn('cd_u32(b + 20)', hdr)
        self.assertIn('i = 28; i < CD_HDR', hdr)
        self.assertNotIn('b + 24', hdr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
