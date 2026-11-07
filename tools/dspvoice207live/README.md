# FM x8 and live feedback component

This separate offline builder extends the existing −4 dB `dspvoice207m4` instrument. Historical `dspvoice207rel` and `dspvoice207m4` sources, builders and pins remain unchanged. The feedback history term uses `asr #0` (x8), preserving the prior x4 experiment's fixed-point caveats. This is not a clean-headroom or click-free claim.

The new `fm.dse` is CRE2, kind 1, queue framing ABI `0x207`, engine version `1.0.0` (informational file build `0x80010000`), base CRBC `FE80B6B5`. It requires an ARM producer that knows the additive live packet. An old FM engine must not receive the new command: its dispatcher rejects or drops unsupported commands. The existing base ID and ABI describe composition and queue framing; neither identifies this new capability. An ARM integration must therefore gate live emission using an explicit capability check, such as an exact admitted file build/body identity. Merely checking FM kind/ABI/base does not suffice.

The new command is `word0 = 0xA0B000 | feedback[6:0]`; bits 7..11 are zero. `word1` retains TONE's 21-bit depth/cutoff/resonance layout, with bits 21..23 zero. A valid packet publishes the feedback target to all four voice records, remembers the global target and processes its TONE payload through the existing handler. Both words are validated before any state changes. Invalid packets have no feedback or tone control-state effects. `0xA08000` remains the original TONE and does not change feedback. `0xA09000` remains an onset latch: algorithm, family and feedback snapshots do not modify an existing voice.

After the first valid live packet, a new voice initializes both its current feedback and target from the latest live target. An older pending onset snapshot cannot revert feedback. Until that first packet, the existing onset feedback snapshot initializes both. Sequencer parameter locks must send the effective live feedback before the matching PATCH_FM/ONSET; held voices then follow the same channel target. Algorithm/family remain per-note snapshots. Valid ALL_OFF and an epoch/fault reset clear the live override; invalid ALL_OFF does not. ALL_OFF sets voice targets to zero while preserving current feedback and phase during the existing kill tail.

Current feedback is at record offset `+0x17`, target at the formerly unused `+0x1e`. X:`5809` is the live-valid flag and X:`580a` its Q23 target. Each rendered sample approaches target by signed `(target-current) >> 6`, snapping to the exact target when the difference is less than 64 Q23 units. This bounds coefficient steps and eventually reaches exact zero. It does not establish inaudible transitions in this nonlinear feedback system. No phase, history, envelope or voice generation is reset by a live packet.

The original entry remains P:`2200`; startup P:`2d80` and all tables remain fixed. Five calls and the inline stable-target comparison add seventeen words to the primary instrument (ending at P:`2d75`). A separate 94-word helper is placed at P:`3300..335d`, above the existing table end. The CRE2 file is 14,906 bytes, below the provisioner's 15,360-byte limit. The existing body from P:`2d75..3260`, including startup and tables, is preserved. Its extension P:`3261..32ff` contains 159 zero words before the helper. Core 1 is unchanged.

Build with local official 5706 SysEx and the pinned assembler:

```sh
uv run --no-project python tools/dspvoice207live/build.py \
  --stock /private/circuit-rhythm-5706.syx \
  --assembler /private/dspasm --out /private/new-output
uv run --no-project python tests/test_dspvoice207live_source.py \
  --stock /private/circuit-rhythm-5706.syx --assembler /private/dspasm
```

`own_body(assembler)` independently reconstructs the engine body without an official image: it begins with zero words at P:`2000` and overlays the own instrument P:`2000..2d74`, own startup P:`2d80..2d8a`, generated tables P:`2da0..3260`, and own helper P:`3300..335d`. Every remaining word is zero. The official core-0 stream has only `0x1fed` words and ends below this body. The mixed-stream builder compares its extracted body byte for byte with this independent reconstruction.

`engine-body.bin` and `fm.dse` therefore contain only MIT-licensed own-source program, generated tables and zero padding; they contain no copied official firmware. The pinned body SHA-256 is `4ae18997b9fed97ac89b486d8c243ffddb7c7d7c81b9a9e2f218c120869da51a`; `fm.dse` is `236b20874ed999c46a8575cd3336b6ac26df9770d11f9f9dfdd6f711cd0029ad`. The complete `core0-instrument.bin`, `core0.u24be` and `core1-suppression.bin` streams combine own code with user-provided official firmware and must remain private.

The builder refuses existing output directories, altered source closure, wrong official input, unsupported assembler or unexpected output identity. It creates no ARM image, installer, SysEx upload or device access. Source identity, interpreter render checks and hardware qualification are separate evidence.

Engine versions are independent of firmware versions: this FM synthesizer 1.0.0 ships with firmware 0.3.0. The CRE2 +24 u32 uses the marked encoding documented in `tools/engine_file.py`; the +12 ABI and +28 reserved-zero word retain their layout. Historical build `0x300` is unmarked and has unknown engine version. The DSP program identity word remains `0x300`; changing it would alter executable identity and is unnecessary for file metadata.

This metadata update changes only the +24 build word from the historical file SHA-256 `987279813143818290947d4da9c6686b746e4f5463467993940261ccb5dcbfbc`. Both bodies are identical: 4,958 words with CRC-32 `5A7949BD`. The new whole-file CRC-32 is `2EB146FE`. The engine header admits the full u32 build as informational: `carddsp.c:cd_hdr` checks ABI/base/kind and reserved bytes, but never binds +24. Engine compatibility is defined by those admission checks; compatibility with firmware 0.3.0 does not promise compatibility with every future firmware. Version metadata alone proves neither executable capability nor exact file identity.
