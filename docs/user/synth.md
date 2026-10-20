# Arrhythmia: the track 8 chord synth

Applies to Arrhythmia 0.1.1 (arrhythmia-firmware build 7261, arrhythmia-devkit build 7228). Test status is in the
[release notes](../../RELEASE-NOTES-0.1.1.md). Items marked **TBD** are not confirmed yet.

The synth is in **arrhythmia-firmware** (build 7261) only. In arrhythmia-devkit (build 7228) track 8 is a stock
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

The synth has **one saw sound** with an attack and a decay (see [Attack and decay](#attack-and-decay)). A note
fades even while you hold the key: there is no sustain. The 4 voices include notes that are still fading, so in
dense playing a new note can take the voice of a fading one and cut its tail short. There are no presets. The synth
page does not show attack, decay or level: set attack and decay with macros 2 and 3 in the normal track view, and the
loudness with the track 8 mixer level.

## Attack and decay

New in 0.1.1. On track 8:

| Control | Sets | Values |
|---|---|---|
| Macro 2, or MIDI CC 22 on track 8's channel | attack | 0 to 127 |
| Macro 3, or MIDI CC 23 on track 8's channel | decay | 0 to 127 |

* **Range (design values, not measured on the unit):** attack about 2 ms to 2 s; decay T60 (the time to fall by
  60 dB) 50 ms to 8 s. T60 is not the time until the note is silent.
* **Default:** new projects start with attack 0 and decay 64. Decay 64 has a T60 of 2 seconds. These values match
  the fixed sound of 0.1.0.
* **No sustain.** A note rises through its attack, then decays. Holding a pad does not hold the level.
* **Fine parts:** Shift + macro 2 or 3 (CC 86 and CC 87) has no effect on the synth.
* **Clear + macro 3** on track 8 sets the decay to 64. Clear + macro 2 sets the attack to 0. In lock edit (Rec held
  with a step), Clear + macro 3 records the value 127 as the lock of that step, as on stock. Tracks 1 to 7 keep the
  stock behaviour.
* **Saving:** attack and decay are saved with the project. A project from stock firmware or from 0.1.0 whose track 8
  decay (macro 3, Length on the stock panel) is at the stock value 127 opens with decay 64. Other values stay. Save
  the project to keep the change. After a save in 0.1.1, every value that you set stays, 127 included.

### Parameter locks

Attack and decay accept parameter locks (Rec, hold a step, turn macro 2 or 3), as the other macros do. A step without
a lock uses the knob (base) value.

* **Global effect.** Attack and decay are global for the synth. A locked value changes all track 8 voices that are
  sounding, not only the note of the locked step. Example: a long decay locked on step 1 becomes shorter when step 2
  plays with the knob value.
* **A step without a note** uses the knob value again. Attack and decay locks stored on a step without a note are
  ignored. A lock carried from the preceding step that has a note still plays. Arrhythmia never erases a lock: put a
  note on the step again and its locks play again.
* **Probability.** A step that holds a note counts as a step with a note, also when its probability skips the note.
  Its attack and decay locks apply.
* **Attack after the attack phase.** A change to attack does not restart the attack of a note that is already in its
  decay.

### Stop, mute and cuts

* When playback stops (press **Play**; the unit has no Stop button), on MIDI CC 123 (all notes off) and when track 8
  is muted, sounding synth notes play out their decay, like samples.
* A note that the sound processor had already accepted at the moment of the stop still starts, then decays. This edge
  is accepted.
* CC 120 (all sound off) is ignored, as on stock: it does not cut the synth notes.
* Loading a project or a pack, a choke and a sample edit cut the synth notes at once.

### Slice mode

Track 8 ignores slice mode. Macros 2 and 3, their locks and their lights, the Note-view keyboard, the octave arrows
and the expanded view work as without slice mode. Lock recording on track 8 in slice mode is tested only while the
transport is stopped.

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
* A project in slice mode: track 8 ignores slice mode and plays the real notes (see [Slice mode](#slice-mode)).
* Choke groups that include track 8 choke the synth like any other track.
