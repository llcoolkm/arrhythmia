/* Core-0 synth queue feed, state (CORE0_SYNTH=1 only).
 * Included by notetap.c right after struct a1. The functions are in core0feed.c.
 *
 * Contract (DSP side; PROVISIONAL, not frozen): X:4D00 REQ_EPOCH, 4D01 ACK_EPOCH, 4D02 PROD,
 * 4D03 CONS, 4D04 FAULT, 4D10..4D4F 16 entries of (epoch, word0, word1, commitseq); ready header X:4E90 MAGIC
 * C0A10D, 4E91 VERSION 000202 (ADSR), 4E92 READY 524459 (written last), 4E93 INIT_COUNT.
 *
 * RAM: struct q0 lives in its own fixed block Q0_AT..Q0_AT + Q0_SIZE directly below struct a1 (devkit_abi3.c), so
 * the A1 reservation grows by Q0_SIZE while every offset of struct a1 stays as in 7251. */
#ifndef CORE0FEED_H
#define CORE0FEED_H

#if !AD_LIVE
#error "CORE0_SYNTH=1 builds on the AD profile (AD_LIVE=1)"
#endif

#define Q0_REQ          0x4D00u
#define Q0_ACK          0x4D01u
#define Q0_PROD         0x4D02u
#define Q0_CONS         0x4D03u
#define Q0_FAULT        0x4D04u
#define Q0_ENTRIES      0x4D10u
#define Q0_CAP          16u
#define Q0_MAGIC_AT     0x4E90u         /* MAGIC, VERSION, READY, INIT_COUNT */
#define Q0_READY_AT     0x4E92u
#define Q0_MAGIC        0xC0A10Du
#if FM_PATCH
#define Q0_VERSION      FM_Q0_VERSION   /* FM patch latches (fm_patch.h; 000206, FM_207: 000207 frozen); every other
                                           VERSION refused */
#elif CORE0_KEYSCALE
#define Q0_VERSION      0x000205u       /* key-scaled FM instrument, same tone packet; others refused */
#elif CORE0_TONE
#define Q0_VERSION      0x000204u       /* ADSR + queued live tone; 000202, 000201 refused */
#else
#define Q0_VERSION      0x000202u       /* the ADSR initializer; 000201 (AD) is refused */
#endif
#define Q0_READY        0x524459u
#define M24             0xFFFFFFu
#define Q0_CORE         0u
/* policy values (choices, not measured): */
#define Q0_WAIT_MS      100u            /* header / ACK wait before slow polling; checked once per Main pass */
#define Q0_SLOW_MS      100u            /* slow poll interval after that */
#define Q0_REFRESH_MS   50u             /* CONS / FAULT refresh while entries are outstanding */
#if CORE0_BENCH
#define Q0_BATCH        4u              /* bench pacing (PROVISIONAL, not SHI / FIFO proof): at most 4 entries per write
                                           hold, at most one write hold per Main pass (q0 held); an envelope pair is
                                           never split */
#define Q0_SILENCE_MS   3u              /* bulk entry while OPEN: bounded wait for the hard-clearing epoch ACK */
#define Q0_SILENCE_POLLS 64u            /* backstop: ACK reads per silence attempt */
#define BST_OPENED      1u              /* admission reached OPEN since the latest DSP load (cleared there) */
#define BST_LIVE        2u              /* the outermost bulk interval began after an OPEN: never the startup rule */
#define Q0_TELEM_CMD    0x0Fu           /* SysEx F0 00 20 29 00 60 0F F7: read-only core-0 telemetry (idle only) */
#else
#define Q0_BATCH        8u              /* entries per bus hold (frame pacing is an open hardware gate) */
#endif
#if defined(FM_207) && FM_207
#define Q0_REL_BLOCKS   15669u          /* ranking deadline after a RELEASE: the VERSION 000207 R127 reference, the
                                           longest release from full scale (207 contract frozen: exact first zero
                                           at helper output sample 501382, ceil(501383 / 32) = 15,669 render blocks);
                                           conservative for every R; voice ranking only, never a proof that a voice is
                                           free */
#else
#define Q0_REL_BLOCKS   15597u          /* ranking deadline after a RELEASE: the longest release, R127 from full scale
                                           (DSP reference: 15,597 blocks); conservative for every R */
