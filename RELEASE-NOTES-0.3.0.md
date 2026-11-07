# Circus Arrhythmia FM 0.3.0 release notes

Circus Arrhythmia FM is custom firmware for the Novation Circuit Rhythm. It ships as a patcher: you apply it to your own copy of
the official firmware file on your own computer, then send the result to the unit. No Novation firmware is included.

Arrhythmia is not affiliated with or endorsed by Novation or Focusrite. The device name says only which hardware this
is for.

0.3.0 builds on 0.2.2. The changes are the engines of track 8, the feedback of the FM synth, the arpeggiator and the
synth page, an expanded Euclid module, and a combined engine and module loader.

## Changes since 0.2.2

### New

* **Engines on track 8.** Each card pack makes track 8 play either its samples or an engine
  ([engines](docs/user/synth.md#engines-on-track-8)):
  * The FM synth is the engine file `fm.dse`. It is a release asset. The firmware does not contain it.
  * Copy `fm.dse` to the folder `engines` on the microSD card.
  * Put a text file `engine.cfg` in a pack folder `Rhythm/NN_<Name>`. Its one line is `fm.dse` or `none`. The pack
    plays that engine, or its samples, from its next load.
  * A pack without `engine.cfg` follows `engines/default.cfg`, with the same format. Without that file it plays
    samples. The factory pack follows `default.cfg` only.
  * A selection that is not valid, or a named file that is missing or damaged, gives the samples of the pack. The
    default and other engines are not used instead.
  * A project change inside a pack keeps the engine. A pack change loads the engine of the new pack. During a pack
    load, track 8 notes stop and new notes do not sound until the load ends.
  * The [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html) writes the selection over USB. Load the pack
    on the unit in the stock Packs view. Save project edits first: loading a pack discards unsaved edits.
* **FM feedback.** Macro 1 of track 8 reaches a feedback of 8x. It acts at once on sounding notes, as FM depth,
  cutoff and resonance do. This needs the `fm.dse` of this release ([macros](docs/user/synth.md#macros-on-track-8)).
* **Arpeggiator on track 8.** Page 3 of the synth page sets on or off, mode, latch, rate, octaves and gate.
  Settings are saved per project. It accepts pads, MIDI, modules and sequencer notes, runs at the project tempo
  with transport stopped, and sends its generated notes to MIDI out
  ([arpeggiator](docs/user/synth.md#the-arpeggiator)).
* **Combined loader.** Manage engines/modules on microSD over USB. The loader lists the actual card pack folder
  names, their engine selections, and engine names and versions. Add, update or remove the published FM engine,
  choose an engine for a pack, and manage the ten existing modules. It reports the current engine state and the
  saved pack selections. **Scan card** and **Check status** only read.
  The old module page forwards to the loader; the firmware installer remains separate.
* **Module assignments in the loader.** Select a module for each pad or add a custom `.mod` file, preview its
  colour, then review the writes and deletions before applying **Write to card**. **Reset to default modules**
  stages the release arrangement without changing the card. Recognized published assignments can be upgraded,
  including the 0.2.2 Euclid; unknown files stay and conflicts stop the plan. List and Verify can recover helper
  files from interrupted writes, so they can change the card. A failed or unknown job stops without automatic
  retry ([modules](docs/user/modules.md#add-modules-with-the-web-page)).
* **Independent engine versions.** `fm.dse` is version 1.0.0; the firmware is 0.3.0. Legacy unmarked engines show an
  unknown version. A different checksum does not prove that an engine is older. Engine compatibility still depends
  on the firmware's ABI, load-base, kind and integrity checks.
* **Scales on track 8.** Pad 8 of row 1 on page 2 of the synth page turns scales on or off for track 8
  ([scales](docs/user/scales.md)).
* **Expanded Euclid module.** Four independent rows now reach 32 steps, with hits, rotation, target, note,
  velocity, accent and chance. Overview shows four rows of eight steps with paging; Track 1 to 4 open each row's
  32-step detail. Note, Velocity, Gate and Pattern Settings turn rows 1 to 4 on or off. Detail has a keyboard for
  up to 24 supported note choices, 16-pad velocity/accent bars and brief value feedback. Track 5 to 8 and 1–16 are
  dark and inactive ([Euclid guide](docs/user/euclid.md)).
* **Euclid pattern write and undo.** Rec writes active rows to their target patterns; Clear undoes only the last
  successful explicit Rec write while Euclid remains open. Assigned target patterns are suppressed during Euclid,
  including targets of off rows. Duplicate active writable targets refuse Rec. Ready, pending, success and refusal
  have distinct button lights; completed results flash briefly, while an unknown outcome remains indicated and
  is never retried automatically. Clear is dark and inactive without an undo. Keep stock Record off before
  opening Euclid for this workflow.
* **Euclid settings and pattern import.** Row settings remain in RAM across closes. At its first start after power
  on or project load, Euclid can recover representable rows from target patterns. It always opens in overview;
  retained settings are not a project save. The [guide](docs/user/euclid.md) explains recovery limits and older hosts.

### Changed behaviour

* **Euclid levels and keyboard feedback.** Velocity and accent edits use 16 positive levels from 7 to 127;
  accent also has off. Imported and retained values stay unchanged until edited; see the
  [first-edit behaviour](docs/user/euclid.md#the-macro-knobs). Each row retains its keyboard display window
  while the chosen pitch remains visible. An all-rows-off Rec refusal pulses all four row-toggle side buttons
  red, matching the conflicting-target selectors, for the brief refusal indication.

* **Migration from 0.2.2: FM now needs the card file.** Updating the firmware alone leaves track 8 playing samples.
  Add `engines/fm.dse` and select it with the pack's `engine.cfg`, or use `engines/default.cfg` for packs without
  a selection and the factory pack. Existing synth projects do not select an engine themselves.

* **Lowercase card names.** New engine and module paths use lowercase: `engines/fm.dse`, `engine.cfg`,
  `engines/default.cfg`, and `modules/<name>.mod`. Selection text uses `fm.dse` or `none`. Existing uppercase FAT
  names and selection text remain compatible. Stock pack folders keep `Rhythm/NN_<Name>`.

* **Arp on or off on page 3 only.** Page 1 row 1 pad 8 is dark and has no function.
* **Synth page macros on page 1 only.** Macros 1 to 4 of the synth page act on page 1. On pages 2 and 3 their knobs
  have no function and their lights are dark.
* **Sample view with an engine.** With the FM engine on track 8, the Sample view stops at page 2. It has the 16
  families on each page, under one algorithm per page. Up and Down navigate those two pages, and opening Sample
  shows the selected sound's page. This changes page navigation, not the existing 32 sounds. Tracks 1 to 7, and
  track 8 without an engine, keep the eight pages.
* **Track 8 without an engine.** Track 8 plays the samples of its pack. Shift + Track 8 opens the scale page only;
  chord and arp pages are unavailable. The scale toggle for track 8 works with samples as well as the FM engine.
* **Ten modules, updated arrangement.** The asset count remains ten. The default files and picker pads are listed
  below; Euclid is the expanded module, not an additional file. Update the card copy to obtain its new controls.

| Pad | File | Module |
|---|---|---|
| 1 | `01life.mod` | Life |
| 2 | `02rain.mod` | Rain |
| 3 | `03balls.mod` | Balls |
| 4 | `04stars.mod` | Stars |
| 5 | `05lights.mod` | Lights Out |
| 9 | `09paint.mod` | Paint |
| 10 | `10euclid.mod` | Euclid |
| 11 | `11pulse.mod` | Pulse |
| 17 | `17snake.mod` | Snake |
| 18 | `18mole.mod` | Whack-a-mole |

## Known limitations

* The track 8 engine interface (CRE2 container, queue ABI 0x207, load base, kind and base id) is defined only by the published source and tools; a written, stable interface specification is in development.

* **Voice count and voice stealing are not measured.** The number of physical voices of the FM synth on the unit,
  and which voice a new note takes when all are busy, are not measured.
* **DSP headroom is not measured.** The headroom with four voices and live feedback is not measured on the unit.
  A very high load can cause audio errors.
* **Selections apply on the next pack load.** The loader saves the choice; load the pack on the unit in the stock
  Packs view. Removing an engine leaves pack pointers intact, and those packs use samples on their next load.
* **Card scan limits.** Unknown, ambiguous or incomplete pack rows cannot be selected. Some non-ASCII pack names
  are shown as flagged short FAT aliases. Unidentified pack positions remain disabled.
* **Euclid also uses the stock recorder.** Keep Record off before opening Euclid for pattern writing. If Record
  is already armed, live notes are recorded while Play runs. Clear undoes the last explicit Rec write, not earlier
  live recording ([controls and limits](docs/user/euclid.md)).
* All known limitations of [0.2.2](RELEASE-NOTES-0.2.2.md#known-issues) and earlier stay.

## Test status

The preceding Euclid revision (rev7) was proved on the unit. The final Euclid revision (rev8), including the
level, keyboard-window and all-rows-off light changes, was tested with the bench emulator and frozen stock
frames. It was not re-proved on the unit; its hardware run was waived for this release. The hardware run for
public firmware build 7358 was also waived. These waivers do not establish a complete hardware pass, listening
qualification, measured voice count or DSP headroom. Earlier release test results apply only to the builds and
scope named in those release notes.

## Install

Use the installer page, as for 0.2.2, then add the FM engine: [install guide](docs/user/install.md). An earlier
Arrhythmia release updates the same way.

1. Install arrhythmia-firmware with the installer page. The page shows build 7358.
2. Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html).
3. Add `fm.dse` with the loader's Engines controls ([engines](docs/user/synth.md#engines-on-track-8)).
4. Save the FM selection for a pack.
5. Save project edits on the unit.
6. Load that pack in the stock Packs view.
7. Update the card modules through the loader to obtain the new Euclid controls.

| Build | Number | Version query (SysEx `70`) ends | Bootloader mode shows |
|---|---|---|---|
| arrhythmia-firmware | 7358 | `00 39 3E` | `73 58` |
| arrhythmia-devkit | 7228 | `00 38 3C` | `72 28` |

arrhythmia-devkit 7228 is unchanged from 0.1.0 and is released again. The release page lists the SHA-256 of each
patched `.syx`. The release file is `release/v0.3.0.json`. The files of the earlier releases stay in the repository as
their record.

**Input:** the official Circuit Rhythm firmware 2.0.1, build 5706, on a unit with bootloader 4799. The installer and
`tools/crpatch.py` refuse any other file.

## Source

The ARM source is in `patches/src/`. The host tools are in `tools/`.

## Licence

MIT (see [LICENSE](LICENSE)) for the Arrhythmia code and documents. Novation's firmware is not included and not
covered.
