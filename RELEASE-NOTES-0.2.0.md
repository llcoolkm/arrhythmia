# Arrhythmia 0.2.0 release notes

Arrhythmia is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.2 is the first stable release of the FM synth on track 8. One part is experimental: the processing time of the
synth has not been measured on the unit (see [Known issues](#known-issues)).

## Changes since 0.1.1

### New

Track 8 in arrhythmia-firmware is now a **4-operator FM synth** with 4 voices. It replaces the saw synth of 0.1.x.
The chord functions, the scales, the parameter lock fix and the modules stay. arrhythmia-devkit is unchanged from
0.1.0.

* **Sounds in the Sample view.** Track 8's Sample view chooses the FM sound. The 16 pads of rows 3 and 4 are 16
  ratio families. The first page plays them under the serial algorithm, the second page (Down) under the dual stack
  algorithm. A tap selects the sound and plays it. The pad of the selected sound is bright, and the view opens on its
  page.
* **Per-step sounds.** The stock sample flip on a step selects the sound for that step. MIDI CC 8 on track 8's channel
  selects the sound.
* **Eight macros on track 8.** Macro 1 tune, 2 attack, 3 decay, 4 sustain, 5 release, 6 FM depth, 7 filter cutoff,
  8 resonance. MIDI CC 21 to 28 and parameter locks work on all eight. If the sounds sound alike, turn macro 6 up.
* **Sustain.** A held note stays at the sustain level until you release it, then fades through its release.
* **Envelope ranges.** Attack and modulator attack run from about 10 ms to 8 s. Release T60 (the time to fall by
  60 dB) runs from about 29 ms to 8 s and starts smoothly. Decay T60 runs from 50 ms to 8 s. These are design values,
  not measurements on the unit.
* **Filter.** Quiet signals keep about the same low-frequency level at every resonance setting, and the top of the
  cutoff range is smooth. Strong resonant peaks are shaped to keep their level bounded; this can change their tone.
* **Feedback and the modulator envelope.** On the synth page (Shift + Track 8), macros 1 to 4 set feedback, modulator
  attack, modulator decay and modulator floor. They apply at the next note start, not to sounding notes. Feedback is
  audible only with FM depth (macro 6) above 0. The knob lights show the values.
* **Pad chords.** With notes per press 1 and poly on, held pads sound together, up to 4.
* **New projects** start on Unity under serial (the first pad of the first page), with FM depth 96.
* **Clear + macro 6** on track 8 sets FM depth to 96, the new-project value.
* **Fixed output level.** The synth's output is lowered by a fixed amount, against crackle on loud chords. Set the
  balance with track 8's mixer level. Details, the amount and a loudness measurement:
  [Output level](docs/user/synth.md#output-level).

Details: [docs/user/synth.md](docs/user/synth.md).

### Changed behaviour

* **Macros 1 to 5 act at note start.** Tune, attack, decay, sustain and release are taken when a note starts. A knob
  turn or a lock changes the next notes, not the sounding ones. In 0.1.x, attack and decay changed every sounding
  note. Macros 6 to 8 are live and also change sounding notes.
* **Stop, CC 123 and mute** send sounding notes to their release.
* **Existing projects** (from 0.1.x, from stock firmware or imported) keep their stored track 8 sample slot, which
  now selects the sound, and their macro values, which now have synth meanings on macros 1 and 4 to 8. Only a new
  project gets the new defaults. One exception, as in 0.1.1: in a project never opened in Arrhythmia, a track 8
  length (macro 3, Length on the stock panel) set to the full stock value 127 is read once as the default decay 64.
  Any other value is kept. Save the project to keep the change.
* **Stored sample flips on track 8** now change the sound of those steps. In 0.1.x they had no audible effect.
  Patterns saved under 0.1.x and factory patterns that flipped track 8's samples sound different.

### Fixed

* **Synth silent after sample loading.** In earlier 0.2 test builds, the synth stayed silent after a sample load
  until a power cycle. Now, after a pack change or a sample transfer or load (including read-back over SysEx), track
  8's synth starts again by itself with the settings loaded at that moment. Notes sounding on track 8 at that moment
  are cut, and notes for track 8 during the load do not sound. Ordinary sample edits do not trigger it. Details:
  [Loading samples](docs/user/synth.md#loading-samples-while-the-unit-runs).
* **FM feedback too weak.** FM feedback (synth page macro 1) was too weak to hear. Its range is increased 4 times.
  Feedback applies from the next note and needs FM depth above 0.

### Other

* arrhythmia-firmware answers a diagnostic request over USB MIDI; it changes no sound, setting or project, while
  diagnostic counters and read bookkeeping advance ([details](docs/dev/devkit-sysex.md#diagnostic-report-02)).

## Known issues

### Processing time is not measured on the unit

This release is experimental in one respect: the DSP processing time of the synth is not measured on the unit.

* Estimate for the heaviest synth case: 128,409 cycles per audio block. It is a prior estimate from an earlier
  build. It is not a measured worst case and is not derived again for this release.
* It is composed from a model (128,288 cycles) plus the model cost of a fixed output level (121 cycles).
* We compare it with 131,072 cycles, a ceiling that we chose for the model. It is not a physical deadline.
* Silicon timing is not measured. The model does not cover all combinations with seven sample tracks and effects.
* If the processor runs out of time, audio can glitch or drop out.
* Glitches may occur without a fault report or Rec indication. Report them with the project settings.

### Restart after a sample load

* If the restart after a sample load does not complete, track 8 stays silent until you turn the unit off and on.
  Save your project first.
* How frequently a restart does not complete on the unit is not known. Test results: see [Test status](#test-status).

### Loud chords and high resonance

Some settings at high level or with high resonance can still crackle. In a model at this output level, the master
filter limits the signal in some cases with overlapping loud notes or transitions at high resonance. The fixed
[output level](docs/user/synth.md#output-level) does not guarantee clean headroom for all settings. A higher track 8
level makes crackle more likely.

### A sample crackled, also on stock firmware

On our unit, the same sample was heard crackling with the verified official firmware 5706 installed. The cause and
extent are unknown.

### High notes lose the character of the family

At high pitch, the modulation decreases gradually to limit aliasing. Aliasing is not fully removed. From the MIDI note
below, the family has no modulation (as FM depth 0), and macro 6 has no effect. Both algorithms, tune 64, design
values, not measured on the unit:

| From note | 91 | 96 | 101 | 103 | 105 | 106 | 109 | 115 | 119 |
|---|---|---|---|---|---|---|---|---|---|
| Family | High metal | Deep bell | Chime | Organ | Hollow | Metal | Bell | Bright | all others |

At high pitch, several families sound the same. Feedback (synth page macro 1) is not free of aliasing.

### Other issues

* Releasing a note before its attack finishes can produce a small tick, especially at the fastest attack settings.
* The very end of a fading note carries a small rounding artefact at a very low level. Its audibility has not been
  established.
* With FM depth (macro 6) at 0 the families sound almost the same. Older projects can have macro 6 at 0.
* Only the first two pages of track 8's Sample view hold sounds. The other six pages are dark and do nothing.
* A tap in the Sample view plays the last note that track 8 played. A very high note can sound plain.
* Track 8 has 4 voices in total, including notes in their release.
* Save does not work while the synth page is open. Close the page first.

### To be verified on the unit

Sample view sound selection; install of build 7323 with the confirmation on Play; FM settings
after power off; the voice of a fifth held pad; the sound after a choke.

## Test status

**Image 7323 itself:** not run on the unit. Image 7323 has the same synth, feedback and sample-load recovery code as the
development builds below; the development builds have a different install gate. The install with the confirmation
on Play is tested in emulation only.

**On the unit, with a development build with the same synth, feedback and sample-load recovery code as 7323 (the
development build has a different install gate):**

* Install, version query and health report: pass.
* Feedback check, played on the unit at feedback 0 to 127 with FM depth above 0: pass.

**On the unit, with a development build with the same sample-load recovery code:**

* Pack switch away and back, automatic recovery on both switches: pass.
* 80-note run after the recovery: pass.

**On the unit, with earlier development builds with the same FM engine, without the sample-load recovery:**

* Install and return to the previous build: pass.
* Stress pattern of 2,458 MIDI messages, health report clean: pass.
* Host-driven four-note attack and release test: pass.
* Clear + macro 6 on the unit sets FM depth 96: pass.
* Host-driven repeated-note test with nine notes: pass.
* Chord crackle test with a fixed output level 2 dB lower than this release: pass.
* Loudness of the synth against stock samples: measured (see [Output level](docs/user/synth.md#output-level)).
* An earlier build, which chose the sound on a separate synth page view: idle for 10 minutes with no fault, single
  notes and chords: pass.

**In emulation only.**

* The sample-load recovery for a sample loaded into a single slot (not tested on the unit), SysEx transfer and
  read-back, and restarts that fail.
* The confirmation on Play during install, which 7323 keeps unchanged from stock.
* The sound selection in the Sample view, the two pages, the dark pages, the entry page, CC 8, per-step sounds, the
  new-project defaults and the synth page macros 1 to 4.

**Not measured:** the processing time of the synth on the unit (see [Known issues](#known-issues)).

**Not tested:** deliberate DSP fault containment (what the unit does when the sound processor is made to stop
answering on purpose); arrhythmia-devkit 7228 on hardware (as in 0.1.0 and 0.1.1); Edge and macOS.

## Install

Use the installer page, as for 0.1.1: [install guide](docs/user/install.md). An earlier Arrhythmia release updates the same
way. The page checks your official firmware file and patches it on your computer.

| Build | Number | SHA-256 of the patched `.syx` |
|---|---|---|
| arrhythmia-firmware | 7323 | `f5f77f2902aa7934c0c5dd22bbb890ce4c5aaa8f683d4f55e10897df95178cd5` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is released again.

The version query (SysEx `70`) ends `00 39 1B` for 7323 and `00 38 3C` for 7228. In bootloader mode the pads show
`73 23` for 7323. The release file is `release/v0.2.0.json`. `release/v0.1.1.json` and `release/v0.1.0.json` stay in
the repository as the record of the earlier releases.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706 (SHA-256
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`), on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The 0.2 ARM source is published in `patches/src/`. The DSP source of the FM synth, with its fixed output level, is
published in `tools/dspvoice207rel/`. The [rebuild steps](docs/README.md#rebuilding-the-images)
describe how image 7323 rebuilds from this tree with the official firmware 5706 file as the only firmware input. Installation
still uses the official firmware and the published release patch. arrhythmia-devkit 7228 and the 0.1.1 image 7261
rebuild from source as before.

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
