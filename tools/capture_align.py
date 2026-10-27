"""Offline analysis of a reference capture: onsets -> stimulus schedule -> per-hit windows, tails and metrics.

    uv run python -I tools/capture_align.py TAKE.wav [--sidecar TAKE.json] [--out REPORT.json] [--channels 1,2]
        [--against REF.wav [--against-sidecar REF.json] --pairs 1=1,2=2] [--segments DIR] [--no-calibration]
        [detection options]

Active window: 200 ms total, from 2 ms before the detected onset to 198 ms after, identical for
every hit and take. It is never zero-padded: a window that leaves the file makes the hit incomplete. The full
post-hit tail (onset to 2 ms before the next onset, i.e. the next window's start, or end of file) is reported separately with the tail figures.

Definitions (also written into every report):
  cosine       sum(a*b) / sqrt(sum(a^2) * sum(b^2)) over raw samples, no mean removal
  correlation  the same over mean-removed vectors (Pearson)
  gain g       least-squares gain of B onto A: sum(a*b) / sum(b^2)
  NRMSE        sqrt(sum((a - g*b)^2) / sum(a^2))

Two kinds of comparison:
  * diagnostics (pairwise hits within a take, --against per hit): each pair gets its own lag and gain. Shape-only
    flag cosine >= 0.99. Never a fidelity verdict.
  * whole-take verdict (--against): each hit its own fractional-sample lag of B within +-1 ms of the hit's fitted
    schedule position (drift term included; the same lag for all channels of the hit; a best lag at the bound
    fails), and ONE gain for all hits and channel pairs together; cosine and NRMSE computed jointly over all hits.
    The lags are reported; the schedule/timing diagnostics must pass separately. Passes only if cosine >= 0.99 AND
    NRMSE <= 0.05 AND no hit's lag at the bound AND no inter-channel skew over SKEW_MAX within a hit AND waveform lags
    consistent with the onsets (TIMING_SPREAD_MAX) AND every expected hit/window is
    complete in both takes AND both captures are valid (metadata present, complete and bound to the WAV, no
    full-scale or non-finite samples, no recorder overrun/xrun/pipe error, rate and length ok) AND no unexplained
    extra onset on either side AND no signal past the 198 ms window or in the 100 ms before the first hit on either
    side (on the common scale, below) AND a recorder-chain calibration on every hardware take with more than one
    analysed channel (CHAIN CALIBRATION, below) AND every hit loud enough for level-independent onset timing (envelope peak at
    least 30 dB over its onset bound) AND the same source on both sides AND every channel of each file (a declared
    timing-reference channel excepted) paired exactly once AND the hit ids at the same relative times on both sides
    AND comparable noise floors (FLOOR_TOL_DB, on the common scale) AND protocol detection settings (see DETECTION)
    AND no recorded protocol deviation in either sidecar AND a schedule offset inside its bound where one applies
    (OFFSET_BOUNDS; only path b is unbounded) AND a plausible take kind and timing (render onsets at its frames, no
    excess drift; see below).
    Common scale: B's level is arbitrary, so level-dependent tests (noise floor, signal past the window or before
    the first hit) map both takes onto one scale with the fitted gain: the hardware capture's when exactly one side
    is a capture, else the louder take's. The result does not depend on the argument order or on B's level.
    Metadata is <stem>.json (capture sidecar) or <stem>.render.json (render provenance, see render_meta()), found
    automatically for both files. Without metadata a file gets diagnostics only. 'silence' and 'no-note' takes are
    silent-audio controls: no hit is expected and no waveform verdict is given.

Chain calibration (analysis policy): the recorder's input gains cannot be hand-matched, so a per-session ratio per
channel, frozen by `capture.py calibrate` (5 pilot hits, schema circuit-rhythm-calibration/1) and embedded in the
take's sidecar, divides each analysed channel of a capture before any analysis (the WAV is never changed). Renders
are never calibrated. A capture analysing more than one channel without one is invalid ("no chain calibration");
--no-calibration analyses it raw as a recorded deviation. Channels labelled "unused" in the channel map (an
interface's switched-off inputs) are neither analysed nor paired, but are checked for full-scale and non-finite
samples and must stay below -80 dBFS.

Not measured: note-to-analog latency. The schedule offset is a fit, only bounded for plausibility (OFFSET_BOUNDS).

Exit status: 0 report written (and, with --against, the whole-take verdict passed); 1 report written but the
whole-take verdict did not pass (failed, or not applicable to a control take); 2 refused (bad arguments or input).
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wavio  # noqa: E402

COSINE_TARGET = 0.99
NRMSE_TARGET = 0.05
LAG_WINDOW = 4096          # samples used to pick a per-pair diagnostic lag (85 ms at 48 kHz = one fixture length)
# whole-take verdict alignment (analysis policy): each hit gets its own fractional-sample lag of B, searched within
# +-LAG_BOUND_MS of the hit's fitted schedule position, the same lag for every channel of the hit; one gain for the
# take. A best lag at the bound is a reason (unaligned). The lag is refined by parabolic interpolation of |cosine|
# around the best integer lag and applied with a band-limited fractional delay (Hann-windowed sinc, 2*SINC_HALF+1
# taps, unit DC gain). SKEW_MAX: largest difference between the channels' own lags within a hit (an inter-channel delay that the
# render does not have is a real difference: stereo L/R fidelity).
LAG_BOUND_MS = 1.0
SINC_HALF = 32
SKEW_MAX = 0.5             # samples
# a fractional lag part below SNAP_FRAC samples is applied as 0 (no sinc taps, so a window ending near the file's end
# still compares): a 0.001-sample delay is a phase error of 2.6e-3 rad at 20 kHz, an NRMSE contribution of about 0.003
SNAP_FRAC = 1e-3
# lag choice: local maxima of |cosine| within LAG_TIE of the best one are a tie, broken towards the lag nearest the
# onset detector's lag among those within LAG_AGREE samples of it (on periodic content a half-period lag of an
# inverted copy and the true lag score alike; the onsets, which do not depend on polarity, say which one is the hit);
# when none agrees, the best |cosine|. The sign is never used to choose, so an inverted take keeps its negative
# cosine, and a hit the onsets place out of reach is not moved to a half-period alias of the other polarity.
# LAG_AGREE: honest waveform and onset lags agree within about 1 sample; the fixture's half period is 32
LAG_TIE = 1e-3
LAG_AGREE = 4              # samples
# onset timing (carrier-independent): a trigger (first |x| over the detection threshold, DETECTION) finds each hit;
# its onset is then the threshold crossing, interpolated between samples, of the hit's energy envelope: the mean of
# x^2 over the analysed channels smoothed by two trailing ENVELOPE_MS boxes (a 2 ms triangular kernel; the protocol
# fixtures' carrier period is 1.33 ms, so its ripple is a fraction of a dB). The threshold is ONSET_REL_DB below the
# hit's own envelope peak (within the active window after the trigger), bounded below by max(floor + 6 dB,
# -80 dBFS) (ONSET_FLOOR_DB, TAIL_FALLBACK; floor = the detection floor below), the same rule for every take, so
# the crossing sits at the same point of the attack whatever the take's level, as long as the peak is at least
# ONSET_REL_DB above that bound. A hit whose peak is not (the threshold is the bound: its crossing moves along the
# attack with the level) fails the verdict as too quiet for onset timing. The floor is a 1 ms block PEAK, about 10 dB
# above the RMS envelope of the same noise, so floor + 6 dB is about 16 dB above the noise's envelope (a threshold
# bias of 0.1 dB); 6 rather than 10 dB lets a sharp drum (envelope peak 8.7 dB below its sample peak) at -35 dBFS
# over -90 dBFS RMS noise keep a relative threshold
ENVELOPE_MS = 1.0
ONSET_REL_DB = 30.0
ONSET_FLOOR_DB = 6.0
# the foot of an attack (where its envelope stops falling when followed back from the onset) is searched at most
# MAX_ATTACK_LEAD_MS before the onset crossing: the next hit's attack is excluded from the previous tail (and from the
# region before the first hit) only that far back, so a falling-then-rising swell or precursor before a hit cannot
# hide in it. Honest leads from the foot to the crossing (envelope, 20 Hz / 12 kHz chain, -6..-35 dBFS and renders):
# 10 ms raised-cosine attack up to 2.1 ms, 10 ms dB-linear from -80 dB up to 7.3 ms; 20 ms leaves a factor 2.7
MAX_ATTACK_LEAD_MS = 20.0
# timing consistency (verdict-blocking, the proposed bound, open for confirmation on the first real capture): per hit,
# d = waveform lag - onset-detector lag (B's onset minus A's onset minus the schedule shift, both onsets
# interpolated); the spread of d over the hits must be at most TIMING_SPREAD_MAX samples. Set from 660 honest
# synthetic takes (attacks 0.3/3/10 ms raised-cosine and dB-linear, -30..-10 dBFS,
# renders at -40..-1 dBFS, +-0.25 ms jitter, 50 ppm drift, a 20 Hz / 12 kHz analog chain, -90 dBFS noise): largest
# spread 0.59 samples (10 ms dB-linear attack at -30 dBFS; the spread is noise-driven, about inversely proportional to
# the hit's level over the floor), so 1.5 leaves a factor 2.5 (8 dB of signal-to-noise). A hit whose shape moved
# inside its window (a dropped attack: over 10 samples for 20 dropped; an alias half a period or a period away: 32,
# 64) does not stay inside it.
TIMING_SPREAD_MAX = 1.5    # samples
# a render is deterministic: its onsets must sit at a constant offset from its declared frames (residual spread)
RENDER_JITTER_MAX = 2.0    # samples
# linear clock drift of each take's schedule (onset = offset + (1 + drift) * event time), fitted with >= 3 matched hits
# by least squares, clamped to +-DRIFT_MAX_PPM for the search centre. A fit beyond the bound is a reason only when the
# clamped schedule leaves a matched onset more than LAG_BOUND_MS away: on 3 hits over 4 s, 1 ms of honest per-hit
# jitter alone fits as about 100 ppm and is not drift
DRIFT_MAX_PPM = 100.0
# take-kind plausibility on the silence floor (median 1 ms block peak, DC removed): a render's floor above
# RENDER_FLOOR_MAX (-120 dBFS; 24-bit dither is about -139) looks analog, a capture's floor of exactly 0 is digital
RENDER_FLOOR_MAX = 1e-6
WINDOW_S, PRE_MS = 0.200, 2.0
TAIL_HOLD_MS = 20
PRE_MARGIN_S = 0.1         # gap between the silence-floor region and the first onset
# protocol detection settings. A verdict needs settings at least this sensitive: a higher threshold or floor, a
# wider matching tolerance, a longer re-arm time (refractory) than half the scheduled spacing, another window or lag
# search, or a restricted channel set can hide an onset or misplace a hit, so it is recorded as a detection deviation
# and blocks the verdict.
DETECTION = {"threshold_db": 20.0, "abs_floor_db": -70.0, "tolerance_ms": 50.0, "pre_ms": PRE_MS, "window_s": WINDOW_S,
             "max_lag": 32}
TAIL_FALLBACK = 1e-4       # -80 dBFS: lower bound of the past-window threshold, max(floor + 10 dB, -80 dBFS); the
                           # threshold when the floor is zero (digital silence) or unavailable
# noise-floor gate: neither take's floor may exceed the other's by more than FLOOR_TOL_DB, both on the common scale.
# A floor below FLOOR_REF (-80 dBFS on the common scale, digital silence included) counts as FLOOR_REF: a hardware
# take always has an analog floor above a digitally silent render, and that difference alone must not fail; added
# signal (hum) above about -70 dBFS on either side does. The past-window threshold uses the same -80 dBFS bound.
FLOOR_TOL_DB, FLOOR_REF = 10.0, 1e-4
# channel-map label of a channel that carries nothing (e.g. the H5studio's switched-off MIC capsule on USB 1/2): not
# analysed or paired, still checked for full-scale/non-finite samples, and it must stay below TAIL_FALLBACK (-80 dBFS)
UNUSED_LABEL = "unused"
# recorder-chain calibration (analysis policy 2026-10-17): one frozen ratio per channel from CAL_MIN_HITS pilot hits per
# session; the per-hit ratios (dB) may spread at most CAL_SPREAD_MAX_DB (max - min), and each hit's channel must match
# the reference channel's shape (COSINE_TARGET, NRMSE_TARGET), else the calibration is refused
CALIBRATION_SCHEMA = "circuit-rhythm-calibration/1"
CAL_SPREAD_MAX_DB = 0.1
CAL_MIN_HITS = 5
PROTOCOL_CHANNELS = 2      # stereo L/R (capture.PROTOCOL): a timing reference must be a channel beyond these
# schedule offset bounds (onset minus event time). A render declares its events at WAV frames, so its offset must be
# about 0 (within the onset tolerance). A live capture (path "a") with host send times has its anchor at the
# estimated host time of sample 0, which is never earlier than the true one (first-chunk arrival minus its frames),
# so the offset is the note-to-analog latency plus capture buffering: >= 0 and, for USB MIDI, the unit's audio block,
# the converters and USB audio, well under LATENCY_MAX_MS. 100 ms is half the active window and 1/20 of the 2 s
# spacing, so a shift of one spacing (a silent first note plus an extra hit, hits relabelled) cannot pass. Only
# path "b" (the recorder's own SD card: the recorder's start is unknown) is not bounded; a capture with no path,
# another path value, or path "a" without valid host send times on its anchor is a reason (nothing to bound it by).
LATENCY_MAX_MS = 100.0
# runtime bounds: schedule fitting is O(onsets^2 * events^2) and the pairwise diagnostics O(hits^2); larger inputs
# are refused with a reason (the protocol has 3 hits)
MAX_EVENTS, MAX_ONSETS = 64, 256
DEFINITIONS = {
    "cosine": "sum(a*b)/sqrt(sum(a^2)*sum(b^2)) over raw samples, no mean removal",
    "correlation": "Pearson: cosine of the mean-removed vectors",
    "gain": "least-squares gain of B onto A: sum(a*b)/sum(b^2)",
    "nrmse": "sqrt(sum((a-g*b)^2)/sum(a^2)) with g the gain above",
    "active_window": "2 ms before the detected onset to 198 ms after (200 ms), never zero-padded",
    "envelope": "1 ms non-overlapping blocks, max |x| over the analysed channels, from the onset",
    "last_crossing_ms": "end of the last envelope block >= threshold, from the onset",
    "sustained_below_ms": f"start of the first run of >= {TAIL_HOLD_MS} ms of blocks all < threshold",
    "censored": "envelope still >= threshold within the last hold period of the tail region (tail reaches the next "
                "hit or the end of the file), or never sustained below",
    "noise_limited": "threshold is below the measured floor (median 1 ms block peak of the pre-hit region)",
    "dc_removed": "each analysed channel's mean over the silence-floor region (the whole channel when there is no "
                  "such region) is subtracted before the floor and the tail envelope are measured; all other figures "
                  "use the stored samples",
    "tail_region": "from the onset to 2 ms before the next onset (the next hit's window start) or the foot of the next "
                   "hit's attack if earlier (where its envelope stops falling, followed back, at most "
                   f"{MAX_ATTACK_LEAD_MS:g} ms before its onset), or the end of the file",
    "before_first_hit": f"the {PRE_MARGIN_S * 1000:g} ms between the silence-floor region and the first onset, up to "
                        "its window start or attack foot as for the tails: any 1 ms block peak at or above the "
                        "past-window threshold is signal before the first hit (per take on its own scale, in the "
                        "verdict on the common scale)",
    "signal_past_window": "last crossing of max(floor+10dB, -80 dBFS) later than the window's end after the onset; "
                          "per take on its own scale (diagnostic), in the verdict on the common scale",
    "common_scale": "the hardware capture's scale when exactly one side is a capture, else the louder take's; the "
                    "other take is mapped onto it with the fitted gain",
    "raw": "integer PCM codes as stored (float files: stored float values); no analog mapping of a DSP LSB",
    "lag": "verdict, per hit: B's window position minus (A's window start + the difference of the two fitted schedule "
           "positions, drift term included, rounded to whole samples), in samples; positive = B's hit later against "
           f"its schedule than A's. Integer search within +-{LAG_BOUND_MS:g} ms by |cosine| over all channels of the "
           f"hit (local maxima within {LAG_TIE:g} of the best: the one nearest the onset detector's lag if within "
           f"{LAG_AGREE} samples of it, else the best; the sign is kept and never used to choose), refined by "
           "parabolic interpolation "
           "of |cosine| at the best integer lag and its neighbours (not past the bound); a best integer lag at the "
           "bound is a reason",
    "fractional_delay": f"B read at the fractional lag by a Hann-windowed sinc, {2 * SINC_HALF + 1} taps, unit DC "
                        "gain; the taps must lie inside the file (never zero-padded), else a reason; a fractional part "
                        f"below {SNAP_FRAC:g} samples is applied as 0 (no taps)",
    "interchannel_skew": "per hit, max - min of each channel's own lag (the same choice per channel alone among the "
                         "shared integer lag and its two neighbours, refined); a diagnostic of an inter-channel delay "
                         f"the other take does not have, a reason above {SKEW_MAX:g} samples",
    "schedule_drift": "per take, least-squares slope of matched onset on event position (>= 3 hits), as ppm, clamped "
                      f"to +-{DRIFT_MAX_PPM:g} ppm; intercept the median residual; drift_scheduled_sample is the hit's "
                      "position on it. A fit beyond the bound is a reason when the clamped schedule leaves an onset "
                      f"more than {LAG_BOUND_MS:g} ms away",
    "onset": "trigger: first sample whose |x| (max over the analysed channels) exceeds the detection threshold; onset: "
             f"the interpolated position where the energy envelope (mean x^2 over the channels, two trailing "
             f"{ENVELOPE_MS:g} ms boxes, square root) rises through max(hit's envelope peak - {ONSET_REL_DB:g} dB, "
             f"detection floor + {ONSET_FLOOR_DB:g} dB, -80 dBFS) before that peak; onset_sample is "
             "the first sample at or above it; onset_bound_limited: the peak is less than "
             f"{ONSET_REL_DB:g} dB above the bound (the threshold is the bound, level-dependent), a verdict reason",
    "timing_consistency": "verdict: per hit, lag minus the onset detector's lag (B's interpolated onset minus A's "
                          "minus the schedule shift); "
                          f"spread (max - min) over the hits, a reason above {TIMING_SPREAD_MAX:g} samples",
    "digital_silence": "every stored sample of the silence-floor region identical per channel; a capture with a zero "
                       "floor and digital silence is a reason",
    "render_timing": "a render's interpolated onset residuals (constant offset, declared frames) spread, a capture "
                     f"reason above {RENDER_JITTER_MAX:g} samples",
    "unused_channels": f"channels labelled '{UNUSED_LABEL}' in the channel map: not analysed or paired; checked for "
                       "full-scale and non-finite samples, and any sample at or above -80 dBFS is a reason (the map may "
                       "be wrong)",
    "chain_calibration": "a capture's analysed channel k is divided by the calibration's frozen ratio r_k (k against "
                         "the calibration's first analysed channel: mean over the calibration hits of 20*log10 of "
                         "the least-squares amplitude ratio sum(x_k*x_ref)/sum(x_ref^2) over each 200 ms window, DC "
                         "removed) before any analysis; the WAV is unchanged and the full-scale/non-finite checks use "
                         "the stored samples; every other figure is on the calibrated scale. Never applied to renders",
}


def db(x):
    return round(20 * math.log10(x), 2) if x and x > 0 else None


def envelope_blocks(sig, block):
    return [max(abs(v) for v in sig[i:i + block]) for i in range(0, len(sig) - block + 1, block)]


def median(xs):
    s = sorted(xs)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def detect_onsets(sig, rate, threshold, refractory_s):
    """First sample whose |x| exceeds threshold, then skip refractory_s. Polarity-independent."""
    if not _is_num(refractory_s) or refractory_s <= 0:
        raise ValueError(f"refractory_s {refractory_s!r} is not a positive finite number")
    out, i, n, skip = [], 0, len(sig), max(1, int(refractory_s * rate))
    while i < n:
        if abs(sig[i]) > threshold:
            out.append(i)
            i += skip
        else:
            i += 1
    return out


def energy_envelope(chans, start, end, w):
    """RMS envelope over [start, end): mean of x^2 over the channels, smoothed by two trailing boxes of w samples
    (samples before 0 count as 0), square-rooted. Running sums over this region only."""
    lo = max(0, start - 2 * (w - 1))
    e = [math.fsum(ch[i] * ch[i] for ch in chans) / len(chans) for i in range(lo, end)]
    out = []
    for _ in range(2):
        acc, sm = 0.0, []
        for i, v in enumerate(e):
            acc += v
            if i >= w:
                acc -= e[i - w]
            sm.append(max(acc, 0.0) / w)
        e = sm
    return [math.sqrt(v) for v in e[start - lo:]]


def refine_onsets(chans, rate, triggers, floor, span):
    """Carrier-independent onsets (ENVELOPE_MS, ONSET_REL_DB): for each trigger, the interpolated sample position where
    the energy envelope last rises through max(peak - ONSET_REL_DB, floor bound) before the hit's envelope peak (peak
    searched from the trigger over span samples, up to the next trigger; the crossing searched back to the previous
    hit's peak or span before the trigger). The attack's foot is where the envelope stops falling when followed back
    from the crossing (the end of the previous tail or of the floor), at most MAX_ATTACK_LEAD_MS before it. Returns
    [(position, threshold, foot, envelope peak, floor bound)] (peak None where the trigger stands). A hit whose
    envelope peak is not above the floor bound uses the relative threshold alone."""
    w = max(1, int(round(ENVELOPE_MS * rate / 1000)))
    lead = int(round(MAX_ATTACK_LEAD_MS * rate / 1000))
    n = min(len(ch) for ch in chans)
    bound = max(floor * 10 ** (ONSET_FLOOR_DB / 20), TAIL_FALLBACK) if finite(floor) and floor > 0 else TAIL_FALLBACK
    out, lo = [], 0
    for k, t in enumerate(triggers):
        hi = min(n, t + span, triggers[k + 1] if k + 1 < len(triggers) else n)
        r0 = max(lo, t - span)
        if hi <= t:
            out.append((float(t), None, t, None, bound))
            continue
        try:
            env = energy_envelope(chans, r0, hi, w)
        except (ValueError, OverflowError):        # inf - inf
            env = [math.nan]
        if not all(math.isfinite(v) for v in env):  # non-finite samples (already a reason): the trigger stands
            out.append((float(t), None, t, None, bound))
            continue
        p = max(range(t - r0, hi - r0), key=env.__getitem__)
        rel = env[p] * 10 ** (-ONSET_REL_DB / 20)
        thr = max(rel, bound) if bound < env[p] else rel
        i = p
        while i > 0 and env[i - 1] >= thr:
            i -= 1
        x = i - 1 + (thr - env[i - 1]) / (env[i] - env[i - 1]) if i > 0 else float(i)
        j = i
        while j > max(0, i - lead) and env[j - 1] < env[j]:
            j -= 1
        out.append((r0 + x, thr, r0 + j, env[p], bound))
        lo = r0 + p + 1
    return out


def fit_schedule(onsets, planned, tol):
    """One constant offset d: onset ~= planned + d (samples). Returns (d, {event index: onset})."""
    best = None
    for o in onsets:
        for p in planned:
            d = o - p
            m, used = {}, set()
            for k, q in enumerate(planned):
                cands = [x for x in onsets if abs(x - (q + d)) <= tol and x not in used]
                if cands:
                    x = min(cands, key=lambda x: abs(x - (q + d)))
                    m[k] = x
                    used.add(x)
            key = (-len(m), sum(abs(m[k] - planned[k] - d) for k in m))
            if best is None or key < best[0]:
                best = (key, m)
    if best is None or not best[1]:
        return None, {}
    m = best[1]
    return median([m[k] - planned[k] for k in m]), m


def dot(a, b):
    """Exact sum of products; NaN (never an exception) when a sample is not finite."""
    try:
        return math.fsum(x * y for x, y in zip(a, b))
    except (ValueError, OverflowError):            # inf - inf, or an overflowing partial sum
        return math.nan


def finite(x):
    return x is not None and math.isfinite(x)


def cosine(a, b):
    na, nb = dot(a, a), dot(b, b)
    return dot(a, b) / math.sqrt(na * nb) if na and nb else None


def correlation(a, b):
    if not a:
        return None
    ma, mb = math.fsum(a) / len(a), math.fsum(b) / len(b)
    return cosine([x - ma for x in a], [y - mb for y in b])


def gain_nrmse(a, b):
    bb, aa = dot(b, b), dot(a, a)
    if not bb or not aa:
        return None, None
    g = dot(a, b) / bb
    return g, math.sqrt(math.fsum((x - g * y) ** 2 for x, y in zip(a, b)) / aa)


def window(chans, start, length):
    """Concatenated channel slices, or None if the window leaves the signal (never zero-padded)."""
    if start < 0 or any(start + length > len(ch) for ch in chans):
        return None
    v = []
    for ch in chans:
        v += ch[start:start + length]
    return v


def r7(x):
    return None if x is None else round(x, 7)


def sum_or_nan(xs):
    try:
        return math.fsum(xs)
    except (ValueError, OverflowError):            # inf - inf
        return math.nan


def frac_weights(frac, half=SINC_HALF):
    """Hann-windowed sinc taps for a delay of frac samples (j = -half..half), normalised to unit DC gain."""
    w = []
    for j in range(-half, half + 1):
        x = j - frac
        s = 1.0 if x == 0 else math.sin(math.pi * x) / (math.pi * x)
        w.append(s * (0.5 + 0.5 * math.cos(math.pi * x / (half + 1))))
    t = math.fsum(w)
    return [v / t for v in w]


def frac_window(chans, start, length, frac):
    """window() read at the fractional position start + frac (|frac| <= 0.5) by band-limited interpolation
    (frac_weights). Needs SINC_HALF real samples on both sides of the window (never zero-padded), else None."""
    if not frac:
        return window(chans, start, length)
    if start - SINC_HALF < 0 or any(start + length + SINC_HALF > len(ch) for ch in chans):
        return None
    w = frac_weights(frac)
    v = []
    for ch in chans:
        seg = ch[start - SINC_HALF:start + length + SINC_HALF]
        out = [0.0] * length
        for j, wj in enumerate(w):
            out = [o + wj * x for o, x in zip(out, seg[j:j + length])]
        v += out
    return v


def parabolic(ym, y0, yp):
    """Vertex offset (clamped to -0.5..0.5) of the parabola through (-1, ym), (0, y0), (1, yp); 0 when a value is
    missing or not finite, or y0 is not a strict peak."""
    if not all(finite(y) for y in (ym, y0, yp)):
        return 0.0
    den = ym - 2 * y0 + yp
    if not den < 0:
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (ym - yp) / den))


def fit_hit_lag(asig, bsig, sa, sb, length, bound, prefer=0.0):
    """One hit of the whole-take verdict. Integer lag l in [-bound, bound] of B's window (start sb) against A's (start
    sa), picked by |cosine| over all channels together: among the local maxima of |cosine| within LAG_TIE of the best,
    the one nearest prefer (the onset detector's lag) if within LAG_AGREE of it, else the best; the sign is kept and
    never used to choose, so an inverted take stays negative. Then refined to l + frac by parabolic interpolation of
    |cosine| at l-1, l, l+1. Each channel's own lag is the best |cosine| of that channel alone among l-1, l, l+1,
    refined the same way (inter-channel skew diagnostic). Returns (l, frac, [(l_c, frac_c) or None per channel]) or
    None when no lag gives a finite cosine (windows leave a file, or samples are not finite)."""
    a = [window([ch], sa, length) for ch in asig]
    if any(x is None for x in a):
        return None
    aa = [dot(x, x) for x in a]
    table = {}
    for lag in range(-bound, bound + 1):
        bs = [window([ch], sb + lag, length) for ch in bsig]
        if all(x is not None for x in bs):
            table[lag] = [(dot(x, y), dot(y, y)) for x, y in zip(a, bs)]

    def cos_at(lag, chans):
        if lag not in table:
            return None
        ab, bb = (sum_or_nan(table[lag][c][i] for c in chans) for i in (0, 1))
        na = sum_or_nan(aa[c] for c in chans)
        return ab / math.sqrt(na * bb) if na and bb else None

    def refine(pick, chans):
        y = [cos_at(pick + i, chans) for i in (-1, 0, 1)]
        return pick, parabolic(*(abs(v) if finite(v) else None for v in y))

    def mag(lag, chans):
        c = cos_at(lag, chans)
        return abs(c) if finite(c) else None
    allc = range(len(asig))
    cs = {lag: mag(lag, allc) for lag in sorted(table)}
    cs = {lag: m for lag, m in cs.items() if m is not None}
    if not cs:
        return None
    top = max(cs.values())
    peaks = [lag for lag, m in cs.items() if m >= top - LAG_TIE
             and all(cs.get(lag + i) is None or m >= cs[lag + i] for i in (-1, 1))]
    agree = [lag for lag in peaks if abs(lag - prefer) <= LAG_AGREE]
    pick = min(agree, key=lambda lag: (abs(lag - prefer), -cs[lag])) if agree else max(cs, key=cs.get)
    shared = refine(pick, allc)
    chl = []
    for c in allc:
        near = [(lag, mag(lag, [c])) for lag in (pick - 1, pick, pick + 1)]
        near = [(lag, m) for lag, m in near if m is not None]
        chl.append(refine(max(near, key=lambda x: x[1])[0], [c]) if near else None)
    return shared[0], shared[1], chl


def compare(a_sig, a_on, b_sig, b_on, pre, length, max_lag):
    """Per-pair DIAGNOSTIC: own lag (searched on the attack) and own gain. a_sig/b_sig: matching channel lists."""
    a = window(a_sig, a_on - pre, length)
    b0 = window(b_sig, b_on - pre, length)
    out = {"scope": "diagnostic: this pair's own lag and gain; not a full-take verdict", "complete": False}
    if a is None or b0 is None:
        return out
    short = min(length, LAG_WINDOW)
    a_s = window(a_sig, a_on - pre, short)
    best_lag, best_s = 0, None
    for lag in range(-max_lag, max_lag + 1):
        bs = window(b_sig, b_on - pre + lag, short)
        c = cosine(a_s, bs) if bs is not None else None
        if finite(c) and (best_s is None or abs(c) > abs(best_s)):
            best_lag, best_s = lag, c
    b = window(b_sig, b_on - pre + best_lag, length) or b0
    if b is b0:
        best_lag = 0
    c = cosine(a, b)
    g, e = gain_nrmse(a, b)
    out.update({"complete": True, "cosine_onset": r7(cosine(a, b0)), "best_lag": best_lag, "cosine_best": r7(c),
                "correlation_best": r7(correlation(a, b)), "gain_b_to_a": r7(g), "nrmse_after_gain": r7(e),
                "cosine_ge_0.99": finite(c) and c >= COSINE_TARGET,
                "nrmse_le_0.05": finite(e) and e <= NRMSE_TARGET})
    out["shape_and_level_ok"] = out["cosine_ge_0.99"] and out["nrmse_le_0.05"]
    return out


def last_crossing_ms(env, t):
    """End of the last 1 ms envelope block >= t (0 if none)."""
    return next((i + 1 for i in range(len(env) - 1, -1, -1) if env[i] >= t), 0)


def past_window_threshold(floor):
    """max(floor + 10 dB, -80 dBFS) on the floor's scale; -80 dBFS when the floor is zero or unavailable."""
    return max(floor * 10 ** 0.5, TAIL_FALLBACK) if finite(floor) and floor > 0 else TAIL_FALLBACK


def tail_figures(sig, onset, end, rate, floor, scale, is_float, post_ms, dc):
    """dc: each channel's mean, removed before the envelope (as before the floor). The envelope is returned as '_env'
    for the verdict's common-scale past-window test; the caller removes it from the report."""
    block = max(1, rate // 1000)
    combined = [max((v - m for v, m in zip(vals, dc)), key=abs) for vals in zip(*(ch[onset:end] for ch in sig))]
    env = envelope_blocks(combined, block)
    hold = TAIL_HOLD_MS                                   # blocks are 1 ms
    thresholds = {"-60dBFS": 1e-3, "-80dBFS": 1e-4,
                  "floor+10dB": floor * 10 ** 0.5 if floor else None}
    figs = {}
    for name, t in thresholds.items():
        if t is None:
            figs[name] = {"available": False, "reason": "noise floor unavailable or zero"}
            continue
        sustained = next((i for i in range(len(env) - hold + 1) if all(e < t for e in env[i:i + hold])), None)
        censored = sustained is None or any(e >= t for e in env[max(0, len(env) - hold):])
        figs[name] = {"available": True, "threshold": t, "threshold_dbfs": db(t),
                      "last_crossing_ms": last_crossing_ms(env, t),
                      "sustained_below_ms": sustained, "censored": censored,
                      "noise_limited": None if floor is None else t < floor}
    basis = "floor+10dB" if past_window_threshold(floor) > TAIL_FALLBACK else "-80dBFS"   # max(floor+10dB, -80)
    beyond = figs[basis]["last_crossing_ms"] > post_ms
    fin = max(1, int(0.020 * rate))

    def raw(v):
        return v if is_float else round(v * scale)
    final = []
    for c, ch in enumerate(sig):
        seg = ch[max(onset, end - fin):end]
        whole = ch[onset:end]
        final.append({"channel": c + 1, "final_20ms_mean_raw": r7(raw(math.fsum(seg) / len(seg))) if seg else None,
                      "final_20ms_peak_abs_raw": raw(max(map(abs, seg))) if seg else None,
                      "final_sample_raw": raw(seg[-1]) if seg else None,
                      "tail_mean_raw": r7(raw(math.fsum(whole) / len(whole))) if whole else None})
    return {"region_samples": [onset, end], "region_ms": round((end - onset) * 1000 / rate, 3),
            "thresholds": figs, "raw_residual": final, "signal_past_window_basis": basis,
            "signal_past_window": beyond, "_env": env}


def hit_metrics(sig, onset, pre, length, info):
    out = []
    scale, is_float = info["full_scale"], info["sample_type"] == "float"
    for c, ch in enumerate(sig):
        seg = ch[onset - pre:onset - pre + length] if onset - pre >= 0 else []
        if len(seg) < length:
            out.append({"channel": c + 1, "complete": False})
            continue
        pk = max(seg, key=abs)
        first = next((v for v in seg if abs(v) >= 0.5 * abs(pk)), 0.0)
        rms = math.sqrt(dot(seg, seg) / len(seg))
        out.append({"channel": c + 1, "complete": True, "peak": round(pk, 9),
                    "peak_raw": pk if is_float else round(pk * scale), "peak_dbfs": db(abs(pk)),
                    "attack_sign": (first > 0) - (first < 0), "rms": round(rms, 9), "rms_dbfs": db(rms),
                    "full_scale_samples": wavio.count_full_scale(seg, info)})
    return out


CAPTURE_SCHEMA = "circuit-rhythm-capture/2"
RENDER_SCHEMA = "circuit-rhythm-render/1"
CONTROL_SOURCES = ("silence", "no-note")      # silent-audio controls: no audio hit is expected
RECORDER_FAULT = re.compile(r"overrun|underrun|xrun", re.I)


def find_sidecar(wav):
    """Adjacent metadata: <stem>.json (capture sidecar) or <stem>.render.json (render provenance)."""
    base = os.path.splitext(wav)[0]
    for cand in (base + ".json", base + ".render.json"):
        if os.path.exists(cand):
            return cand
    return None


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _obj(x):
    """A metadata sub-object, or {} when it is missing or not an object (the caller reports the latter)."""
    return x if isinstance(x, dict) else {}


def duration_reasons(duration, label):
    if not _is_num(duration) or duration <= 0:
        return [f"{label}: duration {duration!r} is not a positive finite number"]
    return []


def schedule_reasons(times, hit_ids, duration, info, label):
    """Event times (s from sample 0 / the anchor) finite and inside [0, duration) and the file; hit IDs unique ints."""
    reasons = []
    file_s = info["frames"] / info["rate"]
    limit = min(duration, file_s) if _is_num(duration) else file_s
    for k, t in enumerate(times):
        if not _is_num(t) or not 0 <= t < limit:
            reasons.append(f"{label}: event {k} at {t!r} s is outside [0, {limit:g}) s "
                           f"(declared duration {duration!r} s, file {file_s:g} s)")
    placed = [t for t in times if _is_num(t)]
    if any(b <= a for a, b in zip(placed, placed[1:])):
        reasons.append(f"{label}: events are not in strictly increasing time order ({placed})")
    for h in hit_ids:
        if not isinstance(h, int) or isinstance(h, bool) or h < 0:
            reasons.append(f"{label}: hit id {h!r} is not a non-negative integer")
    dup = sorted({h for h in hit_ids if hit_ids.count(h) > 1}, key=repr)
    if dup:
        reasons.append(f"{label}: duplicate hit ids {dup}")
    return reasons


def channel_map_reasons(cmap, channels, label):
    """channel_map must name exactly the file's channels, 1-based like the capture sidecars ("1".."N")."""
    if not isinstance(cmap, dict):
        return [f"{label}: channel_map is not an object"]
    want = {str(c) for c in range(1, channels + 1)}
    reasons = []
    if set(cmap) != want:
        reasons.append(f"{label}: channel_map keys {sorted(cmap)} != the file's channels {sorted(want)}")
    if any(not isinstance(v, str) or not v.strip() for v in cmap.values()):
        reasons.append(f"{label}: channel_map has an empty label")
    return reasons


def render_meta(r, wav_path, info):
    """Adapter: render-provenance JSON (RENDER_SCHEMA) -> the capture-sidecar fields analyse() uses.

    Required: schema, source.name, rate, channels, channel_map, duration_s, events (list of {hit, frame} or
    {hit, time_s}, frames counted from sample 0 of the WAV; empty only for a control source), wav_sha256,
    provenance.tool, provenance.commit, provenance.inputs (non-empty), status. Anything missing or inconsistent with
    the WAV is a reason; nothing unknown is counted as valid.
    """
    reasons = []
    for k in ("source", "rate", "channels", "channel_map", "duration_s", "events", "wav_sha256", "provenance", "status"):
        if r.get(k) in (None, "", {}) and not (k == "events" and r.get(k) == []):
            reasons.append(f"render: missing {k}")
    for k in ("source", "provenance"):
        if r.get(k) is not None and not isinstance(r[k], dict):
            reasons.append(f"render: {k} is not an object")
    prov = _obj(r.get("provenance"))
    for k in ("tool", "commit", "inputs"):
        if not prov.get(k):
            reasons.append(f"render: missing provenance.{k}")
    name = _obj(r.get("source")).get("name")
    if not name:
        reasons.append("render: missing source.name")
    if r.get("rate") not in (None, info["rate"]):
        reasons.append(f"render: rate {r.get('rate')} != file {info['rate']}")
    if r.get("channels") not in (None, info["channels"]):
        reasons.append(f"render: channels {r.get('channels')} != file {info['channels']}")
    if r.get("wav_sha256"):
        h = hashlib.sha256(open(wav_path, "rb").read()).hexdigest()
        if h != r["wav_sha256"]:
            reasons.append("render: wav_sha256 does not match the file")
    events, times, ids = [], [], []
    raw_events = r.get("events")
    if raw_events is not None and not isinstance(raw_events, list):
        reasons.append("render: events is not a list")
        raw_events = []
    for k, e in enumerate(raw_events or []):
        if not isinstance(e, dict):
            reasons.append(f"render: event {k} is not an object")
            continue
        if "frame" in e:
            f = e["frame"]
            if not isinstance(f, int) or isinstance(f, bool):
                reasons.append(f"render: event {k} frame {f!r} is not an integer")
                continue
            t = f / info["rate"]
        elif "time_s" in e:
            t = e["time_s"]
            if not _is_num(t):
                reasons.append(f"render: event {k} time_s {t!r} is not finite")
                continue
        else:
            reasons.append(f"render: event {k} has neither frame nor time_s")
            continue
        times.append(t)
        ids.append(e.get("hit", k))
        events.append({"i": k, "hit": e.get("hit", k), "kind": "note_on", "planned_s": t, "bytes": ""})
    dur = r.get("duration_s")
    if dur is not None:
        reasons += duration_reasons(dur, "render")
        if _is_num(dur) and dur > 0 and abs(info["frames"] - dur * info["rate"]) > 1:
            reasons.append(f"render: duration {dur} s != file length {info['frames'] / info['rate']:g} s")
    reasons += schedule_reasons(times, ids, dur, info, "render")
    if r.get("channel_map") is not None:
        reasons += channel_map_reasons(r["channel_map"], info["channels"], "render")
    if not events and name not in CONTROL_SOURCES:
        reasons.append("render: no events for a non-control source")
    return {"schema": RENDER_SCHEMA, "_kind": "render", "_reasons": reasons, "status": r.get("status"),
            "source": _obj(r.get("source")), "capture": {"rate": r.get("rate"), "channel_map": r.get("channel_map")},
            "track": r.get("track"),
            "stimulus": {"events": events, "duration_s": r.get("duration_s")}, "provenance": prov,
            "timing_reference": {"kind": "none", "channel": None}, "wav": {"partial": False}}


EVENT_KINDS = ("note_on", "note_off", "marker")


def hit_id(e):
    """An event's hit id: 'hit', else its index 'i'; None (an invalid id, already a reason) if neither is given."""
    return e.get("hit", e.get("i"))


def load_meta(sidecar, wav_path, info):
    if not sidecar:
        return None
    try:
        m = json.load(open(sidecar))
    except ValueError as e:
        return {"_reasons": [f"metadata is not valid JSON: {e}"]}
    if not isinstance(m, dict):
        return {"_reasons": ["metadata is not a JSON object"]}
    # a calibration (capture.py calibrate) holds its take's capture sidecar under "take": analysed like a capture, but
    # it is the chain measurement itself, so no calibration is applied or required (chain_calibration)
    calibration_take = m.get("schema") == CALIBRATION_SCHEMA
    if calibration_take:
        m = m.get("take")
        if not isinstance(m, dict):
            return {"_reasons": ["calibration: take is not an object"]}
    # '_' keys are the analyser's own (kind, reasons): never taken from the file; the kind comes from the schema only
    reserved = sorted(k for k in m if k.startswith("_"))
    m = {k: v for k, v in m.items() if not k.startswith("_")}
    if m.get("schema") == RENDER_SCHEMA:
        r = render_meta(m, wav_path, info)
        r["_reasons"] += [f"render: reserved keys {reserved} ignored (keys starting with '_' are the analyser's)"
                          ] if reserved else []
        return r
    if m.get("schema") != CAPTURE_SCHEMA:
        m["_reasons"] = [f"unknown metadata schema {m.get('schema')!r}"]
        return m
    m["_kind"] = "capture"
    m["_calibration_take"] = calibration_take
    reasons =[f"sidecar: reserved keys {reserved} ignored (keys starting with '_' are the analyser's)"] if reserved \
        else []
    for k in ("stimulus", "capture", "wav", "source", "timing_reference"):
        if m.get(k) is not None and not isinstance(m[k], dict):
            reasons.append(f"sidecar: {k} is not an object")
    if not isinstance(m.get("stimulus"), dict):
        reasons.append("sidecar: no stimulus")
    # bound to this WAV: hash, channel count, channel map and rate as recorded (a missing value is a reason)
    w, cap = _obj(m.get("wav")), _obj(m.get("capture"))
    if not isinstance(w.get("sha256"), str):
        reasons.append("sidecar: missing wav.sha256")
    elif w["sha256"] != hashlib.sha256(open(wav_path, "rb").read()).hexdigest():
        reasons.append("sidecar: wav.sha256 does not match the file")
    if cap.get("channels") != info["channels"]:
        reasons.append(f"sidecar: capture.channels {cap.get('channels')!r} != file {info['channels']}")
    reasons += channel_map_reasons(cap.get("channel_map"), info["channels"], "sidecar")
    # capture sidecar: the same bounds where they apply (planned schedule inside the planned duration and the file)
    st = _obj(m.get("stimulus"))
    dur = st.get("duration_s")
    raw = st.get("events")
    if raw is not None and not isinstance(raw, list):
        reasons.append("sidecar: stimulus.events is not a list")
        raw = []
    ev = []
    for k, e in enumerate(raw or []):
        if not isinstance(e, dict):
            reasons.append(f"sidecar: event {k} is not an object")
            continue
        if e.get("kind") not in EVENT_KINDS:
            reasons.append(f"sidecar: event {k} kind {e.get('kind')!r} is not one of {list(EVENT_KINDS)}")
        if "i" not in e:
            reasons.append(f"sidecar: event {k} has no index i")
        if e.get("kind") in ("note_on", "marker"):
            ev.append(e)
    reasons += duration_reasons(dur, "sidecar") if dur is not None else []
    reasons += schedule_reasons([e.get("planned_s") for e in ev], [hit_id(e) for e in ev], dur, info, "sidecar")
    pd = m.get("protocol_deviations", [])
    if not isinstance(pd, list) or any(not isinstance(x, str) for x in pd):
        reasons.append(f"sidecar: protocol_deviations {pd!r:.200} is not a list of strings")
    m["_reasons"] = reasons
    return m


def count_nonfinite(data, info):
    """NaN/Inf samples in any channel of the file (only float files can hold them)."""
    if info["sample_type"] != "float":
        return 0
    return sum(1 for ch in data for v in ch if not math.isfinite(v))


def capture_validity(meta, info, data, expected_s, nonfinite=0, extra=()):
    """data: every channel of the file (a timing-reference or unanalysed channel must not clip either)."""
    reasons = []
    if nonfinite:
        reasons.append(f"{nonfinite} non-finite samples (NaN/Inf)")
    clipped = sum(wavio.count_full_scale(ch, info) for ch in data)
    if clipped:
        reasons.append(f"{clipped} full-scale samples")
    if meta is None:
        reasons.append("no capture sidecar or render provenance: diagnostics only, no fidelity verdict")
        reasons += list(extra)
        return {"valid": False, "verdict_eligible": False, "reasons": reasons, "full_scale_samples": clipped,
                "nonfinite_samples": nonfinite}
    reasons += meta.get("_reasons", [])
    reasons += list(extra)
    if meta.get("status") != "complete":
        reasons.append(f"status {meta.get('status')!r}")
    if _obj(meta.get("wav")).get("partial"):
        reasons.append("partial WAV")
    cap = _obj(meta.get("capture"))
    rate = cap.get("rate")
    if rate != info["rate"]:
        reasons.append(f"file rate {info['rate']} != metadata rate {rate!r}")
    err = cap.get("recorder_stderr") or ""
    if not isinstance(err, str):
        reasons.append(f"recorder_stderr is not a string: {err!r:.500}")
    elif RECORDER_FAULT.search(err):
        reasons.append(f"recorder reported: {err.strip()[:500]}")
    if cap.get("pump_error"):
        reasons.append(f"recorder pipe error: {cap['pump_error']}")
    if cap.get("warning"):
        reasons.append(f"capture warning: {cap['warning']}")
    fr, want = cap.get("frames"), cap.get("frames_wanted")
    if want is not None and (not _is_num(want) or fr is not None and not _is_num(fr)):
        reasons.append(f"capture frames {fr!r} / frames_wanted {want!r} are not numbers")
    elif want and fr is not None and fr < want:
        reasons.append(f"short recording: {fr} of {want} frames")
    if not _is_num(expected_s) or expected_s <= 0:
        reasons.append(f"no valid planned duration in the metadata ({expected_s!r})")
    elif info["frames"] < int(round(expected_s * info["rate"])):
        reasons.append(f"short recording: {info['frames']} frames < planned {expected_s} s")
    return {"valid": not reasons, "verdict_eligible": True, "kind": meta.get("_kind"),
            "reasons": reasons, "full_scale_samples": clipped, "nonfinite_samples": nonfinite,
            "protocol_deviations": meta.get("protocol_deviations") or []}


def mean_or_zero(xs):
    """Mean of xs; 0.0 when empty or not finite (a non-finite sample already invalidates the take)."""
    try:
        m = math.fsum(xs) / len(xs) if xs else 0.0
    except (ValueError, OverflowError):
        return 0.0
    return m if math.isfinite(m) else 0.0


def schedule_offset_bound(meta, control, offset, rate, tolerance_ms, anchored_sends):
    """Plausibility bound of the fitted schedule offset (LATENCY_MAX_MS); a violation is a reason, not an exception."""
    if meta is None or control or offset is None:
        return {"checked": False, "why": "no metadata, a control take, or no fitted offset"}
    off_ms = offset * 1000 / rate
    kind, path = meta.get("_kind"), meta.get("path")
    if kind == "render":
        lo, hi = -tolerance_ms, tolerance_ms
        why = "render: events are declared at their WAV frames, so the audio starts there (onset tolerance)"
    elif kind == "capture" and path == "a" and anchored_sends:
        lo, hi = 0.0, LATENCY_MAX_MS
        why = ("live capture: host send times from the estimated host time of sample 0 (never early), so the offset "
               "is latency plus capture buffering")
    elif kind == "capture" and path == "b":
        return {"checked": False, "offset_ms": round(off_ms, 4),
                "why": "path b (the recorder's own SD card): the recorder's start is unknown, so the offset is a "
                       "free fit and the hit identity rests on it"}
    elif kind == "capture":
        why = ("path a without valid host send times on the sample-0 anchor" if path == "a" else
               f"capture path {path!r} is not 'a' or 'b'")
        return {"checked": False, "offset_ms": round(off_ms, 4), "why": why,
                "reason": f"schedule offset {off_ms:.3f} ms cannot be bounded ({why}): only path b is unbounded by "
                          "design; the hits may be matched to the wrong events"}
    else:
        return {"checked": False, "offset_ms": round(off_ms, 4), "why": "unknown metadata kind (already a reason)"}
    out = {"checked": True, "offset_ms": round(off_ms, 4), "bound_ms": [lo, hi], "basis": why}
    if not lo <= off_ms <= hi:
        out["reason"] = (f"schedule offset {off_ms:.3f} ms outside [{lo:g}, {hi:g}] ms ({why}): the hits may be "
                         "matched to the wrong events")
    return out


def schedule_drift(matches, planned, offset, rate):
    """Linear clock drift of the take's schedule: least-squares slope of matched onset on event position (>= 3 hits),
    clamped to +-DRIFT_MAX_PPM; the intercept is the median onset residual about the clamped slope (the constant fit
    is a median too). A fit beyond the bound is a reason only if the clamped schedule leaves an onset more than
    LAG_BOUND_MS away (DRIFT_MAX_PPM)."""
    ks = sorted(matches)
    out = {"fitted": False, "applied_ppm": 0.0, "intercept": offset, "bound_ppm": DRIFT_MAX_PPM}
    if len(ks) < 3 or offset is None:
        out["why"] = "fewer than 3 matched hits: no drift term"
        return out
    p, o = [planned[k] for k in ks], [matches[k] for k in ks]
    pm, om = math.fsum(p) / len(p), math.fsum(o) / len(o)
    spp = math.fsum((x - pm) ** 2 for x in p)
    if not spp:
        out["why"] = "all events at one time"
        return out
    fit = (math.fsum((x - pm) * (y - om) for x, y in zip(p, o)) / spp - 1) * 1e6
    applied = max(-DRIFT_MAX_PPM, min(DRIFT_MAX_PPM, fit))
    beta = 1 + applied * 1e-6
    a0 = median([y - beta * x for x, y in zip(p, o)])
    worst = max(abs(y - a0 - beta * x) for x, y in zip(p, o))
    out.update({"fitted": True, "fit_ppm": round(fit, 3), "applied_ppm": round(applied, 3), "intercept": a0,
                "max_abs_residual_samples": round(worst, 3), "slope": beta})
    if abs(fit) > DRIFT_MAX_PPM and worst > LAG_BOUND_MS * rate / 1000:
        out["reason"] = (f"schedule drift {fit:.1f} ppm beyond +-{DRIFT_MAX_PPM:g} ppm: with the drift clamped at the "
                         f"bound an onset is {worst / rate * 1000:.3f} ms off its schedule (> {LAG_BOUND_MS:g} ms)")
    return out


def drift_position(drift, planned_sample):
    """A hit's schedule position including the drift term (the constant-offset position without a fit)."""
    return drift["intercept"] + drift.get("slope", 1.0) * planned_sample


def unused_channels(meta, nch):
    """File channels (1-based) labelled UNUSED_LABEL in the metadata's channel map (a malformed map is already a
    reason: then none)."""
    cmap = _obj(_obj((meta or {}).get("capture")).get("channel_map"))
    return sorted(int(k) for k, v in cmap.items() if isinstance(k, str) and k.isdigit() and 1 <= int(k) <= nch
                  and isinstance(v, str) and v.strip().lower() == UNUSED_LABEL)


def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _find_near(path, sidecar):
    """path as recorded, else its basename next to the sidecar (a moved directory); None if neither exists."""
    cands = [path] if isinstance(path, str) and path else []
    if cands and sidecar:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sidecar)), os.path.basename(path)))
    return next((c for c in cands if os.path.isfile(c)), None)


