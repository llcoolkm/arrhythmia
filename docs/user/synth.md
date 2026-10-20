# Arrhythmia: the track 8 chord synth

Applies to Arrhythmia 0.1.0 (arrhythmia-firmware build 7229, arrhythmia-devkit build 7228). Test status is in the
[release notes](../../RELEASE-NOTES-0.1.0.md). Items marked **TBD** are not confirmed yet.

The synth is in **arrhythmia-firmware** (build 7229) only. In arrhythmia-devkit (build 7228) track 8 is a stock
sample track and there is no synth page.

## What it is

In Arrhythmia, track 8 is a synth instead of a sample track. It has **4 voices in total**, and that count includes notes
that are still fading out. Tracks 1 to 7 stay sample tracks.

Track 8 keeps its stock mixer level, pan, sends and FX. It plays from:

* the pads, while track 8 is selected;
* the sequencer, from steps recorded on track 8;
* MIDI in on track 8's channel (channel 8 by default);
* modules (for developers).

## Notes per press

The main setting. Each pad press or sequencer step plays this many notes:

| Notes per press | Pads and sequencer | MIDI in |
|---|---|---|
| 1 | one note at a time; the newest note wins | polyphonic: up to 4 notes at once |
| 2 to 4 | a chord; each new press replaces the previous chord | a chord; each new note replaces the previous chord, as from the pads |

## Chords

With notes per press 2 to 4, the pressed note is the root and the chord settings pick the other notes:

* **Chord type:** In key (the chord that fits the project's scale on that root), or a fixed type: Major, Minor,
  Sus2, Sus4, 7th, Minor 7th.
* **Voicing:** root position, first inversion, second inversion, spread.
* **Octave:** -2 to +2.
* **Scale and key:** shared by the whole project and by the scales on tracks 1 to 7 (ten scales, see
  [scales.md](scales.md)). In key builds its chords from that scale.

## The synth page: Shift + Track 8

![The synth page, without and with Shift held](img/synth-page.png)

Example state; bright pads are the current setting.

Hold Shift and press Track 8 to open the synth page. Press it again to close.

| Pad row | Pads (from the left) |
|---|---|
| 1 | 1 to 4: notes per press 1 to 4 (white); 5 to 8 dark |
| 2 | 1 to 6: chord type Major, Minor, Sus2, Sus4, 7th, Minor 7th (orange, dimmer while In key is on); 7: In key (green); 8: natural minor or major (violet) |
| 3 | 1 to 4: voicing root, first inversion, second inversion, spread (cyan); 8: MIDI poly on or off (red) |
| 4 | 1 to 5: octave -2 to +2 (blue; the middle pad is 0) |
| Shift held | rows 1 and 2: the ten scales (violet); rows 3 and 4: a keyboard for the key (C to B, lit yellow) |
| Track 1 to Track 7 | scales on or off for that track (bright green = on) |

The page refuses an edit while the unit is busy (for example saving): it blinks red and nothing changes. Try again.
The page does not open while a module is running.

Arrhythmia 0.1 has **one fixed synth sound**: a saw oscillator with a fixed attack and a decay. Each note fades by
about 60 dB in about 2 seconds and is silent after about 2.6 seconds. It fades even while you hold the key: there is
no sustain yet (sustain is planned for a later version). The 4 voices include notes that are still fading, so in
dense playing a new note can take the voice of a fading one and cut its tail short. There are no presets, and attack,
decay and level are hidden on the page: the macro knobs do nothing while the page is open and do not change the synth
sound. Sound controls are planned for v0.2.

## MIDI

* Channel: track 8's MIDI channel, channel 8 by default.
* With notes per press 1, MIDI notes play polyphonically, up to 4 at once.
* When all 4 voices are busy, a new note takes a voice: first a free one, then the one released longest ago, then the
  one held longest.
* Playing the same note twice, or the same note from MIDI and a pad together: each press is its own note with its
  own voice. A note off releases the newest matching note from the same source.

## Recording

The sequencer stores one note per step on each track. When you record a chord on track 8, the step keeps the root
note you played. On playback the chord is built again from the current settings, so changing the chord type also
changes recorded chords.

**TBD:** what is kept when you record overlapping MIDI notes.

## Saving

The synth settings are saved with each project.

* A project from stock firmware opens with the default synth settings and notes per press 1.
* An Arrhythmia project on stock firmware loads normally. Track 8 then plays its recorded notes on its sample, one at a time.
  If you save it on stock and open it again in Arrhythmia, the synth settings are still there. **TBD:** confirmed on
  hardware.

## Tips

* Four voices at full level are loud. Lower track 8's level if the mix clips. **TBD:** level advice.
* Very heavy projects with all FX and 4 synth voices may make the audio glitch. Stop and Save should keep working
  (tested in emulation; **TBD:** on hardware).
* If Rec blinks white, the sound processor stopped answering: save, then turn the unit off and on (see
  [install.md](install.md#rec-blinks-white)).
* A project in slice mode: track 8 ignores slice mode and plays the real notes.
* Choke groups that include track 8 choke the synth like any other track.
