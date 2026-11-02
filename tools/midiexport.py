"""Export a Circuit Rhythm project as a multitrack Standard MIDI File, a mapping, and a ZIP with the samples.

    uv run python -I tools/midiexport.py IN -o OUT.mid [--map OUT.json] [--md OUT.md] [options]
    uv run python -I tools/midiexport.py IN --zip OUT.zip [options]

IN is a project file (Sessions/NN_<name>.ncs, 169,092 bytes), or a file or zip archive that holds exactly one
project image. Sample names and files come from the pack's PCM folder next to Sessions (or --pcm DIR).

The backends of the arrangement model of tools/crsong.py: SMF type 1 at 96 ticks per quarter note, mapping.json,
MAPPING.md and a stored ZIP. web/export.html is the same program in JavaScript; this file is its reference and
test oracle (tests/test_web_export.py: identical bytes). User guide: docs/user/midi-export.md. Standard library
only; the unit is never accessed.
"""
import argparse
import json
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crsong  # noqa: E402
import smf  # noqa: E402

PPQ = 96
TICKS = PPQ // crsong.PULSES_PER_QUARTER    # ticks per unit pulse
DRUM_BASE = 36
SYNTH = crsong.SYNTH
LANE_PARAMS = ("macro 1", "macro 2", "macro 3", "macro 4", "macro 5", "macro 6", "macro 7", "macro 8",
               "track parameter 18 (a send, name inferred)", "track parameter 19 (a send, name inferred)",
               "track parameter 16 (level, name inferred)", "track parameter 17 (pan, name inferred)")
SAMPLE_MACROS = ("Tune", "Start", "Length", "Slope", "Distortion", "HP filter", "LP filter", "Resonance")
SYNTH_MACROS = ("feedback", "attack", "decay", "sustain", "release", "FM depth", "filter cutoff", "resonance")
NOT_EXPORTED = (
    "Swing: not applied to the note positions (the value is in the text event and in mapping.json).",
    "Probability: every step is written as if it always plays.",
    "Random direction: such patterns are written in forward order.",
    "Macro knob positions: a CC is written only where a step has a parameter lock.",
    "Parameter locks between microsteps at the slow sync rates (the unit interpolates them).",
    "Slice mode: a sliced sample plays as the step note.",
    "Track 8: voice limits and release tails of the synth.",
    "The FM sound per step on track 8 (sample flips of track 8).",
)


# --- note numbers ----------------------------------------------------------------------------------------------------

def note_maps(song, pitched=(), base=DRUM_BASE):
    """Per track: {slot: MIDI note} for drum tracks; None for pitched tracks and track 8 (MIDI note = engine note)."""
    maps = []
    for t in range(8):
        if t == SYNTH or t in pitched:
            maps.append(None)
            continue
        slots = sorted({n[3] for n in song.notes[t]})
        b = min(base, 128 - len(slots)) if slots else base
        maps.append({sl: b + i for i, sl in enumerate(slots)})
    return maps


def midi_notes(song, maps):
    """Per track [start, end, MIDI note, velocity]: mapped, out-of-range notes dropped, then no overlap per number."""
    out = []
    for t in range(8):
        ev = []
        for on, off, e, slot, vel in song.notes[t]:
            m = maps[t][slot] if maps[t] is not None else e
            if not 0 <= m <= 127:
                song.warnings.append(f"track {t + 1}: note {e} at pulse {on} is outside MIDI range; skipped")
                continue
            ev.append([on, off, m, vel])
        out.append(crsong.end_overlaps(ev, 2))
    return out


# --- samples ---------------------------------------------------------------------------------------------------------

def pcm_names(files):
    """{slot: file name} from the file names of a pack's PCM folder (NN_<name>.wav; the first in sorted order)."""
    names = {}
    for f in sorted(files):
        m = re.match(r"^([0-9]+)_.*\.wav\Z", f, re.IGNORECASE)
        if m and int(m.group(1)) < 128 and int(m.group(1)) not in names:
            names[int(m.group(1))] = f
    return names


def stem(f):
    return re.sub(r"\.wav\Z", "", re.sub(r"^[0-9]+_", "", f), flags=re.IGNORECASE)


def zip_sample(slot, f):
    return f"samples/slot{slot:02d}_{stem(f)}.wav"


# --- backends --------------------------------------------------------------------------------------------------------

def track_name(song, t, pcm):
    proj = song.project
    sample = pcm.get(proj.slot[t])
    name = f"T{t + 1} Synth" if t == SYNTH else f"T{t + 1}" + (f" {stem(sample)}" if sample else "")
    return name + (" (muted)" if proj.muted[t] else "")


