/* Track-8 voice command encoder. Wire format: the Arrhythmia fixed-saw voice decoder on DSP core 1 (decoder r4), the
 * format frozen for v0.1. THE ONLY FILE THAT KNOWS THE BIT LAYOUT AND THE PITCH CONVERSION; the allocator and the
 * admission code use the vc_* helpers only. Included by notetap.c; no state, no firmware calls.
 *
 * Words are u24 (the ring writer sends bits 23..0). Class A: word0 bits 23..20 = 0xA.
 *   ONSET   one voice per entry. word0 = (0xA4 + voice) << 16 | M >> 8
 *                                word1 = (M & 255) << 16 | e << 11 | vel << 4 | gen
 *           M = normalised 24-bit mantissa (bit 23 set), e = 0..23, increment = M << e (48 bits), vel 1..127,
 *           gen = (DSP stored generation + 1) & 15: written at PUBLICATION from the ARM mirror (vc_onset_publish).
 *   KILL    word0 = 0xA01000 | voice << 10, word1 = gen (4 bits); one voice per entry. A matching KILL cancels the
 *           pending onset and damps; a stale KILL is accepted without mutation.
 *   ALL OFF word0 = 0xA03000, word1 = 0: zeroes the four generations, cancels pending, damps.
 *   PDATA / PCOMMIT (opcodes 4, 5, A1_PATCH=1 variant only; v0.1 has no patch ops, the r4 decoder passes them on
 *           unchanged): word0 = 0xA04000 | index << 6 | epoch, word1 = value; 0xA05000 | count << 6 | epoch, 0.
 *   PATCH_AD (opcode 6, AD_LIVE=1 builds only; attack and decay in one packet, two fields):
 *           word0 = 0xA06000 exactly, word1 = attack | decay << 7 (indices 0..127), word1 bits 14..23 ZERO (the DSP
 *           refuses them). No level field: loudness is the stock track-8 mixer level. vc_ad() is the one serializer.
 * Sample note words (0x8/0x9 class) pass through untouched; vc_kind() tells the admission code what an entry is.
 *
 * Pitch (voice command ABI r3): inc48(note) =
 * TOP[k] >> o with k = note - 108 + 12 o, o = (119 - note) / 12, TOP[k] the 48-bit increment of note 108 + k at Fs
 * 48046.875 (A4 = 440 Hz), truncating shift; then e = bitlen(inc48) - 24 and M = inc48 ROUNDED to nearest at bit e
 * (half up; a carry renormalises). The rounding threshold 2^(e-1) is a multiple of 2^o, so M depends only on k and
 * e = E_k - o: the ARM carries twelve (M, E) pairs as code constants (the build keeps .text only), the rounded
 * values of ABI r3 (seven differ by one LSB from the truncating r2 rule). Checked against the ABI r3 goldens for all
 * 120 notes. */

#ifndef AD_LIVE
#define AD_LIVE         0               /* 1: PATCH_AD (live attack/decay); the profile switch, build_lightshow.py */
#endif
#define VC_CLASS        0xA00000u
#define VC_HB_ONSET     0xA4u
#define VC_OP_KILL      1u
#define VC_OP_ALLOFF    3u
#define VC_OP_PDATA     4u
#define VC_OP_PCOMMIT   5u
#define VC_OP_AD        6u
#define VC_AD_W0        0xA06000u       /* PATCH_AD word 0, exact */
#define VC_AD_MASK      0x3FFFu         /* word 1: attack bits 0..6, decay bits 7..13; 14..23 zero */
#define VC_NOTE_MAX     119u

enum { VK_STOCK_ON, VK_STOCK_OFF, VK_STOCK_OTHER, VK_ONSET, VK_KILL, VK_ALLOFF, VK_PATCH, VK_AD };
#if CORE0_SYNTH
/* Core-0 queue packets (draft; PROVISIONAL, not frozen):
 *   RELEASE word0 = 0xA02000 | voice << 10, word1 = the expected generation (bits 0..3 only): generation-matched.
 *   ENV AD  word0 = 0xA06000, word1 = A | D << 7;  ENV SR  word0 = 0xA07000, word1 = S | R << 7 (7-bit indices, bits
 *           14..23 zero). The two form one coherent ordered group ahead of the ONSETs that capture them: in the ARM
 *           FIFO the group is ONE entry (VK_AD, word1 = A | D << 7 | S << 14 | R << 21), expanded here at publication.
 *   ONSET   unchanged layout; the pitch includes M1 Tune (vc_tune_pitch, the 60-pair table).
 *   TONE    (CORE0_TONE; the proposed packet, not frozen) word0 = 0xA08000 exactly,
 *           word1 = depth | cutoff << 7 | resonance << 14 (M6 / M7 / M8, params 11 / 12 / 13), bits 21..23 zero. Live:
 *           it acts on sounding voices; the DSP's one queue handler publishes every target before the block latch. */
