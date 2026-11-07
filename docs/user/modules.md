# Circus Arrhythmia FM: the modules on the microSD card

Applies to Circus Arrhythmia FM 0.3.0, firmware build 7358. Test status: see the
[release notes](../../RELEASE-NOTES-0.3.0.md#known-limitations).

A module is a small program that runs on the unit. The release has 10 module files. Each file name starts with a
number: that number is the pad of the module in the picker.

| Pad | File | Module | Clock | Sound |
|---|---|---|---|---|
| 1 | `01life.mod` | [Life](#life-pad-1) | yes | yes |
| 2 | `02rain.mod` | [Rain](#rain-pad-2) | yes | yes |
| 3 | `03balls.mod` | [Balls](#balls-pad-3) | yes | yes |
| 4 | `04stars.mod` | [Stars](#stars-pad-4) | no | no |
| 5 | `05lights.mod` | [Lights Out](#lights-out-pad-5) | no | no |
| 9 | `09paint.mod` | [Paint](#paint-pad-9) | no | yes |
| 10 | `10euclid.mod` | [Euclid](#euclid-pad-10) | yes | yes |
| 11 | `11pulse.mod` | [Pulse](#pulse-pad-11) | yes | no |
| 17 | `17snake.mod` | [Snake](#snake-pad-17) | no | no |
| 18 | `18mole.mod` | [Whack-a-mole](#whack-a-mole-pad-18) | yes | no |

"Clock: yes" means that the module follows the sequencer clock. Push **Play** to start it.

## Put the modules on the card

1. Power the unit off and remove the microSD card. Put the card in a card reader. The USB drive of the unit does not
   show the card.
2. At the root of the card, make a folder `modules`. Existing uppercase FAT folder and file names remain compatible.
3. Copy the `.mod` files into that folder. Keep the file names. The number at the start sets the pad
   ([File names and pads](#file-names-and-pads)).
4. Put the card back and power the unit on. The card must be present at power-on.

The picker only reads the card. It does not change your projects or samples.

## File names and pads

The picker shows up to 32 module files, on pads 1 to 32. Pad 1 is top left, pads 1 to 8 are the first row.

* A module file is a file in the folder `modules` with the extension `.mod` and a size of 26 to 15,360 bytes. Use
  short names: up to 8 characters before `.mod`. The picker uses the short (8.3) name of a file. Hidden and system
  files are not shown.
* A name that starts with two digits `01` to `32` (for example `07drums.mod`) puts the file on that pad.
* If two files have the same number, the first in the list gets the pad. The other file goes on the lowest free pad.
* A file without a number (or with `00` or `33` to `99`) goes on the lowest free pad.
* The list order: first the files with a number, then the other files. Each group is in character order of the name.
* With more than 32 files, only the first 32 in the list order are shown.
* The colour hash uses the name without the number. When colours collide, the picker chooses unused colours
  in pad order, so moving a file can change its final colour.

## Add modules with the web page

Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html) in Chrome. It manages engines and modules
on the microSD card over USB. The old `web/modules.html` address forwards to this page. Firmware installation
uses the [separate installer](https://llcoolkm.github.io/arrhythmia/web/).

The page requires Chrome with Web MIDI and SysEx, and arrhythmia-firmware with the card writer.

1. Close other music applications. Connect the unit by USB, stop playback and select **Connect MIDI**.
2. Under **Modules**, edit the staged assignments. Select a pad and choose its module, or drop a custom `.mod`
   file into the pad panel. Use a short custom name: up to six characters, A to Z, 0 to 9, `_` or `-`; the loader adds
   the two-digit pad number. The rows are **Visualizers** (Life, Rain, Balls, Stars, Lights Out), **Other** (Paint,
   Euclid, Pulse), and **Games** (Snake, Whack-a-mole).
3. **Reset to default modules** stages the default arrangement above. It does not change the card.
4. **Write to card** checks the files and shows the exact writes and deletions. Confirm that preview to apply them.
   A reset only removes numbered published module assignments whose bytes match the frozen release by size and CRC. Conflicts
   stop the plan; unknown files stay. Use **Card files → List**, **Verify**, and **Delete** to inspect or explicitly
   remove conflicts. The published 0.2.2 Euclid is also recognized and upgraded to the current module. Any failed
   or unknown operation stops the remaining jobs without retry.
5. Wait for the page to report the job result before starting another operation. Use **Disconnect** to release
   the MIDI port before using another MIDI tool.

The firmware derives pad colours from names and an 11-colour palette. This loader previews those colours for the
staged set. Other files on the card can change the result. A user-selected 16-colour palette needs additional
firmware support and is deferred to 0.3.1; this version cannot save a chosen colour.

Card commands run one job at a time and stop a running module. Module **List** and **Verify** can recover helper
files left by an interrupted write, so they can change the card. The loader does not start modules; use the picker
on the unit. **Check status** and the engine **Scan card** only read the card. See
[Engines on track 8](synth.md#engines-on-track-8) for engine files, versions and pack selections.

## Start and stop a module

**Shift + Sample Rec** opens the picker.

![The module picker with the 10 module files: each pad with a file is lit in its own colour, Sample Rec is lit](img/module-picker.png)

* A pad with a file comes on in its own colour. Pads without a file stay dark. With no card, no folder `modules` or no module file, no pad
  comes on.
* Push a lit pad to start that module. The pad that loads blinks white.
* A red pad means that the file did not load: it failed its checks, or the unit could not read it. The module does
  not start. The picker stays open.
* Other buttons and the macro knobs do not control the picker. Play controls the transport. Only Sample Rec is lit.
* **Shift + Sample Rec** in the picker closes the picker.
* **Shift + Sample Rec** while a module runs stops the module. The normal lights and controls come back at once.

No module stops automatically. No module starts automatically at power-on.

## While a module runs

* **Play** always starts and stops the transport.
* A module that follows the clock waits for **Play**. While it waits, the Play button blinks dim green. When the
  transport stops, its clock-driven activity stops and its notes are released. The module stays open with idle lights.
* Most modules take the pads, the buttons and the macros. **Shift**, Shift + Sample Rec, the master filter and the
  master volume stay normal.
* Pulse and Stars do not take the controls. The pads, buttons and macros operate normally. The lights show the
  module.
* In all modules except Paint, one value step of a module macro needs four encoder counts. Small knob bounces do not
  change the value. Paint uses the encoder counts directly.

## Notes from the modules

Euclid, Life, Rain and Balls play notes on a selected target track. These rules apply to all four:

* The notes come from the Note view keyboard of the target track. The module uses the notes that the keyboard shows
  in the two octaves from the original pitch of the sample up. Degree 0 is the lowest of these notes. The higher
  degrees go up through these notes. Life, Rain and Balls wrap after the highest note. Euclid’s note knob
  stops at the lowest and highest choices instead of wrapping.
* With scales on (tracks 1 to 8), the notes are in the scale ([scales.md](scales.md)).
* If the module cannot read the keyboard of the track (for example a slice layout), it plays no notes on that track.
* Each track plays one module note at a time. A new note on the same track ends the note before it.
* The notes of Life, Rain and Balls play the sound of the track only. They do not go to MIDI out, and the pattern
  does not record them. The notes of Euclid also go to MIDI out and to the recorder ([Euclid](#euclid-pad-10)).
* On track 8, arrhythmia-firmware plays the FM synth when the pack selects the FM engine, and samples otherwise
  ([synth.md](synth.md#engines-on-track-8)).

Paint plays notes differently ([Paint](#paint-pad-9)).

## Paint (pad 9)

Start: pad 9 in the picker.

![Paint: pushed pads light in the selected colour and fade; each column plays its track](img/module-paint.png)

Pads light where you push them, then fade. Each pad also plays a note.

* **Pads.** A push lights the pad in the selected colour. The light then fades.
* **Sound.** A push plays note 60 (the sample at its original pitch) on the track of the pad's column: column 1 plays
  track 1, column 8 plays track 8. The velocity is half the velocity of the push. The release of the pad sends the
  note off. A one-shot sample plays to its end.
* **Macro 1:** colour. Turn it to go round the colour wheel.
* **Macro 2:** trail length. Clockwise makes the trail longer, from approx. 0.5 s to approx. 16 s in 28 steps. Start
  value: approx. 1.5 s.
* The other buttons stay dim white.
* Paint does not follow the clock. It runs immediately.

## Euclid (pad 10)

Four Euclidean rhythm rows, each up to 32 steps, play chosen target tracks. Start `10euclid.mod` from pad 10 in
the picker. Overview shows all four rhythms; Track 1 to 4 opens a row's 32-step detail and its eight controls.

In arrhythmia-firmware 0.3.0, **Rec** writes active writable rows into patterns and **Clear** undoes the last
explicit write while Euclid remains open. **Keep stock Record off** for this workflow: Clear does not undo
earlier live recording. All four assigned target patterns remain suppressed while Euclid is open, including
those of rows that are off; live pad/MIDI notes and effect tails are not muted.

<a id="euclid-views-and-pads"></a>
<a id="euclid-macros"></a>
<a id="euclid-clock-and-notes"></a>
<a id="euclid-settings"></a>
<a id="euclid-pattern-write"></a>
<a id="write-a-beat-into-the-patterns"></a>
<a id="euclid-lights"></a>
<a id="record-on-older-hosts"></a>

Controls, feedback illustrations, writing, undo and older-host recording: [Euclid user guide](euclid.md).

## Life (pad 1)

Start: pad 1 in the picker.

![Life: living cells in green, the cell that plays in magenta](img/module-life.png)

The Game of Life on the 8 x 4 pads. The edges wrap: the left edge touches the right edge, the top edge touches the
bottom edge. A cell with 3 neighbours comes to life. A living cell with 2 or 3 neighbours stays alive.

* **Pads.** Push a pad to turn its cell on or off.
* **Macro 1:** generation length: a sixteenth note (start value), an eighth note, a quarter note or a half note.
* **Macro 2:** density, 1 to 75 % (start value 30 %). Shortly after you stop the turn, the grid fills at random with
  that density.
* **Macro 3:** target track, 1 to 8 (start value 1).
* **Clock.** Push Play to start. Each generation, one living cell plays a note, in turn through the living cells in
  pad order. The pad number sets the degree: pad 1 is degree 0, pad 2 degree 1, and so on. The velocity is 100. A
  note lasts half a generation.
* An empty grid fills again at random at once. A grid that does not change for 4 generations also fills again.
* **Lights.** Living cells are green. The cell that plays has a different colour. While the transport is stopped, the cells pulse
  dimly.
* At the start, three cells in the second row are alive.

## Rain (pad 2)

Start: pad 2 in the picker.

![Rain: blue drops fall; a drop that lands in the bottom row flashes white and plays](img/module-rain.png)

Drops fall down the pads. A drop that lands plays a note.

* **Pads.** Push a pad to add a drop there.
* **Macro 1:** time between new drops: a sixteenth note, an eighth note (start value), a quarter note or a half
  note. Each new drop starts in a random column of the top row.
* **Macro 2:** range, 1 to 24 degrees (start value 8). Column 1 plays degree 0. Column 8 plays the highest degree
  of the range.
* **Macro 3:** fall speed. The drops move down one row each thirty-second note, sixteenth note (start value), eighth
  note or quarter note.
* **Macro 4:** target track, 1 to 8 (start value 1).
* **Clock.** Push Play to start. A drop in the bottom row lands at the next fall step. The velocity is 100. A note
  lasts half a fall step, with a minimum of about 64 ms (six light frames).
* If drops land together, one note plays: the note of the leftmost column. Drops that land while a note sounds
  flash but do not play.
* **Lights.** Drops are blue. A landing flashes white in the bottom row. With no drops, one pad of the bottom row
  pulses dimly.
* Stop clears all drops.

## Balls (pad 3)

Start: pad 3 in the picker.

![Balls: two balls on the pads, Clear lit green](img/module-balls.png)

Up to four balls bounce on the pads. A ball that hits a wall plays a note.

* **Pads.** Push a pad to add a ball there. This also increases macro 3 (number of balls). With four balls, a push
  replaces one ball, in turn.
* **Clear** puts the balls back at their start positions. The macro values stay.
* **Macro 1:** speed. One movement each half note, quarter note, eighth note or sixteenth note (start value).
  Clockwise is faster.
* **Macro 2:** gravity, 0 to 4 (start value 1).
* **Macro 3:** number of balls, 1 to 4 (start value 2).
* **Macro 4:** target track, 1 to 8 (start value 1).
* **Clock.** Push Play to start. The cell of the hit sets the degree: pad 1 is degree 0, pad 2 degree 1, and so on.
  The velocity is 96. A note lasts half a movement. If balls hit walls at the same time, one note plays.
* **Lights.** Each ball has its own colour. Clear is lit green.
* While the transport is stopped, the balls stay still.

## Pulse (pad 11)

Start: pad 11 in the picker.

![Pulse: one column lit on each eighth note](img/module-pulse.png)

A light show on the beat. Pulse makes no sound.

* Pulse does not take the controls. The pads, buttons and macros operate normally.
* The button and knob lights are dark while Pulse runs.
* **Clock.** Push Play to start. While the transport is stopped, all pads pulse dimly.
* Pulse has three patterns:
  * all pads flash on each beat;
  * one column lights on each eighth note, from left to right;
  * one row lights on each sixteenth note, from top to bottom.
* The pattern changes every four bars. The colour changes every bar: red, yellow, green, cyan, blue, magenta.

## Stars (pad 4)

Start: pad 4 in the picker.

![Stars: single red, green and blue stars on the pads, buttons and knob lights](img/module-stars.png)

Stars come on slowly and fade out slowly, on all the lights of the unit. Stars makes no sound.

* Stars does not take the controls. The pads play the tracks normally.
* Stars does not follow the clock. It runs immediately.

## Snake (pad 17)

Start: pad 17 in the picker.

![Snake: the green snake with its bright head, the orange food, macro 1 lit for the speed](img/module-snake.png)

The snake game. Snake makes no sound.

* The game starts paused. The head of the snake blinks. Push a pad to start.
* **Pads.** Push a pad on the side of the head where you want to go. The larger distance (row or column) sets the
  direction. With equal distances, the snake goes up or down. The snake turns once for each move. It cannot
  reverse its direction in one move.
* At the start, the snake moves right and its body is below the head. If your first push is below the head, the
  snake turns into its own body and the game ends.
* **Macro 1:** speed. Clockwise is faster. The light of macro 1 shows the speed. Snake follows the light frames of
  the unit, not the tempo.
* Orange is the food. Green is the snake. The head is bright. The snake grows when it eats.
* The game ends when the snake hits a wall or its own body. It can move into the cell that its tail leaves.
* **Game over.** The pads flash red. The green pads show the score: one pad for each food eaten.
* **Win.** The snake fills the board. The other pads flash blue.
* After approx. half a second, push a pad to show a new board. Push a pad again to start. The speed stays.
* The buttons are dim white. They have no function.
* Snake does not follow the clock.

## Lights Out (pad 5)

Start: pad 5 in the picker.

![Lights Out: a puzzle with orange lit pads, Clear and Rec lit green](img/module-lights.png)

The Lights Out puzzle. Lights Out makes no sound.

* **Pads.** Push a pad to change it and the pads above, below, left and right of it. The edges do not wrap. A push
  flashes white for a moment.
* The aim: turn all pads off. Lit pads are orange.
* **Clear** starts a new puzzle. Each puzzle is made from the solved board, so it always has a solution.
* **Rec.** Hold Rec to see the number of pushes, 00 to 99, as two digits. Rec is lit green. At 99 or more pushes,
  Rec is red and the count stays at 99.
* **Win.** All pads are green, in turn with the count. This stays until you push Clear. Pad pushes have no effect.
* Clear is lit green.
* Lights Out does not follow the clock.

## Whack-a-mole (pad 18)

Start: pad 18 in the picker.

![Whack-a-mole: one yellow target pad, Clear and Rec green, Duplicate red](img/module-mole.png)

Hit the yellow pad before the next target comes on. Whack-a-mole makes no sound.

* **Clock.** Push Play to start. A new yellow pad comes on at each interval. Two sequential targets are never the
  same pad.
* **Pads.** Push the yellow pad to score. Each target scores once. While a target is active, a push on another pad
  counts a miss. After a hit, pad pushes are ignored until the next target. A target that you do not hit counts a
  miss when the next one comes.
* **Macro 1:** interval: a half note, a quarter note (start value), an eighth note or a sixteenth note. Clockwise is
  faster.
* **Clear** sets the score and the misses to 0. It also removes the target, without a miss.
* **Rec.** Hold Rec to see the score as two green digits.
* **Duplicate.** Hold Duplicate to see the misses as two red digits.
* While you hold Rec or Duplicate, the target does not show.
* Clear and Rec are lit green, Duplicate is lit red.
* The counts stop at 99.
* Start and stop remove the target without a miss.
