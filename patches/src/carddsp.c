/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* Card DSP engine loading (CARD_DSP=1). Included after cardwrite.c for its CRC helper.
 * The boot-only profile reads 1:/MODULES/ENGINE.DSE; ENGINE_PACK uses per-pack selection.
 * Our file has a 32-byte little-endian header followed by 24-bit big-endian program words:
 * magic, word count, CRC-32 of the payload, queue ABI/version, base identity, and profile metadata.
 * Admission validates the header, complete payload CRC and exact EOF before streaming begins.
 * Pass 2 repeats the checks and withholds the final piece until its CRC and EOF agree.
 * A failure before streaming uses the flash image; a failure during streaming resets and
 * restores the flash pair. Core-1 composition is armed only after core 0 completes.
 *
 * Boot-only streaming uses ITCM scratch before modules run. Pack transactions use Main-stack
 * buffers and hold TOK_MAIN around loading. Reads use 255-byte pieces (85 words).
 * The TRY/BAD marker at ITCM 0x80 and our boot record at 0xA0 survive warm resets.
 * TRY records the payload CRC and liveness state. A failed or never-opened engine becomes
 * BAD on the next boot; an opened but unconfirmed engine receives one retry. BAD blocks
 * the matching payload during warm boots. Power loss discards this history.
 *
 * While TRY is active, cd_live samples our queue's FAULT and TICK once per CD_STEP_MS.
 * Closed, excluded, bulk and reconcile periods pause accounting. Healthy time uses the
 * interval from the previous read's end to the next read's start; scheduler delays cannot
 * inflate it. FAULT, O1 failure or insufficient TICK progress makes failure sticky.
 * CD_LIVE_MS accumulated healthy time clears TRY. Queue admission still checks MAGIC and ABI.
 * CRC/base checks bind bytes, not behavior; engines must implement our queue/startup contracts.
 *
 * The CBR1 record stores boot count, outcome/detail/error, previous marker state, entry and
 * pass timestamps, and later-load count. Command 10 0B reads our marker and record.
 * CARD_DSP_BENCH command 10 0C arms a delayed watchdog test with key "IWDGGO";
 * a later eligible Main pass disables interrupts and waits for watchdog reset.
 */
#if CARD_DSP && !(CARD_WRITE && STEP_CHORDS && CORE0_SYNTH)
#error "CARD_DSP=1 needs the far blob (STEP_CHORDS), the writer's CRC (CARD_WRITE) and the core-0 queue (CORE0_SYNTH)"
#endif
/* CARD_DSP_MAIN composes our integration set and padding with the flash prefix,
 * then appends the admitted engine body. CRBC_ID binds the file to the base contract;
 * crbc_ok validates all four contract parts before candidate transmission. A mismatch
 * refuses the pair. This profile runs in the main blob. */
#if CARD_DSP_MAIN
#if !ENGINE_PACK
#error "CARD_DSP_MAIN=1 is the engine-pack layout: it needs ENGINE_OPT (the CRBC check is the pack transaction's)"
#endif
#define CD_PART         (!FAR_PART)
#define CD_STOCK_W      CRBC_C0_W       /* core-0 flash stream word count */
#else
#define CD_PART         FAR_PART
#endif

#define CD_MARK         ((volatile uint32_t *)0x00000080u)  /* [0] state, [1] ~state, [2] the file's CRC, then the
                                                               liveness state: [3] CD_L_* | tries << 8,
                                                               [4] episode (c_open; CD_PAUSE), [5] DSP TICK,
                                                               [6] ARM tick, [7] healthy ms */
#define CD_L_IDLE       0u              /* the queue has not opened in this boot */
#define CD_L_RUN        1u              /* opened: counting */
#define CD_L_FAIL       2u              /* failed: the next boot marks the file BAD */
#define CD_L_MASK       0xFFu
#define CD_PAUSE        0xFFFFFFFFu     /* episode: paused, the next OPEN step takes a new baseline */

