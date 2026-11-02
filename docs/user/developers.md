# Circus Arrhythmia FM modules for developers

Applies to Circus Arrhythmia FM 0.2.2 (firmware build 7339 and arrhythmia-devkit build 7228,
unchanged from 0.1.0). Test status is in the [release notes](../../RELEASE-NOTES-0.2.2.md). For the 0.1.1
image, see the [0.1.1 release notes](../../RELEASE-NOTES-0.1.1.md).

A module is a small program in C that runs on the unit. It can take control of the pads, buttons and lights, play
notes, follow the sequencer clock, change track settings and send MIDI.

The developer reference in [../dev/README.md](../dev/README.md) has the details behind this page: formats,
SysEx commands with their replies, hardware measurements and the DSP budget.

## Interoperability

Modules let your own code operate with the unit.

* **Arrhythmia interface on the existing transport.** The Arrhythmia project designs and documents the module ABI
  and the added developer commands in this guide. They use the existing USB MIDI and SysEx transport of the unit.
* **Selected hooks and calls.** The Arrhythmia patches connect to the stock firmware through selected hooks, calls
  and object checks. In the Arrhythmia source, each such address has a short note on its role.
* **Read-back limited.** The built-in SysEx 7D handler serves module memory (ITCM) only. It refuses direct reads of
  the bootloader and application flash. This is not a security boundary: a module runs as native code on the unit.
* **Nothing else published.** No stock code, no disassembly and no maps of the internals of the stock firmware.

## Which image

Both images have the module loader, the SD card picker, the development SysEx commands and ABI 3 (below).

| | arrhythmia-firmware (build 7339) | arrhythmia-devkit (build 7228) |
|---|---|---|
| Modules, picker, ABI 3 | yes | yes |
| Picker layout | numbered pads (`NNname.mod` on pad NN), no built-in module | built-in paint on pad 1, card files on pads 2 to 32 |
| Play while a module runs | always to the transport | to the module, if the module owns the controls |
| DSP load meter (SysEx 08) | no: every request gets the refusal reply (status 0) | yes |
| Track 8 | 4-operator FM synth with its synth page ([synth.md](synth.md)) | stock sample track |
| Scales on tracks 1 to 7 | yes, set on the synth page or with SysEx 0E | yes, set with SysEx 0E only |
| P-lock fix, DSP fault report (SysEx 0D) | yes | yes |

