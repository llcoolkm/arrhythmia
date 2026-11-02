"""tools/midiexport.py and tools/smf.py on synthetic projects built here. No device access, no Novation data.

    uv run python -I tests/test_midiexport.py [test_name ...]

Optional: MIDIEXPORT_NCS=<dir> also exports every .ncs file in that folder and checks each output parses back.
"""
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import crsong as cs  # noqa: E402
import midiexport as mx  # noqa: E402
import smf  # noqa: E402

T = mx.TICKS                                   # ticks per pulse


# --- synthetic projects ----------------------------------------------------------------------------------------------

def project(name="TEST", tempo=120, swing=50):
    """A project image with every pattern empty: 16 steps, 1/16, forward; no locks; chains and scenes at pattern 1."""
    d = bytearray(cs.SIZE)
    d[0:12] = b"RUSE" + struct.pack("<II", cs.SIZE, 1)
    d[0x10:0x30] = name.encode().ljust(32)
    d[0x34], d[0x35] = tempo, swing
    for t in range(8):
        d[cs.SLOT_AT + t] = 2 * t
        for p in range(8):
            o = cs.EL_AT + t * cs.TRACK_STRIDE + p * cs.EL_LEN
            for s in range(32):
                d[o + 8 * s:o + 8 * s + 8] = bytes([0, 6, 0, 0, 7, 0xFF, 0, 0])
            d[o + 0x100:o + 0x104] = bytes([15, 0, 3, 0])
            d[o + cs.LOCK_AT:o + cs.LOCK_AT + cs.LANES * cs.LANE_LEN] = b"\xFF" * (cs.LANES * cs.LANE_LEN)
    return d


def step(d, t, p, s, note=0x48, gate=6, mask=1, vel=100, prob=7, flip=0xFF, b6=0):
    o = cs.EL_AT + t * cs.TRACK_STRIDE + p * cs.EL_LEN + 8 * s
    d[o:o + 8] = bytes([note, gate, mask, vel, prob, flip, b6, 0])


def settings(d, t, p, end=15, start=0, rate=3, direction=0):
    o = cs.EL_AT + t * cs.TRACK_STRIDE + p * cs.EL_LEN + 0x100
    d[o:o + 4] = bytes([end, start, rate, direction])


def lock(d, t, p, lane, s, values):
    o = cs.EL_AT + t * cs.TRACK_STRIDE + p * cs.EL_LEN + cs.LOCK_AT + lane * cs.LANE_LEN + 6 * s
    d[o:o + 6] = bytes(values)


def block(rec7=None, scales=None, chords=None, crc_ok=True):
    """A TMD1 block: rec7 {byte: value} (version byte 1 added), scales (scale, root, bits), chords [offset tuples]."""
    b = bytearray(cs.EXT_LEN)
    b[0:8] = b"TMD1" + bytes([1, 0]) + struct.pack("<H", cs.EXT_LEN)
    if rec7 is not None:
        b[cs.REC7_AT + 1] = 1
        for k, v in rec7.items():
            b[cs.REC7_AT + k] = v & 0xFF
    if scales is not None:
        b[cs.SCALES_AT:cs.SCALES_AT + 6] = b"SC\x01" + bytes(scales)
    if chords is not None:
        tab = b"CH\x01" + bytes([len(chords)]) + b"".join(bytes(list(c) + [0] * (3 - len(c))) for c in chords)
        b[cs.CHORDS_AT:cs.CHORDS_AT + len(tab)] = tab
    crc = zlib.crc32(b[:cs.EXT_LEN - 4]) ^ (0 if crc_ok else 1)
    b[cs.EXT_LEN - 4:] = struct.pack("<I", crc)
    return bytes(b)


def with_block(d, blk):
    d[cs.EXT_AT:cs.EXT_AT + cs.EXT_LEN] = blk
    return d


def export(d, **kw):
    data, mapping = mx.build(cs.Project(bytes(d)), **kw)
    fmt, ppq, tracks = smf.read_smf(data)
    assert fmt == 1 and ppq == 96 and len(tracks) == 9
    return data, mapping, tracks


