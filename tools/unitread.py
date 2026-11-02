"""Read projects and samples from a Circuit Rhythm over USB MIDI, read only, and export them like tools/midiexport.py.

    uv run python -I tools/unitread.py [--device /dev/snd/midiC4D0] [--transcript T.json] list
    uv run python -I tools/unitread.py [--device ...] [--transcript T.json] read SLOT INDEX -o OUT.zip [--raw DIR]
                                       [--pitched 1,2] [--base 36] [--scenes N-M] [--loops N] [--no-locks]
    uv run python -I tools/unitread.py replay T.json read SLOT INDEX -o OUT.zip        (no device: a transcript)

The unit's content transfer (the protocol Novation Components uses) carries every frame as
`F0 00 20 29 01 63 03 <command> ... F7`. SLOT is the unit's pack number: 0 = the factory pack in the internal
storage, 1..31 = the card folder `Rhythm/NN_Name` with NN = SLOT - 1. INDEX is the number of the project file
`NN_name.ncs` in the pack's Sessions folder.

Each command opens one transfer session: '@' (0x40) begins it, 'A' (0x41) ends it. The tool sends only these
frames, and nothing else (FRAMES):
* the version query `F0 00 20 29 00 70 F7`; on the Circus Arrhythmia FM builds also the status query
  `F0 00 20 29 00 60 0D F7` before '@', after its reply, before 'A' and after its reply (any fault stops the run);
* '@', 'A', the volume list `0B 01`, the pack list `0B 02`, the item list `0B t s` of the Sessions (t = 3) and PCM
  (t = 5) folders, the size query `0A t s i` and the download request `01 <8 x 00> t s i 02 <8 x 00>` of one
  Sessions or PCM item.
No command that writes, renames or deletes is in the allow-list. The unit must be stopped: the tool listens for
IDLE_LISTEN s before '@' and refuses if a note, controller or Start/Continue message arrives.

'@' puts the unit in its transfer state: it copies the project in its memory aside, and 'A' copies it back and
ends the state, so touch no control during a run. On Circus Arrhythmia FM 0.2.2 the track-8 synth pauses during the
transfer and starts again by itself after 'A'. The project and sample files on the unit are not modified; the tool
writes only the output files it is given (-o, --raw, --transcript).

The session counts as open from the attempt to send '@'. A well-formed refusal (NAK), a bad CRC after a complete
stream, the size limit, an absent project and a project that does not decode end the session with 'A'. Any other
failure (timeout, link error, malformed or unexpected reply, Ctrl+C) leaves the state unknown: the tool sends
nothing more, retries nothing and reports it.

The unit sends a whole download in one go and does not serve its watchdog meanwhile (it restarts after about 8 s).
The tool measures the stream rate on the project file and refuses a sample whose predicted stream time exceeds
BUDGET_MS; read such a pack from a copy of the card instead (tools/midiexport.py).

Replies (32-bit numbers travel as 8 nibbles, high first):
* ACK `04 <counter 8> <3 bytes> F7` (20 bytes); NAK `05 <counter 8> <3 bytes> <code> F7` (21 bytes);
* `0B 01 <bits>` (bit 0 internal storage, bit 1 card); `0B 02 <n>` then n x `0C 02 <slot> <name>`;
  `0B t s <n lo7> <n hi7>` then n x `0C t s <index> <name>`; `0A t s i <size 8>` (20 bytes);
* download: `01 <8 x 00> t s i 01 <size 8>` (29 bytes), then frames `02 <counter 8> t s i <data>` with 0x2000 bytes
  each (counter from 1; data in groups of 7 bytes, each group preceded by a byte of their top bits), then
  `03 <counter 8> t s i <CRC-32 8>` (28 bytes); `06 t s i` (12 bytes) if the index is empty. The unit streams a
  download without acknowledgements.
"""
import argparse
import json
import os
import re
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crsong  # noqa: E402
import midiexport  # noqa: E402

PREFIX = bytes.fromhex("F0 00 20 29 01 63 03")
VERSION_REQ = bytes.fromhex("F0 00 20 29 00 70 F7")
VERSION_PREFIX = VERSION_REQ[:6]
STATUS_REQ = bytes.fromhex("F0 00 20 29 00 60 0D F7")
STATUS_PREFIX = STATUS_REQ[:7]
BEGIN, END, START, DATA, FINISH, ACK, NAK, ABSENT, SIZE, LIST, ENTRY = \
    0x40, 0x41, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0A, 0x0B, 0x0C
