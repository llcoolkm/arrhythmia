# Modules: reference

The module guide is [../user/developers.md](../user/developers.md): entry points, input and clock events, building,
loading, the host calls. This page adds the exact formats, the rules the host follows, and what was verified where.
Marks as in [README.md](README.md). Everything here is our design (DESIGN) unless marked otherwise.

## File format

How we know: our design (`tools/build_module.py`, `patches/src/devkit_abi3.c`).

A module is position-independent Thumb code for the Cortex-M7 with a header, built by `tools/build_module.py`. It is
copied to ITCM 0x00000400 and runs there.

| Offset | Field |
|---|---|
| 0 | magic `'CRM1'` (0x314D5243) or `'CRM2'` (0x324D5243) |
| 4 | size of the whole module, header included |
| 8 | CRC-32 (zlib) over the whole module with this field set to zero |
| 12 | `init` offset (0 = none) |
| 16 | `frame` offset (required) |
| 20 | `input` offset (0 = none) |
| 24 | `'CRM2'` only: `clock` offset (0 = none) |
| 24 or 28 | code |

* Offsets are from 0x400 with bit 0 set (Thumb), and each must point inside the code with at least one halfword left.
* Size 26 bytes to 15 KB (0x400 .. 0x3FFF). `build_module.py` writes `'CRM2'` only when the module defines `clock`.
* No data sections and no relocations. Module RAM is ITCM 0x100 .. 0x3FF (768 bytes), zeroed before `init`. Build
  tables in code.
* Every ITCM access by the host first checks that ITCM is enabled and at least 16 KB.

## When a module starts

How we know: our design; upload, read-back and start observed on hardware.

* Over USB: `04` uploads, `05` starts ([devkit-sysex.md](devkit-sysex.md)); `tools/module.py` uploads, reads back
  through `7D`, compares and starts (HW).
* From the microSD card: the picker (below).
* **Loaded this boot.** ITCM survives the warm reset after a flash (HW), so the host starts ITCM code only if it was
  written since boot: by `04`, by a picker load or by copying the built-in module. Nothing starts a module by itself at
  power-on.
* On start the host checks magic, size, entry offsets and CRC, then calls `init`. A module with `input` takes the
  pads, buttons and encoders; Shift and the pots always stay with the firmware.

## Context and timing

How we know: our design; the frame rate measured on hardware.

| Entry | Called from | Notes |
|---|---|---|
| `init(leds)` | the starting path (SysEx or picker) | once |
| `frame(leds, n)` | the UI task, every LED frame | about 94 frames per second on the unit (HW); return 0 to stop |
| `input(leds, kind, index, value)` | the Main task, per event | while the module owns the controls |
| `clock(leds, event, pos, bpm)` | the Main task, inside the sequencer tick | only on pulses on which the tracks step |

* All entries run with the scheduler suspended. Never block, never spin waiting for something, keep each call short.
* The host calls never call firmware code from inside your entry: they append to a ring that the Main task drains
  after the 1 ms timer service. A host call returns 1 if queued.
* Ownership is decided per press: a control's release and pressure follow its press. When a module stops, its
  outstanding presses become tombstones whose releases reach nobody.
* A module that owns the controls sees Play and Stop as buttons. A module without `input` never owns them and its
  LED buffer is always shown.

## The host call ring

How we know: our design; emulated.

One ring of 8 slots is shared by all host calls; each entry carries a type (note, port note, parameter, MIDI), so the
order of calls holds across types. "Set track 3's sample, then play it" works in that order.

* The Main task makes at most 2 firmware calls per pass from the ring. A pass is usually about 1 ms but not bounded;
  a long pass (SD read, DSP transfer) delays the ring.
* A note on, a parameter, or a MIDI message other than a note off needs 2 free slots, so a note off always has room.
  A note off that still does not fit raises a release of that track, ordered after what is already queued.
* One pitch per track: a note on while the host holds a different pitch on that track (or the same pitch on the
  other port) releases the held one first. Every release goes to the port the note was played on.
* A release is a note off, not silence: one-shot samples play out.
* When a module ends (stop, restart, replacement, `frame` returning 0) the ring is flushed: queued parameters and MIDI
  messages are dropped, and every track's held note is released.
* `host_midi` messages are not tied to tracks and are not released when the module ends: send your own note offs.
* Tracks are shared with the pads, the sequencer and MIDI in: a module note and a held pad of the same pitch on the
  same track cut each other.
* Throughput: a triad with three sample changes (3 parameters + 3 notes) takes 3 Main passes.

## Host calls in detail

How we know: our design; per call as marked in the table.

The host table is at 0x0806A570; check `'CRH1'` and ABI 3 before reading words 4 to 7. The signatures are in the
module guide.

