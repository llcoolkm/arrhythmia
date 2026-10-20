# Arrhythmia developer reference

This section is for people who want to build for the Novation Circuit Rhythm: modules, host tools, or their own
firmware patches. It collects what the Arrhythmia project measured on the unit, the wire protocols it uses, and the
designs of its own code. The user guide for modules is [../user/developers.md](../user/developers.md); start there if
you only want to write a module.

**No Novation code or data is included.** These pages describe protocols, behaviour seen on the device, measurements
and our own designs. They contain no disassembly, no stock code or data bytes and no maps of Novation's internal code.
Addresses that appear are either hardware addresses, addresses inside our own code and data, or patch points that the
published Arrhythmia patches already use.

## Evidence levels

Every claim carries one of these marks.

| Mark | Meaning |
|---|---|
| **HW** | verified on hardware: seen or measured on a real unit (one unit, firmware 5706 or an Arrhythmia build) |
| **EMU** | emulated: the stock or Arrhythmia image run in an emulator; not checked on a unit |
| **STATIC** | static: read from the image, a datasheet or the reference manual, or computed; not run |
| **DESIGN** | our own design: what our code is written to do; tested as stated next to it |

Each section also starts with a "How we know" line: observed on hardware, measured, our design, or static analysis.
Facts from static analysis are kept to what is needed to build for, patch or safely use Arrhythmia.

Where a page says "one unit", no second unit was available. Figures without a mark inherit the mark of their section.

## Pages

| Page | Contents |
|---|---|
| [hardware.md](hardware.md) | chips, USB identity, memories and what is free, inputs, audio outputs |
| [flashing.md](flashing.md) | the `.syx` format, the update protocol, the install rule, device identity queries |
| [backup-and-recovery.md](backup-and-recovery.md) | NOR backup over USB, microSD backup, going back to stock |
| [devkit-sysex.md](devkit-sysex.md) | every Arrhythmia SysEx command (`F0 00 20 29 00 60 cc`) with its reply |
| [modules-reference.md](modules-reference.md) | module file format, host table (ABI 3), clock, picker, rules for module code |
| [dsp-meter-and-budget.md](dsp-meter-and-budget.md) | DSP memory and what Arrhythmia uses, the cycle budget, measured load |
| [maps.md](maps.md) | LED, input and macro knob maps |
| [audio-capture.md](audio-capture.md) | recording the unit's output for comparison, and what we measured |
| [rhythm-vs-tracks.md](rhythm-vs-tracks.md) | differences from the Circuit Tracks, for developers coming from that mod |
| [design/project-extension-block.md](design/project-extension-block.md) | our data block inside the project file |
| [design/plock-fix.md](design/plock-fix.md) | the p-lock carry-over fix |
| [design/scales.md](design/scales.md) | scales for sample tracks 1 to 7 |
| [design/o1-fault-handling.md](design/o1-fault-handling.md) | bounded DSP waits, the sticky fault and its report |

## Tools

All tools named here are in the published `tools/` folder and use the Python standard library unless noted. Run them
from the top folder of the Arrhythmia files with `python3 -I tools/<name>.py` (or `uv run python -I ...`). MIDI
tools take a raw MIDI device path; on Linux that is `/dev/snd/midiC<card>D<device>` (find it with `amidi -l`).

## Warnings

* **Never send SysEx `F0 00 20 29 00 77 ...`** to a Circuit Rhythm. On stock firmware it erases the whole external
  NOR: projects, samples, packs and the welcome drive (STATIC, kept for safety). Our tools never send it.
* Send only the commands documented on these pages. Probing other command numbers is not safe.
* A module runs with full access to the unit's memory. Keep backups ([backup-and-recovery.md](backup-and-recovery.md)).