SESSIONS, PCM = 3, 5
SESSION_ACK = PREFIX + bytes([ACK]) + bytes(11) + b"\xF7"
BEGIN_BUSY = PREFIX + bytes([NAK]) + bytes(11) + b"\x03\xF7"
CHUNK = 0x2000
# Application builds whose transfer code was compared with the stock 2.0.1 firmware (identical except the status hooks
# of the Circus Arrhythmia FM builds, which answer the status query).
BUILDS = {5706: "stock 2.0.1", 7338: "Circus Arrhythmia FM 0.2.2 (test build)", 7339: "Circus Arrhythmia FM 0.2.2",
          7340: "Circus Arrhythmia FM 0.2.2 (test build)"}
STATUS_BUILDS = {7338, 7339, 7340}
RELEASES = {7229: "Circus Arrhythmia FM 0.1.0", 7261: "Circus Arrhythmia FM 0.1.1", 7323: "Circus Arrhythmia FM 0.2.0",
            7329: "Circus Arrhythmia FM 0.2.1"}      # for the refusal message only
TIMEOUT = 5.0
IDLE_LISTEN = 1.0
BUDGET_MS = 5000.0      # longest predicted download stream of one item (the unit's watchdog: about 8 s)
NAK_TEXT = {1: "another transfer is in progress", 3: "refused", 5: "volume not mounted", 6: "no such pack",
            7: "no such item"}


class Refused(Exception):
    """Nothing was opened, or the session was closed: the unit is in its normal state."""


class Uncertain(Exception):
    """The session state is not known: no further frame was sent."""


def frame_allowed(msg):
    """The allow-list (module docstring): every frame the tool sends passes here first."""
    if msg in (VERSION_REQ, STATUS_REQ):
        return True
    if not (msg.startswith(PREFIX) and msg[-1] == 0xF7 and all(b <= 0x7F for b in msg[1:-1])):
        return False
    body = msg[len(PREFIX):-1]
    if body in (bytes([BEGIN]), bytes([END]), bytes([LIST, 1]), bytes([LIST, 2])):
        return True
    if len(body) == 3 and body[0] == LIST and body[1] in (SESSIONS, PCM) and body[2] <= 31:
        return True
    if len(body) == 4 and body[0] == SIZE and body[1] in (SESSIONS, PCM) and body[2] <= 31:
        return True
    return (len(body) == 21 and body[0] == START and body[1:9] == bytes(8) and body[9] in (SESSIONS, PCM)
            and body[10] <= 31 and body[12] == 2 and body[13:21] == bytes(8))


def wire_bytes(size):
    """Bytes on the wire of a download of `size` bytes: header 29, one frame of 20 + packed data per 0x2000 bytes, end 28."""
    full, rest = divmod(size, CHUNK)
    packed = lambda n: n + (n + 6) // 7  # noqa: E731
    return 29 + full * (20 + packed(CHUNK)) + (20 + packed(rest) if rest else 0) + 28


def unnib(b):
    v = 0
    for x in b:
        v = v << 4 | x
    return v


def unpack7(b):
    out = bytearray()
    for i in range(0, len(b), 8):
        g = b[i:i + 8]
        out += bytes(c | ((g[0] >> j) & 1) << 7 for j, c in enumerate(g[1:]))
    return bytes(out)


def parse_version(msg):
    """F0 00 20 29 00 70 <bootloader 5> <application 5> 40 02 F7 -> application build."""
    if msg is None or len(msg) != 19 or not msg.startswith(VERSION_PREFIX) or msg[-1] != 0xF7:
        raise Refused(f"no valid reply to the version query: {msg.hex(' ') if msg else 'nothing'}")
    return msg[13] << 14 | msg[14] << 7 | msg[15]


def status_clean(msg):
    """F0 00 20 29 00 60 0D <14 status bytes> F7 (22 bytes): every status byte 0 on a healthy unit."""
    return (msg is not None and len(msg) == 22 and msg.startswith(STATUS_PREFIX) and msg[-1] == 0xF7
            and not any(msg[7:21]))


# -- links --------------------------------------------------------------------------------------------------------

class Transcript:
    """Every frame sent ('>') and received ('<') with its time in ms from the first frame."""

    def __init__(self):
        self.frames, self.t0 = [], None

    def add(self, d, msg, now):
        """now: the link's clock in ms. Returns the frame's time from the first frame."""
        self.t0 = now if self.t0 is None else self.t0
        self.frames.append([d, round(now - self.t0, 1), msg.hex()])
        return self.frames[-1][1]

    def save(self, path, **meta):
        with open(path, "w") as f:
            json.dump(dict(meta, frames=self.frames), f, separators=(",", ":"))


