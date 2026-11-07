/* Euclid write to pattern (PWRITE, issue #47). Profile
 * arrhythmia-firmware-arm-ew only, emulation only. All of it is in the far blob; it owns no RAM.
 *
 * Opt-in. A module takes part only through a capability trailer: the last 16 bytes of its image (inside the header's
 * size and CRC): u32 'CRX1', u8 caps (bit 0 PW write, bit 1 SE silence), u8 0, u16 mailbox address (module RAM,
 * 4-byte aligned, two words), u16 request block address (ITCM after the image), u16 block length (1,328 for PW),
 * u32 ~(magic ^ word 1 ^ word 2); the mailbox is three words. An ABI 3 module has no trailer: its RAM is never read or written here.
 *
 * Handshake word (mailbox + 0), tag << 24 | seq << 16 | low: 0 idle; 'P' offered (module); 'T' taken (host);
 * 'D' done, low = result (host). The module writes the block only in idle or done, then 'P'; it writes 0 only over
 * 'P' (withdraw) or 'D'. The host writes 'T' only over 'P' and 'D' only over its own 'T' or (refused at the take)
 * over 'P'. Both sides change the word only with the scheduler suspended.
 *
 * Service (pw_service, from the far arp tick: Main task, scheduler running, once per Main pass). The take checks, in
 * one suspension, that the module runs and offers a request, that no project file job is active or pending (Content
 * token, request phase), that the track-modes copy is attached, and that nobody holds TOK_MAIN; then it writes 'T'
 * and sets TOK_MAIN, which keeps the Content task out of its job loop until the answer. The take is the
 * linearization point: a taken request is completed and answered in this call, whatever happens to the module
 * meanwhile (start and image writes run in the Main task only, so none can run before the answer). TOK_MAIN is
 * cleared with the answer; the scheduler is never held across a stock setter or notify.
 *
 * Silence (hook_seqdec / hook_seqflip): on a silenced track no new sequencer note starts and no
 * sample flip is played. The hooks suppress new triggers while preserving the offs of notes already held.
 * At each silenced decision the host keeps bit t of the busy word (mailbox + 8) = the player still holds
 * a note, so a module can wait until no sequencer note of the track is held before it plays there. Effective silence of track t: the trailer has SE and (a) the module runs and the silence word
 * (mailbox + 4, 'SE' << 16 | mask) has bit t, or (b) the drain still holds a module note on track t played on port t.
 */
#if PWRITE && FAR_PART
#define CAP_MAGIC       0x31585243u                 /* 'CRX1' */
#define CAP_PW          1u
#define CAP_SE          2u
#define PW_TOP          0x3E00u                     /* request blocks end here or lower (the top 512 B stay free) */
#define PW_LEN          1328u
#define PW_REQ_MAGIC    0x51525750u                 /* 'PWRQ' */
#define PW_TAG_P        0x50u
#define PW_TAG_T        0x54u
#define PW_TAG_D        0x44u
#define SE_MAGIC        0x5345u                     /* 'SE' */
enum { PW_OK = 1, PW_BUSY, PW_CONFLICT, PW_INVALID, PW_NOTRACK, PW_FAULT };
enum { OP_READ = 1, OP_WRITE, OP_UNDO };
#define PW_CTRL_VT      0x080672E4u                 /* UI controller */
#define PW_GRID_VT      0x080678DCu                 /* grid page */
#define PW_PAGE_VT      0x080682CCu                 /* track step page */
#define PW_DEC_ORIG     ((uint32_t (*)(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t))0x0804AEEFu)
#define PW_FLIP_ORIG    ((fn4_t)0x0804B061u)
#define PW_GET          ((uint32_t (*)(uint32_t, uint32_t))0x0803FC59u)     /* cell getter (cell, i): front element */
#define PW_SETP         ((uint32_t (*)(uint32_t, uint32_t))0x0803FD27u)     /* (cell, i): back element, marked dirty */
#define PW_ROWS         0x2E4u                      /* pattern storage base offset */
#define PW_ROW          0x5140u

