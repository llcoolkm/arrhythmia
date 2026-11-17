# SPDX-License-Identifier: MIT
"""Platform class policy across exported host entry points, using synthetic bytes."""
import base64
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cre3_check as ck
import module


def crc_fixture(length, wanted):
    """Make zero-filled synthetic bytes with the required CRC, not executable code."""
    data = bytearray(length)
    initial = zlib.crc32(data)
    basis = {}
    for bit in range(32):
        trial = bytearray(data)
        trial[-4 + bit // 8] = 1 << (bit % 8)
        value, mask = zlib.crc32(trial) ^ initial, 1 << bit
        while value:
            pivot = value.bit_length() - 1
            if pivot not in basis:
                basis[pivot] = value, mask
                break
            value ^= basis[pivot][0]
            mask ^= basis[pivot][1]
    value, mask = wanted ^ initial, 0
    while value:
        pivot = value.bit_length() - 1
        value ^= basis[pivot][0]
        mask ^= basis[pivot][1]
    data[-4:] = mask.to_bytes(4, 'little')
    assert zlib.crc32(data) == wanted
    return data


def fixture(kind=6):
    """Minor 0 legacy for FM class 1; minor 1 with explicit config (+27 = 0x80) for other classes."""
    policy = ck.policy()
    body = crc_fixture(1152, policy['shim_crc32'])
    body.extend(bytes.fromhex('0af08000218400000000000000000c'))
    header = bytearray(64)
    struct.pack_into('<4sHHIIII', header, 0, b'CRE3', 64, 0 if kind == 1 else 1, 389,
                     zlib.crc32(body), 1, 0x300)
    header[24] = kind
    header[27] = 0 if kind == 1 else 128
    struct.pack_into('<4I', header, 28, 0x00010000, 1, 32, 0)
    header[44:48] = bytes((4, 4, 0, 0))
    struct.pack_into('<III', header, 48, 0, policy['cycle_limit'], 0)
    struct.pack_into('<I', header, 60, zlib.crc32(header))
    return bytes(header + body)


class ClassPolicy(unittest.TestCase):
    def test_range_preserves_generic_inspection(self):
        for kind in (128, 255):
            data = fixture(kind)
            self.assertEqual(ck.parse(data)['engine_class'], kind)
            self.assertEqual(data[64:], fixture(6)[64:])
            with self.assertRaisesRegex(ValueError, 'check 16:.*experimental'):
                ck.check(data)
            with self.assertRaisesRegex(ValueError, 'check 16:.*experimental'):
                module.engine_check(data, app_build=ck.policy()['minimum_app_build'])
        for kind in (1, 6, 127):
            self.assertEqual(ck.check(fixture(kind))['engine_class'], kind)
        with self.assertRaisesRegex(ValueError, 'check 16'):
            ck.parse(fixture(0))

    def test_exported_cli_refuses_before_any_device_access(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'engine.dse'
            file.write_bytes(fixture(128))
            commands = (
                ('tools/engine_file.py', 'check3', str(file)),
                ('tools/module.py', 'card', 'engine-put', '--check', str(file)),
            )
            for args in commands:
                result = subprocess.run([sys.executable, '-I', '-B', *args], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('check 16', result.stdout + result.stderr)
            result = subprocess.run([sys.executable, '-I', '-B', 'tools/engine_file.py',
                                     'inspect3', str(file)], cwd=ROOT, capture_output=True,
                                    text=True, check=True)
            inspected = json.loads(result.stdout)
            self.assertEqual(inspected['engine_class'], 128)
            self.assertFalse(inspected['checks']['static_eligible'])

    def test_prior_cre3_firmware_does_not_become_a_cre2_target(self):
        import engine_file
        policy = ck.policy()
        old = policy['cre2_refused_from_app_build']
        legacy = engine_file.make(bytes(12), 1, 0x80010000, 0x207, 0xFE80B6B5)
        for build in (old, policy['minimum_app_build'] - 1, policy['minimum_app_build']):
            with self.subTest(build=build), self.assertRaisesRegex(ValueError, 'FM 1.0.0.*CRE2'):
                module.engine_check(legacy, app_build=build)
        for build in (old, policy['minimum_app_build'] - 1):
            with self.subTest(build=build), self.assertRaisesRegex(ValueError, 'require firmware 0.3.2'):
                module.engine_check(fixture(1), app_build=build)
        self.assertTrue(module.engine_check(fixture(1), app_build=policy['minimum_app_build'])['static_eligible'])

    @unittest.skipUnless(shutil.which('node'), 'node not found')
    def test_browser_config_grammar_matches_shared_checker(self):
        sys.path.insert(0, str(ROOT / 'tests'))
        from test_engine_ui_authoring import source
        import engine_file
        valid = [fixture(1), fixture(6), engine_file.ui3(source(6)),
                 engine_file.ui3(source(1), chords=True, poly_allowed=(1, 2, 4), poly_default=2)]
        rows = list(valid)
        for original, offset, value in ((valid[1], 27, 0), (valid[1], 27, 129),
                                        (valid[0], 27, 130), (valid[3], 64+16+5, 16),
                                        (valid[3], 64+16+6, 3)):
            bad = bytearray(original); bad[offset] = value
            bad[60:64] = bytes(4)
            struct.pack_into('<I', bad, 60, zlib.crc32(bad[:int.from_bytes(bad[4:6], 'little')]))
            rows.append(bytes(bad))
        script = """const fs=require('fs'),h=fs.readFileSync(process.argv[1],'utf8');
const part=n=>h.slice(h.indexOf('/* '+n+' BEGIN */'),h.indexOf('/* '+n+' END */'));
const {LL}=new Function(part('MLCORE')+'\\n'+part('LLCORE')+'\\nreturn {LL};')();
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>{try{LL.checkEngine(Uint8Array.from(Buffer.from(x,'base64')));return true;}catch(e){return false;}})));"""
        result = subprocess.run(['node', '-e', script, str(ROOT/'web/loader.html')],
                                input=json.dumps([base64.b64encode(b).decode() for b in rows]),
                                capture_output=True, text=True, check=True)
        wanted = []
        for row in rows:
            try: ck.check(row); wanted.append(True)
            except ValueError: wanted.append(False)
        self.assertEqual(wanted, [True]*4+[False]*5)
        self.assertEqual(json.loads(result.stdout), wanted)

    @unittest.skipUnless(shutil.which('node'), 'node not found')
    def test_browser_matches_host_policy(self):
        script = """const fs=require('fs');const h=fs.readFileSync(process.argv[1],'utf8');
const part=n=>h.slice(h.indexOf('/* '+n+' BEGIN */'),h.indexOf('/* '+n+' END */'));
const {LL}=new Function(part('MLCORE')+'\\n'+part('LLCORE')+'\\nreturn {LL};')();
const rows=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(rows.map(x=>{
try{return {kind:LL.checkEngine(Uint8Array.from(Buffer.from(x,'base64'))).kind};}
catch(e){return {error:e.message};}})));"""
        kinds = (1, 6, 127, 128, 255, 0)
        result = subprocess.run(['node', '-e', script, str(ROOT / 'web/loader.html')],
                                input=json.dumps([base64.b64encode(fixture(k)).decode()
                                                  for k in kinds]),
                                capture_output=True, text=True, check=True)
        rows = json.loads(result.stdout)
        for kind, row in zip(kinds, rows):
            if 1 <= kind <= 127:
                self.assertEqual(row, {'kind': kind})
            else:
                self.assertIn('check 16', row['error'])

    @unittest.skipUnless(shutil.which('node'), 'node not found')
    def test_browser_matches_checker_on_minor_0_rows(self):
        """Rows on which the minor-0 and minor-1 grammars agree: they must agree today."""
        legacy = fixture(1)
        rows = [legacy]
        for offset, value in ((6, 2), (7, 1), (27, 0x82), (35, 1), (43, 1), (47, 2)):
            bad = bytearray(legacy); bad[offset] = value
            bad[60:64] = bytes(4)
            struct.pack_into('<I', bad, 60, zlib.crc32(bad[:64]))
            rows.append(bytes(bad))
        script = """const fs=require('fs'),h=fs.readFileSync(process.argv[1],'utf8');
const part=n=>h.slice(h.indexOf('/* '+n+' BEGIN */'),h.indexOf('/* '+n+' END */'));
const {LL}=new Function(part('MLCORE')+'\\n'+part('LLCORE')+'\\nreturn {LL};')();
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>{try{LL.checkEngine(Uint8Array.from(Buffer.from(x,'base64')));return true;}catch(e){return false;}})));"""
        result = subprocess.run(['node', '-e', script, str(ROOT/'web/loader.html')],
                                input=json.dumps([base64.b64encode(b).decode() for b in rows]),
                                capture_output=True, text=True, check=True)
        wanted = []
        for row in rows:
            try: ck.check(row); wanted.append(True)
            except ValueError: wanted.append(False)
        self.assertEqual(wanted, [True] + [False]*6)
        self.assertEqual(json.loads(result.stdout), wanted)


if __name__ == '__main__':
    unittest.main()