#define VC_OP_REL       2u
#define VC_SR_W0        0xA07000u
#define VC_TONE_W0      0xA08000u
#define VC_TONE_MASK    0x1FFFFFu
enum { VK_REL = VK_AD + 1, VK_TONE };
#include "tune_pitch.h"
#endif

/* TOP[k] = note 108 + k: (rounded mantissa, exponent) of the 48-bit increment; a switch so that it stays in .text. */
static uint32_t vc_top(uint32_t k, uint32_t *e)
{
    switch (k) {
    case 0:  *e = 21; return 0xB26DC6u;       /* 0x164DB8C6BA25 */
    case 1:  *e = 21; return 0xBD09EBu;       /* 0x17A13D505C1D */
    case 2:  *e = 21; return 0xC84791u;       /* 0x1908F22D8510 */
    case 3:  *e = 21; return 0xD43056u;       /* 0x1A860AB152C5 */
    case 4:  *e = 21; return 0xE0CE64u;       /* 0x1C19CC752714 */
    case 5:  *e = 21; return 0xEE2C83u;       /* 0x1DC5906ED709 */
    case 6:  *e = 21; return 0xFC5621u;       /* 0x1F8AC41764B9 */
    case 7:  *e = 22; return 0x85ABABu;       /* 0x216AEAA33F8A */
    case 8:  *e = 22; return 0x8D9E79u;       /* 0x23679E4D15C7 */
    case 9:  *e = 22; return 0x960A47u;       /* 0x258291B45211 */
    case 10: *e = 22; return 0x9EF645u;       /* 0x27BD91507034 */
    default: *e = 22; return 0xA86A14u;       /* 0x2A1A84FA6692 */
    }
}

/* note 0..119 -> (M, e); notes above 119 are clamped (the chord expander folds them first). */
static uint32_t vc_pitch(uint32_t note, uint32_t *e)
{
    if (note > VC_NOTE_MAX)
        note = VC_NOTE_MAX;
    uint32_t o = (VC_NOTE_MAX - note) / 12u, k = note + 12u * o - 108u, ek;
    uint32_t m = vc_top(k, &ek);
    *e = ek - o;
    return m;
}

/* One ONSET entry for voice v; gen is filled in at publication (vc_onset_publish). */
static void vc_onset_one(uint32_t v, uint32_t note, uint32_t vel, uint32_t *w0, uint32_t *w1)
{
    uint32_t e, m = vc_pitch(note, &e);
    if (!vel)
        vel = 1;
    *w0 = (VC_HB_ONSET + (v & 3u)) << 16 | (m >> 8);
    *w1 = (m & 0xFFu) << 16 | (e & 31u) << 11 | (vel & 0x7Fu) << 4;
}

/* Ring entries one onset of these voices takes: one per voice. Admission reserves them all before allocation. */
static uint32_t vc_onset_entries(uint32_t mask)
{
    uint32_t n = 0;
    for (uint32_t v = 0; v < 4; v++)
        n += mask >> v & 1u;
    return n;
}

/* The entries of one onset, lowest voice first; returns their count (<= 4). note[v] is the note of voice v. */
static uint32_t vc_onset_words(uint32_t mask, const uint8_t *note, uint32_t vel, uint32_t w[4][2])
{
    uint32_t n = 0;
    for (uint32_t v = 0; v < 4; v++)
        if (mask >> v & 1u) {
            vc_onset_one(v, note[v], vel, &w[n][0], &w[n][1]);
            n++;
        }
    return n;
}

static uint32_t vc_onset_mask(uint32_t w0)
{
    return 1u << ((w0 >> 16) & 3u);
}

/* Drop the voices in kill from a queued onset entry; returns the voices it still starts (0: delete the entry). */
static uint32_t vc_onset_strip(uint32_t kill, uint32_t *w0, uint32_t *w1)
{
    (void)w1;
    return vc_onset_mask(*w0) & ~kill;
}

/* At publication: the generation the DSP expects, (mirror + 1) & 15, into word1 bits 3..0. */
static void vc_onset_publish(uint32_t *w1, uint32_t mirror_gen)
{
    *w1 = (*w1 & ~15u) | ((mirror_gen + 1u) & 15u);
}

/* One ring entry of the pending KILL: the lowest pending voice with its mirror generation; returns the voice bit it
 * covers. The admission loop publishes one entry per free slot and keeps the rest pending, still ahead of every
 * queued ONSET, so the allocator (which only sets bits) and its KILL-before-reuse invariant do not depend on this. */
static uint32_t vc_kill_entry(uint32_t pending, uint32_t gens16, uint32_t *w0, uint32_t *w1)
{
    uint32_t v = 0;
    while (v < 3 && !(pending >> v & 1u))
        v++;
    *w0 = VC_CLASS | VC_OP_KILL << 12 | v << 10;
    *w1 = (gens16 >> (4 * v)) & 15u;
    return 1u << v;
}

static void vc_alloff(uint32_t *w0, uint32_t *w1)
{
    *w0 = VC_CLASS | VC_OP_ALLOFF << 12;
    *w1 = 0;
}

