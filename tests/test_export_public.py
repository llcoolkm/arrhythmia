"""tools/export_public.py against a synthetic git repo with a synthetic firmware image. No real firmware needed.

    uv run python -I tests/test_export_public.py
"""
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import export_public as ep  # noqa: E402
import syx  # noqa: E402

# Values the real deny file refuses (serial shape, placeholder, private IPs, e-mail) are built at run time, so this
# file passes the export scan itself.
SERIAL = "CR" + "0TEST0000000"
MARKER = "<SERIAL" + "UPN123>"
OK_EMAIL = "ok" + "@example.com"
SECTION = ".".join(("10", "5", "3", "1"))                            # a section number that reads as an IP
rng = random.Random(1)
IMAGE = bytes(rng.randrange(256) for _ in range(0x4000)) + b"\xff" * 64
LOG = b"Sample Memory Exceeded 0x%X + 0x%X"                          # a log string in the image
LONGTEXT = bytes(rng.randrange(0x20, 0x7F) for _ in range(ep.TEXT_RUN + 1))  # printable, but too long to pass
IMAGE += b"\0" + LOG + b"\0" + LONGTEXT + b"\0"
BOOT = bytes(rng.randrange(256) for _ in range(0x1000))
INIT = bytes([1, 0x63, 0, 0, 0, 7, 3, 1])
DENY = {
    "serials": [SERIAL],
    "serial_patterns": [r"\bCR[0-9A-Z]{12}\b"],
    "markers": [MARKER],
    "marker_exceptions": {"tools/excepted.py": [MARKER]},
    "hostnames": ["example-internal.org"],
    "strings": ["/home/someone"],
    "allowed_emails": [OK_EMAIL],
    "bytes_exceptions": {"docs/probe.md": [IMAGE[0x500:0x511].hex()]},
    "ip_exceptions": {"docs/manual.md": [SECTION]},
}
CLEAN = {
    "tools/a.py": b"print('hello')\n# 15 bytes of firmware are fine\n",
    "tools/run.sh": b"#!/bin/sh\necho hi\n",
    "patches/src/x.c": b"int x;\n",
    "docs/user/README.md": ("# Readme\nmail " + OK_EMAIL + "\nversion 0.0.0.7.4.9, port hw:3,0,0\n").encode(),
    "LICENSE": b"MIT\n",
    "tools/excepted.py": ("PLACEHOLDER = b'" + MARKER + "'\n").encode(),
    "docs/nor-backup.md": ("serial " + SERIAL + "\n").encode(),
    "docs/map.md": b"# Map\n0x08012345 shim -> 0x08040108 sink\nP:1B2B initializes X:38\n",
    "tests/test_x.py": b"assert True\n",
    "patches/p.json": b'{"expect": "not allow-listed"}\n',
    "notes/private.md": b"not allow-listed\n",
    "ghidra_scripts/x.java": b"class X {}\n",
    "export-deny.json": json.dumps(DENY).encode(),
}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp, extra=None):
    repo = Path(tmp) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    files = dict(CLEAN)
    files.update(extra or {})
    for path, data in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    os.chmod(repo / "tools/run.sh", 0o755)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    fw = Path(tmp) / "fw"
    fw.mkdir(exist_ok=True)
    (fw / "fake.syx").write_bytes(syx.build(IMAGE, INIT, bytes([0, 0, 0, 0, 7, 3, 1])))
    (Path(tmp) / "boot.bin").write_bytes(BOOT)
    return repo


# The scanner tests below put synthetic files anywhere under these directories, which the real ALLOW lists by name
# only; they run with the directories allow-listed whole. test_clean_export and test_future_files check the real ALLOW.
SCAN_ALLOW = ep.ALLOW + ("docs/", "tests/", "tools/", "patches/src/", "release/", "web/")
NAMED = ("tools/a.py", "tools/run.sh", "tools/excepted.py", "patches/src/x.c")   # the fixture's public files