class RawMidi:
    """ALSA raw MIDI device (/dev/snd/midiCxDy). Realtime bytes (clock) are dropped from replies."""

    def __init__(self, path):
        import select
        self.select = select
        self.fd, self.buf = os.open(path, os.O_RDWR | os.O_NONBLOCK), b""

    def now(self):
        return time.monotonic() * 1000

    def _read(self, timeout):
        if self.select.select([self.fd], [], [], timeout)[0]:
            try:
                return os.read(self.fd, 65536)
            except BlockingIOError:
                pass
        return b""

    def idle(self, seconds):
        """Listen for `seconds`; returns the status bytes of notes, controllers and Start/Continue heard."""
        end, heard = time.monotonic() + seconds, set()
        while (left := end - time.monotonic()) > 0:
            heard |= {b for b in self._read(left) if 0x80 <= b <= 0xEF or b in (0xFA, 0xFB)}
        self.buf = b""
        return sorted(heard)

    def send(self, msg):
        end, view = time.monotonic() + TIMEOUT, memoryview(msg)
        while view:
            if time.monotonic() > end:
                raise TimeoutError("MIDI write timed out")
            if self.select.select([], [self.fd], [], 0.1)[1]:
                try:
                    view = view[os.write(self.fd, view):]
                except BlockingIOError:
                    pass

    def recv(self, prefix, timeout):
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\xF0")
            j = self.buf.find(b"\xF7", i) if i >= 0 else -1
            if i >= 0 and j >= 0:
                msg, self.buf = self.buf[i:j + 1], self.buf[j + 1:]
                if msg.startswith(prefix):
                    return msg
                continue
            left = end - time.monotonic()
            if left <= 0:
                return None
            self.buf += bytes(b for b in self._read(left) if b < 0xF8)

    def close(self):
        os.close(self.fd)


class Replay:
    """A transcript as the unit: each frame sent must equal the next recorded '>' frame; the recorded '<' frames that
    follow it are the replies."""

    def __init__(self, frames):
        self.frames, self.k, self.pending, self.t = frames, 0, [], 0.0

    def now(self):
        return self.t

    def idle(self, seconds):
        return []

    def send(self, msg):
        if self.k >= len(self.frames) or self.frames[self.k][0] != ">" or bytes.fromhex(self.frames[self.k][2]) != msg:
            want = self.frames[self.k][2][:40] if self.k < len(self.frames) else "end of transcript"
            raise AssertionError(f"frame {self.k}: sent {msg.hex()[:40]}, transcript has {want}")
        self.t = self.frames[self.k][1]
        self.k += 1
        while self.k < len(self.frames) and self.frames[self.k][0] == "<":
            self.pending.append((self.frames[self.k][1], bytes.fromhex(self.frames[self.k][2])))
            self.k += 1

    def recv(self, prefix, timeout):
        while self.pending:
            self.t, m = self.pending.pop(0)
            if m.startswith(prefix):
                return m
        return None

    def close(self):
        pass


# -- the client ---------------------------------------------------------------------------------------------------

