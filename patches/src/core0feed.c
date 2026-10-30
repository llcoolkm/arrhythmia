/* Core-0 synth queue feed (CORE0_SYNTH=1 only): the ARM side of the Arrhythmia 0.2 core-0 synth. Included by
 * notetap.c after nq_purge(); state in core0feed.h (struct q0, its own fixed block below struct a1).
 *
 * Transport (the core-0 queue contract for the DSP side, PROVISIONAL):
 *   writes: the DSP word write XY_WRITE_BODY(0, w << 8, a16) under the DSP bus semaphore 0x20003688, the
 *           address cache 0x20003684 set to FFFFFFFF before and after every hold (a1_enable_write's pattern), so the
 *           first write of every hold sends an explicit address frame; one hold publishes a batch of k entries
 *           (k <= the free count from a validated CONS, k <= Q0_BATCH): per entry epoch, word0, word1, commitseq,
 *           then the final PROD once, last.
 *   reads:  the DSP word read Q0_READ(0, a16) >> 8 under the same semaphore, in its own hold. Policy: invalidate
 *           the ARM address cache after every DSP read and force explicit addressing before the next write; a
 *           read is never inside a write hold.
 * Admission: CLOSED at every DSP load entry; at the mode-0 ready tail ENABLE1 (core 1) as in 0.1.1, then HEADER;
 * each Main pass reads READY (4E92) and, when it matches, MAGIC / VERSION (000202 only); then REQ = ACK + 1 alone,
 * held stable with PROD; each Main pass reads ACK / PROD / CONS / FAULT; on ACK == REQ, FAULT 0, PROD == CONS ==
 * the mirror: the opening envelope group is admitted and published, then note sources see OPEN. 100 ms without a
 * header or an ACK: slow polling (every 100 ms), counted, never a wait loop.
 * Backpressure: free = 16 - (PROD - CONS); CONS is read (with FAULT) only when the cache leaves fewer than 2 free,
 * and refreshed every 50 ms while entries are outstanding. FAULT or a FRESH distance above 16 starts recovery (close, purge, table
 * reset, a new REQ). Entries that do not fit stay in N; nothing waits.
 * Bulk SHI paths (slot upload, SysEx sample transfer, bulk read; o1wait.c wrappers): no queue access at all while
 * one runs; afterwards ACK / FAULT / PROD / CONS and the header are reconciled before the next write. A matching
 * identity resumes (the pause preserves the published voices and generations); anything else is a full epoch
 * recovery (the DSP reset clears its generations as the ARM resets its table).
 * Forced cuts (the core-0 filter is post-sum and shared, a KILL leaves its history
 * ringing): project / clear / pack, choke, sample edit and an unexpected stock channel-7 off are the global ALL OFF;
 * boot, DSP reload and recovery are the epoch reset. KILL is used only for a steal or a lane replacement.
 * Re-entry: struct q0 busy marks our engine-lock transaction (B_ENG) and a bus hold (B_BUS); every entry hook
 * checks it BEFORE the non-recursive engine lock: a press is dropped (counted), key offs are staged, Stop / CC 123 /
 * choke / edit and a DSP load's purge are deferred to the next Main pass. */

#define Q0_READ         ((shr_fn)0x08031269u)   /* DSP word read (core, a16): the word in bits 8..31 */

static void ad_barrier(struct a1 *a);
static void a1_close(struct a1 *a);
static void release_all(struct a1 *a, uint32_t why);
static void all_off(struct a1 *a, uint32_t why, uint32_t project);
static void a1_release(struct a1 *a, uint32_t src, int32_t note);
static void a1_release_at(struct a1 *a, uint32_t src, int32_t note, uint32_t snap);
static uint32_t make_room(struct a1 *a);
static void ad_drain(struct a1 *a);
static uint32_t o1_faulted(void);
static void put_n(struct a1 *a, uint32_t w0, uint32_t w1, uint32_t epoch);

/* ---- bus transactions --------------------------------------------------------------------------------------------- */

#if CORE0_RECOVER
/* Cross-task guard. The queue's only exclusion during a transfer is q->bulk; every bus transaction of ours runs whole
 * under BUS_SEM. A transfer entered from a task other than Main cannot quiesce (the state machine is Main's), so it
 * claims the SHI under BUS_SEM (no transaction of ours is in flight while it holds the semaphore), marks the path
 * excluded and touches nothing else; every later transaction of ours re-checks the claim under the semaphore and makes
 * no frame. bulk changes only by LDREXB/STREXB (two tasks may nest; one core, and a context switch has its own
 * barriers, so relaxed order suffices). The stock transfer itself takes the same semaphore per frame from that task,
 * so this adds no new lock. Before the scheduler starts (flag 0x20006F55, set by the scheduler initializer before
 * vTaskStartScheduler) only one thread runs: it counts as Main. The transfer call sites run in Main; this guard covers the
 * edges that trace leaves n/e. */
#define SCHED_ON        (*(volatile uint8_t *)0x20006F55u)
#define CUR_TCB         (*(volatile uint32_t *)0x20006550u)   /* pxCurrentTCB (PendSV handler literal 0x08025CB8) */
#define TASK_TAB        0x20006C90u     /* task creation's task objects: [0] Main, object +0 = its TCB */
__attribute__((noinline)) static uint32_t q0_foreign(void)
{
    uint32_t t = *(volatile uint32_t *)TASK_TAB;
    return SCHED_ON && t && *(volatile uint32_t *)t != CUR_TCB;
}

#define Q0_NONE         0x01000000u     /* a read that made no frame: no 24-bit value, so every check fails closed */

/* The end of every hold of ours: the address cache invalidated (the next write re-addresses), BUS_SEM given. */
__attribute__((noinline)) static void q0_give(void)
{
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    uint32_t cell = BUS_SEM;
    SEM_GIVE(&cell);
}

/* Take the bus for one transaction of ours: 0 = a transfer holds the SHI (claimed under BUS_SEM), no frame. */
static uint32_t q0_take(struct q0 *q)
{
    q->busy |= B_BUS;
    SEM_TAKE(BUS_SEM);
    if (!q->bulk)
        return 1;
    q0_give();
    q->busy &= (uint8_t)~B_BUS;
    q->excl = 1;
    q->rf |= RF_SKIP;
    return 0;
}
#endif
#if CORE0_RECOVER_TIMING
/* OPTIONAL timing counters: *m = max(*m, t), t saturated at T_SAT. Out of line: four sites (flash budget of the
 * gated timing image, -32 B). */
__attribute__((noinline)) static void q0_tmax(uint16_t *m, uint32_t t)
{
    if (t > T_SAT)
        t = T_SAT;
    if (t > *m)
        *m = (uint16_t)t;
}
#endif

#if CORE0_BENCH
__attribute__((noinline))                     /* bench: flash budget (one copy for every read site) */
#endif
static void q0_rd(uint32_t addr, uint32_t n, uint32_t *out)
{
    struct q0 *q = Q0;
#if CORE0_RECOVER
    if (!q0_take(q)) {
        for (uint32_t i = 0; i < n; i++)
            out[i] = Q0_NONE;
        return;
    }
#else
    q->busy |= B_BUS;
    SEM_TAKE(BUS_SEM);
#endif
    for (uint32_t i = 0; i < n; i++)
        out[i] = (Q0_READ(Q0_CORE, addr + i) >> 8) & M24;
#if CORE0_RECOVER
    q0_give();                                /* the next write re-addresses (shared SHI cursor) */
#else
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;   /* the next write re-addresses (shared SHI cursor) */
    uint32_t cell = BUS_SEM;
    SEM_GIVE(&cell);
#endif
    q->busy &= (uint8_t)~B_BUS;
    q->c_rd++;
}

static void q0_wr1(uint32_t addr, uint32_t v)
{
    struct q0 *q = Q0;
#if CORE0_RECOVER
    if (!q0_take(q))
        return;
#else
    q->busy |= B_BUS;
    SEM_TAKE(BUS_SEM);
#endif
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    XY_WRITE_BODY(Q0_CORE, v << 8, addr, 0);
#if CORE0_RECOVER
    q0_give();
#else
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    uint32_t cell = BUS_SEM;
    SEM_GIVE(&cell);
#endif
    q->busy &= (uint8_t)~B_BUS;
}