#endif
#define SR_DEFAULT      (64u | 0u << 7) /* S 64, R 0: the DSP's values after an epoch reset */
#define ENV_W1_MASK     0x0FFFFFFFu     /* N entry of an envelope group: A | D << 7 | S << 14 | R << 21 */
#define TONE_PACE_MS    5u              /* minimum spacing of knob-driven tone issues (AD_PACE_MS precedent) */
#define TONE_NONE       0xFFFFFFFFu
#if FM_PATCH
#define TONE_RESERVE    13u             /* free transport entries a knob-only tone leaves: 4 KILL + 2 ENV + PATCH_FM +
                                           MOD_AD + 4 ONSET + 1 TONE (the dirty full press) */
#define Q0_GROUP_MAX    FM_GROUP_MAX    /* entries of one expanded envelope group (never split) */
#else
#define TONE_RESERVE    11u             /* free transport entries a knob-only tone leaves: 4 KILL + 2 ENV + 4 ONSET + 1 */
#define Q0_GROUP_MAX    2u              /* the ENV AD + SR pair */
#endif
#define TONE_BAR_MARK   1u              /* N-internal bit 0 of a barrier TONE's word0 (cleared before publication) */
#define REJ_KEY(src, note) ((uint16_t)(((uint32_t)(note) & 0x7Fu) | ((uint32_t)(src) & 7u) << 7))
#define REJ_ONE         0x0400u         /* count unit (bits 10..15) */
#define REJ_SAT         63u             /* count saturation: quarantine */     /* no tone known at the DSP (after a load or an epoch: its caches are gone) */

enum { Q_CLOSED, Q_HEADER, Q_REQ_PH, Q_OPEN, Q_RECOVER, Q_QUAR, Q_INIT, Q_INITW };   /* Q_INIT, Q_INITW: CORE0_RECOVER's
                                                                                    own reinit (never entered without it) */
#if CORE0_RECOVER
#define Q0_COUNT_AT     0x4E93u         /* INIT_COUNT (the DSP increments it once per init, mod 2^24) */
#define RF_RINIT        1u              /* rf: a silent runtime bulk ended: the own reinit is due at the next pass */
#define RF_SEEN         2u              /* READY + INIT_COUNT c0+1 seen once (confirmed on the next consistent read) */
#define RF_TX           4u              /* READY=0 may have been sent: the attempt is committed (no retry, no
                                           re-quiesce) until it is confirmed or a DSP load */
#define RF_QCL          8u              /* the rejection quarantine was entered by a CLOSED drop: it survives the close */
#define RF_SKIP         16u             /* one of our bus transactions found the SHI claimed by a transfer under BUS_SEM
                                           and made no frame (sticky, diagnostic) */
#define RF_LATE         32u             /* a reinit was ACCEPTED (READY valid, INIT_COUNT c0+1 on two consecutive reads)
                                           with the post-read tick Q0_WAIT_MS or more after READY=0: a warning, sticky
                                           until a DSP load (dsp_load), never cleared by another recovery */
#define RF_POST         64u             /* a reinit was confirmed and its epoch is not yet ACKed: at that ACK REJECTED
                                           is read before q0_open; non-zero -> CLOSED with excl (cleared at that ACK,
                                           by a recovery and by a DSP load) */
#define Q0_REJECTED     0x4D06u         /* REJECTED: +1 per DSP queue_fault, zeroed only by the DSP initializer */
#define POOL_LO         0x300000u       /* the allocator reset pool 0x300000 + 0x1000 i, i = 0..2694 */
#define POOL_HI         0xD86000u       /* receiver accepts any destination */
#endif
#define B_ENG           1u              /* busy: one of our producers holds the engine lock */
#define B_BUS           2u              /* busy: a queue bus transaction (read or write hold) is in flight */
#define B_HOLD          4u              /* a press is being assembled: publish its group and ONSETs in one batch */
#define DEF_CLOSE       1u              /* deferred while busy: a DSP load entry */
#define DEF_STOP        2u              /* Stop / mute */
#define DEF_CC123       4u
#define DEF_CHOKE       8u
#define DEF_EDIT        16u
#define DEF_OFFS        32u             /* staged key offs */

