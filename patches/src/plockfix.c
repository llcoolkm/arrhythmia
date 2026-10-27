/* P-lock carry-over fix. Compiled after devkit_abi3.c and trackmodes.c; uses their tm() block, store_of(), in_sram()
 * and PLX_AT.
 *
 * Problem: a parameter lock leaves its value in the engine after the locked step, until the knob is turned. This
 * file keeps, per track and lock lane, whether the engine holds a lock value and the user's base value from before
 * it (for the 14-bit macros 2 and 3 both MSB and LSB), and sends the base back on the first pulse of a step whose lane
 * holds no lock in any of its six microsteps.
 *
 * All 12 lanes x 8 tracks: macros 1..8 and four further lockable parameters per track. Lanes 1 and 2 have LSB
 * companions, sent in the same order as a lock would.
 *
 * Hooks (Main task):
 *   hook_disp      parameter writes: classified by source (below)
 *   hook_macro     macro page encoder: while the turned lane holds a lock (and the track is not in lock edit), the
 *   hook_fx        FX page encoder:    original handler runs on the base, so its own relative mapping and clamping
 *                                      produce the new base; if it sends nothing, the lock-side values come back.
 *   hook_lockplay  after the lock player: the restore pass.
 *   hook_save_ser  after the project's channel values are written for a save: the base, not the lock value, goes into
 *                  the saved copy of every lane that holds a lock (the live values are not touched).
 *   pl_host        from the module drain (devkit_abi3.c) after host_param's channel write: a held lane takes the
 *                  written value as its base.
 *
 * Classification of a parameter write for lane (t, k) or its LSB companion:
 *   lock   lock playback, or a knob write while track t is in lock edit: the engine leaves the base. On the first
 *          write of the pair, base = the channel's own value(s) before the write.
 *   base   any other engine write: the engine now holds the base. An MSB/7-bit write ends the hold; an LSB write
 *          while held becomes the base LSB.
 *   none   lock record (writes the pattern and the display, not the engine).
 * Restore: on every pulse after the lock player, for each lane it processed whose step has no lock in all six
 * microsteps and that holds a lock: send the base the way a lock is sent (engine, display and MIDI out), for lanes
 * 1 and 2 followed by the base LSB. Steps with any microstep locked are untouched, and a pulse on which the player
 * handled a lock value waits for the next pulse, so microstep timing and interpolation are unchanged.
 *
 * State: struct plk, 8 x 12 bytes (bit 7 lock held, bits 0..6 base) + the track-modes generation seen, in the 104
 * spare bytes of the track-modes runtime block (TM_AT + 152 .. TM_AT + 256); struct plx (base LSBs of lanes 1/2,
 * macro 1 raw positions, the macro pages seen) in the 64 bytes PLX_AT below it. Both zeroed by hook_boot. A project
 * refresh (tm_apply) clears every lane: the engine then holds the loaded project's values.
 */

#define PL_DISP_VT      0x080696ACu     /* parameter write hook site */
#define PL_THUNK_VT     0x080696B8u     /* write source check */
#define PL_E_VT         0x08068F34u     /* engine value object check */
#define PL_F_VT         0x08068F44u     /* its observer check */
#define PL_PLAYER_VT    0x08068D24u     /* pattern player check */
#define PL_CH_VT        0x08069084u     /* channel parameter object check */
#define PL_IDTAB        0x08063708u     /* parameter id table */
#define PL_SER_AT       0x28D16u        /* saved channel values offset */
#define PL_SER_LEN      0x29u
#define ORIG_DISP       ((pl_disp_fn)0x08049B29u)
#define ORIG_LOCKPLAY   ((pl_play_fn)0x08055325u)
#define ORIG_SER        ((content_fn)0x08037A11u)
#define PL_ENCODE       ((pl_enc_fn)0x08046647u)    /* parameter encode */
#define PL_GET          ((pl_get_fn)0x080465FFu)    /* parameter read */
#define PL_R_CELL       0x20006EBCu     /* channel lookup root */
#define PL_LOCK         0x80u
#define PL_MACRO_VT     0x08067E34u     /* macro page check */
#define PL_FX_VT        0x0806833Cu     /* FX page check */
#define ORIG_MACRO      ((pl_knob_fn)0x08058161u)
#define ORIG_FX         ((pl_knob_fn)0x0805F2F9u)