/* One hold: k entries in sequence order, then PROD once, the slot wrap 15 -> 0 included. Nothing here can fail or re-enter (no callback; the semaphore take yields to other tasks only). */
static void q0_write(struct q0 *q, uint32_t (*w)[2], uint32_t k)
{
#if CORE0_RECOVER
    if (!q0_take(q))
        return;
#else
    q->busy |= B_BUS;
    SEM_TAKE(BUS_SEM);
#endif
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    uint32_t s = q->prod;
    for (uint32_t i = 0; i < k; i++) {
        uint32_t at = Q0_ENTRIES + 4u * (s & 15u), n = (s + 1u) & M24;
        XY_WRITE_BODY(Q0_CORE, q->epoch << 8, at, 0);
        XY_WRITE_BODY(Q0_CORE, w[i][0] << 8, at + 1u, 0);
        XY_WRITE_BODY(Q0_CORE, w[i][1] << 8, at + 2u, 0);
        XY_WRITE_BODY(Q0_CORE, n << 8, at + 3u, 0);
        s = n;
    }
    XY_WRITE_BODY(Q0_CORE, s << 8, Q0_PROD, 0);
#if CORE0_RECOVER
    q0_give();
#else
    *(volatile uint32_t *)BUS_CACHE = CACHE_NONE;
    uint32_t cell = BUS_SEM;
    SEM_GIVE(&cell);
#endif
    q->busy &= (uint8_t)~B_BUS;
    q->prod = s;
#if CORE0_BENCH
    q->held = 1;                              /* the one queue write hold of this Main pass */
#endif
    q->c_batch++;
    q->c_ent += (uint16_t)k;
}

/* ---- recovery, free count ------------------------------------------------------------------------------------------ */

/* Close for recovery: nothing of ours is published from now on (ad_drain's gate); the next Main pass purges, resets
 * the table and requests a new epoch (q0_service), outside any lock. */
static void q0_fail(struct a1 *a)
{
    Q0->ph = Q_RECOVER;
    a->phase = PH_CLOSED;
    Q0->c_fault++;
}

/* A fresh CONS and FAULT; 0 = a verdict (FAULT, or an unsigned distance above 16): recovery started. */
static uint32_t q0_refresh(struct a1 *a)
{
    struct q0 *q = Q0;
    uint32_t r[2];
    q0_rd(Q0_CONS, 2, r);
    q->t_read = TICK();
    if (r[1] || ((q->prod - r[0]) & M24) > Q0_CAP) {
        q0_fail(a);
        return 0;
    }
    q->cons = r[0];
    return 1;
}

static uint32_t q0_free(struct a1 *a)
{
    struct q0 *q = Q0;
    uint32_t d = (q->prod - q->cons) & M24;
    if (d + Q0_GROUP_MAX <= Q0_CAP && (int32_t)(TICK() - q->t_read) < (int32_t)Q0_REFRESH_MS)
        return Q0_CAP - d;                    /* room for an envelope group by a cached CONS / FAULT read < 50 ms ago */
    if (!q0_refresh(a))                       /* fewer than 2 free by the cache: read once (never a verdict on a
                                                 stale value); health older than 50 ms: read before publishing */
        return 0;
    d = (q->prod - q->cons) & M24;
    return d < Q0_CAP ? Q0_CAP - d : 0;
}

/* ---- N helpers ----------------------------------------------------------------------------------------------------- */

static uint32_t q0_stock_waiting(struct a1 *a)
{
    for (uint32_t i = 0; i < a->nq_n; i++)
        if (!K_OURS(nq_at(a, i)->kind))
            return 1;
    return 0;
}

#if CORE0_RECOVER
__attribute__((noinline))                     /* flash budget (the gated timing image): one copy, -160 B; same result */
#endif
static int32_t q0_next_ours(struct a1 *a, uint32_t from)
{
    for (uint32_t i = from; i < a->nq_n; i++)
        if (K_OURS(nq_at(a, i)->kind))
            return (int32_t)i;
    return -1;
}
#define q0_first_ours(a) q0_next_ours(a, 0)

#if CORE0_TONE
/* A knob-only tone: not a barrier and no ONSET queued behind it (a tone with a note behind it is that note's flush). */
static uint32_t q0_tone_knob_only(struct a1 *a, uint32_t i)
{
    if (nq_at(a, i)->w0 & TONE_BAR_MARK)
        return 0;
    for (uint32_t j = i + 1; j < a->nq_n; j++)
        if (nq_at(a, j)->kind == VK_ONSET)
            return 0;
    return 1;
}
#endif

/* Orphan envelope groups go: a group with no ONSET behind it before the next group or the end
 * of N is deleted (under snapshot it changes nothing a queued note reads; a later press re-issues changed values).
 * The issued values then follow what is left: the last queued group, else the published one. */
static void q0_tidy(struct a1 *a)
{
    int32_t pend = -1;
    for (uint32_t i = 0; i <= a->nq_n; i++) {
        uint32_t k = i < a->nq_n ? nq_at(a, i)->kind : VK_AD;   /* the end acts as a next group */
        if (k == VK_AD) {
            if (pend >= 0) {
#if FM_PATCH                                  /* a deleted barrier's "send all" passes to the next group */
                if (i < a->nq_n)
                    nq_at(a, i)->w1 |= nq_at(a, (uint32_t)pend)->w1 & FMN_BAR;
#endif
                nq_del(a, (uint32_t)pend);
                a->c_ad_purged++;
                i--;
            }
            pend = (int32_t)i;
        } else if (k == VK_ONSET) {
            pend = -1;
        }
    }
    uint32_t env = (uint32_t)a->ad_pub | (uint32_t)Q0->sr_pub << 14;
#if FM_PATCH
    uint32_t fm = Q0->fm_pub, md = Q0->md_pub;
#endif
    for (uint32_t i = 0; i < a->nq_n; i++)
        if (nq_at(a, i)->kind == VK_AD) {
            env = nq_at(a, i)->w1 & ENV_MASK;
#if FM_PATCH
            fm = fmn_fm(nq_at(a, i)->w0, nq_at(a, i)->w1);
            md = fmn_md(nq_at(a, i)->w0);
#endif
        }
    ENV_SET_ISS(a, env);
#if FM_PATCH
    Q0->fm_iss = (uint16_t)fm;
    Q0->md_iss = md;
#endif
#if CORE0_TONE
    /* Tone is live, so a trailing tone stays; but two tones with no ONSET between them (left by a purge) are one:
     * the earlier goes, the later already has the newer values. At most one tone per ONSET gap: N holds <= 4 + 1. */
    int32_t t = -1;
    uint32_t tone = Q0->tone_pub;
    for (uint32_t i = 0; i < a->nq_n; i++) {
        uint32_t k = nq_at(a, i)->kind;
        if (k == VK_TONE) {
            if (t >= 0) {
                nq_at(a, i)->w0 |= nq_at(a, (uint32_t)t)->w0 & TONE_BAR_MARK;   /* the later one inherits a barrier */
                nq_del(a, (uint32_t)t);
                Q0->c_tone_purged++;
                i--;
            }
            t = (int32_t)i;
            tone = nq_at(a, i)->w1 & VC_TONE_MASK;
        } else if (k == VK_ONSET) {
            t = -1;
        }
    }
    Q0->tone_iss = tone;
#endif
}

/* ---- publication ----------------------------------------------------------------------------------------------------- */

/* Bookkeeping of one entry at its publication (t = tick | 1). */
static void q0_book(struct a1 *a, uint32_t w0, uint32_t *w1, uint32_t t)
{
    struct q0 *q = Q0;
    uint32_t k = vc_kind(w0);
    if (k == VK_ONSET) {                      /* the DSP expects gen = stored + 1: from the mirror now */
        uint32_t v = (w0 >> 16) & 3u;
        vc_onset_publish(w1, a->gen[v]);
        a->gen[v] = (uint8_t)((a->gen[v] + 1u) & 15u);
        a->v[v].pub_at = t;
        if (q->sr_pub & 127u) {               /* captures a sustain > 0: no deadline while held */
            q->sus |= (uint8_t)(1u << v);
            a->dl[v] = 0;
        } else {                              /* sustain 0: silent after attack + decay */
            q->sus &= (uint8_t)~(1u << v);
            a->dl[v] = t + ad_ticks(a->ad_pub);
        }
        a->c_onset++;
    } else if (k == VK_REL) {
        uint32_t v = vc_rel_voice(w0);
        q->sus &= (uint8_t)~(1u << v);
        a->dl[v] = t + AD_TICKS(Q0_REL_BLOCKS);
        q->c_rel++;
    } else if (k == VK_ALLOFF) {
        for (uint32_t v = 0; v < 4; v++)
            a->gen[v] = 0;
        q->sus = 0;
        a->c_alloff++;
    } else if (k == VK_KILL) {
        a->c_kill++;
#if CORE0_TONE
    } else if (k == VK_TONE) {
        q->tone_pub = *w1 & VC_TONE_MASK;
        q->c_tone++;
#endif
    }
}

/* Per-target P before N: while the queue is full no N entry of ours moves, so an ONSET never overtakes its KILL or
 * ALL OFF. An envelope group needs two free entries and is never split across two PROD publications. */