def run(tmp, repo, target=None, **kw):
    logs = []
    kw.setdefault("allow", SCAN_ALLOW)
    files = ep.export(repo, target or Path(tmp) / "out", firmware_dirs=[Path(tmp) / "fw"],
                      bootloader_paths=[Path(tmp) / "boot.bin"], log=logs.append, **kw)
    return files, logs


def refused_by(extra, rule, path=None):
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, extra)
        out = Path(tmp) / "out"
        try:
            run(tmp, repo, out)
        except ep.Refused:
            pass
        else:
            raise AssertionError(f"accepted {list(extra)} (expected {rule})")
        assert not out.exists(), "files written despite a finding"
        logs = []
        try:
            ep.export(repo, Path(tmp) / "out2", firmware_dirs=[Path(tmp) / "fw"],
                      bootloader_paths=[Path(tmp) / "boot.bin"], allow=SCAN_ALLOW, log=logs.append)
        except ep.Refused:
            pass
        hits = [line for line in logs if line.startswith("REFUSED")]
        assert any(f": {rule}:" in line and (path is None or f" {path}:" in line) for line in hits), (rule, hits)


def test_clean_export():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        (repo / "tools/uncommitted.py").write_text("x = 1\n")      # working tree only: never exported
        # every path is opt-in by name: with the real ALLOW the fixture's tools/ and patches/src/ files stay private
        files, _ = run(tmp, repo, allow=ep.ALLOW, dry_run=True)
        assert sorted(files) == ["LICENSE", "README.md"], files
        files, _ = run(tmp, repo, allow=ep.ALLOW + NAMED)
        out = Path(tmp) / "out"
        # docs/map.md and tests/test_x.py are not listed, so they stay private
        assert sorted(files) == ["LICENSE", "README.md", "patches/src/x.c", "tools/a.py",
                                 "tools/excepted.py", "tools/run.sh"], files
        assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == sorted(files)
        assert (out / "tools/a.py").read_bytes() == CLEAN["tools/a.py"]
        assert os.stat(out / "tools/run.sh").st_mode & 0o111
        assert not os.stat(out / "tools/a.py").st_mode & 0o111


def test_publish_as():
    """docs/user/README.md is published as the export's README.md; the private root README.md never is."""
    assert ep.PUBLISH_AS == {"docs/user/README.md": "README.md"}
    assert ep.published("docs/user/README.md") == "README.md" and ep.published("LICENSE") == "LICENSE"
    assert ep.denied("README.md") and not ep.allowed("README.md")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"README.md": b"# private\n"})
        files, _ = run(tmp, repo, allow=ep.ALLOW)
        out = Path(tmp) / "out"
        assert sorted(files) == ["LICENSE", "README.md"], files
        assert (out / "README.md").read_bytes() == CLEAN["docs/user/README.md"]
        assert not (out / "docs").exists()
    with tempfile.TemporaryDirectory() as tmp:               # allow-listing the private root README refuses
        repo = make_repo(tmp, {"README.md": b"# private\n"})
        try:
            run(tmp, repo, allow=ep.ALLOW + ("README.md",), exclude=())
        except ep.Refused:
            pass
        else:
            raise AssertionError("private root README.md exported")
    with tempfile.TemporaryDirectory() as tmp:               # two sources for one published path refuse
        repo = make_repo(tmp)
        logs = []
        try:
            ep.export(repo, Path(tmp) / "out", firmware_dirs=[Path(tmp) / "fw"],
                      bootloader_paths=[Path(tmp) / "boot.bin"], allow=ep.ALLOW + ("tools/a.py",),
                      publish_as={"docs/user/README.md": "README.md", "tools/a.py": "README.md"}, log=logs.append)
        except ep.Refused:
            pass
        else:
            raise AssertionError("two files published as README.md")
        assert any("REFUSED README.md: path: published by more than one file" in line for line in logs), logs
        assert not (Path(tmp) / "out").exists()


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        files, _ = run(tmp, repo, dry_run=True)
        assert files and not (Path(tmp) / "out").exists()


