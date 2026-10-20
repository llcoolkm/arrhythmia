# DSP line assembler

Build from this directory's frontend and the exact public dependency described in
[UPSTREAM.md](UPSTREAM.md). Requires Git, CMake >= 3.15, a C++17 compiler and a
native build tool (Make or Ninja). No Python packages or firmware are required.

From the repository root:

```sh
cmake -S tools/dspvoice/toolchain -B build/dspvoice-toolchain \
  -DDSP56300_SOURCE=/path/to/dsp56300 -DCMAKE_BUILD_TYPE=Release
cmake --build build/dspvoice-toolchain --target dspasm -j 2
printf '000100\tnop\n' | build/dspvoice-toolchain/dspasm
```

Expected output (tab separated):

```text
000100  000000  1  nop
```

Assembly input is `HEX_ADDRESS<TAB>INSTRUCTION`, one instruction per line. Output
is `HEX_ADDRESS<TAB>WORD [WORD]<TAB>DECIMAL_LENGTH<TAB>DISASSEMBLY`.
`dspasm dis` accepts `HEX_ADDRESS<TAB>WORD [WORD]` and returns
`HEX_ADDRESS<TAB>DECIMAL_LENGTH<TAB>DISASSEMBLY`. All words and addresses are hex.
Assembly failures produce `ERR<TAB>CODE<TAB>INSTRUCTION` and a nonzero exit status.
This is a line protocol for generated valid input, not a full source assembler:
labels, sections, symbol resolution and malformed protocol recovery are outside
its interface.

The pinned source build has been checked on Ubuntu x86-64 with GCC 15.2.0 and
CMake 4.2.3 (Release). Pins reproduce assembler behavior; executable byte hashes
can differ between compilers or build environments. See [UPSTREAM.md](UPSTREAM.md)
for source acquisition and licenses.
