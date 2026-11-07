"""Own engine asset export checks using synthetic program bytes, no stock input or device."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import engine_file as ef
import export_public as ep


class EngineExport(unittest.TestCase):
    def test_public_rebuild_source_closure(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'tools/rel030release/source-manifest.json').read_text())
        self.assertIn('patches/src/enginelist.c', manifest['files'])
        self.assertTrue(set(manifest['files']) <= set(ep.ALLOW))
        self.assertIn('docs/dev/loader-scan.md', ep.ALLOW)

    def fixture(self, *, abi=0x207, build=0x80010000):
        data = ef.make(bytes(12), 1, build, abi, 0xFE80B6B5)
        row = dict(name='fm.dse', size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   crc32=f'{zlib.crc32(data):08x}', version='1.0.0')
        return data, row

    def page(self, rows):
        return '/* published-engines: ' + json.dumps(rows) + ' */'

    def test_exact_named_asset(self):
        data, row = self.fixture()
        self.assertEqual(ep.page_engine(self.page([row]), data), row)

    def test_file_identity(self):
        data, row = self.fixture()
        for field, wrong in [('name', 'FM.DSE'), ('size', True), ('size', len(data)+1),
                             ('sha256', '0'*64), ('crc32', '00000000'), ('version', '2.0.0')]:
            with self.subTest(field=field, wrong=wrong), self.assertRaises(ep.Refused):
                ep.page_engine(self.page([dict(row, **{field: wrong})]), data)

    def test_incompatible_or_unversioned(self):
        for args in (dict(abi=0x208), dict(build=0x300), dict(build=0x81010000)):
            data, row = self.fixture(**args)
            with self.subTest(args=args), self.assertRaises(ep.Refused):
                ep.page_engine(self.page([row]), data)

    def test_body_integrity(self):
        data, row = self.fixture()
        data = data[:-1] + b'\x01'
        row.update(sha256=hashlib.sha256(data).hexdigest(), crc32=f'{zlib.crc32(data):08x}')
        with self.assertRaises(ep.Refused):
            ep.page_engine(self.page([row]), data)

    def test_descriptor_shape(self):
        data, row = self.fixture()
        for page in ('', '/* published-engines: broken */', self.page([]), self.page([row, row])):
            with self.subTest(page=page), self.assertRaises(ep.Refused):
                ep.page_engine(page, data)

    def test_coincidence_exception_is_not_general(self):
        data = bytes(14906)
        self.assertFalse(ep.reviewed_engine_match(ep.WEB_ENGINE, data, 0x287f, 39))
        self.assertFalse(ep.reviewed_engine_match('other.dse', data, 0x287f, 39))


if __name__ == '__main__':
    unittest.main()
