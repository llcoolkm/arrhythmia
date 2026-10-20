<p align="center"><img src="docs/img/arrhythmia-logo.png" alt="Arrhythmia logo: an anatomical heart beside the word Arrhythmia under a red, irregular heartbeat line" width="600"></p>

# Arrhythmia

Arrhythmia is a patch for the firmware of the Novation Circuit Rhythm. It corrects bugs and adds new functions.

Current release: **0.1.1**. The [release notes](RELEASE-NOTES-0.1.1.md) give the changes and the test status. The
[0.1.0 release notes](RELEASE-NOTES-0.1.0.md) give the earlier record.

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

* **Chord synthesizer on track 8.** Each pad and each sequencer step plays 1 to 4 notes, with 4 voices
  ([synth](docs/user/synth.md)).
* **Attack and decay for the synthesizer (new in 0.1.1).** On track 8, macro 2 sets the attack and macro 3 sets the
  decay. Parameter locks operate on both ([attack and decay](docs/user/synth.md#attack-and-decay)).
* **MIDI polyphony on track 8.** MIDI notes on the channel of track 8 play on the synthesizer.
* **Scales on tracks 1 to 7.** The Note view can use one of ten scales ([scales](docs/user/scales.md)).
* **Modules.** Small programs on the microSD card can control the pads, lights, notes and MIDI. This function is for
  developers ([developers](docs/user/developers.md)).

## Limits

* In arrhythmia-firmware, track 8 plays the synthesizer only. It does not play samples.
* The synthesizer has one fixed saw sound with attack and decay. It has no sustain and no presets.
* Attack and decay apply to all sounding notes of track 8. A parameter lock also changes notes that are sounding.
* The synthesizer has a maximum of 4 voices.
* Arrhythmia is not for the Circuit Tracks or for other firmware versions of the Circuit Rhythm.

## Two images

| Image | For | Contents |
|---|---|---|
| **arrhythmia-firmware** (build 7261) | users | synthesizer with attack and decay, scales, bug fixes, modules |
| **arrhythmia-devkit** (build 7228, unchanged from 0.1.0) | developers | DSP load meter, module tools, scales, bug fixes. No synthesizer. |

If you are not sure, install **arrhythmia-firmware**.

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
* A very high load can cause audio errors.

## Licence

MIT ([LICENSE](LICENSE)). The licence applies only to the Arrhythmia code and documents. It does not apply to the
Novation firmware.

## Acknowledgements

[CIRCUITS-HACKS-RC](https://github.com/Ghillermo/CIRCUITS-HACKS-RC) by Ghillermo, a community modification for the
Circuit Tracks, gave useful information. Arrhythmia is a separate project.
