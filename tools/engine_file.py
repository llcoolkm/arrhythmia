# SPDX-License-Identifier: MIT
"""Engine files for per-pack track-8 engines: make, inspect, reheader and convert.

    python3 -I tools/engine_file.py fm RELEASE.bin out.dse            FM: cut the engine region of a released flat
                                                                      image (P:0x2000..), header it (queue ABI 0x207)
    python3 -I tools/engine_file.py reheader in.dse out.dse           a CRE2 file: the base id only
    python3 -I tools/engine_file.py convert in.dse out.dse --kind fm --abi N [--build N]
                                                                      a stage-1 CRE1 file to CRE2
    python3 -I tools/engine_file.py show file.dse
    python3 -I tools/engine_file.py inspect3|check3 file.dse

The 32-byte little-endian header ('CRE2'):
    +0 magic   +4 words   +8 CRC-32 (zlib) of the body   +12 queue ABI   +16 base id (CRBC, tools/crbc.py)
    +20 kind (1 FM, 2 reserved)   +24 file build (information only)   +28 zero
then `words` 24-bit big-endian P words for P:0x2000 onwards. Kind, file build and queue ABI are separate fields.

A marked CRE2 file build encodes the engine's independent semantic version: bit 31 is 1,
bits 30..24 are zero, and major/minor/patch occupy bits 23..16/15..8/7..0 (0..255 each).
For example engine 1.0.0 is 0x80010000, independent of firmware 0.3.0 and the DSP
program identity word. Unmarked builds (including historical 0x300) and marked values
with reserved bits set have unknown version; their raw build remains valid information.
CRE1's +12 DSP VERSION is never interpreted as engine semver. Firmware admission uses
ABI/base/kind and body integrity, not this informational build. The +28 word stays zero.

reheader rewrites the base id of a CRE2 file and nothing else: body, word count, body CRC, queue ABI, kind and file
build are kept as the file declares them (an ABI the firmware does not speak stays visible and is refused at load,
never relabelled). convert turns a stage-1 'CRE1' file (+12 the DSP VERSION, information only; no kind, no ABI) into
CRE2: the kind and the queue ABI are given explicitly, the file build defaults to that +12 word. Both check the input
fully (magic, zero bytes, length, body CRC) and build the whole result before anything is written; the output is
written to a temporary file in its directory and moved into place (os.replace), so a refused input leaves an
existing output untouched and IN = OUT is safe. A reheader proves identity only: composed-pair bootstrap, queue and
render qualification of the result are separate and still required.
"""
import json
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crbc  # noqa: E402

CRE1, CRE2 = 0x31455243, 0x32455243
KINDS = {"fm": 1, "sid": 2}
QUEUE_ABI = 0x000207                          # the queue ABI of the FM engine cut from our own release
P_REGION, P_TOP, HDR = 0x2000, 0x4000, 32


def need(ok, what):
    if not ok:
        raise ValueError(what)


def encode_semver(major, minor, patch):
    """Pack an engine semantic version into the informational CRE2 u32 build field."""
    need(all(type(v) is int and 0 <= v <= 255 for v in (major, minor, patch)),
         "engine version components must be integers in 0..255")
    return 0x80000000 | major << 16 | minor << 8 | patch


def decode_semver(build):
    """Return M.m.p for a marked build, otherwise None; never reject legacy builds."""
    if type(build) is not int or not 0 <= build <= 0xFFFFFFFF or build & 0xFF000000 != 0x80000000:
        return None
    return f"{build >> 16 & 255}.{build >> 8 & 255}.{build & 255}"


