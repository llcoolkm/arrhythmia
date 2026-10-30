# Arrhythmia modules for developers

Applies to Arrhythmia 0.2.1 (arrhythmia-firmware build 7329 and arrhythmia-devkit build 7228,
unchanged from 0.1.0). Test status is in the [release notes](../../RELEASE-NOTES-0.2.1.md). For the 0.1.1
image 7261, see the [0.1.1 release notes](../../RELEASE-NOTES-0.1.1.md). Modules are a developer feature: most users never
need them.

A module is a small program in C that runs on the unit. It can take over the pads, buttons and lights, play notes,
follow the sequencer clock, change track settings and send MIDI.

The developer reference in [../dev/README.md](../dev/README.md) has the details behind this page: formats,
SysEx commands with their replies, hardware measurements and the DSP budget.

## Interoperability

Modules are why Arrhythmia has an interface at all: they let your own code work with the unit.

* **Our interface on the existing transport.** The module ABI and the added developer commands in this guide are
  designed and documented by us. They use the unit's existing USB MIDI and SysEx transport, which is not ours.
* **Selected hooks and calls.** Our patches integrate with the stock firmware through selected hooks, calls and
  object checks. In our source each such address carries a short note on the role it plays in our code.
* **Read-back limited.** The built-in SysEx 7D handler serves module memory (ITCM) only and refuses direct reads of
  the bootloader and application flash. This is not a security boundary: a module runs as native code on the unit.
* **Nothing else published.** No stock code, no disassembly and no maps of the stock firmware's internals.

## Which image

Both images have the module loader, the SD card picker, the development SysEx commands and ABI 3 (below).

| | arrhythmia-firmware (build 7329) | arrhythmia-devkit (build 7228) |
|---|---|---|
| Modules, picker, ABI 3 | yes | yes |
| Picker layout | numbered pads (`NNname.mod` on pad NN), no built-in module | built-in paint on pad 1, card files on pads 2 to 32 |
| Play while a module runs | always to the transport | to the module, if the module owns the controls |
| DSP load meter (SysEx 08) | no: every request gets the refusal reply (status 0) | yes |
| Track 8 | 4-operator FM synth with its synth page ([synth.md](synth.md)) | stock sample track |
| Scales on tracks 1 to 7 | yes, set on the synth page or with SysEx 0E | yes, set with SysEx 0E only |
| P-lock fix, DSP fault report (SysEx 0D) | yes | yes |

