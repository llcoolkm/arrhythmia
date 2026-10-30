# VERSION207 0.2 release image 7323 (and 0.2.1 image 7329) own-source rebuild

This separate rebuild reproduces the public 0.2 release image arrhythmia-firmware 7323. It keeps the 7316 candidate rebuild in [tools/dspvoice207rc](../dspvoice207rc/README.md) and the 7308 and 206 builders unchanged. The instrument source equals the 7316 source except two instructions: the common mono output gain is −7 dB (`move #>$392cee,x0`), and the FM feedback shift is `asr #1,a,a` (feedback x4) instead of `asr #3,a,a`. The build joins only the exact recovery ARM input. No installer or device access is included.

Build the line assembler from source using [the toolchain instructions](../dspvoice/toolchain/README.md), then run:

```sh
mkdir -p build
python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-fm-recover
python3 -I tools/patch.py patches/arrhythmia-firmware-arm-fm-recover.json build/arrhythmia-firmware-arm-fm-recover-7304
python3 -I tools/dspvoice207rel/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-fm-recover-7304.bin --out build/arrhythmia-7323-source
```

The same builder also joins the 0.2.1 development ARM part (not released) into image 7329, with the same DSP streams:

```sh
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-021 --spec-dir build
python3 -I tools/patch.py build/arrhythmia-firmware-arm-021.json build/arrhythmia-firmware-arm-021-7304
python3 -I tools/dspvoice207rel/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-021-7304.bin --out build/arrhythmia-7329-source
```

The 0.2.1 ARM input must have SHA256 d3ab9200c857eb6abb070ebf9424c26611ddd117c70beba77325cfd84166eb24. Its far blob (0x0801B000 to 0x0801C718) lies in the old stock core-0 stream after the end of core 1; the join accepts ARM changes there only, in that range. Expected 7329 BIN 10939096414b70a25d2e5bdc6d593df4e56563b9abdc9d9870c88d3542fb3619, SYX f756c03a71039ba3edd248d53232f9bac24367a4cf800fe6ffd9b8d00fb15251. The ARM input selects the image (`JOINS` in build.py); any other ARM input is refused.

Omit `--arm-image` to rebuild the two DSP streams only. The ARM input must have SHA256 4cff8b24b1b985b50a594e096522483476682a3ffce0efc27d5a29937483142e (the same recovery ARM as 7316 and 7319). The official 5706 file is the only firmware input; the ARM binary is independently built from source. Combined stock-derived outputs stay local. The public output uses the stock Play confirmation. Gated, altered or wrong-version ARM inputs are refused. Existing output paths, including symlinks, are refused. Linux atomic no-replace directory creation is required.

Expected core0 stream 56c8363d9a68c74fffde5099ed26819da9392d11193136f2242c417c422c4880, core1 b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948, full BIN ee083547dc850a627a987ec15dbbc592d932b3254f8c84f657f1c70410ab73e5, SYX f5f77f2902aa7934c0c5dd22bbb890ce4c5aaa8f683d4f55e10897df95178cd5. These are reproduction pins, not installation or publication approval. No silicon timing or general clean-headroom claim is made.
