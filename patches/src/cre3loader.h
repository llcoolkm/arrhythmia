/* SPDX-License-Identifier: MIT
 * Card-file callback adapter used by the ABI3 firmware binding.
 * No allocation/ITCM/global mutable state; caller owns 228-byte state on ARM.
 * Callbacks return exactly 1 for success; read reports the actual byte count.
 * Composition parts are decoded C0, integration set 2, stock C1, companion edits,
 * in that order, including stream headers. Every required byte is hashed here.
 * Prefix/emit consume buffers synchronously. abort must reset any partial DSP
 * transfer. No callback confers platform qualification or publication authority.
 */
#ifndef CRE3LOADER_H
#define CRE3LOADER_H
#include "cre3state.h"
#if !defined(__arm__) && !defined(__thumb__) && !defined(CRBC3_CONSTANTS_ONLY)
#define CRBC3_CONSTANTS_ONLY
#define CRE3L_UNDEF_CONSTANTS_ONLY
#endif
#include "crbc3.h"
#ifdef CRE3L_UNDEF_CONSTANTS_ONLY
#undef CRE3L_UNDEF_CONSTANTS_ONLY
#undef CRBC3_CONSTANTS_ONLY
#endif
#define CRE3L_IDENTITY 19u
#define CRE3L_COMPOSITION 20u
#define CRE3L_TRANSMIT 21u
#define CRE3L_NO_CANDIDATE 22u
struct cre3l_io {
    void *fs, *composition, *tx;
    uint32_t (*size)(void *, uint32_t *);
    uint32_t (*seek)(void *, uint32_t);
    uint32_t (*read)(void *, uint8_t *, uint32_t, uint32_t *);
    uint32_t (*part)(void *, uint32_t, uint32_t, uint8_t *, uint32_t, uint32_t *);
    uint32_t (*prefix)(void *, uint32_t);
    uint32_t (*emit)(void *, const uint8_t *, uint32_t);
    void (*abort)(void *);
};
/* The former padding byte retains a precise refusal without growing SRAM. */
static uint32_t cre3l_result(struct cre3l_state *s,uint32_t check)
{ s->last_check=(uint8_t)check;return check; }
/* Initialisation is explicit; no synthetic qualified production default. */
static void cre3l_init(struct cre3l_state *s)
{
    uint8_t *b=(uint8_t *)s;
    for (uint32_t i=0;i<sizeof(*s);i++) b[i]=0;
}
static struct cre3_platform cre3l_platform(void)
{
    struct cre3_platform p;
    volatile struct cre3_platform *v=&p; /* scalar stores: no .rodata copy */
    v->id=CRBC3_PLATFORM_ID;v->offer=0;v->windows=0;v->shim_crc=CRBC3_SHIM_CRC;
    v->qualified=CRBC3_QUALIFIED;v->composition_ok=0;
    return p;
}
static uint32_t cre3l_read(void *ctx,uint32_t at,uint8_t *b,uint32_t n)
{
    const struct cre3l_io *io=(const struct cre3l_io *)ctx;
    uint32_t got=0;
    return io->seek && io->read && io->seek(io->fs,at)==1
        && io->read(io->fs,b,n,&got)==1 && got==n;
}
static uint32_t cre3l_eof(const struct cre3l_io *io,uint32_t at)
{
    uint8_t b;uint32_t got=0;
    return io->seek(io->fs,at)==1 && io->read(io->fs,&b,1,&got)==1 && !got;
}
#if ABI3_FAR_MORE && !FAR_PART
static uint32_t cre3l_composition(const struct cre3l_io *io);
#else
static uint32_t cre3l_composition(const struct cre3l_io *io)
{
    uint8_t b[63];uint32_t crc=~0u;
    if (!io->part) return 0;
    for (uint32_t part=0;part<4;part++) {
        uint32_t length=part==0?CRBC3_C0_BYTES:part==1?CRBC3_SET_BYTES:
                        part==2?CRBC3_C1_BYTES:CRBC3_EDIT_BYTES;
        for (uint32_t at=0;at<length;) {
            uint32_t n=length-at<sizeof(b)?length-at:sizeof(b),got=0;
            if (io->part(io->composition,part,at,b,n,&got)!=1 || got!=n) return 0;
            if (!at && (part==0 || part==2)) {
                uint32_t words=part==0?CRBC3_C0_W:CRBC3_C1_W;
                if (((uint32_t)b[0]<<16 | (uint32_t)b[1]<<8 | b[2])!=words
                    || b[3] || b[4] || b[5]) return 0;
            }
            crc=cre3_crc(crc,b,n);at+=n;
        }
    }
    return ~crc==CRBC3_CRC;
}
#endif
static struct cre3l_key cre3l_key(const struct cre3_facts *f)
{
    struct cre3l_key k={cre3_u32(f->h+16),f->h[24],cre3_u32(f->h+12),cre3_u32(f->h+60)};
    return k;
}
/* Read-only pass 1, also used as full pass-2 preflight. The exact composition
 * is independently recomputed, never accepted as a caller boolean. */
