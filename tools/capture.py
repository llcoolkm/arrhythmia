"""Reference-audio capture for Codex's DSP emulator: scripted MIDI stimulus on the unit + WAV + JSON sidecar.

    uv run python -I tools/capture.py plan   --source triangle-positive --track-channel 1     # dry run, opens nothing
    uv run python -I tools/capture.py live   --source triangle-positive --track-channel 1 --alsa-device hw:H5,0 \\
        --firmware-label 6009 --pack ... --slot-ui ... --gain-note ... --master-volume-note ... --fx-state-note ...
    uv run python -I tools/capture.py play   (same options, no --alsa-device)      # path (b): recorder writes SD
    uv run python -I tools/capture.py import --sidecar build/capture/X.json --wav /media/.../ZOOM0001.WAV
    add --pilot to live/play for the one-hit gain check (3 s, one note at 1 s)
    uv run python -I tools/capture.py calibrate --source triangle-positive (live options) --swap-diff-db 0.05
        # recorder-chain calibration: 5 hits, frozen per-channel ratio; then live/play --calibration <calibration>.json

Protocol: one 12 s recording per
source (triangle-positive, triangle-negative, silence) plus a no-note control; sequencer stopped; the reference
preset, then at least 2 s idle before the capture anchor; notes at 2, 4, 6 s on channel 16 note 36 (track 1),
velocity 127, note-off 0.5 s later (PlaybackMode 0); stereo L/R, no timing-reference channel. Any deviation from
these values is listed in the sidecar under protocol_deviations.

Only channel-voice note/CC messages are ever sent: no SysEx, so no DSP read (cmd 08 / 0x5E peek) can happen while
the unit plays. Midi.send() enforces this. Host timestamps are CLOCK_MONOTONIC around each write(); they are NOT
note-to-analog latency. TimingReference is the hook for a reference on the capture clock.

On any exit (error, Ctrl-C) the recorder is stopped, its reader joined, the WAV finalised and the sidecar written
with status "error"/"interrupted", the exception, and what was actually sent; then the exception propagates.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import select
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_align  # noqa: E402
import wavio  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FIXTURES = os.path.join(ROOT, "build", "dsp", "hardware-reference")
SCHEMA = "circuit-rhythm-capture/2"
TRACK_NOTES = (36, 38, 40, 41, 43, 45, 47, 48)          # PRG v5.2 p.5, channel 16, tracks 1..8
DRUM_CHANNEL = 16
SOURCES = {"triangle-positive": "triangle-positive.wav", "triangle-negative": "triangle-negative.wav",
           "silence": "silence.wav", "no-note": None}
# ALSA sample format -> (sample_type, bits). S24_LE (24 in 32, low aligned) is absent on purpose: it would need
# repacking, and a mislabelled WAV is worse than a refusal.
ALSA_FORMATS = {"S16_LE": ("pcm", 16), "S24_3LE": ("pcm", 24), "S32_LE": ("pcm", 32), "FLOAT_LE": ("float", 32)}
# Codex's protocol values (answers 2, 3, 5). Deviations are allowed but recorded.
PROTOCOL = {"track": 1, "velocity": 127, "hits": 3, "lead": 2.0, "spacing": 2.0, "duration": 12.0,
            "min_idle_before": 2.0, "note_off_after": 0.5, "preset": "reference", "channels": 2, "rate": 48000,
            "ref_kind": "none"}
PILOT = {"hits": 1, "lead": 1.0, "duration": 3.0}
# recorder-chain calibration take (Codex ruling 2026-10-17): five hits of the mono centred fixture, 1 s apart
CALIBRATE = {"hits": capture_align.CAL_MIN_HITS, "lead": 1.0, "spacing": 1.0, "duration": 7.0}
CAL_SCHEMA = capture_align.CALIBRATION_SCHEMA
UNUSED = capture_align.UNUSED_LABEL
PILOT_TARGET_DBFS = -12.0
# level-view target (master, 2026-10-17): where the matched gains sit now (ch3 -15.95, ch4 -16.00 dBFS peak on the
# fixture); information for dialling in, never a gate
TARGET_PEAK_DBFS, TARGET_TOLERANCE_DB = -16.0, 1.0
GRID_FX_ONOFF = (("BeatRepeat", 39), ("Reverser", 42), ("Gator", 44), ("Autofilter", 46), ("DigitalLofi", 53),
                 ("Phaser", 56), ("Vinyl", 60))     # PRG v5.2 p.4 "OnOff 0-1, default 0"


# ---------------------------------------------------------------- MIDI messages

def note_on(ch, note, vel):
    return bytes([0x90 | (ch - 1), note & 0x7F, vel & 0x7F])


def note_off(ch, note):
    return bytes([0x80 | (ch - 1), note & 0x7F, 0])


def cc(ch, num, val):
    return bytes([0xB0 | (ch - 1), num & 0x7F, val & 0x7F])


def nrpn(ch, msb, lsb, val):
    return cc(ch, 99, msb) + cc(ch, 98, lsb) + cc(ch, 6, val)


def check_voice_only(data):
    """Refuse anything but complete 3-byte note-off/note-on/CC messages (no SysEx, realtime, PC, running status)."""
    if len(data) % 3:
        raise ValueError("partial message")
    for i in range(0, len(data), 3):
        s = data[i]
        if (s & 0xF0) not in (0x80, 0x90, 0xB0) or data[i + 1] > 0x7F or data[i + 2] > 0x7F:
            raise ValueError(f"refusing to send {data[i:i + 3].hex()}: only note/CC messages are allowed")
    return data


def reference_preset(track_ch, grid_fx_ch):
    """The preset Codex approved (answer 1). Numbers and ranges from PRG v5.2 pp.3-5.

    Signed controls at the centre of their 0..127 encoding, derived from the guide's ranges: Pitch 4..124 = -60..+60
    (64 = 0), Slope 0..127 = -64..63 (64 = 0), Pan -64..63 (64 = 0). Master filter CC79=64 / CC71=30 = guide defaults.
    Compressor and soft clip off.
    Grid FX OnOff = 0 only when grid_fx_ch is given: the guide gives no channel for these CCs, so by default none are
    sent and the operator turns Grid FX off on the unit and records it in --fx-state-note.
    FX bypass (NRPN 1:21) is NOT sent: Codex forbids 1, and the meaning of 0 is not checked in code.
    These are known filter/level controls, not proof of a bit-transparent path.
    """
    t = track_ch
    items = [
        ("PlaybackMode", "cc", t, 12, 0), ("Reverse", "cc", t, 14, 0), ("Choke", "cc", t, 15, 0),
        ("Slice", "cc", t, 16, 0), ("Pitch (0 st)", "cc", t, 21, 64), ("StartCoarse", "cc", t, 22, 0),
        ("StartFine", "cc", t, 86, 0), ("LengthCoarse", "cc", t, 23, 127), ("LengthFine", "cc", t, 87, 127),
        ("Slope (0)", "cc", t, 24, 64), ("Distortion", "cc", t, 25, 0), ("HPF", "cc", t, 26, 0),
        ("LPF", "cc", t, 27, 127), ("Resonance", "cc", t, 28, 0), ("Level", "cc", t, 29, 100),
        ("Pan (centre)", "cc", t, 30, 64), ("ReverbSend", "cc", t, 31, 0), ("DelaySend", "cc", t, 32, 0),
        ("Dipper depth", "nrpn", t, (1, 4), 0),
        ("Master filter freq", "cc", DRUM_CHANNEL, 79, 64), ("Master filter res", "cc", DRUM_CHANNEL, 71, 30),
        ("Compressor on/off", "nrpn", DRUM_CHANNEL, (1, 22), 0),
        ("Compressor soft clip", "nrpn", DRUM_CHANNEL, (1, 26), 0),
    ]
    if grid_fx_ch is not None:              # opt-in only: the channel is unverified, so nothing is sent by default
        items += [(f"Grid FX {n} OnOff (channel unverified)", "cc", grid_fx_ch, num, 0) for n, num in GRID_FX_ONOFF]
    return items


def encode_setting(item):
    name, kind, ch, num, val = item
    data = nrpn(ch, num[0], num[1], val) if kind == "nrpn" else cc(ch, num, val)
    return {"name": name, "kind": kind, "channel": ch, "number": list(num) if kind == "nrpn" else num,
            "value": val, "bytes": data.hex()}


# ---------------------------------------------------------------- stimulus

def build_schedule(a):
    """Planned events, times in seconds from the capture anchor."""
    note = TRACK_NOTES[a.track - 1]
    events = []
    for k in range(a.hits):
        t = a.lead + k * a.spacing
        if a.source == "no-note":
            events.append({"i": len(events), "hit": k, "kind": "marker", "planned_s": round(t, 6), "bytes": ""})
            continue
        events.append({"i": len(events), "hit": k, "kind": "note_on", "planned_s": round(t, 6),
                       "bytes": note_on(DRUM_CHANNEL, note, a.velocity).hex()})
        if a.note_off_after is not None:
            events.append({"i": len(events), "hit": k, "kind": "note_off",
                           "planned_s": round(t + a.note_off_after, 6),
                           "bytes": note_off(DRUM_CHANNEL, note).hex()})
    events.sort(key=lambda e: (e["planned_s"], e["i"]))
    last = max(e["planned_s"] for e in events) if events else 0.0
    if last >= a.duration:
        raise SystemExit(f"schedule ends at {last} s, not inside the {a.duration} s recording")
    for e in events:
        e["sent"] = False
    return events


def settings_list(a):
    out = []
    if a.sample_choice is not None:
        out.append(encode_setting(("SampleChoice", "cc", a.track_channel, 8, a.sample_choice)))
    if a.preset == "reference":
        out += [encode_setting(i) for i in reference_preset(a.track_channel, a.grid_fx_channel)]
    for s in out:
        check_voice_only(bytes.fromhex(s["bytes"]))
        s["sent"] = False
    return out


def unused_channels(a):
    return sorted(int(k) for k, v in a.channel_map.items() if v.strip().lower() == UNUSED)


def protocol_deviations(a):
    """Against PROTOCOL; a calibration take against its own schedule (CALIBRATE). Channels labelled unused in the
    channel map do not count towards the stereo L/R pair."""
    dev = []
    proto = {**PROTOCOL, **CALIBRATE} if a.cmd == "calibrate" else PROTOCOL
    for k in ("track", "velocity", "hits", "lead", "spacing", "duration", "note_off_after", "preset", "rate",
              "ref_kind"):
        if getattr(a, k) != proto[k]:
            dev.append(f"{k}={getattr(a, k)!r} (protocol {proto[k]!r})")
    unused = unused_channels(a)
    used = a.channels - len(unused)
    if used != PROTOCOL["channels"]:
        dev.append(f"channels={a.channels!r}" + (f" with {unused} {UNUSED}, {used} used" if unused else "")
                   + f" (protocol {PROTOCOL['channels']!r})")
    if a.idle_before < PROTOCOL["min_idle_before"]:
        dev.append(f"idle_before={a.idle_before} < {PROTOCOL['min_idle_before']} s")
    if a.sample_choice is not None:
        dev.append("SampleChoice sent (Codex: not for the first run)")
    if a.pilot:
        dev.append("pilot take (gain check only)")
    if a.ref_kind != "none" and a.ref_channel is not None and used <= PROTOCOL["channels"]:
        dev.append(f"ref_channel={a.ref_channel} on a {used}-channel take: the reference takes one of the "
                   "stereo L/R channels, which is then not compared (a reference must be an extra channel)")
    return dev + calibration_deviations(a)


def calibration_deviations(a):
    """A take's chain differs from its calibration's (recalibrate after any gain or cabling change)."""
    cal = getattr(a, "calibration_info", None)
    if not cal:
        return []
    chain = cal.get("chain") or {}
    return [f"calibration {k} {chain.get(k)!r} != take {v!r} (recalibrate after any gain/cabling change)"
            for k, v in (("gain_note", a.gain_note), ("channel_map", a.channel_map), ("interface", a.interface),
                         ("channels", a.channels)) if chain.get(k) != v]


def load_calibration(path):
    """The block a take's sidecar embeds from an accepted calibration JSON (capture.py calibrate): path, sha256 of the
    JSON and of its WAV (checked against the file), ratios, spread, the chain it was measured on. Refuses anything
    else before a device is opened."""
    try:
        c = json.load(open(path))
    except (OSError, ValueError) as e:
        raise SystemExit(f"--calibration {path}: {e}")
    if not isinstance(c, dict) or c.get("schema") != CAL_SCHEMA:
        raise SystemExit(f"--calibration {path}: not a {CAL_SCHEMA} file")
    if c.get("accepted") is not True:
        raise SystemExit(f"--calibration {path}: not accepted: {c.get('reasons')}")
    res, take = c.get("result") or {}, c.get("take") or {}
    w = take.get("wav") or {}
    wav = os.path.join(os.path.dirname(os.path.abspath(path)), str(w.get("file")))
    if not w.get("file") or not os.path.isfile(wav) or sha256(wav) != w.get("sha256"):
        raise SystemExit(f"--calibration {path}: its WAV {w.get('file')!r} is missing or changed")
    chans = res.get("channels") or {}
    return {"path": os.path.abspath(path), "sha256": sha256(path), "created_utc": c.get("created_utc"),
            "wav": {"file": w["file"], "sha256": w["sha256"]}, "reference_channel": res.get("reference_channel"),
            "ratios": res.get("ratios"), "ratios_db": {k: v.get("mean_db") for k, v in chans.items()},
            "delay_samples": {k: v.get("delay_samples") for k, v in chans.items()},
            "spread_db": {k: {"max_min": v.get("spread_db"), "std": v.get("std_db"), "n": v.get("n")}
                          for k, v in chans.items()},
            "chain": c.get("chain"), "swap_evidence": c.get("swap_evidence")}


class TimingReference:
    """Hook for an absolute-latency reference on the capture clock. The default records nothing and claims nothing.

    A subclass may emit a reference pulse into a spare capture channel at each note.
    before_send()/after_send() run just around the MIDI write; their return value is stored in the event record.
    Do not add anything that talks to the unit here.
    """
    kind = "none"
    channel = None
    note = "no timing reference: latency is not measurable from this capture; only relative alignment is"

    def before_send(self, event):
        return None

    def after_send(self, event):
        return None

    def describe(self):
        return {"kind": self.kind, "channel": self.channel, "note": self.note}


class DeclaredReference(TimingReference):
    """A reference produced outside this tool (cable tap, external clicker) declared on the command line."""

    def __init__(self, kind, channel, note):
        self.kind, self.channel, self.note = kind, channel, note


# ---------------------------------------------------------------- devices (only used by live/play)

class Midi:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.last_written = 0          # bytes of the current/last send() that reached write()

    def send(self, data, timeout=1.0):
        check_voice_only(data)
        self.last_written = 0
        deadline = time.monotonic() + timeout
        while self.last_written < len(data):
            if time.monotonic() > deadline:
                raise TimeoutError(f"MIDI write timed out after {self.last_written} of {len(data)} bytes")
            if select.select([], [self.fd], [], 0.05)[1]:
                try:
                    self.last_written += os.write(self.fd, data[self.last_written:])
                except BlockingIOError:
                    pass

    def close(self):
        os.close(self.fd)


# set by a UI's quit key (levelview): every wait below raises Cancelled, so record_live() cleans up as on Ctrl-C
CANCEL = threading.Event()


class Cancelled(KeyboardInterrupt):
    """The operator quit (q in the UI); handled like Ctrl-C: status 'interrupted'."""


def check_cancel():
    if CANCEL.is_set():
        raise Cancelled("cancelled by the operator")


def sleep(seconds):
    """time.sleep that ends early with Cancelled."""
    if CANCEL.wait(seconds):
        raise Cancelled("cancelled by the operator")


def wait_until(ns):
    while True:
        check_cancel()
        left = ns - time.monotonic_ns()
        if left <= 0:
            return
        if left > 3_000_000:
            time.sleep(min(left - 2_000_000, 50_000_000) / 1e9)


def _send_logged(midi, rec, data):
    try:
        midi.send(data)
    except BaseException as e:
        rec["bytes_sent"] = getattr(midi, "last_written", None)
        rec["send_error"] = repr(e)
        raise
    rec["bytes_sent"] = len(data)
    rec["sent"] = True


def send_settings(midi, settings, settle):
    for s in settings:
        s["host_ns"] = time.monotonic_ns()
        _send_logged(midi, s, bytes.fromhex(s["bytes"]))
        time.sleep(0.005)
    sleep(settle)


def run_schedule(midi, events, anchor_ns, ref):
    for e in events:
        wait_until(anchor_ns + int(e["planned_s"] * 1e9))
        e["ref_before"] = ref.before_send(e)
        e["host_send_start_ns"] = time.monotonic_ns()
        if e["bytes"]:
            _send_logged(midi, e, bytes.fromhex(e["bytes"]))
        else:
            e["sent"] = True           # marker: nothing to send
        e["host_send_end_ns"] = time.monotonic_ns()
        e["ref_after"] = ref.after_send(e)
        e["late_ms"] = round((e["host_send_start_ns"] - anchor_ns) / 1e6 - e["planned_s"] * 1e3, 3)


class Recorder:
    """arecord -t raw to a pipe; we write the WAV and log (frames, CLOCK_MONOTONIC ns) at every chunk arrival."""

    def __init__(self, argv, path, rate, channels, fmt, frames_wanted, on_chunk=None):
        self.sample_type, self.bits = ALSA_FORMATS[fmt]
        self.block = channels * self.bits // 8
        self.rate, self.channels, self.wanted = rate, channels, frames_wanted
        self.argv, self.path = argv, path
        self.frames, self.log, self.first = 0, [], threading.Event()
        self.done = threading.Event()
        self.proc = self.f = None
        self.stderr = []
        self.pump_error = None
        self.on_chunk = on_chunk          # called with each block of whole frames as written (a UI's live view)

    def start(self):
        self.f = open(self.path, "wb")
        self.f.write(wavio.header(self.rate, self.channels, self.sample_type, self.bits, 0))
        self.proc = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.t = threading.Thread(target=self._pump, daemon=True)
        self.e = threading.Thread(target=self._drain_stderr, daemon=True)
        self.t.start()
        self.e.start()

    def _drain_stderr(self):
        for line in self.proc.stderr:
            self.stderr.append(line.decode(errors="replace"))

    def _pump(self):
        buf = b""
        try:
            while self.frames < self.wanted:
                chunk = self.proc.stdout.read1(65536)
                now = time.monotonic_ns()
                if not chunk:
                    break
                buf += chunk
                n = min(len(buf) // self.block, self.wanted - self.frames)
                if n:
                    self.f.write(buf[:n * self.block])
                    if self.on_chunk:
                        self.on_chunk(bytes(buf[:n * self.block]))
                    buf = buf[n * self.block:]
                    self.frames += n
                    self.log.append([self.frames, now])
                    self.first.set()
        except Exception as e:          # noqa: BLE001 - reported in the sidecar
            self.pump_error = repr(e)
        finally:
            self.done.set()

    def wait(self, timeout):
        return self.done.wait(timeout)

    def close(self):
        """Idempotent: stop arecord, join the reader threads, then finalise and close the WAV."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc is not None:
            self.t.join(10)
            self.e.join(5)
        if self.f is not None and not self.f.closed:
            wavio.patch_sizes(self.f, self.frames * self.block)
            self.f.close()
        return "".join(self.stderr)


