"""Synthetic transport/patch boundary checks; no proprietary firmware required."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import patch
import syx

INIT = bytes([1, 0x63, 0, 0, 0, 7, 3, 1])
HEADER = bytes([0]) + INIT[2:]
IMAGE = bytearray(b'\xff' * 513)
IMAGE[0x1CC:0x1CE] = INIT[:2]
IMAGE = bytes(IMAGE)
PATCH_IMAGE = IMAGE[:-1]  # Application CRC feeder requires a whole number of words.


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.stream = syx.build(IMAGE, INIT, HEADER)
        self.msgs = syx.split(self.stream)

    def rejected(self, messages):
        with self.assertRaises(ValueError):
            syx.parse(b''.join(messages))

    def test_roundtrip_and_crc_vector(self):
        self.assertEqual(syx.crc32_mpeg2(b'123456789'), 0x0376E6E7)
        for size in (1, 31, 32, 33, 512, 513):
            image = bytes(i % 256 for i in range(size))
            result = syx.parse(syx.build(image, INIT, HEADER))
            self.assertEqual(result['image'], image)
            self.assertTrue(result['crc_ok'])

    def test_order_duplicates_and_extra_blocks(self):
        m = self.msgs
        for invalid in ([m[1], m[0], *m[2:]], [m[0], *m], [*m, m[-1]],
                        [*m[:-1], m[2], m[-1]], [*m[:2], *m[3:]], m[:-1], []):
            with self.subTest(shape=[x[5] for x in invalid]):
                self.rejected(invalid)

    def test_malformed_fields_and_padding(self):
        # Message index, byte index, replacement. Sizes/CRC are hex nibbles;
        # versions are decimal digits and must agree in INIT and HEADER.
        for message, index, value in ((0, 6, 2), (0, 7, 0x65), (0, 13, 2),
                                      (1, 7, 10), (1, 13, 16), (1, 21, 16),
                                      (2, 6, 128), (2, -2, 1)):
            with self.subTest(message=message, index=index):
                msgs = self.msgs.copy()
                b = bytearray(msgs[message]); b[index] = value; msgs[message] = bytes(b)
                self.rejected(msgs)
        self.rejected([self.msgs[0][:-2] + b'\xf7', *self.msgs[1:]])
        self.rejected([*self.msgs[:-1], self.msgs[-1][:-2] + b'\xf7'])
        msgs = self.msgs.copy()
        # The last WRITE holds image byte 512 followed by FF fill.
        msgs[-2] = syx.PREFIX + bytes([syx.WRITE]) + syx.pack7(b'\xff' + b'\0' * 31) + b'\xf7'
        self.rejected(msgs)
        self.rejected([b'\xf0\x00\x20\x29\x00\xf7'])
        self.rejected([self.stream[:-1]])

    def test_build_rejects_ambiguous_metadata(self):
        for image, init, header in ((b'', INIT, HEADER), (IMAGE, INIT + b'\0', HEADER),
                                     (IMAGE, INIT, HEADER[:-1]),
                                     (IMAGE, INIT[:-1] + b'\x0a', HEADER),
                                     (IMAGE, INIT, bytes([4]) + HEADER[1:])):
            with self.assertRaises(ValueError):
                syx.build(image, init, header)


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'base.syx'
        self.source.write_bytes(syx.build(PATCH_IMAGE, INIT, HEADER))
        self.spec = {'base_syx': str(self.source), 'base_sha256': hashlib.sha256(PATCH_IMAGE).hexdigest(),
                     'version_nibbles': [0, 0, 0, 7, 3, 2], 'patches': []}
        self.output = self.root / 'out'

    def apply(self):
        path = self.root / 'patch.json'
        path.write_text(json.dumps(self.spec))
        with contextlib.redirect_stdout(io.StringIO()):
            patch.apply(str(path), str(self.output))

    def rejected(self):
        with self.assertRaises((SystemExit, ValueError)):
            self.apply()
        self.assertFalse(self.output.with_suffix('.bin').exists())
        self.assertFalse(self.output.with_suffix('.syx').exists())
        self.assertFalse(self.output.with_suffix('.manifest.json').exists())

    def test_byte_replacement_and_explicit_ff_extension(self):
        self.spec['patches'] = [{'addr': '0x080101ca', 'expect': 'ffff', 'new': '0f27'},
                                {'addr': '0x08010204', 'expect': 'ffff', 'new': '1234'}]
        self.apply()
        image = self.output.with_suffix('.bin').read_bytes()
        self.assertEqual(image[0x1ca:0x1cc], b'\x0f\x27')
        self.assertEqual(image[512:], b'\xff\xff\xff\xff\x12\x34\xff\xff')
        p = syx.parse(self.output.with_suffix('.syx').read_bytes())
        self.assertTrue(p['crc_ok']); self.assertEqual(p['image'], image)
        manifest = json.loads(self.output.with_suffix('.manifest.json').read_text())
        for kind, binary, stream in [('source', PATCH_IMAGE, self.source.read_bytes()),
                                     ('output', image, self.output.with_suffix('.syx').read_bytes())]:
            self.assertEqual(manifest[kind]['image_sha256'], hashlib.sha256(binary).hexdigest())
            self.assertEqual(manifest[kind]['syx_sha256'], hashlib.sha256(stream).hexdigest())
            self.assertEqual(manifest[kind]['image_size'], len(binary))
            self.assertEqual(manifest[kind]['syx_size'], len(stream))
            self.assertEqual(manifest[kind]['crc32_mpeg2'], f'{syx.crc32_mpeg2(binary):08x}')
        self.assertEqual(manifest['patch_spec_sha256'], hashlib.sha256((self.root / 'patch.json').read_bytes()).hexdigest())
        self.assertEqual(manifest['target'], {'header_type': 0, 'device_id': 0x63,
                                            'device': 'circuit-rhythm', 'image_base': patch.BASE})
        self.assertEqual(manifest['source']['version_digits'], list(INIT[2:]))
        self.assertEqual(manifest['output']['version_digits'], self.spec['version_nibbles'])
        self.assertEqual(manifest['changed_address_ranges'], [
            {'start': 0x080101CA, 'end_exclusive': 0x080101CC},
            {'start': 0x08010200, 'end_exclusive': 0x08010208}])
        self.assertEqual(manifest['changed_bytes'], 10)


    def test_patch_guards(self):
        cases = [
            [{'addr': '0x080101ca', 'expect': '00', 'new': '12'}],
            [{'addr': '0x08010204', 'expect': '00', 'new': '12'}],
            [{'addr': '0x080101ff', 'expect': 'ffff', 'new': '1234'}],
            [{'addr': '0x08010000', 'expect': 'ff', 'new': '12'}],
            [{'addr': '0x08080000', 'expect': 'ff', 'new': '12'}],
            [{'addr': '0x0800ffff', 'expect': 'ff', 'new': '12'}],
            [{'addr': '0x080101ca', 'expect': '', 'new': ''}],
            [{'addr': '0x080101ca', 'expect': 'ff', 'new': '1234'}],
            [{'addr': '0x08010204', 'expect': 'ffff', 'new': '1234'},
             {'addr': '0x08010205', 'expect': 'ffff', 'new': '5678'}],
            [{'addr': '0x080101ca', 'expect': 'ff', 'new': '12'},
             {'addr': '0x080101ca', 'expect': '12', 'new': '34'}],
        ]
        for patches in cases:
            with self.subTest(patches=patches):
                self.spec['patches'] = patches; self.rejected()

    def test_source_crc_type_device_and_hash(self):
        bad_crc = bytearray(syx.build(PATCH_IMAGE, INIT, HEADER))
        # Alter CRC only; image and its pinned SHA are unchanged.
        bad_crc[len(syx.split(bytes(bad_crc))[0]) + 21] ^= 1
        for stream in (bytes(bad_crc), syx.build(PATCH_IMAGE, INIT, bytes([1]) + HEADER[1:]),
                       syx.build(PATCH_IMAGE, bytes([1, 0x64]) + INIT[2:], HEADER)):
            with self.subTest(stream=hashlib.sha256(stream).hexdigest()):
                self.source.write_bytes(stream); self.rejected()
        self.source.write_bytes(syx.build(PATCH_IMAGE, INIT, HEADER))
        self.spec['base_sha256'] = '0' * 64; self.rejected()

    def test_decimal_version_and_base_size(self):
        for version in ([0, 0, 0, 7, 3, 10], [True, 0, 0, 7, 3, 2], [0] * 5):
            self.spec['version_nibbles'] = version; self.rejected()
        self.spec['version_nibbles'] = [0] * 6
        for image in (b'\xff' * 32, PATCH_IMAGE + b'\xff', PATCH_IMAGE + b'\xff' * 0x70000):
            self.source.write_bytes(syx.build(image, INIT, HEADER))
            self.spec['base_sha256'] = hashlib.sha256(image).hexdigest(); self.rejected()

    def test_partial_word_extensions_have_explicit_ff_padding(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                self.spec['patches'] = [{'addr': '0x08010200', 'expect': 'ff' * count,
                                         'new': bytes(range(1, count + 1)).hex()}]
                self.apply()
                image = self.output.with_suffix('.bin').read_bytes()
                expected = PATCH_IMAGE + bytes(range(1, count + 1)) + b'\xff' * (4 - count)
                self.assertEqual(image, expected)
                result = syx.parse(self.output.with_suffix('.syx').read_bytes())
                self.assertEqual(result['size'], 516)
                self.assertEqual(result['image'], expected)
                self.assertEqual(result['crc'], syx.crc32_mpeg2(expected))
                # Without explicit FF bytes, the device's zero-filled final word
                # would disagree with the host CRC of the truncated image.
                truncated = expected[:512 + count]
                self.assertNotEqual(syx.crc32_mpeg2(truncated),
                                    syx.crc32_mpeg2(truncated + b'\0' * (4 - count)))
                manifest = json.loads(self.output.with_suffix('.manifest.json').read_text())
                self.assertEqual(manifest['changed_bytes'], 4)
                self.assertEqual(manifest['changed_address_ranges'], [
                    {'start': 0x08010200, 'end_exclusive': 0x08010204}])
                self.assertEqual(manifest['output']['image_size'], 516)
                self.assertEqual(manifest['output']['image_sha256'], hashlib.sha256(expected).hexdigest())

    def test_pack_rejects_corrupt_reference_before_output(self):
        bad = bytearray(self.source.read_bytes()); bad[len(syx.split(bytes(bad))[0]) + 21] ^= 1
        self.source.write_bytes(bad)
        binary = self.root / 'image.bin'; binary.write_bytes(PATCH_IMAGE)
        output = self.root / 'packed.syx'
        result = subprocess.run([sys.executable, '-I', str(TOOLS / 'syx.py'), 'pack',
                                 str(binary), str(self.source), str(output)], capture_output=True)
        self.assertNotEqual(result.returncode, 0); self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
