# Arrhythmia SysEx commands

Every command Arrhythmia 0.1 adds, in both images unless noted. All use the prefix `F0 00 20 29 00 60 <cmd>` and end
with `F7`. Source of truth: the header of `patches/src/devkit_abi3.c` and the files it names. Marks as in
[README.md](README.md); DESIGN means the command is specified and tested in emulation, HW that it was used on the unit.

How we know: our design (the commands are our code); the commands marked HW were used on a unit.

Conventions:

* `Nx7` is a number sent as N bytes of 7 bits, most significant group first.
* "Nibbles" are 4-bit values, one per byte, high nibble first.
* Malformed requests get no reply unless the table says otherwise. Every command except `08`, `7D` and `7E` is
  silent if Arrhythmia's state block failed its check at boot.
* Commands are handled in the firmware's Main task. Keep requests sequential: send the next one after the reply.
* For the update protocol (`70` to `73`) and the identity queries, see [flashing.md](flashing.md). Never send `77`.

| Cmd | Request (after `60`) | Reply (after `F0 00 20 29 00 60 <cmd>`) | Purpose | Mark |
|---|---|---|---|---|
| `00` | `00` | none | stop any module or light show, normal display | HW |
| `01` | `01 dens decay` | none | glitter light show | DESIGN |
| `02` | `02 k` (k 0..7, `7F` all) | none | static: light the LED bytes whose index has bit k set | DESIGN |
| `03` | `03 hi lo v...` | none | static: set LED bytes from index `hi * 128 + lo` to `v << 1` | DESIGN |
| `04` | `04 a2 a1 a0 (hi lo)...` | none | module upload: write the nibble pairs as bytes at ITCM 0x400 + `a2 << 14 \| a1 << 7 \| a0`; stops a running module first; refused while the picker reads the card | HW |
| `05` | `05` | the `7F` status reply | start the uploaded module after checking magic, size and CRC; refused unless ITCM was written since boot | HW |
| `06` | `06 00` open picker, `06 01 p` press pad p, `06 02` cancel, `06 7F` status | `<phase> <mode> <fresh> <count> <pick> <result> <bad>` | microSD module picker from the host, same paths as the pads | DESIGN |
| `06` | `06 03 i` | `03 i <12 ASCII bytes, 0-padded>` | short name of the file on pad i + 1 (only while choosing) | DESIGN |
| `08` | `08 sel a2 a1 a0 n` | `sel a2 a1 a0 n <status> <6 nibbles per word>` | DSP X read of the load meter's windows (arrhythmia-devkit only; arrhythmia-firmware answers every `08` with status 0) | HW |
| `09` | `09 p` (p 0 or 1) | `p <seq 5x7> <16 entries x 8 nibbles>` | input log, last 32 events | HW |
| `0A` | `0A on` (0 or 1) | the `7F` status reply | hand the controls to the running module (1) or give them back (0) | DESIGN |
| `0B` | `0B` | `<clocked> <running> <pulse> <bpm 2x7> <pulses 5x7> <starts 3x7> <stops 3x7>` | module clock status | DESIGN |
| `0C` | `0C 00` status, `0C 01 t m` set | `<sub> <status> <why> <gen 3x7> <writes 3x7> <site> <m0..m7>` (26 bytes) | stored track modes in the project extension block | DESIGN |
| `0D` | `0D` | `<fault> <last site> <timeouts 3x7> <site mask 2x7> <refused 3x7> <late 3x7> <pack failed>` (22 bytes) | DSP fault report | HW |
| `0E` | `0E 00` status, `0E 01 scale root bits` set | `<sub> <status> <why> <sc> <scale> <root> <bits> <applied>` (16 bytes) | scales for tracks 1 to 7 | HW |
| `7D` | `7D a4 a3 a2 a1 a0` | `<addr 5x7> <64 nibbles>`, or `<addr 5x7>` alone when refused | read 32 bytes of module ITCM | HW |
| `7E` | `7E` | `<limit 5x7> <cursor 5x7>` | heap limit and cursor | DESIGN |
| `7F` | `7F` | `<mode> <ok> <cursor 5x7> <frames 4x7> <module>` (20 bytes) | status | HW |

