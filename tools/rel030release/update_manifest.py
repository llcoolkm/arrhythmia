"""Refresh reviewed public rebuild source hashes; byte pins in the builder stay fixed."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
names = {'tools/build_lightshow.py', 'tools/farblob.py', 'tools/crbc.py', 'tools/patch.py',
         'tools/crpatch.py', 'tools/syx.py', 'tools/rel030_release.py', 'release/v0.2.2.json'}
pending = ['patches/src/arrhythmia.c']
while pending:
    name = pending.pop()
    if name in names:
        continue
    names.add(name)
    pending.extend(str(Path(name).parent / child) for child in
                   re.findall(r'^\s*#\s*include\s+"([^"]+)"', (ROOT / name).read_text(), re.M))
manifest = dict(format='rel030-public-source/1', files={
    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)})
(ROOT / 'tools/rel030release/source-manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
