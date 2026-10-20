/* Track modes, milestone T1: persistence only. Compiled after devkit_abi3.c, whose state, SUSPEND_ALL/RESUME_ALL,
 * TX_SYSEX and header() it uses.
 *
 * T1 is an effective-all-Sample experiment: the stored modes are validated, copied and persisted, nothing plays
 * them. No router wrapper, no parameter copy, no DSP write. tm_effective() is Sample for every track.
 *
 * Extension block v1 (FROZEN): the 880 bytes store + 0x29110 .. + 0x2947F of the project; the four bytes after it are
 * never read or written. Little endian.
 *   +0x000 u32  magic 0x31444D54 (bytes 'T' 'M' 'D' '1')
 *   +0x004 u8   version 1
 *   +0x005 u8   flags: v1 defines no bit; nonzero = unsupported (read-only, effective Sample)
 *   +0x006 u16  length 0x370: the bytes of the extension, CRC included
 *   +0x008      8 x 16-byte track records: byte 0 stored mode (0 Sample, 1 Chord, 2 Synth, 3 Voice-of; 4..255
 *               unsupported = effective Sample), bytes 1..15 mode-specific, opaque in v1
 *   +0x088      8 x 64-byte stored patch slots (a stored encoding, not a DSP layout), opaque in v1
 *   +0x288      228 reserved bytes, opaque in v1 (created zero)
 *   +0x36C u32  CRC-32/ISO-HDLC (zlib crc32: reflected polynomial 0xEDB88320, init 0xFFFFFFFF, final xor
 *               0xFFFFFFFF) over +0x000 .. +0x36B
 * Validation (on every project apply): all 880 bytes zero = EMPTY (new project; all Sample; writable); else
 * magic, version, flags, length, CRC in this order; the first failure names the result and makes the block
 * read-only for us (never rewritten, copied through by normal saves). Only a v1 block that passes all checks is OK
 * (writable). Opaque bytes are never interpreted or normalised; a write changes only the bytes it means to change
 * (header on creation, one mode byte, the CRC) and leaves every other byte of the block as it is.
 *
 * Runtime copy: struct tm (TM_SIZE bytes at TM_AT, below the clock block; hook_boot lowers the heap limit to TM_AT).
 * Rebuilt by tm_apply() right after each project refresh, at three hook sites (load/absent slot, clear, pack
 * switch/boot). T1 only takes a generation mark and a copy, which is idempotent and order-free; code that acts on
 * modes gates on the generation. Main task.
 *
 * Edits (tm_set_mode; SysEx 0C 01) write the live project, which a save later writes to the card. So an edit is
 * refused (BUSY) unless, with the scheduler suspended, (a) no project load/save/clear is pending and (b) the Content
 * task is not inside its job loop (devkit token TOK_CONTENT, hook_content). Both hold for the whole write, which is
 * made inside the same suspension, so no save can read a half-written block. No filesystem call is made.
 *
 * SysEx (devkit cmd 0C, Main task):
 *   0C 00          status
 *   0C 01 t m      set the stored mode of track t (0..7) to m (0..3)
 *   reply F0 00 20 29 00 60 0C <sub> <status> <why> <gen 3x7> <writes 3x7> <site> <m0..m7> F7 (26 bytes)
 *   Both first attach to the current project if no refresh has been seen yet (tm_attach).
 *   status 0 refused (bad arguments, no project seen), 1 ok, 2 busy (project operation or Content job pending),
 *   3 read-only (extension present but not OK); why = TM_* below; gen = project applies seen since boot; writes =
 *   blocks written since boot; site 0 attach, 1 load/absent, 2 clear, 3 pack/boot; mN = stored mode of track N (0x7F if > 0x7E).
 */

#define TM_MAGIC_RT     0x54524D54u     /* "TMRT": runtime copy initialised by hook_boot */
#define TM_EXT          0x29110u        /* store offset of the extension */
#define TM_EXT_LEN      0x370u          /* 880 bytes */
#define TM_BLK_MAGIC    0x31444D54u     /* "TMD1" */
#define TM_VERSION      1u
#define TM_REC          0x008u
#define TM_REC_LEN      16u
#define TM_CRC_AT       0x36Cu
#define TM_MODES        4u              /* 0 Sample, 1 Chord, 2 Synth, 3 Voice-of */
#define STORE_LEN       0x29484u        /* bytes saved and loaded; the self pointer follows */
#define P_VT            0x08066F64u     /* project controller check */
#define ORIG_APPLY      ((content_fn)0x08037367u)   /* project refresh hook target */

