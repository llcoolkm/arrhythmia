/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* Engine selection belongs to the pack (ENGINE_PACK), in the main blob.
 * Included after carddsp.c and o1wait.c. ep_access admits a transaction with TOK_MAIN,
 * refusing nested or busy requests. Lookup checks the pack's ENGINE.CFG/CFO, then the
 * card DEFAULT.CFG/CFO when no pack pointer exists. A pointer names one engine or NONE;
 * malformed, unreadable or refused selections fall back to the flash pair.
 *
 * Healthy, already-published residency with the same kind and CRC can be reused.
 * Otherwise clear publication, validate the complete file, arm the send hook and load.
 * Candidate readiness is bounded; failure permits one flash fallback. A failed fallback
 * or O1 fault terminates the transaction. After loading, enter SAMPLES and release TOK_MAIN.
 * ep_slot counts traversed/ok/failed slots. ep_done publishes only after every slot was
 * traversed, the target pack still matches, and residency and health still agree.
 * Core-1 composition is armed only by a complete core-0 candidate from the same load.
 */

#define EP_REC_PTR      0x20006E24u     /* -> the stock current-pack record */
#define EP_MODE_PTR     0x20000310u     /* mode-load context pointer */
#define EP_MODE_LOAD    ((fn4)0x08027883u)  /* the mode switch (mode object, &requested mode): mode 0 loads the pair */
#define EP_READY_MS     500u            /* 4D: the candidate's queue header, transaction-local */
#define EP_SLOTS        128u
#define EP_CFG_MAX      32u
#define CBR2            ((volatile uint32_t *)0x000000A0u)  /* the boot record, per attempt */
#define CBR2_MAGIC      0x32524243u     /* "CBR2" */

enum { EV_NONE, EV_OK, EV_SAME, EV_NOPTR, EV_STOCKPTR, EV_INVALID, EV_NOFILE, EV_HEADER, EV_PASS1, EV_BAD,
       EV_SESSIONBAD, EV_BUSY, EV_NESTED, EV_O1, EV_REFUSED, EV_CHANGED, EV_NOTREADY, EV_TERMINAL, EV_LDWAIT,
       EV_IOERR };
enum { PS_NONE, PS_PACK, PS_PACK_OLD, PS_DEFAULT, PS_DEFAULT_OLD };

static volatile uint8_t *ep_rec(void)
{
    uint32_t r = *(volatile uint32_t *)EP_REC_PTR;
    return in_sram(r, 0x40) ? (volatile uint8_t *)r : 0;
}

static uint32_t ep_cur_pack(void)
{
    volatile uint8_t *r = ep_rec();
    return r ? r[0] : 0xFFu;
}

#define EP_ABSENT       (-2)            /* no such file or path: the next source may be consulted */
#define EP_IOERR        (-3)            /* the file object busy, or an open or read error: stock, nothing else read */

/* Up to n - 1 bytes of the file at path into b, NUL-terminated; the byte count, EP_ABSENT only for FR_NO_FILE /
 * FR_NO_PATH, EP_IOERR for any other open error, a read error or file object B busy, or EP_CFG_MAX + 1 if it is
 * longer than n - 1. */
static int32_t ep_slurp(const char *path, volatile uint8_t *b, uint32_t n)
{
    uint32_t fil = file_b(0), got = 0;
    if (!fil)
        return EP_IOERR;
    uint32_t r = F_OPEN(fil, path, FA_READ);
    if (r)
        return r == FR_NO_FILE || r == FR_NO_PATH ? EP_ABSENT : EP_IOERR;
    r = F_READ(fil, (uint32_t)b, n, &got);
    F_CLOSE(fil);
    if (r)
        return EP_IOERR;
    if (got >= n)
        return (int32_t)EP_CFG_MAX + 1;
    b[got] = 0;
    return (int32_t)got;
}

/* Parse a pointer line in b (len bytes): 1 + basename into nm (8 bytes, NUL-padded) for "NAME.DSE", 0 for "NONE",
 * -1 for anything else. */
