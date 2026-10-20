/* Track-8 note source tap, chord expansion, ARM voice table, ring admission for every ARM writer. Included after
 * devkit_abi3.c, trackmodes.c and plockfix.c; uses their region(), tm(), SUSPEND_ALL/RESUME_ALL, SEM_TAKE/SEM_GIVE,
 * DELAY and the clock block. Wire format only in a1_cmd.c (voice_emit_* below are the one interface to it).
 *
 * Hooks (sites and original targets asserted by tools/build_lightshow.py):
 *   hook_ron / hook_roff       note on / off: the tap
 *   hook_nm_on / hook_nm_off   slice bypass for track 8
 *   hook_eng_on / hook_eng_off track 8's sample voice is not triggered
 *   hook_fo_eng / hook_fo_choke / hook_fo_edit  track 8 forced off = synth ALL OFF (Stop/mute, choke, sample edit)
 *   hook_midi_alloff           MIDI CC 123
 *   hook_ring_slot/_retrig/_on/_force/_rel  the five writes to the DSP note ring: admission
 *   hook_a1_tick               Main loop, after the timer service: defer drain, project/boot publication
 *   hook_a1_boot               first call of the Main task: activates the fixed block
 * With no block every hook passes through: track 8 stays a sample track.
 */
#include "synth_settings.h"
#include "a1_cmd.c"

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
#define PATCH_FIELDS    (A1_PATCH ? 5u : 0u)
/* Non-droppable entries N can hold once every sample note-on has been evicted, every queued onset purged and the
 * sample offs collapsed: one off per channel (8) and one patch transaction (PATCH_FIELDS + 1). */
/* ... plus at most one queued ONSET entry per voice (4): a steal's KILL boundary purges the stolen voice's earlier
 * entry. KILL entries (up to 4, one per voice) and the ALL OFF live in P, not in N. */
_Static_assert(8 + (PATCH_FIELDS ? PATCH_FIELDS + 1 : 0) + 4 < NQ,
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
#define SYNTH_CORE      1u                      /* core 1 runs the voice decoder */
#define X_ENABLE        0x2E70u                 /* synth ENABLE word */

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
    uint8_t phase, pad2[3];                   /* PH_*: admission state across DSP loads (activation) */
    /* counters (tests, diagnostics) */
    uint16_t c_tap[NSRC], c_drop_on;
    uint16_t c_why[NWHY];
    uint16_t c_deferred, c_recover, c_overflow, c_ch7_slot, c_unexpected, c_cons_read, c_cons_bad, c_stale_off;
    uint16_t c_lost_inst, c_eng_cut, c_nm_bypass, c_onset, c_kill, c_alloff, c_patch, c_direct;
    uint16_t c_evict_on, c_collapse, c_stale_epoch, c_purged, c_enable, c_pre_enable, c_load, c_late, c_gated;
};
_Static_assert(sizeof(struct a1) <= A1_SIZE, "A1 block");

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
    return sem;
}

static void eng_unlock(uint32_t sem)
{
    uint32_t cell = sem;
    SEM_GIVE(&cell);
}

/* ---- ring admission (engine lock held) ------------------------------------------------------------------------
 * Invariant: an entry is written only into a free slot (free = (cons - prod - 1) & 15 > 0). There is no other
 * write path. P (pq_alloff, pq_kill) is published before N; its boundary is its issue sequence: issuing it purges
 * from N exactly the onsets issued before it (all of N at that moment); onsets issued after stay behind it. */