def notes_of(tracks, t):
    """[(start pulse, length pulses, note, vel)] of unit track t (0-based)."""
    out = []
    for start, length, ch, note, vel in smf.notes(tracks[t + 1]):
        assert ch == t and start % T == 0 and length % T == 0
        out.append((start // T, length // T, note, vel))
    return out


def ccs_of(tracks, t):
    return [(tick // T, ev[2], ev[3]) for tick, ev in tracks[t + 1] if ev[0] == "cc"]


# --- SMF writer and reader -------------------------------------------------------------------------------------------

def test_vlq_known_values():
    for n, want in ((0, "00"), (0x40, "40"), (0x7F, "7f"), (0x80, "8100"), (0x2000, "c000"), (0x3FFF, "ff7f"),
                    (0x4000, "818000"), (0x1FFFFF, "ffff7f"), (0x200000, "81808000"), (0x0FFFFFFF, "ffffff7f")):
        assert smf.vlq(n).hex() == want, (n, smf.vlq(n).hex())
        assert smf._read_vlq(smf.vlq(n), 0) == (n, len(want) // 2)
    for bad in (-1, 0x10000000):
        try:
            smf.vlq(bad)
        except smf.SMFError:
            continue
        raise AssertionError(bad)


def test_smf_byte_exact_small_file():
    tr = smf.Track("A")
    tr.note(0, 96, 0, 60, 100)
    want = (b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x00\x60"
            b"MTrk\x00\x00\x00\x11"
            b"\x00\xff\x03\x01A"                # track name "A"
            b"\x00\x90\x3c\x64"                 # note on C4, velocity 100
            b"\x60\x80\x3c\x00"                 # 96 ticks later: note off
            b"\x00\xff\x2f\x00")                # end of track
    assert smf.write_smf([tr], 96) == want


def test_smf_same_tick_order():
    tr = smf.Track()
    tr.note_on(10, 0, 60, 1)
    tr.cc(10, 0, 21, 5)
    tr.note_off(10, 0, 62)
    tr.text(10, "x")
    evs = [e for _t, e in smf.read_smf(smf.write_smf([tr], 96))[2][0]]
    assert [e[0] for e in evs] == ["meta", "note_off", "cc", "note_on", "meta"], evs


def test_smf_reader_running_status_and_errors():
    body = (b"\x00\x90\x3c\x40" b"\x10\x3e\x40"      # running status note on
            b"\x10\x3c\x00" b"\x00\x3e\x00"          # running status note on with velocity 0 = note off
            b"\x00\xb1\x15\x7f" b"\x00\xc1\x05"      # CC, program change
            b"\x00\xff\x2f\x00")
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(body)) + body
    fmt, ppq, (tr,) = smf.read_smf(data)
    assert (fmt, ppq) == (0, 480)
    assert [e for _t, e in tr][:4] == [("note_on", 0, 60, 64), ("note_on", 0, 62, 64), ("note_off", 0, 60, 0),
                                       ("note_off", 0, 62, 0)]
    assert tr[4] == (32, ("cc", 1, 21, 127)) and tr[5][1] == ("other", 0xC1, b"\x05")
    assert smf.notes(tr) == [(0, 32, 0, 60, 64), (16, 16, 0, 62, 64)]
    for bad in (b"RIFF" + data[4:], data[:-1], data[:23] + b"\x3c" + data[24:]):   # no status byte
        try:
            smf.read_smf(bad)
        except smf.SMFError:
            continue
        raise AssertionError(bad[:24])


def test_export_byte_exact_one_note():
    """A whole exported file, built by hand: one note on track 1, no Arrhythmia block, no pack folder."""
    d = project(name="P", tempo=120, swing=55)
    step(d, 0, 0, 2, note=0x48, gate=12, vel=90)          # step 3, 2 steps long
    data, mapping, _ = export(d)

    def trk(body, end=b"\x83\x00"):                       # End of Track at the arrangement end, tick 384
        body += end + b"\xff\x2f\x00"
        return b"MTrk" + struct.pack(">I", len(body)) + body

    head = (b"\x00\xff\x03\x01P" b"\x00\xff\x51\x03\x07\xa1\x20" b"\x00\xff\x58\x04\x04\x02\x18\x08"
            b"\x00\xff\x01\x08swing 55")
    t1 = b"\x00\xff\x03\x02T1" + b"\x30\x90\x24\x5a" + b"\x30\x80\x24\x00"   # tick 48 note 36, off at 96
    rest = [trk(b"\x00\xff\x03" + bytes([len(n)]) + n) for n in (b"T2", b"T3", b"T4", b"T5", b"T6", b"T7", b"T8 Synth")]
    want = b"MThd" + struct.pack(">IHHH", 6, 1, 9, 96) + trk(head) + trk(t1, b"\x82\x20") + b"".join(rest)   # 96 + 288
    assert data == want
    assert mapping["tracks"][0]["notes"] == [{"note": 36, "slot": 0}] and mapping["length_ticks"] == 16 * 6 * T


def end_ticks(data):
    return [trk[-1][0] for trk in smf.read_smf(data)[2]]


def test_end_of_track_at_arrangement_end():
    """Every track ends at the arrangement's end (trailing silence kept) or at a later note-off."""
    d = project(name="SPARSE")
    step(d, 0, 0, 0, gate=6)                              # one note on step 1 of track 1
    data, m, _ = export(d)
    assert m["length_ticks"] == 384 and end_ticks(data) == [384] * 9
    data, m, _ = export(project(name=""))                  # nothing plays
    assert end_ticks(data) == [m["length_ticks"]] * 9 and m["length_ticks"] > 0
    d = project(name="TRAIL")                              # scene chain 1..2; scene 2 is silent and longer
    d[cs.SCENE_CHAIN:cs.SCENE_CHAIN + 2] = bytes([0, 1])
    sc = lambda s, t: cs.SCENE_AT + cs.SCENE_LEN * s + 4 * t      # noqa: E731
    d[sc(1, 0):sc(1, 0) + 2] = bytes([1, 1])
    settings(d, 0, 1, end=31)
    step(d, 0, 0, 0)
    data, m, _ = export(d)
    assert m["length_ticks"] == 384 + 768 and end_ticks(data) == [1152] * 9, (m["length_ticks"], end_ticks(data))
    d = project(name="LONG")                               # the last step's note-off lies past the end
    step(d, 0, 0, 15, gate=60)
    data, m, _ = export(d)
    ends = end_ticks(data)
    off = max(t for t, e in smf.read_smf(data)[2][1] if e[0] != "meta")      # track 1's last channel event
    assert ends[1] == max(384, off) and ends[0] == 384 and ends[2:] == [384] * 7, (ends, off)
    return f"sparse, empty, trailing scene, long note: ends {ends}"


# --- decoding and playback rules -------------------------------------------------------------------------------------

def test_microstep_pulse_table():
    assert cs.MS_PULSE == ((0, 0, 0, 1, 1, 1), (0, 0, 1, 1, 2, 2), (0, 0, 1, 2, 2, 3), (0, 1, 2, 3, 4, 5),
                           (0, 2, 3, 4, 6, 7), (0, 2, 4, 6, 8, 10), (0, 2, 5, 8, 10, 13), (0, 4, 8, 12, 16, 20))


def test_microsteps_gate_and_rates():
    d = project()
    step(d, 0, 0, 0, mask=0b100101, gate=3)                # microsteps 0, 2, 5 at 1/16: pulses 0, 2, 5
    step(d, 0, 0, 4, gate=150)                             # a 25-step tie
    assert notes_of(export(d)[2], 0) == [(0, 2, 36, 100), (2, 3, 36, 100), (5, 3, 36, 100), (24, 150, 36, 100)]
    d = project()
    settings(d, 1, 0, rate=0)                              # 2 pulses per step: microsteps 0..2 share pulse 0
    step(d, 1, 0, 1, mask=0b000111, gate=6)
    assert notes_of(export(d)[2], 1) == [(2, 2, 36, 100), (34, 2, 36, 100), (66, 2, 36, 100)]   # chain loops
    d = project()
    settings(d, 2, 0, rate=6)                              # 16 pulses per step
    step(d, 2, 0, 1, mask=0b001000, gate=1)                # microstep 3 at pulse 8; gate 16 * 1 / 6 = 2 pulses
    _, m, tr = export(d)
    assert notes_of(tr, 2) == [(24, 2, 36, 100)] and m["length_ticks"] == 256 * T


def test_step_order_and_wrap():
    assert cs.step_order(15, 0, cs.FORWARD) == list(range(16))
    assert cs.step_order(15, 0, cs.REVERSE) == list(range(15, -1, -1))
    assert cs.step_order(3, 0, cs.PINGPONG) == [0, 1, 2, 3, 3, 2, 1, 0]
    assert cs.step_order(1, 30, cs.FORWARD) == [30, 31, 0, 1]
    assert cs.step_order(1, 30, cs.PINGPONG) == [30, 31, 0, 1, 1, 0, 31, 30]
    assert cs.pattern_length(1, 30) == 4
    d = project()
    settings(d, 0, 0, end=3, direction=cs.PINGPONG)       # 8 positions of 6 pulses
    step(d, 0, 0, 3)
    settings(d, 0, 1, end=3, direction=cs.REVERSE)
    step(d, 0, 1, 3)
    d[cs.CHAIN_AT:cs.CHAIN_AT + 2] = bytes([0, 1])
    _, m, tr = export(d)
    assert [n[0] for n in notes_of(tr, 0)] == [18, 24, 48, 90] and m["length_ticks"] == 96 * T   # 72-pulse chain loops


def test_scene_chain_flattening():
    d = project()
    d[cs.SCENE_CHAIN:cs.SCENE_CHAIN + 2] = bytes([2, 3])           # scenes 3 and 4
    sc = lambda s, t: cs.SCENE_AT + cs.SCENE_LEN * s + 4 * t      # noqa: E731
    d[sc(2, 0):sc(2, 0) + 2] = bytes([0, 1])                        # scene 3: track 1 patterns 1-2 (32 steps)
    d[sc(2, 1):sc(2, 1) + 2] = bytes([2, 2])                        # track 2 pattern 3 (16 steps): loops twice
    d[sc(3, 0):sc(3, 0) + 2] = bytes([3, 3])                        # scene 4: track 1 pattern 4 only
    settings(d, 0, 3, end=7)                                        # 8 steps: track 2's 16 steps set the length
    d[sc(3, 1):sc(3, 1) + 2] = bytes([2, 2])
    for p in range(4):
        step(d, 0, p, 0, note=0x48 + p)
    step(d, 1, 2, 0)
    _, m, tr = export(d, pitched={0})
    assert [(a["scene"], a["start_tick"] // T, a["length_ticks"] // T) for a in m["arrangement"]] == \
        [(3, 0, 192), (4, 192, 96)]
    assert [(n[0], n[2]) for n in notes_of(tr, 0)] == [(0, 60), (96, 61), (192, 63), (240, 63)]
    assert [n[0] for n in notes_of(tr, 1)] == [0, 96, 192]
    markers = [(t // T, e[2]) for t, e in tr[0] if e[0] == "meta" and e[1] == 0x06]
    assert markers == [(0, b"Scene 3"), (192, b"Scene 4")]
    _, m2, _ = export(d, scenes=(3, 3))
    assert [a["scene"] for a in m2["arrangement"]] == [4]
    d[cs.SCENE_CHAIN:cs.SCENE_CHAIN + 2] = bytes([3, 3])            # one scene: the per-track chains, not scene 4
    _, m3, _ = export(d)
    assert m3["arrangement"] == [{"scene": None, "start_tick": 0, "length_ticks": 96 * T,
                                  "patterns": [[1, 1]] * 8}]
    _, m4, tr4 = export(d, loops=3)
    assert m4["length_ticks"] == 3 * 96 * T and [n[0] for n in notes_of(tr4, 0)] == [0, 96, 192]


def test_drum_notes_flips_and_pitched():
    d = project()
    d[cs.SLOT_AT] = 40
    step(d, 0, 0, 0)                                  # the track's slot 40
    step(d, 0, 0, 1, flip=7)
    step(d, 0, 0, 2, flip=127, note=0x4A)             # pitch shift lost in drum mode
    step(d, 0, 0, 3)
    _, m, tr = export(d)
    assert [n[2] for n in notes_of(tr, 0)] == [37, 36, 38, 37]
    assert m["tracks"][0]["notes"] == [{"note": 36, "slot": 7}, {"note": 37, "slot": 40}, {"note": 38, "slot": 127}]
    _, m, tr = export(d, base=126)                    # 3 slots from 126 do not fit: the base moves down to 125
    assert [n[2] for n in notes_of(tr, 0)] == [126, 125, 127, 126]
    _, m, tr = export(d, pitched={0})
    assert [n[2] for n in notes_of(tr, 0)] == [60, 60, 62, 60] and m["tracks"][0]["mode"] == "pitched"
    step(d, 0, 0, 3, note=0)                          # note 0 plays as 0x30
    _, _, tr = export(d, pitched={0})
    assert notes_of(tr, 0)[3][2] == 36


def test_retrigger_and_overlap():
    d = project()
    step(d, 0, 0, 0, gate=24)                         # 4 steps long
    step(d, 0, 0, 1, gate=6)                          # same note: ends the first at pulse 6
    step(d, 0, 0, 4, gate=24, note=0x48)
    step(d, 0, 0, 5, gate=6, note=0x4C)               # another pitch, same slot: unit keeps both; MIDI note cut
    _, _, tr = export(d)
    assert notes_of(tr, 0) == [(0, 6, 36, 100), (6, 6, 36, 100), (24, 6, 36, 100), (30, 6, 36, 100)]
    _, _, tr = export(d, pitched={0})
    assert notes_of(tr, 0) == [(0, 6, 60, 100), (6, 6, 60, 100), (24, 24, 60, 100), (30, 6, 64, 100)]


def test_track8_newest_wins_and_flips():
    d = project()
    step(d, 7, 0, 0, gate=48, note=0x48)
    step(d, 7, 0, 2, gate=6, note=0x4C, flip=12)
    _, m, tr = export(d)
    assert notes_of(tr, 7) == [(0, 12, 60, 100), (12, 6, 64, 100)]
    assert m["tracks"][7]["sound_slots_flipped"] == [12] and m["tracks"][7]["mode"] == "synth"


# Engine-note vectors: the chord results of the firmware, emulated with the Arrhythmia image
CHORD_CASES = [
    (dict(npp=1), 60, [60]),
    (dict(npp=2), 60, [60, 64]),
    (dict(npp=3), 60, [60, 64, 67]),
    (dict(npp=4), 60, [60, 64, 67, 71]),
    (dict(npp=3), 62, [62, 65, 69]),
    (dict(npp=3, scale=1, root=9), 69, [69, 72, 76]),
    (dict(npp=3, inkey=0, type=0), 61, [61, 65, 68]),
    (dict(npp=3, inkey=0, type=4), 60, [60, 64, 70]),
    (dict(npp=4, inkey=0, type=5), 60, [60, 63, 67, 70]),
    (dict(npp=3, voicing=1), 60, [64, 67, 72]),
    (dict(npp=3, voicing=3), 60, [60, 67, 76]),
    (dict(npp=3, octave=-1), 60, [48, 52, 55]),
    (dict(npp=2), 118, [118, 109]),
    (dict(npp=3), 66, [66, 70, 73]),                               # sub-record unset: C major fallback
    (dict(npp=3, sub=(2, 4, 0)), 66, [66, 69, 73]),                # E dorian
    (dict(npp=3, sub=(8, 4, 0)), 67, [67, 71, 76]),                # E minor pentatonic
]


def _rec(f):
    return {cs.SR_NPP: f.get("npp", 1), cs.SR_TYPE: f.get("type", 0), cs.SR_INKEY: f.get("inkey", 1),
            cs.SR_VOICING: f.get("voicing", 0), cs.SR_SCALE: f.get("scale", 0), cs.SR_ROOT: f.get("root", 0),
            cs.SR_OCTAVE: f.get("octave", 0), 9: 1}


def test_track8_chords_match_firmware_vectors():
    for f, note, want in CHORD_CASES:
        d = with_block(project(), block(_rec(f), scales=f.get("sub")))
        step(d, 7, 0, 0, note=note + 12)
        _, m, tr = export(d)
        got = [n[2] for n in notes_of(tr, 7)]
        assert got == sorted(want), (f, note, got, want)
        assert cs.synth_notes(cs.Project(bytes(d)), note + 12, 0) == want, (f, note)
        assert m["arrhythmia_block"] == "ok"


def test_scale_mapping_of_firmware_022():
    for stored, plays in ((3, 1), (4, 0), (9, 9), (10, 0), (200, 0)):
        p = cs.Project(bytes(with_block(project(), block(_rec({}), scales=(stored, 13, 0x7F)))))
        assert p.scale == plays and p.root == 0 and p.scaled == [True] * 7, (stored, p.scale, p.root)


def test_step_chords():
    shapes = [(4, 7), (3, 7, 10), (12,)]
    d = with_block(project(), block(_rec(dict(npp=3, octave=1)), chords=shapes))
    step(d, 7, 0, 0, note=0x48, b6=1 << 1)               # key 1: C E G, track octave +1
    step(d, 7, 0, 4, note=0x45, b6=2 << 1 | 1)           # key 2 (bit 0 of byte 6 is not the key)
    step(d, 7, 0, 8, note=0x48, b6=9 << 1)               # key 9 > count: root only (NPP not applied)
    step(d, 7, 0, 12, note=0x48)                         # no key: NPP 3 in key
    _, m, tr = export(d)
    by = {}
    for s, _l, n, _v in notes_of(tr, 7):
        by.setdefault(s // 6, []).append(n)
    assert by == {0: [72, 76, 79], 4: [69, 72, 76, 79], 8: [72], 12: [72, 76, 79]}, by
    assert m["track8"]["step_chord_shapes"] == [[4, 7], [3, 7, 10], [12]]
    bad = with_block(project(), block(_rec(dict(npp=3)), chords=[(7, 4)]))      # not ascending: table invalid
    step(bad, 7, 0, 0, note=0x48, b6=1 << 1)
    assert [n[2] for n in notes_of(export(bad)[2], 7)] == [60]


def test_invalid_block_gives_defaults():
    for blk, why in ((block(_rec(dict(npp=3)), crc_ok=False), "bad CRC"), (bytes(cs.EXT_LEN), "empty"),
                     (b"TMD2" + block(_rec(dict(npp=3)))[4:], "bad magic")):
        d = with_block(project(), blk)
        step(d, 7, 0, 0, note=0x48)
        step(d, 7, 0, 1, note=0x48, b6=1 << 1)
        _, m, tr = export(d)
        assert m["arrhythmia_block"] == why and [n[2] for n in notes_of(tr, 7)] == [60, 60], (why, notes_of(tr, 7))
    rec = _rec(dict(npp=3))
    rec[cs.SR_OCTAVE] = 5                                                         # out of range: default 0
    p = cs.Project(bytes(with_block(project(), block(rec))))
    assert p.t8["octave"] == 0 and p.t8["notes_per_press"] == 3


def test_locks_as_cc():
    d = project()
    step(d, 0, 0, 0)
    lock(d, 0, 0, 0, 0, [30, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])          # macro 1 on step 1
    lock(d, 0, 0, 0, 1, [30, 30, 31, 0xFF, 0xFF, 0xFF])              # sent after the unlocked microsteps, repeat
                                                                     # suppressed, change sent
    lock(d, 0, 0, 10, 2, [5] * 6)                                    # lane 10 = CC 29
    lock(d, 0, 0, 0, 4, [64] + [0xFF] * 5)                           # after an unlocked step: sent again
    lock(d, 0, 0, 0, 5, [64] + [0xFF] * 5)
    settings(d, 1, 0, rate=7)
    lock(d, 1, 0, 2, 0, [10, 20, 30, 40, 50, 60])                    # 24 pulses: every 4th pulse
    _, _, tr = export(d)
    first = [(0, 21, 30), (6, 21, 30), (8, 21, 31), (12, 29, 5), (24, 21, 64), (30, 21, 64)]
    assert ccs_of(tr, 0) == [(96 * k + x, c, v) for k in range(4) for x, c, v in first], ccs_of(tr, 0)
    assert ccs_of(tr, 1) == [(4 * k, 23, 10 * (k + 1)) for k in range(6)]
    _, _, tr = export(d, locks=False)
    assert ccs_of(tr, 0) == []


def test_probability_random_and_mute_reported():
    d = project()
    step(d, 2, 0, 0, prob=3)
    settings(d, 3, 0, direction=cs.RANDOM)
    step(d, 3, 0, 5)
    d[cs.MUTE_AT + 4] = 1
    _, m, tr = export(d)
    assert notes_of(tr, 2) == [(0, 6, 36, 100)] and m["tracks"][2]["steps_with_probability"] == 1
    assert m["tracks"][3]["random_direction_patterns"] == [1] and notes_of(tr, 3)[0][0] == 30
    assert any("random order" in w for w in m["warnings"])
    assert m["tracks"][4]["muted"] and m["tracks"][4]["name"] == "T5 (muted)"


def test_find_image_and_errors():
    img = bytes(project(name="Z"))
    assert cs.find_image(img) == img
    assert cs.find_image(b"junk" * 9 + img + b"tail") == img
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("a/readme.txt", "x")
        f.writestr("a/s.bin", b"\0" * 7 + img)
    assert cs.find_image(z.getvalue()) == img
    for bad, msg in ((b"nothing here", "no project image"), (img + img, "2 project images")):
        try:
            cs.find_image(bad)
        except cs.ProjectError as e:
            assert msg in str(e), e
            continue
        raise AssertionError(msg)
    v2 = bytearray(img)
    v2[8] = 2
    try:
        cs.Project(bytes(v2))
    except cs.ProjectError as e:
        assert "version" in str(e)
    else:
        raise AssertionError("version 2 accepted")


def test_cli_with_pack_folder():
    with tempfile.TemporaryDirectory() as tmp:
        pack = os.path.join(tmp, "05_My Pack")
        os.makedirs(os.path.join(pack, "Sessions"))
        os.makedirs(os.path.join(pack, "PCM"))
        for f in ("00_Kick A.wav", "03_Snare.wav", "127_Last.WAV", "notes.txt"):
            open(os.path.join(pack, "PCM", f), "wb").close()
        d = project(name="CLI")
        d[cs.SLOT_AT] = 0
        step(d, 0, 0, 0)
        step(d, 0, 0, 1, flip=3)
        step(d, 0, 0, 2, flip=127)
        src = os.path.join(pack, "Sessions", "02_CLI.ncs")
        with open(src, "wb") as f:
            f.write(d)
        out, mp = os.path.join(tmp, "o.mid"), os.path.join(tmp, "o.json")
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "midiexport.py"), src, "-o", out,
                            "--map", mp], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        m = json.load(open(mp))
        assert m["tracks"][0]["name"] == "T1 Kick A" and m["tracks"][0]["sample"] == "00_Kick A.wav"
        assert m["tracks"][0]["notes"] == [
            {"note": 36, "slot": 0, "sample": "00_Kick A.wav", "file": "samples/slot00_Kick A.wav"},
            {"note": 37, "slot": 3, "sample": "03_Snare.wav", "file": "samples/slot03_Snare.wav"},
            {"note": 38, "slot": 127, "sample": "127_Last.WAV", "file": "samples/slot127_Last.wav"}]
        fmt, _ppq, tracks = smf.read_smf(open(out, "rb").read())
        assert tracks[1][0] == (0, ("meta", smf.META_NAME, b"T1 Kick A"))
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "midiexport.py"), mp, "-o", out],
                           capture_output=True, text=True)
        assert r.returncode == 1 and "no project image" in r.stderr


def test_zip_and_mapping_md():
    z = mx.zip_store([("a.mid", b"MThd"), ("samples/slot05_\u00e9.wav", b"RIFFxx")])
    with zipfile.ZipFile(io.BytesIO(z)) as f:
        assert f.testzip() is None and f.namelist() == ["a.mid", "samples/slot05_\u00e9.wav"]
        assert f.read("samples/slot05_\u00e9.wav") == b"RIFFxx" and f.getinfo("a.mid").date_time == (1980, 1, 1, 0, 0, 0)
    want = (b"PK\x03\x04\x14\x00\x00\x08\x00\x00\x00\x00\x21\x00" + struct.pack("<III", zlib.crc32(b"x"), 1, 1)
            + b"\x01\x00\x00\x00" + b"n" + b"x")
    assert mx.zip_store([("n", b"x")]).startswith(want)
    d = project(name="ZIP")
    d[cs.SLOT_AT] = 3
    step(d, 0, 0, 0)
    step(d, 0, 0, 1, flip=0)                               # slot 0: not in the pack
    data, m, md, zb = mx.export(cs.Project(bytes(d)), "07_ZIP.ncs", ["03_Snare.wav", "x.txt"],
                                lambda f: b"WAV:" + f.encode())
    with zipfile.ZipFile(io.BytesIO(zb)) as f:
        assert f.namelist() == ["07_ZIP.mid", "MAPPING.md", "mapping.json", "samples/slot03_Snare.wav"]
        assert f.read("07_ZIP.mid") == data and f.read("samples/slot03_Snare.wav") == b"WAV:03_Snare.wav"
        assert json.loads(f.read("mapping.json")) == m and f.read("MAPPING.md").decode() == md
    assert m["samples"] == [{"slot": 0, "sample": None}, {"slot": 3, "sample": "03_Snare.wav",
                                                         "file": "samples/slot03_Snare.wav"}]
    assert "| 37 | 3 | samples/slot03_Snare.wav |" in md and "| 36 | 0 | (not in the pack) |" in md
    assert "| 21 | macro 1 | Tune | feedback |" in md


def test_optional_real_projects():
    """MIDIEXPORT_NCS=<dir>: every .ncs file there exports and parses back with matching note counts."""
    folder = os.environ.get("MIDIEXPORT_NCS")
    if not folder:
        return "skip: MIDIEXPORT_NCS not set"
    n = 0
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(".ncs"):
            data, m = mx.build(cs.Project(cs.find_image(open(os.path.join(folder, f), "rb").read())))
            _fmt, _ppq, tracks = smf.read_smf(data)
            assert [len(smf.notes(tracks[t + 1])) for t in range(8)] == [x["notes_written"] for x in m["tracks"]], f
            n += 1
    assert n, f"no .ncs files in {folder}"
    print(f"  {n} projects from {folder}")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            suite.addTest(unittest.FunctionTestCase(fn, description=name))
    return suite


def main(names):
    for name, fn in list(globals().items()):
        if name.startswith("test_") and (not names or name in names):
            r = fn()
            print(f"ok {name}" + (f" ({r})" if isinstance(r, str) else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
