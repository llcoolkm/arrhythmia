# DSP: memory, budget and measured load

What room the DSP leaves for new code, and how busy it is on the unit. Marks as in [README.md](README.md). Addresses
are DSP words. "Core 0" and "core 1" are the ARM's select 0 and 1; that select 0 is physical core 0 is likely
(it owns the audio port) but not proven.

## Budget per block

How we know: computed from the DSP's clock divider settings and the reference manual (needed to size DSP code), and
confirmed by the period the meter measured on hardware.

* Audio is processed in **blocks of 32 stereo frames**.
* The audio port's bit clock is the DSP system clock divided by 80, and a frame is 2 slots of 32 bits, so one frame is
  5,120 DSP cycles and **one block is 163,840 DSP cycles**, whatever the crystal (STATIC).
* At 48 kHz a block lasts 0.667 ms; the absolute crystal frequency was not established.
* The meter's block period read 159,170 .. 169,612 cycles on the unit (build 6011), consistent with 163,840 (HW).

## Memory and what Arrhythmia uses

How we know: the memory switch configuration and the reference manual (STATIC); our own layouts from the build
manifests of our images (DESIGN); core 1's X contents observed on hardware.

| | Core 0 | Core 1 | Mark |
|---|---|---|---|
| Memory switch mode | P 0..0x3FFF, X 0..0x5FFF, Y 0..0x3FFF | P 0..0xFFF, X 0..0x2FFF, Y 0..0x1FFF | STATIC |
| X cleared at boot | yes | **no**: core 1 X holds power-up garbage, so clear what you use | HW |

Core 1 program memory is the scarce resource. On stock 5706, which has no meter, the tail P:0xD32..0xFFF (718 words)
is not loaded (STATIC, EMU). Each Arrhythmia image puts its own core 1 code there (0.1.0 images: devkit 7228, firmware
7229):

| Image | Our code in the core 1 tail | Words used | Words left |
|---|---|---|---|
| arrhythmia-devkit 7228 | the load meter, P:0xD32..0xE78 | 327 | 391 |
| arrhythmia-firmware 7229 | the track 8 synth, P:0xD32..0xFF9 (710 words of voice code, 2 of fail-stop) | 712 | 6 |

The two never share an image: 7228 has the meter and no synth, 7229 the synth and no meter.

Code and data allocations (DESIGN):

| Image | Core | Range | Use |
|---|---|---|---|
| arrhythmia-devkit 7228 | 0 | P:0x1FED..0x213D (337 words); X:0x5C00..0x5E3F | meter code; meter ring, summary and work words (below) |
| arrhythmia-devkit 7228 | 1 | X:0x2C00..0x2E3F | meter ring, summary and work words (below) |
| arrhythmia-firmware 7229 | 1 | X:0x2E40..0x2E6B (44 words) | synth voice state |
| arrhythmia-firmware 7229 | 1 | X:0x2E70 | synth ENABLE word (loaded as 0; the ARM sets it once the synth may run) |
| arrhythmia-firmware 7229 | 1 | X:0x2FD0..0x2FE7 (24 words) | synth command records |
| arrhythmia-firmware 7229 | 1 | Y:0x1500..0x18FF (1,024 words) | four saw tables |
| arrhythmia-firmware 7229 | 1 | one interrupt vector word | SHI receive-overrun fail-stop entry (not part of the synth) |

The 69 synth X words are written to zero by the synth's load records, because core 1 does not clear X at boot.

* External-memory bandwidth on the Rhythm is not known. Keep new voices' state and tables in internal RAM.
* Core 0 waits for core 1 inside every block (the meter measures that wait, HW). Work added to core 1 increases core
  0's wait only once core 1's block work exceeds what core 0's own concurrent processing hides; below that point it
  does not add to core 0's wait.

## Measured load

How we know: measured on hardware with the meter below, one unit, read after each scene was stopped. **Build 6011**
(a devkit test build with the meter), not 7229: these are development measurements, not acceptance results for any
release image. Percent of the 163,840-cycle block.

| Scene (build 6011) | Core 0 window mean | Core 0 worst window | Core 1 window mean | Core 1 worst window |
|---|---|---|---|---|
| idle | 40.9 % | 41.3 % | 1.2 % | 1.2 % |
| clock module, 3 tracks | 40.9 % | 41.3 % | 2.0 % | 8.2 % |
| 8 tracks busy | 41.2 % | 47.4 % | ~11 % | 18.2 % |
| 8 tracks, high pitch | 41.3 % | 50.7 % | ~10 % | 21.6 % |
| 8 tracks, reverb + delay + sidechain on | 41.5 % | 49.7 % | ~10.4 % | 20.5 % |
| heaviest project: 3 patterns, all tracks, microsteps, probability | 44.5 % | 54.5 % (lifetime 55.1 %) | ~14.9 % | 25.0 % (lifetime 25.8 %) |

