# Arrhythmia install guide

Applies to Arrhythmia 0.1.1 (arrhythmia-firmware build 7261, arrhythmia-devkit build 7228). Test status is in the
[release notes](../../RELEASE-NOTES-0.1.1.md). Items marked **TBD** are not confirmed yet.

| Image | Build | SHA-256 of the patched `.syx` (pinned) |
|---|---|---|
| arrhythmia-firmware | 7261 | `920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

## Before you install

1. Back up the microSD card: see [Back up first](#back-up-first).
2. Download Novation's firmware 2.0.1 (build 5706): open
   <https://components.novationmusic.com/api/v2/firmwares/169/file> in your browser and save the file. Any file name
   works; the installer checks the content. If the link does not work, see
   [Other ways to get the firmware file](#other-ways-to-get-the-firmware-file).

### Back up first

> **Back up first.**
>
> 1. **Copy the microSD card. This is the backup to make.** Turn the unit off, take the card out, put it in a card
>    reader and copy the whole card to your computer with a plain file copy. It holds your packs (folder `Rhythm`)
>    and modules (folder `Modules`). The card is not visible over USB.
> 2. **The firmware needs no backup:** you can always reinstall the official firmware (see
>    [Going back to stock](#going-back-to-stock)).
> 3. **Optional, Linux only: a copy of the unit's internal content storage.** This includes the projects, samples,
>    packs and settings saved inside the unit, not the packs on the microSD card. Copying the card does not include
>    content stored only inside the unit. Save pending project edits first, then connect by USB and run this in the
>    Arrhythmia folder:
>
>    ```
>    sudo python3 -I tools/norbackup.py usb auto
>    ```
>
>    It only reads, never writes to the unit, and writes a verified copy to a new folder
>    `rhythm-backup-YYYYMMDD-HHMMSS`. `python3 -I tools/norbackup.py extract <that folder>` takes the files out as
>    normal files. There is no restore tool, and no tested tool for Windows or macOS.
>
> TBD: whether Components can save a pack from the unit to the computer.

## Install with the web page (Chrome on Windows or Linux; Edge untested)

1. Close Novation Components and any other music app. Connect the unit by USB and turn it on.
2. Open https://llcoolkm.github.io/arrhythmia/web/ in Chrome, allow MIDI access when asked, and press
   **Connect MIDI**.
3. Leave the image on **arrhythmia-firmware** unless you develop modules (see
   [the two images](../../README.md#two-images)).
4. Choose the file you downloaded. The page checks it and patches it on your computer; nothing is uploaded.
5. Leave the unit idle (stopped, not recording or saving) and press **Install**.
6. The pads light white and green, then Play flashes. Press **Play** on the unit within 60 seconds. To abort, press
   Cancel on the page.
7. Wait about half a minute while a loading bar fills the pads twice. Do not unplug. The unit restarts, and the page
   shows build 7261 (arrhythmia-firmware) or 7228 (arrhythmia-devkit).

Follow the page's error message. If the outcome is unknown, wait, reconnect and press **Check unit** before retrying.

The page works only on a unit with stock firmware 5706 or an Arrhythmia build. "Save patched .syx" keeps a copy of
the patched file.

## Install from the Linux command line

For people who prefer a terminal. Needs Python 3.12 (standard library only). Run the commands from the top folder of
the Arrhythmia files.

1. Patch the official file (here named `circuit-rhythm-5706.syx`). For arrhythmia-firmware:

       python3 -I tools/crpatch.py apply release/v0.1.1.json circuit-rhythm-5706.syx Arrhythmia-firmware-7261.syx --build firmware

   For arrhythmia-devkit use `--build devkit` and the name `Arrhythmia-devkit-7228.syx`. The tool refuses a wrong
   input file and prints the SHA-256 of what it wrote; it must match the table above.
2. Find the unit's MIDI device: `amidi -l` (for example `hw:3,0,0`, which is `/dev/snd/midiC3D0`).
3. Stop any running module (Shift + Sample Rec) and leave the unit idle, then send the patched file:

       python3 -I tools/flash.py /dev/snd/midiC3D0 Arrhythmia-firmware-7261.syx \
           --expect-sha256 920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b

   (arrhythmia-devkit: `Arrhythmia-devkit-7228.syx` and its SHA-256 from the table.)
4. When it prints "HEADER sent", press the flashing **Play** on the unit within 60 seconds. Only Play confirms; the
   other buttons do nothing. Unplugging or power-cycling before the transfer finishes aborts it and leaves the
   installed firmware untouched.
5. "unit reports CRC OK" means the file arrived intact. The unit then restarts and installs (about 10 seconds off USB).
6. Check the version:

       amidi -p hw:3,0,0 -S 'F0 00 20 29 00 70 F7' -d -t 2

   The reply is `F0 00 20 29 00 70`, five bootloader bytes, five firmware bytes, `40 02 F7`. The build number is in
   the last three firmware bytes: `00 38 5D` is 7261 (arrhythmia-firmware), `00 38 3C` is 7228 (arrhythmia-devkit),
   `00 2C 4A` is 5706 (stock).

## Check that it works

With arrhythmia-firmware:

1. Select track 8 and play some pads: you hear the synth.
2. Shift + Track 8 opens the synth page. Set notes per press to 3, play a pad: you hear a chord.
   Close the page, then turn macro 2 (attack) and macro 3 (decay) on track 8: the sound of new notes changes.
3. Send MIDI notes on channel 8: up to 4 notes play together.
4. Load one of the factory projects: tracks 1 to 7 sound as before.
5. Turn on scales for track 1 (see [scales.md](scales.md)) and play its Note view: every pad that lights is in key.

With arrhythmia-devkit, track 8 is a stock sample track; check steps 4 and 5 only (scales over SysEx, see [scales.md](scales.md)).

On both images, the fault report should be clean:

    amidi -p hw:3,0,0 -S 'F0 00 20 29 00 60 0D F7' -d -t 2

The reply starts `F0 00 20 29 00 60 0D`; the next byte is 0 when there is no fault (see "Rec blinks white" below).

## Switching between the two images

Use the installer again and choose the other image. Nothing else is needed: the two images have different build
numbers (7261 and 7228), and the unit installs any build whose number differs from the one it is running. Projects,
samples and settings stay. Track 8's synth settings stay in the project on arrhythmia-devkit too, which does not use
them, and are back in use after you return to arrhythmia-firmware.

Installing the image that is already running does nothing: the unit restarts in 2 or 3 seconds and keeps what it has.
The web page refuses to start such an install.

## Going back to stock

Send the unmodified official 5706 `.syx` the same way you installed Arrhythmia:

* **Installer page:** click **Back to stock**. Tested on 2026-10-19 in Chrome on Windows with a development version of the
  installer page that differs from this release's only in the release it embeds: detecting the unit, going back to
  stock 5706 and installing a development firmware build all passed.
* **Linux command line:** `tools/flash.py` as in "Install from the Linux command line", with the official file and
  `--expect-sha256 11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`.

Confirm with **Play** as during an install. Afterwards the version query shows `00 2C 4A` (5706).

**Novation Components is not a way back in 0.1.**

* In bootloader mode, Components says the unit is in the wrong mode. Press **Play** to leave bootloader mode.
* In normal mode, Components says the firmware is up to date. That is because Arrhythmia answers the standard identity
  query with the stock version 5706, on purpose. The version query above shows the real build.
* Close the installer tab before you open Components or any other MIDI software. On Windows a MIDI port has one user
  at a time: in our test Components hung until the installer tab was closed.

Bootloader mode (hold **Sample Rec + Sample + Note** while turning the unit on) is not needed to go back to stock. It
shows the installed firmware build on the pads, rows 3 and 4 as hex digits (for example `72 61` for build 7261).
Press **Play** to leave it.

Things to know:

* Stop any running module before you install (Shift + Sample Rec). A module covers the pads, so you cannot see the
  flashing Play.
* The unit installs only a build that differs from the one it is running. If it restarts in 2 or 3 seconds and still
  reports the old build, it skipped the install. arrhythmia-firmware (7261), arrhythmia-devkit (7228) and stock
  (5706) all have different build numbers, so any change between them installs.
* A real install keeps the unit away from USB for about 10 seconds. Afterwards check the version (see "Install from
  the Linux command line", step 6): stock ends in `00 2C 4A` (5706). Shift + Sample Rec then opens no module picker.
* If the unit does not start normally after an install, stop, do not keep flashing, and report it.

## Rec blinks white

If the **Rec** button blinks white about twice a second, the unit's sound processor (DSP) stopped answering and
Arrhythmia stopped waiting for it, so the rest of the unit keeps working. Anything that needs the DSP (loading a
sample pack, sample recording, trimming) is refused, and after a failed sample load new sample notes are refused too;
notes that were already sounding play out. The sequencer, Stop and Save keep working (tested in emulation), so save
your project. **Turn the unit off and on again** to clear it; nothing else clears it. Please report it with the reply to the fault
report command above (`F0 00 20 29 00 60 0D F7`).

## Other ways to get the firmware file

The command lines below call the file `circuit-rhythm-5706.syx`. Its SHA-256 must be
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`; the installer and the patch tool refuse any other
file.