enum { TM_OK, TM_EMPTY, TM_NOSTORE, TM_BADMAGIC, TM_BADVERSION, TM_BADFLAGS, TM_BADLENGTH, TM_BADCRC };
enum { TS_REFUSED, TS_OK, TS_BUSY, TS_READONLY };

struct tm {
    uint32_t magic;
    uint32_t P, store;                        /* seen by the last apply, checked (0 = none) */
    uint32_t gen;                             /* project applies seen since boot */
    uint32_t writes;                          /* blocks written since boot */
    uint8_t why;                              /* TM_* of the last apply (or of our last write: TM_OK) */
    uint8_t site;                             /* 0 attach, 1 load/absent, 2 clear, 3 pack/boot */
    uint8_t pad[2];
    uint8_t rec[8][TM_REC_LEN];               /* stored records as validated (zero unless why == TM_OK) */
};
_Static_assert(sizeof(struct tm) <= TM_SIZE, "track-modes runtime copy must fit its block");
#define TM ((struct tm *)TM_AT)

static struct tm *tm(void)
{
    struct tm *t = TM;
    return region() && t->magic == TM_MAGIC_RT ? t : 0;
}

static void tm_boot(void)                     /* from hook_boot, block already zeroed */
{
    TM->magic = TM_MAGIC_RT;
}

static int in_sram(uint32_t a, uint32_t n)
{
    return (a & 3) == 0 && a >= SRAM_START && a <= SRAM_END - n;
}

/* The store of a checked project controller, 0 if anything does not check out. */
static uint32_t store_of(uint32_t P)
{
    if (!in_sram(P, 0x4C) || *(volatile uint32_t *)P != P_VT)
        return 0;
    uint32_t s = *(volatile uint32_t *)(P + 0x14);
    if (!in_sram(s, STORE_LEN + 4) || *(volatile uint32_t *)(s + STORE_LEN) != s)
        return 0;
    return s;
}

/* CRC-32/ISO-HDLC (zlib crc32), bitwise: the build keeps .text only, so no table. */
static uint32_t tm_crc(const volatile uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return ~c;
}

static uint32_t all_zero(const volatile uint8_t *b, uint32_t n)
{
    uint32_t any = 0;
    for (uint32_t i = 0; i < n; i++)
        any |= b[i];
    return !any;
}

/* Project operation phase: 0 = no load/save/clear operation pending; 0xFF if the object chain does not check out
 * (treated as pending). Reads only. */
static uint32_t phase(uint32_t P)
{
    uint32_t obs = *(volatile uint32_t *)(P + 0x48);
    if (!in_sram(obs, 0x14))
        return 0xFF;
    uint32_t buf = *(volatile uint32_t *)(obs + 0xC + 4 * ((*(volatile uint32_t *)(obs + 4) >> 1) & 1));
    if (!in_sram(buf, 8))
        return 0xFF;
    return *(volatile uint8_t *)(buf + 4);
}

