"""The module loader page (web/modules.html) against the host client (tools/module.py card).

    python3 -I tests/test_web_modules.py

For synthetic modules (CRM1 and CRM2; the smallest, an odd size and the largest), one card write by the page's
JavaScript core (run in node through tests/test_web_modules.mjs --dump, against a fake unit that finishes every job at
the first status poll) must send exactly the SysEx that tools/module.py Card.write sends to an equal fake unit: begin,
every piece, commit, status, verify, status, end. tools/module.py also reads the staged copy back with 7D; the page
does not send 7D (it relies on the unit's CRC-32 and module checks at commit and the read-back of the card file), so
7D requests are left out of the comparison. The node tests of the page run too. The page's list of published
modules must be the module table of docs/user/modules.md. Without node the test is skipped.
No unit and no MIDI device are used.
"""
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
sys.path.insert(0, str(ROOT / "tools"))
import build_module  # noqa: E402
import export_public  # noqa: E402
import module  # noqa: E402

NODE = shutil.which("node")
MJS = ROOT / "tests/test_web_modules.mjs"


def make_module(size, magic2=False, seed=1):
    """A synthetic module that passes build_module.check (pseudo-random body, frame entry just after the header)."""
    x, body = seed, bytearray()
    for _ in range(size):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        body.append(x >> 24)
    hdr = build_module.HDR2 if magic2 else build_module.HDR
    magic = build_module.MOD_MAGIC2 if magic2 else build_module.MOD_MAGIC
    struct.pack_into("<6I", body, 0, magic, size, 0, 0, hdr | 1, 0)
    if magic2:
        struct.pack_into("<I", body, build_module.HDR, 0)
    struct.pack_into("<I", body, 8, zlib.crc32(bytes(body)))
    blob = bytes(body)
    build_module.check(blob)
    return blob


class FakeUnit:
    """cmd 10 and 7D for module.Card: every job finishes at the first status poll (as the node --dump fake)."""
    def __init__(self):
        self.sent, self.phase, self.job, self.result, self.got, self.size, self.info = [], 0, 0, 0, 0, 0, 0
        self.staged, self.files, self.name = b"", {}, ""

    def status(self, sub, answer):
        return (module.PRE + bytes([0x10, sub, answer, self.phase, self.job, self.result, 0]) +
                module.p7(self.got, 3) + module.p7(self.size, 3) + module.p7(self.info, 5) + b"\x00\xF7")

    def xfer(self, msg, cmd):
        self.sent.append(bytes(msg))
        if cmd == 0x7D:
            addr = module.g7(msg[7:12])
            chunk = self.staged[addr - module.MOD_BASE:addr - module.MOD_BASE + 32].ljust(32, b"\0")
            return module.DUMP_PRE + msg[7:12] + b"".join(bytes((b >> 4, b & 15)) for b in chunk) + b"\xF7"
        sub = msg[7]
        if sub == 0x00:
            if self.phase == 2:
                if self.job == 1:
                    self.files[self.name] = self.staged
                elif self.job == 4:
                    f = self.files[self.name]
                    self.size, self.info = len(f), zlib.crc32(f)
                self.phase, self.result = 3, 1
        elif sub == 0x01:
            self.name = msg[8:16].rstrip(b"\0").decode()
            self.size, self.info, self.staged = module.g7(msg[16:19]), module.g7(msg[19:24]), b""
            self.phase, self.job, self.got = 1, 1, 0
        elif sub == 0x02:
            self.staged += bytes((msg[11 + 2 * i] << 4) | msg[12 + 2 * i] for i in range((len(msg) - 12) // 2))
            self.got = len(self.staged)
        elif sub == 0x03:
            self.phase, self.got = 2, 0
        elif sub == 0x07:
            self.name, self.phase, self.job, self.result = msg[8:16].rstrip(b"\0").decode(), 2, 4, 0
        elif sub == 0x7F:
            self.phase = 0
        return self.status(sub, 1)


class PublishedList(unittest.TestCase):
    """The page's published modules are the release's card files (the module table of docs/user/modules.md); the
    export (tools/export_public.py --web-modules, tools/release_packet.py step "web modules") ships exactly these."""
    def test_page_lists_the_release_modules(self):
        import re
        page = export_public.page_modules((ROOT / "web/loader.html").read_text())
        table = re.findall(r"^\| *\d+ *\| *`([0-9a-z_-]+\.mod)` *\|", (ROOT / "docs/user/modules.md").read_text(), re.M)
        self.assertEqual(sorted(page), sorted(table))
        self.assertEqual(len(page), 10)


@unittest.skipUnless(NODE, "node not found")
class PageMatchesModulePy(unittest.TestCase):
    CASES = [(26, False), (30, True), (1000, False), (1025, True), (15360, False)]

    def page_messages(self, blob, name):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.mod"
            p.write_bytes(blob)
            out = subprocess.run([NODE, str(MJS), "--dump", str(p), name], capture_output=True, text=True,
                                 timeout=120, check=True).stdout
        return [bytes.fromhex(h) for h in json.loads(out)]

    def test_same_sysex_as_module_py(self):
        for size, magic2 in self.CASES:
            with self.subTest(size=size, crm2=magic2):
                blob, name = make_module(size, magic2, seed=size), "T_%d" % size
                unit = FakeUnit()
                self.assertEqual(module.Card(unit.xfer).write(blob, name), (size, zlib.crc32(blob)))
                ref = [m for m in unit.sent if m[6] == module.CARD]
                self.assertTrue(any(m[6] == 0x7D for m in unit.sent))
                page = self.page_messages(blob, name)
                self.assertEqual(len(page), len(ref))
                for i, (a, b) in enumerate(zip(page, ref)):
                    self.assertEqual(a.hex(" "), b.hex(" "), f"message {i}")
                subs = [m[7] for m in page]
                pieces = (size + 31) // 32
                self.assertEqual(subs, [0x01] + [0x02] * pieces + [0x03, 0x00, 0x07, 0x00, 0x7F])

    def test_node_suite(self):
        r = subprocess.run([NODE, str(MJS)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
