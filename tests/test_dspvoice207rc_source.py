# SPDX-License-Identifier: MIT
"""Offline source candidate checks; no MIDI or capture device is opened."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from dspvoice207rc import build as b
ARGS = None
class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref=b.syx.parse(ARGS.stock.read_bytes())
        cls.c0,cls.c1,cls.meta=b.build_streams(cls.ref['image'],ARGS.assembler)
    def test_golden_streams_and_full_join(self):
        self.assertEqual(b.a.sha(self.c0),b.CORE0_SHA)
        self.assertEqual(b.a.sha(self.c1),b.CORE1_SHA)
        if ARGS.arm_image:
            im,sx=b.final_firmware(ARGS.arm_image.read_bytes(),self.c0,self.c1,self.ref)
            self.assertEqual(b.a.sha(im),b.IMAGE_SHA);self.assertEqual(b.a.sha(sx),b.SYX_SHA)
            self.assertTrue(b.syx.parse(sx)['crc_ok'])
    def test_old_component_and_valid_mutation_refused(self):
        old=(ROOT/'tools/dspvoice207/instrument.asm').read_text()
        original=Path.read_text
        target=b.HERE/'instrument.asm'
        for text in [old,target.read_text().replace('#>$2d6a86,x0','#>$2d6a85,x0',1)]:
            def read(p,*args,**kwargs):
                return text if p==target else original(p,*args,**kwargs)
            with patch.object(Path,'read_text',read):
                with self.assertRaisesRegex(ValueError,'instrument differs from the supported component pin'):
                    b.own_components(ARGS.assembler)
    def test_wrong_arm_or_stream_refused(self):
        arm=ARGS.arm_image.read_bytes()if ARGS.arm_image else bytes(16)
        with self.assertRaisesRegex(ValueError,'exact public ARM'):
            b.final_firmware(bytes([arm[0]^1])+arm[1:],self.c0,self.c1,self.ref)
        with self.assertRaisesRegex(ValueError,'exact public ARM'):
            b.final_firmware(arm,self.c0[:-1]+bytes([self.c0[-1]^1]),self.c1,self.ref)
    def test_cli_and_atomic_refusals(self):
        with tempfile.TemporaryDirectory()as td:
            out=Path(td)/'result';cmd=[sys.executable,'-I',str(b.HERE/'build.py'),'--stock',str(ARGS.stock),'--assembler',str(ARGS.assembler),'--out',str(out)]
            if ARGS.arm_image:cmd+=['--arm-image',str(ARGS.arm_image)]
            p=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            expected={'core0-instrument.bin','core1-suppression.bin','build.json','SHA256SUMS'}
            if ARGS.arm_image:expected|={'arrhythmia-firmware-7316.bin','arrhythmia-firmware-7316.syx'}
            self.assertEqual({x.name for x in out.iterdir()},expected)
            for line in(out/'SHA256SUMS').read_text().splitlines():
                sha,n=line.split(None,1);self.assertEqual(b.a.sha((out/n).read_bytes()),sha)
            before={p.name:p.read_bytes()for p in out.iterdir()}
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0)
            self.assertEqual({p.name:p.read_bytes()for p in out.iterdir()},before)
            self.assertFalse(any(x.name.startswith('.dspvoice207rc-')for x in Path(td).iterdir()))
    def test_wrong_stock_cli_fails_before_output(self):
        with tempfile.TemporaryDirectory()as td:
            p=Path(td);(p/'wrong.syx').write_bytes(ARGS.stock.read_bytes()[:-1]+b'\0')
            r=subprocess.run([sys.executable,'-I',str(b.HERE/'build.py'),'--stock',str(p/'wrong.syx'),'--assembler',str(ARGS.assembler),'--out',str(p/'out')],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0);self.assertIn('official input SysEx differs',r.stderr);self.assertFalse((p/'out').exists())
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--stock',type=Path,required=True);ap.add_argument('--assembler',type=Path,required=True);ap.add_argument('--arm-image',type=Path);ARGS=ap.parse_args();unittest.main(argv=[sys.argv[0]])