def test_target_rules():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        for bad in (repo, repo / "sub" / "out", repo / "tools"):
            try:
                run(tmp, repo, bad)
            except ep.Refused:
                pass
            else:
                raise AssertionError(f"target {bad} accepted")
        wt = Path(tmp) / "wt"
        git(repo, "worktree", "add", "-q", str(wt))
        try:
            run(tmp, repo, wt / "out")
        except ep.Refused:
            pass
        else:
            raise AssertionError("target inside a worktree accepted")
        full = Path(tmp) / "full"
        full.mkdir()
        (full / "x").write_text("x")
        try:
            run(tmp, repo, full)
        except ep.Refused:
            pass
        else:
            raise AssertionError("non-empty target accepted")
        assert not (repo / "sub").exists()


def test_novation_bytes_raw():
    refused_by({"tools/blob.bin": b"head" + IMAGE[0x1234:0x1234 + 16] + b"tail"}, "novation-bytes", "tools/blob.bin")
    refused_by({"web/x.bin": IMAGE[0x2001:0x2001 + 40]}, "novation-bytes", "web/x.bin")       # any alignment
    refused_by({"tools/boot.bin": BOOT[0x333:0x333 + 16]}, "novation-bytes", "tools/boot.bin")  # bootloader


def test_novation_bytes_hex():
    refused_by({"release/vX.json": json.dumps({"code": IMAGE[0x800:0x820].hex()}).encode()},
               "novation-bytes", "release/vX.json")
    refused_by({"tools/t.py": ("X = '" + IMAGE[0x900:0x920].hex(" ").upper() + "'\n").encode()},
               "novation-bytes", "tools/t.py")


def test_novation_bytes_below_limit_and_fill():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"tools/short.bin": b"\x00" + IMAGE[0x1000:0x100F] + b"\x00",   # 15 bytes
                               "tools/fill.bin": b"\xff" * 64 + b"\x00" * 64})              # fill only
        files, _ = run(tmp, repo)
        assert "tools/short.bin" in files and "tools/fill.bin" in files


def test_serials_and_markers():
    refused_by({"tools/s.py": f"S = '{SERIAL}'\n".encode()}, "serial", "tools/s.py")
    refused_by({"docs/user/s.md": SERIAL.lower().encode()}, "serial", "docs/user/s.md")
    refused_by({"tools/p.py": ("x = 'CR9ABCDE1F2345G'\ny = '" + "CR" + "1ABCDEFGH234'\n").encode()}, "serial-pattern",
               "tools/p.py")
    refused_by({"tools/m.py": ("M = b'" + MARKER + "'\n").encode()}, "marker", "tools/m.py")


def test_email_hostname_strings():
    refused_by({"tools/e.py": ("# contact someone" + "@example.net\n").encode()}, "email", "tools/e.py")
    refused_by({"docs/user/h.md": b"see https://wiki.Example-Internal.org/doc\n"}, "hostname", "docs/user/h.md")
    for ip in ((192, 168, 9, 1), (10, 9, 9, 9), (172, 31, 9, 1)):
        refused_by({"tools/ip.py": ("HOST = '%d.%d.%d.%d:8080'\n" % ip).encode()}, "hostname", "tools/ip.py")
    refused_by({"tools/home.py": b"P = '/home/someone/x'\n"}, "string", "tools/home.py")
    refused_by({"tools/example-internal.org.txt": b"x\n"}, "hostname")                         # in the path
    with tempfile.TemporaryDirectory() as tmp:                                                 # not private
        repo = make_repo(tmp, {"tools/pub.py": b"A = '8.8.8.8'\nB = '172.32.0.1'\nV = '10.0.0.0.7'\n"
                                                 + ("# @decorator, user@ alone, " + OK_EMAIL.upper() + "\n").encode()})
        assert "tools/pub.py" in run(tmp, repo)[0]