/* The ring write exactly as the original (same frames, same order), plus the generation tags of what was published. */
static void ring_put(struct a1 *a, uint32_t w0, uint32_t w1)
{
    volatile uint32_t *pc = (volatile uint32_t *)PROD_CELL;
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
static void nq_purge(struct a1 *a, uint32_t voices, uint32_t patch, uint16_t before)
{
    for (uint32_t i = 0; i < a->nq_n;) {
        struct a1q *e = nq_at(a, i);
        if ((int16_t)(e->seq - before) < 0 &&
            ((e->kind == VK_ONSET && !vc_onset_strip(voices, &e->w0, &e->w1)) || (patch && e->kind == VK_PATCH))) {
            a->c_purged++;
            nq_del(a, i);
        } else {
            i++;
        }
    }
}

static void ad_drain(struct a1 *a)
{
    if (a->phase == PH_CLOSED) {              /* activation gate at the one publication point: while CLOSED no class-A
                                                 entry leaves the ARM (P flags dropped, our N entries removed) */
        if (a->pq_alloff || a->pq_kill)
            a->c_gated++;
        a->pq_alloff = a->pq_kill = 0;
        for (uint32_t i = 0; i < a->nq_n;) {
            uint32_t k = nq_at(a, i)->kind;
            if (k == VK_ONSET || k == VK_PATCH) {
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
            uint32_t ours = e->kind == VK_ONSET || e->kind == VK_PATCH, stale = ours && e->epoch != a->pepoch;
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

/* Class-A ALL OFF at this point of the issue order (Stop, choke, mute, edit, CC 123, project, recovery, an
 * unexpected forced off of channel 7): purges every queued onset issued before it, cancels a pending KILL
 * (the ALL OFF covers it) and asks the Main task to reset its table. */
static void issue_alloff(struct a1 *a)
{
    uint16_t b = (uint16_t)(a->seq + 1);
    nq_purge(a, 15, 0, b);
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
    if (!a->pq_alloff && !a->pq_kill && !a->nq_n && ring_free(a)) {
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
    if (!a->pq_alloff && !a->pq_kill && !a->nq_n && ring_free(a) >= k)
        return 1;
    return a->nq_n + k <= NQ - NQ_ONSET_RESERVE;
}

/* Publishes or queues every entry of the onset in order; a chord may straddle blocks (entries are consumed one per
 * block), but none of its entries is ever dropped alone: only an ALL OFF (which also damps the published ones) or
 * a KILL of its voices removes queued entries. */
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
    nq_purge(a, mask, 0, b);
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
    nq_purge(a, 0, 1, (uint16_t)(a->seq + 1));
    a->epoch = (uint8_t)(a->epoch % 63u + 1u);
    uint32_t w0, w1;
    for (uint32_t i = 0; i < PATCH_FIELDS; i++) {
        vc_pdata(i, a->epoch, patch_value(i), &w0, &w1);
        put_n(a, w0, w1, a->pepoch);
    }
    vc_pcommit(PATCH_FIELDS, a->epoch, &w0, &w1);
    put_n(a, w0, w1, a->pepoch);
}

/* ---- the five note ring write sites ----------------------------------------------------------------------------- */

static void a1_ring(uint32_t *pair, uint32_t origin)
{
    struct a1 *a = a1();
    if (!a) {
        ORIG_RING(pair);
        return;
    }
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
    for (uint32_t i = 0; i < NINST; i++)
        a->in[i].ser = 0;
    a->lane = 0;
}

/* Every forced-off path: class-A ALL OFF (purges queued onsets issued before it; the DSP damps the voices and
 * cancels pending onsets). A project change also starts a new project epoch (queued onsets and patch entries of
 * the old one are purged here and filtered at publication) and publishes the patch after the ALL OFF. */
static void all_off(struct a1 *a, uint32_t why, uint32_t project)
{
    if (a->phase != PH_OPEN) {                /* closed / not yet open: the table is reset, nothing is published */
        a->c_gated++;
        a->c_why[why]++;
        table_reset(a);
        return;
    }
    uint32_t sem = eng_lock();
    if (project) {
        a->pepoch++;
        nq_purge(a, 15, 1, (uint16_t)(a->seq + 1));
    }
    voice_emit_alloff(a);
    if (project)
        voice_emit_patch(a);
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
static uint32_t chord(int32_t note, uint8_t *out)
{
    uint32_t n = (uint32_t)setting(SR_NPP), minor = CHORD_SCALE();
    int32_t oct = 12 * setting(SR_OCTAVE);
    int32_t t[4] = {0, 0, 0, 0};
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

static uint32_t is_aged(const struct a1voice *x, uint32_t now)
{
    return x->state != V_FREE && x->pub_at && (int32_t)(now - x->pub_at) >= (int32_t)AGE_TICKS;
}

/* free (lowest index) -> oldest released (cut first, then release serial) -> oldest held (onset serial). */
static uint32_t pick(struct a1 *a, uint32_t excl, uint32_t now)
{
    int32_t best = -1;
    for (uint32_t v = 0; v < 4; v++)
        if (!(excl >> v & 1) && is_free(&a->v[v]))
            return v;
    for (uint32_t v = 0; v < 4; v++)          /* aged candidates: the oldest onset first */
        if (!(excl >> v & 1) && is_aged(&a->v[v], now) &&
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
    a1_sync(a);
    if (a->phase != PH_OPEN) {                /* activation: admission closed (dropped, counted) */
        a->c_pre_enable++;
        return;
    }
    uint8_t out[4], notes[4] = {0, 0, 0, 0};
    uint32_t n = chord(note, out), now = TICK();
    uint32_t poly = src == SRC_MIDI && n == 1 && setting(SR_POLY);
    uint32_t sem = eng_lock();
    if (!voice_onset_room(a, n)) {               /* dropped before allocation: the table is untouched */
        a->c_drop_on++;
        eng_unlock(sem);
        return;
    }
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
    voice_emit_onset(a, mask, notes, vel);
    eng_unlock(sem);
}

/* The newest unreleased instance with this source and root releases (no command under the AD). */
static void a1_release(struct a1 *a, uint32_t src, int32_t note)
{
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

/* Forced off of group 0x17 = synth ALL OFF; other groups unchanged. */
static uint32_t force(uint32_t g, uint32_t why)
{
    struct a1 *a = a1();
    if (!a || (g & 0xFF) != A1_G)
        return FORCE_OFF(g);
    a1_sync(a);
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
    if (a && (g & 0xFF) == A1_G) {
        a1_sync(a);
        all_off(a, WHY_CC123, 0);
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
    uint32_t sem = eng_lock();
    for (uint32_t i = 0; i < a->nq_n;) {      /* nothing of ours crosses a DSP load; other entries stay */
        uint32_t k = nq_at(a, i)->kind;
        if (k == VK_ONSET || k == VK_PATCH)
            nq_del(a, i);
        else
            i++;
    }
    a->pq_alloff = a->pq_kill = 0;
    for (uint32_t v = 0; v < 4; v++)
        a->gen[v] = 0;
    a->cons_ok = 0;
    a->phase = PH_CLOSED;
    eng_unlock(sem);
    table_reset(a);
}

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

static uint32_t dsp_load(uint32_t mode, uint32_t r1, uint32_t r2, uint32_t r3)
{
    struct a1 *a = a1();
    if (a) {
        a1_close(a);
        a->c_load++;
    }
    return ORIG_DSPLOAD(mode, r1, r2, r3);
}

uint32_t hook_dsp_load0(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return dsp_load(r0, r1, r2, r3); }
uint32_t hook_dsp_load1(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) { return dsp_load(r0, r1, r2, r3); }

uint32_t hook_dsp_ready(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    uint32_t ret = ORIG_TEMPO(r0, r1, r2, r3);          /* the original call completes first */
    struct a1 *a = a1();
    if (!a)
        return ret;
    a1_close(a);                              /* also covers a boot whose load entry was not seen */
    uint32_t sem = eng_lock();
    a->phase = PH_ARMED;                      /* before issuing: the CLOSED gate in ad_drain must not eat it */
    issue_alloff(a);                          /* the first ordered synth entry of this DSP load */
    voice_emit_patch(a);                      /* A1_PATCH=1 variant only: the fixed patch behind the ALL OFF */
    a->tm_gen = tm() ? tm()->gen : 0;         /* the project in force is the one this ALL OFF covers */
    eng_unlock(sem);
    a->c_why[WHY_BOOT]++;
    a1_activate(a);
    if (a->phase != PH_ENABLED)
        a->c_late++;
    return ret;
}

/* ---- Main loop and boot ---------------------------------------------------------------------------------------- */

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
        a1_sync(a);
        if (a->pq_alloff || a->pq_kill || a->nq_n) {     /* unlocked peek; re-checked inside drain */
            uint32_t sem = eng_lock();
            a->cons_ok = 0;                   /* time has passed: read the consumer again if needed */
            ad_drain(a);
            eng_unlock(sem);
        }
    }
    return ORIG_TX(r0, r1, r2, r3);
}

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
