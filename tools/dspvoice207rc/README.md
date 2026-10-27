# VERSION207 candidate7316 own-source rebuild

This separate candidate rebuild preserves the7308 and206 builders. It adds the common mono−9dB output gain and joins only the exact recovery ARM input for7316. Gain selection and release publication remain pending review. No installer or device access is included.

Build the line assembler from source using [the toolchain instructions](../dspvoice/toolchain/README.md), then run:

```sh
mkdir -p build
python3 -I tools/syx.py unpack firmware/circuit-rhythm-5706.syx build/circuit-rhythm-5706.bin
python3 -I tools/build_lightshow.py arrhythmia-firmware-arm-fm-recover
python3 -I tools/patch.py patches/arrhythmia-firmware-arm-fm-recover.json build/arrhythmia-firmware-arm-fm-recover-7304
python3 -I tools/dspvoice207rc/build.py --stock firmware/circuit-rhythm-5706.syx --assembler build/dspvoice-toolchain/dspasm --arm-image build/arrhythmia-firmware-arm-fm-recover-7304.bin --out build/arrhythmia-7316-source
```

Omit `--arm-image` to rebuild the two DSP streams only. The ARM input must have SHA2564cff8b24b1b985b50a594e096522483476682a3ffce0efc27d5a29937483142e. The official5706 file is the only firmware input; the ARM binary is independently built from source. Combined stock-derived outputs stay local. The public output uses the stock Play confirmation. Gated, altered or wrong-version ARM inputs are refused. Existing output paths, including symlinks, are refused. Linux atomic no-replace directory creation is required.

Expected core0 stream8353ffb906c6ff0a8807ef4884980b5cb042d763f56d96303ad8ade63ce9bbf5, core1b3f9e8481e11e634c9aead897b00c7ed3d80c5926434e4456614953ab4e8d948, fullBIN92d83ecaaa3f69faf214458c94ffad75f88b75ce6e5b4ade02344577c7706ba3, SYX21b54eab76a3ac86cd405a4653cd07b77d716d2f30ce8b1bc8b344e93e99048f. These are candidate reproduction pins, not installation or publication approval. No silicon timing or general clean-headroom claim is made.