def test_deny_paths():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        logs = []
        try:
            ep.export(repo, Path(tmp) / "out", firmware_dirs=[Path(tmp) / "fw"],
                      bootloader_paths=[Path(tmp) / "boot.bin"], allow=SCAN_ALLOW + ("export-deny.json", "ghidra_scripts/"),
                      exclude=(), log=logs.append)
        except ep.Refused:
            pass
        else:
            raise AssertionError("private paths exported")
        hits = " ".join(logs)
        assert "docs/nor-backup.md: path:" in hits and "export-deny.json: path:" in hits
        assert "ghidra_scripts/x.java: path:" in hits
        assert "notes/private.md" not in hits and "patches/p.json" not in hits


def test_exclude():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        files, _ = run(tmp, repo, dry_run=True)
        assert "docs/nor-backup.md" not in files and "docs/map.md" in files
        assert all(x in ep.DENY_PATHS for x in ep.EXCLUDE)
        assert "tools/emu.py" in ep.EXCLUDE and "tools/dsp/" in ep.EXCLUDE and "tools/dumpflash.py" in ep.EXCLUDE
        assert "docs/export-audit.md" in ep.EXCLUDE and "docs/release-plan.md" in ep.EXCLUDE          # dropping an exclusion refuses, never publishes


def test_future_files():
    """A new file in any published directory stays private until ALLOW names it."""
    new = {"tools/stock_map.py": b"x = 1\n", "patches/src/stock_map.c": b"int y;\n",
           "release/private.json": b"{}\n", "web/private.html": b"<p>x</p>\n", "docs/new-note.md": b"# x\n",
           "docs/user/new.md": b"# x\n", "tests/test_new.py": b"assert True\n", "tests/fixtures/new.json": b"{}\n",
           "modules/new.c": b"int z;\n", "tools/dspmeter/new.cpp": b"int w;\n"}
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, new)
        files, _ = run(tmp, repo, allow=ep.ALLOW, dry_run=True)
        assert not set(new) & set(files), sorted(set(new) & set(files))
    for path in new:
        assert not ep.allowed(path), path


def test_opt_in():
    assert ep.opt_in_violations(ep.ALLOW) == []
    assert ep.opt_in_violations(ep.ALLOW + ("docs/", "tests/fixtures/")) == ["docs/", "tests/fixtures/"]
    for d in ("tools/", "patches/src/", "release/", "web/", "modules/"):
        assert ep.opt_in_violations(ep.ALLOW + (d,)) == [d], d
    assert not any(a.endswith("/") for a in ep.ALLOW)
    assert not any(a.startswith("ghidra_scripts") for a in ep.ALLOW)
    assert ep.allowed("RELEASE-NOTES-0.1.0.md") and ep.allowed("LICENSE")
    assert ep.allowed("modules/paint.c") and ep.allowed("modules/stars.c")      # the only published modules
    for name in ("chase", "clock_demo", "abi3_demo", "chord"):
        assert not ep.allowed(f"modules/{name}.c"), name
    for path in ep.ALLOW:                                   # nothing allow-listed by name is also denied
        assert not ep.denied(path), path


def test_needs_firmware_and_deny_file():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        for kw in ({"firmware_dirs": [Path(tmp) / "nothing"]},
                   {"deny_file": Path(tmp) / "missing.json"}):
            args = {"firmware_dirs": [Path(tmp) / "fw"], "bootloader_paths": [Path(tmp) / "boot.bin"],
                    "log": lambda *_: None}
            args.update(kw)
            try:
                ep.export(repo, Path(tmp) / "out", **args)
            except ep.Refused:
                pass
            else:
                raise AssertionError(f"ran with {kw}")
        (Path(tmp) / "fw" / "bad.syx").write_bytes(b"\xF0\x00\x20\x29\x00\x71\xF7")
        try:
            run(tmp, repo)
        except ep.Refused:
            pass
        else:
            raise AssertionError("undecodable firmware accepted")


