"""Level and calibration view shared by tools/levelmeter.py and `capture.py calibrate`.

The UI is a layer only. Measurement and gates are plain functions (measure(), target_offset(), capture_align's
calibration_stats); the renderers turn their results into Rich text, and LevelApp (Textual) only lays them out and
refreshes. Callers fall back to line output with --plain or when stdout is not a terminal.

Widgets: one bar per analysed channel (peak, RMS, peak hold, noise floor, clip warning at CLIP_DBFS, the target peak
as a marker with its tolerance band shaded and the offset from it); a centre-zero L/R difference gauge with the
+-DIFF_ZONE_DB zone; the ratio history with its spread; the last hit's channels overlaid for the first WAVE_MS after
the onset (braille); for calibrate the hit progress, per-hit ratios, every gate with its threshold and the file path;
a status line. q quits cleanly: the caller's stop event (capture.CANCEL) makes the worker send any pending note-off
and terminate arecord, then the app exits.
"""
import math
import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_align as ca  # noqa: E402
import wavio  # noqa: E402

CLIP_DBFS = -1.0           # clip warning on a bar (a full-scale sample itself invalidates a take: capture_align)
DIFF_ZONE_DB = 0.2         # L/R difference gauge: target zone +-0.2 dB (master)
DIFF_SPAN_DB = 1.0         # gauge half-width
AMBER_DB = 3.0             # target offset colour: green inside the tolerance, amber within 3 dB, red beyond
BAR_MIN_DBFS = -48.0       # bar scale: BAR_MIN_DBFS .. 0 dBFS
BAR_WIDTH = 48
WAVE_MS = 20.0
WAVE_WIDTH, WAVE_ROWS = 64, 4
HISTORY = 20               # hits kept in the ratio history
HOLD_HITS = 5              # peak hold over the last 5 hits
PRE_S, POST_S = 0.15, 0.45  # live slice around a scheduled hit: floor before it, window and lag margin after


# ---------------------------------------------------------------- measurement (headless)

def dbfs(x):
    return 20 * math.log10(x) if x and x > 0 else None


def decode(raw, sample_type, bits, nch):
    """Interleaved frames -> per-channel floats at full scale 1.0 (as wavio.read)."""
    vals = wavio.decode(raw, sample_type, bits)
    scale = 1.0 if sample_type == "float" else float(1 << (bits - 1))
    return [[v / scale for v in vals[c::nch]] for c in range(nch)]


