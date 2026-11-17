# Arrhythmia: the formant and Rings engines

Applies to Arrhythmia 0.3.2, firmware build 7400, with `formant.dse` 1.0.0 and `rings.dse` 1.0.0. Test status and
known limitations: see the [release notes](../../RELEASE-NOTES-0.3.2.md).

The 0.3.2 release has three engine files for track 8. The FM engine is described in [synth.md](synth.md). This page
describes the other two.

| File | Engine | Sounds | Voices |
|---|---|---|---|
| `fm.dse` 2.1.0 | FM synth ([synth.md](synth.md)) | 32, on two pages of the Sample view | 4 |
| `formant.dse` 1.0.0 | formant: a voice that sings vowels | 16, on page 2 of the Sample view | 4 |
| `rings.dse` 1.0.0 | Rings: struck and plucked resonators | none | 4 |

## Install and select an engine

The engines need firmware 0.3.2, build 7400. The loader of 0.3.2 refuses to install any engine file on firmware below
build 7400. Update the firmware first ([install.md](install.md)).

1. Download `formant.dse` or `rings.dse` from the release page: <https://github.com/llcoolkm/arrhythmia/releases>.
2. Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html) in Chrome and connect.
3. Under **Engines**, select **Scan card**.
4. Add the engine file. This writes `engines/formant.dse` or `engines/rings.dse`.
5. Select a pack row, choose the engine and press **Save choice**.
6. Save project edits on the unit.
7. Load the pack in the stock **Packs** view.