OTHER = bytes(rng.randrange(256) for _ in range(256))                # bytes of an image that is not indexed


def arm_listing(n, base=0x0803A000):
    ops = ("ldr r3, [pc, #12]", "movs r0, #1", "bl 0x08040108", "cmp r0, r3", "bne.n 0x0803a010", "pop {r4, pc}")
    return "".join(f"{base + 2 * i:08x}: {OTHER[i]:02x}{OTHER[i + 1]:02x}  {ops[i % len(ops)]}\n" for i in range(n))


def dsp_listing(n):
    ops = ("move x:(r0)+,a", "jsr $1b2e", "movep y0,x:$ffffef", "rts")
    return "".join(f"P:{0x1B00 + i:04X} {OTHER[i]:02X}{OTHER[i + 1]:02X}{OTHER[i + 2]:02X}  {ops[i % 4]}\n" for i in range(n))


def hex_dump(raw, width=16):
    return "".join(f"{i:08x}  " + " ".join(f"{b:02x}" for b in raw[i:i + width]) + "  |................|\n"
                   for i in range(0, len(raw), width))


def test_listing():
    lim = ep.LISTING_LINES
    md = "# Notes\n\n```\n" + arm_listing(lim) + "```\n"
    refused_by({"docs/arm.md": md.encode()}, "listing", "docs/arm.md")
    refused_by({"docs/dsp.md": dsp_listing(lim).encode()}, "listing", "docs/dsp.md")
    spaced = "".join(f"08 03 a0 {2 * i:02x}  {OTHER[i]:02x} {OTHER[i + 1]:02x}  ldr r3, [pc, #12]\n" for i in range(lim))
    refused_by({"tools/l.txt": spaced.encode()}, "listing", "tools/l.txt")
    # a label and a blank line inside the run do not split it; the report names the line range and length
    split = arm_listing(10) + "\nFUN_0803a014:\n" + arm_listing(lim - 10, 0x0803A014)
    finds = ep.check_file("docs/s.md", split.encode(), DENY, ep.ImageIndex([IMAGE]), ())
    assert ("listing", f"lines 1-{lim + 2}: {lim} disassembly lines (limit {lim - 1})") in finds, finds
    with tempfile.TemporaryDirectory() as tmp:                             # short excerpts and prose pass
        prose = "".join(f"0x{0x08012345 + i:08X} shim -> 0x08040108 sink\nP:1B{i:02X} initializes X:38\n"
                        for i in range(lim))
        far = arm_listing(lim - 1) + "\n\n\n" + arm_listing(lim - 1, 0x0803B000)  # 3 other lines end a run
        repo = make_repo(tmp, {"docs/short.md": ("```\n" + arm_listing(lim - 1) + "```\n").encode(),
                               "docs/prose.md": prose.encode(), "docs/far.md": far.encode()})
        files, _ = run(tmp, repo, dry_run=True)
        assert {"docs/short.md", "docs/prose.md", "docs/far.md"} <= set(files)


