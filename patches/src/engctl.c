/* SPDX-License-Identifier: MIT
 * One EC owner; callbacks stage only, never access DSP/card. The bounded T1
 * write/CRC/current/staging transaction is scheduler- and IRQ-serialized.
 * Timing of the masked CRC is not qualified. */
#if ENGINE_ABI3 && !FAR_PART
#include "cre3controls.h"
static uint32_t abi3_control_source(uint32_t id)
{
    const uint8_t *v=abi3_current_values();
    if(!v || !abi3_resident_facts())return 0;
    if(id==16)return c3ec_u24(v);
    if(id==21)return c3ec_u24(v+3);
    return id>=17 && id<=20?v[6+id-17]:0;
}
static void abi3_controls_commit(void)
{
    SUSPEND_ALL();
    uint32_t mask;
    __asm__ volatile("mrs %0, primask\n\tcpsid i" : "=r"(mask) :: "memory");
    const struct cre3_facts *f=abi3_resident_facts();
    uint8_t *current=abi3_current_values();
    if(!f || !current)goto done;
    struct tm *t=tm();
    uint8_t empty[100],values[10];
    for(uint32_t i=0;i<100;i++)empty[i]=0;
    const uint8_t *p=t && t->why==TM_OK && t->store
        ? (const uint8_t *)(t->store+TM_EXT+0x308u):empty;
    c3ec_values(f,p,values);
    uint32_t changed=0;
    for(uint32_t i=0;i<10;i++){changed|=current[i]!=values[i];current[i]=values[i];}
    if(changed)abi3_project_controls_apply();
done:
    __asm__ volatile("msr primask, %0" :: "r"(mask) : "memory");
    RESUME_ALL();
}
static uint32_t engctl_edit_cb(volatile uint8_t *block,uint32_t arg)
{
    const struct cre3_facts *f=abi3_resident_facts();
    uint8_t *current=abi3_current_values();
    if(!f || !current)return TS_REFUSED;
    struct c3ec_proposal proposal;
    uint32_t record=arg&255, value=arg>>8;
    const uint8_t *old=(const uint8_t *)(block+0x308u);
    if(c3ec_shape(old)==2)return TS_READONLY;
    if(!c3ec_prepare(f,old,record,value,1,&proposal))return TS_REFUSED;
    const uint8_t *r=f->controls+8*record;
    uint32_t source=r[1],word=source==16?c3ec_u24(proposal.current):source==21?
        c3ec_u24(proposal.current+3):proposal.current[6+source-17];
    if(!abi3_stage_control(source,word))return TS_BUSY;
    /* Staging is reserved. No fallible operation remains in this callback. */
    for(uint32_t i=0;i<100;i++)block[0x308u+i]=proposal.project[i];
    for(uint32_t i=0;i<10;i++)current[i]=proposal.current[i];
    return TS_OK;
}
static uint32_t engctl_edit(struct state *s,uint32_t record,uint32_t value)
{
    uint32_t isr;
    __asm__ volatile("mrs %0, ipsr" : "=r"(isr));
    if(isr || record>=16 || value>127 || q0_foreign() || !t8_eng() || !abi3_engine_eligible())return TS_REFUSED;
    uint32_t mask;
    __asm__ volatile("mrs %0, primask\n\tcpsid i" : "=r"(mask) :: "memory");
    uint32_t result=tm_edit(s,engctl_edit_cb,record|(value<<8));
    __asm__ volatile("msr primask, %0" :: "r"(mask) : "memory");
    return result;
}
#endif
