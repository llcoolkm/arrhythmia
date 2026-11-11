/* SPDX-License-Identifier: MIT
 * ABI-3 admission core. No allocation, DSP access or publication.
 * read returns 1 only for the exact requested range; size is the exact EOF.
 * The loader supplies its pinned platform and exact-composition verdict.
 * Return the failed check number, 18 for I/O, or 0 for static admission.
 * Check 14 belongs to cre3_ready(), after loading, never to file parsing. */
#ifndef CRE3_H
#define CRE3_H
#include <stdint.h>
#include "cre3policy.h"
struct cre3_platform {
    uint32_t id, offer, windows, shim_crc;
    uint8_t qualified, composition_ok;
};
struct cre3_facts { uint8_t h[64], controls[128]; };
typedef uint32_t (*cre3_reader)(void *, uint32_t, uint8_t *, uint32_t);
static uint32_t cre3_u32(const uint8_t *b)
{ return b[0] | (uint32_t)b[1]<<8 | (uint32_t)b[2]<<16 | (uint32_t)b[3]<<24; }
static uint32_t cre3_crc(uint32_t c, const uint8_t *b, uint32_t n)
{
    while (n--) {
        c ^= *b++;
        for (uint32_t i=0;i<8;i++) c=(c>>1)^(0xEDB88320u & (0u-(c&1)));
    }
    return c;
}
static uint32_t cre3_sources(uint32_t wanted)
{
    uint32_t n=0, low=wanted&0x1FFFu;
    for (uint32_t x=wanted>>13;x;x>>=1) n+=x&1;
    while (low) {
        uint32_t i=0;
        while (!(low&(1u<<i))) i++;
        if (i>10) i=10;
        low &= ~(7u<<i); n++;
    }
    return n;
}
/* Format/queue minor 0 only. Later minors need their own capability owner. */
#if ABI3_FAR_COLD && !FAR_PART
static uint32_t cre3_controls(const struct cre3_facts *f);
#else
static uint32_t cre3_controls(const struct cre3_facts *f)
{
    const uint8_t *h=f->h;
    uint32_t wanted=cre3_u32(h+40), masks[2]={0,0}, knobs=0, macros=0;
    uint32_t sounds=0, keys=0, editable=0, choice=0;
    for (uint32_t i=0;i<h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        uint32_t k=r[0]&15, s=r[0]>>4, src=r[1], off=r[2], width=r[3], count=r[4];
        if (s>2 || !k || k>6) return 17;
        if (k==5) {
            if (s || (src!=0 && src!=6) || (macros&(1u<<src))
                || (wanted&(63u<<src))!=(63u<<src) || (src==6 && r[4])) return 17;
            for (uint32_t j=2;j<8;j++) if (r[j]>127) return 17;
            macros|=1u<<src; continue;
        }
        if (k==6) {
            if (s || !cre3_u32(r+1) || r[5] || r[6] || r[7] || keys++) return 17;
            continue;
        }
        if (src>21 || !(wanted&(1u<<src)) || r[7]&128 || (r[6]&15)>7 || (r[6]>>4)>7) return 17;
        uint32_t ref=r[7]&31;
        if ((!ref && (r[7]&32)) || ref>i) return 17;
        if (ref && ((f->controls[8*(ref-1)]&15)!=3 || (f->controls[8*(ref-1)+7]&31))) return 17;
        if (k==1) {
            if (s!=1 || src!=8 || off || width || !count || count!=h[46] || r[5]>=count) return 17;
            sounds++; continue;
        }
        editable=1;
        if (k==4) {
            if (s!=2 || src<17 || src>20 || (knobs&(1u<<src)) || off || width || count || r[5]>127 || r[6]>7) return 17;
            knobs|=1u<<src; continue;
        }
        if (!s || (src!=16 && src!=21) || off+width>24) return 17;
        if (k==2 ? (!width || width>3 || count<2 || count>8 || count>(1u<<width) || r[5]>=count)
                 : (width!=1 || count!=1 || r[5]>1)) return 17;
        uint32_t mask=((1u<<width)-1)<<off, bank=src==21;
        if (masks[bank]&mask) return 17;
        masks[bank]|=mask;
        if (s==1) choice=1;
    }
    if (sounds!=(h[46]!=0) || (editable && !keys) || ((wanted&(1u<<16)) && !masks[0])
        || ((wanted&(1u<<21)) && !masks[1]) || (wanted&(15u<<17))!=knobs
        || (choice && h[46]>16)) return 17;
    for (uint32_t s=1;s<=2;s++) {
        uint32_t row=0,col=0,limit=s==1 && choice && h[46] ? 1:3;
        for (uint32_t i=0;i<h[26];i++) {
            const uint8_t *r=f->controls+8*i;
            uint32_t k=r[0]&15;
            if ((k!=2 && k!=3) || (r[0]>>4)!=s) continue;
            uint32_t n=k==2?r[4]:1;
            if (((r[7]&64) && col) || col+n>8) {row++;col=0;}
            if (row>limit) return 17;
            col+=n;
        }
    }
    return 0;
}
#endif

