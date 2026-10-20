/* Track-8 synth settings codec, v0.1. Shared by the settings page (synthpage.c) and the note tap / chord engine /
 * voice table (notetap.c). Header only: static inline functions, no data, no firmware calls, no RAM.
 *
 * Both objects live in the T1 extension block (trackmodes.c; block offsets below are relative to
 * store + 0x29110). The block's header, CRC, the other seven records, the other seven patch slots and the reserved
 * bytes are T1's; this codec owns only the bytes it names. Writes go through the T1 edit path (tm_edit in
 * trackmodes.c: attach, READONLY, BUSY first, CRC), never directly.
 *
 * Record 7 (16 bytes at block +0x078 = TM_REC + 7 * 16)
 *   +0  mode     T1's stored-mode byte. NOT part of this codec: never read or written by it (v0.1 track 8 is the synth
 *                whatever this byte holds).
 *   +1  version  0 = unset (bytes +1..+15 all zero: decode gives the defaults); 1 = this layout; anything else =
 *                unknown (decode gives the defaults; editors must refuse to write, so a newer layout is never damaged)
 *   +2  npp      notes per press 1..4                                   default 1
 *   +3  type     chord type 0 Maj, 1 Min, 2 Sus2, 3 Sus4, 4 7th, 5 m7    default 0 (used when inkey = 0)
 *   +4  inkey    0 = chord type above, 1 = in key: the scale's triad on the played note (its 7th chord at npp 4);
 *                as modules/chord.c "In key"                            default 1
 *   +5  voicing  0 root position, 1 first inversion, 2 second inversion, 3 spread (second-lowest note an octave up)
 *                                                                       default 0
 *   +6  scale    0 major, 1 natural minor                               default 0
 *   +7  root     key root 0..11 (0 = C = engine note 60 mod 12)         default 0
 *   +8  octave   int8 -2..+2, added to every chord note                 default 0
 *   +9  poly     MIDI in on track 8's channel: 1 = poly up to the voice cap, 0 = chord-mono as the pads
 *                                                                       default 1
 *   +10 preset   0..7, reserved for V8 presets; v0.1 writes 0 and ignores it              default 0
 *   +11..+15     reserved: written 0 when the record is created, otherwise preserved as found
 * Patch 7 (64 bytes at block +0x248 = 0x088 + 7 * 64)
 *   +0  codec    0 = unset (all 64 bytes zero: defaults); 1 = SYN_PATCH_SUB (this layout); anything else = unknown
 *   +1  version  1 (with codec 1); any other value with codec 1 = unknown
 *   +2  wave     0 saw, 1 pulse                                         default 0
 *   +3  attack   0..127 (0 = fastest)                                   default 0
 *   +4  decay    0..127                                                 default 64
 *   +5  level    0..127                                                 default 100
 *   +6..+63      reserved: written 0 on creation, otherwise preserved
 * The mapping of attack/decay/level to the DSP constants is the patch publisher's, not this codec's; the stored
 * values are user-scale 0..127.
 *
 * Invalid data. Unset or unknown: every field decodes to its default. Known version with a field out of range: that
 * field decodes to its default, the others as stored (the stored byte is left alone until that field is edited).
 * Status SYN_UNKNOWN must make an editor refuse (the page reports READONLY); SYN_UNSET means the first edit creates the
 * object: header (version, or codec + version), every field at its default, reserved bytes zero, then the edit.
 */
#ifndef SYNTH_SETTINGS_H
#define SYNTH_SETTINGS_H
#include <stdint.h>

#define SYN_REC_AT        0x078u        /* block offset of record 7 */
#define SYN_REC_LEN       16u
#define SYN_PATCH_AT      0x248u        /* block offset of patch slot 7 */
#define SYN_PATCH_LEN     64u
#define SYN_REC_VERSION   1u
#define SYN_PATCH_SUB     1u            /* patch codec: subtractive voice, v0.1 */
#define SYN_PATCH_VERSION 1u

