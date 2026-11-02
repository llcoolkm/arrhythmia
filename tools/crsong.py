"""Decoder of a Circuit Rhythm project file into an arrangement model (the input of every export backend).

    import crsong
    song = crsong.song(crsong.Project(crsong.find_image(data)), scenes=None, loops=1, locks=True)

Project: the decoded file (header, chains, scenes, track data, the Arrhythmia extension block). song(): the
flattened arrangement in unit pulses (24 per quarter note): per track the notes [start, end, engine note, sample
slot, velocity] after the unit's voice rules, and the lock CCs [pulse, lane, value]. Backends map engine notes and
slots to their own note numbers (tools/midiexport.py: SMF, mapping, ZIP; web/export.html: the same in JavaScript).

User guide: docs/user/midi-export.md. Standard library only.
"""
import io
import struct
import zipfile
import zlib

SIZE = 0x29484
MAGICS = (b"RUSE", b"RDEM")
PULSES_PER_QUARTER = 24

SCENES, SCENE_AT, SCENE_LEN = 16, 0x040, 0x28
SCENE_CHAIN = 0x2C0
CHAIN_AT = 0x2C4
EL_AT, TRACK_STRIDE, EL_LEN = 0x2E4, 0x5140, 0xA28
LOCK_AT, LANES, LANE_LEN = 0x128, 12, 0xC0
MUTE_AT, SLOT_AT = 0x28CE4, 0x28CEC
EXT_AT, EXT_LEN = 0x29110, 0x370

RATE_PULSES = (2, 3, 4, 6, 8, 12, 16, 24)
_PULSE_TABLE = ((0, 3), (0, 2, 4), (0, 2, 3, 5), (0, 1, 2, 3, 4, 5), (0, -1, 1, 2, 3, -1, 4, 5),
                (0, -1, 1, -1, 2, -1, 3, -1, 4, -1, 5, -1),
                (0, -1, 1, -1, -1, 2, -1, -1, 3, -1, 4, -1, -1, 5, -1, -1),
                (0, -1, -1, -1, 1, -1, -1, -1, 2, -1, -1, -1, 3, -1, -1, -1, 4, -1, -1, -1, 5, -1, -1, -1))
FORWARD, REVERSE, PINGPONG, RANDOM = 0, 1, 2, 3
LANE_CC = (21, 22, 23, 24, 25, 26, 27, 28, 31, 32, 29, 30)
SYNTH = 7                               # track 8

# Arrhythmia extension block (docs/dev/design/project-extension-block.md)
REC7_AT, SCALES_AT, CHORDS_AT = 0x078, 0x288, 0x298
SR_LIMITS = {2: (1, 4, 1), 3: (0, 5, 0), 4: (0, 1, 1), 5: (0, 3, 0), 6: (0, 1, 0), 7: (0, 11, 0), 8: (-2, 2, 0),
             9: (0, 1, 1), 10: (0, 7, 0)}
SR_NPP, SR_TYPE, SR_INKEY, SR_VOICING, SR_SCALE, SR_ROOT, SR_OCTAVE = 2, 3, 4, 5, 6, 7, 8
SCALE_NAMES = ("major", "natural minor", "dorian", "phrygian", "lydian", "mixolydian", "harmonic minor",
               "major pentatonic", "minor pentatonic", "blues")
SCALE_MASKS = (0xAB5, 0x5AD, 0x6AD, 0x5AB, 0xAD5, 0x6B5, 0x9AD, 0x295, 0x4A9, 0x4E9)
CHORD_TYPES = ("Maj", "Min", "Sus2", "Sus4", "7th", "m7")
VOICINGS = ("root", "inversion 1", "inversion 2", "spread")


class ProjectError(ValueError):
    pass


def microstep_pulse(rate):
    """Pulse within the step of microsteps 0..5 at a rate index: several microsteps share a pulse at rates 0..2."""
    tab = _PULSE_TABLE[rate]
    out = [0] * 6
    for j, k in enumerate(tab):
        if k < 0:
            continue
        nxt = 6 if j == len(tab) - 1 or rate > 2 else tab[j + 1]
        for m in range(k, nxt if rate <= 2 else k + 1):
            out[m] = j
    return out


MS_PULSE = tuple(tuple(microstep_pulse(r)) for r in range(8))


# --- input ---------------------------------------------------------------------------------------------------------

def _is_image(d, i=0):
    return d[i:i + 4] in MAGICS and len(d) - i >= SIZE and struct.unpack_from("<I", d, i + 4)[0] == SIZE


