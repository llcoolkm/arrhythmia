# Arrhythmia documentation

Arrhythmia is custom firmware for the Novation Circuit Rhythm, distributed as a patch that you apply to the official
firmware file on your own computer. No Novation code or firmware is included in this repository.

## For users

* [README.md](../README.md): what Arrhythmia is, the two images, and the risks.
* [user/quickstart.md](user/quickstart.md): install in six steps.
* [user/install.md](user/install.md): the full install guide: backup, other download methods, the command line,
  checking, switching images, going back to stock and troubleshooting.
* [user/synth.md](user/synth.md): the track 8 FM synth, its settings and its known issues.
* [user/scales.md](user/scales.md): scales on tracks 1 to 7.
* [../RELEASE-NOTES-0.2.0.md](../RELEASE-NOTES-0.2.0.md): the 0.2 release notes: changes since 0.1.1, test status, known issues.
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

`release/v0.2.0.json` is the current release in crpatch/1 format (`tools/crpatch.py`); `web/index.html` is the
installer and embeds it. `release/v0.1.1.json` and `release/v0.1.0.json` are the earlier releases.

The 0.2 ARM source and the FM DSP source are published. With the official firmware 5706 file as the only firmware input, the complete 0.2 firmware image 7323
rebuilds from this tree on the checked Linux toolchain. The ARM part builds from `patches/src/`. The DSP part (the FM
synth on DSP core 0, with its fixed output level, and the core-1 change that retires the 0.1.x voice) builds from
`tools/dspvoice207rel/` with the line assembler built from source; no assembler binary is supplied. Installation still
uses the official firmware and the published release patch.
arrhythmia-devkit 7228 and the [full 0.1.1 firmware image 7261](#rebuild-the-011-firmware-image-7261) still rebuild
from this tree, as in 0.1.1.

Everything below needs the official firmware 5706 `.syx` file (see [user/install.md](user/install.md#before-you-install)),
`clang` and the LLVM tools; steps 1 to 4 download nothing. Run every command from the top folder of the Arrhythmia files.

1. Create the folders `firmware/` and `build/`, and copy the official file to `firmware/circuit-rhythm-5706.syx`
   (the patch sets name this path, so the file must be there under exactly this name). Then decode it:
   `python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin`.
2. Build the ARM code (`patches/src/arrhythmia.c`, with the built-in module `modules/paint.c`):
   `python3 -I tools/build_lightshow.py arrhythmia-devkit-arm`.
3. arrhythmia-devkit: `python3 -I tools/dspmeter.py build --base patches/arrhythmia-devkit-arm.json --label 7228
   --version 000791 --out build/arrhythmia-devkit-7228` adds the DSP load meter (`patches/dspmeter.lst`). The result,
   `build/arrhythmia-devkit-7228.syx`, must equal the pinned SHA-256 of build 7228
   (`66e1872c45b742992bdead24ebcbaffb9634d13b0a2db36caf5f2d1e6dd8354d`).
4. arrhythmia-firmware 0.2, ARM part (label 7304, without its DSP code):

   ```sh
   python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-fm-recover
   python3 -I tools/patch.py patches/arrhythmia-firmware-arm-fm-recover.json build/arrhythmia-firmware-arm-fm-recover-7304
   ```

   `build/arrhythmia-firmware-arm-fm-recover-7304.bin` must have the SHA-256
   `4cff8b24b1b985b50a594e096522483476682a3ffce0efc27d5a29937483142e`.

5. Full arrhythmia-firmware 0.2 image 7323:

   ```sh
   python3 -I tools/dspvoice207rel/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-fm-recover-7304.bin --out build/arrhythmia-7323-source
   ```

   The `.syx` must have the SHA-256
   `f5f77f2902aa7934c0c5dd22bbb890ce4c5aaa8f683d4f55e10897df95178cd5` and the decoded image
   `ee083547dc850a627a987ec15dbbc592d932b3254f8c84f657f1c70410ab73e5`. The DSP core 0 stream (the FM synth with its fixed output level
   and the feedback range increased 4 times) must have the SHA-256
   `56c8363d9a68c74fffde5099ed26819da9392d11193136f2242c417c422c4880`. It needs the line assembler: obtain the
   [pinned assembler source](../tools/dspvoice/toolchain/UPSTREAM.md#pinned-assembler-dependency) and follow the
   [line assembler build instructions](../tools/dspvoice/toolchain/README.md#dsp-line-assembler). This needs Git,
   CMake and a C++17 compiler; obtaining the pinned dependencies uses the network. Generated streams and images
   contain material derived from the official file and must stay local.

### Rebuild the 0.1.1 firmware image 7261

After steps 1 and 2 above:

1. Build the 0.1.1 ARM code: `python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-ad`.
2. Its ARM part: `python3 -I tools/patch.py patches/arrhythmia-firmware-arm-ad.json
   build/arrhythmia-firmware-arm-ad-7251` (label 7251, with attack and decay);
   `build/arrhythmia-firmware-arm-ad-7251.syx` must have the SHA-256
   `471008cc21a7ace738cabc8a2ff94a651559fa8da52dcfec07ceb97fee7886d9`.
3. Full arrhythmia-firmware image 7261: build the DSP line assembler, then add the DSP voice code from its source
   (`tools/dspvoice/`) to the ARM part of step 2, as [../tools/dspvoice/README.md](../tools/dspvoice/README.md)
   describes. This step needs Git, CMake and a C++17 compiler, and downloads the pinned public assembler source
   (DSP56300, GPLv3). `build/arrhythmia-7261-source/arrhythmia-firmware-7261.syx` must have the SHA-256 of the
   released build, `920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b`. This rebuild is tested on
   Linux (Ubuntu x86-64) only.
