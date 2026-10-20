"""Public source checks; optional local inputs exercise release reproduction."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

OPTIONS = None
ROOT = Path(__file__).resolve().parents[1]


class PublicSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='dspvoice-source-test-')
        cls.clean = Path(cls.temporary.name)
        shutil.copytree(ROOT / 'tools' / 'dspvoice', cls.clean / 'tools' / 'dspvoice',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / 'tools' / 'syx.py', cls.clean / 'tools' / 'syx.py')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def isolated(self, code, *args):
        prefix = ('import sys\nfrom pathlib import Path\n'
                  'base=Path(sys.argv[1])\n'
                  'sys.path.insert(0,str(base/"tools"))\n'
                  'sys.path.insert(0,str(base/"tools"/"dspvoice"))\n')
        result = subprocess.run([sys.executable, '-I', '-c', prefix + code,
                                 str(self.clean), *map(str, args)],
                                cwd=self.clean, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_named_source_manifest(self):
        manifest = json.loads((ROOT / 'tools/dspvoice/source-manifest.json').read_text())
        self.assertEqual(manifest['firmware_label'], 7261)
        for name, digest in manifest['files'].items():
            path = Path(name)
            self.assertFalse(path.is_absolute())
            self.assertNotIn('..', path.parts)
            self.assertTrue(name.startswith('tools/dspvoice/') or name == 'tests/test_dspvoice_source.py')
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest, name)

    def test_generated_tables(self):
        self.isolated('''
import hashlib
import parameters,tables
x=parameters.initial_words(); y=tables.saw_words()
assert len(x)==258 and len(y)==1024
assert set(x)==set(range(0x2b00,0x2c00))|{0x2ca0,0x2ca1}
assert set(y)==set(range(0x1500,0x1900))
assert (x[0x2ca0],x[0x2ca1])==(0x2aaaab,0x7fb4b6)
assert all(type(w) is int and 0<=w<=0xffffff for w in [*x.values(),*y.values()])
def digest(words):
 return hashlib.sha256(b''.join(v.to_bytes(3,'little') for _,v in sorted(words.items()))).hexdigest()
assert digest(x)=='840e925120708570c9e3a28828d00bcbcfaa330c7e321d2f04ce7d913a77c1d6'
assert digest(y)=='e1a279892c4e799945fbbf3fbf68b1a743a9134a3a332aad23f75421543c5639'
assert parameters.describe()['parameter_tables_u24le_sha256']=='f4487839d99d24e4a6fa1db3326906577f869f1eed86cbcc5fa42520c298d7e5'
''')

    def test_import_closure_and_native_sources(self):
        tools = self.clean / 'tools'
        pending = list((tools / 'dspvoice').glob('*.py'))
        seen = set()
        while pending:
            source = pending.pop()
            if source in seen:
                continue
            seen.add(source)
            tree = ast.parse(source.read_text(), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or '').split('.')[0]]
                else:
                    continue
                for name in names:
                    if name in sys.stdlib_module_names or not name:
                        continue
                    matches = [p for p in (source.parent / (name+'.py'), tools / (name+'.py')) if p.is_file()]
                    self.assertTrue(matches, f'Import outside public closure: {source.name}: {name}')
                    pending.extend(matches)
        symbols = json.loads((tools / 'dspvoice' / 'symbols.json').read_text())
        for name in symbols['components']:
            source = (tools / 'dspvoice' / (name+'.asm')).read_text()
            self.assertIn('@', source)
            self.assertNotRegex(source.lower(), r'(?m)^\s*(dc|dcb|incbin)\b')
            self.assertNotIn('/home/', source)

    def test_scatter_rejects_corrupt_input(self):
        self.isolated('''
import adapter as a
cases=[b'',b'\\x00',a.w24(3),a.w24(0)+a.w24(1),
 a.w24(0)+a.w24(2)+a.w24(0)+a.w24(1),
 a.w24(0)+a.w24(1)+a.w24(0x1000)+a.w24(1)+a.w24(0x100),
 a.w24(1)+a.w24(1)+a.w24(0x40000)+a.w24(1)+a.w24(0x100)]
for payload in cases:
 try: a.parse_payload(payload)
 except ValueError: pass
 else: raise AssertionError('accepted malformed scatter')
records=[('X',4,(1,2)),('X',5,(3,))]
try: a.memory_map(records)
except ValueError: pass
else: raise AssertionError('accepted overlapping writes')
records=[('P',3,(0x123456,)),('X',3,(0xabcdef,))]
payload=a.encode_records(records)+a.w24(0x100)+a.w24(0x55)
parsed,_,suffix=a.parse_payload(payload)
assert parsed==records and suffix==a.w24(0x100)+a.w24(0x55)
''')

    def test_wrong_assembler_refused(self):
        fake = self.clean / 'bad-assembler'
        fake.write_text('#!/bin/sh\nprintf "malformed\\n"\n')
        fake.chmod(0o755)
        self.isolated('''
import build
try: build.own_code(sys.argv[2])
except ValueError: pass
else: raise AssertionError('accepted malformed assembler output')
''', fake)

    def test_assembly_and_changed_source_refused(self):
        if not OPTIONS or not OPTIONS.assembler:
            self.skipTest("requires --assembler")
        self.isolated('''
import build
p,x,y,s=build.own_code(sys.argv[2])
assert (len(x),len(y))==(327,1024)
assert len(p)==697
source=base/'tools'/'dspvoice'/'voice.asm'
original=source.read_text()
try:
 source.write_text(original.replace('clr a','clr b',1))
 assert source.read_text()!=original
 try: build.own_code(sys.argv[2])
 except ValueError: pass
 else: raise AssertionError('accepted changed native source')
finally: source.write_text(original)
''', OPTIONS.assembler)

    def test_clean_build_and_stream_invariants(self):
        if not OPTIONS or not OPTIONS.assembler or not OPTIONS.stock:
            self.skipTest("requires --stock and --assembler")
        self.isolated('''
import build,adapter as a,syx
ref=syx.parse(Path(sys.argv[2]).read_bytes())
image=ref['image']
c0,c1,m=build.build_streams(image,sys.argv[3])
assert a.sha(c0)=='4a8618bb189d5ea05374431d62f39331604221ea5f17219d1d6267d25495b814'
assert a.sha(c1)=='b792d02b12c5526cc538a805244a6456439d65efe6c90c397f3ee3867e5a9b44'
s0,s1=a.pinned_streams(image)
assert c0==s0
before,_,suffix=a.parse_payload(s1[6:]); after,_,new_suffix=a.parse_payload(c1[6:])
assert suffix==new_suffix
old=a.memory_map(before); new=a.memory_map(after)
changed={key for key in old if old[key]!=new[key]}
assert changed=={('P',at) for at in (0x11e,0x2da,0x2fd,0x4e6,0x4f4,0x509)}
assert len(after)==29 and len(c1)==6+16256*3
import subprocess
out=base/'output'
command=[sys.executable,'-I',str(base/'tools'/'dspvoice'/'build.py'),
 '--stock',sys.argv[2],'--assembler',sys.argv[3],'--out',str(out)]
if len(sys.argv)>4: command+=['--arm-image',sys.argv[4]]
result=subprocess.run(command,capture_output=True,text=True)
assert result.returncode==0,result.stdout+result.stderr
assert (out/'core1-voice.bin').read_bytes()==c1
assert (out/'core0-stock-relocated.bin').read_bytes()==c0
assert (out/'SHA256SUMS').is_file()
# The CLI must refuse a corrupt input without creating partial output.
broken=base/'broken.syx'; broken.write_bytes(b'invalid')
failed=base/'failed-output'
command=[sys.executable,'-I',str(base/'tools'/'dspvoice'/'build.py'),
 '--stock',str(broken),'--assembler',sys.argv[3],'--out',str(failed)]
assert subprocess.run(command,capture_output=True).returncode!=0
assert not failed.exists()
bad=bytearray(image); bad[0]^=1
try: build.build_streams(bytes(bad),sys.argv[3])
except ValueError: pass
else: raise AssertionError('accepted modified stock')
if len(sys.argv)>4:
 arm=Path(sys.argv[4]).read_bytes()
 final,stream=build.final_firmware(arm,c0,c1,m,ref)
 assert a.sha(final)=='fef8e4072696e177104aa2d31d7fcc725ba4dc746754e18d1f8a97a33deb5aaf'
 assert a.sha(stream)=='920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b'
 for address in (0,a.PTR_CORE0-a.BASE,a.STREAM1-a.BASE):
  altered=bytearray(arm); altered[address]^=1
  try: build.final_firmware(bytes(altered),c0,c1,m,ref)
  except ValueError: pass
  else: raise AssertionError('accepted modified ARM input')
''', OPTIONS.stock, OPTIONS.assembler,
          *([OPTIONS.arm_image] if OPTIONS.arm_image else []))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    for option in ('stock', 'assembler', 'arm-image'):
        parser.add_argument('--'+option, type=lambda value: Path(value).resolve())
    OPTIONS, remainder = parser.parse_known_args()
    unittest.main(argv=[sys.argv[0], *remainder])
