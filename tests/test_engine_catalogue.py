# SPDX-License-Identifier: MIT
"""Repository catalogue checks use synthetic files, without hardware or downloads."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
import engine_catalogue as cat
import cre3_check
from test_cre3_host_policy import fixture as source_fixture


def blob(kind=7,version=0x80010000,cycles=None):
    data=bytearray(source_fixture(kind))
    struct.pack_into('<I',data,28,version)
    if cycles is not None:struct.pack_into('<I',data,52,cycles)
    data[60:64]=bytes(4);struct.pack_into('<I',data,60,zlib.crc32(data[:64]))
    return bytes(data)


def entry(data=None,name='Demo',file='demo.dse',kind=7,version='1.0.0'):
    data=blob(kind) if data is None else data
    return dict(name=name,file=file,sha256=hashlib.sha256(data).hexdigest(),size=len(data),kind=kind,
                version=version,author='Example',licence='MIT',status='community')


def tree(root,rows,files):
    folder=root/'web/engines';folder.mkdir(parents=True,exist_ok=True)
    (folder/'index.json').write_text(json.dumps(dict(format=cat.FORMAT,engines=rows)))
    for name,data in files.items():(folder/name).write_bytes(data)


def listing(root,name='demo.lst',broken=False):
    text='2180\t0af080 002184\tjmp $2184\n2182\t000000\tnop\n2183\t000000\tnop\n2184\t00000c\trts\n'
    if broken:text=text.replace('00000c','000000')
    (root/'web/engines'/name).write_text(text)


class Catalogue(unittest.TestCase):
    def test_exact_schema_fm_identity_and_provenance(self):
        value=json.loads((ROOT/'web/engines/index.json').read_text())
        rows=cat.validate_catalogue(value)
        self.assertEqual([(r['name'],r['file'],r['kind'],r['version']) for r in rows],
                         [('FM','fm.dse',1,'2.1.0'),('Formant','formant.dse',6,'1.0.0'),('Rings','rings.dse',5,'1.0.0')])
        self.assertTrue(all(2!=r['kind'] for r in rows))     # kind 2 is reserved
        for row in rows:
            self.assertEqual((row['author'],row['licence'],row['status']),('km','MIT','official'))
        assigned=cat.kind_assignments()
        for row in rows:
            self.assertEqual(assigned[str(row['kind'])]['file'],row['file'])
        self.assertIn('Copyright (c) 2026 km',(ROOT/'LICENSE').read_text())

    def test_official_and_community_status(self):
        for status in ('official', 'community'):
            self.assertEqual(cat.validate_catalogue(dict(format=cat.FORMAT,engines=[dict(entry(),status=status)]))[0]['status'],status)
        for status in ('approved', '', None, 1):
            with self.assertRaises(ValueError):cat.validate_catalogue(dict(format=cat.FORMAT,engines=[dict(entry(),status=status)]))

    def test_no_url_path_escape_reserved_name_extra_field_or_duplicate_kind(self):
        for bad in ('https://example.org/a.dse','../a.dse','sub/a.dse','default.dse','none.dse','A.dse'):
            with self.subTest(file=bad),self.assertRaises(ValueError):
                cat.validate_catalogue(dict(format=cat.FORMAT,engines=[dict(entry(),file=bad)]))
        for value in (dict(entry(),extra=True),dict(entry(),status='approved')):
            with self.assertRaises(ValueError):cat.validate_catalogue(dict(format=cat.FORMAT,engines=[value]))
        with self.assertRaises(ValueError):
            cat.validate_catalogue(dict(format=cat.FORMAT,engines=[entry(),entry(name='Other',file='other.dse')]))

    def test_static_checker_hash_size_kind_version_and_check13_are_not_bypassed(self):
        good=blob();row=entry(good)
        with patch.object(cre3_check,'check',wraps=cre3_check.check) as check:
            self.assertTrue(cat.check_asset(row,good)['static_eligible']);check.assert_called_once_with(good)
        for mutant in (dict(row,sha256='0'*64),dict(row,size=len(good)+1),dict(row,kind=8),dict(row,version='2.0.0')):
            with self.assertRaises(ValueError):cat.check_asset(mutant,good)
        for cycles in (0,cre3_check.policy()['cycle_limit']+1):
            data=blob(cycles=cycles)
            with self.assertRaisesRegex(ValueError,'check 13'):cat.check_asset(entry(data),data)
        for kind in (0,128,255):
            with self.assertRaises(ValueError):cat.check_asset(entry(blob(kind),kind=kind),blob(kind))

    def test_local_no_base_check_does_not_require_an_author_listing(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);data=blob();tree(root,[entry(data)],{'demo.dse':data})
            result=cat.check_repository(root)
            self.assertEqual(result['status'],'PASS');self.assertTrue(result['maintainer_review_required'])

    def test_new_entry_requires_bound_listing_and_free_kind(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp)/'new';base=Path(temp)/'base';base.mkdir();data=blob();tree(root,[entry(data)],{'demo.dse':data})
            with patch.object(cat,'kind_assignments',return_value={'1':{'file':'fm.dse'},'2':{'file':None}}):
                with self.assertRaisesRegex(ValueError,'missing'):cat.check_repository(root,base_dir=base)
                listing(root);self.assertEqual(cat.check_repository(root,base_dir=base)['status'],'PASS')
                listing(root,broken=True)
                with self.assertRaisesRegex(ValueError,'listing words'):cat.check_repository(root,base_dir=base)
                reserved=blob(2);tree(root,[entry(reserved,kind=2)],{'demo.dse':reserved});listing(root)
                with self.assertRaisesRegex(ValueError,'already assigned'):cat.check_repository(root,base_dir=base)

    def test_same_file_kind_update_is_allowed_but_collision_or_kind_change_is_not(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp)/'new';base=Path(temp)/'base';old=blob();new=blob(version=0x80010001)
            tree(base,[entry(old)],{'demo.dse':old});tree(root,[entry(new,version='1.0.1')],{'demo.dse':new});listing(root)
            with patch.object(cat,'kind_assignments',return_value={}):
                self.assertEqual(cat.check_repository(root,base_dir=base)['status'],'PASS')
                same_version=bytearray(old);same_version[-1]^=1
                struct.pack_into('<I',same_version,12,zlib.crc32(same_version[64:]))
                same_version[60:64]=bytes(4);struct.pack_into('<I',same_version,60,zlib.crc32(same_version[:64]))
                tree(root,[entry(bytes(same_version))],{'demo.dse':bytes(same_version)})
                with self.assertRaisesRegex(ValueError,'increase its version'):
                    cat.check_repository(root,base_dir=base)
                tree(root,[entry(new,version='1.0.0')],{'demo.dse':new})
                with self.assertRaises(ValueError):cat.check_repository(root,base_dir=base)
                tree(root,[entry(new,file='other.dse',version='1.0.1')],{'other.dse':new})
                with self.assertRaisesRegex(ValueError,'collides'):cat.check_repository(root,base_dir=base)
                changed=blob(8);tree(root,[entry(changed,kind=8)],{'demo.dse':changed})
                with self.assertRaisesRegex(ValueError,'cannot change kind'):cat.check_repository(root,base_dir=base)

    def test_new_unlisted_file_symlink_and_hidden_engine_word_are_refused(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp)/'new';base=Path(temp)/'base';data=blob();tree(base,[entry(data)],{'demo.dse':data});tree(root,[entry(data)],{'demo.dse':data})
            (root/'extra.dse').write_bytes(data)
            with patch.object(cat,'kind_assignments',return_value={}):
                with self.assertRaisesRegex(ValueError,'no catalogue'):cat.check_repository(root,base_dir=base)
            (root/'extra.dse').unlink();p=root/'web/engines/demo.dse';p.unlink();p.symlink_to(base/'web/engines/demo.dse')
            with self.assertRaisesRegex(ValueError,'symlink'):cat.check_repository(root)
            p.unlink();p.write_bytes(data);listing(root)
            q=root/'web/engines/demo.lst';q.write_text('2180\t0af080 002184\tjmp $2184\n')
            with self.assertRaisesRegex(ValueError,'omits'):cat.check_listing(entry(data),data,q)

    def test_canonical_listing_findings_refuse_but_manual_sites_stay_visible(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);data=blob();tree(root,[entry(data)],{'demo.dse':data});listing(root)
            path=root/'web/engines/demo.lst'
            # Synthetic decoded text exercises the shared rule API; the checker
            # explicitly does not authenticate decoded text against opcodes.
            text=path.read_text();path.write_text(text.replace('jmp $2184','do #$2,$2184'))
            with self.assertRaisesRegex(ValueError,'instruction-sequence findings'):
                cat.check_listing(entry(data),data,path)
            path.write_text(text.replace('2182\t000000\tnop','2182\t000000\tmove x0,sr'))
            result=cat.check_listing(entry(data),data,path)
            self.assertTrue(result['review_sites']);self.assertTrue(result['manual_review_required'])

    def test_real_registry_and_nonprivileged_cli(self):
        import subprocess
        assignments=cat.kind_assignments()
        self.assertEqual(assignments['1']['file'],'fm.dse')
        self.assertIsNone(assignments['2']['file'])
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp)/'candidate';base=Path(temp)/'base';base.mkdir()
            data=blob();tree(root,[entry(data)],{'demo.dse':data});listing(root)
            run=subprocess.run([sys.executable,'-I',str(ROOT/'tools/engine_catalogue.py'),
                '--repo-root',str(root),'--base-dir',str(base)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertEqual(json.loads(run.stdout)['status'],'PASS')
            data=blob(6);tree(root,[entry(data,kind=6)],{'demo.dse':data})
            run=subprocess.run([sys.executable,'-I',str(ROOT/'tools/engine_catalogue.py'),
                '--repo-root',str(root),'--base-dir',str(base)],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertIn('assigned or reserved',run.stdout)

    def test_browser_catalogue_schema_matches_python(self):
        row=entry();cases=[dict(format=cat.FORMAT,engines=[row])]
        for status in ('official','community','approved',None,1):
            cases.append(dict(format=cat.FORMAT,engines=[dict(row,status=status)]))
        for field,values in {'name':['',' Demo','Demo ', 'x'*65,'\u00e5'],
                             'author':['x'*65,None], 'licence':['MIT OR BSD','x'*33,''],
                             'version':['01.0.0','1.00.0','1.0.00',['1.0.0'],None],
                             'sha256':[['a'*64],None,'A'*64], 'size':[True,63,15361,64.5],
                             'kind':[True,0,128]}.items():
            cases.extend(dict(format=cat.FORMAT,engines=[dict(row,**{field:value})])for value in values)
        cases.extend([dict(format=cat.FORMAT,engines=[row,dict(row,file='other.dse')]),
                      dict(format=cat.FORMAT,engines=[row,dict(row,name='dEmO',file='other.dse',kind=8)]),
                      dict(format=cat.FORMAT,engines=[dict(row,extra=True)]),
                      dict(format=cat.FORMAT,engines=[],extra=True),
                      dict(format=cat.FORMAT,engines=[dict(row,file=f'e{i}.dse',name=f'E{i}',kind=i%127+1)for i in range(128)])])
        expected=[]
        for value in cases:
            try:cat.validate_catalogue(value);expected.append(True)
            except ValueError:expected.append(False)
        script=r"""const fs=require('node:fs');const html=fs.readFileSync(process.argv[1],'utf8');
const part=n=>html.slice(html.indexOf('/* '+n+' BEGIN */'),html.indexOf('/* '+n+' END */'));
const {LL}=new Function(part('MLCORE')+'\n'+part('LLCORE')+'\nreturn {LL};')();
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>{try{LL.catalogue(x);return true;}catch(e){return false;}})));"""
        result=subprocess.run(['node','-e',script,str(ROOT/'web/loader.html')],input=json.dumps(cases),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),expected)

    def test_ci_is_read_only_pull_request_with_trusted_checker(self):
        workflow=(ROOT/'.github/workflows/community-engine-check.yml').read_text()
        self.assertIn('pull_request:',workflow);self.assertNotIn('pull_request_target',workflow)
        self.assertIn('contents: read',workflow);self.assertEqual(workflow.count('persist-credentials: false'),2)
        self.assertIn('python3 -I trusted/tools/engine_catalogue.py',workflow)
        self.assertNotIn('python3 -I candidate/',workflow)
        self.assertIn('--repo-root candidate --base-dir trusted',workflow)

if __name__=='__main__':unittest.main()