#define CD_STEP_MS      1000u
#define CD_LIVE_MS      10000u
#define CD_TRY          0x59525443u     /* "CTRY" */
#define CD_BAD          0x44414243u     /* "CBAD" */
#define CD_SCRATCH      0x00000300u     /* ITCM, 255 bytes */
#define CD_PIECE        255u
#if ENGINE_PACK
#define CD_MAGIC        0x32455243u     /* "CRE2": +12 queue ABI, +20 kind (1..15), +24 build information, +28..+31 zero */
#define CD_KIND_MAX     15u
#else
#define CD_MAGIC        0x31455243u     /* "CRE1" */
#endif
#define CD_BASE         0x2000u         /* first P word of the engine region */
#define CD_PTOP         0x4000u         /* core-0 P memory size */
#define CD_HDR          32u
#define CD_REC          ((volatile uint32_t *)0x000000A0u)  /* the boot record (header) */
#define CD_RMAGIC       0x31524243u     /* "CBR1" */
enum { CD_O_NONE, CD_O_CARD, CD_O_NOCARD, CD_O_FILBUSY, CD_O_TOKEN, CD_O_MODULE, CD_O_STREAM, CD_O_BADMATCH,
       CD_O_JUDGED, CD_O_NOFILE, CD_O_PASS1, CD_O_P2SAME, CD_O_P2RESTART };
enum { CD_P1_OPEN = 1, CD_P1_SHORTHDR, CD_P1_HDR, CD_P1_BASE, CD_P1_SHORT, CD_P1_LONG, CD_P1_CRC };
#define CD_WD_MS        256u            /* the watchdog test: armed about this long (16 ms steps) before the spin */

#if CD_PART
typedef uint32_t (*cd_fn)(uint32_t, uint32_t, uint32_t, uint32_t);
#define CD_SEND         ((cd_fn)0x080310B1u)    /* bootstrap send (ptr, core): (count + 2) * 3 bytes */
#define CD_SEL          ((cd_fn)0x08031049u)    /* select a core */
#define CD_DESEL        ((cd_fn)0x0803107Bu)    /* deselect a core */
#define CD_BULK         ((cd_fn)0x08046505u)    /* bus TX DMA (ptr, bytes), no wait */
#define CD_IDLE         ((cd_fn)0x08046415u)    /* wait until the bus DMA is idle */
#define CD_FRAME        ((cd_fn)0x08046511u)    /* one 3-byte frame (b0, b1, b2) */
#define CD_RESET        ((cd_fn)0x0802B69Du)    /* DSP reset */
#define CD_DELAY        ((cd_fn)0x0802366Du)    /* stock delay, ticks */
#define CD_BUS          0x20000318u
#define CD_C0_PTR       0x08031658u     /* core-0 flash stream pointer */
#define CD_PRE_A        0x08031638u
#define CD_PRE_B_PTR    0x080315CCu

static uint32_t cd_u24(const volatile uint8_t *p)
{
    return (uint32_t)p[0] << 16 | (uint32_t)p[1] << 8 | p[2];
}

