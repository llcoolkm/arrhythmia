/* Track-8 note source tap, chord expansion, ARM voice table, ring admission for every ARM writer. Included after
 * devkit_abi3.c, trackmodes.c and plockfix.c; uses their region(), tm(), SUSPEND_ALL/RESUME_ALL, SEM_TAKE/SEM_GIVE,
 * DELAY and the clock block. Wire format only in a1_cmd.c (voice_emit_* below are the one interface to it).
 *
 * Hooks (sites and original targets asserted by tools/build_lightshow.py):
 *   hook_ron / hook_roff       note on / off: the tap
 *   hook_nm_on / hook_nm_off   slice bypass for track 8
 *   hook_eng_on / hook_eng_off track 8's sample voice is not triggered
 *   hook_fo_eng / hook_fo_choke / hook_fo_edit  track 8 forced off = synth ALL OFF (Stop/mute, choke, sample edit;
 *                              AD build: Stop/mute is a release, release_all)
 *   hook_midi_alloff           MIDI CC 123 (AD build: a release)
 *   hook_mp_init / hook_mp_obs / hook_nm_obs / hook_save_post / hook_kb_new / hook_nv_col0 / hook_nv_col1 /
 *   hook_sv_press / hook_step_note / hook_oct_c / hook_xv_c   AD build: track 8 ignores slice mode (below)
 *   hook_ring_slot/_retrig/_on/_force/_rel  the five writes to the DSP note ring: admission
 *   hook_a1_tick               Main loop, after the timer service: defer drain, project/boot publication
 *   hook_a1_boot               first call of the Main task: activates the fixed block
 * With no block every hook passes through: track 8 stays a sample track.
 */
#include "synth_settings.h"
#include "a1_cmd.c"
#if AD_LIVE && defined(FM_207) && FM_207
#include "ad_life207.h"                 /* 0.2 profiles (VERSION 000207): the A10 attack, 10 ms to 8 s */
#elif AD_LIVE
#include "ad_life.h"                    /* every other AD profile: the original attack, 2 ms to 2 s */
#endif

#define A1_MAGIC        0x31544E41u     /* "ANT1" */
#ifdef A1_AT
/* Arrhythmia 0.1 (SYNTH=1): devkit_abi3.c (NOTETAP) reserves the fixed block A1_AT..A1_AT + A1_SIZE below struct pg;
 * hook_boot zeroes it with the other blocks and lowers the heap limit to A1_AT. The clock block's spare
 * (CLOCK_AT + 0x14..0x1F) belongs to o1wait.c. */
_Static_assert(A1_SIZE == 0x300u && A1_AT + A1_SIZE == PG_AT, "A1 block directly below struct pg");
#else
#if defined(O1WAIT) && (!defined(SYNTH) || SYNTH)
#error "A1's clock-block pointer (CLOCK_AT + 0x18..0x1F) overlaps O1's state (CLOCK_AT + 0x14..0x1F): define NOTETAP"
#endif
#define A1_PTR          (CLOCK_AT + 0x18u)  /* spare words of the 32-byte clock block (struct clk is 20 bytes) */
#define A1_PTR_CHK      (CLOCK_AT + 0x1Cu)
#define A1_SIZE         0x300u
#endif
#define A1_T            7u              /* track 8 */
#define A1_G            0x17u           /* its engine group */
#define A1_PATCH_START  0x0806A570u     /* router callers at or above this address are our own code (module drain) */
#define A1_PATCH_END    0x08080000u
#define NQ              32u             /* normal FIFO */
#define NQ_ONSET_RESERVE 8u             /* a chord is admitted only if its entries fit below NQ - 8 */
#define NINST           16u
#ifndef A1_PATCH
#define A1_PATCH        0               /* v0.1 has one fixed patch and no patch ops to the DSP. 1 builds the
                                           transaction code (emulation only); 0 links none of it */
#endif
/* The fixed AD lasts AD_BLOCKS executed DSP blocks (no patch ops can change it). The ARM has no
 * block clock, so a published voice is only ever an AGED CANDIDATE (preferred steal, never FREE) once at least
 * AD_BLOCKS * 2/3 ms + the ring backlog (15 blocks) have passed since its ring write: the DSP may have stalled or
 * overrun, so silence is never assumed and every steal emits KILL(v, gen) before the ONSET on the same voice.
 * Voices whose onset is still queued have no age (pub_at 0). */
#ifndef AD_BLOCKS              /* -DAD_BLOCKS=1216u only in the SCALES=0 check build (build_lightshow.py) */
#define AD_BLOCKS       3924u   /* T60 2 s */
#endif
#define AGE_TICKS       ((AD_BLOCKS + 15u) * 2u / 3u + 2u)   /* 2,628 ms: 3,939 blocks of 2/3 ms, plus tick rounding */
/* AD_LIVE=1: attack/decay are live (PATCH_AD). Source: track 8's stock per-track parameters 7 (macro 2, Start) =
 * attack and 8 (macro 3, Length) = decay, coarse 0..127, read from the channel (saved with the project, CC 22/23,
 * p-locks, Clear + macro, host_param for free). Changed values always precede the next ONSET; without a note, issues
 * are at least AD_PACE_MS apart (ad_service); each voice's ranking deadline is its publication tick + the lifetime in
 * force, raised (never lowered) for sounding voices when a new PATCH_AD is published. A deadline only ranks steal
 * candidates; FREE stays unused/killed and every steal still KILLs first. */
/* our parameter entries (epoch-tagged, purged with the patch flag, gated while CLOSED) */
#if AD_LIVE
#define K_PARAM(k)      ((k) == VK_PATCH || (k) == VK_AD)
#else
#define K_PARAM(k)      ((k) == VK_PATCH)
#endif
#define AD_PACE_MS      5u
#define AD_DEFAULT      (0u | 64u << 7)           /* attack 0, decay 64: the DSP's values after a mode-0 load */
#define AD_TICKS(b)     (((b) + 15u) * 2u / 3u + 2u)  /* blocks + ring backlog, 2/3 ms each, plus tick rounding */
#define PATCH_FIELDS    (A1_PATCH ? 5u : 0u)
/* Non-droppable entries N can hold once every sample note-on has been evicted, every queued onset purged and the
 * sample offs collapsed: one off per channel (8) and one patch transaction (PATCH_FIELDS + 1). */
/* ... plus at most one queued ONSET entry per voice (4): a steal's KILL boundary purges the stolen voice's earlier
 * entry. KILL entries (up to 4, one per voice) and the ALL OFF live in P, not in N. */
/* ... plus (AD_LIVE) at most one queued PATCH_AD per queued ONSET entry, plus one (ad_service). */
_Static_assert(8 + (PATCH_FIELDS ? PATCH_FIELDS + 1 : 0) + 4 + (AD_LIVE ? 4 + 1 : 0) < NQ,
               "N always has room for a non-droppable entry after make_room()");

#define RING            0x30410u
#define RING_PROD       0x307B5u
#define RING_CONS       0x307B6u
#define PROD_CELL       0x20006F38u     /* note ring producer index */
#define ENG_CELL        0x20000310u     /* engine lock object */

typedef uint32_t (*fn4_t)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef uint32_t (*fn5_t)(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);
typedef uint32_t (*fn1_t)(uint32_t);
typedef void (*ring_fn)(uint32_t *);
typedef uint32_t (*shw_fn)(uint32_t core, uint32_t v, uint32_t addr);
typedef uint32_t (*shr_fn)(uint32_t core, uint32_t addr);
typedef uint32_t (*tick_fn)(void);
#define ROUTER_ON       ((fn5_t)0x0803B7EDu)
#define ROUTER_OFF      ((fn4_t)0x0803B8F1u)
#define NM_ON           ((fn4_t)0x0803ABD9u)
#define NM_OFF          ((fn4_t)0x0803ACBBu)
#define ENG_ON          ((fn4_t)0x08031A19u)
#define ENG_OFF         ((fn4_t)0x08031A23u)
#define FORCE_OFF       ((fn1_t)0x080467D3u)
#define MIDI_ALLOFF     ((fn4_t)0x0803F799u)
#define ORIG_TX         ((fn4_t)0x080297DFu)
#define ORIG_MAIN0      ((fn4_t)0x08026F39u)
#define ORIG_RING       ((ring_fn)0x080406A9u)
#define SH_WRITE        ((shw_fn)0x08031229u)   /* shared DSP write */
#define SH_READ         ((shr_fn)0x080314D7u)   /* shared DSP read */
#define TICK            ((tick_fn)0x08024069u)  /* RTOS tick (ms) */
/* Activation handshake: the synth ENABLE word lives in core-1 X memory and is written exactly once per DSP mode-0
 * load, after the DSP initialisation and before any track-8 onset is admitted. The write uses the DSP word writer
 * with the address cache invalidated before and after, because the cache is not keyed by core. Never a raw SHI
 * poke, never a DSP read. */
#define ORIG_TEMPO      ((fn4_t)0x08031701u)    /* DSP ready hook target */
#define ORIG_DSPLOAD    ((fn4_t)0x08031111u)    /* DSP load hook target */
#define CACHE_NONE      0xFFFFFFFFu             /* not 0xFFFF: that is the valid host address of Y:7FFF */
#define XY_WRITE_BODY   ((fn4_t)0x080311DBu)    /* DSP word write (no lock) */
#define BUS_CACHE       0x20003684u             /* cached next DSP write address */
#define BUS_SEM         0x20003688u             /* DSP bus semaphore */
#if !CORE0_ONLY                                 /* CORE0_ONLY (0.2): the core-1 voice is retired; no symbol for it */
#define SYNTH_CORE      1u                      /* core 1 runs the voice decoder */
#define X_ENABLE        0x2E70u                 /* synth ENABLE word */
#endif

#ifndef SYNTH
#define SYNTH           1               /* profile switch: 0 = track 8 unchanged (no tap, no commands, no RAM
                                           block, no admission); every hook below becomes a tail call */
#endif
#if SYNTH

enum { SRC_PAD, SRC_SEQ, SRC_MIDI, SRC_MODULE, SRC_OTHER, NSRC };
enum { V_FREE, V_HELD, V_REL };
enum { WHY_STOP, WHY_CHOKE, WHY_EDIT, WHY_CC123, WHY_PROJECT, WHY_BOOT, WHY_STOCK7, NWHY };
enum { O_SLOT, O_RETRIG, O_ON, O_FORCE, O_REL };        /* the five note ring write sites */
enum { PH_CLOSED, PH_ARMED, PH_ENABLED, PH_OPEN };      /* activation: load entry / ALL OFF issued / ENABLE written / Main opened */

struct a1voice {
    uint32_t inst;                            /* serial of the owning instance */
    uint32_t on_ser, rel_ser;                 /* onset / release serial (age) */
    uint32_t pub_at;                          /* tick of the onset's publication (ring write), 0 = not yet */
    uint8_t state, cut, src, note;
};

struct a1inst {
    uint32_t ser;                             /* 0 = slot unused */
    uint8_t src, root, mask, pad;
};

struct a1q {
    uint32_t w0, w1;
    uint16_t seq;                             /* issue order */
    uint8_t kind;                             /* VK_* */
    uint8_t epoch;                            /* project epoch of our entries (others: 0) */
};

struct a1 {
    uint32_t magic;
    uint32_t serial;                          /* instance and age serial (wraps; serial-number compare) */
    uint32_t lane;                            /* serial of the chord-mono lane instance, 0 none */
    uint32_t tm_gen;                          /* tm()->gen seen */
    uint8_t booted, depth, epoch, pepoch;     /* -, router nesting, patch transaction epoch, project epoch */
    struct a1voice v[4];
    struct a1inst in[NINST];
    /* admission: engine lock */
    uint8_t cons, cons_ok, pq_alloff, pq_kill;
    uint8_t gen[4];                           /* 4-bit mirror of the DSP's onset counts (stream tags only) */
    uint8_t nq_head, nq_n;
    uint16_t seq;                             /* issue counter */
    uint16_t alloff_seq, kill_seq;            /* boundary of the pending ALL OFF / KILL */
    uint16_t pad1;
    struct a1q nq[NQ];
    uint32_t reset_seq, reset_seen;           /* admission-side ALL OFF: the Main task resets its table */
    uint8_t phase;                            /* PH_*: admission state across DSP loads (activation) */
    uint8_t fresh;                            /* FM_BANKVIEW: the stock store reset ran since the last refresh */
    uint8_t sk, sr;                           /* STEP_CHORDS: stashed key, audition root (router note; 0 none) */
    /* counters (tests, diagnostics) */
    uint16_t c_tap[NSRC], c_drop_on;
    uint16_t c_why[NWHY];
    uint16_t c_deferred, c_recover, c_overflow, c_ch7_slot, c_unexpected, c_cons_read, c_cons_bad, c_stale_off;
    uint16_t c_lost_inst, c_eng_cut, c_nm_bypass, c_onset, c_kill, c_alloff, c_patch, c_direct;
    uint16_t c_evict_on, c_collapse, c_stale_epoch, c_purged, c_enable, c_pre_enable, c_load, c_late, c_gated;
#if AD_LIVE
    /* PATCH_AD state (offset 720, after the counters: the earlier layout is unchanged) */
    uint32_t dl[4];                           /* per-voice ranking deadline (tick); meaningful while pub_at != 0 */
    uint32_t ad_at;                           /* tick of the last PATCH_AD issue (pacing), valid while ad_ok */
    uint16_t ad_want;                         /* attack | decay << 7 read from track 8's params 7 / 8 */
    uint16_t ad_iss;                          /* last issued (queued or published) */
    uint16_t ad_pub;                          /* last published = in force at the DSP once consumed */
    uint16_t c_ad, c_ad_coal, c_ad_wait, c_ad_paced, c_ad_purged, c_ad_barrier;
    uint16_t c_ad_note;                       /* PATCH_AD issued ahead of a note inside the pacing window */
    uint8_t ad_ok;                            /* ad_at valid (an issue since the last DSP load) */
    uint8_t cg;                               /* STEP_CHORDS: the record accumulator's project (tm gen, low byte) */
#if BOOT_PULSE
    uint32_t bp;                              /* boot pulse: bit 31 on, bits 16..23 pads loaded, bits 0..15 start tick */
#endif
#endif
};
_Static_assert(sizeof(struct a1) <= A1_SIZE, "A1 block");
#if CORE0_SYNTH
#include "core0feed.h"
_Static_assert(Q0_AT + Q0_SIZE == A1_AT, "Q0 block directly below struct a1");
/* N bound: stock offs 8 + ONSET 4 + RELEASE 4 + envelope groups (4 + 1, one N entry each) + tone
 * (4 + 1; emitted only under CORE0_TONE, at most one per ONSET gap) = 26 < 32 (the expanded count, two N entries per group: 31). */
_Static_assert(8 + 4 + 4 + (4 + 1) + (4 + 1) < NQ, "core-0 N bound");
#if FM_PATCH
/* FM_PATCH: the PATCH_FM and MOD_AD latches ride in the same group entry, so N is unchanged (26 entries, admission
 * k + 2). Raw queue entries behind them: 8 stock + 4 ONSET + 4 RELEASE + 5 groups x 4 (ENV pair, PATCH_FM, MOD_AD)
 * + 5 tones = 41 (the expanded count: 31 + 2 x 5), and a dirty full press is 4 KILL + 4 + 4 ONSET + 1 TONE = 13 <= Q0_CAP. */
_Static_assert(8 + 4 + 4 + 5 * FM_GROUP_MAX + 5 == 41 && 4 + FM_GROUP_MAX + 4 + 1 == TONE_RESERVE
               && TONE_RESERVE <= Q0_CAP && FM_GROUP_MAX <= Q0_BATCH, "FM_PATCH queue bound");
#endif
#define K_OURS(k)       ((k) == VK_ONSET || K_PARAM(k) || (k) == VK_REL || (k) == VK_TONE)
#define NQ_PURGE(a, voices, rel, patch, before) nq_purge(a, voices, rel, patch, before)
#else
#define K_OURS(k)       ((k) == VK_ONSET || K_PARAM(k))
#define NQ_PURGE(a, voices, rel, patch, before) nq_purge(a, voices, patch, before)
#define Q0_FLUSH(a)     ((void)0)
#define ENV_MASK        VC_AD_MASK
#define ENV_ISS(a)      (a)->ad_iss
#define ENV_WANT(a)     (a)->ad_want
#define ENV_SET_ISS(a, v)  ((a)->ad_iss = (uint16_t)(v))
#define ENV_SET_WANT(a, v) ((a)->ad_want = (uint16_t)(v))
#endif

static struct a1 *a1(void)
{
#ifdef A1_AT
    uint32_t p = A1_AT;
    if (!region() || ((struct a1 *)p)->magic != A1_MAGIC)
        return 0;
    return (struct a1 *)p;
#else
    uint32_t p = *(volatile uint32_t *)A1_PTR;
    if (!region() || !p || *(volatile uint32_t *)A1_PTR_CHK != ~p || ((struct a1 *)p)->magic != A1_MAGIC)
        return 0;
    return (struct a1 *)p;
#endif
}

static int32_t ser_cmp(uint32_t a, uint32_t b)
{
    return (int32_t)(a - b);
}

/* ---- engine lock ------------------------------------------------------------------------------------------------ */

static uint32_t eng_lock(void)
{
    uint32_t sem = *(volatile uint32_t *)ENG_CELL + 0xCu;
    SEM_TAKE(sem);
#if CORE0_SYNTH
    Q0->busy |= B_ENG;                        /* our transaction: every entry hook checks this BEFORE locking */
#endif
    return sem;
}

static void eng_unlock(uint32_t sem)
{
    uint32_t cell = sem;
#if CORE0_SYNTH
    Q0->busy &= (uint8_t)~B_ENG;
#endif
    SEM_GIVE(&cell);
}

/* ---- ring admission (engine lock held) ------------------------------------------------------------------------
 * Invariant: an entry is written only into a free slot (free = (cons - prod - 1) & 15 > 0). There is no other
 * write path. P (pq_alloff, pq_kill) is published before N; its boundary is its issue sequence: issuing it purges
 * from N exactly the onsets issued before it (all of N at that moment); onsets issued after stay behind it. */

#if AD_LIVE
static uint32_t ad_ticks(uint32_t ad)
{
    return AD_TICKS(ad_life_blocks(ad & 127u, (ad >> 7) & 127u));
}
#endif