class Unit:
    def __init__(self, link, transcript=None, log=lambda s: None):
        self.link, self.tr, self.log = link, transcript or Transcript(), log
        self.build, self.session, self.uncertain = None, False, None
        self.rate = None                          # bytes per ms of the last full download stream

    def _send(self, msg):
        if not frame_allowed(msg):
            raise Refused(f"frame not on the allow-list: {msg[:16].hex(' ')}")
        if self.uncertain:
            raise Uncertain(self.uncertain)
        try:
            self.link.send(msg)
        except Exception as e:                    # partial or ambiguous: inside a session the state is unknown
            self.tr.add(">", msg, self.link.now())
            self._fail(f"send failed: {e}")
        self.tr.add(">", msg, self.link.now())

    def _recv(self, prefix=PREFIX):
        try:
            msg = self.link.recv(prefix, TIMEOUT)
        except Exception as e:                    # a link error, maybe inside a download stream: state unknown
            self._fail(f"receive failed: {e!r}")
        if msg is None:
            self._fail("no reply within %g s" % TIMEOUT)
        self.last = self.tr.add("<", msg, self.link.now())
        if msg[-1] != 0xF7 or any(b > 0x7F for b in msg[1:-1]):
            self._fail(f"malformed reply {msg[:24].hex(' ')}")
        return msg

    def _fail(self, why):
        if self.session:
            self.uncertain = why
            raise Uncertain(why)
        raise Refused(why)

    def _expect(self, ok, msg, what):
        if ok:
            return
        if len(msg) == 21 and msg[7] == NAK and max(msg[8:16]) <= 0xF and msg[19] in NAK_TEXT:
            raise Refused(f"{what}: NAK {msg[19]} ({NAK_TEXT[msg[19]]})")    # a well-formed refusal: certain
        self._fail(f"{what}: unexpected reply {msg[:24].hex(' ')}")

    def _status(self, when):
        if self.build not in STATUS_BUILDS:
            return
        self._send(STATUS_REQ)
        m = self._recv(STATUS_PREFIX)
        if not status_clean(m):
            self._fail(f"the unit reports a fault (status check {when}: {m[7:21].hex(' ')})")

    def probe(self):
        self._send(VERSION_REQ)
        self.build = parse_version(self._recv(VERSION_PREFIX))
        if self.build not in BUILDS:
            raise Refused(f"the unit runs {RELEASES.get(self.build, f'firmware build {self.build}')}; reading from "
                          "the unit needs Circus Arrhythmia FM 0.2.2 or the stock firmware 2.0.1: update to Circus "
                          "Arrhythmia FM 0.2.2, or export from a copy of the card")
        return self.build

    def begin(self):
        try:
            heard = self.link.idle(IDLE_LISTEN)
        except Exception as e:
            raise Refused(f"cannot listen to the unit: {e!r}") from e
        if heard:
            raise Refused("the unit is playing or sending notes (status bytes "
                          f"{' '.join('%02X' % b for b in heard)}): stop it and try again")
        self._status("before '@'")
        self.session = True                       # from the attempt on: a failed send or a lost reply is uncertain
        self._send(PREFIX + bytes([BEGIN]) + b"\xF7")
        m = self._recv()
        if m == BEGIN_BUSY:
            self.session = False
            raise Refused("the unit refused the transfer (busy or playing): stop it and try again")
        if m != SESSION_ACK:
            self._fail(f"begin: reply is not the session acknowledgement: {m[:24].hex(' ')}")
        self._status("after '@'")

    def end(self):
        if not self.session or self.uncertain:
            return
        self._status("before 'A'")
        self._send(PREFIX + bytes([END]) + b"\xF7")
        m = self._recv()
        self.session = False
        if m != SESSION_ACK:
            self.uncertain = f"end: reply is not the session acknowledgement: {m[:24].hex(' ')}"
            raise Uncertain(self.uncertain)
        self._status("after 'A'")

    def volumes(self):
        self._send(PREFIX + bytes([LIST, 1]) + b"\xF7")
        m = self._recv()
        self._expect(len(m) == 11 and m[7:9] == bytes([LIST, 1]), m, "volume list")
        return {"internal": bool(m[9] & 1), "card": bool(m[9] & 2)}

    def packs(self):
        self._send(PREFIX + bytes([LIST, 2]) + b"\xF7")
        m = self._recv()
        self._expect(len(m) == 11 and m[7:9] == bytes([LIST, 2]), m, "pack list")
        out = {}
        for _ in range(m[9]):
            e = self._recv()
            self._expect(len(e) >= 11 and e[7:9] == bytes([ENTRY, 2]), e, "pack entry")
            out[e[9]] = e[10:-1].decode("latin1")
        return out

    def items(self, t, slot):
        self._send(PREFIX + bytes([LIST, t, slot]) + b"\xF7")
        m = self._recv()
        self._expect(len(m) == 13 and m[7:10] == bytes([LIST, t, slot]), m, f"list {t}/{slot}")
        out = {}
        for _ in range(m[10] | m[11] << 7):
            e = self._recv()
            self._expect(len(e) >= 12 and e[7:10] == bytes([ENTRY, t, slot]), e, f"entry {t}/{slot}")
            out[e[10]] = e[11:-1].decode("latin1")
        return out

    def size(self, t, slot, idx):
        self._send(PREFIX + bytes([SIZE, t, slot, idx]) + b"\xF7")
        m = self._recv()
        self._expect(len(m) == 20 and m[7:11] == bytes([SIZE, t, slot, idx]) and max(m[11:19]) <= 0xF, m,
                     f"size of {t}/{slot}/{idx}")
        return unnib(m[11:19])

    def download(self, t, slot, idx):
        tsi = bytes([t, slot, idx])
        self._send(PREFIX + bytes([START]) + bytes(8) + tsi + b"\x02" + bytes(8) + b"\xF7")
        m = self._recv()
        if m == PREFIX + bytes([ABSENT]) + tsi + b"\xF7":
            return None
        self._expect(len(m) == 29 and m[7] == START and m[16:20] == tsi + b"\x01" and max(m[20:28]) <= 0xF, m,
                     f"download {t}/{slot}/{idx}")
        size, data, n, t0 = unnib(m[20:28]), bytearray(), 0, self.last
        while True:
            m = self._recv()
            if m[7] not in (DATA, FINISH) or m[16:19] != tsi or max(m[8:16]) > 0xF:
                self._fail(f"download {t}/{slot}/{idx}: unexpected frame {m[:20].hex(' ')}")
            n += 1
            if unnib(m[8:16]) != n:
                self._fail(f"download {t}/{slot}/{idx}: frame counter {unnib(m[8:16])}, expected {n}")
            if m[7] == DATA:
                data += unpack7(m[19:-1])
                continue
            if len(m) != 28 or max(m[19:27]) > 0xF:
                self._fail(f"download {t}/{slot}/{idx}: malformed end frame {m.hex(' ')}")
            data, crc = bytes(data[:size]), unnib(m[19:27])
            if len(data) != size or zlib.crc32(data) != crc:   # the stream ended in order: the session is certain
                raise Refused(f"{'the project file' if t == SESSIONS else f'sample {idx} of pack {slot}'} arrived "
                              f"damaged ({len(data)} of {size} bytes, CRC check failed): try again")
            if self.last > t0:
                self.rate = (wire_bytes(size) - 29) / (self.last - t0)
            return data