static void q0_drain(struct a1 *a)
{
    struct q0 *q = Q0;
#if CORE0_RECOVER
    if (q->ph != Q_OPEN || q->bulk || q->reval || q->excl || (q->busy & (B_BUS | B_HOLD)) || o1_faulted())
        return;                               /* excl while OPEN: another task's claim (the next pass recovers) */
#else
    if (q->ph != Q_OPEN || q->bulk || q->reval || (q->busy & (B_BUS | B_HOLD)) || o1_faulted())
        return;
#endif
    if (!a->pq_alloff && !a->pq_kill && q0_first_ours(a) < 0)
        return;
#if CORE0_BENCH
    if (q->held) {                            /* bench pacing: one write hold per Main pass; the rest waits in N / P
                                                 for the next pass (the tick clears held, then drains) */
        q->c_hold_wait++;
        return;
    }
#endif
    uint32_t w[Q0_BATCH][2], k = 0, room = q0_free(a), t = TICK() | 1u, from = 0;
    if (!room) {
        q->c_full++;
        return;
    }
#if CORE0_TONE
    uint32_t free_all = room;                 /* the whole free count, for the knob-only tone reserve */
#endif
    if (room > Q0_BATCH)
        room = Q0_BATCH;
    while (k < room) {
        if (a->pq_alloff) {
            vc_alloff(&w[k][0], &w[k][1]);
            a->pq_alloff = 0;
        } else if (a->pq_kill) {
            uint32_t g = 0;
            for (uint32_t v = 0; v < 4; v++)
                if (a->pq_kill >> v & 1)
                    g |= (uint32_t)a->gen[v] << (4 * v);
            a->pq_kill &= (uint8_t)~vc_kill_entry(a->pq_kill, g, &w[k][0], &w[k][1]);
        } else {
            int32_t i = q0_next_ours(a, from);
            if (i < 0)
                break;
            struct a1q *e = nq_at(a, (uint32_t)i);
#if CORE0_TONE
            if (e->kind == VK_TONE && e->epoch == a->pepoch) {
                /* Reserve (feed contract): a knob-only tone is published only if at least
                 * TONE_RESERVE free entries remain after it; otherwise it waits coalesced in N while later entries
                 * (RELEASE, stock) go past it: a live tone edit carries no note ordering. Barrier tones and note
                 * flushes (an ONSET behind it) are exempt: they must precede their note or follow their epoch. */
                if (q0_tone_knob_only(a, (uint32_t)i) && free_all < k + 1u + TONE_RESERVE && !k
                        && (int32_t)(TICK() - q->t_read) >= (int32_t)TONE_PACE_MS) {   /* the real tick: t is
                                                 stamped (| 1) for voice bookkeeping only */
                    if (!q0_refresh(a))       /* the cached CONS is pessimistic: read it once (at most per 5 ms) */
                        return;
                    uint32_t d = (q->prod - q->cons) & M24;
                    free_all = d < Q0_CAP ? Q0_CAP - d : 0;
                    if (room > free_all)
                        room = free_all;
                }
                if (q0_tone_knob_only(a, (uint32_t)i) && free_all < k + 1u + TONE_RESERVE) {
                    q->c_tone_wait++;
                    from = (uint32_t)i + 1u;
                    continue;
                }
                e->w0 &= ~TONE_BAR_MARK;
            }
#endif
            if (e->epoch != a->pepoch) {      /* an older project's entry: never published */
                a->c_stale_epoch++;
                nq_del(a, (uint32_t)i);
                continue;
            }
            if (e->kind == VK_AD) {
#if FM_PATCH                                  /* diff-only expansion against the published values; a barrier sends
                                                 all; the group is never split (FM_GROUP_MAX <= Q0_BATCH) */
                uint32_t env = e->w1 & ENV_MASK, fm = fmn_fm(e->w0, e->w1), md = fmn_md(e->w0);
                uint32_t bar = e->w1 & FMN_BAR;
                uint32_t de = bar || env != ((uint32_t)a->ad_pub | (uint32_t)q->sr_pub << 14);
                uint32_t df = bar || fm != q->fm_pub, dm = bar || md != q->md_pub;
                if (k + 2u * de + df + dm > room)
                    break;
                nq_del(a, (uint32_t)i);
                if (de) {
                    vc_env_pair(env, w + k);
                    a->ad_pub = (uint16_t)(env & 0x3FFFu);
                    q->sr_pub = (uint16_t)(env >> 14);
                    a->c_ad++;
                    k += 2;
                }
                if (df) {
                    w[k][0] = FM_PATCH_W0;
                    w[k++][1] = fm;
                    q->fm_pub = (uint16_t)fm;
                    q->c_fm++;
                }
                if (dm) {
                    w[k][0] = FM_MOD_W0;
                    w[k++][1] = md;
                    q->md_pub = md;
                    q->c_md++;
                }
                if (!(de | df | dm))
                    q->c_grp_eq++;
#else
                if (k + 2u > room)
                    break;
                uint32_t env = e->w1 & ENV_MASK;
                vc_env_pair(env, w + k);
                nq_del(a, (uint32_t)i);
                a->ad_pub = (uint16_t)(env & 0x3FFFu);
                q->sr_pub = (uint16_t)(env >> 14);
                a->c_ad++;
                k += 2;
#endif
                continue;
            }
            w[k][0] = e->w0;
            w[k][1] = e->w1;
            nq_del(a, (uint32_t)i);
        }
        q0_book(a, w[k][0], &w[k][1], t);
        k++;
    }
    if (k)
        q0_write(q, w, k);
#if CORE0_BENCH
    if (k && (a->pq_alloff || a->pq_kill || q0_next_ours(a, from) >= 0))
        q->c_split++;                         /* entries of ours left for a later pass (cap, pair or reserve) */
#endif
}

/* ---- producers ------------------------------------------------------------------------------------------------------- */

#if FM_PATCH
/* The FM settings in force: patch slot 7 codec 2 of the live T1 block (synth_settings.h), else the defaults. Pure
 * reads; into fm_want / md_want. FM_BANKVIEW: the ratio family from track 8's sample slot instead of byte +3. */
static void q0_fm_read(void)
{
    struct q0 *q = Q0;
    struct tm *t = tm();
    const volatile uint8_t *p = t && t->why == TM_OK && t->store ? (const volatile uint8_t *)(t->store + TM_EXT +
                                                                                            SYN_PATCH_AT) : 0;
#if FM_BANKVIEW                               /* family and algorithm are track 8's sample slot (channel parameter 0,
                                                 the track's selected sample): the full field is validated first
                                                 (0..31; never
                                                 masked or narrowed before, so 149 is not 21), a missing channel or any
                                                 other value is slot 0; only then family = slot & 15, algorithm =
                                                 slot >> 4 (page 0 serial4, page 1 dual_stack) */
    uint32_t ch = pl_channel(rw(PL_R_CELL), A1_T), slot = ch ? PL_GET(ch, 0) : 0;
    if (slot >= FM_SLOTS)
        slot = 0;
#if FB_MACRO1                                 /* feedback = main macro 1 (channel parameter 6) at the press */
    (void)p;
    q->fm_want = (uint16_t)fm_patch_w1(slot >> 4, slot & 15u, ch ? syn_fb_of_p6(PL_GET(ch, 6) & 0x7Fu) : FM_DEF_FB);
#else
    q->fm_want = (uint16_t)fm_patch_w1(slot >> 4, slot & 15u, (uint32_t)syn_fm_get(p, SF_FB));
#endif
#else
    q->fm_want = (uint16_t)fm_patch_w1((uint32_t)syn_fm_get(p, SF_ALGO), (uint32_t)syn_fm_get(p, SF_BANK),
                                       (uint32_t)syn_fm_get(p, SF_FB));
#endif
    q->md_want = fm_mod_w1((uint32_t)syn_fm_get(p, SF_MA), (uint32_t)syn_fm_get(p, SF_MD),
                           (uint32_t)syn_fm_get(p, SF_MF));
}
#endif

#if FB_MACRO1
static uint32_t q0_tune(void)                   /* the synth page's macro 1 (patch slot +4), else 64 */
{
    struct tm *t = tm();
    const volatile uint8_t *p = t && t->why == TM_OK && t->store ? (const volatile uint8_t *)(t->store + TM_EXT +
                                                                                            SYN_PATCH_AT) : 0;
    return (uint32_t)syn_fm_get(p, SF_TUNE);
}
#else
static uint32_t q0_tune(void)
{
    uint32_t ch = pl_channel(rw(PL_R_CELL), A1_T);
    return ch ? PL_GET(ch, 6) & 0x7Fu : 64u;
}
#endif