def test_hexdump():
    n = ep.HEXDUMP_BYTES
    refused_by({"docs/dump.md": ("```\n" + hex_dump(OTHER[:n]) + "```\n").encode()}, "hexdump", "docs/dump.md")
    refused_by({"docs/one.md": ("blob " + OTHER[:n].hex() + "\n").encode()}, "hexdump", "docs/one.md")
    words = "\n".join("P:%04X " % (16 * i) + " ".join(OTHER[j:j + 3].hex() for j in range(48 * i, 48 * i + 48, 3))
                      for i in range(2))
    refused_by({"docs/words.md": words.encode()}, "hexdump", "docs/words.md")            # 24-bit DSP words
    # a dump of image bytes spread over short lines: the single-line hex scan misses it, the dump scan does not
    refused_by({"docs/img.md": hex_dump(IMAGE[0x3000:0x3000 + 32], 8).encode()}, "novation-bytes", "docs/img.md")
    finds = ep.check_file("docs/d.md", hex_dump(OTHER[:n]).encode(), DENY, ep.ImageIndex([IMAGE]), ())
    assert ("hexdump", f"lines 1-4: {n} bytes written as hex, not in any indexed image") in finds, finds
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"docs/small.md": hex_dump(OTHER[:n - 1]).encode(),
                               "docs/table.md": b"| pad | note |\n| 01 | 24 |\n| 02 | 25 |\n",
                               "release/p.json": json.dumps({"hex": OTHER.hex()}).encode()})  # crpatch payload
        files, _ = run(tmp, repo, dry_run=True)
        assert {"docs/small.md", "docs/table.md", "release/p.json"} <= set(files)


def test_quoted_text():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"docs/log.md": b'On overflow it logs "' + LOG + b'" and stops.\n'})
        assert "docs/log.md" in run(tmp, repo, dry_run=True)[0]
    refused_by({"docs/long.md": b"quote: " + LONGTEXT + b"\n"}, "novation-bytes", "docs/long.md")      # > TEXT_RUN
    refused_by({"tools/bin.py": b"x = 1\n" + IMAGE[0x1800:0x1810] + b"\n"}, "novation-bytes", "tools/bin.py")
    refused_by({"docs/loghex.md": LOG.hex(" ").encode() + b"\n"}, "novation-bytes", "docs/loghex.md")  # hex: no pass


def test_reviewed_exceptions():
    reply = IMAGE[0x500:0x511]
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"docs/probe.md": ("reply `" + reply.hex(" ").upper() + "`\n").encode(),
                               "docs/manual.md": ("see sections 10.2, " + SECTION + ", 10.6\n").encode()})
        assert {"docs/probe.md", "docs/manual.md"} <= set(run(tmp, repo, dry_run=True)[0])
    refused_by({"docs/other.md": reply.hex(" ").encode()}, "novation-bytes", "docs/other.md")       # other path
    refused_by({"docs/probe.md": IMAGE[0x4F0:0x511].hex(" ").encode()}, "novation-bytes", "docs/probe.md")  # longer
    refused_by({"docs/other.md": ("see " + SECTION + "\n").encode()}, "hostname", "docs/other.md")


def jpeg(segments):
    return b"\xff\xd8" + b"".join(b"\xff" + bytes([m]) + (len(p) + 2).to_bytes(2, "big") + p
                                   for m, p in segments) + b"\xff\xda\x00\x02" + bytes(64) + b"\xff\xd9"


def test_jpeg_huffman_tables():
    table = IMAGE[0x600:0x618]
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {"docs/img/a.jpg": jpeg([(0xE0, b"JFIF\0"), (0xC4, b"\x00" + table)])})
        assert "docs/img/a.jpg" in run(tmp, repo, dry_run=True)[0]
    refused_by({"docs/img/b.jpg": jpeg([(0xE0, b"JFIF\0" + table)])}, "novation-bytes", "docs/img/b.jpg")
    refused_by({"docs/img/c.png": b"\x89PNG" + b"\xff\xc4" + table}, "novation-bytes", "docs/img/c.png")


