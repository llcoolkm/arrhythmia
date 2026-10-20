"""Release patch format (crpatch/1): build a publishable release JSON from a patch.py spec, apply it, scan it.

    python3 -I tools/crpatch.py build  SPEC.json STOCK.syx RELEASE.json [--name N] [--description D] [--add]
                                                                       [--release NAME, default Arrhythmia]
    python3 -I tools/crpatch.py apply  RELEASE.json STOCK.syx OUT.syx [--build N]
    python3 -I tools/crpatch.py scan   RELEASE.json STOCK.syx
    python3 -I tools/crpatch.py embed  RELEASE.json web/index.html

SPEC is a tools/patch.py patch set. A release holds one shared `input` pin and a list of `builds` (D26: v0.1 ships
"firmware" and "devkit"), each with its own name, one-line description, output pins and patches. `build` writes a
release with one build; `--add` appends (or replaces) a build in an existing release whose input must match. The
page shows them as "Arrhythmia <name>" and defaults to the one named "firmware". A legacy single-build file (output and
patches at the top level) is still read.

A release JSON carries only our bytes. Stock content is never published:
* every patch span over stock bytes is checked by `expect_sha256` (hash of the stock span), never by its bytes;
  spans past the stock image end are `expect_ff` (patch.py's extension rule);
* a patch's content is a list of pieces: `hex` (our bytes), `fill` (one repeated byte), or `copy` (a span of the
  user's own stock image, by address, length and SHA-256: relocated stock code is computed locally);
* the whole input (.syx and image) and the whole output (.syx, image, CRC, sizes) are pinned; any mismatch refuses.

apply() reproduces tools/patch.py exactly (same bounds, vector, overlap, extension, word padding, version and
syx.build rules), so the output is byte-identical to patch.py's for the spec the release was built from.
web/index.html implements the same apply in JavaScript; tests/test_web_installer.py asserts byte equality.

The stock scan rejects any 16-byte window of published `hex` bytes that occurs anywhere in the stock image.
Exempt by rule: windows of one repeated byte (zero initialisers, FF padding). The builder turns every stock match of
16 bytes or more into a `copy` piece, so the provenance of each stock-derived byte is explicit in the release.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import syx  # noqa: E402
from patch import BASE, VECTORS_END, FLASH_END  # noqa: E402

FORMAT = "crpatch/1"
WINDOW = 16          # stock-scan window and minimum copy/fill length
LABEL_OFF = 0x1CA    # build label (u16 LE) in the build-info block at 0x080101C8


class Refused(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def label_of(image):
    return int.from_bytes(image[LABEL_OFF:LABEL_OFF + 2], "little")


# ---------------------------------------------------------------- apply

def _pieces(p, stock):
    out = bytearray()
    for pc in p["pieces"]:
        if "hex" in pc:
            out += bytes.fromhex(pc["hex"])
        elif "fill" in pc:
            v, n = int(pc["fill"], 16), pc["len"]
            if not 0 <= v <= 255 or type(n) is not int or n <= 0:
                raise Refused(f"{p['addr']}: bad fill piece")
            out += bytes([v]) * n
        elif "copy" in pc:
            src, n = int(pc["copy"], 16) - BASE, pc["len"]
            if type(n) is not int or n <= 0 or src < 0 or src + n > len(stock):
                raise Refused(f"{p['addr']}: copy source outside the stock image")
            chunk = stock[src:src + n]
            if sha(chunk) != pc["sha256"]:
                raise Refused(f"{p['addr']}: copy source {pc['copy']} does not match its SHA-256")
            out += chunk
        else:
            raise Refused(f"{p['addr']}: unknown piece")
    return bytes(out)


def builds(rel):
    """The list of builds in a release; a legacy single-build file counts as one build."""
    if rel.get("format") != FORMAT:
        raise Refused("not a crpatch/1 release")
    if "builds" in rel:
        bl = rel["builds"]
    else:
        bl = [{"name": rel.get("name", "release"), "description": rel.get("description", ""),
               "output": rel["output"], "patches": rel["patches"]}]
    if not isinstance(bl, list) or not bl:
        raise Refused("release holds no builds")
    names = [b.get("name") for b in bl]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise Refused("build names must be unique non-empty strings")
    return bl


def select(rel, name=None):
    """The build named `name`; with no name, "firmware" if present, else the first build."""
    bl = builds(rel)
    if name is None:
        return next((b for b in bl if b["name"] == "firmware"), bl[0])
    for b in bl:
        if b["name"] == name:
            return b
    raise Refused(f"no build named {name!r} (have {[b['name'] for b in bl]})")


def apply(rel, stock_syx, build=None, pins=True):
    """Return (output .syx bytes, output image) or raise Refused. Mirrors tools/patch.py apply()."""
    b = select(rel, build)
    pin_in, pin_out = rel["input"], b["output"]
    if sha(stock_syx) != pin_in["syx_sha256"]:
        raise Refused("input .syx SHA-256 does not match the pinned stock file")
    try:
        ref = syx.parse(stock_syx)
    except ValueError as e:
        raise Refused(f"input .syx malformed: {e}")
    if not ref["crc_ok"]:
        raise Refused("base stream CRC mismatch")
    stock = ref["image"]
    image = bytearray(stock)
    if sha(stock) != pin_in["image_sha256"]:
        raise Refused("base image SHA-256 does not match the pinned image")
    if ref["header_prefix"][0] != 0:
        raise Refused("base header type is not 0 (application)")
    if not VECTORS_END - BASE + 6 <= len(image) <= FLASH_END - BASE:
        raise Refused("base image size is outside application bounds")
    if len(image) % 4:
        raise Refused("base application image length must be word-aligned")
    if bytes(image[0x1CC:0x1CE]) != ref["init"][:2]:
        raise Refused("INIT family/device does not match the pinned application build-info")
    vv = pin_out["version_digits"]
    if not isinstance(vv, list) or len(vv) != 6 or any(type(v) is not int or not 0 <= v <= 9 for v in vv):
        raise Refused("version_digits must be six decimal digits 0..9")
    version = bytes(vv)
    intervals = []
    for p in b["patches"]:
        addr, n = int(p["addr"], 16), p["len"]
        new = _pieces(p, stock)
        if type(n) is not int or not new:
            raise Refused(f"{p['addr']}: empty patch")
        if len(new) != n:
            raise Refused(f"{p['addr']}: pieces give {len(new)} bytes, len says {n}")
        if addr < BASE or addr + n > FLASH_END:
            raise Refused(f"{p['addr']}: outside the application flash")
        if addr < VECTORS_END and not p.get("allow_vectors"):
            raise Refused(f"{p['addr']}: inside the vector table")
        if any(addr < end and start < addr + n for start, end in intervals):
            raise Refused(f"{p['addr']}: overlaps another patch")
        intervals.append((addr, addr + n))
        off = addr - BASE
        if off + n > len(image):
            if off < len(image):
                raise Refused(f"{p['addr']}: straddles the image end")
            image.extend(b"\xff" * (off + n - len(image)))
            old_now = b"\xff" * n
        else:
            old_now = bytes(image[off:off + n])
        if p.get("expect_ff"):
            ok = old_now == b"\xff" * n
        else:
            ok = sha(old_now) == p["expect_sha256"]
        if not ok:
            raise Refused(f"{p['addr']}: stock span does not match the expected hash")
        image[off:off + n] = new
    image.extend(b"\xff" * (-len(image) % 4))
    init = ref["init"][:2] + version + ref["init"][8:]
    header_prefix = ref["header_prefix"][:1] + version
    out = syx.build(bytes(image), init, header_prefix)
    check = syx.parse(out)
    if not (check["crc_ok"] and check["image"] == bytes(image) and check["header_prefix"][0] == 0):
        raise Refused("output round trip failed")
    got = {"syx_sha256": sha(out), "image_sha256": sha(image), "syx_size": len(out),
           "image_size": len(image), "crc32_mpeg2": f"{check['crc']:08x}", "label": label_of(image)}
    for k, v in got.items():
        if pins and pin_out.get(k) != v:
            raise Refused(f"output {k} {v} does not match the pinned {pin_out.get(k)}")
    return out, bytes(image)


# ---------------------------------------------------------------- build

def _windows(stock):
    idx = {}
    for i in range(len(stock) - WINDOW + 1):
        w = stock[i:i + WINDOW]
        lst = idx.setdefault(w, [])
        if len(lst) < 8:
            lst.append(i)
    return idx


def _split(new, stock, idx):
    """Deterministic decomposition of `new` into hex / fill / copy pieces (greedy, longest at each position)."""
    pieces, lit, i = [], bytearray(), 0

    def flush():
        if lit:
            pieces.append({"hex": lit.hex()})
            lit.clear()
    while i < len(new):
        f = 1
        while i + f < len(new) and new[i + f] == new[i]:
            f += 1
        best_c, best_src = 0, None
        for s in idx.get(new[i:i + WINDOW], ()):
            c = WINDOW
            while i + c < len(new) and s + c < len(stock) and new[i + c] == stock[s + c]:
                c += 1
            if c > best_c:
                best_c, best_src = c, s
        if f >= WINDOW and f >= best_c:
            flush()
            pieces.append({"fill": f"{new[i]:02x}", "len": f})
            i += f
        elif best_c >= WINDOW:
            flush()
            chunk = stock[best_src:best_src + best_c]
            pieces.append({"copy": f"0x{BASE + best_src:08X}", "len": best_c, "sha256": sha(chunk)})
            i += best_c
        else:
            lit.append(new[i])
            i += 1
    flush()
    return pieces


def input_pin(stock_syx):
    ref = syx.parse(stock_syx)
    stock = ref["image"]
    return {"device": "circuit-rhythm", "device_id": ref["device"], "build": label_of(stock),
            "syx_sha256": sha(stock_syx), "syx_size": len(stock_syx),
            "image_sha256": sha(stock), "image_size": len(stock),
            "version_digits": list(ref["init"][2:8])}


PRODUCT = "Arrhythmia"


def build(spec, stock_syx, name=None, description=None, release_name=PRODUCT):
    """A release with one build; see build_one() and add_build()."""
    rel = {"format": FORMAT, "name": release_name, "input": input_pin(stock_syx), "builds": []}
    add_build(rel, build_one(spec, stock_syx, name or "release", description), stock_syx)
    return rel


def add_build(rel, b, stock_syx):
    """Append (or replace by name) a build; the release input pin must match the stock file."""
    if rel["input"] != input_pin(stock_syx):
        raise Refused("release input pin does not match this stock file")
    kept = [x for x in rel.get("builds", []) if x["name"] != b["name"]]
    for x in kept:
        if x["output"]["label"] == b["output"]["label"]:
            raise Refused(f"build {b['name']!r} has the same label {b['output']['label']} as {x['name']!r}")
    trial = dict(rel, builds=kept + [b])
    apply(trial, stock_syx, b["name"])      # refuse before the release is changed
    rel["builds"] = trial["builds"]
    return rel


def build_one(spec, stock_syx, name, description=None):
    ref = syx.parse(stock_syx)
    stock = ref["image"]
    if sha(stock) != spec["base_sha256"]:
        raise Refused("stock image does not match the spec's base_sha256")
    idx = _windows(stock)
    patches = []
    for p in spec["patches"]:
        addr, old, new = int(p["addr"], 16), bytes.fromhex(p["expect"]), bytes.fromhex(p["new"])
        off = addr - BASE
        e = {"addr": p["addr"], "len": len(new)}
        if off + len(old) <= len(stock):
            e["expect_sha256"] = sha(stock[off:off + len(old)])
        elif off >= len(stock) and old == b"\xff" * len(old):
            e["expect_ff"] = True
        else:
            raise Refused(f"{p['addr']}: span straddles the stock image end")
        if p.get("allow_vectors"):
            e["allow_vectors"] = True
        e["pieces"] = _split(new, stock, idx)
        if p.get("note"):
            e["note"] = p["note"]
        patches.append(e)
    b = {"name": name, "description": description if description is not None else spec.get("description", ""),
         "output": {"version_digits": list(spec["version_nibbles"])}, "patches": patches}
    rel = {"format": FORMAT, "input": input_pin(stock_syx), "builds": [b]}
    # Pin the output by applying the build itself, then apply once more with the pins checked.
    out, image = apply(rel, stock_syx, name, pins=False)
    b["output"].update({"syx_sha256": sha(out), "image_sha256": sha(image), "syx_size": len(out),
                        "image_size": len(image), "crc32_mpeg2": f"{syx.parse(out)['crc']:08x}",
                        "label": label_of(image)})
    apply(rel, stock_syx, name)
    return b


# ---------------------------------------------------------------- scan

def exempt(window):
    return len(set(window)) == 1


def scan(rel, stock):
    """Return a list of (patch addr, offset in its hex bytes, stock address) for non-exempt stock windows."""
    stock_set = set(stock[i:i + WINDOW] for i in range(len(stock) - WINDOW + 1))
    hits = []
    for p in (p for b in builds(rel) for p in b["patches"]):
        for pc in p["pieces"]:
            if "hex" not in pc:
                continue
            b = bytes.fromhex(pc["hex"])
            for i in range(len(b) - WINDOW + 1):
                w = b[i:i + WINDOW]
                if w in stock_set and not exempt(w):
                    hits.append((p["addr"], i, f"0x{BASE + stock.find(w):08X}"))
    return hits


# ---------------------------------------------------------------- embed

BEGIN, END = '<script type="application/json" id="release">', "</script>"


def embed(rel_text, html):
    i = html.index(BEGIN) + len(BEGIN)
    j = html.index(END, i)
    body = json.dumps(json.loads(rel_text), separators=(",", ":")).replace("<", "\\u003c")
    return html[:i] + body + html[j:]


def dump(rel):
    return json.dumps(rel, indent=1) + "\n"


def _opts(argv):
    pos, opts, i = [], {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            k = argv[i][2:]
            if k == "add":
                opts[k] = True
            else:
                opts[k] = argv[i + 1]
                i += 1
        else:
            pos.append(argv[i])
        i += 1
    return pos, opts


def main(argv):
    pos, opts = _opts(argv)
    if len(pos) < 3:
        sys.exit(__doc__)
    cmd = pos[0]
    try:
        if cmd == "build" and len(pos) == 4:
            spec, stock_syx = json.loads(Path(pos[1]).read_text()), Path(pos[2]).read_bytes()
            name = opts.get("name", Path(pos[1]).stem)
            b = build_one(spec, stock_syx, name, opts.get("description"))
            if opts.get("add"):
                rel = json.loads(Path(pos[3]).read_text())
                builds(rel)
                if "builds" not in rel:
                    rel = {"format": FORMAT, "name": opts.get("release", PRODUCT), "input": rel["input"],
                           "builds": builds(rel)}
                add_build(rel, b, stock_syx)
            else:
                rel = {"format": FORMAT, "name": opts.get("release", PRODUCT), "input": input_pin(stock_syx),
                       "builds": []}
                add_build(rel, b, stock_syx)
            hits = scan(rel, syx.parse(stock_syx)["image"])
            if hits:
                sys.exit(f"stock scan failed: {hits[:5]}")
            Path(pos[3]).write_text(dump(rel))
            n = {k: sum(1 for p in b["patches"] for pc in p["pieces"] if k in pc) for k in ("hex", "fill", "copy")}
            lit = sum(len(pc["hex"]) // 2 for p in b["patches"] for pc in p["pieces"] if "hex" in pc)
            print(f"wrote {pos[3]}: build {name!r}, {len(b['patches'])} patches, pieces {n}, {lit} published bytes")
            print(f"output label {b['output']['label']}  sha256 {b['output']['syx_sha256']}")
            print(f"builds now: {[x['name'] for x in rel['builds']]}")
        elif cmd == "apply" and len(pos) == 4:
            out, _ = apply(json.loads(Path(pos[1]).read_text()), Path(pos[2]).read_bytes(), opts.get("build"))
            Path(pos[3]).write_bytes(out)
            print(f"wrote {pos[3]}  sha256 {sha(out)}")
        elif cmd == "scan" and len(pos) == 3:
            hits = scan(json.loads(Path(pos[1]).read_text()), syx.parse(Path(pos[2]).read_bytes())["image"])
            for h in hits:
                print("stock run", *h)
            if hits:
                sys.exit(f"{len(hits)} stock windows in published bytes")
            print("no 16-byte stock run in published bytes")
        elif cmd == "embed" and len(pos) == 3:
            html = Path(pos[2]).read_text()
            Path(pos[2]).write_text(embed(Path(pos[1]).read_text(), html))
            print(f"embedded {pos[1]} into {pos[2]}")
        else:
            sys.exit(__doc__)
    except Refused as e:
        sys.exit(f"refused: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
