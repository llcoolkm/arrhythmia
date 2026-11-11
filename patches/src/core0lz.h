#if DSP_C1_LZ
#include "core01lz.h"
#else
/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* C0Z1 storage with a bounded mode-1 LZ256 decoder.
 * Descriptor: five LE u32: magic, compressed length/CRC, raw length/CRC.
 * CRBC continues to bind the decoded original bootstrap, including its header.
 * The boot-zeroed block is used synchronously by one validation or send. No DMA
 * points here. busy detects reentry; failed is sticky until hook_boot/reboot.
 * History needs no clear: a distance can refer only to bytes already produced.
 */
#define C0LZ_DESC       0x0807B838u
#define C0LZ_DATA       (C0LZ_DESC + 20u)
#define C0LZ_BYTES      18349u
#define C0LZ_RAW_CRC    0x012110CCu
#define C0LZ_ENC_CRC    0xE352F013u
struct c0lz {
    const volatile uint8_t *p, *end;
    uint32_t count, left, dist, kind;
    uint8_t ring[256];
    uint32_t failed, busy;
};
_Static_assert(sizeof(struct c0lz) <= C0LZ_SIZE, "owned decoder reservation");
#define C0LZ ((struct c0lz *)C0LZ_AT)

static uint32_t c0lz_failed(void)
{
    return region() && C0LZ->failed;
}

/* Storage failure is O1 site 13, not a timeout. Leave the DSP quarantined and
 * force the existing loader-unwind path; never pass this descriptor to CD_SEND. */
static uint32_t c0lz_fail(void)
{
    if (region()) {
        C0LZ->failed = 1;
        volatile struct o1 *o = o1();
        o->fault = 1;
        o->last_site = 13;
        o->sites |= 1u << 13;
        struct eo *e = eo();
        if (e) {
            e->sel = SEL_STOCK;
            e->c0out = C0_TERMINAL;
            e->fl |= EF_ABORT;
        }
    }
    return 0;
}

static void c0lz_init(void)
{
    struct c0lz *s = C0LZ;
    s->p = (const volatile uint8_t *)C0LZ_DATA;
    s->end = s->p + C0LZ_BYTES;
    s->count = s->left = s->dist = s->kind = 0;
}

static int c0lz_next(uint8_t *out)
{
    struct c0lz *s = C0LZ;
    uint32_t t, v;
    if (s->failed)
        return -1;
    if (s->count == CRBC_C0_BYTES)
        return !s->left && s->p == s->end ? 0 : -1;
    if (!s->left) {
        if (s->p == s->end)
            return -1;
        t = *s->p++;
        s->kind = t >= 128;
        if (!s->kind) {
            s->left = t + 1;
            if ((uint32_t)(s->end - s->p) < s->left)
                return -1;
        } else {
            s->left = t - 125;
            if (s->end - s->p < 2)
                return -1;
            s->dist = s->p[0] | (uint32_t)s->p[1] << 8;
            s->p += 2;
            if (!s->dist || s->dist > 256 || s->dist > s->count)
                return -1;
        }
        if (s->left > CRBC_C0_BYTES - s->count)
            return -1;
    }
    v = s->kind ? s->ring[(s->count - s->dist) & 255] : *s->p++;
    s->ring[s->count++ & 255] = v;
    s->left--;
    *out = v;
    return 1;
}

static uint32_t c0lz_begin(void)
{
    if (!region() || C0LZ->failed || C0LZ->busy)
        return c0lz_fail();
    C0LZ->busy = 1;
    c0lz_init();
    return 1;
}

/* Both CRCs and exact structural exhaustion are checked before any send byte.
 * Return the uncomplemented decoded CRC for the unchanged CRBC concatenation. */
static uint32_t c0lz_check(uint32_t ptr, uint32_t *crc)
{
    if (!c0lz_begin())
        return 0;
    const volatile uint8_t *d = (const volatile uint8_t *)C0LZ_DESC;
    uint32_t ok = ptr == C0LZ_DESC && cd_u32(d) == 0x315A3043u
        && cd_u32(d + 4) == C0LZ_BYTES && cd_u32(d + 8) == C0LZ_ENC_CRC
        && cd_u32(d + 12) == CRBC_C0_BYTES && cd_u32(d + 16) == C0LZ_RAW_CRC;
    uint32_t c = ~0u;
    if (ok)
        ok = ~crc_add(~0u, (const volatile uint8_t *)C0LZ_DATA, C0LZ_BYTES) == C0LZ_ENC_CRC;
    if (ok) {
        uint8_t b;
        int r;
        while ((r = c0lz_next(&b)) == 1)
            c = crc_add(c, &b, 1);
        ok = r == 0 && ~c == C0LZ_RAW_CRC;
    }
    C0LZ->busy = 0;
    if (!ok)
        return c0lz_fail();
    *crc = c;
    return 1;
}

static uint32_t c0lz_word(uint32_t *w)
{
    uint8_t a, b, c;
    if (c0lz_next(&a) != 1 || c0lz_next(&b) != 1 || c0lz_next(&c) != 1)
        return c0lz_fail();
    *w = (uint32_t)a << 16 | (uint32_t)b << 8 | c;
    return 1;
}

/* Full original bootstrap: two header frames and CRBC_C0_W payload frames. */
static uint32_t c0lz_stock(uint32_t ptr)
{
    uint32_t c, w;
    if (!c0lz_check(ptr, &c) || !c0lz_begin())
        return 0;
    CD_IDLE(0, 0, 0, 0);
    CD_SEL(0, 0, 0, 0);
    for (uint32_t i = 0; i < CRBC_C0_BYTES / 3; i++) {
        if (!c0lz_word(&w))
            break;
        CD_FRAME(w >> 16 & 255, w >> 8 & 255, w & 255, 0);
    }
    C0LZ->busy = 0;
    CD_IDLE(0, 0, 0, 0);
    return CD_DESEL(0, 0, 0, 0);
}

#endif
