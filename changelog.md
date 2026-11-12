# Changelog

## 0.3.1

Module update 2026-11-12: `10euclid.mod` revision 9 enables Euclid Rec and Clear on firmware build 7388 (they
worked on 7358 only). Install it with the loader or by copying it to the card.

Changes since 0.3.0. Full installation details, test status and limitations are in the
[release notes](RELEASE-NOTES-0.3.1.md).

### Added

* Engine ABI 3: a written, stable interface for track 8 engines ([engines for developers](docs/user/engines-dev.md)).
* General engine admission: every engine file that passes the checks loads; no list of approved engines.
* Engine-defined controls on the Sample view of track 8 and on synth page 4, saved per project.
* Cycle meter: refusal of a declaration of 0 or above the platform budget, overrun counts, and the largest block
  in `card engstatus`.
* Measured platform budget E of 65,626 DSP cycles per block for platform 1.
* Author rule: the instruction sequence restrictions of Appendix A of the DSP56300 Family Manual, with a listing check.
* FM engine `fm.dse` 2.0.0 for engine ABI 3, 15,346 bytes; it installs over USB. Its output equals FM 1.0.0 in the
  reference scenes in the emulator.

### Changed

* `fm.dse` 1.0.0 and other engine files for 0.3.0 are refused. Install `fm.dse` 2.0.0.
* FM ALL OFF keeps the control values.

## 0.3.0

Changes since 0.2.2. Full installation details, test status and limitations are in the
[release notes](RELEASE-NOTES-0.3.0.md).

### Added

* Optional track 8 engines from the microSD card. `engines/fm.dse` is the FM engine, version 1.0.0,
  independent of firmware 0.3.0. Per-pack `engine.cfg` selects an engine or samples; `engines/default.cfg`
  supplies the selection for packs without their own file and for the factory pack.
* FM feedback reaches 8x and changes sounding notes immediately with this release's engine.
* Track 8 arpeggiator on synth page 3: on/off, five modes, latch, six rates, one to three octaves and four gates.
  Settings are saved per project. It accepts pad, MIDI, module and sequencer notes and sends generated notes
  to MIDI out; it also runs at project tempo with transport stopped.
* Scale toggle for track 8 on synth page 2, row 1 pad 8, with either an engine or samples.
* Combined engine and module loader over USB. It lists actual pack folder names, saved selections, engine names,
  versions and current engine state. It installs, updates and removes engines and saves pack/default selections.
* Staged module assignments, custom module files, colour preview, reset to default modules, and a review of
  writes/deletions before applying them. Known published assignments can be upgraded, including 0.2.2 Euclid.
* Expanded Euclid: four rows up to 32 steps, accent and chance, paged four-row overview and one-row detail,
  side-button row toggles, up to 24 supported note choices, keyboard feedback and 16-pad velocity/accent bars.
* Euclid explicit Rec writes and Clear undo, target-pattern suppression including off rows, duplicate-target
  refusals, operation lights and brief completed-result feedback. Unknown outcomes stay indicated without
  automatic retry; Clear without an undo is inactive. Keep stock Record off before opening Euclid for writing:
  Clear undoes only the last explicit Rec write, not earlier live recording.
* Euclid retains settings in RAM across closes and imports representable target patterns at first start after
  power on or project load. It always opens in overview. See the [Euclid guide](docs/user/euclid.md).

### Changed

* Euclid velocity and accent edits use 16 positive levels from 7 to 127, with accent off below them.
  Each row retains its keyboard display window while its pitch remains visible. An all-rows-off Rec refusal
  pulses all four row-toggle side buttons red. See the [Euclid guide](docs/user/euclid.md#the-macro-knobs)
  for imported values and first-edit behaviour.

* FM is a separate card file. Updating firmware alone leaves track 8 playing samples; existing synth projects
  need the engine installed and selected. Invalid or missing engine selections use samples, without falling
  back to another engine. Project changes within a pack keep its engine; pack loads apply selections.
* New Arrhythmia card paths and selection text use lowercase. Existing uppercase FAT names remain compatible;
  stock pack folders keep `Rhythm/NN_<Name>`.
* With FM selected, track 8's Sample view has two pages for the existing 16 families under each of two algorithms.
  It opens on the selected sound's page. Sample tracks keep eight pages.
* Synth page navigation reaches the new arp page. Page 1 row 1 pad 8 is inactive; synth-page macros 1 to 4 and
  their Clear defaults work on page 1 only. Without an engine, Shift + Track 8 opens only the scale page.
* The release still has ten module assets, with updated default names and pads: Life 1, Rain 2, Balls 3, Stars 4,
  Lights Out 5, Paint 9, Euclid 10, Pulse 11, Snake 17 and Whack-a-mole 18.
* The old module web page forwards to the combined loader. Firmware installation remains separate. The loader
  saves engine pointers; load packs on the unit in the stock Packs view. Save unsaved project edits first.
* Engine Scan card and Check status only read. Module List and Verify can recover interrupted-write helpers and
  change the card. Unknown files stay; conflicts and failed or unknown jobs stop the module plan without retry.
* arrhythmia-firmware is build 7358. arrhythmia-devkit 7228 is unchanged from 0.1.0.

Hardware qualification is still in progress. Physical FM voice count, stealing order and DSP headroom are
unmeasured. Earlier limitations remain: see [known limitations](RELEASE-NOTES-0.3.0.md#known-limitations).

## Earlier releases

The earlier release notes retain their original history and test scope:

* [0.2.2](RELEASE-NOTES-0.2.2.md)
* [0.2.1](RELEASE-NOTES-0.2.1.md)
* [0.2.0](RELEASE-NOTES-0.2.0.md)
* [0.1.1](RELEASE-NOTES-0.1.1.md)
* [0.1.0](RELEASE-NOTES-0.1.0.md)