static void vc_pdata(uint32_t index, uint32_t epoch, uint32_t value, uint32_t *w0, uint32_t *w1)
{
    *w0 = VC_CLASS | VC_OP_PDATA << 12 | (index & 63u) << 6 | (epoch & 63u);
    *w1 = value & 0xFFFFFFu;
}

static void vc_pcommit(uint32_t count, uint32_t epoch, uint32_t *w0, uint32_t *w1)
{
    *w0 = VC_CLASS | VC_OP_PCOMMIT << 12 | (count & 63u) << 6 | (epoch & 63u);
    *w1 = 0;
}

/* PATCH_AD: the only serializer of the AD packet. ad = attack | decay << 7 (the packed form the voice table keeps). */
#if CORE0_SYNTH
#define VC_AD_NMASK     0x0FFFFFFFu     /* the ARM FIFO keeps the whole A/D/S/R group in one entry (above) */
#else
#define VC_AD_NMASK     VC_AD_MASK
#endif
static void vc_ad(uint32_t ad, uint32_t *w0, uint32_t *w1)
{
    *w0 = VC_AD_W0;
    *w1 = ad & VC_AD_NMASK;
}

static uint32_t vc_kind(uint32_t w0)
{
    uint32_t c = w0 & 0xF00000u, hb = w0 >> 16;
    if (c == 0x900000u)
        return VK_STOCK_ON;
    if (c == 0x800000u)
        return VK_STOCK_OFF;
    if (c != VC_CLASS)
        return VK_STOCK_OTHER;
    if (hb >= VC_HB_ONSET && hb < VC_HB_ONSET + 4u)
        return VK_ONSET;
    uint32_t op = (w0 >> 12) & 15u;
#if AD_LIVE
    if (w0 == VC_AD_W0)
        return VK_AD;
#endif
#if CORE0_SYNTH
    if (op == VC_OP_REL)
        return VK_REL;
    if (w0 == VC_TONE_W0)
        return VK_TONE;
#endif
    return op == VC_OP_KILL ? VK_KILL : op == VC_OP_ALLOFF ? VK_ALLOFF : VK_PATCH;
}

/* A sample note word for DSP channel 7 (track 8): note on/off class with channel 7 in bits 2..0. */
static uint32_t vc_stock_ch(uint32_t w0)
{
    return w0 & 7u;
}

static uint32_t vc_stock_ch7(uint32_t w0)
{
    uint32_t k = vc_kind(w0);
    return (k == VK_STOCK_ON || k == VK_STOCK_OFF) && (w0 & 7u) == 7u;
}

#if CORE0_SYNTH
static void vc_release(uint32_t v, uint32_t gen, uint32_t *w0, uint32_t *w1)
{
    *w0 = VC_CLASS | VC_OP_REL << 12 | (v & 3u) << 10;
    *w1 = gen & 15u;
}

static uint32_t vc_rel_voice(uint32_t w0)
{
    return (w0 >> 10) & 3u;
}

/* The two wire entries of one envelope group (N entry word1 = A | D << 7 | S << 14 | R << 21), AD first. */
static void vc_env_pair(uint32_t env, uint32_t w[2][2])
{
    w[0][0] = VC_AD_W0;
    w[0][1] = env & VC_AD_MASK;
    w[1][0] = VC_SR_W0;
    w[1][1] = (env >> 14) & VC_AD_MASK;
}

/* Note 0..119 at Tune t (channel param 6, clamped 4..124, 64 = neutral, 5 steps per semitone) -> M | e << 24:
 * u = 5 note + t - 4 (0..715, nonnegative), row k = u % 60, octave u / 60 - 10, e = E[k] + octave (11..23).
 * The pitch is not folded back into MIDI 0..119. At t = 64 it equals vc_pitch() for all 120 notes (test). */
__attribute__((noinline)) static uint32_t vc_tune_pitch(uint32_t note, uint32_t tune)
{
    if (note > VC_NOTE_MAX)
        note = VC_NOTE_MAX;
    tune = tune < 4u ? 4u : tune > 124u ? 124u : tune;
    uint32_t u = 5u * note + tune - 4u;
    const uint8_t *t = tune_tab() + 4u * (u % 60u);
    return ((uint32_t)t[0] << 16 | (uint32_t)t[1] << 8 | t[2]) | (t[3] + u / 60u - 10u) << 24;
}

static void vc_onset_tuned(uint32_t v, uint32_t note, uint32_t tune, uint32_t vel, uint32_t *w0, uint32_t *w1)
{
    uint32_t p = vc_tune_pitch(note, tune), m = p & 0xFFFFFFu, e = p >> 24;
    if (!vel)
        vel = 1;
    *w0 = (VC_HB_ONSET + (v & 3u)) << 16 | (m >> 8);
    *w1 = (m & 0xFFu) << 16 | (e & 31u) << 11 | (vel & 0x7Fu) << 4;
}
#endif