struct pw_ent {                                     /* one track: 328 bytes */
    uint8_t track, pattern, flags, status;
    uint16_t old_set, new_set;                      /* settings element 0: end | start << 8 */
    uint8_t old[32][5], neu[32][5];                 /* the five request fields of each step */
};
struct pw_req {                                     /* 16 + 4 x 328 = 1,328 bytes */
    uint32_t magic;
    uint8_t op, n, seq, zero;
    uint32_t gen, zero2;
    struct pw_ent e[4];
};
_Static_assert(sizeof(struct pw_req) == PW_LEN, "request block layout");

/* Checked pattern access fields. */
#define PG_A            0x0Cu                       /* step fields A */
#define PG_B            0x10u                       /* step fields B */
#define PG_S            0x14u                       /* pattern settings */
#define PG_C            0x18u                       /* edit selection */
#define PG_P            0x1Cu                       /* playback position */
#define PG_H            0x20u                       /* held-pad guard */
static const uint8_t pw_base[5] = {0, 16, 0, 8, 16};    /* access bases for the five request fields */
static const uint8_t pw_hi[5] = {127, 63, 127, 7, 255}; /* largest value of each field */

static inline uint32_t iw(uint32_t a) { return *(volatile uint32_t *)a; }
static inline void pw_dmb(void) { __asm__ volatile("dmb" ::: "memory"); }

/* The running image's capability trailer with capability `need`, or 0. ITCM must be on before the first ITCM read.
 * Then the header (magic, size aligned and bounded, so the image end is inside the code area), the trailer (magic,
 * zero bits, check word),
 * then the capability and the mailbox address. Reads the image only, never module RAM. */
__attribute__((noinline)) static uint32_t pw_cap(uint32_t need)
{
    if (!itcm_ok())                                 /* before the first ITCM read */
        return 0;
    uint32_t size = iw(MOD_BASE + 4u);
    if (!hdr_len(iw(MOD_BASE)) || (size & 3u) || size < HDR2 + 2u + 16u || size > MOD_MAX)
        return 0;
    uint32_t t = MOD_BASE + size - 16u, w1 = iw(t + 4u), w2 = iw(t + 8u), mb = w1 >> 16;
    if (iw(t) != CAP_MAGIC || iw(t + 12u) != ~(CAP_MAGIC ^ w1 ^ w2) || (w1 & 0xFFFCu) || !(w1 & need))
        return 0;
    return mb >= MOD_RAM && mb <= MOD_BASE - 12u && !(mb & 3u) ? t : 0;
}

/* Validate pattern access for track t; return 0 if any required object is unavailable. */
static const uint32_t pw_chain[3][2] = {{0, PW_CTRL_VT}, {0x3C, PW_GRID_VT}, {0x74, PW_PAGE_VT}};
__attribute__((noinline)) static uint32_t pw_page(uint32_t t)
{
    uint32_t x = rw(BTN_CELL + 4u);
    if (rw(BTN_CELL) != 0x080295D1u || !in_sram(x, 4))
        return 0;
    for (uint32_t i = 0; i < 3; i++)                /* validate each required link */
        if (!in_sram(x = rw(x + pw_chain[i][0] + (i == 2 ? 4u * t : 0)), 0x98) || rw(x) != pw_chain[i][1])
            return 0;
    for (uint32_t i = 0; i < 6; i++)                /* validate all required cells */
        if (!in_sram(rw(x + PG_A + 4u * i), 0x18))
            return 0;
    return x;
}

static inline uint32_t pw_cell(uint32_t pg, uint32_t f) { return rw(pg + ((0x11u >> f) & 1u ? PG_B : PG_A)); }

/* Published (front) byte of field f of step s. */
__attribute__((noinline)) static uint32_t pw_get(uint32_t pg, uint32_t f, uint32_t s)
{
    return *((volatile uint8_t *)PW_GET(pw_cell(pg, f), pw_base[f] + (s >> 2)) + (s & 3u));
}

