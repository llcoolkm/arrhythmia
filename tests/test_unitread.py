"""tools/unitread.py against a fake unit that answers the content transfer as the tool's docstring describes it.

    python3 -I tests/test_unitread.py [test names]

FakeUnit holds packs in memory and has a clock (ms) that advances by the bytes on the wire, so the stream budget is
tested too. Optional: MIDIEXPORT_TRANSCRIPTS=<dir> replays every transcript `read-*.json` there (recorded on the
unit with --transcript) and, when MIDIEXPORT_PACKS names the same pack folders, compares the ZIP with the folder export.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import crsong as cs  # noqa: E402
import midiexport as mx  # noqa: E402
import test_midiexport as tm  # noqa: E402
import unitread as ur  # noqa: E402

OPTS = dict(pitched=set(), base=36, locks=True, scenes=None, loops=1)


def nib(v):
    return bytes((v >> s) & 0xF for s in range(28, -4, -4))


def pack7(data):
    out = bytearray()
    for i in range(0, len(data), 7):
        g = data[i:i + 7]
        out.append(sum(((c >> 7) & 1) << j for j, c in enumerate(g)))
        out += bytes(c & 0x7F for c in g)
    return bytes(out)


class FakeUnit:
    """packs = {slot: (pack name, {type: {index: (file name, bytes)}})}. faults: "busy" ('@' refused), "silent"
    (no reply to '@'), "short" (truncated ACK to '@'), "status-before" / "status-after" (a fault in the status reply
    before / after '@'), ("drop", t, s, i, k) (data frame k of that item lost), ("crc", t, s, i) (wrong CRC in the end
    frame), ("nak", t, s, i) (NAK 7 to its size query). heard: what the idle listen reports. rate: bytes per ms on the wire."""

    def __init__(self, packs, build=7339, faults=(), heard=(), rate=218.0):
        self.packs, self.build, self.faults, self.heard, self.rate = packs, build, set(faults), list(heard), rate
        self.t, self.queue, self.sent, self.session = 0.0, [], [], False

    def now(self):
        return self.t

    def idle(self, seconds):
        self.t += seconds * 1000
        return self.heard

    def close(self):
        pass

    def recv(self, prefix, timeout):
        while self.queue:
            t, m = self.queue.pop(0)
            self.t = max(self.t, t)
            if m.startswith(prefix):
                return m
        self.t += timeout * 1000
        return None

    def send(self, msg):
        self.sent.append(msg)
        self.t += len(msg) / self.rate
        t = self.t + 1.0
        for r in self.reply(msg):
            t += len(r) / self.rate
            self.queue.append((t, r))

    def status(self, when):
        st = bytearray(14)
        if f"status-{when}" in self.faults:
            st[0] = 1
        return [ur.STATUS_PREFIX + bytes(st) + b"\xF7"]

    def reply(self, msg):
        P = ur.PREFIX
        if msg == ur.VERSION_REQ:
            b = self.build
            return [bytes.fromhex("F0 00 20 29 00 70 00 03 00 25 3F 01 01") + bytes([b >> 14, b >> 7 & 0x7F, b & 0x7F])
                    + bytes.fromhex("40 02 F7")]
        if msg == ur.STATUS_REQ:
            return self.status("after" if self.session else "before") if self.build in ur.STATUS_BUILDS else []
        body = msg[len(P):-1]
        cmd = body[0]
        if cmd == ur.BEGIN:
            if "busy" in self.faults:
                return [ur.BEGIN_BUSY]
            self.session = True
            return [] if "silent" in self.faults else [ur.SESSION_ACK[:-3] + b"\xF7"] if "short" in self.faults \
                else [ur.SESSION_ACK]
        if cmd == ur.END:
            self.session = False
            return [ur.SESSION_ACK]
        if not self.session:
            return []
        if body == bytes([ur.LIST, 1]):
            return [P + bytes([ur.LIST, 1, 3]) + b"\xF7"]
        if body == bytes([ur.LIST, 2]):
            return [P + bytes([ur.LIST, 2, len(self.packs)]) + b"\xF7"] + [
                P + bytes([ur.ENTRY, 2, s]) + n.encode("latin1") + b"\xF7" for s, (n, _) in sorted(self.packs.items())]
        if cmd == ur.LIST:
            t, s = body[1], body[2]
            items = self.packs.get(s, ("", {}))[1].get(t, {})
            return [P + bytes([ur.LIST, t, s, len(items) & 0x7F, len(items) >> 7]) + b"\xF7"] + [
                P + bytes([ur.ENTRY, t, s, i]) + n.encode("latin1") + b"\xF7" for i, (n, _) in sorted(items.items())]
        t, s, i = (body[1], body[2], body[3]) if cmd == ur.SIZE else (body[9], body[10], body[11])
        item = self.packs.get(s, ("", {}))[1].get(t, {}).get(i)
        tsi = bytes([t, s, i])
        if cmd == ur.SIZE:
            return [P + bytes([ur.SIZE]) + tsi + nib(len(item[1])) + b"\xF7"] if item and ("nak", t, s, i) not in \
                self.faults else \
                [P + bytes([ur.NAK]) + bytes(8) + tsi + b"\x07\xF7"]
        if item is None:
            return [P + bytes([ur.ABSENT]) + tsi + b"\xF7"]
        data = item[1]
        out = [P + bytes([ur.START]) + bytes(8) + tsi + b"\x01" + nib(len(data)) + b"\xF7"]
        n = 0
        for off in range(0, len(data), ur.CHUNK):
            n += 1
            if ("drop", t, s, i, n) not in self.faults:
                out.append(P + bytes([ur.DATA]) + nib(n) + tsi + pack7(data[off:off + ur.CHUNK]) + b"\xF7")
        crc = zlib.crc32(data) ^ (1 if ("crc", t, s, i) in self.faults else 0)
        return out + [P + bytes([ur.FINISH]) + nib(n + 1) + tsi + nib(crc) + b"\xF7"]


class Inject:
    """A link wrapper that fails like a broken adapter: recv call number `recv_fail` (from 1) raises OSError, or the
    send of a frame whose command byte is `send_fail` raises OSError (after it may have reached the unit)."""

    def __init__(self, link, recv_fail=None, send_fail=None):
        self.link, self.recv_fail, self.send_fail, self.n, self.sent_after = link, recv_fail, send_fail, 0, None

    def now(self):
        return self.link.now()

    def idle(self, s):
        return self.link.idle(s)

    def close(self):
        pass

    def send(self, msg):
        if self.sent_after is not None:
            self.sent_after += 1
        if self.send_fail is not None and msg.startswith(ur.PREFIX) and msg[7] == self.send_fail:
            self.link.send(msg)
            self.sent_after = 0
            raise OSError("adapter: write failed")
        self.link.send(msg)

    def recv(self, prefix, timeout):
        self.n += 1
        if self.n == self.recv_fail:
            self.sent_after = 0
            raise OSError("adapter: read failed")
        return self.link.recv(prefix, timeout)


def a_pack():
    """Pack 3 ('02_Test'): two projects; PCM slots 0, 3, 40 (3 is 50 000 bytes: seven data frames)."""
    d = tm.project(name="UNIT")
    d[cs.SLOT_AT:cs.SLOT_AT + 2] = bytes([0, 3])
    tm.step(d, 0, 0, 0)
    tm.step(d, 0, 0, 4, flip=40)
    tm.step(d, 1, 0, 2)
    tm.step(d, 1, 0, 6, flip=9)                            # slot 9: not in the pack
    pcm = {0: ("00_Kick.wav", b"RIFF" + bytes(range(256)) * 3), 3: ("03_Snare 2.wav", bytes(range(7, 256)) * 200),
           40: ("40_Perc.wav", b"RIFF40"), 41: ("41_Unused.wav", b"never read")}
    return {0: ("00_FACTORY", {3: {}, 5: {}}),
            3: ("02_Test", {3: {1: ("01_UNIT.ncs", bytes(d)), 5: ("05_Other.ncs", bytes(tm.project(name="O")))},
                            5: {i: (n, b) for i, (n, b) in pcm.items()}})}


def folder_zip(packs, slot, idx, opts=OPTS):
    _, items = packs[slot]
    name, data = items[3][idx]
    pcm = {n: b for n, b in items[5].values()}
    return mx.export(cs.Project(data), name, list(pcm), lambda n: pcm[n], **opts)[3]


def sent_kinds(fake):
    return [m.hex()[14:] if m.startswith(ur.PREFIX) else m.hex() for m in fake.sent]


def test_list_frames_and_result():
    fake = FakeUnit(a_pack())
    r = ur.list_projects(ur.Unit(fake))
    assert r == {0: {"pack": "00_FACTORY", "sessions": {}},
                 3: {"pack": "02_Test", "sessions": {1: "01_UNIT.ncs", 5: "05_Other.ncs"}}}, r
    assert [m.hex() for m in fake.sent] == [
        "f00020290070f7", "f000202900600df7", "f000202901630340f7", "f000202900600df7", "f00020290163030b01f7",
        "f00020290163030b02f7", "f00020290163030b0300f7", "f00020290163030b0303f7", "f000202900600df7",
        "f000202901630341f7", "f000202900600df7"]
    assert all(ur.frame_allowed(m) for m in fake.sent) and not fake.session


def test_read_equals_folder_export():
    packs = a_pack()
    fake = FakeUnit(packs)
    tr = ur.Transcript()
    r = ur.read_project(ur.Unit(fake, tr), 3, 1, OPTS)
    assert r["name"] == "01_UNIT.ncs" and r["pack"] == "02_Test" and r["image"] == packs[3][1][3][1][1]
    assert sorted(r["samples"]) == ["00_Kick.wav", "03_Snare 2.wav", "40_Perc.wav"], sorted(r["samples"])
    z = ur.export_zip(r, OPTS)
    assert z == folder_zip(packs, 3, 1)
    assert zipfile.ZipFile(io.BytesIO(z)).namelist() == [
        "01_UNIT.mid", "MAPPING.md", "mapping.json", "samples/slot00_Kick.wav", "samples/slot03_Snare 2.wav",
        "samples/slot40_Perc.wav"]
    kinds = sent_kinds(fake)
    assert kinds[:6] == ["f00020290070f7", "f000202900600df7", "40f7", "f000202900600df7", "0b02f7", "0b0303f7"]
    assert [k for k in kinds if k.startswith("0a")] == ["0a050300f7", "0a050303f7", "0a050328f7"], kinds
    assert kinds[-3:] == ["f000202900600df7", "41f7", "f000202900600df7"] and not fake.session
    # the transcript replays to the same frames and result, without the unit
    rep = ur.Replay(tr.frames)
    tr2 = ur.Transcript()
    r2 = ur.read_project(ur.Unit(rep, tr2), 3, 1, OPTS)
    assert r2 == r and tr2.frames == tr.frames and rep.k == len(tr.frames)


def test_allow_list_refuses_writes():
    P = ur.PREFIX
    for body in ([ur.START] + [0] * 8 + [5, 3, 0, 1] + [0] * 8,           # upload START
                 [ur.DATA] + [0] * 7 + [1, 5, 3, 0, 0],                  # data packet
                 [ur.FINISH] + [0] * 8 + [5, 3, 0] + [0] * 8,            # finish
                 [0x07, 5, 3, 0] + list(b"00_X.wav"), [0x08, 2, 3], [0x08, 5, 3, 0],   # rename, delete
                 [0x0D, 5, 3, 0], [0x09, 1, 1], [ur.LIST, 4, 3], [ur.SIZE, 7, 3, 0],
                 [ur.START] + [0] * 8 + [8, 0, 0, 2] + [0] * 8,          # globals
                 [ur.START] + [0] * 8 + [3, 32, 0, 2] + [0] * 8):        # slot 32
        assert not ur.frame_allowed(P + bytes(body) + b"\xF7"), body
    for m in (bytes.fromhex("F0 00 20 29 00 77 F7"), bytes.fromhex("F0 00 20 29 00 60 08 F7"),
              bytes.fromhex("F0 00 20 29 00 60 0F 00 00 F7"), ur.PREFIX + b"\x40\x00\xF7"):
        assert not ur.frame_allowed(m), m.hex()
    u = ur.Unit(FakeUnit(a_pack()))
    try:
        u._send(P + bytes([0x08, 2, 3]) + b"\xF7")
    except ur.Refused:
        pass
    else:
        raise AssertionError("delete sent")
    assert u.link.sent == []


def expect(exc, fn):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError(f"no {exc.__name__}")


def test_refusals_before_the_session():
    fake = FakeUnit(a_pack(), build=7221)
    assert "7221" in expect(ur.Refused, lambda: ur.list_projects(ur.Unit(fake)))
    assert fake.sent == [ur.VERSION_REQ]
    fake = FakeUnit(a_pack(), heard=[0x90, 0xFA])
    assert "90 FA" in expect(ur.Refused, lambda: ur.list_projects(ur.Unit(fake)))
    assert fake.sent == [ur.VERSION_REQ]
    fake = FakeUnit(a_pack(), faults=["status-before"])
    expect(ur.Refused, lambda: ur.list_projects(ur.Unit(fake)))
    assert fake.sent == [ur.VERSION_REQ, ur.STATUS_REQ]
    fake = FakeUnit(a_pack(), faults=["busy"])
    assert "busy" in expect(ur.Refused, lambda: ur.list_projects(ur.Unit(fake)))
    assert sent_kinds(fake) == ["f00020290070f7", "f000202900600df7", "40f7"]
    fake = FakeUnit(a_pack(), build=5706)                  # stock: no status query
    ur.list_projects(ur.Unit(fake))
    assert ur.STATUS_REQ not in fake.sent and sent_kinds(fake)[1] == "40f7"


def test_uncertain_states_send_nothing_more():
    for faults in (["silent"], ["short"], ["status-after"], [("drop", 5, 3, 3, 2)]):
        fake = FakeUnit(a_pack(), faults=faults)
        u = ur.Unit(fake)
        expect(ur.Uncertain, lambda: ur.read_project(u, 3, 1, OPTS))
        n = len(fake.sent)
        assert ur.PREFIX + b"\x41\xF7" not in fake.sent, faults
        expect(ur.Uncertain, lambda: u._send(ur.PREFIX + bytes([ur.LIST, 2]) + b"\xF7"))
        assert len(fake.sent) == n


def test_link_errors_are_uncertain_and_send_nothing_more():
    # recv calls of a read: 70, status, '@', status, 0B 02 + 2 entries, 0B 03 + 2, download header (11), first data (12)
    fake = FakeUnit(a_pack())
    link = Inject(fake, recv_fail=12)
    u = ur.Unit(link)
    expect(ur.Uncertain, lambda: ur.read_project(u, 3, 1, OPTS))
    assert link.sent_after == 0 and u.uncertain and sent_kinds(fake)[-1].startswith("01"), sent_kinds(fake)[-2:]
    assert ur.PREFIX + b"\x41\xF7" not in fake.sent
    fake = FakeUnit(a_pack())                              # '@' may have reached the unit before the write failed
    link = Inject(fake, send_fail=ur.BEGIN)
    u = ur.Unit(link)
    expect(ur.Uncertain, lambda: ur.list_projects(u))
    assert link.sent_after == 0 and sent_kinds(fake) == ["f00020290070f7", "f000202900600df7", "40f7"]
    fake = FakeUnit(a_pack())                              # a bug-class error inside the body: no cleanup either

    def boom(_u):
        raise KeyError("x")
    u = ur.Unit(fake)
    expect(ur.Uncertain, lambda: ur.run(u, boom))
    assert ur.PREFIX + b"\x41\xF7" not in fake.sent


def test_names_from_the_unit_stay_inside_the_output():
    for bad in ("../escaped", "..", "a/b", "c:\\x", " . "):
        expect(ur.Refused, lambda: ur.safe_component(bad))
    packs = a_pack()
    packs[3] = ("../escaped", packs[3][1])
    tr = ur.Transcript()
    ur.read_project(ur.Unit(FakeUnit(packs), tr), 3, 1, OPTS)      # the read itself is fine; the pack name is data
    with tempfile.TemporaryDirectory() as tmp:
        tr.save(os.path.join(tmp, "t.json"))
        raw = os.path.join(tmp, "raw")
        assert ur.main(["replay", os.path.join(tmp, "t.json"), "read", "3", "1", "--raw", raw]) == 2
        assert not os.path.exists(os.path.join(tmp, "escaped")) and not os.path.exists(raw)
    packs = a_pack()                                       # an unsafe sample name: refused, the session closes
    packs[3][1][5][3] = ("03_../../x.wav", packs[3][1][5][3][1])
    fake = FakeUnit(packs)
    assert "unsafe name" in expect(ur.Refused, lambda: ur.read_project(ur.Unit(fake), 3, 1, OPTS))
    assert sent_kinds(fake)[-2] == "41f7" and not fake.session


def test_certain_failures_close_the_session():
    for faults, unit_args, want in (([("crc", 5, 3, 3)], {}, "CRC"), ([], {"rate": 10.0}, "longer than"),
                                    ([("nak", 5, 3, 3)], {}, "NAK 7")):
        fake = FakeUnit(a_pack(), faults=faults, **unit_args)
        assert want in expect(ur.Refused, lambda: ur.read_project(ur.Unit(fake), 3, 1, OPTS))
        assert sent_kinds(fake)[-3:] == ["f000202900600df7", "41f7", "f000202900600df7"] and not fake.session
    fake = FakeUnit(a_pack())
    assert "no project 2" in expect(ur.Refused, lambda: ur.read_project(ur.Unit(fake), 3, 2, OPTS))
    assert sent_kinds(fake)[-2] == "41f7"
    bad = a_pack()
    bad[3][1][3][1] = ("01_UNIT.ncs", b"not a project")
    fake = FakeUnit(bad)
    expect(cs.ProjectError, lambda: ur.read_project(ur.Unit(fake), 3, 1, OPTS))
    assert sent_kinds(fake)[-2] == "41f7" and not fake.session


def test_budget_from_the_measured_rate():
    # the project file sets the rate; a sample is read only when its predicted stream time is within the budget
    assert ur.wire_bytes(0x2000) == 29 + 9383 + 28 and ur.wire_bytes(169092) == 193740   # measured on the unit
    big = a_pack()
    big[3][1][5][3] = ("03_Snare 2.wav", bytes(1_200_000))
    fake = FakeUnit(big)                                   # 218 B/ms: 1.37 MB on the wire = 6.3 s > 5 s
    assert "1200000 bytes" in expect(ur.Refused, lambda: ur.read_project(ur.Unit(fake), 3, 1, OPTS))
    assert not any(m.startswith(ur.PREFIX + b"\x01") and m[16] == 3 and m[18] == 3 for m in fake.sent)
    big[3][1][5][3] = ("03_Snare 2.wav", bytes(900_000))   # 4.7 s
    ur.read_project(ur.Unit(FakeUnit(big)), 3, 1, OPTS)


def test_cli_replay():
    packs = a_pack()
    tr = ur.Transcript()
    ur.read_project(ur.Unit(FakeUnit(packs), tr), 3, 1, OPTS)
    with tempfile.TemporaryDirectory() as tmp:
        tr.save(os.path.join(tmp, "t.json"), build=7339)
        out = os.path.join(tmp, "o.zip")
        assert ur.main(["replay", os.path.join(tmp, "t.json"), "read", "3", "1", "-o", out, "--raw",
                        os.path.join(tmp, "raw")]) == 0
        assert Path(out).read_bytes() == folder_zip(packs, 3, 1)
        assert Path(tmp, "raw", "02_Test", "PCM", "03_Snare 2.wav").read_bytes() == packs[3][1][5][3][1]


def test_optional_unit_transcripts():
    folder = os.environ.get("MIDIEXPORT_TRANSCRIPTS")
    if not folder:
        return "skip: MIDIEXPORT_TRANSCRIPTS not set"
    packs = [Path(p) for p in os.environ.get("MIDIEXPORT_PACKS", "").split(":") if p]
    n = 0
    for f in sorted(Path(folder).glob("read-*.json")):
        t = json.loads(f.read_text())
        slot, idx = t["slot"], t["index"]
        r = ur.read_project(ur.Unit(ur.Replay(t["frames"])), slot, idx, OPTS)
        z = ur.export_zip(r, OPTS)
        for p in packs:
            if p.name == r["pack"]:
                pcm = os.listdir(p / "PCM")
                want = mx.export(cs.Project(cs.find_image((p / "Sessions" / r["name"]).read_bytes())), r["name"],
                                 pcm, lambda x: (p / "PCM" / x).read_bytes(), **OPTS)[3]
                assert z == want, f
        n += 1
    return f"{n} transcript(s)"


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            suite.addTest(unittest.FunctionTestCase(fn, description=name))
    return suite


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and (len(sys.argv) < 2 or name in sys.argv[1:]):
            r = fn()
            print(f"ok {name}" + (f" ({r})" if isinstance(r, str) else ""))
