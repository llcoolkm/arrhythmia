# Fixed VERSION 207 −4 dB component

This separate component assembles the entire own instrument source at P:2000. Its source differs from historical `dspvoice207rel/instrument.asm` by exactly one immediate: `move #>$392cee,x0` becomes `move #>$50c336,x0` at P:21EB. FM feedback x4 (`asr #1,a,a`) remains unchanged. Historical release and candidate builders, sources and pins remain untouched.

The lightweight builder reuses the historical release builder's source-verified integration, startup, suppression and tables. It assembles the new source, requires identical complete label maps and sizes, replaces the assembled instrument overlay, and checks that the complete stream differs at P:21EB only. The new stream is exactly the 7335 DSP stream, SHA256 `21ac14ffea93a6c8775153a3b1bae4ffe006d6717e5009e238d4bdb8706f167a`. Core 1 remains `b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948`.

```sh
uv run python -I -B tools/dspvoice207m4/build.py --stock firmware/circuit-rhythm-5706.syx --assembler /path/to/pinned/dspasm --out /private/local/component
```

Only the fixed component is supported. There are no gain, label, ARM input or firmware-join options. Output publication is atomic and never replaces an existing destination. Both source closure and assembler binary are pinned before assembly; assembled component and complete output hashes provide independent checks. No firmware or toolchain binary is included in source.

Program origin, entry, label addresses, instruction lengths, memory allocations, VERSION 207 command ABI and gain-helper cycle count are unchanged. This is not blanket production qualification: native DSP audio, mix headroom, scheduling and hardware results must be reviewed separately. No unit access or installer is provided.
