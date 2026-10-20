"""Minimal stdlib WAV reader/writer for the capture tools (no numpy in this project).

Reads RIFF/WAVE with format tag 1 (PCM 16/24/32), 3 (IEEE float 32/64) and 0xFFFE (WAVE_FORMAT_EXTENSIBLE with a
PCM or float subformat). Unknown chunks (bext, iXML, LIST, ...) are skipped, so recorder files with broadcast metadata
load. Samples come back per channel as floats scaled to [-1, 1) (int full scale = 1.0) plus the integer full scale,
so a report can quote both dBFS and raw units.
"""
import array
import os
import struct
import sys

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE
_BIG = sys.byteorder == "big"


class WavError(ValueError):
    pass


def _frombytes(code, raw):
    a = array.array(code)
    a.frombytes(raw[:len(raw) - len(raw) % a.itemsize])
    if _BIG:
        a.byteswap()
    return a


def decode(raw, sample_type, bits):
    """Interleaved bytes -> list of numbers (ints for PCM, floats for float)."""
    if sample_type == "float":
        return list(_frombytes("f" if bits == 32 else "d", raw))
    if bits == 16:
        return list(_frombytes("h", raw))
    if bits == 32:
        return list(_frombytes("i", raw))
    if bits == 24:
        n = len(raw) // 3
        lo, mid, hi = raw[0:3 * n:3], raw[1:3 * n:3], raw[2:3 * n:3]
        return [(a | b << 8 | c << 16) - ((c & 0x80) << 17) for a, b, c in zip(lo, mid, hi)]
    raise WavError(f"unsupported PCM width {bits}")


def encode(values, sample_type, bits):
    """List of numbers (ints for PCM, floats for float) -> interleaved little-endian bytes."""
    if sample_type == "float":
        a = array.array("f" if bits == 32 else "d", values)
    elif bits == 16:
        a = array.array("h", values)
    elif bits == 32:
        a = array.array("i", values)
    elif bits == 24:
        out = bytearray(3 * len(values))
        for i, v in enumerate(values):
            out[3 * i:3 * i + 3] = (v & 0xFFFFFF).to_bytes(3, "little")
        return bytes(out)
    else:
        raise WavError(f"unsupported PCM width {bits}")
    if _BIG:
        a.byteswap()
    return a.tobytes()


def header(rate, channels, sample_type, bits, data_bytes):
    """44-byte canonical header (tag 1 or 3). data_bytes may be patched later with patch_sizes()."""
    tag = FLOAT if sample_type == "float" else PCM
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", data_bytes))


def patch_sizes(f, data_bytes):
    f.seek(4)
    f.write(struct.pack("<I", 36 + data_bytes))
    f.seek(40)
    f.write(struct.pack("<I", data_bytes))


VALID_WIDTHS = {"pcm": (16, 24, 32), "float": (32, 64)}


def read_info(path):
    """Parse and validate chunks; return dict(rate, channels, sample_type, bits, frames, tag, data_offset, data_bytes,
    chunks). Refuses chunks that run past the end of the file, partial frames and implausible formats, so a header
    claiming 12 s over a one-frame payload is an error, not 12 s of audio."""
    size_on_disk = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise WavError(f"{path}: not a RIFF/WAVE file")
        if head[:4] == b"RF64":
            raise WavError(f"{path}: RF64 not supported")
        info, chunks = None, []
        while True:
            ch = f.read(8)
            if not ch:
                break
            if len(ch) < 8:
                raise WavError(f"{path}: truncated chunk header at {f.tell() - len(ch)}")
            cid, size = ch[:4], struct.unpack("<I", ch[4:])[0]
            start = f.tell()
            if start + size > size_on_disk:
                raise WavError(f"{path}: chunk {cid!r} claims {size} bytes, only {size_on_disk - start} left")
            chunks.append(cid.decode("latin-1"))
            if cid == b"fmt ":
                if info is not None:
                    raise WavError(f"{path}: second fmt chunk")
                if size < 16:
                    raise WavError(f"{path}: short fmt chunk")
                body = f.read(size)
                tag, nch, rate, byte_rate, block, bits = struct.unpack("<HHIIHH", body[:16])
                orig = tag
                if tag == EXTENSIBLE:
                    if len(body) < 40:
                        raise WavError(f"{path}: short WAVE_FORMAT_EXTENSIBLE")
                    tag = struct.unpack("<H", body[24:26])[0]
                if tag not in (PCM, FLOAT):
                    raise WavError(f"{path}: format tag {tag:#x} not supported")
                st = "float" if tag == FLOAT else "pcm"
                if bits not in VALID_WIDTHS[st]:
                    raise WavError(f"{path}: {st} width {bits} not supported")
                if not 1 <= nch <= 32:
                    raise WavError(f"{path}: {nch} channels")
                if not 8000 <= rate <= 384000:
                    raise WavError(f"{path}: sample rate {rate}")
                if block != nch * bits // 8 or byte_rate != rate * block:
                    raise WavError(f"{path}: inconsistent block align/byte rate (padded containers unsupported)")
                info = {"rate": rate, "channels": nch, "bits": bits, "tag": orig, "sample_type": st}
            elif cid == b"data":
                if info is None:
                    raise WavError(f"{path}: data before fmt")
                if "data_offset" in info:
                    raise WavError(f"{path}: second data chunk")
                block = info["channels"] * info["bits"] // 8
                if size % block:
                    raise WavError(f"{path}: data is {size} bytes, not a whole number of {block}-byte frames")
                info["data_offset"] = start
                info["data_bytes"] = size
                f.seek(size, 1)
            else:
                f.seek(size, 1)
            if size & 1 and f.tell() < size_on_disk:     # pad byte (tolerated if missing at end of file)
                f.seek(1, 1)
        if info is None or "data_offset" not in info:
            raise WavError(f"{path}: missing fmt or data chunk")
        info["chunks"] = chunks
        info["frames"] = info["data_bytes"] // (info["channels"] * info["bits"] // 8)
        return info


def count_full_scale(samples, info):
    """Samples at the format's full-scale codes (PCM: most negative or most positive code; float: |x| >= 1)."""
    if info["sample_type"] == "float":
        return sum(1 for v in samples if abs(v) >= 1.0)
    top = 1.0 - 1.0 / info["full_scale"]
    return sum(1 for v in samples if v <= -1.0 or v >= top)


def read(path):
    """Return (info, channels) where channels is a list of per-channel float lists scaled to full scale 1.0."""
    info = read_info(path)
    with open(path, "rb") as f:
        f.seek(info["data_offset"])
        raw = f.read(info["data_bytes"])
    vals = decode(raw, info["sample_type"], info["bits"])
    n = info["channels"]
    scale = 1.0 if info["sample_type"] == "float" else float(1 << (info["bits"] - 1))
    info["full_scale"] = scale
    return info, [[v / scale for v in vals[c::n]] for c in range(n)]


def write(path, rate, channels, sample_type="pcm", bits=24):
    """channels: list of per-channel float lists (full scale 1.0). PCM is rounded and clamped."""
    n, frames = len(channels), len(channels[0])
    inter = [0.0] * (n * frames)
    for c, ch in enumerate(channels):
        inter[c::n] = ch
    if sample_type == "pcm":
        top = (1 << (bits - 1))
        inter = [max(-top, min(top - 1, round(v * top))) for v in inter]
    raw = encode(inter, sample_type, bits)
    with open(path, "wb") as f:
        f.write(header(rate, n, sample_type, bits, len(raw)))
        f.write(raw)
