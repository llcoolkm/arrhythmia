# Circus Arrhythmia FM documentation

Circus Arrhythmia FM is custom firmware for the Novation Circuit Rhythm, distributed as a patch that you apply to the official
firmware file on your own computer. No Novation code or firmware is included in this repository.

## For users

* [README.md](../README.md): what Arrhythmia is, the two images, and the risks.
* [user/quickstart.md](user/quickstart.md): install and add the FM engine.
* [user/install.md](user/install.md): the full install guide: backup, other download methods, the command line,
  checking, switching images, going back to stock and troubleshooting.
* [user/synth.md](user/synth.md): the track 8 FM synth, its settings and its known issues.
* [user/scales.md](user/scales.md): scales on tracks 1 to 8.
* [user/euclid.md](user/euclid.md): the four-row rhythm editor, its controls, pattern write and undo.
* [user/modules.md](user/modules.md): use the combined loader or a card reader to add modules, then start, stop and control them.
* [../changelog.md](../changelog.md): the 0.3.1 change summary and earlier release history.
* [../RELEASE-NOTES-0.3.1.md](../RELEASE-NOTES-0.3.1.md): the 0.3.1 release notes: changes since 0.3.0, known limitations, install.
* [../RELEASE-NOTES-0.3.0.md](../RELEASE-NOTES-0.3.0.md): the 0.3.0 release notes: changes since 0.2.2, known limitations, install.
* [../RELEASE-NOTES-0.2.2.md](../RELEASE-NOTES-0.2.2.md): the 0.2.2 release notes: changes since 0.2.1, test status, known issues.
* [../RELEASE-NOTES-0.2.1.md](../RELEASE-NOTES-0.2.1.md): the 0.2.1 release notes.
* [../RELEASE-NOTES-0.2.0.md](../RELEASE-NOTES-0.2.0.md): the 0.2 release notes: changes since 0.1.1, test status, known issues.
* [../RELEASE-NOTES-0.1.1.md](../RELEASE-NOTES-0.1.1.md): the 0.1.1 release notes: the two builds, what was tested, known limitations.
* [../RELEASE-NOTES-0.1.0.md](../RELEASE-NOTES-0.1.0.md): the 0.1.0 release notes.

## For developers

* [user/engines-dev.md](user/engines-dev.md): engine ABI 3, the contract for track 8 engines.
* [user/developers.md](user/developers.md): writing, building and loading modules, the host calls (ABI 3), input
  and clock events, and the DSP load meter.
* [led-map.md](led-map.md) and [led-map.json](led-map.json): the LED buffer a module draws into, byte by byte
  ([img/led-map-verify.jpg](img/led-map-verify.jpg)).
* [input-map.json](input-map.json): pad, button and encoder indices for a module's `input` entry.
* [dev/README.md](dev/README.md): the developer reference: hardware, flashing and identity, backup and recovery,
  every Arrhythmia SysEx command, module details, DSP budget and measured load, maps, and our designs.

## Rebuilding the images

`release/v0.3.1.json` is the current release in crpatch/1 format (`tools/crpatch.py`); `web/index.html` is the
installer and embeds it. Earlier release JSON files remain as historical records.

### Reproduce 0.3.1 firmware 7388

Use the official Circuit Rhythm firmware 2.0.1, build 5706, as input. Run from this repository:

```sh
python3 -I tools/crpatch.py apply release/v0.3.1.json circuit-rhythm-5706.syx arrhythmia-firmware-7388.syx --build firmware
python3 -I tools/crpatch.py scan release/v0.3.1.json circuit-rhythm-5706.syx
```

The patched file must have SHA-256 `31b549966b3facc0a1f86e8b7f2c63d90fb2e218027aa664cf78990d85d1192f`.
The browser installer applies the same patch. The FM engine remains a separate 15,346-byte card file, version 2.0.0,
with SHA-256 `9b79dfe90c4208a8208ffc04f5d5aa80bc3787584524d17a7ec3deaf5629ddae`.

### Rebuild 0.3.0 firmware 7358 and fm.dse

Use the official firmware 5706 `.syx` file, Python 3.12, `clang` and the LLVM tools. Run from the repository root:

```sh
mkdir -p firmware build
# Copy your official file to firmware/circuit-rhythm-5706.syx first.
python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin
python3 -I tools/rel030_release.py --stock firmware/circuit-rhythm-5706.syx --fm /path/to/fm.dse --out build/rel030-new
```