def chain_calibration(meta, sidecar, channels, no_calibration):
    """(report, ratios {file channel: ratio} or None, reasons, deviations) for the recorder-chain calibration
    (DEFINITIONS chain_calibration). Only capture sidecars are calibrated; the embedded calibration must name every
    analysed channel with a positive finite ratio, and its JSON and WAV must still be there, unchanged, and accepted."""
    kind = (meta or {}).get("_kind")
    if kind != "capture":
        return {"applied": False, "why": "render: no recorder chain" if kind == "render" else "no capture metadata"}, \
            None, [], []
    if meta.get("_calibration_take"):
        return {"applied": False, "calibration_take": True,
                "why": "calibration take: the chain measurement itself, analysed raw"}, None, [], []
    if no_calibration:
        return {"applied": False, "why": "--no-calibration"}, None, [], [
            "no_calibration (--no-calibration: recorder-chain calibration not applied)"]
    cal = meta.get("calibration")
    if cal is None:
        if len(channels) > 1:
            return {"applied": False, "why": "no calibration in the sidecar"}, None, [
                f"no chain calibration: {len(channels)} analysed channels {channels} and no recorder-chain calibration "
                "in the sidecar (capture.py calibrate, then --calibration on the take; or --no-calibration, a recorded "
                "deviation)"], []
        return {"applied": False, "why": "one analysed channel: nothing to match"}, None, [], []
    if not isinstance(cal, dict):
        return {"applied": False, "why": "malformed"}, None, ["sidecar: calibration is not an object"], []
    reasons = []
    ratios = cal.get("ratios")
    if not isinstance(ratios, dict):
        ratios = {}
        reasons.append("calibration: ratios is not an object")
    use = {}
    for c in channels:
        r = ratios.get(str(c))
        if not _is_num(r) or r <= 0:
            reasons.append(f"calibration: no positive finite ratio for analysed channel {c} ({r!r})")
        else:
            use[c] = r
    rep = {"source": "sidecar calibration", "path": cal.get("path"), "sha256": cal.get("sha256"),
           "created_utc": cal.get("created_utc"), "reference_channel": cal.get("reference_channel"),
           "ratios": {str(c): r for c, r in use.items()},
           "ratios_db": {str(c): round(20 * math.log10(r), 6) for c, r in use.items()}, "spread_db": cal.get("spread_db"),
           "delay_samples": cal.get("delay_samples"), "delay_applied": False}
    found = _find_near(cal.get("path"), sidecar)
    if found is None:
        reasons.append(f"calibration file {cal.get('path')!r} not found: the embedded ratios cannot be verified")
    elif _sha256_file(found) != cal.get("sha256"):
        reasons.append(f"calibration file {found} does not match the embedded sha256")
    else:
        try:
            cj = json.load(open(found))
        except ValueError as e:
            cj = None
            reasons.append(f"calibration file {found} is not valid JSON: {e}")
        if isinstance(cj, dict):
            res, take = _obj(cj.get("result")), _obj(cj.get("take"))
            if cj.get("schema") != CALIBRATION_SCHEMA or cj.get("accepted") is not True:
                reasons.append(f"calibration file {found}: schema {cj.get('schema')!r}, accepted {cj.get('accepted')!r}"
                               " (an accepted circuit-rhythm-calibration/1 is required)")
            if res.get("ratios") != ratios:
                reasons.append(f"calibration ratios in the sidecar {ratios} != the calibration file's {res.get('ratios')}")
            w = _obj(take.get("wav"))
            wav = _find_near(os.path.join(os.path.dirname(found), str(w.get("file"))), None) if w.get("file") else None
            want = _obj(cal.get("wav")).get("sha256")
            if wav is None:
                reasons.append(f"calibration WAV {w.get('file')!r} not found next to {found}")
            elif _sha256_file(wav) != w.get("sha256") or w.get("sha256") != want:
                reasons.append(f"calibration WAV {wav} does not match the recorded sha256")
            rep["verified_against"] = found
    rep["applied"] = bool(use) and len(use) == len(channels)
    if not rep["applied"]:
        rep["why"] = "ratios missing for an analysed channel"
    rep["reasons"] = reasons
    return rep, (use if rep["applied"] else None), reasons, []


