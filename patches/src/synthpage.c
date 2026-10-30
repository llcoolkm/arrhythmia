/* Track-8 settings page. Included after devkit_abi3.c (SYNTHPAGE blocks: route() and hook_frame() call in here,
 * hook_boot zeroes struct pg), trackmodes.c (TM_EDIT: tm_edit) and plockfix.c. Settings codec: synth_settings.h
 * (shared with the note tap, notetap.c).
 *
 * Gesture: Shift + Track 8 opens the page; Shift + Track 8 again closes it. Both edges of the Track 8 press are
 * swallowed (tombstone), so the normal UI sees neither. Shift itself always reaches the normal UI (route()).
 *
 * Arbitration:
 *   module / picker / lightshow  the page opens only while s->mode == 0. While a module runs (owned or not), the
 *                                picker is open or a lightshow runs, Shift + Track 8 keeps its devkit routing (module
 *                                if it owns the controls, swallowed by the picker, else normal). A module, the picker
 *                                or a lightshow that starts while the page is open (SysEx 04/05/06/0A, 01..03) closes
 *                                the page at the next event or LED frame: the page never draws over, takes input from
 *                                or stops a module, its clock, frame or note queue, and never touches ITCM.
 *   while open                   pads, encoders and every button except Shift, Play and Rec go to the page; Play and
 *                                Rec pass through (audition while editing; so the store is not "tail only" while
 *                                open). Shift + Sample Rec is consumed: the picker cannot open over the page.
 *   pots                         always normal (route() returns 0 for pots before anything here).
 * Press recipients are latched (devkit-mode rule): a release or pad pressure follows its press. A press the page took
 * gets a tombstone (pad_eat / btn_eat), so its release reaches nobody, also after the page closed; a control pressed
 * before the page opened (normal UI or module) keeps its recipient, and its release and pressure reach it while the page
 * is open (route() runs follow() before anything here). Project load/clear/pack changes do not close the page: it
 * shows the current project's settings at every frame.
 *
 * Layout (rows top to bottom; dim = option, bright = in force):
 *   row 1  pads 0..3   notes per press 1..4 (white); pads 4..7 dark (reserved: presets, V8)
 *   row 2  pads 8..13  chord type Maj, Min, Sus2, Sus4, 7, m7 (orange; shown dimmer while In key is on)
 *          pad 14      In key on/off (green)            pad 15  minor scale on/off (violet)
 *   row 3  pads 16..19 voicing root, inv 1, inv 2, spread (cyan)
 *          pads 20, 21 waveform saw / pulse (magenta)   pad 22 dark   pad 23  MIDI poly on/off (red)
 *   row 4  pads 24..28 octave -2..+2 (blue; pad 26 = 0); pads 29..31 dark
 *   Shift held: rows 3 and 4 are a keyboard for the key root: row 4 pads 24..30 C D E F G A B, row 3 pads 17, 18,
 *          20, 21, 22 C# D# F# G# A# (root bright yellow, other keys dim white); rows 1 and 2 as without Shift.
 *   macros 1, 2, 3 (encoders 0..2) attack, decay, level (deltas integrated, clamped 0..127); knob lights show the
 *          value; macros 4..8 are consumed and do nothing (their lights stay dark).
 *   PATCH CONTROLS: v0.1 ships one fixed DSP patch, so the waveform pads and macros 1..3 must not look functional.
 *          pg.hide_patch (set at boot from PG_HIDE_PATCH: 1 in arrhythmia.c, the header default is 0) hides them:
 *          pads 20/21
 *          dark and inert, macros 1..3 consumed without an edit, knob lights dark. The stored patch fields stay in the
 *          codec and in the block; nothing is removed, only the controls. SysEx/tests can poke the byte.
 *   Track 8 light magenta while open; red blink after a refused edit (BUSY, READONLY, REFUSED); Shift light white
 *          while held. Everything else dark.
 * FM VIEW (FM_PATCH builds only): Down shows it, Up returns to the view above (the chord view).
 *   rows 1-2 pads 0..15  ratio bank 0..15 (amber; bright = in force)
 *   row 3 pads 16, 17    algorithm serial4, dual_stack (cyan); pads 18, 19 dark and inert (algorithms not offered)
 *   every other pad dark and inert; Shift held: the scale picker and root keyboard exactly as in the chord view.
 *   macros 1..4: feedback, modulator attack, decay, floor (0..127; knob lights 1, 4, 57, 60 show the stored value);
 *   macros 5..8 consumed, no edit. Encoder deltas are summed in struct pg and written by ONE edit (one CRC, about
 *   0.23 ms) at most every PG_FM_PACE_MS, from the Main pass (pg_fm_tick), never per event.
 *   Up light dim in the FM view, Down light dim in the chord view (the way to the other view).
 *   The values live in patch slot 7 codec 2 (synth_settings.h); every note captures them at its press.
 * FM_BANKVIEW (label 7304): no FM view. Family and algorithm are chosen in track 8's
 *   Sample view (notetap.c); macros 1..4 above act on the single (chord) view, whose own macros are compiled out
 *   (PG_TRIM), with the same coalescing, owner and save rules and knob lights; Up and Down are consumed and do
 *   nothing, their lights dark; pg.view stays 0.
 * Every edit is one tm_edit() call (T1 path: BUSY first, live-tail check, CRC), made at once from the input hook
 * (Main task, inside event()'s suspension; tm_edit nests its own). The read-modify-write of the field happens inside
 * tm_edit's gate, on the live block, so a load or save in flight is never written and a refused edit changes nothing.
 *
 * State: struct pg (PG_SIZE bytes at PG_AT, below the track-modes block; hook_boot lowers the heap limit to PG_AT).
 */