# ---------------------------------------------------------------- metadata

def git_state():
    def g(*args):
        try:
            return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(g("status", "--porcelain", "--untracked-files=no"))}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def source_info(a):
    name = SOURCES[a.source]
    info = {"name": a.source, "fixture": name, "pack": a.pack, "slot_ui": a.slot_ui, "slot_encoded": a.slot,
            "selected_by": "SampleChoice CC 8" if a.sample_choice is not None else "operator on the unit"}
    man = os.path.join(FIXTURES, "manifest.json")
    if name and os.path.exists(man):
        m = json.load(open(man)).get(name)
        if m:
            info["fixture_manifest"] = m
    if name and os.path.exists(os.path.join(FIXTURES, name)):
        info["fixture_sha256"] = sha256(os.path.join(FIXTURES, name))
    return info


def keyboard_note(a):
    """The unit's keyboard-note transposition as known to the operator. This tool sends MIDI and records audio; it
    never observes the transposition, so without --keyboard-note-semitones/--keyboard-note-evidence it is recorded
    as unknown (capture_align then reports the absolute keyboard-note state as unverified)."""
    if a.keyboard_note_semitones is None:
        return {"state": "unknown", "why": "not measured: capture.py does not observe the unit's keyboard-note "
                                           "transposition and none was given (--keyboard-note-semitones)"}
    return {"state": "measured", "semitones": a.keyboard_note_semitones, "evidence": a.keyboard_note_evidence,
            "given_by": "operator (--keyboard-note-semitones/--keyboard-note-evidence)"}


