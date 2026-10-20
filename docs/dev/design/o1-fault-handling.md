# DSP fault handling: bounded waits

In both Arrhythmia 0.1 images. Source: `patches/src/o1wait.c`. Marks as in [../README.md](../README.md).

## Policy

How we know: our design; the unbounded stock waits found by static analysis (kept to what is needed to explain the
fault report).

Rule for 0.1: **audio may degrade, but the ARM must never hang.** On stock firmware some ARM operations wait for an
answer from the DSP without any bound (sample recording start and stop, trim commit, sample pack reset and sample
uploads, bulk reads). If the DSP stops answering, the unit's input, timers and sequencer stop with it.

Arrhythmia bounds every such wait:

* **Bound: 500 ms per wait.** This is a policy value, not a measured handshake time (no handshake time has been
  measured). A block is 0.667 ms, so 500 ms is about 750 blocks. A backstop also ends a wait after a fixed number of
  polls if the 1 ms tick stops.
* **Sticky fault.** The first timeout sets a fault that only a reboot clears. A DSP that answers later does not clear
  it; late answers are counted.
* **Abort.** The operation whose wait timed out stops at its own cleanup path before it sends anything more to the
  DSP. The command that timed out is not withdrawn: a DSP that resumes may still act on it.
* **Quarantine.** While the fault is set, nothing new that depends on the DSP's file service is sent: sample
  recording, trim, preview, sample memory allocation and freeing, pack reset, sample uploads and bulk reads are
  refused and fail the way the stock firmware already fails them ("sample memory exhausted", a failed pack load).
* **Pack failed.** If an upload was aborted, the pack is marked failed: every slot reads as empty and **new sample
  note-ons are refused** on every path (pads, MIDI, sequencer, modules, metronome). Note offs and Stop still work.
  Voices already sounding play out.
* **Recovery is a reboot.** The stock boot reloads the DSP and waits for it, as before.
* Nominal behaviour is unchanged: with a healthy DSP the ARM makes identical reads and writes in the same order as
  stock (EMU).

## What the user sees

How we know: our design; the healthy case observed on hardware.

* **Rec blinks white at about 2 Hz** while the fault is set. Save if needed, then power-cycle.
* A refused operation is a failed operation: a recording that does not start, a pack that does not load, samples that
  show as empty.
* Before any flash, check that Rec is not blinking and the `0D` report shows fault 0.

## The `0D` report

How we know: our design.

    F0 00 20 29 00 60 0D F7
    -> F0 00 20 29 00 60 0D <fault> <last site> <timeouts 3x7> <site mask 2x7> <refused 3x7> <late 3x7> <pack failed> F7

| Field | Meaning |
|---|---|
| fault | 1 = a guarded wait timed out since boot |
| last site | the site of the last timeout; bit 6 set = ended by the backstop |
| timeouts | number of timeouts |
| site mask | bit n set for every site n that timed out |
| refused | operations refused while faulted |
| late | answers that arrived after their wait had ended |
| pack failed | 1 = samples read as empty and new sample note-ons are refused |

Sites: 1 pack reset phase 1, 2 recording stop, 3 recording start, 4 trim commit, 5 pack reset phase 2, 6 and 7 bulk
read on core 0 and core 1, 8 enter bulk mode on core 1, 9 leave bulk mode on core 1, 10 enter or leave on core 0.

Reading it: fault 1 proves that a guarded operation timed out, not which core stopped or why. Fault 0 proves that no
guarded operation timed out, not that the DSP is alive: playback alone may make no guarded request.

## Hardware procedure

How we know: run on one unit with arrhythmia-firmware (musical stress PASS); deliberate fault containment NOT TESTED.

Prerequisites: a NOR backup and a copy of the card ([../backup-and-recovery.md](../backup-and-recovery.md)), a
disposable project slot.

1. Use a disposable project and note the hash of its saved file. Start at modest volume. Send `0D` and record the full
   reply (fault 0, timeouts 0).
2. Play the busiest seven-sample-track scene you can build: sustained and high-pitched samples together, dense
   microsteps, delay, reverb and sidechain on. Record the settings. This is a stress scene, not a certified worst case.
3. On arrhythmia-firmware set track 8 to four notes per press and send note 60, velocity 100, on track 8's MIDI
   channel every 125 ms for 60 s, each note off 60 ms later (or hold and retrigger a chord by hand, and say so).
4. While it plays, press pads, switch views, turn macros, and change scale and root with all seven tracks scaled.
   Then send the final note offs and press Stop. Record glitches, LED and transport responsiveness, and the `0D` reply.
5. Save into the disposable slot, reload, compare with what you intended.
6. Only if a fault occurred (`0D` fault 1, Rec blinking): start a 2 s recording, drag a trim point, switch the sample
   pack. Pass: each returns, the UI stays live, no half take, no partial pack.
7. Power-cycle. Pass: normal boot, Rec not blinking, `0D` clean.

Fail: any freeze longer than about 2 s, a watchdog reset, Stop ignored, a save that never completes, a reload that
differs beyond the intended edits, a partial take or pack after a fault, or a fault that clears without a reboot.

**Deliberate fault containment is not provided by this procedure.** No known front-panel or MIDI sequence makes the
DSP miss its deadline, and heavy MIDI traffic is no proof of an overrun. Testing containment needs a separately
reviewed diagnostic image or a debugger. If the procedure stays healthy, report "musical stress PASS, deliberate fault
containment NOT TESTED".

## Limits

* Each single transfer on the host bus is assumed to complete (its interrupt fires); the bound covers waiting for the
  DSP's answer, not the bus itself.
* A timed-out command left pending is not claimed harmless if the DSP resumes and runs it late.
