"""Offline tests for tools/capture.py, tools/capture_align.py and tools/wavio.py on synthetic WAVs.

    uv run python -I tests/test_capture.py

No device is touched: the live-path tests use a regular file or a fake class as the MIDI port and a Python script
as the recorder. The regression reproductions are
test_codex_repro_*.
"""
import asyncio
import copy
import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import capture  # noqa: E402
import capture_align as ca  # noqa: E402
import levelmeter  # noqa: E402
import levelview as lv  # noqa: E402
import wavio  # noqa: E402

RATE = 48000
# the fixture triangle's onset: the first sample where its energy envelope (two trailing 1 ms boxes) reaches the
# envelope peak - 30 dB (capture_align onset rule), 7 samples after the hit's first (zero) sample
DET = 7


def tri(n=4096, period=64, peak=0.25):
    """Same shape as the reference fixture: period-64 triangle starting at 0."""
    q = period // 4
    out = []
    for i in range(n):
        p = i % period
        v = p / q if p < q else (2 - p / q if p < 3 * q else p / q - 4)
        out.append(peak * v)
    return out


def hit_shape(kind="tri"):
    if kind == "square":
        return [0.25 if (i // 32) % 2 == 0 else -0.25 for i in range(4096)] + [0.0] * 4800
    if kind == "faded":            # 'short' with a 2 ms raised-cosine fade-in: a gradual (band-limited) attack
        n = int(0.002 * RATE)
        return [v * (0.5 - 0.5 * math.cos(math.pi * i / n)) if i < n else v for i, v in enumerate(hit_shape("short"))]
    if kind == "short":            # triangle, then a 5 ms exponential tail: gone well inside 198 ms
        return tri() + [0.25 * math.exp(-i / (0.005 * RATE)) * math.sin(2 * math.pi * 300 * i / RATE)
                        for i in range(int(0.05 * RATE))]
    if kind == "sustain":          # never decays before the next hit
        return [0.1 * math.sin(2 * math.pi * 200 * i / RATE) for i in range(int(2.5 * RATE))]
    # triangle + 0.5 s decaying 200 Hz tail (tau 0.1 s): still about -39 dBFS at 198 ms
    return tri() + [0.08 * math.sin(2 * math.pi * 200 * i / RATE) * math.exp(-i / (0.1 * RATE))
                    for i in range(int(0.5 * RATE))]


def synth(path, onsets, gains, shapes=None, seconds=12.0, noise=1e-5, seed=1, bits=24, sample_type="pcm",
          kind="tri", dc=0.0):
    rng = random.Random(seed)
    n = int(seconds * RATE)
    left = [dc + (rng.gauss(0, noise) if noise else 0.0) for _ in range(n)]
    right = [dc + (rng.gauss(0, noise) if noise else 0.0) for _ in range(n)]
    for k, (o, g) in enumerate(zip(onsets, gains)):
        h = hit_shape((shapes or {}).get(k, kind))
        for i, v in enumerate(h):
            if 0 <= o + i < n:
                left[o + i] += g * v
                right[o + i] += 0.8 * g * v
    wavio.write(path, RATE, [left, right], sample_type, bits)


def unity_calibration(d, nch):
    """An accepted calibration with every ratio 1 (written by hand, not measured) for nch channels, in d."""
    p = os.path.join(d, f"calibration-unity{nch}.json")
    if not os.path.exists(p):
        w = os.path.join(d, f"calibration-unity{nch}.wav")
        wavio.write(w, RATE, [[0.0] * 10 for _ in range(nch)], "pcm", 24)
        ratios = {str(c): 1.0 for c in range(1, nch + 1)}
        json.dump({"schema": ca.CALIBRATION_SCHEMA, "created_utc": "test", "accepted": True, "reasons": [],
                   "chain": {"interface": "Zoom H5studio", "channels": nch, "gain_note": "g",
                             "channel_map": {str(c): f"in {c}" for c in range(1, nch + 1)}},
                   "result": {"reference_channel": 1, "ratios": ratios,
                              "channels": {k: {"mean_db": 0.0, "spread_db": 0.0, "std_db": 0.0, "n": 5}
                                           for k in list(ratios)[1:]}},
                   "take": {"wav": {"file": os.path.basename(w), "sha256": capture.sha256(w)}}}, open(p, "w"))
    return capture.load_calibration(p)


# Synthetic fixtures declare their constructed track explicitly (2026-10-19 policy): track 1 on MIDI channel 1,
# triggered by channel 16 note 36, one preset message; the keyboard note is unknown, as capture.py records it.
SYNTHETIC_TRACK = "synthetic test fixture: constructed track 1, nothing was played on a unit"
SYNTHETIC_SENT = [{"name": "Level", "kind": "cc", "channel": 1, "number": 29, "value": 100, "sent": True}]


def sidecar(path, source, planned, kind="note_on", status="complete", duration=12.0, calibration="unity",
            channel_map=None):
    """Capture sidecar bound to <stem>.wav as it is now (hash, channels, map), like capture.py writes it; path b (the
    recorder's own SD card: the schedule offset is not bounded). calibration: "unity" (an accepted all-1 recorder-chain
    calibration, so the existing tests see unchanged samples), None (none), or a block from capture.load_calibration."""
    ev = [{"i": k, "hit": k, "kind": kind, "planned_s": p, "bytes": ""} for k, p in enumerate(planned)]
    wav = os.path.splitext(path)[0] + ".wav"
    nch = wavio.read_info(wav)["channels"] if os.path.exists(wav) else 2
    if calibration == "unity":
        calibration = unity_calibration(os.path.dirname(path), nch)
    json.dump({"schema": capture.SCHEMA, "path": "b", "status": status, "source": {"name": source},
               "capture": {"rate": RATE, "channels": nch,
                           "channel_map": channel_map or {str(c): f"in {c}" for c in range(1, nch + 1)}},
               "wav": {"partial": False, "sha256": capture.sha256(wav) if os.path.exists(wav) else None},
               "stimulus": {"track": 1, "midi_channel": 16, "note": 36, "events": ev, "duration_s": duration},
               "settings": {"preset": "reference", "track_channel": 1, "sent": SYNTHETIC_SENT},
               "keyboard_note": {"state": "unknown", "why": SYNTHETIC_TRACK}, "synthetic": SYNTHETIC_TRACK,
               "timing_reference": {"kind": "none", "channel": None}, "calibration": calibration}, open(path, "w"))


def offsets(base_s, jitter):
    return [round((base_s + 2.0 * k) * RATE) + j for k, j in enumerate(jitter)]


def take(d, name, onsets, gains, planned=(2.0, 4.0, 6.0), source="triangle-positive", **kw):
    p, s = os.path.join(d, name + ".wav"), os.path.join(d, name + ".json")
    synth(p, onsets, gains, **kw)
    sidecar(s, source, list(planned), kind="marker" if source == "no-note" else "note_on")
    return p, s


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return e
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# ---------------------------------------------------------------- wavio (fix B)

def test_wavio_roundtrip(d):
    sig = [[0.5, -0.5, 0.25, -1.0, 0.999], [0.0, 0.1, -0.1, 0.2, -0.2]]
    for st, bits, tol in (("pcm", 16, 2 / 32768), ("pcm", 24, 2 / 8388608), ("pcm", 32, 1e-9), ("float", 32, 1e-7)):
        p = os.path.join(d, f"rt-{st}{bits}.wav")
        wavio.write(p, RATE, sig, st, bits)
        info, back = wavio.read(p)
        assert (info["sample_type"], info["bits"], info["channels"], info["frames"]) == (st, bits, 2, 5), info
        for c in range(2):
            assert all(abs(a - b) <= tol for a, b in zip(sig[c], back[c])), (st, bits, back[c])
    raw = wavio.encode([0.5, -0.25], "float", 32)
    guid = struct.pack("<H", 3) + bytes.fromhex("000000001000800000aa00389b71")
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, RATE, RATE * 4, 4, 32, 22, 32, 4) + guid
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"bext" + struct.pack("<I", 3) + b"abc\0" \
        + b"data" + struct.pack("<I", len(raw)) + raw
    p = os.path.join(d, "ext.wav")
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    info, back = wavio.read(p)
    assert info["tag"] == 0xFFFE and info["sample_type"] == "float" and back == [[0.5, -0.25]], info
    assert info["chunks"] == ["fmt ", "bext", "data"]


def bad_wav(path, frames_claimed, frames_present, rate=RATE, nch=2, bits=24, extra=b""):
    block = nch * bits // 8
    hdr = wavio.header(rate, nch, "pcm", bits, frames_claimed * block)
    open(path, "wb").write(hdr + b"\0" * (frames_present * block) + extra)


def test_wavio_rejects_truncated_and_bad(d):
    p = os.path.join(d, "trunc.wav")
    bad_wav(p, 576000, 1)                                   # 12 s stereo-24 header, one frame present
    e = raises(wavio.WavError, wavio.read_info, p)
    assert "claims" in str(e), e
    bad_wav(p, 0, 0)
    open(p, "r+b").write(wavio.header(RATE, 2, "pcm", 24, 7) + b"\0" * 7)  # 7 bytes: not whole 6-byte frames
    assert "whole number" in str(raises(wavio.WavError, wavio.read_info, p))
    for rate, nch, bits in ((0, 2, 24), (RATE, 0, 24), (RATE, 2, 8), (RATE, 2, 20)):
        hdr = bytearray(wavio.header(RATE, 2, "pcm", 24, 6))
        struct.pack_into("<HHIIHH", hdr, 20, 1, nch, rate, rate * max(1, nch * bits // 8), max(1, nch * bits // 8),
                         bits)
        open(p, "wb").write(bytes(hdr) + b"\0" * 6)
        raises(wavio.WavError, wavio.read_info, p)
    open(p, "wb").write(wavio.header(RATE, 2, "pcm", 24, 6) + b"\0" * 6 + b"LIS")   # truncated chunk header
    assert "truncated chunk header" in str(raises(wavio.WavError, wavio.read_info, p))
    bad_wav(p, 10, 10)
    assert wavio.read_info(p)["frames"] == 10


# ---------------------------------------------------------------- alignment, levels, floor (fix C, answer 2)

def test_alignment_and_metrics(d):
    shift, jitter, gains = 0.3137, [0, 37, -21], [1.0, 0.9, 1.1]
    p, s = take(d, "pos", offsets(2.0 + shift, jitter), gains)
    r = ca.analyse(p, s)
    assert r["matched_hits"] == 3 and not r["unmatched_onsets"] and r["expected_hits"] == 3, r["onsets"]
    assert r["hits_complete"] and r["capture"]["valid"], r["capture"]
    assert abs(r["schedule_offset_samples"] - (round(shift * RATE) + DET)) <= 1, r["schedule_offset_samples"]
    res = [h["residual_samples"] for h in r["hits"]]
    assert all(abs(a - b) <= 1 for a, b in zip(res, jitter)), res
    assert abs(r["jitter"]["max_abs_ms"] - 37 / 48) < 0.03, r["jitter"]
    assert r["detection"]["window_s"] == 0.2 and r["detection"]["pre_ms"] == 2.0 and r["detection"]["post_ms"] == 198
    for h in r["hits"]:
        assert h["window"]["end"] - h["window"]["start"] == 9600 and h["window"]["start"] == h["onset_sample"] - 96
    # answer 2: the three raw hit levels are reported and compared
    lv = r["levels"][0]
    assert all(abs(pk / (0.25 * 2 ** 23) - g) < 0.01 * g for pk, g in zip(lv["peak_raw_abs"], gains)), lv
    assert abs(lv["peak_spread_db"] - 20 * math.log10(1.1 / 0.9)) < 0.05, lv
    assert abs(lv["peak_db_rel_hit0"][1] - 20 * math.log10(0.9)) < 0.05
    assert all(h["channels"][0]["attack_sign"] == 1 and h["channels"][0]["full_scale_samples"] == 0
               for h in r["hits"])
    for pr in r["pairwise_diagnostics"]:
        assert pr["scope"].startswith("diagnostic") and pr["cosine_ge_0.99"] and pr["best_lag"] == 0, pr
        assert abs(pr["gain_b_to_a"] - gains[pr["a"]] / gains[pr["b"]]) < 0.01, pr
    assert "all_pairs_meet_0.99" not in r                 # no take-level verdict from per-pair fits
    # host send times replace planned times: a 5 ms late send is not counted as jitter
    m = json.load(open(s))
    for k, e in enumerate(m["stimulus"]["events"]):
        e["host_send_start_ns"] = int(e["planned_s"] * 1e9) + (5_000_000 if k == 1 else 0)
    json.dump(m, open(s, "w"))
    synth(p, offsets(2.0 + shift, [0, 240, 0]), gains)
    r = ca.analyse(p, s)
    assert all(abs(h["residual_samples"]) <= 1 for h in r["hits"]), [h["residual_samples"] for h in r["hits"]]


def test_codex_repro_silence_floor(d):
    """Exact 2/4/6 s hits, digital-silent lead; the floor was measured over the whole file (-12.04 dBFS)."""
    p, s = take(d, "x", offsets(2, [0, 0, 0]), [1, 1, 1], noise=0)
    r = ca.analyse(p, s, window_s=.2)
    sf = r["silence_floor"]
    assert sf["available"] and sf["peak"] == 0 and sf["peak_dbfs"] is None, sf
    assert sf["region_samples"][1] == r["onsets"][0] - int(0.1 * RATE) < 2 * RATE, sf


def test_silence_floor_offsets(d):
    # zero offset, noise -100 dBFS: region ends before the first hit and measures only noise
    p, s = take(d, "z", offsets(2, [0, 0, 0]), [1, 1, 1], noise=1e-5)
    sf = ca.analyse(p, s)["silence_floor"]
    assert sf["available"] and sf["region_samples"][1] == 2 * RATE + DET - 4800 and sf["peak_dbfs"] < -80, sf
    # negative offset: hits 0.5 s earlier than planned
    p, s = take(d, "n", offsets(1.5, [0, 0, 0]), [1, 1, 1])
    r = ca.analyse(p, s)
    assert r["matched_hits"] == 3 and r["schedule_offset_samples"] < 0
    assert r["silence_floor"]["region_samples"][1] == int(1.5 * RATE) + DET - 4800 and \
        r["silence_floor"]["peak_dbfs"] < -80, r["silence_floor"]
    # small offset: first hit 50 ms into the file -> no pre-hit region; never the signal instead
    p, s = take(d, "s", offsets(0.05, [0, 0, 0]), [1, 1, 1], planned=(0.0, 2.0, 4.0))
    r = ca.analyse(p, s)
    assert r["matched_hits"] == 3 and r["silence_floor"] == {
        "available": False, "reason": "no signal-free region before the first onset/expected hit"}, r["silence_floor"]
    assert not r["hits"][0]["tail"]["thresholds"]["floor+10dB"]["available"]
    # first hit window starts before the file -> incomplete, not padded
    p, s = take(d, "e", [50] + offsets(2.0, [0, 0])[0:2], [1, 1, 1], planned=(0.0, 2.0, 4.0))
    r = ca.analyse(p, s)
    assert r["matched_hits"] == 3 and not r["hits"][0]["complete"] and not r["hits_complete"]


# ---------------------------------------------------------------- tails (answers 6, 7)

def test_tail_figures(d):
    p, s = take(d, "short", offsets(2, [0, 0, 0]), [1, 1, 1], kind="short", noise=1e-5)
    r = ca.analyse(p, s)
    h = r["hits"][0]
    t = h["tail"]["thresholds"]
    assert not r["signal_past_window"] and "window_note" not in r
    assert h["tail"]["ends_at"] == "next onset" and r["hits"][2]["tail"]["ends_at"] == "end of file"
    assert 85 <= t["-60dBFS"]["last_crossing_ms"] <= 130 and not t["-60dBFS"]["censored"], t["-60dBFS"]
    assert t["-60dBFS"]["sustained_below_ms"] <= t["-60dBFS"]["last_crossing_ms"] + 1
    assert t["-60dBFS"]["noise_limited"] is False and t["floor+10dB"]["available"]
    # long tail (-39 dBFS at 198 ms): flagged, not cropped
    p, s = take(d, "long", offsets(2, [0, 0, 0]), [1, 1, 1], noise=1e-5)
    r = ca.analyse(p, s)
    assert r["signal_past_window"] and "extended window must be agreed" in r["window_note"]
    assert r["hits"][0]["tail"]["thresholds"]["-60dBFS"]["last_crossing_ms"] > 198
    assert r["hits"][0]["tail"]["region_ms"] > 1900                       # the full tail is analysed
    # noise at about -64 dBFS block peak: -80 dBFS lies below the floor -> noise-limited, and censored
    p, s = take(d, "noisy", offsets(2, [0, 0, 0]), [1, 1, 1], kind="short", noise=2e-4)
    t = ca.analyse(p, s)["hits"][0]["tail"]["thresholds"]
    assert t["-80dBFS"]["noise_limited"] and t["-80dBFS"]["censored"], t["-80dBFS"]
    assert t["floor+10dB"]["noise_limited"] is False
    # sustained tone reaching the next hit: censored at every threshold
    # (20 s file so the tone occupies under half of it: the detection floor is a median of 1 ms block peaks)
    p, s = take(d, "sus", offsets(2, [0, 0, 0]), [1, 1, 1], kind="sustain", seconds=20.0)
    r = ca.analyse(p, s)
    assert r["matched_hits"] == 3, r["onsets"]
    assert all(v["censored"] for v in r["hits"][0]["tail"]["thresholds"].values() if v["available"])
    # raw residual in integer codes: DC of 100 codes in 24-bit
    p, s = take(d, "dc", offsets(2, [0, 0, 0]), [1, 1, 1], kind="short", noise=0, dc=100 / 2 ** 23)
    rr = ca.analyse(p, s, abs_floor_db=-60)["hits"][0]["tail"]["raw_residual"][0]
    assert rr["final_20ms_mean_raw"] == 100 and rr["final_sample_raw"] == 100 and rr["final_20ms_peak_abs_raw"] == 100


# ---------------------------------------------------------------- comparisons (fixes D, E)

def test_codex_repro_cosine_only(d):
    """Cosine .995 passes the shape flag but NRMSE is .0999 after optimal gain."""
    c = ca.compare([[1., 0.]], 0, [[.995, (1 - .995 ** 2) ** .5]], 0, 0, 2, 0)
    assert c["cosine_ge_0.99"] and abs(c["nrmse_after_gain"] - 0.0998749) < 1e-6, c
    assert not c["nrmse_le_0.05"] and not c["shape_and_level_ok"] and c["scope"].startswith("diagnostic")


def test_codex_repro_rate_mismatch(d):
    """against() accepted a 44.1 kHz analysis against a 48 kHz one."""
    p, s = take(d, "x", offsets(2, [0, 0, 0]), [1, 1, 1], noise=0)
    r = ca.analyse(p, s, window_s=.2)
    b = dict(r)
    b["format"] = dict(r["format"], rate=44100)
    assert "sample rates differ" in str(raises(ValueError, ca.against, r, b, 0))
    assert "sample rates differ" in str(raises(ValueError, ca.against, r, b, 0, [(1, 1)]))
    assert "explicit channel pairs" in str(raises(ValueError, ca.against, r, r, 0))
    assert "outside" in str(raises(ValueError, ca.whole_take, r, r, [(1, 3)]))


def test_whole_take_verdict(d):
    # "short" hits decay inside the 198 ms window: signal past it would block the verdict (tested separately)
    a = ca.analyse(*take(d, "A", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short"))
    pairs = [(1, 1), (2, 2)]
    # B: same take at half level, shifted by a constant: schedule positions + per-hit lags of 0 + one gain -> pass
    b = ca.analyse(*take(d, "B", offsets(2.7, [0, 0, 0]), [0.5, 0.5, 0.5], seed=2, kind="short"))
    w = ca.whole_take(a, b, pairs)
    assert w["pass"] and w["cosine"] > 0.9999 and w["nrmse"] < 0.01 and abs(w["gain_b_to_a"] - 2) < 0.01, w
    assert [p["schedule_shift_samples"] for p in w["per_hit"]] == [round(0.4 * RATE)] * 3, w["per_hit"]
    assert all(abs(x) < 0.01 for x in w["lags_samples"]) and w["scope"].startswith("whole-take"), w
    # per-hit retiming in B by 30 samples (0.625 ms, inside the +-1 ms bound): fitted and reported, not failed
    bj = ca.analyse(*take(d, "Bj", offsets(2.7, [0, 30, 0]), [1, 1, 1], seed=3, kind="short"))
    w = ca.whole_take(a, bj, pairs)
    assert w["pass"] and abs(w["lags_samples"][1] - 30) < 0.01 and abs(w["lag_spread_ms"] - 0.625) < 0.001, w
    assert w["per_hit"][1]["integer_lag_samples"] == 30 and not w["per_hit"][1]["at_bound"], w["per_hit"]
    assert all(x["cosine_ge_0.99"] for x in ca.against(a, bj, 32, pairs))
    # retimed by 90 samples (1.9 ms): outside the bound; the periodic fixture's nearest match is one period away
    bk = ca.analyse(*take(d, "Bk", offsets(2.7, [0, 90, 0]), [1, 1, 1], seed=3, kind="short"))
    assert not ca.whole_take(a, bk, pairs)["pass"] and not ca.whole_take(bk, a, pairs)["pass"]
    # time-varying level (hit 2 at 0.8): cosine passes, NRMSE fails -> joint criterion fails
    bl = ca.analyse(*take(d, "Bl", offsets(2.7, [0, 0, 0]), [1, 1, 0.8], seed=4, kind="short"))
    w = ca.whole_take(a, bl, pairs)
    assert w["cosine"] >= 0.99 and w["nrmse"] > 0.05 and not w["pass"], w
    # polarity inverted
    bi = ca.analyse(*take(d, "Bi", offsets(2.7, [0, 0, 0]), [-1, -1, -1], seed=5, kind="short"))
    w = ca.whole_take(a, bi, pairs)
    assert w["cosine"] < -0.999 and all(abs(x) < 0.01 for x in w["lags_samples"]) and not w["pass"], w
    assert all(x["cosine_best"] < -0.9999 for x in ca.against(a, bi, 32, pairs))
    # invalid capture (clipping) -> no pass even with identical audio
    bc = ca.analyse(*take(d, "Bc", offsets(2.3, [0, 0, 0]), [1, 1, 1], shapes={0: "square"}))
    pc, sc = take(d, "Ac", offsets(2.3, [0, 0, 0]), [4, 1, 1])                # 4 x 0.25 = full scale
    ac = ca.analyse(pc, sc)
    assert not ac["capture"]["valid"] and "full-scale" in ac["capture"]["reasons"][0]
    assert not ca.whole_take(ac, bc, pairs)["pass"]
    # incomplete status in the sidecar -> invalid
    p, s = take(d, "As", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short")
    sidecar(s, "triangle-positive", [2.0, 4.0, 6.0], status="error")
    assert not ca.whole_take(ca.analyse(p, s), b, pairs)["pass"]


def test_truncated_windows_not_padded(d):
    a = ca.analyse(*take(d, "A2", offsets(2.3, [0, 0, 0]), [1, 1, 1]))
    # B ends 50 ms after its last onset: that window cannot be complete
    pb, sb = os.path.join(d, "Bt.wav"), os.path.join(d, "Bt.json")
    synth(pb, offsets(2.3, [0, 0, 0]), [1, 1, 1], seconds=6.3 + 0.05, seed=7)
    sidecar(sb, "triangle-positive", [2.0, 4.0, 6.0], duration=6.35)
    b = ca.analyse(pb, sb)
    assert b["matched_hits"] == 3 and not b["hits"][2]["complete"] and not b["hits_complete"]
    assert not b["hits"][2]["channels"][0]["complete"]
    w = ca.whole_take(a, b, [(1, 1), (2, 2)])
    assert not w["pass"] and any("not all present and complete" in x for x in w["reasons"]), w
    per = {x["hit"]: x for x in ca.against(a, b, 32, [(1, 1), (2, 2)])}
    assert per[2]["complete"] is False and "cosine_best" not in per[2] and per[0]["complete"]


def test_against_cli(d):
    pos, ps = take(d, "p", offsets(2.2, [0, 5, -3]), [1, 1, 1], seed=2)
    neg, ns = take(d, "n", offsets(2.9, [2, -4, 0]), [-1, -1, -1], seed=3, source="triangle-negative")
    out = os.path.join(d, "pn.align.json")
    raises(SystemExit, ca.main, [pos, "--against", neg, "--against-sidecar", ns, "--out", out])   # no --pairs
    assert ca.main([pos, "--against", neg, "--against-sidecar", ns, "--pairs", "1=1,2=2", "--out", out,
                    "--segments", os.path.join(d, "seg")]) == 1                       # failing verdict: exit 1
    rep = json.load(open(out))
    assert all(h["cosine_best"] < -0.9999 for h in rep["against"]["per_hit_diagnostics"])
    assert rep["against"]["whole_take"]["pass"] is False and "definitions" in rep
    assert sorted(os.listdir(os.path.join(d, "seg"))) == ["hit0.wav", "hit1.wav", "hit2.wav"]
    assert wavio.read_info(os.path.join(d, "seg", "hit0.wav"))["frames"] == 9600


def test_shape_difference(d):
    p, s = take(d, "shape", offsets(2.0, [0, 0, 0]), [1, 1, 1], shapes={2: "square"})
    pr = {(x["a"], x["b"]): x for x in ca.analyse(p, s)["pairwise_diagnostics"]}
    assert pr[(0, 1)]["cosine_ge_0.99"] and not pr[(0, 2)]["cosine_ge_0.99"], pr


def test_silence_controls(d):
    p, s = take(d, "quiet", [], [], noise=3e-5, source="no-note")
    r = ca.analyse(p, s)
    assert r["onsets"] == [] and r["matched_hits"] == 0 and r["control_silent"] and r["expected_hits"] == 0, r
    assert r["hits_complete"] and r["silence_floor"]["available"] and -100 < r["silence_floor"]["rms_dbfs"] < -85
    synth(p, [3 * RATE], [1.0], noise=3e-5)
    r = ca.analyse(p, s)
    assert not r["control_silent"] and r["onsets"] and not r["hits_complete"], r
    wavio.write(p, RATE, [[0.0] * RATE, [0.0] * RATE])
    r = ca.analyse(p, s)
    assert r["onsets"] == [] and r["silence_floor"]["peak_dbfs"] is None


# ---------------------------------------------------------------- verdict gates (second review)

def edit_meta(s, **capture_fields):
    m = json.load(open(s))
    m["capture"].update(capture_fields)
    json.dump(m, open(s, "w"))


def test_codex_gate_recorder_faults(d):
    """Item 1: overruns/xruns, pump errors and short recordings invalidate a take; the text is kept."""
    p, s = take(d, "A", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short")
    a = ca.analyse(p, s)
    assert a["capture"]["valid"] and ca.whole_take(a, a, [(1, 1), (2, 2)])["pass"]
    for fields, needle in (({"recorder_stderr": "overrun!!! (at least 100 ms long)"}, "overrun!!!"),
                           ({"recorder_stderr": "arecord: xrun detected"}, "xrun"),
                           ({"pump_error": "OSError(5, 'Input/output error')"}, "Input/output error"),
                           ({"frames": 1000, "frames_wanted": 576000}, "short recording"),
                           ({"warning": "short recording: 10 of 576000 frames"}, "capture warning")):
        p2, s2 = take(d, "F", offsets(2.3, [0, 0, 0]), [1, 1, 1])
        edit_meta(s2, **fields)
        b = ca.analyse(p2, s2)
        assert not b["capture"]["valid"] and any(needle in x for x in b["capture"]["reasons"]), (fields, b["capture"])
        w = ca.whole_take(a, b, [(1, 1)], max_lag=0)
        assert not w["pass"] and any(needle in x for x in w["reasons"]), w
    # file shorter than the planned duration
    p3, s3 = take(d, "S", offsets(2.3, [0, 0, 0]), [1, 1, 1], seconds=9.0)
    assert any("planned 12.0 s" in x for x in ca.analyse(p3, s3)["capture"]["reasons"])


def test_codex_gate_silence_with_notes(d):
    """Item 2: 'silence' (notes sent, silent fixture) is a silent-audio control like 'no-note'."""
    p, s = take(d, "sil", [], [], source="silence")
    r = ca.analyse(p, s)
    assert r["expected_hits"] == 0 and r["control_silent"] is True and r["hits_complete"], r
    assert r["silence_floor"]["available"] and r["silence_floor"]["region_samples"] == [0, 12 * RATE]
    assert r["stimulus_notes"]["note_on_events"] == 3 and r["capture"]["valid"]
    assert r["matched_hits"] == 0 and r["unexplained_onsets"] == []
    synth(p, [3 * RATE], [1.0], noise=3e-5)                  # something audible in the silence take
    r = ca.analyse(p, s)
    assert r["control_silent"] is False and not r["hits_complete"] and r["matched_hits"] == 0
    assert r["unexplained_onsets"] == r["onsets"] != []
    assert r["silence_floor"]["region_samples"][1] == r["onsets"][0] - int(0.1 * RATE)
    w = ca.whole_take(r, r, [(1, 1)])
    assert w["applicable"] is False and not w["pass"]
    nn = ca.analyse(*take(d, "nn", [], [], source="no-note"))
    assert nn["control_silent"] and nn["stimulus_notes"]["note_on_events"] == 0


def test_codex_gate_extra_onset(d):
    """Item 3: an unexplained extra onset fails the verdict."""
    p, s = take(d, "A", offsets(2.3, [0, 0, 0]), [1, 1, 1])
    a = ca.analyse(p, s)
    p2, s2 = take(d, "extra", offsets(2.3, [0, 0, 0]) + [9 * RATE], [1, 1, 1, 1])
    b = ca.analyse(p2, s2)
    assert b["unexplained_onsets"] == [9 * RATE + DET] and b["hits_complete"]
    w = ca.whole_take(a, b, [(1, 1)], max_lag=0)
    assert not w["pass"] and any("unexplained extra onsets" in x for x in w["reasons"]), w
    w = ca.whole_take(b, a, [(1, 1)], max_lag=0)
    assert not w["pass"] and any("take A: unexplained" in x for x in w["reasons"])


def synthetic_render_track():
    """The render fixtures' constructed track: the capture fixtures' track 1 and preset message (applied), and a
    keyboard note stated as constructed (render vs capture keeps the measured keyboard-note requirement)."""
    return {"number": 1, "track_midi_channel": 1, "trigger": {"midi_channel": 16, "note": 36}, "preset": "reference",
            "preset_messages": [{**{k: v for k, v in m.items() if k != "sent"}, "applied": True}
                                for m in SYNTHETIC_SENT],
            "keyboard_note": {"state": "measured", "semitones": 0.0,
                              "evidence": "synthetic test fixture: constructed at 0 semitones, not measured on a unit"},
            "synthetic": SYNTHETIC_TRACK}


def render_json(path, wav, frames=None, **over):
    m = {"schema": ca.RENDER_SCHEMA, "source": {"name": "triangle-positive"}, "rate": RATE, "channels": 2,
         "channel_map": {"1": "Y:0x21 (unverified)", "2": "Y:0x22 (unverified)"}, "duration_s": 12.0,
         "events": [{"hit": k, "frame": f} for k, f in enumerate(frames or [])],
         "wav_sha256": capture.sha256(wav), "status": "complete",
         "provenance": {"tool": "tools/dsp/test_sample_scene.py", "commit": "deadbeef",
                        "inputs": {"manifest.json": "sha256:..."}},
         "track": synthetic_render_track()}
    m.update(over)
    json.dump(m, open(path, "w"))


def test_codex_gate_metadata_required(d):
    """Item 4: no verdict without validated metadata on both sides; render provenance adapter for emulator output."""
    p, s = take(d, "A", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short")
    a = ca.analyse(p, s)
    b = ca.analyse(p, refractory_s=1.0)        # identical audio, no metadata (no schedule: refractory given)
    assert not b["capture"]["valid"] and b["capture"]["verdict_eligible"] is False and b["expected_hits"] is None
    w = ca.whole_take(a, b, [(1, 1)], max_lag=0)
    assert not w["pass"] and any("no validated schedule" in x for x in w["reasons"]), w
    assert all(x["cosine_ge_0.99"] for x in ca.against(a, b, 0, [(1, 1)]))   # diagnostics still allowed
    # unknown schema is not a sidecar
    bad = os.path.join(d, "bad.json")
    json.dump({"schema": "something-else", "status": "complete"}, open(bad, "w"))
    assert any("unknown metadata schema" in x for x in ca.analyse(p, bad)["capture"]["reasons"])
    # render provenance: an emulator output judged without trusting unknown status
    emu = os.path.join(d, "emu.wav")
    synth(emu, offsets(2.0, [0, 0, 0]), [0.5, 0.5, 0.5], noise=0, kind="short")
    rj = os.path.join(d, "emu.render.json")
    render_json(rj, emu, frames=[2 * RATE, 4 * RATE, 6 * RATE])
    e = ca.analyse(emu, rj)
    assert e["capture"]["valid"] and e["capture"]["kind"] == "render" and e["hits_complete"], e["capture"]
    w = ca.whole_take(a, e, [(1, 1), (2, 2)])
    assert w["pass"] and abs(w["gain_b_to_a"] - 2) < 1e-3, w
    for over, needle in (({"provenance": {"tool": "x"}}, "provenance.commit"), ({"wav_sha256": "0" * 64}, "wav_sha256"),
                         ({"rate": 44100}, "rate"), ({"status": "partial"}, "status"), ({"events": []}, "no events"),
                         ({"duration_s": None}, "duration_s")):
        render_json(rj, emu, frames=[2 * RATE, 4 * RATE, 6 * RATE], **over)
        r = ca.analyse(emu, rj)
        assert not r["capture"]["valid"] and any(needle in x for x in r["capture"]["reasons"]), (over, r["capture"])
        assert not ca.whole_take(a, r, [(1, 1)])["pass"]
    render_json(rj, emu, events=[{"hit": k, "time_s": t} for k, t in enumerate((2.0, 4.0, 6.0))])
    assert ca.analyse(emu, rj)["capture"]["valid"]
    # CLI: B's adjacent metadata is discovered like A's (.json, then .render.json)
    assert ca.find_sidecar(emu) == rj and ca.find_sidecar(p) == s
    out = os.path.join(d, "ae.align.json")
    assert ca.main([p, "--against", emu, "--pairs", "1=1,2=2", "--out", out]) == 0
    rep = json.load(open(out))
    assert rep["against"]["whole_take"]["pass"] and rep["sidecar"] == os.path.abspath(s)
    os.remove(rj)
    assert ca.main([p, "--against", emu, "--pairs", "1=1,2=2", "--out", out]) == 1      # failing verdict: exit 1
    assert json.load(open(out))["against"]["whole_take"]["pass"] is False


def test_codex_hitsets_symmetric(d):
    """Third review item 1: hit-ID sets must be equal on both sides, in both directions; duplicates rejected."""
    a = ca.analyse(*take(d, "ha", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short"))
    b = ca.analyse(*take(d, "hb", offsets(2.3, [0, 0, 0]) + [9 * RATE], [1, 1, 1, 1],
                         planned=[2, 4, 6, 8.7]))
    assert b["expected_hits"] == 4 and b["hits_complete"] and b["unexplained_onsets"] == [], b["unexplained_onsets"]
    for x, y in ((a, b), (b, a)):
        w = ca.whole_take(x, y, [(1, 1)], 0)
        assert not w["pass"] and any("expected hit ids differ" in r for r in w["reasons"]), w
        assert any("matched hit ids differ" in r for r in w["reasons"])
    assert ca.whole_take(a, a, [(1, 1), (2, 2)])["pass"]
    # duplicate hit ids (declared in a render) are refused before any id-keyed map is built
    emu = os.path.join(d, "dup.wav")
    synth(emu, offsets(2.0, [0, 0, 0]), [1, 1, 1], noise=0)
    rj = os.path.join(d, "dup.render.json")
    render_json(rj, emu, events=[{"hit": h, "frame": f} for h, f in zip((0, 0, 1), (2 * RATE, 4 * RATE, 6 * RATE))])
    e = ca.analyse(emu, rj)
    assert any("duplicate hit ids [0]" in r for r in e["capture"]["reasons"]), e["capture"]
    for x, y in ((a, e), (e, a)):
        w = ca.whole_take(x, y, [(1, 1)], 0)
        assert not w["pass"] and any("duplicate" in r for r in w["reasons"]), w


def test_codex_render_bounds(d):
    """Third review item 2: render provenance values are validated, not just present."""
    p, s = take(d, "A", offsets(2.3, [0, 0, 0]), [1, 1, 1])
    a = ca.analyse(p, s)
    emu = os.path.join(d, "emu.wav")
    synth(emu, offsets(2.0, [0, 0, 0]), [0.5, 0.5, 0.5], noise=0)
    rj = os.path.join(d, "emu.render.json")
    good = [2 * RATE, 4 * RATE, 6 * RATE]
    render_json(rj, emu, frames=good)
    assert ca.analyse(emu, rj)["capture"]["valid"]
    cases = (
        ({"duration_s": -1}, "positive finite"),
        ({"events": [{"hit": k, "time_s": t} for k, t in enumerate((20.0, 22.0, 24.0))]}, "outside"),
        ({"channel_map": {"99": "imaginary"}}, "channel_map keys"),
        ({"duration_s": float("nan")}, "positive finite"),
        ({"duration_s": 10.0}, "!= file length"),
        ({"events": [{"hit": 0, "frame": 2.5 * RATE}, {"hit": 1, "frame": 4 * RATE}, {"hit": 2, "frame": 6 * RATE}]},
         "not an integer"),
        ({"events": [{"hit": 0, "time_s": float("nan")}]}, "not finite"),
        ({"events": [{"hit": 0, "frame": -1}]}, "outside"),
        ({"events": [{"hit": "x", "frame": RATE}]}, "not a non-negative integer"),
        ({"channel_map": {"1": "L"}}, "channel_map keys"),
        ({"channel_map": {"1": "L", "2": "R", "3": "x"}}, "channel_map keys"),
        ({"channel_map": {"1": "L", "2": " "}}, "empty label"),
        ({"channel_map": ["L", "R"]}, "not an object"),
        ({"events": "frames"}, "not a list"),
    )
    for over, needle in cases:
        render_json(rj, emu, frames=good, **over)
        r = ca.analyse(emu, rj)
        assert not r["capture"]["valid"] and any(needle in x for x in r["capture"]["reasons"]), (over, r["capture"])
        assert not ca.whole_take(a, r, [(1, 1)])["pass"], over
    # capture sidecars: the same schedule bounds where they apply
    for planned, dur, needle in (([2.0, 4.0, 13.0], 12.0, "outside"), ([2.0, 4.0, 6.0], -1, "positive finite"),
                                 ([2.0, float("nan"), 6.0], 12.0, "outside"), ([2.0, 4.0, 6.0], 20.0, "short recording")):
        p2, s2 = take(d, "B", offsets(2.3, [0, 0, 0]), [1, 1, 1])
        sidecar(s2, "triangle-positive", planned, duration=dur)
        r = ca.analyse(p2, s2)
        assert not r["capture"]["valid"] and any(needle in x for x in r["capture"]["reasons"]), (planned, dur,
                                                                                                r["capture"])
    p3, s3 = take(d, "C", offsets(2.3, [0, 0, 0]), [1, 1, 1])
    m = json.load(open(s3))
    m["stimulus"]["events"][2]["hit"] = 0
    json.dump(m, open(s3, "w"))
    assert any("duplicate hit ids" in x for x in ca.analyse(p3, s3)["capture"]["reasons"])


# ---------------------------------------------------------------- fourth review (independent reviewer)

NAN, INF = float("nan"), float("inf")
BOTH = [(1, 1), (2, 2)]
FRAMES = [2 * RATE + 1, 4 * RATE + 1, 6 * RATE + 1]


def short_take(d, name, **kw):
    """Valid A side: hits at 2/4/6 s (+1 sample) that decay inside the 198 ms window."""
    return take(d, name, offsets(2.0, [0, 0, 0]), [1, 1, 1], kind="short", **kw)


def clean_short(d):
    """short_take's audio without its analog floor (digital silence between hits): the source of render-side
    channels, since a render whose floor looks analog is not a render (round 4)."""
    p = os.path.join(d, "clean-short.wav")
    synth(p, offsets(2.0, [0, 0, 0]), [1, 1, 1], kind="short", noise=0)
    return p


def render(d, name, chans, frames=FRAMES, **over):
    """B side: an emulator-style float32 render with its provenance written after the audio."""
    p, rj = os.path.join(d, name + ".wav"), os.path.join(d, name + ".render.json")
    wavio.write(p, RATE, chans, "float", 32)
    render_json(rj, p, frames=frames, **over)
    return p, rj


def both_fail(a, b, needle, pairs=BOTH):
    for x, y, pr in ((a, b, pairs), (b, a, [(q, p) for p, q in pairs])):
        w = ca.whole_take(x, y, pr, 32)
        assert not w["pass"] and any(needle in r for r in w["reasons"]), (needle, w["reasons"])
        for k in ("cosine", "nrmse", "gain_b_to_a"):           # a NaN is never reported as a figure
            assert w.get(k) is None or math.isfinite(w[k]), (k, w)


def test_review_nonfinite_samples(d):
    """Finding 1: a NaN/Inf sample invalidates the take (A and B, float captures too); NaN never passes a gate."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    assert ca.whole_take(a, ca.analyse(*render(d, "same", ch)), BOTH)["pass"]
    for bad in (NAN, INF, -INF):
        L = list(ch[0])
        L[FRAMES[0] + 500] = bad
        b = ca.analyse(*render(d, "B", [L, ch[1]]))
        assert not b["capture"]["valid"] and "1 non-finite samples (NaN/Inf)" in b["capture"]["reasons"], b["capture"]
        both_fail(a, b, "non-finite")
    # a 32-bit float capture (capture sidecar) with a NaN
    pf, sf = os.path.join(d, "F.wav"), os.path.join(d, "F.json")
    L = list(ch[0])
    L[FRAMES[1] + 7] = NAN
    wavio.write(pf, RATE, [L, ch[1]], "float", 32)
    sidecar(sf, "triangle-positive", [2.0, 4.0, 6.0])
    f = ca.analyse(pf, sf)
    assert not f["capture"]["valid"] and f["capture"]["nonfinite_samples"] == 1
    both_fail(a, f, "non-finite")
    # thresholds are written so that NaN fails; the lag search never keeps a NaN
    c = ca.compare([[NAN, 1.0]], 0, [[1.0, 1.0]], 0, 0, 2, 1)
    assert not c["cosine_ge_0.99"] and not c["nrmse_le_0.05"] and not c["shape_and_level_ok"], c
    assert math.isnan(ca.dot([INF, -INF], [1.0, 1.0]))                  # fsum would raise: NaN instead
    # an all-NaN silence control is neither silent nor valid
    nan = [[NAN] * (12 * RATE)] * 2
    cn = ca.analyse(*render(d, "C", nan, frames=[], source={"name": "silence"}))
    assert cn["control_silent"] is False and not cn["capture"]["valid"] and not cn["hits_complete"], cn["capture"]
    # the CLI report is strict JSON: no bare NaN/Infinity
    L = list(ch[0])
    L[FRAMES[0] + 500] = NAN
    pb, _ = render(d, "Bn", [L, ch[1]])
    out = os.path.join(d, "n.align.json")
    assert ca.main([pa, "--against", pb, "--pairs", "1=1,2=2", "--out", out]) == 1
    json.loads(open(out).read(), parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))


def test_review_signal_past_window(d):
    """Finding 2: signal past 198 ms on either side blocks the verdict; detection knobs cannot hide onsets."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    L, R = list(ch[0]), list(ch[1])
    for o in FRAMES:                       # 300 ms tone 400 ms after each onset, inside the 1 s re-arm time
        for i in range(int(0.3 * RATE)):
            v = 0.5 * math.sin(2 * math.pi * 1000 * i / RATE)
            L[o + int(0.4 * RATE) + i] += v
            R[o + int(0.4 * RATE) + i] += v
    b = ca.analyse(*render(d, "B", [L, R]))
    assert b["capture"]["valid"] and b["unexplained_onsets"] == [] and b["signal_past_window_hits"] == [0, 1, 2]
    w = ca.whole_take(a, b, BOTH)
    assert w["cosine"] > 0.999 and not w["pass"], w                   # the windows alone agree
    both_fail(a, b, "signal past the 198 ms window")
    assert any("take B: signal past" in r for r in w["reasons"])
    assert any("take A: signal past" in r for r in ca.whole_take(b, a, BOTH)["reasons"])
    # long tails on both sides (identical audio) are blocked too, until an extended window is agreed
    pl, sl = take(d, "long", offsets(2.0, [0, 0, 0]), [1, 1, 1])
    lg = ca.analyse(pl, sl)
    assert lg["signal_past_window"] and not ca.whole_take(lg, lg, BOTH)["pass"]
    # detection settings: refractory must be positive and finite; longer than half the spacing is a deviation
    for bad in (0, -1.0, NAN, INF):
        raises(ValueError, ca.analyse, pa, sa, refractory_s=bad)
    raises(ValueError, ca.detect_onsets, [0.0, 1.0], RATE, 0.5, -2.0)
    for kw, needle in (({"refractory_s": 1.5}, "refractory_s=1.5"), ({"threshold_db": 80.0}, "threshold_db=80.0"),
                       ({"abs_floor_db": -20.0}, "abs_floor_db"), ({"tolerance_ms": 500.0}, "tolerance_ms")):
        x = ca.analyse(pa, sa, **kw)
        assert any(needle in r for r in x["detection"]["deviations"]), x["detection"]
        both_fail(a, x, "detection deviations")
    ok = ca.analyse(pa, sa, refractory_s=0.5, threshold_db=10.0)           # more sensitive: allowed
    assert ok["detection"]["deviations"] == [] and ca.whole_take(a, ok, BOTH)["pass"]
    for extra in (["--refractory-s", "0"], ["--refractory-s", "nan"], ["--threshold-db", "inf"]):
        e = raises(SystemExit, ca.main, [pa, "--out", os.path.join(d, "x.json")] + extra)
        assert e.code == 2, extra


def test_review_sidecar_bound_to_wav(d):
    """Finding 3: a capture sidecar must match its WAV (hash, channels, channel map, rate); missing is a reason."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    assert a["capture"]["valid"], a["capture"]
    base = json.load(open(sa))
    cases = ((lambda m: m["wav"].__setitem__("sha256", "0" * 64), "wav.sha256 does not match"),
             (lambda m: m["wav"].pop("sha256"), "missing wav.sha256"),
             (lambda m: m["capture"].__setitem__("channels", 1), "capture.channels 1 != file 2"),
             (lambda m: m["capture"].pop("channels"), "capture.channels None"),
             (lambda m: m["capture"].__setitem__("channel_map", {"1": "L"}), "channel_map keys"),
             (lambda m: m["capture"].pop("channel_map"), "channel_map is not an object"),
             (lambda m: m["capture"].pop("rate"), "metadata rate None"),
             (lambda m: m["capture"].__setitem__("rate", 44100), "metadata rate 44100"))
    for mut, needle in cases:
        m = json.loads(json.dumps(base))
        mut(m)
        sb = os.path.join(d, "B.json")
        json.dump(m, open(sb, "w"))
        b = ca.analyse(pa, sb)
        assert not b["capture"]["valid"] and any(needle in r for r in b["capture"]["reasons"]), (needle, b["capture"])
        both_fail(a, b, needle)
    # the reviewer's case: another (mono) file under a stereo sidecar
    info, ch = wavio.read(pa)
    pm = os.path.join(d, "M.wav")
    wavio.write(pm, RATE, [ch[0]], "pcm", 24)
    mo = ca.analyse(pm, sa)
    assert {"sidecar: wav.sha256 does not match the file", "sidecar: capture.channels 2 != file 1"} <= set(
        mo["capture"]["reasons"]), mo["capture"]
    both_fail(a, mo, "wav.sha256", pairs=[(1, 1)])


def test_review_pairs_complete(d):
    """Finding 4: every analysed channel on each side in exactly one pair; mono vs stereo cannot pass."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    b = ca.analyse(*render(d, "B", [list(ch[0]), [-3 * v for v in ch[1]]]))      # R inverted and +9.5 dB
    both_fail(a, b, "(timing reference excepted) are [1, 2]", pairs=[(1, 1)])
    both_fail(a, b, "paired more than once", pairs=[(1, 1), (1, 1)])
    both_fail(a, b, "cosine")
    bm = ca.analyse(*render(d, "Bm", [list(ch[0])], channels=1, channel_map={"1": "Y:0x21"}))
    assert bm["capture"]["valid"], bm["capture"]
    w = ca.whole_take(a, bm, [(1, 1), (2, 1)])
    assert not w["pass"] and any("take B: channels [1] paired more than once" in r for r in w["reasons"]), w
    w = ca.whole_take(bm, a, [(1, 1), (1, 2)])
    assert not w["pass"] and any("take A: channels [1] paired more than once" in r for r in w["reasons"]), w
    w = ca.whole_take(a, bm, [(1, 1)])
    assert not w["pass"] and any("take A: pairs use channels [1]" in r for r in w["reasons"]), w
    w = ca.whole_take(bm, a, [(1, 1)])
    assert not w["pass"] and any("take B: pairs use channels [1]" in r for r in w["reasons"]), w
    assert ca.whole_take(a, ca.analyse(*render(d, "same", ch)), BOTH)["pass"]
    for bad in ("1=1,1=1", "1=1,2=1", "1=2,1=1"):
        e = raises(SystemExit, ca.parse_pairs, bad)
        assert e.code == 2, bad


def test_review_event_order(d):
    """Finding 5: out-of-order events are refused with a reason; no negative re-arm time, no runaway onset count."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    t0 = time.time()
    b = ca.analyse(*render(d, "B", ch, frames=[FRAMES[2], FRAMES[0], FRAMES[1]]))
    assert time.time() - t0 < 30 and len(b["onsets"]) == 3 and b["detection"]["refractory_s"] == 1.0, b["onsets"]
    assert any("strictly increasing" in r for r in b["capture"]["reasons"]), b["capture"]
    both_fail(a, b, "strictly increasing")
    for planned in ([6.0, 2.0, 4.0], [2.0, 2.0, 6.0]):
        pc, sc = short_take(d, "C")
        sidecar(sc, "triangle-positive", planned)
        c = ca.analyse(pc, sc)
        assert c["detection"]["refractory_s"] > 0 and len(c["onsets"]) == 3, c["detection"]
        assert any("strictly increasing" in r for r in c["capture"]["reasons"]), (planned, c["capture"])
        both_fail(a, c, "strictly increasing")


def test_review_malformed_sidecar(d):
    """Finding 6: malformed sidecar fields make the take invalid with a reason, never an exception."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    base = json.load(open(sa))

    def ev(f):
        def g(m):
            for e in m["stimulus"]["events"]:
                f(e)
        return g

    def sends(offset_ns, anchor=0):
        def g(m):
            m["capture"]["anchor_ns"] = anchor
            for e in m["stimulus"]["events"]:
                e["host_send_start_ns"] = int(e["planned_s"] * 1e9) + offset_ns
        return g
    cases = (
        (ev(lambda e: e.__setitem__("host_send_start_ns", NAN)), "host_send_start_ns"),
        (ev(lambda e: e.__setitem__("host_send_start_ns", "x")), "host_send_start_ns"),
        (lambda m: (m["capture"].__setitem__("anchor_ns", INF),
                    ev(lambda e: e.__setitem__("host_send_start_ns", 1))(m)), "anchor_ns"),
        (sends(100 * 10 ** 9), "sidecar host send: event 0 at"),                # bounds-checked like planned_s
        (sends(-3 * 10 ** 9), "sidecar host send: event 0 at"),
        (lambda m: m["capture"].__setitem__("recorder_stderr", ["overrun!!!"]), "recorder_stderr is not a string"),
        (lambda m: m["stimulus"]["events"].append({"i": 9, "planned_s": 8.0}), "event 3 kind None"),
        (ev(lambda e: e.pop("i")), "has no index i"),
        (lambda m: m["stimulus"]["events"].append(5), "event 3 is not an object"),
        (lambda m: m.__setitem__("stimulus", None), "no stimulus"),
        (lambda m: m["stimulus"].__setitem__("events", "abc"), "events is not a list"),
        (lambda m: m.__setitem__("capture", ["x"]), "capture is not an object"),
        (lambda m: m.__setitem__("source", "silence"), "source is not an object"),
        (lambda m: m.__setitem__("timing_reference", {"channel": "x"}), "timing_reference.channel"),
        (lambda m: m["capture"].__setitem__("frames_wanted", "many"), "not numbers"),
        (lambda m: m["capture"].__setitem__("rate", "48000"), "metadata rate '48000'"),
    )
    sb = os.path.join(d, "B.json")
    for mut, needle in cases:
        m = json.loads(json.dumps(base))
        mut(m)
        json.dump(m, open(sb, "w"))
        b = ca.analyse(pa, sb)
        assert not b["capture"]["valid"] and any(needle in r for r in b["capture"]["reasons"]), (needle, b["capture"])
        assert not ca.whole_take(a, b, BOTH)["pass"] and not ca.whole_take(b, a, BOTH)["pass"]
    # a correct host send within bounds is still used
    sends(5_000_000)(base)
    json.dump(base, open(sb, "w"))
    assert ca.analyse(pa, sb)["capture"]["valid"]
    for text in ("{not json", "[1, 2]"):
        open(sb, "w").write(text)
        assert not ca.analyse(pa, sb)["capture"]["valid"]


def test_review_cli_exit_and_source(d):
    """Finding 7: exit 0 pass / 1 verdict not passed / 2 refused; source names must match.
    (Unique stems: the CLI finds <stem>.json before <stem>.render.json in the shared directory.)"""
    pa, sa = short_take(d, "cliA")
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    pb, _ = render(d, "cliB", ch)
    out = os.path.join(d, "c.align.json")
    assert ca.main([pa, "--against", pb, "--pairs", "1=1,2=2", "--out", out]) == 0
    assert ca.main([pa, "--out", out]) == 0                                      # no verdict requested
    pn, _ = render(d, "cliN", ch, source={"name": "triangle-negative"})
    assert ca.main([pa, "--against", pn, "--pairs", "1=1,2=2", "--out", out]) == 1
    w = json.load(open(out))["against"]["whole_take"]
    assert any("source names differ" in r for r in w["reasons"]), w
    both_fail(ca.analyse(pa, sa), ca.analyse(pn), "source names differ")
    for args in ([pa, "--against", pb, "--out", out], [pa, "--against", pb, "--pairs", "1=1,1=1", "--out", out],
                 [pa, "--channels", "x", "--out", out], [os.path.join(d, "missing.wav"), "--out", out]):
        assert raises(SystemExit, ca.main, args).code == 2, args
    r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(ca.__file__), "capture_align.py"), pa,
                        "--against", pn, "--pairs", "1=1,2=2", "--out", out], capture_output=True, text=True)
    assert r.returncode == 1 and "pass=False" in r.stdout, (r.returncode, r.stdout, r.stderr)


def silent_render(d, name, gains=(1, 1, 1), add=None, scale=1.0):
    """Emulator-style render: the 'short' hits at FRAMES over digital silence (floor exactly 0), plus add(L, R)."""
    p = os.path.join(d, name + "-src.wav")
    synth(p, offsets(2.0, [0, 0, 0]), list(gains), kind="short", noise=0, sample_type="float", bits=32)
    L, R = ([scale * v for v in c] for c in wavio.read(p)[1])
    if add:
        add(L, R)
    return ca.analyse(*render(d, name, [L, R]))


def test_review2_channels_cover_file(d):
    """Round 2, finding 1: validity covers every channel of the file; restricting the analysed channels is a
    deviation; pairing must cover every channel except a declared timing reference (which must not clip either)."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(pa)
    cl = wavio.read(clean_short(d))[1]                # render side: digital silence
    pg, sg = os.path.join(d, "G.wav"), os.path.join(d, "G.json")
    wavio.write(pg, RATE, [list(ch[0]), [1.0 if (i // 50) % 2 else -1.0 for i in range(len(ch[1]))]], "pcm", 24)
    sidecar(sg, "triangle-positive", [2.0, 4.0, 6.0])
    g = ca.analyse(pg, sg, channels=[1])                     # R is full-scale garbage, not analysed
    assert not g["capture"]["valid"] and g["capture"]["full_scale_samples"] > 0, g["capture"]
    assert any("channels=[1]" in x for x in g["detection"]["deviations"]), g["detection"]
    bm = ca.analyse(*render(d, "Bm", [list(cl[0])], channels=1, channel_map={"1": "Y:0x21"}))
    both_fail(g, bm, "full-scale", pairs=[(1, 1)])
    both_fail(g, bm, "(timing reference excepted) are [1, 2]", pairs=[(1, 1)])
    out = os.path.join(d, "g.align.json")
    assert ca.main([pg, "--sidecar", sg, "--channels", "1", "--against", bm["wav"], "--pairs", "1=1",
                    "--out", out]) == 1
    # a clean take restricted to channel 1 (or listing them all, in any order)
    c1 = ca.analyse(pa, sa, channels=[1])
    assert c1["capture"]["valid"] and c1["detection"]["deviations"], c1["detection"]
    both_fail(a, c1, "detection deviations")
    assert ca.analyse(pa, sa, channels=[2, 1])["detection"]["deviations"] == []
    raises(ValueError, ca.analyse, pa, sa, channels=[])
    # a declared timing-reference channel 3: excluded from comparison and pairing, still checked for clipping
    pt, st = os.path.join(d, "T.wav"), os.path.join(d, "T.json")
    clicks = [0.0] * len(ch[0])
    for f in FRAMES:
        for i in range(48):
            clicks[f - 240 + i] = 0.5
    for name, ref in (("ok", clicks), ("clip", [1.0 if i in (100, 101) else v for i, v in enumerate(clicks)])):
        wavio.write(pt, RATE, [list(ch[0]), list(ch[1]), ref], "pcm", 24)
        sidecar(st, "triangle-positive", [2.0, 4.0, 6.0])
        m = json.load(open(st))
        m["timing_reference"] = {"kind": "midi-tap", "channel": 3}
        json.dump(m, open(st, "w"))
        t = ca.analyse(pt, st)
        assert t["analysed_channels"] == [1, 2] and t["comparable_channels"] == [1, 2], t["analysed_channels"]
        if name == "ok":
            assert t["capture"]["valid"] and t["detection"]["deviations"] == [], (t["capture"], t["detection"])
            assert ca.whole_take(t, a, BOTH)["pass"] and ca.whole_take(a, t, BOTH)["pass"]
            w = ca.whole_take(t, t, BOTH + [(3, 3)])
            assert not w["pass"] and any("pairs use channels [1, 2, 3]" in x for x in w["reasons"]), w
        else:
            assert not t["capture"]["valid"] and t["capture"]["full_scale_samples"] == 2, t["capture"]
            both_fail(a, t, "full-scale")


def test_review2_zero_floor_tail(d):
    """Round 2, finding 2: a digitally silent render (floor 0) is judged past the window at -80 dBFS, not -60."""
    a = ca.analyse(*short_take(d, "A"))
    clean = silent_render(d, "Bc")
    assert clean["silence_floor"]["median_1ms_block_peak"] == 0 and not clean["signal_past_window"]
    assert ca.whole_take(a, clean, BOTH)["pass"] and ca.whole_take(clean, a, BOTH)["pass"]

    def tail61(L, R):                       # -61 dBFS 300 Hz for 950 ms after every hit: below -60, above -80
        amp = 10 ** (-61 / 20)
        for o in FRAMES:
            for i in range(int(0.95 * RATE)):
                v = amp * math.sin(2 * math.pi * 300 * i / RATE)
                L[o + i] += v
                R[o + i] += 0.8 * v
    b = silent_render(d, "Bt", add=tail61)
    h = b["hits"][0]["tail"]
    assert h["signal_past_window_basis"] == "-80dBFS" and not h["thresholds"]["floor+10dB"]["available"], h
    assert b["signal_past_window_hits"] == [0, 1, 2] and b["unexplained_onsets"] == [], b["signal_past_window_hits"]
    both_fail(a, b, "signal past the 198 ms window")


def test_review2_attack_not_previous_tail(d):
    """Round 2, finding 3: each tail ends 2 ms before the next onset, so a gradual attack of the next hit is not the
    previous hit's tail; signal before that point still is."""
    pf, sf = take(d, "F", offsets(2.0, [0, 0, 0]), [1, 1, 1], kind="faded", noise=1e-5)
    f = ca.analyse(pf, sf)
    assert not f["signal_past_window"], [h["tail"]["thresholds"]["floor+10dB"] for h in f["hits"]]
    h0, h1 = f["hits"][0], f["hits"][1]
    assert h0["tail"]["region_samples"] == [h0["onset_sample"], h1["onset_sample"] - 96], h0["tail"]
    assert ca.whole_take(f, f, BOTH)["pass"]
    # a -75 dBFS burst 1.9 s after hits 0 and 1 (under the detection threshold, over floor + 10 dB) is still caught
    info, ch = wavio.read(pf)
    L, R = list(ch[0]), list(ch[1])
    for o in FRAMES[:2]:
        for i in range(int(0.01 * RATE)):
            L[o + int(1.9 * RATE) + i] += 10 ** (-75 / 20) * (1 if i % 2 else -1)
    pb = os.path.join(d, "Fb.wav")
    wavio.write(pb, RATE, [L, R], "pcm", 24)
    sb = os.path.join(d, "Fb.json")
    sidecar(sb, "triangle-positive", [2.0, 4.0, 6.0])
    b = ca.analyse(pb, sb)
    assert b["unexplained_onsets"] == [] and b["signal_past_window_hits"] == [0, 1], b["signal_past_window_hits"]
    both_fail(f, b, "signal past the 198 ms window")


def test_review2_noise_floor_gate(d):
    """Round 2, finding 4: added stationary signal (hum) on either side fails; a silent render vs a real analog
    floor does not; B's floor is compared after the fitted gain."""
    a = ca.analyse(*short_take(d, "A"))                  # floor about -91 dBFS
    clean = silent_render(d, "Bc")
    w = ca.whole_take(a, clean, BOTH)
    assert w["pass"] and w["noise_floors"]["b_common_dbfs"] is None, w          # floor 0 counts as -80 dBFS

    def hum(level_db):
        def add(L, R):
            amp = 10 ** (level_db / 20)
            for i in range(len(L)):
                v = amp * math.sin(2 * math.pi * 50 * i / RATE)
                L[i] += v
                R[i] += v
        return add
    b = silent_render(d, "Bh", add=hum(-58))
    assert not b["signal_past_window"] and b["unexplained_onsets"] == []
    assert b["capture"]["reasons"] and all("analog-looking silence floor" in r for r in b["capture"]["reasons"])
    assert ca.whole_take(a, b, BOTH)["cosine"] > 0.99
    both_fail(a, b, "noise floor")
    assert any("take B: noise floor" in x for x in ca.whole_take(a, b, BOTH)["reasons"])
    # hum on the hardware side against a clean render
    ph, sh = short_take(d, "Ah")
    info, ch = wavio.read(ph)
    L, R = list(ch[0]), list(ch[1])
    hum(-58)(L, R)
    wavio.write(ph, RATE, [L, R], "pcm", 24)
    sidecar(sh, "triangle-positive", [2.0, 4.0, 6.0])
    ah = ca.analyse(ph, sh)
    assert ah["capture"]["valid"]
    w = ca.whole_take(ah, clean, BOTH)
    assert not w["pass"] and any("take A: noise floor" in x for x in w["reasons"]), w
    # a render 20 dB quieter with hum at -75 dBFS of its own: -55 dBFS after the fitted gain of 10
    q = silent_render(d, "Bq", scale=0.1, add=hum(-75))
    w = ca.whole_take(a, q, BOTH)
    assert abs(w["gain_b_to_a"] - 10) < 0.1 and not w["pass"], w
    assert any("take B: noise floor" in x for x in w["reasons"]), w
    assert ca.whole_take(a, silent_render(d, "Bs", scale=0.1), BOTH)["pass"]


def test_review2_hit_ids_bound_to_times(d):
    """Round 2, finding 5: a common hit id must name the same event time (relative to the first hit) on both sides."""
    a = ca.analyse(*short_take(d, "A"))
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    pb, sb = render(d, "B", ch)
    render_json(sb, pb, events=[{"hit": h, "frame": f} for h, f in zip((2, 1, 0), FRAMES)])
    b = ca.analyse(pb, sb)
    assert b["capture"]["valid"], b["capture"]
    both_fail(a, b, "different event times")
    # a different absolute schedule (A planned 1 s earlier: the offset is a free fit) with the same spacing passes
    pe, se = short_take(d, "E")
    sidecar(se, "triangle-positive", [1.0, 3.0, 5.0])
    e = ca.analyse(pe, se)
    assert e["capture"]["valid"] and e["expected_hit_times_s"][0] == {"hit": 0, "time_s": 1.0}
    assert ca.whole_take(e, ca.analyse(*render(d, "Bok", ch)), BOTH)["pass"]


def test_review2_runtime_caps_and_max_lag(d):
    """Round 2, finding 6: event and onset counts are capped before fitting (bounded runtime, a reason); a lag search
    other than the protocol's is a deviation."""
    pa, sa = short_take(d, "A")
    a = ca.analyse(pa, sa)
    info, ch = wavio.read(clean_short(d))      # render side: digital silence
    L, R = list(ch[0]), list(ch[1])
    for i in range(RATE, int(6.5 * RATE), 5):                 # a click every 5 samples
        L[i] += 0.3
    pc = os.path.join(d, "C.wav")
    wavio.write(pc, RATE, [L, R], "pcm", 24)
    sc = os.path.join(d, "C.json")
    sidecar(sc, "triangle-positive", [2.0, 4.0, 6.0])
    for kw in ({"refractory_s": 0.00005}, {}):
        t0 = time.time()
        c = ca.analyse(pc, sc, **kw) if kw else ca.analyse(pc, refractory_s=0.001)      # no metadata: cap 64
        assert time.time() - t0 < 60 and c["matched_hits"] == 0 and not c["capture"]["valid"], kw
        assert any("detected onsets >" in x for x in c["capture"]["reasons"]), c["capture"]
    fr = [RATE + k * 4800 for k in range(ca.MAX_EVENTS + 1)]
    Z = [0.0] * (12 * RATE)
    for f in fr:
        Z[f] = 0.5
    t0 = time.time()
    b = ca.analyse(*render(d, "Bev", [Z, list(Z)], frames=fr))
    assert time.time() - t0 < 60 and b["matched_hits"] == 0, b["matched_hits"]
    assert any(f"{ca.MAX_EVENTS + 1} events > {ca.MAX_EVENTS}" in x for x in b["capture"]["reasons"]), b["capture"]
    # max_lag
    x = ca.analyse(pa, sa, max_lag=5)
    assert any("max_lag=5" in r for r in x["detection"]["deviations"]), x["detection"]
    both_fail(a, x, "detection deviations")
    w = ca.whole_take(a, a, BOTH, max_lag=5)
    assert not w["pass"] and any("verdict max_lag=5" in r for r in w["reasons"]), w
    for bad in (-1, 1.5, True, None):
        raises(ValueError, ca.analyse, pa, sa, max_lag=bad)
    pb, _ = render(d, "lagB", ch)
    out = os.path.join(d, "lag.align.json")
    assert ca.main([pa, "--against", pb, "--pairs", "1=1,2=2", "--out", out]) == 0
    assert ca.main([pa, "--against", pb, "--pairs", "1=1,2=2", "--max-lag", "5", "--out", out]) == 1


# ---------------------------------------------------------------- third review round (independent reviewer)

def release_hit(peak, tau_ms, tail_s=0.4):
    """The fixture triangle continuing into an exponential release (tau_ms) for tail_s."""
    n, tau = 4096, tau_ms * RATE / 1000
    full = tri(n + int(tail_s * RATE), 64, peak)
    return full[:n] + [v * math.exp(-i / tau) for i, v in enumerate(full[n:])]


def stereo_take(d, name, hit, frames=FRAMES, noise_db=-90.0, dc=0.0, float_render=False, add=None, seed=7,
                seconds=12.0, planned=None):
    """L = R = hit at frames. A 24-bit capture with a Gaussian floor (sidecar), or a float render with digital
    silence (render provenance); add(L, R) runs before writing. Files are r3<name>.* (the test directory is shared).
    planned: the declared event times in s (default 2/4/6 s; a render declares FRAMES), seconds: the file length."""
    name = "r3" + name
    rng = random.Random(seed)
    n = int(seconds * RATE)
    sd = 0.0 if float_render else 10 ** (noise_db / 20)
    L = [dc + (rng.gauss(0, sd) if sd else 0.0) for _ in range(n)]
    R = [dc + (rng.gauss(0, sd) if sd else 0.0) for _ in range(n)]
    for f in frames:
        for i, v in enumerate(hit):
            if f + i < n:
                L[f + i] += v
                R[f + i] += v
    if add:
        add(L, R)
    if float_render:
        return ca.analyse(*render(d, name, [L, R], [round(t * RATE) for t in planned] if planned else FRAMES,
                                  duration_s=seconds))
    p, s = os.path.join(d, name + ".wav"), os.path.join(d, name + ".json")
    wavio.write(p, RATE, [L, R], "pcm", 24)
    sidecar(s, "triangle-positive", list(planned or (2.0, 4.0, 6.0)), duration=seconds)
    return ca.analyse(p, s)


def both_pass(a, b, pairs=BOTH):
    for x, y, pr in ((a, b, pairs), (b, a, [(q, p) for p, q in pairs])):
        w = ca.whole_take(x, y, pr, 32)
        assert w["pass"], w["reasons"]


def test_review3_tail_on_common_scale(d):
    """Round 3, findings 1 and 2: the past-window test is max(floor + 10 dB, -80 dBFS) on the common scale (the
    hardware take's), so the render's arbitrary level and a tiny non-zero render floor (dither) change nothing."""
    a = stereo_take(d, "A", release_hit(10 ** (-12 / 20), 14))       # release tail, gone inside the window on A
    assert not a["signal_past_window"]
    for lvl in (-12, -6, -1):                                       # same tail, hotter digital level
        b = stereo_take(d, f"B{lvl}", release_hit(10 ** (lvl / 20), 14), float_render=True)
        both_pass(a, b)
        w = ca.whole_take(a, b, BOTH)
        assert w["signal_past_window"]["B"]["threshold_common_dbfs"] == -80.0 and w["noise_floors"]["common_scale"] \
            == "take A", w
    assert b["signal_past_window"]                     # B's own-scale figure (diagnostic) still says so at -1 dBFS

    def tail82(L, R):                                  # 1 s 300 Hz at -82 dBFS raw: about -64 dBFS on A's scale
        for f in FRAMES:
            for i in range(4096, 4096 + RATE):
                v = 10 ** (-82 / 20) * math.sin(2 * math.pi * 300 * i / RATE)
                L[f + i] += v
                R[f + i] += v
    q = stereo_take(d, "Bq", release_hit(10 ** (-30 / 20), 14), float_render=True, add=tail82)
    assert not q["signal_past_window"] and q["unexplained_onsets"] == []
    both_fail(a, q, "signal past the 198 ms window")
    assert any(r.startswith("take B: signal past") for r in ca.whole_take(a, q, BOTH)["reasons"])
    assert ca.whole_take(a, stereo_take(d, "Bq0", release_hit(10 ** (-30 / 20), 14), float_render=True),
                         BOTH)["pass"]

    def dither(L, R):                                  # 24-bit TPDF dither: a floor near -139 dBFS, not 0
        rng, lsb = random.Random(5), 1 / 2 ** 23
        for i in range(len(L)):
            L[i] += (rng.random() - rng.random()) * lsb
            R[i] += (rng.random() - rng.random()) * lsb
    a8 = stereo_take(d, "A8", release_hit(10 ** (-12 / 20), 8))
    b8 = stereo_take(d, "B8", release_hit(10 ** (-1 / 20), 8), float_render=True, add=dither)
    t = b8["hits"][0]["tail"]
    assert b8["silence_floor"]["median_1ms_block_peak_dbfs"] < -130 and t["signal_past_window_basis"] == "-80dBFS", t
    assert not b8["signal_past_window"]
    both_pass(a8, b8)


def test_review3_floor_gate_order(d):
    """Round 3, finding 3: the noise-floor gate compares both floors on the hardware take's scale whichever side is
    A: same verdict and exit code in both orders; hum still fails in both."""
    b = stereo_take(d, "B", release_hit(10 ** (-1 / 20), 8), float_render=True)
    out = os.path.join(d, "fo.align.json")
    for hw in (-12, -20, -30):
        a = stereo_take(d, f"A{hw}", release_hit(10 ** (hw / 20), 8))
        both_pass(a, b)
        w1, w2 = ca.whole_take(a, b, BOTH), ca.whole_take(b, a, BOTH)
        assert w1["noise_floors"]["common_scale"] == "take A" and w2["noise_floors"]["common_scale"] == "take B"
        assert abs(w1["noise_floors"]["a_common_dbfs"] - w2["noise_floors"]["b_common_dbfs"]) < 0.01
        assert ca.main([a["wav"], "--against", b["wav"], "--pairs", "1=1,2=2", "--out", out]) == 0
        assert ca.main([b["wav"], "--against", a["wav"], "--pairs", "1=1,2=2", "--out", out]) == 0

    def hum(L, R):                                     # -50 dBFS on the render: about -61 dBFS on A's scale
        for i in range(len(L)):
            v = 10 ** (-50 / 20) * math.sin(2 * math.pi * 50 * i / RATE)
            L[i] += v
            R[i] += v
    bh = stereo_take(d, "Bh", release_hit(10 ** (-1 / 20), 8), float_render=True, add=hum)
    a = stereo_take(d, "A-12", release_hit(10 ** (-12 / 20), 8))
    both_fail(a, bh, "noise floor")
    for x, y in ((a, bh), (bh, a)):
        assert ca.main([x["wav"], "--against", y["wav"], "--pairs", "1=1,2=2", "--out", out]) == 1


def host_send_sidecar(path, planned, sends_s, path_kind="a", anchor_ns=10 ** 12):
    sidecar(path, "triangle-positive", planned)
    m = json.load(open(path))
    m["path"] = path_kind
    m["capture"]["anchor_ns"] = anchor_ns
    for e, t in zip(m["stimulus"]["events"], sends_s):
        e["host_send_start_ns"] = anchor_ns + int(round(t * 1e9))
    json.dump(m, open(path, "w"))


def test_review3_offset_bounds(d):
    """Round 3, finding 4: a render's offset must be about 0 (its frames are sample-exact); a live capture's offset
    from host send times on the sample-0 anchor must be 0..100 ms; other captures are reported as unbounded."""
    hit = release_hit(0.25, 8)
    a = stereo_take(d, "A", hit)
    late = stereo_take(d, "Bl", hit, frames=[f + 2 * RATE for f in FRAMES], float_render=True)   # audio 2 s late
    assert not late["capture"]["valid"] and late["schedule_offset_bound"]["checked"], late["schedule_offset_bound"]
    off = f"{(2 * RATE + DET) / RATE * 1000:.3f}"
    assert any(f"schedule offset {off} ms outside [-50, 50] ms" in r for r in late["capture"]["reasons"])
    both_fail(a, late, "schedule offset")
    ok = stereo_take(d, "Bo", hit, frames=[f + 3 for f in FRAMES], float_render=True)
    assert ok["capture"]["valid"] and 0 < ok["schedule_offset_bound"]["offset_ms"] < 0.25, ok["schedule_offset_bound"]

    def live(name, audio_frames, latency_s, path_kind="a"):
        t = stereo_take(d, name, hit, frames=audio_frames)
        s = os.path.splitext(t["wav"])[0] + ".json"
        host_send_sidecar(s, [2.0, 4.0, 6.0], [2.0 - latency_s, 4.0 - latency_s, 6.0 - latency_s], path_kind)
        return ca.analyse(t["wav"], s)
    good = live("Lg", [round(t * RATE) for t in (2.0, 4.0, 6.0)], 0.004)        # 4 ms after each send
    assert good["capture"]["valid"] and good["schedule_offset_bound"]["checked"], good["capture"]
    assert abs(good["schedule_offset_bound"]["offset_ms"] - (4 + DET / RATE * 1000)) < 0.1
    both_pass(good, ok)
    # note 0 silent, an identical spurious hit at 8 s: the fit relabels hits with a 2 s offset
    shift = live("Ls", [round(t * RATE) for t in (4.0, 6.0, 8.0)], 0.004)
    assert not shift["capture"]["valid"] and any("outside [0, 100] ms" in r for r in shift["capture"]["reasons"])
    both_fail(shift, ok, "schedule offset")
    early = live("Le", [round(t * RATE) for t in (2.0, 4.0, 6.0)], -0.010)     # audio 10 ms before the send
    assert any("outside [0, 100] ms" in r for r in early["capture"]["reasons"]), early["capture"]
    # path b: the recorder's start is unknown, so the offset is not bounded (reported, not a reason)
    pb = live("Lb", [round(t * RATE) for t in (4.0, 6.0, 8.0)], 0.004, path_kind="b")
    assert pb["capture"]["valid"] and not pb["schedule_offset_bound"]["checked"], pb["schedule_offset_bound"]
    assert not a["schedule_offset_bound"]["checked"]           # no host send times


def test_review3_sidecar_deviations_and_reference(d):
    """Round 3, finding 5: recorded protocol deviations block the verdict; kind 'none' never excludes a channel; a
    timing reference on a 2-channel take is a reason (capture.py records it as a deviation)."""
    hit = release_hit(0.25, 8)
    a = stereo_take(d, "A", hit)
    b = stereo_take(d, "B", hit, float_render=True)
    both_pass(a, b)
    s = os.path.splitext(a["wav"])[0] + ".json"
    for dev, valid in ((["velocity=64 (protocol 127)", "pilot take (gain check only)"], True), ("pilot", False)):
        m = json.load(open(s))
        m["protocol_deviations"] = dev
        json.dump(m, open(s, "w"))
        x = ca.analyse(a["wav"], s)
        assert x["capture"]["valid"] == valid, x["capture"]
        both_fail(x, b, "recorded protocol deviations" if valid else "protocol_deviations 'pilot' is not a list")
    pb, sb = render(d, "r3Bm", [list(wavio.read(b["wav"])[1][0])], channels=1, channel_map={"1": "Y:0x21"})
    bm = ca.analyse(pb, sb)
    for kind, needle in (("none", "no reference is declared"), ("din-tap", "beyond the stereo L/R pair")):
        sidecar(s, "triangle-positive", [2.0, 4.0, 6.0])
        m = json.load(open(s))
        m["timing_reference"] = {"kind": kind, "channel": 2}
        json.dump(m, open(s, "w"))
        x = ca.analyse(a["wav"], s)
        assert not x["capture"]["valid"] and any(needle in r for r in x["capture"]["reasons"]), x["capture"]
        assert x["comparable_channels"] == ([1, 2] if kind == "none" else [1]), x["comparable_channels"]
        both_fail(x, bm, needle, pairs=[(1, 1)])
    # capture.py: a reference on one of the stereo channels is a recorded deviation; an extra channel 3 is not that
    dev = capture.protocol_deviations(plan_args("--track-channel", "1", "--ref-kind", "din-tap", "--ref-channel", "2"))
    assert any(x.startswith("ref_channel=2 on a 2-channel take") for x in dev), dev
    dev = capture.protocol_deviations(plan_args("--track-channel", "1", "--ref-kind", "din-tap", "--ref-channel", "3",
                                                "--channels", "3"))
    assert not any(x.startswith("ref_channel=") for x in dev), dev


def test_review3_dc_not_floor(d):
    """Round 3, finding 6: a DC offset is removed (mean of the floor region) before the floor and tail envelope are
    measured, so it is not reported as floor/hum; the stored samples are otherwise untouched."""
    hit = release_hit(0.25, 8)
    a = stereo_take(d, "A", hit, dc=1e-3)                          # -60 dBFS DC on a -90 dBFS analog floor
    assert a["silence_floor"]["median_1ms_block_peak_dbfs"] < -80, a["silence_floor"]
    assert [abs(x["mean"] - 1e-3) < 1e-5 and x["basis"] == "silence-floor region" for x in a["dc_removed"]] == [
        True, True], a["dc_removed"]
    assert not a["signal_past_window"] and a["hits"][0]["tail"]["raw_residual"][0]["final_20ms_mean_raw"] > 8000
    both_pass(a, stereo_take(d, "B", hit, float_render=True))
    # hum is still hum
    ah = stereo_take(d, "Ah", hit, dc=1e-3, add=lambda L, R: [L.__setitem__(i, L[i] + 1e-3 * math.sin(
        2 * math.pi * 50 * i / RATE)) for i in range(len(L))])
    both_fail(ah, stereo_take(d, "B2", hit, float_render=True), "noise floor")


# ---------------------------------------------------------------- per-hit fractional lag (analysis policy)

def bl_tri(t, period=64):
    """Band-limited triangle (odd harmonics below Nyquist) rising from 0 at t = 0, peak about 1: the fixture's shape,
    exact at any fractional t (independent of the analyser's interpolation)."""
    return 8 / math.pi ** 2 * sum((-1) ** ((k - 1) // 2) / k ** 2 * math.sin(2 * math.pi * k * t / period)
                                  for k in range(1, period // 2, 2))


def bl_low(t):
    """A 100 Hz tone (period 480 samples, no alias inside the +-1 ms lag search)."""
    return math.sin(2 * math.pi * 100 * t / RATE)


def shifted(shape, peak, shifts, r_delay=0.0, n=4096, attack=14.4, tau_ms=8.0, release_s=0.1, frames=FRAMES):
    """add(L, R) for stereo_take: shape * a smooth envelope (0.3 ms raised-cosine attack, exponential release) at
    frames[k] + shifts[k] (fractional samples), R delayed by a further r_delay samples. Evaluated analytically."""
    def hit(t):
        if t <= 0:
            return 0.0
        env = 0.5 - 0.5 * math.cos(math.pi * t / attack) if t < attack else 1.0
        if t > n:
            env *= math.exp(-(t - n) / (tau_ms * RATE / 1000))
        return peak * env * shape(t)

    def add(L, R):
        for f, s in zip(frames, shifts):
            for i in range(n + int(release_s * RATE) + 4):
                L[f + i] += hit(i - s)
                R[f + i] += hit(i - s - r_delay)
    return add


def test_per_hit_fractional_lag(d):
    """Per-hit fractional-sample lag within +-1 ms of the schedule position, one lag for all channels
    of a hit, one gain, joint cosine/NRMSE. Honest hardware timing (clock drift, MIDI/block jitter, sub-sample phase)
    passes with its lags reported; a lag at the bound, an inter-channel delay, inversion and garbage fail. Both
    argument orders."""
    hw, rd = 10 ** (-12 / 20), 10 ** (-1 / 20)
    b = stereo_take(d, "LB", [], float_render=True, add=shifted(bl_tri, rd, (0, 0, 0)))

    def hard(name, shifts, shape=bl_tri, **kw):
        return stereo_take(d, name, [], add=shifted(shape, hw, shifts, **kw))
    for name, shifts in (("drift20ppm", (0, 1.92, 3.84)), ("jitter0.25ms", (0, 12, 5)), ("phase0.5", (0.5,) * 3),
                         ("jitter1ms", (0, 44, 20))):
        a = hard("L" + name, shifts)
        both_pass(a, b)
        w = ca.whole_take(a, b, BOTH)
        assert w["cosine"] > 0.9999 and w["nrmse"] < 0.01, (name, w)
        # lag + schedule shift (the schedule has a drift term) reproduce the hits' relative timing to 0.02 samples
        # (A's hit s samples late: B's lag + shift is -s)
        lags = w["lags_samples"]
        pos = [x + p["schedule_shift_samples"] for x, p in zip(lags, w["per_hit"])]
        assert all(abs((pos[k] - pos[0]) + (shifts[k] - shifts[0])) < 0.02 for k in range(3)), (name, lags)
        assert w["timing_consistency"]["spread_samples"] < 1.0, (name, w["timing_consistency"])
        assert w["lag_bound_samples"] == 48 and all(p["interchannel_skew_samples"] < 0.01 for p in w["per_hit"])
        assert all(abs(x - y) < 0.0002 for x, y in zip(ca.whole_take(b, a, BOTH)["lags_samples"], (-v for v in lags)))
    # the schedule/timing diagnostics apply separately and pass on their own (last take: 1 ms jitter)
    assert a["capture"]["valid"] and a["unexplained_onsets"] == [] and a["matched_hits"] == 3
    # a lag at the bound: a 100 Hz hit retimed by 60 samples (1.25 ms) has its best lag at 48, a reason
    bl = stereo_take(d, "LBl", [], float_render=True, add=shifted(bl_low, rd, (0, 0, 0), tau_ms=15))
    al = hard("Lal", (0, 60, 0), bl_low, tau_ms=15)
    both_fail(al, bl, "hit 1: best lag")
    w = ca.whole_take(al, bl, BOTH)
    assert w["per_hit"][1]["at_bound"] and abs(w["per_hit"][1]["integer_lag_samples"]) == 48, w["per_hit"]
    assert not w["per_hit"][0]["at_bound"] and not w["per_hit"][2]["at_bound"], w["per_hit"]
    both_pass(hard("Lal0", (0, 30, 0), bl_low, tau_ms=15), bl)      # 0.625 ms on the same shape: inside the bound
    # R delayed 1 sample against L: one lag per hit for both channels, the skew is reported and fails
    ar = hard("Lr1", (0, 0, 0), r_delay=1.0)
    both_fail(ar, b, "inter-channel skew")
    w = ca.whole_take(ar, b, BOTH)
    assert all(abs(p["interchannel_skew_samples"] - 1) < 0.01 for p in w["per_hit"]), w["per_hit"]
    # inverted, garbage (noise bursts at the hits) and hum still fail
    both_fail(stereo_take(d, "Linv", [], add=shifted(bl_tri, -hw, (0, 0, 0))), b, "cosine")
    rng = random.Random(3)

    def noise(L, R):
        for f in FRAMES:
            for i in range(4096):
                v = hw * rng.uniform(-1, 1)
                L[f + i] += v
                R[f + i] += v
    both_fail(stereo_take(d, "Lgar", [], add=noise), b, "NRMSE")

    def hum(L, R):
        shifted(bl_tri, hw, (0.5,) * 3)(L, R)
        for i in range(len(L)):
            v = 1e-3 * math.sin(2 * math.pi * 50 * i / RATE)
            L[i] += v
            R[i] += v
    both_fail(stereo_take(d, "Lhum", [], add=hum), b, "noise floor")


# ---------------------------------------------------------------- review round 4 (independent reviewer)

HW, RD = 10 ** (-12 / 20), 10 ** (-1 / 20)


def r4render(d, name, shifts=(0, 0, 0), **kw):
    """Render B: the band-limited fixture at FRAMES + shifts (shifted() keywords), digital silence."""
    return stereo_take(d, name, [], float_render=True, add=shifted(bl_tri, RD, shifts, **kw))


def with_meta(wav, meta_path, change):
    """Re-analyse wav after change(metadata) is written to its metadata file."""
    m = json.load(open(meta_path))
    change(m)
    json.dump(m, open(meta_path, "w"))
    return ca.analyse(wav, meta_path)


def measured(semitones):
    return {"state": "measured", "semitones": semitones, "evidence": "synthetic test fixture: stated, not measured"}


def test_track_gate_capture_vs_capture(d):
    """2026-10-19 policy: capture vs capture needs matching explicit track declarations, not a measured keyboard
    note; a missing or mismatched track fails; two declared notes must agree; an absent note is reported unverified."""
    pa, sa = take(d, "TA", offsets(2.3, [0, 0, 0]), [1, 1, 1], kind="short")
    pb, sb = take(d, "TB", offsets(2.7, [0, 0, 0]), [0.5, 0.5, 0.5], seed=2, kind="short")
    a, b = ca.analyse(pa, sa), ca.analyse(pb, sb)
    assert a["track_state"]["keyboard_note"]["state"] == "unknown"
    w = ca.whole_take(a, b, BOTH)                       # matching tracks, keyboard note unknown on both sides
    assert w["pass"] and w["track"]["match"], w["reasons"]
    assert w["track"]["keyboard_note"].startswith("unverified: no measured keyboard note on take(s) A, B")
    # absent (an older sidecar) is the same as unknown
    b0 = with_meta(pb, sb, lambda m: m.pop("keyboard_note"))
    w = ca.whole_take(a, b0, BOTH)
    assert w["pass"] and w["track"]["keyboard_note"].startswith("unverified"), w["reasons"]
    # one side measured: still unverified, passes
    b1 = with_meta(pb, sb, lambda m: m.__setitem__("keyboard_note", measured(0.0)))
    w = ca.whole_take(a, b1, BOTH)
    assert w["pass"] and w["track"]["keyboard_note"].startswith("unverified: no measured keyboard note on take(s) A")
    # both measured and equal: verified
    a1 = with_meta(pa, sa, lambda m: m.__setitem__("keyboard_note", measured(0.0)))
    w = ca.whole_take(a1, b1, BOTH)
    assert w["pass"] and w["track"]["keyboard_note"] == "verified: both takes measured, equal", w["track"]
    # conflicting declared notes fail, in both orders
    b5 = with_meta(pb, sb, lambda m: m.__setitem__("keyboard_note", measured(5.0)))
    for x, y in ((a1, b5), (b5, a1)):
        w = ca.whole_take(x, y, BOTH)
        assert not w["pass"] and any("keyboard note differs" in r for r in w["reasons"]), w["reasons"]
    # a malformed note is neither measured nor unknown; a measured one without evidence is a reason
    bg = with_meta(pb, sb, lambda m: m.__setitem__("keyboard_note", {"state": "guessed", "semitones": 0}))
    w = ca.whole_take(a, bg, BOTH)
    assert not w["pass"] and "take B: keyboard note {'state': 'guessed', 'semitones': 0} is neither measured nor " \
        "unknown" in w["reasons"], w["reasons"]
    be = with_meta(pb, sb, lambda m: m.__setitem__("keyboard_note", {"state": "measured", "semitones": 0}))
    w = ca.whole_take(a, be, BOTH)
    assert not w["pass"] and "take B: keyboard note has no evidence" in w["reasons"], w["reasons"]
    # missing track fails
    bm = with_meta(pb, sb, lambda m: (m.__setitem__("keyboard_note", {"state": "unknown"}), m["stimulus"].pop("track")))
    for x, y, n in ((a, bm, "B"), (bm, a, "A")):
        w = ca.whole_take(x, y, BOTH)
        assert not w["pass"] and f"take {n}: no declared track" in w["reasons"], w["reasons"]
    # mismatched track (track 2 on its own channel), preset messages and routing fail
    bt = with_meta(pb, sb, lambda m: (m["stimulus"].update({"track": 2, "note": 38}),
                                      m["settings"].update({"track_channel": 2, "sent": [
                                          {**SYNTHETIC_SENT[0], "channel": 2}]})))
    w = ca.whole_take(a, bt, BOTH)
    assert not w["pass"] and not w["track"]["match"] and any("track state differs: number" in r for r in w["reasons"])
    assert any("preset messages differ" in r for r in w["reasons"]), w["reasons"]
    # a track=N deviation is resolved by a match without a measured keyboard note, and kept in the report
    a2 = copy.deepcopy(a)
    a2["capture"]["protocol_deviations"] = ["track=1 (protocol 1)"]
    w = ca.whole_take(a2, b, BOTH)
    assert w["pass"] and [r["deviation"] for r in w["track"]["resolved_deviations"]] == ["track=1 (protocol 1)"], w
    assert "unverified" in w["track"]["resolved_deviations"][0]["resolved_by"]
    # silence/no-note controls keep their own rule: no waveform verdict, track or not
    pc, sc = take(d, "TC", [], [], planned=(2.0, 4.0, 6.0), source="no-note")
    w = ca.whole_take(ca.analyse(pc, sc), ca.analyse(pc, sc), BOTH)
    assert not w["applicable"] and not w["pass"] and "track" not in w, w


def test_track_gate_render_vs_capture(d):
    """2026-10-19 policy: render vs capture keeps the measured keyboard-note requirement and the track match;
    a capture's 'unknown' (capture.py's default) supplies no measurement."""
    a = stereo_take(d, "TGa", [], add=shifted(bl_tri, HW, (0, 0, 0)))
    b = r4render(d, "TGb")
    assert a["track_state"]["keyboard_note"]["state"] == "unknown" and b["capture"]["kind"] == "render"
    both_pass(a, b)                                    # the render carries the (synthetic) measured note
    rj = os.path.splitext(b["wav"])[0] + ".render.json"
    for note, needle in ((None, "keyboard note unknown on both sides"),
                         ({"state": "unknown"}, "take {}: keyboard note state {{'state': 'unknown'}} is not 'measured'")):
        bu = with_meta(b["wav"], rj, lambda m: m["track"].__setitem__("keyboard_note", note))
        for x, y, n in ((a, bu, "B"), (bu, a, "A")):
            w = ca.whole_take(x, y, BOTH, 32)
            assert not w["pass"] and any(needle.format(n) in r for r in w["reasons"]), (needle, w["reasons"])
    # a measured capture note that disagrees with the render's fails
    bm = with_meta(b["wav"], rj, lambda m: m["track"].__setitem__("keyboard_note", measured(0.0)))
    a5 = with_meta(a["wav"], os.path.splitext(a["wav"])[0] + ".json",
                   lambda m: m.__setitem__("keyboard_note", measured(5.0)))
    w = ca.whole_take(a5, bm, BOTH, 32)
    assert not w["pass"] and any("keyboard note differs" in r for r in w["reasons"]), w["reasons"]
    # a render without a declared track fails
    bn = with_meta(b["wav"], rj, lambda m: m.pop("track"))
    w = ca.whole_take(a, bn, BOTH, 32)
    assert not w["pass"] and "take B: no declared track" in w["reasons"], w["reasons"]


def tri_take(d, name, peak, render=False, hit1=None, seed=7):
    """The fixture triangle (4096 samples, period 64) at FRAMES; hit1(h) replaces hit 1 (same frame)."""
    def add(L, R):
        for k, f in enumerate(FRAMES):
            h = tri(4096, 64, peak)
            for i, v in enumerate(hit1(h) if hit1 and k == 1 else h):
                L[f + i] += v
                R[f + i] += v
    return stereo_take(d, name, [], float_render=render, add=add, seed=seed)


def test_review4_timing_consistency(d):
    """Round 4, finding 1: per hit, waveform lag minus the onset detector's lag must agree across the hits (spread
    <= 1.5 samples since round 5): a hit whose shape moved inside its window (dropped attack, alias) fails even
    when its waveform figures pass. Honest hardware timing stays well inside. Both argument orders."""
    b = r4render(d, "TB")
    for name, shifts in (("drift", (0, 1.92, 3.84)), ("jit", (0, 12, 5)), ("ph", (0.37, 0.37, 0.37))):
        a = stereo_take(d, "T" + name, [], add=shifted(bl_tri, HW, shifts))
        both_pass(a, b)
        assert ca.whole_take(a, b, BOTH)["timing_consistency"]["spread_samples"] < 1.0
    a = tri_take(d, "Ta", HW)
    # hit 1 starts 20 samples into its waveform at its frame (a skipped attack): the per-hit lag absorbs it (-20)
    # while its onset moves about 6 samples (the waveform starts loud). In a capture that is a valid take (onset jitter
    # is allowed); in a render the render timing flags the 6 samples as well (round 5: interpolated onsets)
    for name, render in (("Tdropc", False), ("Tdrop", True)):
        drop = tri_take(d, name, RD, render=render, hit1=lambda h: h[20:])
        assert drop["capture"]["valid"] != render, drop["capture"]
        both_fail(a, drop, "timing consistency")
        t = ca.whole_take(a, drop, BOTH)
        assert t["cosine"] >= 0.99 and t["nrmse"] <= 0.05 and t["timing_consistency"]["spread_samples"] > 10, t
    # slow-attack periodic hits, B (a capture: real onset jitter is allowed) hit 1 a whole period late: the lag
    # search (+-48) cannot reach it and its best |cosine| is the inverted copy half a period away (32), while the
    # onsets say 64
    sl = dict(attack=480.0, tau_ms=30.0)
    a = stereo_take(d, "Tsa", [], add=shifted(bl_tri, HW, (0, 0, 0), **sl))
    al = stereo_take(d, "Tsb", [], seed=8, add=shifted(bl_tri, HW, (0, 64, 0), **sl))
    both_fail(a, al, "timing consistency")
    assert ca.whole_take(a, al, BOTH)["timing_consistency"]["spread_samples"] > 30


def test_review4_render_timing(d):
    """Round 4, finding 1: a render is deterministic, so its onsets must sit at one offset from its declared frames
    (residual spread <= 2 samples); retimed render hits are a capture reason."""
    b = r4render(d, "RB")
    assert b["capture"]["valid"] and b["render_timing"]["residual_spread_samples"] == 0, b["render_timing"]
    rt = r4render(d, "RBt", shifts=(0, 30, 0))
    assert rt["render_timing"]["residual_spread_samples"] == 30 and not rt["capture"]["valid"]
    a = stereo_take(d, "RA", [], add=shifted(bl_tri, HW, (0, 0, 0)))
    both_fail(a, rt, "render onsets are not at one offset")
    # the same retiming in a capture is hardware jitter: fitted, reported, passed
    both_pass(a, stereo_take(d, "RAt", [], seed=9, add=shifted(bl_tri, HW, (0, 30, 0))))


def test_review4_kind_from_schema(d):
    """Round 4, finding 2: the take kind comes from the schema only ('_' keys in the metadata are ignored and a
    reason); a 'render' whose floor looks analog or a 'capture' whose floor is digital zero is a reason."""
    a = stereo_take(d, "KA", [], add=shifted(bl_tri, HW, (0, 0, 0)))
    b = r4render(d, "KB")
    sa = os.path.splitext(a["wav"])[0] + ".json"
    m = json.load(open(sa))
    m.update({"_kind": "render", "_reasons": []})
    json.dump(m, open(sa, "w"))
    x = ca.analyse(a["wav"], sa)
    assert x["capture"]["kind"] == "capture" and any("reserved keys ['_kind', '_reasons']" in r
                                                     for r in x["capture"]["reasons"]), x["capture"]
    both_fail(x, b, "reserved keys")
    rj = os.path.splitext(b["wav"])[0] + ".render.json"
    m = json.load(open(rj))
    m["_kind"] = "capture"
    json.dump(m, open(rj, "w"))
    y = ca.analyse(b["wav"], rj)
    assert y["capture"]["kind"] == "render" and any("reserved keys ['_kind']" in r for r in y["capture"]["reasons"])
    # hardware audio declared a render, render audio declared a capture: each is a reason, in both orders
    hw_as_render = ca.analyse(*render(d, "r3Khr", [list(c) for c in wavio.read(a["wav"])[1]]))
    p, s = os.path.join(d, "r3Krc.wav"), os.path.join(d, "r3Krc.json")
    wavio.write(p, RATE, [list(c) for c in wavio.read(b["wav"])[1]], "pcm", 24)
    sidecar(s, "triangle-positive", [2.0, 4.0, 6.0])
    render_as_capture = ca.analyse(p, s)
    both_fail(hw_as_render, render_as_capture, "analog-looking silence floor")
    both_fail(hw_as_render, render_as_capture, "digitally silent floor")
    both_fail(a, render_as_capture, "digitally silent floor")
    both_fail(hw_as_render, b, "analog-looking silence floor")
    both_pass(a, b)


def test_review4_offset_paths(d):
    """Round 4, finding 3: only path b (the recorder's own SD card) leaves the schedule offset unbounded; no path,
    another path value, or path a without host send times on its anchor is a reason. Audio at 4/6/8 s for events at
    2/4/6 s."""
    b = r4render(d, "OB")
    a = stereo_take(d, "OA", [], add=shifted(bl_tri, HW, (0, 0, 0), frames=[f + 2 * RATE for f in FRAMES]))
    s = os.path.splitext(a["wav"])[0] + ".json"
    for path, needle in ((None, "capture path None is not 'a' or 'b'"), ("A", "capture path 'A' is not"),
                         ("a", "path a without valid host send times")):
        m = json.load(open(s))
        m.pop("path", None)
        if path:
            m["path"] = path
        json.dump(m, open(s, "w"))
        x = ca.analyse(a["wav"], s)
        assert not x["capture"]["valid"] and not x["schedule_offset_bound"]["checked"], x["schedule_offset_bound"]
        assert any(needle in r and "cannot be bounded" in r for r in x["capture"]["reasons"]), x["capture"]
        both_fail(x, b, "cannot be bounded")
    sidecar(s, "triangle-positive", [2.0, 4.0, 6.0])                       # path b: unbounded by design
    x = ca.analyse(a["wav"], s)
    assert x["capture"]["valid"] and not x["schedule_offset_bound"]["checked"]
    assert "path b" in x["schedule_offset_bound"]["why"]


def test_review4_file_end(d):
    """Round 4, finding 4: the last window ends 10 samples before the end of the file. A whole-sample lag (fraction
    below 0.001) needs no interpolation taps and passes; a fractional one needs 32 taps past the window and gets its
    own reason, not 'no complete alignment'. Both orders."""
    last = 12 * RATE - (9600 - 96) - 10 - 10              # this hit's onset is 10 samples after its frame
    frames = [2 * RATE, 4 * RATE, last]
    planned = [f / RATE for f in frames]
    b = stereo_take(d, "EB", [], float_render=True, planned=planned, add=shifted(bl_tri, RD, (0, 0, 0), frames=frames))
    a = stereo_take(d, "EA", [], planned=planned, add=shifted(bl_tri, HW, (0, 0, 0), frames=frames))
    assert all(h["complete"] for h in a["hits"] + b["hits"]), (a["hits"], b["hits"])
    assert all(0 < 12 * RATE - x["hits"][2]["window"]["end"] < ca.SINC_HALF for x in (a, b))
    both_pass(a, b)
    af = stereo_take(d, "EAf", [], planned=planned, add=shifted(bl_tri, HW, (0.37,) * 3, frames=frames))
    both_fail(af, b, "interpolation taps")
    for x, y in ((af, b), (b, af)):
        assert not any("no complete alignment" in r for r in ca.whole_take(x, y, BOTH)["reasons"])


def test_review4_half_period(d):
    """Round 4, finding 5 (tie-break changed in round 5, finding 3): |cosine| peaks within 0.001 are a tie, broken
    towards the onset detector's lag (round 4: towards positive cosine, which let a fully inverted take pass), and
    each channel's own lag is chosen within one sample of the shared lag, so there is no false inter-channel skew. A
    periodic hit that drops half a period starts at its frame like an inverted copy: it is matched there, inverted,
    and fails. An inverted hit keeps its negative pick."""
    a = tri_take(d, "Ha", HW)
    half = tri_take(d, "Hh", RD, render=True, hit1=lambda h: h[32:])
    w = ca.whole_take(a, half, BOTH)
    p = w["per_hit"][1]
    assert abs(p["lag_samples"]) < 0.05 and p["cosine"] < -0.99 and p["interchannel_skew_samples"] < 0.01, p
    assert all(abs(x - p["lag_samples"]) <= 1 for x in p["channel_lags_samples"]), p
    both_fail(a, half, "cosine")
    inv = tri_take(d, "Hi", RD, render=True, hit1=lambda h: [-v for v in h])
    p = ca.whole_take(a, inv, BOTH)["per_hit"][1]
    assert p["cosine"] < -0.99 and abs(p["lag_samples"]) < 0.05 and p["interchannel_skew_samples"] < 0.01, p
    both_fail(a, inv, "cosine")


def test_review4_drift(d):
    """Round 4, finding 6: each take's schedule has a linear drift term (least squares, >= 3 hits, clamped to
    +-100 ppm), and the +-1 ms lag search is centred on it. 80 ppm over a 24 s take (hits at 2, 4 and 22 s) is
    1.4 ms at the last hit: passes, drift reported. 250 ppm leaves the last onset > 1 ms off the clamped schedule:
    a reason."""
    planned = [2.0, 4.0, 22.0]
    frames = [round(t * RATE) + 1 for t in planned]
    b = stereo_take(d, "DB", [], float_render=True, seconds=24.0, planned=[f / RATE for f in frames],
                    add=shifted(bl_tri, RD, (0, 0, 0), frames=frames))

    def drifted(name, ppm):
        return stereo_take(d, name, [], seconds=24.0, planned=planned,
                           add=shifted(bl_tri, HW, [f * ppm * 1e-6 for f in frames], frames=frames))
    a = drifted("DA80", 80)
    dr = a["schedule_drift"]
    assert a["capture"]["valid"] and abs(dr["fit_ppm"] - 80) < 5 and dr["applied_ppm"] == dr["fit_ppm"], dr
    both_pass(a, b)
    w = ca.whole_take(a, b, BOTH)
    assert all(abs(x) < 5 for x in w["lags_samples"]), w["lags_samples"]
    a = drifted("DA250", 250)
    assert a["schedule_drift"]["applied_ppm"] == 100 and abs(a["schedule_drift"]["fit_ppm"] - 250) < 5
    both_fail(a, b, "schedule drift")


# ---------------------------------------------------------------- review round 5 (independent reviewer)

def tone_hit(peak, att_ms, kind="cos", rel_ms=30.0, n=4096):
    """The fixture triangle (period 64) under an attack envelope over att_ms ('cos' raised cosine, 'exp' dB-linear
    from -80 dB) and a raised-cosine release over the last rel_ms."""
    a, r = max(1, int(att_ms * RATE / 1000)), int(rel_ms * RATE / 1000)
    out = []
    for i, v in enumerate(tri(n, 64, peak)):
        u = min(i / a, 1.0)
        e = 0.5 - 0.5 * math.cos(math.pi * u) if kind == "cos" else 10 ** (-4 * (1 - u))
        if r and i > n - r:
            e *= 0.5 - 0.5 * math.cos(math.pi * (n - i) / r)
        out.append(v * e)
    return out


def test_review5_carrier_independent_onsets(d):
    """Round 5, finding 1: onsets come from an energy envelope (two 1 ms boxes), interpolated, not from the first |x|
    over a threshold: on a slow periodic attack |x| has a local maximum every half period, and noise moved the old
    crossing by 16-32 samples on single hits, failing the timing gate on honest takes (10 of 12 noise seeds). Both
    argument orders."""
    for kind, att, level in (("cos", 10, -12), ("exp", 10, -30), ("cos", 3, -20)):
        b = stereo_take(d, f"C{kind}{att}b", tone_hit(10 ** (-1 / 20), att, kind), float_render=True)
        for seed in (1, 2, 4, 5):
            a = stereo_take(d, f"C{kind}{att}a{seed}", tone_hit(10 ** (level / 20), att, kind), seed=seed)
            both_pass(a, b)
            t = ca.whole_take(a, b, BOTH)["timing_consistency"]
            assert t["spread_samples"] < 1.0, (kind, att, seed, t)
        # onsets are interpolated and identical on every hit of the render
        rel = [b["onsets_precise"][k] - FRAMES[k] for k in range(3)]
        assert max(rel) - min(rel) < 1e-3 and rel[0] != int(rel[0]), b["onsets_precise"]


def test_review5_level_independent_onsets(d):
    """Round 5, finding 2: the onset threshold is the hit's own envelope peak - 30 dB (bounded below by the floor +
    6 dB since round 6, and -80 dBFS), the same rule on both sides, so the crossing sits at the same point of a slow
    attack whatever the take's level: the verdict and the lags do not depend on B's level. Before, A used floor +
    20 dB and a render -70 dBFS, so B at -1 dBFS detected a 10 ms attack about 1 ms earlier than A and the search found
    an inverted alias."""
    for kind, att, la in (("cos", 10, -30), ("exp", 3, -30), ("exp", 10, -12)):
        a = stereo_take(d, f"V{kind}{att}a", [], add=shifted_hit(tone_hit(10 ** (la / 20), att, kind), 3.3))
        lags, rel = [], []
        for lb in (-1, -20, -40):
            b = stereo_take(d, f"V{kind}{att}b{-lb}", tone_hit(10 ** (lb / 20), att, kind), float_render=True)
            rel += [t - lb for t in b["onset_thresholds_dbfs"]]          # thresholds follow the level
            both_pass(a, b)
            lags.append(ca.whole_take(a, b, BOTH)["lags_samples"])
        assert max(rel) - min(rel) < 0.05, (kind, att, rel)
        assert all(abs(x - y) < 0.01 for lg in lags for x, y in zip(lg, lags[0])), (kind, att, lags)


def shifted_hit(h, s):
    """add(L, R) for stereo_take: the sampled hit h at FRAMES + s (fractional samples, band-limited interpolation)."""
    def add(L, R):
        for f in FRAMES:
            for i in range(-40, len(h) + 40):
                x = i - s
                v = math.fsum(h[m] * (math.sin(math.pi * (x - m)) / (math.pi * (x - m)) if x != m else 1.0)
                              * (0.5 + 0.5 * math.cos(math.pi * (x - m) / 33))
                              for m in range(max(0, math.floor(x) - 32), min(len(h), math.floor(x) + 33)))
                L[f + i] += v
                R[f + i] += v
    return add


def test_review5_inverted_take(d):
    """Round 5, finding 3: the old tie-break (|cosine| peaks within 0.001: the positive one) let a take with every hit
    inverted pass on smooth periodic content, through the half-period alias. Now ties go to the lag nearest the onset
    detector's lag, the sign is never used to choose, and the inverted take fails in both orders, on either side."""
    for tag, att, rel in (("t2", 2, 20), ("t20", 20, 40)):
        h = tone_hit(10 ** (-12 / 20), att, rel_ms=rel)
        r = tone_hit(10 ** (-1 / 20), att, rel_ms=rel)
        a = stereo_take(d, f"I{tag}a", h)
        ai = stereo_take(d, f"I{tag}ai", [-v for v in h])
        b = stereo_take(d, f"I{tag}b", r, float_render=True)
        bi = stereo_take(d, f"I{tag}bi", [-v for v in r], float_render=True)
        both_pass(a, b)
        for x, y in ((a, bi), (ai, b)):
            both_fail(x, y, "cosine")
            for u, v in ((x, y), (y, x)):
                w = ca.whole_take(u, v, BOTH)
                assert w["cosine"] < -0.99 and all(abs(p["lag_samples"] - p["onset_lag_samples"]) < 1 and
                                                   p["cosine"] < -0.99 for p in w["per_hit"]), (tag, w["per_hit"])


def test_review5_quiet_16bit_floor(d):
    """Round 5, finding 4: a 16-bit capture whose analog noise rounds almost every sample to 0 can have a floor
    (median 1 ms block peak, DC removed) of exactly 0 when its few +-1 LSB samples cancel in the mean; it is not
    digital silence. Only a floor region whose stored samples are all identical (per channel) is. A capture of
    digitally silent audio still is a reason."""
    def take16(name, sparse):
        rng = random.Random(5)
        L, R = [0.0] * (12 * RATE), [0.0] * (12 * RATE)
        if sparse:                                   # a +1/-1 LSB pair every ~300 samples: about -110 dBFS RMS
            i = 0
            while i < 12 * RATE - 1:
                for ch in (L, R):
                    ch[i], ch[i + 1] = 1 / 32768, -1 / 32768
                i += rng.randrange(200, 400)
        for f in FRAMES:
            for i, v in enumerate(tri(4096, 64, HW)):
                L[f + i] += v
                R[f + i] += v
        p, s = os.path.join(d, name + ".wav"), os.path.join(d, name + ".json")
        wavio.write(p, RATE, [L, R], "pcm", 16)
        sidecar(s, "triangle-positive", [2.0, 4.0, 6.0])
        return ca.analyse(p, s)
    b = tri_take(d, "Qb", RD, render=True)
    a = take16("Q16s", True)
    sf = a["silence_floor"]
    assert sf["median_1ms_block_peak"] == 0 and not sf["digital_silence"] and a["capture"]["valid"], (sf, a["capture"])
    both_pass(a, b)
    z = take16("Q16z", False)
    assert z["silence_floor"]["digital_silence"] and not z["capture"]["valid"]
    both_fail(z, b, "digitally silent floor")


def swell(top_db, frames, sw_s=0.190, f0=500.0, db_linear=False, bot_db=-140.0, end_s=0.0):
    """add(L, R): a rising f0 Hz tone over the sw_s ending end_s before each of frames, on both channels: linear in
    amplitude up to top_db dBFS raw, or (db_linear) linear in dB from bot_db to top_db."""
    def add(L, R):
        n = int(sw_s * RATE)
        for f in frames:
            e = f - int(end_s * RATE)
            for k in range(n):
                u = (k + 1) / n
                amp = 10 ** ((bot_db + (top_db - bot_db) * u) / 20) if db_linear else 10 ** (top_db / 20) * u
                v = amp * math.sin(2 * math.pi * f0 * k / RATE)
                L[e - n + k] += v
                R[e - n + k] += v
    return add


def drum_hit(peak_db, n=int(0.180 * RATE)):
    """A sharp drum-like hit: 0.3 ms attack, pitched body 180 -> 60 Hz (20 ms decay) and a noise click (1.5 ms), its
    2 ms envelope peak about 8.7 dB below its sample peak (peak_db dBFS)."""
    g, out, ph = random.Random(1), [], 0.0
    for i in range(n):
        t = i / RATE
        ph += 2 * math.pi * (60 + 120 * math.exp(-t / 0.03)) / RATE
        body, click = math.sin(ph) * math.exp(-t / 0.020), g.gauss(0, 0.6) * math.exp(-t / 0.0015)
        out.append(min(1.0, t / 0.0003) * (body + click))
    pk = max(map(abs, out))
    return [10 ** (peak_db / 20) * v / pk for v in out]


def test_review6_attack_foot_bounded(d):
    """Round 6, finding 1: a tail ended at the foot of the next hit's attack, followed back through any falling
    envelope (up to 200 ms), so a rising swell below the trigger threshold in the 190 ms before a hit was cut from the
    previous tail and never tested (a noise-free render: nothing stops the walk). The foot is now at most
    MAX_ATTACK_LEAD_MS (20 ms) before the onset crossing, so such a swell is signal past the window, on either side.
    Honest 10 ms attacks (raised-cosine, dB-linear) keep their own foot and pass. Both argument orders."""
    a = tri_take(d, "Fa", HW)
    lead = int(ca.MAX_ATTACK_LEAD_MS * RATE / 1000)
    # render at -20 dBFS: a swell to -72 dBFS raw is about -64 dBFS on A's scale (threshold -80)
    for name, add in (("lin", swell(-72, FRAMES[1:])), ("dbl", swell(-72, FRAMES[2:], db_linear=True))):
        b = stereo_take(d, "Fb" + name, tri(4096, 64, 0.1), float_render=True, add=add)
        both_fail(a, b, "signal past the 198 ms window")
        ends = [h["tail"]["region_samples"][1] for h in b["hits"][:2]]
        assert all(e >= o - lead - 1 for e, o in zip(ends, b["onsets"][1:])), (ends, b["onsets"])
    # the same on a hardware take: a dB-linear swell from -100 to -66 dBFS before hits 1 and 2
    r = tri_take(d, "Fr", RD, render=True)
    sa = stereo_take(d, "Fsa", tri(4096, 64, HW), add=swell(-66, FRAMES[1:], db_linear=True, bot_db=-100))
    both_fail(sa, r, "signal past the 198 ms window")
    # honest slow attacks: the foot is their own (not clamped), and they pass
    for kind in ("cos", "exp"):
        x = stereo_take(d, f"Fh{kind}a", tone_hit(HW, 10, kind))
        y = stereo_take(d, f"Fh{kind}b", tone_hit(RD, 10, kind), float_render=True)
        both_pass(x, y)
        for t in (x, y):
            ends = [h["tail"]["region_samples"][1] for h in t["hits"][:2]]
            assert all(o - lead < e < o for e, o in zip(ends, t["onsets"][1:])), (kind, ends, t["onsets"])


def test_review6_quiet_hit_onset_bound(d):
    """Round 6, finding 2: a hit whose envelope peak is less than 30 dB over its onset bound (max(floor + 6 dB,
    -80 dBFS)) has its onset on the bound, not at peak - 30 dB, so its timing moves with the level and the tie-break's
    onset lag pointed at the half-period alias: an inverted render at -56/-58 dBFS, and an inverted render against
    hardware at -44 dBFS, passed. Such a hit now fails with its own reason, on either side. Honest takes at reasonable
    levels (renders -1..-40 dBFS, hardware -6..-35 dBFS, a sharp drum included: floor + 6 dB, not 10) pass. Both
    argument orders."""
    a = stereo_take(d, "Qa", tone_hit(HW, 10))
    for lb in (-56, -58):
        bi = stereo_take(d, f"Qbi{-lb}", [-v for v in tone_hit(10 ** (lb / 20), 10)], float_render=True)
        both_fail(a, bi, "hit too quiet for onset timing")
        assert all(h["onset_bound_limited"] for h in bi["hits"]) and bi["hits"][0]["onset_bound_dbfs"] == -80.0
    aq = stereo_take(d, "Qaq", tone_hit(10 ** (-44 / 20), 10))
    assert all(h["onset_bound_limited"] for h in aq["hits"])
    both_fail(aq, stereo_take(d, "Qbi", [-v for v in tone_hit(RD, 10)], float_render=True), "too quiet for onset")
    for name, hit in (("t", lambda lv: tone_hit(10 ** (lv / 20), 10)), ("d", drum_hit)):
        for la in (-6, -35):
            x = stereo_take(d, f"Qh{name}{-la}", hit(la))
            for lb in (-1, -40):
                y = stereo_take(d, f"Qh{name}b{-lb}", hit(lb), float_render=True)
                both_pass(x, y)
                assert not any(h["onset_bound_limited"] for t in (x, y) for h in t["hits"])


def test_review6_before_first_hit(d):
    """Round 6, finding 3: the 100 ms margin between the silence-floor region and the first hit was in neither the
    floor nor any tail, so a precursor or swell before the first hit was never tested. It is now checked like the
    tails (up to the first hit's window start or attack foot), with the past-window threshold on the common scale, on
    either side. Both argument orders."""
    a, r = tri_take(d, "Pa", HW), tri_take(d, "Pr", RD, render=True)
    assert not a["before_first_hit"]["signal"] and not r["before_first_hit"]["signal"]
    # render at -20 dBFS: a precursor to -75 dBFS raw (under the -70 dBFS trigger) is about -67 dBFS on A's scale
    b = stereo_take(d, "Pb", tri(4096, 64, 0.1), float_render=True, add=swell(-75, FRAMES[:1], sw_s=0.06, end_s=0.03))
    assert b["before_first_hit"]["signal"] and b["onsets"] == [f + DET for f in FRAMES], b["onsets"]
    both_fail(a, b, "before the first hit")
    # hardware: a precursor to -66 dBFS (under the trigger at floor + 20 dB, about -61 dBFS)
    sa = stereo_take(d, "Psa", tri(4096, 64, HW), add=swell(-66, FRAMES[:1], sw_s=0.06, end_s=0.03))
    assert sa["capture"]["valid"] and not sa["unexplained_onsets"], sa["capture"]
    both_fail(sa, r, "before the first hit")


# ---------------------------------------------------------------- capture.py: protocol, preset, validation

PROTO_REQ = ["--firmware-label", "t", "--gain-note", "g", "--master-volume-note", "fully clockwise",
             "--fx-state-note", "bypass off, grid fx off", "--pack", "ref pack", "--slot-ui", "A1"]


def plan_args(*extra, source="triangle-positive"):
    return capture.normalise(capture.parser().parse_args(["plan", "--source", source, *extra]))


def test_protocol_defaults_and_preset(d):
    a = plan_args("--track-channel", "1")
    assert capture.protocol_deviations(a) == []
    ev = capture.build_schedule(a)
    ons = [e for e in ev if e["kind"] == "note_on"]
    offs = [e for e in ev if e["kind"] == "note_off"]
    assert [e["planned_s"] for e in ons] == [2.0, 4.0, 6.0] and [e["planned_s"] for e in offs] == [2.5, 4.5, 6.5]
    assert ons[0]["bytes"] == "9f247f" and offs[0]["bytes"] == "8f2400" and a.duration == 12.0
    assert a.preset == "reference" and a.idle_before >= 2.0 and a.channels == 2 and a.ref_kind == "none"
    assert [capture.TRACK_NOTES[t] for t in range(8)] == [36, 38, 40, 41, 43, 45, 47, 48]
    st = capture.settings_list(a)
    by = {s["name"]: s for s in st}
    assert by["Compressor on/off"]["bytes"] == "bf6301bf6216bf0600"
    assert by["Compressor soft clip"]["bytes"] == "bf6301bf621abf0600"
    assert by["Master filter freq"]["bytes"] == "bf4f40" and by["Master filter res"]["bytes"] == "bf471e"
    assert by["Pitch (0 st)"]["bytes"] == "b01540" and by["ReverbSend"]["bytes"] == "b01f00"
    grid = [s for s in st if s["name"].startswith("Grid FX")]
    assert grid == [], "Grid FX CCs are opt-in (channel unverified)"
    opt = capture.reference_preset(1, 16)
    assert [n for n, *_ in opt if n.startswith("Grid FX")] and capture.reference_preset(1, None) == [
        i for i in opt if not i[0].startswith("Grid FX")]
    allbytes = "".join(s["bytes"] for s in st)
    assert "bf6215" not in allbytes and not any(s["name"] == "SampleChoice" for s in st)   # no FX bypass, no CC8
    ts = capture.settings_list(plan_args("--track-channel", "5"))
    assert {s["channel"] for s in ts if not s["name"].startswith(("Master", "Compressor", "Grid"))} == {5}
    for s in st:
        capture.check_voice_only(bytes.fromhex(s["bytes"]))
    for bad in ("f0002029", "fa", "c005", "d040", "9f24ff", "9f24"):
        raises(ValueError, capture.check_voice_only, bytes.fromhex(bad))
    dev = capture.protocol_deviations(plan_args("--track-channel", "1", "--sample-choice", "5", "--idle-before", "1"))
    assert any("SampleChoice" in x for x in dev) and any("idle_before" in x for x in dev)
    pa = plan_args("--track-channel", "1", "--pilot")
    assert (pa.hits, pa.lead, pa.duration) == (1, 1.0, 3.0) and any("pilot" in x for x in capture.protocol_deviations(pa))
    a8 = plan_args("--track", "8", "--track-channel", "8", "--note-off-after", "-1", "--preset", "none")
    assert [e["bytes"] for e in capture.build_schedule(a8)] == ["9f307f"] * 3
    assert [e["kind"] for e in capture.build_schedule(plan_args(source="no-note"))] == ["marker"] * 3


def no_devices():
    real = os.open, subprocess.Popen

    def boom(*a, **k):
        raise AssertionError("device opened")
    os.open, subprocess.Popen = boom, boom
    return real


def restore(real):
    os.open, subprocess.Popen = real


def test_validation_before_devices(d):
    """Fix F: out-of-range values are refused before any device is opened."""
    real = no_devices()
    try:
        base = ["live", "--source", "triangle-positive", "--alsa-device", "hw:9,0", "--track-channel", "1", *PROTO_REQ]
        for extra in (["--sample-choice", "128"], ["--sample-choice", "-1"], ["--slot", "200"],
                      ["--lead", "nan"], ["--lead", "0"], ["--spacing", "-2"], ["--duration", "inf"],
                      ["--idle-before", "nan"], ["--note-off-after", "nan"], ["--velocity", "0"], ["--rate", "0"],
                      ["--channels", "0"], ["--ref-kind", "din-tap", "--ref-channel", "3"], ["--ref-channel", "0"],
                      ["--channel-map", "3=x"], ["--hits", "0"], ["--note-off-after", "2.5"]):
            raises(SystemExit, capture.main, base + extra)
        # required protocol fields for live/play: track channel, master volume, FX state, pack/slot
        for drop in ("--track-channel", "--master-volume-note", "--fx-state-note", "--pack", "--slot-ui"):
            args = list(base)
            i = args.index(drop)
            del args[i:i + 2]
            e = raises(SystemExit, capture.main, args)
            assert drop in str(e), (drop, e)
        capture.main(["plan", "--source", "triangle-negative"])
        capture.main(["live", "--dry-run", "--source", "silence", "--alsa-device", "hw:9,0", "--track-channel", "1"])
    finally:
        restore(real)


# ---------------------------------------------------------------- capture.py: live/play with fakes (fix A)

FAKE_RECORDER = r'''
import math, sys, time, os
midi, rate = sys.argv[1], 48000
if len(sys.argv) > 2:
    open(sys.argv[2], "w").write(str(os.getpid()))
chunk, start, hits, pos = 480, time.monotonic(), [], 0
out = sys.stdout.buffer
def tri(i):
    p = i % 64
    return (p / 16 if p < 16 else (2 - p / 16 if p < 48 else p / 16 - 4)) * 0.25
while True:
    data = open(midi, "rb").read() if os.path.exists(midi) else b""
    n_on = sum(1 for i in range(0, len(data) - 2, 3) if data[i] == 0x9F)
    while len(hits) < n_on:
        hits.append(pos)
    buf = bytearray()
    for k in range(chunk):
        v = sum(tri(pos + k - h) for h in hits if 0 <= pos + k - h < 4096)
        v += 4e-7 * ((pos + k) % 7 - 3)        # a few LSB of floor: a hardware capture is never digital zero
        s = int(v * 8388607).to_bytes(3, "little", signed=True)
        buf += s + s
    out.write(buf); out.flush()
    pos += chunk
    time.sleep(max(0, start + pos / rate - time.monotonic()))
'''

FAST = ["--duration", "1.6", "--lead", "0.2", "--spacing", "0.4", "--note-off-after", "0.1", "--idle-before", "0",
        "--settle", "0"]


def live_args(d, midi, out, pidfile=None):
    rec = os.path.join(d, "fake_arecord.py")
    open(rec, "w").write(FAKE_RECORDER)
    cmd = f"{sys.executable} -I {rec} {midi}" + (f" {pidfile}" if pidfile else "")
    unity_calibration(d, 2)
    return ["live", "--source", "triangle-positive", "--alsa-device", "fake", "--midi-device", midi,
            "--recorder-cmd", cmd, "--track-channel", "1", *PROTO_REQ, *FAST, "--out", out,
            "--calibration", os.path.join(d, "calibration-unity2.json")]


def only_json(out):
    side = [f for f in os.listdir(out) if f.endswith(".json")]
    assert len(side) == 1, side
    return os.path.join(out, side[0]), json.load(open(os.path.join(out, side[0])))


def test_live_path_with_fakes(d):
    midi = os.path.join(d, "midi.bin")
    open(midi, "wb").close()
    out = os.path.join(d, "cap")
    capture.main(live_args(d, midi, out))
    side, meta = only_json(out)
    wav = os.path.join(out, meta["wav"]["file"])
    info = wavio.read_info(wav)
    assert (info["rate"], info["channels"], info["bits"], info["frames"]) == (48000, 2, 24, int(1.6 * 48000)), info
    assert meta["status"] == "complete" and meta["wav"]["partial"] is False and not meta["level_check"]["clipped"]
    sent = open(midi, "rb").read()
    expect = b"".join(bytes.fromhex(s["bytes"]) for s in meta["settings"]["sent"]) + \
        b"".join(bytes.fromhex(e["bytes"]) for e in meta["stimulus"]["events"])
    assert sent == expect and all(s["sent"] for s in meta["settings"]["sent"] + meta["stimulus"]["events"])
    assert all(abs(e["late_ms"]) < 20 for e in meta["stimulus"]["events"])
    assert meta["git"]["commit"] and meta["dsp_reads"].startswith("none") and meta["capture"]["arrival_log"]
    assert meta["source"]["selected_by"] == "operator on the unit" and meta["source"]["slot_ui"] == "A1"
    assert any("duration" in x for x in meta["protocol_deviations"])          # the fast test schedule is recorded
    r = ca.analyse(wav, side, window_s=0.2)
    assert r["matched_hits"] == 3 and r["capture"]["valid"] and r["hits_complete"], (r["onsets"], r["capture"])
    assert all(p["cosine_ge_0.99"] for p in r["pairwise_diagnostics"])
    assert r["jitter"]["max_abs_ms"] <= 10.5, r["jitter"]      # fake is quantised to 10 ms chunks


class FailingMidi:
    """Writes like the real port; on send number fail_at writes `partial` bytes, then raises `exc`."""
    calls = 0

    def __init__(self, path, fail_at, exc, partial=1):
        self.f = open(path, "ab", buffering=0)
        self.fail_at, self.exc, self.partial, self.n = fail_at, exc, partial, 0
        self.last_written = 0

    def send(self, data, timeout=1.0):
        capture.check_voice_only(data)
        self.n += 1
        if self.n == self.fail_at:
            self.f.write(data[:self.partial])
            self.last_written = self.partial
            raise self.exc
        self.f.write(data)
        self.last_written = len(data)

    def close(self):
        self.f.close()


def dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:                                   # reap if it is our zombie child
            if os.waitpid(pid, os.WNOHANG)[0]:
                return True
        except ChildProcessError:
            pass
        time.sleep(0.1)
    return False


def test_live_failure_and_interrupt_cleanup(d):
    n_settings = len(capture.settings_list(plan_args("--track-channel", "1")))
    for name, exc in (("timeout", TimeoutError("MIDI write timed out after 1 of 3 bytes")),
                      ("interrupt", KeyboardInterrupt())):
        midi, out, pidf = os.path.join(d, f"m-{name}.bin"), os.path.join(d, f"cap-{name}"), os.path.join(d, f"{name}.pid")
        open(midi, "wb").close()
        real = capture.Midi
        capture.Midi = lambda path, e=exc: FailingMidi(path, n_settings + 3, e)   # fails on the 2nd note-on
        try:
            raises(type(exc), capture.main, live_args(d, midi, out, pidf))
        finally:
            capture.Midi = real
        side, meta = only_json(out)
        assert meta["status"] == ("interrupted" if name == "interrupt" else "error"), meta["status"]
        assert type(exc).__name__ in meta["error"]
        ev = meta["stimulus"]["events"]
        assert [e["sent"] for e in ev[:2]] == [True, True] and ev[2]["sent"] is False, ev[:3]
        assert ev[2]["bytes_sent"] == 1 and type(exc).__name__ in ev[2]["send_error"] and "host_send_start_ns" in ev[2]
        assert all(not e["sent"] and "bytes_sent" not in e for e in ev[3:])
        assert all(s["sent"] for s in meta["settings"]["sent"])
        wav = os.path.join(out, meta["wav"]["file"])
        info = wavio.read_info(wav)                      # finalised: header sizes match the payload
        assert info["frames"] == meta["capture"]["frames"] > 0 and meta["wav"]["partial"] is True
        assert dead(int(open(pidf).read())), "recorder still running"
        r = ca.analyse(wav, side)
        assert not r["capture"]["valid"] and any("status" in x for x in r["capture"]["reasons"])


def test_play_interrupt_and_import(d):
    midi, out = os.path.join(d, "mp.bin"), os.path.join(d, "cap-play")
    open(midi, "wb").close()
    real = capture.Midi
    capture.Midi = lambda path: FailingMidi(path, 1, KeyboardInterrupt(), partial=0)
    try:
        raises(KeyboardInterrupt, capture.main, ["play", "--no-prompt", "--source", "silence", "--midi-device", midi,
                                                 "--track-channel", "1", *PROTO_REQ, *FAST, "--out", out])
    finally:
        capture.Midi = real
    side, meta = only_json(out)
    assert meta["status"] == "stimulus-interrupted" and meta["settings"]["sent"][0]["bytes_sent"] == 0
    src = os.path.join(d, "ZOOM0002.WAV")
    wavio.write(src, RATE, [[0.0] * 100, [0.0] * 100], "float", 32)
    assert "not complete" in str(raises(SystemExit, capture.main, ["import", "--sidecar", side, "--wav", src]))


def test_play_and_import(d):
    midi, out = os.path.join(d, "midi2.bin"), os.path.join(d, "cap2")
    open(midi, "wb").close()
    capture.main(["play", "--no-prompt", "--source", "silence", "--midi-device", midi, "--track-channel", "1",
                  *PROTO_REQ, *FAST, "--out", out])
    side, meta = only_json(out)
    assert meta["status"] == "stimulus-complete" and meta["path"] == "b"
    # fix B: a truncated recorder file (12 s header, one frame) is refused and not copied
    bad = os.path.join(d, "ZOOM0003.WAV")
    bad_wav(bad, 576000, 1)
    assert "refusing" in str(raises(SystemExit, capture.main, ["import", "--sidecar", side, "--wav", bad]))
    assert not os.path.exists(os.path.splitext(side)[0] + ".wav")
    src = os.path.join(d, "ZOOM0001.WAV")
    wavio.write(src, RATE, [[0.0] * 100, [0.0] * 100], "float", 32)
    capture.main(["import", "--sidecar", side, "--wav", src])
    meta = json.load(open(side))
    assert meta["status"] == "complete" and meta["wav"]["sha256"] == meta["wav"]["source_sha256"]
    assert meta["capture"]["format"] == "float32" and meta["capture"]["frames"] == 100
    raises(SystemExit, capture.main, ["import", "--sidecar", side, "--wav", src])


def test_capture_records_track_and_keyboard_note(d):
    """2026-10-19 policy: the sidecar records the declared track and its channel; the keyboard note is 'unknown'
    unless the operator gives a measured value with its evidence (never invented)."""
    midi = os.path.join(d, "midi-kb.bin")
    open(midi, "wb").close()
    base = ["play", "--no-prompt", "--source", "triangle-positive", "--midi-device", midi, "--track", "2",
            "--track-channel", "2", *PROTO_REQ, *FAST]
    capture.main([*base, "--out", os.path.join(d, "kb-unknown")])
    _, meta = only_json(os.path.join(d, "kb-unknown"))
    assert meta["stimulus"]["track"] == 2 and meta["stimulus"]["note"] == 38 and meta["settings"]["track_channel"] == 2
    assert meta["keyboard_note"]["state"] == "unknown" and "semitones" not in meta["keyboard_note"], meta
    st = ca.track_state({**meta, "_kind": "capture"})
    assert st["number"] == 2 and st["track_midi_channel"] == 2 and st["keyboard_note"]["state"] == "unknown"
    capture.main([*base, "--keyboard-note-semitones", "0", "--keyboard-note-evidence", "tuner: 750.0 Hz at the root",
                  "--out", os.path.join(d, "kb-measured")])
    _, meta = only_json(os.path.join(d, "kb-measured"))
    assert meta["keyboard_note"] == {"state": "measured", "semitones": 0.0, "evidence": "tuner: 750.0 Hz at the root",
                                     "given_by": "operator (--keyboard-note-semitones/--keyboard-note-evidence)"}, meta
    for extra, needle in ((["--keyboard-note-semitones", "0"], "go together"),
                          (["--keyboard-note-evidence", "x"], "go together"),
                          (["--keyboard-note-semitones", "nan", "--keyboard-note-evidence", "x"], "finite"),
                          (["--keyboard-note-semitones", "0", "--keyboard-note-evidence", " "], "not be empty")):
        assert needle in str(raises(SystemExit, plan_args, "--track-channel", "1", *extra)), extra


def test_level_check_clipping(d):
    p = os.path.join(d, "clip.wav")
    wavio.write(p, RATE, [[0.0, 1.0, -1.0, 0.5], [0.0, 0.25, 0.0, 0.0]])
    lc = capture.level_check(p)
    assert lc["clipped"] and lc["channels"][0]["full_scale_samples"] == 2 and lc["channels"][1]["full_scale_samples"] == 0
    assert abs(lc["channels"][1]["peak_dbfs"] - (-12.04)) < 0.01


# ---------------------------------------------------------------- recorder-chain calibration, H5studio 4-channel layout

H5MAP = {"1": "unused", "2": "unused", "3": "Rhythm L", "4": "Rhythm R"}
H5CHAIN = {"interface": "Zoom H5studio", "channels": 4, "channel_map": H5MAP, "gain_note": "in1 -16.0, in2 -15.3"}
IMB_DB = -0.66                 # recorder input 2 against input 1 (synthetic; the unit showed -16.0 vs -15.3 dBFS)
H5_PEAK = 0.16                 # about -16 dBFS


def h5_wav(path, frames, g3, g4, seconds=12.0, noise_db=-90.0, seed=11, unused=None, sample_type="pcm", bits=32,
           skew=0):
    """4-channel H5studio multitrack layout: USB 1/2 the switched-off MIC capsule (digital zero unless unused(u1, u2)
    writes into them), 3 = Rhythm L, 4 = Rhythm R; the fixture triangle at frames with per-hit gains g3/g4, channel 4
    skew samples later (the real H5studio pilot: 4 leads 3 by 1.00 sample)."""
    rng = random.Random(seed)
    n, sd = int(seconds * RATE), 10 ** (noise_db / 20)
    L = [rng.gauss(0, sd) for _ in range(n)]
    R = [rng.gauss(0, sd) for _ in range(n)]
    hit = tri(4096, 64, H5_PEAK)
    for f, a, b in zip(frames, g3, g4):
        for i, v in enumerate(hit):
            L[f + i] += a * v
            R[f + i + skew] += b * v
    u1, u2 = [0.0] * n, [0.0] * n
    if unused:
        unused(u1, u2)
    wavio.write(path, RATE, [u1, u2, L, R], sample_type, bits)


def h5_calibration(d, name, r_db=(IMB_DB,) * 5, g3=None, swap_db=0.05, seed=11, skew=0):
    """A calibration JSON as capture.py calibrate writes it (schema, chain, swap evidence, the take's sidecar under
    "take", result from capture_align.calibration_stats) around a synthetic 4-channel take, hits 1 s apart from 1 s.
    Returns (json path, result)."""
    nh = len(r_db)
    frames = [round((1.0 + k) * RATE) for k in range(nh)]
    w, j = os.path.join(d, name + ".wav"), os.path.join(d, name + ".json")
    h5_wav(w, frames, g3 or [1.0] * nh, [10 ** (x / 20) for x in r_db], seconds=nh + 2.0, seed=seed, skew=skew)
    sidecar(j, "triangle-positive", [1.0 + k for k in range(nh)], duration=nh + 2.0, calibration=None,
            channel_map=H5MAP)
    take_ = json.load(open(j))
    take_["wav"]["file"] = os.path.basename(w)
    cal = {"schema": ca.CALIBRATION_SCHEMA, "created_utc": "2026-10-17T00:00:00+00:00", "status": "recording",
           "accepted": False, "reasons": [], "chain": H5CHAIN,
           "swap_evidence": {"note": "cable swap", "rhythm_r_minus_l_db": swap_db}, "result": None, "take": take_}
    json.dump(cal, open(j, "w"))
    res = ca.calibration_stats(w, j)
    cal.update({"result": res, "accepted": res["accepted"], "reasons": res["reasons"],
                "status": "accepted" if res["accepted"] else "refused"})
    json.dump(cal, open(j, "w"))
    return j, res


def test_calibration_stats(d):
    """Five hits with a 0.66 dB recorder imbalance on the H5 layout: the frozen ratio recovers it within 0.01 dB;
    spread, clipping, polarity, too few hits and an ordinary take are refused."""
    j, res = h5_calibration(d, "calA")
    assert res["accepted"], res["reasons"]
    assert res["analysed_channels"] == [3, 4] and res["reference_channel"] == 3 and res["ratios"]["3"] == 1.0
    c4 = res["channels"]["4"]
    assert abs(c4["mean_db"] - IMB_DB) < 0.01 and abs(20 * math.log10(res["ratios"]["4"]) - IMB_DB) < 0.01, c4
    assert c4["n"] == 5 and c4["spread_db"] < 0.01 and c4["std_db"] <= c4["spread_db"], c4
    assert [u["channel"] for u in res["unused_channels"]] == [1, 2]
    assert all(u["digital_zero"] for u in res["unused_channels"])
    assert len(res["hits"]) == 5 and all(abs(h["channels"]["4"]["ratio_db"] - IMB_DB) < 0.01 for h in res["hits"])
    # least squares over the whole window, and a ratio that holds sample by sample
    assert all(h["channels"]["4"]["cosine"] > 0.9999 and h["channels"]["4"]["nrmse"] < 1e-3 for h in res["hits"])
    assert c4["delay_samples"] == 0 and not c4["delay_exceeds_skew_max"], c4
    # an inter-channel delay (the real H5studio pilot: 1 sample; unaligned there the ratio read 0.602 instead of
    # 0.653 dB at NRMSE 0.108): the ratio is measured at the aligned lag; the delay is reported, not applied
    _, sk = h5_calibration(d, "calSkew", skew=-1)
    s4 = sk["channels"]["4"]
    assert sk["accepted"] and abs(s4["mean_db"] - IMB_DB) < 0.01 and abs(s4["delay_samples"] + 1) < 0.01, sk
    assert s4["delay_exceeds_skew_max"] and all(h["channels"]["4"]["nrmse"] < 1e-3 for h in sk["hits"])
    _, bad = h5_calibration(d, "calSpread", r_db=(IMB_DB - 0.08, IMB_DB, IMB_DB + 0.08, IMB_DB, IMB_DB))
    assert not bad["accepted"] and any("spread 0.16" in r for r in bad["reasons"]), bad["reasons"]
    _, ok = h5_calibration(d, "calSpreadOk", r_db=(IMB_DB - 0.04, IMB_DB, IMB_DB + 0.04, IMB_DB, IMB_DB))
    assert ok["accepted"] and abs(ok["channels"]["4"]["spread_db"] - 0.08) < 0.005, ok
    _, clip = h5_calibration(d, "calClip", g3=[1.0, 1.0, 7.0, 1.0, 1.0])
    assert not clip["accepted"] and any("full-scale" in r for r in clip["reasons"]), clip["reasons"]
    h5_calibration(d, "calInv")
    w, j = os.path.join(d, "calInv.wav"), os.path.join(d, "calInv.json")
    _, data = wavio.read(w)
    wavio.write(w, RATE, data[:3] + [[-v for v in data[3]]], "pcm", 32)
    m = json.load(open(j))
    m["take"]["wav"]["sha256"] = capture.sha256(w)
    json.dump(m, open(j, "w"))
    inv = ca.calibration_stats(w, j)
    assert not inv["accepted"] and any("not positive" in r for r in inv["reasons"]), inv["reasons"]
    _, few = h5_calibration(d, "calFew", r_db=(IMB_DB,) * 3)
    assert not few["accepted"] and any("expected hits 3 < 5" in r for r in few["reasons"]), few["reasons"]
    p, s = take(d, "calNot", offsets(2.0, [0, 0, 0]), [1, 1, 1])
    assert any("not a calibration take" in r for r in ca.calibration_stats(p, s)["reasons"])


H5ARGS = ["--channels", "4", "--channel-map", "1=unused,2=unused,3=Rhythm L,4=Rhythm R", "--interface",
          "Zoom H5studio", "--gain-note", H5CHAIN["gain_note"], "--track-channel", "1"]


def test_calibration_sidecar_embedding(d):
    """A take embeds the accepted calibration (path, sha256 of JSON and WAV, ratios, spread, time); a different gain
    note, channel map, interface or channel count is a recorded deviation; unaccepted or changed files are refused."""
    j, res = h5_calibration(d, "calE")
    a = plan_args(*H5ARGS, "--calibration", j)
    cal = a.calibration_info
    assert cal["sha256"] == capture.sha256(j) and cal["path"] == os.path.abspath(j)
    assert cal["wav"]["sha256"] == capture.sha256(os.path.join(d, "calE.wav")) and cal["ratios"] == res["ratios"]
    assert cal["spread_db"]["4"]["max_min"] == res["channels"]["4"]["spread_db"] and cal["created_utc"]
    assert capture.protocol_deviations(a) == [], capture.protocol_deviations(a)
    side = capture.sidecar(a, "a", capture.build_schedule(a), capture.settings_list(a), capture.make_ref(a))
    assert side["calibration"] == cal
    for flag, val, key in (("--gain-note", "in1 -10", "gain_note"), ("--interface", "other", "interface"),
                           ("--channel-map", "1=unused,2=unused,3=Rhythm R,4=Rhythm L", "channel_map")):
        x = list(H5ARGS)
        x[x.index(flag) + 1] = val
        dev = capture.protocol_deviations(plan_args(*x, "--calibration", j))
        assert len(dev) == 1 and dev[0].startswith(f"calibration {key} "), dev
    dev = capture.protocol_deviations(plan_args("--track-channel", "1", "--gain-note", H5CHAIN["gain_note"],
                                                "--calibration", j))
    assert any(x.startswith("calibration channels 4 != take 2") for x in dev), dev
    # refused before any device: not accepted, changed WAV, not a calibration
    h5_calibration(d, "calE2", r_db=(IMB_DB - 0.1, IMB_DB, IMB_DB + 0.1, IMB_DB, IMB_DB))
    assert "not accepted" in str(raises(SystemExit, plan_args, "--calibration", os.path.join(d, "calE2.json")))
    j3, _ = h5_calibration(d, "calE3")
    open(os.path.join(d, "calE3.wav"), "ab").write(b"\0\0\0\0")
    assert "missing or changed" in str(raises(SystemExit, plan_args, "--calibration", j3))
    _, s = take(d, "calE4", offsets(2.0, [0, 0, 0]), [1, 1, 1])
    assert "not a circuit-rhythm-calibration" in str(raises(SystemExit, plan_args, "--calibration", s))
    real = no_devices()
    try:
        cmd = ["calibrate", "--dry-run", "--alsa-device", "x"]
        assert "neither" in str(raises(SystemExit, capture.main, cmd + ["--source", "triangle-positive",
                                                                        "--calibration", j]))
        for extra in (["--source", "silence"], ["--source", "triangle-positive", "--hits", "4"],
                      ["--source", "triangle-positive", "--pilot"]):
            raises(SystemExit, capture.main, cmd + extra)
        capture.main(cmd + ["--source", "triangle-positive", *H5ARGS])
    finally:
        restore(real)
    c = capture.normalise(capture.parser().parse_args(["calibrate", "--source", "triangle-positive", *H5ARGS]))
    assert (c.hits, c.lead, c.spacing, c.duration) == (5, 1.0, 1.0, 7.0) and capture.protocol_deviations(c) == []
    assert [e["planned_s"] for e in capture.build_schedule(c) if e["kind"] == "note_on"] == [1.0, 2.0, 3.0, 4.0, 5.0]


def h5_take(d, name, calibration, r_db=IMB_DB, unused=None, sample_type="pcm", bits=32):
    """A 12 s protocol take on the H5 layout (hits at FRAMES, input 2 at r_db against input 1), path b sidecar."""
    w, s = os.path.join(d, name + ".wav"), os.path.join(d, name + ".json")
    h5_wav(w, FRAMES, [1.0] * 3, [10 ** (r_db / 20)] * 3, unused=unused, sample_type=sample_type, bits=bits, seed=5)
    sidecar(s, "triangle-positive", [2.0, 4.0, 6.0], calibration=calibration, channel_map=H5MAP)
    return w, s


def test_calibration_applied_in_analysis(d):
    """capture_align divides each analysed channel by its frozen ratio before any analysis (raw WAV untouched): a take
    with the 0.66 dB recorder imbalance passes against a balanced render with the calibration and fails without it."""
    j, res = h5_calibration(d, "calA2")
    cal = capture.load_calibration(j)
    w, s = h5_take(d, "h5cal", cal)
    before = capture.sha256(w)
    a = ca.analyse(w, s)
    cc = a["chain_calibration"]
    assert a["capture"]["valid"] and cc["applied"], a["capture"]
    assert cc["ratios"] == {"3": 1.0, "4": res["ratios"]["4"]} and cc["sha256"] == cal["sha256"], cc
    assert cc["verified_against"] == j and abs(cc["ratios_db"]["4"] - IMB_DB) < 0.01, cc
    lv = {x["channel"]: x["rms"] for x in a["levels"]}
    assert all(abs(20 * math.log10(x / y)) < 0.01 for x, y in zip(lv[3], lv[4])), lv      # matched after calibration
    assert capture.sha256(w) == before
    hit = tri(4096, 64, H5_PEAK)
    ch = [[0.0] * int(12 * RATE) for _ in range(2)]
    for f in FRAMES:
        for i, v in enumerate(hit):
            ch[0][f + i] = ch[1][f + i] = 0.5 * v
    b = ca.analyse(*render(d, "h5B", ch))
    assert b["chain_calibration"] == {"applied": False, "why": "render: no recorder chain"}
    both_pass(a, b, pairs=[(3, 1), (4, 2)])
    assert not ca.whole_take(a, b, [(1, 1), (2, 2)], 32)["pass"]               # unused channels are never paired
    # without a calibration: a verdict-blocking reason; --no-calibration: analysed raw, a recorded deviation
    w2, s2 = h5_take(d, "h5raw", None)
    x = ca.analyse(w2, s2)
    assert not x["capture"]["valid"] and any(r.startswith("no chain calibration: 2 analysed channels [3, 4]")
                                             for r in x["capture"]["reasons"]), x["capture"]
    both_fail(x, b, "no chain calibration", pairs=[(3, 1), (4, 2)])
    y = ca.analyse(w2, s2, no_calibration=True)
    assert y["capture"]["valid"] and any("no_calibration" in r for r in y["detection"]["deviations"])
    both_fail(y, b, "detection deviations", pairs=[(3, 1), (4, 2)])
    lv = {v["channel"]: v["rms"] for v in y["levels"]}                         # raw: the 0.66 dB is there
    assert all(abs(20 * math.log10(q / p) - IMB_DB) < 0.01 for p, q in zip(lv[3], lv[4])), lv
    assert ca.analyse(*render(d, "h5B", ch), no_calibration=True)["detection"]["deviations"] == []
    one = ca.analyse(w2, s2, channels=[3])
    assert not one["chain_calibration"]["applied"] and not any("chain" in r for r in one["capture"]["reasons"])
    assert ca.main([w2, "--sidecar", s2, "--against", b["wav"], "--pairs", "3=1,4=2", "--out",
                    os.path.join(d, "h5raw.align.json")]) == 1
    assert ca.main([w, "--sidecar", s, "--against", b["wav"], "--pairs", "3=1,4=2", "--out",
                    os.path.join(d, "h5cal.align.json")]) == 0
    # the embedded calibration is verified against its files
    m = json.load(open(j))
    m["result"]["ratios"]["4"] = 1.0
    json.dump(m, open(j, "w"))
    z = ca.analyse(w, s)
    assert any("does not match the embedded sha256" in r for r in z["capture"]["reasons"]), z["capture"]
    m = json.load(open(s))
    m["calibration"]["sha256"] = capture.sha256(j)
    json.dump(m, open(s, "w"))
    z = ca.analyse(w, s)
    assert any("!= the calibration file's" in r for r in z["capture"]["reasons"]), z["capture"]
    m["calibration"]["path"] = os.path.join(d, "gone", "nothing.json")
    json.dump(m, open(s, "w"))
    assert any("not found" in r for r in ca.analyse(w, s)["capture"]["reasons"])
    m["calibration"]["ratios"] = {"3": 1.0}
    json.dump(m, open(s, "w"))
    z = ca.analyse(w, s)
    assert not z["chain_calibration"]["applied"] and any("no positive finite ratio for analysed channel 4" in r
                                                         for r in z["capture"]["reasons"]), z["capture"]
    # a calibration take is the measurement itself: analysed raw, no reason
    c = ca.analyse(os.path.join(d, "calA2.wav"), os.path.join(d, "calA2.json"))
    assert c["chain_calibration"]["calibration_take"] and not any("chain" in r for r in c["capture"]["reasons"])


def test_h5_unused_channels(d):
    """Channels labelled unused (the H5studio's MIC capsule, digital zero) are not analysed, paired or deviations, but
    still checked: full-scale, non-finite, and any signal at or above -80 dBFS (a wrong map) are reasons."""
    cal = unity_calibration(d, 4)
    w, s = h5_take(d, "h5u", cal, r_db=0.0)
    a = ca.analyse(w, s)
    assert a["comparable_channels"] == [3, 4] and a["analysed_channels"] == [3, 4] and a["capture"]["valid"], \
        a["capture"]
    assert [(u["channel"], u["digital_zero"], u["peak_dbfs"]) for u in a["unused_channels"]] == [
        (1, True, None), (2, True, None)]
    assert a["detection"]["deviations"] == [] and a["matched_hits"] == 3 and a["chain_calibration"]["applied"]
    for name, fn, needle, st in (
            ("leak", lambda u1, u2: u1.__setitem__(FRAMES[0], 0.01), "labelled 'unused' but peaks at -40.0", "pcm"),
            ("faint", lambda u1, u2: u2.__setitem__(100, 2e-5), None, "pcm"),
            ("clip", lambda u1, u2: u2.__setitem__(100, 1.0), "full-scale samples", "pcm"),
            ("nan", lambda u1, u2: u1.__setitem__(100, math.nan), "non-finite", "float")):
        w2, s2 = h5_take(d, "h5u" + name, cal, r_db=0.0, unused=fn, sample_type=st, bits=32)
        x = ca.analyse(w2, s2)
        if needle is None:                            # -94 dBFS on an unused channel: below -80 dBFS, accepted
            assert x["capture"]["valid"] and not x["unused_channels"][1]["digital_zero"], x["capture"]
        else:
            assert not x["capture"]["valid"] and any(needle in r for r in x["capture"]["reasons"]), (name, x["capture"])
    # capture.py: unused channels do not count towards the stereo L/R pair; the map must name every channel
    a4 = plan_args("--track-channel", "1", "--channels", "4", "--channel-map", "1=unused,2=unused,3=Rhythm L,4=Rhythm R")
    assert capture.protocol_deviations(a4) == [] and capture.unused_channels(a4) == [1, 2]
    dev = capture.protocol_deviations(plan_args("--track-channel", "1", "--channels", "4", "--channel-map",
                                                "1=Mic,2=unused,3=Rhythm L,4=Rhythm R"))
    assert dev == ["channels=4 with [2] unused, 3 used (protocol 2)"], dev
    real = no_devices()
    try:
        base = ["live", "--source", "triangle-positive", "--alsa-device", "hw:9,0", "--track-channel", "1", *PROTO_REQ,
                "--channels", "4"]
        assert "every channel" in str(raises(SystemExit, capture.main, base))           # default 2-channel map
        assert "every channel" in str(raises(SystemExit, capture.main,
                                             base + ["--channel-map", "1=unused,3=Rhythm L,4=Rhythm R"]))
        assert "labelled 'unused'" in str(raises(SystemExit, capture.main, base + [
            "--channel-map", "1=unused,2=unused,3=Rhythm L,4=Rhythm R", "--ref-kind", "din-tap", "--ref-channel", "1"]))
        assert "every channel is labelled" in str(raises(SystemExit, capture.main, [
            "plan", "--source", "triangle-positive", "--channel-map", "1=unused,2=unused"]))
    finally:
        restore(real)


FAKE_RECORDER_H5 = FAKE_RECORDER.replace(
    'midi, rate = sys.argv[1], 48000',
    'midi, rate, gains = sys.argv[1], 48000, [float(g) for g in sys.argv[3].split(",")]').replace(
    'if len(sys.argv) > 2:', 'if sys.argv[2] != "-":').replace(
    '        s = int(v * 8388607).to_bytes(3, "little", signed=True)\n        buf += s + s',
    '        for g in gains:\n            buf += int(v * g * 2147483647).to_bytes(4, "little", signed=True)')


def test_calibrate_live_with_fakes(d):
    """capture.py calibrate on the fake recorder (4-channel S32_LE H5 layout, input 2 at -0.66 dB): an accepted
    calibration JSON next to its raw WAV recovers the ratio; a live take with --calibration embeds it and analyses
    calibrated; a cable-swap difference over the bound refuses the calibration, and a refused one cannot be used."""
    assert "for g in gains" in FAKE_RECORDER_H5 and 'sys.argv[2] != "-"' in FAKE_RECORDER_H5
    rec = os.path.join(d, "fake_arecord_h5.py")
    open(rec, "w").write(FAKE_RECORDER_H5)
    gains = f"0,0,1,{10 ** (IMB_DB / 20)!r}"
    h5 = ["--alsa-device", "fake", "--format", "S32_LE", "--channels", "4", "--channel-map",
          "1=unused,2=unused,3=Rhythm L,4=Rhythm R", "--track-channel", "1", *PROTO_REQ, "--note-off-after", "0.1",
          "--idle-before", "0", "--settle", "0"]

    def run(cmd, out, *extra):
        midi = out + ".midi"
        open(midi, "wb").close()
        return capture.main([cmd, "--source", "triangle-positive", "--midi-device", midi, "--recorder-cmd",
                             f"{sys.executable} -I {rec} {midi} - {gains}", *h5, "--out", out, *extra]
                            + (["--plain"] if cmd == "calibrate" else []))
    out = os.path.join(d, "calrun")
    run("calibrate", out, "--lead", "0.2", "--spacing", "0.4", "--duration", "2.4", "--swap-diff-db", "0.05",
        "--swap-evidence", "same cable and input 1 fed from Rhythm L then R: equal within 0.05 dB")
    j, cal = only_json(out)
    assert os.path.basename(j).startswith("calibration-") and cal["schema"] == ca.CALIBRATION_SCHEMA
    assert cal["accepted"] and cal["status"] == "accepted", cal["reasons"]
    res = cal["result"]
    assert abs(res["channels"]["4"]["mean_db"] - IMB_DB) < 0.01 and res["channels"]["4"]["n"] == 5, res["channels"]
    assert res["analysed_channels"] == [3, 4] and all(u["digital_zero"] for u in res["unused_channels"])
    assert cal["chain"]["channel_map"] == H5MAP and cal["chain"]["gain_note"] == "g" and cal["chain"]["channels"] == 4
    assert cal["swap_evidence"]["rhythm_r_minus_l_db"] == 0.05 and "attribution" not in cal
    assert cal["target"]["peak_dbfs"] == -16.0 and cal["target"]["tolerance_db"] == 1.0
    assert [g["name"] for g in res["gates"]] == ["hits", "clipping", "floor", "capture valid", "shape", "polarity",
                                                 "spread", "swap evidence"]
    assert all(g["status"] == "PASS" for g in res["gates"]), res["gates"]
    tk = cal["take"]
    assert tk["schema"] == capture.SCHEMA and tk["status"] == "complete" and tk["calibration"] is None
    assert "duration=2.4 (protocol 7.0)" in tk["protocol_deviations"], tk["protocol_deviations"]
    wav = os.path.join(out, tk["wav"]["file"])
    assert capture.sha256(wav) == tk["wav"]["sha256"] and wavio.read_info(wav)["channels"] == 4
    out2 = os.path.join(d, "calrun-take")
    run("live", out2, "--duration", "1.6", "--lead", "0.2", "--spacing", "0.4", "--calibration", j)
    s, m = only_json(out2)
    assert m["calibration"]["sha256"] == capture.sha256(j) and m["calibration"]["ratios"] == res["ratios"]
    assert not any(x.startswith("calibration") for x in m["protocol_deviations"]), m["protocol_deviations"]
    r = ca.analyse(os.path.join(out2, m["wav"]["file"]), s, window_s=0.2)
    assert r["chain_calibration"]["applied"] and r["capture"]["valid"] and r["matched_hits"] == 3, r["capture"]
    lv = {x["channel"]: x["rms"] for x in r["levels"]}
    assert all(abs(20 * math.log10(q / p)) < 0.01 for p, q in zip(lv[3], lv[4])), lv
    out3 = os.path.join(d, "calrun-bad")
    e = raises(SystemExit, run, "calibrate", out3, "--lead", "0.2", "--spacing", "0.4", "--duration", "2.4",
               "--swap-diff-db", "0.5")
    assert "REFUSED" in str(e) and "cable swap" in str(e)
    j3, bad = only_json(out3)
    assert not bad["accepted"] and bad["status"] == "refused" and bad["result"]["accepted"]
    assert "not accepted" in str(raises(SystemExit, run, "live", os.path.join(d, "calrun-x"), "--calibration", j3))


# ---------------------------------------------------------------- level view (Textual), headless

def bar_line(app, wid):
    """The bar itself: bar_text's second line, full width (app.bar_width cells)."""
    line = widget_text(app, wid).split("\n")[1]
    assert len(line) == app.bar_width >= 150, (len(line), app.bar_width)  # 160-column test terminal: whole line
    return line


def lv_hit(r_db=IMB_DB, skew=-1, peak=H5_PEAK, invert=False, seed=3):
    rng = random.Random(seed)
    n = int(0.6 * RATE)
    L = [rng.gauss(0, 3e-5) for _ in range(n)]
    R = [rng.gauss(0, 3e-5) for _ in range(n)]
    g = (-1 if invert else 1) * 10 ** (r_db / 20)
    for i, v in enumerate(tri(4096, 64, peak)):
        L[7200 + i] += v
        R[7200 + i + skew] += g * v
    return lv.measure([L, R], RATE)


def test_levelview_render(d):
    """The level view's pure parts: measure (aligned ratio, delay, floor), target offset text and colour, the bar's
    target marker and tolerance band, the gauge zone, the history, the waveform overlay, gate lines."""
    m = lv_hit()
    assert abs(m["ratio_db"][1] - IMB_DB) < 0.01 and m["delay_samples"][1] == -1.0 and m["ratio_sign"][1] == 1, m
    assert abs(m["peak_dbfs"][0] - 20 * math.log10(H5_PEAK)) < 0.05 and -85 < m["floor_dbfs"][0] < -75, m
    assert lv.target_offset(-15.6, -16.0, 1.0) == {"offset_db": 0.4, "colour": "green", "text": "+0.4 dB, turn down"}
    assert lv.target_offset(-18.5, -16.0, 1.0)["colour"] == "yellow" and "turn up" in lv.target_offset(-18.5, -16, 1)[
        "text"]
    assert lv.target_offset(-12.0, -16.0, 1.0)["colour"] == "red"
    cells = lv.bar_cells(-15.9, -24.4, -15.9, -80.0, -16.0, 1.0)
    t, lo, hi = lv._pos(-16.0), lv._pos(-17.0), lv._pos(-15.0)
    assert cells[t][0] == "│" and "yellow" in cells[t][1], cells[t]               # marker on top of the hold
    assert all("grey27" in cells[i][1] for i in range(lo, hi + 1)) and "grey27" not in cells[hi + 1][1]
    assert all("red" not in c[1] for c in cells[:lv._pos(-1.0)]) and cells[-1][1].endswith("dark_red")
    assert "red" in lv.bar_cells(-0.5, -6, None, None, -16, 1)[0][1]               # clip colours the fill
    g = lv.gauge_cells(0.1)
    assert [c[0] for c in g].count("●") == 1 and any(c[0] == "●" and "green" in c[1] for c in g)
    assert any(c[0] == "●" and "red" in c[1] for c in lv.gauge_cells(-0.5)) and lv.gauge_cells(3.0)[-1][0] == "▶"
    st = lv.ViewState([(3, "Rhythm L"), (4, "Rhythm R")], -16.0, 1.0, 5)
    st.add_hit(m)
    st.add_hit(lv_hit(r_db=IMB_DB + 0.05))
    assert "spread 0.050 dB over 2 hits" in str(lv.history_text(st)), str(lv.history_text(st))
    assert "+0.1 dB, turn down" in str(lv.bar_text("ch3 Rhythm L", m, 0, st))
    assert "INVERTED" in str(lv.gauge_text(lv_view(lv_hit(invert=True))))
    sine = [math.sin(2 * math.pi * i / 64) for i in range(128)]
    inv = lv.braille([sine, [-v for v in sine]], width=16, rows=4)     # inverted: the traces part, mirror images
    assert {frozenset(o) for row in inv for _, o in row} >= {frozenset({0}), frozenset({1})}
    assert inv[0][2][1] == {0} and inv[3][2][1] == {1}, (inv[0][2], inv[3][2])          # sample 16: the peak
    same = lv.braille([sine, list(sine)], width=16, rows=4)            # identical: every dot is both
    assert all(o in (set(), {0, 1}) for row in same for _, o in row)
    st.gates = [{"name": "spread", "status": "PASS", "value": "0.0010 dB", "threshold": "<= 0.1 dB max-min"},
                {"name": "swap evidence", "status": "FAIL", "value": "+0.5 dB", "threshold": "|Rhythm R - L| <= 0.1 dB"}]
    gt = str(lv.gates_text(st))
    assert "PASS  spread" in gt and "FAIL  swap evidence" in gt and "pilot hits 2/5" in gt, gt


def lv_view(m):
    st = lv.ViewState([(3, "Rhythm L"), (4, "Rhythm R")], -16.0, 1.0)
    st.add_hit(m)
    return st


def widget_text(app, wid):
    return str(app.query_one(wid).content)


def lv_args(d, name, *extra, pid=None):
    """calibrate options for the fake H5 recorder (as test_calibrate_live_with_fakes), parsed and normalised."""
    rec = os.path.join(d, "fake_arecord_h5.py")
    open(rec, "w").write(FAKE_RECORDER_H5)
    midi = os.path.join(d, name + ".midi")
    open(midi, "wb").close()
    gains = f"0,0,1,{10 ** (IMB_DB / 20)!r}"
    argv = ["calibrate", "--source", "triangle-positive", "--midi-device", midi, "--recorder-cmd",
            f"{sys.executable} -I {rec} {midi} {pid or '-'} {gains}", "--alsa-device", "fake", "--format", "S32_LE",
            "--channels", "4", "--channel-map", "1=unused,2=unused,3=Rhythm L,4=Rhythm R", "--track-channel", "1",
            *PROTO_REQ, "--idle-before", "0", "--settle", "0", "--out", os.path.join(d, name), *extra]
    return capture.normalise(capture.parser().parse_args(argv)), midi


def test_levelview_pilot_calibrate(d):
    """capture.py calibrate in the level view, driven headless by Textual's Pilot on the fake recorder: live bars with
    the target marker and offset text, gauge, per-hit progress, every gate PASS with its threshold, the file path;
    q quits after the run and returns run_calibration's result."""
    a, _ = lv_args(d, "tuiA", "--lead", "0.2", "--spacing", "0.4", "--duration", "2.4", "--note-off-after", "0.1",
                   "--swap-diff-db", "0.05", "--target-peak", "-12", "--target-tolerance", "0.5")
    capture.CANCEL.clear()
    st, work = lv.calibrate_view(a)
    app = lv.make_app(st, work, capture.CANCEL)

    async def drive():
        async with app.run_test(size=(160, 50)) as pilot:
            for _ in range(400):
                if app.done.is_set():
                    break
                await pilot.pause(0.05)
            assert app.done.is_set(), "calibration did not finish"
            await pilot.pause(0.1)
            bar = widget_text(app, "#bar0")
            assert bar.startswith("ch3 Rhythm L") and bar_line(app, "#bar0")[lv._pos(-12.0, app.bar_width)] == "│", bar
            scale = widget_text(app, "#scale").split("\n")
            assert scale[0][lv._pos(-12.0, app.bar_width)] == "┴" and "-48" in scale[1] and scale[1].rstrip().endswith(" 0"), scale
            # the fake peaks at -12.04 (ch3) and -12.70 dBFS (ch4): on target, and amber outside the 0.5 dB band
            assert "-0.0 dB, on target" in bar, bar
            off = lv.target_offset(st.hits[-1]["peak_dbfs"][1], -12.0, 0.5)
            assert off == {"offset_db": -0.7, "colour": "yellow", "text": "-0.7 dB, turn up"}, off
            assert "-0.7 dB, turn up" in widget_text(app, "#bar1")
            spans = app.query_one("#bar1").content.spans
            assert any("yellow" in str(sp.style) and sp.end - sp.start == len(off["text"]) for sp in spans), spans
            assert "-0.660 dB" in widget_text(app, "#gauge") and "MIDI" in widget_text(app, "#status")
            gates = widget_text(app, "#gates")
            assert "pilot hits 5/5" in gates and "ACCEPTED" in gates and "calibration file: " in gates, gates
            for name in ("hits", "clipping", "floor", "spread", "swap evidence"):
                assert f"PASS  {name}" in gates, (name, gates)
            assert "target peak -12.0 dBFS +-0.5 dB: information, not a gate" in gates, gates
            log = [str(line.text) for line in app.query_one("#log").lines]     # one scrolling line per hit, kept
            assert len(log) == 5 and log[0].startswith("#1 ") and log[4].startswith("#5 "), log
            assert all("ch3  -12.04 dBFS" in ln and "ch4-ch3  -0.66 dB" in ln and "delay" in ln for ln in log), log
            await pilot.press("q")
    asyncio.run(drive())
    cal, wav, side, exc = app.return_value
    assert exc is None and cal["accepted"] and os.path.exists(side) and os.path.exists(wav)
    assert cal["target"] == {"peak_dbfs": -12.0, "tolerance_db": 0.5,
                             "role": "information for setting the gains; not a gate"}
    assert len(st.hits) == 5 and all(abs(h["ratio_db"][1] - IMB_DB) < 0.01 for h in st.hits)


def test_levelview_pilot_quit(d):
    """q while a note is held: the run stops, the pending note-off goes out, arecord is terminated, the calibration
    JSON says interrupted."""
    pidf = os.path.join(d, "tuiQ.pid")
    a, midi = lv_args(d, "tuiQ", "--note-off-after", "0.8", pid=pidf)
    capture.CANCEL.clear()
    st, work = lv.calibrate_view(a)
    app = lv.make_app(st, work, capture.CANCEL)

    async def drive():
        async with app.run_test(size=(160, 50)) as pilot:
            for _ in range(200):
                if b"\x9f" in open(midi, "rb").read():
                    break
                await pilot.pause(0.05)
            await pilot.press("q")
            for _ in range(200):
                if app.done.is_set():
                    break
                await pilot.pause(0.05)
            await pilot.pause(0.2)
    asyncio.run(drive())
    cal, wav, side, exc = app.return_value
    assert isinstance(exc, capture.Cancelled) and cal["status"] == "interrupted", cal["status"]
    sent = open(midi, "rb").read()
    assert sent.endswith(bytes.fromhex("8f2400")) and sent.count(b"\x9f") == 1, sent[-12:].hex()
    assert cal["take"]["stimulus"]["cleanup_note_off"] == "8f2400"
    assert dead(int(open(pidf).read())), "recorder still running"
    capture.CANCEL.clear()


def test_levelmeter_plain_and_view(d):
    """tools/levelmeter.py through the shared view: --plain keeps one line per hit (today's format, now the aligned
    ratio); the level view shows the bars, target marker and offset; q sends the note-off and stops arecord."""
    rec = os.path.join(d, "fake_arecord_h5.py")
    open(rec, "w").write(FAKE_RECORDER_H5)
    gains = f"0,0,1,{10 ** (IMB_DB / 20)!r}"

    def argv(name, *extra):
        midi = os.path.join(d, name + ".midi")
        open(midi, "wb").close()
        return midi, ["--midi-device", midi, "--recorder-cmd", f"{sys.executable} -I {rec} {midi} "
                      f"{os.path.join(d, name + '.pid')} {gains}", "--period", "0.5", *extra]
    import contextlib
    import io
    midi, args = argv("lmP", "--plain", "--hits", "2")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        levelmeter.main(args)
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("metering channels 3,4") and len(lines) == 3, lines
    for ln in lines[1:]:
        parts = ln.split()
        assert parts[0] == "ch3" and parts[3] == "ch4" and parts[6] == "ch4-ch3", ln
        assert abs(float(parts[7]) - IMB_DB) < 0.011, ln
    assert open(midi, "rb").read() == bytes.fromhex("9f247f8f2400") * 2
    assert dead(int(open(os.path.join(d, "lmP.pid")).read()))
    midi, args = argv("lmV", "--target-peak", "-13", "--target-tolerance", "1")
    a = levelmeter.parser().parse_args(args)
    stop = threading.Event()
    app, st = levelmeter.meter_app(a, [3, 4], {"3": "Rhythm L", "4": "Rhythm R"}, stop)

    async def drive():
        async with app.run_test(size=(160, 50)) as pilot:
            for _ in range(200):
                if len(st.hits) >= 2:
                    break
                await pilot.pause(0.05)
            await pilot.pause(0.1)
            bar = widget_text(app, "#bar0")
            assert bar_line(app, "#bar0")[lv._pos(-13.0, app.bar_width)] == "│", bar
            off = lv.target_offset(st.hits[-1]["peak_dbfs"][0], -13.0, 1.0)
            assert off["text"] in bar and off["text"].endswith(("turn up", "turn down", "on target")), (off, bar)
            assert "hits " in widget_text(app, "#status") and "-0.660 dB" in widget_text(app, "#gauge")
            await pilot.press("q")
            for _ in range(100):
                if app.done.is_set():
                    break
                await pilot.pause(0.05)
            await pilot.pause(0.1)
    asyncio.run(drive())
    assert app.return_value == len(st.hits) >= 2
    sent = open(midi, "rb").read()
    assert sent.endswith(bytes.fromhex("8f2400")) and sent.count(b"\x9f") == sent.count(b"\x8f"), sent.hex()
    assert dead(int(open(os.path.join(d, "lmV.pid")).read()))


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    with tempfile.TemporaryDirectory() as d:
        for t in tests:
            t(d)
            print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