`--fm` takes the release's 14,906-byte `fm.dse`, SHA-256
`236b20874ed999c46a8575cd3336b6ac26df9770d11f9f9dfdd6f711cd0029ad`. The output directory must not exist.
The builder checks the official input and source pins, compiles twice in fresh processes and checks byte equality.
The integrated firmware hashes are pinned below. A rebuild does not establish hardware qualification.
The firmware `.syx` SHA-256 is `adfd566c1c28d3f9f1484ac44e343d08b3fc7c1638e91b07b2265540af8a852d`;
the decoded image SHA-256 is `a45b7df13361c8a0e341be1082d66b8e5f4a439972996fe999126adf7cf77f39`.
The FM file remains separate, for the card; it is not joined into flash. Its engine version is 1.0.0, independent of
firmware 0.3.0.
See [the public rebuild reference](../tools/rel030release/README.md) for the output files and checks.

To build `fm.dse` from its own source instead, first build the
[pinned line assembler](../tools/dspvoice/toolchain/README.md#dsp-line-assembler), then run:

```sh
python3 -I tools/dspvoice207live/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --out build/fm-live-new
python3 -I tools/rel030_release.py --stock firmware/circuit-rhythm-5706.syx --fm build/fm-live-new/fm.dse --out build/rel030-source-new
```

The assembler needs Git, CMake and a C++17 compiler; obtaining its pinned source uses the network. The FM component
contains own-source code and generated tables. Complete DSP streams, decoded firmware, patched `.syx` files and
intermediate stock inputs contain official firmware material and must remain local. The
[FM source reference](../tools/dspvoice207live/README.md) distinguishes these artifacts.
A successful rebuild does not establish hardware, listening or DSP headroom qualification.

### Historical 0.2.x rebuilds and devkit 7228

The 0.2 ARM source and the FM DSP source are published. With the official firmware 5706 file as the only firmware input, the complete 0.2 firmware image 7323
(and the 0.2.1 and 0.2.2 images 7329 and 7339, steps 6 and 7) rebuilds from this tree on the checked Linux toolchain. The ARM part builds from `patches/src/`. The DSP part (the FM
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

6. arrhythmia-firmware 0.2.1 image 7329. After step 1, build
   the 0.2.1 ARM part (label 7304, without its DSP code, no built-in module), then join it with the same DSP streams
   as 7323:

   ```sh
   python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-021 --spec-dir build
   python3 -I tools/patch.py build/arrhythmia-firmware-arm-021.json build/arrhythmia-firmware-arm-021-7304
   python3 -I tools/dspvoice207rel/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-021-7304.bin --out build/arrhythmia-7329-source
   ```

   `build/arrhythmia-firmware-arm-021-7304.bin` must have the SHA-256
   `d3ab9200c857eb6abb070ebf9424c26611ddd117c70beba77325cfd84166eb24`. The `.syx` must have the SHA-256
   `f756c03a71039ba3edd248d53232f9bac24367a4cf800fe6ffd9b8d00fb15251` and the decoded image
   `10939096414b70a25d2e5bdc6d593df4e56563b9abdc9d9870c88d3542fb3619`. The DSP streams are the streams of step 5.
   The 0.2.1 ARM part puts part of its code (the far blob, 0x0801B000 to 0x0801C718) in flash that the old stock
   core-0 stream used. The join accepts ARM changes in that range only.
7. arrhythmia-firmware 0.2.2 image 7339. After step 1, build the 0.2.2 ARM part (label 7304, without its DSP
   code), then join it with the DSP streams of 0.2.2 (the output level is 4 dB down, not 7 dB):

   ```sh
   python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-rc --spec-dir build
   python3 -I tools/patch.py build/arrhythmia-firmware-arm-rc.json build/arrhythmia-firmware-arm-rc-7304
   python3 -I tools/dspvoice207m4/release.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-rc-7304.bin --out build/arrhythmia-7339-source
   ```

   `build/arrhythmia-firmware-arm-rc-7304.bin` must have the SHA-256
   `a1f5ed53d3d23c250736eede1c8fa27a13d320783d599b758bd9627db0b7c2ac`. The `.syx` must have the SHA-256
   `7aadbde0811a77b23c05a06fca34c36d27d98c98945f3f587bb8d7ccf63fc822` and the decoded image
   `5f10a0ac33e1358ab9bf51eb22f05d1172e22f5c0c91cb5b86dec2e1d23458df`. The DSP source is in `tools/dspvoice207m4/`
   ([../tools/dspvoice207m4/RELEASE.md](../tools/dspvoice207m4/RELEASE.md)). The far blob of the 0.2.2 ARM part is
   0x0801B000 to 0x0801E70C.

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
