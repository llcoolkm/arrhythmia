# DSP emulator harness

An offline harness that runs a Circus Arrhythmia engine file (CRE3) on a software DSP56300
interpreter and measures it: audio level, interpreter cycles per block, and register extremes
at chosen program addresses. It needs no unit, no MIDI and no firmware.

Licence: GPL v3 for this whole directory (see [LICENSE](LICENSE)). The harness links the
dsp56300 emulator, which is GPL v3, and the patches below modify it.

## Upstream emulator

This directory does not contain the emulator. It contains only our two patches, the harness
source and the Python runners.

| | |
| --- | --- |
| project | dsp56300, https://github.com/dsp56300/dsp56300 (GPL v3) |
| pinned commit | the commit dated 2026-10-14 with the subject "JIT: a block that starts at the last address of an untouched region of the block cache no longer writes into the page that all untouched regions share" |
| submodule | `source/asmjit`, taken at the commit the pinned commit records |

## Files

| File | Content |
| --- | --- |
| `access-hook.patch` | adds an optional memory access observer and an optional per-instruction observer (interpreter only) |
| `peripherals-open.patch` | removes `final` from the no-op peripherals class so the harness can derive from it |
| `harness.cpp`, `CMakeLists.txt` | the harness; `DSP56300_SOURCE` points at your patched checkout |
| `runners/abi3.py` | encoders for the engine ABI 3.0 commands (engine page: `docs/user/engines-dev.md`) |
| `runners/rig.py` | scene, stimulus text, trace parsing, audio extraction |
| `runners/measure.py` | the fixture and its measurements |

## Build

Needs git, a C++17 compiler, CMake 3.15 or later and patch. Run from the repository root;
`EMU` is any directory of your choice.

```sh
EMU=$HOME/dsp-emulator
git clone https://github.com/dsp56300/dsp56300.git $EMU/dsp56300
PIN=$(git -C $EMU/dsp56300 log --all --format=%H --fixed-strings \
  --grep='JIT: a block that starts at the last address of an untouched region of the block cache')
git -C $EMU/dsp56300 checkout "$PIN"
git -C $EMU/dsp56300 submodule update --init
patch -d $EMU/dsp56300 -p1 < tools/dsp/emulator/access-hook.patch
patch -d $EMU/dsp56300 -p1 < tools/dsp/emulator/peripherals-open.patch
cmake -S tools/dsp/emulator -B $EMU/build -DDSP56300_SOURCE=$EMU/dsp56300 -DCMAKE_BUILD_TYPE=Release
cmake --build $EMU/build --target dsp-harness -j 8
```

The checkout step finds the pinned commit by its subject; `git checkout` fails unless exactly
one commit matches. Both patches must apply cleanly; `patch` stops on any rejected hunk, and the CMake step
refuses a tree without them.

The binary is `$EMU/build/dsp-harness`. CMake refuses a checkout without both patches. The
build uses the interpreter only (the emulator's JIT is switched off), so cycle counts are
reproducible.

## Run

The engine file is a release asset, for example `fm.dse` of release 0.3.1. The runner needs
Python 3.12 or later and nothing else.

```sh
python3 -I tools/dsp/emulator/runners/measure.py \
  --harness $EMU/build/dsp-harness --engine fm.dse --out $EMU/run-fm
```

The defaults are the FM fixture: MIDI note 60, velocity 100, one lane, sources
`0,0,64,64,0,0,127,0,0,0,64,127` (the FM header defaults), 1,502 held blocks, RELEASE, then
2,253 release blocks (3,759 blocks of 32 samples, about 2.5 s of audio). Output directory must
not exist. The last lines of the result are:

```
"rms_dbfs": -32.2466...,
```

that is, about -32.25 dBFS RMS for `fm.dse` of release 0.3.1 (the unrounded value
is -32.24665988402502). The DSP arithmetic is integer, so the value is reproducible.

Other engines use the same command with their own raw sources, for example a resonator
engine with `--sources 64,64,64,64,64,64,64,36,0,0,64,64 --param 16=0`, or a formant engine
with `--sources 64,64,64,64,64,64,64,64,0,64,64,64 --param 16=0 --param 17=64 --param 18=64
--param 19=64 --param 20=64`. Only the FM engine file is a published release asset at the
time of writing. `--sources` takes sources 0..11, `--param SOURCE=VALUE` adds PARAM singles for
sources 16 and up, `--notes 48,55,60,67` plays one note per lane, `--velocity` sets the
velocity, `--wav` also writes the audio.

## What the outputs mean

`result.json` and the printed copy:

| Field | Meaning |
| --- | --- |
| `rms_dbfs` | RMS of the output from 0.05 s to 0.6 s after the start of the onset block (`--window`), in dB below full scale (2^23). Mono, before any mixer |
| `peak`, `rails` | largest absolute sample as a fraction of full scale; samples at the rails |
| `cycles_max`, `cycles_median` | interpreter cycles between the engine entry and its return to the shim, over the blocks from the onset on |
| `cycles_init` | the same for the first (initialisation) block |
| `all_blocks_completed` | every block entered the engine, returned through the shim and reached the mixer stub; the exit status is 1 otherwise |
| `watch` | with `--watch HEXPC`: how often the program counter passed that P address, and the smallest and largest signed value of x0, x1, y0 and y1 at those moments |

Cycles are the interpreter's count. They are a model, not silicon timing, and no meter or
budget figure follows from them.

## What it does not do

* There is no DSP program of the unit in it. The harness calls the engine through the shim
  that is part of the engine file, with synthetic stubs for the caller and the track mixer
  (the mixer stub only returns). Nothing of the unit's own mixing, effects or timing exists.
* There are no peripherals: every peripheral read returns zero except the output-half word,
  and writes are discarded.
* Silicon behaviour is not reproduced. Hardware hazards of loops, pipeline delays and
  peripheral timing are not modelled, and the interpreter reads some multiply operands its
  own way. A pass here is not a hardware result.
* The harness stimulus language (`harness.cpp`: LOAD, MODE, SR, CALLER, WATCH, WLOG, TRIGGER,
  STARTUP, BLOCK, DUMP, CALL, VEC) is for tool authors; `runners/rig.py` shows its use.