struct q0 {
    uint32_t prod;                            /* mirror of PROD: the next sequence to publish */
    uint32_t cons;                            /* last CONS read (conservative within one epoch) */
    uint32_t epoch;                           /* admitted epoch (= ACK at opening) */
    uint32_t req;                             /* requested epoch */
    uint32_t t_ph, t_poll, t_read;            /* ticks: phase start, last slow poll, last CONS read */
    uint8_t ph, busy, bulk, reval;            /* Q_*; B_*; bulk nesting; header + mirrors to revalidate */
    uint8_t slow, sus, defer, noff;           /* slow polling; voices held at sustain > 0 (no deadline); DEF_*; staged */
    uint16_t sr_want, sr_iss, sr_pub;         /* S | R << 7, as ad_want / ad_iss / ad_pub hold A | D << 7 */
    uint16_t off[4];                          /* staged key offs while busy: src << 8 | note (bound by offsnap) */
    uint16_t c_hdr, c_badver, c_nohdr, c_noack, c_req, c_open, c_full, c_rd;
    uint16_t c_fault, c_bulk, c_batch, c_ent, c_rel, c_reent, c_cancel, c_reval, c_mid, c_bulk_drop, c_rej_over;
    /* offset 88: rejected-press records: note | src << 7 | count << 10, count 0 = empty */
    uint16_t rej[4];
    uint32_t offsnap[4];                      /* offset 96: a->serial when each staged off arrived (its ownership) */
    uint8_t rej_quar;                         /* offset 112: rejection overflow quarantine (the safe action) */
    uint8_t excl;                             /* offset 113: CORE0_BULK_EXCLUDE: closed after a bulk interval */
    uint16_t c_excl;                          /* offset 114 */
#if CORE0_TONE
    /* live tone (CORE0_TONE; offset 116): depth | cutoff << 7 | resonance << 14 */
    uint32_t tone_iss, tone_pub;              /* last issued (queued or published), last published; TONE_NONE */
    uint32_t t_tone;                          /* tick of the last tone issue (pacing), valid while tone_ok */
#if CORE0_RECOVER
    uint8_t tone_ok;
    uint8_t c_xtask;                          /* (was padding) bulk entries from a task other than Main, saturating */
    uint16_t c_alloc_bad;                     /* (was padding) allocator answers refused inside a transfer interval */
#else
    uint8_t tone_ok, pad_t[3];
#endif
    uint16_t c_tone, c_tone_coal, c_tone_paced, c_tone_note, c_tone_purged, c_tone_wait;
#endif
#if CORE0_BENCH
    /* bench (offset 144): pacing, bulk policy, telemetry */
    uint8_t held;                             /* a queue write hold was made in this Main pass (cleared by the tick) */
    uint8_t bst;                              /* BST_*: opened since the latest DSP load; the outermost bulk began
                                                 after that */
    uint16_t c_hold_wait;                     /* drains deferred to the next Main pass by the one-hold rule */
    uint16_t c_split;                         /* holds that left our entries in N because of the 4-entry cap */
    uint16_t c_silence;                       /* bulk entries while OPEN: hard-clearing epoch ACKed before the bulk */
    uint16_t c_loud;                          /* ... not ACKed within Q0_SILENCE_MS (the bulk ran anyway: counted) */
    uint16_t c_boot_bulk;                     /* bulk intervals before OPEN: admission restarted after them */
    uint16_t c_telem, c_telem_busy;           /* 0F replies with DSP words / refused as not idle */
#endif
#if FM_PATCH
    /* FM latches (offset 160): PATCH_FM word1 (fm) and MOD_AD word1 (md), as sr_want / sr_iss / sr_pub */
    uint16_t fm_want, fm_iss, fm_pub;
    uint16_t c_fm;                            /* PATCH_FM published */
    uint32_t md_want, md_iss, md_pub;
    uint16_t c_md;                            /* MOD_AD published */
    uint16_t c_grp_eq;                        /* groups that expanded to nothing (all equal to the published values) */
#endif
#if CORE0_RECOVER
    /* own reinit (offset 184; the 0xC0 block's last 8 bytes) */
    uint32_t init_c0;                         /* INIT_COUNT read before READY was invalidated */
    uint8_t rf;                               /* RF_*: the own reinit attempt, the closed-drop quarantine, RF_SKIP */
    uint8_t rstk;                             /* bit i: rej[i] holds a press dropped while CLOSED: kept across an
                                                 accept and a close, consumed only by its off */
    uint16_t c_init;                          /* own reinitializations confirmed */
#endif
#if CORE0_RECOVER_TIMING
    /* OPTIONAL (offset 192, block 0xD0). Tick: the tick reader = the word 0x20000820, +1 per SysTick (1 ms; it advances
     * while Main waits in SEM_TAKE / yield loops and while the scheduler is suspended, and stops only with interrupts
     * masked during the bounded wait). Intervals are u32 differences (modulo 2^32, exact below 49.7
     * days), sampled immediately before the frame that starts them and after the blocking read (and the checks)
     * that ends them; quantization 1 tick: a reading n means a true interval in (n - 1, n + 1) ms. Stored saturated
     * at T_SAT (never a valid maximum); t_init_last is T_INCOMPLETE from READY=0 until the confirmation. The tick is
     * the SysTick count, so it does not advance inside an ISR that masks or outranks SysTick; every
     * interval here is started and stopped in the Main task.
     * Stop points: t_init_* after the confirming (second consecutive) header read and its READY / MAGIC / VERSION /
     * exact-count checks; INITW reads no FAULT word (Q_INIT checked FAULT and REJECTED before READY=0, and the next
     * FAULT check is the epoch ACK, which t_ack_max covers; REJECTED is read after that ACK, RF_POST). t_ack_max:
     * one maximum over both paths (not labelled apart), started immediately before the REQ frame and stopped after the ACK read with its FAULT (and, for an
     * epoch, PROD = CONS) checks. A late first sighting never restarts the interval (t_ph is set once, before READY=0). */
    uint16_t t_ack_max;                       /* longest REQ frame -> its ACK read with FAULT 0 (and PROD = CONS for an
                                                 epoch): every ACKed pre-transfer quiesce (q0_silence) and every ACKed
                                                 epoch request (q0_request -> Q_REQ_PH: boot, post-init, recovery) */
    uint16_t t_init_max;                      /* longest own reinit: READY=0 frame -> the confirming header read */
    uint16_t t_init_last;                     /* the latest attempt: T_INCOMPLETE until confirmed (a failed attempt
                                                 leaves it, so an old success never reads as the current one) */
    uint8_t c_init_late;                      /* reinits accepted with RF_LATE, saturating at 255; DSP load clears */
    uint8_t pad_tm;
    uint32_t t_ack0;                          /* tick immediately before the latest epoch REQ frame */
#define T_SAT           0xFFFEu         /* t_*: saturated (>= 0xFFFE ticks) */
#define T_INCOMPLETE    0xFFFFu         /* t_init_last: an attempt started (READY=0 sent) and not confirmed */
#endif
};
_Static_assert(sizeof(struct q0) <= Q0_SIZE, "Q0 block");
#if FM_PATCH
_Static_assert(__builtin_offsetof(struct q0, fm_want) == 160
               && sizeof(struct q0) == (CORE0_RECOVER_TIMING ? 204 : CORE0_RECOVER ? 192 : 184),
               "FM mirrors after the bench block (CORE0_RECOVER: + the reinit state, the 0xC0 block exactly)");