__attribute__((noinline)) static uint32_t pw_el0(uint32_t pg, uint32_t off) { return PW_GET(rw(pg + off), 0); }
static inline uint32_t pw_set0(uint32_t pg) { return *(volatile uint16_t *)pw_el0(pg, PG_S); }
static inline uint32_t pw_pattern(uint32_t pg) { return *(volatile uint8_t *)pw_el0(pg, PG_C); }

/* Refuse writes while an edit is pending or a step pad is held. */
__attribute__((noinline)) static uint32_t pw_clean(uint32_t pg)
{
    for (uint32_t o = PG_A; o <= PG_P; o += 4)
        if (rw(rw(pg + o) + 8u))
            return 0;
    return !*(volatile uint8_t *)pw_el0(pg, PG_H);
}

static inline uint32_t pw_set_ok(uint32_t v) { return (v & 0xFFu) <= 31u && (v >> 8) <= 31u; }

/* b != 0: copy the published bytes into a and b (READ); b == 0: OR of the differences from a (compare). */
__attribute__((noinline)) static uint32_t pw_walk(uint32_t pg, volatile uint8_t (*a)[5], volatile uint8_t (*b)[5])
{
    uint32_t d = 0;
    for (uint32_t s = 0; s < 32; s++)
        for (uint32_t f = 0; f < 5; f++) {
            uint32_t v = pw_get(pg, f, s);
            if (b)
                a[s][f] = b[s][f] = (uint8_t)v;
            else
                d |= a[s][f] ^ v;
        }
    return d;
}

/* Nonzero if the bytes or the settings word to write are not allowed: a field above its range, a start or end step
 * above 31, or byte 6 changing outside bits 1..5 (against the published byte). */
__attribute__((noinline)) static uint32_t pw_bad(uint32_t pg, const volatile uint8_t (*p)[5], uint32_t set)
{
    if (!pw_set_ok(set))
        return 1;
    for (uint32_t s = 0; s < 32; s++) {
        for (uint32_t f = 0; f < 4; f++)
            if (p[s][f] > pw_hi[f])
                return 1;
        if ((p[s][4] ^ pw_get(pg, 4, s)) & ~0x3Eu)
            return 1;
    }
    return 0;
}

/* Stage only changed request fields from the snapshot, then notify the cell.
 * Leave every other byte unchanged. */
static void pw_put(uint32_t pg, const uint8_t (*sn)[5], uint32_t b)
{
    uint32_t cell = rw(pg + (b ? PG_B : PG_A)), n = 0;
    for (uint32_t f = 0; f < 5; f++) {
        if (((0x11u >> f) & 1u) != b)
            continue;
        for (uint32_t s = 0; s < 32; s++)
            if (pw_get(pg, f, s) != sn[s][f]) {
                *((volatile uint8_t *)PW_SETP(cell, pw_base[f] + (s >> 2)) + (s & 3u)) = sn[s][f];
                n++;
            }
    }
    if (n)
        CH_NOTIFY(cell);
}

/* Commit one checked entry from an immutable snapshot, validated before the first setter, so a failure leaves
 * nothing staged. After each notify the edit pattern must still be the checked one (observers must not retarget);
 * otherwise the cells not yet staged stay untouched and the entry reports FAULT. */
