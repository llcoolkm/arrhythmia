/* SPDX-License-Identifier: MIT
 * EC project owner core. Pure prepare/read helpers, no T1/CRC or queue write.
 * Main must reserve project-write and producer staging before committing the
 * prepared project/current/staging state under the existing serialization.
 * A proposal alone is not a committed UI edit. Unknown schema is read-only. */
#ifndef CRE3SETTINGS_H
#define CRE3SETTINGS_H
#include "cre3.h"
struct c3ec_proposal { uint8_t project[100], current[10]; };
static uint32_t c3ec_u24(const uint8_t *b)
{ return b[0]|(uint32_t)b[1]<<8|(uint32_t)b[2]<<16; }
static void c3ec_put24(uint8_t *b,uint32_t v)
{ for(uint32_t i=0;i<3;i++)b[i]=(uint8_t)(v>>(8*i)); }
/* 0 unset, 1 recognized, 2 read-only unknown. */
static uint32_t c3ec_shape(const uint8_t *p)
{
    uint32_t any=0;
    for(uint32_t i=0;i<100;i++)any|=p[i];
    if(!any)return 0;
    if(p[0]!='E'||p[1]!='C'||p[2]!=1||p[3]>6)return 2;
    for(uint32_t i=0;i<p[3];i++) {
        uint32_t key=cre3_u32(p+4+14*i);
        if(!key)return 2;
        for(uint32_t j=0;j<i;j++)if(key==cre3_u32(p+4+14*j))return 2;
    }
    for(uint32_t i=4+14*p[3];i<100;i++)if(p[i])return 2;
    return 1;
}
static uint32_t c3ec_key(const struct cre3_facts *f)
{
    for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        if((r[0]&15)==6)return cre3_u32(r+1);
    }
    return 0;
}
static uint32_t c3ec_index(const uint8_t *p,uint32_t key)
{
    if(c3ec_shape(p)==1 && key)
        for(uint32_t i=0;i<p[3];i++)if(cre3_u32(p+4+14*i)==key)return i;
    return 6;
}
/* Normalized wire values. Stored unclaimed/invalid bytes are never overwritten. */
static void c3ec_values(const struct cre3_facts *f,const uint8_t *p,uint8_t *v)
{
    uint32_t index=c3ec_index(p,c3ec_key(f));
    const uint8_t *raw=index<6?p+4+14*index+4:0;
    for(uint32_t i=0;i<10;i++)v[i]=0;
    for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        uint32_t k=r[0]&15, src=r[1], value=r[5];
        if(k==2||k==3) {
            uint32_t at=src==16?0:3, mask=(1u<<r[3])-1;
            if(raw) {
                uint32_t saved=(c3ec_u24(raw+at)>>r[2])&mask;
                if(saved<(k==3?2u:r[4]))value=saved;
            }
            c3ec_put24(v+at,c3ec_u24(v+at)|(value<<r[2]));
        } else if(k==4) {
            uint32_t at=6+src-17;
            if(raw&&raw[at]<=127)value=raw[at];
            v[at]=(uint8_t)value;
        }
    }
}
static uint32_t c3ec_enabled(const struct cre3_facts *f,uint32_t i,const uint8_t *v)
{
    const uint8_t *r=f->controls+8*i;
    uint32_t ref=r[7]&31;
    if(!ref)return 1;
    const uint8_t *t=f->controls+8*(ref-1);
    uint32_t on=(c3ec_u24(v+(t[1]==16?0:3))>>t[2])&1;
    return on!=((r[7]>>5)&1);
}
/* False leaves BOTH input and output unchanged. Caller validates f first. */
static uint32_t c3ec_prepare(const struct cre3_facts *f,const uint8_t *p,
                            uint32_t record,uint32_t value,uint32_t writable,
                            struct c3ec_proposal *out)
{
    uint32_t key=c3ec_key(f), shape=c3ec_shape(p);
    if(!writable || !key || shape==2 || record>=f->h[26])return 0;
    const uint8_t *r=f->controls+8*record;
    uint32_t k=r[0]&15;
    if(k<2||k>4 || value>=(k==4?128u:k==3?2u:r[4]))return 0;
    struct c3ec_proposal next;
    c3ec_values(f,p,next.current);
    if(!c3ec_enabled(f,record,next.current))return 0;
    for(uint32_t i=0;i<100;i++)next.project[i]=p[i];
    uint8_t entry[14]={0};
    uint32_t index=c3ec_index(p,key), n=shape?p[3]:0;
    if(index<6)for(uint32_t i=0;i<14;i++)entry[i]=p[4+14*index+i];
    else {
        for(uint32_t i=0;i<4;i++)entry[i]=(uint8_t)(key>>(8*i));
        for(uint32_t i=0;i<10;i++)entry[4+i]=next.current[i];
        index=n<6?n:5;
        if(n<6)n++;
    }
    if(k==4)entry[10+r[1]-17]=(uint8_t)value;
    else {
        uint32_t at=r[1]==16?4:7, mask=((1u<<r[3])-1)<<r[2];
        c3ec_put24(entry+at,(c3ec_u24(entry+at)&~mask)|(value<<r[2]));
    }
    for(uint32_t i=index;i;i--)
        for(uint32_t j=0;j<14;j++)next.project[4+14*i+j]=next.project[4+14*(i-1)+j];
    for(uint32_t i=0;i<14;i++)next.project[4+i]=entry[i];
    next.project[0]='E';next.project[1]='C';next.project[2]=1;next.project[3]=(uint8_t)n;
    c3ec_values(f,next.project,next.current);
    /* The firmware stores .text only and has no AEABI memcpy runtime. */
    volatile uint8_t *dest=(volatile uint8_t *)out;
    const uint8_t *src=(const uint8_t *)&next;
    for(uint32_t i=0;i<sizeof(next);i++)dest[i]=src[i];
    return 1;
}
#endif
