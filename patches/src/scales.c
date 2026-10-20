/* Scales for the sample tracks 1..7 (build switch SCALES). Included by arrhythmia.c after devkit_abi3.c,
 * trackmodes.c and plockfix.c, before synthpage.c and notetap.c (both use the project-wide scale in SCALES builds).
 *
 * The Note view of every track plays from a per-track keyboard table (128 entries: the note of every keyboard pad
 * position, index = pad + 16 * (5 - octave), 0 = silent), with a reverse map and a partner entry per index that
 * colours root pads. A scaled track gets its table rewritten here: one octave per table row (degrees 0..n, root to
 * root; +0x19C = 1, so index = pad + 8 * (5 - octave)), Up/Down still move by an octave and the simple view's two
 * rows are the octave and the one above. Root pads carry a partner entry so the Note-view colours mark them and both
 * pads of a root light together. No other note is held twice: the Note view lights a sounding note only at its
 * reverse-map index and that index's partner, so a non-root duplicate would light the wrong pad. Playback of the
 * table is unchanged: each held pad releases exactly the note it started, so a scale change while a pad is held
 * cannot strand a note; a held step takes the table's note. Slice layouts and track 8 are never touched. MIDI in and
 * sequencer playback do not read the table (they light their notes through the same reverse map and partner).
 *
 * Settings: a versioned sub-record in the T1 block's reserved area (block + 0x288, 16 bytes), written only through
 * tm_edit (BUSY first, live-tail check, CRC):
 *   +0 'S' +1 'C' magic, +2 version 1, +3 scale 0..SYN_SCALES-1 (synth_settings.h), +4 root 0..11 (0 = C),
 *   +5 bit t = track t + 1 scaled (t = 0..6; bit 7 reserved, written 0), +6..+15 reserved (0 on creation, preserved).
 *   All 16 zero = unset: scale and root fall back to track 8's record 7 (+6 scale 0/1, +7 root), no track scaled.
 *   Any other content = unknown: same fallback, nothing scaled, every edit refused (READONLY).
 * Runtime copy: struct sc (SC_SIZE bytes at SC_AT, the new lowest reservation; hook_boot lowers the heap limit to it),
 * refreshed by tm_apply and tm_edit (sc_copy, inside their suspension).
 *
 * Hooks: hook_kb_obs (keyboard layout change): original first, then the scaled table again if a chromatic layout was
 * just rebuilt for a track whose bit is set. sc_main (from hook_main, Main task, after the timer
 * service): attach to the boot project if no apply was seen (tm_attach and its gates), then rebuild AT MOST ONE
 * keyboard whose layout differs from the settings (sc_sync; all seven converge within seven passes).
 * SysEx 0E (debug, Main task): 0E 00 status; 0E 01 scale root bits: set all three (one tm_edit).
 *   reply F0 00 20 29 00 60 0E <sub> <status> <why> <sc> <scale> <root> <bits> <applied> F7 (16 bytes): status TS_*,
 *   why = T1 TM_*, sc 0 unset / 1 v1 / 2 unknown, scale/root/bits as decoded, applied = tracks whose keyboard holds
 *   the scaled table of the current settings now (partial while the layouts converge).
 */
#include "synth_settings.h"

#define SC_SUB_AT       0x288u          /* block offset of the sub-record (T1 reserved area) */
#define SC_SUB_LEN      16u
#define SC_VERSION      1u
#define SC_SCALE        3u
#define SC_ROOT         4u
#define SC_BITS         5u
#define SC_TRACKS       7u              /* tracks 1..7 (t = 0..6); track 8 is the synth's */
#define SC_MAGIC_RT     0x31524353u     /* "SCR1": runtime block initialised by hook_boot */
#define NV_VT           0x08067248u     /* note-view handler check */
#define KB_VT           0x080696F4u     /* keyboard object check; layout hook site */
#define KB_TABLE        0x08u
#define KB_REV          0x88u
#define KB_REV_LEN      0x8Cu
#define KB_PAIR         0x114u
#define KB_ROWS         0x19Cu          /* rows per octave step: 2 chromatic, 0 slice, 1 scaled */
#define KB_LO           0x19u           /* the chromatic table's note range */
#define KB_HI           0x6Cu
#define KB_REBUILD      ((void (*)(uint32_t))0x08055F8Du)   /* original layout rebuild */
#define KB_OBSERVER     ((fn4_t)0x08055F69u)                /* layout hook target */
#define NOTIFY          ((void (*)(uint32_t))0x0803FBEDu)   /* change notify */