def run(unit, body):
    """probe, '@', body(unit), 'A'. 'A' follows only the certain outcomes: success, Refused (raised between
    complete replies) and a project file that does not decode. Any other error, a link error included, and Ctrl+C
    can lie inside a download stream: the state is uncertain and nothing more is sent."""
    unit.probe()
    unit.begin()
    try:
        r = body(unit)
    except (Refused, crsong.ProjectError):
        unit.end()
        raise
    except Uncertain:
        raise
    except Exception as e:
        unit.uncertain = unit.uncertain or f"unexpected error: {e!r}"
        raise Uncertain(unit.uncertain) from e
    unit.end()
    return r


def list_projects(unit):
    """{slot: {"pack": name, "sessions": {index: file name}}} for every pack."""
    def body(u):
        vols = u.volumes()
        out = {}
        for slot, name in sorted(u.packs().items()):
            if slot == 0 and not vols["internal"] or slot > 0 and not vols["card"]:
                continue
            out[slot] = {"pack": name, "sessions": u.items(SESSIONS, slot)}
        return out
    return run(unit, body)


def read_project(unit, slot, index, opts):
    """The project file, the pack's PCM file names and the samples the export needs (opts as midiexport.export).
    -> {"pack", "name", "image", "pcm": [names], "samples": {name: bytes}}"""
    def body(u):
        packs = u.packs()
        if slot not in packs:
            raise Refused(f"the unit has no pack {slot}")
        sessions = u.items(SESSIONS, slot)
        if index not in sessions:
            raise Refused(f"pack {slot} has no project {index}")
        safe_component(sessions[index])
        image = u.download(SESSIONS, slot, index)
        if image is None:
            raise Refused(f"pack {slot} project {index} is empty")
        pcm = u.items(PCM, slot)
        names = {pcm[i]: i for i in sorted(pcm)}
        proj = crsong.Project(crsong.find_image(image))
        _, m, _, _ = midiexport.export(proj, sessions[index], list(names), None, **opts)
        rate, samples = u.rate, {}
        for x in m["samples"]:
            if x["sample"] and x["sample"] not in samples:
                safe_component(x["sample"])                # it becomes part of a ZIP entry name
                n = u.size(PCM, slot, names[x["sample"]])
                if rate is None or wire_bytes(n) / rate > BUDGET_MS:
                    raise Refused(f"sample {x['sample']} ({n} bytes) is too large to read over USB: the transfer "
                                  f"would take longer than {BUDGET_MS / 1000:g} s, and the unit restarts itself during "
                                  "a longer transfer. Export this pack from a copy of the card")
                d = u.download(PCM, slot, names[x["sample"]])
                if d is None:
                    raise Refused(f"sample {x['sample']} vanished from pack {slot}")
                samples[x["sample"]] = d
        return {"pack": packs[slot], "name": sessions[index], "image": image, "pcm": list(names), "samples": samples}
    return run(unit, body)


