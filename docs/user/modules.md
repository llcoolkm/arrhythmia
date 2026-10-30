# Arrhythmia: the modules on the microSD card

Applies to Arrhythmia 0.2.1, arrhythmia-firmware build 7329. Test status: see the
[release notes](../../RELEASE-NOTES-0.2.1.md#test-status).

A module is a small program that runs on the unit. The release has 10 module files. Each file name starts with a
number: that number is the pad of the module in the picker.

| Pad | File | Module | Clock | Sound |
|---|---|---|---|---|
| 1 | `01paint.mod` | [Paint](#paint-pad-1) | no | yes |
| 2 | `02euclid.mod` | [Euclid](#euclid-pad-2) | yes | yes |
| 3 | `03life.mod` | [Life](#life-pad-3) | yes | yes |
| 4 | `04rain.mod` | [Rain](#rain-pad-4) | yes | yes |
| 5 | `05balls.mod` | [Balls](#balls-pad-5) | yes | yes |
| 9 | `09pulse.mod` | [Pulse](#pulse-pad-9) | yes | no |
| 10 | `10stars.mod` | [Stars](#stars-pad-10) | no | no |
| 17 | `17snake.mod` | [Snake](#snake-pad-17) | no | no |
| 18 | `18lights.mod` | [Lights Out](#lights-out-pad-18) | no | no |
| 19 | `19mole.mod` | [Whack-a-mole](#whack-a-mole-pad-19) | yes | no |

"Clock: yes" means that the module follows the sequencer. Push **Play** to start it.

## Put the modules on the card

1. Power the unit off and remove the microSD card. The USB drive of the unit does not show the card, so use a card
   reader.
2. At the root of the card, make a folder `Modules`. The name is not case-sensitive.
3. Copy the `.mod` files into that folder. Keep the file names: the number at the start sets the pad.
4. Put the card back and power the unit on. The card must be present at power-on.

The loader only reads the card. It does not write to the card and does not change your projects or samples.

## Start and stop a module

* **Shift + Sample Rec** opens the picker. Pad 1 is top left. Pads 1 to 8 are the first row. A pad with a file comes
  on in its own colour. Pads without a file stay dark.
* Push a lit pad to start that module. The pad that loads blinks white. A red pad means that the file failed its
  checks. The module did not start.
* **Shift + Sample Rec** in the picker closes the picker.
* **Shift + Sample Rec** while a module runs stops the module. The normal lights and controls come back at once.

No module stops by itself. No module starts by itself at power-on.

## While a module runs

* **Play** always starts and stops the transport, also while the picker is open.
* A module that follows the clock waits for **Play**. While it waits, the Play button blinks dim green. When the
  transport stops, its clock-driven activity stops and its notes are released. The module stays open with idle lights.
* Most modules take the pads, the buttons and the macros. **Shift**, Shift + Sample Rec, the master filter and the
  master volume stay normal.
* Pulse and stars do not take the controls. The pads, buttons and macros work as usual, but the lights show the
  module.
* Except in Paint, each value step of a module macro needs four counts of the encoder. Small bounces of the knob
  do not change the value. Paint uses the encoder counts directly.

## Notes from the modules

Euclid, life, rain and balls play notes on a target track that you select. These rules apply to all four:

* The notes come from the Note view keyboard of the target track. The module uses the notes that the keyboard shows
  in the two octaves from the original pitch of the sample up. Degree 0 is the lowest of these notes. The higher
  degrees go up through these notes. After the highest note, the count starts again at the lowest note.
* With scales on (tracks 1 to 7), the notes are in the scale (see [scales.md](scales.md)).
* If the module cannot read the keyboard of the track (for example a slice layout), it plays no note on that track.
* Each track plays one module note at a time. A new note on the same track ends the note before it.
* The notes play the sound of the track only. They do not go to MIDI out, and the pattern does not record them.
* On track 8, arrhythmia-firmware plays the synth (see [synth.md](synth.md)).

Paint plays notes in a different way (see [Paint](#paint-pad-1)).

## Paint (pad 1)

Pads light where you push them, then fade. Each pad also plays a note.

* **Pads.** A push lights the pad in the selected colour. The light then fades.
* **Sound.** A push plays note 60 (the sample at its original pitch) on the track of the pad's column: column 1 plays
  track 1, column 8 plays track 8. The velocity is half the velocity of the push. The release of the pad sends the
  note off. A one-shot sample plays to its end.
* **Macro 1:** colour. Turn it to go round the colour wheel.
* **Macro 2:** trail length. Clockwise makes the trail longer, from approx. 0.5 s to approx. 16 s in 28 steps. Start
  value: approx. 1.5 s.
* The other buttons stay dim white.
* Paint does not follow the clock. It runs as soon as it starts.

## Euclid (pad 2)

Four rhythm rows of up to 8 steps. Each row spreads its hits as evenly as possible over its steps (a Euclidean
rhythm).

* **Pads.** Each pad row is one rhythm row. Push any pad of a row to select that row.
* **Macros 1 to 6** change the selected row:

  | Macro | Value | Range | Start value |
  |---|---|---|---|
  | 1 | hits | 0 to the number of steps | row 1: 1, row 2: 2, row 3: 3, row 4: 4 |
  | 2 | steps | 1 to 8 | 8 |
  | 3 | rotation | 0 to steps - 1 | 0 |
  | 4 | target track | 1 to 8 | row 1: track 1 to row 4: track 4 |
  | 5 | degree | 0 to 20 | 0 |
  | 6 | velocity | 1 to 127 | 96 |

* Rotation moves the hits to later steps.
* A change of a value releases the notes that sound.
* **Clock.** Push Play to start. One step is a sixteenth note. A note lasts half a step. Each bar starts all rows at
  their first step. A row with fewer than 8 steps repeats in its own length in the bar.
* If two rows play on the same track on the same step, the upper row plays.
* **Lights.** While the transport runs, the hits are lit, the current step is white and the selected row has a blue
  tint. Steps past the row length have no hit light, but keep the dim background and selected-row tint. While the
  transport is stopped, the rows show dimly and pulse.

## Life (pad 3)

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

## Rain (pad 4)

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

## Balls (pad 5)

Up to four balls bounce on the pads. A ball that hits a wall plays a note.

* **Pads.** Push a pad to add a ball there. With four balls, a push replaces one ball, in turn.
* **Clear** puts the balls back at their start positions. The macro values stay.
* **Macro 1:** speed. One movement each half note, quarter note, eighth note or sixteenth note (start value).
  Clockwise is faster.
* **Macro 2:** gravity, 0 to 4 (start value 1).
* **Macro 3:** number of balls, 1 to 4 (start value 2).
* **Macro 4:** target track, 1 to 8 (start value 1).
* **Clock.** Push Play to start. The cell of the hit sets the degree: pad 1 is degree 0, pad 2 degree 1, and so on.
  The velocity is 96. A note lasts half a movement. If balls hit walls at the same time, one note plays.
* **Lights.** Each ball has its own colour. Clear is lit.
* While the transport is stopped, the balls stay still.

## Pulse (pad 9)

A light show on the beat. Pulse makes no sound.

* Pulse does not take the controls. Play the unit as usual: the pads, buttons and macros work as normal.
* **Clock.** Push Play to start. While the transport is stopped, all pads pulse dimly.
* Pulse has three patterns:
  * all pads flash on each beat;
  * one column lights on each eighth note, from left to right;
  * one row lights on each sixteenth note, from top to bottom.
* The pattern changes every four bars. The colour changes every bar: red, yellow, green, cyan, blue, magenta.

## Stars (pad 10)

Stars that come on slowly and fade out slowly, on all the lights of the unit. Stars makes no sound.

* Stars does not take the controls. The pads play the tracks as usual.
* Stars does not follow the clock. It runs as soon as it starts.

## Snake (pad 17)

The snake game. Snake makes no sound.

* The game starts paused. The head of the snake blinks. Push a pad to start.
* **Pads.** Push a pad on the side of the head where you want to go. The larger distance (row or column) sets the
  direction. With equal distances, the snake goes up or down. The snake turns once for each move. It cannot turn
  back on itself.
* **Macro 1:** speed. Clockwise is faster. The light of macro 1 shows the speed. Snake follows the light frames of
  the unit, not the tempo.
* Orange is the food. Green is the snake; the head is bright. The snake grows when it eats.
* The game ends when the snake hits a wall or its own body. It can move into the cell that its tail leaves.
* **Game over.** The pads flash red. The green pads show the score: one pad for each food eaten.
* **Win.** The snake fills the board. The other pads flash blue.
* After approx. half a second, push a pad to start a new game. The speed stays.
* Snake does not follow the clock.

## Lights Out (pad 18)

The Lights Out puzzle. Lights Out makes no sound.

* **Pads.** Push a pad to change it and the pads above, below, left and right of it. The edges do not wrap. A push
  flashes white for a moment.
* The aim: turn all pads off. Lit pads are orange.
* **Clear** starts a new puzzle. Each puzzle is made from the solved board, so it always has a solution.
* **Rec.** Hold Rec to see the number of pushes, 00 to 99, as two digits. Rec is lit green. At 99 or more pushes,
  Rec is red and the count stays at 99.
* **Win.** All pads are green, in turn with the count. This stays until you push Clear.
* Lights Out does not follow the clock.

## Whack-a-mole (pad 19)

Hit the yellow pad before the next one comes. Whack-a-mole makes no sound.

* **Clock.** Push Play to start. A new yellow pad comes on at each interval. It is never the same pad two times in
  sequence.
* **Pads.** Push the yellow pad to score. Each target scores once. While a target is active, a push on another pad
  counts a miss. After a hit, pad pushes are ignored until the next target. A target that you do not hit counts a
  miss when the next one comes.
* **Macro 1:** interval: a half note, a quarter note (start value), an eighth note or a sixteenth note. Clockwise is
  faster.
* **Clear** sets the score and the misses to 0.
* **Rec.** Hold Rec to see the score as two green digits.
* **Duplicate.** Hold Duplicate to see the misses as two red digits.
* The counts stop at 99.
* Start and stop remove the target without a miss.