/* The ring write exactly as the original (same frames, same order), plus the generation tags of what was published. */
static void ring_put(struct a1 *a, uint32_t w0, uint32_t w1)
{
    volatile uint32_t *pc = (volatile uint32_t *)PROD_CELL;
#if CORE0_ONLY && TRIM_RING                   /* trim C1: only stock words reach the stock ring (ours go to the core-0
                                                 queue), so none of the class-A bookkeeping below can run */
    {
        uint32_t p = *pc, at = RING + 2 * p;
        SH_WRITE(0, w0 << 8, at);
        SH_WRITE(0, w1 << 8, at + 1);
        p = (p + 1) & 15u;
        *pc = p;
        SH_WRITE(0, p << 8, RING_PROD);
        (void)a;
        return;
    }
#endif
    uint32_t p = *pc, at = RING + 2 * p, k = vc_kind(w0), m = 0;
    if (k == VK_ONSET) {                      /* the DSP expects gen = stored + 1: taken from the mirror now */
        m = vc_onset_mask(w0);
        for (uint32_t v = 0; v < 4; v++)
            if (m >> v & 1)
                vc_onset_publish(&w1, a->gen[v]);
    }
    SH_WRITE(0, w0 << 8, at);
    SH_WRITE(0, w1 << 8, at + 1);
    p = (p + 1) & 15u;
    *pc = p;
    SH_WRITE(0, p << 8, RING_PROD);
    if (k == VK_ONSET) {
        uint32_t t = TICK() | 1u;
        for (uint32_t v = 0; v < 4; v++)
            if (m >> v & 1) {
                a->gen[v] = (uint8_t)((a->gen[v] + 1) & 15u);
                a->v[v].pub_at = t;
#if AD_LIVE
                a->dl[v] = t + ad_ticks(a->ad_pub);
#endif
            }
        a->c_onset++;
    } else if (k == VK_ALLOFF) {
        for (uint32_t v = 0; v < 4; v++)
            a->gen[v] = 0;
        a->c_alloff++;
    } else if (k == VK_KILL) {
        a->c_kill++;
    } else if (k == VK_PATCH) {
        a->c_patch++;
#if AD_LIVE
    } else if (k == VK_AD) {                  /* in force from the DSP's next block, for sounding voices too: raise
                                                 their deadlines to the new full lifetime from now (conservative) */
        uint32_t t = TICK() | 1u, d = t + ad_ticks(w1);
        a->ad_pub = (uint16_t)(w1 & VC_AD_MASK);
        for (uint32_t v = 0; v < 4; v++)
            if (a->v[v].state != V_FREE && a->v[v].pub_at && (int32_t)(d - a->dl[v]) > 0)
                a->dl[v] = d;
        a->c_ad++;
#endif
    }
}

/* Free ring entries; the consumer index is read over SHI only when the cached one says full. A value that is not
 * a ring index gives 0 (nothing is published until a later read succeeds). */
static uint32_t ring_free(struct a1 *a)
{
    uint32_t p = *(volatile uint32_t *)PROD_CELL & 15u, f = (a->cons - p - 1) & 15u;
    if (f && a->cons_ok)
        return f;
    uint32_t c = SH_READ(0, RING_CONS) >> 8;
    a->c_cons_read++;
    if (c > 15) {
        a->c_cons_bad++;
        a->cons_ok = 0;
        return 0;
    }
    a->cons = (uint8_t)c;
    a->cons_ok = 1;
    return (c - p - 1) & 15u;
}

static struct a1q *nq_at(struct a1 *a, uint32_t i)
{
    return &a->nq[(a->nq_head + i) % NQ];
}

static void nq_push(struct a1 *a, uint32_t w0, uint32_t w1, uint32_t kind, uint32_t epoch)
{
    struct a1q *e = nq_at(a, a->nq_n++);
    e->w0 = w0;
    e->w1 = w1;
    e->seq = ++a->seq;
    e->kind = (uint8_t)kind;
    e->epoch = (uint8_t)epoch;
}

static void nq_del(struct a1 *a, uint32_t i)
{
    for (; i + 1 < a->nq_n; i++)
        *nq_at(a, i) = *nq_at(a, i + 1);
    a->nq_n--;
}

/* Purge from N, among the entries issued before boundary `before`: the voices in `voices` from onsets (an onset
 * left with none is deleted) and, if `patch`, every patch entry. Entries issued at or after the boundary stay. */
#if CORE0_SYNTH
/* CORE0: also the RELEASE entries of `voices` (rel), and the envelope groups (patch); then orphan groups go (q0_tidy). */
static void q0_tidy(struct a1 *a);
static void nq_purge(struct a1 *a, uint32_t voices, uint32_t rel, uint32_t patch, uint16_t before)
{
    for (uint32_t i = 0; i < a->nq_n;) {
        struct a1q *e = nq_at(a, i);
        if ((int16_t)(e->seq - before) < 0 &&
            ((e->kind == VK_ONSET && !vc_onset_strip(voices, &e->w0, &e->w1)) ||
             (rel && e->kind == VK_REL && (voices >> vc_rel_voice(e->w0) & 1u)) ||
             (patch && (K_PARAM(e->kind) || e->kind == VK_TONE)))) {
            a->c_purged++;
            nq_del(a, i);
        } else {
            i++;
        }
    }
    q0_tidy(a);
}
#include "core0feed.c"
#else
static void nq_purge(struct a1 *a, uint32_t voices, uint32_t patch, uint16_t before)
{
    for (uint32_t i = 0; i < a->nq_n;) {
        struct a1q *e = nq_at(a, i);
        if ((int16_t)(e->seq - before) < 0 &&
            ((e->kind == VK_ONSET && !vc_onset_strip(voices, &e->w0, &e->w1)) ||
             (patch && K_PARAM(e->kind)))) {
            a->c_purged++;
            nq_del(a, i);
        } else {
            i++;
        }
    }
}
#endif

#if CORE0_SYNTH
/* Two targets, one FIFO: the stock ring takes the stock words in order, the core-0 queue takes ours (P first, then
 * ours in N in order; q0_drain). While the queue is not OPEN no entry of ours leaves the ARM (dropped, counted). */
static void ad_drain(struct a1 *a)
{
    if (Q0->ph != Q_OPEN) {
        if (a->pq_alloff || a->pq_kill)
            a->c_gated++;
        a->pq_alloff = a->pq_kill = 0;
        for (uint32_t i = 0; i < a->nq_n;) {
            if (K_OURS(nq_at(a, i)->kind)) {
                a->c_gated++;
                nq_del(a, i);
            } else {
                i++;
            }
        }
    }
    for (uint32_t i = 0; i < a->nq_n;) {
        struct a1q *e = nq_at(a, i);
        if (K_OURS(e->kind)) {
            i++;
            continue;
        }
        if (!ring_free(a))
            break;
        uint32_t w0 = e->w0, w1 = e->w1;
        nq_del(a, i);
        ring_put(a, w0, w1);
    }
    q0_drain(a);
}
#else
static void ad_drain(struct a1 *a)
{
    if (a->phase == PH_CLOSED) {              /* activation gate at the one publication point: while CLOSED no class-A
                                                 entry leaves the ARM (P flags dropped, our N entries removed) */
        if (a->pq_alloff || a->pq_kill)
            a->c_gated++;
        a->pq_alloff = a->pq_kill = 0;
        for (uint32_t i = 0; i < a->nq_n;) {
            uint32_t k = nq_at(a, i)->kind;
            if (k == VK_ONSET || K_PARAM(k)) {
                a->c_gated++;
                nq_del(a, i);
            } else {
                i++;
            }
        }
    }
    while (a->pq_alloff || a->pq_kill || a->nq_n) {
        if (!ring_free(a))
            return;
        uint32_t w0, w1;
        if (a->pq_alloff) {
            vc_alloff(&w0, &w1);
            a->pq_alloff = 0;
        } else if (a->pq_kill) {              /* one entry per pass; the serializer says which voices it covers */
            uint32_t g = 0;
            for (uint32_t v = 0; v < 4; v++)
                if (a->pq_kill >> v & 1)
                    g |= (uint32_t)a->gen[v] << (4 * v);
            a->pq_kill &= (uint8_t)~vc_kill_entry(a->pq_kill, g, &w0, &w1);
        } else {
            struct a1q *e = nq_at(a, 0);
            w0 = e->w0;
            w1 = e->w1;
            uint32_t ours = e->kind == VK_ONSET || K_PARAM(e->kind), stale = ours && e->epoch != a->pepoch;
            a->nq_head = (uint8_t)((a->nq_head + 1) % NQ);
            a->nq_n--;
            if (stale) {                      /* an older project's onset or patch entry: never published */
                a->c_stale_epoch++;
                continue;
            }
        }
        ring_put(a, w0, w1);
    }
}
#endif

/* Class-A ALL OFF at this point of the issue order (Stop, choke, mute, edit, CC 123, project, recovery, an
 * unexpected forced off of channel 7): purges every queued onset issued before it, cancels a pending KILL
 * (the ALL OFF covers it) and asks the Main task to reset its table. */
static void issue_alloff(struct a1 *a)
{
    uint16_t b = (uint16_t)(a->seq + 1);
    NQ_PURGE(a, 15, 1, CORE0_SYNTH, b);
    a->pq_alloff = 1;
    a->pq_kill = 0;
    a->alloff_seq = b;
    a->seq = b;
    a->reset_seq++;
}

/* Collapse sample note-offs: a queued off of channel c with an earlier queued off of c and no note-on of c in
 * between changes nothing at the DSP; it is merged into the earlier one. */
static uint32_t collapse_offs(struct a1 *a)
{
    uint32_t n = 0;
    for (uint32_t i = 1; i < a->nq_n;) {
        struct a1q *e = nq_at(a, i);
        int32_t j = -1;
        if (e->kind == VK_STOCK_OFF)
            for (int32_t k = (int32_t)i - 1; k >= 0; k--) {
                struct a1q *x = nq_at(a, (uint32_t)k);
                if (x->kind == VK_STOCK_ON && vc_stock_ch(x->w0) == vc_stock_ch(e->w0))
                    break;
                if (x->kind == VK_STOCK_OFF && vc_stock_ch(x->w0) == vc_stock_ch(e->w0)) {
                    j = k;
                    break;
                }
            }
        if (j >= 0) {
            if (e->w1 == 3)
                nq_at(a, (uint32_t)j)->w1 = 3;
            nq_del(a, i);
            n++;
        } else {
            i++;
        }
    }
    a->c_collapse += (uint16_t)n;
    return n;
}

/* Room in N for one non-droppable entry, lossless first: collapse sample offs; else evict the newest queued sample
 * note-on; else ALL OFF recovery (purges our onsets; N holds at most one onset per voice, so this is a last resort
 * the static bound makes unreachable). Returns 0 only if nothing frees a slot, which the bound excludes; the caller
 * then counts the entry as lost (never written over unread data). */
static uint32_t make_room(struct a1 *a)
{
    while (a->nq_n >= NQ) {
        if (collapse_offs(a))
            continue;
        int32_t last = -1;
        uint32_t ours = 0;
        for (uint32_t i = 0; i < a->nq_n; i++) {
            if (nq_at(a, i)->kind == VK_STOCK_ON)
                last = (int32_t)i;
            ours |= nq_at(a, i)->kind == VK_ONSET;
        }
        if (last >= 0) {
            nq_del(a, (uint32_t)last);
            a->c_evict_on++;
        } else if (ours) {
            a->c_recover++;
            issue_alloff(a);
        } else {
            return 0;
        }
    }
    return 1;
}

/* A non-droppable entry (sample word, patch entry): straight into the ring when nothing waits and a slot is free,
 * else at the tail of N. */