def analyse(path, sidecar=None, channels=None, threshold_db=20.0, abs_floor_db=-70.0, tolerance_ms=50.0,
            pre_ms=PRE_MS, window_s=WINDOW_S, max_lag=DETECTION["max_lag"], refractory_s=None, no_calibration=False):
    for k, v in (("threshold_db", threshold_db), ("abs_floor_db", abs_floor_db), ("tolerance_ms", tolerance_ms),
                 ("pre_ms", pre_ms), ("window_s", window_s)):
        if not _is_num(v):
            raise ValueError(f"{k} {v!r} is not a finite number")
    if tolerance_ms <= 0 or pre_ms < 0 or window_s <= 0:
        raise ValueError("tolerance_ms and window_s must be > 0, pre_ms >= 0")
    if refractory_s is not None and (not _is_num(refractory_s) or refractory_s <= 0):
        raise ValueError(f"refractory_s {refractory_s!r} is not a positive finite number")
    if not isinstance(max_lag, int) or isinstance(max_lag, bool) or max_lag < 0:
        raise ValueError(f"max_lag {max_lag!r} is not a non-negative integer")
    info, data = wavio.read(path)
    rate = info["rate"]
    meta = load_meta(sidecar, path, info)
    extra = []                                        # analysis-time metadata reasons (invalidate the capture)
    unused = unused_channels(meta, info["channels"])
    ref = _obj((meta or {}).get("timing_reference"))
    ref_ch, ref_kind = ref.get("channel"), ref.get("kind")
    if ref_ch is not None and (not isinstance(ref_ch, int) or isinstance(ref_ch, bool)
                               or not 1 <= ref_ch <= info["channels"]):
        extra.append(f"timing_reference.channel {ref_ch!r} is not a channel of the file")
        ref_ch = None
    elif ref_ch is not None and (not isinstance(ref_kind, str) or ref_kind in ("", "none")):
        # only a declared reference (a kind other than "none") removes a channel from the comparison
        extra.append(f"timing_reference.channel {ref_ch} with kind {ref_kind!r}: no reference is declared, the channel "
                     "is compared like any other")
        ref_ch = None
    elif ref_ch is not None and ref_ch in unused:
        extra.append(f"timing_reference.channel {ref_ch} is labelled {UNUSED_LABEL!r} in the channel map")
    elif ref_ch is not None and info["channels"] - len(unused) <= PROTOCOL_CHANNELS:
        extra.append(f"timing_reference.channel {ref_ch} on a {info['channels'] - len(unused)}-channel take (unused "
                     f"channels excepted): a reference must be a channel beyond the stereo L/R pair "
                     f"({PROTOCOL_CHANNELS} channels), else L or R is not compared")
    # every channel of the file is compared except a declared timing reference and channels labelled unused; validity
    # covers all of them
    comparable = [c for c in range(1, info["channels"] + 1) if c != ref_ch and c not in unused]
    if not comparable:
        raise ValueError(f"no channel to analyse: channels {unused} are labelled {UNUSED_LABEL!r}"
                         + (f" and {ref_ch} is the timing reference" if ref_ch else ""))
    if channels is None:
        channels = comparable
    if not channels or any(not 1 <= c <= info["channels"] for c in channels):
        raise ValueError(f"channels {channels} not in 1..{info['channels']}")
    unused_report = []
    for c in unused:                                  # full-scale/non-finite: capture_validity, on every channel
        vals = [abs(v) for v in data[c - 1] if math.isfinite(v)]
        pk = max(vals, default=0.0)
        unused_report.append({"channel": c, "peak_dbfs": db(pk), "digital_zero": pk == 0 and len(vals) ==
                              len(data[c - 1]), "max_dbfs": db(TAIL_FALLBACK)})
        if pk >= TAIL_FALLBACK:
            extra.append(f"channel {c} is labelled {UNUSED_LABEL!r} but peaks at {db(pk)} dBFS (>= "
                         f"{db(TAIL_FALLBACK)} dBFS): the channel map may be wrong")
    cal_report, ratios, cal_reasons, cal_dev = chain_calibration(meta, sidecar, channels, no_calibration)
    extra += cal_reasons
    raw_data = data                                   # stored samples: validity (full-scale, non-finite) only
    if ratios:
        data = [[v / ratios[c + 1] for v in ch] if ratios.get(c + 1, 1.0) != 1.0 else ch for c, ch in enumerate(data)]
    sig = [data[c - 1] for c in channels]
    nonfinite = count_nonfinite(raw_data, info)
    mono = [max(vals, key=abs) for vals in zip(*sig)] if len(sig) > 1 else sig[0]
    block = max(1, rate // 1000)

    # events with a non-finite time or a bad shape are already reasons (invalid take); they cannot be placed, so
    # they are skipped
    stim = _obj((meta or {}).get("stimulus"))
    all_events = [e for e in (stim.get("events") if isinstance(stim.get("events"), list) else [])
                  if isinstance(e, dict)]
    events = [e for e in all_events if e.get("kind") in ("note_on", "marker") and _is_num(e.get("planned_s"))]
    source = _obj((meta or {}).get("source")).get("name")
    control = source in CONTROL_SOURCES
    expected_s = stim.get("duration_s")
    anchor_ns = _obj((meta or {}).get("capture")).get("anchor_ns")
    planned_s = [e["planned_s"] for e in events]
    anchored_sends = False                            # event times = host sends relative to the recorded anchor
    if events and all("host_send_start_ns" in e for e in events):
        sends = [e["host_send_start_ns"] for e in events]
        if anchor_ns is not None and not _is_num(anchor_ns):
            extra.append(f"capture.anchor_ns {anchor_ns!r} is not a finite number; host send times not used")
        elif not all(_is_num(x) for x in sends):
            extra.append(f"host_send_start_ns values {sends!r} are not all finite numbers; host send times not used")
        else:
            base = anchor_ns if anchor_ns is not None else sends[0] - planned_s[0] * 1e9
            sent_s = [(x - base) / 1e9 for x in sends]
            bad = schedule_reasons(sent_s, [], expected_s, info, "sidecar host send")
            if bad:                                   # same bounds as planned_s; then the planned times are used
                extra += bad
            else:
                planned_s = sent_s
                anchored_sends = anchor_ns is not None
    # an unsorted schedule is already a reason; the spacing still comes from the sorted times so a negative or zero
    # gap can never give a negative re-arm time (every sample an onset)
    ordered = sorted(planned_s)
    spacing = min((b - a for a, b in zip(ordered, ordered[1:]) if b > a), default=None)
    default_refractory = spacing / 2 if spacing else 0.5
    deviations = [f"{k}={v!r} (protocol {DETECTION[k]!r}: less sensitive)" for k, v in (
        ("threshold_db", threshold_db), ("abs_floor_db", abs_floor_db), ("tolerance_ms", tolerance_ms))
        if v > DETECTION[k]]
    deviations += [f"{k}={v!r} (protocol {DETECTION[k]!r})" for k, v in (("pre_ms", pre_ms), ("window_s", window_s))
                   if v != DETECTION[k]]
    if sorted(channels) != comparable:
        deviations.append(f"channels={channels!r} (protocol {comparable!r}: every channel except a timing reference)")
    if max_lag != DETECTION["max_lag"]:
        deviations.append(f"max_lag={max_lag!r} (protocol {DETECTION['max_lag']!r})")
    deviations += cal_dev
    if refractory_s is None:
        refractory_s = default_refractory
    elif refractory_s > default_refractory:
        deviations.append(f"refractory_s={refractory_s!r} > {default_refractory!r} (half the scheduled spacing): "
                          "can hide an onset")

    # detection threshold from the whole file's median 1 ms block peak (hits occupy a small fraction)
    det_floor = median(envelope_blocks(mono, block)) or 0.0
    threshold = max(det_floor * 10 ** (threshold_db / 20), 10 ** (abs_floor_db / 20))
    triggers = detect_onsets(mono, rate, threshold, refractory_s)
    pre, length = int(round(pre_ms * rate / 1000)), int(round(window_s * rate))
    # carrier-independent onset per trigger (refine_onsets); above the runtime cap the triggers stand (refused below)
    refined = refine_onsets(sig, rate, triggers, det_floor, length) if len(triggers) <= MAX_ONSETS else [
        (float(t), None, t, None, None) for t in triggers]
    onsets = [math.floor(x) + 1 if x != int(x) else int(x) for x, *_ in refined]   # first sample at the threshold
    precise = dict(zip(onsets, (round(x[0], 4) for x in refined)))
    foot = dict(zip(onsets, (x[2] for x in refined)))
    env_peak = dict(zip(onsets, ((x[3], x[4]) for x in refined)))
    onset_thresholds = [x[1] for x in refined]

    planned = [round(p * rate) for p in planned_s]
    bounded = True                                    # runtime bounds, checked before any fitting
    if len(events) > MAX_EVENTS:
        extra.append(f"{len(events)} events > {MAX_EVENTS}: refused (runtime bound), nothing matched")
        bounded = False
    if len(onsets) > MAX_ONSETS or meta is None and len(onsets) > MAX_EVENTS:
        extra.append(f"{len(onsets)} detected onsets > {MAX_ONSETS if meta is not None else MAX_EVENTS}: refused "
                     "(runtime bound), nothing matched")
        bounded = False
    if planned and onsets and not control and bounded:   # controls expect no audio hit: nothing is matched
        offset, matches = fit_schedule(onsets, planned, tolerance_ms * rate / 1000)
    else:
        offset, matches = None, {}
    offset_bound = schedule_offset_bound(meta, control, offset, rate, tolerance_ms, anchored_sends)
    if offset_bound.get("reason"):
        extra.append(offset_bound["reason"])
    if meta is None:                                  # no metadata: every onset is a (diagnostic) hit
        matches, planned, offset = dict(enumerate(onsets)) if bounded else {}, list(onsets), 0
    drift = schedule_drift(matches, planned, offset, rate) if meta is not None else {
        "fitted": False, "why": "no metadata", "applied_ppm": 0.0, "intercept": offset}
    if drift.get("reason"):
        extra.append(drift["reason"])
    post_ms = window_s * 1000 - pre_ms

    # silence floor: strictly before the first signal (first detected onset, else first expected hit)
    firsts = [o for o in onsets[:1]]
    if not firsts and offset is not None and planned:
        firsts = [int(offset + planned[0])]
    if firsts:
        pre_end = firsts[0] - int(PRE_MARGIN_S * rate)
    else:
        pre_end = len(mono) if (control or not events) else None
    # DC: each channel's mean over the floor region (whole channel without one) is removed before the floor and tail
    # envelope are measured, so a constant offset is not reported as floor/hum; all other figures use stored samples
    has_region = pre_end is not None and pre_end >= block
    dc = [mean_or_zero(ch[:pre_end] if has_region else ch) for ch in sig]
    if has_region:
        region = [max((v - m for v, m in zip(vals, dc)), key=abs) for vals in zip(*(ch[:pre_end] for ch in sig))]
        fl = median(envelope_blocks(region, block))
        # digital silence: every stored sample of the region identical per channel (a quiet 16-bit analog floor
        # rounds most 1 ms blocks to 0, so a zero median alone is not proof)
        digital_silence = all(v == ch[0] for ch in sig for v in ch[:pre_end])
        silence = {"available": True, "region_samples": [0, pre_end], "peak": max(map(abs, region)),
                   "peak_dbfs": db(max(map(abs, region))),
                   "rms_dbfs": db(math.sqrt(dot(region, region) / len(region))),
                   "median_1ms_block_peak": fl, "median_1ms_block_peak_dbfs": db(fl),
                   "digital_silence": digital_silence}
    else:
        fl, digital_silence = None, False
        silence = {"available": False, "reason": "no signal-free region before the first onset/expected hit"}
    # the PRE_MARGIN_S margin before the first onset, up to its window start or the foot of its attack where that is
    # earlier (as for the tails): a precursor or swell there is never compared (DC removed, 1 ms peak envelope)
    pre_env = None
    if has_region and onsets:
        e1 = max(pre_end, min(onsets[0] - pre, foot[onsets[0]]))
        comb = [max((v - m for v, m in zip(vals, dc)), key=abs) for vals in zip(*(ch[pre_end:e1] for ch in sig))]
        pre_env = envelope_blocks(comb, block)
        pk1, t1 = max(pre_env, default=0.0), past_window_threshold(fl)
        pre_first = {"available": True, "region_samples": [pre_end, e1], "peak_dbfs": db(pk1),
                     "threshold_dbfs": db(t1), "signal": pk1 >= t1}
    else:
        pre_first = {"available": False, "reason": "no silence-floor region or no onset"}
    dc_report = [{"channel": c, "mean": r7(m), "mean_dbfs": db(abs(m)),
                              "basis": "silence-floor region" if has_region else "whole channel"}
                 for c, m in zip(channels, dc)]

    ref_onsets = []
    if ref_ch:
        rsig = data[ref_ch - 1]
        rfl = median(envelope_blocks(rsig, block)) or 0.0
        ref_onsets = detect_onsets(rsig, rate, max(rfl * 10 ** (threshold_db / 20), 10 ** (abs_floor_db / 20)),
                                   refractory_s)

    hits, tail_env = [], []
    for k in sorted(matches):
        o = matches[k]
        nxt = next((x for x in onsets if x > o), len(mono))
        # the next hit's window, and the foot of its attack where that is earlier (a slow attack; at most
        # MAX_ATTACK_LEAD_MS before its onset), excluded
        end = max(o, min(nxt - pre, foot[nxt])) if nxt < len(mono) else nxt
        pk, bd = env_peak[o]
        h = {"event": events[k].get("i") if events else k, "hit": hit_id(events[k]) if events else k,
             "onset_sample": o, "onset_precise_sample": precise[o], "onset_s": round(o / rate, 6),
             "scheduled_sample": round(planned[k] + offset, 3),
             "drift_scheduled_sample": round(drift_position(drift, planned[k]), 3),
             "residual_samples": round(o - planned[k] - offset, 3),
             "residual_ms": round((o - planned[k] - offset) * 1000 / rate, 4),
             "onset_envelope_peak_dbfs": db(pk), "onset_bound_dbfs": db(bd),
             "onset_bound_limited": None if pk is None else not pk >= bd * 10 ** (ONSET_REL_DB / 20),
             "window": {"start": o - pre, "end": o - pre + length},
             "complete": o - pre >= 0 and o - pre + length <= len(mono),
             "channels": hit_metrics(sig, o, pre, length, info),
             "tail": tail_figures(sig, o, end, rate, fl, info["full_scale"], info["sample_type"] == "float", post_ms,
                                  dc)}
        tail_env.append(h["tail"].pop("_env"))
        h["tail"]["ends_at"] = "next onset" if nxt < len(mono) else "end of file"
        if ref_onsets:
            prior = [r for r in ref_onsets if r <= o + int(0.005 * rate)]
            if prior:
                h["minus_reference_ms"] = round((o - prior[-1]) * 1000 / rate, 4)
        hits.append(h)

    res = [h["residual_ms"] for h in hits]
    render_timing = None
    if (meta or {}).get("_kind") == "render" and hits:
        rs = [round(h["onset_precise_sample"] - planned[k] - offset, 3) for k, h in zip(sorted(matches), hits)]
        render_timing = {"residual_spread_samples": round(max(rs) - min(rs), 3), "bound_samples": RENDER_JITTER_MAX}
        if not render_timing["residual_spread_samples"] <= RENDER_JITTER_MAX:
            extra.append(f"render onsets are not at one offset from their declared frames: residual spread "
                         f"{render_timing['residual_spread_samples']} samples > {RENDER_JITTER_MAX:g} (residuals "
                         f"{rs}); a deterministic render has no onset jitter")
    kind = (meta or {}).get("_kind")
    if kind == "render" and finite(fl) and fl > RENDER_FLOOR_MAX:
        extra.append(f"render with an analog-looking silence floor ({db(fl)} dBFS > {db(RENDER_FLOOR_MAX)} dBFS): "
                     "not a render?")
    elif kind == "capture" and fl == 0 and digital_silence:
        extra.append("capture with a digitally silent floor (every sample before the first onset identical, DC "
                     "included): not a hardware capture?")
    mean = sum(res) / len(res) if res else None
    jitter = {"n": len(res), "max_abs_ms": max(map(abs, res)) if res else None,
              "std_ms": round(math.sqrt(sum((r - mean) ** 2 for r in res) / len(res)), 4) if res else None,
              "basis": "residuals after one fitted offset; event times from host sends when present, else planned"}

    levels = []
    for c in range(len(sig)):
        pk = [abs(h["channels"][c]["peak_raw"]) for h in hits if h["channels"][c].get("complete")]
        rm = [h["channels"][c]["rms"] for h in hits if h["channels"][c].get("complete")]
        levels.append({"channel": channels[c], "peak_raw_abs": pk, "rms": rm,
                       "peak_spread_db": db(max(pk) / min(pk)) if pk and min(pk) else None,
                       "rms_spread_db": db(max(rm) / min(rm)) if rm and min(rm) else None,
                       "peak_db_rel_hit0": [db(p / pk[0]) for p in pk] if pk and pk[0] else None})

    pairs = []
    for i in range(len(hits)):
        for j in range(i + 1, len(hits)):
            c = compare(sig, hits[i]["onset_sample"], sig, hits[j]["onset_sample"], pre, length, max_lag)
            pairs.append({"a": hits[i]["hit"], "b": hits[j]["hit"], **c})

    expected = 0 if control else (len(planned) if events else None)
    unmatched = [o for o in onsets if o not in matches.values()]
    notes_sent = [e for e in all_events if e.get("kind") == "note_on"]
    complete = (expected is not None and len(hits) == expected and all(h["complete"] for h in hits)
                and (not control or not onsets and not nonfinite))
    rep = {
        "wav": os.path.abspath(path), "sidecar": os.path.abspath(sidecar) if sidecar else None, "source": source,
        "track_state": track_state(meta),
        "format": {k: info[k] for k in ("rate", "channels", "sample_type", "bits", "frames")},
        "definitions": DEFINITIONS, "analysed_channels": channels, "comparable_channels": comparable,
        "timing_reference_channel": ref_ch, "unused_channels": unused_report, "chain_calibration": cal_report,
        "detection": {"threshold": threshold, "threshold_dbfs": db(threshold), "threshold_db_over_floor": threshold_db,
                      "abs_floor_dbfs": abs_floor_db, "refractory_s": refractory_s, "tolerance_ms": tolerance_ms,
                      "pre_ms": pre_ms, "window_s": window_s, "post_ms": post_ms, "max_lag": max_lag,
                      "protocol": DETECTION, "default_refractory_s": default_refractory, "deviations": deviations,
                      "onset_rule": {"envelope_ms": ENVELOPE_MS, "relative_db": -ONSET_REL_DB,
                                     "floor_bound_db": ONSET_FLOOR_DB, "abs_bound_dbfs": db(TAIL_FALLBACK)},
                      "deviations_note": "less sensitive than the protocol settings, or --no-calibration: blocks the "
                                         "whole-take verdict"},
        "onsets": onsets, "onsets_precise": [precise[o] for o in onsets], "triggers": triggers,
        "onset_thresholds_dbfs": [db(t) for t in onset_thresholds], "unmatched_onsets": unmatched,
        "unexplained_onsets": unmatched if (events or control) else [],
        "unexplained_note": "audible onsets matching no expected hit: the take fails a verdict until the operator "
                            "classifies them",
        "stimulus_notes": {"note_on_events": len(notes_sent),
                           "note": "MIDI stimulus as recorded in the metadata; for a control no audio hit is expected"}, "schedule_offset_samples": offset,
        "schedule_offset_note": "fit of onset minus event time from the anchor; NOT a latency measurement",
        "schedule_offset_bound": offset_bound, "schedule_drift": drift, "render_timing": render_timing,
        "expected_hits": expected, "matched_hits": len(hits), "hits_complete": complete,
        "expected_hit_ids": [] if control else ([hit_id(e) for e in events] if events else None),
        "expected_hit_times_s": [] if control else ([{"hit": hit_id(e), "time_s": t} for e, t in zip(events, planned_s)]
                                                    if events else None),
        "matched_hit_ids": [h["hit"] for h in hits],
        "missing_events": [events[k].get("i") for k in range(len(planned)) if k not in matches] if events else [],
        "capture": capture_validity(meta, info, raw_data, expected_s, nonfinite, extra),
        "silence_floor": silence, "before_first_hit": pre_first, "dc_removed": dc_report, "jitter": jitter,
        "levels": levels, "hits": hits,
        "signal_past_window": any(h["tail"]["signal_past_window"] for h in hits),
        "signal_past_window_hits": [h["hit"] for h in hits if h["tail"]["signal_past_window"]],
        "pairwise_diagnostics": pairs,
    }
    if rep["signal_past_window"]:
        rep["window_note"] = ("signal persists past the 198 ms active window: a uniformly extended window must be "
                              "agreed with Codex; nothing was cropped from the tail figures")
    if control:                                       # NaN/Inf samples are invisible to detection: never 'silent'
        rep["control_silent"] = not onsets and not nonfinite
    rep["_data"], rep["_sig"], rep["_info"], rep["_pre_len"] = data, sig, info, (pre, length)
    rep["_dc"] = dc
    rep["_tail_env"] = tail_env                       # per hit, DC removed, for the verdict's common-scale test
    rep["_pre_env"] = pre_env                         # the margin before the first hit, likewise
    return rep


CAL_METHOD = ("per calibration hit and analysed channel k: x_k is first aligned to x_ref (the first analysed channel) "
              "at its own fractional inter-channel delay (fit_hit_lag within +-1 ms, as one hit of the whole-take "
              "verdict; band-limited fractional read), then least-squares amplitude ratio r = sum(x_k*x_ref)/"
              "sum(x_ref^2) over the hit's 200 ms active window (DC removed: each channel's silence-floor mean); "
              "ratio_db = 20*log10(r); frozen ratio = 10^(mean ratio_db / 20); spread = max - min of ratio_db over the "
              "hits, std = population standard deviation of ratio_db. The delay (positive = x_k later) is reported, "
              "never applied: an unaligned ratio is biased by the delay's phase error")


def calibration_stats(path, sidecar):
    """Recorder-chain calibration from a calibration take (capture.py calibrate): the per-hit channel ratios, their
    mean and spread, and the acceptance reasons (CAL_SPREAD_MAX_DB, CAL_MIN_HITS, shape match, a valid capture with
    every hit matched and complete, no clipping). CAL_METHOD; DEFINITIONS chain_calibration."""
    rep = analyse(path, sidecar)
    reasons = [f"capture: {r}" for r in rep["capture"]["reasons"]]
    if not rep["chain_calibration"].get("calibration_take"):
        reasons.append("not a calibration take (schema circuit-rhythm-calibration/1)")
    chans = rep["analysed_channels"]
    if len(chans) < 2:
        reasons.append(f"analysed channels {chans}: a calibration needs at least two")
    if rep["expected_hits"] is None or rep["expected_hits"] < CAL_MIN_HITS:
        reasons.append(f"expected hits {rep['expected_hits']!r} < {CAL_MIN_HITS}")
    if not rep["hits_complete"]:
        reasons.append(f"hits not all matched and complete ({rep['matched_hits']} of {rep['expected_hits']})")
    if rep["unexplained_onsets"]:
        reasons.append(f"unexplained onsets at samples {rep['unexplained_onsets']}")
    quiet = [h["hit"] for h in rep["hits"] if h.get("onset_bound_limited")]
    if quiet:
        reasons.append(f"hits {quiet} too quiet for onset timing")
    sig = [[v - m for v in ch] for ch, m in zip(rep["_sig"], rep["_dc"])]
    bound = int(round(LAG_BOUND_MS * rep["format"]["rate"] / 1000))
    hits, per, delays = [], {str(c): [] for c in chans[1:]}, {str(c): [] for c in chans[1:]}
    for h in rep["hits"]:
        if not h["complete"]:
            continue
        s, e = h["window"]["start"], h["window"]["end"]
        x0 = sig[0][s:e]
        row = {"hit": h["hit"], "onset_sample": h["onset_sample"], "window": [s, e], "reference_channel": chans[0],
               "reference_rms_dbfs": db(math.sqrt(dot(x0, x0) / len(x0))), "channels": {}}
        for i, c in enumerate(chans[1:], 1):
            f = fit_hit_lag([sig[0]], [sig[i]], s, s, e - s, bound)
            lag, frac = (f[0], 0.0 if abs(f[1]) < SNAP_FRAC else f[1]) if f else (0, 0.0)
            xk = frac_window([sig[i]], s + lag, e - s, frac) if f else None
            if xk is None or abs(lag) == bound:
                reasons.append(f"hit {h['hit']} channel {c}: no alignment with channel {chans[0]} inside +-{bound} "
                               "samples")
                xk = sig[i][s:e]
            else:
                delays[str(c)].append(lag + frac)
            r, nr = gain_nrmse(xk, x0)
            cs = cosine(xk, x0)
            row["channels"][str(c)] = {"ratio": r, "ratio_db": round(20 * math.log10(r), 6) if finite(r) and r > 0
                                       else None, "cosine": r7(cs), "nrmse": r7(nr), "delay_samples": round(lag + frac, 4),
                                       "rms_dbfs": db(math.sqrt(dot(xk, xk) / len(xk)))}
            if not finite(r) or r <= 0:
                reasons.append(f"hit {h['hit']} channel {c}: ratio {r!r} is not positive (inverted or no signal)")
            else:
                per[str(c)].append(20 * math.log10(r))
            if cs is None or not cs >= COSINE_TARGET or nr is None or not nr <= NRMSE_TARGET:
                reasons.append(f"hit {h['hit']} channel {c}: cosine {r7(cs)} / NRMSE {r7(nr)} against channel "
                               f"{chans[0]} (need >= {COSINE_TARGET} / <= {NRMSE_TARGET}): not the same signal")
        hits.append(row)
    out_ch, ratios = {}, {str(chans[0]): 1.0} if chans else {}
    for c, dbs in per.items():
        if not dbs:
            continue
        m = math.fsum(dbs) / len(dbs)
        sp = max(dbs) - min(dbs)
        sd = math.sqrt(math.fsum((x - m) ** 2 for x in dbs) / len(dbs))
        ratios[c] = 10 ** (m / 20)
        dl = delays[c]
        out_ch[c] = {"ratio": ratios[c], "mean_db": round(m, 6), "spread_db": round(sp, 6), "std_db": round(sd, 6),
                     "per_hit_db": [round(x, 6) for x in dbs], "n": len(dbs),
                     "delay_samples": round(math.fsum(dl) / len(dl), 4) if dl else None,
                     "delay_spread_samples": round(max(dl) - min(dl), 4) if dl else None,
                     "delay_exceeds_skew_max": bool(dl) and abs(math.fsum(dl) / len(dl)) > SKEW_MAX}
        if not sp <= CAL_SPREAD_MAX_DB:
            reasons.append(f"channel {c}: per-hit ratio spread {sp:.4f} dB > {CAL_SPREAD_MAX_DB:g} dB (gain or signal "
                           "not settled; recalibrate)")
    if len(ratios) != len(chans):
        reasons.append("no ratio for every analysed channel")
    cells = [x for h in hits for x in h["channels"].values()]
    cos_min = min((x["cosine"] for x in cells if x["cosine"] is not None), default=None)
    nr_max = max((x["nrmse"] for x in cells if x["nrmse"] is not None), default=None)
    sp_max = max((v["spread_db"] for v in out_ch.values()), default=None)

    def gate(name, ok, value, threshold):
        return {"name": name, "status": "PASS" if ok else "FAIL", "value": value, "threshold": threshold}
    gates = [  # the same tests as the reasons above, for display (levelview); the reasons decide
        gate("hits", rep["hits_complete"] and (rep["expected_hits"] or 0) >= CAL_MIN_HITS
             and not rep["unexplained_onsets"], f"{rep['matched_hits']}/{rep['expected_hits']}",
             f">= {CAL_MIN_HITS} matched and complete, no extra onset"),
        gate("clipping", not rep["capture"]["full_scale_samples"], f"{rep['capture']['full_scale_samples']} samples",
             "0 full-scale samples"),
        gate("floor", not quiet, f"floor {rep['silence_floor'].get('median_1ms_block_peak_dbfs')} dBFS"
             + (f", quiet hits {quiet}" if quiet else ""), f"hit envelope peak >= {ONSET_REL_DB:g} dB over the onset "
             f"bound (floor + {ONSET_FLOOR_DB:g} dB, >= {db(TAIL_FALLBACK):g} dBFS)"),
        gate("capture valid", rep["capture"]["valid"], f"{len(rep['capture']['reasons'])} reasons", "no reason"),
        gate("shape", bool(cells) and cos_min is not None and cos_min >= COSINE_TARGET and nr_max is not None
             and nr_max <= NRMSE_TARGET, f"cosine >= {cos_min}, NRMSE <= {nr_max}",
             f"cosine >= {COSINE_TARGET}, NRMSE <= {NRMSE_TARGET}"),
        gate("polarity", bool(cells) and all(x["ratio"] is not None and finite(x["ratio"]) and x["ratio"] > 0
                                             for x in cells), "ratios positive" if cells else "no ratio",
             "every ratio > 0"),
        gate("spread", sp_max is not None and sp_max <= CAL_SPREAD_MAX_DB,
             f"{sp_max:.4f} dB" if sp_max is not None else "none", f"<= {CAL_SPREAD_MAX_DB:g} dB max-min"),
    ]
    return {"accepted": not reasons, "gates": gates, "reasons": reasons, "method": CAL_METHOD, "reference_channel": chans[0],
            "delay_note": f"inter-channel delay measured, not applied; above {SKEW_MAX:g} samples the whole-take "
                          "verdict's inter-channel skew gate fails every take on this chain",
            "analysed_channels": chans, "unused_channels": rep["unused_channels"], "ratios": ratios,
            "channels": out_ch, "spread_bound_db": CAL_SPREAD_MAX_DB, "min_hits": CAL_MIN_HITS, "hits": hits,
            "matched_hits": rep["matched_hits"], "expected_hits": rep["expected_hits"],
            "capture_valid": rep["capture"]["valid"], "full_scale_samples": rep["capture"]["full_scale_samples"],
            "schedule_offset_samples": rep["schedule_offset_samples"],
            "silence_floor_dbfs": rep["silence_floor"].get("median_1ms_block_peak_dbfs")}


def _check_pairing(a, b, pairs):
    if a["format"]["rate"] != b["format"]["rate"]:
        raise ValueError(f"sample rates differ ({a['format']['rate']} vs {b['format']['rate']}); no resampling is "
                         "done here: resample explicitly, log it, and compare the resampled file")
    if not pairs:
        raise ValueError("explicit channel pairs required, e.g. --pairs 1=1,2=2 (A channel = B channel)")
    for ca, cb in pairs:
        if not 1 <= ca <= a["format"]["channels"] or not 1 <= cb <= b["format"]["channels"]:
            raise ValueError(f"pair {ca}={cb} outside the files' channels")
    if a["_pre_len"] != b["_pre_len"]:
        raise ValueError("pre/window lengths differ between the two analyses")


def against(a, b, max_lag, pairs=None):
    """Per-hit DIAGNOSTICS between two takes: B's own onsets, B's own samples, each hit its own lag and gain."""
    _check_pairing(a, b, pairs)
    pre, length = a["_pre_len"]
    asig = [a["_data"][ca - 1] for ca, _ in pairs]
    bsig = [b["_data"][cb - 1] for _, cb in pairs]
    bh = {h["hit"]: h for h in b["hits"]}
    return [{"hit": h["hit"], **compare(asig, h["onset_sample"], bsig, bh[h["hit"]]["onset_sample"], pre, length,
                                        max_lag)}
            for h in a["hits"] if h["hit"] in bh]


def pairing_reasons(a, b, pairs):
    """Every channel of each file (a declared timing reference excepted) in exactly one pair: no partial, duplicated
    or mono-vs-stereo pairing. Restricting the analysed channels does not shrink this set."""
    reasons = []
    for name, r, side in (("A", a, [x for x, _ in pairs]), ("B", b, [y for _, y in pairs])):
        dup = sorted({c for c in side if side.count(c) > 1})
        if dup:
            reasons.append(f"take {name}: channels {dup} paired more than once")
        if sorted(set(side)) != r["comparable_channels"]:
            reasons.append(f"take {name}: pairs use channels {sorted(set(side))}, the file's channels (timing "
                           f"reference excepted) are {r['comparable_channels']}: each must be paired exactly once")
    return reasons


def hit_time_reasons(a, b):
    """Each common hit id at the same event time on both sides, relative to the first common hit (A's order), within
    the onset tolerance: ids that name different events are refused."""
    ta = {repr(e["hit"]): e["time_s"] for e in a.get("expected_hit_times_s") or []}
    tb = {repr(e["hit"]): e["time_s"] for e in b.get("expected_hit_times_s") or []}
    ids = [k for k in sorted(ta, key=ta.get) if k in tb]
    if not ids:
        return []
    tol = min(a["detection"]["tolerance_ms"], b["detection"]["tolerance_ms"]) / 1000
    k0 = ids[0]
    bad = [k for k in ids if not abs((ta[k] - ta[k0]) - (tb[k] - tb[k0])) <= tol]
    if not bad:
        return []
    return [f"hit ids {', '.join(bad)} are at different event times relative to hit {k0} on the two sides "
            f"(A {[round(ta[k] - ta[k0], 6) for k in bad]} s vs B {[round(tb[k] - tb[k0], 6) for k in bad]} s, "
            f"tolerance {tol * 1000:g} ms): the ids do not name the same events"]


def common_scale(a, b, g, av, bv):
    """(k_a, k_b, side): factors mapping each take's samples onto the common scale (DEFINITIONS common_scale). The
    hardware capture's scale when exactly one side is a capture (FLOOR_REF and the -80 dBFS tail bound are absolute
    dBFS of a real capture), else the louder take's. Independent of the argument order and of the render's level."""
    kinds = [r["capture"].get("kind") for r in (a, b)]
    if kinds.count("capture") == 1:
        side = "A" if kinds[0] == "capture" else "B"
    else:
        side = "A" if dot(av, av) >= dot(bv, bv) else "B"
    return (1.0, abs(g), side) if side == "A" else (1 / abs(g), 1.0, side)


def floor_reasons(a, b, ka, kb, side):
    """Noise-floor gate (FLOOR_TOL_DB, FLOOR_REF) with both floors on the common scale (ka, kb)."""
    fa = a["silence_floor"].get("median_1ms_block_peak")
    fb = b["silence_floor"].get("median_1ms_block_peak")
    if not finite(fa) or not finite(fb):
        return [f"noise floors not comparable (A {fa!r}, B {fb!r}): a silence floor is unavailable"], None
    ea, eb = max(fa * ka, FLOOR_REF), max(fb * kb, FLOOR_REF)
    info = {"a_dbfs": db(fa), "b_dbfs": db(fb), "common_scale": f"take {side}", "a_common_dbfs": db(fa * ka),
            "b_common_dbfs": db(fb * kb), "reference_dbfs": db(FLOOR_REF), "tolerance_db": FLOOR_TOL_DB,
            "rule": "on the common scale, neither floor (below the reference counts as the reference) exceeds the "
                    "other by more than the tolerance"}
    lim = 10 ** (FLOOR_TOL_DB / 20)
    for n, x, y in (("A", ea, eb), ("B", eb, ea)):
        if x > y * lim:
            return [f"take {n}: noise floor {db(x)} dBFS exceeds the other take's {db(y)} dBFS by more than "
                    f"{FLOOR_TOL_DB:g} dB (both on the scale of take {side}; floors below {db(FLOOR_REF)} dBFS count "
                    f"as {db(FLOOR_REF)}): added signal such as hum"], info
    return [], info


def past_window(r, k):
    """Hits of take r with signal past the window, threshold max(floor + 10 dB, -80 dBFS) on the common scale (k maps
    r onto it), so the result does not depend on r's own level."""
    f = r["silence_floor"].get("median_1ms_block_peak")
    t_common = past_window_threshold(f * k if finite(f) else None)
    t = t_common / k
    post = r["detection"]["post_ms"]
    hits = [h["hit"] for h, env in zip(r["hits"], r["_tail_env"]) if last_crossing_ms(env, t) > post]
    pk = max(r["_pre_env"], default=0.0) if r["_pre_env"] is not None else None
    return {"threshold_common_dbfs": db(t_common), "threshold_own_dbfs": db(t), "hits": hits,
            "before_first_hit_peak_common_dbfs": db(pk * k) if pk is not None else None,
            "before_first_hit": pk is not None and pk >= t}


SCOPE = "whole-take verdict: per-hit fractional lag within +-1 ms of the schedule, one gain, joint figures"


TRACK_DEVIATION = re.compile(r"^track=(\d+) \(protocol 1\)$")
TRACK_RULE = ("both takes declare a track (1..8) with its MIDI channel routing, trigger, preset name and every preset "
              "message (sent on a capture, applied in a render); all declared values equal. Keyboard note: when a "
              "render is involved, a measured keyboard note (semitones and evidence) on at least one side, and every "
              "declared note measured and equal (a capture's 'unknown' declares nothing); capture vs capture needs no "
              "measured note, but two measured notes must be equal, and with fewer than two the absolute keyboard-note "
              "state is reported unverified (never inferred from the waveform verdict). A 'track=N (protocol 1)' deviation is then resolved (kept and "
              "listed), not deleted; missing or unequal state fails (Codex rulings 2026-10-17, 2026-10-19)")


def track_state(meta):
    """The take's declared track state, or None. Capture: stimulus.track, settings.track_channel/preset/sent and an
    optional top-level keyboard_note. Render: the provenance's "track" object, as written."""
    if not isinstance(meta, dict):
        return None
    if meta.get("_kind") == "render":
        t = meta.get("track")
        return t if isinstance(t, dict) else None
    st, se = _obj(meta.get("stimulus")), _obj(meta.get("settings"))
    if "track" not in st:
        return None
    sent = se.get("sent")
    msgs = None if not isinstance(sent, list) else [
        {k: m.get(k) for k in ("name", "kind", "channel", "number", "value", "sent")} if isinstance(m, dict) else m
        for m in sent]
    return {"number": st.get("track"), "track_midi_channel": se.get("track_channel"),
            "trigger": {"midi_channel": st.get("midi_channel"), "note": st.get("note")},
            "preset": se.get("preset"), "preset_messages": msgs, "keyboard_note": meta.get("keyboard_note")}


def _int_in(x, lo, hi):
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def keyboard_note_reasons(k, name):
    if not isinstance(k, dict) or k.get("state") != "measured":
        return [f"take {name}: keyboard note state {k!r} is not 'measured'"]
    out = []
    if not _is_num(k.get("semitones")):
        out.append(f"take {name}: keyboard note semitones {k.get('semitones')!r} is not a finite number")
    if not isinstance(k.get("evidence"), str) or not k["evidence"].strip():
        out.append(f"take {name}: keyboard note has no evidence")
    return out


def _capture_keyboard_notes(ta, tb):
    """Capture vs capture (2026-10-19 policy): (reasons, status). No measured note is required; an absent or
    'unknown' note leaves the absolute keyboard-note state unverified; two measured notes must be equal."""
    reasons, measured, unverified = [], {}, []
    for n, t in (("A", ta), ("B", tb)):
        k = t.get("keyboard_note")
        if k is None or isinstance(k, dict) and k.get("state") == "unknown":
            unverified.append(n)
        elif isinstance(k, dict) and k.get("state") == "measured":
            bad = keyboard_note_reasons(k, n)
            reasons += bad
            if not bad:
                measured[n] = k
        else:
            reasons.append(f"take {n}: keyboard note {k!r} is neither measured nor unknown")
    if len(measured) == 2 and measured["A"]["semitones"] != measured["B"]["semitones"]:
        reasons.append(f"keyboard note differs: A {measured['A']['semitones']} vs B {measured['B']['semitones']} "
                       "semitones")
    if reasons:
        status = "invalid"
    elif len(measured) == 2:
        status = "verified: both takes measured, equal"
    else:
        status = (f"unverified: no measured keyboard note on take(s) {', '.join(unverified)}; the absolute "
                  "keyboard-note state is not inferred from the waveform verdict")
    return reasons, status


def track_match(ta, tb, kinds=None):
    """(reasons, keyboard-note status): reasons why the two takes' track states are not explicit and equal (empty
    when they are). kinds: the two takes' kinds; ("capture", "capture") applies the capture-vs-capture keyboard-note
    rule, anything else (a render involved, or unknown) needs a measured keyboard note."""
    reasons, msgsets = [], {}
    for name, t in (("A", ta), ("B", tb)):
        if not isinstance(t, dict):
            reasons.append(f"take {name}: no declared track")
            continue
        if not _int_in(t.get("number"), 1, 8):
            reasons.append(f"take {name}: track {t.get('number')!r} is not 1..8")
        if not _int_in(t.get("track_midi_channel"), 1, 16):
            reasons.append(f"take {name}: track MIDI channel {t.get('track_midi_channel')!r} is not 1..16")
        trig = t.get("trigger")
        if not isinstance(trig, dict) or not _int_in(trig.get("midi_channel"), 1, 16) \
                or not _int_in(trig.get("note"), 0, 127):
            reasons.append(f"take {name}: trigger {trig!r} is not {{midi_channel 1..16, note 0..127}}")
        if not isinstance(t.get("preset"), str) or not t["preset"]:
            reasons.append(f"take {name}: no preset name")
        msgs = t.get("preset_messages")
        if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) for m in msgs):
            reasons.append(f"take {name}: no preset messages")
            continue
        bad = [m.get("name") for m in msgs if m.get("sent") is not True and m.get("applied") is not True]
        if bad:
            reasons.append(f"take {name}: preset messages not sent/applied: {bad}")
        msgsets[name] = {json.dumps([m.get("kind"), m.get("channel"), m.get("number"), m.get("value")]) for m in msgs}
    if reasons:
        return reasons, "not checked: track state incomplete"
    for key in ("number", "track_midi_channel", "trigger", "preset"):
        if ta[key] != tb[key]:
            reasons.append(f"track state differs: {key} A {ta[key]!r} vs B {tb[key]!r}")
    if msgsets["A"] != msgsets["B"]:
        reasons.append(f"preset messages differ: only A {sorted(msgsets['A'] - msgsets['B'])}, "
                       f"only B {sorted(msgsets['B'] - msgsets['A'])}")
    if kinds is not None and tuple(kinds) == ("capture", "capture"):
        kb_reasons, status = _capture_keyboard_notes(ta, tb)
        return reasons + kb_reasons, status
    # a capture's explicit 'unknown' (capture.py's default) declares nothing, like an absent note; a render's must be
    # measured
    kinds = list(kinds) if kinds is not None else [None, None]
    kbs, kb_reasons = {}, []
    for n, t, kind in (("A", ta, kinds[0]), ("B", tb, kinds[1])):
        k = t.get("keyboard_note")
        if k is None or kind == "capture" and isinstance(k, dict) and k.get("state") == "unknown":
            continue
        kbs[n] = k
    if not kbs:
        kb_reasons.append("keyboard note unknown on both sides (no measured state)")
    for n, k in kbs.items():
        kb_reasons += keyboard_note_reasons(k, n)
    if len(kbs) == 2 and not kb_reasons and kbs["A"]["semitones"] != kbs["B"]["semitones"]:
        kb_reasons.append(f"keyboard note differs: A {kbs['A']['semitones']} vs B {kbs['B']['semitones']} semitones")
    status = "invalid" if kb_reasons else f"verified: measured on take(s) {', '.join(kbs)}, every declared note equal"
    return reasons + kb_reasons, status


