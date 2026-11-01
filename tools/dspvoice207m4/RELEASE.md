# Public7339 own-source rebuild

This separate wrapper preserves the accepted -4 dB component and every historical
builder. It assembles that component from its complete pinned source closure,
then joins only the exact approved public7339 ARM input. No alternate labels,
gains, DSP streams, gates or ARM inputs are accepted. Firmware outputs contain
material from the supplied official update and remain local.

The only accepted ARM input is the public7304 ARM part from frozen source
b51d653, SHA-256 `a1f5ed53d3d23c250736eede1c8fa27a13d320783d599b758bd9627db0b7c2ac`
(419316 bytes). It produces public7339 / update version000839 only:

- BIN: `5f10a0ac33e1358ab9bf51eb22f05d1172e22f5c0c91cb5b86dec2e1d23458df`
- SYX: `7aadbde0811a77b23c05a06fca34c36d27d98c98945f3f587bb8d7ccf63fc822`
- BIN size458732 bytes; update CRC-32/MPEG-2 `6E5A4843`.

The release manifest binds the exact ARM main and far blobs, far header/key/table
count and placement. Far code is preserved at0801B000..0801E70C; the new core0
stream starts080768C0. The public stock update INIT slot remains08043379.
The gated7340 image is deliberately unsupported.

```
python -I tools/dspvoice207m4/release.py --stock firmware/circuit-rhythm-5706.syx \
  --assembler /path/to/dspasm --arm-image /path/to/public7339-arm.bin --out /new/output
```

The output directory must not exist; publication uses atomic no-replace rename.
The release manifest binds the component manifest and the separate wrapper,
documentation and focused tests. This source identity does not establish hardware
qualification. Component build instructions and assembler provenance remain in
README.md and the exported toolchain documentation.

Run the focused rebuild and refusal checks with the same three local inputs:

```
python -I tests/test_dspvoice207m4_release.py --stock /path/to/circuit-rhythm-5706.syx \
  --assembler /path/to/dspasm --arm-image /path/to/public7339-arm.bin
```

Without input arguments, tests check source closure and atomic publication only;
the integration checks are explicitly skipped.