static void put_n(struct a1 *a, uint32_t w0, uint32_t w1, uint32_t epoch)
{
    ad_drain(a);
#if CORE0_SYNTH                               /* a stock word goes straight to the ring if no stock word waits */
    if (!K_OURS(vc_kind(w0)) && !q0_stock_waiting(a) && ring_free(a)) {
#else
    if (!a->pq_alloff && !a->pq_kill && !a->nq_n && ring_free(a)) {
#endif
        a->c_direct++;
        ring_put(a, w0, w1);
        return;
    }
    if (!make_room(a)) {
        a->c_overflow++;                      /* excluded by the bound; counted, never written over unread data */
        return;
    }
    a->c_deferred++;
    nq_push(a, w0, w1, vc_kind(w0), epoch);
    ad_drain(a);
}

/* ---- voice_emit: the semantic interface (engine lock held) ------------------------------------------------------ */

/* All-or-nothing chord admission, decided before allocation: either every entry of the onset can be published
 * now (nothing waits and the ring has room for all of them) or all of them fit in N below the onset reserve. */
static uint32_t voice_onset_room(struct a1 *a, uint32_t nvoices)
{
    uint32_t k = vc_onset_entries((1u << nvoices) - 1u);
    ad_drain(a);
#if CORE0_SYNTH                               /* the whole press reserved before either envelope half: k ONSET + the
                                                 envelope group + a tone entry */
    return a->nq_n + k + 2u <= NQ - NQ_ONSET_RESERVE;
#else
    if (!a->pq_alloff && !a->pq_kill && !a->nq_n && ring_free(a) >= k)
        return 1;
    return a->nq_n + k <= NQ - NQ_ONSET_RESERVE;
#endif
}

/* Publishes or queues every entry of the onset in order; a chord may straddle blocks (entries are consumed one per
 * block), but none of its entries is ever dropped alone: only an ALL OFF (which also damps the published ones) or
 * a KILL of its voices removes queued entries. */
#if CORE0_SYNTH
static void voice_emit_onset_t(struct a1 *a, uint32_t mask, const uint8_t *note, uint32_t vel, uint32_t tune)
{
    for (uint32_t v = 0; v < 4; v++)
        if (mask >> v & 1u) {
            uint32_t w0, w1;
            vc_onset_tuned(v, note[v], tune, vel, &w0, &w1);
            a->c_deferred++;
            nq_push(a, w0, w1, VK_ONSET, a->pepoch);     /* room reserved by voice_onset_room() */
        }
    ad_drain(a);
}
#endif

static void voice_emit_onset(struct a1 *a, uint32_t mask, const uint8_t *note, uint32_t vel)
{
    uint32_t w[4][2], n = vc_onset_words(mask, note, vel, w);
    for (uint32_t i = 0; i < n; i++) {
        if (!a->pq_alloff && !a->pq_kill && !a->nq_n && ring_free(a)) {
            a->c_direct++;
            ring_put(a, w[i][0], w[i][1]);
        } else {
            a->c_deferred++;
            nq_push(a, w[i][0], w[i][1], VK_ONSET, a->pepoch);   /* room checked by voice_onset_room() */
        }
    }
    ad_drain(a);
}

static void voice_emit_kill(struct a1 *a, uint32_t mask)
{
    uint16_t b = (uint16_t)(a->seq + 1);
    NQ_PURGE(a, mask, 1, 0, b);
    a->pq_kill |= (uint8_t)mask;
    a->kill_seq = b;
    a->seq = b;
    ad_drain(a);
}

static void voice_emit_alloff(struct a1 *a)
{
    issue_alloff(a);
    ad_drain(a);
}

/* The fixed v0.1 patch (voice-command-abi 5.1 boot bank values, T60 2 s decay_k and env_floor; the stored patch 7 is A3's to map). A new
 * transaction replaces any queued older one (DATA and COMMIT), so N holds at most one. */
static uint32_t patch_value(uint32_t i)
{
    return i == 0 ? 0 : i == 1 ? 0x100000u : i == 2 ? 0x2AAAABu : i == 3 ? 0x7FB4B6u : 0x400u;
}

static void voice_emit_patch(struct a1 *a)
{
    if (!A1_PATCH)
        return;
    NQ_PURGE(a, 0, 0, 1, (uint16_t)(a->seq + 1));
    a->epoch = (uint8_t)(a->epoch % 63u + 1u);
    uint32_t w0, w1;
    for (uint32_t i = 0; i < PATCH_FIELDS; i++) {
        vc_pdata(i, a->epoch, patch_value(i), &w0, &w1);
        put_n(a, w0, w1, a->pepoch);
    }
    vc_pcommit(PATCH_FIELDS, a->epoch, &w0, &w1);
    put_n(a, w0, w1, a->pepoch);
}

#if AD_LIVE
/* ---- PATCH_AD (engine lock held unless noted) ---------------------------------------------------------------------
 * Source: track 8's stock parameters 7 (Start) and 8 (Length), coarse part. Mapping: attack = Start, decay = Length,
 * both identity over 0..127. The stock default Start 0 = attack 0; projects start at Length 64 = decay 64 (T60 2 s,
 * the fixed envelope of the builds without AD_LIVE): ad_preapply. */
static uint32_t ad_attack_of(uint32_t start)
{
    return start & 127u;
}

static uint32_t ad_decay_of(uint32_t length)
{
    return length & 127u;
}

/* The values in force on track 8's channel (pure reads, any task); the last known ones if the channel does not
 * check out. */
static uint32_t ad_read(struct a1 *a)
{
    uint32_t ch = pl_channel(rw(PL_R_CELL), A1_T);
    if (!ch)
        return ENV_WANT(a);
#if CORE0_SYNTH                               /* snapshot A, D, S, R: macros 2..5 = params 7..10 */
    return ad_attack_of(PL_GET(ch, 7) & 0x7Fu) | ad_decay_of(PL_GET(ch, 8) & 0x7Fu) << 7 |
           (PL_GET(ch, 9) & 0x7Fu) << 14 | (PL_GET(ch, 10) & 0x7Fu) << 21;
#else
    return ad_attack_of(PL_GET(ch, 7) & 0x7Fu) | ad_decay_of(PL_GET(ch, 8) & 0x7Fu) << 7;
#endif
}

/* Issue (or coalesce) the latest values; mode AD_KNOB (Main pass: knob, CC, lock with no note), AD_NOTE (a track-8
 * press, before its onset is admitted) or AD_BAR (barrier).
 * Order: a PATCH_AD issued before an ONSET always reaches the DSP before it (FIFO). Changed values are ALWAYS issued
 * ahead of the next ONSET (AD_NOTE: pacing exception), because a late packet cannot restore a slow attack the note has
 * already passed. The newest queued PATCH_AD takes the latest values in place while no ONSET is queued behind it;
 * one with an ONSET behind it is never rewritten. A queued PATCH_AD followed by a later one with no ONSET in between
 * is superseded and deleted, so N holds at most one PATCH_AD per queued ONSET entry plus one.
 * AD_KNOB issues only with no PATCH_AD queued and at least AD_PACE_MS after the previous issue (minimum spacing). */
enum { AD_KNOB, AD_NOTE, AD_BAR };
#if FM_PATCH
/* FM_PATCH: the group also carries the FM latches (fm_patch.h), so "the values" are the envelope, PATCH_FM and MOD_AD
 * together: the same issue, coalescing and supersede rules apply to all three as one snapshot. */
#define FM_SAME(w0, w1, env) (((w1) & ENV_MASK) == (env) && fmn_fm(w0, w1) == Q0->fm_want && fmn_md(w0) == Q0->md_want)
#endif
static void ad_service(struct a1 *a, uint32_t want, uint32_t mode)
{
    uint32_t now = TICK();
    ENV_SET_WANT(a, want);
#if FM_PATCH
    q0_fm_read();
#endif
    int32_t q = -1;
    uint32_t behind = 0;
    for (uint32_t i = 0; i < a->nq_n; i++) {
        uint32_t k = nq_at(a, i)->kind;
        if (k == VK_AD) {
            if (q >= 0 && !behind) {          /* superseded: nothing between it and this one uses the values */
#if FM_PATCH
                nq_at(a, i)->w1 |= nq_at(a, (uint32_t)q)->w1 & FMN_BAR;   /* the later one inherits a barrier */
#endif
                nq_del(a, (uint32_t)q);
                a->c_ad_coal++;
                i--;
            }
            q = (int32_t)i, behind = 0;
        } else if (q >= 0 && k == VK_ONSET) {
            behind = 1;
        }
    }
    if (q >= 0 && !behind) {
        struct a1q *e = nq_at(a, (uint32_t)q);
#if FM_PATCH
        if (!FM_SAME(e->w0, e->w1, want)) {   /* coalesced in place, the barrier mark kept */
            e->w0 = fmn_w0(Q0->fm_want, Q0->md_want);
            e->w1 = fmn_w1(want, Q0->fm_want, e->w1 & FMN_BAR);
            ENV_SET_ISS(a, want);
            Q0->fm_iss = Q0->fm_want;
            Q0->md_iss = Q0->md_want;
            a->c_ad_coal++;
        }
#else
        if ((e->w1 & ENV_MASK) != want) {
            uint32_t w0;
            vc_ad(want, &w0, &e->w1);         /* coalesced: the queued snapshot takes the latest values */
            ENV_SET_ISS(a, want);
            a->c_ad_coal++;
        }
#endif
        return;
    }
#if FM_PATCH
    if (mode != AD_BAR && want == ENV_ISS(a) && Q0->fm_want == Q0->fm_iss && Q0->md_want == Q0->md_iss)
        return;
#else
    if (mode != AD_BAR && want == ENV_ISS(a))
        return;
#endif
    uint32_t early = a->ad_ok && (int32_t)(now - a->ad_at) < (int32_t)AD_PACE_MS;
    if (mode == AD_KNOB) {
        if (q >= 0) {                         /* one queued already (an ONSET behind it): wait for its publication */
            a->c_ad_wait++;
            return;
        }
        if (early) {
            a->c_ad_paced++;
            return;
        }
    } else if (mode == AD_NOTE && early) {
        a->c_ad_note++;                       /* pacing exception: the values go ahead of the note */
    }
#if FM_PATCH                                  /* our group: straight into N (put_n would classify it by word0) */
    a->ad_at = now;                           /* the actual tick; validity in ad_ok */
    a->ad_ok = 1;
    ENV_SET_ISS(a, want);
    Q0->fm_iss = Q0->fm_want;
    Q0->md_iss = Q0->md_want;
    ad_drain(a);
    if (!make_room(a)) {
        a->c_overflow++;
        return;
    }
    a->c_deferred++;
    nq_push(a, fmn_w0(Q0->fm_want, Q0->md_want), fmn_w1(want, Q0->fm_want, mode == AD_BAR), VK_AD, a->pepoch);
    ad_drain(a);
#else
    uint32_t w0, w1;
    vc_ad(want, &w0, &w1);
    a->ad_at = now;                           /* the actual tick; validity in ad_ok */
    a->ad_ok = 1;
    ENV_SET_ISS(a, want);
    put_n(a, w0, w1, a->pepoch);
#endif
}

/* Barrier (ALL OFF from Stop, choke, edit, CC 123, project change; boot ALL OFF of a DSP load): every queued
 * PATCH_AD (any epoch) is purged and the current values are published behind the ALL OFF, before any later note. */
static void ad_barrier(struct a1 *a)
{
    for (uint32_t i = 0; i < a->nq_n;) {
        if (nq_at(a, i)->kind == VK_AD) {
            a->c_ad_purged++;
            nq_del(a, i);
        } else {
            i++;
        }
    }
    a->c_ad_barrier++;
    ad_service(a, ad_read(a), AD_BAR);
#if CORE0_TONE                                /* and the current tone, always (an epoch drops the DSP's tone caches) */
    q0_tone(a, q0_tone_read(), TONE_BAR);
#endif
}
#define AD_BARRIER(a)   ad_barrier(a)

/* ---- New-project default Length 64 ------------------------------------------------------------------------------
 * A project whose track 8 never had the synth, i.e. T1 patch slot 7 unset (synth_settings.h; also an all-zero T1
 * block), and whose track-8 Length is exactly the stock default 127/127 (coarse/fine)
 * gets Length 64/0. Patch slot 7 is created at the same time (codec defaults; its fields stay unused and hidden), so
 * the project is converted once: a later save keeps whatever the user sets, 127 included. A slot that is set (or
 * unknown) and a read-only T1 block are never touched. The slot is created whatever the Length was, so a later 127 is
 * kept as well. */
#define AD_LEN_NEW      64u
#define MIDI_ROOT       0x20006EECu     /* MIDI dispatcher object; + 0x60: its src-4 parameter thunk (MIDI CC in) */

/* Track 8's Length, coarse << 7 | fine, of a channel-layout buffer (the store's saved channel values), read with
 * the stock getter through a stand-in channel: descriptors of the real channel, buffer pointer = buf. */
static uint32_t ad_len14(uint32_t ch, uint32_t buf)
{
    uint32_t f[4] = {0, rw(ch + 4), 0, (uint32_t)&buf};
    return (PL_GET((uint32_t)f, 8) & 0x7Fu) << 7 | (PL_GET((uint32_t)f, 15) & 0x7Fu);
}

static uint32_t ad_slot_unset(volatile uint8_t *b, uint32_t why)
{
    return why == TM_EMPTY || (why == TM_OK && syn_patch_status(b + SYN_PATCH_AT) == SYN_UNSET);
}

#if FB_MACRO1
/* Codec 2 version 3 at its defaults (Tune 64), one out-of-line copy. */
__attribute__((noinline)) static void ad_fm_new(volatile uint8_t *p)
{
    syn_fm_create(p);
}
#endif

/* Create patch slot 7 in block b (status why: EMPTY or OK with the slot unset), the block header too if it was
 * empty (as tm_edit does), then the CRC. Under the suspension, by the caller. */
__attribute__((noinline)) static void ad_make_slot(volatile uint8_t *b, uint32_t why)
{
    if (why == TM_EMPTY) {
        wr32(b, TM_BLK_MAGIC);
        b[4] = TM_VERSION;
        b[5] = 0;
        b[6] = (uint8_t)TM_EXT_LEN;
        b[7] = (uint8_t)(TM_EXT_LEN >> 8);
    }
#if FB_MACRO1                                 /* codec 2 version 3 at once: never mistaken for a 0.2.0 slot */
    ad_fm_new(b + SYN_PATCH_AT);
#else
    syn_patch_create(b + SYN_PATCH_AT);
#endif
    wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
}

#if FB_MACRO1
/* A block whose slot is of 0.2.0 or older (syn_fm_old): converted once by ad_fb_convert. */
__attribute__((noinline)) static uint32_t ad_fb_old(volatile uint8_t *b, uint32_t why)
{
    return why == TM_OK && syn_fm_old(b + SYN_PATCH_AT);
}
/* A slot of 0.2.0 or older (syn_fm_old) -> codec 2 version 3, once, so the project sounds as before: its Tune
 * (channel parameter 6, `tune`) moves to +4, and its feedback (+4 of codec 2; the default of codec 1) is returned as
 * the new parameter-6 value, which the caller writes. Other bytes are kept. Under the suspension, by the caller. */
__attribute__((noinline)) static uint32_t ad_fb_convert(volatile uint8_t *b, uint32_t tune)
{
    volatile uint8_t *p = b + SYN_PATCH_AT;
    uint32_t fb = FM_DEF_FB;
    if (p[SP_CODEC] == SYN_PATCH_FM) {
        fb = p[SF_FB] <= 127u ? p[SF_FB] : FM_DEF_FB;
        p[SP_VERSION] = SYN_PATCH_FM_VERSION;
    } else {
        ad_fm_new(p);
    }
    p[SF_TUNE] = (uint8_t)(tune & 0x7Fu);
    wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
    return syn_p6_of_fb(fb);
}

/* Track 8's parameter 6 in a channel-layout buffer (as ad_len14). */
static uint32_t ad_p6(uint32_t ch, uint32_t buf)
{
    uint32_t f[4] = {0, rw(ch + 4), 0, (uint32_t)&buf};
    return PL_GET((uint32_t)f, 6) & 0x7Fu;
}
#endif

#if FM_BANKVIEW
#define SV_BASE_AT      0x28CECu        /* store: each track's selected sample slot (the base; the refresh copies it into
                                           the base observable and channel parameter 0) */
#define OBS_SETP        ((uint32_t (*)(uint32_t, uint32_t))0x0803FD27u)   /* (cell, i): element i, writable */
#define AD_M6_NEW       96u             /* new projects: macro 6 (FM depth, channel parameter 11) = 96 */
#define ST_RESET        ((void (*)(uint32_t))0x08032DB3u)   /* the stock store reset (new project) */

/* ---- Fresh creation ----------------------------------------------------------------------------
 * The new-project defaults (slot 0, M6 96) are written only when the stock firmware created a new project. Our four
 * hooks wrap its store reset where a project operation creates one: hook_rst_absent (an empty slot selected),
 * hook_rst_tail (a loaded file that is not a valid project), hook_rst_clear (clear) and hook_rst_pack (a pack switch
 * with no file for the slot). Each runs the reset, then sets a1's flag, which the next refresh consumes
 * (ad_preapply). A valid project loaded sets nothing, so an old or imported project keeps its slot byte and M6 as
 * stored (its Length too, except the 0.1.1 conversion of exactly 127/127 to 64/0 in ad_preapply / ad_boot_default);
 * the boot project is never given the defaults. */
void hook_store_reset(uint32_t store)
{
    ST_RESET(store);
    struct a1 *a = a1();
    if (a)
        a->fresh = 1;
}
void hook_rst_absent(uint32_t store) __attribute__((alias("hook_store_reset")));
void hook_rst_tail(uint32_t store) __attribute__((alias("hook_store_reset")));
void hook_rst_clear(uint32_t store) __attribute__((alias("hook_store_reset")));
void hook_rst_pack(uint32_t store) __attribute__((alias("hook_store_reset")));

/* Codec 2 version 1 -> 2, once (a private bench file of the 7302 / 7303 line): algorithm +2 (0..1) and bank +3
 * (0..15) are validated independently, an invalid field reading 0, and only then composed into track 8's selected
 * slot bank + 16 x algorithm `sel` (the store byte the refresh applies, or 0 for the caller to send); then the
 * version byte 2 and the CRC. Bytes +2 and +3 stay as they were (reserved, preserved), every other byte too. Under
 * the suspension, by the caller. Returns the slot. */
__attribute__((noinline)) static uint32_t ad_fm_convert(volatile uint8_t *b, uint32_t sel)
{
    volatile uint8_t *p = b + SYN_PATCH_AT;
    uint32_t a = p[SF_ALGO], f = p[SF_BANK];
    f = (f < FM_BANKS ? f : 0u) + (a < FM_ALGOS ? 16u * a : 0u);
    if (sel)
        *(volatile uint8_t *)sel = (uint8_t)f;
#if FB_MACRO1
    p[SP_VERSION] = 2u;                       /* version 2; ad_fb_convert then takes it to 3 */
#else
    p[SP_VERSION] = SYN_PATCH_FM_VERSION;
#endif
    wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
    return f;
}
#endif

/* Before every stock project refresh (load / absent slot, clear, pack switch; trackmodes.c TM_HOOK): the store is
 * the project about to be applied, and the refresh loads the channels, their value cells and displays from it, so
 * the new Length is consistent everywhere from the first moment and the project's first PATCH_AD carries it. Inside
 * the project operation (no save can run), under the suspension like tm_apply. */
__attribute__((noinline)) static void ad_preapply(uint32_t P)
{
    uint32_t s = store_of(P), ch = s ? pl_channel(rw(P + 0x54), A1_T) : 0;
#if FM_BANKVIEW
    struct a1 *a = a1();
    if (!a)
        return;
    uint32_t fresh = a->fresh;                /* a stock new project (hook_store_reset), consumed by this refresh */
    a->fresh = 0;
    if (!ch)
        return;
#else
    if (!ch || !a1())
        return;
#endif
    volatile uint8_t *b = (volatile uint8_t *)(s + TM_EXT);
    uint32_t buf = s + PL_SER_AT + PL_SER_LEN * A1_T;
    SUSPEND_ALL();
    uint32_t why = tm_check(b);
    if (ad_slot_unset(b, why)) {
        if (ad_len14(ch, buf) == 0x3FFFu) {   /* the one preservation exception (0.1.1, kept deliberately): a stored Length
                                                 (decay, parameters 8 / 15) of
                                                 exactly 127/127 becomes 64/0, once; attack, M6, the slot and every
                                                 other Length stay as stored */
            pl_put(ch, 8, buf, AD_LEN_NEW);
            pl_put(ch, 15, buf, 0);
        }
        ad_make_slot(b, why);                 /* the metadata; every other channel value and slot byte as stored */
#if FM_BANKVIEW
        if (fresh) {                          /* a stock new project only: slot 0, Unity under serial4 (the selected
                                                 slot, which the refresh copies into the base and channel parameter 0),
                                                 and FM depth 96 */
            *(volatile uint8_t *)(s + SV_BASE_AT + A1_T) = (uint8_t)FM_DEF_BANK;
            pl_put(ch, 11, buf, AD_M6_NEW);
#if FB_MACRO1
            pl_put(ch, 6, buf, FB_P6_LO);     /* feedback 0 (stock: Tune 64) */
#endif
        }
    } else if (why == TM_OK && syn_fm_v1(b + SYN_PATCH_AT)) {
        ad_fm_convert(b, s + SV_BASE_AT + A1_T);
#endif
    }
#if FB_MACRO1                                 /* a 0.2.0 or older slot (a v1 slot converted just above included) */
    if (ad_fb_old(b, why))
        pl_put(ch, 6, buf, ad_fb_convert(b, ad_p6(ch, buf)));
#endif
    RESUME_ALL();
}

/* A project in force that no refresh has applied (the boot project, tm site 0): the same conversion on the live
 * project, Main task (the DSP ready tail, before the boot barrier, and every Main pass until it is done). The slot
 * is written with tm_edit's gates (BUSY: again on the next pass; an EMPTY apply whose block is no longer all zero:
 * never), then Length 64 / fine 0 goes through the MIDI-in source of the parameter dispatcher (src 4: channel, value
 * cell and display as CC 87 / 23 would write them, no MIDI out; the p-lock fix sees a base write). */
/* The parameter dispatcher's MIDI-in source thunk (src 4), checked down to the engine; 0 if anything is unexpected. */
static uint32_t ad_cc_thunk(void)
{
    uint32_t m = rw(MIDI_ROOT), th = in_sram(m, 0x64) ? rw(m + 0x60) : 0;
    return in_sram(th, 12) && rw(th) == PL_THUNK_VT && *(volatile uint8_t *)(th + 8) == 4 && pl_engine(rw(th + 4))
        ? th : 0;
}