enum { SC_UNSET, SC_V1, SC_UNKNOWN };
enum { SC_OP_ALL, SC_OP_SCALE, SC_OP_ROOT, SC_OP_TOGGLE, SC_OP_MINOR };

struct sc {
    uint32_t magic;
    uint8_t sub[SC_SUB_LEN];                  /* the sub-record as of the last apply / edit (zero unless block OK) */
    uint8_t rec_scale, rec_root;              /* record 7 fallback, decoded at the same time */
    uint8_t applied[SC_TRACKS];               /* diagnostic: layout last reconciled, 0 unscaled, else 1 + scale * 12 + root
                                                 (decisions read the keyboard objects, never this) */
    uint8_t builds, restores;                 /* scaled builds / unscaled rebuilds by sc_sync or the observer (mod 256) */
    uint8_t next;                             /* sc_sync's round-robin start: the track after the last one rebuilt */
};
_Static_assert(sizeof(struct sc) <= SC_SIZE, "scales runtime block must fit");
#define SC ((volatile struct sc *)SC_AT)

typedef uint32_t (*fn4_t)(uint32_t, uint32_t, uint32_t, uint32_t);

static volatile struct sc *sc(void)
{
    volatile struct sc *c = SC;
    return region() && c->magic == SC_MAGIC_RT ? c : 0;
}

static void sc_boot(void)                     /* from hook_boot, block already zeroed */
{
    SC->magic = SC_MAGIC_RT;
}

static uint32_t sc_status(const volatile uint8_t *p)
{
    if (all_zero(p, SC_SUB_LEN))
        return SC_UNSET;
    return p[0] == 'S' && p[1] == 'C' && p[2] == SC_VERSION ? SC_V1 : SC_UNKNOWN;
}

/* From tm_apply / tm_edit with the scheduler suspended: b = the live block if it is OK (or was just written), else 0. */
static void sc_copy(const volatile uint8_t *b)
{
    volatile struct sc *c = sc();
    if (!c)
        return;
    for (uint32_t i = 0; i < SC_SUB_LEN; i++)
        c->sub[i] = b ? b[SC_SUB_AT + i] : 0;
    const volatile uint8_t *rec = b ? b + SYN_REC_AT : 0;
    c->rec_scale = (uint8_t)syn_rec_get(rec, SR_SCALE);
    c->rec_root = (uint8_t)syn_rec_get(rec, SR_ROOT);
}

/* Decoded settings (runtime copy). Out-of-range fields of a v1 sub-record decode to major / C. */
static uint32_t sc_scale(void)
{
    volatile struct sc *c = sc();
    if (!c)
        return SYN_MAJOR;
    if (sc_status(c->sub) != SC_V1)
        return c->rec_scale;
    return c->sub[SC_SCALE] < SYN_SCALES ? c->sub[SC_SCALE] : SYN_MAJOR;
}

static uint32_t sc_root(void)
{
    volatile struct sc *c = sc();
    if (!c)
        return 0;
    if (sc_status(c->sub) != SC_V1)
        return c->rec_root;
    return c->sub[SC_ROOT] < 12 ? c->sub[SC_ROOT] : 0;
}

static uint32_t sc_bits(void)
{
    volatile struct sc *c = sc();
    return c && sc_status(c->sub) == SC_V1 ? c->sub[SC_BITS] & 0x7Fu : 0;
}

/* tm_edit callback (live block, scheduler suspended, after the BUSY gate): arg = op | a << 8 | b << 16 | c << 24.
 * Creating an unset sub-record takes scale and root from record 7 (the values in force), so nothing jumps. */