def to_smf(song, notes, pcm):
    proj = song.project
    head = smf.Track(proj.name or "Circuit Rhythm project")
    head.tempo(0, proj.tempo if proj.tempo else 120)
    head.time_signature(0, 4, 4)
    head.text(0, f"swing {proj.swing}")
    one = song.length // song.loops if song.loops else 0
    for loop in range(song.loops):
        for s, start, _l, _sp in song.parts:
            if s is not None:
                head.meta((loop * one + start) * TICKS, 0x06, f"Scene {s + 1}".encode())
    tracks = [head]
    for t in range(8):
        tr = smf.Track(track_name(song, t, pcm))
        for on, off, m, vel in notes[t]:
            tr.note(on * TICKS, (off - on) * TICKS, t, m, vel)
        for pulse, lane, v in song.ccs[t]:
            tr.cc(pulse * TICKS, t, crsong.LANE_CC[lane], v)
        tracks.append(tr)
    for tr in tracks:
        tr.end = song.length * TICKS               # the arrangement's end, trailing silence included
    return smf.write_smf(tracks, PPQ)


def mapping(song, maps, notes, pcm, source=""):
    proj = song.project
    info = []
    for t in range(8):
        ti = {"track": t + 1, "channel": t + 1, "name": track_name(song, t, pcm), "muted": proj.muted[t],
              "mode": "synth" if t == SYNTH else "pitched" if maps[t] is None else "drum",
              "sample_slot": proj.slot[t], "notes_written": len(notes[t]), "ccs_written": len(song.ccs[t]),
              "steps_with_probability": song.probability_steps[t],
              "random_direction_patterns": sorted(song.random_patterns[t])}
        if t < SYNTH:
            ti["scaled"] = proj.scaled[t]
            if proj.slot[t] in pcm:
                ti["sample"] = pcm[proj.slot[t]]
        if maps[t] is not None:
            ti["notes"] = [dict({"note": n, "slot": sl},
                                **({"sample": pcm[sl], "file": zip_sample(sl, pcm[sl])} if sl in pcm else {}))
                           for sl, n in sorted(maps[t].items(), key=lambda kv: kv[1])]
        if t == SYNTH:
            ti["sound_slots_flipped"] = sorted(song.flips[t])
        info.append(ti)
    used = sorted({n[3] for t in range(SYNTH) for n in song.notes[t]})
    return {
        "project": proj.name, "source": source, "tempo": proj.tempo, "swing": proj.swing, "ppq": PPQ,
        "length_ticks": song.length * TICKS, "loops": song.loops,
        "arrangement": [{"scene": None if s is None else s + 1, "start_tick": start * TICKS,
                         "length_ticks": length * TICKS,
                         "patterns": [[a + 1, b + 1] for a, b in spans]} for s, start, length, spans in song.parts],
        "arrhythmia_block": proj.ext,
        "scale": {"scale": crsong.SCALE_NAMES[proj.scale], "key": proj.root},
        "track8": {"notes_per_press": proj.t8["notes_per_press"],
                   "chord_type": crsong.CHORD_TYPES[proj.t8["chord_type"]], "in_key": bool(proj.t8["in_key"]),
                   "voicing": crsong.VOICINGS[proj.t8["voicing"]], "octave": proj.t8["octave"],
                   "step_chord_shapes": None if proj.chords is None else [list(c) for c in proj.chords]},
        "cc_lanes": [{"lane": k + 1, "cc": crsong.LANE_CC[k], "parameter": LANE_PARAMS[k]} for k in range(12)],
        "tracks": info,
        "samples": [dict({"slot": sl}, **({"sample": pcm[sl], "file": zip_sample(sl, pcm[sl])} if sl in pcm
                                          else {"sample": None})) for sl in used],
        "not_exported": list(NOT_EXPORTED),
        "warnings": list(song.warnings),
    }


KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def mapping_md(m):
    """MAPPING.md: the mapping as a page to read while building Drum Racks."""
    beats = m["length_ticks"] // m["ppq"]
    out = [f"# {m['project'] or 'Circuit Rhythm project'}", "",
           f"Source: {m['source'] or '(unknown)'}. Tempo {m['tempo']} BPM, swing {m['swing']}. Length {beats} beats "
           f"({m['ppq']} ticks per quarter note). Passes through the arrangement: {m['loops']}.", "",
           "## Arrangement", "", "| Part | Scene | Start (beats) | Length (beats) | Patterns of tracks 1 to 8 |",
           "|---|---|---|---|---|"]
    for i, a in enumerate(m["arrangement"]):
        pats = " ".join(f"{x}-{y}" if x != y else f"{x}" for x, y in a["patterns"])
        out.append(f"| {i + 1} | {a['scene'] or '-'} | {a['start_tick'] / m['ppq']:g} | "
                   f"{a['length_ticks'] / m['ppq']:g} | {pats} |")
    out += ["", "## Tracks", ""]
    for t in m["tracks"]:
        out.append(f"### {t['name']}")
        out.append("")
        out.append(f"MIDI channel {t['channel']}, {t['mode']}, {t['notes_written']} notes, {t['ccs_written']} CC "
                   "events." + (" Muted on the unit." if t["muted"] else ""))
        if t["mode"] == "drum":
            out += ["", "| MIDI note | Sample slot | Sample file |", "|---|---|---|"]
            for n in t["notes"]:
                out.append(f"| {n['note']} | {n['slot']} | {n.get('file', '(not in the pack)')} |")
        elif t["mode"] == "pitched":
            out.append("MIDI note 60 plays the sample at its original pitch.")
        else:
            s, t8 = m["scale"], m["track8"]
            out.append(f"The notes of the FM synth, chords expanded: notes per press {t8['notes_per_press']}, "
                       f"chord type {t8['chord_type']}, In key {'on' if t8['in_key'] else 'off'} "
                       f"({KEYS[s['key']]} {s['scale']}), voicing {t8['voicing']}, octave {t8['octave']}.")
        if t["steps_with_probability"]:
            out.append(f"{t['steps_with_probability']} steps have a probability below 100 %.")
        if t["random_direction_patterns"]:
            out.append(f"Patterns in random direction: {', '.join(map(str, t['random_direction_patterns']))}.")
        out.append("")
    out += ["## CC events (parameter locks)", "",
            "Ableton Live ignores CC events when it imports a MIDI file.", "",
            "| CC | Parameter | Tracks 1 to 7 | Track 8 |", "|---|---|---|---|"]
    for c in m["cc_lanes"]:
        k = c["lane"] - 1
        out.append(f"| {c['cc']} | {c['parameter']} | {SAMPLE_MACROS[k] if k < 8 else ''} | "
                   f"{SYNTH_MACROS[k] if k < 8 else ''} |")
    out += ["", "## Not exported", ""] + [f"* {x}" for x in m["not_exported"]]
    if m["warnings"]:
        out += ["", "## Warnings", ""] + [f"* {w}" for w in m["warnings"]]
    return "\n".join(out) + "\n"


def mapping_json(m):
    return json.dumps(m, indent=1, ensure_ascii=False) + "\n"


def zip_store(entries):
    """A ZIP archive (stored, UTF-8 names, fixed time 1980-01-01 00:00) of [(name, bytes)]."""
    body, central = bytearray(), bytearray()
    for name, data in entries:
        n = name.encode("utf-8")
        crc = zlib.crc32(data)
        fields = struct.pack("<HHHHHIII", 20, 0x0800, 0, 0, 0x21, crc, len(data), len(data))
        central += struct.pack("<IH", 0x02014B50, 20) + fields + struct.pack("<HHHHHII", len(n), 0, 0, 0, 0, 0,
                                                                              len(body)) + n
        body += struct.pack("<I", 0x04034B50) + fields + struct.pack("<HH", len(n), 0) + n + data
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(body), 0)
    return bytes(body + central + end)


def export(proj, source="", pcm_files=(), read_sample=None, pitched=(), base=DRUM_BASE, locks=True, scenes=None,
           loops=1):
    """(SMF bytes, mapping dict, MAPPING.md text, ZIP bytes or None). read_sample(file name) -> bytes, for the ZIP."""
    s = crsong.song(proj, scenes, loops, locks)
    pcm = pcm_names(pcm_files)
    maps = note_maps(s, pitched, base)
    notes = midi_notes(s, maps)
    data = to_smf(s, notes, pcm)
    m = mapping(s, maps, notes, pcm, source)
    md = mapping_md(m)
    z = None
    if read_sample is not None:
        base_name = re.sub(r"\.ncs\Z", "", source, flags=re.IGNORECASE) or "project"
        entries = [(base_name + ".mid", data), ("MAPPING.md", md.encode()), ("mapping.json", mapping_json(m).encode())]
        entries += [(x["file"], read_sample(x["sample"])) for x in m["samples"] if x["sample"]]
        z = zip_store(entries)
    return data, m, md, z


