/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT.
 * Pure bounded file predicate. No decoder, global state, file or DSP operation. */
#ifndef CRE3SCAN_FILE_H
#define CRE3SCAN_FILE_H
struct cre3_scan_file {
    struct cre3_facts facts;
    uint32_t header_crc,body_crc,shim_crc;
    uint8_t vector[12],check;
};
struct cre3_scan_args {
    uint32_t first,second;
    const struct cre3_platform *platform;
};
#if ABI3_FAR_SCAN && !FAR_PART
static void cre3_scan_first(struct cre3_scan_file *,const uint8_t *,uint32_t,uint32_t,
                            const struct cre3_platform *);
static void cre3_scan_piece(struct cre3_scan_file *,const uint8_t *,uint32_t,uint32_t);
static uint32_t cre3_scan_finish(struct cre3_scan_file *,const struct cre3_platform *);
#else
static void cre3_scan_first(struct cre3_scan_file *s,const uint8_t *b,uint32_t got,
                            uint32_t size,const struct cre3_platform *p)
{
    s->check=1;
    if (got<64)return;
    for(uint32_t i=0;i<64;i++)s->facts.h[i]=b[i];
    s->check=(uint8_t)(p->qualified && !p->composition_ok?CRE3L_COMPOSITION:
                       cre3_header(s->facts.h,size,p));
    if (s->check)return;
    uint32_t n=8u*s->facts.h[26];
    if (got<64+n) {s->check=18;return;}
    for(uint32_t i=0;i<n;i++)s->facts.controls[i]=b[64+i];
    s->check=(uint8_t)cre3_controls(&s->facts);
    s->header_crc=s->body_crc=s->shim_crc=~0u;
}
static void cre3_scan_piece(struct cre3_scan_file *s,const uint8_t *b,uint32_t at,uint32_t n)
{
    if (s->check)return;
    uint32_t H=s->facts.h[4]|(uint32_t)s->facts.h[5]<<8;
    uint32_t end=at+n,stop=end<H?end:H;
    if(at<stop) {
        if(!at) {
            uint32_t zero=0;
            s->header_crc=cre3_crc(s->header_crc,b,60);
            s->header_crc=cre3_crc(s->header_crc,(const uint8_t *)&zero,4);
            if(stop>64)s->header_crc=cre3_crc(s->header_crc,b+64,stop-64);
        } else s->header_crc=cre3_crc(s->header_crc,b,stop-at);
    }
    uint32_t start=at>H?at:H;
    if(start<end) s->body_crc=cre3_crc(s->body_crc,b+start-at,end-start);
    stop=end<H+1152?end:H+1152;
    if(start<stop)s->shim_crc=cre3_crc(s->shim_crc,b+start-at,stop-start);
    start=at>H+1152?at:H+1152;stop=end<H+1164?end:H+1164;
    for(uint32_t i=start;i<stop;i++)s->vector[i-H-1152]=b[i-at];
}
static uint32_t cre3_scan_finish(struct cre3_scan_file *s,const struct cre3_platform *p)
{
    if(s->check)return s->check;
    if(~s->header_crc!=cre3_u32(s->facts.h+60))return 2;
    uint32_t e=cre3_vector(s->vector,cre3_u32(s->facts.h+8));
    if(e)return e;
    return ~s->body_crc!=cre3_u32(s->facts.h+12) || ~s->shim_crc!=p->shim_crc?10:0;
}
#endif
#endif