#if CORE0_TONE
/* ---- live tone (the proposed A08000 packet; engine lock held) --------------------------------------------------
 * Source: track 8's params 11 (M6 FM depth), 12 (M7 cutoff), 13 (M8 resonance), coarse 0..127, read on every Main
 * pass and at each press. One coalesced entry: the newest queued tone takes the latest values in place while no ONSET
 * is queued behind it (never across a note); otherwise a new entry follows that ONSET. Knob-driven issues are at least
 * TONE_PACE_MS apart; a press flushes changed values ahead of its ONSETs (pacing exception); a barrier (opening, every
 * epoch, ALL OFF, project) issues the current values even when unchanged, because an epoch drops the DSP's caches. */
enum { TONE_KNOB, TONE_NOTE, TONE_BAR };

static uint32_t q0_tone_read(void)
{
    uint32_t ch = pl_channel(rw(PL_R_CELL), A1_T);
    if (!ch)
        return Q0->tone_iss != TONE_NONE ? Q0->tone_iss : 0u | 127u << 7;
    return (PL_GET(ch, 11) & 0x7Fu) | (PL_GET(ch, 12) & 0x7Fu) << 7 | (PL_GET(ch, 13) & 0x7Fu) << 14;
}

static void q0_tone(struct a1 *a, uint32_t want, uint32_t mode)
{
    struct q0 *q = Q0;
    uint32_t now = TICK();
    int32_t t = -1;
    uint32_t behind = 0;
    for (uint32_t i = 0; i < a->nq_n; i++) {
        uint32_t k = nq_at(a, i)->kind;
        if (k == VK_TONE)
            t = (int32_t)i, behind = 0;
        else if (t >= 0 && k == VK_ONSET)
            behind = 1;
    }
    if (t >= 0 && !behind) {                  /* coalesced in place: no note between it and these values */
        struct a1q *e = nq_at(a, (uint32_t)t);
        if (mode == TONE_BAR)
            e->w0 |= TONE_BAR_MARK;
        if (e->w1 != want) {
            e->w1 = want;
            q->c_tone_coal++;
        }
        q->tone_iss = want;
        return;
    }
    if (mode != TONE_BAR && want == q->tone_iss)
        return;
    if (mode == TONE_KNOB && q->tone_ok && (int32_t)(now - q->t_tone) < (int32_t)TONE_PACE_MS) {
        q->c_tone_paced++;
        return;
    }
    if (mode == TONE_NOTE && q->tone_ok && (int32_t)(now - q->t_tone) < (int32_t)TONE_PACE_MS)
        q->c_tone_note++;
    q->t_tone = now;
    q->tone_ok = 1;
    q->tone_iss = want;
    if (mode == TONE_BAR) {                   /* marked as a barrier (exempt from the reserve), then drained */
        if (!make_room(a)) {
            a->c_overflow++;
            return;
        }
        nq_push(a, VC_TONE_W0 | TONE_BAR_MARK, want & VC_TONE_MASK, VK_TONE, a->pepoch);
        ad_drain(a);
        return;
    }
    put_n(a, VC_TONE_W0, want & VC_TONE_MASK, a->pepoch);
}
#endif

/* Key / step off (inst = the instance) or the Stop fan-out (inst 0): engine lock held. A voice whose ONSET is
 * PROD-published releases with a generation-matched RELEASE (the gen its ONSET carried = the mirror now); a voice whose
 * ONSET is still in N is cancelled (purged, FREE; a pending KILL of its older owner stays in P). */
static void q0_release_voices(struct a1 *a, uint32_t mask, uint32_t inst)
{
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if (!(mask >> v & 1u) || x->state != V_HELD || (inst && x->inst != inst))
            continue;
        if (!x->pub_at) {
            NQ_PURGE(a, 1u << v, 0, 0, (uint16_t)(a->seq + 1));
            x->state = V_FREE, x->cut = 0, x->inst = 0, a->dl[v] = 0;
            Q0->c_cancel++;
            continue;
        }
        x->state = V_REL;
        x->rel_ser = ++a->serial;
        if (a->phase != PH_OPEN)
            continue;
        if (!make_room(a)) {
            a->c_overflow++;
            continue;
        }
        uint32_t w0, w1;
        vc_release(v, a->gen[v], &w0, &w1);
        nq_push(a, w0, w1, VK_REL, a->pepoch);
    }
}

/* ---- press ownership at receipt ------------------------------------------------------------------------
 * Policy: an off belongs to the newest press of its source and pitch that arrived BEFORE it, whether that press was
 * accepted or rejected. The decision is taken when the off arrives, never later:
 * * a rejected press (re-entered while busy, dropped inside a bulk interval, no room, or refused in quarantine)
 *   increments its key's record; an off arriving while the record is nonzero decrements it and is swallowed;
 * * an accepted press clears every record of its key (all older rejected presses are older than it);
 * * any other off is bound at once to the newest accepted instance existing at its arrival: immediately, or, when it
 *   is staged inside our transaction, by the snapshot of a->serial taken at arrival (instances created later have
 *   larger serials and are never chosen). So a staged off can never consume a later rejection, nor a later
 *   rejection steal a staged off, and arrival order is kept.
 * Records: four keys with a 6-bit count each. Overflow (a fifth key, or a count reaching 63) has one safe action:
 * QUARANTINE. Every further press is refused and every further off is swallowed, so no off is ever attributed to an
 * older held instance; held notes keep sounding until the next global boundary (Stop, CC 123, ALL OFF, project,
 * close), which releases or cuts every voice and lifts it. Counted in c_rej_over. */
static void q0_rej_add(uint32_t src, int32_t note)
{
    struct q0 *q = Q0;
    uint16_t key = REJ_KEY(src, note);
    int32_t e = -1;
    if (q->rej_quar) {
        q->c_rej_over++;
        return;
    }
    for (uint32_t i = 0; i < 4; i++) {
        if (q->rej[i] >> 10 && (q->rej[i] & 0x3FFu) == key) {
            if ((q->rej[i] >> 10) + 1u >= REJ_SAT) {
                q->rej_quar = 1;
                q->c_rej_over++;
                return;
            }
            q->rej[i] = (uint16_t)(q->rej[i] + REJ_ONE);
            return;
        }
        if (!(q->rej[i] >> 10) && e < 0)
            e = (int32_t)i;
    }
    if (e < 0) {
        q->rej_quar = 1;
        q->c_rej_over++;
        return;
    }
    q->rej[e] = (uint16_t)(key | REJ_ONE);
#if CORE0_RECOVER
    q->rstk &= (uint8_t)~(1u << e);
#endif
}

#if CORE0_RECOVER
/* Late-off gate: a press dropped while admission is CLOSED. Its record is STICKY: an accepted press of the
 * same key does not supersede it and a close (recovery, reinit, DSP load) does not clear it, so the dropped press's
 * own late off is swallowed instead of releasing a new same-pitch instance accepted after the reopen. Only an off
 * (or a global boundary while OPEN) consumes it. Overflow: quarantine that also survives the close (RF_QCL). */
static void q0_rej_drop(uint32_t src, int32_t note)
{
    struct q0 *q = Q0;
    q0_rej_add(src, note);
    if (q->rej_quar) {
        q->rf |= RF_QCL;
        return;
    }
    uint16_t key = REJ_KEY(src, note);
    for (uint32_t i = 0; i < 4; i++)
        if (q->rej[i] >> 10 && (q->rej[i] & 0x3FFu) == key)
            q->rstk |= (uint8_t)(1u << i);
}
#endif

/* An off at its arrival: 1 = swallowed (it belongs to a rejected press, or quarantine). */
static uint32_t q0_rej_off(uint32_t src, int32_t note)
{
    struct q0 *q = Q0;
    uint16_t key = REJ_KEY(src, note);
    if (q->rej_quar)
        return 1;
    for (uint32_t i = 0; i < 4; i++)
        if (q->rej[i] >> 10 && (q->rej[i] & 0x3FFu) == key) {
#if CORE0_RECOVER
            if ((q->rej[i] >> 10) == 1u) {
                q->rej[i] = 0;
                q->rstk &= (uint8_t)~(1u << i);
            } else {
                q->rej[i] = (uint16_t)(q->rej[i] - REJ_ONE);
            }
#else
            q->rej[i] = (q->rej[i] >> 10) == 1u ? 0 : (uint16_t)(q->rej[i] - REJ_ONE);
#endif
            return 1;
        }
    return 0;
}

static void q0_rej_accept(uint32_t src, int32_t note)
{
    struct q0 *q = Q0;
    uint16_t key = REJ_KEY(src, note);
    for (uint32_t i = 0; i < 4; i++)
#if CORE0_RECOVER
        if ((q->rej[i] & 0x3FFu) == key && !(q->rstk >> i & 1u))   /* a CLOSED drop is not superseded */
#else
        if ((q->rej[i] & 0x3FFu) == key)
#endif
            q->rej[i] = 0;
}

#if CORE0_RECOVER
/* keep: a boundary while admission is not OPEN (close, recovery, a cut while closed) keeps the sticky CLOSED-drop
 * records and a quarantine they caused; a global boundary while OPEN clears everything, as before. */
