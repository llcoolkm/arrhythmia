# Circus Arrhythmia FM: the track 8 FM synth

Applies to Circus Arrhythmia FM 0.2.2, firmware build 7339. Test status and full known issues: see the
[release notes](../../RELEASE-NOTES-0.2.2.md).

* Track 8 is a 4-operator FM synth. Tracks 1 to 7 are sample tracks.
* The synth has 4 voices. A note that fades out after its release occupies a voice.
* Note sources: pads (track 8 selected), sequencer, MIDI on the channel of track 8 (default 8), modules.
* The synth output passes through the mixer channel of track 8: level, pan, sends and FX apply.

## Choosing a sound: the Sample view

1. Select track 8.
2. Open the Sample view.
3. Tap a pad in row 3 or 4. The tap selects the sound and plays it.

![Track 8's Sample view: the 16 families on the first page (serial) and the second page (dual stack)](img/synth-sample-view.png)

* The view opens on the page of the selected sound.
* A new sound applies from the next note. Feedback, modulator envelope and FM depth do not change.
* At low FM depth, the families sound almost the same. Increase macro 6.
* Sound per step: hold a sound pad and tap a step, or the reverse. A recorded sound pad press also stores the sound.
* CC 8 values 32 to 127 select Unity, serial.

<a id="attack-and-decay"></a>

## Macros on track 8

Select track 8. The macro knobs then control the synth.

![The eight macros on track 8 with their MIDI CC numbers](img/synth-macros.png)

The ranges are design values. They are not measured on the unit.

| Macro | Range |
|---|---|
| 1 feedback | 0 to 127; audible only with FM depth (macro 6) above 0 |
| 2 attack | approx. 10 ms to 8 s |
| 3 decay | T60 50 ms to 8 s |
| 4 sustain level | linear, 0 = silent, 127 = full |
| 5 release | T60 approx. 29 ms to 8 s, smooth start |
| 6 FM depth | 0 = no FM; new projects 96 |
| 7 filter cutoff | 0 to 127 (refer to Filter below) |
| 8 resonance | 0 to 127 (refer to Filter below) |

* MIDI CC 21 to 28 and parameter locks operate on all eight macros.
* Macros 1 to 5 apply at note start. A knob turn changes the next notes, not the sounding notes.
* Macros 6 to 8 apply immediately.
* A step without a lock uses the knob value. A lock on macro 1 sets the feedback of that step.
* The lights of macro 1 (feedback) and macro 4 (sustain) go from off to full.
* T60 is the time to decrease by 60 dB. It is not the time to silence.
* Clear + macro 1 sets feedback 0. Clear + macro 6 sets FM depth 96.
* Clear + macro 3 sets decay 64. In lock edit (Rec held with a step), it records 127, as on stock.
* Shift + macro 2 or 3 (CC 86, CC 87) has no effect.
* Filter: quiet signals keep approximately the same low-frequency level at all resonance values.
* Filter: the top of the cutoff range is smooth. Strong resonant peaks are shaped to limit their level. This shaping
  changes their tone.

## Output level

* The synth output is decreased by a fixed **4 dB**, before the level, pan, sends and FX of track 8.
* No setting changes this value.
* Set the balance with the mixer level of track 8. A higher level increases the risk of crackle on loud chords.

## The synth page

Open the synth page with **Shift + Track 8**.

![The two pages of the synth page and its macros 1 to 4](img/synth-page.png)

| Page | Pads |
|---|---|
| 1 chords | notes per press, chord type, In key, minor or major, voicing, poly, octave |
| 2 scale | row 1, pads 1 to 7: scales on or off for track 1 to 7; row 2: scale (eight scales); rows 3 and 4: key, C to B ([scales.md](scales.md)) |

* **Down** shows page 2. **Up** shows page 1. The Up or Down light is on when there is a page in that direction.
* The synth page opens on the last page used. After a power cycle, it opens on page 1.
* A view or track button (for example Note, Sample, Velocity, Gate, Mixer, Track 1 to 8) closes the page. The unit
  goes to that view or track, as from any other view. The button press is not lost.
* Note goes to the Note view in its last layout (normal or expanded). It does not switch between the layouts.
* Shift + Track 8 also closes the page.
* Play and Rec operate while the page is open. The page stays open.
* Shift has no function on the page. Shift + pad does the same as the pad.

Lights while the page is open:

* The view and track buttons keep their normal lights. Track 8 is magenta.
* Up or Down is green when there is a page in that direction.
* Shift keeps its normal light.
* Clear keeps its normal light (dim red when that light is off). Clear is red while you hold it.

![The lights of the synth page on page 1 and page 2: exit buttons with their normal lights, Track 8 magenta, Clear dim, Up or Down green, knob lights 1 to 4 green](img/synth-page-lights.png)

| Synth page macro (all pages) | New project and Clear + macro |
|---|---|
| 1 tune (64: no change; 5 steps per semitone, plus or minus 12 semitones) | 64 |
| 2 modulator attack (same range as attack) | 0 |
| 3 modulator decay | 64 |
| 4 modulator floor (modulation level after its decay) | 127 |

* Macros 1 to 4 apply at the next note start. They do not change sounding notes.
* **Clear + macro** sets that macro to the value in the table. Hold Clear and turn the knob.
* Macros 5 to 8 have no function on the page.
* The light of macro 1 (tune) has its centre at 64.
* The project saves the values.
* **Save** closes the page and starts the save, as from any other view. A knob turn immediately before the save is saved.
* When the unit is busy, the Track 8 light blinks red and the page ignores the edit. Do the edit again.
* The page does not open while a module runs. Shift + Sample Rec on the page closes the page and opens the module
  picker.

## Notes, chords and voices

| Notes per press | Pads | Sequencer | MIDI in |
|---|---|---|---|
| 1, poly on | held pads sound together, up to 4 | newest note wins | up to 4 notes |
| 1, poly off | newest note wins | newest note wins | newest note wins |
| 2 to 4 | chord; a new press replaces it | same as pads | chord; a new note replaces it |

* Chords: the pressed note is the root. In key uses the project scale and key, shared with tracks 1 to 7
  ([scales.md](scales.md)).
* Voice order when all 4 voices are busy: free voice, voice released longest ago, voice held longest.
* Each press of the same note has its own voice. A note off releases the newest matching note from that source.
* With notes per press 2 to 4, a recorded chord stores its root note. Playback uses the current chord settings.
* With notes per press 1, pads or MIDI notes played together record as a step chord ([Step chords](#step-chords)).
* Track 8 ignores slice mode.
* Stop (press Play), CC 123 and mute: sounding notes go to their release. CC 120 has no effect, as on stock.
* Project load, pack load, choke and sample edit: sounding notes stop immediately. Choke groups operate on track 8.

## Step chords

A step of track 8 holds up to 4 notes. All notes of the step share its velocity, gate, probability, microsteps,
sound and locks.

Enter a chord in the Note view:

1. Hold the step.
2. Press a key. On an empty step, this key sets the root, as on stock.
3. Press more keys. Each key adds its note, up to 4. The step plays the chord.
4. Press a chord note again to remove it. Press the lowest note to remove the step. The step stays removed when you
   release it. A step with 1 note operates as on stock: when you release the step, it comes back as a new step.

* The lowest note is the root of the step. A new lower note becomes the root. The other notes stay.
* While you hold the step, the other notes of the chord show in a second colour.
* Notes more than 96 semitones above the lowest note are not added.

Record a chord:

1. Set notes per press to 1.
2. Start the recording.
3. Press 2 to 4 pads together, or send 2 to 4 MIDI notes together.

* Notes that start in the same step, or within 1 microstep of the first note, make one chord step.
* A note joins the chord only while another note of the chord is held.
* The gate runs from the first note to the release of the last released note.
* A new note while chord notes are held ends the gate of the chord at the new note.
* A project load, a recording stop, or notes per press 2 to 4 ends the chord in recording. The next note starts a
  new step.

Playback and storage:

* The stored notes play as stored. Notes per press, chord type and In key do not change them. Track octave applies.
* A step with 1 note uses the current chord settings.
* MIDI out sends the root only.
* A project holds up to 31 different chord shapes (the intervals above the root). When all 31 are in use, a new
  shape is not added. Remove chord steps that use other shapes first. Shapes that no step uses are reused.
* During a project save or load, a step does not change and a recording stores a single note.
* If the Arrhythmia data of the project is damaged or from a newer version, or its chord shapes are not valid,
  chord steps play only the root and do not change.
* The stock firmware plays only the root of a chord step. A stock save keeps the chords for Arrhythmia.

## Loading samples while the unit runs

The synth starts again automatically after a sample load.

| Event | Recovery |
|---|---|
| Pack change; sample transfer or load, including SysEx read-back | yes |
| Sample edit that transfers sample data | yes, same path |
| Other sample edits (preview, trim, start, end, tune, other parameters) | no; can stop sounding track 8 notes |
| Project change in the same pack | no; can stop sounding track 8 notes |
| Loading at startup | no |

* During the load, sounding track 8 notes stop and new track 8 notes do not sound.
* After the load, the synth uses the settings loaded at that time. After a pack change, these settings can be
  different.
* If the restart does not complete, track 8 stays silent. Save the project. Then turn the unit off and on.

## Saving and older projects

The project saves the synth settings, the selected sound, the sounds per step and the macros.

| Project | Opens with |
|---|---|
| New | Unity, serial; FM depth 96; other defaults as in the images |
| Stock or imported | its stored track 8 sample slot as sound; its macro values; feedback from its old Tune value |
| Arrhythmia 0.1.0 to 0.2.0 | stored slot as sound; macros; synth page; same sound (tune to the page, feedback to macro 1) |

* Slot 0 to 31 (counted from 0) selects that pad on page 1 or 2. Slot 32 or higher plays Unity, serial.
* In stock projects, macros 1 and 4 to 8 set synth values. Examine them. Macro 6 at 0 gives no FM depth.
* Stock or imported projects open with feedback from their old Tune value. Examine macro 1.
* Locks on macro 1 in older projects were tune locks. They set feedback.
* Arrhythmia 0.2.0 does not read the tune and feedback of a project saved by 0.2.1. It uses its defaults.
* Stored sample flips on track 8 select a sound per step (slot rules as above). Old patterns can sound different.
  Arrhythmia does not remove the flips.
* In a project never opened in Arrhythmia, track 8 length (macro 3) 127 opens as decay 64. Save to keep it.
* If Rec blinks white, the DSP does not answer. Save, then turn the unit off and on
  ([install.md](install.md#rec-blinks-white)).

## Known issues

Details: [release notes, known issues](../../RELEASE-NOTES-0.2.2.md#known-issues),
[0.2.1 known issues](../../RELEASE-NOTES-0.2.1.md#known-issues) and
[0.2.0 known issues](../../RELEASE-NOTES-0.2.0.md#known-issues).

* Some settings with high level or high resonance can crackle.
* On the test unit, one sample also crackles with the official firmware 5706.
* High notes lose the character of the family.
* A note released before its attack ends can produce a small tick.
* The end of a fading note has a small rounding artefact at a very low level.