static void ad_boot_default(void)
{
    struct state *st = region();
    struct tm *t = st ? tm_attach(st) : 0;
    if (!t || !t->store || t->site != 0 || store_of(t->P) != t->store)
        return;
    volatile uint8_t *b = (volatile uint8_t *)(t->store + TM_EXT);
#if FB_MACRO1                                 /* also any 0.2.0 or older slot (ad_fb_convert), v1 included */
    if (!ad_slot_unset(b, t->why) && !ad_fb_old(b, t->why))
        return;
#elif FM_BANKVIEW                             /* also a codec-2 version-1 slot: converted once (ad_fm_convert) */
    if (!ad_slot_unset(b, t->why) && !(t->why == TM_OK && syn_fm_v1(b + SYN_PATCH_AT)))
        return;
#else
    if (!ad_slot_unset(b, t->why))
        return;
#endif
    uint32_t ch = pl_channel(rw(PL_R_CELL), A1_T), th = ad_cc_thunk();
    if (!ch || !th)
        return;
    uint32_t def = (PL_GET(ch, 8) & 0x7Fu) == 127u && (PL_GET(ch, 15) & 0x7Fu) == 127u, done = 0;
#if FM_BANKVIEW
    uint32_t fam = 0;
#endif
#if FB_MACRO1
    uint32_t tune = PL_GET(ch, 6) & 0x7Fu, p6 = 0, fbd = 0;
#endif
    SUSPEND_ALL();
    if (!(st->tok & TOK_CONTENT) && phase(t->P) == 0 && ad_slot_unset(b, t->why) &&
        (t->why != TM_EMPTY || all_zero(b, TM_EXT_LEN))) {
        ad_make_slot(b, t->why);              /* the records and the scales sub-record are unchanged: no copy */
        t->why = TM_OK;
        t->writes++;
        done = 1;
#if FM_BANKVIEW
    } else if (!(st->tok & TOK_CONTENT) && phase(t->P) == 0 && t->why == TM_OK && syn_fm_v1(b + SYN_PATCH_AT)) {
        fam = ad_fm_convert(b, 0);            /* the block now; the slot through the CC-8 path below */
        t->writes++;
        done = 2;
#endif
    }
#if FB_MACRO1                                 /* the block now; parameter 6 through the CC-21 path below */
    if (!(st->tok & TOK_CONTENT) && phase(t->P) == 0 && ad_fb_old(b, t->why)) {
        p6 = ad_fb_convert(b, tune);
        t->writes++;
        fbd = 1;
    }
#endif
    RESUME_ALL();
#if FB_MACRO1
    if (fbd)
        ((pl_sink_fn)rw(rw(th)))(th, 16u * A1_T, p6);
#endif
#if FM_BANKVIEW
    if (done == 1 && def) {                   /* LSB first, then the MSB, as the macro page sends a 14-bit pair */
#else
    if (done && def) {                        /* LSB first, then the MSB, as the macro page sends a 14-bit pair */
#endif
        ((pl_sink_fn)rw(rw(th)))(th, 16u * A1_T + 15u, 0);
        ((pl_sink_fn)rw(rw(th)))(th, 16u * A1_T + 2u, AD_LEN_NEW);
    }
#if FM_BANKVIEW                               /* the v1 conversion only (done 2): the converted slot is sent as
                                                 channel parameter 0 (the CC-8 source) and set and notified in the
                                                 project's base cell, as a CC 8 does, so the store byte follows. The boot
                                                 project (done 1: slot unset) keeps
                                                 its slot and M6 as stored: no defaults at boot (hook_store_reset) */
    if (done == 2) {
        ((pl_sink_fn)rw(rw(th)))(th, 16u * A1_T + 8u, fam);
        uint32_t bc = rw(t->P + 0x9Cu + 4u * A1_T);
        if (in_sram(bc, 0x14)) {
            *(volatile uint8_t *)OBS_SETP(bc, 0) = (uint8_t)fam;
            NOTIFY(bc);
        }
    }
#endif
}

/* ---- Track 8 ignores slice mode --------------------------------------------------------------------------------
 * With track t's setting C (channel parameter 4) = 1, the track is in slice mode. What that changes, as observed:
 *  1. macro page: macros 2 / 3 edit the selected slice's start / end instead of Start / Length (the page's flag
 *     + 0x4F); no Start / Length change, no lock record, no MIDI out;
 *  2. lock playback: Start / Length locks do not play (lane bits 1 / 2 of the track's lane mask are cleared);
 *  3. save: Start / Length (params 7 / 14, 8 / 15) are saved as their defaults 0 / 0, 127 / 127;
 *  4. notes: a note writes the slice's start / end into params 7 / 14, 8 / 15 (bypassed for track 8 by hook_nm_on);
 *  5. Note view keyboard: the slice layout instead of the chromatic one;
 *  6. Note view pad colours: the slice-marker pads are lit instead of the root pads;
 *  7. Sample view audition and steps set from a held Sample-view pad use router note 12 (slice 0);
 *  8. the Up / Down arrows and the expanded Note view (below).
 * MIDI CC 22 / 23 (dispatcher src 4) and host_param are not gated by setting C. For track 8 each of 1..3 and 5..8 is
 * undone right after (or around) the stock code; setting C itself, the slice table and the project bytes are left
 * alone. */
#define NM_VT           0x08067B24u                 /* slice converter check (its vtable word 0, hook_nm_on's site) */
#define NM_OBS          ((fn4_t)0x0803AD3Du)        /* its observer entry, this = converter + 4 */
#define NM_LANES_ON     ((fn4_t)0x0803B13Bu)        /* (conv, t): lane bits 1 / 2 of track t set again */
#define MP_OBS          ((fn4_t)0x080584DBu)        /* macro page observer entry, this = page + 8 */
#define MP_LED          ((fn4_t)0x080586E7u)        /* (page, k): knob k's light */
typedef uint32_t (*fn6_t)(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);
#define SAVE_POST       ((fn6_t)0x080472E3u)        /* the save's job post, after the slice-mode reset */
#define MP_INIT         ((fn4_t)0x0805862Du)        /* macro page: every knob's light (constructor) */
typedef uint64_t (*fn4l_t)(uint32_t, uint32_t, uint32_t, uint32_t);
#define KB_NEW          ((fn4l_t)0x08055DEDu)       /* keyboard constructor */
#define KB_CLEAR        ((fn4_t)0x08055E59u)        /* (kb): table, reverse map, partners, markers cleared */
#define KB_CHROM        ((fn4_t)0x08055E8Du)        /* (kb): the chromatic layout */
#define NV_COL          ((fn4_t)0x0805DABFu)        /* Note view pad colour */
#define SV_PRESS        ((fn4_t)0x0805C1ADu)        /* Sample view pad press */
#define STEP_NEW        ((fn4_t)0x0805E5A1u)        /* a step set from a held Sample-view pad */
#define STEP_SET        ((fn4_t)0x08054A5Du)        /* (array, step, byte): a step's note */

/* Track 8's value cell (QB: the macro values and, at element 0xC, setting C; the cell track 8's keyboard observes,
 * + 0x198), through the MIDI-in source and the engine; 0 if unexpected. Independent of the page on screen. */
static uint32_t ad_qb8(void)
{
    uint32_t th = ad_cc_thunk();
    return th ? pl_qb(pl_engine(rw(th + 4)), 4 * A1_T) : 0;
}

/* 1: track 8's macro page edits Start / Length whatever setting C says. Returns 1 if it had to clear the flag. */
static uint32_t ad_page_flag(uint32_t page)
{
    if (!a1() || !in_sram(page, 0x68) || rw(page) != PL_MACRO_VT || *(volatile uint16_t *)(page + 0x44) != 16u * A1_T
        || !*(volatile uint8_t *)(page + 0x4F))
        return 0;
    *(volatile uint8_t *)(page + 0x4F) = 0;
    return 1;
}

static void ad_noslice_page(uint32_t page)
{
    if (ad_page_flag(page)) {
        MP_LED(page, 1, 0, 0);                /* the two knobs' lights as on a track that is not slicing */
        MP_LED(page, 2, 0, 0);
    }
}

/* The macro page constructor's light init, after + 0x4F is taken from setting C: a page built while track 8 already
 * slices starts with the Start / Length lights. */
uint32_t hook_mp_init(uint32_t page, uint32_t b, uint32_t c, uint32_t d)
{
    ad_page_flag(page);
    return MP_INIT(page, b, c, d);
}

/* 5: track 8's Note-view keyboard keeps the chromatic layout. After the keyboard constructor or its observer
 * (scales.c hook_kb_obs) has built the slice layout, the chromatic one is built again (clear, chromatic layout,
 * notify), as with setting C = 0.
 * The keyboard must be track 8's: its cell pointer (+ 0x198) equals track 8's value cell. Only the constructor call
 * (ctor = 1, a site that builds track 8's keyboard only) may proceed while that cell cannot be resolved yet; the
 * observer path, shared by all eight keyboards, requires a positive match. */
static void ad_noslice_kb(uint32_t kb, uint32_t ctor)
{
    if (!a1() || !in_sram(kb, 0x1A0) || rw(kb) != KB_VT || *(volatile uint8_t *)(kb + KB_ROWS) != 0)
        return;
    uint32_t q8 = ad_qb8();
    if (q8 ? rw(kb + 0x198) != q8 : !ctor)
        return;
    KB_CLEAR(kb, 0, 0, 0);
    KB_CHROM(kb, 0, 0, 0);
    NOTIFY(rw(kb + 4));
}

/* The constructor of track 8's keyboard (asserted by the tests: the object is the one track 8's Note view reads). It
 * returns the object in r0 and its fourth argument in r1, both passed on. */
uint64_t hook_kb_new(uint32_t kb, uint32_t b, uint32_t c, uint32_t d)
{
    uint64_t r = KB_NEW(kb, b, c, d);
    ad_noslice_kb(kb, 1);
    return r;
}

#if STEP_CHORDS
/* The far blob (tools/farblob.py): table function i, only if the header is the one this build was made with. */
#define FAR_AT          0x0801B000u
#define FAR_MAGIC       0x31465243u                 /* 'CRF1' */
enum { FAR_CHORD, FAR_COL,
#if BOOT_PULSE
       FAR_PULSE,
#endif
       FAR_N };
__attribute__((noinline)) static uint32_t far_call(uint32_t i, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    volatile uint32_t *h = (volatile uint32_t *)FAR_AT;
    return h[0] == FAR_MAGIC && h[1] == FAR_KEY && h[2] == FAR_N ? ((fn4_t)h[4 + i])(a, b, c, d) : 0;
}
#endif

#if BOOT_PULSE
/* ---- Startup pulse line (BOOT_PULSE, 0.2.1, issue #5; drawing in the far blob) ---------------------------------------
 * During the boot pack load (called from the boot composition root: the progress object 0x20000A8C
 * draws a 32-pad bar, +5 = 0 only at boot), the pads show the ECG pulse line of the logo over the load progress:
 * loaded pads dim green (the stock bar's rule: pad k when i >= (k + 1) n / 32), the line in red. A head moves one
 * column every BP_COL_MS and wraps; the 6 columns behind it fade by about 16 % per step, the 7th is off. The line is
 * the beat shape (rows 2 2 1 2 0 3 2 2, row 0 at the top) with flat gaps of 3, 7, 2 and 6 columns (row 2) between
 * beats; the pads between two consecutive rows of a column are lit. It starts at the first bar draw and ends at the
 * bar's end (+0x10), where the stock removes the bar. The hooks only record progress (O(1), boot is not delayed);
 * the UI task draws (hook_frame) into the module LED buffer, which is otherwise unused in mode 0. The power symbol
 * (storage wait), SysEx transfers (+5 = 1) and the firmware update displays are never drawn over. */
#define BP_ON           (1u << 31)
#define BP_COL_MS       110u
#define BP_DIM          24u
static uint32_t bp_on(void)
{
    struct a1 *a = a1();
    return a && (a->bp & BP_ON);
}

/* hook_frame (UI task, under the suspension): s->leds = the pulse frame; 0 if the far blob is absent */
static uint32_t bp_leds(struct state *s)
{
    struct a1 *a = a1();
    return a ? far_call(FAR_PULSE, (uint32_t)s, a->bp, TICK(), 0) : 0;
}

#if FAR_PART
#define PB_DRAW         ((fn4_t)0x08054147u)        /* progress object vt (0x08068E70) +8: bar (obj, i, n) */
#define PB_END          ((fn4_t)0x080541ABu)        /* +0x10: end */

uint32_t hook_pb_draw(uint32_t o, uint32_t i, uint32_t n, uint32_t d)
{
    struct a1 *a = a1();
    if (a && !*(volatile uint8_t *)(o + 5u) && (n >> 5)) {
        uint32_t k = i / (n >> 5);
        a->bp = (a->bp & BP_ON ? a->bp & 0xFFFFu : TICK() & 0xFFFFu) | (k < 32u ? k : 32u) << 16 | BP_ON;
    }
    return PB_DRAW(o, i, n, d);
}

uint32_t hook_pb_end(uint32_t o, uint32_t b, uint32_t c, uint32_t d)
{
    struct a1 *a = a1();
    if (a && !*(volatile uint8_t *)(o + 5u))
        a->bp = 0;
    return PB_END(o, b, c, d);
}

/* The line's row at stream column c (cycle 50: 4 beats of 8 and the gaps 3, 7, 2, 6). */
static uint32_t bp_row(uint32_t c)
{
    c %= 50u;
    for (uint32_t g = 0x6273u;; g >>= 4) {
        if (c < 8u)
            return 0xAC9Au >> 2u * c & 3u;
        c -= 8u;
        if (c < (g & 15u))
            return 2u;
        c -= g & 15u;
    }
}

/* far table FAR_PULSE: the frame for state bp at tick now into s->leds (all other lights dark) */
uint32_t far_pulse(uint32_t sp, uint32_t bp, uint32_t now, uint32_t d)
{
    struct state *s = (struct state *)sp;
    const uint8_t *t = pad_leds();
    uint32_t c = ((now - bp) & 0xFFFFu) / BP_COL_MS, x = c & 7u, done = bp >> 16 & 0xFFu;
    (void)d;
    for (uint32_t i = 0; i < LEDS; i++)
        s->leds[i] = 0;
    for (uint32_t k = 0; k < done && k < 32u; k++)
        pad_rgb(s, t, k, 0, BP_DIM, 0);
    for (uint32_t a = 0; a < 7u && a <= c; a++) {
        uint32_t lv = (uint32_t)(0x596A7E96B3D5FEull >> 8u * a) & 0xFFu;
        uint32_t r1 = bp_row(c - a), r0 = c > a ? bp_row(c - a - 1u) : r1, col = (x - a) & 7u;
        for (uint32_t r = r0 < r1 ? r0 : r1; r <= (r0 < r1 ? r1 : r0); r++)
            pad_rgb(s, t, r * 8u + col, (uint8_t)lv, 0, 0);
    }
    return 1;
}
#endif
#endif

#if STEP_CHORDS && FAR_PART
/* ---- Step chords (STEP_CHORDS, 0.2.1, issue #3; in the far blob) ---------------------------------------------------
 * A track-8 step holds up to 4 notes: byte 0 = the lowest (stock), byte 6 bits 1..5 = key k (0 none) into the
 * project's shape table at T1 block + CH_AT ('C' 'H' version count, then 31 x 3 ascending offsets above byte 0,
 * 1..96, 0 = none). Bit 0 and bits 6..7 of byte 6 are never changed. Every stock step operation copies, swaps or
 * zeroes byte 6 with the step; stock firmware plays byte 0 only.
 *   playback  hook_seqstep (track 8's player, vtable word): the step's key is stashed for the press it causes; the
 *             press plays the stored notes verbatim (track octave applies; NPP not applied), one shared gate.
 *   entry     hook_se (Note view, step held, a key that is not the step's root): adds the key's note (up to 4) or
 *             removes it if it is in the chord; the lowest note becomes byte 0; the press auditions the whole chord.
 *   lights    ad_nv_col: with a step held, the chord's other notes take the held-root colour + 2.
 * A table that is invalid or read-only plays the root only. When the table is full, unused shapes (no key in any
 * step of the 8 tracks x 8 patterns) are reused; if none is free the note is not added. */
#define CH_AT           0x298u          /* block offset of the shape table (T1 reserved area, after scales) */
#define CH_N            31u
#define CH_VER          1u
#define CH_EL8          (0x2E4u + 7u * 0x5140u)     /* store offset of track 8's pattern elements */
#define CH_GET          ((fn4_t)0x0803FC59u)        /* cell getter (cell, i) */
#define CH_SETP         ((fn4_t)0x0803FD27u)        /* cell element i, writable */
#define CH_NOTIFY       ((void (*)(uint32_t))0x0803FBEDu)
#define CH_ON           ((fn4_t)0x08054AE1u)        /* (array, step): the step's on byte */
#define CH_AUD          ((fn4_t)0x0805DA85u)        /* (view): the press auditions */
#define SE_ORIG         ((fn4_t)0x0805DF1Bu)        /* (view, note): the held step's note (creates the step) */
#define SEQ_ORIG        ((fn4l_t)0x0804AFBDu)       /* player: one microstep */

/* struct tm with A1 up and a store, else 0. */
__attribute__((noinline)) static struct tm *ch_tm(void)
{
    struct tm *t = tm();
    return a1() && t && t->store ? t : 0;
}
#define CH_A1           ((struct a1 *)A1_AT)        /* after ch_tm() */

/* The one validity rule of the shape table, for the reader (ch_notes) and the writer (ch_edit_cb): 'C' 'H' CH_VER,
 * count <= 31, and each of the count shapes is 1 to 3 strictly ascending offsets in 1..96, then zeros. */
__attribute__((noinline)) static uint32_t ch_valid(const volatile uint8_t *p)
{
    if (p[0] != 'C' || p[1] != 'H' || p[2] != CH_VER || p[3] > CH_N)
        return 0;
    for (const volatile uint8_t *e = p + 4, *z = e + 3u * p[3]; e < z; e += 3) {
        if (!e[0] || e[0] > 96u || (e[1] && (e[1] <= e[0] || e[1] > 96u)) || (e[2] && (!e[1] || e[2] <= e[1]
                                                                                       || e[2] > 96u)))
            return 0;
    }
    return 1;
}

/* The notes of a chord step: x[0] = byte 0 r, then r + the offsets of key k in the live table (none if k = 0 or the
 * table is invalid (ch_valid) or read-only). Returns the count. */
__attribute__((noinline)) static uint32_t ch_notes(uint32_t r, uint32_t k, uint8_t *x)
{
    struct tm *t = ch_tm();
    const volatile uint8_t *p = t && t->why == TM_OK ? (volatile uint8_t *)(t->store + TM_EXT + CH_AT) : 0;
    uint32_t n = 0;
    x[n++] = (uint8_t)r;
    if (p && ch_valid(p) && k - 1u < p[3])
        for (p += 1u + 3u * k; n < 4u && *p; p++)
            x[n++] = (uint8_t)(r + *p);           /* ascending, 0 = none: the entry ends at its first 0 */
    return n;
}

/* tm_edit callback: the key of shape arg (o0 | o1 << 8 | o2 << 16) into a1->sk, appended or reusing an unused one. */
static uint32_t ch_edit_cb(volatile uint8_t *b, uint32_t arg)
{
    volatile uint8_t *p = b + CH_AT;
    struct a1 *a = CH_A1;
    if (!ch_valid(p)) {
        if (!all_zero(p, 4u + 3u * CH_N))
            return TS_READONLY;               /* invalid content (the reader plays the root only): never overwritten */
        p[0] = 'C';
        p[1] = 'H';
        p[2] = CH_VER;
    }
    uint32_t n = p[3], k = 0, used = 0;
    for (uint32_t i = 0; i < n; i++)
        if ((p[4 + 3 * i] | p[5 + 3 * i] << 8 | p[6 + 3 * i] << 16) == arg) {
            a->sk = (uint8_t)(i + 1u);
            return TS_REFUSED;                /* present: nothing written */
        }
    if (n < CH_N) {
        k = ++n;
        p[3] = (uint8_t)n;
    } else {                                  /* full: the first shape no step uses */
        const volatile uint8_t *e = b - TM_EXT + 0x2E4u + 6u;
        for (uint32_t i = 0; i < 64u; i++, e += i & 7u ? 0xA28u : 0x5140u - 7u * 0xA28u)
            for (uint32_t s = 0; s < 256u; s += 8u)
                used |= 1u << (e[s] >> 1 & 31u);
        for (k = 1; k <= CH_N && (used >> k & 1u); k++)
            ;
        if (k > CH_N)
            return TS_REFUSED;
    }
    p += 1u + 3u * k;
    p[0] = (uint8_t)arg;
    p[1] = (uint8_t)(arg >> 8);
    p[2] = (uint8_t)(arg >> 16);
    a->sk = (uint8_t)k;
    return TS_OK;
}

/* The held step of track 8's Note view h: its on state, byte 0 (*r) and byte 6 cell element (*w6); 32 = none. */
__attribute__((noinline)) static uint32_t ch_held(uint32_t h, uint32_t *r, volatile uint8_t **w6)
{
    uint32_t g = rw(0x20006E94u), s;          /* the controller: grid page + 0x54 + 4t = track t's Note view */
    g = in_sram(g, 4) ? rw(g) : 0;
    g = in_sram(g, 0x40) ? rw(g + 0x3C) : 0;
    if (!ch_tm() || !in_sram(g, 0x74) || h != rw(g + 0x54u + 4u * A1_T)
        || (s = *(volatile uint8_t *)CH_GET(rw(h + 0x2C), 1, 0, 0)) > 31u || !CH_ON(rw(h + 0x24), s, 0, 0))
        return 32;
    uint32_t B = rw(h + 0x28);
    *r = *((volatile uint8_t *)CH_GET(B, s >> 2, 0, 0) + (s & 3u));
    *w6 = (volatile uint8_t *)CH_GET(B, 0x10u + (s >> 2), 0, 0) + (s & 3u);
    return s;
}


/* Element 0 of the cell at a = 1, notified (the two edit marks of the stock step setter at 0x0805DF1A, + 0x7C
 * before and + 0x80 after). */
static void ch_flag(uint32_t a)
{
    *(volatile uint8_t *)CH_SETP(rw(a), 0, 0, 0) = 1;
    CH_NOTIFY(rw(a));
}

uint32_t hook_se(uint32_t h, uint32_t note, uint32_t c, uint32_t d)
{
    uint32_t r, s;
    volatile uint8_t *w6;
    struct a1 *a = CH_A1;
    if ((s = ch_held(h, &r, &w6)) > 31u)
        return SE_ORIG(h, note, c, d);        /* an empty step: stock creates it with this root */
    uint8_t x[4];
    uint32_t n = ch_notes(r, *w6 >> 1 & 31u, x), i = 1, arg = 0, key = 0;
    while (i < n && x[i] != note)
        i++;
    if (i < n)
        x[i] = x[--n];                        /* in the chord: removed */
    else if (n < 4u)
        x[n++] = (uint8_t)note;               /* added */
    for (i = 1; i < n; i++)
        for (uint32_t j = i; j && x[j - 1] > x[j]; j--) {
            uint8_t t = x[j];
            x[j] = x[j - 1];
            x[j - 1] = t;
        }
    for (i = n; i-- > 1;) {
        if (x[i] - x[0] > 96)
            goto keep;                        /* beyond the stored range: no change */
        arg = arg << 8 | (uint32_t)(x[i] - x[0]);
    }
    if (n > 1) {
        a->sk = 0;
        tm_edit(region(), ch_edit_cb, arg);
        if (!(key = a->sk))
            goto keep;                        /* busy, read-only or full: no change */
    }
    if (x[0] != r)
        SE_ORIG(h, x[0], c, d);               /* the new lowest note into byte 0 */
    ch_flag(h + 0x7Cu);                       /* the hold edited the step, as the stock setter marks it (else */
    uint32_t B = rw(h + 0x28);                /* the step pad's release toggles the step off) */
    volatile uint8_t *p = (volatile uint8_t *)CH_SETP(B, 0x10u + (s >> 2), 0, 0) + (s & 3u);
    *p = (uint8_t)((*p & 0xC1u) | key << 1);
    ch_flag(h + 0x80u);
    CH_NOTIFY(B);
    a->sk = (uint8_t)key;
    a->sr = CH_AUD(h, 0, 0, 0) ? x[0] : 0;    /* the press that follows auditions the chord */
    if (!a->sr)
        a->sk = 0;
    return 0;
keep:                                         /* no change; the hold still counts as an edit (else the stock */
    ch_flag(h + 0x7Cu);                       /* toggles the step off at the step pad's release) */
    ch_flag(h + 0x80u);
    return 0;
}

/* The Note view's pad press (vtable word): when it removes a chord step (the root's key: the stock clears the step
 * without its edit marks), the marks are set, so the step pad's release does not re-create a plain step. A step
 * without a chord (key 0) keeps the stock behaviour. */
#define NP_ORIG         ((fn4_t)0x0805D7A1u)        /* (view, pad, velocity) */
uint32_t hook_np(uint32_t h, uint32_t pad, uint32_t vel, uint32_t d)
{
    uint32_t r;
    volatile uint8_t *w6;
    uint32_t k = ch_held(h, &r, &w6) < 32u ? *w6 >> 1 & 31u : 0;
    uint32_t x = NP_ORIG(h, pad, vel, d);
    if (k && ch_held(h, &r, &w6) > 31u) {
        ch_flag(h + 0x7Cu);
        ch_flag(h + 0x80u);
    }
    return x;
}

/* The Note view's observer (both vtable words; this = the view, or the view + 8 for the observer interface). The
 * stock observer repaints only the pads of the notes it changes, so the chord notes' colour (far_col) went stale:
 * it stayed after the selected step or its note changed until each pad was repainted (#3, unit session 021), and
 * a step held showed only its root. After the stock observer, if track 8's Note view has a chord step selected (the
 * held step, ch_held) or still shows a chord-note colour, all 128 pads are repainted (the stock full repaint, which takes far_col through
 * ad_nv_col). Every other view and event is unchanged. */
#define NV_OBS_ORIG     ((fn4_t)0x0805E08Fu)        /* (view, cell, bits): the observer */
#define NV_OBS_ORIG8    ((fn4_t)0x0805E08Du)        /* the same through the interface at view + 8 */
#define NV_ALL          ((fn4_t)0x0805D9B5u)        /* (view): every pad's colour */
#define NV_PAL          0x0805DC84u                 /* literal: the colour id -> pad colour table */
static void nv_chord_repaint(uint32_t h)
{
    uint32_t r;
    volatile uint8_t *w6;
    if (!in_sram(h, 0x80) || !in_sram(rw(h + 4), 4) || !in_sram(rw(rw(h + 4)), 256))
        return;
    if (ch_held(h, &r, &w6) > 31u || !(*w6 >> 1 & 31u)) {   /* no chord step selected (or another view): repaint
                                                               only if a chord colour remains */
        uint32_t g = rw(0x20006E94u);
        g = in_sram(g, 4) ? rw(g) : 0;
        g = in_sram(g, 0x40) ? rw(g + 0x3C) : 0;
        if (!in_sram(g, 0x74) || h != rw(g + 0x54u + 4u * A1_T))
            return;
        uint16_t amber = *((volatile uint16_t *)rw(NV_PAL) + (uint32_t)(*(volatile int16_t *)(h + 0x74) + 2));
        volatile uint16_t *col = (volatile uint16_t *)rw(rw(h + 4));
        uint32_t i = 0;
        while (i < 128u && col[i] != amber)
            i++;
        if (i == 128u)
            return;
    }
    NV_ALL(h, 0, 0, 0);
}

uint32_t hook_nv_obs(uint32_t h, uint32_t c, uint32_t bits, uint32_t d)
{
    uint32_t x = NV_OBS_ORIG(h, c, bits, d);
    nv_chord_repaint(h);
    return x;
}

uint32_t hook_nv_obs8(uint32_t h, uint32_t c, uint32_t bits, uint32_t d)
{
    uint32_t x = NV_OBS_ORIG8(h, c, bits, d);
    nv_chord_repaint(h - 8u);
    return x;
}

uint64_t hook_seqstep(uint32_t pl, uint32_t pat, uint32_t st, uint32_t ms)
{
    struct tm *t = ch_tm();
    uint32_t e = t ? t->store + CH_EL8 : 0;
    struct a1 *a = CH_A1;
    if (e && rw(pl + 0x50) == e && st < 32u) {
        a->sk = *(volatile uint8_t *)(e + (uint32_t)(int32_t)(int16_t)pat * 0xA28u + st * 8u + 6u) >> 1 & 31u;
        a->sr = 0;
        uint64_t r = SEQ_ORIG(pl, pat, st, ms);
        a->sk = 0;
        return r;
    }
    return SEQ_ORIG(pl, pat, st, ms);
}

static int32_t setting(uint32_t off);

typedef uint64_t (*rec_on_fn)(uint32_t, uint32_t, uint32_t, int32_t, uint32_t, uint32_t, uint32_t);
#define REC_ON          ((rec_on_fn)0x08048B79u)    /* recorder vt (0x08067870): note-on (rec, pat, step, off, t, note, vel) */
#define REC_OFF         ((fn4l_t)0x08048E71u)       /* recorder: note-off writes the step (rec, note, t) */

/* Live recording into track 8 (pads, MIDI): note-ons that land in the same step of one pass (start times less than a
 * step, 6 microsteps, apart), or that come within 1 microstep of the first (then at its position, so record quantise
 * does not split a chord), make one chord step: byte 0 = the lowest, the others into the table. The gate is the
 * longest: from the first note-on to the release of the LAST chord note released. The stock writes the step once, at
 * the release of the last note pressed (the stock's current note); while other chord notes are still held that
 * write waits (CH_PEND, the stock entry stays active) and is made at the last release (hook_rec_rel), at the next
 * note-on (its gate then ends at that note-on), or at the stock's flush of all notes (record off, stop). NPP 2..4
 * presses record as the stock (root, key 0). The table full, BUSY or read-only: the stock single note.
 * The accumulator is bound: a note joins only while a chord note is still held and the stock's current note is the
 * accumulator's last note (an abandoned, released or replaced note ends the chord), within the time window, with
 * NPP 1 (NPP 2..4 clears it), in the project it was started in
 * (cg = tm()->gen: after a load, clear or pack apply it is dropped, a waiting step write with it, never into the new
 * project). */
struct chacc {
    uint8_t pos;                              /* pattern << 5 | step */
    uint8_t n;                                /* bits 0..2 notes (0 = empty), bit 3 CH_PEND, bits 4..7 microstep offset */
    uint16_t t;                               /* bits 0..10 the first note-on's start time (recorder clock, microsteps),
                                                 bits 11..14 note[i] still held */
    uint8_t note[4];                          /* in press order; the last is the stock's current note */
};
_Static_assert(sizeof(struct chacc) == sizeof(((struct plx *)0)->pad), "the accumulator is struct plx's spare");
#define CH_ACC          ((volatile struct chacc *)(PLX_AT + __builtin_offsetof(struct plx, pad)))
#define CH_PEND         8u
#define REC_REL         ((fn4l_t)0x08048C59u)       /* recorder vt (0x08067874): note-off (rec, t, note, d) */
#if !AD_LIVE
#error "STEP_CHORDS keeps the accumulator's project in struct a1 cg (AD_LIVE layout)"
#endif

/* 1 if rec is track 8's recorder and A1 is up. */
static uint32_t ch_rec8(uint32_t rec)
{
    struct tm *t = ch_tm();
    return t && in_sram(rec, 0x68) && rw(rec + 0x50) == t->store + CH_EL8;
}

/* The accumulator's notes (0 = none) for track 8's recorder rec; a stale one (another project) is dropped, and a
 * waiting step write with it (its stock entry deactivated, nothing written). */
__attribute__((noinline)) static uint32_t ch_acc(uint32_t rec)
{
    volatile struct chacc *c = CH_ACC;
    uint32_t n = c->n & 7u;
    if (n && CH_A1->cg != (uint8_t)ch_tm()->gen) {
        if (c->n & CH_PEND)
            *(volatile uint32_t *)(rec + 0x64u + 4u * c->note[n - 1]) &= ~1u;
        c->n = 0;
        n = 0;
    }
    return n;
}

/* The waiting chord step written now (gate to t): its held notes count as released, the stock note-off of the
 * chord's last note writes it (hook_rec_off0). */
static uint64_t ch_flush(uint32_t rec, uint32_t t, uint32_t d)
{
    volatile struct chacc *c = CH_ACC;
    c->t &= 0x7FFu;
    return REC_REL(rec, t, c->note[(c->n & 7u) - 1u], d);
}

uint64_t hook_rec_on(uint32_t rec, uint32_t pat, uint32_t step, int32_t off, uint32_t t, uint32_t note, uint32_t vel)
{
    volatile struct chacc *c = CH_ACC;
    if (ch_rec8(rec)) {
        uint32_t n = ch_acc(rec), i = 0, dt = (t - c->t) & 0x7FFu;
        if (n && (c->n & CH_PEND)) {          /* a new note-on ends the waiting chord */
            ch_flush(rec, t, 0);
            n = 0;
        }
        while (i < n && c->note[i] != note)
            i++;
        if (note >= 0x8Cu || setting(SR_NPP) >= 2) {
            c->n = 0;                         /* NPP 2..4 (or no note): no chord, nothing accumulated */
        } else if (n && n < 4u && i == n && (c->t >> 11 & 15u) && *(volatile uint8_t *)(rec + 0x60) == c->note[n - 1]
                   && (dt <= 1u || (c->pos == (pat << 5 | step) && dt < 6u))) {
            pat = c->pos >> 5;                /* joins the chord at its first note's position */
            step = c->pos & 31u;
            off = (int32_t)(int8_t)c->n >> 4;
            c->note[n] = (uint8_t)note;
            c->n = (uint8_t)((c->n & 0xF0u) | (n + 1u));
            c->t |= (uint16_t)(1u << (11u + n));
        } else {
            c->pos = (uint8_t)(pat << 5 | step);
            c->n = (uint8_t)((uint32_t)off << 4 | 1u);
            c->t = (uint16_t)((t & 0x7FFu) | 1u << 11);
            c->note[0] = (uint8_t)note;
            CH_A1->cg = (uint8_t)ch_tm()->gen;
        }
    }
    return REC_ON(rec, pat, step, off, t, note, vel);
}

/* recorder note-off (vtable word): a chord note's release; the last one makes the waiting step write */
uint64_t hook_rec_rel(uint32_t rec, uint32_t t, uint32_t note, uint32_t d)
{
    volatile struct chacc *c = CH_ACC;
    uint32_t n = ch_rec8(rec) ? ch_acc(rec) : 0;
    for (uint32_t i = 0; i < n; i++)
        if (c->note[i] == note)
            c->t &= (uint16_t)~(1u << (11u + i));
    uint64_t r = REC_REL(rec, t, note, d);
    if (n && (c->n & 7u) == n && (c->n & CH_PEND) && !(c->t >> 11 & 15u))
        return ch_flush(rec, t, d);
    return r;
}

static uint64_t ch_rec_off(uint32_t rec, uint32_t note, uint32_t now, uint32_t d, uint32_t all)
{
    volatile struct chacc *c = CH_ACC;
    volatile uint32_t *w = (volatile uint32_t *)(rec + 0x64u + 4u * note);
    if (!ch_rec8(rec) || note >= 0x8Cu || !(*w & 1u))
        return REC_OFF(rec, note, now, d);    /* not track 8, or no step write */
    uint32_t pos = (*w >> 6 & 7u) << 5 | (*w & 0x3Fu) >> 1, n = ch_acc(rec), key = 0, lo = note, arg = 0;
    uint8_t x[4];
    if (n >= 2u && c->pos == pos && c->note[n - 1] == note && setting(SR_NPP) < 2) {
        if (!all && (c->t >> 11 & 15u)) {
            c->n |= CH_PEND;                  /* other chord notes still held: the write waits, the entry stays */
            return (uint64_t)d << 32;         /* as the stock note-off with nothing written */
        }
        *w = (*w & ~(0x7FFu << 16)) | (uint32_t)(c->t & 0x7FFu) << 16;   /* the gate from the first note-on */
        for (uint32_t i = 0; i < n; i++) {
            x[i] = c->note[i];
            for (uint32_t j = i; j && x[j - 1] > x[j]; j--) {
                uint8_t q = x[j];
                x[j] = x[j - 1];
                x[j - 1] = q;
            }
        }
        for (uint32_t i = n; i-- > 1 && x[i] - x[0] <= 96;)
            arg = arg << 8 | (uint32_t)(x[i] - x[0]);
        if (x[n - 1] - x[0] <= 96) {
            CH_A1->sk = 0;
            tm_edit(region(), ch_edit_cb, arg);
            if ((key = CH_A1->sk))
                lo = x[0];
            CH_A1->sk = 0;
        }
    }
    c->n = 0;
    uint64_t r = REC_OFF(rec, note, now, d);
    uint32_t pat = pos >> 5, s = pos & 31u;
    if (*(volatile uint8_t *)CH_GET(rw(rec + 0x14), 0, 0, 0) == pat) {   /* the edit pattern: its mirror (stock) */
        uint32_t B = rw(rec + 0x58);
        *((volatile uint8_t *)CH_SETP(B, s >> 2, 0, 0) + (s & 3u)) = (uint8_t)lo;
        volatile uint8_t *p = (volatile uint8_t *)CH_SETP(B, 0x10u + (s >> 2), 0, 0) + (s & 3u);
        *p = (uint8_t)((*p & 0xC1u) | key << 1);
    } else {                                  /* another pattern: the store (stock clears bit 0 only) */
        volatile uint8_t *e = (volatile uint8_t *)(rw(rec + 0x50) + pat * 0xA28u + s * 8u);
        e[0] = (uint8_t)lo;
        e[6] = (uint8_t)((e[6] & 0xC1u) | key << 1);
    }
    return r;
}

/* the three stock BL sites of the note-off step write; site 1 is the stock's flush of all notes (record off, stop):
 * a waiting chord is written there */
uint64_t hook_rec_off0(uint32_t rec, uint32_t note, uint32_t now, uint32_t d)
{
    return ch_rec_off(rec, note, now, d, 0);
}
uint64_t hook_rec_off1(uint32_t rec, uint32_t note, uint32_t now, uint32_t d)
{
    return ch_rec_off(rec, note, now, d, 1);
}
uint64_t hook_rec_off2(uint32_t rec, uint32_t note, uint32_t now, uint32_t d)
{
    return ch_rec_off(rec, note, now, d, 0);
}

/* far table FAR_CHORD, from chord(): the stashed chord of this press (playback, audition) into *note (the lowest)
 * and t (offsets above it); the stash is consumed. 0 = none stashed (the live rule applies). */
uint32_t far_chord(struct a1 *a, uint32_t src, int32_t *note, int32_t *t)
{
    if (!a->sr && !(a->sk && src == SRC_SEQ))
        return 0;
    uint8_t x[4];
    uint32_t n = ch_notes(a->sr ? a->sr : (uint32_t)(*note + 12), a->sk, x);
    *note = (int32_t)x[0] - 12;
    for (uint32_t v = 1; v < n; v++)
        t[v] = x[v] - x[0];
    a->sk = a->sr = 0;
    return n;
}

/* far table FAR_COL, from ad_nv_col: the colour of key note kn in track 8's Note view nv if it is a chord note of
 * the held step (the held-root colour + 2), else 0. Only while the stock shows the held step's root (element 0 of
 * the cell at view + 0x7C set: the stock Note view colour function (NV_COL) tests it at 0x0805DB14; it is set by the hold
 * timer about 300 ms after the step press, or at once by a key press, and cleared on release), so the chord notes
 * come and go with the red root (unit session 022: they came before it and stayed after the release). */
uint32_t far_col(uint32_t nv, uint32_t kn)
{
    uint32_t r;
    volatile uint8_t *w6;
    uint8_t x[4];
    if (kn && ch_held(nv, &r, &w6) < 32u && *(volatile uint8_t *)CH_GET(rw(nv + 0x7Cu), 0, 0, 0))
        for (uint32_t i = 1, n = ch_notes(r, *w6 >> 1 & 31u, x); i < n; i++)
            if (x[i] == kn)
                return (uint32_t)*(volatile int16_t *)(nv + 0x74) + 2u;
    return 0;
}
#endif

/* 6: the Note view's pad colour (view, index, note) marks, with setting C = 1, only the pads at the slice
 * markers (+ 0x194 / + 0x195), with C = 0 the pads whose index has a partner (+ 0x114: the root pads). For track 8's
 * keyboard the markers are set for this one call to what C = 0 would mark (the index itself if it has a partner,
 * else none), then restored; with C = 0 the stock code does not read them, so the result is the C = 0 colour
 * either way. */
static uint32_t ad_nv_col(uint32_t nv, uint32_t idx, uint32_t note, uint32_t d)
{
    uint32_t kb = in_sram(nv, 0x50) ? rw(nv + 0x4C) : 0, q8;
    if (!a1() || idx > 127 || !in_sram(kb, 0x1A0) || *(volatile uint8_t *)(kb + KB_ROWS) == 0 || !(q8 = ad_qb8())
        || rw(kb + 0x198) != q8)
        return NV_COL(nv, idx, note, d);
#if STEP_CHORDS
    uint32_t c = far_call(FAR_COL, nv, *(volatile uint8_t *)(kb + 8 + idx), 0, 0);
    if (c)
        return c;                             /* a chord note of the held step */
#endif
    volatile uint8_t *m = (volatile uint8_t *)(kb + 0x194);
    uint8_t m0 = m[0], m1 = m[1];
    m[0] = m[1] = *(volatile uint8_t *)(kb + KB_PAIR + idx) != 0xFF ? (uint8_t)idx : 0xFF;
    uint32_t r = NV_COL(nv, idx, note, d);
    m[0] = m0;
    m[1] = m1;
    return r;
}

uint32_t hook_nv_col0(uint32_t nv, uint32_t idx, uint32_t note, uint32_t d) { return ad_nv_col(nv, idx, note, d); }
uint32_t hook_nv_col1(uint32_t nv, uint32_t idx, uint32_t note, uint32_t d) { return ad_nv_col(nv, idx, note, d); }

/* 7: the track's own note instead of the slice note. With setting C = 1 two stock paths use router note 12 (slice 0)
 * where a track that is not slicing uses its note cell (element 0 of a per-track cell: the last played note, router
 * scale, 72 = C at boot):
 *   hook_sv_press: the Sample view's pad press, the audition note;
 *   hook_step_note: a step set while a Sample-view pad is held, the step's note.
 * Both read C from track t's value cell (element 0xC); the object's cell pointer identifies the track. */
static volatile uint8_t *ad_c8(uint32_t qb)
{
    uint32_t q = ad_qb8();
    return a1() && q && qb == q ? (volatile uint8_t *)obs_el(q, 0xC) : 0;
}

#if FM_BANKVIEW
/* ---- Track 8's Sample view selects the FM ratio family and algorithm ---------------------------------------------
 * Slots 0..31 are valid (page 0: families under serial4, page 1: under dual_stack, both in block 0); pages 2..7
 * (slots 32..127, blocks 1..3) are dark and inert. Our hooks: the view's press and release (select a family and
 * algorithm, or swallow an inert press), its observer (the invalid positions dark again after every redraw) and the
 * Main pass (sv_entry: open the page of the selected base). The offsets in the code are the fields it reads and
 * writes. */
#define SV_VT           0x08068294u                 /* Sample view vtable (word 0: press, 1: release) */
#define SV_REL          ((fn4_t)0x0805C2BFu)        /* its release (vtable word 0x08068298) */
#define SV_OBS          ((fn4_t)0x0805C405u)        /* its observer entry (vtable word 0x080682C0, this = view + 8) */
#define SV_COLTAB       0x08062AB0u                 /* the view's colour table (u16 palette values) */
#define SV_OFF          0u                          /* palette value 0: dark (the stock Note view's unlit pads) */

/* The view's block (element 0 of its block cell, the whole byte) and colour buffer, 0 if they do not check out. */
static volatile uint16_t *sv_col(uint32_t view)
{
    uint32_t o = rw(view + 4), b = in_sram(o, 4) ? rw(o) : 0;
    return in_sram(b, 64) ? (volatile uint16_t *)b : 0;
}

/* Every buffer position whose slot (32 x block + position) is not valid is dark: the whole buffer for any block but
 * 0 (block 0 is pages 0 and 1, all 32 slots valid). Main task (input, observers). */
__attribute__((noinline)) static void sv_dark(uint32_t view)
{
    volatile uint16_t *col = sv_col(view);
    uint32_t blk = *(volatile uint8_t *)obs_el(rw(view + 0x1C), 0);
    for (uint32_t i = 0; col && blk && i < 32; i++)
        col[i] = SV_OFF;
}

/* Track 8's Sample view, 0 otherwise (the object checks out and its track cell is track 8's). */
static uint32_t sv_t8(uint32_t view)
{
    return in_sram(view, 0x58) && rw(view) == SV_VT && ad_c8(rw(view + 0x14)) ? view : 0;
}

/* The view's observer: the stock observer first, then the invalid positions dark again. On a change to block 0 the
 * plain colour of all 32 positions is restored first (an earlier block left them dark). */
uint32_t hook_sv_obs(uint32_t th, uint32_t obs, uint32_t c, uint32_t d)
{
    uint32_t view = sv_t8(th - 8);
    volatile uint16_t *col = view ? sv_col(view) : 0;
    if (col && obs == rw(view + 0x1C) && *(volatile uint8_t *)obs_el(obs, 0) == 0) {
        uint16_t plain = *(const volatile uint16_t *)(SV_COLTAB + 2u * (*(volatile uint16_t *)(view + 0x24) + 2u));
        for (uint32_t i = 0; i < 32; i++)
            col[i] = plain;
    }
    uint32_t r = SV_OBS(th, obs, c, d);
    if (col)
        sv_dark(view);
    return r;
}

/* Main pass: track 8's Sample view in front (host_ui snapshot: track 8, view 0, front) that was not at the previous
 * pass, or a project applied since: the page of the selected base is set in the page cell and notified, as an Up
 * press does, then the invalid positions go dark. The selected base is track 8's store byte (SV_BASE_AT), live channel parameter 0 only when no base is set (0xFF);
 * 0..15 opens page 0, 16..31 page 1, anything else page 0. Read-only for the selection: the base, parameter 0 and the
 * FM template are never written. Leaving and coming back applies the rule again; Up and Down browse freely while the
 * view stays in front (they select nothing). */
__attribute__((noinline)) static void sv_entry(void)
{
    volatile struct pg *g = PG;               /* the caller holds a1(), so the region and struct tm are live */
    struct tm *t = TM;
    uint8_t gen = (uint8_t)t->gen;
    if (((volatile struct state *)REGION)->ui != (0x80010000u | A1_T)) {
        g->sv_in = 0;
        return;
    }
    if (g->sv_in && g->sv_gen == gen)
        return;
    /* the snapshot was taken this pass through checked links (ui_snap: lambda, controller and grid page checks) */
    uint32_t grid = rw(rw(rw(BTN_CELL + 4)) + 0x3C), pg8 = rw(grid + 0x19C + 4 * A1_T);
    uint32_t view = sv_t8(rw(grid + 0x14 + 4 * A1_T)), cell = in_sram(pg8, 0x14) ? rw(pg8 + 0x10) : 0;
    if (!view || !in_sram(cell, 0x14))
        return;                               /* not resolvable yet: again at the next pass */
    uint32_t base = t->store ? *(volatile uint8_t *)(t->store + SV_BASE_AT + A1_T) : 0xFFu, ch;
    if (base == 0xFFu)                        /* no base set: live parameter 0 */
        base = (ch = pl_channel(rw(PL_R_CELL), A1_T)) ? PL_GET(ch, 0) : 0;
    uint8_t page = (uint8_t)(base < FM_SLOTS ? base >> 4 : 0);
    if (*(volatile uint8_t *)obs_el(cell, 0) != page) {
        *(volatile uint8_t *)OBS_SETP(cell, 0) = page;
        NOTIFY(cell);
    }
    sv_dark(view);
    g->sv_in = 1;
    g->sv_gen = gen;
}

/* The press decides its physical pad (bit (arg & 15): arg = pad - 16 + 16 x (page & 1)). On track 8's view the full
 * slot is computed in 32 bits from arg and the whole block byte; it selects (family slot & 15, algorithm slot >> 4)
 * only for arg <= 31 and slot < FM_SLOTS (32), which also requires block 0 (the note's block <= 3 is implied). Any
 * other press is inert: nothing reaches the stock press (no slot write, no audition, no held pad), and the pad's bit
 * is set so its release is swallowed as well. Every other press clears its pad's bit. */
uint32_t hook_sv_press(uint32_t obj, uint32_t pad, uint32_t vel, uint32_t d)
{
    volatile uint8_t *c = in_sram(obj, 0x58) ? ad_c8(rw(obj + 0x14)) : 0;
    uint16_t bit = (uint16_t)(1u << (pad & 15u));
    if (c) {
        uint32_t blk = *(volatile uint8_t *)obs_el(rw(obj + 0x1C), 0), slot = pad + 32u * blk;
        if (pad > 31u || slot >= FM_SLOTS) {
            PG->sv_mask |= bit;
            return d;
        }
    }
    PG->sv_mask &= (uint16_t)~bit;
#else
/* The audition's note is sent from inside the stock function: setting C reads 0 for that one call (the value cell's
 * byte, no notify, restored right after; Main task, which is where the cell is read). */
uint32_t hook_sv_press(uint32_t obj, uint32_t pad, uint32_t vel, uint32_t d)
{
    volatile uint8_t *c = in_sram(obj, 0x58) ? ad_c8(rw(obj + 0x14)) : 0;
#endif
    uint8_t was = c ? *c : 0;
    if (was)
        *c = 0;
    uint32_t r = SV_PRESS(obj, pad, vel, d);
    if (was)
        *c = was;
    return r;
}

#if FM_BANKVIEW
/* The release of an inert press is swallowed (any view of the Sample-view vtable, any page: the bit belongs to the
 * physical pad), so it can never end an earlier valid audition held on another pad. */
uint32_t hook_sv_rel(uint32_t obj, uint32_t pad, uint32_t c, uint32_t d)
{
    uint16_t bit = (uint16_t)(1u << (pad & 15u));
    if (PG->sv_mask & bit) {
        PG->sv_mask &= (uint16_t)~bit;
        return d;
    }
    return SV_REL(obj, pad, c, d);
}
#endif

/* The step's note is written into the step's array; with C = 1 it is rewritten with the note cell, as C = 0 writes it
 * (the step's other fields do not depend on C). */
uint32_t hook_step_note(uint32_t obj, uint32_t pad, uint32_t c3, uint32_t d)
{
    uint32_t r = STEP_NEW(obj, pad, c3, d);
    volatile uint8_t *c = in_sram(obj, 0x28) ? ad_c8(rw(obj + 0x24)) : 0;
    uint32_t n = c && *c ? rw(obj + 0x10) : 0;
    if (in_sram(n, 0x14))
        STEP_SET(rw(obj + 0xC), pad, *(volatile uint8_t *)obs_el(n, 0), 0);
    return r;
}

/* 8: two more reads of setting C, each through the stock cell getter at a call site of its own; for track 8's cell
 * the getter returns a zero byte instead (nothing is written):
 *   hook_oct_c: "the selected track's view has octaves" (from track 8's value cell, element 0xC). With C = 1 the
 *     Up / Down arrows are dark and inert.
 *   hook_xv_c: entering (and leaving) the expanded Note view with the selected track's settings cell (the slice
 *     converter's, + 0x2C [track]) at C = 1 starts the slice select state: no pressed-pad light, the slice overlay. */
#define OBS_GET         ((fn4_t)0x0803FC59u)        /* cell getter (cell, i): element i of the current values */
#define RT_VT           0x080694B0u                 /* note router */

__attribute__((noinline)) static uint32_t ad_zero(void)
{
    uint32_t p;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t.word 0\n2:" : "=r"(p));
    return p;
}

/* Track 8's settings cell in the slice converter (MIDI-in source -> router -> converter + 0x2C [7]); 0 if unknown. */
static uint32_t ad_st8(void)
{
    uint32_t p = rw(MIDI_ROOT);
    for (uint32_t i = 0, hop = 0; i < 4; i++, hop = i == 1 ? 0x24 : 4) {
        if (!in_sram(p, 0x28))
            return 0;
        p = rw(p + hop);
    }
    if (!in_sram(p, 0xA0) || rw(p) != RT_VT || !in_sram(rw(p + 0x9C), 8))
        return 0;
    uint32_t nm = rw(rw(p + 0x9C) + 4);
    if (!in_sram(nm, 0x30) || rw(nm) != NM_VT)
        return 0;
    uint32_t arr = rw(nm + 0x2C), stride = rw(nm + 0x24);
    return stride <= 0x100 && in_sram(arr + stride * A1_T, 4) ? rw(arr + stride * A1_T) : 0;
}

static uint32_t ad_c_get(uint32_t cell, uint32_t i, uint32_t q8)
{
    return a1() && i == 0xC && q8 && cell == q8 ? ad_zero() : OBS_GET(cell, i, 0, 0);
}

uint32_t hook_oct_c(uint32_t cell, uint32_t i, uint32_t c, uint32_t d) { return ad_c_get(cell, i, ad_qb8()); }
uint32_t hook_xv_c(uint32_t cell, uint32_t i, uint32_t c, uint32_t d) { return ad_c_get(cell, i, ad_st8()); }

uint32_t hook_mp_obs(uint32_t th, uint32_t obs, uint32_t bits, uint32_t d)
{
    uint32_t r = MP_OBS(th, obs, bits, d);
    ad_noslice_page(th - 8);
    return r;
}

/* 2: track 8's Start / Length lock lanes stay enabled. */
uint32_t hook_nm_obs(uint32_t th, uint32_t obs, uint32_t bits, uint32_t d)
{
    uint32_t r = NM_OBS(th, obs, bits, d), nm = th - 4;
    if (a1() && in_sram(nm, 0x60) && rw(nm) == NM_VT && *(volatile uint8_t *)(nm + 0x54 + A1_T) == 1) {
        uint32_t arr = rw(nm + 0x2C), stride = rw(nm + 0x24);
        if (stride <= 0x100 && in_sram(arr + stride * A1_T, 4) && obs == rw(arr + stride * A1_T))
            NM_LANES_ON(nm, A1_T, 0, 0);
    }
    return r;
}

/* 3: track 8's saved Start / Length are the live values (a held p-lock lane: its base and base LSB, as
 * hook_save_ser writes them), also when track 8 is slicing. For a track that is not slicing this rewrites the same
 * bytes. */
uint32_t hook_save_post(uint32_t a, uint32_t b, uint32_t c, uint32_t store, uint32_t e, uint32_t f)
{
    uint32_t P = f - 0x10u, ch = a1() && store_of(P) == store ? pl_channel(rw(P + 0x54), A1_T) : 0;
    struct plk *p = ch ? plk() : 0;
    if (p) {
        uint32_t buf = store + PL_SER_AT + PL_SER_LEN * A1_T;
        for (uint32_t k = 1; k <= 2; k++) {   /* lane 1 = params 7 / 14, lane 2 = params 8 / 15 */
            uint8_t v = p->slot[A1_T][k];
            pl_put(ch, 6 + k, buf, v & PL_LOCK ? v & 0x7Fu : PL_GET(ch, 6 + k) & 0x7Fu);
            pl_put(ch, 13 + k, buf, v & PL_LOCK ? PLX->lsb[A1_T][k - 1] : PL_GET(ch, 13 + k) & 0x7Fu);
        }
    }
#if FM_PATCH
    pg_fm_save(store);                        /* the FM view's pending macro deltas are in the saved slot */
#endif
    return SAVE_POST(a, b, c, store, e, f);
}
#else
#define AD_BARRIER(a)   ((void)0)
#endif

/* ---- the five note ring write sites ----------------------------------------------------------------------------- */

static void a1_ring(uint32_t *pair, uint32_t origin)
{
    struct a1 *a = a1();
    if (!a) {
        ORIG_RING(pair);
        return;
    }
#if CORE0_SYNTH
    if (Q0->busy) {                           /* re-entered inside our own transaction (unreachable by inference: no
                                                 stock ring writer runs inside it); never lock or touch N here */
        Q0->c_reent++;
        return;
    }
#endif
    if (vc_stock_ch7(pair[0])) {
        if (origin == O_SLOT) {               /* a sample-slot change on track 8 (exempt) */
            a->c_ch7_slot++;
            return;
        }
        a->c_unexpected++;                    /* an unhooked producer reached voice 7: published as is, and an off
                                                 also stops the synth as every forced off does */
        if (vc_kind(pair[0]) == VK_STOCK_OFF) {
            a->c_why[WHY_STOCK7]++;
            if (a->phase == PH_OPEN)
                issue_alloff(a);
            else
                a->c_gated++;
        }
    }
    put_n(a, pair[0], pair[1], 0);
}

void hook_ring_slot(uint32_t *pair) { a1_ring(pair, O_SLOT); }      /* sample-slot change */
void hook_ring_retrig(uint32_t *pair) { a1_ring(pair, O_RETRIG); }  /* retrigger forced off */
void hook_ring_on(uint32_t *pair) { a1_ring(pair, O_ON); }          /* note on */
void hook_ring_force(uint32_t *pair) { a1_ring(pair, O_FORCE); }    /* forced off */
void hook_ring_rel(uint32_t *pair) { a1_ring(pair, O_REL); }        /* release */

/* ---- voice table (Main task) ---------------------------------------------------------------------------------- */

static void table_reset(struct a1 *a)
{
    for (uint32_t v = 0; v < 4; v++)
        a->v[v].state = V_FREE, a->v[v].cut = 0, a->v[v].inst = 0, a->v[v].pub_at = 0;
#if AD_LIVE
    for (uint32_t v = 0; v < 4; v++)
        a->dl[v] = 0;
#endif
    for (uint32_t i = 0; i < NINST; i++)
        a->in[i].ser = 0;
    a->lane = 0;
#if CORE0_SYNTH
    Q0->sus = 0;
#endif
}

/* Every forced-off path: class-A ALL OFF (purges queued onsets issued before it; the DSP damps the voices and
 * cancels pending onsets). A project change also starts a new project epoch (queued onsets and patch entries of
 * the old one are purged here and filtered at publication) and publishes the patch after the ALL OFF. */
static void all_off(struct a1 *a, uint32_t why, uint32_t project)
{
#if CORE0_SYNTH
    REJ_CLEAR(a->phase != PH_OPEN);           /* a global boundary: no outstanding rejected press survives it
                                                 (CORE0_RECOVER: CLOSED drops survive a boundary while closed) */
#endif
    if (a->phase != PH_OPEN) {                /* closed / not yet open: the table is reset, nothing is published */
        a->c_gated++;
        a->c_why[why]++;
        table_reset(a);
        return;
    }
    uint32_t sem = eng_lock();
    if (project) {
        a->pepoch++;
        NQ_PURGE(a, 15, 1, 1, (uint16_t)(a->seq + 1));
    }
    voice_emit_alloff(a);
    if (project)
        voice_emit_patch(a);
    AD_BARRIER(a);                            /* the current attack/decay behind the ALL OFF, before later notes */
    a->reset_seen = a->reset_seq;
    eng_unlock(sem);
    table_reset(a);
    a->c_why[why]++;
}

/* Boot publication, project change (T1 generation at sites 1..3), admission recovery: before any table use. */
static void a1_sync(struct a1 *a)
{
    struct tm *t = tm();
    if (a->phase != PH_OPEN)                  /* closed: nothing of ours is published; the activation hooks open */
        return;
    if (t && t->gen != a->tm_gen) {
        a->tm_gen = t->gen;
        if (t->site != 0)
            all_off(a, WHY_PROJECT, 1);
    }
    if (a->reset_seen != a->reset_seq) {      /* written under the engine lock by recover() */
        uint32_t sem = eng_lock();
        a->reset_seen = a->reset_seq;
        eng_unlock(sem);
        table_reset(a);
    }
}

static int32_t setting(uint32_t off)
{
    struct tm *t = tm();
    return syn_rec_get(t ? t->rec[A1_T] : 0, off);
}

static uint32_t fold(int32_t p)
{
    while (p > 119)
        p -= 12;
    while (p < 0)
        p += 12;
    return (uint32_t)p;
}

#if SCALES
/* SCALES builds: the scale and key root are project-wide (scales.c: the sub-record at T1 block + 0x288,
 * falling back to this record's +6/+7 while it is unset); the scale table is synth_settings.h's, shared with the
 * sample tracks. Degree d of an n-note scale; the in-key chord stacks every other degree as before. */
#define CHORD_SCALE()   syn_scale_mask(sc_scale())
#define CHORD_ROOT()    ((int32_t)sc_root())
#define CHORD_DEGREES(m) syn_scale_size(m)
static uint32_t scale_semi(uint32_t mask, uint32_t i)
{
    return syn_scale_semi(mask, i);
}
#else
#define CHORD_SCALE()   ((uint32_t)setting(SR_SCALE))
#define CHORD_ROOT()    setting(SR_ROOT)
#define CHORD_DEGREES(m) 7u
static uint32_t scale_semi(uint32_t minor, uint32_t i)
{
    uint32_t d = i % 7, s;
    if (minor)
        s = d == 0 ? 0 : d == 1 ? 2 : d == 2 ? 3 : d == 3 ? 5 : d == 4 ? 7 : d == 5 ? 8 : 10;
    else
        s = d == 0 ? 0 : d == 1 ? 2 : d == 2 ? 4 : d == 3 ? 5 : d == 4 ? 7 : d == 5 ? 9 : 11;
    return 12 * (i / 7) + s;
}
#endif

/* Chord of the played note (modules/chord.c rules); returns the note count. */
#if STEP_CHORDS
static uint32_t chord(int32_t note, uint8_t *out, struct a1 *a, uint32_t src)
#else
static uint32_t chord(int32_t note, uint8_t *out)
#endif
{
    uint32_t n = (uint32_t)setting(SR_NPP), minor = CHORD_SCALE();
    int32_t oct = 12 * setting(SR_OCTAVE);
    int32_t t[4] = {0, 0, 0, 0};
#if STEP_CHORDS                               /* a stashed step chord (playback, audition): its notes verbatim */
    uint32_t k = far_call(FAR_CHORD, (uint32_t)a, src, (uint32_t)&note, (uint32_t)t);
    if (k)
        n = k;
    else
#endif
    if (n >= 2) {
        if (setting(SR_INKEY)) {
            uint32_t rel = (uint32_t)(((note - CHORD_ROOT()) % 12 + 12) % 12), d = 0;
            for (uint32_t k = 0; k < CHORD_DEGREES(minor); k++)
                if (scale_semi(minor, k) <= rel)
                    d = k;                    /* the degree of the note, or the degree below it */
            for (uint32_t v = 0; v < n; v++)
                t[v] = (int32_t)(scale_semi(minor, d + 2 * v) - scale_semi(minor, d));
        } else {
            uint32_t ty = (uint32_t)setting(SR_TYPE);
            int32_t third = ty == 0 || ty == 4 ? 4 : ty == 1 || ty == 5 ? 3 : ty == 2 ? 2 : 5;
            t[1] = third;
            if (ty >= 4) {
                t[2] = n == 4 ? 7 : 10;
                t[3] = 10;
            } else {
                t[2] = 7;
                t[3] = 12;
            }
        }
        uint32_t vo = (uint32_t)setting(SR_VOICING);
        if (vo == 3)
            t[1] += 12;
        else
            for (uint32_t i = 0; i < vo; i++)
                t[i] += 12;
        for (uint32_t i = 1; i < n; i++)
            for (uint32_t j = i; j > 0 && t[j - 1] > t[j]; j--) {
                int32_t x = t[j];
                t[j] = t[j - 1];
                t[j - 1] = x;
            }
    } else {
        n = 1;
    }
    for (uint32_t v = 0; v < n; v++)
        out[v] = (uint8_t)fold(note + t[v] + oct);
    return n;
}

/* FREE = never used since the last ALL OFF, or killed (no DSP evidence is needed: a KILL or ALL OFF went first).
 * AGED = published at least AGE_TICKS ago (pub_at is TICK() | 1, so it can be one tick ahead of a press in the
 * same tick: signed compare). Aged voices are the first steal candidates; a steal always emits KILL first. */
static uint32_t is_free(const struct a1voice *x)
{
    return x->state == V_FREE;
}

static uint32_t is_aged(struct a1 *a, uint32_t v, uint32_t now)
{
    const struct a1voice *x = &a->v[v];
#if CORE0_SYNTH                               /* snapshot: a voice held at a sustain > 0 has no deadline */
    return x->state != V_FREE && x->pub_at && !(Q0->sus >> v & 1u) && (int32_t)(now - a->dl[v]) >= 0;
#elif AD_LIVE                                 /* the per-voice deadline from the attack + decay in force */
    return x->state != V_FREE && x->pub_at && (int32_t)(now - a->dl[v]) >= 0;
#else
    (void)a;
    return x->state != V_FREE && x->pub_at && (int32_t)(now - x->pub_at) >= (int32_t)AGE_TICKS;
#endif
}

/* free (lowest index) -> oldest released (cut first, then release serial) -> oldest held (onset serial). */
static uint32_t pick(struct a1 *a, uint32_t excl, uint32_t now)
{
    int32_t best = -1;
    for (uint32_t v = 0; v < 4; v++)
        if (!(excl >> v & 1) && is_free(&a->v[v]))
            return v;
    for (uint32_t v = 0; v < 4; v++)          /* aged candidates: the oldest onset first */
        if (!(excl >> v & 1) && is_aged(a, v, now) &&
            (best < 0 || ser_cmp(a->v[v].on_ser, a->v[best].on_ser) < 0))
            best = (int32_t)v;
    if (best >= 0)
        return (uint32_t)best;
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if ((excl >> v & 1) || x->state != V_REL)
            continue;
        if (best < 0)
            best = (int32_t)v;
        else {
            struct a1voice *b = &a->v[best];
            if (x->cut > b->cut || (x->cut == b->cut && ser_cmp(x->rel_ser, b->rel_ser) < 0))
                best = (int32_t)v;
        }
    }
    if (best >= 0)
        return (uint32_t)best;
    for (uint32_t v = 0; v < 4; v++) {
        if (excl >> v & 1)
            continue;
        if (best < 0 || ser_cmp(a->v[v].on_ser, a->v[best].on_ser) < 0)
            best = (int32_t)v;
    }
    return (uint32_t)best;
}

static struct a1inst *inst_of(struct a1 *a, uint32_t ser)
{
    for (uint32_t i = 0; i < NINST; i++)
        if (ser && a->in[i].ser == ser)
            return &a->in[i];
    return 0;
}

static struct a1inst *inst_new(struct a1 *a)
{
    struct a1inst *best = 0;
    for (uint32_t i = 0; i < NINST; i++)
        if (!a->in[i].ser)
            return &a->in[i];
    for (uint32_t i = 0; i < NINST; i++)    /* full: drop the oldest record that owns no voice */
        if (!a->in[i].mask && (!best || ser_cmp(a->in[i].ser, best->ser) < 0))
            best = &a->in[i];
    a->c_lost_inst++;
    return best ? best : &a->in[0];
}

static void a1_press(struct a1 *a, uint32_t src, int32_t note, uint32_t vel)
{
    Q0_FLUSH(a);                              /* deferred boundaries first: never applied to a later note */
    a1_sync(a);
    if (a->phase != PH_OPEN) {                /* activation: admission closed (dropped, counted) */
        a->c_pre_enable++;
#if CORE0_RECOVER
        q0_rej_drop(src, note);               /* recorded, so its late off is swallowed after the reopen */
#endif
        return;
    }
    uint8_t out[4], notes[4] = {0, 0, 0, 0};
#if STEP_CHORDS
    uint32_t n = chord(note, out, a, src), now = TICK();
#else
    uint32_t n = chord(note, out), now = TICK();
#endif
#if PAD_POLY                                  /* pads join MIDI: true pad chords with POLY on (master 2026-10-21) */
    uint32_t poly = (src == SRC_MIDI || src == SRC_PAD) && n == 1 && setting(SR_POLY);
#else
    uint32_t poly = src == SRC_MIDI && n == 1 && setting(SR_POLY);
#endif
    uint32_t sem = eng_lock();
#if CORE0_SYNTH
    if (Q0->bulk || Q0->rej_quar) {           /* inside a bulk SHI interval, or in rejection quarantine: refused */
        if (Q0->bulk)
            Q0->c_bulk_drop++;
        q0_rej_add(src, note);                /* its off must not release an older instance */
        eng_unlock(sem);
        return;
    }
#elif AD_LIVE
    ad_service(a, ad_read(a), AD_NOTE);       /* changed values always go ahead of this note */
#endif
    if (!voice_onset_room(a, n)) {               /* dropped before allocation: the table is untouched */
        a->c_drop_on++;
#if CORE0_SYNTH
        q0_rej_add(src, note);
#endif
        eng_unlock(sem);
        return;
    }
#if CORE0_SYNTH
    q0_rej_accept(src, note);                 /* accepted: every older rejection of this pitch is superseded */
#endif
    struct a1inst *in = inst_new(a);
    uint32_t ser = ++a->serial;
    if (!ser)
        ser = ++a->serial;                    /* 0 means "none" */
    uint32_t cutm = 0, killm = 0, mask = 0;
    if (!poly) {                              /* chord-mono lane: the previous lane instance is replaced */
        for (uint32_t v = 0; v < 4; v++) {
            struct a1voice *x = &a->v[v];
            if (a->lane && x->inst == a->lane && !is_free(x)) {
                if (x->state == V_HELD)
                    x->rel_ser = ++a->serial;
                x->state = V_REL;
                x->cut = 1;
                cutm |= 1u << v;
            }
        }
        struct a1inst *old = inst_of(a, a->lane);
        if (old)
            old->mask = 0;
        a->lane = ser;
    }
    for (uint32_t i = 0; i < n; i++) {
        uint32_t v = pick(a, mask, now);
        struct a1voice *x = &a->v[v];
        struct a1inst *own = x->state == V_HELD ? inst_of(a, x->inst) : 0;
        if (own && own != in)
            own->mask &= (uint8_t)~(1u << v);   /* stolen */
        if (!is_free(x))
            killm |= 1u << v;                 /* every steal (aged, released or held): KILL(v, gen) before the ONSET */
        x->state = V_HELD;
        x->cut = 0;
        x->inst = ser;
        x->src = (uint8_t)src;
        x->note = out[i];
        x->on_ser = ++a->serial;
        x->pub_at = 0;
        notes[v] = out[i];
        mask |= 1u << v;
    }
    in->ser = ser;
    in->src = (uint8_t)src;
    in->root = (uint8_t)(note & 0x7F);
    in->mask = (uint8_t)mask;
    cutm &= ~mask;
    if (cutm | killm) {                       /* one merged KILL: stolen voices and cut voices the chord did not take;
                                                 published before the ONSET (P before N) with the mirror gens */
        voice_emit_kill(a, cutm | killm);
        for (uint32_t v = 0; v < 4; v++)
            if (cutm >> v & 1)
                a->v[v].state = V_FREE, a->v[v].cut = 0;
    }
#if CORE0_SYNTH                               /* after the KILL boundary (its purge tidies orphan groups): the press's
                                                 envelope group (if changed), then its ONSETs with the Tune snapshot */
    Q0->busy |= B_HOLD;                       /* the group and the ONSETs go out together (one hold if they fit) */
    ad_service(a, ad_read(a), AD_NOTE);
#if CORE0_TONE
    q0_tone(a, q0_tone_read(), TONE_NOTE);    /* changed tone values go ahead of the note (pacing exception) */
#endif
    Q0->busy &= (uint8_t)~B_HOLD;
    voice_emit_onset_t(a, mask, notes, vel, q0_tune());
#else
    voice_emit_onset(a, mask, notes, vel);
#endif
    eng_unlock(sem);
}

/* The newest unreleased instance with this source and root releases (no command under the AD). */
#if CORE0_SYNTH
/* An off bound at its arrival (core0feed.c, press ownership): the newest instance of src / note whose serial is not
 * newer than snap, the value of a->serial when the off arrived. */
static void a1_release_at(struct a1 *a, uint32_t src, int32_t note, uint32_t snap)
{
    a1_sync(a);
    struct a1inst *best = 0;
    for (uint32_t i = 0; i < NINST; i++) {
        struct a1inst *in = &a->in[i];
        if (in->ser && in->src == src && in->root == (uint8_t)(note & 0x7F) && ser_cmp(in->ser, snap) <= 0
                && (!best || ser_cmp(in->ser, best->ser) > 0))
            best = in;
    }
    if (!best) {
        a->c_stale_off++;
        return;
    }
    uint32_t sem = eng_lock();
    q0_release_voices(a, best->mask, best->ser);
    best->ser = 0;
    ad_drain(a);
    eng_unlock(sem);
}

static void a1_release(struct a1 *a, uint32_t src, int32_t note)
{
    Q0_FLUSH(a);                              /* older staged offs (each already bound at its arrival) go first */
    if (q0_rej_off(src, note)) {              /* decided now: the off of a rejected press is swallowed */
        a->c_stale_off++;
        return;
    }
    a1_release_at(a, src, note, a->serial);
}
#else
static void a1_release(struct a1 *a, uint32_t src, int32_t note)
{
    Q0_FLUSH(a);                              /* an older staged off resolves before this one */
    a1_sync(a);
    struct a1inst *best = 0;
    for (uint32_t i = 0; i < NINST; i++) {
        struct a1inst *in = &a->in[i];
        if (in->ser && in->src == src && in->root == (uint8_t)(note & 0x7F) && (!best || ser_cmp(in->ser, best->ser) > 0))
            best = in;
    }
    if (!best) {
        a->c_stale_off++;
        return;
    }
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if ((best->mask >> v & 1) && x->inst == best->ser && x->state == V_HELD) {
            x->state = V_REL;
            x->rel_ser = ++a->serial;
        }
    }
    best->ser = 0;
}
#endif

