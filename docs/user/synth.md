# Arrhythmia: the track 8 FM synth

Applies to Arrhythmia 0.2, arrhythmia-firmware build 7323. Test status and full known issues: see the
[release notes](../../RELEASE-NOTES-0.2.0.md). **TBD** items are not confirmed.

* Track 8 is a 4-operator FM synth. It replaces the saw synth of 0.1.x. Tracks 1 to 7 are sample tracks.
* arrhythmia-devkit has no synth. Track 8 is a stock sample track there.
* The synth has 4 voices. Notes in their release use voices.
* Note sources: pads (track 8 selected), sequencer, MIDI on track 8's channel (default 8), modules.
* Track 8 keeps its stock mixer level, pan, sends and FX.

## Choosing a sound: the Sample view

1. Select track 8.
2. Open the Sample view.
3. Tap a pad in row 3 or 4. The tap selects the sound and plays it.

![Track 8's Sample view: the 16 families on the first page (serial) and the second page (dual stack)](img/synth-sample-view.png)

* The view opens on the page of the selected sound.
* A new sound applies from the next note. Feedback, modulator envelope and FM depth do not change.
* With low FM depth, the families sound almost the same. Increase macro 6.
* Per-step sound: hold a sound pad and tap a step, or the reverse. A recorded sound pad press also stores the sound.
* CC 8 values 32 to 127 select Unity, serial.

## Macros on track 8

![The eight macros on track 8 with their MIDI CC numbers](img/synth-macros.png)

| Macro | Range (design values, not measured on the unit) |
|---|---|
| 2 attack | approx. 10 ms to 8 s |
| 3 decay | T60 50 ms to 8 s |
| 4 sustain level | linear, 0 = silent, 127 = full |
| 5 release | T60 approx. 29 ms to 8 s, smooth start |

* T60 is the time to decrease by 60 dB. It is not the time to silence.
* In 0.1.x, attack and decay changed sounding notes. In 0.2 they apply at note start.
* A step without a lock uses the knob value.
* Shift + macro 2 or 3 (CC 86, CC 87) has no effect.
* Clear + macro 3 sets decay 64. In lock edit (Rec held with a step), it records 127, as on stock.
* Clear + macro 6 sets FM depth 96.
* Filter: quiet signals keep approximately the same low-frequency level at all resonance values.
* Filter: the top of the cutoff range is smooth. Strong resonant peaks are shaped to limit their level. This can
  change their tone.

## Output level

* The synth output is decreased by a fixed **7 dB**, before track 8's mixer level, pan, sends and FX.
* No setting changes this value. Only this page gives the value.
* Set the balance with track 8's mixer level. A higher level increases the risk of crackle on loud chords.

Loudness test (one test only): a development build with output 2 dB lower than this release; the loaded project's
sound; median of seven samples played on the unit; loudness over 9.5 s. Results: single note approx. 3 dB quieter,
four-note chord approx. 3 dB louder, four-note chord with hot settings approx. 15 dB louder. Other conditions give
other results.

## The synth page: Shift + Track 8

![The synth page, without and with Shift held, and its macros 1 to 4](img/synth-page.png)

| Synth page macro | New project |
|---|---|
| 1 feedback | 0 |
| 2 modulator attack (same range as attack) | 0 |
| 3 modulator decay | 64 |
| 4 modulator floor (modulation level after its decay) | 127 |

* Macros 1 to 4 apply at the next note start. They do not change sounding notes.
* Feedback is audible only with FM depth (macro 6) above 0.
* The project saves the values.
* **Save does not operate while the synth page is open.** Close the page, then press Save.
* When the unit is busy, the page blinks red and ignores the edit. Try again.
* The page does not open while a module runs. Up and Down have no function on the page.

## Notes, chords and voices

| Notes per press | Pads | Sequencer | MIDI in |
|---|---|---|---|
| 1, poly on | held pads sound together, up to 4 | newest note wins | up to 4 notes |
| 1, poly off | newest note wins | newest note wins | newest note wins |
| 2 to 4 | chord; a new press replaces it | same as pads | chord; a new note replaces it |

* Chords: the pressed note is the root. In key uses the project scale and key, shared with tracks 1 to 7
  ([scales.md](scales.md)).
* Voice order when all 4 are busy: free voice, voice released longest ago, voice held longest.
* Each press of the same note has its own voice. A note off releases the newest matching note from that source.
* A recorded chord stores its root note. Playback uses the current chord settings.
* **TBD:** the result of recording overlapping MIDI notes or held pad chords.
* Track 8 ignores slice mode, as in 0.1.1.
* Stop (press Play), CC 123 and mute: sounding notes go to their release. CC 120 has no effect, as on stock.
* Project load, pack load, choke and sample edit: sounding notes stop immediately. Choke groups operate on track 8.

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
* After the load, the synth uses the settings loaded at that time. After a pack change, these can be different.
* If the restart does not complete, track 8 stays silent. Save the project. Then turn the unit off and on.

## Saving and older projects

The project saves the synth settings, the selected sound, the per-step sounds and the macros.

| Project | Opens with |
|---|---|
| New | Unity, serial; FM depth 96; other defaults as in the images |
| Stock or imported | its stored track 8 sample slot as sound; its macro values |
| Arrhythmia 0.1.0 or 0.1.1 | its stored sample slot as sound; its macro values; its synth page settings |

* Slot 0 to 31 (counted from 0) selects that pad on page 1 or 2. Slot 32 or higher plays Unity, serial.
* Stock macros 1 and 4 to 8 now set synth values. Examine them. Macro 6 at 0 gives no FM depth.
* Stored sample flips on track 8 now select a sound per step (slot rules as above). Old patterns can sound different.
  Arrhythmia does not remove the flips.
* In a project never opened in Arrhythmia, track 8 length (macro 3) 127 opens as decay 64. Save to keep it.
* If Rec blinks white, the DSP does not answer. Save, then turn the unit off and on
  ([install.md](install.md#rec-blinks-white)).

## Known issues

Details: [release notes, known issues](../../RELEASE-NOTES-0.2.0.md#known-issues).

* Processing time is not measured on the unit. Glitches may occur without a fault report or Rec indication.
* A restart after a sample load can fail. Then track 8 stays silent until a power cycle.
* Some settings at high level or with high resonance can still crackle.
* On our unit, a sample crackled also with the official firmware 5706.
* High notes lose the character of the family.
* Releasing a note before its attack finishes can produce a small tick.
* The end of a fading note has a small rounding artefact at a very low level.