__attribute__((noinline)) static uint32_t pw_commit(uint32_t pg, uint32_t pattern, volatile struct pw_ent *e, uint32_t undo)
{
    uint8_t sn[32][5];
    const volatile uint8_t (*src)[5] = undo ? e->old : e->neu;
    uint32_t nset = undo ? e->old_set : e->new_set;
    for (uint32_t s = 0; s < 32; s++)
        for (uint32_t f = 0; f < 5; f++)
            sn[s][f] = src[s][f];
    if (pw_bad(pg, sn, nset) || pw_pattern(pg) != pattern)
        return PW_FAULT;
    for (uint32_t b = 0; b < 2; b++) {
        pw_put(pg, sn, b);
        if (pw_pattern(pg) != pattern)
            return PW_FAULT;
    }
    if (pw_set0(pg) != nset) {
        *(volatile uint16_t *)PW_SETP(rw(pg + PG_S), 0) = (uint16_t)nset;
        CH_NOTIFY(rw(pg + PG_S));
        if (pw_pattern(pg) != pattern)
            return PW_FAULT;
    }
    return PW_OK;
}

/* One taken request: extent, format, targets, pending state, token and compare, values; then READ or the commit.
 * Every entry passes every check before the first setter. Returns the result code. */
__attribute__((noinline)) static uint32_t pw_run(struct tm *m, uint32_t t, uint32_t seq)
{
    uint32_t w2 = iw(t + 8u), blk = w2 & 0xFFFFu, end = MOD_BASE + iw(MOD_BASE + 4u);
    if ((w2 >> 16) != PW_LEN || (blk & 3u) || blk < end || blk + PW_LEN > PW_TOP)
        return PW_INVALID;                          /* the block is never touched */
    volatile struct pw_req *q = (volatile struct pw_req *)blk;
    uint32_t op = q->op, n = q->n, pg[4], seen = 0, i;
    if (q->magic != PW_REQ_MAGIC || op - 1u > 2u || n - 1u > 3u || q->seq != seq || q->zero || q->zero2)
        return PW_INVALID;
    for (i = 0; i < n; i++) {
        uint32_t tr = q->e[i].track;
        if (tr > 7u || (seen >> tr & 1u) || q->e[i].flags)
            return PW_INVALID;
        seen |= 1u << tr;
        if (!(pg[i] = pw_page(tr)))
            return PW_NOTRACK;
    }
    if (store_of(m->P) != m->store)
        return PW_BUSY;
    for (i = 0; i < n; i++)
        if (!pw_clean(pg[i]))
            return PW_BUSY;
    uint32_t undo = op == OP_UNDO;
    if (op != OP_READ && q->gen != m->gen)
        return PW_CONFLICT;
    for (i = 0; i < n; i++) {
        volatile struct pw_ent *e = &q->e[i];
        volatile uint8_t (*want)[5] = undo ? e->neu : e->old, (*put)[5] = undo ? e->old : e->neu;
        if (op == OP_READ) {
            e->pattern = (uint8_t)pw_pattern(pg[i]);
            e->old_set = e->new_set = (uint16_t)pw_set0(pg[i]);
            pw_walk(pg[i], want, put);
            e->status = PW_OK;
        } else if (pw_pattern(pg[i]) != e->pattern || pw_set0(pg[i]) != (undo ? e->new_set : e->old_set)
                   || pw_walk(pg[i], want, 0)) {
            return PW_CONFLICT;
        } else if (pw_bad(pg[i], put, undo ? e->old_set : e->new_set)) {
            return PW_INVALID;
        }
    }
    if (op == OP_READ) {
        q->gen = m->gen;
        return PW_OK;
    }
    for (i = 0; i < n; i++) {
        volatile struct pw_ent *e = &q->e[i];
        uint32_t r = pw_commit(pg[i], e->pattern, e, undo);
        e->status = (uint8_t)r;
        if (r != PW_OK)
            return r;
    }
    return PW_OK;
}

/* The devkit state, with the track-modes copy attached to a store (A1 up), or 0. */
__attribute__((noinline)) static struct state *pw_ctx(struct tm **m)
{
#if ENGINE_OPT
    struct tm *t = TM;                              /* tracks 1..4: struct a1 (module host) and the track-modes
                                                       copy, not a track-8 engine (ch_tm() also requires pub);
                                                       a1() != 0 implies region() != 0 (REGION) */
    *m = a1() && t->magic == TM_MAGIC_RT && t->store ? t : 0;
    return *m ? (struct state *)REGION : 0;
#else
    return (*m = ch_tm()) ? region() : 0;
#endif
}

