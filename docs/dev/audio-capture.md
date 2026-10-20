# Audio capture

Tools to record the unit's main outputs against a scripted MIDI stimulus, align the takes and measure them, plus what
we measured with them. Marks as in [README.md](README.md).

## Tools

How we know: our design; tested on synthetic WAV files and with a real recorder on one unit.

| File | Does |
|---|---|
| `tools/capture.py` | `plan` (dry run), `live` (record over USB with `arecord`), `play` + `import` (the recorder writes its own card), `--pilot` (one hit for setting gains), `calibrate` (recorder-chain calibration) |
| `tools/capture_align.py` | offline: chain calibration, onsets, schedule fit, 200 ms windows, tails, levels, whole-take verdict |
| `tools/levelmeter.py` | live L/R meter for setting gains, one hit per period |
| `tools/levelview.py` | the terminal level view shared by the two above (uses the Textual package; everything else is stdlib) |
| `tools/wavio.py` | WAV reader and writer: PCM 16/24/32, float 32/64, WAVE_FORMAT_EXTENSIBLE; rejects truncated files |

`capture.py` sends only note and CC messages, never SysEx, so nothing reads DSP memory while the unit plays. Every take
writes a WAV and a JSON sidecar with exactly what was sent, the settings, hashes and any deviation from the protocol.
Run `python3 -I tools/capture.py --help` for the options.

## Protocol

How we know: our design.

* Takes of 12 s; notes at 2, 4 and 6 s after the capture start, velocity 127, note off 0.5 s later, sequencer stopped.
* Notes go out on channel 16 (the drum-map trigger: notes 36 38 40 41 43 45 47 48 = tracks 1 to 8). The unit plays
  the track at its selected keyboard note, so the track's pitch and keyboard note must be at their defaults for
  unity pitch.
* A reference preset is sent first (playback mode, filter, level, pan, sends, compressor and soft clip set to known
  values); every byte sent is recorded. Grid FX is switched off on the unit by hand.
* Stereo L/R, 48 kHz, 24-bit or 32-bit float, fixed manual gain; Master Volume fully clockwise and unchanged for a
  whole set; aim the recorder's peak at about -12 to -16 dBFS. Any full-scale sample invalidates a take.

## Recorder-chain calibration

How we know: our design; measured on hardware.

The two input gains of a recorder cannot be matched by hand (one recorder's knobs had dead spots below 0.5 dB and one
shifted about 2 dB while plugs were swapped). `calibrate` plays five centred hits, fits a least-squares amplitude ratio
per channel after aligning each channel at its own sub-sample delay, freezes that ratio for the session, and refuses
when the hits disagree by more than 0.1 dB. Recalibrate after any change of gain or cabling. A cable swap (one input
fed from L, then from R) separates the recorder's imbalance from the unit's.

## What we measured

How we know: measured on hardware, one unit, with a 4-channel USB recorder at 48 kHz, on **build 6009** (a devkit test
build). These are development measurements, not acceptance results for arrhythmia-firmware 7229.

* **The right main output leads the left by one sample** (1.00 sample at 48 kHz). In a cable swap the lead followed
  the cable, so it is the unit's, not the recorder's. Aligned, the two channels agree to NRMSE 0.0012.
* **L and R levels agree within 0.05 dB.**
* **Noise floor** -76 dBFS (1 ms peak) on a quiet bench; -50.7 dBFS with hum at multiples of 60 Hz and hiss at 10 to
  24 kHz when the unit leaned against a TV.
* **Pitch CC:** CC 21 = 54 gave -2.00 semitones (5 steps per semitone); 64 gave exactly 0.
* A 750.00 Hz test sample played back at 750.00 Hz; a polarity-inverted copy correlated at -0.99989.

Practical lessons from the sessions:

* Check every track's MIDI channel before a take: a CC 21 probe on the channel, with a hit before and after, shows
  whether that channel reaches the track.
* Check the track's keyboard note: a note other than the root plays the sample transposed, and the pitch CC does not
  reset it.
* Use the main outputs, not the headphone output (different amplifier and volume path). The clock-out jack carries a
  beat pulse, not audio.
* Master Filter is a physical pot: do not touch it after sending the preset.

## Not measured

* Absolute latency from a MIDI message to the analogue output: host timestamps and audio run on different clocks.
  `capture.py` has a hook (`TimingReference`) for a reference channel on the audio clock; none was built.