/* ---- hooks: the tap -------------------------------------------------------------------------------------------- */

static int32_t tap_source(struct a1 *a, uint32_t port, uint32_t lr)
{
    uint32_t ours = lr >= A1_PATCH_START && lr < A1_PATCH_END;
    if (!a)
        return -1;
    if (port == A1_T)
        return ours ? SRC_MODULE : SRC_PAD;
    if (port == 0x08u + A1_T)
        return SRC_SEQ;
    if (port == 0x10u + A1_T)
        return SRC_MIDI;
    if (port == 0x18u + A1_T && !a->depth)
        return ours ? SRC_MODULE : SRC_OTHER;
    return -1;
}

uint32_t hook_ron(uint32_t r, uint32_t n12, uint32_t vel, uint32_t port, uint32_t a5)
{
    struct a1 *a = a1();
    int32_t src = tap_source(a, port, (uint32_t)__builtin_return_address(0));
    if (src < 0)
        return ROUTER_ON(r, n12, vel, port, a5);
    a->c_tap[src]++;
#if CORE0_SYNTH
    if (Q0->busy)                             /* inside our transaction: no lock, no N (press dropped, off staged) */
        q0_reent_note(a, (uint32_t)src, (int32_t)n12 - 12, vel & 0x7F);
    else
#endif
    if (vel & 0x7F)
        a1_press(a, (uint32_t)src, (int32_t)n12 - 12, vel & 0x7F);
    else
        a1_release(a, (uint32_t)src, (int32_t)n12 - 12);
    a->depth++;
    uint32_t ret = ROUTER_ON(r, n12, vel, port, a5);   /* original: recorder, MIDI out, LEDs, choke trigger */
    a->depth--;
    return ret;
}

