"""Standard MIDI File writer and reader (type 0 and 1). Standard library only.

Writer: build Track objects, add events at absolute ticks, then write_smf(). Events at the same tick are written in
a fixed order: meta events, note-offs, controller changes, note-ons; ties keep insertion order. Running status is
not used.

Reader: read_smf() returns (format, ppq, tracks); each track is a list of (tick, event) with event one of
    ("meta", type, data)   ("note_on", ch, note, vel)   ("note_off", ch, note, vel)   ("cc", ch, num, val)
    ("other", status, data)   ("sysex", data)
A note-on with velocity 0 is returned as note_off.
"""
import struct

META_TEXT, META_NAME, META_END, META_TEMPO, META_TIMESIG = 0x01, 0x03, 0x2F, 0x51, 0x58
_ORDER_META, _ORDER_OFF, _ORDER_CC, _ORDER_ON = 0, 1, 2, 3


class SMFError(ValueError):
    pass


def vlq(n):
    """Variable-length quantity, 0 .. 0x0FFFFFFF."""
    if not 0 <= n <= 0x0FFFFFFF:
        raise SMFError(f"value out of range for a variable-length quantity: {n}")
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _u7(v, what):
    if not 0 <= v <= 127:
        raise SMFError(f"{what} out of range 0..127: {v}")
    return v


def _ch(ch):
    if not 0 <= ch <= 15:
        raise SMFError(f"channel out of range 0..15: {ch}")
    return ch


class Track:
    def __init__(self, name=None):
        self.events = []                       # (tick, order, seq, bytes)
        self.end = 0                           # End of Track tick (at least the last event)
        if name is not None:
            self.meta(0, META_NAME, name.encode("latin-1", "replace"))

    def _add(self, tick, order, data):
        if tick < 0:
            raise SMFError(f"negative tick {tick}")
        self.events.append((tick, order, len(self.events), bytes(data)))

    def meta(self, tick, kind, data):
        self._add(tick, _ORDER_META, bytes([0xFF, kind]) + vlq(len(data)) + bytes(data))

    def text(self, tick, s):
        self.meta(tick, META_TEXT, s.encode("latin-1", "replace"))

    def tempo(self, tick, bpm):
        us = round(60_000_000 / bpm)
        if not 0 < us < 1 << 24:
            raise SMFError(f"tempo out of range: {bpm}")
        self.meta(tick, META_TEMPO, us.to_bytes(3, "big"))

    def time_signature(self, tick, num, den, clocks=24, n32=8):
        self.meta(tick, META_TIMESIG, bytes([num, den.bit_length() - 1, clocks, n32]))

    def note_on(self, tick, ch, note, vel):
        self._add(tick, _ORDER_ON, (0x90 | _ch(ch), _u7(note, "note"), _u7(vel, "velocity")))

    def note_off(self, tick, ch, note, vel=0):
        self._add(tick, _ORDER_OFF, (0x80 | _ch(ch), _u7(note, "note"), _u7(vel, "velocity")))

    def note(self, tick, length, ch, note, vel):
        self.note_on(tick, ch, note, vel)
        self.note_off(tick + length, ch, note)

    def cc(self, tick, ch, num, val):
        self._add(tick, _ORDER_CC, (0xB0 | _ch(ch), _u7(num, "controller"), _u7(val, "value")))

    def chunk(self):
        body = bytearray()
        last = 0
        evs = sorted(self.events)
        for tick, _o, _s, data in evs:
            body += vlq(tick - last) + data
            last = tick
        body += vlq(max(0, self.end - last)) + bytes([0xFF, META_END, 0])   # End of Track at self.end or later
        return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_smf(tracks, ppq, fmt=1):
    if fmt not in (0, 1) or (fmt == 0 and len(tracks) != 1):
        raise SMFError("format 0 needs exactly one track; formats 0 and 1 only")
    if not 0 < ppq < 0x8000:
        raise SMFError(f"ticks per quarter note out of range: {ppq}")
    return b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq) + b"".join(t.chunk() for t in tracks)


def _read_vlq(d, i):
    n = 0
    for _ in range(4):
        if i >= len(d):
            raise SMFError("truncated variable-length quantity")
        b = d[i]
        i += 1
        n = n << 7 | (b & 0x7F)
        if not b & 0x80:
            return n, i
    raise SMFError("variable-length quantity longer than 4 bytes")


_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def _read_track(d):
    out, i, tick, status = [], 0, 0, None
    while i < len(d):
        delta, i = _read_vlq(d, i)
        tick += delta
        if i >= len(d):
            raise SMFError("truncated event")
        b = d[i]
        if b == 0xFF:
            if i + 2 > len(d):
                raise SMFError("truncated meta event")
            kind = d[i + 1]
            n, j = _read_vlq(d, i + 2)
            out.append((tick, ("meta", kind, bytes(d[j:j + n]))))
            i = j + n
            if kind == META_END:
                if i != len(d):
                    raise SMFError("data after end of track")
                return out
            continue
        if b in (0xF0, 0xF7):
            n, j = _read_vlq(d, i + 1)
            out.append((tick, ("sysex", bytes(d[j:j + n]))))
            i = j + n
            status = None
            continue
        if b & 0x80:
            status = b
            i += 1
        elif status is None:
            raise SMFError("running status without a status byte")
        hi, ch = status & 0xF0, status & 0x0F
        n = _DATA_LEN.get(hi)
        if n is None or i + n > len(d):
            raise SMFError(f"bad or truncated channel event, status {status:#x}")
        a = d[i:i + n]
        i += n
        if hi == 0x90 and a[1] == 0:
            out.append((tick, ("note_off", ch, a[0], 0)))
        elif hi == 0x90:
            out.append((tick, ("note_on", ch, a[0], a[1])))
        elif hi == 0x80:
            out.append((tick, ("note_off", ch, a[0], a[1])))
        elif hi == 0xB0:
            out.append((tick, ("cc", ch, a[0], a[1])))
        else:
            out.append((tick, ("other", status, bytes(a))))
    raise SMFError("track without end-of-track event")


def read_smf(data):
    d = memoryview(data)
    if bytes(d[:4]) != b"MThd" or len(d) < 14:
        raise SMFError("not a Standard MIDI File")
    hlen, fmt, ntrk, ppq = struct.unpack(">IHHH", d[4:14])
    if hlen < 6 or ppq & 0x8000:
        raise SMFError("unsupported header (SMPTE time or short header)")
    i, tracks = 8 + hlen, []
    while len(tracks) < ntrk:
        if i + 8 > len(d):
            raise SMFError("truncated file")
        tag, n = bytes(d[i:i + 4]), struct.unpack(">I", d[i + 4:i + 8])[0]
        body = d[i + 8:i + 8 + n]
        if len(body) != n:
            raise SMFError("truncated chunk")
        if tag == b"MTrk":
            tracks.append(_read_track(body))
        i += 8 + n
    return fmt, ppq, tracks


def notes(track):
    """Pair note-ons with note-offs: [(start, length, ch, note, vel)] sorted by start, channel, note."""
    held, out = {}, []
    for tick, ev in track:
        if ev[0] == "note_on":
            held.setdefault((ev[1], ev[2]), []).append((tick, ev[3]))
        elif ev[0] == "note_off" and held.get((ev[1], ev[2])):
            start, vel = held[(ev[1], ev[2])].pop(0)
            out.append((start, tick - start, ev[1], ev[2], vel))
    if any(held.values()):
        raise SMFError("note-on without note-off")
    return sorted(out)