def sidecar(a, path_kind, events, settings, ref):
    return {
        "schema": SCHEMA, "path": path_kind, "status": "started", "error": None, "pilot": a.pilot,
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(), "git": git_state(),
        "protocol_deviations": protocol_deviations(a),
        "unit": {"firmware_label": a.firmware_label, "firmware_sha256": a.firmware_sha256, "midi_device": a.midi_device,
                 "master_volume_note": a.master_volume_note, "fx_state_note": a.fx_state_note},
        "capture": {"interface": a.interface, "alsa_device": a.alsa_device if path_kind == "a" else None,
                    "rate": a.rate, "channels": a.channels, "format": a.format if path_kind == "a" else None,
                    "gain_note": a.gain_note, "channel_map": a.channel_map, "recorder_settings_note": a.recorder_note},
        "source": source_info(a),
        "settings": {"preset": a.preset, "track_channel": a.track_channel, "grid_fx_channel": a.grid_fx_channel,
                     "fx_bypass": "not sent (Codex); actual state in unit.fx_state_note", "sent": settings,
                     "sequencer": "stopped (operator)", "operator_notes": a.notes},
        "keyboard_note": keyboard_note(a),
        "stimulus": {"track": a.track, "midi_channel": DRUM_CHANNEL, "note": TRACK_NOTES[a.track - 1],
                     "velocity": a.velocity, "hits": a.hits, "lead_s": a.lead, "spacing_s": a.spacing,
                     "duration_s": a.duration, "idle_before_s": a.idle_before, "settle_s": a.settle,
                     "note_off_policy": (f"note-off {a.note_off_after} s after each note-on (PlaybackMode 0)"
                                         if a.note_off_after is not None else "no note-off sent"),
                     "clock": "CLOCK_MONOTONIC ns (host); not the audio clock",
                     "events": events},
        "timing_reference": ref.describe(),
        "calibration": getattr(a, "calibration_info", None),
        "dsp_reads": "none: this tool sends note/CC messages only",
        "wav": None,
    }