def make(body, kind, build, abi, base=None):
    need(body and len(body) % 3 == 0 and len(body) // 3 <= P_TOP - P_REGION, f"body of {len(body)} bytes")
    need(0 < kind <= 15 and 0 < abi < 1 << 24, f"kind {kind}, ABI {abi:#x}")
    base = crbc.base_id(crbc.preimage(crbc.parts())) if base is None else base
    return struct.pack("<8I", CRE2, len(body) // 3, zlib.crc32(body), abi, base, kind, build, 0) + body


def parse(f):
    """The header fields and the body, checked (magic, zero bytes, length, body CRC); ValueError if refused."""
    need(len(f) >= HDR, f"{len(f)} bytes: shorter than the header")
    magic, words, crc = struct.unpack_from("<3I", f)
    body = f[HDR:]
    need(magic in (CRE1, CRE2), f"magic {magic:#x}")
    need(0 < words <= P_TOP - P_REGION and len(body) == 3 * words, f"words {words}, body {len(body)} bytes")
    need(zlib.crc32(body) == crc, "body CRC")
    if magic == CRE1:
        info, base = struct.unpack_from("<2I", f, 12)
        need(not any(f[20:HDR]), "CRE1 zero bytes")
        return dict(magic="CRE1", words=words, crc=crc, build=info, base=base, kind=None, abi=None, version=None, body=body)
    abi, base, kind, build = struct.unpack_from("<4I", f, 12)
    need(not any(f[28:HDR]), "CRE2 zero bytes")
    return dict(magic="CRE2", words=words, crc=crc, abi=abi, base=base, kind=kind, build=build, version=decode_semver(build), body=body)


def _same_body(out, h):
    n = parse(out)
    need(n["body"] == h["body"] and (n["words"], n["crc"]) == (h["words"], h["crc"]), "body unchanged")
    return n


def reheader(f, base=None):
    """A CRE2 file with the current (or the given) base id; every other field as the file declares it."""
    h = parse(f)
    need(h["magic"] == "CRE2", "a CRE1 file: convert it (kind and queue ABI given explicitly)")
    out = make(h["body"], h["kind"], h["build"], h["abi"], base)
    n = _same_body(out, h)
    need((n["abi"], n["kind"], n["build"]) == (h["abi"], h["kind"], h["build"]), "ABI, kind and build kept")
    return out


def convert(f, kind, abi, build=None, base=None):
    """A stage-1 CRE1 file as CRE2: kind and queue ABI explicit; the build defaults to the CRE1 +12 word."""
    h = parse(f)
    need(h["magic"] == "CRE1", "not a CRE1 file (a CRE2 file is reheadered, its fields kept)")
    out = make(h["body"], kind, h["build"] if build is None else build, abi, base)
    _same_body(out, h)
    return out


def fm_body(release):
    """The FM engine body of a released flat image: its core-0 stream's words from P:0x2000 (the select-0 stream)."""
    at = struct.unpack_from("<I", release, crbc.C0_PTR_AT - crbc.B)[0] - crbc.B
    n = crbc.u24(release, at)
    need(crbc.u24(release, at + 3) == 0 and n > P_REGION, "a flat image with an engine region")
    return release[at + 6 + 3 * P_REGION:at + 6 + 3 * n]


def write_atomic(path, data):
    """data at path, or nothing: a temporary file in the same directory, flushed, then moved into place."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=d)
    try:
        with os.fdopen(fd, "wb") as o:
            o.write(data)
            o.flush()
            os.fsync(o.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _opts(a, allowed):
    o = {}
    for k in range(0, len(a), 2):
        if a[k] not in allowed or k + 1 >= len(a) or a[k] in o:
            raise ValueError(f"options: {' '.join(a)}")
        o[a[k]] = a[k + 1]
    return o


def inspect_cre3(path, *, details):
    """Read the file and report its static eligibility for this release."""
    import cre3_check

    with open(path, 'rb') as stream:
        data = stream.read()
    info = cre3_check.parse(data) if details else {}
    checks = dict(static_eligible=True, runtime_check_14='NOT_RUN', device_admissible=False, errors=[])
    try:
        cre3_check.check(data)
    except ValueError as exc:
        checks.update(static_eligible=False, errors=[str(exc)])
    if details:
        info.pop('body')
        info['checks'] = checks
        print(json.dumps(info, sort_keys=True, indent=2))
        return 0
    print(json.dumps(checks, sort_keys=True, indent=2))
    return int(not checks['static_eligible'])


def main(a):
    """The CLI: every input read and checked and the result built before the output is touched. Returns the code."""
    try:
        if a and a[0] in ("inspect3", "check3"):
            need(len(a) == 2, f"{a[0]} FILE")
            return inspect_cre3(a[1], details=a[0] == "inspect3")
        if a and a[0] in ("make3", "wrap3", "rewrap3"):
            try:
                import abi3_file
            except ModuleNotFoundError as exc:
                if exc.name != "abi3_file":
                    raise
                raise ValueError("CRE3 authoring tools are unavailable in this checkout.") from None
            return abi3_file.cli(a)
        if a[:1] == ["fm"] and len(a) == 3:
            out = make(fm_body(open(a[1], "rb").read()), KINDS["fm"], QUEUE_ABI, QUEUE_ABI)
        elif a[:1] == ["reheader"] and len(a) == 3:
            out = reheader(open(a[1], "rb").read())
        elif a[:1] == ["convert"] and len(a) >= 3:
            o = _opts(a[3:], ("--kind", "--abi", "--build"))
            need("--kind" in o and "--abi" in o, "convert needs --kind and --abi")
            need(o["--kind"] in KINDS, f"kind {o['--kind']}")
            out = convert(open(a[1], "rb").read(), KINDS[o["--kind"]], int(o["--abi"], 0),
                          int(o["--build"], 0) if "--build" in o else None)
        elif a[:1] == ["show"] and len(a) == 2:
            h = parse(open(a[1], "rb").read())
            print({k: (f"0x{v:X}" if isinstance(v, int) else v) for k, v in h.items() if k != "body"})
            return 0
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (ValueError, OSError, KeyError, TypeError, struct.error) as x:
        print(f"refused: {x}", file=sys.stderr)
        return 1
    write_atomic(a[2], out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
