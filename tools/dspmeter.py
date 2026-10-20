"""DSP cycle meter: program, listing, firmware layer, readout layout.

    python3 -I tools/dspmeter.py listing --dspasm build/dspmeter-tools/dspasm     # regenerate patches/dspmeter.lst
    python3 -I tools/dspmeter.py check-listing [--dspasm PATH]                    # listing == program (+ re-assembly)
    uv run python -I tools/dspmeter.py build [--base patches/devkit-load.json] [--label 6911] [--out build/devkit-meter]
    uv run python -I tools/dspmeter.py build --base patches/devkit-clock.json --label 6011 --version 000749 \
        --out build/devkit-meter-6011                                             # 6011 devkit-meter = 6010 + meter
    python3 -I tools/dspmeter.py extract IMAGE.bin OUTDIR                         # DSP streams of a built image

Each core measures, per 32-frame block, the wall-clock span from block start to the point where the core is done with
the block, using its own triple timer 1 (TCR1 X:$FFFF88, internal clock CLK/2, DSP56724RM chapter 11). Results stay in
DSP X memory (lifetime max/min/sum/count, a 256-window ring) and are read out later, idle only, with cmd 08
(tools/dspload.py; readout layout in docs/user/developers.md).

No Novation bytes are stored in git: the layer is computed from the user's pinned stock image at build time.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

BASE = 0x08010000
FLASH_END = 0x08080000
STOCK_SHA256 = "d5b9ff5dadf2e284f80d69ecb7087486cf0df315a697bed8272af9de0e90df15"
PTR_CORE0 = 0x08031658              # stream pointer, select 0
PTR_CORE1 = 0x08031660              # stream pointer, select 1
STREAM0 = 0x0801A838                # stock stream, select 0
STREAM1 = 0x080101F0                # stock stream, select 1
STOCK_CORE0_WORDS = 0x1FED
STOCK_CORE1_WORDS = 14187
SCATTER_TERMINATOR = 0x000100
LISTING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patches", "dspmeter.lst")

TCSR1, TCR1 = 0xFFFF8B, 0xFFFF88    # timer 1 control/status and count (core-local TEC / TEC_1)
WINDOW = 2048                        # blocks per ring entry
RING_ENTRIES = 256
MAGIC = 0x4D5452                     # "MTR"
VERSION = 1
CORES = {
    0: {"code": 0x1FED, "code_end": 0x4000, "ring": 0x5C00, "pub": 0x5E00, "work": 0x5E10, "x_end": 0x5E40},
    1: {"code": 0xD32, "code_end": 0x1000, "ring": 0x2C00, "pub": 0x2E00, "work": 0x2E10, "x_end": 0x2E40},
}
PUB = ["END", "MAGIC", "ID", "CNT", "MAX", "MIN", "SUMH", "SUML", "WMAX", "WSUMH", "WSUML", "PMAX", "PMIN", "WIN",
       "SKIP", "BEGIN"]
COPIED = PUB[3:15]                   # published from the working copy, same order
WORK = ["T0", "T1", "W0", "W1", "TPREV", "FLAGS", "CB", "CW"] + COPIED + ["ZN", "ZMAX", "ZSUMH", "ZSUML",
        "SA2", "SA1", "SA0", "SB2", "SB1", "SB0", "SX0", "SX1", "SR6", "SSR"]

# Words replaced in place (DSP word address -> expected stock word). The builder refuses any other value.
CORE0_SITES = {0x1FF: 0x0AF0A1, 0x200: 0x000202,          # hook B0START
               0x228: 0x0BF080, 0x229: 0x001A11,          # hook W0START
               0x1A17: 0x65F400, 0x1A18: 0x0306F0,        # hook W0END
               0x2BA: 0x059A67, 0x2BB: 0x00000C}          # hook B0END
CORE1_SITES = {0x117: 0x55012B,                           # hook M1START
               0x129: 0x050FC8}                           # hook M1END
CC = {"cc": 0, "ge": 1, "ne": 2, "pl": 3, "nn": 4, "ec": 5, "lc": 6, "gt": 7, "cs": 8, "lt": 9, "eq": 0xA, "mi": 0xB,
      "nr": 0xC, "es": 0xD, "ls": 0xE, "le": 0xF}


def x_addr(core, name):
    c = CORES[core]
    if name in WORK:
        return c["work"] + WORK.index(name)
    return c["pub"] + PUB.index(name[2:]) if name.startswith("P_") else None


def program(core):
    """[(label, text)] for one core. Symbols in {braces} are X addresses, <<L>> are code labels."""
    c = CORES[core]
    out = []

    def op(text, label=None):
        out.append((label, text))

    if core == 0:
        op("movep x:<<$ffff88,x:>{T0}", "B0START")
        op("jmp >$202")
        op("movep x:<<$ffff88,x:>{W0}", "W0START")
        op("jmp >$1a11")
        op("movep x:<<$ffff88,x:>{W1}", "W0END")
        op("move #>$306f0,r5")                           # displaced word
        op("rts")
        op("movep x:<<$ffff88,x:>{T1}", "B0END")
        op("dc $059a67")                                 # displaced word
    else:
        op("dc $55012b", "M1START")                      # displaced word
        op("movep x:<<$ffff88,x:>{T0}")
        op("rts")
        op("movep x:<<$ffff88,x:>{T1}", "M1END")
    # ---- statistics, shared by both cores; every register used is saved and restored ----
    for reg, slot in (("a2", "SA2"), ("a1", "SA1"), ("a0", "SA0"), ("b2", "SB2"), ("b1", "SB1"), ("b0", "SB0"),
                      ("x0", "SX0"), ("x1", "SX1"), ("r6", "SR6"), ("sr", "SSR")):
        op(f"move {reg},x:>{{{slot}}}")
    op("jset #0,x:<<$ffff8b,>RUN")                      # timer 1 running?
    op("movep #>$1,x:<<$ffff8b")                         # no: start it
    op("jmp >SKIP")                                      # and drop this block, its stamps are not valid
    # busy = T1 - T0 (ticks of CLK/2, modulo 2^24), plausible only below 2^22 ticks
    op("move x:>{T1},a", "RUN")
    op("move x:>{T0},x0")
    op("sub x0,a")
    op("move a1,x0")
    op("jset #23,x0,>SKIP")
    op("jset #22,x0,>SKIP")
    op("move x0,b")
    op("asl b")                                          # cycles = 2 * ticks
    op("move b1,x:>{CB}")
    # wait = W1 - W0 (core 0 spin on core 1; both stay 0 on core 1)
    op("move x:>{W1},a")
    op("move x:>{W0},x0")
    op("sub x0,a")
    op("move a1,x0")
    op("jset #23,x0,>SKIP")
    op("jset #22,x0,>SKIP")
    op("move x0,b")
    op("asl b")
    op("move b1,x:>{CW}")
    # period = T0 - previous T0, when the previous block was measured
    op("move x:>{FLAGS},x0")
    op("jclr #0,x0,>NOPER")
    op("move x:>{T0},a")
    op("move x:>{TPREV},x0")
    op("sub x0,a")
    op("move a1,x0")
    op("jset #23,x0,>NOPER")
    op("jset #22,x0,>NOPER")
    op("move x0,b")
    op("asl b")
    op("move x:>{PMAX},x0")
    op("cmp x0,b")
    op("jle >PMAXOK")
    op("move b1,x:>{PMAX}")
    op("move x:>{PMIN},a", "PMAXOK")
    op("tst a")
    op("jeq >PMINSET")                                   # PMIN 0 = no period yet
    op("move a1,x0")
    op("cmp x0,b")
    op("jge >NOPER")
    op("move b1,x:>{PMIN}", "PMINSET")
    op("move x:>{T0},x0", "NOPER")
    op("move x0,x:>{TPREV}")
    op("move #>$1,x0")
    op("move x0,x:>{FLAGS}")
    # lifetime max / min (min taken from the first block, CNT 0)
    op("move x:>{CB},b")
    op("move x:>{MAX},x0")
    op("cmp x0,b")
    op("jle >MAXOK")
    op("move b1,x:>{MAX}")
    op("move x:>{CNT},a", "MAXOK")
    op("tst a")
    op("jeq >MINSET")
    op("move x:>{MIN},x0")
    op("cmp x0,b")
    op("jge >MINOK")
    op("move b1,x:>{MIN}", "MINSET")
    op("move x:>{CW},b", "MINOK")
    op("move x:>{WMAX},x0")
    op("cmp x0,b")
    op("jle >WMAXOK")
    op("move b1,x:>{WMAX}")
    # count and 48-bit sums; all three stop when CNT reaches $FFFFFF (about 3 hours)
    op("move x:>{CNT},a", "WMAXOK")
    op("add #1,a")
    op("tst a")
    op("jeq >SAT")
    op("move a1,x:>{CNT}")
    op("move #0,x1")
    op("move x:>{CB},x0")
    op("move x:>{SUMH},a")
    op("move x:>{SUML},a0")
    op("add x,a")
    op("move a1,x:>{SUMH}")
    op("move a0,x:>{SUML}")
    op("move x:>{CW},x0")
    op("move x:>{WSUMH},a")
    op("move x:>{WSUML},a0")
    op("add x,a")
    op("move a1,x:>{WSUMH}")
    op("move a0,x:>{WSUML}")
    # window: max and 48-bit sum of busy cycles
    op("move x:>{CB},b", "SAT")
    op("move x:>{ZMAX},x0")
    op("cmp x0,b")
    op("jle >ZMAXOK")
    op("move b1,x:>{ZMAX}")
    op("move #0,x1", "ZMAXOK")
    op("move x:>{CB},x0")
    op("move x:>{ZSUMH},a")
    op("move x:>{ZSUML},a0")
    op("add x,a")
    op("move a1,x:>{ZSUMH}")
    op("move a0,x:>{ZSUML}")
    # every block, measured or skipped, counts toward the window, so windows keep closing even if all are skipped
    op("move x:>{ZN},a", "COUNT")
    op("add #1,a")
    op("move a1,x:>{ZN}")
    op(f"move #>${WINDOW:x},x0")
    op("cmp x0,a")
    op("jlt >DONE")
    # window complete: ring[WIN mod 256] = (max, sum / WINDOW)
    op("move x:>{WIN},a")
    op(f"and #>${RING_ENTRIES - 1:x},a")
    op("asl a")
    op(f"add #>${c['ring']:x},a")
    op("nop")
    op("move a1,r6")
    op("nop")
    op("nop")
    op("move x:>{ZMAX},x0")
    op("move x0,x:(r6)")
    op("add #1,a")
    op("nop")
    op("move a1,r6")
    op("move x:>{ZSUMH},a")
    op("move x:>{ZSUML},a0")
    op(f"asr #{WINDOW.bit_length() - 1},a,a")
    op("move a0,x:(r6)")
    op("move #0,x0")
    for f in ("ZN", "ZMAX", "ZSUMH", "ZSUML"):
        op(f"move x0,x:>{{{f}}}")
    op("move x:>{WIN},a")
    op("add #1,a")
    op("move a1,x:>{WIN}")
    # publish, seqlock order: BEGIN (highest word) first, END (lowest word) last; readers go upwards
    op("move a1,x:>{P_BEGIN}")
    op(f"move #>${MAGIC:x},x0")
    op("move x0,x:>{P_MAGIC}")
    op(f"move #>${VERSION << 8 | core:x},x0")
    op("move x0,x:>{P_ID}")
    for f in COPIED:
        op(f"move x:>{{{f}}},x0")
        op(f"move x0,x:>{{P_{f}}}")
    op("move x:>{WIN},x0")
    op("move x0,x:>{P_END}")
    op("jmp >DONE")
    op("move x:>{SKIP},a", "SKIP")
    op("add #1,a")
    op("move a1,x:>{SKIP}")
    op("move #0,x0")
    op("move x0,x:>{FLAGS}")
    op("jmp >COUNT")
    for reg, slot in (("a2", "SA2"), ("a1", "SA1"), ("a0", "SA0"), ("b2", "SB2"), ("b1", "SB1"), ("b0", "SB0"),
                      ("x0", "SX0"), ("x1", "SX1"), ("r6", "SR6")):
        op(f"move x:>{{{slot}}},{reg}", "DONE" if reg == "a2" else None)
    op("move x:>{SSR},sr")
    op("nop")
    op("rts" if core == 0 else "jmp <$111")
    return out


def _encode_custom(text, labels):
    """Jumps the pinned upstream assembler lacks, and raw words. None if `text` is not one of them."""
    m = re.fullmatch(r"dc \$([0-9a-f]{1,6})", text)
    if m:
        return [int(m.group(1), 16)]
    m = re.fullmatch(r"(jmp|jsr|j([a-z]{2})) ([<>])(\$[0-9a-f]+|[A-Z0-9]+)", text)
    if not m:
        return None
    target = int(m.group(4)[1:], 16) if m.group(4).startswith("$") else labels.get(m.group(4), 0)
    kind, cc, size = m.group(1), m.group(2), m.group(3)
    if size == "<":
        assert target < 0x1000 and kind in ("jmp", "jsr"), text
        return [(0x0C0000 if kind == "jmp" else 0x0D0000) | target]
    if kind == "jmp":
        return [0x0AF080, target]
    if kind == "jsr":
        return [0x0BF080, target]
    return [0x0AF0A0 | CC[cc], target]


def resolve(core, labels, line_asm):
    """Program lines with X symbols and labels substituted: [(addr, label, text, words)].
    line_asm(index, addr, text) -> words; label values never change an instruction's size (absolute forms are
    forced), so two passes settle every address."""
    lines, addr = [], CORES[core]["code"]
    for index, (label, text) in enumerate(program(core)):
        t = re.sub(r"\{(\w+)\}", lambda m: f"${x_addr(core, m.group(1)):x}", text)
        t = re.sub(r",>([A-Z][A-Z0-9]+)$", lambda m: f",${labels.get(m.group(1), 0):x}", t)
        words = _encode_custom(t, labels) or line_asm(index, addr, t)
        lines.append((addr, label, t, words))
        addr += len(words)
    return lines


def assemble(core, line_asm):
    labels = {}
    for _ in range(3):
        result = resolve(core, labels, line_asm)
        new = {label: a for a, label, _, _ in result if label}
        if new == labels:
            break
        labels = new
    else:
        sys.exit(f"core {core}: label addresses do not settle")
    end = result[-1][0] + len(result[-1][3])
    if end > CORES[core]["code_end"]:
        sys.exit(f"core {core}: code ends at {end:#x}, beyond {CORES[core]['code_end']:#x}")
    return result, labels


class DspAsm:
    """The pinned dsp56300 assembler through tools/dspmeter/dspasm.cpp (build tool; never part of the image)."""

    def __init__(self, path):
        self.path = path

    def __call__(self, index, addr, text):
        r = subprocess.run([self.path], input=f"{addr:x}\t{text}\n", capture_output=True, text=True)
        f = r.stdout.strip().split("\t")
        if r.returncode or f[0] == "ERR":
            sys.exit(f"dspasm refused '{text}': {r.stdout.strip()}")
        return [int(w, 16) for w in f[1].split()]

    def disassemble(self, addr, words):
        r = subprocess.run([self.path, "dis"], input=f"{addr:x}\t{' '.join(f'{w:06x}' for w in words)}\n",
                           capture_output=True, text=True, check=True)
        f = r.stdout.strip().split("\t")
        return int(f[1]), f[2]


def write_listing(dspasm):
    with open(LISTING, "w") as f:
        f.write("; DSP cycle meter, generated by tools/dspmeter.py listing from program(); do not edit.\n"
                "; core addr words label text. Our code only, no Novation bytes (the two dc words are copies of the\n"
                "; stock instructions they displace, checked against the stock image by the builder).\n")
        for core in (0, 1):
            result, _ = assemble(core, dspasm)
            for addr, label, text, words in result:
                n, dis = dspasm.disassemble(addr, words + [0])
                if n != len(words):
                    sys.exit(f"core {core} {addr:#x} '{text}': disassembler reads {n} words, assembled {len(words)}")
                f.write(f"{core} {addr:04x} {' '.join(f'{w:06x}' for w in words):13s} {label or '-':8s} {text}"
                        f"    ; {dis}\n")


def read_listing():
    out = {0: [], 1: []}
    for line in open(LISTING):
        if line.startswith(";") or not line.strip():
            continue
        core, addr, rest = line.split(" ", 2)
        words = rest[:13].split()
        label, text = rest[14:].split(" ", 1)
        out[int(core)].append((int(addr, 16), None if label == "-" else label,
                               text.split("    ; ")[0].strip(), [int(w, 16) for w in words]))
    return out


def check_listing(dspasm=None):
    """The committed listing must be exactly program(); with dspasm also re-assembled word for word."""
    listed = read_listing()
    for core in (0, 1):
        if len(listed[core]) != len(program(core)):
            sys.exit(f"core {core}: listing has {len(listed[core])} lines, program() {len(program(core))}")
        asm = dspasm or (lambda i, a, t, rows=listed[core]: rows[i][3])
        result, labels = assemble(core, asm)
        if [tuple(r) for r in result] != listed[core]:
            bad = next(i for i, (r, s) in enumerate(zip(result, listed[core])) if tuple(r) != s)
            sys.exit(f"core {core}: listing does not match program() at line {bad}: {result[bad]} vs "
                     f"{listed[core][bad]}")
    return True


def code_words(core):
    """(start, words, labels) of one core's meter code from the committed listing."""
    check_listing()
    lst = read_listing()[core]
    words = [w for _, _, _, ws in lst for w in ws]
    return lst[0][0], words, {l: a for a, l, _, _ in lst if l}