def level_check(path):
    """Whole-file peak per channel and full-scale-code count; used for the pilot and to reject clipped takes."""
    info, data = wavio.read(path)
    out = []
    for c, ch in enumerate(data):
        pk = max(map(abs, ch), default=0.0)
        out.append({"channel": c + 1, "peak_dbfs": round(20 * math.log10(pk), 2) if pk else None,
                    "full_scale_samples": wavio.count_full_scale(ch, info)})
    return {"channels": out, "clipped": any(c["full_scale_samples"] for c in out),
            "pilot_target_dbfs": PILOT_TARGET_DBFS}


# ---------------------------------------------------------------- commands

def stem(a):
    utc = datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')
    if a.cmd == "calibrate":
        return f"calibration-{utc}"
    return ("pilot-" if a.pilot else "") + f"{a.source}-{utc}"


def arecord_argv(a):
    return ["arecord", "-D", a.alsa_device, "-f", a.format, "-r", str(a.rate), "-c", str(a.channels),
            "-t", "raw", "-q"]


def cmd_plan(a):
    events, settings, ref = build_schedule(a), settings_list(a), make_ref(a)
    print(f"# dry run: no device is opened. source={a.source} track={a.track} "
          f"note={TRACK_NOTES[a.track - 1]} ch{DRUM_CHANNEL} vel={a.velocity}")
    if settings:
        print(f"# settings, sent once, then {a.settle} s settle:")
        for s in settings:
            print(f"  {s['bytes']:<20} {s['name']} ch{s['channel']} {s['kind']} {s['number']} = {s['value']}")
    print(f"# then {a.idle_before} s idle; capture anchor (t = 0); duration {a.duration} s")
    for e in events:
        print(f"  t={e['planned_s']:8.3f} s  {e['kind']:<9} {e['bytes']}")
    print(f"# path (a) recorder: {' '.join(arecord_argv(a)) if a.alsa_device else '(set --alsa-device)'}")
    print(f"# timing reference: {ref.describe()}")
    cal = getattr(a, "calibration_info", None)
    if cal:
        print(f"# chain calibration: {cal['path']} ratios {cal['ratios_db']} dB (sha256 {cal['sha256'][:12]})")
    for d in protocol_deviations(a):
        print(f"# protocol deviation: {d}")
    print("# check: all bytes are note/CC messages (no SysEx, no DSP access)")


def make_ref(a):
    if a.ref_kind == "none":
        return TimingReference()
    return DeclaredReference(a.ref_kind, a.ref_channel, a.ref_note or "")