| Call | Details | Mark |
|---|---|---|
| `host_note(t, note, vel)` | engine only: no MIDI out, no recording. note in engine numbering, 60 = sample unpitched, at most 115; vel above 127 is clamped | HW (tracks 1 to 3 with a clock module) |
| `host_note_port(t, note, vel, 1)` | the front-panel pad path: sound, MIDI out on the track's channel, and the pattern while Rec is armed and the transport runs | HW: notes recorded and replayed; MIDI out on the track's channel |
| `host_note_port(..., 0)` | the same as `host_note` | EMU |
| `host_param(t, idx, val)` | idx 0..24, val 0..127 in the parameter's stored 7-bit form; the firmware clamps to the parameter's range. Applies at the track's next note (or the firmware's periodic parameter update). Not recorded as automation; the display may show the old value until it refreshes; whether a project save keeps it was not checked | EMU |
| `host_ui()` | `track \| view << 8 \| front << 16 \| 1 << 31`, 0 = unknown. view 0 Sample, 1 Note, 2 Velocity, 3 Gate, 4 Pattern Settings; front 1 while the track grid is in front, 0 while another page (Patterns, Mixer, FX, Tempo, Projects) owns the pads. A snapshot taken once per Main pass, so it lags a button press by one pass | EMU |
| `host_midi(msg)` | channel voice messages only: status 0x80..0xEF, data below 0x80, d2 = 0 for 0xCn and 0xDn. Sent to all three MIDI outputs. The output rings drop silently when full (stock behaviour) | EMU |

Parameter indices 6 to 13 are the eight macro parameters of the track ([maps.md](maps.md)); 0 is the sample slot.
On arrhythmia-firmware, track 8 notes play the chord synth (EMU); on arrhythmia-devkit they play track 8's sample.

There is deliberately no call to read DSP memory: DSP reads are allowed only while the unit is idle, which a module
cannot guarantee.

## Clock

How we know: our design; emulated, and a clock module observed on hardware.

* START comes right before the first pulse the module sees while it is marked stopped (on Play, or mid-bar if the
  module starts while the sequencer runs). STOP comes in the first Main pass after the transport stops.
* `pos`: pulse 0..95 within the bar (24 PPQN), step = pulse / 6, beat = pulse / 24; it is the bar position, not the
  pattern step, so 32-step patterns repeat it. `bpm` 40..240 on the internal clock.
* Pulses are quantised to the 1 ms timer, like the stock steps: at 90 BPM the intervals are 27 or 28 ms with a mean of
  27.789 ms (EMU). Module notes queued in `clock` leave in the same pass as the stock step notes.
* Not tested: external MIDI clock, swing, pattern lengths other than 16 steps. If an external clock stops without a
  MIDI Stop, the transport stays running and no STOP comes.
* On the unit, a clock module played in step with the sequencer, followed tempo changes at once and stopped with the
  transport (HW).

## microSD picker

How we know: our design; emulated. The facts about the firmware's own file handling come from static analysis,
kept to what is needed to load modules safely.

* Folder `MODULES` at the card root (not case-sensitive). The firmware's own files live under `Rhythm`, so the
  folders cannot collide. The USB disk does not show the card: use a card reader (HW).
* A file is listed when its 8.3 short name ends in `.MOD`. Long names work under their short name (for example
  `MYPAIN~1.MOD`). Up to 31 files, the 31 smallest short names in byte order; folders, hidden, system and label
  entries, and files outside 26 bytes to 15 KB are skipped; at most 512 directory entries are examined.
* Pad 1 (dim cyan) is the built-in module, pads 2 to 32 (dim white) the files; a failed load turns its pad dim red and
  leaves the picker open. While the folder is read, pad 1 blinks and pads are ignored.
* **Read-only.** The loader opens files read-only and calls no write, sync, seek, unlink, mkdir, rename, mount or
  format function. In emulation, with the firmware's own FAT code on a FAT16 image and every disk write trapped: zero
  writes (EMU).
* The firmware's FAT code is not reentrant. The loader runs in the Main task at the top of a pass and keeps the
  firmware's file-job task out with a token while a loader call is in flight; one card read per Main pass, so a pass
  stalls for at most one read (DESIGN, EMU). Sequencer timing during a load from a slow card was not measured.

## Verified on hardware

How we know: observed on hardware.

On one unit: upload and read-back with `tools/module.py`; a clock module
in time with the sequencer; `host_note_port` with recording and MIDI out (notes replayed from the pattern after
leaving the module, MIDI out on the track's channel). The other host calls are emulated only. DIN MIDI out from a
module was not checked. The picker's card loading was used on an arrhythmia-firmware build on 2026-10-19: the stars
example (`Modules/STARS.MOD`) was picked with Shift + Sample Rec and ran during a one-hour soak, and the built-in
paint module was started and used. Refusal of bad module files is emulated only.