#define REJ_CLEAR(keep) q0_rej_clear(keep)
static void q0_rej_clear(uint32_t keep)
{
    struct q0 *q = Q0;
    uint32_t k = keep ? q->rstk : 0u;
    for (uint32_t i = 0; i < 4; i++)
        if (!(k >> i & 1u))
            q->rej[i] = 0;
    q->rstk = (uint8_t)k;
    if (keep && (q->rf & RF_QCL)) {
        q->rej_quar = 1;
    } else {
        q->rej_quar = 0;
        q->rf &= (uint8_t)~RF_QCL;
    }
}
#else
#define REJ_CLEAR(keep) q0_rej_clear()        /* without CORE0_RECOVER every boundary clears (keep not evaluated) */
static void q0_rej_clear(void)
{
    struct q0 *q = Q0;
    for (uint32_t i = 0; i < 4; i++)
        q->rej[i] = 0;
    q->rej_quar = 0;
}
#endif

/* A note event reached while our transaction is in flight: never lock, never touch N. Ownership is decided here. */
static void q0_reent_note(struct a1 *a, uint32_t src, int32_t note, uint32_t vel)
{
    struct q0 *q = Q0;
    q->c_reent++;
    if (vel) {                                /* a press: rejected, recorded for its off */
        q0_rej_add(src, note);
        return;
    }
    if (q0_rej_off(src, note)) {              /* the off of a rejected press: swallowed now */
        a->c_stale_off++;
        return;
    }
    if (q->noff < 4u) {
        q->offsnap[q->noff] = a->serial;      /* bound to the newest instance existing now */
        q->off[q->noff++] = (uint16_t)(src << 8 | ((uint32_t)note & 0x7Fu));
    } else {
        q->defer |= DEF_STOP;                 /* no room to stage it: release everything rather than hang a note */
    }
    q->defer |= DEF_OFFS;
}

static void q0_close(void)
{
    struct q0 *q = Q0;
    q->ph = Q_CLOSED;
    q->reval = 0;
    q->slow = 0;
}

/* ---- Main pass ------------------------------------------------------------------------------------------------------- */

static uint32_t q0_header_ok(struct q0 *q)
{
    uint32_t h[2];
    q->c_hdr++;
    q0_rd(Q0_READY_AT, 1, h);                 /* READY first: it is written last */
    if (h[0] != Q0_READY)
        return 0;
    q0_rd(Q0_MAGIC_AT, 2, h);
    if (h[0] != Q0_MAGIC)
        return 0;
    if (h[1] != Q0_VERSION) {
        q->c_badver++;
        return 0;
    }
    return 1;
}

static void q0_request(struct q0 *q, uint32_t now)
{
    uint32_t r[4];
    q0_rd(Q0_ACK, 4, r);                      /* ACK, PROD, CONS, FAULT */
    q->prod = r[1];                           /* adopted; the ARM does not write PROD until the ACK */
    q->cons = r[2];
    q->req = (r[0] + 1u) & M24;               /* every 24-bit value is valid, 0 included */
#if CORE0_RECOVER_TIMING
    q->t_ack0 = TICK();                       /* immediately before the REQ frame */
#endif
    q0_wr1(Q0_REQ, q->req);
    q->ph = Q_REQ_PH;
    q->t_ph = q->t_poll = now;
    q->slow = 0;
    q->c_req++;
}

/* ACK seen: the opening barrier (the current envelope group; the reset restored the DSP's defaults) is admitted and
 * published before note sources see OPEN. No ALL OFF: the epoch reset hard-cleared the voices. */
static void q0_open(struct a1 *a, uint32_t now)
{
    struct q0 *q = Q0;
#if CORE0_BULK_EXCLUDE
    if (q->excl) {                            /* the reset has hard-cleared the voices: stay closed until a DSP load */
        q->ph = Q_CLOSED;
        q->c_excl++;
        return;
    }
#endif
    uint32_t sem = eng_lock();
    q->epoch = q->req;
    q->cons = q->prod;
    q->t_read = now;
    q->reval = 0;
    q->ph = Q_OPEN;
#if CORE0_BENCH
    q->bst |= BST_OPENED;
#endif
    a->ad_pub = AD_DEFAULT;
    q->sr_pub = SR_DEFAULT;
#if FM_PATCH                                  /* the reset restored the DSP's FM defaults too (fm_patch.h) */
    q->fm_pub = (uint16_t)FM_DEF_PATCH;
    q->md_pub = FM_DEF_MOD;
#endif
    ad_barrier(a);
    a->phase = PH_OPEN;
    a->booted = 1;
    struct tm *t = tm();
    a->tm_gen = t ? t->gen : 0;
    eng_unlock(sem);
    q->c_open++;
}

/* After a bulk interval: header, ACK, FAULT, PROD, CONS fresh. Same identity: resume (mirrors rebuilt, published
 * voices and generations kept). Otherwise: full epoch recovery. */
static void q0_reconcile(struct a1 *a, uint32_t now)
{
    struct q0 *q = Q0;
#if CORE0_BULK_EXCLUDE
    if (q->excl) {                            /* no pause / ownership claim: the epoch recovery, then closed */
        q->reval = 0;
        q0_fail(a);
        return;
    }
#endif
    uint32_t h[3], r[4];
    q0_rd(Q0_MAGIC_AT, 3, h);
    q0_rd(Q0_ACK, 4, r);
    q->reval = 0;
    if (h[0] == Q0_MAGIC && h[1] == Q0_VERSION && h[2] == Q0_READY && r[0] == q->epoch && !r[3] && r[1] == q->prod
            && ((r[1] - r[2]) & M24) <= Q0_CAP) {
        q->cons = r[2];
        q->t_read = now;
        q->c_reval++;
        return;
    }
    q0_fail(a);
}

static void q0_deferred(struct a1 *a)
{
    struct q0 *q = Q0;
    uint32_t d = q->defer;
    q->defer = 0;
    if (d & DEF_CLOSE)
        a1_close(a);
    if (d & DEF_OFFS) {
        for (uint32_t i = 0; i < q->noff; i++)
            a1_release_at(a, q->off[i] >> 8, q->off[i] & 0x7Fu, q->offsnap[i]);
        q->noff = 0;
    }
    if (d & DEF_STOP)
        release_all(a, WHY_STOP);
    if (d & DEF_CC123)
        release_all(a, WHY_CC123);
    if (d & DEF_CHOKE)
        all_off(a, WHY_CHOKE, 0);
    if (d & DEF_EDIT)
        all_off(a, WHY_EDIT, 0);
}

/* Deferred boundaries (events staged while our transaction was in flight) are applied before any later note event or
 * cut is admitted: a staged off still resolves to the instance that was held when it
 * arrived, and a staged Stop / choke / edit / close never reaches a note pressed after it. Every note entry and cut
 * entry runs this first (busy is clear there), and so does the Main pass. */
#define Q0_FLUSH(a)     do { if (Q0->defer && !Q0->busy) q0_deferred(a); } while (0)