static uint32_t cd_u32(const volatile uint8_t *p)
{
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

#if CARD_DSP_MAIN
#include "crbc.h"

static uint32_t crc_add(uint32_t c, const volatile uint8_t *p, uint32_t n)   /* as cardwrite.c's (far blob) */
{
    for (uint32_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return c;
}

/* Validate both flash headers and the CRC-32 of the four base-contract parts:
 * core-0 stream, integration set, core-1 stream, suppression set. */
static uint32_t crbc_ok(uint32_t p0, uint32_t p1)
{
    const volatile uint8_t *a = (const volatile uint8_t *)p0, *b = (const volatile uint8_t *)p1;
    if ((p0 & 0xFF000000u) != 0x08000000u || (p1 & 0xFF000000u) != 0x08000000u || cd_u24(a) != CRBC_C0_W
            || cd_u24(a + 3) || cd_u24(b) != CRBC_C1_W || cd_u24(b + 3))
        return 0;
    uint32_t c = crc_add(~0u, a, CRBC_C0_BYTES);
    c = crc_add(c, (const volatile uint8_t *)crbc_set(), CRBC_SET_BYTES);
    c = crc_add(c, b, CRBC_C1_BYTES);
    return ~crc_add(c, (const volatile uint8_t *)crbc_edits(), CRBC_EDIT_BYTES) == CRBC_CRC;
}

/* The integration word at P:i from the CRBC part 2 (n | n x (address, word) | pad), else 0x1000000 (not a site). */
static uint32_t cd_site(uint32_t i)
{
    const volatile uint8_t *p = (const volatile uint8_t *)crbc_set() + 3;
    for (uint32_t k = 0; k < CRBC_SET_N; k++, p += 6)
        if (cd_u24(p) == i)
            return cd_u24(p + 3);
    return 0x1000000u;
}

/* Compose our prefix using DMA for unchanged flash spans and frames for integration
 * and padding words. Each DMA span is at most 0xAA words. */
static void cd_pfx(uint32_t ptr)
{
    for (uint32_t i = 0; i < CD_BASE;) {
        uint32_t w = i >= CD_STOCK_W ? 0u : cd_site(i);
        if (w < 0x1000000u) {                 /* a site word or a zero pad word */
            CD_FRAME(w >> 16 & 0xFF, w >> 8 & 0xFF, w & 0xFF, 0);
            i++;
            continue;
        }
        uint32_t j = i;                       /* a run of stock words up to the next site or the stock end */
        while (j < CD_STOCK_W && cd_site(j) >= 0x1000000u && j - i < 0xAA)
            j++;
        CD_BULK(ptr + 6 + 3 * i, 3 * (j - i), 0, 0);
        CD_IDLE(0, 0, 0, 0);
        i = j;
    }
}
#endif

/* The boot's outcome into the record (the marker found, word 2's top byte, is kept). The last call wins. */
static void cd_out(uint32_t o, uint32_t detail, uint32_t fr)
{
    volatile uint32_t *r = CD_REC;
    r[2] = (r[2] & 0xFF000000u) | o | detail << 8 | (fr & 0xFF) << 16;
}

/* "1:/MODULES/ENGINE.DSE" at d. Plain stores: no data section. */
static void cd_path(volatile char *d)
{
    uint32_t n = dir_path(d);
    d[n] = '/'; d[n + 1] = 'E'; d[n + 2] = 'N'; d[n + 3] = 'G'; d[n + 4] = 'I'; d[n + 5] = 'N'; d[n + 6] = 'E';
    d[n + 7] = '.'; d[n + 8] = 'D'; d[n + 9] = 'S'; d[n + 10] = 'E'; d[n + 11] = 0;
}

/* Read exactly n bytes into the caller's buffer: 1 on success. Boot-only profiles
 * use ITCM scratch; pack transactions use a Main-stack buffer. */
static uint32_t cd_read(uint32_t fil, volatile uint8_t *b, uint32_t n)
{
    uint32_t got = 0;
    return F_READ(fil, (uint32_t)b, n, &got) == 0 && got == n;
}

/* Open the file at path into FIL fil and read its header into b: 1 ok. On 0 the file is closed. */
static uint32_t cd_open(uint32_t fil, volatile uint8_t *b, const char *path)
{
    uint32_t r = F_OPEN(fil, path, FA_READ);
    if (r) {
        if (r == FR_NO_FILE || r == FR_NO_PATH)
            cd_out(CD_O_NOFILE, 0, r);
        else
            cd_out(CD_O_PASS1, CD_P1_OPEN, r);
        return 0;
    }
    if (cd_read(fil, b, CD_HDR))
        return 1;
    cd_out(CD_O_PASS1, CD_P1_SHORTHDR, 0);
    F_CLOSE(fil);
    return 0;
}

/* The header in the scratch: the word count if the magic, the count, the zero bytes and the base id (base) hold;
 * else 0. */
__attribute__((noinline)) static uint32_t cd_header(const volatile uint8_t *b)
{
    uint32_t words = cd_u32(b + 4), ok = cd_u32(b) == CD_MAGIC && words && words <= CD_PTOP - CD_BASE;
#if ENGINE_PACK
    uint32_t kind = cd_u32(b + 20);
    ok &= cd_u32(b + 12) == Q0_VERSION && kind && kind <= CD_KIND_MAX;   /* the queue ABI this ARM speaks; a kind */
    for (uint32_t i = 28; i < CD_HDR; i++)
#else
    for (uint32_t i = 20; i < CD_HDR; i++)
#endif
        ok &= b[i] == 0;
    return ok ? words : 0;
}

static uint32_t cd_hdr(const volatile uint8_t *b, uint32_t base)
{
    uint32_t words = cd_header(b);
    if (!words || cd_u32(b + 16) != base) {
        cd_out(CD_O_PASS1, words ? CD_P1_BASE : CD_P1_HDR, 0);
        return 0;
    }
    return words;
}

/* 1 if the open file has no byte left (read into a stack byte: the scratch may hold the last piece). */
static uint32_t cd_eof(uint32_t fil)
{
    uint8_t x;
    uint32_t extra = 0;
    return F_READ(fil, (uint32_t)&x, 1, &extra) == 0 && extra == 0;
}

/* Pass 1: header checks and the CRC of every word byte. Returns the word count (0: refused); *crc = the file's CRC,
 * *base = the base id of the flash stream. The file is closed on every path. */
static uint32_t cd_check(uint32_t fil, volatile uint8_t *b, const char *path, uint32_t ptr, uint32_t *crc,
                         uint32_t *base)
{
    if (!cd_open(fil, b, path))
        return 0;
#if CARD_DSP_MAIN
    uint32_t id = CRBC_ID;                    /* the base contract (the parts themselves: crbc_ok, before pass 2) */
#else
    uint32_t id = ~crc_add(~0u, (const volatile uint8_t *)(ptr + 6), 3 * CD_BASE);
#endif
    uint32_t want = cd_u32(b + 8), words = cd_hdr(b, id), ok = words != 0;
#if ENGINE_PACK
    if (ok && cd_u32(b + 20) != eo()->c_kind) {    /* the identity bound at the lookup: the kind too */
        ok = 0;
        cd_out(CD_O_PASS1, CD_P1_HDR, 0);
    }
#endif
    uint32_t c = ~0u;
    for (uint32_t left = ok ? 3 * words : 0; left;) {
        uint32_t k = left < CD_PIECE ? left : CD_PIECE;
        if (!cd_read(fil, b, k)) {
            ok = 0;
            cd_out(CD_O_PASS1, CD_P1_SHORT, 0);
            break;
        }
        c = crc_add(c, b, k);
        left -= k;
    }
    if (ok && !cd_eof(fil)) {                 /* the file must end after the last word */
        ok = 0;
        cd_out(CD_O_PASS1, CD_P1_LONG, 0);
    }
    F_CLOSE(fil);
    *crc = want;
    *base = id;
    if (ok && ~c != want) {
        ok = 0;
        cd_out(CD_O_PASS1, CD_P1_CRC, 0);
    }
    return ok ? words : 0;
}

/* Reset and restore the flash bootstrap after a pass-2 failure. */
static void cd_restart(uint32_t ptr)
{
    CD_IDLE(0, 0, 0, 0);
    CD_RESET(CD_BUS, 0, 0, 0);
    CD_DELAY(1, 0, 0, 0);
    CD_RESET(CD_BUS, 0, 0, 0);
    CD_SEND(CD_PRE_A, 0, 0, 0);
    CD_DELAY(1, 0, 0, 0);
    CD_SEND(*(volatile uint32_t *)CD_PRE_B_PTR, 1, 0, 0);
    CD_DELAY(1, 0, 0, 0);
    CD_SEND(ptr, 0, 0, 0);
}

/* Pass 2 and the marker. Pass 2 checks again what it sends: the header must equal pass 1's (magic, count, CRC, base
 * id, zero bytes), the CRC of the bytes sent must be the header's, and the file must end after them. The last piece is
 * held back until those checks pass, so a mismatch never completes the bootstrap: a read error or a mismatch closes
 * the file and sends the flash stream after a reset (cd_restart). Returns 1 if the card stream went out complete, 2
 * if cd_restart sent the flash stream, 0 if nothing was sent (the caller sends the flash stream). */
static uint32_t cd_stream(uint32_t fil, volatile uint8_t *b, const char *path, uint32_t ptr, uint32_t words,
                          uint32_t crc, uint32_t base, uint32_t tries)
{
    if (!cd_open(fil, b, path))
        return 0;
    if (cd_hdr(b, base) != words || cd_u32(b + 8) != crc
#if ENGINE_PACK
            || cd_u32(b + 20) != eo()->c_kind  /* the candidate's identity: words, CRC, ABI, base and kind */
#endif
            ) {
        F_CLOSE(fil);
        return 0;                             /* the file changed between the passes: nothing sent yet */
    }
    volatile uint32_t *m = CD_MARK;
#if ENGINE_PACK
    m[3] = tries << 8 | (uint32_t)eo()->rgen << 16;  /* a fresh liveness state, keyed to this resident's rgen */
#else
    m[3] = tries << 8;                        /* a fresh liveness state: CD_L_IDLE, tries, nothing healthy yet */
#endif
    m[7] = 0;
    m[4] = CD_PAUSE;
    m[0] = CD_TRY;
    m[1] = ~CD_TRY;
    m[2] = crc;
    uint32_t n = CD_BASE + words, c = ~0u;
    CD_IDLE(0, 0, 0, 0);
    CD_SEL(0, 0, 0, 0);
    CD_FRAME(n >> 16 & 0xFF, n >> 8 & 0xFF, n & 0xFF, 0);
    CD_FRAME(0, 0, 0, 0);
#if CARD_DSP_MAIN
    cd_pfx(ptr);                              /* one flat image: the composed prefix, then the body below */
#else
    for (uint32_t i = 0; i < CD_BASE; i += 0xAA) {
        uint32_t k = CD_BASE - i < 0xAA ? CD_BASE - i : 0xAA;
        CD_BULK(ptr + 6 + 3 * i, 3 * k, 0, 0);
        CD_IDLE(0, 0, 0, 0);
    }
#endif
    for (uint32_t left = 3 * words; left;) {
        uint32_t k = left < CD_PIECE ? left : CD_PIECE;
        uint32_t ok = cd_read(fil, b, k);
        if (ok) {
            c = crc_add(c, b, k);
            if (left == k)                    /* the last piece: sent only if the bytes sent are the checked ones */
                ok = ~c == crc && cd_eof(fil);
        }
        if (!ok) {
            F_CLOSE(fil);
            CD_DESEL(0, 0, 0, 0);
            m[0] = 0;
            cd_restart(ptr);
            return 2;
        }
        for (uint32_t j = 0; j < k; j += 3)
            CD_FRAME(b[j], b[j + 1], b[j + 2], 0);
        left -= k;
    }
    F_CLOSE(fil);
    CD_IDLE(0, 0, 0, 0);
    CD_DESEL(0, 0, 0, 0);
    return 1;
}

#if ENGINE_PACK

/* Core-1 composition appends our suppression edit records before the preserved
 * suffix. The base contract is checked before either core receives candidate bytes. */
#define C1_PTR          0x08031660u     /* core-1 flash stream pointer */
#define C1_EDIT_W       (CRBC_EDIT_BYTES / 3u)

/* Send n flash bytes in DMA chunks of at most 0xAA words, waiting after each. */
static void c1_bulk(uint32_t a, uint32_t n)
{
    for (uint32_t i = 0; i < n;) {
        uint32_t k = n - i < 3u * 0xAA ? n - i : 3u * 0xAA;
        CD_BULK(a + i, k, 0, 0);
        CD_IDLE(0, 0, 0, 0);
        i += k;
    }
}

/* Core-1 send hook: compose only after the complete core-0 candidate from the same
 * load. Clear the one-shot selection first; other sends use the flash stream. */
uint32_t hook_c1_send(uint32_t ptr, uint32_t core, uint32_t r2, uint32_t r3)
{
    struct eo *e = eo();
    if (e && (e->fl & EF_OWN) && !WD->c1) {   /* load timing (struct wd): the core-1 part of an own load starts */
        WD->c1at = TV();
        WD->c1 = 1;
    }
    if (core != 1 || !e || e->sel != SEL_C1)
        return CD_SEND(ptr, core, r2, r3);
    e->sel = SEL_STOCK;
    uint32_t n = CRBC_C1_W + C1_EDIT_W;
    CD_IDLE(0, 0, 0, 0);
    CD_SEL(1, 0, 0, 0);
    CD_FRAME(n >> 16 & 0xFF, n >> 8 & 0xFF, n & 0xFF, 0);
    CD_FRAME(0, 0, 0, 0);
    c1_bulk(ptr + 6, 3 * CRBC_C1_PFX_W);
    c1_bulk(crbc_edits(), CRBC_EDIT_BYTES);
    c1_bulk(ptr + 6 + 3 * CRBC_C1_PFX_W, 3 * (CRBC_C1_W - CRBC_C1_PFX_W));
    CD_IDLE(0, 0, 0, 0);
    return CD_DESEL(1, 0, 0, 0);
}

/* Build "1:/ENGINES/<name>.DSE" from an 8-byte NUL-padded basename, using plain stores. */
static void cd_epath(char *d, const volatile char *nm)
{
    d[0] = '1'; d[1] = ':'; d[2] = '/'; d[3] = 'e'; d[4] = 'n'; d[5] = 'g'; d[6] = 'i'; d[7] = 'n'; d[8] = 'e';
    d[9] = 's'; d[10] = '/';
    uint32_t n = 11;
    for (uint32_t i = 0; i < 8 && nm[i]; i++)
        d[n++] = nm[i];
    d[n] = '.'; d[n + 1] = 'd'; d[n + 2] = 's'; d[n + 3] = 'e'; d[n + 4] = 0;
}

/* Core-0 send hook consumes the transaction's one-shot SEL_ENGINE selection.
 * Pass 2 composes the admitted candidate and records C0_CANDIDATE on completion.
 * Pre-stream refusal records C0_STOCK_NODATA and sends flash. Mid-stream failure
 * restores flash, records C0_STOCK_RESTART and increments the resident generation.
 * TOK_MAIN remains owned by the surrounding transaction. */
uint32_t hook_c0_send(uint32_t ptr, uint32_t core, uint32_t r2, uint32_t r3)
{
    struct eo *e = eo();
    if (core || !e || e->sel != SEL_ENGINE)
        return CD_SEND(ptr, core, r2, r3);
    e->sel = SEL_STOCK;
    uint32_t fil, sent = 0;
    if (crbc_ok(ptr, *(volatile uint32_t *)C1_PTR)    /* the base contract's parts: else the pair is refused */
            && fs_state() == 1 && (fil = file_b(0)) != 0) {
        volatile uint8_t b[256];
        char path[24];
        cd_epath(path, e->c_name);
        sent = cd_stream(fil, b, path, ptr, e->c_words, e->c_crc, CRBC_ID, (e->fl & EF_TRIES) ? 1u : 0u);
    }
    if (sent == 1) {
        e->sel = SEL_C1;                      /* the core-1 send of this load composes the suppression set */
        e->c0out = C0_CANDIDATE;
        e->r_pair = R_ENGINE;
        e->r_kind = e->c_kind;
        e->r_crc = e->c_crc;
        return 0;
    }
    if (sent == 2) {
        e->c0out = C0_STOCK_RESTART;
        e->rgen++;                            /* the restart replaced the partial candidate: a new resident lifecycle */
        return 0;
    }
    e->c0out = C0_STOCK_NODATA;
    return CD_SEND(ptr, core, r2, r3);
}
#else
/* Boot-only core-0 send hook. */
uint32_t hook_c0_send(uint32_t ptr, uint32_t core, uint32_t r2, uint32_t r3)
{
    struct state *s = region();
    struct a1 *a = a1();
    if (core || !s || !a)
        return CD_SEND(ptr, core, r2, r3);
    volatile uint32_t *mk = CD_MARK, *rc = CD_REC;
    uint32_t once = a->cd_once;
    a->cd_once = 1;
    if (!itcm_ok())
        return CD_SEND(ptr, core, r2, r3);
    uint32_t m = mk[0], valid = mk[1] == ~m, bad = valid && m == CD_BAD, last = mk[2], st = mk[3], tries = 0;
    if (!once) {                              /* the boot record: this boot's first core-0 send */
        rc[1] = rc[0] == CD_RMAGIC ? rc[1] + 1 : 1;
        rc[0] = CD_RMAGIC;
        rc[2] = (valid && m == CD_TRY ? 1u : bad ? 2u : 0u) << 24;
        rc[3] = st;
        rc[4] = TICK();
        rc[5] = 0;
        rc[6] = 0;
        rc[7] = 0;
    } else
        rc[7]++;
#if CARD_DSP_MAIN
    uint32_t shape = cd_u24((const volatile uint8_t *)ptr) != CD_STOCK_W;    /* not the exact stock stream */
#else
    uint32_t shape = cd_u24((const volatile uint8_t *)ptr) < CD_BASE;
#endif
    if (once || s->mod_frame || s->ld_phase != LD_IDLE || cd_u24((const volatile uint8_t *)ptr + 3) != 0 || shape) {
        if (!once)
            cd_out(s->mod_frame || s->ld_phase != LD_IDLE ? CD_O_MODULE : CD_O_STREAM, 0, 0);
        if (valid && m == CD_TRY)
            mk[0] = 0;                        /* a later load (boot only: always flash): no card engine to judge */
        return CD_SEND(ptr, core, r2, r3);
    }
    if (valid && m == CD_TRY) {               /* the boot before this reset ran a card engine, unconfirmed */
        if ((st & CD_L_MASK) != CD_L_RUN || st >> 8) {
            mk[0] = CD_BAD;                   /* failed, never opened, or the second unconfirmed boot: flash, BAD */
            mk[1] = ~CD_BAD;
            cd_out(CD_O_JUDGED, st & CD_L_MASK, 0);
            return CD_SEND(ptr, core, r2, r3);
        }
        tries = 1;                            /* opened, unconfirmed, no failure: the same file once more */
        mk[0] = 0;                            /* until pass 2 writes TRY again */
    }
    SUSPEND_ALL();
    uint32_t free = !(s->tok & TOK_CONTENT);
    if (free)
        s->tok |= TOK_MAIN;
    RESUME_ALL();
    if (!free) {
        cd_out(CD_O_TOKEN, 0, 0);
        return CD_SEND(ptr, core, r2, r3);
    }
    uint32_t sent = 0, fil = 0, crc = 0, base = 0, words = 0;
    if (fs_state() != 1)
        cd_out(CD_O_NOCARD, 0, 0);
    else if (!(fil = file_b(0)))
        cd_out(CD_O_FILBUSY, 0, 0);
    else {
        char path[24];
        cd_path(path);
        words = cd_check(fil, (volatile uint8_t *)CD_SCRATCH, path, ptr, &crc, &base);   /* records its refusal */
        rc[5] = TICK();
    }
    if (words && bad && crc == last)
        cd_out(CD_O_BADMATCH, 0, 0);
    else if (words) {
        char path[24];
        cd_path(path);
        sent = cd_stream(fil, (volatile uint8_t *)CD_SCRATCH, path, ptr, words, crc, base, crc == last ? tries : 0);
        rc[6] = TICK();
        cd_out(sent == 1 ? CD_O_CARD : sent == 2 ? CD_O_P2RESTART : CD_O_P2SAME, 0, 0);
    }
    SUSPEND_ALL();
    s->tok &= (uint8_t)~TOK_MAIN;
    RESUME_ALL();
#if ENGINE_OPT
    if (sent == 1)
        a->eng = 1;                           /* the card engine went out complete: track 8 is the engine's (pub) */
#endif
    return sent ? 0 : CD_SEND(ptr, core, r2, r3);
}
#endif
#endif

#if CD_PART
/* far table FAR_CD_LIVE: every Main pass (q0_service), q = the queue; q 0: the queue's health verdict. See the header. */
#if CARD_DSP_MAIN
static uint32_t cd_live(uint32_t qp, uint32_t c, uint32_t d, uint32_t e)
#else
uint32_t far_cd_live(uint32_t qp, uint32_t c, uint32_t d, uint32_t e)
#endif
{
    (void)c; (void)d; (void)e;
    struct q0 *q = (struct q0 *)qp;
#if CARD_DSP_BENCH
    struct a1 *a = a1();
    if (a && a->cd_wd && (((TICK() >> 4) - a->cd_wd) & 0x7F) >= CD_WD_MS >> 4) {
        struct state *s = region();
        uint32_t t = CLK->transport;
        if (s && !(s->tok & TOK_CONTENT) && !(t && link(t, 0) == TRANSPORT_VT && (link(t, 0x10) & 0xFF))) {
            __asm volatile("cpsid i" ::: "memory");
            for (;;)                          /* the watchdog test: nothing feeds the IWDG any more */
                __asm volatile("" ::: "memory");
        }
    }
#endif
    volatile uint32_t *m = CD_MARK;
#if ENGINE_PACK
    struct eo *eop = eo();
    if (!eop || eop->r_pair != R_ENGINE)
        return 1;                             /* the stock pair: no engine to judge */
    if (!q || o1_faulted()) {                 /* the queue's health verdict or an O1 fault: the resident failed */
        eop->r_fault = 1;
        if (!itcm_ok() || m[0] != CD_TRY || m[1] != ~CD_TRY || (m[3] >> 16) != eop->rgen)
            eop->bad_crc = eop->r_crc;            /* after its TRY period: stock for this body until the next boot */
    }
    if (!itcm_ok() || (m[3] >> 16) != eop->rgen)
        return 1;                             /* a TRY of a replaced resident: neither cleared nor failed here */
#endif
    uint32_t st = m[3];
    if (!itcm_ok() || m[0] != CD_TRY || m[1] != ~CD_TRY || (st & CD_L_MASK) == CD_L_FAIL)
        return 1;                             /* not a card boot, or failed already: nothing to do */
    if (!q || o1_faulted()) {
        m[3] = (st & ~CD_L_MASK) | CD_L_FAIL;
        return 1;
    }
    if (q->ph != Q_OPEN || q->excl || q->bulk || q->reval) {
        m[4] = CD_PAUSE;                      /* closed, excluded, bulk, reconcile: paused, never failed */
        return 1;
    }
    uint32_t ep = q->c_open, t0 = TICK();
    if (m[4] == ep && (int32_t)(t0 - m[6]) < (int32_t)CD_STEP_MS)
        return 1;
    if (q->busy)
        return 1;
    uint32_t r[4];
    q0_rd(Q0_FAULT, 4, r);                    /* FAULT, CONSUMED, REJECTED, TICK: sampled inside [t0, t1] */
    uint32_t t1 = TICK();
    /* The read gives BUS_SEM back before it returns: another task may have claimed a bulk there, and Main may have
     * waited any time before t1. So the state is checked again, and the sample is judged only against bounds that
     * no such delay can stretch. */
    if (o1_faulted()) {
        m[3] = (st & ~CD_L_MASK) | CD_L_FAIL;
        return 1;
    }
    if (q->ph != Q_OPEN || q->excl || q->bulk || q->reval || q->c_open != ep) {
        m[4] = CD_PAUSE;                      /* the state changed around the read: no sample, paused */
        return 1;
    }
    if (r[0] == Q0_NONE || r[3] == Q0_NONE)
        return 1;
    if (r[0]) {
        m[3] = (st & ~CD_L_MASK) | CD_L_FAIL;
#if ENGINE_PACK
        eop->r_fault = 1;
#endif
        return 1;
    }
    if (m[4] != ep) {                         /* a new or resumed episode: the baseline */
        m[4] = ep;
        m[5] = r[3];
        m[6] = t1;                            /* the end of the read: the latest the sample can be */
        m[3] = (st & ~CD_L_MASK) | CD_L_RUN;
        return 1;
    }
    /* Elapsed between the two samples, at least t0 - (the previous read's end): a lower bound, so a scheduler or
     * semaphore delay after a sample can only shorten it, never turn into a stall or into healthy time. */
    uint32_t dt = t0 - m[6], adv = (r[3] - m[5]) & M24;
    if (adv * 4u < dt * 3u) {                 /* under 3/4 of a block count per ms: stalled */
        m[3] = (st & ~CD_L_MASK) | CD_L_FAIL;
#if ENGINE_PACK
        eop->r_fault = 1;
#endif
        return 1;
    }
    m[5] = r[3];
    m[6] = t1;
    m[7] += dt;
    if (m[7] >= CD_LIVE_MS)
        m[0] = 0;                             /* healthy: the next warm boot loads the card engine again */
    return 1;
}
#endif

#if CD_PART && CARD_DSP_WRITER
/* cmd 10 0B: F0 00 20 29 00 60 10 0B <itcm> <ITCM 0x80..0xBF: 16 words, 5x7 each> <RTOS tick 5x7> F7 (95 bytes):
 * the marker and the boot record (header), read-only. itcm 0: ITCM is off, the words read 0. */
static void cd_status(void)
{
    volatile uint8_t r[95];
    header(r, 0x10);
    r[7] = 0x0B;
    SUSPEND_ALL();
    uint32_t ok = itcm_ok();
    r[8] = (uint8_t)ok;
    for (uint32_t i = 0; i < 16; i++)
        put7(r + 9 + 5 * i, ok ? CD_MARK[i] : 0, 5);
    RESUME_ALL();
    put7(r + 89, TICK(), 5);
    r[94] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

#if CARD_DSP_BENCH
/* cmd 10 0C 'I' 'W' 'D' 'G' 'G' 'O' (caller holds the suspension; st = the writer's state): W_OK armed, W_BUSY a
 * writer job runs (nothing armed), 0 a wrong key (no reply). See the header. */
static uint32_t cd_wd(const uint8_t *m, uint32_t st)
{
    struct a1 *a = a1();
    if (m[8] != 'I' || m[9] != 'W' || m[10] != 'D' || m[11] != 'G' || m[12] != 'G' || m[13] != 'O')
        return 0;
    if (st == CW_JOB || !a)
        return W_BUSY;
    a->cd_wd = (uint8_t)(0x80u | ((TICK() >> 4) & 0x7F));
    return W_OK;
}
#endif
#endif

/* Main task (core0feed.c): q = the queue, every pass, a liveness step; q = 0, the queue's health verdict (FAULT or a
 * CONS distance over 16, q0_refresh): failed. All of it in the far blob. */
static void card_dsp_live(struct q0 *q)
{
#if CARD_DSP_MAIN
#if !FAR_PART
    cd_live((uint32_t)q, 0, 0, 0);
#else
    (void)q;
#endif
#else
    far_call(FAR_CD_LIVE, (uint32_t)q, 0, 0, 0);
#endif
}