def test_cli():
    with tempfile.TemporaryDirectory() as tmp:
        addr = "a" + "@b.example.net"
        repo = make_repo(tmp, {"tools/crpatch.py": ("# " + addr + "\n").encode()})   # a name ALLOW lists
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "export_public.py")
        base = [sys.executable, "-I", tool, "--repo", str(repo), "--firmware-dir", str(Path(tmp) / "fw"),
                "--bootloader", str(Path(tmp) / "boot.bin")]
        r = subprocess.run(base + [str(Path(tmp) / "o1")], capture_output=True, text=True)
        assert r.returncode == 1 and "REFUSED tools/crpatch.py: email" in r.stdout, r.stdout + r.stderr
        r = subprocess.run(base + ["--allow-email", addr, str(Path(tmp) / "o2")],
                           capture_output=True, text=True)
        assert r.returncode == 0 and (Path(tmp) / "o2" / "tools" / "crpatch.py").exists(), r.stdout + r.stderr


# Provenance rules (D7). Every stock name, hash and branch name is built at run time, so this file passes them itself.
FUN = "FUN" + "_0805c060"
H7, H7B = "63fe" + "2e0", "8265" + "dba"
H40 = "1a" * 20
BRANCH = "subtractive" + "-m2"


def prov(text, path="patches/src/x.c", allow=ep.ALLOW, passed=()):
    return sorted({r for r, _ in ep.provenance_findings(path, text.encode(), allow, ep.EXCLUDE, passed)})


def prov_details(text, rule, path="patches/src/x.c"):
    return sorted(d for r, d in ep.provenance_findings(path, text.encode()) if r == rule)


def test_provenance_rules():
    # decompiler names (Ghidra and IDA auto-names of a stock address)
    for t in ("/* the view (" + FUN + " object) */", "thunk_" + "FUN" + "_08031268(0)", "DAT_" + "20003688",
              "LAB_" + "0803b7ec:", "sub_" + "0803B7EC", "loc_" + "8031268", "PTR_" + "FUN" + "_08031268",
              "PTR_" + "DAT" + "_20003688", "(" + "PTR_" + "PTR_" + "FUN" + "_0805c060)"):
        assert prov(t) == ["decompiler-name"], (t, prov(t))
    for t in ("0x0805C060u", "FUN_ny", "FUNCTION_0805c060", "MY_FUN_0805c060", "fn_0805c060", "sub_total 12",
              "PTR_FUN_ny", "XPTR_" + "FUN" + "_0805c060"):
        assert "decompiler-name" not in prov(t), t
    # citations of decompilation and the reverse-engineering tool
    for t in ("runs in the Main task [decomp, x]", "the decompiled loop", "Decompilation shows", "Ghid" + "ra script",
              "ghid" + "ra_scripts/x.java"):
        assert "decompilation" in prov(t), t
    for t in ("decompress the stream", "decomposition", "a decompressor"):
        assert "decompilation" not in prov(t), t
    # private paths: a private doc or test by path, an unpublished docs/ path, a private doc by bare name
    assert prov_details("see docs/a1-notetap.md section 2", "private-path") == ["docs/a1-notetap.md"]
    assert prov_details("(tests/test_core0_feed.py)", "private-path") == ["tests/test_core0_feed.py"]
    assert prov_details("design docs/never-listed.md.", "private-path") == ["docs/never-listed.md"]
    assert prov_details("[decomp, note-path.md]", "private-path") == ["note-path.md"]
    bench = "tools/core0_" + "bench_profile.py"                       # an EXCLUDE entry outside docs/ and tests/
    assert prov_details("# " + bench + " --only adds the gate", "private-path") == [bench]
    assert prov_details("# the private bench build adds the gate; tools/build_lightshow.py", "private-path") == []
    assert "docs/core0-public-0.2-candidate.md" in prov_details("docs/core0-public-0.2-candidate.md", "private-path")
    for t in ("docs/user/README.md", "docs/led-map.json", "docs/dev/ and docs/user/", "docs/dev/design/scales.md",
              "plock-fix.md", "scales.md", "README.md", "mydocs/x.md", "tests/test_export_public.py"):
        assert "private-path" not in prov(t), t
    # commit hashes: 40-hex object names; short hashes by their context word, before or after, and their chains
    assert prov_details("pinned at " + H40, "commit-hash") == [H40]
    for t, want in (("(Codex review " + H7 + ", finding 2)", [H7]), ("commit " + H7, [H7]), (H7 + " commit", [H7]),
                    ("Codex's table, " + H7 + " / " + H7B + ")", sorted([H7, H7B])),
                    ("(" + H7 + ": pads poly with POLY on, Codex approved)", [H7]),
                    ("branch x/y " + H7, [H7]), ("@" + H7, [H7])):
        assert prov_details(t, "commit-hash") == want, (t, prov_details(t, "commit-hash"))
    for t in ("SHA-256 " + "ab12" * 16, "Codex 7304 and 7306", "Codex: 0x1234abcu", "Codex defaced",
              '{"hex": "44f057ff44f056fe"}', "ARM region " + "a0d0" + "1b8e...", "review " + "x" * 70 + " " + H7,
              "label " + "1218" + "424f"):
        assert "commit-hash" not in prov(t), t
    # reviewed exceptions and the tool's own exemption
    assert prov("upstream " + H40, passed=(H40,)) == []
    assert prov("ghid" + "ra_scripts/ docs/a1-notetap.md", path="tools/export_public.py") == []
    assert prov(FUN, path="tools/export_public.py") == ["decompiler-name"]       # exempt from two rules only