def whole_take(a, b, pairs, max_lag=DETECTION["max_lag"]):
    """Fidelity VERDICT: per-hit fractional lag (within +-LAG_BOUND_MS of the schedule, shared by all channels of the
    hit) and one gain for the whole take (all hits, all channel pairs), judged jointly."""
    _check_pairing(a, b, pairs)
    pre, length = a["_pre_len"]
    asig = [a["_data"][ca - 1] for ca, _ in pairs]
    bsig = [b["_data"][cb - 1] for _, cb in pairs]
    reasons = []
    for name, r in (("A", a), ("B", b)):
        for key in ("expected_hit_ids", "matched_hit_ids"):
            ids = r.get(key) or []
            if len(ids) != len(set(map(repr, ids))):
                reasons.append(f"take {name}: duplicate {key} {ids}")
    if reasons:                                       # before building any id-keyed map
        return {"scope": SCOPE, "applicable": True, "pass": False,
                "hits": [], "reasons": reasons}
    bh = {h["hit"]: h for h in b["hits"]}
    common = [h for h in a["hits"] if h["hit"] in bh]
    out = {"scope": SCOPE, "definitions": DEFINITIONS,
           "pairs": [f"{x}={y}" for x, y in pairs], "applicable": True}
    if a.get("control_silent") is not None or b.get("control_silent") is not None:
        out.update({"applicable": False, "pass": False, "hits": [],
                    "reasons": ["control take: judged by control_silent and silence_floor, not by a waveform verdict"]})
        return out
    reasons += pairing_reasons(a, b, pairs)
    reasons += hit_time_reasons(a, b)
    kinds = [r["capture"].get("kind") for r in (a, b)]
    track_reasons, keyboard_note = track_match(a.get("track_state"), b.get("track_state"), kinds)
    reasons += track_reasons
    resolved = []
    out["track"] = {"rule": TRACK_RULE, "match": not track_reasons, "reasons": track_reasons,
                    "keyboard_note": keyboard_note, "resolved_deviations": resolved,
                    "A": a.get("track_state"), "B": b.get("track_state")}
    resolved_by = ("explicit track match (both sides declare the same track, routing and preset messages; keyboard "
                   f"note {keyboard_note})")
    if max_lag != DETECTION["max_lag"]:
        reasons.append(f"verdict max_lag={max_lag!r} (protocol {DETECTION['max_lag']!r})")
    if a.get("source") != b.get("source"):
        reasons.append(f"source names differ: A {a.get('source')!r} vs B {b.get('source')!r}")
    for name, r in (("A", a), ("B", b)):
        if r["detection"].get("deviations"):
            reasons.append(f"take {name}: detection deviations {r['detection']['deviations']}")
        remaining = []
        for d in r["capture"].get("protocol_deviations") or []:
            m = TRACK_DEVIATION.match(d) if isinstance(d, str) else None
            if m and not track_reasons and int(m.group(1)) == (r.get("track_state") or {}).get("number"):
                resolved.append({"take": name, "deviation": d, "resolved_by": resolved_by})
            else:
                remaining.append(d)
        if remaining:
            reasons.append(f"take {name}: recorded protocol deviations {remaining}")
        if r["expected_hits"] is None:
            reasons.append(f"take {name}: no validated schedule (sidecar or render provenance) - diagnostics only")
        elif not r["hits_complete"]:
            reasons.append(f"take {name}: expected hits/windows not all present and complete")
        if r.get("unexplained_onsets"):
            reasons.append(f"take {name}: unexplained extra onsets at samples {r['unexplained_onsets']} "
                           "(invalid pending operator classification)")
    sets = {n: (set(r.get("expected_hit_ids") or []), set(r.get("matched_hit_ids") or [])) for n, r in
            (("A", a), ("B", b))}
    if sets["A"][0] != sets["B"][0]:
        reasons.append(f"expected hit ids differ: A {sorted(sets['A'][0], key=repr)} vs B {sorted(sets['B'][0], key=repr)}")
    if sets["A"][1] != sets["B"][1]:
        reasons.append(f"matched hit ids differ: A {sorted(sets['A'][1], key=repr)} vs B {sorted(sets['B'][1], key=repr)}")
    for n in ("A", "B"):
        if sets[n][1] != sets[n][0]:
            reasons.append(f"take {n}: matched hit ids {sorted(sets[n][1], key=repr)} != expected {sorted(sets[n][0], key=repr)}")
    if not common:
        reasons.append("no common hits")
    for name, r in (("A", a), ("B", b)):
        if not r["capture"]["valid"]:
            reasons.append(f"capture {name} invalid: {r['capture']['reasons']}")
    for name, r in (("A", a), ("B", b)):
        quiet = [h for h in r["hits"] if h.get("onset_bound_limited")]
        if quiet:
            pk, bd = [h["onset_envelope_peak_dbfs"] for h in quiet], [h["onset_bound_dbfs"] for h in quiet]
            reasons.append(f"take {name}: hit too quiet for onset timing: hits {[h['hit'] for h in quiet]} have "
                           f"envelope peaks {pk} dBFS, less than {ONSET_REL_DB:g} dB above their onset bounds {bd} "
                           f"dBFS (max(floor + {ONSET_FLOOR_DB:g} dB, {db(TAIL_FALLBACK):g} dBFS)): the onset sits on "
                           f"the bound, not at peak - {ONSET_REL_DB:g} dB, so its timing depends on the take's level")
    out["hits"] = [h["hit"] for h in common]
    if not common:
        out.update({"pass": False, "reasons": reasons})
        return out
    rate = a["format"]["rate"]
    bound = int(round(LAG_BOUND_MS * rate / 1000))
    fits, av, bv = [], [], []
    for h in common:
        # B's window starts at A's plus the difference of the two schedule positions (drift term included, whole
        # samples), plus the lag
        shift = int(round(bh[h["hit"]]["drift_scheduled_sample"] - h["drift_scheduled_sample"]))
        sa = h["onset_sample"] - pre
        # the onset detector's lag (interpolated onsets): B's onset minus A's minus the schedule shift
        onset_lag = bh[h["hit"]]["onset_precise_sample"] - h["onset_precise_sample"] - shift
        f = fit_hit_lag(asig, bsig, sa, sa + shift, length, bound, onset_lag)
        if f is None:
            reasons.append(f"hit {h['hit']}: no complete alignment with a finite cosine within +-{bound} samples: "
                           "windows leave a file (not zero-padded) or samples are not finite")
            out.update({"pass": False, "reasons": reasons})
            return out
        lag, frac, chl = f
        frac = 0.0 if abs(frac) < SNAP_FRAC else frac  # applied as a whole-sample lag: no interpolation taps
        wb = frac_window(bsig, sa + shift + lag, length, frac)
        if wb is None:
            reasons.append(f"hit {h['hit']}: fractional lag {lag + frac:.4f} samples needs {SINC_HALF} interpolation "
                           "taps on both sides of B's window and they leave the file (never zero-padded): this hit "
                           "cannot be compared at a fractional lag")
            out.update({"pass": False, "reasons": reasons})
            return out
        fits.append((h, shift, onset_lag, (lag, frac, chl)))
        av += window(asig, sa, length)
        bv += wb
    g, e = gain_nrmse(av, bv)
    c = cosine(av, bv)
    floors, tails = None, {}
    if finite(g) and g:
        ka, kb, side = common_scale(a, b, g, av, bv)
        fr, floors = floor_reasons(a, b, ka, kb, side)
        reasons += fr
        for name, r, k in (("A", a, ka), ("B", b, kb)):
            tails[name] = past_window(r, k)
            if tails[name]["hits"]:
                reasons.append(f"take {name}: signal past the {r['detection']['post_ms']:g} ms window at hits "
                               f"{tails[name]['hits']} (threshold {tails[name]['threshold_common_dbfs']} dBFS on the "
                               f"scale of take {side}): the window would not compare it; a uniformly extended window "
                               "must be agreed")
            if tails[name]["before_first_hit"]:
                reasons.append(f"take {name}: signal in the {PRE_MARGIN_S * 1000:g} ms before the first hit's attack "
                               f"(peak {tails[name]['before_first_hit_peak_common_dbfs']} dBFS >= threshold "
                               f"{tails[name]['threshold_common_dbfs']} dBFS on the scale of take {side}): a precursor "
                               "or swell the window would not compare")
    else:
        reasons.append(f"no finite non-zero gain ({g!r}): noise floors and tails cannot be put on a common scale")
    per = []
    n = len(pairs) * length
    for k, (h, shift, onset_lag, (lag, frac, chl)) in enumerate(fits):
        ha, hb = av[k * n:(k + 1) * n], bv[k * n:(k + 1) * n]
        aa = dot(ha, ha)
        ch_lags = [None if x is None else round(x[0] + x[1], 4) for x in chl]
        known = [x for x in ch_lags if x is not None]
        skew = round(max(known) - min(known), 4) if known else None
        per.append({"hit": h["hit"], "schedule_shift_samples": shift, "lag_samples": round(lag + frac, 4),
                    "lag_ms": round((lag + frac) * 1000 / rate, 4), "integer_lag_samples": lag,
                    "at_bound": abs(lag) == bound, "onset_lag_samples": round(onset_lag, 4),
                    "lag_minus_onset_samples": round(lag + frac - onset_lag, 4), "channel_lags_samples": ch_lags,
                    "interchannel_skew_samples": skew, "cosine": r7(cosine(ha, hb)),
                    "nrmse_at_take_gain": r7(math.sqrt(math.fsum((x - g * y) ** 2 for x, y in zip(ha, hb)) / aa))
                    if aa and finite(g) else None})
        if abs(lag) == bound:
            reasons.append(f"hit {h['hit']}: best lag {lag} samples is at the +-{bound} sample ({LAG_BOUND_MS:g} ms) "
                           "bound around its schedule position: not aligned")
        if skew is None or skew > SKEW_MAX:
            reasons.append(f"hit {h['hit']}: inter-channel skew {skew} samples (channel lags {ch_lags}) is not <= "
                           f"{SKEW_MAX:g}: the channels are not aligned as in the other take (one lag per hit for all "
                           "channels)")
    lags = [p["lag_samples"] for p in per]
    dis = [p["lag_minus_onset_samples"] for p in per]
    timing = {"lag_minus_onset_samples": dis, "spread_samples": round(max(dis) - min(dis), 4),
              "bound_samples": TIMING_SPREAD_MAX,
              "rule": "per hit, waveform lag minus the onset detector's lag (B's interpolated onset minus A's minus "
                      "the schedule shift); the spread over the hits must not exceed the bound"}
    if not timing["spread_samples"] <= TIMING_SPREAD_MAX:
        reasons.append(f"timing consistency: waveform lag minus onset lag spreads {timing['spread_samples']} samples "
                       f"over the hits ({dis}) > {TIMING_SPREAD_MAX:g}: a hit's shape moved inside its window against "
                       "its onset (dropped attack, alias)")
    if c is None or not c >= COSINE_TARGET:          # written so that NaN fails
        reasons.append(f"cosine {r7(c)} is not >= {COSINE_TARGET}")
    if e is None or not e <= NRMSE_TARGET:
        reasons.append(f"NRMSE {r7(e)} is not <= {NRMSE_TARGET}")
    spread = round(max(lags) - min(lags), 4)
    out.update({"lag_bound_samples": bound, "lag_bound_ms": LAG_BOUND_MS, "lags_samples": lags,
                "lag_spread_samples": spread, "lag_spread_ms": round(spread * 1000 / rate, 4),
                "gain_b_to_a": r7(g), "cosine": r7(c), "correlation": r7(correlation(av, bv)), "nrmse": r7(e),
                "per_hit": per, "timing_consistency": timing, "noise_floors": floors, "signal_past_window": tails,
                "pass": not reasons,
                "reasons": reasons})
    return out