static void q0_service(struct a1 *a)
{
    struct q0 *q = Q0;
    if (q->defer)
        q0_deferred(a);
    if (q->ph == Q_CLOSED || q->ph == Q_QUAR)
        return;
    if (o1_faulted()) {                       /* O1 quarantine: closed, no further queue access, no healing */
        q->ph = Q_QUAR;
        a1_close(a);
        return;
    }
    if (q->bulk)
        return;
    uint32_t now = TICK();
    if (q->ph == Q_RECOVER) {                 /* the in-flight producer has finished: purge, reset, then a new epoch */
        a1_close(a);
#if CORE0_RECOVER
        q->ph = (q->rf & RF_RINIT) ? Q_INIT : Q_HEADER;
        q->rf &= (uint8_t)~(RF_RINIT | RF_POST);   /* a recovery ends a pending post-init check (a reinit, if any,
                                                      zeroes REJECTED again) */
#else
        q->ph = Q_HEADER;
#endif
        q->t_ph = q->t_poll = now;
        q->slow = 0;
    }
#if CORE0_RECOVER
    /* The own reinit (Main only: bulk 0, not busy, no O1, checked above). Q_INIT: MAGIC, VERSION and a valid READY
     * (no overlapping initializer), FAULT and REJECTED 0 (no reinit over a faulted queue), INIT_COUNT recorded as c0,
     * RF_TX committed, t_ph stamped, then READY = 0 once. Q_INITW, once per pass, deciding on the tick read AFTER the
     * blocking header read: READY valid with INIT_COUNT c0 + 1 on two consecutive reads -> Q_HEADER (the existing
     * epoch and barrier follow; RF_POST: at that epoch's ACK REJECTED must still be 0, else CLOSED with excl). The
     * deadline Q0_WAIT_MS is NOT absolute: it bounds the wait while READY is invalid.
     * A completed init (both sightings consistent; the producer was closed throughout, so nothing could publish) is
     * accepted whatever the elapsed time, but not silently: confirmed Q0_WAIT_MS or more after READY=0 sets the
     * sticky RF_LATE (both sightings are post-read samples and the second is the later, so a late first sighting
     * makes the confirmation late too). Every other outcome (READY still invalid at the deadline, a count jump or
     * reset, MAGIC / VERSION changed, READY with the old count, a change between the sightings, an exclusion from
     * elsewhere) is CLOSED with excl, no retry. */
    if (q->ph >= Q_INIT) {                    /* the own reinit through READY (the DSP's existing init entry) */
        uint32_t h[4];
        if (q->excl)
            goto shut;
        q0_rd(Q0_MAGIC_AT, 4, h);
        if (h[0] != Q0_MAGIC || h[1] != Q0_VERSION)
            goto shut;
        if (q->ph == Q_INIT) {
            if (h[2] != Q0_READY || (q->rf & RF_TX))   /* No overlapping initializer; one READY=0 per attempt */
                goto shut;
            uint32_t f[3];
            q0_rd(Q0_FAULT, 3, f);            /* FAULT, CONSUMED, REJECTED: never reinit over a faulted queue */
            if (f[0] || f[2])
                goto shut;
            q->init_c0 = h[3];
            q->rf = (uint8_t)((q->rf & ~RF_SEEN) | RF_TX);   /* committed BEFORE the frame: a transfer that begins
                                                                 from here on excludes (back-to-back rule) */
            __asm__ volatile("" ::: "memory");
#if CORE0_RECOVER_TIMING
            q->t_init_last = T_INCOMPLETE;    /* this attempt's outcome is unknown until the confirmation */
#endif
            q->t_ph = TICK();                 /* the deadline's origin: immediately before the single READY=0 frame */
            q0_wr1(Q0_READY_AT, 0);
            q->ph = Q_INITW;
            return;
        }
        uint32_t want = (q->init_c0 + 1u) & M24;
        now = TICK();                         /* after the blocking header read: no decision on a stale tick */
        uint32_t late = (int32_t)(now - q->t_ph) >= (int32_t)Q0_WAIT_MS;
        if (h[2] == Q0_READY) {
            if (h[3] != want)
                goto shut;
            if (q->rf & RF_SEEN) {
#if CORE0_RECOVER_TIMING
                q->t_init_last = 0;           /* READY=0 frame -> this post-read tick, saturated */
                q0_tmax(&q->t_init_last, now - q->t_ph);
                q0_tmax(&q->t_init_max, now - q->t_ph);
                if (late && q->c_init_late != 0xFFu)
                    q->c_init_late++;
#endif
                q->ph = Q_HEADER;
                q->t_ph = q->t_poll = now;
                q->rf = (uint8_t)((q->rf & ~(RF_SEEN | RF_TX)) | RF_POST);   /* confirmed: the attempt is over;
                                                                 its epoch ACK still owes the REJECTED check */
                if (late)
                    q->rf |= RF_LATE;         /* accepted after the deadline: reported (warning), sticky */
                q->c_init++;
                return;
            }
            q->rf |= RF_SEEN;                 /* READY and the new count: stable on the next pass */
            return;
        }
        if ((q->rf & RF_SEEN) || (h[3] != want && h[3] != q->init_c0) || late)
            goto shut;
        return;
shut:                                         /* uncertain: closed until a DSP load, no retry */
        q->rf &= (uint8_t)~RF_POST;           /* also the post-init epoch's refusals (pre-REQ, post-ACK) */
        q->excl = 1;
        q->ph = Q_CLOSED;
        q->c_excl++;
        return;
    }
#endif
    if (q->ph == Q_OPEN) {
#if CORE0_RECOVER
        if (q->reval || q->excl)              /* excl while OPEN: another task's claim: reconcile, then closed */
#else
        if (q->reval)
#endif
            q0_reconcile(a, now);
        else if ((int32_t)(now - q->t_read) >= (int32_t)Q0_REFRESH_MS)
            q0_refresh(a);                    /* health (FAULT, distance) every 50 ms while OPEN, drained or not:
                                                 a policy under the Main / bus progress assumption, not a stall
                                                 detector */
        return;
    }
    if (q->slow && (int32_t)(now - q->t_poll) < (int32_t)Q0_SLOW_MS)
        return;
    q->t_poll = now;
    if (q->ph == Q_HEADER) {
        if (q0_header_ok(q)) {
#if CORE0_RECOVER
            /* The epoch after an own reinit (RF_POST): fail before the request. The reinit zeroed FAULT and REJECTED
             * (the DSP initializer), so a non-zero FAULT or REJECTED here is a queue fault since the reinit: no REQ,
             * CLOSED with excl, no retry (the reinit's own exclusion). A fault after this read is caught by the
             * REJECTED read after the ACK (Q_REQ_PH). Other epochs (boot; a runtime recovery, which requests BECAUSE of
             * a fault) are unchanged. */
            if (q->rf & RF_POST) {
                uint32_t f[3];
                q0_rd(Q0_FAULT, 3, f);        /* FAULT, CONSUMED, REJECTED */
                if (f[0] || f[2])
                    goto shut;
            }
#endif
            q0_request(q, now);
        }
        else if (!q->slow && (int32_t)(now - q->t_ph) >= (int32_t)Q0_WAIT_MS) {
            q->slow = 1;
            q->c_nohdr++;
        }
    } else if (q->ph == Q_REQ_PH) {
        uint32_t r[4];
        q0_rd(Q0_ACK, 4, r);
        if (r[0] == q->req && !r[3] && r[1] == q->prod && r[2] == q->prod) {
#if CORE0_RECOVER_TIMING
            q0_tmax(&q->t_ack_max, TICK() - q->t_ack0);  /* after the ACK read and its checks */
#endif
#if CORE0_RECOVER
            /* The epoch after an own reinit (RF_POST): fail closed on REJECTED before anything opens. The DSP's
             * queue_new_epoch clears FAULT before it ACKs, so a queue_fault between the reinit and this ACK leaves
             * FAULT 0 here; REJECTED survives the epoch (only the DSP initializer zeroes it, and the reinit did) and
             * queue_fault, the only writer that sets FAULT, increments it in the same routine. Non-zero (or no frame:
             * Q0_NONE): no open, no barrier, CLOSED with excl, no retry (the reinit's own exclusion). */
            if (q->rf & RF_POST) {
                q->rf &= (uint8_t)~RF_POST;
                q0_rd(Q0_REJECTED, 1, r);
                if (r[0])
                    goto shut;                /* into the reinit block's exclusion tail (no VLA in scope) */
            }
#endif
            q0_open(a, now);
        } else if (!q->slow && (int32_t)(now - q->t_ph) >= (int32_t)Q0_WAIT_MS) {
            q->slow = 1;
            q->c_noack++;
        }
    }
}

/* ---- bulk SHI intervals (o1wait.c wrappers, Main task) ------------------------------------------------------------- */

#if CORE0_BENCH
/* Bench, runtime bulk (any bulk interval that begins after the queue has been OPEN since the latest DSP load,
 * whatever the phase now: OPEN, recovery, a new admission). This path is UNQUALIFIED and excluded from the allowed
 * bench stimuli (no runtime content loading while the synth is open). What the
 * ARM does is a DIAGNOSTIC, not containment: it requests a new epoch (REQ = ACK + 1; the DSP's reset hard-clears its
 * voices) and waits a bounded Q0_SILENCE_MS (plus a poll backstop) for the ACK. ACKed: c_silence. Not ACKed, or the
 * request impossible (our bus transaction in flight, O1): c_loud, and the stock transfer PROCEEDS anyway (a timeout
 * is not containment). Then recovery and exclusion until the next DSP load. Main task,
 * outside any lock and hold; reads and the REQ write keep the addressing policy (q0_rd / q0_wr1 invalidate the cache). */