def require(a, *names):
    names = list(names) + ["firmware_label", "gain_note", "master_volume_note", "fx_state_note"]
    if a.preset == "reference" or a.sample_choice is not None:
        names.append("track_channel")
    if a.source != "no-note":
        names += ["pack", "slot_ui"]
    missing = [n for n in names if getattr(a, n) in (None, "")]
    if missing:
        raise SystemExit("missing: " + ", ".join("--" + n.replace("_", "-") for n in missing))


def out_paths(a):
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, stem(a))
    return base + ".wav", base + ".json"


def write_json(path, meta):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)


def finish_status(meta, exc):
    if exc is None:
        meta["status"] = "complete"
    else:
        meta["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        meta["error"] = repr(exc)


def note_off_pending(midi, events):
    """After an error or a quit: a note-off for every note-on sent whose own note-off was not (the unit must not be
    left holding a note). Returns the bytes sent; failures are ignored (the port may be what failed)."""
    sent = b""
    for e in events:
        if e["kind"] == "note_on" and e.get("sent") and not any(
                x["kind"] == "note_off" and x["hit"] == e["hit"] and x.get("sent") for x in events):
            off = bytes([0x80 | (DRUM_CHANNEL - 1), bytes.fromhex(e["bytes"])[1], 0])
            try:
                midi.send(off)
                sent += off
            except Exception:              # noqa: BLE001 - best effort during cleanup
                pass
    return sent


def record_live(a, wav, meta, events, settings, ref, on_chunk=None):
    """Path (a): preset, idle, arecord, schedule. Fills meta (status, capture, wav, level_check) and returns the
    exception (None on success); the caller writes the metadata and re-raises. On an error or a quit (CANCEL), a
    pending note-off is sent (meta stimulus.cleanup_note_off) and arecord is terminated."""
    frames = int(round(a.duration * a.rate))
    argv = a.recorder_cmd.split() if a.recorder_cmd else arecord_argv(a)
    meta["capture"]["recorder_argv"] = argv
    midi = rec = anchor = None
    exc = None
    try:
        check_cancel()
        midi = Midi(a.midi_device)
        send_settings(midi, settings, a.settle)
        sleep(a.idle_before)
        rec = Recorder(argv, wav, a.rate, a.channels, a.format, frames, on_chunk)
        rec.start()
        if not rec.first.wait(5.0):
            raise RuntimeError("recorder produced no audio within 5 s; no note was sent")
        # estimated host time of sample 0: first chunk arrival minus the frames it carried (buffering before that
        # arrival is unknown, so this is an estimate, not a latency measurement)
        anchor = rec.log[0][1] - int(rec.log[0][0] * 1e9 / a.rate)
        run_schedule(midi, events, anchor, ref)
        deadline = time.monotonic() + a.duration + 10
        while not rec.wait(0.05):
            check_cancel()
            if time.monotonic() > deadline:
                raise RuntimeError("recorder did not deliver the full duration")
    except BaseException as e:
        exc = e
    finally:
        if midi and exc is not None:
            off = note_off_pending(midi, events)
            if off:
                meta["stimulus"]["cleanup_note_off"] = off.hex()
        err = rec.close() if rec else ""
        if midi:
            midi.close()
        finish_status(meta, exc)
        meta["capture"].update({"anchor_ns": anchor,
                                "anchor": "events[].planned_s relative to first-chunk arrival minus its frames/rate "
                                          "(estimated host time of sample 0)"})
        if rec:
            meta["capture"].update({"frames": rec.frames, "frames_wanted": frames, "recorder_stderr": err,
                                    "pump_error": rec.pump_error,
                                    "arrival_log": rec.log if len(rec.log) <= 4000 else rec.log[::len(rec.log) // 4000 + 1]})
            if rec.frames < frames:
                meta["capture"]["warning"] = f"short recording: {rec.frames} of {frames} frames"
            if os.path.exists(wav):
                meta["wav"] = {"file": os.path.basename(wav), "sha256": sha256(wav), "imported_from": None,
                               "partial": exc is not None or rec.frames < frames}
                try:
                    meta["level_check"] = level_check(wav)
                except Exception as le:        # noqa: BLE001
                    meta["level_check"] = {"error": repr(le)}
    return exc


def print_levels(a, meta):
    lc = meta.get("level_check", {})
    if lc.get("channels"):
        print("peaks: " + ", ".join(f"ch{c['channel']} {c['peak_dbfs']} dBFS" for c in lc["channels"])
              + (" CLIPPED - take is invalid" if lc.get("clipped") else "")
              + (f" (pilot target about {PILOT_TARGET_DBFS} dBFS)" if a.pilot else ""))
    if "warning" in meta["capture"] or "overrun" in meta["capture"].get("recorder_stderr", ""):
        print(f"WARNING: {meta['capture'].get('warning', '')} {meta['capture'].get('recorder_stderr', '').strip()}")


def warn_uncalibrated(a):
    if not getattr(a, "calibration_info", None) and a.channels - len(unused_channels(a)) > 1 and not a.pilot:
        print("WARNING: no --calibration: capture_align will refuse a verdict on this take (no chain calibration)")


def cmd_live(a):
    require(a, "alsa_device")
    warn_uncalibrated(a)
    events, settings, ref = build_schedule(a), settings_list(a), make_ref(a)
    wav, side = out_paths(a)
    meta = sidecar(a, "a", events, settings, ref)
    exc = record_live(a, wav, meta, events, settings, ref)
    write_json(side, meta)
    if exc is not None:
        print(f"{meta['status']}: partial data and metadata kept in {side}", file=sys.stderr)
        raise exc
    print(f"wrote {wav}\n      {side}")
    print_levels(a, meta)


def chain_info(a):
    """The recorder chain a calibration is valid for; a take that differs records a deviation."""
    return {"interface": a.interface, "channels": a.channels, "channel_map": a.channel_map, "gain_note": a.gain_note,
            "format": a.format, "rate": a.rate, "alsa_device": a.alsa_device, "recorder_note": a.recorder_note,
            "master_volume_note": a.master_volume_note}


def calibration_gates(res, swap_diff_db):
    """The calibration's gates for display (capture_align.calibration_stats gates plus the cable-swap evidence)."""
    gates = list(res.get("gates") or [])
    bound = capture_align.CAL_SPREAD_MAX_DB
    if swap_diff_db is None:
        gates.append({"name": "swap evidence", "status": "INFO", "value": "not given",
                      "threshold": f"|Rhythm R - L| <= {bound:g} dB (attribution unverified)"})
    else:
        gates.append({"name": "swap evidence", "status": "PASS" if abs(swap_diff_db) <= bound else "FAIL",
                      "value": f"{swap_diff_db:+g} dB", "threshold": f"|Rhythm R - L| <= {bound:g} dB"})
    return gates


def run_calibration(a, on_chunk=None):
    """Path (a) calibration take: the reference preset, then CALIBRATE hits of the mono centred fixture; the take's
    sidecar goes under "take" of a circuit-rhythm-calibration/1 JSON with the frozen per-channel ratios
    (capture_align.calibration_stats). Raw WAV kept; a refused calibration is written too. Headless: returns
    (cal, wav, side, exc); the presenters (cmd_calibrate, levelview) print or draw it. on_chunk: Recorder hook."""
    events, settings, ref = build_schedule(a), settings_list(a), make_ref(a)
    wav, side = out_paths(a)
    take = sidecar(a, "a", events, settings, ref)
    swap = {"note": a.swap_evidence, "rhythm_r_minus_l_db": a.swap_diff_db,
            "use": "evidence only: the whole measured ratio is attributed to the recorder chain and frozen; a "
                   f"|Rhythm R - L| over {capture_align.CAL_SPREAD_MAX_DB:g} dB refuses the calibration (it would "
                   "remove a genuine Rhythm imbalance)"}
    cal = {"schema": CAL_SCHEMA, "created_utc": take["created_utc"], "status": "recording", "accepted": False,
           "reasons": [], "chain": chain_info(a), "swap_evidence": swap,
           "target": {"peak_dbfs": a.target_peak, "tolerance_db": a.target_tolerance,
                      "role": "information for setting the gains; not a gate"},
           "ruling": "Codex 2026-10-17: freeze one recorder-channel ratio from five pilot hits per session, preserve "
                     "raw WAVs, log ratio/spread, recalibrate after any gain/cabling change, one shared take gain",
           "result": None, "take": take}
    exc = record_live(a, wav, take, events, settings, ref, on_chunk)
    if exc is None:
        write_json(side, cal)                     # the take is bound to its WAV; then analysed from this file
        try:
            res = capture_align.calibration_stats(wav, side)
        except (ValueError, OSError) as e:        # wavio.WavError is a ValueError
            res = {"accepted": False, "reasons": [f"analysis refused: {e}"]}
        reasons = list(res["reasons"])
        if take.get("level_check", {}).get("clipped"):
            reasons.append("level check: full-scale samples")
        if a.swap_diff_db is not None and not abs(a.swap_diff_db) <= capture_align.CAL_SPREAD_MAX_DB:
            reasons.append(f"cable swap: Rhythm R - L = {a.swap_diff_db} dB, over "
                           f"{capture_align.CAL_SPREAD_MAX_DB:g} dB: the ratio is not the recorder's alone")
        if a.swap_diff_db is None and not a.swap_evidence:
            cal["attribution"] = "unverified: no cable-swap evidence given"
        res["gates"] = calibration_gates(res, a.swap_diff_db)
        cal.update({"result": res, "accepted": not reasons, "reasons": reasons,
                    "status": "accepted" if not reasons else "refused"})
    else:
        cal["status"] = take["status"]
    write_json(side, cal)
    return cal, wav, side, exc


def use_tui(a):
    return not a.plain and sys.stdout.isatty() and sys.stdin.isatty()


def cmd_calibrate(a):
    require(a, "alsa_device")
    if use_tui(a):
        import levelview                          # the UI layer (Textual) is loaded only when drawn
        cal, wav, side, exc = levelview.run_calibrate_tui(a)
    else:
        cal, wav, side, exc = run_calibration(a)
    if exc is not None:
        print(f"{cal['status']}: partial data and metadata kept in {side}", file=sys.stderr)
        raise exc
    print(f"wrote {wav}\n      {side}")
    take = cal["take"]
    print_levels(a, take)
    res = cal["result"]
    for c, v in (res.get("channels") or {}).items():
        print(f"ch{c} / ch{res['reference_channel']}: {v['mean_db']:+.4f} dB (ratio {v['ratio']:.6f}), spread "
              f"{v['spread_db']:.4f} dB max-min, std {v['std_db']:.4f} dB over {v['n']} hits {v['per_hit_db']}; "
              f"delay {v['delay_samples']} samples (spread {v['delay_spread_samples']}; positive = later; not applied)")
        if v.get("delay_exceeds_skew_max"):
            print(f"WARNING: ch{c} delay {v['delay_samples']} samples > {capture_align.SKEW_MAX:g}: "
                  + res["delay_note"])
    if cal.get("attribution"):
        print(f"WARNING: attribution {cal['attribution']}")
    if not cal["accepted"]:
        raise SystemExit("calibration REFUSED: " + "; ".join(cal["reasons"]))
    print(f"calibration accepted: use --calibration {side} on every take of this session")


def cmd_play(a):
    require(a)
    warn_uncalibrated(a)
    events, settings, ref = build_schedule(a), settings_list(a), make_ref(a)
    _, side = out_paths(a)
    meta = sidecar(a, "b", events, settings, ref)
    midi = anchor = None
    exc = None
    try:
        midi = Midi(a.midi_device)
        send_settings(midi, settings, a.settle)
        print(f"Start the recorder now (record at least {a.duration + 4:.0f} s). Press Enter once it is recording.")
        if not a.no_prompt:
            input()
        time.sleep(a.idle_before)
        anchor = time.monotonic_ns()
        run_schedule(midi, events, anchor, ref)
        wait_until(anchor + int(a.duration * 1e9))
    except BaseException as e:
        exc = e
    finally:
        if midi:
            midi.close()
        finish_status(meta, exc)
        meta["status"] = "stimulus-" + meta["status"]
        meta["capture"]["anchor_ns"] = anchor
        meta["capture"]["anchor"] = ("events[].planned_s relative to a host instant after Enter + idle_before; the "
                                     "recorder's file start is unknown, capture_align fits the offset")
        write_json(side, meta)
    if exc is not None:
        raise exc
    print(f"Stimulus done; stop the recorder. Then:\n  uv run python -I tools/capture.py import --sidecar {side} "
          f"--wav <file from the SD card>")


def cmd_import(a):
    meta = json.load(open(a.sidecar))
    if meta.get("schema") != SCHEMA:
        raise SystemExit("not a capture sidecar (or an older schema)")
    if meta.get("wav"):
        raise SystemExit("sidecar already has a WAV")
    if meta.get("status") != "stimulus-complete":
        raise SystemExit(f"stimulus status is {meta.get('status')!r}, not complete: re-run the take")
    try:
        info = wavio.read_info(a.wav)
    except wavio.WavError as e:
        raise SystemExit(f"refusing {a.wav}: {e}")
    dst = os.path.splitext(a.sidecar)[0] + ".wav"
    if os.path.exists(dst):
        raise SystemExit(f"{dst} exists")
    shutil.copyfile(a.wav, dst)
    meta["capture"].update({"rate": info["rate"], "channels": info["channels"],
                            "format": f"{info['sample_type']}{info['bits']}", "wav_format_tag": info["tag"],
                            "wav_chunks": info["chunks"], "frames": info["frames"]})
    if info["rate"] != PROTOCOL["rate"]:
        meta["protocol_deviations"].append(f"file rate {info['rate']}")
        print(f"WARNING: file rate is {info['rate']} Hz, not {PROTOCOL['rate']}")
    cal_ch = ((meta.get("calibration") or {}).get("chain") or {}).get("channels")
    if cal_ch is not None and cal_ch != info["channels"]:
        meta["protocol_deviations"].append(f"calibration channels {cal_ch!r} != file {info['channels']!r}")
        print(f"WARNING: the file has {info['channels']} channels, the calibration {cal_ch}")
    meta["wav"] = {"file": os.path.basename(dst), "sha256": sha256(dst), "imported_from": os.path.abspath(a.wav),
                   "source_sha256": sha256(a.wav), "partial": False}
    meta["level_check"] = level_check(dst)
    meta["status"] = "complete"
    write_json(a.sidecar, meta)
    print(f"imported {a.wav} -> {dst} ({info['sample_type']} {info['bits']}-bit, {info['channels']} ch, "
          f"{info['rate']} Hz, {info['frames'] / info['rate']:.2f} s)"
          + (" CLIPPED - take is invalid" if meta["level_check"]["clipped"] else ""))


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--source", choices=list(SOURCES), required=True)
    c.add_argument("--track", type=int, choices=range(1, 9), default=PROTOCOL["track"])
    c.add_argument("--track-channel", type=int, choices=range(1, 17), default=None,
                   help="the track's configured MIDI channel, checked on the unit (no default: 1 is not universal)")
    c.add_argument("--keyboard-note-semitones", type=float, default=None,
                   help="the unit's keyboard-note transposition, MEASURED (with --keyboard-note-evidence); "
                        "default: recorded as unknown")
    c.add_argument("--keyboard-note-evidence", default=None,
                   help="the measurement behind --keyboard-note-semitones")
    c.add_argument("--grid-fx-channel", type=int, choices=range(1, 17), default=None,
                   help="send Grid FX OnOff = 0 on this channel (unverified; default: not sent)")
    c.add_argument("--velocity", type=int, default=PROTOCOL["velocity"])
    # schedule defaults (None): PROTOCOL, or CALIBRATE for calibrate, filled in by normalise()
    c.add_argument("--hits", type=int, default=None, help=f"default {PROTOCOL['hits']} (calibrate {CALIBRATE['hits']})")
    c.add_argument("--lead", type=float, default=None, help="seconds from the anchor to hit 1")
    c.add_argument("--spacing", type=float, default=None)
    c.add_argument("--duration", type=float, default=None)
    c.add_argument("--idle-before", type=float, default=PROTOCOL["min_idle_before"],
                   help="idle seconds after the settings, before the capture anchor (protocol: >= 2)")
    c.add_argument("--note-off-after", type=float, default=PROTOCOL["note_off_after"],
                   help="seconds; negative = send no note-off")
    c.add_argument("--preset", choices=["none", "reference"], default=PROTOCOL["preset"])
    c.add_argument("--sample-choice", type=int, default=None,
                   help="send SampleChoice CC 8 = value 0..127 (Codex: not for the first run)")
    c.add_argument("--settle", type=float, default=1.0)
    c.add_argument("--pack", default=None, help="sample pack / project chosen by master")
    c.add_argument("--slot-ui", default=None, help="slot as the unit's UI shows it")
    c.add_argument("--slot", type=int, default=None, help="encoded slot number 0..127 if known")
    c.add_argument("--midi-device", default="/dev/snd/midiC3D0")
    c.add_argument("--alsa-device", default=None, help="path (a) only, e.g. hw:H5studio,0")
    c.add_argument("--format", choices=list(ALSA_FORMATS), default="S24_3LE")
    c.add_argument("--rate", type=int, default=PROTOCOL["rate"])
    c.add_argument("--channels", type=int, default=PROTOCOL["channels"])
    c.add_argument("--channel-map", default="1=Rhythm L,2=Rhythm R",
                   help=f"every channel 1..--channels; label '{UNUSED}' for a channel that carries nothing (H5studio "
                        "multitrack: 1=unused,2=unused,3=Rhythm L,4=Rhythm R)")
    c.add_argument("--interface", default="Zoom H5studio")
    c.add_argument("--gain-note", default=None, help="recorder input gain, as set")
    c.add_argument("--recorder-note", default=None, help="recorder settings: line, limiter/comp/low-cut off, etc.")
    c.add_argument("--firmware-label", default=None, help="build label on the unit, e.g. 6009")
    c.add_argument("--firmware-sha256", default=None)
    c.add_argument("--master-volume-note", default=None, help="Master Volume position (protocol: fully clockwise)")
    c.add_argument("--fx-state-note", default=None,
                   help="FX bypass and Grid FX state as seen on the unit; old FX tails cleared")
    c.add_argument("--notes", default=None, help="project/sample state, anything else")
    c.add_argument("--ref-kind", default=PROTOCOL["ref_kind"],
                   help="none | din-tap | external-click | other (declared, produced outside this tool)")
    c.add_argument("--ref-channel", type=int, default=None, help="capture channel (1-based) holding the reference")
    c.add_argument("--ref-note", default=None, help="how the reference is produced and characterised")
    c.add_argument("--pilot", action="store_true", help="one-hit gain check: 1 note at 1 s, 3 s capture")
    c.add_argument("--calibration", default=None,
                   help="live/play: the session's accepted recorder-chain calibration JSON (capture.py calibrate)")
    c.add_argument("--out", default=os.path.join(ROOT, "build", "capture"))
    c.add_argument("--recorder-cmd", default=None, help=argparse.SUPPRESS)  # tests: replaces arecord
    c.add_argument("--no-prompt", action="store_true", help=argparse.SUPPRESS)
    for name, help_ in (("plan", "dry run: print the stimulus, open nothing"), ("live", "path (a): arecord"),
                        ("play", "path (b): stimulus only, recorder writes its SD card"),
                        ("calibrate", "path (a): recorder-chain calibration, 5 hits, frozen per-channel ratio")):
        sp = sub.add_parser(name, parents=[c], help=help_)
        sp.add_argument("--dry-run", action="store_true")
        if name == "calibrate":
            sp.add_argument("--swap-evidence", default=None,
                            help="cable-swap test, free text (one cable and recorder input fed from Rhythm L, then R)")
            sp.add_argument("--swap-diff-db", type=float, default=None,
                            help="cable-swap result: Rhythm R minus Rhythm L level in dB through the same input")
            sp.add_argument("--plain", action="store_true",
                            help="line output instead of the level view (also whenever stdout is not a terminal)")
            sp.add_argument("--target-peak", type=float, default=TARGET_PEAK_DBFS,
                            help="level-view target peak in dBFS (information only, not a gate)")
            sp.add_argument("--target-tolerance", type=float, default=TARGET_TOLERANCE_DB,
                            help="level-view tolerance band around the target, dB")
    imp = sub.add_parser("import", help="path (b): attach the recorder's WAV to a play sidecar")
    imp.add_argument("--sidecar", required=True)
    imp.add_argument("--wav", required=True)
    return p


def _finite(name, v, minimum, strict=True):
    if v is None or not math.isfinite(v) or (v <= minimum if strict else v < minimum):
        raise SystemExit(f"--{name.replace('_', '-')} must be finite and {'>' if strict else '>='} {minimum}")


def normalise(a):
    """Validate everything before any device is opened."""
    if a.cmd == "import":
        return a
    sched = {**PROTOCOL, **CALIBRATE} if a.cmd == "calibrate" else PROTOCOL
    for k in ("hits", "lead", "spacing", "duration"):
        if getattr(a, k) is None:
            setattr(a, k, sched[k])
    if a.cmd == "calibrate":
        if a.pilot or a.calibration:
            raise SystemExit("calibrate takes neither --pilot nor --calibration")
        if a.source in ("silence", "no-note"):
            raise SystemExit("calibrate needs an audible fixture (the mono centred triangle-positive)")
        if a.hits < CALIBRATE["hits"]:
            raise SystemExit(f"calibrate needs --hits >= {CALIBRATE['hits']}")
        if a.swap_diff_db is not None and not math.isfinite(a.swap_diff_db):
            raise SystemExit("--swap-diff-db must be finite")
        if not math.isfinite(a.target_peak) or not math.isfinite(a.target_tolerance) or a.target_tolerance <= 0:
            raise SystemExit("--target-peak must be finite and --target-tolerance finite and > 0")
    if a.pilot:
        a.hits, a.lead, a.duration = PILOT["hits"], PILOT["lead"], PILOT["duration"]
    if a.note_off_after is not None and math.isfinite(a.note_off_after) and a.note_off_after < 0:
        a.note_off_after = None
    for n in ("lead", "spacing", "duration"):
        _finite(n, getattr(a, n), 0)
    for n in ("idle_before", "settle"):
        _finite(n, getattr(a, n), 0, strict=False)
    if a.note_off_after is not None:
        _finite("note_off_after", a.note_off_after, 0)
        if a.note_off_after >= a.spacing:
            raise SystemExit("--note-off-after must be shorter than --spacing")
    if (a.keyboard_note_semitones is None) != (a.keyboard_note_evidence is None):
        raise SystemExit("--keyboard-note-semitones and --keyboard-note-evidence go together")
    if a.keyboard_note_semitones is not None:
        if not math.isfinite(a.keyboard_note_semitones):
            raise SystemExit("--keyboard-note-semitones must be finite")
        if not a.keyboard_note_evidence.strip():
            raise SystemExit("--keyboard-note-evidence must not be empty")
    if not 1 <= a.velocity <= 127:
        raise SystemExit("--velocity must be 1..127")
    if a.hits < 1:
        raise SystemExit("--hits must be >= 1")
    if a.sample_choice is not None and not 0 <= a.sample_choice <= 127:
        raise SystemExit("--sample-choice must be 0..127")
    if a.slot is not None and not 0 <= a.slot <= 127:
        raise SystemExit("--slot must be 0..127")
    if not 8000 <= a.rate <= 192000:
        raise SystemExit("--rate must be 8000..192000")
    if not 1 <= a.channels <= 32:
        raise SystemExit("--channels must be 1..32")
    if a.preset == "reference" and a.track_channel is None and a.cmd == "plan":
        a.track_channel = a.track
        print(f"# plan: --track-channel not given, showing track CCs on channel {a.track} for illustration only")
    try:
        a.channel_map = dict(kv.split("=", 1) for kv in a.channel_map.split(",")) if a.channel_map else {}
    except ValueError:
        raise SystemExit("--channel-map must look like '1=Rhythm L,2=Rhythm R'")
    if any(not k.isdigit() or not 1 <= int(k) <= a.channels for k in a.channel_map):
        raise SystemExit("--channel-map channels must be 1..--channels")
    if a.cmd != "plan" and sorted(map(int, a.channel_map)) != list(range(1, a.channels + 1)):
        raise SystemExit(f"--channel-map must name every channel 1..{a.channels} once (label '{UNUSED}' for a channel "
                         "that carries nothing)")
    if a.channels - len(unused_channels(a)) < 1:
        raise SystemExit(f"--channel-map: every channel is labelled '{UNUSED}'")
    if a.ref_kind != "none":
        if a.ref_channel is None:
            raise SystemExit("--ref-kind needs --ref-channel")
    if a.ref_channel is not None and not 1 <= a.ref_channel <= a.channels:
        raise SystemExit("--ref-channel must be 1..--channels")
    if a.ref_channel is not None and a.ref_channel in unused_channels(a):
        raise SystemExit(f"--ref-channel {a.ref_channel} is labelled '{UNUSED}' in --channel-map")
    a.calibration_info = load_calibration(a.calibration) if a.calibration else None
    return a


def main(argv=None):
    a = normalise(parser().parse_args(argv))
    if a.cmd == "plan" or getattr(a, "dry_run", False):
        cmd_plan(a)
    else:
        {"live": cmd_live, "play": cmd_play, "calibrate": cmd_calibrate, "import": cmd_import}[a.cmd](a)


if __name__ == "__main__":
    main()
