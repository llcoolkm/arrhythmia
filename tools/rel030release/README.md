# Public 0.3.0 offline rebuild

Build label 7358, version 000858, application bytes 00 39 3E. This public firmware contains no flashing Play gate. User-facing limitations are recorded in RELEASE-NOTES-0.3.0.md.

Complete the repository's official stock setup first: `firmware/circuit-rhythm-5706.syx` and its decoded `build/circuit-rhythm-5706.bin`. The builder checks the explicit SysEx input and the local decoded image against the official SHA-256 pins before compiling. Use the documented clang/LLVM toolchain. Source hashes in `source-manifest.json` bind the rebuild closure; reviewed source changes require deliberate `python3 -I tools/rel030release/update_manifest.py` regeneration and byte equality verification. The builder enforces fixed BIN/SYX byte pins after recompilation.

```sh
python3 -I tools/rel030_release.py --stock firmware/circuit-rhythm-5706.syx --fm /path/to/fm.dse --out /path/to/new-output
python3 -I tools/rel030release/test_release.py
```

`--fm` requires the pinned 14906-byte FM component, SHA-256 `236b20874ed999c46a8575cd3336b6ac26df9770d11f9f9dfdd6f711cd0029ad`. Rebuild it using `tools/dspvoice207live/build.py` and its documented pinned assembler and official stock input. Its body CRC is 5A7949BD; whole-file CRC 2EB146FE. This component stays on the card; it is never joined into flash.

The fresh output directory contains two independently compiled builds, the byte-identical BIN/SYX, the FM component, checksums, an UNPINNED build report, and `release/v0.3.0.json`. The JSON carries devkit 7228 unchanged from 0.2.2 as the secondary installer build. It contains hash-checked patch pieces only: relocated stock comes from the user's local stock image. The build verifies local JSON application equals BIN/SYX and rejects published literal stock matches. Temporary compiler specs containing local stock bytes are deleted after use. BIN/SYX and intermediate stock inputs are local artifacts and must not be committed or published.

The image uses ENGINE_FLASH=0, exact stock core 0 relocated to 0x0807A01C, unchanged stock core 1 and the stock INIT vtable slot. A fresh rebuild is evidence only; independent pinning and publication are separate decisions.
