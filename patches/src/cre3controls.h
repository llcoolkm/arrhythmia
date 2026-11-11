/* SPDX-License-Identifier: MIT
 * Stateless engine-defined UI core. Call only with admitted records.
 * Logical surface1 pages map to Sample view rows3/4; surface2 is synth page4.
 * The adapter maps hue0..7/intensity to platform lights and owns input commit.
 * No project, producer, DSP or stock UI state is changed here. */
#ifndef CRE3CONTROLS_H
#define CRE3CONTROLS_H
#include "cre3settings.h"
struct c3ui_position { uint8_t surface, pad, count, knob; };
typedef void (*c3ui_pad_fn)(void *,uint32_t,uint32_t,uint32_t);
typedef void (*c3ui_knob_fn)(void *,uint32_t,uint32_t,uint32_t);
static uint32_t c3ui_position(const struct cre3_facts *f,uint32_t index,struct c3ui_position *out)
{
    uint32_t row[2]={0,0},col[2]={0,0},knob=0,choice=0;
    for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        if((r[0]>>4)==1 && ((r[0]&15)==2 || (r[0]&15)==3))choice=1;
    }
    for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        uint32_t kind=r[0]&15,s=r[0]>>4,pad=0,n=0,k=0;
        if(kind==1){pad=choice?16:0;n=r[4];}
        else if(kind==2||kind==3) {
            uint32_t at=s-1;n=kind==2?r[4]:1;
            if(((r[7]&64)&&col[at])||col[at]+n>8){row[at]++;col[at]=0;}
            pad=8*row[at]+col[at];col[at]+=n;
        } else if(kind==4)k=++knob;
        else continue;
        if(i==index){out->surface=(uint8_t)s;out->pad=(uint8_t)pad;out->count=(uint8_t)n;out->knob=(uint8_t)k;return 1;}
    }
    return 0;
}
static uint32_t c3ui_value(const uint8_t *r,const uint8_t *v,uint32_t sound)
{
    uint32_t k=r[0]&15;
    if(k==1)return sound;
    if(k==4)return v[6+r[1]-17];
    return (c3ec_u24(v+(r[1]==16?0:3))>>r[2])&((1u<<r[3])-1);
}
/* Number of Sample pages; presence of synth page4 is returned as0/1. */
static uint32_t c3ui_pages(const struct cre3_facts *f,uint32_t surface)
{
    uint32_t pages=0;
    for(uint32_t i=0;i<f->h[26];i++) {
        struct c3ui_position p;
        if(!c3ui_position(f,i,&p)||p.surface!=surface)continue;
        uint32_t n=surface==2?1:p.count?(p.pad+p.count+15)/16:0;
        if(n>pages)pages=n;
    }
    return pages;
}
static void c3ui_render(const struct cre3_facts *f,const uint8_t *values,uint32_t sound,
                       uint32_t surface,uint32_t page,void *ctx,c3ui_pad_fn pad,c3ui_knob_fn knob)
{
    uint32_t total=surface==1?16:32,base=surface==1?16*page:0;
    for(uint32_t i=0;i<total;i++)pad(ctx,i,0,0);
    if(surface==2)for(uint32_t i=0;i<8;i++)knob(ctx,i,0,0);
    if((surface!=1&&surface!=2)||page>=c3ui_pages(f,surface))return;
    for(uint32_t i=0;i<f->h[26];i++) {
        struct c3ui_position p;
        if(!c3ui_position(f,i,&p)||p.surface!=surface)continue;
        const uint8_t *r=f->controls+8*i;
        if(!c3ec_enabled(f,i,values))continue;
        uint32_t k=r[0]&15,v=c3ui_value(r,values,sound);
        if(k==4){knob(ctx,p.knob-1,r[6],v);continue;}
        for(uint32_t j=0;j<p.count;j++) {
            uint32_t at=p.pad+j;
            if(at<base||at>=base+total)continue;
            uint32_t on=k==3?v!=0:j==v;
            pad(ctx,at-base,on?r[6]&15:r[6]>>4,on?2:1);
        }
    }
}
/* Returns record+1,0=inert. Sound result is handled by existing sound/note
 * routing; choice/toggle goes through the project's atomic edit owner. */
static uint32_t c3ui_input(const struct cre3_facts *f,const uint8_t *values,uint32_t surface,
                          uint32_t page,uint32_t pad,uint32_t clear,uint32_t *value)
{
    uint32_t total=surface==1?16:32;
    if((surface!=1&&surface!=2)||pad>=total||page>=c3ui_pages(f,surface))return 0;
    uint32_t at=pad+(surface==1?16*page:0);
    for(uint32_t i=0;i<f->h[26];i++) {
        struct c3ui_position p;
        if(!c3ui_position(f,i,&p)||p.surface!=surface||!p.count||at<p.pad||at>=p.pad+p.count)continue;
        const uint8_t *r=f->controls+8*i;
        if(!c3ec_enabled(f,i,values))return 0;
        uint32_t k=r[0]&15;
        *value=k==1?at-p.pad:clear?r[5]:k==3?!c3ui_value(r,values,0):at-p.pad;
        return i+1;
    }
    return 0;
}
#endif