#endif
#if CORE0_RECOVER
_Static_assert(__builtin_offsetof(struct q0, c_xtask) == 129 && __builtin_offsetof(struct q0, c_alloc_bad) == 130
               && __builtin_offsetof(struct q0, init_c0) == 184 && __builtin_offsetof(struct q0, rf) == 188
               && __builtin_offsetof(struct q0, rstk) == 189 && __builtin_offsetof(struct q0, c_init) == 190,
               "CORE0_RECOVER state: telemetry and state checks use these offsets");
#endif
#if CORE0_RECOVER_TIMING
_Static_assert(__builtin_offsetof(struct q0, t_ack_max) == 192 && __builtin_offsetof(struct q0, t_init_last) == 196
               && __builtin_offsetof(struct q0, c_init_late) == 198 && __builtin_offsetof(struct q0, t_ack0) == 200,
               "CORE0_RECOVER_TIMING counters");
#endif
#define Q0              ((struct q0 *)Q0_AT)

/* the envelope value the voice table works with: A | D << 7 in struct a1 (7251 layout), S | R << 7 in struct q0 */
#define ENV_MASK        ENV_W1_MASK
#define ENV_ISS(a)      ((uint32_t)(a)->ad_iss | (uint32_t)Q0->sr_iss << 14)
#define ENV_WANT(a)     ((uint32_t)(a)->ad_want | (uint32_t)Q0->sr_want << 14)
#define ENV_SET_ISS(a, v)  ((a)->ad_iss = (uint16_t)((v) & 0x3FFFu), Q0->sr_iss = (uint16_t)(((v) >> 14) & 0x3FFFu))
#define ENV_SET_WANT(a, v) ((a)->ad_want = (uint16_t)((v) & 0x3FFFu), Q0->sr_want = (uint16_t)(((v) >> 14) & 0x3FFFu))
#endif