uint32_t hook_roff(uint32_t r, uint32_t n12, uint32_t port, uint32_t a4)
{
    struct a1 *a = a1();
    int32_t src = tap_source(a, port, (uint32_t)__builtin_return_address(0));
    if (src < 0)
        return ROUTER_OFF(r, n12, port, a4);
    a->c_tap[src]++;
#if CORE0_SYNTH
    if (Q0->busy)
        q0_reent_note(a, (uint32_t)src, (int32_t)n12 - 12, 0);
    else
#endif
    a1_release(a, (uint32_t)src, (int32_t)n12 - 12);
    a->depth++;
    uint32_t ret = ROUTER_OFF(r, n12, port, a4);
    a->depth--;
    return ret;
}

/* Slice bypass: group 0x17 goes from the note-mode stage straight to its next stage, exactly as the non-slice path
 * does; the note is not replaced, no parameter is written. */
uint32_t hook_nm_on(uint32_t nm, uint32_t g, uint32_t note, uint32_t vel)
{
    struct a1 *a = a1();
    if (!a || g != A1_G)
        return NM_ON(nm, g, note, vel);
    a->c_nm_bypass++;
    uint32_t next = *(volatile uint32_t *)(nm + 0xC);
    return ((fn4_t)(*(volatile uint32_t *)*(volatile uint32_t *)next))(next, g, note, vel);
}