/* Far arp tick, once per Main pass: take at most one offered request and answer it. */
void pw_service(void)
{
    struct tm *m;
    struct state *s = pw_ctx(&m);                   /* s != 0 implies m != 0 */
    uint32_t t = s ? pw_cap(CAP_PW) : 0;
    if (!t)
        return;
    volatile uint32_t *mb = (volatile uint32_t *)(iw(t + 4u) >> 16);
    uint32_t w, r = 0;
    SUSPEND_ALL();
    w = *mb;
    if (s->mode != 3 || !s->mod_frame || (w >> 24) != PW_TAG_P) {
        w = 0;
    } else if ((s->tok & (TOK_MAIN | TOK_CONTENT)) || phase(m->P) != 0) {
        r = PW_BUSY;                                /* refused at the take: nothing read, nothing written */
        *mb = PW_TAG_D << 24 | (w & 0xFF0000u) | r;
    } else {
        s->tok |= TOK_MAIN;                         /* the Content task stays out of its job loop until the answer */
        *mb = PW_TAG_T << 24 | (w & 0xFF0000u);
    }
    RESUME_ALL();
    if (!w || r)
        return;
    pw_dmb();
    r = pw_run(m, t, w >> 16 & 0xFFu);
    pw_dmb();
    SUSPEND_ALL();
    *mb = PW_TAG_D << 24 | (w & 0xFF0000u) | r;
    s->tok &= (uint8_t)~TOK_MAIN;
    RESUME_ALL();
}

/* Effective silence of the player's track (see the header): mailbox address << 8 | track, or 0. */
__attribute__((noinline)) static uint32_t pw_silenced(uint32_t pl)
{
    struct tm *m;
    struct state *s = pw_ctx(&m);
    if (!s)
        return 0;
    uint32_t row = rw(pl + 0x50u) - (m->store + PW_ROWS), k = row / PW_ROW, t;
    if (k > 7u || row != k * PW_ROW || !(t = pw_cap(CAP_SE)))
        return 0;
    uint32_t mb = iw(t + 4u) >> 16, w = iw(mb + 4u), r = mb << 8 | k;
    if ((s->sport >> k & 1u) && s->sounding[k])
        return r;                                   /* (b) a module note on this track still held by the drain */
    return s->mode == 3 && s->mod_frame && w >> 16 == SE_MAGIC && (w >> k & 1u) ? r : 0;     /* (a) */
}

/* Silence hook for one microstep's on/off decision (player, pattern, step, microstep, first). */
uint32_t hook_seqdec(uint32_t pl, uint32_t pat, uint32_t st, uint32_t ms, uint32_t first)
{
    uint32_t r = pw_silenced(pl), m = 1u << (r & 7u);
    if (!r)
        return PW_DEC_ORIG(pl, pat, st, ms, first);
    volatile uint32_t *busy = (volatile uint32_t *)((r >> 8) + 8u);
    *busy = *(volatile uint8_t *)(pl + 0x8Au) != 0xFFu ? *busy | m : *busy & ~m;   /* a held note: busy */
    if (ms < 6u)
        *(volatile uint8_t *)(pl + 0x14Cu) &= (uint8_t)~(1u << ms);
    if (first)
        for (uint32_t i = 0; i < 0x14u; i++)
            *(volatile uint8_t *)(pl + 0x138u + i) = 0;
    return PW_DEC_ORIG(pl, pat, st, 6u, first);     /* preserve gate expiry without starting a new note */
}

/* Suppress sample flips while the track is silenced. */
uint32_t hook_seqflip(uint32_t pl, uint32_t pat, uint32_t st, uint32_t d)
{
    return pw_silenced(pl) ? d : PW_FLIP_ORIG(pl, pat, st, d);
}
#endif