#include "synth_settings.h"

#define BTN_TRACK8      0x07u
#define BTN_REC         0x10u
#define BTN_PLAY        0x11u
#define PG_FB_FRAMES    24u             /* LED frames of the refused-edit blink */
#define PG_SET          0u              /* edit ops (arg bits 16..23) */
#define PG_TOGGLE       1u
#define PG_ADD          2u
#define PG_PATCH        0x80u           /* arg bit 7: the field is a patch byte offset, else a record byte offset */
#if FM_PATCH
#define PG_FMSET        3u              /* op: FM field (codec 2 byte offset) = value */
#define PG_FMFLUSH      4u              /* op: add the summed macro deltas pg.acc[] to the FM fields SF_FB..SF_MF */
#define PG_FM_PACE_MS   20u             /* minimum spacing of the coalesced macro writes (policy value) */
#define BTN_DOWN        0x13u
#define BTN_UP          0x14u
#define PG_TICK         ((uint32_t (*)(void))0x08024069u)   /* RTOS tick (ms), as notetap.c's TICK */
#endif

struct pg {
    uint8_t open;                       /* the page is shown and takes input (only while s->mode == 0) */
    uint8_t fb;                         /* refused-edit blink, LED frames left */
    uint8_t res;                        /* TS_* of the last edit */
    uint8_t opens;                      /* times opened since boot (mod 256) */
    uint32_t edits, refused;            /* edits made / refused since boot */
    uint8_t hide_patch;                 /* 1: waveform pads and macros 1..3 hidden and inert (one fixed DSP patch) */
#if FM_PATCH
    uint8_t view;                       /* 0 the chord view, 1 the FM view */
    uint8_t pad[2];
    int8_t acc[4];                      /* summed deltas of macros 1..4 not yet written (feedback, mod A, D, floor) */
    uint32_t fm_t;                      /* tick of the last coalesced write */
    uint32_t acc_gen;                   /* tm()->gen (project applies) the pending acc[] belongs to: a load, clear or
                                           pack switch since cancels it, whatever the store address */
#if FM_BANKVIEW                         /* track 8's Sample view (notetap.c hook_sv_press / hook_sv_rel / sv_entry) */
    uint16_t sv_mask;                   /* physical sample pads 16..31 (bit pad - 16) whose press was inert: their
                                           release is swallowed too, whatever view or page it reaches */
    uint8_t sv_in;                      /* 1: track 8's Sample view was in front at the last Main pass (entered) */
    uint8_t sv_gen;                     /* tm()->gen (low byte) at that entry: a project apply re-enters */
#endif
#else
    uint8_t pad[3];
#endif
};
#ifndef PG_HIDE_PATCH
#define PG_HIDE_PATCH 0
#endif
_Static_assert(sizeof(struct pg) <= PG_SIZE, "page state must fit its block");
#if FM_BANKVIEW
_Static_assert(__builtin_offsetof(struct pg, acc_gen) == 24 && __builtin_offsetof(struct pg, sv_mask) == 28
               && sizeof(struct pg) == PG_SIZE, "FM_BANKVIEW: the Sample-view state fills the last 4 bytes of struct pg");