static uint32_t sc_edit_cb(volatile uint8_t *b, uint32_t arg)
{
    volatile uint8_t *p = b + SC_SUB_AT;
    uint32_t st = sc_status(p), op = arg & 0xFF, x = arg >> 8 & 0xFF, y = arg >> 16 & 0xFF, z = arg >> 24;
    if (st == SC_UNKNOWN)
        return TS_READONLY;
    if ((op == SC_OP_ALL && (x >= SYN_SCALES || y > 11 || z > 0x7F)) || (op == SC_OP_SCALE && x >= SYN_SCALES)
            || (op == SC_OP_ROOT && x > 11) || (op == SC_OP_TOGGLE && x >= SC_TRACKS) || op > SC_OP_MINOR)
        return TS_REFUSED;
    if (st == SC_UNSET) {
        p[0] = 'S';
        p[1] = 'C';
        p[2] = SC_VERSION;
        p[SC_SCALE] = (uint8_t)syn_rec_get(b + SYN_REC_AT, SR_SCALE);
        p[SC_ROOT] = (uint8_t)syn_rec_get(b + SYN_REC_AT, SR_ROOT);
    }
    if (op == SC_OP_ALL) {
        p[SC_SCALE] = (uint8_t)x;
        p[SC_ROOT] = (uint8_t)y;
        p[SC_BITS] = (uint8_t)z;
    } else if (op == SC_OP_SCALE) {
        p[SC_SCALE] = (uint8_t)x;
    } else if (op == SC_OP_ROOT) {
        p[SC_ROOT] = (uint8_t)x;
    } else if (op == SC_OP_TOGGLE) {
        p[SC_BITS] = (uint8_t)((p[SC_BITS] ^ 1u << x) & 0x7F);
    } else {                                  /* the page's minor pad: natural minor <-> major */
        p[SC_SCALE] = p[SC_SCALE] == SYN_MINOR ? SYN_MAJOR : SYN_MINOR;
    }
    return TS_OK;
}

static uint32_t sc_edit(struct state *s, uint32_t arg)
{
    return tm_edit(s, sc_edit_cb, arg);
}

/* ---- keyboard layouts ----------------------------------------------------------------------------------------- */

/* Byte i of a value cell; 0xFF if the chain is not readable. */
static uint32_t obs_byte(uint32_t obs, uint32_t i)
{
    uint32_t buf = link(obs, 0xC + 4 * ((link(obs, 4) >> i) & 1));
    return buf >= SRAM_START && buf + 4 * i < SRAM_END ? *(volatile uint8_t *)(buf + 4 * i) : 0xFF;
}

/* Keyboard object of track t (0..7) through the checked chain button cell -> controller -> grid page -> note-view
 * handler -> keyboard; 0 if any link does not check out. */
static uint32_t sc_kb(uint32_t t)
{
    if (link(BTN_CELL, 0) != BTN_LAMBDA)
        return 0;
    uint32_t ctrl = link(link(BTN_CELL, 4), 0), grid = link(ctrl, 0x3C);
    if (link(ctrl, 0) != CTRL_VT || link(grid, 0) != GRID_VT)
        return 0;
    uint32_t nv = link(grid, 0x54 + 4 * t), kb = link(nv, 0x4C);
    if (link(nv, 0) != NV_VT || link(kb, 0) != KB_VT || (kb & 3) || kb > SRAM_END - 0x1A0)
        return 0;
    return kb;
}

/* 1 if the track's slice setting is off, i.e. the chromatic layout is used. */
static uint32_t sc_chromatic(uint32_t kb)
{
    return obs_byte(link(kb, 0x198), 0xC) == 0;
}

/* Write the scaled table into keyboard object kb, in the chromatic table's format but with ONE ROW PER OCTAVE and
 * kb[0x19C] = 1 (pad -> index and the LED window both read index = pad + 8 * rows * (5 - octave)): table row k
 * (indexes 8k..8k+7) = 0x48 + root + 12 (5 - k) + degree c, c = 0..n of an n-note scale (root to root), columns
 * n+1..7 gaps (0; only for n < 7). Row k column n is row k - 1 column 0 (the root an octave up): the only notes held
 * twice, partnered, so the Note view's LED update (reverse-map index + its partner) lights every pad of a sounding
 * note. Simple view (pads 16..31, rows 7 - o and 8 - o at octave o; 5- and 6-note scales: gap pads past the upper
 * root); Up / Down one octave; the expanded view (pads 0..31, rows 5 - o .. 8 - o) shows four consecutive octaves. Notes outside the
 * range 0x19..0x6C are silent (0). Reverse map: the first index of each note; root partners: the other index of the
 * same note, else itself. Under the suspension (the UI task reads the table for the LEDs); the change notify
 * afterwards. */