def test_provenance_export():
    refused_by({"patches/src/v.c": ("/* the view (" + FUN + " object) */\n").encode()}, "decompiler-name",
               "patches/src/v.c")
    refused_by({"tools/c.py": b"# hook_sysex runs in the Main task [decomp]\n"}, "decompilation", "tools/c.py")
    refused_by({"tools/r.py": b"# design: docs/a1-notetap.md\n"}, "private-path", "tools/r.py")
    refused_by({"tools/h.py": ("# (Codex review " + H7 + ")\n").encode()}, "commit-hash", "tools/h.py")
    deny = dict(DENY, strings=DENY["strings"] + [BRANCH])
    refused_by({"export-deny.json": json.dumps(deny).encode(),
                "tools/b.py": ("# dsp/" + BRANCH + " streams\n").encode()}, "string", "tools/b.py")
    with tempfile.TemporaryDirectory() as tmp:          # reviewed exception, and clean text, pass
        deny = dict(DENY, provenance_exceptions={"tools/pin.py": [H40]})
        repo = make_repo(tmp, {"export-deny.json": json.dumps(deny).encode(),
                               "tools/pin.py": ("PIN = '" + H40 + "'\n").encode(),
                               "tools/ok.py": b"# DSP word read (core, a16): the word in bits 8..31; docs/user/\n"})
        files = run(tmp, repo)[0]
        assert "tools/pin.py" in files and "tools/ok.py" in files, files



def test_dspvoice207_named_closure():
    expected = {'tests/test_dspvoice207_source.py',
                'tools/dspvoice207/NOTICE.md', 'tools/dspvoice207/README.md',
                'tools/dspvoice207/build.py', 'tools/dspvoice207/instrument.asm',
                'tools/dspvoice207/tables.py', 'tools/dspvoice207/source-manifest.json'}
    assert set(ep.PUBLIC_DSPVOICE207) == expected
    assert expected <= set(ep.ALLOW)
    assert 'tools/dspvoice207/' not in ep.ALLOW
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, {'tools/dspvoice207/build.py': b'pass\n',
                               'tools/dspvoice207/unlisted.py': b'pass\n'})
        files, _ = run(tmp, repo, allow=ep.ALLOW)
        assert 'tools/dspvoice207/build.py' in files
        assert 'tools/dspvoice207/unlisted.py' not in files
    # A newly named source path gets the same provenance checks as every other public file.
    refused_by({'tools/dspvoice207/build.py': b'# docs/a1-notetap.md\n'},
               'private-path', 'tools/dspvoice207/build.py')


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")


if __name__ == "__main__":
    main()
