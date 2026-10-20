# Arrhythmia 0.1.1 release notes

Arrhythmia is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

## What is new in 0.1.1

The track 8 chord synth in arrhythmia-firmware now has **attack and decay**. arrhythmia-devkit is unchanged from
0.1.0.

* **Macro 2 sets the attack** and **macro 3 sets the decay** on track 8. The values are 0 to 127. MIDI CC 22 and
  CC 23 on track 8's channel set the same values.
* **Parameter locks** work on both macros, as on the other macros.
* **New projects** start with attack 0 and decay 64. These values match the fixed sound of the 0.1.0 synth (a decay
  of about 60 dB in about 2 seconds).
* **Clear + macro 3** on track 8 sets the decay to 64, not to the stock value 127. Clear + macro 2 sets the attack to
  0, as on stock. Tracks 1 to 7 keep the stock behaviour.
* **Notes play out.** When playback stops (press Play), on MIDI CC 123 (all notes off) and when track 8 is muted,
  sounding synth notes play out their decay, like samples.
* **Track 8 ignores slice mode.** Macros 2 and 3, their locks and lights, the Note-view keyboard, the octave arrows
  and the expanded view work as without slice mode.

Details: [docs/user/synth.md](docs/user/synth.md#attack-and-decay).

## How attack and decay operate

* **No sustain.** A note rises through its attack, then decays. Holding a pad does not hold the level.
* **Attack and decay are global for the synth.** A knob turn or a parameter lock changes all track 8 voices that are
  sounding, not only the note of the locked step. Example: a long decay locked on step 1 becomes shorter when step 2
  plays with the knob value.
* **Range (design values, not measured on the unit):** attack about 2 ms to 2 s; decay T60 (the time to fall by
  60 dB) 50 ms to 8 s. Decay 64 has a T60 of 2 s. T60 is not the time until the note is silent.
* **Fine parts:** Shift + macro 2 or 3 (CC 86 and CC 87) has no effect on the synth.
* **Loudness:** the track 8 mixer level sets the loudness, as before.
* **Steps without a note:** such a step uses the knob (base) value again. Attack and decay locks stored on a step
  without a note are ignored. A lock carried from the preceding step that has a note still plays. Arrhythmia never
  erases a lock: put a note on the step again and its locks play again. A step that holds a note counts as a step with
  a note also when its probability skips the note: its locks apply.
* **Attack after the attack phase:** a change to attack does not restart the attack of a note that is already in its
  decay.
* **Clear + macro 3 in lock edit** (Rec held with a step) records the value 127 as the lock of that step, as on stock.
* **Projects from stock firmware or from 0.1.0:** if track 8's decay (macro 3, Length on the stock panel) is at the
  stock value 127, the project opens with decay 64. Other values stay. Save the project to keep the change. After a
  save in 0.1.1, every value that you set stays, 127 included.
* **Stop, CC 123 and mute:** notes play out their decay. One edge is accepted: a note that the sound processor had
  already accepted at the moment of the stop still starts, then decays. CC 120 (all sound off) is ignored, as on stock: it does not cut the synth notes.
* **Cuts:** loading a project or a pack, a choke and a sample edit cut the synth notes at once.

## Install

The procedure is the same as for 0.1.0: [install guide](docs/user/install.md). The installer page offers the two
images of this release.

## Two builds

| Build | Number | SHA-256 of the patched `.syx` |
|---|---|---|
| arrhythmia-firmware | 7261 | `920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

**arrhythmia-firmware (7261), for players:**

* Track 8 is a chord synth: 4 voices, one saw sound with attack (macro 2) and decay (macro 3), no sustain. Pads,
  the sequencer, MIDI on track 8's channel and modules play it.
* The synth page opens with **Shift + Track 8**: notes per press, chord type, voicing, octave, MIDI poly.
* Scales on tracks 1 to 7 (ten scales, shared key), set on the synth page.
* P-lock fix on all tracks: an unlocked step goes back to the knob's value.
* SD card module loader, with the built-in paint module.
* No DSP load meter.

**arrhythmia-devkit (7228), for developers:** unchanged from 0.1.0. The same image is released again in 0.1.1.

* DSP load meter and its read command.
* Module loader and SDK.
* Scales on tracks 1 to 7, set over SysEx only.
* P-lock fix.
* **No synth:** track 8 stays a stock sample track and there is no synth page.

The version query (SysEx `70`) ends `00 38 5D` for 7261 and `00 38 3C` for 7228. In bootloader mode the pads show
`72 61` for 7261. The release file is `release/v0.1.1.json`. `release/v0.1.0.json` stays in the repository as the
record of 0.1.0.

Image 7261 is byte-identical to the tested development build 7255 except for the build number. The ARM code of 0.1.1
is the 0.1.0 code plus the attack and decay code, which a build switch enables. The scales code for tracks 1 to 7 is
the same as in 0.1.0.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706 (SHA-256
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`), on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## What was tested on hardware

Development builds 7253, 7254 and 7255 of arrhythmia-firmware, on one unit, 2026-10-20:

* Install.
* Attack and decay with the knobs and with parameter locks.
* Clear + macro 3.
* Notes that play out after Play stops, on CC 123 and when track 8 is muted.
* Slice mode on track 8.
* Steps without a note.
* Save and power cycle.
* No DSP fault: the fault query was clean at the end.

Image 7261 was not separately hardware-tested. It differs from hardware-tested development build 7255 only in the build number.

Tested in emulation only:

* A lock on a single micro-step only (the carried lock of a step with a note). The exact stimulus cannot be set by
  hand on the unit.
* Timing. The timing check is an emulation; there is no hardware timing (WCET) claim.

Not tested:

* Deliberate DSP fault containment (what the unit does when the sound processor is made to stop answering on
  purpose).
* arrhythmia-devkit 7228 on hardware (as in 0.1.0).
* Edge and macOS.

The functions that 0.1.1 does not change keep the test record of the
[0.1.0 release notes](RELEASE-NOTES-0.1.0.md).

## Known limitations

* No sustain. The synth has one saw sound with attack and decay; there are no presets.
* Attack and decay are global: a lock also changes the notes of other steps that are still sounding.
* The 4 synth voices include notes that are still decaying, so dense playing can cut tails short.
* A note that the sound processor had already accepted at a stop still starts, then decays.
* Lock recording on track 8 in slice mode is tested only while the transport is stopped.
* Timing is checked in emulation only. A full pattern with long decays can cause audio errors; listen for them.
* The source of the sound processor (DSP) code of the synth is not published yet, so the full arrhythmia-firmware
  image cannot be rebuilt from source yet. The ARM side and arrhythmia-devkit can.
  Addendum 2026-10-21: the DSP source is now published in `tools/dspvoice/`. The full arrhythmia-firmware image (build
  7261) can now be rebuilt from source on Linux (tested on Ubuntu x86-64 only). See
  [tools/dspvoice/README.md](tools/dspvoice/README.md).
* The limitations of 0.1.0 that 0.1.1 does not change still apply (expanded view at the default octave, MIDI clock
  during scale changes, SysEx 7D, the soak result, power-cycle before flashing). See the
  [0.1.0 release notes](RELEASE-NOTES-0.1.0.md#known-limitations).

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
