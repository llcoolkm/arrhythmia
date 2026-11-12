# Circus Arrhythmia FM 0.3.1 release notes

Circus Arrhythmia FM is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own
copy of the official firmware file on your own computer, then send the result to the unit. No Novation firmware is
included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.3.1 builds on 0.3.0. It adds engine ABI 3, a written and stable interface for track 8 engines, and the FM engine
2.0.0, which is built for it. Engines now ship on their own, as files that you install over USB. Any engine file that
passes the checks of the firmware loads.

## Changes since 0.3.0

### New

* **Engine ABI 3.** The interface between the firmware and a track 8 engine is now a written contract:
  [engines for developers](docs/user/engines-dev.md). It covers the engine file, the checks before a load, the memory,
  the command queue, the control sources, the engine controls on the unit and the health rules. Its stability promise
  starts with this release ([versioning](docs/user/engines-dev.md#versioning-and-compatibility)).
* **General engine admission.** The firmware keeps no list of approved engines. It loads every engine file that passes
  all its checks ([checks](docs/user/engines-dev.md#checks)). A file that fails a check gives the samples of the pack,
  and `card engstatus` shows the reason.
* **Engines ship separately.** An engine is a file with its own version and its own release cadence. Install it over
  USB with the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html). A file of at most 15,360 bytes installs
  over USB. A larger file needs a card reader.
* **FM engine 2.0.0.** `fm.dse` 2.0.0 is the FM synth of 0.3.0, ported to engine ABI 3. It keeps the file name, so a
  pack that selects `fm.dse` needs no change. It is a 4-operator FM synth with 4 voices and the same 32 sounds. The
  file is 15,346 bytes and installs over USB. In the reference scenes in the emulator, its output is word for word the
  output of FM 1.0.0.
* **Engine-defined controls.** An engine declares its own controls: sound slots and choice and toggle pads on the
  Sample view of track 8, and pads and knobs on synth page 4. The firmware draws them with one fixed rule and saves
  their values with the project ([engine controls](docs/user/engines-dev.md#engine-controls-on-the-unit)).
* **Cycle meter.** The firmware measures the time that the engine takes in every block. An engine declares its worst
  block. The firmware refuses an engine that declares more than the platform budget, and counts every block in which
  the engine runs over its declaration. `card engstatus` shows the largest block since the load.
* **Measured platform budget.** Platform 1 gives an engine 65,626 DSP cycles per block. This is the largest engine
  block measured on a unit with clean audio and clean health while a heavy stock scene plays all eight tracks with
  reverb on. It is a measurement, not a calculation. The FM engine 2.0.0 declares 65,626 cycles. Its largest measured
  block was 64,940 cycles.

### Changed behaviour

* **Migration from 0.3.0: replace `fm.dse`.** Firmware 0.3.1 refuses `fm.dse` 1.0.0. Until you install `fm.dse` 2.0.0,
  every pack that selects the FM engine plays its samples. Pack selections (`engine.cfg`, `engines/default.cfg`) stay
  valid.
* **Older engine files are refused.** Engine files for the interface of 0.3.0 do not load on 0.3.1. Their authors
  must rebuild them for engine ABI 3.
* **ALL OFF keeps the values of the controls.** In FM 2.0.0, all-sound-off stops every note but keeps the macro and
  control values. A new epoch of the queue still resets the voices and values.

## What an engine author gets

The page [engines for developers](docs/user/engines-dev.md) is the contract. In short, platform 1 grants:

| Item | Value |
|---|---|
| program memory for engine code and tables | up to 7,808 words |
| data memory | windows 0, 1 and 2: 6,288 words |
| stack depth below the entry | 11 locations |
| cycle budget E per block | 65,626 cycles, measured on a unit |
| fixed shim overhead F inside every span | 64 to 66 cycles |
| voices (lanes) | 1 to 4 in format minor 0 |
| engine controls | up to 16 control records |
| largest file over USB | 15,360 bytes |

* Declare the cycles of your worst block (header +52). Measure them on a unit: declare E for a first trial, run your
  worst case, read the largest span in `card engstatus`, then declare a figure from that maximum up to E. A declaration
  of 0, or above E, is refused.
* Obey the instruction sequence restrictions of Appendix A of the DSP56300 Family Manual. The emulator does not detect
  them; the unit does. Check your listing from the source tree with
  `python3 -I tools/dsp/abi3/sequence_warning.py path/to/assembled.lst`
  ([restricted instruction sequences](docs/user/engines-dev.md#restricted-instruction-sequences)).
* **Stack mismatch.** The 0.3.1 platform qualification exercised the mismatch repair path of the shim, including the
  direct stack counter rebuild, on a unit with a test engine. After a mismatch the engine stays loaded until the next
  engine load or restart: the shim keeps calling it and repairing the stack every block, and the firmware does not load
  it again this session. Keep the stack balanced; never depend on that path.
* The budget E and the grants are frozen for platform 1. A firmware that changes them uses a new platform id.
* The firmware cannot stop or contain a running engine. It detects what it can and gives a verdict. Run the
  conformance runner before you ship an engine.

## Known limitations

* **An engine runs with full access to the sound processor.** A faulty engine can stop the DSP. Rec then blinks white
  and only a power cycle recovers the unit ([Rec blinks white](docs/user/install.md#rec-blinks-white)). Before the
  power cycle, set the pack to Samples with the loader, or the same engine loads again at power-on.
* **Restricted instruction sequences.** An engine that breaks a sequence restriction of Appendix A can corrupt the
  stack on the unit although it passes in the emulator. The firmware counts the stack mismatch and fails the engine,
  or the DSP stops.
* **Kind 5 defaults for source 0.** Seven declared defaults of main macro 1 reach the engine changed by one: 9 as 10,
  27 as 28, 45 as 46, 63 as 64, 82 as 81, 100 as 99 and 118 as 117. The tools warn.
* **Tune.** Tune is not a control source. Clear held with synth page 1 macro 1 does nothing while an engine owns
  track 8.
* **No append over USB.** A file above 15,360 bytes installs only with a card reader.
* **Euclid Rec and Clear need module revision 9 (2026-11-12).** The Euclid module of 0.3.0 enables writing to
  patterns only on firmware build 7358. On 7388 it plays but Rec and Clear stay dark. `10euclid.mod` revision 9
  (11,776 bytes, SHA-256 `ae2722375255964b5e1264e0cc70736e562c3a2e63bf8110971169968728e3b6`) on the release page and
  in the loader enables them on 7358 and 7388. Its writing path passed the offline module tests for both builds; on a
  unit it was loaded and started but writing was not exercised yet.
* **Modules.** A module that writes a DSP peripheral register voids the platform facts of the engine page. The
  firmware does not detect it.
* All known limitations of [0.3.0](RELEASE-NOTES-0.3.0.md#known-limitations) and earlier that 0.3.1 does not change
  stay.

## Test status

The release candidate ran all 27 stages on a Circuit Rhythm. The record installed the production test twin,
provisioned FM 2.0.0 over USB, reloaded it from the card, and played three four-voice lifecycle scenes with the
heavy stock reference. The FM scenes had zero meter overruns and stack mismatches, zero detected audio dropouts
and no clipped samples. The shipped FM candidate's maximum in the earlier acceptance was 64,940 cycles.
The platform ceiling, 65,626 cycles, comes from the largest normal maximum in a successful four-voice FM run.

A deliberately unready engine exercised stock fallback and refusal on the following warm boot. A deliberately
unbalanced engine exercised repeated stack repair while the ARM remained responsive. Its mismatch count was 27,050.
The record then selected Samples, reloaded, checked stock audibility, restored FM, checked module upload/start/close,
and installed and checked the ungated production image. Audio was captured before and after the mismatch-engine
test. Silence of its output half and the mixer call during the mismatch interval were checked in emulation and
against the source.

The offline record runs the emitted ARM parser, SD streaming, ready and health paths, status serializer and SDK
upload/start/close. Its storage, sound-processor interface, boot and audio boundaries are modelled. It also exercises
USB device return with delayed permissions. The release checks include the CLI and browser engine installer.

The published firmware uses the stock Play confirmation. The test twin differs only in its installation gate. The FM engine 2.0.0 ran on
a unit in three scenes with no overrun, fault, stack mismatch or dropout; its largest normal block was 64,940 cycles.

## Install

Use the installer page, as for 0.3.0, then replace the FM engine: [install guide](docs/user/install.md).

1. Install arrhythmia-firmware with the installer page. The page shows build 7388.
2. Download `fm.dse` 2.0.0 from the release page.
3. Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html).
4. Under **Engines**, select **Scan card**.
5. Add `fm.dse` 2.0.0. It replaces `engines/fm.dse` 1.0.0.
6. Save project edits on the unit.
7. Load a pack that selects the FM engine in the stock Packs view.

| Build | Number | Version query (SysEx `70`) ends | Bootloader mode shows |
|---|---|---|---|
| arrhythmia-firmware | 7388 | `00 39 5C` | `7388` |
| arrhythmia-devkit | 7228 | `00 38 3C` | `72 28` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is released again. The release page lists the SHA-256 of each
patched `.syx`: arrhythmia-firmware `31b549966b3facc0a1f86e8b7f2c63d90fb2e218027aa664cf78990d85d1192f`, arrhythmia-devkit
`66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d`. `fm.dse` 2.0.0 is 15,346 bytes, SHA-256
`9b79dfe90c4208a8208ffc04f5d5aa80bc3787584524d17a7ec3deaf5629ddae`. It declares 65,626 cycles. The release file is
`release/v0.3.1.json`. The files of the earlier releases stay in the repository as their record.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706, on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The published ARM sources are in `patches/src/` and the host tools in `tools/`. The release patch reproduces build 7388 from the official input with the [published patch command](docs/README.md#reproduce-031-firmware-7388).

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