# ---------------------------------------------------------------- firmware image layer
def u24(b, i):
    return int.from_bytes(b[3 * i:3 * i + 3], "big")


def w24(v):
    return (v & 0xFFFFFF).to_bytes(3, "big")


def stock_streams(image):
    off0, off1 = STREAM0 - BASE, STREAM1 - BASE
    if hashlib.sha256(image).hexdigest() != STOCK_SHA256:
        sys.exit("not the pinned stock 5706 image")
    p0, p1 = struct.unpack_from("<I", image, PTR_CORE0 - BASE)[0], struct.unpack_from("<I", image, PTR_CORE1 - BASE)[0]
    if (p0, p1) != (STREAM0, STREAM1):
        sys.exit(f"pointer table holds {p0:#x}/{p1:#x}")
    h0, h1 = image[off0:off0 + 6], image[off1:off1 + 6]
    if h0 != w24(STOCK_CORE0_WORDS) + w24(0) or h1 != w24(STOCK_CORE1_WORDS) + w24(0):
        sys.exit("unexpected bootstrap headers")
    core0 = image[off0 + 6:off0 + 6 + 3 * STOCK_CORE0_WORDS]
    core1 = image[off1 + 6:off1 + 6 + 3 * STOCK_CORE1_WORDS]
    return core0, core1