static uint32_t rd32(const volatile uint8_t *p)
{
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void wr32(volatile uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

static uint32_t tm_check(const volatile uint8_t *b)
{
    if (all_zero(b, TM_EXT_LEN))
        return TM_EMPTY;
    if (rd32(b) != TM_BLK_MAGIC)
        return TM_BADMAGIC;
    if (b[4] != TM_VERSION)
        return TM_BADVERSION;
    if (b[5] != 0)
        return TM_BADFLAGS;
    if ((b[6] | b[7] << 8) != TM_EXT_LEN)
        return TM_BADLENGTH;
    if (tm_crc(b, TM_CRC_AT) != rd32(b + TM_CRC_AT))
        return TM_BADCRC;
    return TM_OK;
}

/* After each project refresh: validate the extension of the project now loaded, rebuild the runtime copy. */
static void tm_apply(uint32_t P, uint32_t site)
{
    struct tm *t = tm();
    if (!t)
        return;
    uint32_t s = store_of(P), why = TM_NOSTORE;
    const volatile uint8_t *b = (const volatile uint8_t *)(s + TM_EXT);
    if (s)
        why = tm_check(b);
    SUSPEND_ALL();
    t->P = s ? P : 0;
    t->store = s;
    t->why = (uint8_t)why;
    t->site = (uint8_t)site;
    for (uint32_t k = 0; k < 8 * TM_REC_LEN; k++)
        t->rec[k / TM_REC_LEN][k % TM_REC_LEN] = why == TM_OK ? b[TM_REC + k] : 0;
#if SCALES
    sc_copy(why == TM_OK ? b : 0);            /* the scales sub-record's runtime copy (scales.c) */
#endif
    t->gen++;
    RESUME_ALL();
}

#define TM_HOOK(name, site)                                                     \
    uint32_t name(uint32_t a, uint32_t b, uint32_t c, uint32_t d)               \
    {                                                                           \
        uint32_t r = ORIG_APPLY(a, b, c, d);      /* original call first, unchanged */                                    \
        tm_apply(a, site);                                                      \
        return r;                                                               \
    }
TM_HOOK(hook_apply_load, 1)                   /* site 1: load / absent slot */
TM_HOOK(hook_apply_clear, 2)                  /* site 2: clear project */
TM_HOOK(hook_apply_pack, 3)                   /* site 3: pack switch / boot */

/* No refresh seen yet (the boot project may arrive without one): find the project through checked links and take
 * the copy as an apply would (site 0), but only while no project operation is pending and the Content task is
 * outside its job loop. Main task. Returns the runtime copy if a project is known. */
static struct tm *tm_attach(struct state *s)
{
    struct tm *t = tm();
    if (!t || t->store)
        return t;
    uint32_t a = *(volatile uint32_t *)DISP_PTR;
    if (!in_sram(a, 4) || !in_sram(a = *(volatile uint32_t *)a, 0x3C) || !in_sram(a = *(volatile uint32_t *)(a + 0x38), 4))
        return t;
    uint32_t P = *(volatile uint32_t *)a - 4;
    if (!store_of(P) || phase(P) != 0 || (s->tok & TOK_CONTENT))
        return t;
    tm_apply(P, 0);
    return t;
}

/* ---- API for later milestones (Main task) ------------------------------------------------------------------------ */

static uint32_t tm_stored(uint32_t track)     /* stored mode byte, 0 when unknown */
{
    struct tm *t = tm();
    return t && track < 8 ? t->rec[track][0] : 0;
}

__attribute__((unused)) static uint32_t tm_effective(uint32_t track)  /* T1: every track plays as Sample */
{
    (void)track;
    return 0;
}

/* Set the stored mode of a track in the store (= the project file content). TS_* result. */
static uint32_t tm_set_mode(struct state *s, uint32_t track, uint32_t mode)
{
    struct tm *t = tm_attach(s);
    if (!t || track > 7 || mode >= TM_MODES || !t->store || store_of(t->P) != t->store)
        return TS_REFUSED;
    if (t->why != TM_OK && t->why != TM_EMPTY)
        return TS_READONLY;
    volatile uint8_t *b = (volatile uint8_t *)(t->store + TM_EXT);
    uint32_t r = TS_BUSY;
    SUSPEND_ALL();
#ifdef TM_BUSY_FIRST    /* the BUSY gate first, then the live tail */
    if ((s->tok & TOK_CONTENT) || phase(t->P) != 0) {
        r = TS_BUSY;
    } else
#endif
    if (t->why == TM_EMPTY && !all_zero(b, TM_EXT_LEN)) {
        r = TS_READONLY;                      /* not what the apply saw: never overwrite it */
    } else if (!(s->tok & TOK_CONTENT) && phase(t->P) == 0) {
        if (t->why == TM_EMPTY) {             /* create in an all-zero block */
            wr32(b, TM_BLK_MAGIC);
            b[4] = TM_VERSION;
            b[5] = 0;
            b[6] = (uint8_t)TM_EXT_LEN;
            b[7] = (uint8_t)(TM_EXT_LEN >> 8);
        }
        b[TM_REC + track * TM_REC_LEN] = (uint8_t)mode;
        wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
        t->rec[track][0] = (uint8_t)mode;
        t->why = TM_OK;
        t->writes++;
        r = TS_OK;
    }
    RESUME_ALL();
    return r;
}

#ifdef TM_EDIT
/* Generic edit through the same path as tm_set_mode (settings page): attach,
 * REFUSED/READONLY checks, then, with the scheduler suspended, the BUSY gate first (Content token, request phase),
 * the live-tail check against an EMPTY apply, the header on creation, edit(b, arg) on the live block, the CRC and the
 * runtime copy of the records. edit returns TS_OK after writing, or anything else after writing nothing (then no
 * header is created and the CRC is not touched). Main task. TS_* result. */
typedef uint32_t (*tm_edit_fn)(volatile uint8_t *b, uint32_t arg);
#if SCALES     /* two callbacks (page, scales): inline every call so each callback stays a direct call (no address
                  constant, which the .text-only build cannot relocate) */
#define TM_EDIT_INLINE __attribute__((always_inline)) inline
#else
#define TM_EDIT_INLINE
#endif

static TM_EDIT_INLINE uint32_t tm_edit(struct state *s, tm_edit_fn edit, uint32_t arg)
{
    struct tm *t = tm_attach(s);
    if (!t || !t->store || store_of(t->P) != t->store)
        return TS_REFUSED;
    if (t->why != TM_OK && t->why != TM_EMPTY)
        return TS_READONLY;
    volatile uint8_t *b = (volatile uint8_t *)(t->store + TM_EXT);
    uint32_t r;
    SUSPEND_ALL();
    if ((s->tok & TOK_CONTENT) || phase(t->P) != 0) {
        r = TS_BUSY;
    } else if (t->why == TM_EMPTY && !all_zero(b, TM_EXT_LEN)) {
        r = TS_READONLY;                      /* not what the apply saw: never overwrite it */
    } else if ((r = edit(b, arg)) == TS_OK) {
        if (t->why == TM_EMPTY) {             /* create around the edit: the rest of the block is still zero */
            wr32(b, TM_BLK_MAGIC);
            b[4] = TM_VERSION;
            b[5] = 0;
            b[6] = (uint8_t)TM_EXT_LEN;
            b[7] = (uint8_t)(TM_EXT_LEN >> 8);
        }
        wr32(b + TM_CRC_AT, tm_crc(b, TM_CRC_AT));
        for (uint32_t k = 0; k < 8 * TM_REC_LEN; k++)
            t->rec[k / TM_REC_LEN][k % TM_REC_LEN] = b[TM_REC + k];
#if SCALES
        sc_copy(b);
#endif
        t->why = TM_OK;
        t->writes++;
    }
    RESUME_ALL();
    return r;
}
#endif

/* ---- SysEx 0C (debug) ------------------------------------------------------------------------------------------- */

static void tm_sysex(struct state *s, const uint8_t *m, uint32_t len)
{
    uint32_t sub = len >= 9 ? m[7] : 0xFF, status = TS_REFUSED;
    if (len == 9 && sub == 0x00)
        status = tm_attach(s) && tm()->store ? TS_OK : TS_REFUSED;
    else if (len == 11 && sub == 0x01)
        status = tm_set_mode(s, m[8], m[9]);
    else
        return;                               /* malformed: no reply */
    volatile uint8_t r[26];
    struct tm *t = tm();
    header(r, 0x0C);
    r[7] = (uint8_t)sub;
    r[8] = (uint8_t)status;
    r[9] = t ? t->why : TM_NOSTORE;
    put7(r + 10, t ? t->gen : 0, 3);
    put7(r + 13, t ? t->writes : 0, 3);
    r[16] = t ? t->site : 0;
    for (uint32_t k = 0; k < 8; k++) {
        uint32_t v = tm_stored(k);
        r[17 + k] = (uint8_t)(v > 0x7E ? 0x7F : v);
    }
    r[25] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
