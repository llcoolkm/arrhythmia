# Licence notice for tools/dspmeter

`dspasm.cpp` and `CMakeLists.txt` in this directory are our original frontend source and build recipe. They are MIT
licensed. They build against the DSP56300 library (https://github.com/dsp56300/dsp56300), which is GPLv3. Our MIT
notice does not cover that upstream dependency or its own dependencies.

No compiled tool or upstream source is distributed here. Building the frontend links it with the GPLv3 library; any
distribution of that combined executable must meet GPLv3 obligations, including corresponding source and the
applicable full licences and copyright notices. The frontend source keeps its MIT licence.

The same frontend arrangement, the pinned upstream revisions and the full dependency licence texts are described in
[../dspvoice/toolchain/UPSTREAM.md](../dspvoice/toolchain/UPSTREAM.md) and
[../dspvoice/toolchain/NOTICES.md](../dspvoice/toolchain/NOTICES.md).
