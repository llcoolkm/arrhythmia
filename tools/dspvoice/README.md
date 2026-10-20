# Arrhythmia 0.1.1 DSP source

This directory contains the Arrhythmia DSP voice source and the small adapter
needed to build it with a user's official Circuit Rhythm firmware file. It
rebuilds the DSP layer of released firmware build 7261. The synthesizer has four
table-saw voices and live attack/decay; there is no sustain stage.

The assembly here is our source, not a disassembly of the stock program.
Integration points and memory constants are needed to connect our code to the
supported input. The adapter validates the complete input hash, derives the
required stock records locally, preserves them except for the six integration
hooks, and checks every output against the released hashes. There are no
extracted load files, stock program maps or firmware binaries in this bundle.

Our source is MIT licensed ([LICENSE](LICENSE)). The assembler's dependencies
have their own licenses: see [toolchain/UPSTREAM.md](toolchain/UPSTREAM.md) and
[toolchain/NOTICES.md](toolchain/NOTICES.md). No compiled assembler is supplied.

## Build the DSP streams

Requires Python 3.12 or later, and the locally built line assembler. Its clean
pinned-source build instructions are in [toolchain/README.md](toolchain/README.md).
The complete build below has been checked on Ubuntu x86-64. Other toolchains
and operating systems have not been checked; final hash validation fails
closed if numerical or assembler behaviour differs.

Run from the repository root. Place your official file at
`firmware/circuit-rhythm-5706.syx`. Its SHA-256 must be
`11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf`.

```sh
python3 -I tools/dspvoice/build.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --out build/dspvoice-0.1.1
```

Choose a fresh output directory; an existing directory is refused. The command
does not access MIDI, a device or the network. Outputs are:

| Local output | SHA-256 |
| --- | --- |
| core1-voice.bin | b792d02b12c5526cc538a805244a6456439d65efe6c90c397f3ee3867e5a9b44 |
| core0-stock-relocated.bin | 4a8618bb189d5ea05374431d62f39331604221ea5f17219d1d6267d25495b814 |

It also writes `manifest.json` and `SHA256SUMS`. The manifest records source
hashes, the actual assembler hash and integration layout; its hash can vary
with source packaging or the compiler. The two stream hashes above do not.

**Keep these outputs local.** They combine our code with material read from
your stock file. They are not the publishable source package or release patch.
The core-0 stream remains byte-identical to its input; relocation is performed
only when combining the final image.

## Rebuild the complete released firmware

This also needs the published ARM source and tools in the Arrhythmia repository,
plus its documented ARM compiler. The commands below build the ARM base from
source; they do not take DSP code from the release JSON.

```sh
mkdir -p build
python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-ad
python3 -I tools/patch.py patches/arrhythmia-firmware-arm-ad.json build/arrhythmia-firmware-arm-ad-7251
python3 -I tools/dspvoice/build.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --arm-image build/arrhythmia-firmware-arm-ad-7251.bin \
  --out build/arrhythmia-7261-source
```

The final `.syx` SHA-256 is
`920b2893e8427fc0ea468a414ad7bfc82705aa499828be4375196d6dca2b011b`.
The image SHA-256 is
`fef8e4072696e177104aa2d31d7fcc725ba4dc746754e18d1f8a97a33deb5aaf`.
Any mismatch refuses to write the output directory. The command builds files
only; use the separate installation instructions if installing released firmware.

## Check the source package

`source-manifest.json` names and hashes the source/test files of this bundle.
It excludes itself; the package tests verify the named files before optional
firmware reproduction.

Checks without a firmware file run the generated-table, import-closure and
malformed-input tests. The optional inputs enable assembler and byte-identical
stream/image reproduction in a temporary copy of this package:

```sh
python3 -I tests/test_dspvoice_source.py
python3 -I tests/test_dspvoice_source.py \
  --stock firmware/circuit-rhythm-5706.syx \
  --assembler build/dspvoice-toolchain/dspasm \
  --arm-image build/arrhythmia-firmware-arm-ad-7251.bin
```

The source assembly, generated parameters and expected component hashes are
pinned to the release. This is a reproducibility builder, not a general synth
editor: an intentional DSP change needs new expected component/stream/image
hashes and separate validation. Reproduction tests establish byte identity;
they are not additional hardware tests or timing measurements.
