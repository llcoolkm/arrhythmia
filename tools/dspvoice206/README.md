# Arrhythmia VERSION 206 own DSP source (historical 7306)

This is the historical builder of image 7306, kept so that it stays
reproducible. For the current 0.2 image 7316, use
[tools/dspvoice207rc/README.md](../dspvoice207rc/README.md).

This source reconstructs the fixed four-voice FM instrument and its
suppression-only companion. It reproduces the DSP streams and the historical
7306 image; it does not introduce another engine or change the firmware bytes.

Only our assembly and table formulas are included here. Supply your official
Circuit Rhythm 5706 firmware locally. Combined streams and images contain
material from that input; keep generated outputs local. The builder does not
access MIDI, a device or the network.

The source is MIT licensed, as specified in the shared [LICENSE](../dspvoice/LICENSE).
See [NOTICE.md](NOTICE.md) and the assembler's
[dependency notices](../dspvoice/toolchain/NOTICES.md).

## Build

Use Python 3.12 or later. Build the pinned line assembler using the existing
[toolchain instructions](../dspvoice/toolchain/README.md). No assembler binary
is supplied. Choose a fresh output directory and run from the repository root:

```sh
python3 -I tools/dspvoice206/build.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --out build/dspvoice206
```

The official input SysEx SHA-256 is
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`.
Every source dependency, assembled component, generated table and output is
checked against a fixed digest. Arithmetic or assembler differences are
refused instead of silently changing the image. Existing output directories
are refused, and validation failure does not leave a final output directory.

| Local output | SHA-256 |
|---|---|
| core0-instrument.bin | `cbbeb9b1185fb52cd1ebee536d04d3589f716c94b55589153bd36f57bf7bf8c8` |
| core1-suppression.bin | `b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948` |

The optional full-image join accepts only the historical ARM-only image,
SHA-256 `ee39d911082fed98a4e5dd6e89a29ad0477fa4fb7ace96c323e2f71a2bd1dc6f`.
The [rebuild prerequisites](../../docs/README.md#rebuilding-the-images) cover the
official input and the ARM toolchain. Then build the historical ARM part:

```sh
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-fm206
python3 -I tools/patch.py patches/arrhythmia-firmware-arm-fm206.json \
  build/arrhythmia-firmware-arm-fm206-7304
```

This dedicated VERSION 206 ARM profile preserves the original image. The current
VERSION 207 ARM profile is not interchangeable with it.

Then add `--arm-image build/arrhythmia-firmware-arm-fm206-7304.bin` to the
source-build command above. It produces:

| Local output | SHA-256 |
|---|---|
| arrhythmia-firmware-7306.bin | `ab8b07a2930e57620902b0fa45f3f0d7870475ca99b01c1c295f5a0610b1fe78` |
| arrhythmia-firmware-7306.syx | `d72fd952b3375df8b45582576f2ce67619fe16437d8d75c20b2d359a06615e72` |

An already joined or development-gated ARM image is refused. This join is
specific to these fixed inputs and outputs; it is not a general flash tool.
The 0.1.1 source builder and its original validation rules remain separate.

## Source and checks

`instrument.asm`, `startup.asm` and `suppression.asm` are our labelled source,
not disassemblies of the combined streams. `tables.py` generates the original
envelope, sine, filter, depth and ratio data with stated rounding rules.
`assembler.py` resolves local labels through the shared line assembler.
`build.py` derives the two stock stream records from the user's input and
checks their composition, mutation bounds, suffix and final placement.
`source-manifest.json` lists the exact source dependency closure, including
the reused public adapter, frontend and toolchain recipe.

Run the tests from the repository root; optional local inputs enable full
component, stream, mutation and final-image checks:

```sh
python3 -I tests/test_dspvoice206_source.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --arm-image build/arrhythmia-firmware-arm-fm206-7304.bin
```

Source reproduction does not add hardware or silicon timing evidence.