uint32_t hook_nm_off(uint32_t nm, uint32_t g, uint32_t note, uint32_t d)
{
    struct a1 *a = a1();
    if (!a || g != A1_G)
        return NM_OFF(nm, g, note, d);
    uint32_t next = *(volatile uint32_t *)(nm + 0xC);
    return ((fn4_t)(*(volatile uint32_t *)(*(volatile uint32_t *)next + 4)))(next, g, note, d);
}

/* Track 8's sample voice is never triggered: the tap owns its notes. */
uint32_t hook_eng_on(uint32_t e, uint32_t g, uint32_t note, uint32_t vel)
{
    struct a1 *a = a1();
    if (!a || g != A1_G)
        return ENG_ON(e, g, note, vel);
    a->c_eng_cut++;
    return 0;
}

uint32_t hook_eng_off(uint32_t e, uint32_t g, uint32_t note, uint32_t d)
{
    struct a1 *a = a1();
    if (!a || g != A1_G)
        return ENG_OFF(e, g, note, d);
    a->c_eng_cut++;
    return 0;
}

#if AD_LIVE
/* Stop / mute and MIDI CC 123 (AD build): sounding synth notes play out their attack and
 * decay, as the sample tracks do. Every word the DSP has (class-A ALL OFF, KILL, the stock channel-7 forced off)
 * damps, so nothing is published: onsets of ours still queued in N (issued before this point) are purged, every
 * held voice is released and every instance record ends (later offs are stale). Voices with a published onset keep
 * their state, generation mirror and deadline: they are never FREE while the DSP may still run them, so their
 * reuse is still an ordered KILL -> ONSET. A voice whose onset was purged never reached the DSP (a steal's KILL
 * stays pending in P): FREE. An onset already in the ring cannot be withdrawn and starts, then decays. Closed:
 * as all_off (table reset, nothing published). */
