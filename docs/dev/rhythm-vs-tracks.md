# Differences from Circuit Tracks

For developers who know the Circuit Tracks and its community mod, Ghillermo's
[CIRCUITS-HACKS-RC](https://github.com/Ghillermo/CIRCUITS-HACKS-RC), the mod for the Circuit Tracks that came first.
Its published notes helped us, for example the rule that the bootloader installs only an image whose version block
differs. Arrhythmia is a separate project. Read their documents for their figures; this page only says what we found
on the Circuit Rhythm. Marks as in [README.md](README.md).

## The products

How we know: the products' documented features.

| | Circuit Rhythm | Circuit Tracks |
|---|---|---|
| Tracks | 8 sample tracks | 2 synth tracks, 4 drum tracks, 2 MIDI tracks |
| Sound sources | samples, with slicing and sample recording from the audio input | synth engines plus drum samples |
| Firmware versions referred to | 2.0.1, build 5706 | 1.2.1, build 4486 |

## What differs for a developer

How we know: the products' features, and measurement on the Rhythm.

* **Same update file format, different device byte:** the INIT device byte is `63` for the Rhythm and `64` for the
  Tracks ([flashing.md](flashing.md), STATIC on the published files).
* **The panel is laid out differently:** the Rhythm's buttons have their own labels and meanings ([maps.md](maps.md),
  HW).
* **The Rhythm's USB disk is read-only** and shows the welcome drive and the content volume
  ([hardware.md](hardware.md), HW).
* **The Rhythm has no synth engines.** Arrhythmia's track 8 synth is our own DSP code, not a port of the Tracks'.
* **DSP room and load had to be found again on the Rhythm.** Arrhythmia's own core 1 DSP code lives in the 718-word
  program tail that stock 5706 leaves unloaded ([dsp-meter-and-budget.md](dsp-meter-and-budget.md)). On a devkit test build, core 0 sat near 41 % of a
  block at idle and reached 55.1 % in the heaviest project we played; core 1 1.2 % idle, 25.8 % worst (HW, build
  6011).

## Which findings carried over

| Their finding (Circuit Tracks) | On the Rhythm | Mark |
|---|---|---|
| The bootloader installs only an image whose build-info block differs from the installed one | holds; the bootloader never compares versions | HW (a skipped install observed) |
| Application size cap of the bootloader | the same cap, 448 KB | STATIC |
| The heap never frees | holds | STATIC; the cursor stays put after boot (HW) |
| ITCM (16 KB) is free | no stock 5706 writes observed in the tested sessions; Arrhythmia runs modules there | HW ([hardware.md](hardware.md)) |
| Backup SRAM as spare RAM | **does not carry over** | STATIC |
| DSP figures | **do not carry over**: every figure had to be found again on the Rhythm; external-memory bandwidth on the Rhythm is still unknown | see [dsp-meter-and-budget.md](dsp-meter-and-budget.md) |
| Reserved bytes in the saved project | the Rhythm's project file has its own spare tail, used by Arrhythmia | EMU, HW ([design/project-extension-block.md](design/project-extension-block.md)) |