**Windows PowerShell** (tested in PowerShell 7):

    Invoke-WebRequest -UseBasicParsing `
      -Uri https://components.novationmusic.com/api/v2/firmwares/169/file `
      -OutFile circuit-rhythm-5706.syx
    Get-FileHash circuit-rhythm-5706.syx

The hash must be `11D604A6B6BEF0D41378DA03718658C7FE9C959AC24E7FEF6563796B9932AFAF`. On old Windows PowerShell
5.1, if it reports a TLS error, first run `[Net.ServicePointManager]::SecurityProtocol = 'Tls12'` (not tested on
5.1).

**Linux or macOS with curl and jq:**

    curl -s https://components.novationmusic.com/api/v2/firmwares \
      | jq -r '.firmwares[] | select(.product == "circuit-rhythm" and .build == 5706) | .url' \
      | xargs curl -sL -o circuit-rhythm-5706.syx
    sha256sum circuit-rhythm-5706.syx          # Linux
    shasum -a 256 circuit-rhythm-5706.syx      # macOS

Without any of these, open `https://components.novationmusic.com/api/v2/firmwares` in a browser, find the entry
with `"product": "circuit-rhythm"` and `"build": 5706`, and download the address in its `"url"` field.

## Troubleshooting

| Problem | What to do |
|---|---|
| "Port busy" or no MIDI device | Close Components, DAWs and other browser tabs that use MIDI (on Windows a MIDI port has one user at a time). Replug the cable. |
| Play does not flash | Stop the module, stop the transport, try again. |
| Timed out waiting for Play | Nothing changed. Try again and press Play within 60 seconds (only Play confirms). |
| Unplugged or power-cycled during the transfer | The transfer aborted; the installed firmware is untouched. Try again. |
| Components says "up to date" or "wrong mode" | Expected (see "Going back to stock"). Use the installer page or `tools/flash.py`. |
| CRC mismatch | Nothing installed. Download the official file again and check its SHA-256. |
| Rec blinks white | A DSP fault (see above). Save, then turn the unit off and on. |
| The unit does not start after installing | Stop, do not keep flashing, and report it. |