def export_zip(r, opts):
    proj = crsong.Project(crsong.find_image(r["image"]))
    return midiexport.export(proj, r["name"], r["pcm"], lambda n: r["samples"][n], **opts)[3]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", default=None, help="raw MIDI device of the unit (default: the first 'Circuit Rhythm')")
    ap.add_argument("--transcript", help="write every frame sent and received to this JSON file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the packs and their projects")
    rd = sub.add_parser("read", help="read one project and its samples, write the export ZIP")
    rp = sub.add_parser("replay", help="run 'read' against a transcript instead of the unit")
    rp.add_argument("file")
    rp.add_argument("what", choices=["read", "list"])
    for p in (rd, rp):
        p.add_argument("slot", type=int, nargs="?" if p is rp else None)
        p.add_argument("index", type=int, nargs="?" if p is rp else None)
        p.add_argument("-o", "--out", help="ZIP file to write")
        p.add_argument("--raw", help="also write the project file and the samples as read into this directory")
        midiexport.add_export_args(p)
    a = ap.parse_args(argv)
    say = lambda s: print(s, file=sys.stderr)  # noqa: E731
    tr = Transcript()
    if a.cmd == "replay":
        link = Replay(json.load(open(a.file))["frames"])
        a.cmd = a.what
    else:
        dev = a.device or find_device()
        if dev is None:
            say("no 'Circuit Rhythm' MIDI device found")
            return 2
        link = RawMidi(dev)
    unit = Unit(link, tr)
    code = 0
    try:
        if a.cmd == "list":
            for slot, p in list_projects(unit).items():
                print(f"{slot:2d} {p['pack']}")
                for i, n in sorted(p["sessions"].items()):
                    print(f"     {i:2d} {n}")
        else:
            opts = midiexport.export_opts(a)
            r = read_project(unit, a.slot, a.index, opts)
            z = export_zip(r, opts)
            if a.out:
                with open(a.out, "wb") as f:
                    f.write(z)
            if a.raw:
                write_raw(a.raw, r)
            say(f"{r['pack']} / {r['name']}: {len(r['image'])} bytes, {len(r['samples'])} sample(s), ZIP {len(z)} bytes")
    except Refused as e:
        say(f"refused: {e}")
        code = 2
    except (Uncertain, KeyboardInterrupt) as e:
        say(f"Stopped inside a transfer: {e or 'interrupted'}. Nothing more was sent and nothing is retried. Do "
            "not save on the unit and do not start another read; report this message"
            + (f" and the transcript {a.transcript}" if a.transcript else "") + ".")
        code = 1
    except crsong.ProjectError as e:
        say(f"refused: {e}")
        code = 2
    finally:
        link.close()
        if a.transcript:
            tr.save(a.transcript, build=unit.build, command=a.cmd, slot=getattr(a, "slot", None),
                    index=getattr(a, "index", None))
    return code


def safe_component(name):
    """A file or folder name from the unit (or a transcript) usable as one path component, else Refused."""
    if not re.fullmatch(r"[^/\\\0:]+", name) or name.strip(" .") == "" or name in (".", ".."):
        raise Refused(f"unsafe name from the unit: {name!r}")
    return name


def write_raw(root, r):
    """root/<pack>/Sessions/<project> and root/<pack>/PCM/<sample>; every path stays inside root."""
    base = os.path.realpath(root)
    files = [("Sessions", r["name"], r["image"])] + [("PCM", n, d) for n, d in r["samples"].items()]
    paths = []
    for sub, name, data in files:
        path = os.path.join(base, safe_component(r["pack"]), sub, safe_component(name))
        if os.path.commonpath([base, os.path.realpath(path)]) != base:
            raise Refused(f"path outside {root}: {path!r}")
        paths.append((path, data))
    for path, data in paths:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def find_device(asound="/proc/asound"):
    """The first raw MIDI device of a sound card named 'Circuit Rhythm' (/proc/asound/cards)."""
    try:
        cards = open(os.path.join(asound, "cards")).read()
    except OSError:
        return None
    for m in re.finditer(r"^\s*(\d+) \[[^\]]*\]: [^\n]*- Circuit Rhythm$", cards, re.M):
        dev = f"/dev/snd/midiC{m.group(1)}D0"
        if os.path.exists(dev):
            return dev
    return None


if __name__ == "__main__":
    sys.exit(main())