With a card reader, copy the file into the folder `engines` of the card. Write the file name, for example
`rings.dse`, as the one line of the pack's `engine.cfg`, or of `engines/default.cfg`
([Engines on track 8](synth.md#engines-on-track-8)). One pack plays one engine. Use another pack for another engine.

## Controls of every engine

* **Sample view of track 8.** The engine sets its pads. Down and Up change the page when the engine has two pages.
* **Macros 1 to 8 on track 8.** The main controls of the engine. MIDI CC 21 to 28 and parameter locks operate on them,
  as with FM.
* **Shift + Track 8.** Page 1 is the engine page. Page 2 is the scales and page 3 the arpeggiator. Pages 2 and 3 are
  the same for every engine and belong to the project ([The synth page](synth.md#the-synth-page)).
* **Engine page.** Knob 5 is dark. A dark knob has no function. The pads of the engine page are dark: the choices of
  these engines are in the Sample view.
* **Clear + knob** resets a knob to its default where it has one.
* Chords, notes per press and poly are FM controls. These engines play one voice per note, up to 4 voices.
* The project saves the values.

## Formant

A voice that sings vowels. Each note plays a buzz or breath source through the vowel formants of a male, female or
child voice. The vowel can move during the note.

### Sample view

| Page | Row 3 | Row 4 |
|---|---|---|
| 1 | voice: sound, male, female, child | vowel: sound, a, e, i, o, u |
| 2 | sounds 1 to 8 | sounds 9 to 16 |

* **Sound** on page 1 follows the voice or the vowel of the selected sound. Any other pad sets the voice or the
  vowel for every sound.
* A voice or vowel pad plays no note.
* A sound pad on page 2 selects the sound and plays it.

| Sound | Name | Sound | Name |
|---|---|---|---|
| 1 | Choir Ah | 9 | Whisper |
| 2 | Choir Oo | 10 | Robot |
| 3 | Monk Om | 11 | Throat |
| 4 | Tenor Ee | 12 | Wah Pad |
| 5 | Soprano Oh | 13 | Kids Choir |
| 6 | Vowel Lead | 14 | Ghost |
| 7 | Talk Bass | 15 | Vowel Pluck |
| 8 | Yoi Sweep | 16 | Giant |

### Macros on track 8

| Macro | Control |
|---|---|
| 1 | morph: moves the vowel through a, e, i, o, u |
| 2 | attack |
| 3 | decay |
| 4 | sustain |
| 5 | release |
| 6 | formant shift: up to 12 semitones down or up |
| 7 | bandwidth: narrow to wide formants |
| 8 | breath: noise in the source |

* At 64, every macro plays the sound as designed. The default of each macro is 64.
* Attack, decay and release move the time from the sound's own value.

### Engine page

| Knob | Control |
|---|---|
| 1 | vibrato rate |
| 2 | vibrato depth |
| 3 | vibrato delay |
| 4 | drift: slow random pitch movement |
| 5 | none; dark |
| 6 | buzz: the source from smooth to buzzy |
| 7 | morph amount: how far the vowel moves during the note, in both directions |
| 8 | morph decay: how fast the vowel moves |

## Rings

Resonators that you strike or pluck. The models are ports of the resonators of Mutable Instruments Rings by Emilie
Gillet, MIT licence ([notice](#notice-for-the-rings-port)). Rings has no sounds. Set the model, the exciter and the macros.

### Sample view

Rings has one page. It has no page 2.

| Row | Pads |
|---|---|
| 3 | model: modal, sympathetic, string |
| 4 | exciter: auto, strike, pluck |

* **Modal:** a bank of resonators, from a harmonic tone to a bell.
* **Sympathetic:** the played string and a second string that rings along with it.
* **String:** one string.
* **Auto** strikes the modal model and plucks the string models.
* A model or exciter pad plays no note. A new model applies from the next note. Sounding notes ring on.
* The other pads of the page are dark.

### Macros on track 8

| Macro | Control | Default |
|---|---|---|
| 1 | structure: the stretch of the partials (modal), the interval of the second string (sympathetic), the stiffness (string) | 64 |
| 2 | brightness | 64 |
| 3 | damping: from a short thud to a long ring | 64 |
| 4 | position: where the resonator is struck | 64 |
| 5 | release: the decay after the note ends | 64 |
| 6 | velocity: how much the velocity changes level and brightness | 64 |
| 7 | tone: the brightness of the exciter | 64 |
| 8 | drone: the pitch of the second string of the sympathetic model; no effect in the other models | 36 |

### Engine page

| Knob | Control |
|---|---|
| 1 to 5 | none; dark |
| 6 | vibrato depth |
| 7 | vibrato rate |
| 8 | drive: from clean to saturated |

## Levels

Unit measurements of the engines are in the [release notes](../../RELEASE-NOTES-0.3.2.md#engine-files). Set the balance
with the mixer level of track 8.

## Notice for the Rings port

`rings.dse` contains a port of algorithms, tables and coefficients of the Rings module firmware by Mutable
Instruments (repository `pichenettes/eurorack`, directory
`rings/`). The port is DSP56300 fixed-point code. It copies no C++ source. The DSP source of the port is not published.
The Rings engine itself is MIT licensed, copyright 2026 km.

The ported parts, by upstream file:

* `rings/dsp/resonator.cc`: the modal resonator bank (partial stretch from the stiffness table, Q from
  500 x 10^(4 x damping), the brightness loss, the position amplitudes of the cosine oscillator).
* `rings/dsp/string.cc`: the string with its damping FIR and IIR, delay compensation, dispersion all-pass, curved
  bridge, dispersion noise and pickup comb.
* `rings/dsp/part.cc`: the model gains, the sympathetic string notes, the per-string LFO rates, and the internal
  strike exciter with its low-pass.
* `rings/dsp/plucker.h`: the plucker.
* `rings/dsp/limiter.h`: the output limiter.
* `rings/resources/lookup_tables.py`: the stiffness table and the SVF shift relation.

It also ports approximations of `stmlib` (repository `pichenettes/stmlib`): `dsp/filter.h` (the fast and dirty filter tangents, stored as ratio
tables, and the state-variable filter), `dsp/delay_line.h` (Hermite interpolation) and `dsp/dsp.h` (the soft limiter).
`stmlib` has the same MIT text under "Copyright 2012 Emilie Gillet." in its LICENSE file; the ported headers carry the
years 2012 to 2014.

The polynomial fits (sine, cosine, 2^x) and the per-block structure are the port's own. No third-party source file is
included.

The licence of Rings:

    Copyright 2015 Emilie Gillet.

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
    THE SOFTWARE.
