# Backup and recovery

How to back up what is on the unit before you experiment, and how to get back to stock firmware. Marks as in
[README.md](README.md).

## What can be lost

How we know: observed on hardware (what the USB disk and the card hold).

* **Projects, samples, packs, global settings:** on the external NOR, in the FAT16 content volume.
* **Modules and anything else on the microSD card.**
* **Firmware:** recoverable by flashing (below). The bootloader is never written by the update protocol for type 0,
  and our tools refuse other types.

## NOR backup over USB (`tools/norbackup.py usb`)

How we know: our tool's design; observed on hardware (two backups on one unit, each of two matching passes). The
`auto` disk selection and the default output folder were added after those backups.

The unit's USB disk is a read-only view of NOR from offset 0x94000 to the end: the welcome drive and the content
volume ([hardware.md](hardware.md)). It does not include the update staging area or the bytes below 0x94000: this
USB backup path and our published tools do not read them. A module runs as trusted code with full access to the
unit's memory, so this is not a limit on what code running on the unit could reach.

    sudo python3 -I tools/norbackup.py usb auto
    python3 -I tools/norbackup.py verify OUTDIR
    python3 -I tools/norbackup.py extract OUTDIR      # optional: content files into OUTDIR/files/

The tools use only the Python standard library; nothing needs installing. `usb auto` finds the unit's disk by its USB
identity (it refuses if there is none or more than one; `usb /dev/sdX` names the disk instead) and writes to a new
folder `rhythm-backup-YYYYMMDD-HHMMSS` in the current directory unless you give OUTDIR after the device. `sudo` is
needed when your user cannot read raw disks; the output folder is then given back to your user. Pass the folder it
printed to `verify`. `extract` checks `usb-disk.img` against `SHA256SUMS` and copies the files of the FAT16 content
volume (projects, samples, packs, settings) into `OUTDIR/files/` by parsing the image; nothing is mounted.

The tool refuses (exit 2, nothing read) unless the path is a whole-disk block device of a USB device `1235:0138` with
0x2000 blocks of 4096 bytes and nothing on it is mounted read-write (a read-only automount of the welcome drive is
allowed). It opens the device read-only with direct I/O, reads it twice and writes nothing unless both passes agree
(DESIGN).

| Output | Content |
|---|---|
| `usb-disk.img` | the 32 MB disk exactly as read |
| `nor-0x094000-0x2000000.bin` | the same bytes with the serial substitution undone, so it equals the NOR |
| `usb-blocks.sha256` | SHA-256 of every 4 KB block with its disk and NOR offset, for block-level comparison |
| `SHA256SUMS`, `manifest.json` | file hashes; identity, geometry, mounts, per-pass hashes, checks |

Expect three serial replacements (HW). Exit 3 means a manifest check warned and the files were kept: read the warning.

**Two backups are not identical, and that is normal** (HW). Two backups taken with a power cycle in between differed
in exactly one 4 KB block and in two bytes of it: the global settings file `Globals/00_GLOB.CG` (148 bytes), which
the unit rewrites around boot or a settings change. Treat it as live data when you compare backups.

There is no restore tool. A backup is evidence and a basis for a later restore, not a one-command undo. The USB disk
is write-protected by the firmware, so nothing can be written through it.

## microSD backup

How we know: observed on hardware.

**The microSD card is not visible over USB**, so the NOR backup does not include it: copy the card with a card reader,
with the unit off. Image the card as the **input** only (check the
device twice: a `dd` to the wrong device cannot be undone), hash the image, and put the card back with the unit off.
The firmware detects the card at power-on.

## Going back to stock firmware

How we know: observed on hardware (one unit, 2026-10-19).

Tested routes:

* **Installer page, Back to stock** (Chrome on Windows): detecting the unit, flashing stock 5706 and reinstalling
  Arrhythmia all passed.
* **`tools/flash.py`** (Linux) with the official, unmodified 5706 file:

      python3 -I tools/flash.py /dev/snd/midiC<card>D0 circuit-rhythm-5706.syx \
          --expect-sha256 11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf

Confirm with Play when the unit asks. Pass: `73 7F`, the unit off USB for about 10 s, then the `70` query shows
application bytes `01 01 00 2C 4A` (5706); Shift + Sample Rec no longer opens a module picker.

**Novation Components is not a recovery route for Arrhythmia 0.1** (HW). In bootloader mode it reports that the unit
is in the wrong mode; in normal mode it reports the firmware as up to date, because Arrhythmia answers the Device
Inquiry with 5706 ([flashing.md](flashing.md)). On Windows, Components hung until the installer tab released the MIDI
port.

Both tested routes start from a unit that boots Arrhythmia and answers over USB. They are not proven as a rescue for
an image that does not boot; for that case see the end of the next section.

## When a flash goes wrong

How we know: observed on hardware, except where the table says otherwise.

| Symptom | Meaning | Action |
|---|---|---|
| HEADER times out (no ack `0x20` within 60 s) | Play not pressed, or a module hid the prompt | nothing changed; stop the module, retry |
| a WRITE ack is missing, the tool aborts before FINISH | cable or busy port | nothing installed; power-cycle, retry |
| `73 7E` | CRC mismatch | nothing installed; check the file hash, retry |
| no FINISH reply in 30 s | outcome unknown | wait, query `70`; if it shows the old build, retry |
| `73 7F`, back on USB in 2 to 3 s, `70` shows the old build | the bootloader skipped an identical build-info block | you flashed what is installed; nothing to do |
| red pads, no Play prompt | seen once after reading the DSP meter | power-cycle, retry |

Before any flash on Arrhythmia 0.1, check that Rec is not blinking white and `F0 00 20 29 00 60 0D F7` reports no
fault; a DSP fault is cleared only by a power cycle
([design/o1-fault-handling.md](design/o1-fault-handling.md)).

If the unit does not start at all after an install that reported `73 7F`, power-cycle once and try bootloader mode
(Sample Rec + Sample + Note at power-on) with `tools/flash.py`; this case has not occurred and that route is
untested. The debug port is the last resort and is untested.