def measure(chans, rate, window_s=ca.WINDOW_S):
    """One hit in a slice of the analysed channels (first = reference): a quiet region, then the hit. Onset: the first
    sample over max(floor + 20 dB, -70 dBFS) on the largest |x| (capture_align's detection settings); per channel the
    peak and RMS over the window from 2 ms before it, the floor (median 1 ms block peak before the onset, DC removed),
    and for each other channel the least-squares ratio and delay against the reference after aligning it
    (capture_align.fit_hit_lag, as calibration_stats). None where not measurable."""
    n = min(len(c) for c in chans)
    block = max(1, rate // 1000)
    mono = [max(v, key=abs) for v in zip(*chans)] if len(chans) > 1 else chans[0]
    det_floor = ca.median(ca.envelope_blocks(mono, block)) or 0.0
    thr = max(det_floor * 10 ** (ca.DETECTION["threshold_db"] / 20), 10 ** (ca.DETECTION["abs_floor_db"] / 20))
    onset = next((i for i, v in enumerate(mono) if abs(v) > thr), None)
    out = {"onset": onset, "peak_dbfs": [], "rms_dbfs": [], "floor_dbfs": [], "ratio_db": [], "ratio_sign": [],
           "delay_samples": [], "cosine": [], "wave": []}
    pre = int(round(ca.PRE_MS * rate / 1000))
    bound = int(round(ca.LAG_BOUND_MS * rate / 1000))
    if onset is None:
        for c in chans:
            out["peak_dbfs"].append(dbfs(max(map(abs, c[:n]), default=0.0)))
            for k in ("rms_dbfs", "floor_dbfs", "ratio_db", "ratio_sign", "delay_samples", "cosine"):
                out[k].append(None)
            out["wave"].append([])
        return out
    s = max(0, onset - pre)
    length = min(int(round(window_s * rate)), n - s - bound - ca.SINC_HALF - 1)
    fl_end = max(0, onset - 5 * block)
    wn = int(round(WAVE_MS * rate / 1000))
    for c in chans:
        seg = c[s:s + max(length, 1)]
        out["peak_dbfs"].append(dbfs(max(map(abs, seg), default=0.0)))
        out["rms_dbfs"].append(dbfs(math.sqrt(ca.dot(seg, seg) / len(seg))) if seg else None)
        if fl_end >= block:
            m = math.fsum(c[:fl_end]) / fl_end
            out["floor_dbfs"].append(dbfs(ca.median(ca.envelope_blocks([v - m for v in c[:fl_end]], block))))
        else:
            out["floor_dbfs"].append(None)
        out["wave"].append(c[onset:onset + wn])
    ref = chans[0]
    out["ratio_db"].append(0.0)
    out["ratio_sign"].append(1)
    out["delay_samples"].append(0.0)
    out["cosine"].append(1.0)
    for c in chans[1:]:
        f = ca.fit_hit_lag([ref], [c], s, s, length, bound) if length > 0 and s >= bound + ca.SINC_HALF else None
        lag, frac = (f[0], 0.0 if abs(f[1]) < ca.SNAP_FRAC else f[1]) if f else (0, 0.0)
        x = ca.frac_window([c], s + lag, length, frac) if f else (c[s:s + length] if length > 0 else None)
        r = ca.window([ref], s, length) if length > 0 else None
        g = ca.gain_nrmse(x, r)[0] if x and r else None
        out["ratio_db"].append(round(20 * math.log10(abs(g)), 4) if g else None)
        out["ratio_sign"].append((g > 0) - (g < 0) if g else None)
        out["delay_samples"].append(round(lag + frac, 3) if f else None)
        out["cosine"].append(ca.r7(ca.cosine(x, r)) if x and r else None)
    return out


def target_offset(peak_db, target, tol):
    """Offset of a peak from the target: text ("+0.4 dB, turn down") and colour (green inside +-tol, amber within
    AMBER_DB, red beyond). Information only, never a gate."""
    if peak_db is None:
        return {"offset_db": None, "colour": "red", "text": "no signal"}
    off = peak_db - target
    colour = "green" if abs(off) <= tol else ("yellow" if abs(off) <= AMBER_DB else "red")
    word = "on target" if abs(off) < 0.05 else ("turn down" if off > 0 else "turn up")
    return {"offset_db": round(off, 2), "colour": colour, "text": f"{off:+.1f} dB, {word}"}


class LiveHits:
    """Collects a recording's frames as they arrive (Recorder.on_chunk) and measures each scheduled hit once the slice
    PRE_S before to POST_S after its planned time has arrived. hit_frames: planned frames from sample 0. feed() only
    queues the bytes: decoding and measuring run on this object's own thread, so the recorder's pump never waits
    (a stalled pump would overrun arecord). finish() waits until everything queued is processed."""

    def __init__(self, fmt, nch, analysed, rate, hit_frames, on_hit):
        self.sample_type, self.bits = fmt
        self.nch, self.analysed, self.rate = nch, analysed, rate
        self.block = nch * self.bits // 8
        self.frames, self.data = 0, [[] for _ in analysed]
        self.todo, self.on_hit, self.rest = list(hit_frames), on_hit, b""
        self.q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def feed(self, raw):
        self.q.put(raw)

    def finish(self, timeout=30.0):
        done = threading.Event()
        self.q.put(done)
        done.wait(timeout)

    def _loop(self):
        while True:
            item = self.q.get()
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                self._process(item)
            except Exception:                         # noqa: BLE001 - a preview must never stop the recording
                pass

    def _process(self, raw):
        raw = self.rest + raw
        n = len(raw) // self.block
        self.rest = raw[n * self.block:]
        chans = decode(raw[:n * self.block], self.sample_type, self.bits, self.nch)
        for d, c in zip(self.data, self.analysed):
            d.extend(chans[c - 1])
        self.frames += n
        while self.todo and self.frames >= self.todo[0] + int(POST_S * self.rate):
            f = self.todo.pop(0)
            a, b = max(0, f - int(PRE_S * self.rate)), f + int(POST_S * self.rate)
            self.on_hit(measure([d[a:b] for d in self.data], self.rate))


# ---------------------------------------------------------------- view state and renderers (headless)

class ViewState:
    def __init__(self, channels, target, tol, total_hits=None, title=""):
        self.channels = channels                      # [(file channel, label)], first = reference
        self.target, self.tol, self.total, self.title = target, tol, total_hits, title
        self.hits, self.status, self.gates, self.result, self.message = [], "", None, None, ""

    def add_hit(self, m):
        self.hits.append(m)

    def hold(self, i):
        vals = [h["peak_dbfs"][i] for h in self.hits[-HOLD_HITS:] if h["peak_dbfs"][i] is not None]
        return max(vals) if vals else None

    def ratios(self):
        return [h["ratio_db"][1] for h in self.hits[-HISTORY:] if len(h["ratio_db"]) > 1 and h["ratio_db"][1]
                is not None]


def hit_line(st, n, m):
    """One scrolling log line per hit: hit number, each channel's peak, then each other channel's ratio and delay
    against the reference (levelmeter's --plain line, plus the delay)."""
    f = lambda v, fmt: "   n/a" if v is None else format(v, fmt)
    ref = st.channels[0][0]
    parts = [f"#{n:<4d}"] + [f"ch{c} {f(pk, '7.2f')} dBFS" for (c, _), pk in zip(st.channels, m["peak_dbfs"])]
    for k, (c, _) in enumerate(st.channels[1:], 1):
        parts.append(f"ch{c}-ch{ref} {f(m['ratio_db'][k], '+6.2f')} dB  delay {f(m['delay_samples'][k], '+5.2f')} smp")
    return "   ".join(parts)


def _pos(v, width=BAR_WIDTH, lo=BAR_MIN_DBFS, hi=0.0):
    return max(0, min(width - 1, round((v - lo) / (hi - lo) * (width - 1))))


def bar_cells(peak, rms, hold, floor, target, tol, width=BAR_WIDTH):
    """[(char, style)] for one level bar: RMS solid, peak shaded, tolerance band shaded behind, peak hold '▌', floor
    '·', red past CLIP_DBFS, and the target marker '│' on top."""
    cells = [[" ", ""] for _ in range(width)]
    for i in range(_pos(target - tol, width), _pos(target + tol, width) + 1):
        cells[i][1] = "on grey27"
    for i in range(_pos(CLIP_DBFS, width), width):
        cells[i][1] = "on dark_red"
    clip = peak is not None and peak >= CLIP_DBFS
    if peak is not None and peak > BAR_MIN_DBFS:
        r = _pos(rms, width) if rms is not None and rms > BAR_MIN_DBFS else -1
        for i in range(0, _pos(peak, width) + 1):
            cells[i] = ["█" if i <= r else "▒", ("red" if clip else "green") + (" " + cells[i][1] if cells[i][1]
                                                                                  else "")]
    def mark(i, ch, fg):                              # a marker keeps the cell's background (band, clip zone)
        bg = cells[i][1][cells[i][1].index("on "):] if "on " in cells[i][1] else ""
        cells[i] = [ch, f"{fg} {bg}".strip()]
    if floor is not None and floor > BAR_MIN_DBFS:
        mark(_pos(floor, width), "·", "bold white")
    if hold is not None and hold > BAR_MIN_DBFS:
        mark(_pos(hold, width), "▌", "bold white")
    mark(_pos(target, width), "│", "bold yellow")     # drawn last: the target is never hidden
    return [tuple(c) for c in cells]


def bar_text(label, m, i, st, width=BAR_WIDTH):
    """Two lines: label, figures and target offset; then the bar alone across the whole width."""
    from rich.text import Text
    pk, rms, fl = (m["peak_dbfs"][i], m["rms_dbfs"][i], m["floor_dbfs"][i]) if m else (None, None, None)
    off = target_offset(pk, st.target, st.tol)
    f = (lambda v: "  --  " if v is None else f"{v:6.1f}")
    t = Text(f"{label:<14}")
    t.append(f"pk {f(pk)} rms {f(rms)} hold {f(st.hold(i))} floor {f(fl)} dBFS  ")
    t.append(off["text"], style=f"bold {off['colour']}")
    if pk is not None and pk >= CLIP_DBFS:
        t.append("  CLIP", style="bold white on red")
    t.append("\n")
    for ch, style in bar_cells(pk, rms, st.hold(i), fl, st.target, st.tol, width):
        t.append(ch, style=style or None)
    return t


def scale_text(width=BAR_WIDTH, step=6):
    """dB ruler under the bars: a tick and its value every `step` dB from BAR_MIN_DBFS to 0, aligned with _pos."""
    ticks, labels = [" "] * width, [" "] * width
    for v in range(int(BAR_MIN_DBFS), 1, step):
        p = _pos(v, width)
        ticks[p] = "┴"
        lab = str(v)
        start = max(0, min(width - len(lab), p - len(lab) // 2))
        if all(c == " " for c in labels[max(0, start - 1):start + len(lab) + 1]):
            labels[start:start + len(lab)] = lab
    return "".join(ticks) + "\n" + "".join(labels)


def gauge_cells(diff, width=41, span=DIFF_SPAN_DB, zone=DIFF_ZONE_DB):
    """Centre-zero gauge of diff (dB): '┼' at 0, zone shaded, needle '●' green inside the zone, red outside, an arrow
    at the end when beyond the span."""
    cells = [["─", ""] for _ in range(width)]
    p = (lambda v: max(0, min(width - 1, round((v + span) / (2 * span) * (width - 1)))))
    for i in range(p(-zone), p(zone) + 1):
        cells[i][1] = "on grey27"
    cells[p(0)][0] = "┼"
    if diff is not None:
        ok = abs(diff) <= zone
        ch = "●" if abs(diff) <= span else ("◀" if diff < 0 else "▶")
        cells[p(diff)] = [ch, ("bold green" if ok else "bold red") + (" on grey27" if ok else "")]
    return [tuple(c) for c in cells]


def gauge_text(st):
    from rich.text import Text
    m = st.hits[-1] if st.hits else None
    diff = m["ratio_db"][1] if m and len(m["ratio_db"]) > 1 else None
    t = Text(f"{'R - L':<14}{-DIFF_SPAN_DB:+.1f} ")
    for ch, style in gauge_cells(diff):
        t.append(ch, style=style or None)
    t.append(f" {DIFF_SPAN_DB:+.1f} dB   ")
    if diff is None:
        t.append("--")
    else:
        ok = abs(diff) <= DIFF_ZONE_DB
        t.append(f"{diff:+.3f} dB", style="bold green" if ok else "bold red")
        if m["ratio_sign"][1] == -1:
            t.append("  INVERTED", style="bold white on red")
        if m["delay_samples"][1]:
            t.append(f"  delay {m['delay_samples'][1]:+.2f} smp")
    t.append(f"  (zone +-{DIFF_ZONE_DB:g} dB)")
    return t


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(vals):
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7 + 0.5))] for v in vals)


