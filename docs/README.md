# Arrhythmia documentation

Arrhythmia is custom firmware for the Novation Circuit Rhythm, distributed as a patch that you apply to the official
firmware file on your own computer. No Novation code or firmware is included in this repository.

## For users

* [README.md](../README.md): what Arrhythmia is, the two images, and the risks.
* [user/quickstart.md](user/quickstart.md): install in six steps.
* [user/install.md](user/install.md): the full install guide: backup, other download methods, the command line,
  checking, switching images, going back to stock and troubleshooting.
* [user/synth.md](user/synth.md): the track 8 chord synth and its settings page.
* [user/scales.md](user/scales.md): scales on tracks 1 to 7.
* [../RELEASE-NOTES-0.1.1.md](../RELEASE-NOTES-0.1.1.md): the 0.1.1 release notes: the two builds, what was tested, known limitations.
* [../RELEASE-NOTES-0.1.0.md](../RELEASE-NOTES-0.1.0.md): the 0.1.0 release notes.

## For developers

* [user/developers.md](user/developers.md): writing, building and loading modules, the host calls (ABI 3), input
  and clock events, and the DSP load meter.
* [led-map.md](led-map.md) and [led-map.json](led-map.json): the LED buffer a module draws into, byte by byte
  ([img/led-map-verify.jpg](img/led-map-verify.jpg)).
* [input-map.json](input-map.json): pad, button and encoder indices for a module's `input` entry.
* [dev/README.md](dev/README.md): the developer reference: hardware, flashing and identity, backup and recovery,
  every Arrhythmia SysEx command, module details, DSP budget and measured load, maps, and our designs.

## Rebuilding the images

`release/v0.1.1.json` is the current release in crpatch/1 format (`tools/crpatch.py`); `web/index.html` is the
installer and embeds it. `release/v0.1.0.json` is the 0.1.0 release. The steps below rebuild the ARM parts of 0.1.1 from
this tree.
Everything below needs the official firmware 5706 `.syx` file (see [user/install.md](user/install.md#before-you-install)),
`clang` and the LLVM tools; nothing is downloaded. Run every command from the top folder of the Arrhythmia files.

1. Create the folders `firmware/` and `build/`, and copy the official file to `firmware/circuit-rhythm-5706.syx`
   (the patch sets name this path, so the file must be there under exactly this name). Then decode it:
   `python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin`.
2. Build the ARM code (`patches/src/arrhythmia.c`, with the built-in module `modules/paint.c`):
   `python3 -I tools/build_lightshow.py arrhythmia-devkit-arm` and `... arrhythmia-firmware-arm-ad`.
3. arrhythmia-devkit: `python3 -I tools/dspmeter.py build --base patches/arrhythmia-devkit-arm.json --label 7228
   --version 000791 --out build/arrhythmia-devkit-7228` adds the DSP load meter (`patches/dspmeter.lst`). The result,
   `build/arrhythmia-devkit-7228.syx`, must equal the pinned SHA-256 of build 7228
   (`66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d`).
4. arrhythmia-firmware: `python3 -I tools/patch.py patches/arrhythmia-firmware-arm-ad.json
   build/arrhythmia-firmware-arm-ad-7251` builds its ARM part (label 7251, with attack and decay);
   `build/arrhythmia-firmware-arm-ad-7251.syx` must have the SHA-256
   `471008cc21a7ace738cabc8a2ff94a651559fa8da52dcfec07ceb97fee7886d9`. The DSP voice code that `tools/voicestack.py`
   adds on top is developed separately and its source is not published yet, so image 7261 cannot be rebuilt from this
   tree alone; check it with the release file instead (`tools/crpatch.py apply`).