#if CORE0_SYNTH
static void release_all(struct a1 *a, uint32_t why)
{
    REJ_CLEAR(a->phase != PH_OPEN);
    if (a->phase != PH_OPEN) {
        all_off(a, why, 0);
        return;
    }
                                              /* Stop / mute / CC 123: the 0.1.1 cancellation of
                                                 unpublished onsets (and their orphan groups), then a generation-matched
                                                 RELEASE for every held voice whose ONSET is PROD-published */
    uint32_t sem = eng_lock();
    uint16_t b = (uint16_t)(a->seq + 1);
    NQ_PURGE(a, 15, 0, 0, b);
    a->seq = b;
    uint32_t held = 0;
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if (x->state == V_FREE)
            continue;
        if (!x->pub_at)
            x->state = V_FREE, x->cut = 0, x->inst = 0, a->dl[v] = 0;
        else if (x->state == V_HELD)
            held |= 1u << v;
    }
    q0_release_voices(a, held, 0);
    for (uint32_t i = 0; i < NINST; i++)
        a->in[i].ser = 0;
    ad_drain(a);
    eng_unlock(sem);
    a->c_why[why]++;
}
#else
static void release_all(struct a1 *a, uint32_t why)
{
    if (a->phase != PH_OPEN) {
        all_off(a, why, 0);
        return;
    }
    uint32_t sem = eng_lock();
    uint16_t b = (uint16_t)(a->seq + 1);
    nq_purge(a, 15, 0, b);
    a->seq = b;
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if (x->state == V_FREE)
            continue;
        if (!x->pub_at) {
            x->state = V_FREE, x->cut = 0, x->inst = 0, a->dl[v] = 0;
        } else if (x->state == V_HELD) {
            x->state = V_REL;
            x->rel_ser = ++a->serial;
        }
    }
    for (uint32_t i = 0; i < NINST; i++)
        a->in[i].ser = 0;
    eng_unlock(sem);
    a->c_why[why]++;
}
#endif
#define SOFT_OFF(a, why) release_all(a, why)
#else
#define SOFT_OFF(a, why) all_off(a, why, 0)
#endif

/* Forced off of group 0x17: Stop / mute = synth release (AD build) or ALL OFF; choke, sample edit = ALL OFF; other
 * groups unchanged. */
static uint32_t force(uint32_t g, uint32_t why)
{
    struct a1 *a = a1();
    if (!a || (g & 0xFF) != A1_G)
        return FORCE_OFF(g);
#if CORE0_SYNTH
    if (Q0->busy) {                           /* deferred to the next Main pass, never locked from here */
        Q0->defer |= (uint8_t)(why == WHY_STOP ? DEF_STOP : why == WHY_CHOKE ? DEF_CHOKE : DEF_EDIT);
        Q0->c_reent++;
        return 0;
    }
    Q0_FLUSH(a);
#endif
    a1_sync(a);
#if AD_LIVE
    if (why == WHY_STOP) {
        release_all(a, why);
        return 0;
    }
#endif
    all_off(a, why, 0);
    return 0;
}

uint32_t hook_fo_eng(uint32_t g) { return force(g, WHY_STOP); }      /* Stop / mute */
uint32_t hook_fo_choke(uint32_t g) { return force(g, WHY_CHOKE); }   /* choke */
uint32_t hook_fo_edit(uint32_t g) { return force(g, WHY_EDIT); }     /* sample edit */

/* MIDI CC 123 on track 8's channel: the original sends one off per held-pitch bit, which cannot release duplicates. */
uint32_t hook_midi_alloff(uint32_t midi_in, uint32_t g, uint32_t c, uint32_t d)
{
    struct a1 *a = a1();
#if CORE0_SYNTH
    if (a && (g & 0xFF) == A1_G && Q0->busy) {
        Q0->defer |= DEF_CC123;
        Q0->c_reent++;
    } else
#endif
    if (a && (g & 0xFF) == A1_G) {
        Q0_FLUSH(a);
        a1_sync(a);
        SOFT_OFF(a, WHY_CC123);
    }
    return MIDI_ALLOFF(midi_in, g, c, d);
}

/* ---- activation handshake ---------------------------------------------------------------------------------------
 * Load entry (DSP load, mode 0 or 1): close admission, discard our queue, reconcile the mirror (the stream replays
 * zeros). Mode-0 ready tail: the
 * boot ALL OFF as the FIRST ordered synth ring entry, then the ENABLE write (a render gate, not a command gate), then
 * the Main loop opens admission on its next pass. Mode 1 never enables. */

static void a1_close(struct a1 *a)
{
#if CORE0_SYNTH
    REJ_CLEAR(1);                             /* CORE0_RECOVER: CLOSED drops survive the close (late-off gate) */
#endif
    uint32_t sem = eng_lock();
    for (uint32_t i = 0; i < a->nq_n;) {      /* nothing of ours crosses a DSP load; other entries stay */
        uint32_t k = nq_at(a, i)->kind;
        if (K_OURS(k))
            nq_del(a, i);
        else
            i++;
    }
    a->pq_alloff = a->pq_kill = 0;
    for (uint32_t v = 0; v < 4; v++)
        a->gen[v] = 0;
    a->cons_ok = 0;
    a->phase = PH_CLOSED;
#if AD_LIVE
    a->ad_pub = a->ad_iss = a->ad_want = AD_DEFAULT;  /* a mode-0 load restores the DSP's defaults */
    a->ad_ok = 0;
#endif
#if CORE0_SYNTH                               /* and so does an epoch reset (S 64, R 0) */
    Q0->sr_pub = Q0->sr_iss = Q0->sr_want = SR_DEFAULT;
#if CORE0_TONE
    Q0->tone_pub = Q0->tone_iss = TONE_NONE;  /* the DSP's tone caches do not survive a load or an epoch */
    Q0->tone_ok = 0;
#endif
#endif
    eng_unlock(sem);
    table_reset(a);
}

#if !CORE0_ONLY
/* X:2E70 <- 1 on core 1 through the DSP word writer, under the bus semaphore, with the global (not per-core) address
 * cache invalidated before and after. Never the locking writer from here (deadlock), never SuspendAll around the
 * blocking transport, never a DSP read. */
static void a1_enable_write(void)
{
    SEM_TAKE(BUS_SEM);
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    XY_WRITE_BODY(SYNTH_CORE, 1u << 8, X_ENABLE, 0);
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    uint32_t cell = BUS_SEM;
    SEM_GIVE(&cell);
}

/* ARMED -> ENABLED once the boot ALL OFF is in the ring (immediately in the ready hook unless the ring was full of
 * other entries; then the Main loop finishes it, counted as late). */
static void a1_activate(struct a1 *a)
{
    uint32_t sem = eng_lock();
    ad_drain(a);
    uint32_t published = a->phase == PH_ARMED && !a->pq_alloff;
    if (published)
        a->phase = PH_ENABLED;
    eng_unlock(sem);
    if (published) {
        a1_enable_write();
        a->c_enable++;
    }
}
#endif

static uint32_t dsp_load(uint32_t mode, uint32_t r1, uint32_t r2, uint32_t r3)
{
    struct a1 *a = a1();
#if CORE0_SYNTH
    if (a) {
        q0_close();                           /* no queue access from here until the next ready header and ACK */
#if CORE0_BULK_EXCLUDE
        Q0->excl = 0;                         /* a DSP load re-copies the instrument's tables: the path may reopen */
#if CORE0_RECOVER
        Q0->rf &= (uint8_t)~(RF_RINIT | RF_SEEN | RF_TX | RF_LATE | RF_POST);   /* no reinit attempt (nor its
                                                                         post-init check) survives a DSP load; the
                                                                         late-acceptance warning ends with it */
#if CORE0_RECOVER_TIMING
        Q0->c_init_late = 0;
#endif
#endif
#if CORE0_BENCH
        Q0->bst = 0;                          /* not yet OPEN since this load: the startup rule applies again */
#endif
#endif
        if (Q0->busy)
            Q0->defer |= DEF_CLOSE;           /* inside our transaction: the purge waits for the next Main pass */
        else
            a1_close(a);
        a->c_load++;
    }
#else
    if (a) {
        a1_close(a);
        a->c_load++;
    }
#endif
    return ORIG_DSPLOAD(mode, r1, r2, r3);
}

uint32_t hook_dsp_load0(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return dsp_load(r0, r1, r2, r3); }
uint32_t hook_dsp_load1(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return dsp_load(r0, r1, r2, r3); }

uint32_t hook_dsp_ready(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    uint32_t ret = ORIG_TEMPO(r0, r1, r2, r3);          /* the original call completes first */
    struct a1 *a = a1();
#if CORE0_SYNTH                               /* ENABLE1 to core 1 as before; no class-A word on the old ring and no boot
                                                 ALL OFF; admission waits for the ready header and the epoch ACK */
    if (!a || Q0->busy)
        return ret;
    q0_close();
    a1_close(a);
    ad_boot_default();
#if !CORE0_ONLY                               /* CORE0_ONLY: no ENABLE, no core-1 frame; c_enable stays 0 */
    a1_enable_write();
    a->c_enable++;
#endif
    Q0->ph = Q_HEADER;
    Q0->t_ph = Q0->t_poll = TICK();
    a->c_why[WHY_BOOT]++;
    return ret;
#endif
#if !CORE0_ONLY                               /* the 0.1.x core-1 activation (unreachable with CORE0_SYNTH; absent with
                                                 CORE0_ONLY, which retires a1_activate) */
    if (!a)
        return ret;
    a1_close(a);                              /* also covers a boot whose load entry was not seen */
#if AD_LIVE
    ad_boot_default();                        /* Main task, no lock held: the boot project's Length before its barrier */
#endif
    uint32_t sem = eng_lock();
    a->phase = PH_ARMED;                      /* before issuing: the CLOSED gate in ad_drain must not eat it */
    issue_alloff(a);                          /* the first ordered synth entry of this DSP load */
    voice_emit_patch(a);                      /* A1_PATCH=1 variant only: the fixed patch behind the ALL OFF */
    AD_BARRIER(a);                            /* AD_LIVE: the project's attack/decay behind the boot ALL OFF */
    a->tm_gen = tm() ? tm()->gen : 0;         /* the project in force is the one this ALL OFF covers */
    eng_unlock(sem);
    a->c_why[WHY_BOOT]++;
    a1_activate(a);
    if (a->phase != PH_ENABLED)
        a->c_late++;
    return ret;
#endif
}

/* ---- Main loop and boot ---------------------------------------------------------------------------------------- */

#if CORE0_SYNTH
uint32_t hook_a1_tick(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    struct a1 *a = a1();
#if FM_PATCH
    pg_fm_tick();                             /* the FM view's coalesced macro write (synthpage.c) */
#endif
    if (a && !Q0->busy) {
        ad_boot_default();
#if FM_BANKVIEW
        sv_entry();                           /* track 8's Sample view opens on the selected base's page (no lock) */
#endif
#if CORE0_BENCH
        Q0->held = 0;                         /* a new Main pass: one queue write hold allowed (bench pacing) */
#endif
        q0_service(a);                        /* deferred work, header / ACK polls, recovery, bulk reconcile, refresh */
        a1_sync(a);
#if CORE0_TONE
        if (a->phase == PH_OPEN) {            /* M6..M8 knob, CC, p-lock, host_param: picked up on every Main pass */
            uint32_t want = q0_tone_read();
            if (want != Q0->tone_iss) {
                uint32_t sem = eng_lock();
                q0_tone(a, want, TONE_KNOB);
                eng_unlock(sem);
            }
        }
#endif
        if (a->pq_alloff || a->pq_kill || a->nq_n) {
            uint32_t sem = eng_lock();
            a->cons_ok = 0;
            ad_drain(a);
            eng_unlock(sem);
        }
    }
    return ORIG_TX(r0, r1, r2, r3);
}
#else
uint32_t hook_a1_tick(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    struct a1 *a = a1();
    if (a) {
        if (a->phase == PH_ARMED)             /* the ready hook found the ring full: finish ALL OFF -> ENABLE here */
            a1_activate(a);
        if (a->phase == PH_ENABLED) {         /* ENABLE written, bookkeeping done: admission opens */
            a->phase = PH_OPEN;
            a->booted = 1;
        }
#if AD_LIVE
        ad_boot_default();                    /* before a1_sync / ad_read: this pass already publishes it */
#endif
        a1_sync(a);
#if AD_LIVE
        if (a->phase == PH_OPEN) {            /* knob, CC, p-lock, host_param: picked up on every Main pass */
            uint32_t want = ad_read(a);
            if (want != a->ad_iss || want != a->ad_want) {
                uint32_t sem = eng_lock();
                ad_service(a, want, AD_KNOB);
                eng_unlock(sem);
            }
        }
#endif
        if (a->pq_alloff || a->pq_kill || a->nq_n) {     /* unlocked peek; re-checked inside drain */
            uint32_t sem = eng_lock();
            a->cons_ok = 0;                   /* time has passed: read the consumer again if needed */
            ad_drain(a);
            eng_unlock(sem);
        }
    }
    return ORIG_TX(r0, r1, r2, r3);
}
#endif

uint32_t hook_a1_boot(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
#ifdef A1_AT
    /* fixed block, zeroed by hook_boot: activated here (the Main task's first call), so every hook passes through
     * until then */
    SUSPEND_ALL();
    if (region())
        ((volatile struct a1 *)A1_AT)->magic = A1_MAGIC;
    RESUME_ALL();
#else
    volatile uint32_t *ctl = (volatile uint32_t *)HEAP_CTL;
    SUSPEND_ALL();
    uint32_t p = ctl[1];
    if (region() && !(p & 3) && p + A1_SIZE <= ctl[0]) {     /* heap bump allocation */
        ctl[1] = p + A1_SIZE;
        for (uint32_t i = 0; i < A1_SIZE; i += 4)
            *(volatile uint32_t *)(p + i) = 0;
        ((struct a1 *)p)->magic = A1_MAGIC;
        *(volatile uint32_t *)A1_PTR = p;
        *(volatile uint32_t *)A1_PTR_CHK = ~p;
    }
    RESUME_ALL();
#endif
    return ORIG_MAIN0(r0, r1, r2, r3);
}

#else /* SYNTH == 0: the profile without the synth keeps the hook symbols (if a build still lists the sites) as plain
         tail calls; the proper profile omits the tap sites and hooks altogether */

void hook_ring_slot(uint32_t *pair) { ORIG_RING(pair); }
void hook_ring_retrig(uint32_t *pair) { ORIG_RING(pair); }
void hook_ring_on(uint32_t *pair) { ORIG_RING(pair); }
void hook_ring_force(uint32_t *pair) { ORIG_RING(pair); }
void hook_ring_rel(uint32_t *pair) { ORIG_RING(pair); }
uint32_t hook_ron(uint32_t r, uint32_t n12, uint32_t vel, uint32_t port, uint32_t a5)
{
    return ROUTER_ON(r, n12, vel, port, a5);
}
uint32_t hook_roff(uint32_t r, uint32_t n12, uint32_t port, uint32_t a4) { return ROUTER_OFF(r, n12, port, a4); }
uint32_t hook_nm_on(uint32_t nm, uint32_t g, uint32_t note, uint32_t vel) { return NM_ON(nm, g, note, vel); }
uint32_t hook_nm_off(uint32_t nm, uint32_t g, uint32_t note, uint32_t d) { return NM_OFF(nm, g, note, d); }
uint32_t hook_eng_on(uint32_t e, uint32_t g, uint32_t note, uint32_t vel) { return ENG_ON(e, g, note, vel); }
uint32_t hook_eng_off(uint32_t e, uint32_t g, uint32_t note, uint32_t d) { return ENG_OFF(e, g, note, d); }
uint32_t hook_fo_eng(uint32_t g) { return FORCE_OFF(g); }
uint32_t hook_fo_choke(uint32_t g) { return FORCE_OFF(g); }
uint32_t hook_fo_edit(uint32_t g) { return FORCE_OFF(g); }
uint32_t hook_midi_alloff(uint32_t midi_in, uint32_t g, uint32_t c, uint32_t d) { return MIDI_ALLOFF(midi_in, g, c, d); }
uint32_t hook_a1_tick(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return ORIG_TX(r0, r1, r2, r3); }
uint32_t hook_dsp_ready(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return ORIG_TEMPO(r0, r1, r2, r3); }
uint32_t hook_dsp_load0(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return ORIG_DSPLOAD(r0, r1, r2, r3); }
uint32_t hook_dsp_load1(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return ORIG_DSPLOAD(r0, r1, r2, r3); }
uint32_t hook_a1_boot(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return ORIG_MAIN0(r0, r1, r2, r3); }

#endif /* SYNTH */
