# SPDX-License-Identifier: MIT
"""Own-source checks; optional local inputs reproduce the VERSION206 streams."""
import argparse
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = None


class Source206Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='dspvoice206-source-test-')
        cls.clean = Path(cls.temp.name)
        for name in ('dspvoice206', 'dspvoice'):
            shutil.copytree(ROOT/'tools'/name, cls.clean/'tools'/name,
                            ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT/'tools/syx.py', cls.clean/'tools/syx.py')
        (cls.clean/'tests').mkdir()
        shutil.copy2(Path(__file__), cls.clean/'tests'/Path(__file__).name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def isolated(self, code, *args):
        prefix = 'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(sys.argv[1])/"tools"))\n'
        out = subprocess.run([sys.executable, '-I', '-c', prefix+code, str(self.clean), *map(str,args)],
                             cwd=self.clean, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout+out.stderr)
        return out.stdout

    def frontend(self, name, body):
        p = self.clean/name
        p.write_text('#!'+sys.executable+'\nimport sys\n'+body)
        p.chmod(0o755)
        return p

    def test_resident_tables_exact_and_layout(self):
        self.isolated('''
import hashlib
from dspvoice206 import tables as t
w=t.table_data()
assert len(w)==1217 and all(type(x)is int and 0<=x<=0xffffff for x in w)
assert hashlib.sha256(b''.join(x.to_bytes(3,'big') for x in w)).hexdigest()==\
'b3e5dc3efea197b0db27a7bb09b78a892e539bb771d9595b7ba7fabf08bea03c'
a,d,s,r=[w[i:i+128] for i in range(0,512,128)]
assert a[0]==0x2aaaab and d[64]==0x7fb4b6 and s[0]==0 and s[-1]==0x7fffff
assert all(x>=y for x,y in zip(a,a[1:]))
assert all(x<=y for row in (d,s,r) for x,y in zip(row,row[1:]))
assert w[512]==0 and w[768]==0x7fffff
assert w[769+126]==w[769+127]  # bypass index's unused cutoff coefficient
assert w[1025]==0 and w[1152]==0x7fffff
assert len(t.BANKS)==16 and w[1153:]==[n for _,bank in t.BANKS for n in bank]
''')

    def test_arithmetic_drift_refused(self):
        self.isolated('''
from dspvoice206 import tables as t
original=t.math.sin
t.math.sin=lambda x:original(x)+0.0001
try:t.table_data()
except ValueError as e:assert 'drift' in str(e)
else:raise AssertionError('silently accepted changed sine arithmetic')
''')

    def test_imports_stay_in_public_closure(self):
        tools=self.clean/'tools'
        allowed={'dspvoice206','dspvoice','syx'}|sys.stdlib_module_names
        for source in [*(tools/'dspvoice206').glob('*.py'),
                       self.clean/'tests'/Path(__file__).name]:
            for node in ast.walk(ast.parse(source.read_text())):
                names=([x.name.split('.')[0] for x in node.names] if isinstance(node,ast.Import) else
                       [(node.module or '').split('.')[0]] if isinstance(node,ast.ImportFrom) else [])
                for name in names:
                    self.assertIn(name,allowed,f'{source.name}: non-public import {name}')
        for source in (tools/'dspvoice206').glob('*.asm'):
            self.assertNotRegex(source.read_text().lower(),r'(?m)^\s*(incbin|dc|dcb)\b')

    def test_source_manifest_strict_closure_and_comment_tamper(self):
        self.isolated("""
import json
from dspvoice206 import build as b
root=Path(sys.argv[1]);manifest=root/'tools/dspvoice206/source-manifest.json'
original=manifest.read_bytes();doc=json.loads(original)
assert b.verify_sources(root)==doc
assert set(doc['files'])==set(b.SOURCE_FILES)
for action in ('missing','extra','traversal','wrong-pin'):
 changed=json.loads(original)
 if action=='missing':del changed['files'][next(iter(changed['files']))]
 elif action=='extra':changed['files']['tools/dspvoice206/unlisted.asm']='0'*64
 elif action=='traversal':changed['files']['../outside']='0'*64
 else:changed['core0_sha256']='0'*64
 manifest.write_text(json.dumps(changed))
 try:b.verify_sources(root)
 except ValueError:pass
 else:raise AssertionError(('bad manifest accepted',action))
 manifest.write_bytes(original)
p=root/'tools/dspvoice206/startup.asm';saved=p.read_bytes()
try:
 p.write_bytes(saved+b'\\n; comment-only tamper must also be detected\\n')
 try:b.verify_sources(root)
 except ValueError as e:assert 'mismatch' in str(e)
 else:raise AssertionError('source comment tamper not detected')
finally:p.write_bytes(saved)
assert b.verify_sources(root)==doc
""")

    def test_label_and_address_validation(self):
        fake=self.frontend('one-word', '''for row in sys.stdin:
 a,s=row.rstrip('\\n').split('\\t',1)
 print(a+'\\t000000\\tignored\\t'+s)
''')
        self.isolated('''
from dspvoice206.assembler import assemble
p=sys.argv[2]
for origin in (-1,0x1000000,True,1.5):
 try:assemble(p,'nop',origin)
 except ValueError:pass
 else:raise AssertionError(('invalid origin accepted',origin))
for source in ('x:\\nnop\\nx:\\nrts','jmp @absent','bad label:\\nnop'):
 try:assemble(p,source,0x2200)
 except ValueError:pass
 else:raise AssertionError(('invalid labels accepted',source))
try:assemble(p,'nop\\nnop',0xffffff)
except ValueError:pass
else:raise AssertionError('program beyond24-bit range accepted')
w,listing,labels=assemble(p,'start:\\njmp @end\\njsr @start\\nend:\\nnop',0x2200)
assert labels=={'start':0x2200,'end':0x2204}
assert w==[0x0af080,0x2204,0x0bf080,0x2200,0]
''',fake)

    def test_frontend_malformed_and_length_change_refused(self):
        malformed=self.frontend('malformed', "print('malformed')\n")
        out_of_range=self.frontend('out-of-range', '''for row in sys.stdin:
 a,s=row.rstrip('\\n').split('\\t',1)
 print(a+'\\t1000000\\tignored\\t'+s)
''')
        changing=self.frontend('changing', '''for row in sys.stdin:
 a,s=row.rstrip('\\n').split('\\t',1)
 words='000000' if a=='0' else '000000 000000'
 print(a+'\\t'+words+'\\tignored\\t'+s)
''')
        self.isolated('''
from dspvoice206.assembler import assemble
for p in sys.argv[2:]:
 try:assemble(p,'nop',0x2200)
 except ValueError:pass
 else:raise AssertionError('malformed or length-changing frontend accepted')
''',malformed,out_of_range,changing)


    def test_local_streams_and_mutation_boundary(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for local reproduction')
        self.isolated("""
import hashlib
import syx
from dspvoice import adapter as a
from dspvoice206 import build as b
raw=Path(sys.argv[2]).read_bytes();assert a.sha(raw)==a.STOCK_SYX_SHA256
ref=syx.parse(raw);image=ref['image'];exe=Path(sys.argv[3])
components,labels=b.own_components(exe)
for name,(origin,count,digest) in b.COMPONENTS.items():
 assert len(components[name])==count and a.sha(b.packed(components[name]))==digest
c0,c1,meta=b.build_streams(image,exe)
assert a.sha(c0)=='cbbeb9b1185fb52cd1ebee536d04d3589f716c94b55589153bd36f57bf7bf8c8'
assert a.sha(c1)=='b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948'
s0,s1=a.pinned_streams(image)
old=[a.u24(s0[6:],i) for i in range(a.STOCK_CORE0_WORDS)]
new=[a.u24(c0[6:],i) for i in range(a.u24(c0,0))]
assert len(new)==12897
assert {i for i,w in enumerate(old) if w!=new[i]}=={0x100,0x101,0x225}  # P224 absolute JSR opcode already matches
x,_,tail=a.parse_payload(s1[6:]);y,_,newtail=a.parse_payload(c1[6:])
assert len(x)==20 and len(y)==23 and tail==newtail
before,after=a.memory_map(x),a.memory_map(y)
assert {k for k in before if before[k]!=after[k]}=={('P',n) for n in (0x2da,0x2fd,0x4f4,0x509)}
added=set(after)-set(before)
assert added=={('P',n) for n in [0x4a,0xff8,0xff9,*range(0xd32,0xd57)]}
assert all(after[k]==v for k,v in before.items() if k[0] in ('X','Y'))
# The public adapter rejects even a modification outside DSP regions.
bad=bytearray(image);bad[0]^=1
try:b.build_streams(bytes(bad),exe)
except ValueError:pass
else:raise AssertionError('modified stock accepted')
# Overlap validation must run before applying any overlays.
original_own=b.own_components;original_components=b.COMPONENTS
try:
 b.own_components=lambda _: (components,labels)
 b.COMPONENTS=dict(b.COMPONENTS);v=b.COMPONENTS['startup'];b.COMPONENTS['startup']=(0x2200,v[1],v[2])
 try:b.build_streams(image,exe)
 except ValueError as e:assert 'overlap' in str(e)
 else:raise AssertionError('overlapping own components accepted')
finally:b.own_components=original_own;b.COMPONENTS=original_components
# Duplicate scatter records are forbidden rather than last-write-wins.
try:a.memory_map([('P',4,(1,)),('P',4,(1,))])
except ValueError:pass
else:raise AssertionError('duplicate scatter write accepted')
# Valid assembly with different executed instructions cannot pass component pins.
p=b.HERE/'startup.asm';saved=p.read_text()
try:
 p.write_text('nop\\n'+saved)
 try:b.own_components(exe)
 except ValueError:pass
 else:raise AssertionError('modified source accepted')
finally:p.write_text(saved)
if sys.argv[4]!='-':
 arm=Path(sys.argv[4]).read_bytes();final,stream=b.final_firmware(arm,c0,c1,ref)
 assert a.sha(final)=='ab8b07a2930e57620902b0fa45f3f0d7870475ca99b01c1c295f5a0610b1fe78'
 assert a.sha(stream)=='d72fd952b3375df8b45582576f2ce67619fe16437d8d75c20b2d359a06615e72'
 for index in range(3):
  inputs=[arm,c0,c1];bad=bytearray(inputs[index]);bad[0]^=1;inputs[index]=bytes(bad)
  try:b.final_firmware(*inputs,ref)
  except ValueError:pass
  else:raise AssertionError(('modified join input accepted',index))
""",OPTIONS.stock.resolve(),OPTIONS.assembler.resolve(),
                       OPTIONS.arm_image.resolve() if OPTIONS.arm_image else '-')

    def test_206_structural_refusals(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for structural integration checks')
        self.isolated("""
import syx
from dspvoice import adapter as a
from dspvoice206 import build as b
ref=syx.parse(Path(sys.argv[2]).read_bytes());image=ref['image'];exe=Path(sys.argv[3])
components,labels=b.own_components(exe);stock0,stock1=a.pinned_streams(image)
records,_,suffix=a.parse_payload(stock1[6:])
def rejected(call,message):
 try:call()
 except ValueError as error:
  assert message in str(error),(message,str(error))
 else:raise AssertionError(('structural rejection missing',message))
def scatter(rows):
 payload=a.encode_records(rows)+suffix
 return a.w24(len(payload)//3)+a.w24(0)+payload
# Keep record count and all integration hooks intact except the one under test.
changed=[]
for space,start,words in records:
 words=list(words)
 if space=='P' and start<=0x2da<start+len(words):words[0x2da-start]^=1
 changed.append((space,start,tuple(words)))
rejected(lambda:b.core1_stream(scatter(changed),components,labels,exe),'integration entry')
# Replace one non-program record so the20-record layout contains an own-code collision.
index=next(i for i,row in enumerate(records) if row[0]!='P')
changed=list(records);changed[index]=('P',0xd32,(0,))
rejected(lambda:b.core1_stream(scatter(changed),components,labels,exe),'overlaps input')
# Duplicate writes enter through the206 core1 path, not just a direct adapter call.
program=next(row for row in records if row[0]=='P')
changed=list(records);changed[index]=('P',program[1],(program[2][0],))
rejected(lambda:b.core1_stream(scatter(changed),components,labels,exe),'overlapping')
# Insert a premature terminator after the correct new records: the decoded memory is
# still exact, but the opaque suffix now differs. This targets the suffix guard.
encode_records=a.encode_records
try:
 a.encode_records=lambda rows:encode_records(rows)+a.w24(0x100)+a.w24(0x123456)
 rejected(lambda:b.core1_stream(stock1,components,labels,exe),'suffix change')
finally:a.encode_records=encode_records
# Bypass only component generation, keeping actual assembled words. Each layout
# mutation must fail its structural guard, before a final output digest can mask it.
own=b.own_components;original=b.COMPONENTS
try:
 b.own_components=lambda _:(components,labels)
 for name,origin,message in [('instrument',0x3fff,'out-of-bounds'),
                             ('startup',0x2000,'reserved program gap')]:
  b.COMPONENTS=dict(original);old=b.COMPONENTS[name];b.COMPONENTS[name]=(origin,*old[1:])
  rejected(lambda:b.build_streams(image,exe),message)
 b.COMPONENTS=original
 # Keep the real stock streams while exposing a non-erased proposed placement.
 # Only the upstream whole-stock identity seam is bypassed for this one unit test.
 pinned=a.pinned_streams
 try:
  a.pinned_streams=lambda _:(stock0,stock1)
  occupied=bytearray(image);at=b.CORE0_AT-a.BASE
  occupied.extend(bytes([255])*(at+1-len(occupied)));occupied[at]=0
  rejected(lambda:b.build_streams(bytes(occupied),exe),'flash placement is occupied')
 finally:a.pinned_streams=pinned
finally:b.own_components=own;b.COMPONENTS=original
""",OPTIONS.stock.resolve(),OPTIONS.assembler.resolve())

    def test_cli_refusal_leaves_no_partial_output(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for CLI refusals')
        directory=self.clean/'cli-refusals';directory.mkdir(exist_ok=True)
        existing=directory/'existing-output';existing.mkdir();marker=existing/'keep';marker.write_bytes(b'unchanged')
        command=[sys.executable,'-I',str(self.clean/'tools/dspvoice206/build.py'),
                 '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                 '--out',str(existing)]
        proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('output directory already exists',proc.stderr)
        self.assertEqual(list(existing.iterdir()),[marker]);self.assertEqual(marker.read_bytes(),b'unchanged')
        self.assertFalse(list(directory.glob('.dspvoice206-*')))
        invalid=directory/'bad.syx';invalid.write_bytes(OPTIONS.stock.read_bytes()+b'!')
        cases=[('--stock',invalid)]
        if OPTIONS.arm_image:
            badarm=directory/'badarm.bin';data=bytearray(OPTIONS.arm_image.read_bytes());data[0]^=1;badarm.write_bytes(data)
            cases.append(('--arm-image',badarm))
        for number,(flag,path) in enumerate(cases):
            out=directory/f'output-{number}'
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice206/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(out)]
            if flag=='--stock':command[command.index('--stock')+1]=str(path)
            else:command.extend([flag,str(path)])
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertFalse(out.exists())
            self.assertFalse(list(directory.glob('.dspvoice206-*')))
        source=self.clean/'tools/dspvoice206/startup.asm';saved=source.read_bytes()
        out=directory/'tampered-source-output'
        try:
            source.write_bytes(saved+b'\n; source tamper\n')
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice206/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(out)]
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertFalse(out.exists())
            self.assertFalse(list(directory.glob('.dspvoice206-*')))
        finally:source.write_bytes(saved)



if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stock',type=Path)
    ap.add_argument('--assembler',type=Path)
    ap.add_argument('--arm-image',type=Path)
    OPTIONS,rest=ap.parse_known_args()
    unittest.main(argv=[sys.argv[0],*rest])
