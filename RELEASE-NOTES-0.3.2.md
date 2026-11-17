# Arrhythmia 0.3.2 release notes

Arrhythmia is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own
copy of the official firmware file on your own computer, then send the result to the unit. No Novation firmware is
included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.3.2 builds on 0.3.1. Engine files can now declare their own config page. Every engine gets the same three pages on
Shift + Track 8: its own page, the scales and the arpeggiator. The release adds new engine files besides FM.

## Changes since 0.3.1

### New

* **Engine config declarations.** An engine file can declare its config page in its header: header byte 27, config
  macro records for sources 9 to 11 with a default and a knob colour, and for the FM class the allowed voice counts.
  The contract is in [engines for developers](docs/user/engines-dev.md#engine-config-in-firmware-032).
* **Stable engine file format, minor 1.** 0.3.2 declares the engine file format stable at format minor 1 (header
  bytes 6 and 7). Every header byte has a defined meaning. The firmware reads minors 0 and 1 and refuses any other.
  A file with declarations is minor 1. A file without declarations can stay minor 0. `tools/cre3_check.py` applies
  the same rules, and `engine_file.py ui3` writes minor 1. The byte table and the choice of minor are in
  [engines for developers](docs/user/engines-dev.md#format-version).
* **One config page for every engine.** Shift + Track 8 opens the same pages for every engine: page 1 the engine
  configuration, page 2 the scales, page 3 the arpeggiator. Without an engine, page 1 is the scales and page 2 the
  arpeggiator ([synth page](docs/user/synth.md#the-synth-page)).
* **The engine page.** For an engine other than FM, knobs 1 to 4 of page 1 are the controls of the engine, knob 5 is
  dark, and knobs 6 to 8 are the macros that the engine declares. A knob that the engine does not declare is dark and
  has no function. There is no tune on the engine page: pitch comes from the pads, the scale and MIDI notes
  ([another engine](docs/user/synth.md#another-engine-on-the-synth-page)).
* **Engine pads in the Sample view.** The Sample view of track 8 shows the pads that the engine file declares. FM
  keeps its 32 sounds on two pages. Formant shows its voice and vowel pads on page 1 and 16 sounds on page 2. Rings
  shows its model and exciter pads on page 1. It has no sounds and no page 2
  ([engines](docs/user/engines.md)). A tap on a sound pad selects the sound and plays it, as with FM.
* **New engine files.** The release adds engine files besides `fm.dse`. Each has its own version
  ([engine files](#engine-files)).
* **Tools and loader.** `tools/cre3_check.py`, `tools/engine_file.py` and the loader accept the new declarations.
  `engine_file.py ui3` adds declarations to an existing engine file without a change to its DSP body.

### Changed behaviour

* **Scale controls on the scale page only.** Page 1 of the FM engine no longer has the minor or major pad. Set the
  scale on the scale page.
* **Scales and arpeggiator without an engine.** Without an engine on track 8, Shift + Track 8 now opens two pages:
  scales and the arpeggiator. Up and Down change the page.
* **Chords and poly are FM controls.** Another engine plays one voice per note, up to its own voice count.
* **Fixed: FM page read-only on older projects.** In 0.3.1 a project whose FM settings were saved by 0.2.x opened
  with the FM page refusing every knob edit (the Track 8 light blinked red). The first edit now resets those FM
  settings to their defaults and saves them in the current form.
* **Fixed: slow sequencer on the track 8 view.** In unpublished test builds of 0.3.2, showing track 8 with an engine
  that has its own pad page slowed the sequencer, by up to a third with a large engine, and the tempo jumped
  back when leaving the track. The page now repaints only when something on it changes.
* **Fixed: silence after a pack switch.** A pack switch from the panel could silence the whole unit, about once in
  sixteen loads, until a power cycle. This affects 0.3.1 and the unpublished test builds of 0.3.2. The firmware now
  sets its note ring index again after the DSP reload. The fix is in the shipped build and is tested offline. Its
  check on a unit is pending ([test status](#test-status)).
* **Fixed: knob turns on the engine pages.** A left turn of a page knob was read as a jump to the maximum. All
  page knobs now follow the encoder in both directions, and Clear held with the tune knob returns it to 64.

## Upgrade notes

* This release ships `fm.dse` 2.1.0. `fm.dse` 2.0.0 still loads on 0.3.2. Replace it with 2.1.0.
* **Install policy.** The loader and `tools/module.py` of 0.3.2 refuse to install any engine file on firmware below
  build 7400. This includes format minor 0 files such as `fm.dse` 2.0.0 and 2.1.0. Update the firmware first, then
  install the engine files.
* **Compatibility.** A format minor 1 file needs firmware 0.3.2. Firmware 0.3.1 refuses it at check 1, and the
  pack plays samples. The tools and the loader of 0.3.1 refuse it at check 1. A minor 0 file, such as `fm.dse`
  2.0.0 or 2.1.0, loads on firmware 0.3.1 and on 0.3.2.
* The `check3` and `cre3_check` tools of 0.3.2 refuse a file with declarations at format minor 0, as firmware 0.3.2
  does. `fm.dse` 2.1.0 has no declarations and is format minor 0. `formant.dse` and `rings.dse` are format minor 1.
* Pack selections (`engine.cfg`, `engines/default.cfg`) stay valid.
* Install the Euclid revision for build 7400, or Euclid Rec and Clear stay dark ([known limitations](#known-limitations)).

## Engine files

| File | Engine | Version | Voices | Size | SHA-256 |
|---|---|---|---|---|---|
| `fm.dse` | FM, class 1 | 2.1.0 | 4 | 15,329 bytes | `e5f074d5b69fc7a27d2b9340acda7dd975849220bbc367cdb2c2125d553da489` |
| `formant.dse` | formant, class 6 | 1.0.0 | 4, fixed | 13,037 bytes | `3d4c45bf4018309154d4d2fc72d80615b136c419ad2840c0635464f42017beab` |
| `rings.dse` | physical model, class 5 | 1.0.0 | 4, fixed | 14,979 bytes | `703e91205a2d056c24a0c683ac12f4a2f4f93d1bb9ba086634e7ba002baeb31f` |

`rings.dse` ports the resonators of Mutable Instruments Rings under the MIT licence. The notice is in [the engines guide](docs/user/engines.md#notice-for-the-rings-port).

* Engine class 2 is reserved.
* **Loudness.** The output of the formant and rings engines was raised by 6 dB during development. FM 2.1.0 plays 6 dB
  louder than 2.0.0 (measured on a unit: the same recording at twice the amplitude). The measured unit level of the
  released file is listed below.
* **Levels on a unit.** Measured on 2026-11-13 on an instrumented test build of the 0.3.2 line: track 8 mixer at
  100, one note per press, MIDI 60 at velocity 100 unless stated, RMS over 0.05 to 0.60 s after the note, each engine
  at its defaults. The measured files had the same DSP bodies as the FM and formant files of this release, under
  earlier candidate headers. Rings was measured at its previous output ceiling. The
  shipped files, with their release headers, have not yet been loaded on a unit.

| Engine and scene | RMS | Peak |
|---|---|---|
| FM 2.1.0, one note | -31.4 dBFS | -23.1 dBFS |
| FM 2.1.0, four-note chord at velocity 127 | -23.3 dBFS | -10.9 dBFS |
| formant 1.0.0, one note | -26.6 dBFS | -13.2 dBFS |
| rings, at its previous output ceiling, modal, auto exciter, one note | -30.2 dBFS | -13.1 dBFS |

* The `rings.dse` of this release is not the file measured. Its output ceiling is 4 dB lower. It was not yet measured
  on a unit.

## What an engine author gets

The page [engines for developers](docs/user/engines-dev.md) is the contract. 0.3.2 adds:

| Item | Value |
|---|---|
| format minor (header bytes 6 and 7) | 0 legacy, without declarations; 1 the stable format; 2 and above refused |
| header byte 27 | 0 legacy; 0x80 explicit declarations; 0x81 explicit with FM chords, FM class 1 only; 0x80 and 0x81 need minor 1 |
| config macros | sources 9 to 11, one kind 4 record on surface 0 each: default 0 to 127, knob hue 0 to 7 |
| polyphony control | FM class 1 only: allowed counts 1 to 4 and a default, in bytes 5 and 6 of the kind 6 record |
| engine page | page 1 of the config page for other classes: surface 2 pads, knobs 1 to 4, knob 5 dark, config macros on knobs 6 to 8 |

* The settings key does not change when you add declarations. Stored values survive.
* A declaration is metadata. Test the engine on a unit with firmware 0.3.2.
* The platform 1 facts of 0.3.1 stay: memory grants, stack depth, cycle budget E of 65,626 cycles and the USB limit of
  15,360 bytes.

## Known limitations

* **Euclid Rec and Clear on build 7400.** The Euclid module enables writing to patterns only on the builds that its
  revision lists. Revision 9 lists 7358 and 7388; on 7400 it plays but Rec and Clear stay dark. Revision 10 adds 7400
  (11,848 bytes, SHA-256 `b214a506d76f4598407290875883f403a680cc29b7ff613f2e6de113294d6c7f`), with no other change. Its
  host-table pins were read from the 7400 image and its module tests pass against that image. Revision 10 was written
  to a unit and read back. Rec and Clear with revision 10 on a unit running 7400 are not yet tested.
* **Surface 2 records of an FM class file.** Firmware 0.3.2 and the checker accept surface 2 records in a class 1
  file: check 17 does not test the class. They have no page. Page 1 of an FM engine is the FM page, and the engine
  page with the surface 2 knobs and pads exists only for the other classes. No panel control edits such a record.
* **An engine runs with full access to the sound processor.** A faulty engine can stop the DSP. Rec then blinks
  white and only a power cycle recovers the unit ([Rec blinks white](docs/user/install.md#rec-blinks-white)). Before the
  power cycle, set the pack to Samples with the loader, or the same engine loads again at power-on.
* **Tune.** Tune is not a control source. Clear held with the tune knob returns it to 64.
* **No append over USB.** A file above 15,360 bytes installs only with a card reader.
* **Modules.** A module that writes a DSP peripheral register voids the platform facts of the engine page. The
  firmware does not detect it.
* **Formant attack on some sounds.** Macro 2 (attack) moves the attack from the sound's own value. On a sound with a
  very short attack, such as Vowel Pluck, knob values 0 to 54 give the same, shortest attack. A
  report that only attack 127 changes the sound was not reproduced offline and was not checked again on a unit.
* **Faint flicker on dark pads.** On the engine pages, dark pads can flicker faintly while you turn a knob. Lit pads
  do not flicker.
* All known limitations of [0.3.1](RELEASE-NOTES-0.3.1.md#known-limitations) and earlier that 0.3.2 does not change
  stay.

## Test status

The firmware build of this release has not yet run on a unit. The unit records below ran on earlier builds of the
0.3.2 line.

**Release candidate on a unit.** The record ran on 2026-11-13 on an instrumented test build of the 0.3.2 line, with
FM 2.1.0, formant 1.0.0 and rings at its previous output ceiling:

* The tempo counters of the track 8 view stayed within 8 percent of track 1 on every engine. The view did not repaint
  at idle.
* The unit stayed stable through 10 seconds at idle. The page repainted on a press, a page switch and a track switch.
* The choice pages played a preview.
* The config pages opened for every engine (engine, scales, arpeggiator) and without an engine (scales,
  arpeggiator).
* Encoder edits applied, with zero refusals.
* Health was clean in 57 status reads: no overrun, no stack mismatch, no fault.

**Production image of the 0.3.2 line on a unit, before the pack switch fix.**

* The USB writer refused a file with a bad body CRC, a format minor 2 header and a minor 0 header with declarations.
  Each refusal came before the commit and left nothing on the card.
* A good file was accepted and verified.
* Euclid revision 10 was written and read back.
* The formant engine played.

**A defect found after these runs.** A pack switch from the panel could silence the whole unit, about once in sixteen
loads. The firmware did not set its note ring index again after the DSP reload. The shipped build has the fix and a
test for it. The fix is verified offline for all 16 index values and 17 repeated switches. Its check on a unit is
pending.

**Offline record.**

* The release run passed every step: source, production build, image, 13 pinned assets and the release tests.
  All 19 test sets ran and passed. Two of them skipped individual checks as expected, 7 and 1: the historical
  installer rebuild checks, and one check that needs a private engine directory.
* The release JSON reproduces firmware 7400 byte for byte from the official input. The public rebuild passes
  ([reproduce 0.3.2](docs/README.md#reproduce-032-firmware-7400)).
* The links of the documents resolve.

**Not yet verified on a unit.**

* The shipped firmware build. It has not yet run on a unit.
* The shipped engine files with their release headers. None has been loaded on a unit. The unit runs used the same
  FM and formant DSP bodies under earlier candidate headers.
* The pack switch fix of the shipped build.
* The shipped `rings.dse`, with the 4 dB lower output ceiling. It was written to a card but has not yet played on a
  unit.
* Euclid Rec and Clear with revision 10 on build 7400.
* The formant attack report at 127.
* The Sample view of each engine, and dropouts and clipped samples, have no separate entry in the unit record.

## Install

Use the installer page, as for 0.3.1: [install guide](docs/user/install.md).

1. Install arrhythmia-firmware with the installer page. The page shows build 7400.
2. Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html).
3. Under **Engines**, select **Scan card**.
4. Replace `fm.dse` 2.0.0 with 2.1.0 of this release. Add the other engine files if you want them.
5. Under **Modules**, update `10euclid.mod` to the revision for build 7400.
6. Save project edits on the unit.
7. Load a pack that selects an engine in the stock Packs view.

| Build | Number | Version query (SysEx `70`) ends | Bootloader mode shows |
|---|---|---|---|
| arrhythmia-firmware | 7400 | `00 39 68` | `74 00` |
| arrhythmia-devkit | 7228 | `00 38 3C` | `72 28` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is not re-released; the 0.1.0 asset stays valid. The release page lists the
SHA-256 of each patched `.syx`: arrhythmia-firmware `3f7b4fa2e59f5e3057f2792d7b4b5e4d5a30ddd6a029ed7f37d1441b28b4a021`, arrhythmia-devkit
`66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d`. The release file is `release/v0.3.2.json`. The
files of the earlier releases stay in the repository as their record.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706, on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The published ARM sources are in `patches/src/` and the host tools in `tools/`. The release patch reproduces build 7400
from the official input with the [published patch command](docs/README.md#reproduce-032-firmware-7400).

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