* Core 0 is a near-constant 41 %: its processing, effects included, runs every block whether used or not. Turning
  reverb, delay and sidechain on costs about 0.2 points.
* Core 0's peaks above that are mostly waiting for core 1 (worst wait 23,196 cycles in the heaviest scene).
* Core 1 scales with voice activity (note starts, microsteps); high pitch costs little.
* Worst block seen: core 0 at 55.1 % (73,644 cycles free), core 1 at 25.8 % (121,500 free).
* An emulator without external-memory wait states reads core 0 about 10 points lower; use hardware figures.

Rough sizes for new voices (our estimates): a subtractive voice (one band-limited oscillator, a 2- or 4-pole filter,
envelope, amplifier) costs about 1,000 to 1,600 instructions per block; eight of them 5 to 8 % of a block. A
three-voice SID-style engine is 2 to 4 % at 48 kHz.

### What was tested on 7229

arrhythmia-firmware 7229 carries no meter, so none of the figures above are from it. On one unit 7229 was tested for
scales (install, no DSP fault, scales, save and power cycle, track 8 unaffected; HW). Its DSP code was tested on that
unit, in a development build that differs from 7229 only in the scales code and the build number, for listening, musical stress,
save and power cycles and a 1 hour soak (HW). **The soak's audio result is inconclusive**:
no reference take on stock firmware and no close listening were done. **Deliberate DSP fault containment is not
tested** ([design/o1-fault-handling.md](design/o1-fault-handling.md)).

## The load meter (arrhythmia-devkit)

How we know: our design; emulated, and read on hardware.

The meter is our DSP code (`patches/dspmeter.lst`, built by `tools/dspmeter.py`). It runs on both cores.

* Each core reads its own timer 1, which counts at half the DSP clock, at block start and end: busy cycles =
  2 x ticks, 2-cycle resolution (STATIC; the period check above confirms the factor, HW).
* **Core 0 busy** covers its whole block, including its wait for core 1; the wait is also kept on its own (WAIT), so
  busy minus wait is core 0's own work. **Core 1 busy** runs from its start signal to its completion signal.
  **Period** is the time between two block starts.
* Per core: block count (saturating at 0xFFFFFF, after about 3.1 h), minimum, maximum, 48-bit sum, WAIT maximum and
  sum (core 0), period minimum and maximum, skipped measurements. Every window of 2,048 blocks (1.37 s) it writes the
  window maximum and mean into a 256-entry ring (5.8 min of history) and publishes a summary.
* Code: core 0 P:$1FED..$213D (337 words), core 1 P:$D32..$E78 (327 words), as in the tables above. Data: X
  windows below. The layer adds a record of zeros for core 1's X, which the boot does not clear.
* Cost: under 0.25 % of a block in total; 9 cycles on core 1's critical path (EMU). Stock audio is bit-identical with
  and without the meter in every emulated scene (EMU).

| Core | Ring | Summary | Work |
|---|---|---|---|
| 0 | X:$5C00..$5DFF | X:$5E00..$5E0F | X:$5E10..$5E3F |
| 1 | X:$2C00..$2DFF | X:$2E00..$2E0F | X:$2E10..$2E3F |

Summary words, lowest address first: `END, MAGIC 0x4D5452, ID (1 << 8 | core), CNT, MAX, MIN, SUMH, SUML, WMAX,
WSUMH, WSUML, PMAX, PMIN, WIN, SKIP, BEGIN`. BEGIN is written first and END last, and a read returns words in
ascending order, so a reader that sees END = BEGIN and the magic has one consistent publication. One 16-word read
takes about 1.8 ms, longer than a block, which is why the summary is published once per window. The first core 1
block is skipped (SKIP = 1 is normal).

Reading:

    uv run python -I tools/dspload.py /dev/snd/midiC<card>D0 --unit-idle [--out meter.json]

The tool reads the summary, the ring (512 words) and the summary again, and drops ring slots that could have been
rewritten in between. **Read only while the unit is idle** (transport stopped, nothing loading); the tool refuses
without `--unit-idle`. For clean lifetime figures power-cycle between scenes, or use the ring (newest windows first).
