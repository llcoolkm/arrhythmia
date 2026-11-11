/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* C0Z1/C1Z1 storage with a bounded mode-1 LZ256 decoder.
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
#define C1LZ_DESC       0x080101F0u
#define C1LZ_BYTES      40143u
#define C1LZ_RAW_CRC    0x26FEED89u
#define C1LZ_ENC_CRC    0xE182F018u
#ifndef DSP_FACTORY_LZ
#define DSP_FACTORY_LZ 0
#endif
#ifndef DSP_FACTORY_C1_LZ
#define DSP_FACTORY_C1_LZ 0
#endif
#define F0LZ_DESC       0x08062130u
#define F1LZ_DESC       0x08062F20u
#if DSP_FACTORY_LZ
/* Internal source pins, once in read-only .text. Descriptor fields are checked
 * against these constants, never trusted as source length/CRC parameters. */
#if defined(__arm__) || defined(__thumb__)
__attribute__((naked,used,noinline)) static const uint32_t *c01lz_pins(void)
{
    __asm__ volatile("adr r0, 1f\n\tbx lr\n\t.p2align 2\n"
        "1: .word 0x315a3043,18349,0xe352f013,24525,0x012110cc\n"
        ".word 0x315a3143,40143,0xe182f018,42567,0x26feed89\n"
        ".word 0x315a3046,1451,0x4e8c3616,2430,0xf16e1653\n"
#if DSP_FACTORY_C1_LZ
        ".word 0x315a3146,335,0x8bc29c81,1086,0x86ec63ad\n"
#endif
    );
}
#else
static const uint32_t *c01lz_pins(void)
{
    static const uint32_t pins[]={0x315a3043,18349,0xe352f013,24525,0x012110cc,
        0x315a3143,40143,0xe182f018,42567,0x26feed89,
        0x315a3046,1451,0x4e8c3616,2430,0xf16e1653,
#if DSP_FACTORY_C1_LZ
        0x315a3146,335,0x8bc29c81,1086,0x86ec63ad,
#endif
    };return pins;
}
#endif
__attribute__((noinline)) static const uint32_t *c01lz_meta(uint32_t ptr)
{
    uint32_t i;
    if (ptr==C0LZ_DESC) i=0;
    else if (ptr==C1LZ_DESC) i=1;
    else if (ptr==F0LZ_DESC) i=2;
#if DSP_FACTORY_C1_LZ
    else if (ptr==F1LZ_DESC) i=3;
#endif
    else return 0;
    return c01lz_pins()+5*i;
}
#endif
struct c0lz {
    const volatile uint8_t *p, *end;
    uint32_t count, left, dist, kind;
    uint8_t ring[256];
    uint32_t failed, busy, raw_limit, source;
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

static int c0lz_next_state(struct c0lz *s, uint8_t *out)
{
    uint32_t t, v;
    if (s->failed)
        return -1;
    if (s->count == s->raw_limit)
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
        if (s->left > s->raw_limit - s->count)
            return -1;
    }
    v = s->kind ? s->ring[(s->count - s->dist) & 255] : *s->p++;
    s->ring[s->count++ & 255] = v;
    s->left--;
    *out = v;
    return 1;
}

static int c0lz_next(uint8_t *out)
{return c0lz_next_state(C0LZ,out);}

/* Only the two pinned mode-0 descriptors are legal compressed sources. */
#if DSP_FACTORY_LZ
static uint32_t c01lz_begin(uint32_t ptr)
{
    if (!region() || C0LZ->failed || C0LZ->busy) return c0lz_fail();
    const uint32_t *pin=c01lz_meta(ptr);
    if (!pin) return c0lz_fail();
    struct c0lz *s=C0LZ;
    s->busy=1;s->source=ptr;s->p=(const volatile uint8_t *)(ptr+20u);
    s->end=s->p+pin[1];s->raw_limit=pin[3];
    s->count=s->left=s->dist=s->kind=0;
    return 1;
}
#else
static uint32_t c01lz_begin(uint32_t ptr)
{
    if (!region() || C0LZ->failed || C0LZ->busy || (ptr!=C0LZ_DESC && ptr!=C1LZ_DESC))
        return c0lz_fail();
    struct c0lz *s=C0LZ;
    s->busy=1;s->source=ptr;s->p=(const volatile uint8_t *)(ptr+20u);
    s->end=s->p+(ptr==C0LZ_DESC?C0LZ_BYTES:C1LZ_BYTES);
    s->raw_limit=ptr==C0LZ_DESC?CRBC_C0_BYTES:CRBC_C1_BYTES;
    s->count=s->left=s->dist=s->kind=0;
    return 1;
}
#endif
static uint32_t c0lz_begin(void) {return c01lz_begin(C0LZ_DESC);}

