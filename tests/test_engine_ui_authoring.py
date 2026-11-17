"""Public UI metadata authoring on synthetic bytes; no DSP execution or device access."""
import hashlib,json,struct,subprocess,sys,tempfile,unittest,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'tests'))
import cre3_check as ck
import engine_file as ef
from test_cre3_host_policy import fixture

def source(kind=6):
    data=fixture(kind);records=[bytes((5,6,64,64,0,32,0,0)),bytes((0x11,8,0,0,1,0,0x77,0)),bytes((6,1,0,0,0,0,0,0))]
    h=bytearray(data[:64]+b''.join(records));struct.pack_into('<H',h,4,len(h));h[26]=len(records);h[27]=0;h[46]=1;struct.pack_into('<I',h,40,0xfc0);h[60:64]=bytes(4);struct.pack_into('<I',h,60,zlib.crc32(h));return bytes(h)+data[64:]

class Authoring(unittest.TestCase):
    def test_roundtrip_preserves_body_and_identity(self):
        data=source();before=ck.parse(data);out=ef.ui3(data,macro_hues=(2,4,6));after=ck.check(out)
        self.assertEqual(after['body'],before['body']);self.assertEqual(after['body_crc'],before['body_crc']);self.assertEqual(after['ui_capabilities'],128)
        rec=[bytes.fromhex(r)for r in after['controls']];self.assertIn(bytes((6,1,0,0,0,0,0,0)),rec)
        self.assertEqual(rec[-3:],[bytes((4,9,0,0,0,32,2,0)),bytes((4,10,0,0,0,0,4,0)),bytes((4,11,0,0,0,0,6,0))])
    def test_fm_chords_and_bounded_poly(self):
        out=ef.ui3(source(1),chords=True,poly_allowed=(1,2,4),poly_default=2);r=ck.check(out)
        self.assertEqual(r['ui_capabilities'],129);self.assertIn(bytes((6,1,0,0,0,11,2,0)).hex(),r['controls'])
    def test_bad_declarations_refuse(self):
        for opts in (dict(macro_hues=(4,4,8)),dict(macro_hues=(4,4)),dict(chords=True),dict(poly_allowed=(1,),poly_default=1),dict(poly_allowed=(0,),poly_default=0)):
            with self.subTest(opts=opts),self.assertRaises(ValueError):ef.ui3(source(),**opts)
        for opts in (dict(poly_allowed=(1,1),poly_default=1),dict(poly_allowed=(1,2),poly_default=4),dict(poly_default=1)):
            with self.subTest(opts=opts),self.assertRaises(ValueError):ef.ui3(source(1),**opts)
        with self.assertRaisesRegex(ValueError,'legacy header'):ef.ui3(ef.ui3(source()))
    def test_export_only_cli_authoring_and_check(self):
        with tempfile.TemporaryDirectory()as temp:
            p=Path(temp);tools=p/'tools';tools.mkdir()
            for name in ('engine_file.py','crbc.py','cre3_check.py','abi3_usb_policy.json'):(tools/name).write_bytes((ROOT/'tools'/name).read_bytes())
            original=p/'in.dse';original.write_bytes(source());out=p/'out.dse'
            for args in (('ui3',str(original),str(out),'--macro-hues','2,4,6'),('check3',str(out))):
                r=subprocess.run([sys.executable,'-I',str(tools/'engine_file.py'),*args],cwd=p,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertEqual(ck.parse(out.read_bytes())['body'],ck.parse(original.read_bytes())['body'])
            kept=out.read_bytes();r=subprocess.run([sys.executable,'-I',str(tools/'engine_file.py'),'ui3',str(original),str(out),'--chords','1'],capture_output=True,text=True);self.assertEqual(r.returncode,1);self.assertEqual(out.read_bytes(),kept)
if __name__=='__main__':unittest.main()
