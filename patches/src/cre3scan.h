/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT.
 * Bounded card inventory work. Storage aliases dead FILINFO only, never the
 * resident loader facts or the boot decoder. No DSP or file-write operation. */
#ifndef CRE3SCAN_H
#define CRE3SCAN_H
#include "cre3scan_file.h"
union cre3_scan_scratch {
    uint32_t fi[40];
    struct c0lz decoder;
    struct cre3_scan_file file;
};
#define CRE3_SCAN_WORK 128u
/* Phases 0/3 initialize the two decoder rings; 1/4 decode, 2/5 hash constants,
 * 6 is an exact composition and 7 a refusal. Each call handles <=128 raw bytes.
 * crc, encoded and raw/off alias the inventory's existing three accumulators. */
static void cre3_scan_composition(struct c0lz *s,uint8_t *phase,
                                 uint32_t *crc,uint32_t *encoded,uint32_t *raw)
{
    uint32_t one=*phase>=3;
    uint32_t ptr=one?C1LZ_DESC:C0LZ_DESC;
    uint32_t bytes=one?C1LZ_BYTES:C0LZ_BYTES;
    uint32_t limit=one?CRBC3_C1_BYTES:CRBC3_C0_BYTES;
    uint32_t enc_crc=one?C1LZ_ENC_CRC:C0LZ_ENC_CRC;
    uint32_t raw_crc=one?C1LZ_RAW_CRC:C0LZ_RAW_CRC;
    if (*phase==0 || *phase==3) {
        const volatile uint8_t *d=(const volatile uint8_t *)(uintptr_t)ptr;
        if (*(volatile uint32_t *)CD_C0_PTR!=C0LZ_DESC || *(volatile uint32_t *)C1_PTR!=C1LZ_DESC
            || cd_u32(d)!=(one?0x315A3143u:0x315A3043u) || cd_u32(d+4)!=bytes
            || cd_u32(d+8)!=enc_crc || cd_u32(d+12)!=limit || cd_u32(d+16)!=raw_crc) { *phase=7;return; }
        s->p=d+20;s->end=s->p+bytes;s->count=s->left=s->dist=s->kind=0;
        s->raw_limit=limit;s->failed=s->busy=0;s->source=ptr;
        if (!*phase) *crc=~0u;
        *encoded=*raw=~0u;(*phase)++;
    }
    if (*phase==1 || *phase==4) {
        const volatile uint8_t *begin=s->p;
        uint32_t n=limit-s->count;
        if (n>CRE3_SCAN_WORK)n=CRE3_SCAN_WORK;
        for (uint32_t i=0;i<n;i++) {
            uint8_t b;
            if (c0lz_next_state(s,&b)!=1) { *phase=7;return; }
            *raw=cre3_crc(*raw,&b,1);*crc=cre3_crc(*crc,&b,1);
        }
        *encoded=cre3_crc(*encoded,(const uint8_t *)begin,(uint32_t)(s->p-begin));
        if (s->count==limit) {
            uint8_t b;
            if (c0lz_next_state(s,&b)!=0 || ~*encoded!=enc_crc || ~*raw!=raw_crc) { *phase=7;return; }
            (*phase)++;*raw=0;
        }
    } else if (*phase==2 || *phase==5) {
        uint32_t size=*phase==2?CRBC3_SET_BYTES:CRBC3_EDIT_BYTES;
        const uint8_t *p=(const uint8_t *)(uintptr_t)(*phase==2?crbc3_set():crbc3_edits());
        uint32_t n=size-*raw;if(n>CRE3_SCAN_WORK)n=CRE3_SCAN_WORK;
        *crc=cre3_crc(*crc,p+*raw,n);*raw+=n;
        if (*raw==size) {(*phase)++;if(*phase==6 && ~*crc!=CRBC3_CRC)*phase=7;}
    }
}
#endif