static void sc_build(uint32_t kb, uint32_t scale, uint32_t root)
{
    volatile uint8_t *k = (volatile uint8_t *)kb;
    uint32_t mask = syn_scale_mask(scale), n = syn_scale_size(mask);
    SUSPEND_ALL();
    for (uint32_t i = 0; i < 0x80; i++) {
        k[KB_TABLE + i] = 0;
        k[KB_PAIR + i] = 0xFF;
    }
    for (uint32_t i = 0; i < KB_REV_LEN; i++)
        k[KB_REV + i] = 0xFF;
    for (uint32_t r = 0; r < 16; r++)
        for (uint32_t c = 0; c <= n && c < 8; c++) {
            int32_t v = 0x48 + (int32_t)root + 12 * (5 - (int32_t)r) + (int32_t)syn_scale_semi(mask, c);
            k[KB_TABLE + 8 * r + c] = (uint8_t)(v >= (int32_t)KB_LO && v <= (int32_t)KB_HI ? v : 0);
        }
    for (uint32_t i = 0; i < 0x80; i++) {
        uint32_t v = k[KB_TABLE + i];
        if (v && k[KB_REV + v] == 0xFF)
            k[KB_REV + v] = (uint8_t)i;
    }
    for (uint32_t i = 0; i < 0x80; i++) {
        uint32_t v = k[KB_TABLE + i], p = i;
        if (!v || v % 12 != root)
            continue;
        for (uint32_t q = 0; q < 0x80; q++)
            if (q != i && k[KB_TABLE + q] == v)
                p = q;
        k[KB_PAIR + i] = (uint8_t)p;
    }
    k[KB_ROWS] = 1;
    k[0x194] = 0xFF;
    k[0x195] = 0xFF;
    RESUME_ALL();
    NOTIFY(link(kb, 4));
}

/* Signature of the scaled layout for (scale, root), as sc_build writes it: [0] = index 0x30 (row 6, column 0:
 * 0x3C + root; a gap pad, 0, in the chromatic layout), [1..8] = indexes 0x28..0x2F (row 5, the simple view's
 * bottom row at the default octave: 0x48 + root + semi(c) for c <= n, 0 beyond). Every note is in range, and the
 * nine bytes differ for any two (scale, root). */
static void sc_sig(uint32_t scale, uint32_t root, uint8_t *sig)
{
    uint32_t mask = syn_scale_mask(scale), n = syn_scale_size(mask);
    for (uint32_t c = 0; c < 8; c++)
        sig[1 + c] = (uint8_t)(c <= n ? 0x48 + root + syn_scale_semi(mask, c) : 0);
    sig[0] = (uint8_t)(0x3C + root);
}

/* 1 if the layout the keyboard object holds now is the wanted one (read from the object, nothing remembered): a
 * slicing track holds the slice layout whatever its bit (the observer hook rescales it when slicing ends);
 * otherwise want 0 = the chromatic table (index 0x30 empty), want != 0 = the scaled table of sig. */
static uint32_t sc_holds(uint32_t kb, uint32_t want, const uint8_t *sig)
{
    if (!sc_chromatic(kb))
        return 1;
    const volatile uint8_t *k = (const volatile uint8_t *)(kb + KB_TABLE);
    if (!want)
        return k[0x30] == 0;
    for (uint32_t i = 0; i < 8; i++)
        if (k[0x28 + i] != sig[1 + i])
            return 0;
    return k[0x30] == sig[0] && k[KB_ROWS - KB_TABLE] == 1;
}

/* Reconcile the layouts with the settings, AT MOST ONE keyboard rebuild per Main pass (Main task, outside any
 * suspension: the original rebuild notifies). A scaled rebuild costs at most 50,652 instructions per pass
 * in all under the suspension, plus the notify; seven in one pass would delay the unit's MIDI clock output.
 * Each pass reads the current settings and every track's object afresh (no queued jobs, no remembered pointers): the
 * latest setting supersedes anything not yet built, and a reconstructed or project-changed keyboard is seen as it
 * is. The scan starts after the track rebuilt last (round robin), so all seven converge within seven passes of the
 * last change. */