#endif
#define PG ((volatile struct pg *)PG_AT)
#if FM_PATCH
/* The owner of pending macro deltas: tm()->gen + 1 (every project apply, load, clear or pack switch, increments gen,
 * also when the new project lands at the same store address); 0 = no runtime copy, never an owner. */
__attribute__((noinline)) static uint32_t pg_gen(void)
{
    struct tm *t = tm();
    return t ? t->gen + 1u : 0u;
}
#endif
#if PG_HIDE_PATCH && TRIM_HIDE                  /* trim C2: hidden at compile time, the patch controls compiled out */
#define PG_TRIM 1
#else
#define PG_TRIM 0
#endif

static void pg_boot(void)               /* from hook_boot, block already zeroed */
{
    PG->hide_patch = PG_HIDE_PATCH;
}

/* The page is open and allowed: anything else in front (s->mode != 0) closes it. Caller holds the suspension. */
static uint32_t pg_live(struct state *s)
{
    volatile struct pg *g = PG;
    if (g->open && s->mode)
        g->open = 0;
    return g->open;
}

/* tm_edit callback: arg = field offset (| PG_PATCH) | (int8) value << 8 | op << 16. Runs on the live block with the
 * scheduler suspended, after the BUSY gate. */
static uint32_t pg_edit_cb(volatile uint8_t *b, uint32_t arg)
{
    uint32_t off = arg & 0x7F, op = (arg >> 16) & 0xFF;
    int32_t v = (int8_t)(arg >> 8);
#if FM_PATCH
    if (op >= PG_FMSET) {                     /* FM fields: codec 2, created over an unset or codec-1 slot */
        volatile struct pg *g = PG;
        if (op == PG_FMFLUSH && (!g->acc_gen || pg_gen() != g->acc_gen)) {
            for (uint32_t i = 0; i < 4; i++)  /* a project applied since the deltas: cancelled before the block is */
                g->acc[i] = 0;                /* read, created or written (the store may be a stale capture) */
            return TS_REFUSED;
        }
        struct tm *t = tm();                  /* b was captured before the suspension: a project applied in between */
        if (!t || b != (volatile uint8_t *)(t->store + TM_EXT))   /* (B owns the deltas, b is still A): A is */
            return TS_BUSY;                   /* never touched, the deltas stay for the next pass with B's block */
        volatile uint8_t *p = b + SYN_PATCH_AT;
        uint32_t st = syn_fm_status(p);
        if (st == SYN_UNKNOWN) {
            for (uint32_t i = 0; i < 4; i++)
                g->acc[i] = 0;
            return TS_READONLY;
        }
        if (st != SYN_V1)
            syn_fm_create(p);
#if FM_BANKVIEW                             /* no FMSET op: +2 and +3 are reserved, never edited */
        (void)off;
        (void)v;
        {
#else
        if (op == PG_FMSET) {
            p[off] = syn_fm_byte(off, v);
        } else {
#endif
            for (uint32_t i = 0; i < 4; i++) {
                p[SF_FB + i] = syn_fm_byte(SF_FB + i, syn_fm_get(p, SF_FB + i) + g->acc[i]);
                g->acc[i] = 0;
            }
        }
        return TS_OK;
    }
#endif
#if PG_TRIM
    if (arg & PG_PATCH)
        return TS_READONLY;
#else
    if (arg & PG_PATCH) {
        volatile uint8_t *p = b + SYN_PATCH_AT;
        uint32_t st = syn_patch_status(p);
        if (st == SYN_UNKNOWN || off < SP_WAVE || off >= SP_WAVE + SP_FIELDS)
            return TS_READONLY;
        int32_t cur = syn_patch_get(p, off);
        if (st == SYN_UNSET)
            syn_patch_create(p);
        p[off] = syn_patch_byte(off, op == PG_ADD ? cur + v : op == PG_TOGGLE ? !cur : v);
        return TS_OK;
    }
#endif
    volatile uint8_t *r = b + SYN_REC_AT;
    uint32_t st = syn_rec_status(r);
    if (st == SYN_UNKNOWN || off < SR_NPP || off >= SR_NPP + SR_FIELDS)
        return TS_READONLY;
    int32_t cur = syn_rec_get(r, off);
    if (st == SYN_UNSET)
        syn_rec_create(r);
    r[off] = syn_rec_byte(off, op == PG_ADD ? cur + v : op == PG_TOGGLE ? !cur : v);
    return TS_OK;
}

static void pg_edit(struct state *s, uint32_t field, int32_t v, uint32_t op)
{
    volatile struct pg *g = PG;
    uint32_t r = tm_edit(s, pg_edit_cb, field | ((uint32_t)v & 0xFF) << 8 | op << 16);
    g->res = (uint8_t)r;
    if (r == TS_OK) {
        g->edits++;
    } else {
        g->refused++;
        g->fb = PG_FB_FRAMES;
    }
}

#if SCALES
/* SCALES builds: scale and key root are project-wide (scales.c sub-record), edited through the same
 * tm_edit gate; the result is reported as for any page edit. */
static void pg_sc(struct state *s, uint32_t arg)
{
    volatile struct pg *g = PG;
    uint32_t r = sc_edit(s, arg);
    g->res = (uint8_t)r;
    if (r == TS_OK) {
        g->edits++;
    } else {
        g->refused++;
        g->fb = PG_FB_FRAMES;
    }
}
#endif

/* Root keyboard (Shift held): pad -> semitone 0..11, or -1. Row 4 white keys, row 3 black keys. */
static int32_t pg_key(uint32_t pad)
{
    switch (pad) {
    case 24: return 0;  case 17: return 1;  case 25: return 2;  case 18: return 3;  case 26: return 4;
    case 27: return 5;  case 20: return 6;  case 28: return 7;  case 21: return 8;  case 29: return 9;
    case 22: return 10; case 30: return 11;
    default: return -1;
    }
}

/* A pad press on the page. Caller holds the suspension. */
static void pg_pad(struct state *s, uint32_t pad)
{
#if SCALES
    if (s->shift) {                         /* rows 1-2: scale picker (pads 0..9), rows 3-4: key root keyboard */
        int32_t k = pad >= 16 ? pg_key(pad) : pad < SYN_SCALES ? (int32_t)pad : -1;
        if (k >= 0)
            pg_sc(s, (pad >= 16 ? SC_OP_ROOT : SC_OP_SCALE) | (uint32_t)k << 8);
        return;
    }
#if FM_PATCH && !FM_BANKVIEW
    if (PG->view) {                         /* FM view: banks, algorithms; every other pad inert */
        if (pad < FM_BANKS)
            pg_edit(s, SF_BANK, (int32_t)pad, PG_FMSET);
        else if (pad >= 16 && pad < 16 + FM_ALGOS)
            pg_edit(s, SF_ALGO, (int32_t)pad - 16, PG_FMSET);
        return;
    }
#endif
    if (pad == 15) {
        pg_sc(s, SC_OP_MINOR);
        return;
    }
#endif
    if (s->shift && pad >= 16) {
        int32_t k = pg_key(pad);
        if (k >= 0)
            pg_edit(s, SR_ROOT, k, PG_SET);
        return;
    }
    if (pad < 4)
        pg_edit(s, SR_NPP, (int32_t)pad + 1, PG_SET);
    else if (pad >= 8 && pad <= 13)
        pg_edit(s, SR_TYPE, (int32_t)pad - 8, PG_SET);
    else if (pad == 14)
        pg_edit(s, SR_INKEY, 0, PG_TOGGLE);
    else if (pad == 15)
        pg_edit(s, SR_SCALE, 0, PG_TOGGLE);
    else if (pad >= 16 && pad <= 19)
        pg_edit(s, SR_VOICING, (int32_t)pad - 16, PG_SET);
    else if ((pad == 20 || pad == 21) && !PG_TRIM && !PG->hide_patch)
        pg_edit(s, PG_PATCH | SP_WAVE, (int32_t)pad - 20, PG_SET);
    else if (pad == 23)
        pg_edit(s, SR_POLY, 0, PG_TOGGLE);
    else if (pad >= 24 && pad <= 28)
        pg_edit(s, SR_OCTAVE, (int32_t)pad - 26, PG_SET);
}

/* Button press (down) for the page, after route() has handled Shift and every release. Returns 1 if the normal UI
 * must not see it. Caller holds the suspension. */
static uint32_t pg_button(struct state *s, uint32_t index, uint32_t bit)
{
    volatile struct pg *g = PG;
    uint32_t open = pg_live(s);
    if (index == BTN_TRACK8 && s->shift && (open || s->mode == 0)) {
        g->open = (uint8_t)!open;
        if (!open)
            g->opens++;
        s->btn_eat |= bit;                  /* both edges of the gesture are nobody's */
        return 1;
    }
    if (!open || index == BTN_PLAY || index == BTN_REC)
        return 0;                           /* Play and Rec pass through; their releases follow */
    s->btn_eat |= bit;                      /* consumed: the release is swallowed, also after the page closed */
#if FM_PATCH && !FM_BANKVIEW
    if (index == BTN_DOWN || index == BTN_UP)
        g->view = index == BTN_DOWN;
#endif
#if SCALES
    if (index < SC_TRACKS)                  /* Track 1..7: that sample track's Note view in scale or chromatic */
        pg_sc(s, SC_OP_TOGGLE | index << 8);
#endif
    return 1;
}

/* Pad press for the page. Returns 1 if consumed. Caller holds the suspension. */
static uint32_t pg_pad_press(struct state *s, uint32_t index, uint32_t bit)
{
    if (!pg_live(s) || !bit)
        return 0;
    s->pad_eat |= bit;                      /* its pressure and release are swallowed */
    pg_pad(s, index);
    return 1;
}

/* Encoder event for the page. Returns 1 if consumed. Caller holds the suspension. */
static uint32_t pg_encoder(struct state *s, uint32_t knob, uint32_t delta)
{
    if (!pg_live(s))
        return 0;
    int32_t d = (int16_t)delta;
#if FM_PATCH
    if (FM_BANKVIEW || PG->view) {          /* summed here, written by pg_fm_tick (one CRC per PG_FM_PACE_MS) */
        if (knob < 4) {
            uint32_t gen = pg_gen();
            if (PG->acc_gen != gen) {           /* deltas left from an earlier project: dropped */
                for (uint32_t i = 0; i < 4; i++)
                    PG->acc[i] = 0;
                PG->acc_gen = gen;
            }
            int32_t x = PG->acc[knob] + d;
            PG->acc[knob] = (int8_t)(x < -127 ? -127 : x > 127 ? 127 : x);
        }
        return 1;
    }
#endif
    if (knob < 3 && d && !PG_TRIM && !PG->hide_patch)
        pg_edit(s, PG_PATCH | (SP_ATTACK + knob), d < -127 ? -127 : d > 127 ? 127 : d, PG_ADD);
    return 1;
}

#if FM_PATCH
/* Drop the pending sum of owner `own`, decided and cleared in one suspended step: an encoder event that re-owned the
 * sum since (pg_encoder, under event()'s suspension: acc reset, acc_gen = the new owner) keeps its delta. */
__attribute__((noinline)) static void pg_fm_drop(uint32_t own)
{
    volatile struct pg *g = PG;
    SUSPEND_ALL();
    if (g->acc_gen == own)
        for (uint32_t i = 0; i < 4; i++)
            g->acc[i] = 0;
    RESUME_ALL();
}

/* Main pass (hook_a1_tick): the pending macro deltas as one edit, at most every PG_FM_PACE_MS. A BUSY edit keeps them
 * for the next pass; READONLY / REFUSED drop them (the page blinks, as for any refused edit). Open or closed page.
 * Runs without the suspension: every drop goes through pg_fm_drop with the owner seen here. */
static void pg_fm_tick(void)
{
    struct state *s = region();
    volatile struct pg *g = PG;
    if (!s || !(g->acc[0] | g->acc[1] | g->acc[2] | g->acc[3]))
        return;
    uint32_t own = g->acc_gen;
    if (!own || pg_gen() != own) {            /* a project barrier since the deltas (load, clear, pack): cancelled */
        pg_fm_drop(own);
        return;
    }
    if ((int32_t)(PG_TICK() - g->fm_t) < (int32_t)PG_FM_PACE_MS)
        return;
    g->fm_t = PG_TICK();
    pg_edit(s, 0, 0, PG_FMFLUSH);
    if (g->res != TS_OK && g->res != TS_BUSY)
        pg_fm_drop(own);
}
#endif

#if FM_PATCH
/* The stock save of `store` is about to post its write job (hook_save_post, notetap.c): owned pending deltas go into
 * the store now, so the saved slot holds every completed knob edit. Inside the save, tm_edit would answer BUSY; this is
 * its body without that gate (the save itself is the operation in progress), as the save hooks edit the store. */
__attribute__((noinline)) static void pg_fm_save(uint32_t store)
{
    volatile struct pg *g = PG;
    struct tm *t = tm();
    if (!(g->acc[0] | g->acc[1] | g->acc[2] | g->acc[3]))
        return;
    volatile uint8_t *b = (volatile uint8_t *)(store + TM_EXT);
    SUSPEND_ALL();
    if (t && t->store == store && (t->why == TM_OK || (t->why == TM_EMPTY && all_zero(b, TM_EXT_LEN))) &&
            pg_edit_cb(b, PG_FMFLUSH << 16) == TS_OK) {
        if (t->why == TM_EMPTY) {
            wr32(b, TM_BLK_MAGIC);
            b[4] = TM_VERSION;
            b[5] = 0;
            b[6] = (uint8_t)TM_EXT_LEN;
            b[7] = (uint8_t)(TM_EXT_LEN >> 8);
        }
        wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
        t->why = TM_OK;
        t->writes++;
    }
    for (uint32_t i = 0; i < 4; i++)          /* written, cancelled (not owned) or refused: nothing stays pending */
        g->acc[i] = 0;
    RESUME_ALL();
}
#endif

/* The page's lights into s->leds (UI task, LED frame, caller holds the suspension; s->mode == 0, so the buffer is
 * not a module's). Reads the runtime copy of record 7 and the live patch slot. */
static void pg_leds(struct state *s)
{
    const uint8_t *t = pad_leds();
    struct tm *m = tm();
    const volatile uint8_t *rec = m && m->why == TM_OK ? m->rec[7] : 0;
    __attribute__((unused)) const volatile uint8_t *pat = m && m->why == TM_OK && m->store ? (const volatile uint8_t *)(m->store + TM_EXT +
                                                                                                 SYN_PATCH_AT) : 0;
    for (uint32_t i = 0; i < LEDS; i++)
        s->leds[i] = 0;
    int32_t npp = syn_rec_get(rec, SR_NPP), type = syn_rec_get(rec, SR_TYPE), inkey = syn_rec_get(rec, SR_INKEY);
    int32_t voicing = syn_rec_get(rec, SR_VOICING), oct = syn_rec_get(rec, SR_OCTAVE);
    for (uint32_t i = 0; i < 4; i++) {
        uint8_t v = (int32_t)i + 1 == npp ? BRIGHT : DIM;
        pad_rgb(s, t, i, v, v, v);
    }
    for (uint32_t i = 0; i < 6; i++) {
        uint8_t v = (int32_t)i == type ? (inkey ? 0x30 : BRIGHT) : (inkey ? 0x04 : DIM);
        pad_rgb(s, t, 8 + i, v, v >> 2, 0);
    }
    uint8_t v = inkey ? BRIGHT : DIM;
    pad_rgb(s, t, 14, 0, v, 0);
#if SCALES
    v = sc_scale() == SYN_MINOR ? BRIGHT : DIM;
    pad_rgb(s, t, 15, v >> 1, 0, v);
    uint32_t bits = sc_bits();             /* Track 1..7 lights: green (btn_1..btn_7 G, led-map.json), bright = scaled */
    for (uint32_t i = 0; i < SC_TRACKS; i++) {
        uint32_t g8 = i == 0 ? 45 : i == 1 ? 32 : i == 2 ? 111 : i == 3 ? 98 : i == 4 ? 167 : i == 5 ? 154 : 210;
        s->leds[g8] = (bits >> i & 1) ? BRIGHT : DIM;
    }
    if (s->shift) {                         /* scale picker, rows 1-2 (violet); rows 1-2 of the normal layout hidden */
        for (uint32_t i = 0; i < 16; i++)
            pad_rgb(s, t, i, 0, 0, 0);
        for (uint32_t i = 0; i < SYN_SCALES; i++) {
            v = i == sc_scale() ? BRIGHT : DIM;
            pad_rgb(s, t, i, v >> 1, 0, v);
        }
    }
    if (s->shift) {                         /* root keyboard */
        int32_t root = (int32_t)sc_root();
#else
    v = syn_rec_get(rec, SR_SCALE) ? BRIGHT : DIM;
    pad_rgb(s, t, 15, v >> 1, 0, v);
    if (s->shift) {                         /* root keyboard */
        int32_t root = syn_rec_get(rec, SR_ROOT);
#endif
        for (uint32_t pad = 16; pad < 32; pad++) {
            int32_t k = pg_key(pad);
            if (k >= 0)
                pad_rgb(s, t, pad, k == root ? BRIGHT : DIM, k == root ? BRIGHT : DIM, k == root ? 0 : DIM);
        }
    } else {
        for (uint32_t i = 0; i < 4; i++) {
            v = (int32_t)i == voicing ? BRIGHT : DIM;
            pad_rgb(s, t, 16 + i, 0, v, v);
        }
#if !PG_TRIM
        int32_t wave = syn_patch_get(pat, SP_WAVE);
        for (uint32_t i = 0; i < 2 && !PG->hide_patch; i++) {
            v = (int32_t)i == wave ? BRIGHT : DIM;
            pad_rgb(s, t, 20 + i, v, 0, v);
        }
#endif
        v = syn_rec_get(rec, SR_POLY) ? BRIGHT : DIM;
        pad_rgb(s, t, 23, v, 0, 0);
        for (uint32_t i = 0; i < 5; i++) {
            v = (int32_t)i - 2 == oct ? BRIGHT : DIM;
            pad_rgb(s, t, 24 + i, 0, 0, v);
        }
    }
    volatile struct pg *g = PG;
#if FM_BANKVIEW                             /* the single view: knob lights 1, 4, 57, 60 (macros 1..4, green); Up and
                                               Down dark (no other view) */
#if FB_MACRO1                               /* macro 1 is Tune: centred like the stock tune light (dim at 64, full
                                               at 0 and 127) */
    {
        int32_t tu = syn_fm_get(pat, SF_TUNE);
        s->leds[1] = (uint8_t)(((tu < 64 ? 63 - tu : tu - 64) << 1) & 0x7E);
    }
#else
    s->leds[1] = (uint8_t)(syn_fm_get(pat, SF_FB) & 0x7E);
#endif
    s->leds[4] = (uint8_t)(syn_fm_get(pat, SF_MA) & 0x7E);
    s->leds[57] = (uint8_t)(syn_fm_get(pat, SF_MD) & 0x7E);
    s->leds[60] = (uint8_t)(syn_fm_get(pat, SF_MF) & 0x7E);
#elif FM_PATCH
    if (g->view && !s->shift) {             /* FM view (Shift shows the chord view's scale and root layers) */
        int32_t bank = syn_fm_get(pat, SF_BANK), algo = syn_fm_get(pat, SF_ALGO);
        for (uint32_t i = 0; i < 32; i++) {
            v = (int32_t)i == bank || (int32_t)i - 16 == algo ? BRIGHT : DIM;
            if (i < FM_BANKS)
                pad_rgb(s, t, i, v, v >> 1, 0);
            else if (i < 16 + FM_ALGOS)
                pad_rgb(s, t, i, 0, v, v);
            else
                pad_rgb(s, t, i, 0, 0, 0);
        }
    }
    if (g->view) {                          /* knob lights 1, 4, 57, 60 (macros 1..4, green), Up light (55) */
        s->leds[1] = (uint8_t)(syn_fm_get(pat, SF_FB) & 0x7E);
        s->leds[4] = (uint8_t)(syn_fm_get(pat, SF_MA) & 0x7E);
        s->leds[57] = (uint8_t)(syn_fm_get(pat, SF_MD) & 0x7E);
        s->leds[60] = (uint8_t)(syn_fm_get(pat, SF_MF) & 0x7E);
        s->leds[55] = DIM;
    } else {
        s->leds[52] = DIM;                  /* Down light: the FM view is below */
    }
#endif
    /* knob lights: macro 1 (0, 1, 2), macro 2 (3, 4, 5), macro 3 (56, 57, 58) (docs/led-map.json), green by value */
    if (!PG_TRIM && !g->hide_patch) {
        s->leds[1] = (uint8_t)(syn_patch_get(pat, SP_ATTACK) & 0x7E);
        s->leds[4] = (uint8_t)(syn_patch_get(pat, SP_DECAY) & 0x7E);
        s->leds[57] = (uint8_t)(syn_patch_get(pat, SP_LEVEL) & 0x7E);
    }
    if (g->fb) {                            /* btn_8: 212, 213, 214 */
        g->fb--;
        s->leds[212] = (g->fb & 4) ? BRIGHT : 0;
    } else {
        s->leds[212] = BRIGHT;
        s->leds[214] = BRIGHT;
    }
    if (s->shift) {                         /* btn_Shift: 222, 223, 208 */
        s->leds[222] = DIM;
        s->leds[223] = DIM;
        s->leds[208] = DIM;
    }
}