static int32_t ep_parse(volatile uint8_t *b, uint32_t len, volatile char *nm)
{
    if (len && b[len - 1] == '\n')
        len--;
    if (len && b[len - 1] == '\r')
        len--;
    if (len == 4 && (b[0] | 32) == 'n' && (b[1] | 32) == 'o' && (b[2] | 32) == 'n' && (b[3] | 32) == 'e')
        return 0;
    uint32_t i = 0;
    for (; i < len && b[i] != '.'; i++) {
        uint32_t c = b[i];
        if (c >= 'A' && c <= 'Z')
            c += 32;
        if (i >= 8 || !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return -1;
        nm[i] = (char)c;
    }
    if (!i || i + 4 != len || (b[i + 1] | 32) != 'd' || (b[i + 2] | 32) != 's' || (b[i + 3] | 32) != 'e')
        return -1;
    for (uint32_t k = i; k < 8; k++)
        nm[k] = 0;
    if (i == 7 && nm[0] == 'd' && nm[1] == 'e' && nm[2] == 'f' && nm[3] == 'a' && nm[4] == 'u' && nm[5] == 'l'
            && nm[6] == 't')
        return -1;                            /* DEFAULT is the card default's reserved basename */
    return 1;
}

/* d = "<root>/<folder>/ENGINE.CF" + x (root, folder from the record), or "1:/ENGINES/DEFAULT.CF" + x (rec 0). */
static void ep_cfgpath(char *d, volatile uint8_t *rec, char x)
{
    uint32_t n = 0;
    if (rec) {
        const volatile char *root = (const volatile char *)(*(volatile uint32_t *)(rec + 0x3C) + 5);
        for (uint32_t i = 0; i < 16 && root[i]; i++)
            d[n++] = root[i];
        d[n++] = '/';
        for (uint32_t i = 1; i <= 32 && rec[i]; i++)
            d[n++] = (char)rec[i];
        d[n++] = '/';
        d[n++] = 'e'; d[n++] = 'n'; d[n++] = 'g'; d[n++] = 'i'; d[n++] = 'n'; d[n++] = 'e';
    } else {
        d[n++] = '1'; d[n++] = ':'; d[n++] = '/'; d[n++] = 'e'; d[n++] = 'n'; d[n++] = 'g'; d[n++] = 'i';
        d[n++] = 'n'; d[n++] = 'e'; d[n++] = 's'; d[n++] = '/';
        d[n++] = 'd'; d[n++] = 'e'; d[n++] = 'f'; d[n++] = 'a'; d[n++] = 'u'; d[n++] = 'l'; d[n++] = 't';
    }
    d[n++] = '.'; d[n++] = 'c'; d[n++] = 'f'; d[n++] = x | 32; d[n] = 0;
}

/* One pointer source (the pack's or the default): 1 / 0 / -1 as ep_parse, EP_ABSENT if neither .CFG nor .CFO
 * exists, EP_IOERR if either could not be read. The .CFO is consulted only when the .CFG is absent (a cut between the
 * two renames leaves the committed old copy); an unreadable .CFG is never replaced by a stale .CFO. *old = 1 if the
 * .CFO was read. */
static int32_t ep_pointer(volatile uint8_t *rec, volatile char *nm, uint32_t *old)
{
    volatile uint8_t b[EP_CFG_MAX + 1];
    char path[64];
    *old = 0;
    ep_cfgpath(path, rec, 'G');
    int32_t n = ep_slurp(path, b, sizeof b);
    if (n == EP_ABSENT) {
        ep_cfgpath(path, rec, 'O');
        n = ep_slurp(path, b, sizeof b);
        *old = 1;
    }
    if (n < 0)
        return n;
    return n > (int32_t)EP_CFG_MAX ? -1 : ep_parse(b, (uint32_t)n, nm);
}

/* Step 2: the selection for the current pack into cand (c_kind 0 = stock), the verdict and pointer state. */
__attribute__((noinline)) static void ep_lookup(struct eo *e)
{
    volatile uint8_t *rec = ep_rec();
    uint32_t old = 0;
    int32_t r = EP_ABSENT;
    e->c_kind = 0;
    e->c_crc = 0;
    e->c_words = 0;
    e->ptr = PS_NONE;
    if (fs_state() != 1 || !rec) {
        e->verdict = EV_NOPTR;
        return;
    }
    const volatile char *root = (const volatile char *)(*(volatile uint32_t *)(rec + 0x3C) + 5);
    if (in_sram((uint32_t)root & ~3u, 4) && root[0] == '1') {      /* a card pack: its own pointer first */
        r = ep_pointer(rec, e->c_name, &old);
        if (r != EP_ABSENT)
            e->ptr = old ? PS_PACK_OLD : PS_PACK;
    }
    if (r == EP_ABSENT) {                     /* no pointer in the pack (or the NOR pack): the card default */
        r = ep_pointer(0, e->c_name, &old);
        if (r != EP_ABSENT)
            e->ptr = old ? PS_DEFAULT_OLD : PS_DEFAULT;
    }
    if (r <= 0) {                             /* absent, NONE, invalid or unreadable: stock, nothing else consulted */
        e->verdict = r == EP_ABSENT ? EV_NOPTR : r == EP_IOERR ? EV_IOERR : r == 0 ? EV_STOCKPTR : EV_INVALID;
        return;
    }
    volatile uint8_t b[64];
    char path[24];
    cd_epath(path, e->c_name);
    uint32_t fil = file_b(0);
    if (!fil || !cd_open(fil, b, path)) {
        e->verdict = EV_NOFILE;
        return;
    }
    F_CLOSE(fil);
    uint32_t words = cd_hdr(b, CRBC_ID);       /* the base contract: its parts are rechecked before pass 2 */
    if (!words) {
        e->verdict = EV_HEADER;
        return;
    }
    e->c_kind = (uint8_t)cd_u32(b + 20);
    e->c_crc = cd_u32(b + 8);
    e->c_words = (uint16_t)words;
    e->verdict = EV_OK;
}

/* #36's policy, once per boot, at the first transaction: a TRY found from the boot before this reset is BAD at
 * once if that engine failed, never opened, or was already retried; opened and unconfirmed: one retry (EF_TRIES for
 * the marker's CRC). */
static void ep_judge(struct eo *e)
{
    volatile uint32_t *m = CD_MARK;
    e->judged = 1;
    if (!itcm_ok() || m[1] != ~m[0] || m[0] != CD_TRY)
        return;
    uint32_t st = m[3];
    if ((st & CD_L_MASK) != CD_L_RUN || (st >> 8 & 0xFFu)) {
        m[0] = CD_BAD;
        m[1] = ~CD_BAD;
        return;
    }
    e->fl |= EF_TRIES;
    m[0] = 0;
}

/* 4B: pass 1 of the candidate (256-byte Main-stack buffer, popped before the load). 1 admitted. */
__attribute__((noinline)) static uint32_t ep_pass1(struct eo *e)
{
    volatile uint32_t *m = CD_MARK;
    if (e->c_crc == e->bad_crc && e->bad_crc) {
        e->verdict = EV_SESSIONBAD;
        return 0;
    }
    if (itcm_ok() && m[1] == ~m[0] && m[0] == CD_BAD && m[2] == e->c_crc) {
        e->verdict = EV_BAD;
        return 0;
    }
    volatile uint8_t b[256];
    char path[24];
    uint32_t fil = file_b(0), crc = 0, base = 0;
    cd_epath(path, e->c_name);
    uint32_t w = fil ? cd_check(fil, b, path, *(volatile uint32_t *)CD_C0_PTR, &crc, &base) : 0;
    if (!w || w != e->c_words || crc != e->c_crc) {    /* (cd_check also binds the kind to c_kind) */
        e->verdict = EV_PASS1;
        return 0;
    }
    if (!((e->fl & EF_TRIES) && m[2] == crc))
        e->fl &= (uint8_t)~EF_TRIES;
    return 1;
}

/* struct wd's 16-bit fields saturate (enginepack.h). */
static uint16_t wd_sat(uint32_t d)
{
    return d > 0xFFFFu ? 0xFFFFu : (uint16_t)d;
}

/* 4C / 4E: one mode-0 DSP load through the stock mode switch, armed for the candidate or not. */
static void ep_load(struct eo *e, uint32_t engine)
{
    volatile uint8_t req[4] = {0, 0, 0, 0};
    if (o1_faulted()) {                       /* sticky O1 (seen in 4D's poll, or after a trial's loader wait): no new
                                                 DSP traffic, the transaction ends terminal */
        e->c0out = C0_TERMINAL;
        return;
    }
    e->c0out = C0_NONE;
    e->fl |= EF_OWN;
    e->sel = engine ? SEL_ENGINE : SEL_STOCK;
    e->loads++;
    uint32_t t = TV(), c;
    WD->c1 = 0;
    EP_MODE_LOAD(*(volatile uint32_t *)EP_MODE_PTR, (uint32_t)req, 0, 0);
    uint32_t now = TV();
    c = WD->c1 ? WD->c1at : now;              /* no core-1 send (an unwound load): all of it counts as core 0 */
    WD->dsp0 = wd_sat(WD->dsp0 + (c - t));
    WD->dsp1 = wd_sat(WD->dsp1 + (now - c));
    e->sel = SEL_STOCK;
    e->fl &= (uint8_t)~(EF_OWN | EF_ABORT);
    if (!engine || e->c0out == C0_NONE)
        e->c0out = engine ? C0_STOCK_NODATA : C0_STOCK;
    if (o1_faulted())
        e->c0out = C0_TERMINAL;
}

/* 4D: the candidate's queue header within EP_READY_MS (the queue reads under its own rules), watchdog fed. */
static uint32_t ep_ready(void)
{
    uint32_t t0 = TICK();
    do {
        O1_KICK();
        if (q0_header_ok(Q0))
            return 1;
    } while (TICK() - t0 < EP_READY_MS && !o1_faulted());
    return 0;
}

static void ep_record(struct eo *e)
{
    volatile uint32_t *r = CBR2;
    if (!itcm_ok())
        return;
    if (r[0] != CBR2_MAGIC) {
        r[0] = CBR2_MAGIC;
        r[1] = 0;
    }
    r[1]++;
    r[2] = e->c_pack | (uint32_t)e->c_kind << 8 | (uint32_t)e->ptr << 16 | (uint32_t)e->verdict << 24;
    r[3] = e->c_crc;
    r[4] = e->rgen | (uint32_t)e->att << 16;
    r[5] = e->loads | (uint32_t)e->c0out << 8 | (uint32_t)e->tf << 16 | (uint32_t)e->busy << 24;
    r[6] = e->n_trav | (uint32_t)e->n_ok << 8 | (uint32_t)e->n_fail << 16
           | (uint32_t)(uint8_t)(e->n_trav - e->n_ok - e->n_fail) << 24;       /* empty */
    r[7] = TICK();
}

/* Our load-wait hooks bound ready-pin and handshake waits by O1_MS during EF_OWN.
 * A candidate timeout records C0_WAIT, marks its resident failed and unwinds so the
 * transaction can try flash once. A flash-attempt timeout records C0_TERMINAL and
 * sets sticky O1 failure. Loads outside our transaction retain the original waits. */
typedef uint32_t (*xy_read_fn)(uint32_t core, uint32_t a16);
#define EP_XY_READ      ((xy_read_fn)0x08031269u)   /* X/Y read (core, address) */
#define EP_HS_STOCK     ((fn4)0x080312ADu)          /* the stock handshake wait (unbounded) */
#define EP_LD_CONT      0x08031197u                 /* ready-wait continuation */

static uint32_t ep_own(void)
{
    struct eo *e = eo();
    return e && (e->fl & EF_OWN);
}

/* A wait of an own load timed out (or the DSP is already quarantined): decide the outcome; 1 = unwind. */
static uint32_t ep_ldwait(uint32_t site, uint32_t backstop)
{
    struct eo *e = eo();
    volatile struct o1 *o = o1();
    if (e->c0out == C0_CANDIDATE && !(o && o->fault)) {
        e->c0out = C0_WAIT;                   /* the trial failed: the transaction's 4E stock load follows */
        e->r_fault = 1;
    } else {
        o1_fail(o, site, backstop);           /* the stock attempt failed: terminal */
        e->c0out = C0_TERMINAL;
    }
    e->fl |= EF_ABORT;
    return 1;
}

/* Ready-pin wait hook: 1 requests unwind. */
static uint32_t __attribute__((used, noinline)) ep_ldready_c(uint32_t bus)
{
    uint32_t p0 = *(volatile uint32_t *)(*(volatile uint32_t *)(bus + 0x10) + 8);
    uint32_t p1 = *(volatile uint32_t *)(*(volatile uint32_t *)(bus + 0x14) + 8);
    if (!ep_own()) {
        while (O1_PIN(p0) || O1_PIN(p1))
            ;                                 /* stock: unbounded */
        return 0;
    }
    if (o1_faulted())
        return ep_ldwait(11, 0);
    uint32_t t0 = O1_TICK(), seen = t0, same = 0;
    while (O1_PIN(p0) || O1_PIN(p1)) {
        uint32_t now = O1_TICK();
        if (now - t0 >= O1_MS)
            return ep_ldwait(11, 0);
        if (now != seen) {
            seen = now;
            same = 0;
        } else if (++same >= O1_FROZEN_RDY) {
            return ep_ldwait(11, 1);
        }
    }
    return 0;
}

/* Resume or unwind after our ready-pin wait. */
__attribute__((naked)) void hook_ld_ready(void)
{
    __asm__ volatile(
        "mov r0, r5\n\t"
        "bl ep_ldready_c\n\t"
        "cbnz r0, 1f\n\t"
        "movw r0, #0x1197\n\t"
        "movt r0, #0x0803\n\t"
        "bx r0\n"                            /* ready: resume the continuation */
        "1: pop {r3, r4, r5, r6, r7, pc}");  /* unwind: back to hook_dsp_load0, which skips the wrapper's rest */
}

/* Handshake wait hook: wait for the requested zero/nonzero word state; 1 requests unwind. */
static uint32_t __attribute__((used, noinline)) ep_ldhs_c(uint32_t core, uint32_t a16, uint32_t mode)
{
    if (!ep_own()) {
        EP_HS_STOCK(core, a16, mode, 0);      /* stock: unbounded */
        return 0;
    }
    if (o1_faulted())
        return ep_ldwait(12, 0);
    uint32_t t0 = O1_TICK(), seen = t0, same = 0;
    for (;;) {
        uint32_t v = EP_XY_READ(core, a16);
        if (mode ? v != 0 : v == 0)
            return 0;
        uint32_t now = O1_TICK();
        if (now - t0 >= O1_MS)
            return ep_ldwait(12, 0);
        if (now != seen) {
            seen = now;
            same = 0;
        } else if (++same >= O1_FROZEN_MB) {
            return ep_ldwait(12, 1);
        }
    }
}

/* Handshake adapter: preserve the normal return or unwind on our abort result. */
__attribute__((naked)) uint32_t hook_ld_hs(uint32_t core, uint32_t a16, uint32_t mode)
{
    __asm__ volatile(
        "push {r4, lr}\n\t"
        "bl ep_ldhs_c\n\t"
        "cbnz r0, 1f\n\t"
        "pop {r4, pc}\n"
        "1: pop {r4, lr}\n\t"
        "pop {r2, r3, r4, r5, r6, lr}\n\t"
        "pop {r4, pc}");
}

/* During a transaction, reject factory-mode SysEx class F0 00 20 29 01 63 70
 * before forwarding and increment busy. Idle requests pass through EP_SX_FACTORY.
 * The DSP-load backstop remains active. */
#define EP_SX_FACTORY   ((fn4)0x0802CAB1u)

uint32_t hook_sx_factory(uint32_t port, uint32_t msg, uint32_t c, uint32_t d)
{
    struct eo *e = eo();
    if (e && e->phase != EP_IDLE) {          /* message descriptor: data and length */
        uint32_t m = *(volatile uint32_t *)msg, len = *(volatile uint32_t *)(msg + 4);
        const volatile uint8_t *b = (const volatile uint8_t *)m;
        if (len > 8 && b[0] == 0xF0 && b[1] == 0x00 && b[2] == 0x20 && b[3] == 0x29 && b[4] == 0x01
                && b[5] == 0x63 && b[6] == 0x70) {
            e->busy++;
            return 0;
        }
    }
    return EP_SX_FACTORY(port, msg, c, d);
}

_Static_assert(CW_BASE_ID == CRBC_ID, "cardwrite.c 10 0E admits the base contract's engine files only");

/* Watchdog/load timing: hook_feed refreshes IWDG and records the RTOS tick with
 * interrupts masked, preserving PRIMASK. The wrapper preserves registers and flags.
 * Durations/counters saturate at 0xFFFF. Tick intervals may undercount time while
 * interrupts are masked; this is not NMI or fault exclusion. */
#define IWDG_KR         0x40003000u
#define IRQ_SAVE(pm)    __asm__ volatile("mrs %0, primask\n\tcpsid i" : "=r"(pm) :: "memory")
#define IRQ_RESTORE(pm) __asm__ volatile("msr primask, %0" :: "r"(pm) : "memory")

static void __attribute__((used, noinline)) ep_feed_c(void)
{
    uint32_t pm;
    IRQ_SAVE(pm);
    *(volatile uint32_t *)IWDG_KR = 0xAAAAu;
    if (*(volatile uint32_t *)HEAP_CTL == HEAP_FLOOR) {   /* hook_boot has reserved and zeroed the block */
        volatile struct wd *w = WD;
        uint32_t now = TV(), d = now - w->last;
        w->last = now;
        if (w->open) {
            if (w->feeds != 0xFFFFu)
                w->feeds++;
            if (d > w->gap)
                w->gap = wd_sat(d);
        }
    }
    IRQ_RESTORE(pm);
}

__attribute__((naked)) void hook_feed(void)
{
    __asm__ volatile(
        "push {r0, r1, r2, r3, r12, lr}\n\t"
        "mrs r0, apsr\n\t"
        "push {r0, r1}\n\t"
        "bl ep_feed_c\n\t"
        "pop {r0, r1}\n\t"
        "msr apsr_nzcvqg, r0\n\t"
        "pop {r0, r1, r2, r3, r12, lr}\n\t"
        "bx lr");                            /* preserve the caller's return address */
}

/* start 1: a transaction admitted (the age, the gap seeded with it); 0: its end (the open stretch counted). */
static void __attribute__((used, noinline)) wd_mark(uint32_t start)   /* noinline: the emulator's probe */
{
    uint32_t pm;
    IRQ_SAVE(pm);
    volatile struct wd *w = WD;
    uint32_t now = TV();
    uint16_t d = wd_sat(now - w->last);
    if (start) {
        w->t0 = now;
        w->age = w->gap = d;
        w->dsp0 = w->dsp1 = w->feeds = 0;
        w->open = 1;
        if (w->n != 0xFFFFu)
            w->n++;
    } else if (w->open) {
        w->t1 = now;
        if (d > w->gap)
            w->gap = d;
        if (w->gap > w->wmax)
            w->wmax = w->gap;
        w->open = 0;
    }
    IRQ_RESTORE(pm);
}

static void ep_release(struct state *s)
{
    SUSPEND_ALL();
    s->tok &= (uint8_t)~TOK_MAIN;
    RESUME_ALL();
}

/* hook_pack_access (o1wait.c), Main task: the transaction's access stage. Returns the loader's verdict (1 refused). */
static uint32_t ep_access(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    struct eo *e = eo();
    struct state *s = region();
    struct a1 *a8p = a1();
    if (!e || !s || !a8p)
        return STOCK_PACK_ACCESS(a, b, c, d);
    if (e->phase != EP_IDLE) {                /* a nested pack load: refused, the outer transaction untouched */
        e->busy++;
        return 1;
    }
    uint32_t target = a & 0xFFu;
    if (target == 0xFEu)
        target = ep_cur_pack();
    SUSPEND_ALL();
    uint32_t free = !(s->tok & (TOK_CONTENT | TOK_MAIN));
    if (free)
        s->tok |= TOK_MAIN;
    RESUME_ALL();
    if (!free) {                              /* BUSY: refused before the stock check, nothing changed (D3) */
        e->busy++;
        e->verdict = EV_BUSY;
        return 1;
    }
    wd_mark(1);
    e->phase = EP_SELECT;
    e->att++;
    e->c_pack = (uint8_t)target;
    e->tf = 0;
    e->loads = 0;
    e->fl &= (uint8_t)~(EF_PUSHED | EF_REFRESHED | EF_SLOTERR);
    e->c0out = C0_NONE;
    e->n_trav = e->n_ok = e->n_fail = 0;
    if (!e->judged)
        ep_judge(e);
    uint32_t r = STOCK_PACK_ACCESS(a, b, c, d);
    if (r == 1) {                             /* the stock check refused: nothing of ours changed */
        e->verdict = EV_REFUSED;
        e->phase = EP_IDLE;
        ep_release(s);
        wd_mark(0);
        return 1;
    }
    if (ep_cur_pack() != target) {            /* not the target after the check: the changed path to stock */
        e->c_kind = 0;
        e->verdict = EV_CHANGED;
    } else
        ep_lookup(e);
    struct q0 *q = Q0;
    uint32_t healthy = e->r_pair == R_ENGINE && !e->r_fault && q->ph == Q_OPEN && !q->excl && !o1_faulted()
                       && a8p->eng == e->r_kind;   /* published: a resident whose publish was withheld takes the full
                                                     path (one more load), never a reuse that leaves pub 0 */
    uint32_t keep = e->r_pair == R_STOCK      /* no selection: the stock pair, or (ENGINE_FLASH regression variant
                                                 only) the flash engine, resident: nothing to load */
                    || (ENGINE_FLASH && e->r_pair == R_ENGINE && !e->r_crc);
    if (e->c_kind ? (healthy && e->c_kind == e->r_kind && e->c_crc == e->r_crc) : keep) {
        if (e->c_kind)
            e->verdict = EV_SAME;             /* same engine, resident and healthy: today's path */
        e->phase = EP_IDLE;
        ep_release(s);
        wd_mark(0);
        return 0;
    }
    e->fl = (uint8_t)((e->fl & ~EF_WASENG) | (a8p->eng ? EF_WASENG : 0));
    a8p->eng = 0;                             /* 4A */
    if (e->c_kind && !ep_pass1(e))            /* 4B: refused -> stock */
        e->c_kind = 0;
    if (e->c_kind || e->r_pair != R_STOCK) {  /* a load is needed: a candidate, or stock replacing an engine */
        ep_load(e, e->c_kind);                /* 4C */
        if (e->c_kind && e->c0out != C0_CANDIDATE && e->c0out != C0_TERMINAL)
            e->tf = 1;                        /* STOCK_NODATA / STOCK_RESTART / WAIT: the trial failed */
        uint32_t w = e->c0out == C0_WAIT;
        if (w || (e->c0out == C0_CANDIDATE && !ep_ready())) {    /* a loader wait, or 4D */
            e->tf = 1;
            e->verdict = w ? EV_LDWAIT : EV_NOTREADY;
            ep_load(e, 0);                    /* 4E: exactly one stock load */
        }
        if (e->c0out == C0_TERMINAL) {        /* a load ended in an O1 fault: no PCM */
            e->verdict = EV_TERMINAL;
            e->phase = EP_IDLE;
            ep_release(s);
            wd_mark(0);
            ep_record(e);
            o1_refuse();
            return 1;
        }
    }
    e->phase = EP_SAMPLES;                    /* 4F */
    ep_release(s);
    return 0;
}

/* In SAMPLES, count each slot completion: EF_SLOTERR or O1 failure means failed;
 * otherwise a nonzero result means uploaded and zero means empty. Clear EF_SLOTERR
 * after counting. Nested transaction admission is refused; callbacks outside SAMPLES
 * do not change these counters. Resident liveness completions use rgen separately. */
static void ep_slot(uint32_t r)
{
    struct eo *e = eo();
    if (!e || e->phase != EP_SAMPLES)
        return;
    e->n_trav++;
    if (e->fl & EF_SLOTERR || o1_faulted())
        e->n_fail++;
    else if (r)
        e->n_ok++;
    e->fl &= (uint8_t)~EF_SLOTERR;
}

/* Mark read/allocation failure only for a Main-task slot in SAMPLES. The read
 * hooks pass results through; only our guarded allocation sites report failures.
 * Short or absent headers count as empty; incomplete data chunks fail the slot. */
static void ep_slot_err(uint32_t bad)
{
    struct eo *e = eo();
    if (e && e->phase == EP_SAMPLES && bad && !q0_foreign())
        e->fl |= EF_SLOTERR;
}

/* After a transaction load, re-push global DSP settings and invalidate the cached
 * tempo so it is sent again. Refresh an already-applied project. Queue OPEN resets
 * our FM mirrors, so the next note supplies its patch. When publication changes,
 * rebuild track 8's keyboard and let sc_sync reapply its scale on Main passes. */
#define EP_REFRESH      ((fn4)0x08037367u)          /* the stock project refresh (controller, 0) */
#define EP_SETTINGS     ((void (*)(void))0x08030E2Du)   /* the global DSP settings push (group 0x21), no arguments */
#define EP_SEQ_CELL     0x20006A1Cu                 /* sequencer context cell */
#define EP_TEMPO_SENT   0x90u                       /* last-sent tempo cache offset */

static void ep_repush(struct eo *e)
{
    EP_SETTINGS();                            /* restore global DSP settings after the load */
    e->fl |= EF_PUSHED;
    uint32_t seq = link(EP_SEQ_CELL, 0);
    if (in_sram(seq, 0xA0))
        *(volatile uint8_t *)(seq + EP_TEMPO_SENT) = 0;   /* the clock re-sends whatever tempo is current */
    struct tm *t = tm();
    uint32_t P = t ? t->P : 0;
    if (!P || !t->store || link(P, 0) != P_VT || link(P, 0x14) != t->store)
        return;                               /* no project applied yet: the stock project load follows */
    EP_REFRESH(P, 0, 0, 0);
    e->fl |= EF_REFRESHED;
}

static void ep_keyboard(uint32_t pub)
{
    uint32_t kb = sc_kb(7);
    if (!kb)
        return;
    if (pub)
        ad_chrom_kb(kb);
    else
        KB_REBUILD(kb);
}

/* hook_pack_done: the re-push, the publish, the keyboard, then IDLE. */
static void ep_done(void)
{
    struct eo *e = eo();
    struct a1 *a = a1();
    if (!e || e->phase != EP_SAMPLES)
        return;
    if (e->loads && !o1_faulted())
        ep_repush(e);
    uint32_t ok = e->n_trav == EP_SLOTS && ep_cur_pack() == e->c_pack && !o1_faulted();
    if (e->c_kind)
        ok &= e->r_pair == R_ENGINE && e->r_kind == e->c_kind && e->r_crc == e->c_crc && !e->r_fault;
    if (a) {
        a->eng = ok && e->c_kind && e->r_pair == R_ENGINE ? e->r_kind : 0;
        if (!a->eng != !(e->fl & EF_WASENG))  /* pub before 4A against pub now */
            ep_keyboard(a->eng);
    }
    e->phase = EP_IDLE;
    wd_mark(0);
    ep_record(e);
}

/* cmd 10 0B (ENGINE_PACK): the engine status, read-only. It opens no session, starts no job, writes nothing but its
 * own reply buffer, and touches no card or DSP. Every field, the tick included, is read inside one RTOS scheduler
 * suspension: no other task runs in between, but interrupts do (it is not IRQ atomicity). It is the ARM's own
 * record of the engine state, not a reading of the DSP's live health. Reply (layout 3, 223 bytes):
 *   F0 00 20 29 00 60 10 0B 'E' <ver 3> <flags> <struct eo: 12 words, 5x7 each> <ITCM 0x80..0xBF: 16 words, 5x7>
 *   <pub> <token> <writer phase> <queue phase> <queue busy> <queue excl> <RTOS tick 5x7> <struct wd: 8 words, 5x7> <struct pk: 4 words, 5x7> F7
 * Layout 2 (203 bytes) omits struct pk; layout 1 (163 bytes) also omits struct wd. The host decodes all three. The tick and struct wd
 * are copied together with interrupts masked (one snapshot: a feed may come from an interrupt); struct wd's words
 * read 0 without struct eo. The other fields are consistent against tasks (the suspension), not against interrupts.
 * flags: 1 ENGINE_PACK, 2 ENGINE_FLASH, 4 ITCM on (else its words read 0), 8 struct eo present, 16 a1 present.
 * pub = a1's published engine kind; token = the devkit token byte (TOK_MAIN 1, TOK_CONTENT 2); the writer phase is
 * the devkit ld_phase (LD_JOB 5: a card job runs). A closed queue (phase 0) with pub 0 is the stock state. The #36
 * card-DSP layout of 10 0B (marker words only, no 'E') is the CARD_DSP_WRITER profiles'; the host tells them apart
 * by byte 8 and the length. */
/* Remote pack selection: a SysEx request only reserves a record. Main rechecks the admission guards, calls the
 * stock lower selection controller once, and waits for stock project completion. No custom file write path. */
#if REMOTE_PACK_LOAD
#define PK_SELECT       ((fn4)0x08039C71u)  /* (controller, pack by value, 0, 0): stock lower pack selection */
#define PK_TIMEOUT_MS   30000u

static uint32_t pk_controller(void)
{
    if (*(volatile uint32_t *)0x20006EA8u != 0x080358F1u)
        return 0;
    uint32_t c = *(volatile uint32_t *)0x20006EACu;
    if (!in_sram(c, 0x60) || *(volatile uint32_t *)c != 0x08068F50u)
        return 0;
    c = *(volatile uint32_t *)(c + 0x5C);
    return in_sram(c, 0x20) && *(volatile uint32_t *)c == 0x08068414u ? c : 0;
}

static uint32_t pk_project_phase(void)
{
    struct tm *t = tm();
    uint32_t p = t ? t->P : 0;
    if (!p) {                               /* read-only version of tm_attach's checked boot lookup */
        uint32_t a = *(volatile uint32_t *)DISP_PTR;
        if (!in_sram(a, 4) || !in_sram(a = *(volatile uint32_t *)a, 0x3C)
                || !in_sram(a = *(volatile uint32_t *)(a + 0x38), 4))
            return 0xFF;
        p = *(volatile uint32_t *)a - 4;
    }
    return store_of(p) ? phase(p) : 0xFF;
}

static uint32_t pk_selected(uint32_t c)
{
    uint32_t o = c ? *(volatile uint32_t *)(c + 0x1C) : 0;
    if (!in_sram(o, 0x14))
        return 0xFF;
    uint32_t p = *(volatile uint32_t *)(o + 0xC + 4 * (*(volatile uint32_t *)(o + 4) & 1u));
    return in_sram(p, 4) ? *(volatile uint8_t *)p : 0xFF;
}

/* Read-only transport lookup for admission; validate each interface before use. */
static uint32_t pk_transport(void)
{
    uint32_t tr = CLK->transport;
    if (!tr) {
        uint32_t p = link(0x20006EA4u, 0);
        if (link(p, 0) != 0x080672E4u)
            return 0;
        p = link(p, 0x60);
        if (link(p, 0) != 0x08067CACu)
            return 0;
        p = link(p, 0xC);
        if (link(p, 0) != 0x08069030u)
            return 0;
        p = link(p, 4);
        if (link(p, 0) != 0x08066B80u)
            return 0;
        tr = link(p, 8);
    }
    return in_sram(tr, 0x14) && link(tr, 0) == TRANSPORT_VT ? tr : 0;
}

static uint32_t pk_guard(const struct state *s)
{
    struct eo *e = eo();
    if (!e || !pk_controller() || fs_state() != 1)
        return PA_NOTREADY;
    if (o1_faulted() || e->r_fault)
        return PA_FAULT;
    if (s->ld_phase == LD_JOB)
        return PA_CARD;
    if (s->mod_frame || s->mode == 3 || s->mode == MODE_PICK)
        return PA_MODULE;
    if (!file_b(0) || s->tok || (s->ld_phase != LD_IDLE && s->ld_phase != LD_DONE) || e->phase != EP_IDLE
            || Q0->busy || Q0->excl)
        return PA_BUSY;
    uint32_t tr = pk_transport();
    if (!tr)
        return PA_NOTREADY;
    if (*(volatile uint8_t *)(tr + 0x10))
        return PA_PLAYING;
    uint32_t ph = pk_project_phase();
    return ph == 0 ? PA_ACCEPT : ph == 0xFF ? PA_NOTREADY : PA_BUSY;
}

static void ep_remote_cmd(struct state *s, const uint8_t *m, uint32_t len)
{
    uint8_t r[19];
    uint32_t snap[4], pm, answer;
    SUSPEND_ALL();
    IRQ_SAVE(pm);
    if (len != 10 || m[8] > 0x7F)
        answer = PA_MALFORMED;
    else if (!m[8] || m[8] > 31)
        answer = PA_RANGE;
    else if ((uint32_t)PK->state - PK_QUEUED < 2u)
        answer = PA_BUSY;
    else if ((answer = pk_guard(s)) == PA_ACCEPT) {
        PK->seq++;
        if (!PK->seq)
            PK->seq++;
        PK->started = TV();
        PK->finished = 0;
        PK->target = m[8];
        uint32_t loaded = ep_cur_pack();
        PK->loaded = loaded <= 31 ? loaded : 0x7F;
        PK->result = PR_NONE;
        PK->state = PK_QUEUED;
    }
    for (uint32_t i = 0; i < 4; i++)
        snap[i] = ((volatile uint32_t *)PK)[i];
    IRQ_RESTORE(pm);
    RESUME_ALL();
    header(r, 0x10);
    r[7] = 0x10;
    r[8] = (uint8_t)answer;
    put7(r + 9, snap[0], 5);
    for (uint32_t i = 0; i < 4; i++)
        r[14 + i] = (uint8_t)(snap[3] >> (8 * i)) & 0x7F;
    r[18] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

static void pk_finish(uint32_t result)
{
    uint32_t loaded = ep_cur_pack();
    PK->loaded = loaded <= 31 ? loaded : 0x7F;
    PK->result = (uint8_t)result;
    PK->finished = TV();
    PK->state = result == PR_OK ? PK_DONE : PK_FAILED;
}

static void ep_remote_main(struct state *s)
{
    if (PK->state == PK_QUEUED) {
        uint32_t answer = pk_guard(s);
        if (answer != PA_ACCEPT) {
            pk_finish(answer == PA_PLAYING ? PR_PLAYING : answer == PA_MODULE ? PR_MODULE
                      : answer == PA_CARD ? PR_CARD : answer == PA_FAULT ? PR_FAULT
                      : answer == PA_NOTREADY ? PR_NOTREADY : PR_BUSY);
            return;
        }
        PK->state = PK_RUNNING;
        /* No suspension/token is held over stock calls: its loader requires Content task progress. */
        uint32_t ret = PK_SELECT(pk_controller(), PK->target, 0, 0);
        if (o1_faulted() || !eo() || eo()->r_fault) {
            pk_finish(PR_FAULT);
            return;
        }
        if (ret) {
            pk_finish(PR_REFUSED);
            return;
        }
        PK->result = PR_OK;                  /* call returned; a reentrant Main tail cannot complete it before this */
    }
    if (PK->state != PK_RUNNING || PK->result != PR_OK)
        return;
    struct eo *e = eo();
    if (!e || o1_faulted() || e->r_fault)
        pk_finish(PR_FAULT);
    else if (ep_cur_pack() != PK->target || pk_selected(pk_controller()) != PK->target)
        pk_finish(PR_CHANGED);
    else if (e->phase == EP_IDLE && pk_project_phase() == 0)
        pk_finish(PR_OK);
    else if ((uint32_t)(TV() - PK->started) >= PK_TIMEOUT_MS)
        pk_finish(PR_NOTREADY);              /* no cancellation, rollback, second load or retry */
}

 #endif /* REMOTE_PACK_LOAD */

static void ep_status(const struct state *s)
{
    uint8_t r[223];
    uint32_t w[41], pm;
    SUSPEND_ALL();
    struct eo *e = eo();
    struct a1 *a = a1();
    uint32_t ok = itcm_ok();
    for (uint32_t i = 0; i < 12; i++)
        w[i] = e ? ((const volatile uint32_t *)e)[i] : 0;
    for (uint32_t i = 0; i < 16; i++)
        w[12 + i] = ok ? CD_MARK[i] : 0;
    uint32_t f = 1u | (ENGINE_FLASH ? 2u : 0u) | (ok ? 4u : 0u) | (e ? 8u : 0u) | (a ? 16u : 0u);
    uint32_t b = (a ? a->eng : 0) | (uint32_t)s->tok << 8 | (uint32_t)s->ld_phase << 16 | (uint32_t)Q0->ph << 24;
    uint32_t q = Q0->busy | (uint32_t)Q0->excl << 8;
    IRQ_SAVE(pm);                             /* the tick and struct wd in one snapshot: no interrupt (SysTick, a feed)
                                                 between them, so the reply's tick is never older than its last feed */
    w[28] = TV();
    for (uint32_t i = 0; i < 8; i++)
        w[29 + i] = e ? ((const volatile uint32_t *)WD_AT)[i] : 0;
    for (uint32_t i = 0; i < 4; i++)
        w[37 + i] = ((volatile uint32_t *)PK)[i];
    IRQ_RESTORE(pm);
    RESUME_ALL();
    header(r, 0x10);
    r[7] = 0x0B;
    r[8] = 'E';
    r[9] = 3;
    r[10] = (uint8_t)f;
    for (uint32_t i = 0; i < 41; i++)
        put7(r + 11 + 5 * i + (i < 28 ? 0 : 6), w[i], 5);
    for (uint32_t i = 0; i < 6; i++)
        r[151 + i] = (uint8_t)(((i < 4 ? b >> 8 * i : q >> 8 * (i - 4))) & 0x7F);
    r[222] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

#if CARD_INVENTORY
#include "enginelist.c"
#endif