static void sc_sync(void)
{
    volatile struct sc *c = sc();
    if (!c)
        return;
    uint32_t scale = sc_scale(), root = sc_root(), bits = sc_bits(), on = 1 + scale * 12 + root;
    uint8_t sig[9] = {0};
    if (bits)
        sc_sig(scale, root, sig);
    for (uint32_t i = 0, t = c->next % SC_TRACKS; i < SC_TRACKS; i++, t = t + 1 == SC_TRACKS ? 0 : t + 1) {
        uint32_t want = bits >> t & 1 ? on : 0, kb = sc_kb(t);
        if (!kb)
            continue;
        if (!sc_holds(kb, want, sig)) {
            if (want) {
                sc_build(kb, scale, root);
                c->builds++;
            } else {
                KB_REBUILD(kb);               /* back to the unscaled layout (chromatic or slice) */
                c->restores++;
            }
            c->applied[t] = (uint8_t)want;
            c->next = (uint8_t)(t + 1 == SC_TRACKS ? 0 : t + 1);
            return;
        }
        c->applied[t] = (uint8_t)want;
    }
}

/* noinline: one entry for the per-pass cost measurement (tests/test_scales.py test_one_rebuild_per_pass) */
__attribute__((noinline)) static void sc_main(struct state *s)
{
    struct tm *t = tm();
    if (t && !t->store)
        tm_attach(s);                         /* boot project: attach once its gates allow */
    sc_sync();
}

/* Keyboard layout change: original first; if it rebuilt the chromatic layout of a track whose bit is set, the scaled
 * table goes back at once, for that one keyboard (slice exit), with the current settings. */
#if defined(AD_LIVE) && AD_LIVE
static void ad_noslice_kb(uint32_t kb, uint32_t ctor);  /* notetap.c: track 8's keyboard stays chromatic */
#endif
uint32_t hook_kb_obs(uint32_t kb, uint32_t obs, uint32_t flags, uint32_t d)
{
    uint32_t r = KB_OBSERVER(kb, obs, flags, d);
#if defined(AD_LIVE) && AD_LIVE
    ad_noslice_kb(kb, 0);                     /* acts only on a positive match with track 8's value cell */
#endif
    volatile struct sc *c = sc();
    if (!c)
        return r;
    for (uint32_t t = 0; t < SC_TRACKS; t++) {
        if (sc_kb(t) != kb)
            continue;
        if ((sc_bits() >> t & 1) && (flags & 0x1000u) && sc_chromatic(kb)) {   /* rebuilt chromatic just now */
            uint32_t scale = sc_scale(), root = sc_root();
            sc_build(kb, scale, root);
            c->builds++;
            c->applied[t] = (uint8_t)(1 + scale * 12 + root);
        }
        break;
    }
    return r;
}

/* Tracks whose keyboard holds the scaled table of the CURRENT settings now (read from the objects): during a
 * convergence the tracks not yet rebuilt are not in the mask. */
static uint32_t sc_applied_mask(void)
{
    volatile struct sc *c = sc();
    uint32_t bits = sc_bits(), m = 0, kb;
    uint8_t sig[9];
    if (!c || !bits)
        return 0;
    sc_sig(sc_scale(), sc_root(), sig);
    for (uint32_t t = 0; t < SC_TRACKS; t++)
        if ((bits >> t & 1) && (kb = sc_kb(t)) && sc_chromatic(kb) && sc_holds(kb, 1, sig))
            m |= 1u << t;
    return m;
}

/* ---- SysEx 0E (debug) ----------------------------------------------------------------------------------------- */

static void sc_sysex(struct state *s, const uint8_t *m, uint32_t len)
{
    uint32_t sub = len >= 9 ? m[7] : 0xFF, status = TS_REFUSED;
    if (len == 9 && sub == 0x00)
        status = tm_attach(s) && tm()->store ? TS_OK : TS_REFUSED;
    else if (len == 12 && sub == 0x01)
        status = sc_edit(s, SC_OP_ALL | (uint32_t)m[8] << 8 | (uint32_t)m[9] << 16 | (uint32_t)m[10] << 24);
    else
        return;                               /* malformed: no reply */
    volatile struct sc *c = sc();
    struct tm *t = tm();
    volatile uint8_t r[16];
    header(r, 0x0E);
    r[7] = (uint8_t)sub;
    r[8] = (uint8_t)status;
    r[9] = t ? t->why : TM_NOSTORE;
    r[10] = (uint8_t)(c ? sc_status(c->sub) : SC_UNSET);
    r[11] = (uint8_t)sc_scale();
    r[12] = (uint8_t)sc_root();
    r[13] = (uint8_t)sc_bits();
    r[14] = (uint8_t)sc_applied_mask();
    r[15] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
