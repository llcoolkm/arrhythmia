# Circus Arrhythmia FM: export a project as MIDI

The MIDI export converts a project of the unit into a ZIP file for a DAW, for example Ableton Live. The ZIP holds a
multitrack MIDI file, the samples that the project plays, and a mapping of MIDI notes to samples. The export runs on
the computer. It reads the project files from a folder, or from the unit over USB. It does not modify the project
and sample files that it reads, on the computer or on the unit. It writes only the files you ask for: the ZIP, and
with `tools/unitread.py --raw` copies of the files as read.

Three programs make the same ZIP:

* the web page `web/export.html` (Chrome; no installation): from a folder or from the unit;
* the command-line tool `tools/midiexport.py` (Python 3.12 or newer, standard library only): from a folder;
* the command-line tool `tools/unitread.py` (Linux, Python 3.12 or newer): from the unit.

## Contents of the ZIP

| File | Content |
|---|---|
| `NN_<name>.mid` | MIDI file, type 1, 96 ticks per quarter note. One MIDI track for each track of the unit. |
| `samples/slotNN_<name>.wav` | the WAV files of the samples that tracks 1 to 7 play, copied from the pack. `NN` is the sample slot. |
| `MAPPING.md` | for each track: the MIDI note of each sample and its file; the CC numbers; what is not exported. |
| `mapping.json` | the same data for programs. |

The MIDI file contains:

* **Tracks 1 to 7** on MIDI channels 1 to 7: one MIDI note for each sample. A step with a sample flip uses the note of
  the flipped sample. The samples of a track get consecutive notes from note 36 (C1), in the order of their slot.
* **Track 8** on MIDI channel 8: the notes of the FM synth, with chords as they play on the unit. Step chords, notes
  per press, chord type, In key, voicing and the track octave apply.
* Velocity and note length of each step. Notes start at their exact microstep position.
* The arrangement: the scene chain, or the pattern chains when no scene chain is set. Each scene has a marker.
* Tempo, time signature 4/4, and the swing value as a text event.
* The length of the arrangement: every MIDI track ends at the end of the arrangement, silent parts included.
* Parameter locks as CC events: macros 1 to 8 as CC 21 to 28, the track parameters as CC 29 to 32. Ableton Live
  ignores CC events when it imports a MIDI file.

## Get the project files