def history_text(st):
    from rich.text import Text
    v = st.ratios()
    t = Text(f"{'ratio history':<14}")
    t.append(sparkline(v), style="cyan")
    if v:
        t.append(f"  last {v[-1]:+.3f} dB  spread {max(v) - min(v):.3f} dB over {len(v)} hits  "
                 f"(calibration bound {ca.CAL_SPREAD_MAX_DB:g} dB)")
    return t


BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def braille(traces, width=WAVE_WIDTH, rows=WAVE_ROWS):
    """Overlay of traces (lists of samples) as rows of (char, owners) cells: braille dots, 2 x 4 per cell; each trace is
    scaled by the largest |x| of all, centre = 0, and its columns joined vertically. owners: set of trace indices."""
    W, H = 2 * width, 4 * rows
    peak = max((abs(v) for t in traces for v in t), default=0.0) or 1.0
    bits = [[0] * width for _ in range(rows)]
    own = [[set() for _ in range(width)] for _ in range(rows)]
    for k, tr in enumerate(traces):
        if not tr:
            continue
        prev = None
        for x in range(W):
            v = tr[min(len(tr) - 1, x * len(tr) // W)]
            y = max(0, min(H - 1, round((1 - v / peak) / 2 * (H - 1))))
            for yy in range(min(y, prev if prev is not None else y), max(y, prev if prev is not None else y) + 1):
                bits[yy // 4][x // 2] |= BRAILLE[yy % 4][x % 2]
                own[yy // 4][x // 2].add(k)
            prev = y
    return [[(chr(0x2800 + bits[r][c]), own[r][c]) for c in range(width)] for r in range(rows)]


def wave_text(st):
    from rich.text import Text
    m = st.hits[-1] if st.hits else None
    labels = [lab for _, lab in st.channels]
    t = Text(f"last hit, first {WAVE_MS:g} ms after onset: ")
    for k, lab in enumerate(labels[:2]):
        t.append(lab, style=("cyan", "magenta")[k])
        t.append("  ")
    t.append("both", style="white")
    t.append("\n")
    if not m or not any(m["wave"]):
        return t.append("(no hit yet)")
    for row in braille(m["wave"][:2]):
        for ch, owners in row:
            t.append(ch, style="white" if len(owners) > 1 else ("cyan" if owners == {0} else "magenta"))
        t.append("\n")
    return t


def gates_text(st):
    from rich.text import Text
    t = Text()
    if st.total:
        done = len(st.hits)
        t.append(f"pilot hits {done}/{st.total}  ", style="bold")
        t.append("".join("●" if i < done else "○" for i in range(st.total)) + "  per-hit R - L: ")
        t.append(" ".join(f"{h['ratio_db'][1]:+.3f}" if len(h["ratio_db"]) > 1 and h["ratio_db"][1] is not None
                          else "--" for h in st.hits) or "--")
        t.append(" dB (live preview)\n")
    if st.result is not None:
        res = st.result.get("result") or {}
        for c, v in (res.get("channels") or {}).items():
            t.append(f"frozen ratio ch{c}/ch{res.get('reference_channel')}: {v['mean_db']:+.4f} dB, per hit "
                     f"{' '.join(f'{x:+.4f}' for x in v['per_hit_db'])}, std {v['std_db']:.4f} dB, delay "
                     f"{v['delay_samples']} smp\n")
        tg = st.result.get("target") or {}
        if tg:
            t.append(f"target peak {tg.get('peak_dbfs')} dBFS +-{tg.get('tolerance_db')} dB: information, not a gate\n",
                     style="dim")
    for g in st.gates or []:
        style = {"PASS": "bold green", "FAIL": "bold red"}.get(g["status"], "bold yellow")
        t.append(f"{g['status']:<5}", style=style)
        t.append(f" {g['name']:<14} {g['value']}  ({g['threshold']})\n")
    if st.result is not None:
        ok = st.result.get("accepted")
        t.append("ACCEPTED" if ok else f"REFUSED ({st.result.get('status')})",
                 style="bold white on green" if ok else "bold white on red")
        t.append(f"  {st.message}\n")
    elif st.message:
        t.append(st.message + "\n")
    return t


# ---------------------------------------------------------------- Textual app (the UI layer)

def make_app(st, work, stop):
    """LevelApp around a ViewState. work(post) runs in a worker thread and calls post() after changing st; stop: the
    threading.Event the work honours (q sets it, then the app exits when the work has cleaned up)."""
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import Footer, RichLog, Static

    class LevelApp(App):
        TITLE = st.title
        BINDINGS = [("q", "quit_clean", "Quit (note-off, stop recorder)")]
        CSS = ("Static { margin: 0 1; } #status { background: $boost; } #panels { height: auto; max-height: 75%; } "
               "#log { height: 1fr; min-height: 4; margin: 0 1; border-top: solid $primary; }")

        def __init__(self):
            super().__init__()
            self.state, self.done, self.outcome, self.quitting = st, threading.Event(), None, False
            self.logged, self.bar_width = 0, BAR_WIDTH

        def compose(self) -> ComposeResult:
            yield Static(id="status")
            with VerticalScroll(id="panels"):
                for k, _ in enumerate(st.channels):
                    yield Static(id=f"bar{k}")
                yield Static(id="scale")
                yield Static(id="gauge")
                yield Static(id="history")
                yield Static(id="wave")
                yield Static(id="gates")
            yield RichLog(id="log", max_lines=2000)
            yield Footer()

        def on_mount(self):
            self.redraw()
            self.run_worker(self._run, thread=True, exclusive=True)

        def _run(self):
            try:
                self.outcome = work(lambda: self.call_from_thread(self.redraw))
            finally:
                self.done.set()
                self.call_from_thread(self._finished)

        def _finished(self):
            self.redraw()
            if self.quitting:
                self.exit(self.outcome)

        def redraw(self):
            from rich.text import Text
            m = st.hits[-1] if st.hits else None
            self.query_one("#status", Static).update(Text(st.status))
            self.bar_width = w = max(BAR_WIDTH, self.size.width - 4)    # margins and a scrollbar
            for k, (c, lab) in enumerate(st.channels):
                self.query_one(f"#bar{k}", Static).update(bar_text(f"ch{c} {lab}", m, k, st, w))
            self.query_one("#scale", Static).update(Text(scale_text(w), style="dim"))
            self.query_one("#gauge", Static).update(gauge_text(st))
            self.query_one("#history", Static).update(history_text(st))
            self.query_one("#wave", Static).update(wave_text(st))
            self.query_one("#gates", Static).update(gates_text(st))
            log = self.query_one("#log", RichLog)
            while self.logged < len(st.hits):
                self.logged += 1
                log.write(hit_line(st, self.logged, st.hits[self.logged - 1]))

        def on_resize(self, _event):
            self.redraw()

        def action_quit_clean(self):
            self.quitting = True
            if self.done.is_set():
                self.exit(self.outcome)
            else:
                st.message = "quitting: note-off, stopping the recorder ..."
                stop.set()
                self.redraw()

    return LevelApp()


def calibrate_view(a):
    """(ViewState, work) for capture.py calibrate; work runs capture.run_calibration with a LiveHits preview."""
    import capture
    analysed = [int(k) for k in sorted(a.channel_map, key=int) if a.channel_map[k].strip().lower() != ca.UNUSED_LABEL]
    if a.ref_channel in analysed:
        analysed.remove(a.ref_channel)
    st = ViewState([(c, a.channel_map[str(c)]) for c in analysed], a.target_peak, a.target_tolerance, a.hits,
                   "capture.py calibrate")
    st.status = (f"ALSA {a.alsa_device} | MIDI {a.midi_device} | {a.format} {a.rate} Hz {a.channels} ch | "
                 f"{a.hits} hits, {a.spacing:g} s apart | q quits")

    def work(post):
        planned = [round(e["planned_s"] * a.rate) for e in capture.build_schedule(a) if e["kind"] == "note_on"]
        live = LiveHits(capture.ALSA_FORMATS[a.format], a.channels, analysed, a.rate, planned,
                        lambda m: (st.add_hit(m), post()))
        st.message = "recording ..."
        post()
        cal, wav, side, exc = capture.run_calibration(a, live.feed)
        live.finish()
        st.result = cal
        st.gates = (cal.get("result") or {}).get("gates")
        st.message = (f"calibration file: {side}" if exc is None else f"{cal['status']}: kept in {side}")
        post()
        return cal, wav, side, exc
    return st, work


def run_calibrate_tui(a):
    """capture.py calibrate in the level view. Returns run_calibration's (cal, wav, side, exc)."""
    import capture
    capture.CANCEL.clear()
    st, work = calibrate_view(a)
    app = make_app(st, work, capture.CANCEL)
    out = app.run()
    if out is None:                               # closed before the worker returned (should not happen)
        raise SystemExit("level view closed without a result")
    return out