def refuse(msg):
    """Exit status 2: the arguments or inputs were refused (argparse uses 2 as well)."""
    print(f"capture_align: {msg}", file=sys.stderr)
    raise SystemExit(2)


def parse_pairs(s):
    if not s:
        return None
    try:
        pairs = [tuple(int(x) for x in kv.split("=", 1)) for kv in s.split(",")]
    except ValueError:
        refuse("--pairs must look like 1=1,2=2")
    if any(len(p) != 2 for p in pairs):
        refuse("--pairs must look like 1=1,2=2")
    if len(set(x for x, _ in pairs)) != len(pairs) or len(set(y for _, y in pairs)) != len(pairs):
        refuse(f"--pairs {s}: a channel is paired more than once")
    return pairs


def strip(rep):
    return {k: v for k, v in rep.items() if not k.startswith("_")}


def json_safe(x):
    """Non-finite floats -> None, so a report is strict JSON (no bare NaN/Infinity)."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    return x


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wav")
    p.add_argument("--sidecar")
    p.add_argument("--out")
    p.add_argument("--channels", help="1-based, comma separated (default: all except a timing-reference channel; any "
                                      "other set is a detection deviation that blocks the verdict)")
    p.add_argument("--threshold-db", type=float, default=20.0)
    p.add_argument("--abs-floor-db", type=float, default=-70.0)
    p.add_argument("--tolerance-ms", type=float, default=50.0)
    p.add_argument("--max-lag", type=int, default=DETECTION["max_lag"],
                   help="lag search in samples; another value is a detection deviation that blocks the verdict")
    p.add_argument("--refractory-s", type=float, default=None,
                   help="onset re-arm time; default half the scheduled spacing, 0.5 s without a schedule; a longer "
                        "one is a detection deviation that blocks the verdict")
    p.add_argument("--against")
    p.add_argument("--against-sidecar")
    p.add_argument("--pairs", help="explicit channel pairs for --against: A=B,... e.g. 1=1,2=2")
    p.add_argument("--segments", help="directory: write each hit's active window as a float WAV")
    p.add_argument("--no-calibration", action="store_true",
                   help="analyse a capture raw, without its recorder-chain calibration: a recorded deviation that "
                        "blocks the verdict (renders are never calibrated)")
    a = p.parse_args(argv)
    if a.sidecar is None:
        a.sidecar = find_sidecar(a.wav)
    if a.against and a.against_sidecar is None:
        a.against_sidecar = find_sidecar(a.against)
    try:
        chans = [int(c) for c in a.channels.split(",")] if a.channels else None
    except ValueError:
        refuse("--channels must look like 1,2")
    if a.max_lag < 0:
        refuse("--max-lag must be >= 0")
    kw = dict(threshold_db=a.threshold_db, abs_floor_db=a.abs_floor_db, tolerance_ms=a.tolerance_ms,
              max_lag=a.max_lag, refractory_s=a.refractory_s, no_calibration=a.no_calibration)
    try:
        rep = analyse(a.wav, a.sidecar, channels=chans, **kw)
        if a.against:
            pairs = parse_pairs(a.pairs)
            if not pairs:
                refuse("--against needs explicit --pairs (e.g. 1=1,2=2)")
            other = analyse(a.against, a.against_sidecar, **kw)
            rep["against"] = {"wav": os.path.abspath(a.against), "per_hit_diagnostics": against(rep, other, a.max_lag,
                                                                                                  pairs),
                              "whole_take": whole_take(rep, other, pairs, a.max_lag)}
    except (ValueError, OSError) as e:                # wavio.WavError is a ValueError
        refuse(str(e))
    if a.segments:
        os.makedirs(a.segments, exist_ok=True)
        pre, length = rep["_pre_len"]
        for h in rep["hits"]:
            if h["complete"]:
                o = h["onset_sample"] - pre
                wavio.write(os.path.join(a.segments, f"hit{h['hit']}.wav"), rep["format"]["rate"],
                            [ch[o:o + length] for ch in rep["_sig"]], "float", 32)
    out = a.out or os.path.splitext(a.wav)[0] + ".align.json"
    json.dump(json_safe(strip(rep)), open(out, "w"), indent=1, allow_nan=False)
    print(f"{rep['matched_hits']} hits matched (expected {rep['expected_hits']}), complete={rep['hits_complete']}, "
          f"capture valid={rep['capture']['valid']} {rep['capture']['reasons']}")
    cc = rep["chain_calibration"]
    print("  chain calibration: " + (f"applied, ratios {cc['ratios_db']} dB from {cc.get('path')}" if cc["applied"]
                                      else f"not applied ({cc.get('why')})"))
    print(f"  jitter max {rep['jitter']['max_abs_ms']} ms; floor "
          f"{rep['silence_floor'].get('median_1ms_block_peak_dbfs', 'unavailable')} dBFS (median 1 ms peak)")
    for lv in rep["levels"]:
        print(f"  ch{lv['channel']} peaks raw {lv['peak_raw_abs']} spread {lv['peak_spread_db']} dB")
    if rep["signal_past_window"]:
        print("  NOTE: " + rep["window_note"])
    for pr in rep["pairwise_diagnostics"]:
        print(f"  [diagnostic] hit {pr['a']} vs {pr['b']}: cosine {pr.get('cosine_best')} (lag {pr.get('best_lag')}), "
              f"nrmse {pr.get('nrmse_after_gain')}")
    if "against" in rep:
        w = rep["against"]["whole_take"]
        print(f"  WHOLE-TAKE VERDICT: pass={w['pass']} cosine {w.get('cosine')} nrmse {w.get('nrmse')} "
              f"gain {w.get('gain_b_to_a')} lags {w.get('lags_samples')} samples (spread {w.get('lag_spread_ms')} ms) "
              f"{w['reasons']}")
    print(f"wrote {out}")
    return 1 if "against" in rep and not rep["against"]["whole_take"]["pass"] else 0


if __name__ == "__main__":
    sys.exit(main())