The projects are in the folders `Sessions` of the packs. The samples are in the folders `PCM` of the same packs. The
USB disk of the unit shows only a welcome page to the computer. The projects are not visible there. To read them
from the unit without a copy, see [Read from the unit](#read-from-the-unit).

**Packs on the microSD card:**

1. Turn the unit off and remove the microSD card.
2. Copy the folder `Rhythm` of the card to the computer with a card reader.
3. Put the card back with the unit off.
4. The pack folders are in the copy of `Rhythm`.

**Projects saved inside the unit (Linux):**

1. Save pending project edits on the unit.
2. Connect the unit by USB.
3. Make a copy of the internal storage and extract its files:

   ```
   sudo python3 -I tools/norbackup.py usb auto backup
   python3 -I tools/norbackup.py extract backup
   ```

4. The pack folders are in `backup/files/RHYTHM`.

**One project file:** a file `NN_<name>.ncs` from a `Sessions` folder, or a file that contains project files
unchanged. The export of a single file has no sample files: the mapping gives the sample slots only.

## Export with the web page

1. Open `web/export.html` in Chrome. The installer page links to it.
2. Click **Open folder** and select the copy of `Rhythm`, the folder `backup/files`, or one pack folder. Or click
   **Open project file** and select one or more files.
3. The page lists the projects: pack, project number, name, and the number of sample files of the pack.
4. Click a project. The page shows the tracks, the number of notes and the warnings.
5. Optional: set the options (table below).
6. Click **Download ZIP**.

| Option | Function |
|---|---|
| Pitched notes on tracks | write these sample tracks as pitched notes instead of one note per sample |
| First note | the MIDI note of the first sample of a track (default 36) |
| Passes | play the arrangement this number of times (default 1) |
| Parameter locks as CC | write parameter locks as CC events (default on) |

## Read from the unit

The web page and `tools/unitread.py` read the projects and the samples from the unit over USB MIDI, with the
content transfer that Novation Components uses. They only read: the project and sample files on the unit are not
modified. During each read the unit is in its transfer state for a few seconds. On Circus Arrhythmia FM 0.2.2 the
track-8 synth pauses during the transfer and starts again by itself.

Requirements:

* The unit runs Circus Arrhythmia FM 0.2.2 or the stock firmware 2.0.1.
* The sequencer is stopped.
* No other program uses the unit's MIDI port: close Components, the installer page and any DAW.

**With the web page:**

1. Connect the unit by USB and stop the sequencer.
2. Open `web/export.html` in Chrome and click **Read from unit**. Allow MIDI access when Chrome asks.
3. The page lists the projects of every pack on the unit. The Samples column shows "on the unit".
4. Click a project. The page reads the project and the samples it plays, then shows the tracks.
5. Set the options and click **Download ZIP**.

Touch no control on the unit while the page reads. A project with its samples takes a few seconds.

**With the command-line tool (Linux):**

1. Connect the unit by USB and stop the sequencer.
2. List the packs and projects:

   ```
   python3 -I tools/unitread.py list
   ```

3. Read one project into a ZIP. SLOT is the number of the pack in the list (0 is the internal pack, 1 to 31 are
   card packs), INDEX the number of the project:

   ```
   python3 -I tools/unitread.py read SLOT INDEX -o song.zip
   ```

| Option | Function |
|---|---|
| `-o FILE` | write the ZIP |
| `--raw FOLDER` | also write the project file and the samples as read, in `FOLDER/<pack>/Sessions` and `FOLDER/<pack>/PCM` |
| `--device PATH` | the raw MIDI device of the unit (default: the first sound card named Circuit Rhythm) |
| `--transcript FILE` | write every MIDI frame sent and received to a JSON file |
| `--pitched`, `--base`, `--scenes`, `--loops`, `--no-locks` | as for `tools/midiexport.py` |

Limits and messages:

* A sample that would take more than 5 seconds to transfer is not read, and the read stops with its name. Export
  that pack from a copy of the card.
* "the unit refused the transfer" or "the unit is playing": stop the sequencer and try again.
* "Stopped inside a transfer": the transfer ended in an unknown state, and nothing is retried. Do not save on the
  unit and do not start another read. Report the message, with the transcript file if `--transcript` was given.

## Export with the command-line tool

1. Open a terminal in the Arrhythmia folder.
2. Run:

   ```
   python3 -I tools/midiexport.py "Rhythm/<pack>/Sessions/NN_<name>.ncs" --zip song.zip
   ```

3. The tool writes `song.zip`. It shows the length, the notes of each track and the warnings.

| Option | Function |
|---|---|
| `--zip FILE` | write the ZIP |
| `-o FILE` | write only the MIDI file |
| `--map FILE`, `--md FILE` | write `mapping.json` or `MAPPING.md` |
| `--pcm FOLDER` | the `PCM` folder of the pack, if it is not next to `Sessions` |
| `--pitched 3,5` | write these sample tracks (1 to 7) as pitched notes |
| `--base N` | the MIDI note of the first sample of a track (default 36) |
| `--scenes 1-4` | export these scenes (1 to 16) instead of the scene chain |
| `--loops N` | play the arrangement N times (default 1) |
| `--no-locks` | do not write parameter locks |

## Import into Ableton Live

1. Extract the ZIP.
2. Drag the `.mid` file from the browser into the Arrangement view. Live puts each track of the file on its own
   MIDI track.
3. For each of tracks 1 to 7: put a Drum Rack on the track. Open `MAPPING.md`. For each row of the track, drag the
   sample file onto the pad of the MIDI note. Note 36 is C1, the first pad of a new Drum Rack.
4. Put an instrument on track 8.
5. Set the tempo of the Live Set to the tempo in `MAPPING.md`.

## Sample notes

* Each sample track uses its own notes. Two tracks can use the same note for different samples.
* A pitched track writes the pitch of each step: MIDI note 60 plays the sample at its original pitch. Use this for a
  track that plays one melodic sample. Sample flips are not in the MIDI file of a pitched track.
* A sample slot without a file in the pack has a note but no sample file. `MAPPING.md` shows "(not in the pack)".

## How the arrangement is made

* **Scene chain.** The scenes of the chain play in order, one time. A scene lasts as long as its longest pattern
  chain. A track with a shorter pattern chain repeats it until the scene ends.
* **No scene chain** (one scene selected): the pattern chains of the 8 tracks play one time, for the length of the
  longest chain.
* **Patterns.** Each pattern plays from its start step to its end step, in its direction and at its sync rate.
  Ping-pong plays both end steps two times.
* The arrangement is the saved project. Changes that are not saved on the unit are not in the export.

## Not exported

* Swing is not applied to the note positions. The swing value is in the text event and in the mapping.
* Steps with a probability below 100 % are written as if they always play. The mapping gives the number of these
  steps for each track.
* A pattern in random direction is written in forward order. The mapping and the warnings name these patterns.
* The positions of the macro knobs. A CC event is written only where a step has a parameter lock.
* Parameter locks between microsteps at the slow sync rates. The unit interpolates them.
* Slice mode. A sliced sample plays as the step note.
* Voice limits and release tails of the track 8 synth, and the FM sound per step of track 8.
* Muted tracks are exported. Their track name ends with "(muted)".

## Record the MIDI output instead

The unit also sends the notes of each track on its MIDI channel while it plays. A DAW can record this output. A recording does not show which sample plays (a sample flip does not
change the note), has track 8 chords as the root note only, and has timing differences of approximately 15 ms.
