# Track 8 engines for developers

Applies to Circus Arrhythmia FM 0.3.1, firmware build 7388, and later firmware with engine ABI 3.

An engine is your own program for the digital signal processor (DSP) of the unit. It renders track 8. This page is
the contract between an engine and the firmware: the file, the checks before a load, the memory, the command queue,
the control sources, the engine controls on the unit, the notes and the health rules.

**Two different "ABI" numbers.** Engine ABI 3 is the interface of track 8 engines on this page. The module host table
also has an application binary interface (ABI) version ([developers.md](developers.md#abi-3-calling-the-host)). The
two numbers are not related.

**Status marks on this page.** "In development" means that the part is designed but not built or not released. Do
not rely on these parts as promises. The stability promise starts with the first firmware release that ships this
interface ([Versioning and compatibility](#versioning-and-compatibility)).

## What an engine is

* An engine runs on core 0 of the DSP (DSP56300 family). The stock program runs on the same core, in every block.
* The firmware loads the engine from the microSD card when the current pack selects it
  ([synth.md](synth.md#engines-on-track-8)). One engine is resident at a time.
* The main processor of the unit (the ARM) sends notes and control values to the engine through a command queue in
  DSP memory.
* The engine writes one mono buffer of 32 samples per block. The mixer channel of track 8 applies level, pan, sends
  and effects after the engine.
* An engine runs with full access to core 0. The firmware cannot stop, contain or recover a running engine. It
  detects what it can and gives a verdict.

### What the firmware guarantees

* It loads only files that pass every pre-load file and platform check ([Checks](#checks)). After startup, it
  accepts the running engine only when check 14 passes. It refuses every other file and plays the samples of the
  pack. When the DSP fault state is set, or the DSP hangs, a refusal promises no audio ([Health](#health)).
* It sends only the commands, fields and sources that the engine declares in its file.
* It sends the opening facts (HELLO), then every wanted control value, after each epoch and before the first note.
* It owns the allocation of notes to lanes. It never sends a lane at or above the declared lane count.
* It lays out the controls that the engine declares with one fixed rule ([Engine controls on the
  unit](#engine-controls-on-the-unit)). It never sends a control value that the declaration does not allow.
* It provides a firmware-owned code block, the shim, inside every engine file. The shim saves and restores the
  registers of the stock program, gives the engine its output address and calls the stock track mixer.
* It publishes queue entries in a defined order with a bounded overtaking rule
  ([Publication order and fairness](#publication-order-and-fairness)).

Platform 1 grants the engine data windows 0 to 2, a stack depth of 11 locations below its entry and a cycle budget E
([Known limits](#known-limits)). The firmware admits an engine only when its declared cycles fit E. The meter counts
every armed normal block that runs over the declaration. The firmware cannot stop an engine that runs over. It does not
guarantee a wall-clock latency. The time that the ARM adds to the queue is not known.

The platform facts on this page assume that no SDK module writes a DSP peripheral register. A module that writes one
voids platform 1: those facts no longer hold. The firmware does not detect such a write.

### Terms

| Term | Meaning |
|---|---|
| word | one 24-bit DSP word |
| P, X, Y | the program, X data and Y data memory of core 0 |
| block | 32 audio frames, the unit of DSP processing, about 0.666 ms |
| normal block | a block in which READY is valid when the shim is entered; every other block is an initialization block |
| CRC | cyclic redundancy check; the body CRC and the shim CRC use CRC-32 with the zlib parameters |
| Q23 | signed 24-bit fraction: 0x800000 is -1, 0x7FFFFF is 1 - 2^-23 |
| lane | one voice slot that the ARM addresses; 1 to 8 per engine |
| epoch | a numbered session of the queue; a new epoch resets voices and values |
| platform | a written contract of the facts an engine sees: shim, memory grants, stack depth G, cycle budget E, output, block size; named by a platform id |
| control record | an 8-byte header record that declares one engine control ([Control records](#control-records)) |
| settings key | a 32-bit value that identifies the stored control values of one engine design |
| BPM | beats per minute |
| SR, SP, SC | status register, stack pointer, stack counter |
| LA, LC | loop address and loop counter registers |
| FM | frequency modulation |

Numbers written `0x...` are hexadecimal. Bit 0 is the least significant bit. Counters and sequence numbers count
modulo 2^24 unless a row states otherwise.

## The engine file

An engine file has the extension `.dse`. Its format is CRE3: a header of H bytes, followed by a body of n words. The
file size is exactly H + 3n bytes.

### Fixed header

All multi-byte header fields are little-endian.

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 4 | magic | 0x33455243 (the bytes `CRE3`) |
| 4 | 2 | H, header size | 64 + 8T + 8C to 4,096, a multiple of 4 |
| 6 | 2 | format minor | 0; the firmware refuses any other value |
| 8 | 4 | n, body words | 389 to 8,192, the shim words included |
| 12 | 4 | body CRC | CRC-32 of the 3n body bytes |
| 16 | 4 | platform id | 1 for platform 1 |
| 20 | 4 | queue ABI | 0x000300 + ABI minor |
| 24 | 1 | engine class | 0 refused; 1 FM, 2 reserved, 3 subtractive, 4 wavetable, 5 drum or physical model; 6 to 127 for allocation; 128 to 255 experimental |
| 25 | 1 | T, table descriptors | 0 to 15; 0 below ABI minor 2 |
| 26 | 1 | C, control records | 0 to 16 ([Control records](#control-records)) |
| 27 | 1 | reserved | 0; checked |
| 28 | 4 | engine version | information only; never a compatibility fact |
| 32 | 4 | engine capabilities | [Engine capability bits](#engine-capability-bits); bits 24 to 31 zero |
| 36 | 4 | host needs | [Host capability bits](#host-capability-bits); bits 24 to 31 zero |
| 40 | 4 | wanted sources | bit s = 1: the engine wants control source s ([Source ids](#source-ids)) |
| 44 | 1 | lanes | 1 to 8 |
| 45 | 1 | K | 4 to 16: entries consumed per block ([Consumption](#consumption)) |
| 46 | 1 | sound slots | 0 to 128; 0: the Sample view of track 8 stays stock, unless a control record uses it |
| 47 | 1 | flags | bit 0 pitch form (0 increment, 1 note); bits 1 to 7 zero |
| 48 | 4 | release tail | blocks after RELEASE until a voice is silent; 0: the firmware default of 15,669 blocks |
| 52 | 4 | declared cycles | a declaration of the engine: its worst read-to-read span of a normal block, in DSP cycles, measured on a unit; 1 to E; 0 and values above E are refused (check 13) |
| 56 | 4 | memory claims | bit w = 1: the engine uses data window w ([Data memory](#data-memory)) |
| 60 | 4 | header CRC | CRC-32 of bytes 0 to H - 1, with this field taken as zero; see the notes below |
| 64 | 8T | table descriptors | [Table descriptors](#table-descriptors) |
| 64 + 8T | 8C | control records | [Control records](#control-records) |
| 64 + 8T + 8C | rest of H | metadata | [Metadata records](#metadata-records) |

* The class is a label for the loader. The firmware never changes the commands it sends because of the class.
  Classes 1 to 255 are accepted. The loader shows an unallocated class as unallocated.
* The engine version is information only. The firmware never checks it.
* To give a semantic version, use the marked form of the older engine files: bit 31 is 1, bits 30 to 24 are 0, and
  bits 23 to 16, 15 to 8 and 7 to 0 hold major, minor and patch. Any other value is allowed. The tools show it as an
  unknown version and keep the raw word.
* The format minor (+6) and the ABI minor (+20) are different fields. The format minor stays 0.
* The header CRC covers the fixed header, the table descriptors, the control records and the metadata, with +60
  taken as zero. It does not cover the body, which has its own CRC at +12. The CRC parameters of +60 are not yet
  fixed in the design. The zlib parameters of the body CRC are intended.
* The declared cycles (+52) are an admission condition (check 13) and the budget B of the meter ([Shim
  counters](#shim-counters)). The figure is the larger of two worst cases, each in one normal block: every lane sounding
  in its costliest combination of states (held, onset or release) while the engine consumes K of its costliest commands;
  and an epoch reset with every lane sounding. It includes the fixed shim overhead F. Initialization blocks are not part
  of it ([Initialization](#initialization)).
* Measure the figure on a unit. Declare E for a first trial, run both worst cases, read the normal-block span maximum in
  `card engstatus`, then declare a figure from that maximum up to E. If the maximum is above E, the engine does not fit
  platform 1. A declaration equal to the maximum you observed fails a later trial that reaches a heavier block.
* A figure from the emulator is not silicon timing, and no conversion factor turns one into the other.
* A reserved field is always checked, never ignored.

### Engine capability bits

Offset +32. The engine also writes bits 0 to 23 of this word into ready header word 4.

| Bit | Name | ABI minor | Meaning |
|---|---|---|---|
| 0 | CORE | 0 | implements the core set: ONSET, KILL, RELEASE, ALL OFF, HELLO, PARAM, PARAM3, NOP; required |
| 1 | DATA | 2 | accepts DATA BEGIN, DATA and DATA END for its descriptors |
| 2 | QUERY | 2 | answers QUERY through the reply block |
| 3 | VOICEMAP | 0 | keeps the voice activity bitmap in ready header word 6 |
| 4 | SHED | 0 | drops voices itself under load and counts them in ready header word 7 |
| 5 to 23 | reserved | | 0 |
| 24 to 31 | reserved, file only | | 0; the file is refused if one is set |

An engine with SHED keeps itself under its declared cycles by dropping voices. It counts the drops.

### Host capability bits

Offset +36 holds the needs of the engine. HELLO 1 holds the offer of the firmware.

| Bit | Name | ABI minor | Meaning |
|---|---|---|---|
| 0 | reserved | | 0; PARAM3 is part of the core on both sides |
| 1 | DATA | 2 | the firmware uploads tables; offered only to an engine with DATA |
| 2 | QUERY | 2 | the firmware can send QUERY; offered only to an engine with QUERY |
| 3 | NOTEPITCH | 1 | the firmware sends ONSET NOTE instead of ONSET |
| 4 | LANES8 | 1 | the firmware allocates more than 4 lanes |
| 5 | METER | 0 | the shim of the platform has the cycle meter |
| 6 to 23 | reserved | | 0 |
| 24 to 31 | reserved, file only | | 0; the file is refused if one is set |

* Set a needs bit only for a feature that the engine cannot work without. A firmware that does not offer a needed
  feature refuses the file. There is no fallback.
* A firmware on a platform without the meter never offers METER.
* The production shim of platform 1 is the meter variant. The meter reads timer 1 of core 0. It configures the timer
  only while the timer is stopped, and never stops it. Platform 1 is qualified by the 0.3.1 platform qualification.
* No capability bit gates the control records. Every ABI 3 firmware reads them.

### Table descriptors

ABI minor 2. A descriptor tells the firmware that a table exists, where it goes and whether the engine can play
without it. Each descriptor is 8 bytes.

| Offset | Size | Field | Rule |
|---|---|---|---|
| 0 | 1 | table id | 0 to 15; unique and ascending across the array |
| 1 | 1 | window bit | a data window that is set in the memory claims (+56) |
| 2 | 1 | flags | bit 0 REQUIRED; bit 1 FALLBACK (the engine has a built-in default); bits 2 to 7 zero |
| 3 | 1 | reserved | 0 |
| 4 | 2 | offset in the window, words | offset + length at most the window size, computed in 32 bits |
| 6 | 2 | length, words | 1 to the window size |

* The firmware checks every rule at admission. A failed rule refuses the file.
* The tools check that two tables in one window do not overlap. The firmware does not.
* The table data comes from a bank file of the pack (in development; proposed name `engine.bnk` beside
  `engine.cfg`). The descriptor only gives its shape.

### Metadata records

Metadata fills bytes 64 + 8T + 8C to H - 1. The firmware streams these bytes through the header CRC but does not
read them.
The loader, the card scan and the tools read them.

* A record is a tag (1 byte), a length L (1 byte) and L data bytes. Multi-byte data fields are little-endian.
* Tag 0xFF is the end marker: one byte, no length. Every byte after it, up to H, is 0x00.
* An empty metadata area (H = 64 + 8T + 8C) has no end marker. A metadata area with records must end with 0xFF.
* Tag 0x00 is invalid. A record that runs past H is invalid.
* The tools and the scan show a file with invalid metadata as invalid. The firmware still loads it if every
  admission check passes.
* The tools and the scan skip an unknown tag by its length and show it as unknown.

| Tag | L | Content | Count |
|---|---|---|---|
| 0x01 | 1 to 24 | name, printable ASCII 0x20 to 0x7E | at most one |
| 0x02 | 1 to 32 | author, printable ASCII | at most one |
| 0x03 | 1 to 32 | licence, SPDX (Software Package Data Exchange) identifier | at most one |
| 0x04 | 1 to 128 | description, printable ASCII | at most one |
| 0x10 | 2 to 13 | source label: source id (1 byte), label (1 to 12 ASCII) | at most one per source id |
| 0x11 | 3 | source detail: source id, latched (0) or live (1), default 0 to 127, information only | at most one per source id |
| 0x12 | 2 to 13 | control label: record index (1 byte), label (1 to 12 ASCII) | at most one per control record |
| 0x20 | 11 to 22 | engine parameter: id 32 to 255 (1), min (3), max (3), default (3), label (1 to 12) | at most one per id; ABI minor 2 |
| 0x30 | 2 to 13 | sound name: slot below +46 (1), name (1 to 12) | at most one per slot |
| 0x40 | | retired | never |
| 0x50 | 21 to 52 | build: source commit (20), assembler identity (1 to 32) | at most one |
| 0x60 | 10 | measurement: lanes held (1), cycles (4), meter image build (4), scene id (1) | any number |

* The firmware takes the defaults of control values from the control records, not from tag 0x11.
* The unit has no screen. The loader and the tools show the labels of tag 0x12.
* A missing or invalid tag 0x12 label gives a tool warning. It is not an admission failure. The other metadata rules
  still apply.

### Control records

An engine declares its controls on the unit with C control records (+26). The records start at byte 64 + 8T,
directly after the table descriptors. Each record is 8 bytes. The records are part of format minor 0 and ABI minor 0.
No capability bit gates them.

* An engine with C = 0 has no Sample view controls and no synth page 4. It plays its built-in defaults for every
  value that it does not receive.
* A record carries no label and no meaning. The engine owns the meaning of every value.
* The header CRC and the residency key cover the records. A change of a record forces a new load.
* A byte that a kind does not use must be 0.

| Byte | Field | Rule |
|---|---|---|
| 0 | kind and surface | bits 3 to 0 kind; bits 5 and 4 surface; bits 7 and 6 zero |
| 1 | source id | kind 1: 8; kinds 2 and 3: 16 or 21; kind 4: 17 to 20 |
| 2 | bit offset | kinds 2 and 3: 0 to 23, the lowest bit of the field in the bank word |
| 3 | width | kind 2: 1 to 3 bits; kind 3: 1 |
| 4 | count | kind 1: equal to +46; kind 2: 2 to 8, and at most 2^width; kind 3: 1 |
| 5 | default | kinds 1 and 2: 0 to count - 1; kind 3: 0 or 1; kind 4: 0 to 127 |
| 6 | colours | kinds 1 to 3: bits 3 to 0 the bright hue, bits 7 to 4 the dim hue; kind 4: bits 3 to 0 the knob hue, bits 7 to 4 zero |
| 7 | flags | bits 4 to 0 enable reference; bit 5 enable sense; bit 6 new row; bit 7 zero |

Kinds 5 and 6 use bytes 1 to 7 as their table states.

| Kind | Name | Surface | Purpose |
|---|---|---|---|
| 1 | sounds | 1 | the sound slots of source 8: count, default and colours; at most one |
| 2 | choice | 1 or 2 | a radio field in a bank word: count pads, one current value |
| 3 | toggle | 1 or 2 | a one-bit field on one pad |
| 4 | continuous | 2 | a value 0 to 127 on a knob of synth page 4 |
| 5 | macro defaults | 0 | byte 1 the first source id, 0 or 6; bytes 2 to 7 the defaults 0 to 127 of the six sources from that id |
| 6 | identity | 0 | the settings key: bytes 1 to 4, not 0, little-endian; bytes 5 to 7 zero; at most one |

* Kinds 0 and 7 to 15 are refused.
* Surface codes: 0 none; 1 the Sample view of track 8, rows 3 and 4; 2 synth page 4; 3 refused.
* In a kind 5 record with first id 6, the byte for source 8 is 0. Kind 1 gives the default of source 8.
* A kind 5 record names all six sources from its first id; with first id 6 that includes source 8. All six must be
  wanted (+40), also a source the engine does not use (check 17, rule 11).
* Source 0 (main macro 1) keeps the stored encoding of the firmware. Seven declared defaults reach the engine changed by
  one: 9 as 10, 27 as 28, 45 as 46, 63 as 64, 82 as 81, 100 as 99 and 118 as 117. The tools warn about such a default.
  The file is still admitted.
* Enable reference k: 0 means always enabled. A value k from 1 to 16 means that the record is enabled while toggle
  record k - 1 is on (bit 5 = 0) or off (bit 5 = 1).
* New row (bit 6) makes the field start on a new row ([Placement](#placement)).

Hue ids:

| Hue | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Colour | white | red | orange | yellow | green | cyan | blue | magenta |

The hue ids and the roles bright, dim and dark are stable. The exact light levels are internal.

Capacity:

| Item | Limit |
|---|---|
| records per engine | 16 |
| Sample view | 32 pads: rows 3 and 4 of page 1 and of page 2 |
| Sample view with choices and sounds | 16 choice pads on page 1 and 16 sound slots on page 2 |
| synth page 4 | 32 pads in rows 1 to 4, and knobs 1 to 4 |
| pads per field | 1 for a toggle, 2 to 8 for a choice; a field never spans two rows |
| bank words | 2 (sources 16 and 21), 24 bits each |
| fields | at most 16 in all, one per record |

### Body and shim block

The body is n words of 3 bytes each, most significant byte first. Body word i loads at P:0x2000 + i.

| Body words | P address | Content | Owner |
|---|---|---|---|
| 0 to 383 | P:0x2000 to P:0x217F | the shim image of the platform | firmware, inserted by the tools |
| 384, 385 | P:0x2180, P:0x2181 | engine vector: `jmp` absolute, 0x0AF080, then the target address t | engine |
| 386, 387 | P:0x2182, P:0x2183 | reserved, 0 | |
| 388 to n - 1 | P:0x2184 to P:0x2000 + n - 1 | engine code and tables | engine |

* The shim image is the same in every file for one platform. The firmware checks the CRC-32 of body bytes 0 to
  1,151 against the set of shim images it accepts for the platform id.
* The target t must satisfy 0x2184 <= t < 0x2000 + n. The first engine instruction is then a loaded word of the file.
  This check does not prove that the control flow of the engine is safe.
* The smallest body is 389 words: 384 shim words, the vector pair, two zero words and one engine instruction.
* The engine has at most 7,808 words: P:0x2180 to P:0x3FFF.
* Platform 1 reserves 384 words, P:0x2000 to P:0x217F, for the shim. The body layout, the shim CRC range, the smallest
  body and the entry bound depend on this reservation.
* The shim code can use fewer words. The pinned shim image fills the whole reservation, padding included.
* The production shim of platform 1 is assembled. It has 319 words of code and 65 zero words of padding, 384 words in
  all. Platform 1 is qualified by the 0.3.1 platform qualification.
* A different reservation would need a revision of this layout and of the platform contract.

**What the shim does.** It has three vectors at P:0x2000 (startup), P:0x2002 (block) and P:0x2004 (return).

* At startup after a DSP load, it sets SP and SC to 0 and writes 0 to READY (X:0x4E92). Then it continues the stock
  startup.
* At each block, it saves 37 register components of the stock program: SR, A2, A1, A0, B2, B1, B0, X0, X1, Y0, Y1,
  R0 to R7, N0 to N7, M0 to M7, LA and LC. It records SP and SC. It sets r0 to the output address of the block and
  restores SR. Then it jumps to the engine vector at P:0x2180.
* At P:0x2004 it counts the arrival and compares SP and SC with their values at entry. If they are equal, it restores
  the 37 components, calls the stock track mixer and returns to the stock program.
* If SP or SC differ, it counts a stack mismatch. It is designed to write silence into the 32 output words, rebuild the
  return frame, restore the 37 components and call the mixer. The 0.3.1 platform qualification exercised this path on a
  unit with a test engine. The shim does this in every block in which SP or SC differ; it does not stop calling the
  engine. Keep SP and SC balanced; never depend on the mismatch path.
* It does not save SP, SC, SSH, SSL, OMR, VBA, the stack contents or any peripheral register.
* The shim is a diagnostic aid. It is not a protection boundary.

### Build an engine file

The packaging commands are in development. Their arguments are not yet fixed.

1. Write the engine source with its origin at P:0x2180. Put the engine vector and the two zero words first.
2. Assemble the source to raw 24-bit words for P:0x2180 onwards. Use the
   [pinned line assembler](../../tools/dspvoice/toolchain/README.md#dsp-line-assembler) or your own assembler. The
   choice of assembler does not replace the conformance runner. Record the assembler in metadata tag 0x50.
3. Write a manifest with the header fields, the control records and the metadata of your engine. The declared cycles
   (+52) must be 1 to E ([Fixed header](#fixed-header)).
4. Run `make3`. It inserts the pinned shim image of the platform as body words 0 to 383. It writes the control
   records and the control labels (tag 0x12). It computes the body CRC and the header CRC.
5. Run `check`. It applies every file rule of this page, check 17 included.
6. Run the conformance runner ([Building and conformance](#building-and-conformance)).

```sh
python3 -I tools/engine_file.py make3 ...        # in development
python3 -I tools/engine_file.py check3 myeng.dse
python3 -I tools/engine_file.py inspect3 myeng.dse
```

* `rewrap` (in development) replaces body words 0 to 383 with another pinned shim image and computes both CRCs again.
  The engine words do not change. The result is a new file and must pass the conformance runner again. Rewrap does
  not make a file compatible with any other change of the facts an engine sees.
* Use a file name of 1 to 8 lowercase letters or digits, then `.dse`. Every path of the firmware accepts these
  names.

## Load and admission

### Selection and residency

* The pack selects the engine with `engine.cfg`. Packs without one, and the factory pack, use `engines/default.cfg`
  ([synth.md](synth.md#engines-on-track-8)).
* The firmware loads the engine at a pack load: at power-on and at a pack change. A project change inside a pack keeps
  the engine.
* A pack load reuses the resident engine when the platform id, the class, the body CRC and the header CRC are equal,
  and the resident engine is healthy. Any change of the header, metadata included, forces a new load.
* A new DSP load restarts both DSP cores. The firmware then uploads the samples of the pack again.

### Checks

Platform 1 admits every engine file that passes all the checks below. The firmware keeps no list of approved engines. It
refuses the file if one check fails. The pack then plays samples.

In addition to the checks below, the complete body must match the CRC at +12, the file must end at exactly H + 3n
bytes, and the transmission pass must retain its integrity checks.

| # | Check |
|---|---|
| 1 | magic 0x33455243; format minor 0; H from 64 + 8T + 8C to 4,096 and a multiple of 4; file size exactly H + 3n |
| 2 | header CRC (+60) over all H bytes, the control records included |
| 3 | queue ABI 0x000300 + m, with m at most the ABI minor of the firmware |
| 4 | the platform id is one that this firmware composes |
| 5 | every bit set in +32, +36 and +40 is defined at the minor m of the file; bits 24 to 31 of +32 and +36 are zero; CORE (bit 0 of +32) is set |
| 6 | the host needs (+36) are a subset of the host capabilities that this firmware offers |
| 7 | lanes 1 to 4, or 5 to 8 with LANES8 in +36; K 4 to 16; sound slots 0 to 32, or up to 128 at minor 1 or later |
| 8 | flags: bit 0 set only with NOTEPITCH in +36; bits 1 to 7 zero |
| 9 | the memory claims (+56) are a subset of the windows that the platform grants |
| 10 | n from 389 to 8,192; the CRC-32 of body bytes 0 to 1,151 is in the accepted shim set of the platform |
| 11 | body word 384 is 0x0AF080; word 385, the target t, satisfies 0x2184 <= t < 0x2000 + n; words 386 and 387 are zero |
| 12 | table descriptors: T at most 15, only at minor 2 or later, every rule of [Table descriptors](#table-descriptors) |
| 13 | declared cycles (+52) from 1 to E, the platform budget ([Cycles per block](#cycles-per-block)) |
| 14 | after the load: ready header MAGIC, VERSION equal to +20, word 4 equal to bits 0 to 23 of +32, word 5 equal to +44 and +45 |
| 15 | the press size S + L is at most 24, with S computed from the actual wanted sources, 14 to 21 included ([How presses are staged](#how-presses-are-staged)) |
| 16 | the reserved byte +27 is zero; the class (+24) is not 0 |
| 17 | the control records and their placement ([Check 17](#check-17-control-records)) |

* Check 5: wanted bits 14 to 21 are defined at ABI minor 0. Bits 12 and 13 need minor 1. Bits 22 to 31 are reserved.

### Who applies the checks

| Group | Checks | Applied by |
|---|---|---|
| File checks: they depend only on the file | 1, 2, 5, 7, 8, 11, 12, 15, 16, 17 and the bounds of 10 | the firmware in its load passes; the USB writer before it commits a file; the card scan, as a verdict; the tools |
| Platform checks: they depend on the firmware | 3, 4, 6, 9, 13 and the shim set of 10 | the firmware in its load passes; the card scan, as a verdict; the tools, against a platform table |
| Runtime check: it depends on the running engine | 14 | the firmware only, after streaming and startup |

* Every site uses the same written rule for each file and platform check. The USB writer never commits a file that
  the firmware would refuse on a file check. The scan never shows such a file as loadable.
* The scan and the tools never claim check 14. They report static eligibility, or a conformance result recorded
  separately.

### Check 17: control records

Every site that applies the file checks applies check 17 with the same written rule. A failure refuses the file with
the reason "controls", and the pack plays samples.

1. C is at most 16. H is at least 64 + 8T + 8C.
2. Each kind, surface and source id is one that [Control records](#control-records) allows. Every unused byte is 0.
3. Exactly one kind 1 record exists when +46 is not 0, and none when +46 is 0. Its count equals +46. Above 32 sound
   slots (ABI minor 1), no other record uses the Sample view.
4. At most two kind 5 records exist, with first ids 0 and 6, each at most once. The byte for source 8 is 0. Every
   default is 0 to 127.
5. At most one kind 6 record exists, and its key is not 0. Kind 6 must exist if a kind 2, 3 or 4 record exists.
6. Kinds 2 and 3: offset + width is at most 24. In one bank word, the fields do not overlap.
7. Kind 2: count 2 to 8, count at most 2^width, default below count. Kind 3: width 1, count 1, default 0 or 1.
8. Kind 4: surface 2, at most 4 records, each of sources 17 to 20 at most once.
9. Every hue is 0 to 7.
10. An enable reference points to an earlier kind 3 record that has no enable reference of its own.
11. Every source that a record names is wanted (+40). Every wanted bank source (16, 21) has at least one field
    record. Every wanted source 17 to 20 has its kind 4 record.
12. The fields fit their surfaces under the [placement rule](#placement).
13. Check 15 holds with the actual wanted sources.

### Refusals

* Every refusal gives samples on track 8. `tools/module.py card engstatus` shows the refusal reason. The loader shows
  the reason too (in development).
* A file in the older CRE2 format is refused at the magic check. The loader says that the engine needs its ABI 3
  build.
* A file for another platform id is refused with the reason "platform". Rewrap it for the new platform and run the
  conformance runner again.
* A file with declared cycles 0, or above E, is refused at check 13. To measure a new engine, declare E for its first
  trial ([Fixed header](#fixed-header)).
* A check 14 failure, or no ready header within 500 ms after the load, gives one stock DSP load and then samples.
* When the DSP fault state is set, or the DSP hangs, a refusal promises no audio ([Health](#health)).

## Memory and entry

### Program memory

| Range | Owner |
|---|---|
| P:0x0000 to P:0x1FFF | the stock program and its padding; never write |
| P:0x2000 to P:0x217F | the shim, body words 0 to 383 |
| P:0x2180 to P:0x2183 | the engine vector and two zero words |
| P:0x2184 to P:0x3FFF | engine code and tables |

* The DSP bootstrap loads P only. The stock startup clears X and Y after the load. Keep every table in P and copy it
  to X or Y at initialization, or receive it with DATA (ABI minor 2).
* Code, tables and the shim share the 8,192 words of the engine region.

### Data memory

The engine owns a data window only after the platform table grants it. A grant is a positive reservation after an
emulator watch of the stock program. Platform 1 grants windows 0, 1 and 2 (mask 0x07). Windows 3, 4 and 5 are not
granted: a file that claims one is refused (check 9).

| Window bit | Range | Words | Status |
|---|---|---|---|
| 0 | X:0x4E00 to X:0x4E8F | 144 | granted; used by the FM engine |
| 1 | X:0x5000 to X:0x5BFF | 3,072 | granted; partly used by the FM engine |
| 2 | Y:0x3400 to Y:0x3FFF | 3,072 | granted; partly used by the FM engine |
| 3 | X:0x4D50 to X:0x4DFF | 176 | not granted |
| 4 | X:0x4EA0 to X:0x4EFF | 96 | not granted |
| 5 | X:0x4F40 to X:0x4FFF | 192 | not granted |

Words with a fixed role:

| Range | Role |
|---|---|
| X:0x4D00 to X:0x4D07 | queue control ([Layout](#layout)) |
| X:0x4D08 to X:0x4D0F | reply block ([Reply block](#reply-block)) |
| X:0x4D10 to X:0x4D4F | queue entries |
| X:0x4E90 to X:0x4E9F | ready header ([Ready header](#ready-header)) |
| X:0x4F00 to X:0x4F3F | shim data; never write |
| X:0x5C00 to X:0x5E3F | DSP load meter of the devkit image; excluded |

* The granted windows 0, 1 and 2 total 6,288 words.
* Set a claim bit (+56) for every window the engine uses. The firmware cannot police data writes while the engine
  runs. The conformance runner checks the writes of the engine offline.

### Writable memory

This list is complete. The engine writes no other address.

| Region | When |
|---|---|
| the 32 output words at Y:r0 | in this block |
| X:0x4D01 (ACK) and X:0x4D03 to X:0x4D07 (CONS, FAULT, CONSUMED, REJECTED, TICK) | in normal operation |
| X:0x4D00 to X:0x4D4F, all of it | at initialization only |
| X:0x4D08 to X:0x4D0F, the reply block | only as [Reply block](#reply-block) states |
| X:0x4E90 to X:0x4E9F, the ready header | as [Initialization](#initialization) and [Ready header](#ready-header) state |
| the claimed windows | always |

* Never write P.
* Shared memory 0x030000 to 0x037FFF is one physical RAM for both cores, in P, X and Y. Never write it through any
  alias outside the 32 output words. Never write the output words through an X or P alias.
* Never write external memory. The stock program keeps samples and mixer buffers there.

### Registers at entry

At P:0x2180 in every block:

* SR equals the word that the shim saved at the stock call. The shim uses its own arithmetic modes for its own work
  only. The engine does not receive them.
* Platform 1 states these modes at engine entry. In SR: no scaling (S1:S0 = 00), two's-complement rounding (RM, bit 21,
  set), no arithmetic saturation (SM, bit 20, 0), no double-precision multiply (DM, bit 14, 0), sixteen-bit
  compatibility off (SC, bit 13, 0) and sixteen-bit arithmetic off (SA, bit 17, 0). In OMR: stack extension off (SEN 0).
  The ABI names no other mode value. The shim needs bits 13 and 17 to be 0 at the stock call.
* r0 holds the Y address of the 32 output words of this block. m0 is 0xFFFFFF.
* Every other register holds what the stock program and the shim left. The engine can change any register, except as
  below. It can return with any value in any register. r0 need not be unchanged.

The engine must:

1. Set every arithmetic mode it needs (scaling, rounding, saturation), unless the platform table states it.
2. Keep the interrupt mask of SR unchanged.
3. Keep bit 13 of SR at 0 at all times. Bit 13 is the sixteen-bit compatibility mode, not the stack counter SC.
4. Change other mode bits of SR only if it restores them before it jumps to P:0x2004. Restore bit 17 of SR to 0 in
   particular. DO, ENDDO and the loop end are allowed.
5. Never change OMR, VBA, the interrupt vectors or any peripheral register.
6. Never write X:0x4F00 to X:0x4F3F.

The shim does not repair the modes of an engine that breaks rules 3 or 4.

The engine needs no peripheral access. The shim finds the output half.

### Stack

* Use the hardware stack only through balanced JSR and RTS, BSR and RTS, and DO loops that end normally or by ENDDO.
* Never name SP, SC, SSH, SSL, SZ or EP in any instruction. The conformance runner scans for these names.
* At the jump to P:0x2004, SP and SC must equal their values at entry to P:0x2180.
* Never pop below the entry level. The location there holds the return address of the stock program. The shim cannot
  see an overwrite of that location.
* A JSR or BSR takes one stack location. A DO loop takes two.
* Without stack extension, the hardware stack has 15 usable locations. The ABI assumes the stack without extension.
  Use of stack extension would need its own contract.
* A stack overflow is not trapped on platform 1. The stock stack-error vector does nothing.
* The engine can nest at most G locations below its entry. G is 15 - D - H. D is SP at the shim entry. H is the deepest
  interrupt use during the block call.
* Platform 1: D = 2, H = 2, G = 11. H already covers interrupts; do not count them again.
* Count the depth below the entry level, not the absolute SP. An engine of depth 7 runs at SP 9, and at SP 11 with
  interrupts.
* The static depth of the engine over every path of its code must be at most 11. The static scan of the conformance
  runner checks it.
* With G = 11, the shim, the stock track mixer after the return, the startup and interrupt nesting all stay within the
  15 locations.
* The FM engine 2.0 reaches 7 locations.

### Output

* The output is exactly the 32 words Y:r0 to Y:r0 + 31 of this block, in initialization blocks too.
* The format is signed Q23 mono. +1 is not representable.
* Write all 32 words in every block, silence included.
* You can keep the output address in a word of a claimed window during the block. No ownership of the address or of
  the buffer half survives into the next block.

### Startup and the first blocks

| Step | Who | What happens |
|---|---|---|
| 1 | ARM | streams the composed P image: the stock program, the integration words and the engine file body |
| 2 | stock program and shim | the stock startup clears X and shared RAM; the shim startup sets SP and SC to 0 and READY to 0 |
| 3 | shim | first block: initializes its own data; the block is an initialization block |
| 4 | engine | READY is not valid: initializes, possibly over several blocks; writes silence and increments TICK in every block; writes READY last; consumes no queue entry before READY |
| 5 | ARM | polls the ready header for up to 500 ms; applies check 14 |
| 6 | ARM | arms the meter budget B: the declared cycles (+52) |
| 7 | ARM | requests an epoch |
| 8 | engine | acknowledges the epoch in a later block |
| 9 | ARM | sends HELLO 0 to 3, every wanted source, then the missing tables (ABI minor 2), then normal traffic |

* Write READY and acknowledge an epoch without waiting for HELLO, a source value or a table. An engine that waits
  for a command before READY or ACK deadlocks this order.
* The engine has no separate startup entry. The shim runs the startup. The engine initializes in its block code.
* Silence until the first ONSET is the normal state. A missing table makes voices silent. It never delays READY.

### The block and the return path

The order of steps 2 and 3 is the order of the FM engine. The bullets after the list are the rules.

1. The shim jumps to the engine vector at P:0x2180.
2. The engine checks the ready header. If it is not valid, the engine initializes ([Initialization](#initialization)).
3. The engine increments TICK before any queue work, in every block.
4. In a normal block, the engine services the epoch or consumes entries ([Consumption](#consumption)).
5. The engine writes its 32 output words.
6. The engine jumps to P:0x2004, with SP and SC equal to their entry values.

* Increment TICK before any queue work, in every block. Write the output before the jump to P:0x2004.
* The jump to P:0x2004 is the only return path. If the engine never jumps there, the shim cannot run. The stock track
  mixer is then not called and the stock program does not continue.
* A runtime reinitialization keeps the shim data and counters.

## The command queue

The ARM sends commands through a ring of 16 entries in X memory of core 0. All values are 24-bit. The transport is
unchanged from the queue of the older engine interface (ABI 0x207).

### Layout

| Address | Name | Written by | Meaning |
|---|---|---|---|
| X:0x4D00 | REQ | ARM | requested epoch |
| X:0x4D01 | ACK | engine | acknowledged epoch |
| X:0x4D02 | PROD | ARM | sequence number of the next entry that the ARM publishes |
| X:0x4D03 | CONS | engine | sequence number of the next entry that the engine consumes |
| X:0x4D04 | FAULT | engine | 0 healthy; not 0: the queue is faulted |
| X:0x4D05 | CONSUMED | engine | plus 1 for each consumed entry |
| X:0x4D06 | REJECTED | engine | plus 1 for each queue fault; set to 0 only by an initialization |
| X:0x4D07 | TICK | engine | plus 1 in every block |
| X:0x4D08 to X:0x4D0F | reply block | engine | [Reply block](#reply-block) |
| X:0x4D10 to X:0x4D4F | entries | ARM | 16 entries of 4 words |

### Entries and publication

The entry of sequence number s is at X:0x4D10 + 4 x (s mod 16).

| Word | Content |
|---|---|
| +0 | epoch: the current epoch |
| +1 | word0: the command |
| +2 | word1: the argument |
| +3 | commit: s + 1 |

* The ARM writes each entry in the order epoch, word0, word1, commit. It writes PROD once after the last entry of a
  write.
* Read the commit and the epoch of an entry before word0 and word1.
* The ARM never writes PROD before the epoch acknowledgement.

### Initialization

At each block entry, read MAGIC, VERSION and READY. If one differs from its value in the ready header table,
initialize:

1. Write 0 to READY.
2. Add 1 to INIT_COUNT.
3. Write 0 to X:0x4D00 to X:0x4D4F.
4. Reset every voice and every value to the defaults of the engine.
5. Write ready header words 4 to 15.
6. Write MAGIC, then VERSION, then READY. READY is last.

* After a DSP load, READY is 0, so the first blocks initialize.
* The ARM writes 0 to READY to request an initialization at run time, for example after a sample transfer. The
  engine loses its state and its tables.
* Finish a runtime initialization within 100 ms. This is a recommendation. The ARM waits 100 ms, then polls more
  slowly, and accepts a late completed initialization.
* An initialization block runs inside the audio block too. Keep each initialization block within E, and split long work
  over several blocks. The meter records the largest initialization span but counts no overrun.

### Epochs

1. The ARM writes REQ = ACK + 1.
2. When the engine sees REQ different from ACK, it resets in that block. Every voice stops at once, without a release
   tail, and every value returns to its default. A table upload in progress is cancelled. A completed table stays
   valid.
3. The engine writes CONS = PROD, then FAULT = 0, then ACK = REQ. ACK is last.
4. The engine consumes no entry after the reset in that block.

The ARM requests a new epoch after each DSP load, after a queue fault, after a runtime initialization and before some
sample transfers.

### Consumption

In every normal block with no epoch request and FAULT 0:

1. Read d = (PROD - CONS) mod 2^24 once.
2. Consume exactly min(K, d) entries, in order. K is the value in header +45.
3. Add 1 to CONS and to CONSUMED for each consumed entry.

Exceptions:

* An initialization block consumes no entry.
* A block that services an epoch request consumes no entry after the reset.
* A block that sets FAULT consumes no entry after the fault.

**Progress bound.** The ring holds 16 entries. An entry already published at ring position p (1 for the oldest) is
consumed within ceil(p / K) normal blocks after its publication. That is at most 4 normal blocks. Later publications
cannot delay it.

Scope of the bound:

* It covers entries already published in the ring, in normal blocks only.
* Initialization, epoch-reset and FAULT blocks are excluded.
* It is not a wall-clock bound from a pad press and not an admission guarantee for entries that wait on the ARM.

### Queue faults

The engine raises a queue fault only for these conditions:

* d is more than 16, as a 24-bit unsigned distance.
* The commit of the entry at CONS is not CONS + 1.
* The epoch of the entry at CONS is not ACK.

At a queue fault the engine resets as at an epoch reset, writes FAULT = 1 and adds 1 to REJECTED. It refuses every
entry until the next epoch. The firmware treats a fault as a failure of the engine ([Health](#health)).

### Commands

word0 bits 23 to 20 are the command class. Engine ABI 3 uses classes 0xA, 0xB and 0xC. "Exactly" means that every
bit of word0 not named in the row is 0. The sender writes reserved bits as 0.

Class 0xA:

| Command | word0 | word1 | Needs |
|---|---|---|---|
| NOP | 0xA00000 exactly | 0 | CORE |
| KILL | 0xA01000 \| lane << 9: lane in bits 11 to 9, bits 8 to 0 zero | bits 3 to 0 generation; bits 23 to 4 zero | CORE |
| RELEASE | 0xA02000 \| lane << 9: as KILL | as KILL | CORE |
| ALL OFF | 0xA03000 exactly | 0 | CORE |
| DATA BEGIN | 0xA04000 \| table in bits 3 to 0; bits 11 to 4 zero | length in words, equal to the descriptor's; bits 23 to 16 zero | DATA |
| DATA END | 0xA05000 \| table in bits 3 to 0; bits 11 to 4 zero | sum of the table words modulo 2^24 | DATA |
| HELLO | 0xA0C000 \| n in bits 3 to 0; bits 11 to 4 zero | host fact word n ([HELLO facts](#hello-facts)) | CORE |
| QUERY | 0xA0D000 \| q in bits 7 to 0; bits 11 to 8 zero | argument | QUERY |
| PARAM | 0xA10000 \| id << 8: id 0 to 255 in bits 15 to 8, bits 7 to 0 zero | value, 24 bits | CORE |
| PARAM3 | 0xA20000 \| id in bits 7 to 0 (0 to 10); bits 15 to 8 zero | bits 6 to 0 v0, 13 to 7 v1, 20 to 14 v2; bit 21 v0 present, bit 22 v1 present, bit 23 v2 present | CORE |
| ONSET | 0xA80000 \| lane << 16 \| m bits 22 to 7: lane 0 to 7 in bits 18 to 16, m bits 22 to 7 in bits 15 to 0 | bits 23 to 17 m bits 6 to 0; bits 16 to 12 e; bits 11 to 5 velocity; bit 4 zero; bits 3 to 0 generation | CORE |

Unused codes of class 0xA: 0xA0E000, 0xA0F000 and 0xA3xxxx are reserved. 0xA06000 to 0xA0BFFF and 0xA4xxxx to
0xA7xxxx stay unused.

Class 0xB:

| Command | word0 | word1 | Needs |
|---|---|---|---|
| DATA | 0xB00000 \| table << 16 \| offset: table 0 to 15 in bits 19 to 16, offset 0 to 65,535 in bits 15 to 0 | data word | DATA |

Class 0xC:

| Command | word0 | word1 | Needs |
|---|---|---|---|
| ONSET NOTE | 0xC00000 \| lane << 16 \| pitch: bit 19 zero, lane in bits 18 to 16, pitch in bits 15 to 0 | bits 11 to 5 velocity; bits 3 to 0 generation; bits 23 to 12 and bit 4 zero | NOTEPITCH |

### Field rules

* PARAM3 carries sources id, id + 1 and id + 2. Only sources 0 to 12 go in a triple. Pitch bend (13) is sent with
  PARAM only, so id is at most 10.
* Sources 14 to 21 are sent with PARAM only, also the 7-bit sources 17 to 20. They are never in a triple.
* ONSET pitch: the phase increment per sample is M << e, with M = 1 << 23 | m. m is the 23 bits sent. e is 0 to 23.
  Bit 23 of the mantissa is implicit.
* ONSET NOTE pitch: bits 15 to 9 are the semitone, 0 to 127. Bits 8 to 0 are the fraction in 1/512 semitone. The ARM
  applies the tune before it sends, and rounds to the nearest 1/512.
* Velocity is 1 to 127. 0 is out of range.
* Generation: 4 bits per lane.
* A lane at or above the declared lane count is out of range.

### Required effects

| Command | Effect |
|---|---|
| ONSET, ONSET NOTE | If the generation is the stored generation of the lane plus 1 (modulo 16), store it and start the note on the lane. A sounding lane is damped first. A wrong generation has no effect. Latched values are taken at this moment. |
| KILL | Two independent tests. If the generation matches the pending ONSET of the lane, cancel it. If the generation matches the sounding note, stop that note quickly. |
| RELEASE | As KILL, but the sounding note goes to its release. A note that is already stopping stays stopping. A cancel does not change the stored generation. |
| ALL OFF | Stop every note quickly, cancel every pending ONSET, set every generation to 0. |
| HELLO | Store the fact or ignore it. No change of sound. |
| PARAM, PARAM3 | Set the value of the source or parameter. The engine decides whether the value is latched for the next ONSET or changes the live targets of sounding voices. Smoothing is the engine's choice. Values of one entry apply together. Values of two entries can apply in different blocks. An id that the engine does not use has no effect. A bank word (sources 16 and 21) with a declared field at or above its count, or with any undeclared bit set, has no effect as a whole. |
| DATA BEGIN | If a descriptor for the table exists and the length equals its length, open the table. The table is invalid from this moment until an accepted DATA END. A new BEGIN discards an open table. Otherwise no effect. |
| DATA | If the table is open and the offset is the next one expected (0, 1, 2 and so on), store the word and add it to the running sum. Otherwise no effect, and the END fails. |
| DATA END | If the table is open, every offset from 0 to length - 1 arrived once in order and the sum matches, the table becomes valid. Otherwise it stays invalid; no old content is kept. Write the reply. Never set FAULT. |
| QUERY | Write the reply. q 0: status (ready header word 8) and the table validity bits. q 1: the voice bitmap. q 2: the value of the source or parameter id in the argument. q 3: the state of the table in the argument. The positions and encoding of these answers are not yet decided. |

* A pending ONSET is an accepted ONSET that waits while its lane is damped.
* Every 3.0 command handler is bounded. No command makes the engine loop over a table. DATA END compares a sum that
  DATA accumulates.
* Write table words into their final storage. There is no staging copy.
* A completed table survives an epoch reset. An epoch reset cancels an upload in progress and the reply of an
  outstanding request.
* Only an initialization makes every table invalid.
* While a table without FALLBACK is invalid, the voices that need it are silent. While a table with FALLBACK is
  invalid, the engine uses its built-in default.
* The ARM uploads a missing or invalid table only in the opening phase of an epoch, before its first ONSET. A valid
  table needs no new upload after an epoch reset. An upload never overwrites a table that a sounding voice reads.

Panic and reset need no other command:

| Action | Commands |
|---|---|
| all notes off | RELEASE for each sounding lane |
| all sound off | ALL OFF |
| reset of voices and values | an epoch reset |
| full reinitialization | READY = 0 |

### Unknown commands

Consume with no effect, and never set FAULT:

* an unknown command;
* a field out of range;
* a reserved bit set in word0 or word1.

Example: KILL with word0 0xA01000 and word1 0x10 | g has no effect.

### HELLO facts

After every epoch acknowledgement, the ARM sends HELLO 0 to 3 before any other command. The engine can store them or
ignore them.

| HELLO n | word1 |
|---|---|
| 0 | bits 23 to 16 ABI major (3); bits 15 to 8 ABI minor of the firmware; bits 7 to 0 number of HELLO words sent |
| 1 | the host capability bits that the firmware uses with this engine |
| 2 | bits 7 to 0 frames per block (32); bits 11 to 8 output form (0: mono Q23); bits 15 to 12 lanes granted; bits 23 to 16 rate code (1: 48,046.875 Hz nominal) |
| 3 | firmware build number, information only |

Block size and rate are platform facts. An engine at ABI minor 0 must not need HELLO to learn them.

### Reply block

ABI minor 2. QUERY and DATA END write a reply. Below minor 2 the block stays zero.

| Word | Content |
|---|---|
| X:0x4D08 | END: the request id, written last |
| X:0x4D09 | TAG: the QUERY code q, or 0x100 + table for a DATA END |
| X:0x4D0A to X:0x4D0E | answer words; for DATA END, X:0x4D0A is 1 accepted or 0 not accepted; for QUERY, not yet decided |
| X:0x4D0F | BEGIN: the request id, written first |

**QUERY answers are not yet decided.** The positions of the QUERY answers in X:0x4D0A to X:0x4D0E, the encoding of
the table state (q 3) and the value of unused answer words are not fixed. Do not implement QUERY until they are. The
rules below apply to DATA END now, and to QUERY when its answers are decided.

Engine rules:

1. Take the request id from the commit word of the request entry: CONS + 1 modulo 2^24 at the time of consumption.
2. In the block that consumes the request, before CONS passes it, write BEGIN.
3. Write TAG and the answer words.
4. Write END last.

* Write the reply block at no other time. Initialization clears it with the queue.

ARM rules:

* The ARM keeps at most one reply request (QUERY or DATA END) outstanding per epoch. It publishes the next one only
  after it has finished the previous one.
* It reads the reply only after CONS has passed the request: for the request at sequence number i,
  1 <= (CONS - i) mod 2^24 <= (PROD - i) mod 2^24.
* It reads in ascending order, END first and BEGIN last. It accepts the reply only if END = BEGIN = the request id.
* It finishes or cancels the request before it has published 4,096 entries after it.
* An epoch request, READY = 0, a reload or a FAULT cancels the outstanding request.
* A read that does not match counts as no reply. The ARM never retries a read into the reply of a later request.

**Readability rule.** The reply block holds the reply from the consumption of the request until the ARM publishes the
next reply request. Ordinary entries can follow the request. They do not touch the block. The reply therefore stays
readable however far CONS advances.

One read of the 8 words takes about 0.9 ms. QUERY is a diagnostic tool, not a streaming channel.

### Opening phase

After every epoch acknowledgement, the ARM sends:

1. HELLO 0 to 3.
2. Every wanted source. It uses PARAM3 where three adjacent 7-bit sources fit, else PARAM. Sources 14 to 21 go as
   PARAM singles. The values of sources 16 to 21 come from the stored project values, else from the record defaults.
3. From ABI minor 2: every missing or invalid table that the bank file of the pack provides, one at a time: BEGIN,
   DATA in offset order, END, then it reads the reply. A REQUIRED table that fails is uploaded once more in the same
   epoch.
4. Normal traffic. The first ONSET waits until every END of step 3 has its reply.

* The ARM always sends every wanted value, after every epoch. It keeps no copy of the defaults of the engine beyond
  the control records.
* If a REQUIRED table without FALLBACK is not accepted, or the bank file lacks it, the ARM sends no ONSET to this
  engine until the next load. Track 8 is silent and the status says why. This is never a queue fault.

### Publication order and fairness

The ARM promises this order:

1. ALL OFF and KILL go before every staged entry that is still waiting when they are published. The priority cap
   below decides when that is.
2. Staged entries reach the queue in staging order.
3. Every source entry of a press precedes the first ONSET of the press.
4. ONSETs go lowest lane first.

No other order and no atomicity across entries are promised.

**Fairness.** The staging list is strict FIFO (first in, first out). Presses, releases and knob changes are published
in staging order. An entry already in the ring is never overtaken. Before publication, only ALL OFF and KILL overtake.
The pending set bounds how many wait at one time. The priority cap bounds replenishment: at most L + 1 priority
entries, 9 at 8 lanes, go before each staged entry. At most L + 1 priority entries therefore overtake a waiting head
of the staging list. When the head is eligible under the knob reserve, it is the next publication. A knob-only head
can wait longer for its reserve of 4 free entries after it. At L = 8, K = 4 and a full ring, three normal blocks of
priority refill leave three positions free, and the knob entry is published after the fourth. That wait belongs to
the reserve, not to overtaking. This bounds overtaking, given publication opportunities. The rate of publication
opportunities is not known, so it is not a wall-clock bound. Entries deleted by an epoch change are cancelled, not
served.

* A press can span several writes. The ARM never holds back an entry of a press to keep the press together.
* Values of one entry apply together. If several sources must change together, give them adjacent ids in one PARAM3
  triple, or latch them. No multi-entry transaction exists.

## Control sources

The ARM forwards fixed control sources raw. The ARM normalizes each value. The engine owns its meaning. The ARM has no
mapping table for engines.

### Source ids

Values are 0 to 127 unless the row states otherwise.

| Id | Value | ABI minor | Binding (internal) |
|---|---|---|---|
| 0 | 0 to 127 | 0 | main macro 1 (the normalized value) |
| 1 to 4 | 0 to 127 | 0 | main macros 2 to 5 |
| 5 to 7 | 0 to 127 | 0 | main macros 6 to 8 |
| 8 | 0 to 127 | 0 | sound: the Sample view slot of track 8, the validated full-width value |
| 9 to 11 | 0 to 127 | 0 | synth page macros 2 to 4 |
| 12 | 0 to 127 | 1 | pad pressure of track 8 |
| 13 | 0 to 16,383, PARAM only | 1 | pitch bend |
| 14 | tempo: BPM x 16; 0 no tempo, else 320 to 4,080 (20 to 255 BPM); PARAM only | 0 | the clock of the unit |
| 15 | transport, 24-bit, PARAM only ([Tempo and transport](#tempo-and-transport)) | 0 | Play and Stop |
| 16 | choice bank A, 24-bit, PARAM only | 0 | choice and toggle records |
| 17 to 20 | 0 to 127, PARAM only | 0 | knobs 1 to 4 of synth page 4 (kind 4 records) |
| 21 | choice bank B, 24-bit, PARAM only | 0 | choice and toggle records |
| 22 to 31 | reserved | | |
| 32 to 255 | 24-bit, PARAM only | 2 | engine parameters, set by the host over SysEx (in development) |

* The ids and the value encodings are in the frozen scope
  ([Versioning and compatibility](#versioning-and-compatibility)). The bindings in the last column are internal,
  except the surfaces and the placement rule of the engine controls.
* At ABI minor 0 the firmware has 20 fixed sources: 0 to 11 and 14 to 21. Minor 1 adds 12 and 13.
* A bank word is the OR of each field value shifted left by its offset. Bits that no record declares are 0. The ARM
  never sends a field value at or above its count.
* Tune (synth page macro 1) is part of the pitch, not a source.
* Set bit s of +40 for each source s that the engine wants. A bit for a source that is not defined at the minor of
  the file refuses the file.
* The labels on the unit do not change. The loader shows the source labels (tag 0x10) and the control labels (tag
  0x12) of the engine from its metadata.

### Tempo and transport

Source 14, tempo:

* The value is the tempo in BPM x 16. 0 means no tempo.
* With the internal clock, the value is the tempo of the unit x 16. With an external clock, it is the estimate of
  the unit x 16.
* The ARM sends it in the opening phase and at every change, while the transport runs or stops.

Source 15, transport:

| Bits | Content |
|---|---|
| 23 | running: 1 while the transport runs; the pulse is valid only then |
| 17 and 16 | Play counter: plus 1 at each Play, modulo 4 |
| 9 to 0 | pulse 0 to 767 in an eight-bar window counted from Play, 24 pulses per beat |
| all other bits | 0 |

* The ARM sends source 15 in the opening phase, at every Play and Stop, and at every beat (pulse modulo 24 = 0)
  while the transport runs.
* Every note length that an engine syncs to divides 768, triplets included. The phase is therefore continuous when
  the pulse wraps from 767 to 0.
* Each Play is staged as a new entry. A later value without a Play may replace only the latest eligible unpublished
  source 15 entry after the last queued Play edge, with no ONSET behind it. A matching counter in an older entry is
  not sufficient.
* At an epoch reset, set the stored Play counter to 0xFFFFFF. The first transport value of the epoch is then always
  a restart.
* Make the restart decision once per received value, and apply it to every element that follows the transport.
* If a transport event cannot be captured and staged coherently, or a Play cannot stage because the staging list is
  full, publication stops for queue reconciliation with a new epoch. While it waits, the ARM retains the latest
  transport state. Publication resumes only after the acknowledgement and the opening phase, which sends the latest
  transport value. Counter wrap or equality with an earlier value does not cancel the reconciliation.
* Intermediate Play events may be lost on this recovery path only. It adds no wire flag, does not reload the engine
  and promises no recovery from a DSP hang.
* No latency is promised. A beat entry has the latency of any entry without an ONSET behind it.

### How presses are staged

* The ARM keeps one issued copy per wanted source: the value of the last entry it staged for that source.
* At a press, the ARM stages, in this order: one PARAM or PARAM3 entry for every wanted source whose value differs
  from its issued copy, then the ONSETs of the press, lowest lane first.
* KILLs go into a pending set, not into the staging list.
* The source entries that a press stages carry its captured values. They never change after staging. A press that
  waits keeps its values when a later press stages new values. Only an eligible source-only entry, with no ONSET
  behind it, can be coalesced, as the next rule states.
* A knob change without a press stages a knob-only entry. While no ONSET stands behind it, the ARM can overwrite it in
  place with a newer value. An entry with an ONSET behind it is never overwritten.
* A control edit, a tempo value or a transport value staged without a press is a knob-only entry too, while no
  ONSET stands behind it. Source 15 also follows the rules of [Tempo and transport](#tempo-and-transport).
* A note press after a control edit carries the bank words in its snapshot, before its ONSETs.
* The ARM stages a press whole or not at all. If the staging list lacks room, the ARM refuses the press and counts it.
* Admission bound: S + L is at most 24. S is the number of entries that carry every wanted source with the PARAM3
  packing of the firmware. L is the lane count.

| Interface | Largest S | Lanes | Largest S + L |
|---|---|---|---|
| ABI minor 0: sources 0 to 11 and 14 to 21 | 12 | at most 4 | 16 |
| ABI minor 1: sources 12 and 13 added | 14 | at most 8 | 22 |

* The largest full sets in the table give this arithmetic: sources 0 to 11 take 4 PARAM3 entries, and each of
  sources 12 to 21 takes one PARAM entry. This is not an encoding rule. At ABI minor 1, source 12 can be in a PARAM3
  entry with base id 10. Sources 13 to 21 use PARAM singles, the 7-bit sources 17 to 20 included.
* A source added at a later minor must keep S + L within 24.
* An epoch change deletes the staged entries of the old epoch. The opening phase then sends every value again.
* The ARM assigns the generation bits of an ONSET when it publishes the ONSET.
* The ARM can cancel an ONSET that is not yet published. It removes the ONSET only. The source entries of the press
  are still published.

### Physical bindings

The physical control or MIDI binding that feeds a source id is internal. A later firmware can change it with the user
interface. Do not depend on a binding. Depend on the id and its value encoding.

The surfaces and the placement rule of the engine controls are not internal. They are frozen with the record format.

## Engine controls on the unit

The firmware has one renderer for every engine. It lays out the control records with one fixed rule. Records name a
surface and can force a new row. They never name a pad. The firmware has no mapping table for engines.

### Surfaces

| Surface | Where | When it exists |
|---|---|---|
| 1, Sample view of track 8 | page 1: pad rows 3 and 4; page 2: pad rows 3 and 4. Rows 1 and 2 stay the steps | when a kind 1 record or another surface 1 record exists. Page 2 exists only if a record uses it. Down shows page 2, Up shows page 1; the Down light is off on the last page |
| 2, synth page 4 | a page after the arp page (page 3): pad rows 1 to 4 and knobs 1 to 4 | when a kind 2, 3 or 4 record uses surface 2. A page with knobs only and dark pads exists. With no such record, the synth page has pages 1 to 3 only |
| 0, the existing macros | main macros (sources 0 to 7) and synth page 1 macros 2 to 4 (sources 9 to 11) | always; kind 5 gives only their defaults |

* With no surface 1 record and +46 = 0, the Sample view stays stock.
* For an engine with a low-frequency oscillator (LFO), synth page 4 is the page of its LFO controls.

### Placement

Only kinds 2 and 3 take pads in the placement rule. Kind 1 takes the sound slots. Kind 4 takes knobs only. Kinds 5
and 6 take no position.

Permitted rows:

| Surface | Choice and toggle rows | Sound slot rows |
|---|---|---|
| Sample view, with sounds and choices | page 1, rows 3 and 4 (16 pads) | page 2, rows 3 and 4 (16 slots) |
| Sample view, with choices only | page 1 and page 2, four rows (32 pads) | none |
| Sample view, with sounds only | none | page 1 and page 2, four rows (32 slots) |
| synth page 4 | rows 1 to 4 (32 pads) | none |

For each surface, the renderer places the kind 2 and kind 3 records in record order:

1. A field takes count pads for kind 2 and 1 pad for kind 3.
2. A field with the new row flag starts a new row, unless it already starts at pad 1 of a row.
3. A field that does not fit in the rest of its row starts on the next row.
4. A field that needs a row after the last permitted row refuses the file (check 17).

* Kind 1 places the sound slots left to right in their rows.
* Kind 4 records go to knobs 1 to 4 of synth page 4, in record order.
* Pad i of a choice field, counted from 0, sets value i.
* Pads that no field uses are dark and have no function.
* The firmware never scrolls, truncates or adds a page. A fifth row is refused.
* Above 32 sound slots (ABI minor 1), the Sample view holds sound slots only. Choices then go to synth page 4.

### Lights

| Pad or knob | Light |
|---|---|
| selected sound slot | bright hue of kind 1 |
| other sound slots | dim hue of kind 1 |
| choice pad of the current value | bright hue |
| other choice pads of the field | dim hue |
| toggle on | bright hue |
| toggle off | dim hue |
| any pad of a disabled field | dark |
| unused pad | dark |
| knob of a kind 4 record | its hue, from off to full with the value, as the stock macro lights |
| knob of a disabled kind 4 record; knobs 5 to 8 on synth page 4 | dark |

### Input

| Input | Effect |
|---|---|
| sound slot pad | selects the slot (source 8) and plays it; a step can take it as a sound per step |
| choice pad i | the value of the field becomes i; no note plays; never a sound per step |
| toggle pad | the field changes to its other value; no note plays |
| pad of a disabled field, unused pad | nothing |
| Clear held + a choice or toggle pad | the field returns to its default |
| kind 4 knob | sets its value 0 to 127; a disabled record ignores the turn |
| Clear held + a kind 4 knob, a main macro or synth page 1 macros 2 to 4 | the declared default of kind 4 or kind 5; without a kind 5 record for that macro, nothing changes |
| Clear held + synth page 1 macro 1 (Tune) | nothing, while an engine owns track 8; Tune is not a source and has no kind 5 default |
| Shift + pad | the same as the pad |
| busy | the Track 8 light blinks red and the edit is ignored |

* A disabled field keeps its value. The ARM still stores and sends it. The engine decides what a disabled field
  means.
* Bank and continuous values have no parameter locks and no MIDI binding. They are project values. The main macros
  keep their locks and their MIDI control changes.
* Every knob is continuous and monotonic. Put a discrete choice on a choice or toggle record, never on a knob.

### Sound slots and starting sounds

* With declared sound slots (+46 greater than 0), source 8 stays the sound slot, 0 to +46 - 1. The engine latches
  it at ONSET. A step can lock it.
* A sound slot selects a starting sound inside the engine. It changes nothing that a pad or a knob shows. The ARM
  keeps no values per sound.
* A choice never changes when a slot is selected. A slot never changes when a choice changes.
* An engine can declare no sound slots and put its choices on the Sample view. It can also declare slots and put its
  choices on Sample view page 1 or on synth page 4.

### Values per project

The firmware saves the bank and continuous values of an engine in the project. The values of sources 0 to 11 stay
where the firmware already keeps them: the macros and the sound slot of track 8.

| Item | Rule |
|---|---|
| entry | one per settings key: the key, bank 16, bank 21 and the values of sources 17 to 20 |
| capacity | six engines per project |
| order | most recently edited first |
| eviction | the first edit of a seventh engine evicts the least recently edited entry |
| lookup | at a pack load and a project load; it never writes and never changes the order |
| invalid stored value | that field uses its default; the other fields keep their stored values |
| bits and values that the current records do not declare | kept unchanged; an edit changes only the edited field |
| stored data that the firmware cannot read | read-only: the engine plays its defaults, edits are refused, the data is kept |
| read-only or busy project | edits are refused |

* Eviction loses the saved control values of that engine for this project. A later use starts from the defaults.
* A new project starts with the record defaults. The macros keep the stock values of a new project, unless a kind 5
  record gives their defaults. Kind 1 gives the default sound slot.
* An engine change at a pack load selects the entry of the new key, or the defaults. Entries of other engines do not
  change. Sources 0 to 11 keep their raw values. Kind 5 defaults are not applied at an engine change.
* A refused engine, or samples, reads and writes no control values.

### Settings key

* Kind 6 holds the settings key. It is required when the engine has a kind 2, 3 or 4 record.
* Choose a random 32-bit value, not 0, once per engine design.
* Keep the key in an engine update that keeps the meaning and the offset of every field. The stored values then
  survive the update. A new body CRC or header CRC does not change the key.
* Change the key when the meaning of a field changes.
* The tools refuse a key that the release catalog already lists. This is not a check against every engine that exists.

## Notes and lanes

* The ARM owns the allocation of notes to lanes and the stealing of voices.
* The engine declares 1 to 8 lanes (+44). More than 4 lanes needs LANES8 on both sides (ABI minor 1).
* Polyphony equals the lane count. A mono engine declares 1 lane.
* The ARM ranks voices with the declared release tail (+48). With 0, it uses 15,669 blocks. The ranking is a choice
  of the allocator. It is not a proof that a voice is silent.
* With VOICEMAP, the ARM can read ready header word 6 and rank a silent lane as free.
* User actions do not guarantee an off command. The ARM can cancel an ONSET that is not yet published, and a closed
  queue publishes nothing. Do not assume that every accepted ONSET gets a RELEASE.
* Arpeggiator notes use the ordinary off. With poly off, a lane replacement also sends KILL.
* The engine does not choose voices in engine ABI 3.

Note commands in short:

| Command | Pending ONSET of the lane | Sounding note of the lane | Generation |
|---|---|---|---|
| ONSET, ONSET NOTE | not defined by the ABI; the generation rule still applies | damped, then the new note starts | stored if it is the stored value plus 1; else no effect |
| KILL | cancelled if the generation matches | stops quickly if the generation matches | unchanged |
| RELEASE | cancelled if the generation matches | goes to its release if the generation matches | unchanged |
| ALL OFF | all cancelled | all stop quickly | all set to 0 |

## Health

### Ready header

X:0x4E90 to X:0x4E9F. The file is authoritative. The ready header must match it.

| Word | Address | Content |
|---|---|---|
| 0 | X:0x4E90 | MAGIC 0xC0A10D |
| 1 | X:0x4E91 | VERSION 0x000300 + minor, equal to header +20 |
| 2 | X:0x4E92 | READY 0x524459, written last |
| 3 | X:0x4E93 | INIT_COUNT |
| 4 | X:0x4E94 | engine capabilities, equal to bits 0 to 23 of header +32 |
| 5 | X:0x4E95 | bits 3 to 0 lanes; bits 12 to 8 K; equal to header +44 and +45 |
| 6 | X:0x4E96 | voice activity bitmap: bit l = lane l sounding (VOICEMAP) |
| 7 | X:0x4E97 | voices dropped for load, saturating (SHED) |
| 8 | X:0x4E98 | engine status: bit 0 a REQUIRED table without FALLBACK is invalid; bits 1 to 7 reserved; bits 8 to 23 engine-defined |
| 9 to 15 | X:0x4E99 to X:0x4E9F | reserved, 0 |

### TICK and FAULT

* TICK counts block entries. The engine increments it before queue work, also in fault and epoch blocks. TICK does
  not show that output or consumption completed.
* During the trial, the firmware checks every 1 s of eligible time that TICK advances by at least 0.75 per ms.
* The firmware reads CONS and FAULT under a 50 ms policy while entries are outstanding. This is not a watchdog.
* FAULT not 0, or a distance over 16, closes the queue. The firmware marks the resident engine failed and opens a new
  epoch on a later pass.

### Shim counters

The shim keeps counters in its data words. The ARM reads them and never resets them. They restart at the next DSP
load. They count modulo 2^24. `card engstatus` shows them.

| Counter | What it proves | What it does not prove |
|---|---|---|
| arrival count | the engine reached its jump to P:0x2004 that many times since the load | that the 32 output words were written, that SP and SC matched, that the mixer call completed, or that audio reached the output |
| stack mismatch count | SP or SC differed from their entry values at the return | the state of the stack below the entry level |
| overrun count (meter) | armed normal blocks whose span exceeded the budget | the cost of the stock program, the shim save and restore, and the mixer: they are outside the span |

* The arrival count is diagnostic only. No verdict uses it.
* The span is read to read: from the last timer read of the shim before the engine to the first timer read after
  P:0x2004. It contains the block code of the engine and a fixed shim overhead F.
* The meter also keeps the largest span of a normal block since the load, F included. This value is a maximum, not a
  counter. `card engstatus` shows it.
* The ARM sets the budget B once per load, after check 14 and before the first epoch. B is the declared cycles (+52).
  Check 13 has already bounded it to 1 to E, so every admitted engine runs armed. The meter counts the overruns at run
  time.
* The production shim of platform 1 has the meter. The meter uses timer 1 of core 0.
* F is 64 to 66 cycles on platform 1. The largest null-engine span observed was 196 cycles; other work can fall inside a
  span. F is inside every span and is never subtracted: the declared cycles, B and E all include it.
* E is in [Cycles per block](#cycles-per-block).

### TRY and BAD

* A new engine starts on trial (TRY). After 10 s of healthy time, the firmware confirms it.
* During the trial, these failures fail it: a TICK stall, a FAULT, the DSP fault state, and an overrun count above its
  value at arming.
* A failed trial blocks the engine body for the rest of the session. A later pack load plays samples.
* A stack mismatch, in any phase, marks the resident engine failed and fails the trial. The firmware will not load that
  engine again for the rest of the session. The engine stays resident until the next engine load or restart: the shim
  keeps calling it and repairing the stack every block. Do not rely on its output after a mismatch. The 0.3.1 platform
  qualification exercised this path on a unit with a test engine. Keep SP and SC balanced; never depend on the mismatch
  path.
* After a warm reset, an unconfirmed trial whose queue opened gets one retry. A trial that failed, that never opened
  its queue, or that was already retried becomes BAD. BAD blocks that engine body.
* The BAD marker survives a warm reset. A power cycle clears it.
* After confirmation, the firmware does not check TICK progress. FAULT, a queue distance over 16, a later initialization
  or a bounded DSP wait can still report a failure. After confirmation, the meter still counts overruns and
  `card engstatus` shows them; they are reported, not a failure.

### When track 8 plays samples

| Condition | Track 8 |
|---|---|
| an admission check fails | samples; `card engstatus` shows the reason |
| no ready header within 500 ms after the load | one stock DSP load, then samples |
| ready header words 1, 4 or 5 differ from the file | one stock DSP load, then samples |
| a DSP answer during the load takes over 500 ms in one wait | one stock DSP load, then samples |
| the trial fails and the firmware will not load the engine again this session, or the body is marked BAD | samples at the next pack load |
| a REQUIRED table without FALLBACK is not accepted | silent until the next load, not samples; status "table failed" or "bank missing" |
| a table with FALLBACK, or not REQUIRED, is not accepted | the engine plays with its default or without the table |
| the pack load ends without publication, for example when it does not traverse all 128 sample slots | the engine stays resident without publication; note-driven track 8 is silent until the next pack load |
| the DSP fault state | no promise of audio; see below |

### If the unit stops answering

* A hang of core 0 also stops the stock program. The firmware cannot interrupt a loop of the engine without a DSP
  reset.
* A hang sets the DSP fault state only at the next bounded DSP wait. The fault state stays until the unit restarts.
  Rec blinks white ([install.md](install.md#rec-blinks-white)).
* The firmware cannot recover from the fault state without a power cycle.

If Rec blinks white soon after you chose an engine:

1. Save the project.
2. Set the pack to Samples with the loader. Card writes do not need the DSP.
3. Power-cycle the unit.

A power cycle clears the BAD marker. Without step 2, the same engine loads again at power-on.

Recovery without a power cycle, a persistent BAD marker on the card and a swap of the engine without a pack load are
not part of engine ABI 3.

## Building and conformance

The conformance runner is in development. It runs the engine in a DSP emulator. It composes core 0 locally from your
own copy of the official firmware file. No stock code is distributed.

Result layers:

| Layer | Proves | Does not prove |
|---|---|---|
| file check (`engine_file.py check3`) | the file rules: header, CRCs, descriptors, control records (check 17), metadata, shim image, entry vector | that the engine behaves |
| static scan | no SP, SC, SSH, SSL, SZ or EP names; no write outside the writable list, where the scan can resolve it; static stack depth at most G (11 on platform 1) | the paths that the scan cannot resolve; the instruction sequence restrictions of Appendix A ([Restricted instruction sequences](#restricted-instruction-sequences)) |
| emulator conformance | the behaviour below, in the emulator | hardware behaviour or silicon timing |
| bench on a unit | hardware behaviour of the tested scenes | other scenes |

A file that passes the file check is not conformant. An engine that is conformant in the emulator is not
qualified on hardware. Emulated timing is not silicon timing.

### Restricted instruction sequences

Appendix A of the DSP56300 Family Manual restricts some sequences of instructions. These restrictions bind the DSP on
the unit. The emulator of the conformance runner does not detect them. A sequence that breaks one can play correctly in
the emulator and corrupt the hardware stack on a unit.

An engine must obey every restriction of Appendix A that applies to its code. These sections apply to every engine:

| Manual section | Restriction |
|---|---|
| A.3.1 | The last six words of a hardware loop, LA-5 to LA. Each position has its own list of forbidden instructions. A move to or from program memory counts as a MOVEM and is forbidden at LA-1 and LA-2. Jumps and calls are restricted near LA. Count program words, extension words included. |
| A.3.2 | Loop setup: the instructions before a DO or DOR, and the loop count. |
| A.3.3 | The instructions before an ENDDO. |
| A.3.4 | The instructions directly before a BRKcc: arithmetic and compare instructions, conditional instructions and control register writes. |
| A.3.5 | The instructions before an RTS. |
| A.3.6 | The separation between a write of SP or SC and a later SSH or SSL access. An engine never names these registers ([Stack](#stack)). |
| A.3.8 | The instructions that REP must not repeat. |

* Sections A.3.7, A.3.9, A.3.10, A.4 and A.5 cover fast interrupt routines, stack extension, peripherals and
  sixteen-bit mode. The engine rules of platform 1 exclude all of these: an engine has no interrupt routine, uses no
  stack extension, writes no peripheral register and keeps sixteen-bit modes off.
* The delays of section A.2 change cycle counts only. The declared cycles, measured on a unit, include them.
* Check every reachable path: loop exits, returns, and instructions reached through a branch or a call.

**What the firmware sees.** The firmware cannot detect a forbidden sequence. When a broken sequence corrupts the
stack, the shim counts a stack mismatch at the return: the trial fails and the firmware will not load the engine again
this session ([TRY and BAD](#try-and-bad)). A corrupted stack can also stop the DSP before the shim runs
([If the unit stops answering](#if-the-unit-stops-answering)).

**Check the listing.** From the exported tree, run the sequence check on the assembled listing of the engine, at the
addresses and instruction lengths of the file:

    python3 -I tools/dsp/abi3/sequence_warning.py path/to/assembled.lst

It reports the forbidden patterns that a listing shows, for loops started by DO and by DOR. A clean report does not
cover rules that depend on execution order, register state, memory placement or mode. Review those by hand.

A new engine must pass, in the emulator:

* the file rules;
* initialization from a cold start, across several blocks, and after READY = 0 at run time;
* READY and ACK without waiting for HELLO or a table;
* output: all 32 words in every block, the correct half, no alias writes;
* entry state: varied SR, LA, LC and address modifiers from the caller; the return with SP and SC unchanged; bit 13
  of SR never set; every changed SR mode, bit 17 included, restored before the jump to P:0x2004;
* the queue: all 16 slots, sequence and epoch wrap, distances 0, 16 and 17, wrong commit, wrong epoch, sticky FAULT;
* every command, every reserved bit and every field out of range, consumed with no effect;
* the generation rules of ONSET, KILL, RELEASE and ALL OFF, pending and sounding;
* minimum service: exactly min(K, d) entries per normal block at depths 0 to 16, and none in abnormal blocks;
* replies (ABI minor 2): a reply read after more than 16 later entries, cancellation, epoch and wrap;
* tables (ABI minor 2): required, optional and FALLBACK tables; out-of-order offsets; sum failure; a new BEGIN; a
  completed table that stays valid after an epoch reset and becomes invalid at an initialization;
* bank words: a field at or above its count, or an undeclared bit set, has no effect as a whole;
* transport: a continuous phase across the wrap of the pulse for every synced note length; the same restart
  decision for every element that follows the transport; a restart after every epoch reset;
* the worst normal block within the declared figure, in both worst cases of [Fixed header](#fixed-header). This is a
  check in the emulator. The declared figure itself is measured on a unit.
* outside the emulator: a clean `tools/dsp/abi3/sequence_warning.py` report on the assembled listing, and a review of
  the Appendix A rules that the tool cannot check ([Restricted instruction
  sequences](#restricted-instruction-sequences)).

The engine review also checks these rules, which a test cannot prove:

* Every knob is continuous and monotonic.
* No discrete choice hides in a knob.
* The selection of a sound slot changes nothing that a pad or a knob shows.

The tools and every site of the firmware give the same check 17 verdict for every refused control layout. The
firmware side of the controls (renderer, project values, staging, tempo and transport) has its own tests. An engine
author does not run them.

Engines on the release page pass the conformance runner before they ship.

## Versioning and compatibility

The stability promise starts with the first firmware release that ships engine ABI 3. From that release, within ABI
3.x:

* the items of the frozen scope below keep their encodings and their required effects;
* an engine file for a platform id keeps loading on later 3.x firmware that composes that platform
  ([Same-platform portability](#same-platform-portability));
* the firmware also checks the exact composition of the stock program and the integration words.

A firmware with a different composition must be qualified again. The grants of a platform (data windows, stack depth G
and budget E) belong to its platform contract. E is frozen per platform id: a firmware that changes E carries a new
platform id, so a declaration is never judged against another budget. Until the first release ships, this page can
change.

### Frozen scope

| Item | Scope |
|---|---|
| queue transport: X:0x4D00 to X:0x4D4F layout, entry words, commit rule, epoch handshake, FAULT conditions | frozen; identical to the older interface |
| ready header words 0 to 3 and the initialization order | frozen |
| ready header words 4 to 15, the reply block | frozen |
| CRE3 fixed header, table descriptors and metadata rules | frozen; new fields only in reserved fields, defined at a higher minor |
| platform contract: shim vectors, accepted shim images, entry state, return rules, memory grants, stack grant G, budget E, output facts | frozen per platform id; a change of E takes a new platform id |
| command encodings of ABI 3.0 and their required effects | frozen |
| source ids 0 to 11 and 14 to 21 at ABI minor 0, 12 and 13 at minor 1, and their value ranges | frozen; new ids only at a higher minor |
| control records: the byte format, the kinds, the hue ids, the surfaces and the placement rule | frozen for format minor 0; an extension needs a higher format minor, and a new source also needs a higher ABI minor |
| minimum service per block | frozen |
| reply protocol | frozen from ABI minor 2, after the QUERY answers are decided |
| minor versions | additive only |

### Internal

These can change with any firmware update:

* the pacing of the ARM: writes per pass, entries per write, the knob reserve, the coalescing intervals;
* the liveness thresholds and the recovery scheduling, never stricter than [Health](#health) states;
* which physical control or MIDI binding feeds a source id, except the surfaces and the placement rule of the engine
  controls;
* the exact light levels of the hues;
* the card file names and the loader;
* the internals of the shim.

### Rules for change

* A minor version only adds: new commands, capability bits, HELLO words, sources and metadata tags.
* New behaviour never comes from a reserved field. It needs a capability bit or a minor, declared by the engine and
  offered by the firmware.
* The firmware never sends a command, field or source that the engine did not declare.
* A change of the transport, of a 3.0 encoding or of an effect in the frozen scope needs a new major version.

| Case | Result |
|---|---|
| an engine of minor 0 on a firmware of a higher minor | loads; the firmware sends only what the engine declared |
| an engine of a higher minor than the firmware | refused, samples |
| an engine that needs a host capability the firmware lacks | refused, samples |
| an engine for another platform id | refused, samples; rewrap it for that platform and test it again |
| a CRE2 engine on an ABI 3 firmware | refused at the magic check, samples, reason shown |
| a CRE3 engine on a firmware with the older interface | refused at the magic check, samples |

### Same-platform portability

* An engine file built for platform id p loads on every later 3.x firmware that composes p, from the first release
  that ships engine ABI 3.
* A firmware composes p only if it accepts every shim image ever pinned for p. The accepted set grows. It never
  shrinks.
* A shim change that cannot keep an older image acceptable takes a new platform id. Files for the old id are then
  refused with the reason "platform". Rewrap them.
* The platform id names a written contract in the firmware sources. It is not a checksum and not an authentication.
* The firmware also checks the exact composition of the stock program and the integration words. A damaged or foreign
  firmware image is still refused.
* The firmware accepts platform id 1 only with the exact composition that is qualified for it. Platform 1 is qualified
  by the 0.3.1 platform qualification.
* The shim CRC is an integrity check against accidents and wrong tools. It is not an authentication.

## Known limits

### Memory

| Item | Value |
|---|---|
| engine words in P | at most 7,808 (P:0x2180 to P:0x3FFF); the shim takes 384 of the 8,192-word region |
| smallest body | 389 words |
| granted X and Y | windows 0 to 2, 6,288 words |
| stack depth G | 11 locations below the entry (D = 2, H = 2) |
| preloaded X or Y data | not possible: P only is loaded |

### Cycles per block

| Item | Value |
|---|---|
| one block | 163,840 DSP cycles nominal, about 0.666 ms |
| platform budget E | 65,626 cycles, measured on a unit |
| fixed shim overhead F inside the span | 64 to 66 cycles; the largest null-engine span observed was 196 |
| shim save and restore | about 150 cycles, an estimate; outside the span |
| meter work outside the span | about 40 to 60 cycles, an estimate |
| FM engine 2.0, declared cycles | 65,626 cycles; its largest measured normal block was 64,940 cycles |

* E is the largest engine span measured on a unit with no dropout in the audio capture and clean health while the heavy
  reference runs: a stock scene that plays all eight tracks with reverb on. The measurements are in the 0.3.1 acceptance
  record. In them, the armed budget B is above the spans being measured, so the overrun count is a report, not the
  criterion.
* E stands only as measured on the unit. It is not computed from the block period or from other figures, and no reserve
  is added to it or taken from it.
* Do not add other cycle figures to E, or subtract them from the block, to judge a fit. A block of core 0 also contains
  waiting for core 1, so the figures are not additive.
* E is a span of the meter, in the same domain as the declared cycles. It includes F.
* The FM engine 2.0 declares E. In the 0.3.1 acceptance record, its largest normal block was 64,940 cycles and its
  largest initialization block 24,256 cycles, with no overrun, fault, stack mismatch or dropout in three scenes.

### Queue latency

These figures are conditional arithmetic, not wall-clock guarantees. They assume K = 4, entries published as fast as
the ring frees, no new priority entries and no cancellation. The ARM adds its own staging and publication time, which
is not known. The ARM promises no fixed write size and no fixed pass rate.

| Case | Normal blocks | Time |
|---|---|---|
| one ONSET, empty ring | 1 | 0.7 ms |
| largest press at ABI minor 0: 16 staged entries and 4 KILLs, empty ring | 5 | 3.3 ms |
| largest press at ABI minor 1: 22 staged entries and 8 KILLs, empty ring | 8 | 5.3 ms |
| largest press at ABI minor 1, behind 12 knob-only entries | 11 | 7.3 ms |
| any entry already in the ring | at most 4 | at most 2.7 ms |

* While the transport runs, source 15 adds 2 to 4 entries per second at normal tempos. A tempo change adds one
  entry.
* Tempo and transport entries have no latency limit.

| Data through the queue | 256 words | 4,096 words |
|---|---|---|
| DATA at 4 entries per block | about 43 ms | about 0.68 s |

### Engine controls

| Item | Value |
|---|---|
| control records | 16 per engine, 8 bytes each |
| Sample view | 32 pads; with choices and sounds, 16 choice pads and 16 sound slots |
| synth page 4 | 32 pads and 4 knobs |
| bank words | 2, 24 bits each, at most 16 fields in all |
| engines with stored values per project | 6; the least recently edited entry is evicted |

### File size

| Item | Value |
|---|---|
| USB writer staging area | 15,360 bytes for the whole file |
| largest CRE3 file over USB, H = 64 | n = 5,098 words, 15,358 bytes: 4,714 engine words |
| largest CRE3 file over USB, H = 4,096 | n = 3,754 words: 3,370 engine words |
| largest CRE3 file | 4,096 + 3 x 8,192 = 28,672 bytes |
| card scan | files under 32,768 bytes |
| card reader | the whole engine region |

* The shim words count against the USB limit. The production shim adds 1,152 bytes to every engine file.
* The USB writer accepts a file only if H + 3n is at most 15,360 bytes.
* The FM engine 2.0 file is 15,346 bytes. It fits the USB writer.
* Install a larger file with a card reader ([synth.md](synth.md#select-the-fm-engine-with-a-card-reader)). This firmware
  has no append job for larger files.

### Load time

* The bootstrap sends about 25.6 us per word. An 8,192-word body adds about 83 ms of wire time over a 4,958-word
  engine, plus its card reads.
* A DSP load outside a pack load loses every sample. Per-project engines are therefore not part of engine ABI 3.

## The FM engine port

The FM engine 2.0.0 is the FM engine of 0.3.0, ported to engine ABI 3. It keeps the file name `fm.dse`, so a pack that
selects FM needs no change. In the reference scenes in the emulator, its output is word for word the output of FM 1.0.0.
Engines ship on their own cadence; the [changelog](../../changelog.md) lists each release.