static void q0_silence(struct a1 *a)
{
    struct q0 *q = Q0;
    uint32_t r[4];
    q0_rd(Q0_ACK, 4, r);
    uint32_t req = (r[0] + 1u) & M24, t0 = TICK();
    q0_wr1(Q0_REQ, req);
    q->req = req;
    for (uint32_t n = 0; n < Q0_SILENCE_POLLS && (int32_t)(TICK() - t0) <= (int32_t)Q0_SILENCE_MS; n++) {
#if CORE0_RECOVER
        q0_rd(Q0_ACK, 4, r);                  /* ACK..FAULT: quiet = ACK with FAULT 0 */
        if (r[0] == req && !r[3]) {
#else
        q0_rd(Q0_ACK, 1, r);
        if (r[0] == req) {
#endif
            q->c_silence++;
#if CORE0_RECOVER_TIMING
            q0_tmax(&q->t_ack_max, TICK() - t0);
#endif
            break;
        }
    }
#if CORE0_RECOVER
    uint32_t loud = r[0] != req || r[3];
#else
    uint32_t loud = r[0] != req;
#endif
    if (loud)
        q->c_loud++;
    if (q->ph != Q_RECOVER)
        q0_fail(a);                           /* one recovery count per fault: a pending recovery is not counted twice */
#if CORE0_RECOVER
    if (loud)                                 /* only a loud entry excludes; an ACKed one reinits after the transfer */
#endif
        q->excl = 1;
}
#endif

static void q0_bulk_enter(void)
{
    struct a1 *a = a1();
    if (!a)
        return;
    struct q0 *q = Q0;
#if CORE0_RECOVER
    if (q0_foreign()) {                       /* another task: claim under BUS_SEM, excluded, nothing else */
        SEM_TAKE(BUS_SEM);
        __atomic_add_fetch(&q->bulk, 1, __ATOMIC_RELAXED);
        q->excl = 1;
        q->c_xtask += q->c_xtask != 0xFFu;   /* entries (a pack counts 1 + its slots), saturating: diagnostic */
        q0_give();
        return;
    }
#endif
    if (q->busy & B_BUS)
        q->c_mid++;                           /* a bulk entry inside a queue hold: never expected (test asserts 0) */
#if CORE0_BENCH
    if (!q->bulk) {
        q->bst &= (uint8_t)~BST_LIVE;
        if ((q->bst & BST_OPENED) && q->ph != Q_QUAR) {
            q->bst |= BST_LIVE;               /* after an OPEN: the runtime rule, never the startup exemption */
#if CORE0_RECOVER
            if ((q->rf & RF_TX) || q->ph >= Q_INIT) {   /* back-to-back: READY=0 may have been sent: exclude until
                                                             a DSP load, no retry */
                q->excl = 1;
                q->c_loud++;
            } else if (q->ph != Q_CLOSED) {   /* CLOSED here = already excluded at an ACK: the reset hard-cleared.
                                                 A reinit still pending (RF_RINIT, READY=0 not sent) re-quiesces
                                                 here like any other runtime entry */
                if (!q->busy && !o1_faulted()) {
                    q0_silence(a);
                } else {
                    q->c_loud++;
                    q->excl = 1;              /* the request was impossible: never a reinit after it */
                }
            }
#else
            if (q->ph != Q_CLOSED) {          /* CLOSED here = already excluded at an ACK: the reset hard-cleared */
                if (!q->busy && !o1_faulted())
                    q0_silence(a);
                else
                    q->c_loud++;
            }
#endif
        }
    }
#endif
#if CORE0_RECOVER
    __atomic_add_fetch(&q->bulk, 1, __ATOMIC_RELAXED);   /* another task may claim concurrently */
#else
    q->bulk++;
#endif
    q->c_bulk++;
}

static void q0_bulk_leave(void)
{
    if (!a1())
        return;
    struct q0 *q = Q0;
#if CORE0_RECOVER
    if (q0_foreign()) {                       /* another task: its claim ends; Main finalizes nothing for it */
        __atomic_sub_fetch(&q->bulk, 1, __ATOMIC_RELAXED);
        return;
    }
    if (q->bulk && !__atomic_sub_fetch(&q->bulk, 1, __ATOMIC_RELAXED)) {
#else
    if (q->bulk && !--q->bulk) {
#endif
#if CORE0_BULK_EXCLUDE
        /* Bench policy: bulk preservation of the DSP voice / filter state and tables is not established, so the synth
         * path claims none. Any bulk interval excludes it until the next DSP load: an open queue takes one epoch
         * recovery (the DSP hard-clears every voice, so nothing is left sounding), and admission then stays closed
         * at the ACK; a queue not yet open just closes. */
#if CORE0_BENCH
        /* Startup content: the composition restore's transfers follow the DSP ready tail. A
         * bulk interval that began before the queue was EVER OPEN since the latest DSP load published nothing and found
         * nothing of ours sounding, and admission restarts after it, so the epoch reset that opens the queue comes
         * after the transfer: it does not exclude. The DSP side stays unproven.
         * After any OPEN (also during a recovery or a new admission): the runtime rule, excluded. */
        if (!(q->bst & BST_LIVE)) {           /* never OPEN since the latest DSP load: genuine startup */
            if (q->ph == Q_HEADER || q->ph == Q_REQ_PH) {
                q->ph = Q_HEADER;
                q->t_ph = q->t_poll = TICK();
                q->slow = 0;
                q->c_boot_bulk++;
            }
            return;
        }
#if CORE0_RECOVER
        /* The pre-transfer epoch was ACKed with FAULT 0 (excl clear): the pending recovery (Q_RECOVER) purges, the own
         * reinit runs (Q_INIT, Q_INITW), then a NEW epoch and the barrier, as at startup. Loud or impossible:
         * excluded as before. */
        if (q->ph == Q_OPEN) {                /* the epoch request was impossible (busy / O1): as without it */
            q->excl = 1;
            q->reval = 1;
        } else if (!q->excl && q->ph == Q_RECOVER) {
            q->rf |= RF_RINIT;                /* silent: the own reinit at the next pass */
        }
#else
        q->excl = 1;
        if (q->ph == Q_OPEN)                  /* the epoch request was impossible (busy / O1): as without it */
            q->reval = 1;
#endif
        return;
#endif
        q->excl = 1;
        if (q->ph == Q_OPEN)
            q->reval = 1;                     /* the next Main pass: q0_fail -> epoch -> closed at the ACK */
        else if (q->ph != Q_QUAR)
            q->ph = Q_CLOSED;
#else
        if (q->ph == Q_OPEN)
            q->reval = 1;
#endif
    }
}

#if CORE0_RECOVER
/* Allocator validation. Inside a transfer interval, a stock allocator answer outside the real pool
 * (POOL_LO + 0x1000 i up to POOL_HI, 4 KiB aligned; the receiver itself accepts any
 * destination) is refused: hook_fs_alloc returns 0, the stock slot body's 'exhausted' path ends that upload before
 * any PCM goes to the address, and the synth path is excluded until a DSP load (no reinit, no reopen). */
static uint32_t q0_alloc_bad(uint32_t r)
{
    struct q0 *q = Q0;
    if (!q->bulk || (!(r & 0xFFFu) && r - POOL_LO <= POOL_HI - POOL_LO))   /* bulk > 0: an enter passed a1() */
        return 0;
    q->excl = 1;
    q->c_alloc_bad++;
    return 1;
}
#endif

#if CORE0_BENCH
/* ---- SysEx 0F: read-only core-0 telemetry (bench) ---------------------------------------------------------
 * Request F0 00 20 29 00 60 0F <n1> <n0> F7 (exactly 10 bytes: a 14-bit request nonce chosen by the host, echoed so
 * a stale reply cannot pass for a fresh one; anything else is ignored). hook_sysex runs in the Main task.
 * Reply, TELEM_LEN = 157 bytes (173 with CORE0_RECOVER), every value big-endian 7-bit digits:
 *   F0 00 20 29 00 60 0F <status> <ph> <flags> <n1> <n0>
 *   <11 DSP words x 4 digits: MAGIC VERSION READY, REQ ACK PROD CONS FAULT CONSUMED REJECTED TICK (X:4D07, calls to
 *    queue_poll: two reads apart show foreground progress; a diagnostic, never a reason to recover)> (0 unless
 *    status 0)
 *   <4 ARM words x 4 digits: prod mirror, cons mirror, epoch, req>
 *   <28 counters x 3 digits: hdr badver nohdr noack req open full rd fault bulk batch ent rel reent cancel reval mid
 *    bulk_drop rej_over (struct q0 order), excl, hold_wait split silence loud boot_bulk telem telem_busy, overflow>
 *   CORE0_RECOVER only (TELEM_LEN 173): <INIT_COUNT x 4 digits (0 unless status 0)> <c_init rf c_xtask c_alloc_bad
 *    x 3 digits>; ph also INIT (6) and INITW (7); the first bounded read covers 4 words (+ INIT_COUNT)
 *   OPTIONAL CORE0_RECOVER_TIMING (TELEM_LEN 185): then <t_ack_max t_init_max t_init_last c_init_late x 3 digits>
 *    (ticks; 0xFFFE saturated, 0xFFFF incomplete)
 *   F7
 * status 0: DSP words read now; 1: not idle, no DSP access (a bulk interval, our transaction, an envelope or note
 * entry pending, or a voice held or inside its release deadline); 2: no DSP access allowed (closed before the ready
 * tail or during a DSP load, or O1 quarantine). flags: b0 bulk, b1 busy, b2 not quiet, b3 O1 fault, b4 excluded,
 * b5 rejection quarantine, b6 reconcile pending. Bounded: two reads (3 + 8 words), each its own hold under the bus
 * semaphore with the address cache invalidated (the next write re-addresses); never inside a write hold; no write.
 * Operator rule on top (tool side, as cmd 08): the sequencer stopped, no pack, sample or project loading. */
#if CORE0_RECOVER_TIMING
#define TELEM_LEN       (12u + 4u * 16u + 3u * 36u + 1u)   /* + t_ack_max t_init_max t_init_last c_init_late */
#elif CORE0_RECOVER
#define TELEM_LEN       (12u + 4u * 16u + 3u * 32u + 1u)   /* + INIT_COUNT (4 digits); c_init rf c_xtask c_alloc_bad (3) */
#else
#define TELEM_LEN       (12u + 4u * 15u + 3u * 28u + 1u)
#endif
_Static_assert(__builtin_offsetof(struct q0, c_rej_over) - __builtin_offsetof(struct q0, c_hdr) == 2 * 18
               && __builtin_offsetof(struct q0, c_telem_busy) - __builtin_offsetof(struct q0, c_hold_wait) == 2 * 6
               && __builtin_offsetof(struct q0, req) == 12, "telemetry blocks are contiguous (19 + 7 u16, 4 u32)");

static uint32_t q0_quiet(struct a1 *a)
{
    uint32_t now = TICK();
    if (a->nq_n || a->pq_alloff || a->pq_kill)
        return 0;
    for (uint32_t v = 0; v < 4; v++) {
        struct a1voice *x = &a->v[v];
        if (x->state == V_HELD || (x->state == V_REL && (int32_t)(now - a->dl[v]) < 0))
            return 0;
    }
    return 1;
}

static void q0_telem(const uint8_t *m, uint32_t len)
{
    struct a1 *a = a1();
    if (len != 10 || !a)
        return;
    struct q0 *q = Q0;
    volatile uint8_t r[TELEM_LEN];            /* every byte is written below */
    header(r, 0x0F);
    uint32_t quiet = q0_quiet(a), flt = o1_faulted();
    uint32_t loaded = q->ph != Q_CLOSED || q->excl;
    uint32_t st = (flt || q->ph == Q_QUAR || !loaded) ? 2u : (q->bulk || q->busy || !quiet) ? 1u : 0u;
#if CORE0_RECOVER
    uint32_t w[12];
    if (!st) {
        q0_rd(Q0_MAGIC_AT, 4, w);             /* MAGIC VERSION READY INIT_COUNT */
        w[11] = w[3];
        q0_rd(Q0_REQ, 8, w + 3);
#else
    uint32_t w[11];
    if (!st) {
        q0_rd(Q0_MAGIC_AT, 3, w);
        q0_rd(Q0_REQ, 8, w + 3);
#endif
        q->c_telem++;
    } else if (st == 1) {
        q->c_telem_busy++;
    }
    r[7] = (uint8_t)st;
    r[8] = q->ph;
    r[9] = (uint8_t)((q->bulk ? 1 : 0) | (q->busy ? 2 : 0) | (quiet ? 0 : 4) | (flt ? 8 : 0) | (q->excl ? 16 : 0)
                     | (q->rej_quar ? 32 : 0) | (q->reval ? 64 : 0));
    r[10] = m[7] & 0x7Fu;                     /* the request nonce, echoed */
    r[11] = m[8] & 0x7Fu;
#if CORE0_RECOVER_TIMING
    uint32_t v[52], n = 0;                    /* the CORE0_RECOVER layout, then t_ack_max t_init_max t_init_last
                                                 c_init_late */
#elif CORE0_RECOVER
    uint32_t v[48], n = 0;                    /* 11 DSP + 4 ARM words (4 digits), then 28 counters (3 digits); tail:
                                                 INIT_COUNT (4 digits), c_init, rf, c_xtask, c_alloc_bad (3) */
#else
    uint32_t v[43], n = 0;                    /* 11 DSP + 4 ARM words (4 digits), then 28 counters (3 digits) */
#endif
    for (uint32_t i = 0; i < 11; i++)
        v[n++] = st ? 0u : w[i];
    for (uint32_t i = 0; i < 4; i++)
        v[n++] = (&q->prod)[i];               /* prod, cons, epoch, req */
    for (uint32_t i = 0; i < 19; i++)
        v[n++] = (&q->c_hdr)[i];              /* c_hdr .. c_rej_over, in struct order */
    v[n++] = q->c_excl;
    for (uint32_t i = 0; i < 7; i++)
        v[n++] = (&q->c_hold_wait)[i];        /* hold_wait, split, silence, loud, boot_bulk, telem, telem_busy */
    v[n++] = a->c_overflow;
#if CORE0_RECOVER
    v[n++] = st ? 0u : w[11];
    v[n++] = q->c_init;
    v[n++] = q->rf;
    v[n++] = q->c_xtask;
    v[n++] = q->c_alloc_bad;
#endif
#if CORE0_RECOVER_TIMING
    v[n++] = q->t_ack_max;
    v[n++] = q->t_init_max;
    v[n++] = q->t_init_last;
    v[n++] = q->c_init_late;
#endif
    volatile uint8_t *p = r + 12;
    for (uint32_t i = 0; i < n; i++) {
#if CORE0_RECOVER
        uint32_t d = i < 15 || i == 43 ? 4u : 3u;
#else
        uint32_t d = i < 15 ? 4u : 3u;
#endif
        put7(p, v[i], (int)d);
        p += d;
    }
    *p = 0xF7;                                /* p = r + TELEM_LEN - 1 */
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

#if BENCH_METER_READ
/* SysEx 08, the restricted core-0 meter readout (bench only, BENCH_METER_READ=1; not in the 0.2 firmware):
 *   request F0 00 20 29 00 60 08 <n1> <n0> <a1> <a0> <c> F7        nonce n (14 bit), word offset a from X:$5C00, count c
 *   reply   F0 00 20 29 00 60 08 <st> <n1> <n0> <a1> <a0> <c> <c x 4 digits> F7     (no words when st is 3)
 * st 0: words read; 1: not idle; 2: no DSP access allowed (the 0F status rule, q0_telem); 3: refused (length, a
 * byte above 0x7F, c not 1..16, a + c above $260: the window [0x5C00, 0x5E60)); 4: the reply deadline passed. The
 * idle rule and the deadline are checked before EVERY word; a refusal or a stop makes no further DSP access, and
 * every word of a reply that is not st 0 is zero. Each word is one q0_rd (bus semaphore, address cache invalidated
 * after it, never inside a write hold since the hold is part of the rule); core 0, X only; no write path. */
#define MR_LO           0x5C00u
#define MR_WORDS        0x260u          /* [0x5C00, 0x5E60): 608 words (schema window_end_exclusive) */
#define MR_MAX          16u
#define MR_DEADLINE_MS  50u

/* The 0F status rule (q0_telem's expression, the same fields in the same order), 2 without struct a1. */
__attribute__((noinline)) static uint32_t q0_meter_st(void)
{
    struct a1 *a = a1();
    if (!a)
        return 2u;
    struct q0 *q = Q0;
    uint32_t quiet = q0_quiet(a), flt = o1_faulted();
    uint32_t loaded = q->ph != Q_CLOSED || q->excl;
    return (flt || q->ph == Q_QUAR || !loaded) ? 2u : (q->bulk || q->busy || !quiet) ? 1u : 0u;
}

__attribute__((noinline)) static void q0_meter(const uint8_t *m, uint32_t len)
{
    uint8_t r[14 + 4 * MR_MAX];               /* escapes to TX_SYSEX through out[]: every byte sent is written */
    uint32_t w[MR_MAX], st = 3, c = 0, bad = len != 13, addr = 0;
    header((volatile uint8_t *)r, 0x08);
    for (uint32_t i = 0; i < 5; i++) {        /* message bytes beyond len are never read */
        uint32_t b = len == 13 ? m[7 + i] : 0u;
        bad |= b & 0x80;
        r[8 + i] = (uint8_t)(b & 0x7F);
    }
    if (!bad) {
        c = m[11];
        addr = (uint32_t)m[9] << 7 | m[10];   /* < 2^14, c <= 127: no wrap in the sum */
        if (c - 1u < MR_MAX && addr + c <= MR_WORDS)
            st = 0;
        addr += MR_LO;
    }
    if (st)
        c = 0;
    uint32_t t0 = TICK();
    for (uint32_t i = 0; !st && i < c; i++) {
        st = q0_meter_st();
        if (!st && TICK() - t0 > MR_DEADLINE_MS)
            st = 4;
        if (!st)
            q0_rd(addr + i, 1, &w[i]);
    }
    r[7] = (uint8_t)st;
    for (uint32_t i = 0; i < c; i++)
        put7((volatile uint8_t *)r + 13 + 4 * i, st ? 0u : w[i], 4);
    r[13 + 4 * c] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, 14 + 4 * c};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
#endif
#endif
