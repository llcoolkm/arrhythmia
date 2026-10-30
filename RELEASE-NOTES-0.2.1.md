# Arrhythmia 0.2.1 release notes

Arrhythmia is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.2.1 builds on 0.2.0. The FM synth on track 8 is the same. The changes are in the controls, the sequencer and the
modules.

## Changes since 0.2.0

### New

* **Feedback on macro 1.** On track 8, macro 1 now sets FM feedback. It was tune. Feedback is audible only with FM
  depth (macro 6) above 0. Clear + macro 1 sets feedback 0.
* **Tune on the synth page.** Tune is now macro 1 of the synth page (Shift + Track 8). 64 is no change. There are 5
  steps per semitone, up to 12 semitones up or down.
* **Step chords.** A track 8 step can hold up to 4 notes. In the Note view, hold the step and press more keys. Each
  key adds a note. Press a chord note again to remove it. With notes per press 1, pads or MIDI notes played together
  record as one chord step. Details: [Step chords](docs/user/synth.md#step-chords).
* **Startup pulse.** While the unit loads the pack at power-on, the pads show a heartbeat line over the load
  progress. Loaded pads are dim green.
* **Modules from the microSD card.** Put module files in the folder `Modules` on the card. Shift + Sample Rec opens
  the picker.
  * **Numbered pads.** A file `NNname.mod` goes on pad NN (01 to 32). Pad 1 is top left. Pads without a file are
    dark. Files without a number go on the free pads. Details: [Loading](docs/user/developers.md#loading).
  * **Colours.** Each pad of the picker has a colour from the file name. A module keeps its colour when you change
    its number.
  * **Play always works.** Play starts and stops the transport while a module runs, and while the picker is open.
    Modules that follow the clock start with Play.
  * **Play blinks.** While a module waits for Play, the Play button blinks green.
* **8 new modules.** They are card files in the release, with paint and stars:

  | Pad | File | Module |
  |---|---|---|
  | 1 | `01paint.mod` | Paint: pads light, fade and play notes |
  | 2 | `02euclid.mod` | Euclid: four Euclidean rhythm rows |
  | 3 | `03life.mod` | Life: the Game of Life plays notes |
  | 4 | `04rain.mod` | Rain: falling drops play notes |
  | 5 | `05balls.mod` | Balls: bouncing balls play notes at the walls |
  | 9 | `09pulse.mod` | Pulse: lights on the beat |
  | 10 | `10stars.mod` | Stars: twinkling lights |
  | 17 | `17snake.mod` | Snake: a game |
  | 18 | `18lights.mod` | Lights Out: a puzzle |
  | 19 | `19mole.mod` | Whack-a-mole: a game on the beat |

  Euclid, life, rain, balls, pulse and mole follow the clock: push Play. The controls of each module:
  [modules](docs/user/modules.md).

### Changed behaviour

* **Paint is not built in.** Paint is now a card file, `01paint.mod`. Without a card, the picker shows no pads.
* **0.2.0 projects are converted.** When you open a project from 0.2.0 or earlier, its tune goes to the synth page
  and its feedback goes to macro 1. The sound stays the same. Save the project to keep the change. 0.2.0 does not
  read the tune and feedback of a project saved by 0.2.1.
* **Projects from the stock firmware** open with feedback from their old Tune value. Examine macro 1.
* **Locks on macro 1** in older projects were tune locks. They now set feedback.
* **Knob lights.** On track 8, the lights of macro 1 (feedback) and macro 4 (sustain) go from off to full. The light
  of tune on the synth page has its centre at 64.

### Fixed

* The light of macro 1 on track 8 showed feedback as a value with a centre. It now goes from off to full.
* In the Note view, chord notes kept their second colour after you selected another step.
* When you held a chord step, only its root showed. All notes of the chord now show.

## Known issues

* **Feedback and sustain apply to new notes.** Macro 1 (feedback) and macro 4 (sustain) take effect at the next
  note start. A knob turn does not change sounding notes.
* **Stars does not take the pads.** While stars runs, the pads play track sounds as usual.
* All known issues of 0.2.0 stay: see the [0.2.0 release notes](RELEASE-NOTES-0.2.0.md#known-issues).

## Test status

**On the unit, image 7329 itself:**

* Install with no Play from a development build, version query and identity: pass.
* The synth plays on track 8, and FM depth (macro 6) changes the tone: pass.
* The health report right after that install was refused because notes were playing. It was not repeated.
* Install from 7329 to a development build, with the confirmation on Play (the install gate of the public image):
  pass.

**On the unit, with a development build with the same code as 7329 except the install gate (7330):**

* Install and health report: pass.
* Chord note lights in the Note view: the second colour clears after the release, and the root and the chord notes
  come on together: pass. Both come on after the hold delay of the unit, approx. 300 ms.

**On the unit, with an earlier development build (7328):**

* Startup pulse line, feedback knob light, tune light, chord notes that follow the selected step, the picker, life
  with Play, snake paused: pass.
* Sustain light: it dims at the bottom of its range, as the other knob lights do (stock behaviour).

**In emulation only:** the conversion of 0.2.0 projects, and the modules and module functions not named above.

**Not measured:** the processing time of the synth on the unit, as in 0.2.0.

## Install

Use the installer page, as for 0.2.0: [install guide](docs/user/install.md). An earlier Arrhythmia release updates
the same way.

| Build | Number | SHA-256 of the patched `.syx` |
|---|---|---|
| arrhythmia-firmware | 7329 | `f756c03a71039ba3edd248d53232f9bac24367a4cf800fe6ffd9b8d00fb15251` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is released again.

The version query (SysEx `70`) ends `00 39 21` for 7329 and `00 38 3C` for 7228. In bootloader mode the pads show
`73 29` for 7329. The release file is `release/v0.2.1.json`. The files of the earlier releases stay in the repository
as their record.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706 (SHA-256
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`), on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The ARM source is in `patches/src/`. The DSP source is in `tools/dspvoice207rel/`; it is the same as in 0.2.0. The
[rebuild steps](docs/README.md#rebuilding-the-images) describe how image 7329 rebuilds from this tree with the official
firmware 5706 file as the only firmware input.

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