def find_image(data):
    """The project image in data: the whole file, a zip member, or one embedded image (found by magic + size)."""
    if len(data) == SIZE and _is_image(data):
        return bytes(data)
    if data[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            z = None
        if z is not None:
            found = []
            for info in z.infolist():
                if info.file_size <= 64 << 20:
                    try:
                        found += [(info.filename, img) for img in _embedded(z.read(info))]
                    except (zipfile.BadZipFile, NotImplementedError, RuntimeError):
                        pass
            return _one(found, "zip members")
    return _one([("offset", img) for img in _embedded(data)], "the file")


def _embedded(d):
    out = []
    for m in MAGICS:
        i = d.find(m)
        while i >= 0:
            if _is_image(d, i):
                out.append(bytes(d[i:i + SIZE]))
            i = d.find(m, i + 1)
    return out


def _one(found, where):
    if len(found) == 1:
        return found[0][1]
    if not found:
        raise ProjectError(f"no project image ({SIZE} bytes starting 'RUSE' or 'RDEM') in {where}")
    raise ProjectError(f"{len(found)} project images in {where}; export one project file instead")


# --- decoding ------------------------------------------------------------------------------------------------------

def step_bytes(img, t, p, s):
    o = EL_AT + t * TRACK_STRIDE + p * EL_LEN + 8 * s
    return img[o:o + 8]


def pattern_settings(img, t, p):
    o = EL_AT + t * TRACK_STRIDE + p * EL_LEN + 0x100
    end, start, rate, direction = img[o:o + 4]
    return end & 31, start & 31, rate, direction


def lock_lane(img, t, p, lane, s):
    o = EL_AT + t * TRACK_STRIDE + p * EL_LEN + LOCK_AT + lane * LANE_LEN + 6 * s
    return img[o:o + 6]


def pattern_length(end, start):
    return (32 if end < start else 0) + end - start + 1


def ext_status(b):
    """TMD1 block status: 'ok', 'empty', or the first failed check (block read-only: settings at defaults)."""
    if not any(b):
        return "empty"
    if b[0:4] != b"TMD1":
        return "bad magic"
    if b[4] != 1:
        return "bad version"
    if b[5] != 0:
        return "bad flags"
    if struct.unpack_from("<H", b, 6)[0] != EXT_LEN:
        return "bad length"
    if zlib.crc32(b[:EXT_LEN - 4]) != struct.unpack_from("<I", b, EXT_LEN - 4)[0]:
        return "bad CRC"
    return "ok"


def rec_get(rec, off):
    lo, hi, default = SR_LIMITS[off]
    if rec is None or not any(rec[1:16]) or rec[1] != 1:
        return default
    v = struct.unpack_from("<b", rec, off)[0] if off == SR_OCTAVE else rec[off]
    return default if v < lo or v > hi else v


def chord_table(b):
    """The step chord shape table, [offsets per key 1..count], or None if invalid."""
    if b[0:3] != b"CH\x01" or b[3] > 31:
        return None
    shapes = []
    for k in range(b[3]):
        e = b[4 + 3 * k:7 + 3 * k]
        if not e[0] or e[0] > 96 or (e[1] and (e[1] <= e[0] or e[1] > 96)) or \
                (e[2] and (not e[1] or e[2] <= e[1] or e[2] > 96)):
            return None
        shapes.append(tuple(x for x in e if x))
    return shapes


class Project:
    def __init__(self, img):
        img = bytes(img)
        if len(img) != SIZE or img[:4] not in MAGICS or struct.unpack_from("<I", img, 4)[0] != SIZE:
            raise ProjectError("not a project image (magic, size)")
        version = struct.unpack_from("<I", img, 8)[0]
        if version >> 1:
            raise ProjectError(f"unsupported project version {version}")
        self.img = img
        self.name = img[0x10:0x30].decode("latin-1").rstrip(" \0")
        self.tempo = img[0x34]
        self.swing = img[0x35]
        self.scene_chain = (img[SCENE_CHAIN], img[SCENE_CHAIN + 1])
        self.chains = [(img[CHAIN_AT + 4 * t], img[CHAIN_AT + 4 * t + 1]) for t in range(8)]
        self.scene_spans = [[(img[SCENE_AT + SCENE_LEN * s + 4 * t], img[SCENE_AT + SCENE_LEN * s + 4 * t + 1])
                             for t in range(8)] for s in range(SCENES)]
        self.muted = [bool(img[MUTE_AT + t]) for t in range(8)]
        self.slot = [img[SLOT_AT + t] for t in range(8)]
        blk = img[EXT_AT:EXT_AT + EXT_LEN]
        self.ext = ext_status(blk)
        ok = self.ext == "ok"
        rec = blk[REC7_AT:REC7_AT + 16] if ok else None
        self.t8 = {k: rec_get(rec, off) for k, off in (("notes_per_press", SR_NPP), ("chord_type", SR_TYPE),
                                                        ("in_key", SR_INKEY), ("voicing", SR_VOICING),
                                                        ("octave", SR_OCTAVE))}
        sub = blk[SCALES_AT:SCALES_AT + 16] if ok else bytes(16)
        if sub[0:3] == b"SC\x01":
            x = sub[3]
            self.scale = 1 if x == 3 else 0 if x == 4 or x >= 10 else x    # 0.2.2 plays Phrygian as minor, Lydian as major
            self.root = sub[4] if sub[4] < 12 else 0
            self.scaled = [bool(sub[5] >> t & 1) for t in range(7)]
        else:
            self.scale, self.root, self.scaled = rec_get(rec, SR_SCALE), rec_get(rec, SR_ROOT), [False] * 7
        self.chords = chord_table(blk[CHORDS_AT:CHORDS_AT + 4 + 93]) if ok else None


def scale_semi(mask, i):
    n = bin(mask).count("1")
    d, k = i % n, 0
    for k in range(12):
        if mask >> k & 1:
            if d == 0:
                break
            d -= 1
    return 12 * (i // n) + k


def fold(p):
    while p > 119:
        p -= 12
    while p < 0:
        p += 12
    return p


def synth_notes(proj, b0, b6):
    """Engine notes of one track-8 trigger (Arrhythmia firmware rules)."""
    note = (b0 or 0x30) - 12
    key = b6 >> 1 & 31
    t = [0, 0, 0, 0]
    if key:                                   # a step chord: stored notes verbatim, root only if not decodable
        offs = proj.chords[key - 1] if proj.chords is not None and key <= len(proj.chords) else ()
        n = 1 + len(offs)
        t[1:n] = offs
    elif proj.t8["notes_per_press"] >= 2:
        n = proj.t8["notes_per_press"]
        if proj.t8["in_key"]:
            mask = SCALE_MASKS[proj.scale]
            rel = ((note - proj.root) % 12 + 12) % 12
            d = 0
            for k in range(bin(mask).count("1")):
                if scale_semi(mask, k) <= rel:
                    d = k
            for v in range(n):
                t[v] = scale_semi(mask, d + 2 * v) - scale_semi(mask, d)
        else:
            ty = proj.t8["chord_type"]
            t[1] = 4 if ty in (0, 4) else 3 if ty in (1, 5) else 2 if ty == 2 else 5
            t[2], t[3] = ((7 if n == 4 else 10), 10) if ty >= 4 else (7, 12)
        vo = proj.t8["voicing"]
        if vo == 3:
            t[1] += 12
        else:
            for i in range(vo):
                t[i] += 12
        t[:n] = sorted(t[:n])
    else:
        n = 1
    oct_ = 12 * proj.t8["octave"]
    return [fold(note + t[v] + oct_) for v in range(n)]


# --- arrangement ---------------------------------------------------------------------------------------------------

def pattern_duration(img, t, p):
    end, start, rate, direction = pattern_settings(img, t, p)
    return RATE_PULSES[rate & 7] * pattern_length(end, start) * (2 if direction == PINGPONG else 1)


def _span(sp):
    a, b = sp
    a, b = a & 7, b & 7
    return (a, b) if a <= b else (a, a)


def arrangement(proj, scenes=None):
    """[(scene or None, start pulse, length in pulses, 8 spans)]; scenes = (first, last) overrides the chain."""
    first, last = scenes if scenes is not None else proj.scene_chain
    if scenes is None and first == last:
        parts = [(None, [_span(c) for c in proj.chains])]
    else:
        if not (0 <= first <= last < SCENES):
            raise ProjectError(f"scene range {first + 1}..{last + 1} is not valid")
        parts = [(s, [_span(c) for c in proj.scene_spans[s]]) for s in range(first, last + 1)]
    out, pos = [], 0
    for s, spans in parts:
        length = max(sum(pattern_duration(proj.img, t, p) for p in range(a, b + 1)) for t, (a, b) in enumerate(spans))
        out.append((s, pos, length, spans))
        pos += length
    return out


def step_order(end, start, direction):
    n = pattern_length(end, start)
    if direction == REVERSE:
        return [(end - i) & 31 for i in range(n)]
    if direction == PINGPONG:
        return [(start + i) & 31 for i in range(n)] + [(end - i) & 31 for i in range(n)]
    return [(start + i) & 31 for i in range(n)]


class Song:
    """The arrangement of one project: everything a backend needs, in unit pulses."""

    def __init__(self, proj, parts, loops):
        self.project = proj
        self.parts = parts                      # [(scene or None, start pulse, length, 8 spans)] of one pass
        self.loops = loops
        self.length = (parts[-1][1] + parts[-1][2]) * loops if parts else 0
        self.notes = [[] for _ in range(8)]     # [start, end, engine note, slot, velocity]
        self.ccs = [[] for _ in range(8)]       # [pulse, lane, value]
        self.probability_steps = [0] * 8
        self.random_patterns = [set() for _ in range(8)]
        self.flips = [set() for _ in range(8)]
        self.warnings = []


def song(proj, scenes=None, loops=1, locks=True):
    parts = arrangement(proj, scenes)
    s = Song(proj, parts, loops)
    one = parts[-1][1] + parts[-1][2] if parts else 0
    for loop in range(loops):
        for _scene, start, length, spans in parts:
            for t in range(8):
                _track(s, t, start + loop * one, start + loop * one + length, spans[t], locks)
    for t in range(8):
        s.notes[t] = voice_rules(s.notes[t], t == SYNTH)
        if s.random_patterns[t]:
            s.warnings.append(f"track {t + 1}: patterns {sorted(s.random_patterns[t])} play in random order; "
                              "written in forward order")
    return s


def _track(s, t, start, stop, span, locks):
    img, pos = s.project.img, start
    chain = list(range(span[0], span[1] + 1))
    if sum(pattern_duration(img, t, p) for p in chain) == 0:
        return
    last = [None] * LANES
    while pos < stop:
        for p in chain:
            end, st, rate, direction = pattern_settings(img, t, p)
            if rate > 7 or direction > 3:
                s.warnings.append(f"track {t + 1} pattern {p + 1}: rate {rate} / direction {direction} outside the "
                                  "known range; read as rate & 7, forward")
                rate &= 7
                direction = FORWARD if direction > 3 else direction
            if direction == RANDOM:
                s.random_patterns[t].add(p + 1)
            rp = RATE_PULSES[rate]
            for i, st_ in enumerate(step_order(end, st, direction)):
                sp = pos + i * rp
                if sp >= stop:
                    return
                _step(s, t, p, st_, rate, sp, stop)
                if locks:
                    _locks(s, t, p, st_, rate, sp, stop, last)
            pos += pattern_duration(img, t, p)


def _step(s, t, p, st, rate, sp, stop):
    proj = s.project
    b = step_bytes(proj.img, t, p, st)
    if not b[2] & 0x3F:
        return
    if b[4] < 7:
        s.probability_steps[t] += 1
    length = max(1, RATE_PULSES[rate] * b[1] // 6)
    slot = b[5] if b[5] != 0xFF else proj.slot[t]
    if b[5] != 0xFF:
        s.flips[t].add(b[5])
    engines = synth_notes(proj, b[0], b[6]) if t == SYNTH else [(b[0] or 0x30) - 12]
    for j in sorted({MS_PULSE[rate][k] for k in range(6) if b[2] >> k & 1}):
        on = sp + j
        if on >= stop:
            break
        for e in engines:
            s.notes[t].append([on, on + length, e, slot, max(1, b[3] & 0x7F)])


def _locks(s, t, p, st, rate, sp, stop, last):
    tab = _PULSE_TABLE[rate]
    for lane in range(LANES):
        v6 = lock_lane(s.project.img, t, p, lane, st)
        if v6 == b"\xFF" * 6:
            last[lane] = None
            continue
        for j in range(RATE_PULSES[rate]):
            k = tab[j]
            if k < 0:
                continue                      # interpolated by the unit between microsteps: not written
            v = v6[k]
            if v > 127:
                last[lane] = None
            elif v != last[lane] and sp + j < stop:
                s.ccs[t].append([sp + j, lane, v])
                last[lane] = v


def end_overlaps(ev, key):
    """A note that starts while an earlier note with the same ev[key] sounds ends the earlier one."""
    open_ = {}
    for x in ev:
        prev = open_.get(x[key])
        if prev is not None and prev[1] > x[0]:
            prev[1] = x[0]
        open_[x[key]] = x
    return [x for x in ev if x[1] > x[0]]


def voice_rules(notes, synth):
    """The unit's rules on one track's triggers: a retrigger of a sounding note ends it; on track 8 the sequencer
    plays one press at a time (a new trigger ends the notes of the previous one)."""
    ev = sorted(notes, key=lambda x: (x[0], x[2]))
    if synth:
        starts = sorted({x[0] for x in ev})
        nxt = dict(zip(starts, starts[1:]))
        for x in ev:
            if x[0] in nxt:
                x[1] = min(x[1], nxt[x[0]])
    return end_overlaps(ev, 2)
