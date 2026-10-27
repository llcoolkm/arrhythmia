<p align="center"><img src="docs/img/arrhythmia-logo.png" alt="Arrhythmia FM logo: an anatomical heart beside the word Arrhythmia under a red, irregular heartbeat line, over a pink sunset, a neon grid and palm trees, with chrome letters FM" width="700"></p>

# Arrhythmia

Arrhythmia is a patch for the firmware of the Novation Circuit Rhythm. It corrects bugs and adds new functions.

Current release: **0.2**. The [release notes](RELEASE-NOTES-0.2.0.md) give the changes and the test status.
The [0.1.1](RELEASE-NOTES-0.1.1.md) and [0.1.0](RELEASE-NOTES-0.1.0.md) release notes give the earlier record.

## Principles

* **Bug fixes first.** The main objective is to correct bugs in the stock firmware 2.0.1.
* **Patch only.** Arrhythmia does not contain the Novation firmware. You download the official firmware file from
  Novation. The patcher reads that file and adds the changes on your computer.
* **Interoperability.** The patch lets the unit operate with corrected and new software. Your own programs (modules)
  can also operate with the unit.
* **Standard update path.** The installation uses the SysEx update protocol of the unit. You must confirm each
  installation on the unit.
* **Reversible.** You can install the stock firmware again through the same path.

Arrhythmia is not affiliated with Novation or Focusrite. Novation and Focusrite do not endorse Arrhythmia.

## Install

Before you start, make a backup of the microSD card ([procedure](docs/user/install.md#back-up-first)).

1. Download the Novation firmware 2.0.1: <https://components.novationmusic.com/api/v2/firmwares/169/file>.
2. Close Novation Components and all other music applications.
3. Connect the unit to the computer with a USB cable.
4. Open <https://llcoolkm.github.io/arrhythmia/web/> in Chrome.
5. Click **Connect MIDI**.
6. Select the downloaded file.
7. Click **Install**.
8. When **Play** flashes on the unit, push **Play**.

For problems, other download procedures and the return to stock, refer to the
[install guide](docs/user/install.md).

## Bug fixes

* **Parameter locks.** On the stock firmware, a parameter lock stays active on the next steps until you turn the
  knob. With Arrhythmia, a step without a lock uses the value of the knob.
* **Sound processor fault.** On the stock firmware, the unit can stop when the sound processor does not answer.
  Arrhythmia limits the time that the unit waits for an answer. After a timeout, **Rec** flashes white
  ([procedure](docs/user/install.md#rec-blinks-white)). This function is tested in emulation only.

## New functions

The synthesizer functions are in arrhythmia-firmware only.

* **FM synthesizer on track 8 (new in 0.2).** A 4-operator FM synthesizer with 4 voices replaces the saw
  synthesizer of 0.1.x ([synth](docs/user/synth.md)).
* **Synth recovery after sample loading (new in 0.2).** After a pack change or a sample load, the synth starts
  again by itself. If a restart fails, the synth stays silent until you turn the unit off and on
  ([loading samples](docs/user/synth.md#loading-samples-while-the-unit-runs)).
* **Sounds chosen in the Sample view (new in 0.2).** In track 8's Sample view, the 16 pads of rows 3 and 4 select
  one of 16 ratio families. The first page uses the serial algorithm, the second page the dual stack algorithm. A tap
  selects the sound and plays it. Sample flips on steps choose the sound per step
  ([choosing a sound](docs/user/synth.md#choosing-a-sound-the-sample-view)).
* **Full envelope and tone controls (new in 0.2).** On track 8, macros 1 to 8 set tune, attack, decay, sustain,
  release, FM depth, filter cutoff and resonance. MIDI CC 21 to 28 and parameter locks operate on all eight
  ([macros](docs/user/synth.md#macros-on-track-8)).
* **FM feedback and modulator envelope (new in 0.2).** On the synth page, macros 1 to 4 set feedback, modulator
  attack, modulator decay and modulator floor.
* **Chords on track 8.** Each pad and each sequencer step plays 1 to 4 notes. With 1 note per press and poly on,
  held pads sound together (new in 0.2).
* **MIDI polyphony on track 8.** MIDI notes on the channel of track 8 play on the synthesizer.
* **Scales on tracks 1 to 7.** The Note view can use one of ten scales ([scales](docs/user/scales.md)).
* **Modules.** Small programs on the microSD card can control the pads, lights, notes and MIDI. This function is for
  developers ([developers](docs/user/developers.md)).

## Limits

* In arrhythmia-firmware, track 8 plays the synthesizer only. It does not play samples.
* The synthesizer has 32 sounds: 16 ratio families under 2 algorithms. It has no presets. The other six pages of
  track 8's Sample view are dark.
* At high pitch the ratio families fade to a plain tone and sound alike
  ([known issues](docs/user/synth.md#known-issues)).
* Sample flips stored on track 8 in 0.1.x projects and factory patterns now change the sound on those steps.
* The synthesizer has a maximum of 4 voices, including notes in their release.
* While samples load into the unit (a pack change, or a sample transfer or load including SysEx read-back), the
  synth stops and its sounding notes are cut. It starts again by itself after the load
  ([loading samples](docs/user/synth.md#loading-samples-while-the-unit-runs)).
* The processing time of the synth has not been measured on the unit. Under very heavy use the audio can glitch or
  drop out ([known issues](RELEASE-NOTES-0.2.0.md#processing-time-is-not-measured-on-the-unit)).
* The synth's output is lowered by a fixed amount. Set the balance with track 8's mixer level ([output level](docs/user/synth.md#output-level)).
* Arrhythmia is not for the Circuit Tracks or for other firmware versions of the Circuit Rhythm.

## Two images

| Image | For | Contents |
|---|---|---|
| **arrhythmia-firmware** (build 7323) | users | FM synthesizer, scales, bug fixes, modules |
| **arrhythmia-devkit** (build 7228, unchanged from 0.1.0) | developers | DSP load meter, module tools, scales, bug fixes. No synthesizer. |

If you are not sure, install **arrhythmia-firmware**.

The [source rebuild guide](docs/README.md#rebuilding-the-images) covers the current 0.2 image and the
[0.1.1 image 7261](docs/README.md#rebuild-the-011-firmware-image-7261).

## Requirements

* A Circuit Rhythm with firmware 2.0.1 (build 5706) and bootloader 4799.
* The official firmware file 5706 from Novation.
* Chrome on Windows or Linux, or Python 3.12 on Linux. macOS is not tested.
* A USB cable and a microSD card reader.

## Return to stock

Use **Back to stock** on the installer page, or `tools/flash.py` on Linux
([procedure](docs/user/install.md#going-back-to-stock)). Novation Components cannot do this.

## Risks

* You install modified firmware at your own risk.
* Make a backup before the installation.
* A very high load can cause audio errors. The processing time of the synth is not measured on the unit.

## Licence

MIT ([LICENSE](LICENSE)). The licence applies only to the Arrhythmia code and documents. It does not apply to the
Novation firmware.

## Acknowledgements

[CIRCUITS-HACKS-RC](https://github.com/Ghillermo/CIRCUITS-HACKS-RC) by Ghillermo, a community modification for the
Circuit Tracks, gave useful information. Arrhythmia is a separate project.
