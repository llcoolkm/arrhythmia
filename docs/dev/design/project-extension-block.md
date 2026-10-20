# Project extension block

Arrhythmia stores its per-project settings (track modes, the track 8 synth settings, scales) inside the project file
itself, in bytes the stock firmware leaves unused. No file of our own is written. Marks as in
[../README.md](../README.md).
Source: `patches/src/trackmodes.c`, `patches/src/synth_settings.h`, `patches/src/scales.c`.

## Where it lives

How we know: static analysis and emulation of the stock save and load (kept to what is needed to use Arrhythmia
projects safely); persistence observed on hardware.

* A project file has a **spare tail of 880 bytes**, followed by a **4-byte trailer** at the very end of the file. The
  block occupies exactly the 880 bytes; the trailer is never read or written by Arrhythmia (its bytes are nonzero in
  factory projects; meaning unknown).
* Stock 5706 creates these 880 bytes as zero (all 18 factory projects hold zeros there) and carries them unchanged
  through load and save (EMU: a canary survived load, playback, a step edit and save, and reloaded byte for byte).
* On the unit, the synth settings stored in the block survived a save and 10 power cycles (HW).
* So a project made with Arrhythmia should load on stock 5706 unchanged, and a stock re-save keeps our bytes (EMU).
  Loading an Arrhythmia project on stock firmware has **not** been confirmed on hardware.
* The firmware writes the in-memory project as the file, so an edit to the block is saved by the normal Save; no
  save code of ours is involved (EMU).

## Format v1 (frozen)

How we know: our design. Little endian.

| Offset | Size | Field |
|---|---|---|
| 0x000 | 4 | magic `54 4D 44 31` ("TMD1") |
| 0x004 | 1 | version 1 |
| 0x005 | 1 | flags; v1 defines none; nonzero = unsupported |
| 0x006 | 2 | length 0x370 (the block, CRC included) |
| 0x008 | 8 x 16 | track records, track 1 first; byte 0 = stored mode: 0 Sample, 1 Chord, 2 Synth, 3 Voice-of, others unsupported |
| 0x088 | 8 x 64 | patch slots, one per track (a stored encoding, not a DSP layout) |
| 0x288 | 228 | reserved; 0x288 .. 0x297 is the scales sub-record (below) |
| 0x36C | 4 | CRC-32/ISO-HDLC over 0x000 .. 0x36B (zlib `crc32`; check value 0xCBF43926) |

Known answer: header `54 4D 44 31 01 00 70 03`, byte 0 of track 3's record (offset 0x028) = 1, every other byte
zero gives CRC `0x2A810DFA` (tested against the firmware's own output, EMU).

### Track 8 settings (record 7, block 0x078, 16 bytes)

| Byte | Field | Range | Default |
|---|---|---|---|
| +0 | stored mode byte (see above); not part of these settings | | |
| +1 | version: 0 with +1 .. +15 all zero = unset; 1 = v1; else unknown | | |
| +2 | notes per press | 1..4 | 1 |
| +3 | chord type: 0 Maj, 1 Min, 2 Sus2, 3 Sus4, 4 7th, 5 m7 (used when In key is 0) | 0..5 | 0 |
| +4 | In key: the scale's chord on the played note | 0..1 | 1 |
| +5 | voicing: root, inversion 1, inversion 2, spread | 0..3 | 0 |
| +6 | scale (used only when the scales sub-record is unset): 0 major, 1 natural minor | 0..1 | 0 |
| +7 | key root (same rule), 0 = C | 0..11 | 0 |
| +8 | octave, signed | -2..+2 | 0 |
| +9 | MIDI poly (1 poly up to the voice cap, 0 chord-mono as the pads) | 0..1 | 1 |
| +10 | preset, reserved (0.1 writes 0 and ignores it) | 0..7 | 0 |
| +11 .. +15 | reserved: 0 on creation, else preserved | | |

### Track 8 patch (slot 7, block 0x248, 64 bytes)

Byte 0 codec (0 with all 64 bytes zero = unset, 1 = subtractive v0.1), byte 1 version, then waveform (0 saw, 1
pulse), attack, decay (default 64) and level (default 100), 0..127; bytes 6 .. 63 reserved. **Arrhythmia 0.1 ships
one fixed sound and neither shows nor reads these fields**; a 0.1 project leaves the slot unset.

### Scales sub-record (block 0x288, 16 bytes)

| Byte | Field |
|---|---|
| +0, +1 | magic `'S' 'C'` |
| +2 | version 1 |
| +3 | scale 0..9 (out of range decodes to major) |
| +4 | key root 0..11, 0 = C (out of range decodes to C) |
| +5 | bit n = track n + 1 scaled (tracks 1 to 7); bit 7 reserved, written 0 |
| +6 .. +15 | reserved: 0 on creation, preserved |

All 16 bytes zero = unset: no track scaled, and the chord engine takes scale and root from record 7. The first edit
creates the sub-record from those values. Anything else that is not a valid v1 record is unknown: nothing scaled and
every edit refused.

## Rules

How we know: our design; emulated.

* **Validation** after every project load, clear and pack switch, and once at boot: all 880 bytes zero = EMPTY (a
  stock project: everything at its default, writable). Otherwise magic, version, flags, length and CRC are checked in
  that order; the first failure makes the block **read-only for Arrhythmia**: never rewritten, copied through by
  stock saves, and every setting at its default.
* **Unknown or invalid fields** decode to their defaults; the stored bytes stay until that field is edited. A record
  or patch of an unknown version refuses edits.
* **Writes** change only what they mean: on an EMPTY block the header, the edited field and the CRC; on a valid block
  the field and the CRC. Reserved and unknown bytes are never normalised.
* **No edit while a project operation is in flight.** An edit is made only when no load, save or clear is pending and
  the firmware's file task is not running a job; otherwise it is refused as BUSY and nothing is written. The whole
  write happens with the scheduler suspended. The settings page blinks red on a refused edit.
* A write with its CRC holds the suspension for about 0.23 ms (STATIC estimate, not measured).

Inspect or set the stored track modes with SysEx `0C` and the scales with `0E`
([../devkit-sysex.md](../devkit-sysex.md)).

## Writing your own fields

Use the reserved bytes of the block only through a new version or sub-record with its own magic, so that older
Arrhythmia builds treat it as unknown and preserve it. Never touch the 4-byte trailer or the stock part of the file.
