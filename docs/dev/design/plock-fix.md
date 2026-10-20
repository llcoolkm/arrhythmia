# P-lock carry-over fix

In both Arrhythmia 0.1 images. Source: `patches/src/plockfix.c`. Marks as in [../README.md](../README.md).

## The problem

How we know: emulated with the stock image (the behaviour this fix exists to change).

On stock 5706 a parameter lock (p-lock) on one step stays in effect on every later unlocked step until the knob is
turned. Example: macro 1 at 74, a lock of 20 on step 1, notes on steps 1 and 5: step 5 plays 20, not 74, and keeps
doing so on every bar. The lock overwrites the same value the knob writes, so after the locked step the knob's value
is gone. A saved project has the same problem: a save while a lock is in effect stores the lock as the track's value.

## Behaviour with the fix

How we know: our design; emulated. Not separately tested on hardware (the 0.1 hardware tests played projects with
locks, but did not check this case step by step).

**An unlocked step plays the knob's value (the base).** Per track the fix covers 12 lock lanes: the eight macros and
four mixer lanes (parameters 16 to 19, [../maps.md](../maps.md)).

| Case | Result |
|---|---|
| macro 1 = 74, lock 20 on step 1, notes on 1 and 5 | 20 on step 1, 74 again from step 2, every bar |
| all 12 lanes locked on one step | each restored to its own base on the next step; macros 2 and 3 with their fine part |
| knob turned while a lock is in effect | the turn edits the base, with the knob's own mapping (Tune snapping, Slope half steps, coarse and fine for Start and Length); the base takes effect at the next unlocked step |
| stopped, Rec + held step + knob | edits the step's lock, as stock; the base is unchanged |
| lock on step 16 | restored on step 1 of the next bar |
| pattern chain | lock to lock across the boundary without a restore in between |
| save while a lock is in effect | the file stores the base (stock stored the lock); reload plays the lock on its step and the base elsewhere |
| microsteps | lock sends identical to stock's, the base after the last one |
| Clear + knob (clear the lane) | the lane is cleared; the base comes back on the next pulse |
| project load, clear or pack switch | every held lane is dropped: the new project's values apply |
| module `host_param` on a held lane | becomes the new base, as a knob turn |

## How it works

How we know: our design. The hook points are patch points of the published patches (`tools/build_lightshow.py`,
`patches/src/arrhythmia.c`).

* **State.** One byte per track and lane: "the engine holds a lock value here" plus the base it replaced (7 bits), and
  for the 14-bit lanes the fine part of the base. Only lanes holding a lock need a base: otherwise the engine value
  is the base.
* **Capture.** When a lock reaches the engine (from the pattern player, or from a lock-edit preview), the fix reads the
  track's current value first and keeps it as the base. A normal write (knob, MIDI CC) ends the hold, because the
  engine then holds the new base.
* **Restore.** After the stock pattern player has run for a pulse, every lane that still holds a lock, whose step has
  no lock in any microstep and on which the player sent nothing this pulse, gets its base sent through the player's
  own lock path: engine, display and MIDI out exactly as a lock of that value. Locked steps are never touched, so
  microstep timing and interpolation stay stock.
* **Knob on a held lane.** The fix gives the knob page the base instead of the lock for the duration of the stock knob
  handler, so the handler's own mapping applies to the base. A later version that subtracted values instead failed
  at the range limits; the stock handler's mapping is the only mapping.
* **Save.** After the stock code copies the track values into the project, the fix writes the bases of held lanes
  into the copy (with the stock value encoding). The live values are not touched.
* **Cost.** About 175 emulated instructions per player per pulse with nothing held, about 290 with all 96 lanes held
  (12 to 20 microseconds per pulse for 8 tracks, estimate). 64 bytes of RAM plus 104 bytes in the track-modes block.

## Limits

* A lock writer the fix does not classify as a lock preview counts as a base write. That path then keeps the stock
  carry-over for its lane: it fails toward stock, never toward losing a knob edit.
* Knob writers other than the macro and mixer pages are not redirected; a turn there on a held lane computes from the
  lock as on stock, and that value becomes the base.
* A held lane survives only within a project; the first project refresh clears it.
* Listening for zipper noise or double sends on restore was not done specifically (one extra parameter write per lane
  per lock run).
