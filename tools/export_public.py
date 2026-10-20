"""Build the curated public export tree from this private repo (release plan D7, docs/release-plan.md §2.5).

    uv run python -I tools/export_public.py OUTDIR [--ref HEAD] [--deny-file export-deny.json]
                                           [--firmware-dir DIR] [--bootloader FILE] [--allow-email ADDR] [--dry-run]

Copies only allow-listed paths (ALLOW, every file by name, minus EXCLUDE) as committed at --ref (never the working tree),
each to its own path or the one PUBLISH_AS names (docs/user/README.md becomes the export's README.md), and only after
every file passes these checks; if any file fails, nothing is written and the findings are listed (exit 1):

* novation-bytes: a run of 16 or more bytes equal to bytes of a Novation image: every firmware/*.syx decoded with
  tools/syx.py, and build/bootloader-4799.bin if present. Checked on the raw file bytes and on long hex strings decoded
  (patch and release JSON carry bytes as hex). Runs made of at most two distinct byte values (zero or FF fill) are
  ignored, and so are, in the raw file bytes only: runs of printable ASCII (0x20..0x7E, tab, CR, LF) of at most
  TEXT_RUN bytes (quoted log and error strings), and runs inside a JPEG's Huffman tables (DHT segments, standard
  tables). A deny-file bytes_exceptions entry (path -> hex strings) passes a reviewed run found in that path, raw or
  as hex. The index holds the images' 8-byte windows at every 4th offset, so any longer run that holds an indexed
  window that is not one repeated byte is found; every hit is confirmed byte for byte.
* serial, serial-pattern, marker: the unit serial, serial-shaped strings and the firmware's serial placeholder, from
  the deny file. A marker may be excepted for a named path in the deny file (reviewed exceptions only).
* email: any e-mail address not in the deny file's allowed_emails or --allow-email.
* hostname: internal host and domain names from the deny file; private IPv4 addresses (10/8, 172.16/12, 192.168/16)
  except the strings the deny file's ip_exceptions lists for a path (reviewed: e.g. a manual section number).
* string: other private strings from the deny file (local paths).
* path: paths in DENY_PATHS (private even if the allow-list ever grows to include them).
* listing: a run of LISTING_LINES or more disassembly-like lines (a flash address 0x080xxxxx, 080xxxx:, 08 0x xx xx,
  or a DSP P:xxxx, followed by hex bytes/words or an ARM/DSP56300 mnemonic), fenced code blocks included. Up to
  LISTING_GAP other lines (labels, comments, blank lines) may sit between listing lines of one run. A long listing
  reproduces Novation's code in a form the byte scan cannot see; short excerpts pass.
* hexdump: HEXDUMP_BYTES or more bytes written as hex that the byte scan does not match (they may come from an image
  that is not indexed): consecutive dump lines (optional address, then 4 or more whitespace-separated hex tokens of
  2, 4, 6 or 8 digits), or one unbroken hex string. Dumps of RUN bytes or more are also byte-scanned (a dump spread
  over short lines escapes the single-line hex scan above); a match is reported as novation-bytes.
  The values of JSON "hex" keys are exempt from this rule only: they are crpatch payloads built from our own code
  (release/, web/, test fixtures) and are still byte-scanned.

The deny file (default export-deny.json at the repo root) is private: it holds the serial and host names, so it is
outside the allow-list and in DENY_PATHS. Firmware images are looked up in this checkout, then in the main checkout of
the same repository (worktrees share no ignored files).

OUTDIR must not exist or be empty, and must not lie inside this repository or any of its worktrees.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import syx  # noqa: E402

# Published paths, every one by name (release plan §2.1; D7): a new file anywhere stays private until it is listed here
# (public-maps review, 2026-10-19: tools/, patches/src/, release/ and web/ too, not only docs/
# and tests/). patches/*.json stay out: their "expect" fields carry stock bytes. modules/paint.c is the built-in module
# compiled into both 0.1 images, published so that their ARM bytes can be rebuilt and audited; patches/dspmeter.lst is
# the DSP meter's own program listing, which tools/dspmeter.py builds arrhythmia-devkit's DSP layer from.
# modules/stars.c is the one published example module (master, 2026-10-19); the other examples stay private.
PUBLIC_DOCS = ("docs/README.md", "docs/user/README.md", "docs/user/quickstart.md", "docs/user/install.md",
               "docs/user/synth.md",
               "docs/user/scales.md", "docs/user/developers.md", "docs/led-map.md", "docs/led-map.json",
               "docs/img/led-map-verify.jpg", "docs/img/arrhythmia-logo.png", "docs/input-map.json", "docs/user/img/synth-page.png",
               "docs/user/img/scales-note-view.png")
# Developer reference (master, 2026-10-19): curated from the private notes under D7, one file by name.
PUBLIC_DEV_DOCS = ("docs/dev/README.md", "docs/dev/hardware.md", "docs/dev/flashing.md",
                   "docs/dev/backup-and-recovery.md", "docs/dev/devkit-sysex.md", "docs/dev/modules-reference.md",
                   "docs/dev/dsp-meter-and-budget.md", "docs/dev/maps.md", "docs/dev/audio-capture.md",
                   "docs/dev/rhythm-vs-tracks.md", "docs/dev/design/project-extension-block.md",
                   "docs/dev/design/plock-fix.md", "docs/dev/design/scales.md",
                   "docs/dev/design/o1-fault-handling.md")
PUBLIC_TESTS = ("tests/fixtures/two-builds.json", "tests/test_web_installer.py", "tests/test_web_installer.mjs",
                "tests/test_patch_transport.py", "tests/test_export_public.py", "tests/test_build_module.py",
                "tests/test_capture.py", "tests/test_flash_mock.py", "tests/test_norbackup.py",
                "tests/test_module_readback.py")
PUBLIC_TOOLS = ("tools/build_lightshow.py", "tools/build_module.py", "tools/capture.py", "tools/capture_align.py",
                "tools/crpatch.py", "tools/dspload.py", "tools/dspmeter.py", "tools/dspmeter/CMakeLists.txt",
                "tools/dspmeter/dspasm.cpp", "tools/dspmeter/NOTICE.md", "tools/export_public.py", "tools/flash.py", "tools/inputlog.py",
                "tools/ledmap.py", "tools/ledscan.py", "tools/levelmeter.py", "tools/levelview.py", "tools/module.py",
                "tools/norbackup.py", "tools/patch.py", "tools/syx.py", "tools/voicestack.py", "tools/wavio.py")
PUBLIC_SRC = ("patches/src/a1_cmd.c", "patches/src/devkit_a2.c", "patches/src/devkit_abi3.c", "patches/src/devkit_o1.c",
              "patches/src/devkit_plock.c", "patches/src/devkit_tm.c", "patches/src/notetap.c", "patches/src/o1wait.c",
              "patches/src/arrhythmia.c", "patches/src/ad_life.h", "patches/src/plockfix.c", "patches/src/scales.c",
              "patches/src/synth_settings.h", "patches/src/synthpage.c", "patches/src/trackmodes.c",
              "patches/dspmeter.lst", "modules/paint.c", "modules/stars.c")
# DSP voice source of arrhythmia-firmware 0.1.1 (build 7261): our assembly, generators, adapter, builder, the line
# assembler frontend and its licence notices, and the test; exactly the files that tools/dspvoice/source-manifest.json
# names, plus the manifest itself.
PUBLIC_DSPVOICE = ("tools/dspvoice/source-manifest.json", "tools/dspvoice/LICENSE", "tools/dspvoice/README.md",
                   "tools/dspvoice/adapter.py", "tools/dspvoice/assemble.py", "tools/dspvoice/build.py",
                   "tools/dspvoice/decoder.asm", "tools/dspvoice/guard.asm", "tools/dspvoice/voice.asm",
                   "tools/dspvoice/parameters.py", "tools/dspvoice/tables.py", "tools/dspvoice/symbols.json",
                   "tools/dspvoice/toolchain/CMakeLists.txt", "tools/dspvoice/toolchain/NOTICES.md",
                   "tools/dspvoice/toolchain/README.md", "tools/dspvoice/toolchain/UPSTREAM.md",
                   "tools/dspvoice/toolchain/dspasm.cpp", "tests/test_dspvoice_source.py")
PUBLIC_RELEASE = ("release/devkit-tm-6013.json", "release/v0.1.0.json", "release/v0.1.1.json", "web/index.html")
ALLOW = (("LICENSE", "RELEASE-NOTES-0.1.0.md", "RELEASE-NOTES-0.1.1.md") + PUBLIC_TOOLS + PUBLIC_SRC + PUBLIC_RELEASE
         + PUBLIC_DOCS + PUBLIC_DEV_DOCS + PUBLIC_TESTS + PUBLIC_DSPVOICE)
# Allow-listed files published under another name (source path -> published path). The public README is
# docs/user/README.md, published at the export root; this repo's own root README.md is private (DENY_PATHS).
PUBLISH_AS = {"docs/user/README.md": "README.md"}
OPT_IN = ("docs/", "tests/", "tools/", "patches/", "release/", "web/", "modules/")   # opt-in by named file only
# Left out without a finding (they were once inside allow-listed directories; listing one by name refuses). Every EXCLUDE entry should also be in DENY_PATHS,
# so that dropping it from EXCLUDE refuses the export instead of publishing it. Public-maps review (2026-10-19):
# tooling that extracts, reads or runs stock code (DSP extraction and emulator harness, stock DSP readers, the
# Unicorn harness, the flash reader), and patch sources superseded by patches/src/arrhythmia.c.
EXCLUDE = ("docs/nor-backup.md", "docs/codex-brief.md", "docs/release-plan.md", "docs/user-wishlist.md",
           "docs/export-audit.md",
           "tools/dsp/", "tools/dspmeter/meter_probe.cpp", "tools/dspmeter/scenes.py", "tools/dsp_extract.py",
           "tools/dsp_host_init.py", "tools/dsp_sample_params.py", "tools/dspcensus.py", "tools/dsppeek.py",
           "tools/dspxy.py", "tools/fxsweep.py", "tools/emu.py", "tools/dumpflash.py",
           "patches/src/devkit.c", "patches/src/devkit_dsp.c", "patches/src/devkit_input.c",
           "patches/src/devkit_mode.c", "patches/src/devkit_play.c", "patches/src/devkit_load.c",
           "patches/src/devkit_clock.c", "patches/src/lightshow.c", "patches/src/heapdiag.c",
           "patches/src/flashdump.c")
# Never exported: an allow-listed path that is not excluded but matches DENY_PATHS refuses the whole export.
# docs/nor-backup.md holds the unit serial; the other four EXCLUDE docs are internal process documents (master,
# 2026-10-19). PRIVATE_DOCS / PRIVATE_TESTS: notes and tests built on maps of the stock firmware (decompilation,
# emulation of stock code); listed so that a later opt-in refuses instead of publishing (public-maps review).
PRIVATE_DOCS = ("docs/a1-notetap.md", "docs/a1-trace-t60-2s.txt", "docs/a1-trace.txt", "docs/arm-map.md",
                "docs/bootloader.md", "docs/capture.md", "docs/device-probe.md", "docs/diagrams/",
                "docs/dsp-budget.md", "docs/dsp-emulator.md", "docs/dsp-host-writes.md", "docs/dsp-hw-comparison.md",
                "docs/dsp-map.md", "docs/dsp-meter.md", "docs/dsp-peek.md", "docs/dsp-timing-audit.md",
                "docs/dsp-timing.md", "docs/fx-map.md", "docs/input-map.md", "docs/knob-map.md", "docs/memory.md",
                "docs/module-abi3.md", "docs/module-clock.md", "docs/note-path.md", "docs/o1-bounded-waits.md",
                "docs/pattern-map.md", "docs/phase1.md", "docs/phase4-prep.md", "docs/plock-fix.md",
                "docs/recovery.md", "docs/rhythm-vs-tracks.md", "docs/scales.md", "docs/sd-modules.md",
                "docs/synth-page.md", "docs/track-modes-t1.md", "docs/track-modes.md",
                "docs/tracks-generative-feasibility.md", "docs/tracks-on-rhythm.md", "docs/tracks-resources.md",
                "docs/v01-integration.md", "docs/voice-stream.md",
                "docs/dsp-source-bundle-handoff.md")
PRIVATE_TESTS = ("tests/test_a1_notetap.py", "tests/test_a2_page.py", "tests/test_a2_regressions.py",
                 "tests/test_cmd08_meter.py", "tests/test_cmd7d.py", "tests/test_devkit.py", "tests/test_devkit_clock.py",
                 "tests/test_devkit_dsp.py", "tests/test_devkit_input.py", "tests/test_devkit_load.py",
                 "tests/test_devkit_mode.py", "tests/test_devkit_play.py", "tests/test_dsp_extract.py",
                 "tests/test_dsp_host_init.py", "tests/test_dsp_sample_params.py", "tests/test_dspmeter.py",
                 "tests/test_dsppeek.py", "tests/test_dumpflash_mock.py", "tests/test_emu_io.py",
                 "tests/test_flashdump.py", "tests/test_heapdiag.py", "tests/test_hello_stream.py",
                 "tests/test_knob_map.py", "tests/test_lightshow.py", "tests/test_module_abi3.py",
                 "tests/test_o1_bounded_waits.py", "tests/test_pattern_map.py", "tests/test_plock_fix.py",
                 "tests/test_scales.py", "tests/test_track_gate.py", "tests/test_track_modes.py",
                 "tests/test_trackmodes_t1.py", "tests/test_update_crc.py", "tests/test_version_reply.py",
                 "tests/test_voicestack.py")
DENY_PATHS = EXCLUDE + PRIVATE_DOCS + PRIVATE_TESTS + ("README.md", "ghidra_scripts/", "export-deny.json", "firmware/",
                                                       "build/")
SKIP_PARTS = ("__pycache__",)

RUN = 16                    # shortest Novation byte run that is refused
WIN = 8                     # index window
STRIDE = 4                  # index step; RUN >= WIN + STRIDE - 1 puts an indexed window inside every run
MAXREFS = 256               # occurrences kept per window (identical code across firmware versions)
BOOTLOADER = "build/bootloader-4799.bin"

EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
IPV4 = re.compile(rb"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
HEXRUN = re.compile(rb"(?:[0-9A-Fa-f]{2}[ ]?){%d,}" % RUN)

LISTING_LINES = 24          # refuse a run of this many disassembly-like lines (or more)
LISTING_GAP = 2             # other lines tolerated between two listing lines of one run
HEXDUMP_BYTES = 64          # refuse this many unmatched hex-written bytes (or more)
TEXT_RUN = 64               # a matched run of printable ASCII up to this long is a quoted string and passes

_ARM = (rb"ldr|ldrb|ldrh|ldrsb|ldrsh|ldrd|ldm|ldmia|ldmdb|ldrex|str|strb|strh|strd|stm|stmia|stmdb|strex|mov|movs|"
        rb"movw|movt|mvn|mvns|add|adds|addw|adc|adcs|sub|subs|subw|sbc|sbcs|rsb|rsbs|mul|muls|mla|mls|umull|smull|"
        rb"umlal|smlal|sdiv|udiv|and|ands|orr|orrs|orn|eor|eors|bic|bics|lsl|lsls|lsr|lsrs|asr|asrs|ror|rors|cmp|cmn|"
        rb"tst|teq|b|bl|blx|bx|cbz|cbnz|it|itt|ite|ittt|itte|itet|itee|ittee|push|pop|nop|svc|bkpt|uxtb|uxth|sxtb|"
        rb"sxth|ubfx|sbfx|bfi|bfc|clz|rev|rev16|tbb|tbh|adr|dmb|dsb|isb|cpsid|cpsie|mrs|msr|wfi|wfe|vldr|vstr|vmov|"
        rb"vadd|vsub|vmul|vdiv|vmla|vcvt|vcmp|vcmpe|vmrs|vpush|vpop|vneg|vabs|vsqrt|vldm|vstm")
_DSP = (rb"move|movec|movem|movep|jmp|jsr|bra|bsr|rts|rti|do|dor|enddo|rep|mpy|mpyr|mac|macr|clr|tfr|asl|not|neg|"
        rb"abs|cmpm|bset|bclr|bchg|btst|jset|jclr|jsset|jsclr|brset|brclr|andi|ori|lua|lra|norm|rnd|max|maxm|"
        rb"extract|extractu|insert|merge|dec|inc|debug|wait|stop|illegal|trap|vsl|div|or")
_CC = rb"(?:cc|cs|ec|eq|es|ge|gt|lc|le|ls|lt|mi|ne|nr|pl|nn|hs|lo|hi|vs|vc|al)"
LISTING = re.compile(
    rb"^[ \t>*|`#/;-]*"
    rb"(?:(?:0x)?0?80[0-9A-Fa-f]{5}|08 0[0-9A-Fa-f] [0-9A-Fa-f]{2} [0-9A-Fa-f]{2}|[Pp]:\$?(?:0x)?[0-9A-Fa-f]{1,6})"
    rb":?[ \t]+"
    rb"(?:(?:[0-9A-Fa-f]{2}){1,4}(?:[ \t]|$)|(?:" + _ARM + rb"|" + _DSP + rb"|[bj]s?" + _CC + rb"|b" + _CC + rb")"
    rb"(?:\.[wn])?(?:[ \t]|$))", re.I)
DUMPADDR = re.compile(rb"^[ \t>*|`#/;-]*(?:(?:0x)?[0-9A-Fa-f]{4,8}:?|[PXYLpxyl]:\$?(?:0x)?[0-9A-Fa-f]{1,6}:?)[ \t]+")
HEXTOK = re.compile(rb"^(?:[0-9A-Fa-f]{2}){1,4}$")
HEXSTR = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{%d,}(?![0-9A-Fa-f])" % (2 * HEXDUMP_BYTES))
JSONHEX = re.compile(rb'"hex"[ \t]*:[ \t]*"$')


class Refused(Exception):
    pass


def git(repo, *args, data=False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return r.stdout if data else r.stdout.decode()


def opt_in_violations(allow):
    """Entries of allow that publish a whole directory (every path is opt-in by named file)."""
    return [a for a in allow if a.endswith("/")]


assert not opt_in_violations(ALLOW), opt_in_violations(ALLOW)


def allowed(path, allow=ALLOW, exclude=EXCLUDE):
    if any(part in SKIP_PARTS for part in path.split("/")):
        return False
    if any(path == x or (x.endswith("/") and path.startswith(x)) for x in exclude):
        return False
    return any(path == a or (a.endswith("/") and path.startswith(a)) for a in allow)


def published(path, publish_as=None):
    """The path a source file is written to in the export."""
    return (PUBLISH_AS if publish_as is None else publish_as).get(path, path)


def denied(path):
    return any(path == d or (d.endswith("/") and path.startswith(d)) for d in DENY_PATHS)


# ---- Novation byte index -------------------------------------------------------------------------------------------

class ImageIndex:
    def __init__(self, images):
        self.images = [bytes(i) for i in images]
        self.index = {}
        for n, img in enumerate(self.images):
            for off in range(0, len(img) - WIN + 1, STRIDE):
                w = img[off:off + WIN]
                if w.count(w[0]) == WIN:
                    continue
                key = int.from_bytes(w, "little")
                ref = n << 32 | off
                old = self.index.get(key)
                if old is None:
                    self.index[key] = ref
                elif isinstance(old, list):
                    if len(old) < MAXREFS:
                        old.append(ref)
                else:
                    self.index[key] = [old, ref]

    def find(self, data, skip=None):
        """First (offset, length, image number) of a run >= RUN shared with an image and not trivial, or None.
        Runs for which skip(start, length) is true are passed over."""
        n = len(data)
        i = 0
        while i + WIN <= n:
            refs = self.index.get(int.from_bytes(data[i:i + WIN], "little"))
            if refs is not None:
                for ref in refs if isinstance(refs, list) else (refs,):
                    img = self.images[ref >> 32]
                    off = ref & 0xFFFFFFFF
                    if img[off:off + WIN] != data[i:i + WIN]:
                        continue
                    b = 0
                    while i - b > 0 and off - b > 0 and data[i - b - 1] == img[off - b - 1]:
                        b += 1
                    f = WIN
                    while i + f < n and off + f < len(img) and data[i + f] == img[off + f]:
                        f += 1
                    start, length = i - b, b + f
                    if length >= RUN and len(set(data[start:start + length])) > 2:
                        if skip is None or not skip(start, length):
                            return start, length, ref >> 32
            i += 1
        return None


def load_images(firmware_dirs, bootloader_paths, log):
    images, names = [], []
    for d in firmware_dirs:
        files = sorted(Path(d).glob("*.syx")) if Path(d).is_dir() else []
        if files:
            for f in files:
                try:
                    images.append(syx.parse(f.read_bytes())["image"])
                except ValueError as e:
                    raise Refused(f"cannot decode {f}: {e}; the byte scan would be incomplete")
                names.append(str(f))
            break
    if not images:
        raise Refused(f"no firmware/*.syx found in {', '.join(map(str, firmware_dirs))}; pass --firmware-dir")
    for b in bootloader_paths:
        if Path(b).is_file():
            images.append(Path(b).read_bytes())
            names.append(str(b))
            break
    else:
        log(f"note: no {BOOTLOADER} found; bootloader bytes not scanned")
    for nm in names:
        log(f"scanning against {nm}")
    return images, names


# ---- text rules ----------------------------------------------------------------------------------------------------

def load_deny(path):
    try:
        cfg = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise Refused(f"deny file {path} not found")
    for key in ("serials", "serial_patterns", "markers", "hostnames", "strings", "allowed_emails"):
        cfg.setdefault(key, [])
    cfg.setdefault("marker_exceptions", {})
    cfg.setdefault("ip_exceptions", {})
    cfg.setdefault("bytes_exceptions", {})
    if not cfg["serials"] or not cfg["hostnames"]:
        raise Refused(f"deny file {path} lists no serials or no hostnames; refusing to run without them")
    return cfg


def private_ip(m):
    a, b = int(m.group(1)), int(m.group(2))
    if any(int(x) > 255 for x in m.groups()):
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def text_findings(path, data, cfg, allow_emails):
    out = []
    low = data.lower()
    for s in cfg["serials"]:
        if s.encode().lower() in low:
            out.append(("serial", s))
    for p in cfg["serial_patterns"]:
        for m in re.finditer(p.encode(), data):
            out.append(("serial-pattern", m.group().decode("latin-1")))
    for s in cfg["markers"]:
        if s.encode() in data and s not in cfg["marker_exceptions"].get(path, []):
            out.append(("marker", s))
    for h in cfg["hostnames"]:
        rx = re.compile(rb"(?<![A-Za-z0-9-])" + re.escape(h.encode()) + rb"(?![A-Za-z0-9-])", re.I)
        m = rx.search(data)
        if m:
            out.append(("hostname", m.group().decode("latin-1")))
    for m in IPV4.finditer(data):
        if private_ip(m) and m.group().decode() not in cfg["ip_exceptions"].get(path, []):
            out.append(("hostname", m.group().decode()))
    for s in cfg["strings"]:
        if s.encode() in data:
            out.append(("string", s))
    ok = {e.lower() for e in cfg["allowed_emails"]} | {e.lower() for e in allow_emails}
    for m in EMAIL.finditer(data):
        e = m.group().decode("latin-1")
        if e.lower() not in ok:
            out.append(("email", e))
    return out


def hex_runs(data):
    for m in HEXRUN.finditer(data):
        h = m.group().replace(b" ", b"")
        h = h[:len(h) & ~1]
        yield m.start(), bytes.fromhex(h.decode())


def listing_runs(data):
    """(first line, last line, listing lines) of every run of disassembly-like lines, 1-based line numbers."""
    runs, start, last, count = [], None, None, 0
    for n, line in enumerate(data.split(b"\n"), 1):
        if not LISTING.match(line):
            continue
        if start is not None and n - last - 1 <= LISTING_GAP:
            last, count = n, count + 1
            continue
        if start is not None:
            runs.append((start, last, count))
        start, last, count = n, n, 1
    if start is not None:
        runs.append((start, last, count))
    return runs


def dump_line(line):
    """Bytes of a hex dump line (optional address, then 4+ hex tokens), or None."""
    m = DUMPADDR.match(line)
    rest = line[m.end():] if m else line.lstrip(b" \t>*|`#/;-")
    toks = []
    for t in rest.split():
        if not HEXTOK.match(t):
            break
        toks.append(t)
    if len(toks) < 4:
        return None
    return bytes.fromhex(b"".join(toks).decode())


def hex_dumps(data):
    """(first line, last line, bytes) of every dump: consecutive dump lines, or one unbroken hex string."""
    out, cur, start = [], b"", None
    lines = data.split(b"\n")
    for n, line in enumerate(lines + [b""], 1):
        raw = dump_line(line)
        if raw is not None:
            if start is None:
                start, cur = n, b""
            cur += raw
            continue
        if start is not None:
            out.append((start, n - 1, cur))
            start = None
    for m in HEXSTR.finditer(data):
        if JSONHEX.search(data[max(0, m.start() - 16):m.start()]):
            continue
        h = m.group()[:len(m.group()) & ~1]
        n = data.count(b"\n", 0, m.start()) + 1
        out.append((n, n, bytes.fromhex(h.decode())))
    return out


def is_text(run):
    return len(run) <= TEXT_RUN and all(32 <= c <= 126 or c in (9, 10, 13) for c in run)


def jpeg_dht(data):
    """(start, end) of every DHT segment (marker FFC4) in the header of a JPEG file, before the scan data."""
    out, i = [], 2
    if not data.startswith(b"\xff\xd8"):
        return out
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker, size = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xDA or size < 2:
            break
        if marker == 0xC4:
            out.append((i, i + 2 + size))
        i += 2 + size
    return out


def check_file(path, data, cfg, index, allow_emails):
    out = []
    if denied(path):
        out.append(("path", "private path"))
    out += text_findings(path, path.encode(), cfg, allow_emails)
    out += text_findings(path, data, cfg, allow_emails)
    excepted = [bytes.fromhex(h) for h in cfg["bytes_exceptions"].get(path, [])]
    dht = jpeg_dht(data)

    def reviewed(buf):
        return lambda start, length: any(buf[start:start + length] in e for e in excepted)

    def raw_ok(start, length):
        return (is_text(data[start:start + length]) or reviewed(data)(start, length)
                or any(a <= start and start + length <= b for a, b in dht))

    hit = index.find(data, raw_ok)
    if hit:
        out.append(("novation-bytes", f"{hit[1]} bytes at file offset 0x{hit[0]:x} match image {hit[2]}"))
    for pos, raw in hex_runs(data):
        hit = index.find(raw, reviewed(raw))
        if hit:
            out.append(("novation-bytes", f"hex string at file offset 0x{pos:x}: {hit[1]} bytes match image {hit[2]}"))
            break
    if b"\0" in data:
        return out
    for first, last, count in listing_runs(data):
        if count >= LISTING_LINES:
            out.append(("listing", f"lines {first}-{last}: {count} disassembly lines (limit {LISTING_LINES - 1})"))
    for first, last, raw in hex_dumps(data):
        hit = index.find(raw, reviewed(raw)) if len(raw) >= RUN else None
        where = f"line {first}" if first == last else f"lines {first}-{last}"
        if hit:
            out.append(("novation-bytes", f"hex dump at {where}: {hit[1]} bytes match image {hit[2]}"))
        elif len(raw) >= HEXDUMP_BYTES:
            out.append(("hexdump", f"{where}: {len(raw)} bytes written as hex, not in any indexed image"))
    return out


# ---- export --------------------------------------------------------------------------------------------------------

def worktrees(repo):
    paths = [Path(repo).resolve()]
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[9:]).resolve())
    return paths


def main_checkout(repo):
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    return common.parent if common.name == ".git" else None


def check_target(repo, target):
    t = Path(target).resolve()
    for w in worktrees(repo):
        if t == w or w in t.parents:
            raise Refused(f"target {t} is inside the repository or its worktree {w}")
    if t.exists() and (not t.is_dir() or any(t.iterdir())):
        raise Refused(f"target {t} exists and is not an empty directory")
    return t


def export(repo, target, ref="HEAD", deny_file=None, firmware_dirs=None, bootloader_paths=None, allow=ALLOW,
           allow_emails=(), dry_run=False, log=print, exclude=EXCLUDE, publish_as=None):
    repo = Path(git(repo, "rev-parse", "--show-toplevel").strip())
    t = check_target(repo, target)
    cfg = load_deny(deny_file or repo / "export-deny.json")
    main = main_checkout(repo)
    if firmware_dirs is None:
        firmware_dirs = [repo / "firmware"] + ([main / "firmware"] if main else [])
    if bootloader_paths is None:
        bootloader_paths = [repo / BOOTLOADER] + ([main / BOOTLOADER] if main else [])
    images, _ = load_images(firmware_dirs, bootloader_paths, log)
    index = ImageIndex(images)

    entries = []
    for line in git(repo, "ls-tree", "-r", "-z", ref, data=True).split(b"\0"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        mode, kind, obj = meta.split()
        path = path.decode()
        if kind == b"blob" and allowed(path, allow, exclude):
            entries.append((path, mode.decode(), obj.decode()))
    if not entries:
        raise Refused(f"no allow-listed files at {ref}")

    findings, files = [], []
    for path, mode, obj in entries:
        if mode == "120000":
            findings.append((path, "path", "symbolic link"))
            continue
        data = git(repo, "cat-file", "blob", obj, data=True)
        for rule, detail in check_file(path, data, cfg, index, allow_emails):
            findings.append((path, rule, detail))
        files.append((path, mode, data))
    dests = {}
    for path, _, _ in files:
        dests.setdefault(published(path, publish_as), []).append(path)
    for dest, srcs in sorted(dests.items()):
        if len(srcs) > 1:
            findings.append((dest, "path", "published by more than one file: " + ", ".join(sorted(srcs))))
    if findings:
        for path, rule, detail in findings:
            log(f"REFUSED {path}: {rule}: {detail}")
        raise Refused(f"{len(findings)} finding(s) in {len({f[0] for f in findings})} file(s); nothing written")
    if dry_run:
        log(f"dry run: {len(files)} files would be exported to {t}")
        return sorted(published(f[0], publish_as) for f in files)
    for path, mode, data in files:
        out = t / published(path, publish_as)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        os.chmod(out, 0o755 if mode == "100755" else 0o644)
    log(f"exported {len(files)} files from {ref} ({git(repo, 'rev-parse', '--short', ref).strip()}) to {t}")
    return sorted(published(f[0], publish_as) for f in files)


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--deny-file")
    ap.add_argument("--firmware-dir", action="append")
    ap.add_argument("--bootloader", action="append")
    ap.add_argument("--allow-email", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        export(a.repo, a.outdir, a.ref, a.deny_file, a.firmware_dir, a.bootloader, ALLOW, a.allow_email, a.dry_run)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