## Details

**`06` picker.** phase 0 idle, 1 reading the folder, 2 choosing, 3 loading. result 0 none, 1 ok, 2 no card, 3 no
folder, 4 folder read error, 5 file object busy, 6 open error, 7 bad size, 8 read error, 9 bad module, 10 file object
taken over. `count` to `bad` are 0 while the loader is idle. See [modules-reference.md](modules-reference.md).

**`08` DSP read.** `sel` 0 = core 0 X, 2 = core 1 X; address `a2 << 14 | a1 << 7 | a0`; n = 1 to 16 words. status 1 =
read; status 0 = refused, no words, nothing touched. Only ranges wholly inside the meter windows are served (core 0
X:$5C00..$5E3F, core 1 X:$2C00..$2E3F). **Read only while the unit is idle**: transport stopped, nothing loading. One
16-word read takes about 1.8 ms on the unit (HW). `tools/dspload.py` does this for you
([dsp-meter-and-budget.md](dsp-meter-and-budget.md)).

**`09` input log.** `seq` counts all events since boot; entry k of page p is ring slot 16p + k, and event i (0-based)
lives in slot i mod 32. An entry is `kind | index << 8 | value << 16`: kind 1 pad (value = velocity | on << 8),
2 button (value 1 down, 0 up), 3 pot (value), 4 pressure (value), 5 encoder (value = signed 16-bit delta). Pressure
streams while a pad is held, so a slow poller loses events (HW). `tools/inputlog.py` polls both pages and reports
losses.

**`0B` clock.** `clocked` 1 while a module with a clock entry runs; `running` the transport as last seen; `pulse` the
bar pulse 0..95; `pulses`, `starts` and `stops` count since boot.

**`0C` track modes.** Sets the stored mode byte of track t (0..7) to m (0..3: Sample, Chord, Synth, Voice-of). In
0.1 nothing plays these modes; the byte is stored and reported. status 0 refused, 1 ok, 2 busy, 3 read-only. why
0 OK, 1 EMPTY, 2 no project, 3 bad magic, 4 bad version, 5 bad flags, 6 bad length, 7 bad CRC. site 0 attach, 1
load or absent slot, 2 clear, 3 pack switch. mN is the stored mode (`7F` if above `7E`). See
[design/project-extension-block.md](design/project-extension-block.md).

**`0D` fault report.** fault 1 = a guarded DSP wait timed out since boot; `last site` bit 6 = ended by the backstop.
See [design/o1-fault-handling.md](design/o1-fault-handling.md) for the sites and what each counter means.

**`0E` scales.** scale 0..9, root 0..11 (0 = C), bits = tracks 1..7 scaled (bit 0 = track 1). status 0 refused (bad
arguments or no project), 1 ok, 2 busy, 3 read-only; why as for `0C`; sc 0 unset, 1 version 1, 2 unknown; `applied`
lists the tracks whose keyboard already holds the current scale. See [design/scales.md](design/scales.md).

**`7D` read-back.** Address `a4..a0` as 5x7, 32-byte aligned. Serves only ITCM 0x00000100 .. 0x00003FFF (module RAM
and code), and only while ITCM is enabled. Every other address, internal flash and the bootloader included, gets the
13-byte refusal with no data (HW). `tools/module.py` uses it to verify an upload.

**`7F` status.** mode 0 normal, 1 glitter, 2 static lights, 3 module, 4 picker. `ok` = 1 | owned << 1 (owned = the
module has the controls). `cursor` is the heap cursor, `frames` counts LED frames. `module` 0 none, 1 valid, 2 bad,
3 ITCM unavailable.

## Unknown command numbers

A message with the `00 60` prefix and an unknown command number is swallowed by Arrhythmia (no reply, DESIGN). Send
these commands only to a unit whose `70` reply shows an Arrhythmia build.