#if ABI3_FAR_CHECK && !FAR_PART
static uint32_t cre3l_check(const struct cre3l_io *io,const struct cre3_platform *platform,
                           struct cre3l_state *s);
#else
static uint32_t cre3l_check(const struct cre3l_io *io,const struct cre3_platform *platform,
                           struct cre3l_state *s)
{
    uint32_t size,e;struct cre3_platform p=*platform;
    s->candidate_valid=0;s->transmitted=0;
    if (!io->size || !io->seek || !io->read || io->size(io->fs,&size)!=1) return cre3l_result(s,18);
    if (!p.qualified) return cre3l_result(s,4);
    p.composition_ok=cre3l_composition(io);
    if (!p.composition_ok) return cre3l_result(s,CRE3L_COMPOSITION);
    e=cre3_file(cre3l_read,(void *)io,size,&p,&s->facts);
    if (e) return cre3l_result(s,e);
    if (!cre3l_eof(io,size)) return cre3l_result(s,18);
    s->candidate=cre3l_key(&s->facts);s->candidate_valid=1;
    return cre3l_result(s,0);
}
#endif
static uint32_t cre3l_reuse(const struct cre3l_state *s,uint32_t resident_healthy,uint32_t published)
{
    return resident_healthy && published && s->candidate_valid && s->resident_valid
        && cre3l_equal(&s->candidate,&s->resident);
}
/* Full revalidation before prefix costs one additional body pass relative to
 * old cd_stream. Then CRC checks the actual emitted bytes again. Only the final
 * <=63 bytes are held pending CRC + shim CRC + exact EOF; errors after prefix
 * always abort. Admission/preflight never replaces a healthy resident. */
#if ABI3_FAR_STREAM && !FAR_PART
static uint32_t cre3l_stream(const struct cre3l_io *io,const struct cre3_platform *platform,
                            struct cre3l_state *s);
#else
static uint32_t cre3l_stream(const struct cre3l_io *io,const struct cre3_platform *platform,
                            struct cre3l_state *s)
{
    uint8_t b[63];uint32_t e,crc=~0u,shim=~0u;
    if (!s->candidate_valid) return cre3l_result(s,CRE3L_NO_CANDIDATE);
    if (!io->prefix || !io->emit || !io->abort) return cre3l_result(s,CRE3L_TRANSMIT);
    struct cre3l_key expected=s->candidate;
    e=cre3l_check(io,platform,s);
    if (e) return e;
    if (!cre3l_equal(&expected,&s->candidate)) {s->candidate_valid=0;return cre3l_result(s,CRE3L_IDENTITY);}
    uint32_t H=s->facts.h[4]|(uint32_t)s->facts.h[5]<<8;
    uint32_t bytes=3*cre3_u32(s->facts.h+8);
    s->resident_valid=0;
    if (io->prefix(io->tx,bytes/3)!=1) {io->abort(io->tx);return cre3l_result(s,CRE3L_TRANSMIT);}
    for (uint32_t at=0;at<bytes;) {
        uint32_t n=bytes-at<sizeof(b)?bytes-at:sizeof(b);
        if (!cre3l_read((void *)io,H+at,b,n)) {e=18;goto abort;}
        crc=cre3_crc(crc,b,n);
        if (at<1152) shim=cre3_crc(shim,b,1152-at<n?1152-at:n);
        if (at+n==bytes) {
            if (~crc!=s->candidate.body_crc || ~shim!=platform->shim_crc) {e=10;goto abort;}
            if (!cre3l_eof(io,H+bytes)) {e=18;goto abort;}
        }
        if (io->emit(io->tx,b,n)!=1) {e=CRE3L_TRANSMIT;goto abort;}
        at+=n;
    }
    s->transmitted=1;return cre3l_result(s,0);
abort:
    io->abort(io->tx);return cre3l_result(s,e);
}
#endif
/* Runtime-only resident identity commit. This does not publish an engine. */
static uint32_t cre3l_ready(struct cre3l_state *s,const uint32_t *ready)
{
    if (!s->candidate_valid || !s->transmitted) return cre3l_result(s,CRE3L_NO_CANDIDATE);
    s->transmitted=0;
    if (!cre3_ready(&s->facts,ready)) return cre3l_result(s,14);
    s->resident=s->candidate;s->resident_valid=1;return cre3l_result(s,0);
}
#endif