/* Shared file-header predicate: the streaming loader and bounded inventory
 * use this exact grammar. Check 14 is deliberately absent. */
static uint32_t cre3_header(const uint8_t *h,uint32_t size,const struct cre3_platform *p)
{
    uint32_t H=h[4]|(uint32_t)h[5]<<8, n=cre3_u32(h+8), C=h[26];
    if (cre3_u32(h)!=0x33455243u || h[6] || h[7]
        || H<64+8u*(h[25]+C) || H>4096 || H%4) return 1;
    if (n<389 || n>8192) return 10;
    if (size!=H+3*n) return 1;
    if (C>16) return 17;
    if (h[25]>15) return 12;
    if (cre3_u32(h+20)!=0x300u) return 3;
    if (!p->qualified || !p->composition_ok || cre3_u32(h+16)!=p->id) return 4;
    uint32_t caps=cre3_u32(h+32), needs=cre3_u32(h+36), wanted=cre3_u32(h+40);
    if (!(caps&1) || caps&~0x19u || needs&~0x20u || wanted&~0x3FCFFFu) return 5;
    if (needs&~p->offer) return 6;
    if (!h[44] || h[44]>4 || h[45]<4 || h[45]>16 || h[46]>32) return 7;
    if (h[47]) return 8;
    if (cre3_u32(h+56)&~p->windows) return 9;
#if ABI3_PRODUCTION_RC
    if (!cre3_u32(h+52) || cre3_u32(h+52)>ABI3_PRODUCTION_E) return 13;
#endif
    if (h[25]) return 12;
    if (cre3_sources(wanted)+h[44]>24) return 15;
    if (!h[24] || h[27]) return 16;
    return 0;
}
static uint32_t cre3_vector(const uint8_t *b,uint32_t n)
{
    uint32_t t=(uint32_t)b[3]<<16 | (uint32_t)b[4]<<8 | b[5];
    return b[0]!=0x0A || b[1]!=0xF0 || b[2]!=0x80 || t<0x2184 || t>=0x2000+n
        || b[6] || b[7] || b[8] || b[9] || b[10] || b[11] ? 11:0;
}

static uint32_t cre3_file(cre3_reader read, void *ctx, uint32_t size,
                         const struct cre3_platform *p, struct cre3_facts *f)
{
    uint8_t b[64];
    uint8_t *h=f->h;
    if (size<64) return 1;
    if (!read(ctx,0,h,64)) return 18;
    uint32_t e=cre3_header(h,size,p);
    if (e) return e;
    uint32_t H=h[4]|(uint32_t)h[5]<<8, n=cre3_u32(h+8), C=h[26];
    if (C && !read(ctx,64,f->controls,8*C)) return 18;
    e=cre3_controls(f);
    if (e) return e;
    uint32_t crc=~0u;
    for (uint32_t at=0;at<H;) {
        uint32_t k;
        if (!at) {
            k=64;
            for (uint32_t i=0;i<k;i++) b[i]=h[i];
        } else if (at<64+8*C) {
            k=64+8*C-at<64?64+8*C-at:64;
            for (uint32_t i=0;i<k;i++) b[i]=f->controls[at-64+i];
        } else {
            k=H-at<64?H-at:64;
            if (!read(ctx,at,b,k)) return 18;
        }
        if (!at) for (uint32_t i=60;i<64;i++) b[i]=0;
        crc=cre3_crc(crc,b,k);at+=k;
    }
    if (~crc!=cre3_u32(h+60)) return 2;
    crc=~0u;
    uint32_t shim=~0u;
    for (uint32_t at=0;at<3*n;) {
        uint32_t k=3*n-at<64?3*n-at:64;
        if (!read(ctx,H+at,b,k)) return 18;
        crc=cre3_crc(crc,b,k);
        if (at<1152) shim=cre3_crc(shim,b,k);
        if (at==1152 && cre3_vector(b,n)) return 11;
        at+=k;
    }
    return ~crc!=cre3_u32(h+12) || ~shim!=p->shim_crc ? 10:0;
}
static uint32_t cre3_ready(const struct cre3_facts *f, const uint32_t *r)
{
    return r[0]==0xC0A10Du && r[1]==cre3_u32(f->h+20) && r[2]==0x524459u
        && r[4]==cre3_u32(f->h+32) && r[5]==(f->h[44]|(uint32_t)f->h[45]<<8);
}
#endif
