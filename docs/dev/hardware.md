# Hardware

What the Circuit Rhythm is made of, as far as it matters for code, and what we measured on it. Marks as in
[README.md](README.md).

## Chips

How we know: static analysis (kept to what is needed to build code for the unit), sizes observed on hardware.

| Part | What a developer needs | Mark |
|---|---|---|
| MCU | Cortex-M7 of the STM32F745 class: 320 KB SRAM ending at 0x20050000, 16 KB ITCM, application in internal flash from 0x08010000. The part number on the chip was not read | STATIC |
| DSP | Freescale DSP56724: two DSP56300 cores, reached from the ARM over one SPI host bus with a select line per core | STATIC |
| External NOR | 32 MB: projects, samples, packs, a welcome drive and the firmware update staging area | HW (size) |
| microSD | read by the firmware as its second drive | HW |

## USB

How we know: observed on hardware (USB descriptors, the mass-storage disk read on the unit).

| Item | Value |
|---|---|
| Vendor / product | `1235:0138`, "Focusrite A.E" / "Circuit Rhythm", USB 2.0, bcdDevice 2.00, 500 mA |
| Interfaces | Audio Control, MIDI Streaming ("Circuit Rhythm MIDI"), Mass Storage ("Novation Circuit 1.00") |
| Audio streaming | none: the unit does not stream audio over USB |
| Mass storage | 33,554,432 bytes, read-only (the device reports write protection), logical block size 4096 |

The mass-storage disk is a raw read-only view of most of the external NOR. Its first 256 KB are a FAT12 volume
"RHYTHM" (the welcome drive); a FAT16 volume "RHYTHM" with the content (packs, samples, projects, global settings)
starts at disk offset 0x40000. A host mounts only the first one; the second is reachable by offset, for example with
`losetup -o 0x40000 -r` on a backup copy. The last 0x94000 bytes of the disk read as zero, and the microSD card is not
visible over USB at all. The unit's serial number appears three times in the welcome drive's pages as served, in
place of a placeholder, so a disk image is not byte-identical to the NOR there. See
[backup-and-recovery.md](backup-and-recovery.md).

A Windows MIDI port has one user at a time: close any other MIDI program (or the installer tab) before a tool opens
the port.

## Memories

### RAM for Arrhythmia and modules

How we know: measured on hardware (heap cursor); our design (the blocks).

* The heap is filled at boot and its cursor does not move afterwards: after boot and after heavy use it stood at the
  same place, **3,272 bytes below the heap limit** (HW, read with a diagnostic build). Memory is not given back, so
  do not count on allocating at run time.
* Arrhythmia takes its state from the top of the heap once at boot, by lowering the heap limit (DESIGN):

| Block | Range | Size | Images |
|---|---|---|---|
| devkit state (LED buffer, module ring, picker) | 0x2004F560 .. 0x2004F760 | 512 B | both |
| clock block (module clock, fault state) | 0x2004F540 .. 0x2004F560 | 32 B | both |
| project extension runtime copy and p-lock lanes | 0x2004F440 .. 0x2004F540 | 256 B | both |
| p-lock base values | 0x2004F400 .. 0x2004F440 | 64 B | both |
| settings page | 0x2004F3E0 .. 0x2004F400 | 32 B | firmware |
| track 8 note tap and chord engine | 0x2004F0E0 .. 0x2004F3E0 | 768 B | firmware |
| scales runtime copy | the 32 B below the lowest block above | 32 B | both |

  The heap limit becomes 0x2004F0C0 on arrhythmia-firmware and 0x2004F3E0 on arrhythmia-devkit, which leaves 1,576
  and 2,376 bytes above the measured cursor (computed, not remeasured on the 0.1 images). SysEx `7E` reports the
  limit and the cursor on a running unit ([devkit-sysex.md](devkit-sysex.md)).

### ITCM (16 KB at 0x00000000)

How we know: observed on hardware.

**No writes by the stock firmware observed in the tested sessions.** A canary pattern written to all 16 KB came back
with zero changed bytes after a play session that included recording. That is not proof that no stock path ever
writes it. ITCM is core-only (no DMA) and zero wait state. Its contents do not survive power-off
(a power-up leaves about 6 % of bits different from the previous one), but they do survive the warm reset after a
firmware flash, which is why the module loader starts only a module written since boot. Arrhythmia uses 0x100 .. 0x3FF
as module RAM and 0x400 .. 0x3FFF for module code; the first 256 bytes are never touched (DESIGN;
[modules-reference.md](modules-reference.md)).

### Internal flash

How we know: static analysis (kept to what is needed to place patch code).

The application region is 0x08010000 .. 0x0807FFFF (448 KB, the bootloader's size cap,
[flashing.md](flashing.md)). The stock image ends at 0x0806A570; Arrhythmia's code and host table start there.

## Inputs

How we know: observed on hardware (every control logged with SysEx `09`). Index maps are in [maps.md](maps.md).

* 28 buttons, 32 pads with velocity and pressure, 8 endless macro encoders, 2 absolute pots.
* Pressure events **stream continuously while a pad is held** (482 in one mapping session), so a host that polls the
  32-event log can lose events during long holds.
* Pad velocity seen at the decoder: 49 .. 249 on a 0 .. 255 scale; modules get 1 .. 127.
* Encoder deltas per event were -6 .. +7 (acceleration). Clockwise is positive, but single events can show the wrong
  sign from wiper bounce: integrate, do not trust one event.
* Master Filter and Master Volume are absolute pots, 0 .. 255, rising clockwise. Master Filter is a pot, not an
  encoder. Modules never receive the pots.

## Audio and jacks

How we know: measured on hardware ([audio-capture.md](audio-capture.md)).

* **The right main output leaves one sample (at 48 kHz) ahead of the left**; the lead followed the cable in a
  cable-swap test. L and R gains agree within 0.05 dB.
* The noise floor at the main outputs was -76 dBFS (1 ms peak) on a quiet bench, and -50.7 dBFS with hum and hiss
  when the unit stood against a TV.
* The clock-out jack carries a beat pulse, not audio.
* The metronome (Shift + Clear) is off after every boot.

## Panel behaviour worth knowing

How we know: observed on hardware.

* **Bootloader mode:** hold Sample Rec + Sample + Note while turning the unit on. The pads show the installed
  application build as hex digits on rows 3 and 4 (for example `72 29` for build 7229). Play leaves it.
* The microSD card is read at power-on: change cards with the unit off.
* A stored scene chain in a project switches patterns when Play is pressed. That is stock behaviour, easy to mistake
  for a bug in your own code.
