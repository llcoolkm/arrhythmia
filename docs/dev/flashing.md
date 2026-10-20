# Flashing and device identity

How a firmware image gets onto the unit, how to tell what is installed, and what the bootloader accepts. Marks as in
[README.md](README.md). The tools are `tools/syx.py` (format), `tools/flash.py` (sender), `tools/crpatch.py`
(Arrhythmia release patches) and the installer page `web/index.html`.

## The `.syx` file

How we know: read from the published firmware files, ten Circuit Rhythm and Circuit Tracks builds (needed to patch
and send a file); the format round-trips byte for byte.

A firmware file is a sequence of SysEx messages, all starting `F0 00 20 29 00 <cmd>`:

| Message | Bytes after the command byte | Notes |
|---|---|---|
| `71` INIT | `01 <device> d1 d2 d3 d4 d5 d6` | device `63` = Circuit Rhythm, `64` = Circuit Tracks; d1..d6 = version digits |
| `7C` HEADER | `<type> d1..d6 <size: 8 nibbles> <CRC: 8 nibbles>` (30 bytes with framing) | type 0 = application |
| `72` WRITE | 37 bytes of 7-bit data = 32 image bytes (3 trailing pad bits) | repeated |
| `73` FINISH | same encoding as WRITE | sent **last** but holds the **first** 32 bytes (the vector table) |

* The image loads at 0x08010000. Padding after `size` in the last block is `FF`.
* CRC: CRC-32/MPEG-2 (polynomial 0x04C11DB7, init 0xFFFFFFFF, not reflected, no final XOR) over `size` bytes.
* `tools/syx.py` unpacks and packs; `pack(unpack(x)) == x` byte for byte on all ten files.
* Because FINISH carries the vector table, an update that stops early can never leave a bootable half image.
* Stock 5706 has version digits `0 0 0 7 3 1` and an image of 370,032 bytes. The Arrhythmia 0.1 images are 458,736
  bytes (arrhythmia-firmware) and fit the cap below.

## Update protocol

