/* SPDX-License-Identifier: MIT
 * Generic engine controls only: no engine class/name or file I/O in the UI.
 * All fields are decoded from admitted immutable resident records. No cache.
 */
#if ENGINE_ABI3 && !FAR_PART
/* Committed metadata may exist before publication for HELLO/configuration.
 * User surfaces belong only to the published engine. */
static const struct cre3_facts *engctl_visible_facts(void)
{ return t8_eng()?abi3_resident_facts():0; }
/* Internal UI extension: zero preserves published FM metadata, never gives
 * another engine implicit FM controls. Explicit capabilities live in +27 and
 * the existing kind6 record's formerly reserved bytes5/6. */
static uint32_t engctl_legacy_fm(void)
{
    const struct cre3_facts *f=engctl_visible_facts();
    return f && f->h[24]==1 && !f->h[27];
}
static uint32_t engctl_is_fm(void)
{const struct cre3_facts *f=engctl_visible_facts();return f && f->h[24]==1;}
static uint32_t engctl_chords(void)
{
    const struct cre3_facts *f=engctl_visible_facts();
    return f && f->h[24]==1 && ((f->h[27]&1u) || !f->h[27]);
}
static uint32_t engctl_poly_meta(uint32_t which)
{
    const struct cre3_facts *f=engctl_visible_facts();
    if(!f || f->h[24]!=1)return 0;
    if(!f->h[27])return which?1:15;
    for(uint32_t i=0;i<f->h[26];i++)if((f->controls[8*i]&15)==6)
        return f->controls[8*i+5+which];
    return 0;
}
static uint32_t engctl_poly_mask(void){return engctl_poly_meta(0);}
static uint32_t engctl_poly_default(void){return engctl_poly_meta(1);}
static const uint8_t *engctl_macro_record(uint32_t source)
{
    const struct cre3_facts *f=engctl_visible_facts();
    if(f)for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        if(r[0]==4 && r[1]==source)return r;
    }
    return 0;
}
static uint32_t engctl_macro_declared(uint32_t source)
{
    const struct cre3_facts *f=engctl_visible_facts();
    const uint8_t *r=engctl_macro_record(source),*values=abi3_current_values();
    /* This argument8 names global Tune, not ABI source8 (sound). Only9..11
     * are engine-declared macros; no source8 control record is admitted. */
    if(source==8)return f && f->h[24]==1;
    if(r)return values && c3ec_enabled(f,(uint32_t)(r-f->controls)/8,values);
    return source>=8 && source<=11 && engctl_legacy_fm();
}
static void engctl_rgb(uint8_t *dst,uint32_t hue,uint32_t level)
{
    dst[0]=(uint8_t)((hue<4 || hue==7)?level:0);
    dst[1]=(uint8_t)((hue==0 || hue==3 || hue==4 || hue==5)?level:hue==2?level>>2:0);
    dst[2]=(uint8_t)((hue==0 || hue>=5)?level:0);
}
static void engctl_config_macros_at(struct state *s,const volatile uint8_t *patch,uint32_t first)
{
    for(uint32_t knob=0;knob<4;knob++) {
        uint32_t source=8+knob,index=first+knob,at=56*(index>>1)+3*(index&1);
        for(uint32_t c=0;c<3;c++)s->leds[at+c]=0;
        if(!engctl_macro_declared(source))continue;
        const uint8_t *r=engctl_macro_record(source);
        uint32_t value=syn_fm_get(patch,SF_TUNE+knob);
        if(!knob)value=(value<64?63-value:value-64)*2;
        if(!r) s->leds[at+1]=(uint8_t)((first && !knob && !value)?DIM:value&0x7Eu); /* Legacy FM Tune rendering is unchanged. */
        else engctl_rgb(s->leds+at,r[6],(value*BRIGHT+63)/127);
    }
}
static void engctl_config_macros(struct state *s,const volatile uint8_t *patch)
{engctl_config_macros_at(s,patch,0);}
static void engctl_pad_light(void *ctx,uint32_t pad,uint32_t hue,uint32_t intensity)
{
    struct engctl_lights *c=ctx;
    uint8_t rgb[3];
    engctl_rgb(rgb,hue,intensity==2?BRIGHT:intensity==1?DIM:0);
    const uint8_t *map=pad_leds()+3*(pad+c->base);
    for(uint32_t i=0;i<3;i++)c->leds[map[i]]=rgb[i];
}
static void engctl_knob_light(void *ctx,uint32_t knob,uint32_t hue,uint32_t value)
{
    struct engctl_lights *c=ctx;
    if(knob>=8)return;
    uint32_t at=56*(knob>>1)+3*(knob&1);
    engctl_rgb(c->leds+at,hue,(value*BRIGHT+63)/127);
}
static void engctl_feedback(uint32_t result)
{
    PG->res=(uint8_t)result;
    if(result==TS_OK)PG->edits++;
    else {PG->refused++;PG->fb=PG_FB_FRAMES;}
}
static uint32_t engctl_synth_pages(void)
{
    const struct cre3_facts *f=engctl_visible_facts();
    if(!f)return 0;
    if(c3ui_pages(f,2))return 1;
    if(f->h[24]!=1)for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        if(r[0]==4 && r[1]>=9 && r[1]<=11)return 1;
    }
    return 0;
}
static uint32_t engctl_sample_pages(void)
{
    const struct cre3_facts *f=engctl_visible_facts();
    return f?c3ui_pages(f,1):0;
}
static uint32_t engctl_sample_selected_page(uint32_t sound)
{
    const struct cre3_facts *f=engctl_visible_facts();
    if(!f)return 0;
    for(uint32_t i=0;i<f->h[26];i++)if((f->controls[8*i]&15)==1) {
        struct c3ui_position p;
        if(c3ui_position(f,i,&p) && sound<p.count)return (p.pad+sound)/16;
        break;
    }
    return 0;
}
static uint32_t engctl_pad_input(struct state *s,uint32_t surface,uint32_t page,
                                 uint32_t pad,uint32_t clear,uint32_t *sound)
{
    const struct cre3_facts *f=engctl_visible_facts();
    const uint8_t *values=abi3_current_values();
    if(!f || !values || !c3ui_pages(f,surface))return 0;
    uint32_t value, record=c3ui_input(f,values,surface,page,pad,clear,&value);
    if(!record)return 1;
    if((f->controls[8*(record-1)]&15)==1) {*sound=value;return 2;}
    uint32_t result=engctl_edit(s,record-1,value);
    engctl_feedback(result);
    return result==TS_OK?3u:1u; /* successful bank edit; Sample may preview */
}
static uint32_t engctl_sample_pad(struct state *s,uint32_t page,uint32_t pad,
                                 uint32_t clear,uint32_t *sound)
{
    return engctl_pad_input(s,1,page,pad,clear,sound);
}
static void engctl_synth_pad(struct state *s,uint32_t pad,uint32_t clear)
{
    uint32_t unused;
    (void)engctl_pad_input(s,2,0,pad,clear,&unused);
}
static void engctl_synth_encoder(struct state *s,uint32_t knob,uint32_t delta,uint32_t clear)
{
    const struct cre3_facts *f=engctl_visible_facts();
    const uint8_t *values=abi3_current_values();
    if(!f || !values || knob>=4)return;
    for(uint32_t i=0;i<f->h[26];i++) {
        struct c3ui_position p;
        if(!c3ui_position(f,i,&p) || p.surface!=2 || p.knob!=knob+1)continue;
        const uint8_t *r=f->controls+8*i;
        if(!c3ec_enabled(f,i,values))return;
        int32_t current=values[6+r[1]-17], next=clear?r[5]:current+(int16_t)delta;
        next=next<0?0:next>127?127:next;
        if(next!=current)engctl_feedback(engctl_edit(s,i,(uint32_t)next));
        return;
    }
}
static void engctl_synth_render(struct state *s)
{
    const struct cre3_facts *f=engctl_visible_facts();
    const uint8_t *values=abi3_current_values();
    if(!f || !values)return;
    struct engctl_lights ctx={s->leds,0};
    c3ui_render(f,values,0,2,0,&ctx,engctl_pad_light,engctl_knob_light);
    if(f->h[24]!=1) {
        struct tm *m=tm();
        const volatile uint8_t *patch=m && m->why==TM_OK && m->store?
            (const volatile uint8_t *)(m->store+TM_EXT+SYN_PATCH_AT):0;
        engctl_config_macros_at(s,patch,4);
    }
}

#endif
