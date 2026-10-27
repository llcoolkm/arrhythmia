# SPDX-License-Identifier: MIT
"""Own-source checks; optional local inputs reproduce the VERSION207 streams."""
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


class Source207Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='dspvoice207-source-test-')
        cls.clean = Path(cls.temp.name)
        for name in ('dspvoice207', 'dspvoice206', 'dspvoice'):
            shutil.copytree(ROOT/'tools'/name, cls.clean/'tools'/name,
                            ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT/'tools/syx.py', cls.clean/'tools/syx.py')
        (cls.clean/'tests').mkdir()
        shutil.copy2(Path(__file__), cls.clean/'tests'/Path(__file__).name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def isolated(self, code, *args):
        prefix = 'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(sys.argv[1])/"tools"))\n_before=set(sys.modules)\n'
        out = subprocess.run([sys.executable, '-I', '-c', prefix+code, str(self.clean), *map(str,args)],
                             cwd=self.clean, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout+out.stderr)
        if 'runtime public imports:' in out.stdout or 'same-layout instruction mutation:' in out.stdout:
            print(out.stdout.strip())
        return out.stdout

    def frontend(self, name, body):
        p = self.clean/name
        p.write_text('#!'+sys.executable+'\nimport sys\n'+body)
        p.chmod(0o755)
        return p

    def test_resident_tables_exact_and_layout(self):
        self.isolated('''
import hashlib
from dspvoice207 import tables as t
w=t.table_data()
assert len(w)==1217 and all(type(x)is int and 0<=x<=0xffffff for x in w)
assert hashlib.sha256(b''.join(x.to_bytes(3,'big') for x in w)).hexdigest()==\
'7983e39f6a9381887a8d909d2a3b39f6dd7865a13d6433dda0ebae0805095fc4'
a,d,s,r=[w[i:i+128] for i in range(0,512,128)]
assert a[0]==559241 and d[64]==0x7fb4b6 and s[0]==0 and s[-1]==0x7fffff
assert all(x>=y for x,y in zip(a,a[1:]))
assert all(x<=y for row in (d,s) for x,y in zip(row,row[1:]))
assert r[0]==1505886 and all(x>y for x,y in zip(r,r[1:]))
assert w[512]==0 and w[768]==0x7fffff
assert w[769+126]==w[769+127]  # bypass index's unused cutoff coefficient
assert w[1025]==0 and w[1152]==0x7fffff
assert len(t.BANKS)==16 and w[1153:]==[n for _,bank in t.BANKS for n in bank]
''')

    def test_arithmetic_drift_refused(self):
        self.isolated('''
from dspvoice207 import tables as t
original=t.math.sin
t.math.sin=lambda x:original(x)+0.0001
try:t.table_data()
except ValueError as e:assert 'drift' in str(e)
else:raise AssertionError('silently accepted changed sine arithmetic')
''')

    def test_imports_stay_in_public_closure(self):
        tools=self.clean/'tools'
        allowed={'dspvoice207','dspvoice206','dspvoice','syx'}|sys.stdlib_module_names
        import json
        manifest=json.loads((tools/'dspvoice207/source-manifest.json').read_text())
        sources=[self.clean/name for name in manifest['files'] if name.endswith('.py')]
        self.assertIn(self.clean/'tests'/Path(__file__).name,sources)
        for source in sources:
            for node in ast.walk(ast.parse(source.read_text())):
                names=([x.name.split('.')[0] for x in node.names] if isinstance(node,ast.Import) else
                       [(node.module or '').split('.')[0]] if isinstance(node,ast.ImportFrom) else [])
                for name in names:
                    self.assertIn(name,allowed,f'{source.name}: non-public import {name}')
        for source in (tools/'dspvoice207').glob('*.asm'):
            self.assertNotRegex(source.read_text().lower(),r'(?m)^\s*(incbin|dc|dcb)\b')

    def test_source_manifest_strict_closure_and_comment_tamper(self):
        self.isolated("""
import json
from dspvoice207 import build as b
root=Path(sys.argv[1]);manifest=root/'tools/dspvoice207/source-manifest.json'
original=manifest.read_bytes();doc=json.loads(original)
assert b.verify_sources(root)==doc
assert set(doc['files'])==set(b.SOURCE_FILES)
for action in ('missing','extra','traversal','wrong-pin','wrong-arm','wrong-label','wrong-version'):
 changed=json.loads(original)
 if action=='missing':del changed['files'][next(iter(changed['files']))]
 elif action=='extra':changed['files']['tools/dspvoice207/unlisted.asm']='0'*64
 elif action=='traversal':changed['files']['../outside']='0'*64
 elif action=='wrong-pin':changed['core0_sha256']='0'*64
 elif action=='wrong-arm':changed['arm_sha256']='0'*64
 elif action=='wrong-label':changed['release_label']=7309
 else:changed['release_version']=[0,0,0,0,0,0]
 manifest.write_text(json.dumps(changed))
 try:b.verify_sources(root)
 except ValueError:pass
 else:raise AssertionError(('bad manifest accepted',action))
 manifest.write_bytes(original)
p=root/'tools/dspvoice207/instrument.asm';saved=p.read_bytes()
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
from dspvoice207 import build as b
raw=Path(sys.argv[2]).read_bytes();assert a.sha(raw)==a.STOCK_SYX_SHA256
ref=syx.parse(raw);image=ref['image'];exe=Path(sys.argv[3])
components,labels=b.own_components(exe)
for name,(origin,count,digest) in b.COMPONENTS.items():
 assert len(components[name])==count and a.sha(b.packed(components[name]))==digest
c0,c1,meta=b.build_streams(image,exe)
# Inspect actual imports after executing the complete local builder, not just import-time syntax.
root=Path(sys.argv[1]).resolve();loaded=[]
for name,module in sorted(sys.modules.items()):
 path=getattr(module,'__file__',None)
 if path is None or name=='__main__' or name in _before:continue
 first=name.split('.')[0]
 if first in sys.stdlib_module_names:continue
 resolved=Path(path).resolve()
 assert resolved.is_relative_to(root/'tools'),(name,str(resolved))
 loaded.append((name,str(resolved.relative_to(root))))
assert not any('/voice/' in path for _,path in loaded)
print('runtime public imports:',loaded)
assert a.sha(c0)=='edf31f2710e587928c9dc071336d42cb1e52fd361a7414aaffbe7897578f6fe0'
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
 b.COMPONENTS=dict(b.COMPONENTS);v=b.COMPONENTS['startup'];b.COMPONENTS['startup']=(0x2000,v[1],v[2])
 try:b.build_streams(image,exe)
 except ValueError as e:assert 'overlap' in str(e)
 else:raise AssertionError('overlapping own components accepted')
finally:b.own_components=original_own;b.COMPONENTS=original_components
# Duplicate scatter records are forbidden rather than last-write-wins.
try:a.memory_map([('P',4,(1,)),('P',4,(1,))])
except ValueError:pass
else:raise AssertionError('duplicate scatter write accepted')
# Valid assembly with different executed instructions cannot pass component pins.
p=b.HERE/'instrument.asm';saved=p.read_text()
try:
 p.write_text('nop\\n'+saved)
 try:b.own_components(exe)
 except ValueError:pass
 else:raise AssertionError('modified source accepted')
finally:p.write_text(saved)
if sys.argv[4]!='-':
 arm=Path(sys.argv[4]).read_bytes()
 if b.ARM_SHA is None:
  try:b.final_firmware(arm,c0,c1,ref)
  except ValueError as e:assert 'bound' in str(e).lower() or 'pin' in str(e).lower(),str(e)
  else:raise AssertionError('unbound final-image join accepted')
 else:
  final,stream=b.final_firmware(arm,c0,c1,ref)
  assert a.sha(final)==b.IMAGE_SHA and a.sha(stream)==b.SYX_SHA
  parsed=syx.parse(stream)
  assert parsed['crc_ok'] and parsed['device']==0x63 and parsed['header_prefix'][0]==0
  for index in range(3):
   inputs=[arm,c0,c1];bad=bytearray(inputs[index]);bad[0]^=1;inputs[index]=bytes(bad)
   try:b.final_firmware(*inputs,ref)
   except ValueError:pass
   else:raise AssertionError(('modified join input accepted',index))
""",OPTIONS.stock.resolve(),OPTIONS.assembler.resolve(),
                       OPTIONS.arm_image.resolve() if OPTIONS.arm_image else '-')

    def test_207_structural_refusals(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for structural integration checks')
        self.isolated("""
import syx
from dspvoice import adapter as a
from dspvoice207 import build as b
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
# Duplicate writes enter through the207 core1 path, not just a direct adapter call.
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
                             ('startup',0x1ff0,'program-gap overlay')]:
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

    def test_runtime_imports_use_only_the_clean_public_tree(self):
        self.isolated("""
from dspvoice207 import build as b
from dspvoice207 import tables as t
b.verify_sources()
t.table_data()
root=Path(sys.argv[1]).resolve()
loaded=[]
for name,module in sorted(sys.modules.items()):
 path=getattr(module,'__file__',None)
 if path is None or name=='__main__' or name in _before:continue
 first=name.split('.')[0]
 if first in sys.stdlib_module_names:continue
 resolved=Path(path).resolve()
 assert resolved.is_relative_to(root/'tools'),(name,str(resolved))
 loaded.append((name,str(resolved.relative_to(root))))
assert any(name=='dspvoice207.build' for name,_ in loaded)
assert not any('/voice/' in path for _,path in loaded)
print('runtime public imports:',loaded)
""")

    def test_unbound_final_join_is_explicitly_refused(self):
        self.isolated("""
from dspvoice207 import build as b
saved=(b.ARM_SHA,b.IMAGE_SHA,b.SYX_SHA)
try:
 b.ARM_SHA=b.IMAGE_SHA=b.SYX_SHA=None
 try:b.final_firmware(b'',b'',b'',{})
 except ValueError as error:
  assert 'bound' in str(error).lower() or 'pin' in str(error).lower(),str(error)
 else:raise AssertionError('unbound final-image join accepted')
finally:b.ARM_SHA,b.IMAGE_SHA,b.SYX_SHA=saved
""")

    def test_own_program_pin_and_default_coefficients(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for actual own-code checks')
        self.isolated("""
from dspvoice207 import build as b
from dspvoice207 import tables as t
components,labels=b.own_components(Path(sys.argv[2]))
origin,count,digest=b.COMPONENTS['instrument']
assert (origin,count,digest)==(0x2000,3428,
 '572002303b3eb31a690c01c20a012c45d346139c54e24e5b6c937b4dc1f88c0c')
assert b.a.sha(b.packed(components['instrument']))==digest
assert labels['instrument']['entry']==0x2200
assert labels['instrument']['release_cosine_weights']==0x21c2
assert b.a.sha(b.packed(t.table_data()))== '7983e39f6a9381887a8d909d2a3b39f6dd7865a13d6433dda0ebae0805095fc4'
assert t.table_data()[0]==559241 and t.table_data()[384]==1505886
""",OPTIONS.assembler.resolve())

    def test_atomic_directory_publication_preserves_racing_targets(self):
        self.isolated("""
from dspvoice207 import build as b
root=Path(sys.argv[1])/'atomic-publication';root.mkdir()
for kind in ('empty-directory','file','dangling-link'):
 source=root/('source-'+kind);source.mkdir();(source/'new').write_bytes(b'new')
 target=root/('target-'+kind)
 if kind=='empty-directory':target.mkdir()
 elif kind=='file':target.write_bytes(b'kept')
 else:target.symlink_to(root/'absent')
 try:b.rename_noreplace(source,target)
 except OSError:pass
 else:raise AssertionError(('replaced pre-existing target',kind))
 assert (source/'new').read_bytes()==b'new'
 if kind=='empty-directory':assert target.is_dir() and not list(target.iterdir())
 elif kind=='file':assert target.read_bytes()==b'kept'
 else:assert target.is_symlink() and not (root/'absent').exists()
source=root/'successful-source';source.mkdir();(source/'new').write_bytes(b'new')
target=root/'new-target'
b.rename_noreplace(source,target)
assert not source.exists() and (target/'new').read_bytes()==b'new'
""")

    def test_actual_entry_and_terminal_placeholder_guards(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for assembly guards')
        self.isolated("""
from dspvoice207 import build as b
exe=Path(sys.argv[2]);source=b.HERE/'instrument.asm';original=source.read_text()
def rejected(text,message):
 source.write_text(text)
 try:b.own_components(exe)
 except ValueError as error:assert message in str(error),(message,str(error))
 else:raise AssertionError(('accepted altered assembly',message))
 finally:source.write_text(original)
rejected('nop\\n'+original,'entry')
start=original.index('release_cosine_weights:')
at=original.index('nop',start)
rejected(original[:at]+'rts'+original[at+3:],'placeholder')
assert source.read_text()==original
""",OPTIONS.assembler.resolve())

    def test_same_layout_instruction_mutation_hits_component_pin(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for the executed-component pin refusal')
        self.isolated("""
from dspvoice207 import build as b
exe=Path(sys.argv[2]);source=b.HERE/'instrument.asm';original=source.read_text()
components,labels=b.own_components(exe)
old='move #>$400000,b';new='move #>$3fffff,b'
assert old in original
mutated=original.replace(old,new,1)
try:
 source.write_text(mutated)
 # The mutation is valid assembly: entry, labels and word count do not move.
 words,_,changed_labels=b.assemble(exe,mutated,b.COMPONENTS['instrument'][0])
 assert len(words)==len(components['instrument'])==3428
 assert changed_labels==labels['instrument'] and changed_labels['entry']==0x2200
 try:b.own_components(exe)
 except ValueError as error:
  assert str(error)=='instrument differs from the supported component pin',str(error)
  print('same-layout instruction mutation:',str(error))
 else:raise AssertionError('valid same-layout changed instructions passed their component pin')
finally:source.write_text(original)
assert b.own_components(exe)[0]['instrument']==components['instrument']
""",OPTIONS.assembler.resolve())

    def test_cli_success_files_checksums_and_full_image(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for successful CLI builds')
        import json
        directory=self.clean/'cli-success';directory.mkdir()
        cases=[('streams',None)]
        if OPTIONS.arm_image:
            cases.append(('full-image',OPTIONS.arm_image.resolve()))
        for name,arm in cases:
            output=directory/name
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(output)]
            if arm is not None:
                command.extend(['--arm-image',str(arm)])
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            expected={'core0-instrument.bin','core1-suppression.bin','build.json','SHA256SUMS'}
            if arm is not None:
                expected|={'arrhythmia-firmware-7308.bin','arrhythmia-firmware-7308.syx'}
            self.assertEqual({p.name for p in output.iterdir()},expected)
            self.assertTrue(all(p.is_file() and not p.is_symlink() for p in output.iterdir()))
            sums=(output/'SHA256SUMS').read_text().splitlines()
            self.assertEqual(len(sums),len(expected)-1)
            recorded={}
            for line in sums:
                digest,file=line.split('  ',1)
                self.assertNotIn(file,recorded)
                self.assertRegex(digest,r'^[0-9a-f]{64}$')
                recorded[file]=digest
            self.assertEqual(set(recorded),expected-{'SHA256SUMS'})
            for file,digest in recorded.items():
                self.assertEqual(hashlib.sha256((output/file).read_bytes()).hexdigest(),digest)
            self.assertEqual((output/'SHA256SUMS').read_text(),proc.stdout)
            self.assertEqual(recorded['core0-instrument.bin'],
                             'edf31f2710e587928c9dc071336d42cb1e52fd361a7414aaffbe7897578f6fe0')
            self.assertEqual(recorded['core1-suppression.bin'],
                             'b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948')
            metadata=json.loads((output/'build.json').read_text())
            self.assertEqual(metadata['format'],'arrhythmia-dsp207-local-build/1')
            self.assertEqual(metadata['version'],0x207)
            self.assertEqual(metadata['core0_sha256'],recorded['core0-instrument.bin'])
            self.assertEqual(metadata['core1_sha256'],recorded['core1-suppression.bin'])
            self.assertEqual((metadata['core0_pointer'],metadata['core1_pointer']),
                             (0x080768C0,0x080101F0))
            self.assertEqual(metadata['own_components']['instrument'],
                             {'origin':0x2000,'words':3428,
                              'sha256':'572002303b3eb31a690c01c20a012c45d346139c54e24e5b6c937b4dc1f88c0c'})
            self.assertEqual(metadata['assembler_sha256'],
                             hashlib.sha256(OPTIONS.assembler.resolve().read_bytes()).hexdigest())
            self.assertFalse(list(directory.glob('.dspvoice207-*')))
            if arm is not None:
                self.assertEqual(recorded['arrhythmia-firmware-7308.bin'],
                                 '72ec2c87d7c4629b66de8debd9d46ebc3f58575bea521226d04a92df842a5b6d')
                self.assertEqual(recorded['arrhythmia-firmware-7308.syx'],
                                 '2731b3d8eba47928b25e1f6fc124212878eb57f0182a24127e1f92c0fd3d0381')
                self.isolated("""
import struct
import syx
output=Path(sys.argv[2]);image=(output/'arrhythmia-firmware-7308.bin').read_bytes()
ref=syx.parse((output/'arrhythmia-firmware-7308.syx').read_bytes())
assert ref['crc_ok'] and ref['device']==0x63 and bytes(ref['image'])==image
assert ref['header_prefix']==bytes([0,0,0,0,8,0,8])
assert struct.unpack_from('<H',image,0x1ca)[0]==7308
""",output)

    def test_preexisting_files_and_symlinks_are_preserved(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for CLI path refusals')
        directory=self.clean/'path-refusals';directory.mkdir(exist_ok=True)
        target=directory/'kept-directory';target.mkdir();marker=target/'keep';marker.write_bytes(b'unchanged')
        file=directory/'kept-file';file.write_bytes(b'file-marker')
        link=directory/'directory-link';link.symlink_to(target,target_is_directory=True)
        dangling=directory/'dangling-link';dangling.symlink_to(directory/'absent')
        for output in (target,file,link,dangling):
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(output)]
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertIn('already exists',proc.stderr)
            self.assertEqual(marker.read_bytes(),b'unchanged')
            self.assertEqual(file.read_bytes(),b'file-marker')
            self.assertTrue(link.is_symlink());self.assertTrue(dangling.is_symlink())
            self.assertFalse((directory/'absent').exists())
            self.assertFalse(list(directory.glob('.dspvoice207-*')))

    def test_gated_arm_image_is_refused_when_supplied(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler or not OPTIONS.gated_arm_image:
            self.skipTest('supply --stock, --assembler and --gated-arm-image for the actual gated artifact refusal')
        output=self.clean/'gated-refusal-output'
        command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                 '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                 '--arm-image',str(OPTIONS.gated_arm_image.resolve()),'--out',str(output)]
        proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.clean.glob('.dspvoice207-*')))

    def test_cli_refusal_leaves_no_partial_output(self):
        if not OPTIONS or not OPTIONS.stock or not OPTIONS.assembler:
            self.skipTest('supply --stock and --assembler for CLI refusals')
        directory=self.clean/'cli-refusals';directory.mkdir(exist_ok=True)
        existing=directory/'existing-output';existing.mkdir();marker=existing/'keep';marker.write_bytes(b'unchanged')
        command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                 '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                 '--out',str(existing)]
        proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('output directory already exists',proc.stderr)
        self.assertEqual(list(existing.iterdir()),[marker]);self.assertEqual(marker.read_bytes(),b'unchanged')
        self.assertFalse(list(directory.glob('.dspvoice207-*')))
        invalid=directory/'bad.syx';invalid.write_bytes(OPTIONS.stock.read_bytes()+b'!')
        cases=[('--stock',invalid)]
        if OPTIONS.arm_image:
            badarm=directory/'badarm.bin';data=bytearray(OPTIONS.arm_image.read_bytes());data[0]^=1;badarm.write_bytes(data)
            cases.append(('--arm-image',badarm))
        for number,(flag,path) in enumerate(cases):
            out=directory/f'output-{number}'
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(out)]
            if flag=='--stock':command[command.index('--stock')+1]=str(path)
            else:command.extend([flag,str(path)])
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertFalse(out.exists())
            self.assertFalse(list(directory.glob('.dspvoice207-*')))
        source=self.clean/'tools/dspvoice207/instrument.asm';saved=source.read_bytes()
        out=directory/'tampered-source-output'
        try:
            source.write_bytes(saved+b'\n; source tamper\n')
            command=[sys.executable,'-I',str(self.clean/'tools/dspvoice207/build.py'),
                     '--stock',str(OPTIONS.stock.resolve()),'--assembler',str(OPTIONS.assembler.resolve()),
                     '--out',str(out)]
            proc=subprocess.run(command,cwd=self.clean,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertFalse(out.exists())
            self.assertFalse(list(directory.glob('.dspvoice207-*')))
        finally:source.write_bytes(saved)



if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stock',type=Path)
    ap.add_argument('--assembler',type=Path)
    ap.add_argument('--arm-image',type=Path)
    ap.add_argument('--gated-arm-image',type=Path)
    OPTIONS,rest=ap.parse_known_args()
    unittest.main(argv=[sys.argv[0],*rest])
