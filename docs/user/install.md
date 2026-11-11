# Circus Arrhythmia FM install guide

Applies to Circus Arrhythmia FM 0.3.1, firmware build 7388 (arrhythmia-devkit build 7228). Known limitations are
in the [release notes](../../RELEASE-NOTES-0.3.1.md).

| Image | Build | SHA-256 of the patched `.syx` (pinned) |
|---|---|---|
| arrhythmia-firmware | 7388 | `31b549966b3facc0a1f86e8b7f2c63d90fb2e218027aa664cf78990d85d1192f` |
| arrhythmia-devkit | 7228 | `66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d` |

## Before you install

1. Back up the microSD card ([Back up first](#back-up-first)).
2. Download the Novation firmware 2.0.1 (build 5706) from
   <https://components.novationmusic.com/api/v2/firmwares/169/file>. The file name is not important. The installer
   checks the content. For other download procedures, refer to
   [Other ways to get the firmware file](#other-ways-to-get-the-firmware-file).

### Back up first

> **Back up first.**
>
> 1. **Copy the microSD card.** This is the necessary backup. Turn the unit off and remove the card. Put the card
>    in a card reader. Copy the whole card to your computer with a plain file copy. The card holds your packs
>    (folder `Rhythm`) and modules (folder `modules`). The card is not visible over USB.
> 2. **The firmware needs no backup.** The official firmware is always available for a new installation
>    ([Going back to stock](#going-back-to-stock)).
> 3. **Optional, Linux only: a copy of the internal content storage of the unit.** This storage holds the projects,
>    samples, packs and settings saved inside the unit. The copy of the card does not include this content. Save
>    pending project edits. Connect the unit by USB. Run this command in the Arrhythmia folder:
>
>    ```
>    sudo python3 -I tools/norbackup.py usb auto
>    ```
>
>    The tool only reads from the unit. It writes a verified copy to a new folder `rhythm-backup-YYYYMMDD-HHMMSS`.
>    `python3 -I tools/norbackup.py extract <that folder>` extracts the content as normal files. There is no restore
>    tool. There is no tested tool for Windows or macOS.

## Install with the web page

Requires Chrome on Windows or Linux. Edge is not tested.

1. Close Novation Components and all other music applications. Connect the unit by USB and turn it on.
2. Open https://llcoolkm.github.io/arrhythmia/web/ in Chrome. Allow MIDI access when Chrome asks. Press
   **Connect MIDI**.
3. Keep the image setting **arrhythmia-firmware** unless you develop modules
   ([the two images](../../README.md#two-images)).
4. Select the downloaded file. The page checks the file and patches it on your computer. The page uploads nothing.
5. Keep the unit idle (stopped, not recording, not saving). Press **Install**.
6. The pads light white and green, then Play flashes. Press **Play** on the unit within 60 seconds. To abort, press
   Cancel on the page.
7. A loading bar fills the pads two times, in approximately half a minute. Do not disconnect the cable. The unit
   restarts. The page shows build 7388 (arrhythmia-firmware) or 7228 (arrhythmia-devkit).

If an error occurs, follow the message on the page. If the result is unknown, wait, connect again and press
**Check unit** before a new attempt.

The page operates only on a unit with the stock firmware 5706 or an Arrhythmia build. "Save patched .syx" saves a
copy of the patched file.

## Install from the Linux command line

Requires Python 3.12 (standard library only). Run the commands from the top folder of the Arrhythmia files.

1. Patch the official file (here named `circuit-rhythm-5706.syx`). For arrhythmia-firmware:

       python3 -I tools/crpatch.py apply release/v0.3.1.json circuit-rhythm-5706.syx Arrhythmia-firmware-7388.syx --build firmware

   For arrhythmia-devkit, use `--build devkit` and the name `Arrhythmia-devkit-7228.syx`. The tool refuses an
   incorrect input file. It prints the SHA-256 of the output file. This value must agree with the table above.
2. Find the MIDI device of the unit with `amidi -l` (for example `hw:3,0,0`, which is `/dev/snd/midiC3D0`).
3. Stop all running modules (Shift + Sample Rec). Keep the unit idle. Send the patched file:

       python3 -I tools/flash.py /dev/snd/midiC3D0 Arrhythmia-firmware-7388.syx \
           --expect-sha256 31b549966b3facc0a1f86e8b7f2c63d90fb2e218027aa664cf78990d85d1192f

   For arrhythmia-devkit, use `Arrhythmia-devkit-7228.syx` and its SHA-256 from the table.
4. When the tool prints "HEADER sent", press the flashing **Play** on the unit within 60 seconds. Only Play
   confirms. The other buttons have no effect. A disconnection or power cycle before the end of the transfer aborts
   it. The installed firmware does not change.
5. "unit reports CRC OK" means that the file arrived without errors. The unit then restarts and installs. It is off
   USB for approximately 10 seconds.
6. Check the version:

       amidi -p hw:3,0,0 -S 'F0 00 20 29 00 70 F7' -d -t 2

   The reply is `F0 00 20 29 00 70`, five bootloader bytes, five firmware bytes, `40 02 F7`. The last three firmware
   bytes give the build number: `00 39 5C` is 7388 (arrhythmia-firmware), `00 38 3C` is 7228 (arrhythmia-devkit),
   `00 2C 4A` is 5706 (stock).

## Add the FM engine

arrhythmia-firmware contains no engine. Track 8 plays the samples of its pack until a pack selects the FM engine.
0.3.1 needs `fm.dse` 2.0.0. It refuses `fm.dse` 1.0.0 from 0.3.0.

1. Download `fm.dse` from the release page: <https://github.com/llcoolkm/arrhythmia/releases>.
2. Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html).
3. Add the published engine. It replaces an existing `engines/fm.dse`.
4. Select it for a pack ([Engines on track 8](synth.md#engines-on-track-8)).
5. Save project edits on the unit.
6. Load the pack in the unit's Packs view.

The file goes over USB if it is at most 15,360 bytes. `fm.dse` 2.0.0 is 15,346 bytes, SHA-256
`9b79dfe90c4208a8208ffc04f5d5aa80bc3787584524d17a7ec3deaf5629ddae`.

## Check that it works

With arrhythmia-firmware, before the FM engine is added:

1. Select track 8 and play some pads. The samples of the pack sound.
2. Load a factory project. Tracks 1 to 7 sound as on stock.
3. Turn on scales for track 1 ([scales.md](scales.md)) and play its Note view. All lit pads are in key.

With arrhythmia-firmware, after the FM engine is added and its pack is loaded:

1. Run `card engstatus` or check the loader's engine state. The FM engine is loaded and healthy, with stack mismatch
   0 and overrun 0.
2. Select track 8 and play some pads. The FM synth sounds.
3. Open the Sample view of track 8 and tap the pads of rows 3 and 4. Each tap plays a different sound
   ([synth.md](synth.md#choosing-a-sound-the-sample-view)).
4. Press Shift + Track 8 to open the synth page. Set notes per press to 3 and play a pad. A chord sounds.
5. Close the page. On track 8, turn macro 2 (attack) and macro 3 (decay). The sound of new notes changes.
6. Set macro 6 (FM depth) above 0. Hold a pad and turn macro 1 (feedback). The tone changes while the note sounds.
7. On page 1 of the synth page, turn macro 1 (tune). The pitch of new notes changes.
8. Send MIDI notes on channel 8. Up to 4 notes play together.
9. Change to another pack and back, then play track 8. The synth plays after the load
   ([Loading samples](synth.md#loading-samples-while-the-unit-runs)).

With arrhythmia-devkit, track 8 is a stock sample track. Do the first list, steps 2 and 3 only (scales over SysEx,
refer to [scales.md](scales.md)).

Modules: put the module files of the release (for example `09paint.mod`) into the folder `modules` at the root of
the card. Use a card reader or the loader ([modules.md](modules.md)). Shift + Sample Rec opens the
picker. `09paint.mod` is on pad 9. arrhythmia-firmware has no built-in module.

On both images, the fault report is clean after a correct installation:

    amidi -p hw:3,0,0 -S 'F0 00 20 29 00 60 0D F7' -d -t 2

The reply starts `F0 00 20 29 00 60 0D`. The next byte is 0 when there is no fault
([Rec blinks white](#rec-blinks-white)).

## Switching between the two images

Use the installer again and select the other image. No other step is necessary. The two images have different
build numbers (7388 and 7228). The unit installs every build whose number is different from the running build.
Projects, samples and settings stay. arrhythmia-devkit keeps the synth settings of track 8 in the project but does
not use them. arrhythmia-firmware uses them again after the return.

An installation of the running image has no effect: the unit restarts in 2 or 3 seconds and keeps its firmware.
The web page refuses such an installation.

## Going back to stock

Send the unmodified official 5706 `.syx` with the same procedure as the Arrhythmia installation:

* **Installer page:** click **Back to stock**.
* **Linux command line:** use `tools/flash.py` as in [Install from the Linux command line](#install-from-the-linux-command-line),
  with the official file and `--expect-sha256 11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`.

Confirm with **Play** as during an installation. After the installation, the version query shows `00 2C 4A` (5706).

**Novation Components cannot go back to stock.**

* In bootloader mode, Components reports that the unit is in the wrong mode. Press **Play** to leave bootloader
  mode.
* In normal mode, Components reports that the firmware is up to date. Arrhythmia answers the standard identity
  query with the stock version 5706. The version query above shows the real build.
* Close the installer tab before you open Components or other MIDI software. On Windows, a MIDI port has one user
  at a time. Components can stop responding until the installer tab is closed.

Bootloader mode (hold **Sample Rec + Sample + Note** while you turn the unit on) is not necessary to go back to
stock. It shows the installed firmware build on the pads, rows 3 and 4, as hex digits (for example `7388` for
build 7388). Press **Play** to leave it.

Rules:

* Stop all running modules before an installation (Shift + Sample Rec). A module covers the pads, and the flashing
  Play is then not visible.
* The unit installs only a build that is different from the running build. A restart in 2 or 3 seconds with the old
  build in the version reply means that the unit skipped the installation. arrhythmia-firmware (7388),
  arrhythmia-devkit (7228) and stock (5706) have different build numbers, so each change between them installs.
* A real installation keeps the unit off USB for approximately 10 seconds. After it, check the version
  ([Install from the Linux command line](#install-from-the-linux-command-line), step 6). Stock ends in `00 2C 4A`
  (5706). On stock, Shift + Sample Rec opens no module picker.
* If the unit does not start normally after an installation, stop. Do not flash again. Report the problem.

## Rec blinks white

**Rec** blinks white approximately two times per second when the sound processor (DSP) of the unit stops answering.
Arrhythmia then stops the wait for the DSP. The rest of the unit continues to operate.

* The unit refuses all functions that need the DSP: pack loading, sample recording, trimming.
* After a failed sample load, the unit also refuses new sample notes. Sounding notes play to their end.
* The sequencer, Stop and Save continue to operate (verified in emulation). Save your project.
* **Turn the unit off and on** to clear the fault. No other action clears it.
* Report the fault with the reply to the fault report command (`F0 00 20 29 00 60 0D F7`).

If Rec blinks white soon after you chose an engine, set the pack to Samples with the loader before the power cycle
([Engines from other authors](synth.md#engines-from-other-authors)).

## Other ways to get the firmware file

The commands below use the file name `circuit-rhythm-5706.syx`. The SHA-256 of the file must be
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`. The installer and the patch tool refuse all other
files.

**Windows PowerShell** (tested in PowerShell 7):

    Invoke-WebRequest -UseBasicParsing `
      -Uri https://components.novationmusic.com/api/v2/firmwares/169/file `
      -OutFile circuit-rhythm-5706.syx
    Get-FileHash circuit-rhythm-5706.syx

The hash must be `11D604A6B6BEF0D41378DA03718658C7FE9C959AC24E7FEF6563796B9932AFAF`. If Windows PowerShell 5.1
reports a TLS error, run `[Net.ServicePointManager]::SecurityProtocol = 'Tls12'` first. This command is not tested
on 5.1.

**Linux or macOS with curl and jq:**

    curl -s https://components.novationmusic.com/api/v2/firmwares \
      | jq -r '.firmwares[] | select(.product == "circuit-rhythm" and .build == 5706) | .url' \
      | xargs curl -sL -o circuit-rhythm-5706.syx
    sha256sum circuit-rhythm-5706.syx          # Linux
    shasum -a 256 circuit-rhythm-5706.syx      # macOS

**Browser only:**

1. Open `https://components.novationmusic.com/api/v2/firmwares`.
2. Find the entry with `"product": "circuit-rhythm"` and `"build": 5706`.
3. Download the address in its `"url"` field.

## Troubleshooting

| Problem | Action |
|---|---|
| "Port busy" or no MIDI device | Close Components, DAWs and other browser tabs that use MIDI. On Windows, a MIDI port has one user at a time. Connect the cable again. |
| Play does not flash | Stop the module and the transport. Try again. |
| Timed out waiting for Play | No change occurred. Try again and press Play within 60 seconds. Only Play confirms. |
| Disconnection or power cycle during the transfer | The transfer aborted. The installed firmware did not change. Try again. |
| Components reports "up to date" or "wrong mode" | Correct behaviour ([Going back to stock](#going-back-to-stock)). Use the installer page or `tools/flash.py`. |
| CRC mismatch | No installation occurred. Download the official file again and check its SHA-256. |
| Rec blinks white | A DSP fault ([Rec blinks white](#rec-blinks-white)). Save, then turn the unit off and on. |
| The unit does not start after the installation | Stop. Do not flash again. Report the problem. |