typedef uint32_t (*pl_disp_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef void (*pl_play_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef uint32_t (*pl_lane_fn)(uint32_t, uint32_t, uint32_t);
typedef uint32_t (*pl_sink_fn)(uint32_t, uint32_t, uint32_t);
typedef uint32_t (*pl_enc_fn)(uint32_t, const uint32_t *, uint32_t);
typedef uint32_t (*pl_get_fn)(uint32_t, uint32_t);
typedef uint32_t (*pl_knob_fn)(uint32_t, uint32_t, uint32_t, uint32_t);

struct plk {
    uint8_t slot[8][12];                      /* bit 7: the engine holds a lock value; bits 0..6: base */
    uint32_t gen;                             /* tm()->gen last seen */
    uint8_t busy;                             /* our own restore send is in the dispatcher */
    uint8_t ad_clr;                           /* AD build: track 8's Clear + macro 3 is in the stock page (hook_macro) */
    uint8_t ad_step;                          /* AD build: track 8's latched step + 1 (0 = none), bit 7 = no note */
    uint8_t ad_pat;                           /* AD build: the latch's pattern + 1 (0 = none) */
};
_Static_assert(sizeof(struct tm) + sizeof(struct plk) <= TM_SIZE, "p-lock state must fit the 6013 block's spare");
#define PLK ((struct plk *)(TM_AT + sizeof(struct tm)))

struct plx {
    uint8_t lsb[8][2];                        /* while lane 1 / 2 is held: its base LSB (param 0x0E / 0x0F) */
    uint8_t raw0[8];                          /* while lane 0 is held: the page's raw position for the base,
                                                 0xFF unset (the page then reads the value) */
    uint32_t page[8];                         /* track t's macro page, as last seen by hook_macro (0 = not yet) */
    uint8_t pad[8];
};
_Static_assert(sizeof(struct plx) <= PLX_SIZE && PLX_AT + PLX_SIZE == TM_AT, "struct plx: the block below struct tm");
#define PLX ((struct plx *)PLX_AT)

static inline uint32_t rw(uint32_t a) { return *(volatile uint32_t *)a; }

/* Element i of a value cell (pure read). */
static uint32_t obs_el(uint32_t obs, uint32_t i)
{
    return rw(obs + 0xC + ((rw(obs + 4) >> i) & 1) * 4) + 4 * i;
}

static struct plk *plk(void)
{
    struct tm *t = tm();
    if (!t)
        return 0;
    struct plk *p = PLK;
    if (p->gen != t->gen) {                   /* a project refresh since we last looked */
        if (t->site != 0)
            for (uint32_t k = 0; k < sizeof p->slot; k++)
                (&p->slot[0][0])[k] = 0;
#if defined(AD_LIVE) && AD_LIVE
        p->ad_step = p->ad_pat = 0;           /* noteless-step latch: a new project or session */
#endif
        p->gen = t->gen;
    }
    return p;
}

/* Lane slot of a parameter id (t * 12 + k), -1 for ids that are not lock lanes. */
static int pl_slot(uint32_t id)
{
    if (id < 0x80)
        return (id & 15) < 8 ? (int)((id >> 4) * 12 + (id & 15)) : -1;
    if (id < 0x88)
        return (int)((id - 0x80) * 12 + 8);
    if (id < 0x90)
        return (int)((id - 0x88) * 12 + 9);
    if (id >= 0x91 && id < 0x99)
        return (int)((id - 0x91) * 12 + 10);
    if (id >= 0x99 && id < 0xA1)
        return (int)((id - 0x99) * 12 + 11);
    return -1;
}

static uint32_t pl_id(uint32_t t, uint32_t k)
{
    if (k < 8)
        return 16 * t + k;                    /* no table: the build keeps .text only */
    return (k == 8 ? 0x80u : k == 9 ? 0x88u : k == 10 ? 0x91u : 0x99u) + t;
}

/* As pl_slot, plus the LSB companions 16t + 14 / 15 of lanes 1 / 2 (*lsb = 1). */
static int pl_slot2(uint32_t id, uint32_t *lsb)
{
    *lsb = id < 0x80 && (id & 15) >= 14;
    return *lsb ? (int)((id >> 4) * 12 + (id & 15) - 13) : pl_slot(id);
}

/* The engine sink E of dispatcher D, checked; 0 if anything is unexpected. */
static uint32_t pl_engine(uint32_t D)
{
    if (!in_sram(D, 0x1C) || rw(D) != PL_DISP_VT)
        return 0;
    uint32_t th = rw(D + 0xC);
    if (!in_sram(th, 12) || rw(th) != PL_THUNK_VT)
        return 0;
    uint32_t E = rw(th + 4);
    if (!in_sram(E, 0x38) || rw(E) != PL_E_VT || rw(E + 4) != PL_F_VT || !in_sram(rw(E + 0x24), 0x34))
        return 0;
    return E;
}

/* Current engine value of id. */
static uint32_t pl_qa(uint32_t E, uint32_t id)
{
    uint32_t q = rw(E + 0x24), off, i;
    if (id < 0x80)
        off = (id >> 4) * 4, i = id & 15;
    else if (id < 0x88)
        off = 0x20, i = id - 0x80;
    else if (id < 0x90)
        off = 0x24, i = id - 0x88;
    else if (id < 0x99)
        off = 0x2C, i = id - 0x91;
    else
        off = 0x30, i = id - 0x99;
    uint32_t o = rw(q + off);
    if (!in_sram(o, 0x14))
        return 0;
    return *(volatile uint8_t *)obs_el(o, i) & 0x7F;
}

/* Track t's channel parameter object (group 0x10 + t), checked; 0 if unexpected. */
static uint32_t pl_channel(uint32_t R, uint32_t t)
{
    if (!in_sram(R, 0x220))
        return 0;
    uint32_t ch = rw(R + 0x1A4 + 4 * (0x10 + t));
    if (!in_sram(ch, 0x14) || rw(ch) != PL_CH_VT || *(volatile uint8_t *)(ch + 8) != 0x10 + t)
        return 0;
    return ch;
}

/* The engine's current value of a lane id: the channel's own parameter, or the engine value cell if the channel does
 * not check out. */
static uint32_t pl_now(uint32_t E, uint32_t id, uint32_t t)
{
    uint32_t ch = pl_channel(rw(PL_R_CELL), t), e = PL_IDTAB + 4 * id;
    if (ch && *(volatile uint8_t *)e == 0x10 + t)
        return PL_GET(ch, *(volatile uint16_t *)(e + 2)) & 0x7F;
    return pl_qa(E, id);
}

/* Track t's lock-edit flag (step held for lock editing). */
static uint32_t pl_editing(uint32_t E, uint32_t t)
{
    uint32_t F = E + 4, arr = rw(F + 0x30), stride = rw(F + 0x28);
    if (!in_sram(arr, 4) || stride > 0x100)
        return 0;
    uint32_t it = rw(arr + t * stride);
    if (!in_sram(it, 0x28))
        return 0;
    uint32_t x = rw(it + 0x24);
    if (!in_sram(x, 0x14))
        return 0;
    return *(volatile uint8_t *)obs_el(x, 0);
}

/* Track t's macro page as hook_macro saw it, checked; 0 if none. */
static uint32_t pl_page(uint32_t t)
{
    uint32_t pg = PLX->page[t];
    return in_sram(pg, 0x68) && rw(pg) == PL_MACRO_VT && *(volatile uint16_t *)(pg + 0x44) == 16 * t ? pg : 0;
}

/* First lock-class write of a lane (or its LSB companion): the engine still holds the base, keep it. Macro 1 also
 * keeps the page's raw position, which while nothing is held is the page's own for the base (it is set
 * on every outside change); not on src 1, which comes from inside the page's own handler (lock edit). */
static void pl_capture(struct plk *p, uint32_t E, uint32_t t, uint32_t k, uint32_t src)
{
    uint8_t *s = &p->slot[t][k];
    if (*s & PL_LOCK)
        return;
    *s = (uint8_t)(PL_LOCK | pl_now(E, pl_id(t, k), t));
    uint32_t pg = k == 0 && src != 1 ? pl_page(t) : 0;
    if (k == 0)
        PLX->raw0[t] = pg ? *(volatile uint8_t *)(pg + 0x4E) : 0xFF;
    else if (k <= 2)
        PLX->lsb[t][k - 1] = (uint8_t)pl_now(E, 16 * t + 13 + k, t);
}

#ifndef LEGACY_FM206_ARM
#define LEGACY_FM206_ARM 0              /* original VERSION 206 controls, only for its rebuild profile */
#endif

#if defined(AD_LIVE) && AD_LIVE
/* AD build: Clear + macro 3 on track 8 resets Length to 64/0 instead of the stock default 127/127. The macro page
 * sends the reset through the dispatcher as a turn does (src 1, LSB id 16t + 15, then MSB 16t + 2); while hook_macro
 * has marked the call as track 8's Clear + macro 3, exactly those two writes take 64 / 0 here, before the stock
 * dispatcher, so the channel, value cell, display, MIDI out and the p-lock fix see the one value. A write in lock
 * edit (Rec + held step: the preview of the lock the page records with src 2) is lock-class and stays stock, as do
 * src 2 and the lane clear (anticlockwise).
 * FM_BANKVIEW builds only (0.2, labels 7304 / 7306, where track 8's macro 6 is FM depth): Clear + macro 6 resets FM
 * depth to 96 (AD_CLR_M6 = AD_M6_NEW) instead of the stock 0, the same way (src 1, id 16t + 5, stock value 0). Every
 * other AD build (7251 / 7261 and the 7280..7302 prototypes; historical profiles keep their existing macro 6 meaning)
 * compiles the macro-3-only code below, byte for byte as before. */
#define AD_CLR_T        7u              /* track 8 */
#if defined(FM_BANKVIEW) && FM_BANKVIEW && !LEGACY_FM206_ARM
#define AD_CLR_M6       96u             /* track 8 macro 6 (FM depth) default: matches AD_M6_NEW (notetap.c) */
#define AD_CLR_KNOB(k)  ((k) == 2 || (k) == 5)
static uint32_t ad_clr_value(uint32_t D, uint32_t id, uint32_t v, uint32_t src)
{
    uint32_t E;
    if (src != 1 || (E = pl_engine(D)) == 0 || pl_editing(E, AD_CLR_T))
        return v;
    if (v == 127u && (id == 16u * AD_CLR_T + 2u || id == 16u * AD_CLR_T + 15u))
        return id == 16u * AD_CLR_T + 2u ? 64u : 0u;
    if (v == 0u && id == 16u * AD_CLR_T + 5u)
        return AD_CLR_M6;
    return v;
}
#else
#define AD_CLR_KNOB(k)  ((k) == 2)
static uint32_t ad_clr_value(uint32_t D, uint32_t id, uint32_t v, uint32_t src)
{
    uint32_t E;
    if (src != 1 || v != 127u || (id != 16u * AD_CLR_T + 2u && id != 16u * AD_CLR_T + 15u) ||
        (E = pl_engine(D)) == 0 || pl_editing(E, AD_CLR_T))
        return v;
    return id == 16u * AD_CLR_T + 2u ? 64u : 0u;
}
#endif
#endif

uint32_t hook_disp(uint32_t D, uint32_t id, uint32_t v, uint32_t src)
{
    struct plk *p = plk();
#if defined(AD_LIVE) && AD_LIVE
    if (p && p->ad_clr)
        v = ad_clr_value(D, id, v, src);
#endif
    uint32_t lsb, E = 0, t = 0, k = 0, fine = 0;
    int sl = pl_slot2(id, &lsb);
    if (p && !p->busy && sl >= 0 && (E = pl_engine(D)) != 0) {
        t = (uint32_t)sl / 12, k = (uint32_t)sl % 12;
        uint8_t *s = &p->slot[t][k];
        if (src == 3 || ((src == 0 || src == 1) && pl_editing(E, t)))
            pl_capture(p, E, t, k, src);
        else if (src == 0 || src == 1 || src == 4) {
            if (!lsb)
                *s = 0;                       /* the engine now holds the base: nothing to restore */
            else
                fine = *s & PL_LOCK;          /* a base LSB while held: the hold lasts until its MSB */
        }
    }
    uint32_t r = ORIG_DISP(D, id, v, src);
    if (fine)
        PLX->lsb[t][k - 1] = (uint8_t)pl_now(E, id, t);
    return r;
}

/* The display value cell of lane id. */
static uint32_t pl_qb(uint32_t E, uint32_t off)
{
    uint32_t l = rw(E + 0x28);
    if (!in_sram(l, 0x34))
        return 0;
    uint32_t o = rw(l + off);
    return in_sram(o, 0x14) && in_sram(rw(o + 0xC), 0x40) && in_sram(rw(o + 0x10), 0x40) ? o : 0;
}

static inline volatile uint8_t *pl_el(uint32_t obs, uint32_t i) { return (volatile uint8_t *)obs_el(obs, i); }

/* The engine behind a page's src-1 thunk, if the track is not in lock edit and the thunk checks out. */
static uint32_t pl_page_engine(uint32_t th, uint32_t t)
{
    if (!in_sram(th, 12) || rw(th) != PL_THUNK_VT || *(volatile uint8_t *)(th + 8) != 1)
        return 0;
    uint32_t E = pl_engine(rw(th + 4));
    return E && !pl_editing(E, t) ? E : 0;
}

#if defined(AD_LIVE) && AD_LIVE
#define PL_MACRO        pl_macro        /* hook_macro below marks track 8's Clear + macro 3 first */
static uint32_t pl_macro(uint32_t page, uint32_t k, uint32_t delta, uint32_t d);
#else
#define PL_MACRO        hook_macro
#endif

/* Macro page knob k of track t: run the original handler on the base (and, macro 1, its raw position), restore the
 * lock-side values if it wrote nothing. */
uint32_t PL_MACRO(uint32_t page, uint32_t k, uint32_t delta, uint32_t d)
{
    struct plk *p = plk();
    if (!p || p->busy || k > 7 || !in_sram(page, 0x68) || rw(page) != PL_MACRO_VT)
        return ORIG_MACRO(page, k, delta, d);
    uint32_t id0 = *(volatile uint16_t *)(page + 0x44), t = id0 >> 4;
    if ((id0 & 15) || t > 7)
        return ORIG_MACRO(page, k, delta, d);
    PLX->page[t] = page;
    uint8_t *s = &p->slot[t][k];
    uint32_t E, q;
    if (!(*s & PL_LOCK) || (E = pl_page_engine(rw(page + 0x14), t)) == 0 ||
        (q = pl_qb(E, 4 * t)) == 0 || q != rw(page + 0xC))
        return ORIG_MACRO(page, k, delta, d);
    volatile uint8_t *raw = (volatile uint8_t *)(page + 0x4E);
    uint8_t m = *pl_el(q, k), l = k == 1 || k == 2 ? *pl_el(q, 13 + k) : 0, c = *raw;
    *pl_el(q, k) = *s & 0x7F;
    if (k == 1 || k == 2)
        *pl_el(q, 13 + k) = PLX->lsb[t][k - 1];
    if (k == 0)
        *raw = PLX->raw0[t];
    uint32_t r = ORIG_MACRO(page, k, delta, d);
    if (*s & PL_LOCK) {                       /* no base write: the lock is still what the engine holds */
        *pl_el(q, k) = m;
        if (k == 1 || k == 2)
            *pl_el(q, 13 + k) = l;
        if (k == 0) {
            PLX->raw0[t] = *raw;
            *raw = c;
        }
    }
    return r;
}

#if defined(AD_LIVE) && AD_LIVE
/* Track 8's macro page, knob 3 (k = 2, Length; FM_BANKVIEW builds also knob 6, k = 5, FM depth), with Clear held (the
 * page's cell + 0x20, element 0 bit 0): every dispatcher write the page makes in this call is the Clear reset (Clear
 * sends nothing else through the dispatcher), so hook_disp gives it Length 64/0 (FM_BANKVIEW: or macro 6 96)
 * (ad_clr_value). */
uint32_t hook_macro(uint32_t page, uint32_t k, uint32_t delta, uint32_t d)
{
    struct plk *p = plk();
    uint32_t c = in_sram(page, 0x68) ? rw(page + 0x20) : 0;
    uint32_t arm = p && AD_CLR_KNOB(k) && c && rw(page) == PL_MACRO_VT && *(volatile uint16_t *)(page + 0x44) == 16u * AD_CLR_T &&
                   in_sram(c, 0x14) && in_sram(rw(c + 0xC), 4) && in_sram(rw(c + 0x10), 4) && (*pl_el(c, 0) & 1);
    if (arm)
        p->ad_clr = 1;
    uint32_t r = pl_macro(page, k, delta, d);
    if (arm)
        p->ad_clr = 0;
    return r;
}
#endif

/* FX page knob k = track k, lane 10 or 11: the same on its display value. */
uint32_t hook_fx(uint32_t page, uint32_t k, uint32_t delta, uint32_t d)
{
    struct plk *p = plk();
    if (!p || p->busy || k > 7 || !in_sram(page, 0x48) || rw(page) != PL_FX_VT || !in_sram(rw(page + 0x20), 0x14))
        return ORIG_FX(page, k, delta, d);
    uint32_t bank = *pl_el(rw(page + 0x20), 0) != 0;
    uint8_t *s = &p->slot[k][bank ? 11 : 10];
    uint32_t E, q;
    if (!(*s & PL_LOCK) || (E = pl_page_engine(rw(page + 0x2C), k)) == 0 ||
        (q = pl_qb(E, bank ? 0x30 : 0x2C)) == 0 || q != rw(page + (bank ? 0x1C : 0x18)))
        return ORIG_FX(page, k, delta, d);
    uint8_t m = *pl_el(q, k);
    *pl_el(q, k) = *s & 0x7F;
    uint32_t r = ORIG_FX(page, k, delta, d);
    if (*s & PL_LOCK)
        *pl_el(q, k) = m;
    return r;
}

/* Module host_param (drain, after the parameter write): the value a held lane's engine now holds is its base. */
static void pl_host(uint32_t t, uint32_t idx)
{
    struct plk *p = plk();
    uint32_t ch = p && t < 8 ? pl_channel(rw(PL_R_CELL), t) : 0;
    if (!ch)
        return;
    if (idx == 0x0E || idx == 0x0F) {
        if (p->slot[t][idx - 0x0D] & PL_LOCK)
            PLX->lsb[t][idx - 0x0E] = (uint8_t)(PL_GET(ch, idx) & 0x7F);
        return;
    }
    int k = idx >= 6 && idx <= 13 ? (int)idx - 6 : idx == 0x12 ? 8 : idx == 0x13 ? 9 : idx == 0x10 ? 10 :
            idx == 0x11 ? 11 : -1;
    if (k >= 0 && (p->slot[t][k] & PL_LOCK)) {
        p->slot[t][k] = (uint8_t)(PL_LOCK | (PL_GET(ch, idx) & 0x7F));
        if (k == 0)
            PLX->raw0[t] = 0xFF;
    }
}

static void pl_send(struct plk *p, uint32_t sink, uint32_t id, uint32_t v)
{
    p->busy = 1;
    ((pl_sink_fn)rw(rw(sink)))(sink, id, v);
    p->busy = 0;
}

#if defined(AD_LIVE) && AD_LIVE
/* AD build: track 8's attack / decay lock lanes (ids 0x71 Start, 0x72 Length) do not play on a step without a note.
 * Stock plays a step's locks whether or not it holds a note; removing a note keeps its locks, and with global A/D a
 * lock on a noteless step reshapes the notes still sounding. Such a step now behaves as one without A/D locks (as
 * after Clear + step): for that call the stock lock player reads the step's A/D lane bytes as 0xFF through a view
 * (ad_lane; the store is not touched; a lock carried from an earlier noted step still plays) and the restore pass
 * below treats them as unlocked, so a held lock gets its base back and nothing new becomes the base.
 * "Has a note", latched at the step's pulse 0 (or the first pulse of it the player handles): its microstep mask
 * (byte 2, any of the six microsteps; probability is not consulted, as stock plays a probability-skipped step's
 * locks) or a sequencer note of the track still within its gate (a tie from an earlier step: the player's pending
 * note-off list head, + 0x8A, set at the step's pulse 0 only by an earlier note). Playback only: knobs, CC,
 * host_param, lock edit and its preview, lock recording and save do not pass here. */
#define AD_T8_ID0       0x70u
static uint32_t ad_quiet_lanes(struct plk *p, uint32_t player, uint32_t pattern, uint32_t step, uint32_t pulse,
                               uint32_t lanes, uint32_t ids)
{
    if (*(volatile uint16_t *)ids != AD_T8_ID0 || pattern > 7)
        return 0;
    uint32_t row = rw(player + 0x50), sb = row + pattern * 0xA28u + step * 8u;
    if (!in_sram(sb, 8))
        return 0;
    if (pulse == 0 || (p->ad_step & 0x3F) != step + 1 || p->ad_pat != pattern + 1) {
        /* the step's pulse 0, or the first pulse of this (pattern, step) the player handles */
        uint32_t note = *(volatile uint8_t *)(sb + 2) & 0x3F, tie = *(volatile uint8_t *)(player + 0x8A) != 0xFF;
        p->ad_step = (uint8_t)((step + 1) | (note || tie ? 0 : 0x80));
        p->ad_pat = (uint8_t)(pattern + 1);
    }
    if (!(p->ad_step & 0x80))
        return 0;
    uint32_t m = 0;
    for (uint32_t k = 0; k < lanes; k++) {
        uint32_t id = *(volatile uint16_t *)(ids + 2 * k);
        if (id == AD_T8_ID0 + 1 || id == AD_T8_ID0 + 2)
            m |= 1u << k;
    }
    return m;
}
#endif

#if defined(AD_LIVE) && AD_LIVE
/* The player's vtable while a quiet step plays: a copy of the stock one (with the two words before it) whose lane
 * accessor is ad_lane; the call's state follows it. */
struct ad_view {
    uint32_t vt[8];                           /* [0..1] the words before the stock vtable, [2..7] its six entries */
    uint32_t orig, quiet, step, pattern, seen;
    uint8_t ff[8];
};

static uint32_t __attribute__((used, noinline)) ad_lane(uint32_t player, uint32_t pattern, uint32_t lane)
{
    struct ad_view *v = (struct ad_view *)(rw(player) - 8);
    uint32_t b = 1u << (lane & 31);
    if (lane < 12 && (v->quiet & b) && pattern == v->pattern && !(v->seen & b)) {
        v->seen |= b;                         /* the player reads the step's bytes at + step * 6 + 0..5 only */
        return (uint32_t)v->ff - v->step * 6u;
    }
    return ((pl_lane_fn)rw(v->orig + 0x14))(player, pattern, lane);
}

static uint32_t ad_lane_addr(void)
{
    uint32_t a;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\tb.w ad_lane\n2:" : "=r"(a));
    return a | 1u;
}
#endif

void hook_lockplay(uint32_t player, uint32_t pattern, uint32_t step, uint32_t pulse)
{
#if defined(AD_LIVE) && AD_LIVE
    struct plk *p = plk();
    uint32_t quiet = 0;
    if (p && in_sram(player, 0x150) && rw(player) == PL_PLAYER_VT && step <= 31) {
        uint32_t ids0 = rw(player + 0x40), n0 = *(volatile uint8_t *)(player + 0x3C);
        if (n0 <= 12 && ((ids0 >= FLASH_START && ids0 <= FLASH_END - 24) || in_sram(ids0, 24)))
            quiet = ad_quiet_lanes(p, player, pattern, step, pulse, n0, ids0);
    }
    /* For this call the stock player reads this step's A/D lanes through a view: its lane accessor (vtable slot 5)
     * returns, for the first read of a quiet lane in this pattern, a pointer whose step bytes are six 0xFF on our
     * stack. The project store is never written. A lock carried from an earlier, noted step (rates 0..2:
     * player + 0x48) still plays as on stock, and no carry is taken from this step. */
    struct ad_view v;
    uint32_t vt0 = rw(player);
    if (quiet) {
        for (uint32_t i = 0; i < 8; i++)
            v.vt[i] = rw(vt0 - 8 + 4 * i);
        v.vt[7] = ad_lane_addr();
        v.orig = vt0, v.quiet = quiet, v.step = step, v.pattern = pattern, v.seen = 0;
        for (uint32_t m = 0; m < 8; m++)
            v.ff[m] = 0xFF;
        *(volatile uint32_t *)player = (uint32_t)&v.vt[2];
    }
    ORIG_LOCKPLAY(player, pattern, step, pulse);
    if (quiet)
        *(volatile uint32_t *)player = vt0;
#else
    ORIG_LOCKPLAY(player, pattern, step, pulse);
    struct plk *p = plk();
#endif
    if (!p || !in_sram(player, 0x48) || rw(player) != PL_PLAYER_VT || step > 31)
        return;
    uint32_t last = rw(player + 0x4C);        /* u16 per lane: what the player handled this pulse */
    uint32_t ids = rw(player + 0x40), sink = rw(player + 0x2C), lanes = *(volatile uint8_t *)(player + 0x3C);
    if (!((ids >= FLASH_START && ids <= FLASH_END - 24) || in_sram(ids, 24)) || !in_sram(sink, 12) || lanes > 12 ||
        !in_sram(last, 24))
        return;
    int sl0 = pl_slot(*(volatile uint16_t *)ids);   /* lane 0 = 16t: all of track t's lanes are p->slot[t] */
    uint32_t any = 0;
    if (sl0 >= 0)
        for (uint32_t k = 0; k < 12; k++)
            any |= p->slot[sl0 / 12][k];
    if (sl0 >= 0 && !(any & PL_LOCK))
        return;                               /* cheap exit: nothing held on this track */
    uint32_t en = rw(obs_el(rw(player + 0x30), 0));
    uint32_t sup = rw(obs_el(rw(player + 0x34), 0));
    uint32_t all = *(volatile uint8_t *)obs_el(rw(player + 0x38), 0);
    uint32_t base_id = *(volatile uint16_t *)(player + 0x44);
    for (uint32_t k = 0; k < lanes; k++) {
        uint32_t id = *(volatile uint16_t *)(ids + 2 * k);
        int sl = pl_slot(id);
        if (sl < 0)
            continue;
        uint8_t *s = &p->slot[0][0] + sl;
        if (!(*s & PL_LOCK) || !((en >> k) & 1) || (!all && ((sup >> k) & 1)))
            continue;                         /* nothing held, or the player skipped this lane */
        if (*(volatile uint16_t *)(last + 2 * k) != 0xFFFF)
            continue;                         /* a lock value played on this pulse (at rates 0..2 a carried microstep
                                                 of the previous step lands on this step's pulse 0): not yet */
        uint32_t a = ((pl_lane_fn)rw(rw(player) + 0x14))(player, pattern, k);
        if (!in_sram(a, 0xC0))
            continue;
        uint32_t locked = 0;
        for (uint32_t m = 0; m < 6; m++)
            locked |= *(volatile uint8_t *)(a + step * 6 + m) != 0xFF;
#if defined(AD_LIVE) && AD_LIVE
        if ((quiet >> k) & 1)
            locked = 0;                       /* a noteless step's A/D locks count as none */
#endif
        if (locked)
            continue;                         /* a locked step: unchanged, microsteps included */
        uint32_t b = *s & 0x7F;
        *s = 0;
        pl_send(p, sink, id, b);
        if (id == base_id + 1 || id == base_id + 2)     /* MSB, then its LSB, as a lock */
            pl_send(p, sink, base_id + 13 + (id - base_id), PLX->lsb[sl / 12][id - base_id - 1]);
    }
}

/* Save: after the channel values are written, the saved copy of each lane holding a lock gets the base instead. */
static void pl_put(uint32_t ch, uint32_t param, uint32_t buf, uint32_t v)
{
    uint32_t d = rw(ch + 4) + 8 * param, len = rw(ch + 0x10);
    if (len > PL_SER_LEN || (d & 1) || d < FLASH_START || d >= FLASH_END)
        return;
    uint32_t off = *(volatile uint16_t *)d;
    if (off + 2 > len)
        return;
    uint32_t bp = buf;
    PL_ENCODE(d, &bp, v);
}

uint32_t hook_save_ser(uint32_t P, uint32_t b, uint32_t c, uint32_t d)
{
    uint32_t r = ORIG_SER(P, b, c, d);
    struct plk *p = plk();
    uint32_t s = store_of(P), R = s ? rw(P + 0x54) : 0;
    if (!p || !in_sram(R, 0x220))
        return r;
    for (uint32_t t = 0; t < 8; t++) {
        uint32_t ch = pl_channel(R, t);
        if (!ch)
            continue;
        uint32_t buf = s + PL_SER_AT + PL_SER_LEN * t;
        for (uint32_t k = 0; k < 12; k++) {
            uint8_t v = p->slot[t][k];
            if (!(v & PL_LOCK))
                continue;
            uint32_t id = pl_id(t, k), e = PL_IDTAB + 4 * id;
            if (*(volatile uint8_t *)e != 0x10 + t)
                continue;
            pl_put(ch, *(volatile uint16_t *)(e + 2), buf, v & 0x7F);
            if (k == 1 || k == 2) {           /* the 14-bit macros: the base LSB on id 16t + 14 / 15 */
                uint32_t e2 = PL_IDTAB + 4 * (16 * t + 13 + k);
                if (*(volatile uint8_t *)e2 == 0x10 + t)
                    pl_put(ch, *(volatile uint16_t *)(e2 + 2), buf, PLX->lsb[t][k - 1]);
            }
        }
    }
    return r;
}
