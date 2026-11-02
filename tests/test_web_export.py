"""The MIDI export page (web/export.html) against its reference, tools/midiexport.py.

    python3 -I tests/test_web_export.py

Each fixture project is exported twice: by the page's JavaScript core in node (tests/test_web_export.mjs --jobs)
and by midiexport.export(). The ZIP files (MIDI file, MAPPING.md, mapping.json, samples) must be identical bytes.
Fixtures: synthetic projects built with the helpers of tests/test_midiexport.py (every playback rule, the chord
vectors, option variants, sample names outside ASCII, a missing sample). Optional: MIDIEXPORT_NCS=<dir> adds every
.ncs file there; MIDIEXPORT_PACKS=<dir>[:<dir>...] adds every project of those pack folders (Sessions + PCM) with
its real samples. The node tests of the page run too. Without node the test is skipped. No unit is used.

Reading from the unit: the Python client (tools/unitread.py) runs against the fake unit of tests/test_unitread.py in
every scenario (a normal list and read, each refusal and each uncertain state that a transcript can carry), and the
page's client replays each transcript in node. Outcome, the frames sent and received, and the ZIP must be identical.
Optional: MIDIEXPORT_TRANSCRIPTS=<dir> also replays the transcripts `read-*.json` recorded on the unit.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import crsong as cs  # noqa: E402
import midiexport as mx  # noqa: E402
import test_midiexport as tm  # noqa: E402
import test_unitread as tu  # noqa: E402
import unitread as ur  # noqa: E402

NODE = shutil.which("node")
MJS = ROOT / "tests/test_web_export.mjs"
SAMPLES = ["00_Kick é.wav", "03_Snare 鼓.WAV", "07_Hat 🎵.wav", "24_Perc.wav", "40_Bass.wav", "127_Last.wav",
           "notes.txt", "05_not a sample.aif"]


def rich():
    """One project with every rule: scene chain, chains, directions, rates, microsteps, flips, locks, track 8 step
    chords and in-key chords, probability, random direction, a mute, a missing sample."""
    d = tm.with_block(tm.project(name="RICH Ünï", tempo=97, swing=58),
                      tm.block(tm._rec(dict(npp=3, octave=-1)), scales=(8, 4, 0x15), chords=[(4, 7), (3, 7, 10)]))
    d[cs.SCENE_CHAIN:cs.SCENE_CHAIN + 2] = bytes([1, 2])
    sc = lambda s, t: cs.SCENE_AT + cs.SCENE_LEN * s + 4 * t      # noqa: E731
    d[sc(1, 0):sc(1, 0) + 2] = bytes([0, 1])
    d[sc(1, 7):sc(1, 7) + 2] = bytes([0, 0])
    d[sc(2, 0):sc(2, 0) + 2] = bytes([2, 2])
    d[sc(2, 2):sc(2, 2) + 2] = bytes([1, 3])
    d[cs.SLOT_AT:cs.SLOT_AT + 8] = bytes([0, 3, 40, 5, 9, 24, 127, 12])
    d[cs.MUTE_AT + 5] = 1
    tm.settings(d, 0, 1, end=7, direction=cs.PINGPONG)
    tm.settings(d, 0, 2, end=1, start=30, rate=0, direction=cs.REVERSE)
    tm.settings(d, 2, 1, rate=6)
    tm.settings(d, 2, 3, direction=cs.RANDOM, rate=7)
    for s in range(0, 16, 2):
        tm.step(d, 0, 0, s, note=0x48 + s % 5, vel=60 + s, gate=3 + s, mask=1 | (s & 6) << 2, flip=(3, 0xFF, 7, 0)[s % 4])
    tm.step(d, 0, 1, 3, gate=40)
    tm.step(d, 0, 2, 31, mask=0b111111)
    tm.step(d, 1, 0, 0, prob=2, flip=40)
    tm.step(d, 2, 1, 2, mask=0b101010, gate=1)
    tm.step(d, 2, 3, 9, note=0)
    tm.step(d, 4, 0, 0)                                    # slot 9: not in the pack
    tm.step(d, 5, 0, 4)
    tm.step(d, 6, 0, 8, note=0x60)
    for s, (n, key) in enumerate([(0x48, 1), (0x45, 2), (0x4A, 0), (0x48, 9), (0x50, 0)]):
        tm.step(d, 7, 0, 3 * s, note=n, b6=key << 1, gate=20, flip=s)
    tm.lock(d, 0, 0, 0, 0, [30, 31, 0xFF, 32, 32, 0xFF])
    tm.lock(d, 0, 0, 11, 2, [64] * 6)
    tm.lock(d, 2, 1, 1, 2, [1, 2, 3, 4, 5, 6])
    tm.lock(d, 7, 0, 5, 3, [100] + [0xFF] * 5)
    return d


def sparse():
    d = tm.project(name="SPARSE")
    tm.step(d, 0, 0, 0)
    return d


def trailing():
    """Scene chain 1..2; scene 2 plays a silent 32-step pattern: the arrangement ends in silence."""
    d = tm.project(name="TRAIL")
    d[cs.SCENE_CHAIN:cs.SCENE_CHAIN + 2] = bytes([0, 1])
    at = cs.SCENE_AT + cs.SCENE_LEN * 1
    d[at:at + 2] = bytes([1, 1])
    tm.settings(d, 0, 1, end=31)
    tm.step(d, 0, 0, 0)
    return d


def long_note():
    d = tm.project(name="LONG")
    tm.step(d, 0, 0, 15, gate=60)
    return d


def fixtures():
    """[(label, project bytes, source name, with pcm folder, opts)]."""
    out = [("rich", rich(), "05_RICH.ncs", True, {}),
           ("rich pitched/base/loops/no locks", rich(), "05_RICH.ncs", True,
            {"pitched": [0, 2], "base": 120, "loops": 3, "locks": False}),
           ("rich no pcm", rich(), "rich.NCS", False, {}),
           ("empty", tm.project(name=""), "x.bin", False, {}),
           ("embedded", b"head" + bytes(tm.project(name="E")) + b"tail", "e.ncs", False, {}),
           ("sparse", sparse(), "s.ncs", False, {}), ("trailing scene", trailing(), "t.ncs", False, {"loops": 2}),
           ("long last note", long_note(), "l.ncs", False, {})]
    for i, (f, note, _want) in enumerate(tm.CHORD_CASES):
        d = tm.with_block(tm.project(), tm.block(tm._rec(f), scales=f.get("sub")))
        tm.step(d, 7, 0, 0, note=note + 12)
        out.append((f"chord {i}", d, f"{i:02d}_C.ncs", False, {}))
    for blk in (tm.block(tm._rec(dict(npp=3)), crc_ok=False), b"TMD2" + tm.block(tm._rec({}))[4:]):
        d = tm.with_block(tm.project(), blk)
        tm.step(d, 7, 0, 0, b6=1 << 1)
        out.append(("bad block", d, "b.ncs", False, {}))
    folder = os.environ.get("MIDIEXPORT_NCS")
    if folder:
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".ncs"):
                out.append((f, Path(folder, f).read_bytes(), f, False, {}))
    for pack in filter(None, os.environ.get("MIDIEXPORT_PACKS", "").split(":")):
        ses = Path(pack, "Sessions")
        for f in sorted(os.listdir(ses)):
            if f.lower().endswith(".ncs"):
                out.append((f"{pack}/{f}", (ses / f).read_bytes(), f, Path(pack, "PCM"), {}))
    return out


def python_zip(data, source, pcm, opts):
    files = os.listdir(pcm) if pcm else []

    def read(f):
        return Path(pcm, f).read_bytes()
    proj = cs.Project(cs.find_image(data))
    return mx.export(proj, source, files, read, set(opts.get("pitched", ())), opts.get("base", mx.DRUM_BASE),
                     opts.get("locks", True), None, opts.get("loops", 1))[3]


def diff(a, b):
    za, zb = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
    if za.namelist() != zb.namelist():
        return f"names {za.namelist()} != {zb.namelist()}"
    for n in za.namelist():
        x, y = za.read(n), zb.read(n)
        if x != y:
            i = next((k for k in range(min(len(x), len(y))) if x[k] != y[k]), min(len(x), len(y)))
            return f"{n}: first difference at byte {i}: {x[max(0, i - 40):i + 40]!r} != {y[max(0, i - 40):i + 40]!r}"
    return "same members, different container bytes"


def test_page_zip_equals_python():
    if not NODE:
        return "skip: node not found"
    with tempfile.TemporaryDirectory() as tmp:
        pcm = Path(tmp, "pack", "PCM")
        pcm.mkdir(parents=True)
        for i, f in enumerate(SAMPLES):
            (pcm / f).write_bytes(b"RIFF" + bytes([i]) * (17 + i))
        jobs, want = [], []
        for k, (label, data, source, with_pcm, opts) in enumerate(fixtures()):
            ncs = Path(tmp, f"{k}.ncs")
            ncs.write_bytes(data)
            p = with_pcm if isinstance(with_pcm, Path) else (pcm if with_pcm else None)
            out = Path(tmp, f"{k}.zip")
            jobs.append({"ncs": str(ncs), "source": source, "pcm": str(p) if p else None, "opts": opts, "out": str(out)})
            want.append((label, python_zip(data, source, p, opts), out))
        Path(tmp, "jobs.json").write_text(json.dumps(jobs))
        subprocess.run([NODE, str(MJS), "--jobs", str(Path(tmp, "jobs.json"))], check=True)
        for label, py, out in want:
            js = out.read_bytes()
            assert js == py, f"{label}: {diff(js, py)}"
        z = zipfile.ZipFile(io.BytesIO(want[0][1]))
        assert z.testzip() is None and z.namelist() == [
            "05_RICH.mid", "MAPPING.md", "mapping.json", "samples/slot00_Kick é.wav", "samples/slot03_Snare 鼓.wav",
            "samples/slot07_Hat 🎵.wav", "samples/slot24_Perc.wav", "samples/slot40_Bass.wav",
            "samples/slot127_Last.wav"], z.namelist()
        m = json.loads(z.read("mapping.json"))
        assert {"slot": 9, "sample": None} in m["samples"] and m["tracks"][5]["muted"]
        assert any("random order" in w for w in m["warnings"]) and m["arrhythmia_block"] == "ok"
        return f"{len(want)} projects, identical ZIP bytes"


def unit_scenarios():
    """[(label, fake unit, what, slot, index, injected link error)]: every case a transcript carries, plus adapter
    errors injected at the same call in both clients (an idle listen that hears notes is not in a transcript:
    tests/test_web_export.mjs covers it)."""
    big = tu.a_pack()
    big[3][1][5][3] = ("03_Snare 2.wav", bytes(1_200_000))
    bad = tu.a_pack()
    bad[3][1][3][1] = ("01_UNIT.ncs", b"not a project")
    F = tu.FakeUnit
    unsafe = tu.a_pack()
    unsafe[3][1][5][3] = ("03_../../x.wav", unsafe[3][1][5][3][1])
    return [("recv error in a stream", F(tu.a_pack()), "read", 3, 1, {"recv_fail": 12}),
            ("send error on '@'", F(tu.a_pack()), "list", None, None, {"send_fail": ur.BEGIN}),
            ("recv error before '@'", F(tu.a_pack()), "list", None, None, {"recv_fail": 1}),
            ("unsafe sample name", F(unsafe), "read", 3, 1, None)] + [s + (None,) for s in [
            ("list", F(tu.a_pack()), "list", None, None), ("list stock", F(tu.a_pack(), build=5706), "list", None, None),
            ("read", F(tu.a_pack()), "read", 3, 1), ("read other", F(tu.a_pack()), "read", 3, 5),
            ("no project", F(tu.a_pack()), "read", 3, 2), ("no pack", F(tu.a_pack()), "read", 9, 0),
            ("build", F(tu.a_pack(), build=7221), "read", 3, 1), ("busy", F(tu.a_pack(), faults=["busy"]), "read", 3, 1),
            ("status before", F(tu.a_pack(), faults=["status-before"]), "read", 3, 1),
            ("status after", F(tu.a_pack(), faults=["status-after"]), "read", 3, 1),
            ("silent", F(tu.a_pack(), faults=["silent"]), "read", 3, 1),
            ("short", F(tu.a_pack(), faults=["short"]), "read", 3, 1),
            ("drop", F(tu.a_pack(), faults=[("drop", 5, 3, 3, 2)]), "read", 3, 1),
            ("crc", F(tu.a_pack(), faults=[("crc", 5, 3, 3)]), "read", 3, 1),
            ("nak", F(tu.a_pack(), faults=[("nak", 5, 3, 3)]), "read", 3, 1),
            ("slow", F(tu.a_pack(), rate=10.0), "read", 3, 1), ("budget", F(big), "read", 3, 1),
            ("bad project", F(bad), "read", 3, 1)]]


def python_unit(link, what, slot, index, inject=None):
    """(outcome, transcript frames, listing or ZIP) of the Python client."""
    tr = ur.Transcript()
    u = ur.Unit(tu.Inject(link, **inject) if inject else link, tr)
    try:
        if what == "list":
            r = ur.list_projects(u)
            res = {str(k): {"pack": v["pack"], "sessions": {str(i): n for i, n in v["sessions"].items()}}
                   for k, v in r.items()}
        else:
            res = ur.export_zip(ur.read_project(u, slot, index, tu.OPTS), tu.OPTS)
        return "ok", tr.frames, res
    except ur.Uncertain:
        return "uncertain", tr.frames, None
    except ur.Refused:
        return "refused", tr.frames, None
    except cs.ProjectError:
        return "project", tr.frames, None


def test_page_unit_read_equals_python():
    if not NODE:
        return "skip: node not found"
    js_opts = {"pitched": [], "base": 36, "loops": 1, "locks": True}
    with tempfile.TemporaryDirectory() as tmp:
        jobs, want = [], []
        cases = [(label, fake, what, slot, index, inject, None)
                 for label, fake, what, slot, index, inject in unit_scenarios()]
        folder = os.environ.get("MIDIEXPORT_TRANSCRIPTS")
        for f in sorted(Path(folder).glob("read-*.json")) if folder else []:
            t = json.loads(f.read_text())
            cases.append((f.name, ur.Replay(t["frames"]), "read", t["slot"], t["index"], None, len(t["frames"])))
        for k, (label, link, what, slot, index, inject, n) in enumerate(cases):
            outcome, frames, res = python_unit(link, what, slot, index, inject)
            assert n is None or (outcome == "ok" and len(frames) == n), (label, outcome)
            tpath = Path(tmp, f"{k}.json")
            tpath.write_text(json.dumps({"frames": frames}))
            jobs.append({"transcript": str(tpath), "what": what, "slot": slot, "index": index, "opts": js_opts,
                         "inject": inject,
                         "out": str(Path(tmp, f"{k}.zip")), "result": str(Path(tmp, f"{k}.result.json"))})
            want.append((label, outcome, frames, res))
        Path(tmp, "replay.json").write_text(json.dumps(jobs))
        subprocess.run([NODE, str(MJS), "--replay", str(Path(tmp, "replay.json"))], check=True)
        outcomes = {}
        for job, (label, outcome, frames, res) in zip(jobs, want):
            got = json.loads(Path(job["result"]).read_text())
            assert got["outcome"] == outcome, (label, got["outcome"], outcome)
            assert got["frames"] == frames, (label, len(got["frames"]), len(frames))
            assert got["consumed"] == len(frames), label
            if outcome == "ok" and job["what"] == "read":
                js = Path(job["out"]).read_bytes()
                assert js == res, f"{label}: {diff(js, res)}"
            elif outcome == "ok":
                assert got["listing"] == res, label
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        assert outcomes["uncertain"] == 6 and outcomes["refused"] == 11, outcomes
        return f"{len(jobs)} transcripts, same outcome and frames; {outcomes}"


def test_node_unit_tests():
    if not NODE:
        return "skip: node not found"
    r = subprocess.run([NODE, str(MJS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_page_has_no_network_or_external_code():
    html = (ROOT / "web/export.html").read_text()
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "<script src", "http://", "import(", "localStorage",
                "sendBeacon"):
        assert bad not in html, bad
    assert html.count("requestMIDIAccess(") == 1                # the unit read only
    assert html.count("/* EXCORE BEGIN */") == 1 and html.count("/* EXCORE END */") == 1


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            suite.addTest(unittest.FunctionTestCase(fn, description=name))
    return suite


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            r = fn()
            print(f"ok {name}" + (f" ({r})" if isinstance(r, str) else ""))