Both images install with the same installer ([install.md](install.md)). The two have different build numbers, so a
change between them always installs. Both are released in 0.2.2 and pinned by their SHA-256
([release notes](../../RELEASE-NOTES-0.2.2.md#install)). arrhythmia-devkit 7228 is unchanged from 0.1.0.

The DSP load meter gives per-core cycle counts of the DSP, read with SysEx 08. Read it only while the unit is idle
(transport stopped, nothing loading). `tools/dspload.py` reads and prints it. arrhythmia-devkit answers only for the
memory windows of the meter. It refuses all other reads ([DSP load meter](#dsp-load-meter-arrhythmia-devkit)).

## DSP load meter (arrhythmia-devkit)

The meter runs on both DSP cores and counts cycles per audio block (32 frames). One block has a budget of 163,840
DSP cycles. Free cycles are 163,840 minus busy.

* **Core 0 busy** covers its whole block, including the wait for core 1 to finish its part. The meter also counts
  that wait separately (WAIT). Busy minus wait is the own work of core 0.
* **Core 1 busy** covers its block from the start signal to its acknowledgement. Core 0 waits for core 1, so the
  work of core 1 also increases the busy count of core 0.
* **Period** is the time between two block starts. The expected value is 163,840. A different value means that the
  clock assumption is wrong, and all figures scale with it.

Per core, the meter keeps:

* a count of blocks (saturating at 0xFFFFFF), and the minimum, maximum and 48-bit sum of busy;
* the WAIT maximum and sum (core 0);
* the period minimum and maximum;
* a count of skipped measurements.

For each window of 2,048 blocks (1.37 s at 48 kHz), the meter writes the maximum and mean (sum >> 11) of the window
into a 256-entry ring (5.8 min of history) and publishes a summary.

Memory windows (DSP X memory, 24-bit words). SysEx 08 serves only these addresses on arrhythmia-devkit:

| Core | Ring | Summary | Work |
|---|---|---|---|
| 0 | X:$5C00..$5DFF | X:$5E00..$5E0F | X:$5E10..$5E3F |
| 1 | X:$2C00..$2DFF | X:$2E00..$2E0F | X:$2E10..$2E3F |

Summary words, lowest address first: `END, MAGIC 0x4D5452, ID (1 << 8 | core), CNT, MAX, MIN, SUMH, SUML, WMAX,
WSUMH, WSUML, PMAX, PMIN, WIN, SKIP, BEGIN`.

* The meter writes BEGIN (highest word) first and END (lowest word) last. A read returns words in ascending order.
  A read with END equal to BEGIN and the correct magic is one consistent publication (a sequence lock).
* One 16-word read takes approximately 1.8 ms on the unit, longer than one block. For this reason, the meter
  publishes the summary once per window.
* `tools/dspload.py` reads the summary, the ring (512 words) and the summary again. It drops ring slots that can
  have changed between the reads.

SysEx 08: `F0 00 20 29 00 60 08 sel a2 a1 a0 n F7`, with `sel` 0 for core 0 X or 2 for core 1 X, address
`a2 << 14 | a1 << 7 | a0` and `n` words (1 to 16). The reply is `F0 00 20 29 00 60 08 sel a2 a1 a0 n status
<6 nibbles per word, MSB first> F7`, status 1 = read. The unit refuses the request (status 0, no words, nothing
touched) unless `sel` is 0 or 2 and the whole range `[addr, addr + n)` is inside the windows of one core above. On
arrhythmia-firmware, every SysEx 08 gets that refusal.

## How a module runs

* The unit loads a module into a 15 KB area of fast RAM (ITCM). The module runs from there.
* A module defines up to four entry points. `frame` is necessary. The other entry points are optional.

  ```c
  void     init(uint8_t *leds);
  uint32_t frame(uint8_t *leds, uint32_t frame_no);              /* each LED frame; return 0 to stop */
  void     input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value);
  void     clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm);   /* 1 START, 2 PULSE, 3 STOP */
  ```

* `leds` is the LED buffer of the unit. The byte of each light and colour: [led-map.md](../led-map.md).
* While a module with `input` runs, it owns the pads, buttons and encoders. Shift and the knobs stay stock.
* Keep each entry short. The entries run inside the tasks of the unit. `clock` runs inside the sequencer tick.
* A module has no data sections and no relocations. State goes in a fixed RAM area. Code builds the tables.

### Input events

`input` gets one call per event while the module owns the controls: `kind` 1 pad (`value` = velocity | on << 8),
2 button (`value` = 1 down, 0 up), 4 pad pressure (`value`), 5 encoder (`value` = signed 16-bit step count). Indices:
[input-map.json](../input-map.json) (pads 0 to 31 by row, buttons 0 to 27, encoders 0 to 7).

### Clock events

`clock` follows the sequencer. `event` is 1 START, 2 PULSE or 3 STOP.

* `pos` = `pulse | step << 8 | beat << 16`: pulse 0 to 95 within the bar, step = pulse / 6 (0 to 15),
  beat = pulse / 24 (0 to 3). For STOP, it is the last pulse delivered. It is the bar position, not the pattern
  step: patterns of 32 steps repeat it.
* `bpm` is the tempo in BPM (40 to 240 on the internal clock).
* START comes immediately before the first PULSE that the module receives while it is stopped. This occurs on Play,
  or at the next pulse when the module starts while the sequencer runs (then mid-bar).
* STOP comes in the next main-loop pass of the unit after the transport stops. A Stop and a new Play within one
  pass do not produce STOP and START. Resynchronise from the pulse index.
* A stop, restart or replacement of the module, or `frame` returning 0, turns its clock off. The next start begins
  stopped. A module that ends gets no STOP.
* A module that only follows Play and Stop needs no `input` entry.
* In arrhythmia-firmware 7339, Play always goes to the transport. A module with `input` also receives the Play
  events. It must not use Play as its own control. In devkit 7228, an owning module receives Play instead of the
  transport.

## Building

Requires `clang`, the LLVM tools `llvm-readelf` and `llvm-objcopy`, and Python 3.12.

    python3 -I tools/build_module.py mymodule.c mymodule.mod

The tool compiles for the Cortex-M7, checks that the code is self-contained, and adds a header with a CRC-32. A
module with `clock` gets a `CRM2` header. Other modules get a `CRM1` header. The file is 26 bytes to 15 KB.

## Loading

From the microSD card (picker layout and colours of arrhythmia-firmware 7339; devkit 7228 has built-in paint on
pad 1 and up to 31 card files on pads 2 to 32):

1. Power the unit off and remove the card. Put the card in a card reader. The USB drive of the unit does not show
   the card.
2. At the root of the card, make a folder `Modules` (not case-sensitive). Copy the module there with a short name
   that ends in `.mod`, for example `stars.mod`. Case is not important: the picker lists the 8.3 short name.
3. Put the card back and power the unit on. The card must be present at power-on.
4. **Shift + Sample Rec** opens the picker. The pads with a file come on, each in its own colour. A red pad means
   that the file failed its checks, and nothing started. With no card or no module file, no pad comes on. Shift +
   Sample Rec again closes the picker.
5. **Shift + Sample Rec** stops the running module.

The modules of the release and their controls: [modules.md](modules.md).

The loader only reads the card. It never writes to the card and never touches the projects and samples of the unit.

**Naming.** The picker lists a file when its 8.3 short name ends in `.MOD`. Plain 8.3 names are best. A long name
such as `My Paint.mod` shows under its short name (for example `MYPAIN~1.MOD`).

**Numbered pads** (arrhythmia-firmware 7339):

* A file `NNname.mod`, with NN = `01` to `32`, goes on pad NN. Pad 1 is top left. Pads 1 to 8 are the first row.
* The name part has a maximum of 6 characters, so that the name stays 8.3.
* Pads without a file stay dark.
* If two files have the same number, the file with the lowest name gets the pad.
* The other files with that number and all files without a number go on the free pads, lowest pad first, in name
  order. `00`, `33` to `99` and one digit count as no number. A card without numbers shows file 1 on pad 1, file 2
  on pad 2, and so on.
* The picker shows up to 32 files: first the numbered files, then the other files by name (byte order, digits before
  letters). The layout does not depend on directory order.

**Colours.** Each pad has a colour from its name (a hash of the name part, without the number and `.mod`), one of
11: orange, yellow, lime, green, mint, cyan, sky, blue, violet, magenta, pink. If an earlier pad has that colour,
the pad gets the next free colour, until all 11 are in use. Red means a failed file. The pad that loads blinks white.

**Play.** In arrhythmia-firmware 7339, Play always goes to the transport of the unit, in the picker and while a
module runs. A module that owns the controls also gets the Play button event. While a module with a clock entry
waits for START, the Play button blinks dim green.

**Built-in module.** arrhythmia-firmware 7339 has no built-in module. Paint is a card file, `01paint.mod`, in the
release files. devkit 7228 keeps its built-in paint.

The picker skips folders, hidden and system files, and files outside 26 bytes to 15 KB. A module in fast RAM starts
only if the unit loaded it after power-on.

Over USB, for development: `tools/module.py` uploads a module, verifies it and starts it. Both images keep the
development commands that it needs (SysEx 04, 05, 06, 0A, 0B, and 7D for the read-back).

SysEx 7D (`F0 00 20 29 00 60 7D a4 a3 a2 a1 a0 F7`, 32 bytes per reply) reads only the module memory: ITCM
`0x00000100` to `0x00003FFF` (module RAM `0x100` to `0x3FF`, module code from `0x400`), 32-byte aligned, while ITCM is
enabled. All other addresses, all internal flash included, get the refusal `F0 00 20 29 00 60 7D a4 a3 a2 a1 a0 F7`
(the address echoed, no data).

## ABI 3: calling the host

The host table is at the fixed flash address `0x0806A570`. Check it before a call:

| Word | Content |
|---|---|
| 0 | magic `'CRH1'` (0x31485243) |
| 1 | ABI version: 3 |
| 2 | `host_note` |
| 3 | address of the built-in module; 0 in arrhythmia-firmware 7339; devkit 7228 keeps built-in paint |
| 4 | `host_note_port` |
| 5 | `host_param` |
| 6 | `host_ui` |
| 7 | `host_midi` |

Read words 4 to 7 only when word 0 is `'CRH1'` and word 1 is 3 or more. On older builds, those words are code, not
function pointers. Words 0 to 3 are frozen.

```c
uint32_t host_note(uint32_t t, uint32_t note, uint32_t vel);
uint32_t host_note_port(uint32_t t, uint32_t note, uint32_t vel, uint32_t rec);
uint32_t host_param(uint32_t t, uint32_t idx, uint32_t val);
uint32_t host_ui(void);
uint32_t host_midi(uint32_t msg);
```

* `t` is the track, 0 to 7 (track 1 to 8). `note` uses the engine numbering: 60 plays a sample unpitched.
  `vel` 1 to 127 is a note on, 0 a note off.
* **host_note** plays a note on the sound of the track only.
* **host_note_port** with `rec = 1` uses the same path as a pad press: the sound, MIDI out on the channel of the
  track, and the pattern when Rec is armed and the transport runs. With `rec = 0`, it operates as `host_note`.
* **host_param** sets one of the 25 parameters of the track (`idx` 0 to 24, `val` 0 to 127). It applies at the next
  note.
* **host_ui** returns `track | view << 8 | front << 16 | 1 << 31`, or 0 if not known yet.
* **host_midi** sends one channel message, `status | d1 << 8 | d2 << 16`, to all MIDI outputs. A module that sends
  note ons must send its own note offs.
* All calls only queue the request. They return 1 if the request is in the queue. The queue has 8 slots, shared by
  all calls. The unit handles a maximum of 2 router calls per Main pass (no fixed rate per millisecond). Note offs
  are never lost.
* In arrhythmia-firmware, `host_note(7, ...)` and `host_note_port(7, ...)` play the track 8 synth. In
  arrhythmia-devkit, they play the sample of track 8, as on stock.

## Examples and source

The folder `modules/` holds the source of the eleven release modules and the shared header `module_common.h`:
`paint.c`, `euclid.c`, `life.c`, `rain.c`, `balls.c`, `pulse.c`, `stars.c`, `scroll.c`, `snake.c`, `lights.c` and
`mole.c`. The
built `.mod` files are release assets. The module files web page also has them
([add modules with the web page](modules.md#add-modules-with-the-web-page)).

* Build a module from its source, for example `01paint.mod`:

      uv run python -I tools/build_module.py modules/paint.c build/01paint.mod

* In arrhythmia-devkit, paint is also compiled into the image as the built-in module.
* `modules/stars.c` builds to a 164-byte module with the SHA-256
  `fe63caa0e36500b0150f9580ca356e36dbdd5f31ecfe522f26206b58f56c5c27`. Copy a built module to `Modules` on the card
  and select it with **Shift + Sample Rec** ([Loading](#loading)).

The ARM part of both images and the whole arrhythmia-devkit image rebuild from the published sources. The DSP source
of the arrhythmia-firmware image 7339 is in `tools/dspvoice207m4/`, so the full arrhythmia-firmware image also
rebuilds from source, on Linux (tested on Ubuntu x86-64 only). Refer to
[the rebuild steps](../README.md#rebuilding-the-images) and
[tools/dspvoice207m4/README.md](../../tools/dspvoice207m4/README.md). `tools/dspvoice/` holds the DSP voice source of
the 0.1.1 image.

## Text field of the scroll module

`20scroll.mod` keeps its text in one fixed field in the code. A tool changes the text without a compiler
(`tools/scrolltext.py`, [modules.md](modules.md#change-the-text)). To change it in another tool:

1. Find the first 4 bytes `TXT:` (`54 58 54 3A`) in the file. The text can also contain `TXT:`, so use the first
   occurrence only. The offset can change between builds: search for the bytes, do not use a fixed offset.
2. The 124 bytes after `TXT:` are the field. Write the text there (1 to 120 bytes) and set the remaining bytes of
   the field to `00`. Do not change the bytes outside the field.
3. Set bytes 8 to 11 (the CRC field of the header) to `00`. Calculate the CRC-32 (zlib, the CRC of PNG and ZIP) of
   the whole file. Write it at offset 8, little-endian.

| Item | Value |
|---|---|
| Field | `TXT:`, then 124 bytes |
| Text | 1 to 120 bytes, then `00` to the end of the field |
| Characters | `A` to `Z`, `0` to `9`, space, `. , ! ? ' - : / &` (ASCII) |
| File size | unchanged |
| Checksum | CRC-32 at offset 8, as in [the file format](../dev/modules-reference.md#file-format) |

The module reads the text when it starts. It shows `a` to `z` as capitals and every other byte that is not in the
font as a space. It stops at the first `00` or after 120 bytes.

## Safety

A module runs with full access to the memory of the unit. A faulty or malicious module can crash the unit or damage
data. Run only trusted modules, and keep backups. To exit a hung module, power-cycle the unit. No module starts
automatically at power-on.