/* record 7 byte offsets */
#define SR_VERSION 1u
#define SR_NPP     2u
#define SR_TYPE    3u
#define SR_INKEY   4u
#define SR_VOICING 5u
#define SR_SCALE   6u
#define SR_ROOT    7u
#define SR_OCTAVE  8u
#define SR_POLY    9u
#define SR_PRESET  10u
#define SR_FIELDS  9u                   /* fields SR_NPP .. SR_PRESET */
/* patch 7 byte offsets */
#define SP_CODEC   0u
#define SP_VERSION 1u
#define SP_WAVE    2u
#define SP_ATTACK  3u
#define SP_DECAY   4u
#define SP_LEVEL   5u
#define SP_FIELDS  4u                   /* fields SP_WAVE .. SP_LEVEL */

enum { SYN_UNSET, SYN_V1, SYN_UNKNOWN };

/* Field limits, indexed by byte offset - first field offset: {min, max, default}; octave as int8. Kept as code, not
 * data: the hook build has no data sections. */
static inline int32_t syn_rec_lim(uint32_t off, uint32_t which)
{
    int32_t lo = 0, hi = 1, def = 0;
    switch (off) {
    case SR_NPP:     lo = 1; hi = 4; def = 1; break;
    case SR_TYPE:    hi = 5; break;
    case SR_INKEY:   def = 1; break;
    case SR_VOICING: hi = 3; break;
    case SR_SCALE:   break;
    case SR_ROOT:    hi = 11; break;
    case SR_OCTAVE:  lo = -2; hi = 2; break;
    case SR_POLY:    def = 1; break;
    case SR_PRESET:  hi = 7; break;
    default:         hi = 0; break;
    }
    return which == 0 ? lo : which == 1 ? hi : def;
}

static inline int32_t syn_patch_lim(uint32_t off, uint32_t which)
{
    int32_t lo = 0, hi = 127, def = 0;
    switch (off) {
    case SP_WAVE:   hi = 1; break;
    case SP_ATTACK: break;
    case SP_DECAY:  def = 64; break;
    case SP_LEVEL:  def = 100; break;
    default:        hi = 0; break;
    }
    return which == 0 ? lo : which == 1 ? hi : def;
}

static inline uint32_t syn_zero(const volatile uint8_t *p, uint32_t from, uint32_t n)
{
    uint32_t any = 0;
    for (uint32_t i = from; i < n; i++)
        any |= p[i];
    return !any;
}

/* Status of record 7 (16 bytes, byte 0 = T1 mode, ignored). */
static inline uint32_t syn_rec_status(const volatile uint8_t *rec)
{
    if (syn_zero(rec, 1, SYN_REC_LEN))
        return SYN_UNSET;
    return rec[SR_VERSION] == SYN_REC_VERSION ? SYN_V1 : SYN_UNKNOWN;
}

static inline uint32_t syn_patch_status(const volatile uint8_t *p)
{
    if (syn_zero(p, 0, SYN_PATCH_LEN))
        return SYN_UNSET;
    return p[SP_CODEC] == SYN_PATCH_SUB && p[SP_VERSION] == SYN_PATCH_VERSION ? SYN_V1 : SYN_UNKNOWN;
}

/* Decoded value of one record field (byte offset SR_*); rec may be 0 (= unset). */
static inline int32_t syn_rec_get(const volatile uint8_t *rec, uint32_t off)
{
    int32_t def = syn_rec_lim(off, 2);
    if (!rec || syn_rec_status(rec) != SYN_V1)
        return def;
    int32_t v = off == SR_OCTAVE ? (int8_t)rec[off] : rec[off];
    return v < syn_rec_lim(off, 0) || v > syn_rec_lim(off, 1) ? def : v;
}

static inline int32_t syn_patch_get(const volatile uint8_t *p, uint32_t off)
{
    int32_t def = syn_patch_lim(off, 2);
    if (!p || syn_patch_status(p) != SYN_V1)
        return def;
    int32_t v = p[off];
    return v > syn_patch_lim(off, 1) ? def : v;
}