Install either with the same installer (see [install.md](install.md)). The two have different build numbers, so
switching always installs. Both are released in 0.2.1 and pinned by their SHA-256 (see the
[release notes](../../RELEASE-NOTES-0.2.1.md#install)). The test status of arrhythmia-firmware 7329 is in the
[release notes](../../RELEASE-NOTES-0.2.1.md#test-status). arrhythmia-devkit 7228 is unchanged from 0.1.0.
It was not tested on hardware; a development build that differs from it only in the scales code and the build
number had a short hardware test (install, version reply, no DSP fault, SysEx 7D restriction, DSP load meter read),
with no listening or stress test. Deliberate DSP fault containment is not tested in either image.

The DSP load meter gives per-core cycle counts of the DSP, read with SysEx 08. Read it only while the unit is idle
(transport stopped, nothing loading). `tools/dspload.py` reads and prints it. arrhythmia-devkit answers only for the
meter's own memory windows; any other read is refused (see "DSP load meter" below).

## DSP load meter (arrhythmia-devkit)

The meter runs on both DSP cores and counts cycles per audio block (32 frames). One block has a budget of 163,840
DSP cycles; free cycles are 163,840 minus busy.

* **Core 0 busy** covers its whole block, including the time it waits for core 1 to finish its part. That wait is
  also counted on its own (WAIT), so busy minus wait is core 0's own work.
* **Core 1 busy** covers its block from the start signal to its acknowledgement. Because core 0 waits for core 1,
  core 1's work also lengthens core 0's busy.
* **Period** is the time between two block starts. It should sit near 163,840; if it does not, the clock assumption
  is wrong and every figure scales with it.

Per core the meter keeps a count of blocks (saturating at 0xFFFFFF), minimum, maximum and a 48-bit sum of busy, the
WAIT maximum and sum (core 0), the period minimum and maximum, and a count of skipped measurements. Every window of
2,048 blocks (1.37 s at 48 kHz) it writes the window's maximum and mean (sum >> 11) into a 256-entry ring (5.8 min of
history) and publishes a summary.

Memory windows (DSP X memory, 24-bit words). These are the only addresses SysEx 08 serves on arrhythmia-devkit:

| Core | Ring | Summary | Work |
|---|---|---|---|
| 0 | X:$5C00..$5DFF | X:$5E00..$5E0F | X:$5E10..$5E3F |
| 1 | X:$2C00..$2DFF | X:$2E00..$2E0F | X:$2E10..$2E3F |

Summary words, lowest address first: `END, MAGIC 0x4D5452, ID (1 << 8 | core), CNT, MAX, MIN, SUMH, SUML, WMAX,
WSUMH, WSUML, PMAX, PMIN, WIN, SKIP, BEGIN`. The meter writes BEGIN (highest word) first and END (lowest) last, and
a read returns words in ascending order, so a reader that sees END equal to BEGIN and the magic has one consistent
publication (a sequence lock). One 16-word read takes about 1.8 ms on the unit, longer than one block, which is why
the summary is published once per window. `tools/dspload.py` reads the summary, the ring (512 words) and the summary
again, and drops ring slots that could have been rewritten in between.

SysEx 08: `F0 00 20 29 00 60 08 sel a2 a1 a0 n F7`, with `sel` 0 for core 0 X or 2 for core 1 X, address
`a2 << 14 | a1 << 7 | a0` and `n` words (1 to 16). The reply is `F0 00 20 29 00 60 08 sel a2 a1 a0 n status
<6 nibbles per word, MSB first> F7`, status 1 = read. The request is refused (status 0, no words, nothing touched)
unless `sel` is 0 or 2 and the whole range `[addr, addr + n)` lies inside one core's windows above. On
arrhythmia-firmware every SysEx 08 gets that refusal.

## How a module runs

* A module is loaded into a 15 KB area of fast RAM (ITCM) and runs from there.
* It defines up to four entry points. `frame` is required; the rest are optional.

  ```c
  void     init(uint8_t *leds);
  uint32_t frame(uint8_t *leds, uint32_t frame_no);              /* each LED frame; return 0 to stop */
  void     input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value);
  void     clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm);   /* 1 START, 2 PULSE, 3 STOP */
  ```

* `leds` is the unit's LED buffer. Which byte is which light and colour: [led-map.md](../led-map.md).
* While a module that has `input` runs, it owns the pads, buttons and encoders. Shift and the knobs stay stock.
* Keep every entry short. They run inside the unit's own tasks, and `clock` runs inside the sequencer tick.
* A module has no data sections and no relocations: state goes in a fixed RAM area, and tables are built by code.

### Input events

`input` gets one call per event while the module owns the controls: `kind` 1 pad (`value` = velocity | on << 8),
2 button (`value` = 1 down, 0 up), 4 pad pressure (`value`), 5 encoder (`value` = signed 16-bit step count). Indices:
[input-map.json](../input-map.json) (pads 0 to 31 by row, buttons 0 to 27, encoders 0 to 7).

### Clock events

`clock` follows the sequencer. `event` is 1 START, 2 PULSE or 3 STOP.

* `pos` = `pulse | step << 8 | beat << 16`: pulse 0 to 95 within the bar, step = pulse / 6 (0 to 15),
  beat = pulse / 24 (0 to 3). For STOP it is the last pulse delivered. It is the bar position, not the pattern step:
  patterns of 32 steps repeat it.
* `bpm` is the tempo in BPM (40 to 240 on the internal clock).
* START comes right before the first PULSE the module sees while it is stopped: on Play, or at the next pulse when
  the module starts while the sequencer already runs (then mid-bar).
* STOP comes in the unit's next main-loop pass after the transport stops. A Stop and a new Play within one such
  pass are not seen as STOP and START; resync from the pulse index.
* Stopping, restarting or replacing the module, or `frame` returning 0, turns its clock off; the next start begins
  stopped. A module that ends gets no STOP.
* A module that only follows Play and Stop needs no `input` entry. In arrhythmia-firmware 7329, Play always reaches
  the transport; a module with `input` also receives the Play events. It must not use Play as its own control.
  In the unchanged devkit 7228, an owning module receives Play instead of the transport.

## Building

Needs `clang` and LLVM tools (`llvm-readelf`, `llvm-objcopy`), and Python 3.12.

    python3 -I tools/build_module.py mymodule.c mymodule.mod

The tool compiles for the Cortex-M7, checks that the code is self-contained, and adds a header with a CRC-32. A module
with `clock` gets a `CRM2` header, any other module a `CRM1` header. The file is 26 bytes to 15 KB.

## Loading

From the microSD card:

The picker layout and colours below apply to arrhythmia-firmware 7329. The unchanged devkit 7228 has built-in
paint on pad 1 and up to 31 card files on pads 2 to 32, with its earlier picker layout.

1. Power the unit off and take the card out. The unit's USB drive does not show the card, so use a card reader.
2. Create a folder `Modules` (the name is not case-sensitive) at the card's root. Copy the module there with a short
   name ending in `.mod`, for example `stars.mod`. Case does not matter: the picker lists the 8.3 short name.
3. Put the card back and power the unit on. The card must be present at power-on.
4. **Shift + Sample Rec** opens the picker. The pads that have a file come on, each in its own colour. A red pad
   means the file failed its checks; nothing was started. If there is no card or no module file, no pad comes on.
   Push Shift + Sample Rec again to close the picker.
5. **Shift + Sample Rec** again stops the running module.

The modules of the release and their controls: [modules.md](modules.md).

The loader only reads the card. It never writes to it and never touches the unit's own projects and samples.

Naming: a file is listed when its 8.3 short name ends in `.MOD`; plain 8.3 names are best. A long name such as
`My Paint.mod` also works and appears under its short name (for example `MYPAIN~1.MOD`).

Numbered pads (arrhythmia-firmware 7329): a file `NNname.mod`, with NN = `01` to `32`, goes on pad NN. Pad 1 is top left, pads 1 to 8
are the first row. The name part is at most 6 characters, so that the name stays 8.3. Pads that have no file stay
dark. If two files have the same number, the one with the lowest name gets the pad. The other files with that number
and every file without a number (`00`, `33` to `99` and one digit count as no number) go on the free pads, lowest pad first, in name order.
So a card without numbers shows file 1 on pad 1, file 2 on pad 2, and so on. Up to 32 files are shown: the numbered
files first, then the other files by name (byte order, digits before letters), so the layout does not depend on
directory order.

Colours: each pad has a colour from its name (a hash of the name part, without the number and `.mod`), one of 11:
orange, yellow, lime, green, mint, cyan, sky, blue, violet, magenta, pink. If an earlier pad has that colour, the pad
gets the next colour that is free, until all 11 are used. Red means a failed file; the pad that loads blinks white.

In arrhythmia-firmware 7329, Play always goes to the unit's transport, in the picker and while a module runs.
A module that owns the controls also gets the Play button event. While a module with a clock entry waits for
START, the Play button blinks dim green.

Before 0.2.1, arrhythmia-firmware had a built-in module (paint) on pad 1, and pads 2 to 32 showed up to 31 card files.
In arrhythmia-firmware 7329, there is no built-in module. Paint is a card file, `01paint.mod`, in the release files.
The unchanged devkit 7228 keeps its built-in paint.
Folders, hidden and system files, and files outside 26 bytes to 15 KB are skipped. A module in fast RAM starts only
if it was loaded since power-on.

Over USB, for development: `tools/module.py` uploads a module, verifies it and starts it. Both images in the table keep the
development commands it needs (SysEx 04, 05, 06, 0A, 0B, and 7D for the read-back).

SysEx 7D (`F0 00 20 29 00 60 7D a4 a3 a2 a1 a0 F7`, 32 bytes per reply) reads only the module memory: ITCM
`0x00000100` to `0x00003FFF` (module RAM `0x100` to `0x3FF`, module code from `0x400`), 32-byte aligned, while ITCM is
enabled. Any other address, all internal flash included, gets the refusal `F0 00 20 29 00 60 7D a4 a3 a2 a1 a0 F7`
(the address echoed, no data).

## ABI 3: calling the host

The host table sits at the fixed flash address `0x0806A570`. Check it before calling anything:

| Word | Content |
|---|---|
| 0 | magic `'CRH1'` (0x31485243) |
| 1 | ABI version: 3 in Arrhythmia 0.1 |
| 2 | `host_note` |
| 3 | address of the built-in module; 0 in arrhythmia-firmware 7329; devkit 7228 keeps built-in paint |
| 4 | `host_note_port` |
| 5 | `host_param` |
| 6 | `host_ui` |
| 7 | `host_midi` |

Read words 4 to 7 only when word 0 is `'CRH1'` and word 1 is at least 3. On older builds those words are code, not
function pointers. Words 0 to 3 are frozen.

```c
uint32_t host_note(uint32_t t, uint32_t note, uint32_t vel);
uint32_t host_note_port(uint32_t t, uint32_t note, uint32_t vel, uint32_t rec);
uint32_t host_param(uint32_t t, uint32_t idx, uint32_t val);
uint32_t host_ui(void);
uint32_t host_midi(uint32_t msg);
```

* `t` is the track, 0 to 7 (track 1 to 8). `note` uses the engine's numbering: 60 plays a sample unpitched.
  `vel` 1 to 127 is a note on, 0 a note off.
* **host_note** plays a note on the track's sound only.
* **host_note_port** with `rec = 1` takes the same path as a pad press: the sound, MIDI out on the track's channel, and
  the pattern when Rec is armed and the transport runs. With `rec = 0` it acts like `host_note`.
* **host_param** sets one of the track's 25 parameters (`idx` 0 to 24, `val` 0 to 127). It applies at the next note.
* **host_ui** returns `track | view << 8 | front << 16 | 1 << 31`, or 0 if not known yet.
* **host_midi** sends one channel message, `status | d1 << 8 | d2 << 16`, to all MIDI outputs. A module that sends
  note ons must send its own note offs.
* All calls only queue the request and return 1 if it was queued. The queue has 8 slots, shared by all calls, and the
  unit handles them with at most 2 router calls per Main pass (no fixed rate per millisecond). Note offs are never
  lost.
* In arrhythmia-firmware, `host_note(7, ...)` and `host_note_port(7, ...)` play the track 8 synth (tested in
  emulation). In arrhythmia-devkit they play track 8's sample, as on stock.

## Examples

`stars`, `paint`, `chase` (lights and pads), `clock_demo` (sequencer clock), `abi3_demo` (ABI 3 calls) and `chord`
(chords spread over sample tracks 5 to 7). `modules/paint.c` is the source of `01paint.mod`. To build it:
`uv run python -I tools/build_module.py modules/paint.c build/01paint.mod`. In 0.2.0 and earlier, paint is also
compiled into both images as the built-in module. The ARM part of both images and the whole arrhythmia-devkit image
can be rebuilt from the published sources. The source of the DSP code of the 0.2 arrhythmia-firmware image 7323 is
in `tools/dspvoice207rel/`, so the full arrhythmia-firmware image can be rebuilt from source too, on Linux (tested on
Ubuntu x86-64 only). See
[the rebuild steps](../README.md#rebuilding-the-images) and
[tools/dspvoice207rel/README.md](../../tools/dspvoice207rel/README.md). `tools/dspvoice/` holds the DSP voice source of
the 0.1.1 image 7261.

`modules/stars.c` is published too. It is a light show with no sound: sparse stars that swell up and fade out slowly.
Build it with

    python3 -I tools/build_module.py modules/stars.c stars.mod

or take the prebuilt `stars.mod` (164 bytes) from the release page. Its SHA-256 is
`fe63caa0e36500b0150f9580ca356e36dbdd5f31ecfe522f26206b58f56c5c27`. Copy it to `Modules` on the card and pick it with
**Shift + Sample Rec**, as in [Loading](#loading).

The other example sources and a shared C header (SDK) are not published in Arrhythmia 0.1.

## Safety

A module runs with full access to the unit's memory. A faulty or malicious module can crash the unit or damage data.
Only run modules you trust, and keep backups. To get out of a hung module, power-cycle the unit; nothing starts a
module by itself at power-on.
