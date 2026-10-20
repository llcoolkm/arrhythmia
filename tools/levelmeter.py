"""Live L/R level meter for dialling in recorder gains.

Records continuously from the interface and sends one hit per period on the drum channel (channel 16, the
track's note), then shows each analysed channel's peak and the amplitude ratio of each channel to the first,
fitted by least squares over the hit window after aligning the channel at its own inter-channel delay (as the
calibration does). Only note-on/off is sent: no SysEx, so no DSP read.

On a terminal it draws the level view (tools/levelview.py: bars with the target peak, L/R difference gauge, ratio
history, waveform overlay); q quits. --plain, or stdout that is not a terminal, prints one line per hit instead
(Ctrl-C stops). Either way the pending note-off is sent and arecord is terminated on exit.

    uv run python -I tools/levelmeter.py --midi-device /dev/snd/midiC4D0 --alsa-device hw:CARD=H5studio,DEV=0 \\
        --channels 4 --analyse 3,4
"""
import argparse
import os
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import levelview  # noqa: E402

TRACK_NOTES = (36, 38, 40, 41, 43, 45, 47, 48)          # as tools/capture.py: channel 16, tracks 1..8
DRUM_CHANNEL = 16
RATE, WIDTH = 48000, 4                                   # S32_LE
PRE_S = 0.1                                              # quiet audio kept before each hit (the floor region)


def db(v):
    return float("-inf") if v is None else v


def line(chans, m):
    """Today's line format: peaks, then each channel's ratio to the first."""
    parts = [f"ch{c} {db(pk):7.2f} dBFS" for c, pk in zip(chans, m["peak_dbfs"])]
    for c, r, dl in zip(chans[1:], m["ratio_db"][1:], m["delay_samples"][1:]):
        parts.append(f"ch{c}-ch{chans[0]} {db(r):+6.2f} dB  delay " + ("  n/a" if dl is None else f"{dl:+5.2f}") + " smp")
    return "   ".join(parts)


def run_meter(a, chans, on_hit, stop):
    """Headless meter loop: arecord, one note per period, measure each hit (levelview.measure) and on_hit(m); ends
    when stop is set or after a.hits hits. Always sends the pending note-off and terminates arecord."""
    frame = a.channels * WIDTH
    note = TRACK_NOTES[a.track - 1]
    argv = a.recorder_cmd.split() if a.recorder_cmd else [
        "arecord", "-q", "-D", a.alsa_device, "-f", "S32_LE", "-r", str(RATE), "-c", str(a.channels), "-t", "raw"]
    rec = subprocess.Popen(argv, stdout=subprocess.PIPE)
    buf = bytearray()
    lock = threading.Lock()

    def pump():
        while True:
            chunk = rec.stdout.read1(frame * 480)
            if not chunk:
                return
            with lock:
                buf.extend(chunk)

    threading.Thread(target=pump, daemon=True).start()
    midi = None
    on = False
    n = 0
    try:
        midi = os.open(a.midi_device, os.O_WRONLY)
        stop.wait(0.5)
        while not stop.is_set() and (a.hits is None or n < a.hits):
            with lock:
                keep = len(buf) // frame * frame
                pre = bytes(buf[max(0, keep - int(PRE_S * RATE) * frame):keep])
                buf.clear()
            os.write(midi, bytes([0x90 | (DRUM_CHANNEL - 1), note, a.velocity]))
            on = True
            stop.wait(a.window + 0.05)
            os.write(midi, bytes([0x80 | (DRUM_CHANNEL - 1), note, 0]))
            on = False
            with lock:
                raw = pre + bytes(buf[:len(buf) // frame * frame])
            got = len(raw) // frame
            if got < int((PRE_S + 0.05) * RATE):
                on_hit(None)
            else:
                vals = struct.unpack(f"<{got * a.channels}i", raw[:got * frame])
                sig = [[vals[i * a.channels + c - 1] / 2147483648.0 for i in range(got)] for c in chans]
                on_hit(levelview.measure(sig, RATE, a.window))
            n += 1
            stop.wait(max(0.0, a.period - a.window - 0.05))
    finally:
        if midi is not None:
            if on:
                os.write(midi, bytes([0x80 | (DRUM_CHANNEL - 1), note, 0]))
            os.close(midi)
        rec.terminate()
        rec.wait()


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--midi-device", default="/dev/snd/midiC4D0")
    p.add_argument("--alsa-device", default="hw:CARD=H5studio,DEV=0")
    p.add_argument("--channels", type=int, default=4)
    p.add_argument("--analyse", default="3,4", help="1-based channels to meter, first is the reference")
    p.add_argument("--labels", default="3=Rhythm L,4=Rhythm R", help="channel labels for the level view")
    p.add_argument("--track", type=int, choices=range(1, 9), default=1)
    p.add_argument("--velocity", type=int, default=127)
    p.add_argument("--period", type=float, default=1.0, help="seconds between hits (>= 0.5)")
    p.add_argument("--window", type=float, default=0.15, help="seconds analysed after each hit")
    p.add_argument("--target-peak", type=float, default=-16.0, help="target peak, dBFS (information, not a gate)")
    p.add_argument("--target-tolerance", type=float, default=1.0, help="tolerance band around the target, dB")
    p.add_argument("--plain", action="store_true", help="line output instead of the level view")
    p.add_argument("--hits", type=int, default=None, help="stop after this many hits (default: until q / Ctrl-C)")
    p.add_argument("--recorder-cmd", default=None, help=argparse.SUPPRESS)   # tests: replaces arecord
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        chans = [int(c) for c in a.analyse.split(",")]
        labels = dict(kv.split("=", 1) for kv in a.labels.split(",")) if a.labels else {}
    except ValueError:
        sys.exit("bad --analyse/--labels")
    if a.period < 0.5 or not 0.02 <= a.window <= a.period - 0.2 or not chans:
        sys.exit("bad --period/--window/--analyse")
    if any(not 1 <= c <= a.channels for c in chans):
        sys.exit("--analyse names a channel outside --channels")
    if a.target_tolerance <= 0:
        sys.exit("--target-tolerance must be > 0")
    stop = threading.Event()
    if a.plain or not (sys.stdout.isatty() and sys.stdin.isatty()):
        print(f"metering channels {a.analyse} (first = reference), hit every {a.period} s; Ctrl-C to stop", flush=True)
        try:
            run_meter(a, chans, lambda m: print(line(chans, m) if m else "no audio received", flush=True), stop)
        except KeyboardInterrupt:
            pass
        return
    app, _ = meter_app(a, chans, labels, stop)
    app.run()


def meter_app(a, chans, labels, stop):
    """(LevelApp, ViewState) for the meter: the shared level view, no gates."""
    st = levelview.ViewState([(c, labels.get(str(c), "")) for c in chans], a.target_peak, a.target_tolerance,
                             title="levelmeter")
    st.status = (f"ALSA {a.alsa_device} | MIDI {a.midi_device} | S32_LE {RATE} Hz {a.channels} ch | hit every "
                 f"{a.period:g} s | q quits")

    def work(post):
        def hit(m):
            if m is None:
                st.message = "no audio received"
            else:
                st.message = ""
                st.add_hit(m)
            st.status = st.status.split(" | hits")[0] + f" | hits {len(st.hits)}"
            post()
        t0 = time.monotonic()
        run_meter(a, chans, hit, stop)
        st.message = f"stopped after {time.monotonic() - t0:.0f} s: note-off sent, recorder stopped"
        return len(st.hits)
    return levelview.make_app(st, work, stop), st


if __name__ == "__main__":
    main()
