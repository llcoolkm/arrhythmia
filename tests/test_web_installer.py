"""Release format (tools/crpatch.py) and web installer (web/index.html) checks.

    python3 -I tests/test_web_installer.py

Synthetic cases need no firmware. With firmware/circuit-rhythm-5706.syx and build/devkit-tm-6013.json present, the
6013 release is rebuilt and must equal release/devkit-tm-6013.json, reproduce the pinned output hash, match
tools/patch.py byte for byte, and match the JavaScript patcher (run in node). The 0.1.0 release (release/v0.1.0.json,
firmware 7229 + devkit 7228, the release the page embeds) is checked the same way; its rebuild needs
build/arrhythmia-firmware-7229.json and build/arrhythmia-devkit-7228.json.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import crpatch  # noqa: E402
import patch  # noqa: E402
import syx  # noqa: E402

STOCK = ROOT / "firmware/circuit-rhythm-5706.syx"
SPEC_6013 = ROOT / "build/devkit-tm-6013.json"
REL_6013 = ROOT / "release/devkit-tm-6013.json"
SPEC_6012 = ROOT / "build/devkit-abi3-6012.json"
TWO = ROOT / "tests/fixtures/two-builds.json"
REL_V010 = ROOT / "release/v0.1.0.json"
SPEC_7229 = ROOT / "build/arrhythmia-firmware-7229.json"
SPEC_7228 = ROOT / "build/arrhythmia-devkit-7228.json"
PIN_7229 = "13d983b24c30bbdb3df0b4c2203d1016a8ab85bede4528751dad7d45b9145571"
PIN_7228 = "66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d"
PIN_6013 = "23aef940c2cad2e55c0c8f00b5359b7952e2f9421cedd1d879cefed61c1e8d42"
PIN_6012 = "b19549ff200d4c173971b9b24c7d460a4475f637e5e1e848fede5cc3f5c56662"
NODE = shutil.which("node")
ALLOWED_KEYS = {"addr", "len", "expect_sha256", "expect_ff", "allow_vectors", "pieces", "note"}
PIECE_KEYS = [{"hex"}, {"fill", "len"}, {"copy", "len", "sha256"}]


def B(rel):
    return crpatch.select(rel)


def stock_windows(stock):
    return {stock[i:i + 16] for i in range(len(stock) - 15)}


def published_runs(rel, stock):
    """Independent stock scan: 16-byte windows of published hex that occur in stock, minus uniform windows."""
    sw, hits = stock_windows(stock), []
    for p in (p for b in crpatch.builds(rel) for p in b["patches"]):
        for pc in p["pieces"]:
            b = bytes.fromhex(pc.get("hex", ""))
            hits += [(p["addr"], i) for i in range(len(b) - 15) if b[i:i + 16] in sw and len(set(b[i:i + 16])) > 1]
    return hits


def check_shape(t, rel):
    t.assertIn("builds", rel)
    t.assertNotIn("output", rel)
    t.assertNotIn("patches", rel)
    for b in rel["builds"]:
        t.assertEqual(set(b), {"name", "description", "output", "patches"})
        t.assertIsInstance(b["description"], str)
        for k in ("syx_sha256", "image_sha256", "syx_size", "image_size", "crc32_mpeg2", "label", "version_digits"):
            t.assertIn(k, b["output"])
    for p in (p for b in rel["builds"] for p in b["patches"]):
        t.assertLessEqual(set(p), ALLOWED_KEYS)
        t.assertNotIn("expect", p)
        t.assertNotIn("new", p)
        t.assertTrue(("expect_sha256" in p) != bool(p.get("expect_ff")))
        for pc in p["pieces"]:
            t.assertIn(set(pc), PIECE_KEYS)
    for k in ("syx_sha256", "image_sha256", "syx_size", "image_size"):
        t.assertIn(k, rel["input"])


class Synthetic(unittest.TestCase):
    """A made-up 'stock' image; exercises build, apply, refusals and the scan without Novation bytes."""

    def setUp(self):
        img = bytearray(hashlib.sha256(bytes([i % 251 for i in range(64)])).digest() * 128)  # 4096 bytes
        img[0x1CA:0x1CC] = (5706).to_bytes(2, "little")
        img[0x1CC:0x1CE] = b"\x01\x63"
        self.stock_img = bytes(img)
        self.stock = syx.build(self.stock_img, bytes([1, 0x63, 0, 0, 0, 7, 3, 1]), bytes([0, 0, 0, 0, 7, 3, 1]))
        ours = bytes(range(200, 256)) * 3 + b"\x00" * 40 + self.stock_img[0x300:0x340] + b"\x5a" * 7
        self.spec = {"base_sha256": hashlib.sha256(self.stock_img).hexdigest(), "version_nibbles": [0, 0, 0, 7, 5, 9],
                     "patches": [
                         {"addr": "0x080101CA", "expect": self.stock_img[0x1CA:0x1CC].hex(), "new": "a017"},
                         {"addr": "0x08010400", "expect": self.stock_img[0x400:0x404].hex(), "new": "00f029fd"},
                         {"addr": "0x08011000", "expect": "ff" * len(ours), "new": ours.hex(), "note": "tail"}]}
        # A second, smaller image (no "tail" code, another label): the devkit/firmware pair in miniature.
        self.spec2 = {"base_sha256": self.spec["base_sha256"], "version_nibbles": [0, 0, 0, 7, 5, 8],
                      "description": "second image",
                      "patches": [{"addr": "0x080101CA", "expect": self.stock_img[0x1CA:0x1CC].hex(), "new": "9f17"},
                                  {"addr": "0x08010400", "expect": self.stock_img[0x400:0x404].hex(),
                                   "new": "00f029fd"}]}

    def two(self):
        rel = crpatch.build(self.spec, self.stock, "two", "player image")
        crpatch.add_build(rel, crpatch.build_one(self.spec2, self.stock, "devkit", "developer image"), self.stock)
        rel["builds"][0]["name"] = "firmware"
        return rel

    def test_build_apply_matches_patch_py(self):
        rel = crpatch.build(self.spec, self.stock, "synthetic")
        check_shape(self, rel)
        out, image = crpatch.apply(rel, self.stock)
        self.assertEqual(B(rel)["output"]["label"], 6048)
        tail = B(rel)["patches"][2]["pieces"]
        self.assertTrue(any("copy" in pc for pc in tail) and any("fill" in pc for pc in tail))
        self.assertEqual(published_runs(rel, self.stock_img), [])
        with tempfile.TemporaryDirectory() as d:
            base = Path(d, "stock.syx")
            base.write_bytes(self.stock)
            spec = dict(self.spec, base_syx=str(base))
            Path(d, "spec.json").write_text(json.dumps(spec))
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                patch.apply(str(Path(d, "spec.json")), str(Path(d, "out")))
            self.assertEqual(Path(d, "out.syx").read_bytes(), out)

    def test_two_builds(self):
        rel = self.two()
        check_shape(self, rel)
        self.assertEqual([b["name"] for b in rel["builds"]], ["firmware", "devkit"])
        self.assertEqual([b["output"]["label"] for b in rel["builds"]], [6048, 6047])
        self.assertEqual(crpatch.select(rel)["name"], "firmware")
        for b in rel["builds"]:
            out, _ = crpatch.apply(rel, self.stock, b["name"])
            self.assertEqual(hashlib.sha256(out).hexdigest(), b["output"]["syx_sha256"])
        a, _ = crpatch.apply(rel, self.stock, "firmware")
        d, _ = crpatch.apply(rel, self.stock, "devkit")
        self.assertNotEqual(a, d)
        self.assertEqual(crpatch.apply(rel, self.stock)[0], a)
        with self.assertRaisesRegex(crpatch.Refused, "no build named"):
            crpatch.apply(rel, self.stock, "nope")
        with self.assertRaisesRegex(crpatch.Refused, "same label"):
            crpatch.add_build(rel, crpatch.build_one(self.spec2, self.stock, "again"), self.stock)
        other = syx.build(self.stock_img[:-4], bytes([1, 0x63, 0, 0, 0, 7, 3, 1]), bytes([0, 0, 0, 0, 7, 3, 1]))
        with self.assertRaisesRegex(crpatch.Refused, "input pin"):
            crpatch.add_build(rel, rel["builds"][1], other)
        # --add replaces a build of the same name
        crpatch.add_build(rel, crpatch.build_one(self.spec2, self.stock, "devkit", "v2"), self.stock)
        self.assertEqual(len(rel["builds"]), 2)
        self.assertEqual(rel["builds"][1]["description"], "v2")
        # a legacy single-build file still reads
        legacy = {"format": "crpatch/1", "name": "legacy", "input": rel["input"],
                  "output": rel["builds"][1]["output"], "patches": rel["builds"][1]["patches"]}
        self.assertEqual(crpatch.apply(legacy, self.stock)[0], d)
        dup = json.loads(json.dumps(rel))
        dup["builds"][1]["name"] = "firmware"
        with self.assertRaisesRegex(crpatch.Refused, "unique"):
            crpatch.apply(dup, self.stock)

    @unittest.skipUnless(NODE, "node not installed")
    def test_two_builds_js_equals_python(self):
        rel = self.two()
        with tempfile.TemporaryDirectory() as d:
            Path(d, "stock.syx").write_bytes(self.stock)
            Path(d, "rel.json").write_text(crpatch.dump(rel))
            r = subprocess.run([NODE, str(ROOT / "tests/test_web_installer.mjs"), "--stock", str(Path(d, "stock.syx")),
                                "--release", str(Path(d, "rel.json")), "--outdir", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for b in rel["builds"]:
                self.assertEqual(Path(d, f"{b['name']}.syx").read_bytes(), crpatch.apply(rel, self.stock, b["name"])[0])

    def test_refusals(self):
        rel = crpatch.build(self.spec, self.stock, "synthetic")
        bad = bytearray(self.stock)
        bad[300] ^= 1
        with self.assertRaisesRegex(crpatch.Refused, "input"):
            crpatch.apply(rel, bytes(bad))
        for mutate, msg in (
                (lambda r: B(r)["patches"][1].update(expect_sha256="0" * 64), "expected hash"),
                (lambda r: B(r)["output"].update(syx_sha256="1" * 64), "output syx_sha256"),
                (lambda r: B(r)["patches"][0]["pieces"][0].update(hex="a117"), "output"),
                (lambda r: next(pc for pc in B(r)["patches"][2]["pieces"] if "copy" in pc).update(sha256="0" * 64),
                 "copy source"),
                (lambda r: B(r)["patches"].append(dict(B(r)["patches"][1])), "overlaps")):
            r = json.loads(json.dumps(rel))
            mutate(r)
            with self.assertRaisesRegex(crpatch.Refused, msg):
                crpatch.apply(r, self.stock)

    def test_scan_flags_literal_stock_runs_but_not_uniform_runs(self):
        rel = crpatch.build(self.spec, self.stock, "synthetic")
        r = json.loads(json.dumps(rel))
        B(r)["patches"][2]["pieces"].append({"hex": self.stock_img[0x500:0x511].hex()})
        self.assertEqual(len(crpatch.scan(r, self.stock_img)), 2)
        self.assertEqual(len(published_runs(r, self.stock_img)), 2)
        B(r)["patches"][2]["pieces"][-1] = {"hex": "00" * 32}
        self.assertEqual(crpatch.scan(r, self.stock_img), [])

    def test_embed(self):
        rel = crpatch.build(self.spec, self.stock, "synthetic")
        rel["description"] = "</script><b>"
        html = crpatch.embed(json.dumps(rel), Path(ROOT / "web/index.html").read_text())
        i = html.index(crpatch.BEGIN) + len(crpatch.BEGIN)
        body = html[i:html.index("</script>", i)]
        self.assertNotIn("<", body)
        self.assertEqual(json.loads(body), rel)


@unittest.skipUnless(STOCK.exists(), "firmware/circuit-rhythm-5706.syx not present")
class Release6013(unittest.TestCase):
    def setUp(self):
        self.stock = STOCK.read_bytes()
        self.rel = json.loads(REL_6013.read_text())

    def test_committed_release_reproduces_the_pin(self):
        check_shape(self, self.rel)
        out, _ = crpatch.apply(self.rel, self.stock)
        self.assertEqual(hashlib.sha256(out).hexdigest(), PIN_6013)
        self.assertEqual(B(self.rel)["output"]["syx_sha256"], PIN_6013)
        self.assertEqual([b["name"] for b in self.rel["builds"]], ["devkit-tm-6013"])
        self.assertEqual(self.rel["name"], "Arrhythmia")
        built = ROOT / "build/devkit-tm-6013.syx"
        if built.exists():
            self.assertEqual(out, built.read_bytes())

    def test_no_stock_bytes_published(self):
        image = syx.parse(self.stock)["image"]
        self.assertEqual(published_runs(self.rel, image), [])
        self.assertEqual(crpatch.scan(self.rel, image), [])
        text = REL_6013.read_text()
        self.assertNotIn('"expect"', text)
        self.assertNotIn('"new"', text)

    @unittest.skipUnless(SPEC_6013.exists(), "build/devkit-tm-6013.json not present")
    def test_rebuild_is_reproducible(self):
        rel = crpatch.build(json.loads(SPEC_6013.read_text()), self.stock, "devkit-tm-6013")
        self.assertEqual(rel["name"], "Arrhythmia")
        self.assertEqual(crpatch.dump(rel), REL_6013.read_text())

    def test_two_build_fixture_reproduces_both_pins(self):
        rel = json.loads(TWO.read_text())
        check_shape(self, rel)
        self.assertEqual([(b["name"], b["output"]["label"]) for b in rel["builds"]],
                         [("firmware", 6013), ("devkit", 6012)])
        self.assertTrue(all(b["description"] for b in rel["builds"]))
        pins = {"firmware": PIN_6013, "devkit": PIN_6012}
        for b in rel["builds"]:
            out, _ = crpatch.apply(rel, self.stock, b["name"])
            self.assertEqual(hashlib.sha256(out).hexdigest(), pins[b["name"]])
            self.assertEqual(b["output"]["syx_sha256"], pins[b["name"]])
        self.assertEqual(crpatch.select(rel)["name"], "firmware")
        self.assertEqual(rel["name"], "Arrhythmia")
        self.assertEqual(published_runs(rel, syx.parse(self.stock)["image"]), [])
        image = syx.parse(self.stock)["image"]
        self.assertEqual(crpatch.scan(rel, image), [])

    @unittest.skipUnless(SPEC_6013.exists() and SPEC_6012.exists(), "build specs not present")
    def test_two_build_fixture_is_reproducible(self):
        rel = crpatch.build(json.loads(SPEC_6013.read_text()), self.stock, "firmware",
                            "Test stand-in for the v0.1 player image (devkit-tm 6013, approved)")
        self.assertEqual(rel["name"], "Arrhythmia")
        crpatch.add_build(rel, crpatch.build_one(
            json.loads(SPEC_6012.read_text()), self.stock, "devkit",
            "Test stand-in for the v0.1 developer image (devkit-abi3 6012, meter and module SDK)"), self.stock)
        self.assertEqual(crpatch.dump(rel), TWO.read_text())

    @unittest.skipUnless(NODE, "node not installed")
    def test_two_build_fixture_js_equals_python(self):
        rel = json.loads(TWO.read_text())
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([NODE, str(ROOT / "tests/test_web_installer.mjs"), "--stock", str(STOCK),
                                "--release", str(TWO), "--outdir", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for b in rel["builds"]:
                self.assertEqual(Path(d, f"{b['name']}.syx").read_bytes(), crpatch.apply(rel, self.stock, b["name"])[0])

    def test_page_naming(self):
        """Arrhythmia in title, heading and filenames; the maker's name only in the descriptive line."""
        html = (ROOT / "web/index.html").read_text()
        self.assertIn("<title>Arrhythmia Installer</title>", html)
        self.assertIn("<h1>Arrhythmia</h1>", html)
        self.assertIn("Custom firmware for the Novation Circuit Rhythm", html)
        body = html[html.index("<body>"):html.index("<script")]
        self.assertEqual(body.count("Novation"), 1)
        self.assertEqual(body.count("Circuit Rhythm"), 1)
        self.assertIn("`${PRODUCT}-${b.name}-${b.output.label}.syx`", html)
        self.assertNotIn("logo", html.lower())

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_equals_python(self):
        py, _ = crpatch.apply(self.rel, self.stock)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "js.syx")
            r = subprocess.run([NODE, str(ROOT / "tests/test_web_installer.mjs"), "--stock", str(STOCK),
                                "--release", str(REL_6013), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(out.read_bytes(), py)
        print(r.stdout.strip().splitlines()[-1], file=sys.stderr)


@unittest.skipUnless(STOCK.exists(), "firmware/circuit-rhythm-5706.syx not present")
class ReleaseV010(unittest.TestCase):
    """release/v0.1.0.json: firmware 7229 and devkit 7228, the release the page embeds. Firmware 7229
    tested on one unit (deliberate DSP fault containment not tested); devkit 7228 not tested on hardware; each build's
    description says so. Input: stock 5706, pinned by SHA-256."""

    def setUp(self):
        self.stock = STOCK.read_bytes()
        self.rel = json.loads(REL_V010.read_text())

    def test_committed_release_reproduces_both_pins(self):
        check_shape(self, self.rel)
        self.assertEqual(self.rel["name"], "Arrhythmia 0.1.0")
        self.assertEqual(self.rel["input"]["build"], 5706)
        self.assertEqual(self.rel["input"]["syx_sha256"], hashlib.sha256(self.stock).hexdigest())
        self.assertEqual([(b["name"], b["output"]["label"]) for b in self.rel["builds"]],
                         [("firmware", 7229), ("devkit", 7228)])
        self.assertEqual(crpatch.select(self.rel)["name"], "firmware")
        pins = {"firmware": PIN_7229, "devkit": PIN_7228}
        for b in self.rel["builds"]:
            self.assertTrue(b["description"].startswith("Arrhythmia 0.1.0."))
            self.assertIn({"firmware": "fault containment not tested", "devkit": "not tested on hardware"}[b["name"]],
                          b["description"])
            out, _ = crpatch.apply(self.rel, self.stock, b["name"])
            self.assertEqual(hashlib.sha256(out).hexdigest(), pins[b["name"]])
            self.assertEqual(b["output"]["syx_sha256"], pins[b["name"]])
            built = ROOT / f"build/arrhythmia-{b['name']}-{b['output']['label']}.syx"
            if built.exists():
                self.assertEqual(out, built.read_bytes())

    def test_no_stock_bytes_published(self):
        image = syx.parse(self.stock)["image"]
        self.assertEqual(published_runs(self.rel, image), [])
        self.assertEqual(crpatch.scan(self.rel, image), [])
        text = REL_V010.read_text()
        self.assertNotIn('"expect"', text)
        self.assertNotIn('"new"', text)

    @unittest.skipUnless(SPEC_7229.exists() and SPEC_7228.exists(), "build specs not present")
    def test_rebuild_is_reproducible(self):
        fw, dk = crpatch.select(self.rel, "firmware"), crpatch.select(self.rel, "devkit")
        rel = crpatch.build(json.loads(SPEC_7229.read_text()), self.stock, "firmware", fw["description"])
        crpatch.add_build(rel, crpatch.build_one(json.loads(SPEC_7228.read_text()), self.stock, "devkit",
                                                 dk["description"]), self.stock)
        rel["name"] = "Arrhythmia 0.1.0"
        self.assertEqual(crpatch.dump(rel), REL_V010.read_text())

    def test_page_embeds_this_release(self):
        html = (ROOT / "web/index.html").read_text()
        i = html.index(crpatch.BEGIN) + len(crpatch.BEGIN)
        self.assertEqual(json.loads(html[i:html.index("</script>", i)]), self.rel)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_equals_python(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([NODE, str(ROOT / "tests/test_web_installer.mjs"), "--stock", str(STOCK),
                                "--release", str(REL_V010), "--outdir", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ok   install over a different build label", r.stdout)
            for b in self.rel["builds"]:
                self.assertEqual(Path(d, f"{b['name']}.syx").read_bytes(),
                                 crpatch.apply(self.rel, self.stock, b["name"])[0])


@unittest.skipUnless(NODE, "node not installed")
class NodeProtocol(unittest.TestCase):
    def test_fake_midi_without_firmware(self):
        r = subprocess.run([NODE, str(ROOT / "tests/test_web_installer.mjs")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
