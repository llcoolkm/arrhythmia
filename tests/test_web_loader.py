"""Offline combined-loader contracts and Node/Chrome suites; no unit or MIDI device."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
sys.path.insert(0,str(ROOT/'tools'))
import engine_catalogue



class LoaderContracts(unittest.TestCase):
    def test_published_engine_metadata(self):
        html = (ROOT / 'web/loader.html').read_text()
        catalogue=json.loads((ROOT/'web/engines/index.json').read_text())
        rows=engine_catalogue.validate_catalogue(catalogue)
        self.assertEqual([(r['file'],r['kind'],r['version']) for r in rows],
                         [('fm.dse',1,'2.1.0'),('formant.dse',6,'1.0.0'),('rings.dse',5,'1.0.0')])
        entry=rows[0]
        legacy=re.search(r'/\* published-engines: (.*?) \*/',html)
        if legacy:
            pinned=json.loads(legacy.group(1))[0]
            self.assertEqual((pinned['name'],pinned['size'],pinned['sha256'],pinned['version']),
                             (entry['file'],entry['size'],entry['sha256'],entry['version']))
        self.assertIn("fetch('engines/index.json'",html)
        self.assertIn("fetch('engines/'+entry.file",html)

    def test_compatibility_forward(self):
        html = (ROOT / 'web/modules.html').read_text()
        self.assertIn('url=loader.html', html)
        self.assertIn("location.replace('loader.html' + location.search + location.hash)", html)
        self.assertNotIn('requestMIDIAccess', html)

    @unittest.skipUnless(NODE, 'node not found')
    def test_loader_core(self):
        self.run_node('test_web_loader.mjs')

    @unittest.skipUnless(NODE, 'node not found')
    def test_module_plans(self):
        self.run_node('test_web_loader_modules.mjs')

    @unittest.skipUnless(NODE, 'node not found')
    def test_loader_browser(self):
        self.run_node('test_web_loader_browser.mjs')

    def run_node(self, script):
        result = subprocess.run([NODE, str(ROOT / 'tests' / script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
