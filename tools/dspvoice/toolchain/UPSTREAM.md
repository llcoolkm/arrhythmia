# Pinned assembler dependency

The build uses public upstream source, not a redistributed executable or firmware.

| Component | Repository | Exact revision | License evidence |
| --- | --- | --- | --- |
| DSP56300 | https://github.com/dsp56300/dsp56300 | `2a7ca1e9ce4b0d74b47d9a6384f6d15c1588d2b0` | `LICENSE.md`: GNU GPL version 3; `README.md`: GPLv3 |
| AsmJit submodule (`source/asmjit`) | https://github.com/dsp56300/asmjit | `3577608cab0bc509f856ebf6e41b2f9d9f71acc4` | `LICENSE.md`: zlib license, Copyright (c) 2008-2020 The AsmJit Authors |
| Bundled Intel VTune support (Linux/Windows upstream build dependency) | Included in DSP56300 revision above | Same parent revision | File headers: `GPL-2.0-only OR BSD-3-Clause`; use BSD alternative, text in `source/vtuneSdk/src/ittnotify/LICENSE.BSD` |

Obtain the exact source and its declared submodule:

```sh
git clone https://github.com/dsp56300/dsp56300.git /path/to/dsp56300
git -C /path/to/dsp56300 checkout --detach 2a7ca1e9ce4b0d74b47d9a6384f6d15c1588d2b0
git -C /path/to/dsp56300 submodule update --init --recursive
```

CMake verifies both revisions and requires clean trees, including untracked files.
Use an out-of-tree build. The debugger and interpreter/JIT execution are not used
by this line assembler; upstream's library target still builds its normal static
library dependencies. Only the `dspasm` executable is requested by our build.

Our original frontend source and build recipe are MIT licensed. DSP56300 remains
GPLv3; our MIT notice does not cover upstream dependencies. No compiled tool or
upstream source is distributed here. Building the frontend links it with the GPLv3
library; any distribution of that combined executable must meet GPLv3 obligations,
including corresponding source (frontend/build recipe, exact upstream source and
submodule) and applicable full licenses and copyright notices. The frontend source
retains its MIT license. Merely assembling our own DSP firmware does not insert
upstream tool source into it or change that firmware's license. Full dependency
license texts are retained in [NOTICES.md](NOTICES.md); source acquisition above
retrieves the upstream copyright and license notices unchanged.
