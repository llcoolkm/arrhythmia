# Arrhythmia 0.1.0 release notes

Arrhythmia is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

## Install in three steps

1. Download Novation's official firmware 2.0.1 (build 5706): open this link in your browser and save the file:
   <https://components.novationmusic.com/api/v2/firmwares/169/file>. Any file name works (the browser may call it
   `circuitrhythm-firmware-5706.syx`); the installer checks the content. That is the only download you need. Other
   ways (PowerShell, curl) are in
   [the install guide](https://github.com/llcoolkm/arrhythmia/blob/main/docs/user/install.md#before-you-install).
   The "Source code" archives on the release page are for developers; `STARS.MOD` is an optional light-show module.
2. Close Novation Components and any other music software or browser tab that uses MIDI. Do not start Components: it
   holds the unit's MIDI port and the installer then cannot reach the unit (on Windows a MIDI port has one user at a
   time).
3. Open <https://llcoolkm.github.io/arrhythmia/web/> in Chrome (Edge untested) and connect the unit by USB. Press
   "Connect MIDI", choose the file in "2. Choose the stock firmware", pick the image (arrhythmia-firmware for players),
   press "Install", and press Play on the unit when it blinks. The page checks the file's SHA-256
   (`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`) before anything is sent.

Before installing, read [Back up first](https://github.com/llcoolkm/arrhythmia/blob/main/docs/user/install.md#back-up-first) and [Going back to stock](https://github.com/llcoolkm/arrhythmia/blob/main/docs/user/install.md#going-back-to-stock).

**Close the installer tab before you open Components or any other MIDI software.** On Windows a MIDI port has one
user at a time; in our test Components hung until the installer tab was closed.

**Going back to stock:** use **Back to stock** on the installer page, or send the unmodified 5706 file with
`tools/flash.py`. Novation Components cannot return the unit to stock: it reports the unit as up to date, because
Arrhythmia answers the standard identity query with the stock version 5706 on purpose.

## Scales on the Note view

On a scaled track each row of pads is exactly one octave, from the key note to the key note above it:

* Every pad you play lights itself, and a note the sequencer plays lights its own pad.
* Only key notes sit on two pads (the end of one row and the start of the next), and both light together.
* 5- and 6-note scales leave dark, silent pads past the upper key note: two per row in the pentatonic scales, one in
  blues.
* Expanded view: at the default octave the upper rows run above the unit's highest note and stay dark. Press **Down**
  twice for four full rows (in key C, once is enough).

Details and examples: [docs/user/scales.md](docs/user/scales.md).

## Two builds

| Build | Number | SHA-256 of the patched `.syx` |
|---|---|---|
| arrhythmia-firmware | 7229 | `13d983b24c30bbdb3df0b4c2203d1016a8ab85bede4528751dad7d45b9145571` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

**arrhythmia-firmware (7229), for players:**

* Track 8 becomes a chord synth: 4 voices, one saw sound with a fixed attack and a decay of about 2 seconds, no
  sustain yet. Pads, the sequencer, MIDI on track 8's channel and modules play it.
* The synth page opens with **Shift + Track 8**: notes per press, chord type, voicing, octave, MIDI poly.
* Scales on tracks 1 to 7 (ten scales, shared key), set on the synth page.
* P-lock fix on all tracks: an unlocked step goes back to the knob's value.
* SD card module loader, with the built-in paint module.
* No DSP load meter.

**arrhythmia-devkit (7228), for developers:**

* DSP load meter and its read command.
* Module loader and SDK.
* Scales on tracks 1 to 7, set over SysEx only.
* P-lock fix.
* **No synth:** track 8 stays a stock sample track and there is no synth page.

If you are not sure, install arrhythmia-firmware. You can switch between the two with the installer at any time.

The version query (SysEx `70`) ends `00 38 3D` for 7229 and `00 38 3C` for 7228. The release file is
`release/v0.1.0.json`.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706 (SHA-256
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`), on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file, and the installer checks both versions on the unit.

## What was tested on hardware

On one unit, 2026-10-20, arrhythmia-firmware 7229 installed with `tools/flash.py` on Linux:

* The version reply shows 7229 and no DSP fault is reported.
* C major in the normal and the expanded view: every pad lights itself.
* A minor pentatonic: the two dark pads at the right of each row.
* A note held across a scale change stops when released.
* Scales off: the stock keyboard comes back.
* Save and power cycle: the settings are kept.
* Track 8's synth and unscaled tracks are unaffected.

All passed. arrhythmia-devkit 7228 was **not tested on hardware**.

The code outside the scales feature was tested on one unit, 2026-10-19, in development builds that differ from 7229 and 7228 only
in the scales code and the build number (the sound processor code is the same), installed with `tools/flash.py` on
Linux:

* Development firmware build: listening; a 60 second musical stress test; save plus 10 power cycles (settings kept); a 1 hour soak
  with a module in the second half: the unit stayed responsive with no faults reported, but the audio result is
  inconclusive (see known limitations).
* Development devkit build: the version reply and no DSP fault reported; SysEx 7D refuses reads of the bootloader and the stock
  firmware and allows module ITCM; the DSP load meter reads through command 08 (idle: core 0 about 41 %, core 1 about
  1 %). No listening or stress test on devkit.
* Windows with Chrome and a development version of the installer page that differs from this one only in the
  release it embeds: detecting the unit, back to stock 5706, and installing a development firmware build all passed. This release's page itself was not run
  against the unit.

Not tested:

* Listening or stress tests on arrhythmia-devkit.
* Edge (Chromium based, expected to work, but untested).
* macOS.
* Deliberate DSP fault containment (what the unit does when the sound processor is made to stop answering on
  purpose).
* Loading an Arrhythmia project on stock firmware is not confirmed on hardware.
* There is no restore tool for a backup of the unit's memory.

## Known limitations

* In the expanded view at the default octave the upper rows are mostly dark; press Down twice.
* Changing a scale while the sequencer plays can stretch the MIDI clock the unit sends by a few milliseconds. At
  130 BPM the longest clock interval measured during scale changes was 26.9 ms, against 24.0 ms without changes.
  Devices that follow the unit's clock may notice it.
* The 4 synth voices include notes that are still fading out, so dense playing can cut tails short.
* No synth sound controls: one fixed sound, no presets, no attack, decay or level. Planned for 0.2.
* The DSP voice source is not published yet, so the full arrhythmia-firmware image cannot be rebuilt from source yet.
  The ARM side and arrhythmia-devkit can.
* SysEx command 7D reads only module ITCM; it refuses reads of the bootloader and the stock firmware.
* Deliberate fault containment is untested (see above).
* The 1 hour soak audio result is inconclusive: no reference take on stock firmware and no close listening were done.
* After reading the devkit DSP meter, one flash attempt showed red pads and did not reach the Play confirmation. The
  tool aborted and the installed firmware was untouched; a power cycle and a retry worked. Power-cycle the unit
  before flashing.

## Licence

MIT (see [LICENSE](LICENSE)) for our code and documents. Novation's firmware is not included and not covered.

## Thanks

Thanks to Ghillermo's [CIRCUITS-HACKS-RC](https://github.com/Ghillermo/CIRCUITS-HACKS-RC), the community mod for the
Circuit Tracks that came first. Its published notes helped us, for example the rule that the bootloader installs only
an image whose version block differs. Arrhythmia is a separate project.
