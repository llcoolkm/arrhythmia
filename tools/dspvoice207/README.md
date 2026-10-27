# Arrhythmia VERSION 207 own DSP source (historical 7308)

This is the historical builder of image 7308: the VERSION 207 FM engine without
the fixed output level and without automatic recovery after sample loading. It
is kept so that 7308 stays reproducible. For the current 0.2 image 7316, use
[tools/dspvoice207rc/README.md](../dspvoice207rc/README.md).

This source reconstructs the four-voice FM instrument of image 7308 and its
suppression-only companion. It adds the selected filter and envelope
changes to the earlier VERSION 206 instrument. The earlier
[VERSION 206 builder](../dspvoice206/README.md) and the
[0.1.1 builder](../dspvoice/README.md) remain separate, with their original pins.

Only our assembly and table formulas are included here. Supply your official
Circuit Rhythm 5706 firmware locally. Combined streams and images contain
material from that input; keep generated outputs local. The builder does not
access MIDI, a device or the network.

The source is MIT licensed, as specified in the shared [LICENSE](../dspvoice/LICENSE).
See [NOTICE.md](NOTICE.md) and the assembler's
[dependency notices](../dspvoice/toolchain/NOTICES.md).

## Build

Builds are checked on Ubuntu x86-64. Use Python 3.12 or later. The final
no-overwrite output-directory step requires Linux `renameat2` and refuses
unsupported platforms. Build the pinned line assembler using the existing
[toolchain instructions](../dspvoice/toolchain/README.md). No assembler binary
is supplied. Choose a fresh output directory and run from the repository root:

```sh
python3 -I tools/dspvoice207/build.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --out build/dspvoice207
```

The official input SysEx SHA-256 is
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`.
Every source dependency, assembled component, generated table and output is
checked against a fixed digest. Arithmetic or assembler differences are
refused instead of silently changing the image. Existing output directories
are refused, and validation failure does not leave a final output directory.

| Local output | SHA-256 |
|---|---|
| core0-instrument.bin | `edf31f2710e587928c9dc071336d42cb1e52fd361a7414aaffbe7897578f6fe0` |
| core1-suppression.bin | `b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948` |

### Optional full-image join

The optional join is specific to the historical image 7308. It
accepts only the exact ARM-only image with SHA-256
`9055ff901cbce68d7c0e498222e5294df40c4416026373e9f4e0b1c8109b0fe0`.
It refuses an already joined or development-gated image and cannot join an
arbitrary ARM build.

The [rebuild prerequisites](../../docs/README.md#rebuilding-the-images) cover the
official input and the ARM toolchain. Then build the historical ARM part:

```sh
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-fm
python3 -I tools/patch.py patches/arrhythmia-firmware-arm-fm.json \
  build/arrhythmia-firmware-arm-fm-7304
```

Then add `--arm-image build/arrhythmia-firmware-arm-fm-7304.bin` to the
source-build command above. It produces:

| Local output | SHA-256 |
|---|---|
| arrhythmia-firmware-7308.bin | `72ec2c87d7c4629b66de8debd9d46ebc3f58575bea521226d04a92df842a5b6d` |
| arrhythmia-firmware-7308.syx | `2731b3d8eba47928b25e1f6fc124212878eb57f0182a24127e1f92c0fd3d0381` |

These are fixed historical digests of 7308. This is a local image builder,
not a flash tool.

The VERSION 206 join uses the dedicated `arrhythmia-firmware-arm-fm206`
profile, which reproduces its earlier pinned ARM input. Follow the
[VERSION 206 rebuild instructions](../dspvoice206/README.md#build); the current
VERSION 207 ARM profile is not a substitute for it.

## Source and checks

`instrument.asm` is our labelled filter, release and FM source, assembled as
one unit so that its local calls remain checked. Its terminal weights and
resident tables come from `tables.py`, with explicit rounding and digest
checks. The startup source, suppression source and label assembler are reused
unchanged from `tools/dspvoice206/`. The original envelope, sine, filter,
depth and ratio formulas are reused there too; the attack and release tables
are replaced by the selected VERSION 207 formulas.

`build.py` derives the required firmware material from the user's input and
checks the integration sites, own allocations, mutation bounds, scatter
suffix and final placement. `source-manifest.json` lists the exact dependency
closure, including reused public sources and the toolchain recipe. Source
reproduction does not establish unrestricted memory ownership, hardware
performance or silicon timing.

Run the tests from the repository root; optional local inputs enable full
component, stream, mutation and final-image checks:

```sh
python3 -I tests/test_dspvoice207_source.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --arm-image build/arrhythmia-firmware-arm-fm-7304.bin
```

Current user-facing limitations are listed in the
[synth guide](../../docs/user/synth.md#known-issues).
