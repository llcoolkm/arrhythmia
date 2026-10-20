# Scales for sample tracks 1 to 7

In both Arrhythmia 0.1 images (0.1.0: builds 7229 and 7228): set on the synth page in arrhythmia-firmware, with SysEx `0E` in arrhythmia-devkit.
Source: `patches/src/scales.c`, `patches/src/synth_settings.h`. Marks as in [../README.md](../README.md).

## What it does

How we know: our design; emulated; on hardware with build 7222, scale changes during playback measured and held-note
release and persistence checked; on hardware with build 7229 (0.1.0, 2026-10-20), the one-octave-per-row layout
checked (below).

* A project-wide **scale** (ten: major, natural minor, dorian, phrygian, lydian, mixolydian, harmonic minor, major
  pentatonic, minor pentatonic, blues), a **key root** (C to B) and a **scaled bit per track** for tracks 1 to 7.
* On a scaled track the **Note view keyboard** (the bottom two pad rows, or all four in the expanded view) plays the
  scale, **one octave per row**: each row runs from the root to the root an octave up (degrees 0 to n of an n-note
  scale), the row above one octave higher. 5-note scales leave the two right-hand pads of each row dark and silent,
  blues the rightmost one. Up and Down still move by an octave. Root pads are lit in the colour the stock Note view
  gives its C pads, and every played pad lights itself (a root also its twin at the end of the neighbouring row).
* Nothing else changes: Sample view pads, slice layouts, MIDI in, sequencer playback, track 8's keyboard.
* Recording and step entry store the note that was played. A later change of scale, root or bit does not touch
  recorded notes: playback keeps its pitches.
* A held key releases the note it started, even if the scale changes while it is held. Verified on hardware with
  devkit build 7222: note 62 held, scale changed, release turned off 62, and the next press played 66
  in the new scale (HW).
* On arrhythmia-firmware the track 8 chord engine uses the same scale and root.
* Settings are stored in the project ([project-extension-block.md](project-extension-block.md), scales
  sub-record).

## Mapping

How we know: our design.

Scale masks (12 bits, bit 0 = root): major 0xAB5, natural minor 0x5AD, dorian 0x6AD, phrygian 0x5AB, lydian 0xAD5,
mixolydian 0x6B5, harmonic minor 0x9AD, major pentatonic 0x295, minor pentatonic 0x4A9, blues 0x4E9 (ids 0 to 9 in
that order).

The keyboard table holds **one octave per table row** and its rows-per-octave field
is 1 (stock chromatic: 2), so pad -> index is pad + 8 (5 - octave) and Up / Down move by one row, i.e. one octave.
Table row k (indexes 8k to 8k + 7), column c plays 0x48 + root + 12 (5 - k) + semi(c) (router note numbers, which are
engine notes + 12) for c = 0 to n; columns n + 1 to 7 are empty (no note: two for the pentatonics, one for blues, none
for the 7-note scales). semi(i) = 12 (i / n) + the (i mod n)-th interval of the n-note scale. At the default octave 3
the bottom-left pad plays 0x48 + root; the simple view (pads 16 to 31) shows rows 7 - octave and 8 - octave, the
expanded view (pads 0 to 31) rows 5 - octave to 8 - octave. Notes outside the stock keyboard's range 0x19 to 0x6C are
silent and dark, like the stock gaps.

Only the roots occur twice (row k column n is row k - 1 column 0), and they carry partner entries, so both light
together. Every other note sits on exactly one pad. This matters because the stock Note view lights a sounding note
(pressed, played by the sequencer or by MIDI in) only at the first table index of that note and at its partner, never
at the pressed pad.

Range (computed from these constants): in the simple view every pad of every scale and key is in range at the
default octave and below; one press of Up darkens top-right pads in every key except C. In the expanded view at the
default octave the top row is above the range (key C: its first pad only) and the second row is cut off at 0x6C; two
presses of Down give four full rows in every scale and key.

Row pairs (bottom row degrees 0 to 7, top row degrees n to n + 7, rows per octave 2) do not work: each pair's top
row repeats the bottom row of the pair above (expanded view C4 C5 C5 C6), so a non-root note sits on two pads and
lights the hidden one. One octave per row avoids that; in 5- and 6-note scales the pads past the upper root stay
empty.

## User interface

How we know: our design.

| Control (synth page open: Shift + Track 8) | Does |
|---|---|
| Track 1 to 7 | toggle that track's scaled bit; light bright green = scaled, dim = not |
| Shift held, pads 1 to 10 (rows 1 and 2) | pick the scale (violet; bright = in force) |
| Shift held, rows 3 and 4 | key root keyboard |
| pad 16 | natural minor or major |
| a refused edit (busy, read-only) | the page blinks red, nothing is written |

SysEx `0E` reads and sets all three at once on both images ([../devkit-sysex.md](../devkit-sysex.md)).

## Timing

How we know: measured on hardware (MIDI clock), plus our design.

A scale change rebuilds the keyboard of each scaled track, at most one track per Main pass, so after a change the
keyboards converge over up to seven passes; `0E 00` reports in `applied` which tracks already hold the new scale.

On the unit at 130 BPM, with all seven tracks scaled, the longest interval of the unit's MIDI clock output during
scale changes was **26.9 ms, against 24.0 ms in a run without changes** (HW).
Devices that follow the unit's clock may notice it.

## Known issues

* In the expanded view at the default octave the two upper rows are mostly dark (above the range); press Down twice
  for four full rows.
* arrhythmia-devkit has no page; SysEx `0E` is the only control.
* A project whose scale was changed in a scales build and then opened in an older build plays chromatic on tracks 1
  to 7 there, and track 8's chords use record 7's older scale and key. The sub-record is kept by that build's saves.
* Verified on hardware (build 7222): held-note release across a scale change (above), and a D harmonic minor setting
  that persisted through save and power cycle.
* Verified on hardware (build 7229, 2026-10-20): C major in the simple and expanded views with every pad lighting
  itself, A minor pentatonic with its two empty pads per row, a note held across a scale change, scales off restoring
  the stock keyboard, save and power cycle, track 8 and unscaled tracks unaffected. arrhythmia-devkit 7228 was not
  tested on hardware.
* Not verified on hardware: the root-pad colours as such, slice layouts on a scaled track, and loading the project on
  stock firmware.
