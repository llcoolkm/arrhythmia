"""Offline combined-loader contracts and Node/Chrome suites; no unit or MIDI device."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


class LoaderContracts(unittest.TestCase):
    def test_published_engine_metadata(self):
        html = (ROOT / 'web/loader.html').read_text()
        engines = json.loads(re.search(r'/\* published-engines: (.*?) \*/', html).group(1))
        self.assertEqual(engines, [{
            'name': 'fm.dse', 'size': 15346, 'crc32': '0b9611b1',
            'sha256': '9b79dfe90c4208a8208ffc04f5d5aa80bc3787584524d17a7ec3deaf5629ddae',
            'version': '2.0.0',
        }])
        self.assertIn("fetch('engines/fm.dse'", html)

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
