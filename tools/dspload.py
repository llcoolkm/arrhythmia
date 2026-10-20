"""Read the DSP cycle meter (devkit image; docs/user/developers.md) through cmd 08 and print the load per core.

    uv run python -I tools/dspload.py /dev/snd/midiC3D0 --unit-idle [--windows 40] [--out f.json]
    python3 -I tools/dspload.py --dump0 meter0.u24be --dump1 meter1.u24be      # emulator X dumps, same decoder

Idle only: the rule for cmd 08 is that the unit is stopped, with no pack,
sample or project loading. The tool refuses to talk to the unit without --unit-idle. It only reads (cmd 08 has no
write form). The meter keeps counting while the tool reads; it publishes a consistent summary once per window
(2048 blocks), guarded by a sequence word at each end, so a torn read is detected and retried.
"""
import argparse
import json
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dspmeter  # noqa: E402

BUDGET = 163840          # DSP cycles per 32-frame block
FIELDS = dspmeter.PUB
SPAN = {c: v["x_end"] - v["ring"] for c, v in dspmeter.CORES.items()}
WHAT = {0: "core 0: its whole block, including the wait for core 1",
        1: "core 1: block start signal to its acknowledgement"}


def summary(words):
    """Published record (16 words) -> dict, or None while torn or not yet published."""
    s = dict(zip(FIELDS, words))
    if s["END"] != s["BEGIN"] or s["MAGIC"] != dspmeter.MAGIC:
        return None
    return s


def decode(core, pub, ring_words, done_before, done_after=None):
    """pub: summary dict. ring_words: 2 * RING_ENTRIES words. Windows that may have been rewritten while the ring was
    read (those completed between `done_before` and `done_after`) are dropped with their slots."""
    if pub["ID"] != (dspmeter.VERSION << 8 | core):
        raise ValueError(f"core {core}: meter id {pub['ID']:#x}, expected {dspmeter.VERSION << 8 | core:#x}")
    n = pub["CNT"]
    s48 = lambda hi, lo: hi << 24 | lo  # noqa: E731
    out = {"core": core, "what": WHAT[core], "blocks": n, "skipped": pub["SKIP"], "windows": pub["WIN"],
           "busy_max": pub["MAX"], "busy_min": pub["MIN"], "busy_mean": s48(pub["SUMH"], pub["SUML"]) / n if n else None,
           "wait_max": pub["WMAX"], "wait_mean": s48(pub["WSUMH"], pub["WSUML"]) / n if n else None,
           "period_min": pub["PMIN"], "period_max": pub["PMAX"], "saturated": n == 0xFFFFFF}
    after = done_before if done_after is None else done_after
    lo = max(0, done_before - dspmeter.RING_ENTRIES + (after - done_before))
    out["ring"] = [{"window": w, "max": ring_words[2 * (w % dspmeter.RING_ENTRIES)],
                    "mean": ring_words[2 * (w % dspmeter.RING_ENTRIES) + 1]} for w in range(done_before - 1, lo - 1, -1)]
    return out


def pct(v):
    return f"{100 * v / BUDGET:5.1f} %" if v is not None else "  n/a"


def report(r, windows=40):
    lines = [f"{r['what']}"]
    if not r["blocks"]:
        return "\n".join(lines + ["  no blocks measured yet"])
    lines.append(f"  blocks {r['blocks']}{' (count saturated)' if r['saturated'] else ''}, skipped {r['skipped']}, "
                 f"windows {r['windows']} of {dspmeter.WINDOW} blocks")
    lines.append(f"  busy cycles/block  min {r['busy_min']:7d}  mean {r['busy_mean']:9.1f}  max {r['busy_max']:7d}"
                 f"   = {pct(r['busy_min'])} / {pct(r['busy_mean'])} / {pct(r['busy_max'])} of {BUDGET}")
    lines.append(f"  free cycles/block  worst {BUDGET - r['busy_max']:7d}  mean {BUDGET - r['busy_mean']:9.1f}"
                 f"  (budget {BUDGET} minus busy)")
    if r["core"] == 0:
        lines.append(f"  of which waiting on core 1: mean {r['wait_mean']:8.1f}  max {r['wait_max']:6d}")
    if r["period_min"]:
        lines.append(f"  block period (timer) min {r['period_min']}  max {r['period_max']}  (nominal {BUDGET})")
    if r["ring"] and windows:
        lines.append(f"  newest windows first (max % / mean %), {min(windows, len(r['ring']))} of {len(r['ring'])}:")
        row = []
        for e in r["ring"][:windows]:
            row.append(f"{e['window']:5d}:{100 * e['max'] / BUDGET:5.1f}/{100 * e['mean'] / BUDGET:5.1f}")
            if len(row) == 5:
                lines.append("    " + "  ".join(row))
                row = []
        if row:
            lines.append("    " + "  ".join(row))
    return "\n".join(lines)