/* Clamp v to the field's range: the stored byte for it. */
static inline uint8_t syn_rec_byte(uint32_t off, int32_t v)
{
    int32_t lo = syn_rec_lim(off, 0), hi = syn_rec_lim(off, 1);
    return (uint8_t)(v < lo ? lo : v > hi ? hi : v);
}

static inline uint8_t syn_patch_byte(uint32_t off, int32_t v)
{
    int32_t hi = syn_patch_lim(off, 1);
    return (uint8_t)(v < 0 ? 0 : v > hi ? hi : v);
}

/* Create an unset record in place: version and every field at its default; bytes +11..+15 are already zero (unset
 * means +1..+15 zero). Byte 0 (T1 mode) is not touched. */
static inline void syn_rec_create(volatile uint8_t *rec)
{
    rec[SR_VERSION] = SYN_REC_VERSION;
    for (uint32_t off = SR_NPP; off < SR_NPP + SR_FIELDS; off++)
        rec[off] = syn_rec_byte(off, syn_rec_lim(off, 2));
}

static inline void syn_patch_create(volatile uint8_t *p)
{
    p[SP_CODEC] = SYN_PATCH_SUB;
    p[SP_VERSION] = SYN_PATCH_VERSION;
    for (uint32_t off = SP_WAVE; off < SP_WAVE + SP_FIELDS; off++)
        p[off] = syn_patch_byte(off, syn_patch_lim(off, 2));
}

/* Scale table shared by the track-8 chord engine (notetap.c, in SCALES builds) and the sample-track scales
 * (scales.c). Ids 0 and 1 are the record-7 values above (major, natural minor); the SCALES sub-record stores
 * 0..SYN_SCALES-1. A 12-bit mask per scale, bit k = k semitones above the root. Code, not data (the hook build keeps
 * .text only); nothing here is emitted unless a SCALES build calls it. */
#define SYN_SCALES 10u
enum { SYN_MAJOR, SYN_MINOR, SYN_DORIAN, SYN_PHRYGIAN, SYN_LYDIAN, SYN_MIXOLYDIAN, SYN_HARMONIC_MINOR,
       SYN_MAJOR_PENTA, SYN_MINOR_PENTA, SYN_BLUES };

static inline uint32_t syn_scale_mask(uint32_t id)
{
    switch (id) {
    case SYN_MINOR:          return 0x5ADu;     /* 0 2 3 5 7 8 10 */
    case SYN_DORIAN:         return 0x6ADu;     /* 0 2 3 5 7 9 10 */
    case SYN_PHRYGIAN:       return 0x5ABu;     /* 0 1 3 5 7 8 10 */
    case SYN_LYDIAN:         return 0xAD5u;     /* 0 2 4 6 7 9 11 */
    case SYN_MIXOLYDIAN:     return 0x6B5u;     /* 0 2 4 5 7 9 10 */
    case SYN_HARMONIC_MINOR: return 0x9ADu;     /* 0 2 3 5 7 8 11 */
    case SYN_MAJOR_PENTA:    return 0x295u;     /* 0 2 4 7 9 */
    case SYN_MINOR_PENTA:    return 0x4A9u;     /* 0 3 5 7 10 */
    case SYN_BLUES:          return 0x4E9u;     /* 0 3 5 6 7 10 */
    default:                 return 0xAB5u;     /* major: 0 2 4 5 7 9 11 */
    }
}

static inline uint32_t syn_scale_size(uint32_t mask)
{
    uint32_t n = 0;
    for (uint32_t k = 0; k < 12; k++)
        n += mask >> k & 1;
    return n;
}

/* Semitones of degree i (0, 1, ...) above the root: octave i / n, then the (i % n)-th set bit of the mask. */
static inline uint32_t syn_scale_semi(uint32_t mask, uint32_t i)
{
    uint32_t n = syn_scale_size(mask), d = i % n, k = 0;
    for (; k < 12; k++)
        if ((mask >> k & 1) && d-- == 0)
            break;
    return 12 * (i / n) + k;
}

#endif
