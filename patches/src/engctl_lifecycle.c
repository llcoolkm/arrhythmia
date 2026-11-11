/* SPDX-License-Identifier: MIT
 * Declared defaults over the existing stock macro encoding. No shadow values,
 * settings entries, card operations or defaults on ordinary project apply.
 */
#if ENGINE_ABI3 && !FAR_PART
/* 128 is absent. Tune is pitch, not source8; kind1 owns source8 separately. */
static uint32_t engctl_macro_default(uint32_t source)
{
    const struct cre3_facts *f=abi3_resident_facts();
    if(!f || source>=12 || source==8)return 128;
    for(uint32_t i=0;i<f->h[26];i++) {
        const uint8_t *r=f->controls+8*i;
        if((r[0]&15)==5 && source>=r[1] && source<r[1]+6u)
            return r[2+source-r[1]];
    }
    return 128;
}
static uint32_t engctl_macro_raw_default(uint32_t source)
{
    uint32_t value=engctl_macro_default(source);
    return value>127 || source?value:syn_p6_of_fb(value);
}
/* Called immediately AFTER the real stock reset, BEFORE its native channel
 * deserialization and refresh. A load/engine change without reset never calls
 * this. The already-reset EC subrecord remains all zero and has no LRU edit.
 * Source0 retains the existing normalized<->physical projection. Lead ruling
 * e50d9d0c accepts its seven quantized defaults, with a tool/document warning.
 */
static void engctl_new_project(uint32_t store)
{
    const struct cre3_facts *f=abi3_resident_facts();
    if(!f || !in_sram(store,STORE_LEN+4) || rw(store+STORE_LEN)!=store ||
       !all_zero((const volatile uint8_t *)(store+TM_EXT),TM_EXT_LEN))return;
    SUSPEND_ALL();
    uint32_t buf=store+PL_SER_AT+7u*PL_SER_LEN, patch=0;
    volatile uint8_t *b=(volatile uint8_t *)(store+TM_EXT);
    for(uint32_t id=0;id<12;id++) {
        uint32_t value=engctl_macro_default(id);
        if(value>127)continue;
        if(id<8) {
            uint32_t p=buf;
            PL_ENCODE(CH_DESC+8u*(6u+id),&p,id?value:syn_p6_of_fb(value));
            p=buf;
            if(id==1 || id==2)PL_ENCODE(CH_DESC+8u*(13u+id),&p,0);
        } else {
            if(!patch) {
                wr32(b,TM_BLK_MAGIC);b[4]=TM_VERSION;
                b[6]=(uint8_t)TM_EXT_LEN;b[7]=(uint8_t)(TM_EXT_LEN>>8);
                syn_fm_create(b+SYN_PATCH_AT);patch=1;
            }
            b[SYN_PATCH_AT+SF_MA+id-9]=(uint8_t)value;
        }
    }
    for(uint32_t i=0;i<f->h[26];i++)if((f->controls[8*i]&15)==1) {
        uint32_t value=f->controls[8*i+5],p=buf;
        *(volatile uint8_t *)(store+SV_BASE_AT+7)=value;
        PL_ENCODE(CH_DESC,&p,value);
    }
    if(patch)wr32(b+TM_CRC_AT,tm_crc(b,TM_CRC_AT));
    RESUME_ALL();
}
#endif