/* Check encoded and decoded integrity before any transmit. The independent raw
 * CRC authenticates this part; seed preserves the exact composition CRC order. */
#if ABI3_C1_PROBE
__attribute__((used,noinline))
#endif
static uint32_t c01lz_check(uint32_t ptr,uint32_t seed,uint32_t *crc)
{
    if (!c01lz_begin(ptr)) return 0;
    const volatile uint8_t *d=(const volatile uint8_t *)ptr;
#if DSP_FACTORY_LZ
    const uint32_t *pin=c01lz_meta(ptr);
    uint32_t bytes=pin[1],enc=pin[2],raw=pin[4],ok=1;
    for (uint32_t i=0;i<5;i++) if (cd_u32(d+4*i)!=pin[i]) ok=0;
#else
    uint32_t one=ptr==C1LZ_DESC;
    uint32_t bytes=one?C1LZ_BYTES:C0LZ_BYTES;
    uint32_t enc=one?C1LZ_ENC_CRC:C0LZ_ENC_CRC;
    uint32_t raw=one?C1LZ_RAW_CRC:C0LZ_RAW_CRC;
    uint32_t ok=cd_u32(d)==(one?0x315A3143u:0x315A3043u)
        && cd_u32(d+4)==bytes && cd_u32(d+8)==enc
        && cd_u32(d+12)==C0LZ->raw_limit && cd_u32(d+16)==raw;
#endif
    uint32_t c=~0u, composed=seed;
    if (ok) ok=~crc_add(~0u,d+20,bytes)==enc;
    if (ok) {
        uint8_t b;int r;
        while ((r=c0lz_next(&b))==1) {
            c=crc_add(c,&b,1);
            composed=crc_add(composed,&b,1);
        }
        ok=r==0 && ~c==raw;
    }
    C0LZ->busy=0;
    if (!ok) return c0lz_fail();
    *crc=composed;return 1;
}
static uint32_t c0lz_check(uint32_t ptr,uint32_t *crc)
{return c01lz_check(ptr,~0u,crc);}

static uint32_t c0lz_word(uint32_t *w)
{
    uint8_t a, b, c;
    if (c0lz_next(&a) != 1 || c0lz_next(&b) != 1 || c0lz_next(&c) != 1)
        return c0lz_fail();
    *w = (uint32_t)a << 16 | (uint32_t)b << 8 | c;
    return 1;
}

/* Full original bootstrap, including both header frames. */
static uint32_t c01lz_stock(uint32_t ptr,uint32_t core)
{
    uint32_t c,w;
    if (!c01lz_check(ptr,~0u,&c) || !c01lz_begin(ptr)) return 0;
    CD_IDLE(0,0,0,0);CD_SEL(core,0,0,0);
    for (uint32_t i=0;i<C0LZ->raw_limit/3;i++) {
        if (!c0lz_word(&w)) break;
        CD_FRAME(w>>16&255,w>>8&255,w&255,0);
    }
    uint8_t extra;uint32_t ok=!C0LZ->failed && c0lz_next(&extra)==0;
    C0LZ->busy=0;
    if (!ok) c0lz_fail();
    CD_IDLE(0,0,0,0);return CD_DESEL(core,0,0,0);
}
static uint32_t c0lz_stock(uint32_t ptr) {return c01lz_stock(ptr,0);}
