# Maps: lights, controls and macro knobs

Marks as in [README.md](README.md).

## LED buffer

How we know: observed on hardware (each byte lit alone and filmed, then a known pattern drawn and photographed).

[../led-map.md](../led-map.md) and [../led-map.json](../led-map.json) give the light and colour channel of every byte
of the 224-byte LED buffer that a module draws into (HW). 69 RGB lights use 207 bytes; 17 bytes drive nothing. About a
quarter of the lights do not have R, G and B in consecutive bytes, so always go through the table. Brightness is held
in bits 1..7 of each byte (0 .. 0xFE). Photo of the check: [../img/led-map-verify.jpg](../img/led-map-verify.jpg).

## Controls

How we know: observed on hardware (every control pressed once in panel order and logged with SysEx `09`; then ten
controls asked for in random order, all ten as predicted).

[../input-map.json](../input-map.json) gives the index of every control as a module's `input` entry and SysEx `09` see
it, with the same names as `led-map.json` (HW):

* **Buttons** 0 to 27: track buttons 1 to 8 are 0 to 7, then Sample 8, Note 9, Velocity 10, Gate 11, Pattern Settings
  12, Patterns 13, Mixer 14, FX 15, Rec 16, Play 17, Sample Rec 18, Down 19, Up 20, 1-16 21, Tempo 22, Clear 23,
  Duplicate 24, Save 25, Projects 26, Shift 27.
* **Pads** 0 to 31: index = row x 8 + column, row 0 at the top, column 0 at the left.
* **Encoders** 0 to 7 = macro knobs 1 to 8. On the panel the top row holds macros 2, 4, 6, 8 and the bottom row 1, 3,
  5, 7. Clockwise is positive.
* **Pots**: 0 = Master Filter, 1 = Master Volume, 0 .. 255, rising clockwise. Modules do not receive the pots.

Behaviour of the controls: [hardware.md](hardware.md) ("Inputs").

## Macro knobs and MIDI CCs

How we know: the CC numbers, ranges, defaults and knob steps were emulated with the stock image (needed to use
`host_param` and MIDI with Arrhythmia); the Tune scale was measured on hardware. All 8 tracks behave the same; the CCs
are sent and received on the track's MIDI channel.

| Macro | Panel label | Parameter index (`host_param`) | MIDI CC (in and out) | Stored range (default) | Knob step |
|---|---|---|---|---|---|
| 1 | Tune | 6 | 21 | 4..124, 64 = 0 (64) | 1 count = 1 value = 1/5 semitone; Shift snaps to semitones |
| 2 | Start | 7 (fine: 14) | 22 (fine: 86) | 0..127 / 0..127 (0 / 0) | 1 count = 1 coarse step; Shift = 4 fine steps |
| 3 | Length | 8 (fine: 15) | 23 (fine: 87) | 0..127 / 0..127 (127 / 127) | as Start |
| 4 | Slope | 9 | 24 | 0..127, 64 = 0 (64) | one value per 2 counts |
| 5 | Distortion | 10 | 25 | 0..127 (0) | 1 |
| 6 | HP Filter | 11 | 26 | 0..127 (0) | 1 |
| 7 | LP Filter | 12 | 27 | 0..127 (127) | 1 |
| 8 | Resonance | 13 | 28 | 0..127 (0) | 1 |

* **Tune is 5 steps per semitone**: 120 stored steps cover +-12 semitones. On the unit, CC 21 = 54 measured -2.00
  semitones and 64 exactly 0 (HW). Values outside 4..124 are clamped (EMU).
* A knob turn sends its CC (when the unit's MIDI CC output is enabled); macros 2 and 3 send the fine CC first, then
  the coarse one (EMU).
* Received CCs write the parameter without sending MIDI. CC 86/87 write only the fine part, 22/23 only the coarse
  part; they are not paired (EMU).
* A turn takes effect at the track's next note (EMU).
* Clear + macro, turned 40 counts clockwise, resets the parameter to its default; 40 counts anticlockwise clears that
  knob's p-locks (EMU).
* Other parameters of each track, for `host_param`: 0 sample slot (CC 8), 1 (0..2, CC 12), 2 to 5 (CC 14 to 17),
  16 (0..127, default 100, CC 29), 17 (centre 64, CC 30), 18 (default 0, CC 31), 19 (default 0, CC 32). The names of
  16 to 19 as level, pan and two sends are inferred from defaults and encoding, not verified (EMU).