def parse_scatter(payload):
    """[(space, addr, word_offset_of_data, count)], terminator offset."""
    recs, i, n = [], 0, len(payload) // 3
    while i < n:
        space = u24(payload, i)
        if space > 2:
            if space != SCATTER_TERMINATOR:
                sys.exit(f"scatter: selector {space:#x} at {i}")
            return recs, i
        count, addr = u24(payload, i + 1), u24(payload, i + 2)
        recs.append(("PXY"[space], addr, i + 3, count))
        i += 3 + count
    sys.exit("scatter: no terminator")


def layer(image):
    """Patch list (addr, expect, new, note) for the meter on top of the stock image."""
    core0, core1 = stock_streams(image)
    start0, code0, _ = code_words(0)
    start1, code1, _ = code_words(1)
    assert start0 == STOCK_CORE0_WORDS and start1 == CORES[1]["code"]
    # core 0: the stock program, hooks rewritten, meter appended at P:$1FED
    prog = bytearray(core0)
    labels0 = {l: a for a, l, _, _ in read_listing()[0] if l}
    labels1 = {l: a for a, l, _, _ in read_listing()[1] if l}
    new0 = {0x1FF: 0x0AF0A1, 0x200: labels0["B0START"], 0x228: 0x0BF080, 0x229: labels0["W0START"],
            0x1A17: 0x0BF080, 0x1A18: labels0["W0END"], 0x2BA: 0x0AF080, 0x2BB: labels0["B0END"]}
    for a, stock in CORE0_SITES.items():
        if u24(prog, a) != stock:
            sys.exit(f"core 0 P:{a:#x} holds {u24(prog, a):06x}, expected {stock:06x}")
        prog[3 * a:3 * a + 3] = w24(new0[a])
    prog += b"".join(w24(w) for w in code0)
    stream0 = w24(len(prog) // 3) + w24(0) + bytes(prog)
    # core 1: hooks in the P:$100 record, two records before the terminator
    recs, term = parse_scatter(core1)
    pay = bytearray(core1)
    new1 = {0x117: 0x0D0000 | labels1["M1START"], 0x129: 0x0C0000 | labels1["M1END"]}
    for a, stock in CORE1_SITES.items():
        hit = [(base, off) for sp, base, off, cnt in recs if sp == "P" and base <= a < base + cnt]
        if len(hit) != 1:
            sys.exit(f"core 1 P:{a:#x} not in exactly one record")
        i = hit[0][1] + a - hit[0][0]
        if u24(pay, i) != stock:
            sys.exit(f"core 1 P:{a:#x} holds {u24(pay, i):06x}, expected {stock:06x}")
        pay[3 * i:3 * i + 3] = w24(new1[a])
    for sp, base, off, cnt in recs:      # the new ranges must not be loaded by any stock record
        for lo, hi, s in ((start1, start1 + len(code1), "P"), (CORES[1]["ring"], CORES[1]["x_end"], "X")):
            if sp == s and base < hi and lo < base + cnt:
                sys.exit(f"stock record {sp}:{base:#x}+{cnt} overlaps the meter")
    zeros = CORES[1]["x_end"] - CORES[1]["ring"]
    extra = (w24(0) + w24(len(code1)) + w24(start1) + b"".join(w24(w) for w in code1)
             + w24(1) + w24(zeros) + w24(CORES[1]["ring"]) + w24(0) * zeros)
    pay = pay[:3 * term] + extra + pay[3 * term:]
    stream1 = w24(len(pay) // 3) + w24(0) + bytes(pay)
    # placement: core 1 in place (it may now run into the old core 0 stream), core 0 at the end of the flash
    old_end0 = STREAM0 + 6 + 3 * STOCK_CORE0_WORDS
    if STREAM1 + len(stream1) > old_end0:
        sys.exit("core 1 stream outgrew the old core 0 stream")
    at0 = (FLASH_END - len(stream0)) & ~0x1F
    patches = []
    old1 = image[STREAM1 - BASE:STREAM1 - BASE + len(stream1)]
    diffs = [i for i in range(len(stream1)) if stream1[i] != old1[i]]
    for a, b in _runs(diffs):
        patches.append((STREAM1 + a, old1[a:b], stream1[a:b], "core 1 scatter stream (header count, hooks, records)"))
    patches.append((PTR_CORE0, struct.pack("<I", STREAM0), struct.pack("<I", at0), "core 0 stream pointer"))
    patches.append((at0, b"\xff" * len(stream0), stream0, f"core 0 stream, {len(prog) // 3} words (stock + meter)"))
    return patches, {"core0_stream": at0, "core0_words": len(prog) // 3, "core1_words": len(pay) // 3,
                     "core1_stream_end": STREAM1 + len(stream1), "core0_code": [start0, start0 + len(code0)],
                     "core1_code": [start1, start1 + len(code1)], "labels": {"0": labels0, "1": labels1}}


def _runs(idx, gap=16):
    """Group sorted byte offsets into [start, end) runs, merging gaps shorter than `gap`."""
    runs = []
    for i in idx:
        if runs and i - runs[-1][1] < gap:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    return [tuple(r) for r in runs]


def build(base_spec, label, out, version=None):
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import patch as patchtool
    import syx
    spec = json.load(open(base_spec))
    stock_syx = os.path.join(root, spec["base_syx"])
    ref = syx.parse(open(stock_syx, "rb").read())
    image = bytes(ref["image"])
    patches, info = layer(image)
    meter = [{"addr": f"0x{a:08X}", "expect": e.hex(), "new": n.hex(), "note": note} for a, e, n, note in patches]
    base = [p for p in spec["patches"] if p["addr"] != "0x080101CA"]      # drop the base's label patch
    if len(base) != len(spec["patches"]) - 1:
        sys.exit("base spec has no build-number patch at 0x080101CA")
    if not 6000 <= label <= 7999:
        sys.exit("meter test labels are 69xx (on 6009) or 60xx/70xx when stacked; refuse anything else")
    label_patch = next(p for p in spec["patches"] if p["addr"] == "0x080101CA")
    old_label = struct.unpack("<H", bytes.fromhex(label_patch["new"]))[0]
    if label == old_label:
        sys.exit(f"label {label} is the base's own; the bootloader would skip the install")
    test = 6900 <= label <= 6999
    if version is None:
        version = spec["version_nibbles"]
    elif not test and version <= spec["version_nibbles"]:
        sys.exit(f"version {version} is not above the base's {spec['version_nibbles']}")
    lab = {"addr": "0x080101CA", "expect": "4a16", "new": struct.pack("<H", label).hex(),
           "note": f"build-info block: build number 5706 -> {label} (install marker"
                   + ("; TEST ONLY, not for flashing)" if test else ")")}
    desc = (f"TEST ONLY {label}: {spec['description']} Plus the DSP cycle meter (docs/user/developers.md)." if test else
            f"{spec['description']} Plus the DSP cycle meter for both cores, read idle-only with cmd 08 "
            "(docs/user/developers.md).")
    out_spec = dict(spec, description=desc, patches=base + meter + [lab], version_nibbles=version,
                    dsp_meter=info)
    path = out + ".json"
    with open(path, "w") as f:
        json.dump(out_spec, f, indent=1)
    cwd = os.getcwd()
    os.chdir(root)
    try:
        patchtool.apply(os.path.abspath(path), os.path.abspath(out))
    finally:
        os.chdir(cwd)
    return info


def extract(image, outdir):
    """The DSP streams a built image delivers, as emulator inputs (core0.program.bin, core1 segment files, json)."""
    os.makedirs(outdir)
    p0 = struct.unpack_from("<I", image, PTR_CORE0 - BASE)[0]
    p1 = struct.unpack_from("<I", image, PTR_CORE1 - BASE)[0]
    n0 = u24(image[p0 - BASE:], 0) & 0xFFFF
    prog = image[p0 - BASE + 6:p0 - BASE + 6 + 3 * n0]
    open(os.path.join(outdir, "core0.program.bin"), "wb").write(prog)
    n1 = u24(image[p1 - BASE:], 0) & 0xFFFF
    pay = image[p1 - BASE + 6:p1 - BASE + 6 + 3 * n1]
    recs, term = parse_scatter(pay)
    segs = []
    for k, (sp, base, off, cnt) in enumerate(recs):
        name = f"core1.seg{k:02d}.{sp}.bin"
        open(os.path.join(outdir, name), "wb").write(pay[3 * off:3 * (off + cnt)])
        segs.append({"space": sp, "addr": base, "words": cnt, "file": name})
    meta = {"core0_ptr": p0, "core0_words": n0, "core1_ptr": p1, "core1_words": n1, "core1_terminator": term,
            "core1_trailing": n1 - term - 1, "core1_segments": segs}
    json.dump(meta, open(os.path.join(outdir, "streams.json"), "w"), indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("listing")
    s.add_argument("--dspasm", required=True)
    s = sub.add_parser("check-listing")
    s.add_argument("--dspasm")
    s = sub.add_parser("build")
    s.add_argument("--base", default="patches/devkit-load.json")
    s.add_argument("--label", type=int, default=6911)
    s.add_argument("--out", default="build/devkit-meter")
    s.add_argument("--version", help="six version digits, e.g. 000749 (default: the base spec's)")
    s = sub.add_parser("extract")
    s.add_argument("image")
    s.add_argument("outdir")
    a = ap.parse_args()
    if a.cmd == "listing":
        write_listing(DspAsm(a.dspasm))
        check_listing(DspAsm(a.dspasm))
        print(f"wrote {LISTING}")
    elif a.cmd == "check-listing":
        check_listing(DspAsm(a.dspasm) if a.dspasm else None)
        print("listing matches program()" + (" and the assembler" if a.dspasm else ""))
    elif a.cmd == "build":
        version = None
        if a.version is not None:
            if not re.fullmatch(r"[0-9]{6}", a.version):
                sys.exit("--version takes six decimal digits")
            version = [int(c) for c in a.version]
        info = build(a.base, a.label, a.out, version)
        print(json.dumps({k: v for k, v in info.items() if k != "labels"}, default=hex))
    else:
        print(json.dumps(extract(open(a.image, "rb").read(), a.outdir), indent=1))


if __name__ == "__main__":
    main()