def build(proj, pitched=(), base=DRUM_BASE, locks=True, pcm=None, scenes=None, loops=1):
    """(SMF bytes, mapping dict); pcm = {slot: file name}."""
    data, m, _md, _z = export(proj, "", list((pcm or {}).values()), None, pitched, base, locks, scenes, loops)
    return data, m


def _scene_range(s):
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", s)
    if not m:
        raise argparse.ArgumentTypeError("scene range is N or N-M (1..16)")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= a <= b <= crsong.SCENES:
        raise argparse.ArgumentTypeError("scene range is N or N-M with 1 <= N <= M <= 16")
    return a - 1, b - 1


def _tracks(s):
    try:
        ts = {int(x) for x in s.split(",") if x}
    except ValueError:
        raise argparse.ArgumentTypeError("tracks are a comma-separated list of 1..7") from None
    if not ts or not ts <= set(range(1, 8)):
        raise argparse.ArgumentTypeError("tracks are a comma-separated list of 1..7")
    return {t - 1 for t in ts}


def add_export_args(ap):
    """The export options shared with tools/unitread.py."""
    ap.add_argument("--pitched", type=_tracks, default=set(), help="sample tracks to write as pitched notes, e.g. 3,5")
    ap.add_argument("--base", type=int, default=DRUM_BASE, help="first Drum Rack note of a sample track (default 36)")
    ap.add_argument("--scenes", type=_scene_range, help="scene range to export, e.g. 1-4 (default: the scene chain)")
    ap.add_argument("--loops", type=int, default=1, help="number of passes through the arrangement (default 1)")
    ap.add_argument("--no-locks", action="store_true", help="do not write parameter locks as CC events")


def export_opts(a):
    """Keyword arguments of export() from the parsed options of add_export_args()."""
    return dict(pitched=a.pitched, base=a.base, locks=not a.no_locks, scenes=a.scenes, loops=a.loops)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export a Circuit Rhythm project as a multitrack MIDI file.")
    ap.add_argument("input", help="project file (.ncs), or a file / zip that holds one project image")
    ap.add_argument("-o", "--output", help="the MIDI file to write")
    ap.add_argument("--map", help="write the mapping (JSON) to this file")
    ap.add_argument("--md", help="write the mapping as MAPPING.md text to this file")
    ap.add_argument("--zip", help="write a ZIP: the MIDI file, MAPPING.md, mapping.json and the samples used")
    ap.add_argument("--pcm", help="the pack's PCM folder (default: ../PCM next to the project's Sessions folder)")
    add_export_args(ap)
    a = ap.parse_args(argv)
    if not (a.output or a.zip):
        ap.error("give -o OUT.mid and/or --zip OUT.zip")
    if not 0 <= a.base <= 127 or a.loops < 1:
        ap.error("--base is 0..127 and --loops at least 1")
    try:
        with open(a.input, "rb") as f:
            proj = crsong.Project(crsong.find_image(f.read()))
        pcm = a.pcm
        if pcm is None:
            d = os.path.dirname(os.path.abspath(a.input))
            pcm = os.path.join(os.path.dirname(d), "PCM") if os.path.basename(d).lower() == "sessions" else None
        files = os.listdir(pcm) if pcm and os.path.isdir(pcm) else []

        def read_sample(f):
            with open(os.path.join(pcm, f), "rb") as g:
                return g.read()
        data, m, md, z = export(proj, os.path.basename(a.input), files, read_sample if a.zip else None,
                                **export_opts(a))
    except (OSError, crsong.ProjectError, smf.SMFError) as e:
        print(f"midiexport: {e}", file=sys.stderr)
        return 1
    for path, content in ((a.output, data), (a.zip, z), (a.map, mapping_json(m).encode()), (a.md, md.encode())):
        if path:
            with open(path, "wb") as f:
                f.write(content)
    secs = m["length_ticks"] / PPQ * 60 / (proj.tempo or 120)
    print(f"{proj.name!r}: {len(m['arrangement'])} part(s), {m['length_ticks'] // PPQ} beats "
          f"({secs:.1f} s at {proj.tempo} BPM) -> {', '.join(p for p in (a.output, a.zip) if p)}")
    for t in m["tracks"]:
        print(f"  {t['name']}: {t['notes_written']} notes, {t['ccs_written']} CCs, {t['mode']}")
    missing = [x["slot"] for x in m["samples"] if not x["sample"]]
    if missing:
        print(f"  samples not in the pack: slots {missing}")
    for w in m["warnings"]:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