PRE = bytes.fromhex("F0 00 20 29 00 60")
CMD08 = 0x08
MAX_WORDS = 16


def cmd08(fd, core, addr, n, timeout=1.0):
    """One SysEx 08 read of n (1..16) X words of a core's meter windows; returns the 24-bit words."""
    c = dspmeter.CORES[core]
    if not (1 <= n <= MAX_WORDS and c["ring"] <= addr and addr + n <= c["x_end"]):
        raise ValueError(f"core {core} X {addr:#x}+{n}: outside the meter windows")
    msg = PRE + bytes([CMD08, 2 * core, (addr >> 14) & 0x7F, (addr >> 7) & 0x7F, addr & 0x7F, n]) + b"\xF7"
    deadline = time.monotonic() + timeout
    data = msg
    while data:
        if time.monotonic() > deadline:
            raise TimeoutError("write timed out")
        if select.select([], [fd], [], 0.1)[1]:
            try:
                data = data[os.write(fd, data):]
            except BlockingIOError:
                pass
    buf = b""
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)
            except BlockingIOError:
                pass
        while True:
            i = buf.find(PRE + bytes([CMD08]))
            j = buf.find(b"\xF7", i) if i >= 0 else -1
            if i < 0 or j < 0:
                break
            frame, buf = buf[i:j + 1], buf[j + 1:]
            if frame[7:12] != msg[7:12]:
                continue
            if len(frame) < 14 or frame[12] != 1:
                raise ValueError("refused by the unit (arrhythmia-devkit only, meter windows only)")
            if len(frame) != 14 + 6 * n or any(b > 0x0F for b in frame[13:-1]):
                raise ValueError(f"reply length {len(frame)}, expected {14 + 6 * n}")
            return [int("".join(f"{x:x}" for x in frame[13 + 6 * i:19 + 6 * i]), 16) for i in range(n)]
    raise TimeoutError("no reply (is arrhythmia-devkit installed?)")


def read_x(fd, core, addr, count):
    words = []
    for off in range(0, count, MAX_WORDS):
        words += cmd08(fd, core, addr + off, min(MAX_WORDS, count - off))
    return words


def read_unit(fd, core, tries=20):
    c = dspmeter.CORES[core]
    for _ in range(tries):
        s1 = summary(read_x(fd, core, c["pub"], len(FIELDS)))
        if s1 is None:
            time.sleep(0.05)
            continue
        ring = read_x(fd, core, c["ring"], 2 * dspmeter.RING_ENTRIES)
        s2 = summary(read_x(fd, core, c["pub"], len(FIELDS)))
        if s2 is None:
            continue
        return decode(core, s1, ring, s1["WIN"], s2["WIN"]), {"summary": s1, "ring": ring, "summary_after": s2}
    raise RuntimeError(f"core {core}: no consistent summary in {tries} tries (meter not installed, or no window "
                       "completed yet: wait 2 s after boot)")


def from_dump(core, path):
    data = open(path, "rb").read()
    words = [int.from_bytes(data[i:i + 3], "big") for i in range(0, len(data), 3)]
    if len(words) != SPAN[core]:
        raise ValueError(f"{path}: {len(words)} words, expected {SPAN[core]}")
    c = dspmeter.CORES[core]
    pub = summary(words[c["pub"] - c["ring"]:c["pub"] - c["ring"] + len(FIELDS)])
    if pub is None:
        raise ValueError(f"{path}: no published summary")
    return decode(core, pub, words[:2 * dspmeter.RING_ENTRIES], pub["WIN"]), words


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dev", nargs="?")
    ap.add_argument("--unit-idle", action="store_true", help="confirm: unit stopped, nothing loading")
    ap.add_argument("--dump0")
    ap.add_argument("--dump1")
    ap.add_argument("--windows", type=int, default=40)
    ap.add_argument("--out")
    a = ap.parse_args()
    results, raw = [], {}
    if a.dump0 or a.dump1:
        for core, path in ((0, a.dump0), (1, a.dump1)):
            if path:
                r, raw[core] = from_dump(core, path)
                results.append(r)
    else:
        if not a.dev:
            ap.error("give the MIDI device, or --dump0/--dump1")
        if not a.unit_idle:
            ap.error("reads are allowed only while the unit is idle: stop playback, wait for any load to finish, "
                     "then pass --unit-idle")
        if a.out and os.path.exists(a.out):
            sys.exit(f"{a.out} exists")
        fd = os.open(a.dev, os.O_RDWR | os.O_NONBLOCK)
        try:
            for core in (0, 1):
                r, raw[core] = read_unit(fd, core)
                results.append(r)
        finally:
            os.close(fd)
    for r in results:
        print(report(r, a.windows))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"tool": "dspload", "monotonic": time.monotonic(), "wall": time.time(), "budget": BUDGET,
                       "results": results, "raw": raw}, f, indent=1)


if __name__ == "__main__":
    main()
