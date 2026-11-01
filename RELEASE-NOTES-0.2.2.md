# Circus Arrhythmia FM 0.2.2 release notes

Circus Arrhythmia FM is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.2.2 builds on 0.2.1. The changes are in the synth page, the output level of the synth and the modules.

## Changes since 0.2.1

### New

* **Synth page as a view.** Shift + Track 8 opens the synth page. It has two pages
  ([synth page](docs/user/synth.md#the-synth-page)):
  * Page 1, chords: notes per press, chord type, In key, minor or major, voicing, poly, octave.
  * Page 2, scale: row 1 turns scales on or off for track 1 to 7, row 2 selects the scale, rows 3 and 4 set the key.
  * **Down** shows page 2. **Up** shows page 1. The page opens on the last page you used.
  * A view or track button (for example Note, Sample, Mixer, Track 1 to 8) closes the page. The unit goes to that
    view or track. The button press is not lost. Play and Rec operate while the page is open.
  * **Clear + macro** sets that macro of the synth page to its default: tune 64, modulator attack 0, modulator
    decay 64, modulator floor 127.
  * **Lights.** The exit buttons and Shift keep their normal lights. Track 8 is magenta. Up or Down is green when
    there is a page in that direction. Clear keeps its normal light (dim red when that light is off) and is red
    while you hold it. While you hold Shift in the normal views, Track 8 is lit.
* **Module files over USB.** The firmware can write module files to the folder `Modules` on the microSD card. The
  module files web page `web/modules.html` uses this function. It lists the files on the card in the order of the
  picker, with the pad of each file. It writes, verifies, replaces and deletes files. You do not need a card reader
  ([add modules with the web page](docs/user/modules.md#add-modules-with-the-web-page)).
* **Published modules on the website.** The 10 module files of the release are on the website. The module files
  web page writes them to the card with one button each. They keep their numbers, so each goes on its pad.
* **New name.** The project is now Circus Arrhythmia FM, with a new logo. The build names stay arrhythmia-firmware
  and arrhythmia-devkit.

### Changed behaviour

* **Synth output 4 dB down.** The fixed decrease of the synth output is now 4 dB. It was 7 dB. The synth is
  approx. 3 dB louder than in 0.2.1. Set the balance with track 8's mixer level
  ([output level](docs/user/synth.md#output-level)).
* **Eight scales.** arrhythmia-firmware has eight scales. It has no Phrygian and no Lydian. A project that has
  Phrygian plays and shows natural minor. A project that has Lydian plays and shows major. The project keeps its
  stored value. The SysEx scale command refuses Phrygian and Lydian on arrhythmia-firmware
  ([scales](docs/user/scales.md)). arrhythmia-devkit keeps all ten scales.
* **Scales on page 2.** Scale, key and the tracks with scales are on page 2 of the synth page. In 0.2.1 you held
  Shift to select scale and key, and Track 1 to 7 turned scales on or off. Now Track 1 to 7 close the page and select
  that track.

## Known issues

* **Macro 1 light at step 8 to 9.** On track 8, the light of macro 1 can flicker when the sequencer goes from step
  8 to step 9. The cause is not known.
* **Crackle on the tested unit.** A sample was heard crackling on the tested unit with the stock firmware 5706.
  The cause and extent are unknown.
* All known issues of 0.2.1 stay: see the [0.2.1 release notes](RELEASE-NOTES-0.2.1.md#known-issues).

## Test status

**On the unit, with development builds that have the code of 0.2.2 except the final light changes:**

* Install and health report: pass.
* The synth plays on track 8: pass.
* Synth page: Up and Down, the layout of both pages, the exit buttons and their lights, Note exit from the normal
  and the expanded Note view, Track 8 lit while Shift is held, Clear + macros 1 to 4, tune saved and loaded with the
  project: pass.
* Card writer with the module files web page: write, verify, list and delete module files, and write a published
  module again with its button: pass. The picker shows a written file and starts it: pass.
* Output level: measured on the unit against the 7 dB decrease of 0.2.1. Six capture comparisons on development
  builds showed about 3 dB more level than 0.2.1. Live settings were not fully established across boots, so this
  was not a complete headroom test.

The public image itself was not run on a unit. The final light and list-order changes (Clear and Shift lights on
the synth page, list order on the module page) were tested in emulation only.

**In emulation only:** the module functions and synth page functions not named above.

**Not measured:** the processing time of the synth on the unit, as in 0.2.0.

## Install

Use the installer page, as for 0.2.1: [install guide](docs/user/install.md). An earlier Arrhythmia release updates
the same way.

| Build | Number | SHA-256 of the patched `.syx` |
|---|---|---|
| arrhythmia-firmware | 7339 | `7aadbde0811a77b23c05a06fca34c36d27d98c98945f3f587bb8d7ccf63fc822` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is released again.

The version query (SysEx `70`) ends `00 39 2B` for 7339 and `00 38 3C` for 7228. In bootloader mode the pads show
`73 39` for 7339. The release file is `release/v0.2.2.json`. The files of the earlier releases stay in the repository
as their record.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706 (SHA-256
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`), on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The ARM source is in `patches/src/`. The DSP source is in `tools/dspvoice207m4/`. It is the DSP source of 0.2.1
(`tools/dspvoice207rel/`) with one change: the output level constant. The
[rebuild steps](docs/README.md#rebuilding-the-images) describe how image 7339 rebuilds from this tree with the official
firmware 5706 file as the only firmware input.

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
