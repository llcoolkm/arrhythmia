/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* Engine-pack state (ENGINE_PACK = ENGINE_OPT && CARD_DSP_MAIN).
 * Our reserved block is zeroed by hook_boot. Keep publication, residency and candidate
 * selection separate: a1.eng exposes the track-8 kind; r_* records the loaded pair and
 * health; c_* identifies the selected file. att counts transactions; rgen counts resident
 * replacements, including in-hook restarts. sel arms one send; c0out records its outcome.
 */
#ifndef ENGINEPACK_H
#define ENGINEPACK_H

#define EO_MAGIC        0x31504F45u     /* "EOP1" */
enum { EP_IDLE, EP_SELECT, EP_SAMPLES };                /* phase */
enum { SEL_STOCK, SEL_ENGINE, SEL_C1 };                 /* SEL_ENGINE: the next core-0 send sends the candidate (armed
                                                           by the transaction); SEL_C1: the candidate went out
                                                           complete, the next core-1 send composes the suppression
                                                           set (one shot; anything else sends stock core 1) */
enum { R_STOCK, R_ENGINE };                             /* res pair */
enum { C0_NONE, C0_CANDIDATE, C0_STOCK_NODATA, C0_STOCK_RESTART, C0_TERMINAL, C0_STOCK,
       C0_WAIT };                                       /* c0out; C0_WAIT: the complete candidate's loader wait timed
                                                           out, the continuation unwound (a trial failure, not O1) */
#define EF_OWN          1u              /* the DSP load running is the transaction's own (4C / 4E) */
#define EF_TRIES        2u              /* the candidate is a retry of an opened, unconfirmed TRY (#36 policy) */
#define EF_ABORT        4u              /* a loader wait of an own load timed out: unwind the mode-0 continuation */
#define EF_PUSHED       8u              /* step 5: this transaction's re-push ran (settings, tempo invalidated) */
#define EF_WASENG       16u             /* pub was an engine when 4A cleared it (step 5 rebuilds on a change) */
#define EF_REFRESHED    32u             /* step 5: the project refresh ran too (a project apply had been seen) */
#define EP_ALLOC_SITE1  0x08053F15u     /* first guarded slot-allocation return site */
#define EP_ALLOC_SITE2  0x08054061u     /* second guarded slot-allocation return site */
#define EF_SLOTERR      64u             /* the slot uploading now failed (a read, or the allocator); per slot */

struct eo {
    uint32_t magic;
    uint8_t phase, tf, sel, c0out;              /* tf: this transaction's trial failed */
    uint8_t r_pair, r_kind, r_fault, judged;    /* res; r_fault: the resident failed (liveness, queue); judged: the
                                                   boot marker was judged in this boot */
    uint32_t r_crc;                             /* res body CRC (0 for the stock pair) */
    uint8_t c_pack, c_kind;                     /* cand */
    uint16_t att;                               /* transaction attempt */
    uint32_t c_crc;
    uint16_t rgen;                              /* resident lifecycle generation */
    uint16_t c_words;
    uint8_t n_trav, n_ok, n_fail, fl;           /* slots traversed / ok / failed in phase SAMPLES (empty: the rest
                                                   of n_trav); fl: EF_* */
    uint32_t bad_crc;                           /* session BAD: a body that faulted after its TRY period */
    uint8_t verdict, ptr, busy, loads;          /* last lookup verdict (EV_*), pointer state (PS_*), BUSY refusals,
                                                   DSP loads made by this transaction */
    char c_name[8];                             /* cand body basename in 1:/ENGINES/ (no extension), NUL-padded */
};
_Static_assert(sizeof(struct eo) == EO_SIZE, "struct eo fills its block");

/* Remote pack request (cmd 10 10), lifecycle separate from the engine transaction.
 * Last accepted request remains readable when a later request is refused. No ITCM or file staging is used. */
enum { PK_NONE, PK_QUEUED, PK_RUNNING, PK_DONE, PK_FAILED };
enum { PA_ACCEPT, PA_BUSY, PA_RANGE, PA_PLAYING, PA_CARD, PA_MODULE, PA_FAULT, PA_NOTREADY, PA_MALFORMED };
enum { PR_NONE, PR_OK, PR_REFUSED, PR_CHANGED, PR_FAULT, PR_BUSY, PR_PLAYING, PR_MODULE, PR_CARD, PR_NOTREADY };
struct pk {
    uint32_t seq, started, finished;
    uint8_t state, target, loaded, result;
};
_Static_assert(sizeof(struct pk) == PK_SIZE, "remote pack request fits its reserved block");
#define PK ((volatile struct pk *)PK_AT)

/* Watchdog and load timing of the pack transactions (10 0B layout 2), in RTOS ticks (1 ms), at WD_AT below struct eo
 * (hook_boot zeroes it). A transaction is timed from its admission in the access hook to its end: the access hook's
 * own exit (refused, same engine, terminal), else hook_pack_done. Every 16-bit field saturates at 0xFFFF (at least
 * 65535 ms / feeds / transactions) and never wraps. */
struct wd {
    uint32_t last;                              /* tick of the last watchdog feed (hook_feed: every caller) */
    uint32_t t0, t1;                            /* the last timed transaction's start and end */
    uint32_t c1at;                              /* scratch: tick at the core-1 send of an own load (valid if c1) */
    uint16_t dsp0, dsp1;                        /* its own DSP loads (4C, 4E): the mode switch's entry to the core-1
                                                   send (core 0), and from there to the switch's return (core 1) */
    uint16_t age, gap;                          /* ms since the last feed at the start; the largest time without a
                                                   feed within (age and the open stretch at the end included) */
    uint16_t feeds;                             /* feeds within */
    uint8_t open, c1;                           /* open: started, not ended; c1: the running own load's core-1 send
                                                   was stamped (c1at valid) */
    uint16_t wmax, n;                           /* the largest gap of the timed transactions this boot (only those:
                                                   no interval outside them); how many */
};
_Static_assert(sizeof(struct wd) == WD_SIZE, "struct wd fills its block");
#define WD              ((volatile struct wd *)WD_AT)
#define TV()            (*(volatile uint32_t *)0x20000820u)  /* RTOS tick word */

static struct eo *eo(void)
{
    struct eo *e = (struct eo *)EO_AT;
    return region() && e->magic == EO_MAGIC ? e : 0;
}

/* 1 if the resident is an engine (the core-0 queue's admission and recovery run on it, whatever pub is). */
static uint32_t eo_res_engine(void)
{
    struct eo *e = eo();
    return e && e->r_pair == R_ENGINE;
}

#endif