How we know: observed on hardware (the unit's replies while flashing, from Linux and from Windows).

As spoken by `tools/flash.py` and the installer page:

| Step | Message | Reply / wait |
|---|---|---|
| INIT | `F0 00 20 29 00 71 01 63 ... F7` | optional reply, 2 s |
| HEADER | `7C`, 30 bytes | **the unit asks for Play**: pads white and green on alternate rows, then dark, Play flashes. Only Play confirms; other buttons do nothing. Then ack offset `0x20` within 60 s |
| WRITE x n | `72`, 44 bytes (32 data bytes); n at most 0x70000 / 32 = 14,336 | wait for each ack: offset = (b6 \| b7 << 7 \| b8 << 14) << 5, 0 at the end; 5 s each |
| FINISH | `73` with the first 32 bytes | `73 7F` CRC OK (the unit resets and installs); `73 7E` CRC mismatch; nothing in 30 s = unknown, query `70` |

* The WRITE ack is `F0 00 20 29 00 72 b6 b7 b8 F7`; the FINISH reply is `F0 00 20 29 00 73 <status> 7F 7F F7`.
* The port also carries unrelated traffic (messages with `01 63` after the manufacturer ID); skip it while waiting.
* A whole transfer is about 14,000 small messages. On Linux, 11,563 blocks took 23 s (HW). Send each WRITE only after
  the previous ack; do not stream.
* At HEADER only Play confirms; pressing any other button does nothing (HW, from Linux and from Windows). To abort
  at the prompt, stop the sender or unplug the unit.
* Nothing is installed until FINISH passes the CRC check. Aborting earlier, unplugging or power-cycling before the
  end leaves the installed firmware untouched (HW).
* **`73 7F` is not success.** It means the staged image passed the CRC check. Success is a `70` reply after the
  reset that shows the expected application build.
* A sender should allow only `70`, `71`, `7C`, `72` and `73`, and refuse a HEADER type other than 0. **Never send
  `77`** (it erases the external NOR).
* Stop any running module first (SysEx `F0 00 20 29 00 60 00 F7` or Shift + Sample Rec). A module draws over the pads
  and hides the Play prompt, and the transfer then times out at HEADER (HW).
* After reading the devkit DSP meter, one flash attempt showed red pads and never reached the Play prompt; it aborted
  without changing anything, and a power cycle and retry worked. Power-cycle before flashing (HW, once).

`tools/flash.py` usage:

    python3 -I tools/flash.py /dev/snd/midiC<card>D0 FILE.syx --expect-sha256 <sha256 of FILE.syx>

## What the bootloader installs

How we know: observed on hardware (installs and skips, told apart by the USB gap and the `70` reply); the exact
checks by static analysis, kept to what a patch must satisfy to install.

The bootloader (build 4799) checks the staged image after the reset:

* size at most 0x70000 bytes (the application region 0x08010000 .. 0x0807FFFF);
* HEADER type 0, a CRC that is not `FFFFFFFF`, and a CRC that matches the staged bytes;
* the product bytes at image offset 0x1CC .. 0x1CD are `01 63` (Circuit Rhythm);
* **the 38-byte build-info block at image offset 0x1C8 differs from the installed one.**

It never compares versions: there is no "newer", only "different". The HEADER version digits are parsed but not
compared. Consequences:

* Every build that must install needs a build-info block different from the running one. Arrhythmia changes the
  16-bit build label at image offset 0x1CA (address 0x080101CA), a patch point of the published patches. That label
  is what the `70` query reports.
* Flashing the build that is already installed is skipped: the unit is back on USB in 2 or 3 s with the old build. A
  real install keeps it off USB for about 10 to 11 s (HW). The installer page refuses to start such an install.
* Going back to stock 5706 from any Arrhythmia build installs, because the labels differ (HW).

## Device identity

How we know: observed on hardware (the replies of stock and Arrhythmia units).

### Version query `70`

    F0 00 20 29 00 70 F7
    -> F0 00 20 29 00 70 | <boot: b0 b1 n2 n1 n0> | <app: b0 b1 n2 n1 n0> | 40 02 F7

The build number is `n2 << 14 | n1 << 7 | n0` (HW):

| Unit | Bootloader bytes | Application bytes | Builds |
|---|---|---|---|
| stock 5706 | `00 03 00 25 3F` | `01 01 00 2C 4A` | bootloader 4799, application 5706 |
| arrhythmia-firmware 0.1.0 | same | ends `00 38 3D` | 7229 |
| arrhythmia-devkit 0.1.0 | same | ends `00 38 3C` | 7228 |

Use `70` to tell builds apart; it is the only query that shows the Arrhythmia label. The installer page and the release
tools require bootloader 4799 and application 5706 or a 0.1 label before they flash.

### Universal Device Inquiry

    F0 7E 7F 06 01 F7
    -> F0 7E 00 06 02 | 00 20 29 | 01 63 | 00 00 | 01 01 2C 4A | F7

Manufacturer `00 20 29` (Novation), family `01 63`, version bytes `01 01 2C 4A` = 1.1, build 5706 (HW). **Arrhythmia
answers with the stock version 5706 on purpose**, so Novation Components sees an up-to-date unit. That is also why
Components cannot be used to go back to stock ([backup-and-recovery.md](backup-and-recovery.md)).

## Release patches

How we know: our design.

Arrhythmia is published as patches against the user's own stock file, never as an image. `tools/crpatch.py` describes
the format (crpatch/1) in its docstring: every span over stock bytes is checked by a SHA-256 of the stock span, our
bytes are carried as hex, and the whole input and output are pinned by SHA-256. `release/v0.1.0.json` is the
0.1.0 release; `web/index.html` embeds it and
applies it in the browser with the same rules.
