/* SPDX-License-Identifier: MIT
 * Compressed-layout firmware binding. Admission follows the compiled policy.
 * Candidate facts are not a resident renderer buffer: expose them only while
 * their complete identity equals the committed resident. No module ITCM used. */
#ifndef CRE3FIRMWARE_H
#define CRE3FIRMWARE_H
#include "cre3loader.h"
#include "cre3health.h"
struct cd3_state {struct cre3l_state loader;uint8_t current[10],session_bad_valid,reserved;struct c3h health;};
_Static_assert(sizeof(struct cd3_state)==CRE3L_STATE_SIZE,"explicit loader SRAM reservation");
_Static_assert(__builtin_offsetof(struct cre3l_state,last_check)==CRE3L_LAST_CHECK_OFFSET,"diagnostic check offset");
_Static_assert(__builtin_offsetof(struct cd3_state,health)==CRE3L_HEALTH_OFFSET,"diagnostic health offset");
#define CD3_STATE ((struct cd3_state *)CRE3L_STATE_AT)
#define CD3 (&CD3_STATE->loader)
struct cd3_ctx {uint32_t fil,ptr,c1,aborted;};
/* Positive rows exist only in explicitly labelled host fixtures, never ARM. */
#if defined(CRE3_FIRMWARE_HOST_MODEL) && (defined(__arm__) || defined(__thumb__))
#error "CRE3_FIRMWARE_HOST_MODEL is CPU fixture only, not an ARM qualification override"
#endif
#if defined(__arm__) || defined(__thumb__)
/* Immutable text bytes read through volatile, so qualified=0 does not strip
 * parser/control code and understate its budget. Policy1 is emitted only by
 * the explicitly bound production-RC profile. Its builder records whether E is
 * still a placeholder. Health readiness remains runtime state. */
__attribute__((naked,used,noinline)) static const volatile uint8_t *cd3_policy(void)
{
    __asm__ volatile("adr r0, 1f\n\t"
                     "bx lr\n\t"
                     ".p2align 2\n"
                     #if ABI3_PRODUCTION_RC
                     "1: .byte 1, 0\n\t"
#else
                     "1: .byte 0, 0\n\t"
#endif
                     ".p2align 1");
}
#endif
static uint32_t cd3_health_ready(void)
{return CD3_STATE->health.armed && !CD3_STATE->health.reason;}
static uint32_t cd3_health_reason(void)
{return CD3_STATE->health.reason;}
static void cd3_health_reset(void) {c3h_reset(&CD3_STATE->health);}
#ifndef CRE3_FIRMWARE_HOST_MODEL
/* Existing marker generation binds the verdict to the currently resident body.
 * CRC0 is valid, hence the separately charged session_bad_valid byte. */
static void cd3_health_fail(uint32_t reason)
{
    struct eo *e=eo();
    CD3_STATE->health.reason=(uint8_t)reason;
    if (!e) return;
    e->r_fault=1;e->tf=1;e->bad_crc=e->r_crc;CD3_STATE->session_bad_valid=1;
    volatile uint32_t *m=CD_MARK;
    if (itcm_ok() && m[0]==CD_TRY && m[1]==~CD_TRY && (m[3]>>16)==e->rgen)
        m[3]=(m[3]&~CD_L_MASK)|CD_L_FAIL;
}
#endif
/* Ready14 already passed. B is the only ARM-written shim word. q0_wr1 may
 * decline a bus claim, so read B back before publication is made eligible.
 * Production check 13 has already required a positive declaration within E.
 * A u32 declaration above the24-bit meter range is explicitly unready pending
 * contract disposition; never silently truncate it or add a preload rule. */
static uint32_t cd3_health_arm(void)
{
    struct c3h *s=&CD3_STATE->health;
    if (s->armed || s->reason==C3H_MISMATCH || s->reason==C3H_OVERRUN)
        return cd3_health_ready();
#ifdef CRE3_FIRMWARE_HOST_MODEL
    return !c3h_start(s,0,0,0); /* synthetic CPU fixture only */
#else
    uint32_t budget=cre3_u32(CD3->facts.h+52),r[2],actual;
    if (!s->started) {
        q0_rd(0x4F31u,2,r);
        if (r[0]>C3H_MASK || r[1]>C3H_MASK) {s->reason=budget>C3H_MASK?C3H_BUDGET:C3H_READ;return 0;}
        if (c3h_start(s,r[0],r[1],TICK())) {cd3_health_fail(C3H_MISMATCH);return 0;}
        s->armed=0;
        /* Unsupported budgets stay unready, but may not hide a mismatch. */
        if (budget>C3H_MASK) {s->reason=C3H_BUDGET;return 0;}
        s->started=1;
        q0_wr1(0x4F2Du,budget); /* one attempt per load; an uncertain write never retries */
    }
    q0_rd(0x4F2Du,1,&actual);
    q0_rd(0x4F31u,2,r);
    if (r[0]>C3H_MASK || r[1]>C3H_MASK) {s->reason=budget>C3H_MASK?C3H_BUDGET:C3H_READ;return 0;}
    /* Do not lose a mismatch or newly armed overrun during readback/retries. */
    s->reason=C3H_OK;
    uint32_t reason=c3h_observe(s,r[0],r[1],1);
    if (reason) {cd3_health_fail(reason);return 0;}
    if (budget>C3H_MASK) {s->reason=C3H_BUDGET;return 0;}
    if (actual!=budget) {s->reason=C3H_READ;return 0;}
    s->armed=1;s->reason=C3H_OK;return 1;
#endif
}
/* Root calls this before cd_live's TRY-only early returns. Counter snapshots
 * survive ordinary runtime initialization and are sampled outside queue OPEN
 * too. Never read while the actual bus/load owner excludes Main. */
static void cd3_health_poll(struct q0 *q)
{
#ifndef CRE3_FIRMWARE_HOST_MODEL
    struct eo *e=eo();
    if (!e || e->r_pair!=R_ENGINE) return;
    struct c3h *s=&CD3_STATE->health;
    if (!q || !s->armed || s->reason || q->busy || q->bulk || q->excl || o1_faulted()) return;
    uint32_t now=TICK();
    if ((uint32_t)(now-s->poll_at)<1000u) return;
#if ABI3_PRODUCTION_RC
    /* Retained normalMAX is X:4F33, after mismatch/overrun at4F31/32. */
    uint32_t meter[3];q0_rd(0x4F31u,3,meter);
    if (meter[0]>C3H_MASK || meter[1]>C3H_MASK || meter[2]>C3H_MASK
        || !s->armed || q->bulk || q->excl || o1_faulted()) return;
    s->normal_max=meter[2];
    uint32_t *r=meter;
#else
    uint32_t r[2];q0_rd(0x4F31u,2,r);
    if (r[0]>C3H_MASK || r[1]>C3H_MASK || !s->armed || q->bulk || q->excl || o1_faulted()) return;
#endif
    s->poll_at=now;
    volatile uint32_t *m=CD_MARK;
    uint32_t trying=itcm_ok() && m[0]==CD_TRY && m[1]==~CD_TRY && (m[3]>>16)==e->rgen;
    uint32_t reason=c3h_observe(s,r[0],r[1],trying);
    if (reason) cd3_health_fail(reason);
#else
    (void)q;
#endif
}
#if ABI3_FM_ACCEPTANCE
/* Test identity only. This never changes cd3_policy or the public row.
 * The final header CRC binds metadata as well as the fixed facts below. An
 * unbound header pin rejects every file until the builder supplies its value. */
#ifndef ABI3_FM_BODY_CRC
#define ABI3_FM_BODY_CRC 0xB161B051u
#endif
#ifndef ABI3_FM_HEADER_CRC
#define ABI3_FM_HEADER_CRC 0u
#endif
#ifndef ABI3_FM_WORDS
#define ABI3_FM_WORDS 5146u
#endif
static uint32_t cd3_trial_facts(const struct cre3_facts *f)
{
    /* Only used after the full parser validates HCRC, controls, shim and body.
     * The exact header pin binds every fact, control record and metadata byte. */
    return ABI3_FM_HEADER_CRC && cre3_u32(f->h+8)==ABI3_FM_WORDS &&
        cre3_u32(f->h+12)==ABI3_FM_BODY_CRC &&
        cre3_u32(f->h+60)==ABI3_FM_HEADER_CRC;
}
static uint32_t cd3_trial_candidate(void)
{
    return cd3_trial_facts(&CD3->facts) && CD3->candidate.body_crc==ABI3_FM_BODY_CRC &&
        CD3->candidate.header_crc==ABI3_FM_HEADER_CRC;
}
#endif
static struct cre3_platform cd3_platform(void)
{
    struct cre3_platform p=cre3l_platform();
    /* Implemented target shape only: METER and six candidate windows. These
     * numbers confer no grant while the separate qualified predicate is zero. */
    p.offer=32;
#if ABI3_PRODUCTION_RC
    p.windows=CRE3_PRODUCTION_WINDOWS;
#else
    p.windows=63;
#endif
#if ABI3_GENERAL_BENCH
    /* Test context, not a production qualification or policy grant. */
    p.qualified=1;
#elif defined(CRE3_FIRMWARE_HOST_MODEL) && !ABI3_FM_ACCEPTANCE
    p.qualified=1;
#elif defined(__arm__) || defined(__thumb__)
    p.qualified=cd3_policy()[0]==1;
#endif
    return p;
}
static uint32_t abi3_platform_offer(void) {return cd3_platform().offer;}
static uint32_t cd3_session_blocked(uint32_t crc,uint32_t bad)
{return CD3_STATE->session_bad_valid && crc==bad;}
__attribute__((used,noinline)) static uint32_t cd3_size(void *v,uint32_t *n)
{struct cd3_ctx *c=v;*n=link(c->fil,FIL_FSIZE);return 1;}
__attribute__((used,noinline)) static uint32_t cd3_seek(void *v,uint32_t at)
{
    struct cd3_ctx *c=v;
    typedef uint32_t (*seek_fn)(uint32_t,uint32_t);
#ifdef CRE3_FIRMWARE_HOST_MODEL
    return CD3_MODEL_SEEK(c->fil,at)==0;
#else
    return ((seek_fn)0x080223D9u)(c->fil,at)==0;
#endif
}
__attribute__((used,noinline)) static uint32_t cd3_read(void *v,uint8_t *b,uint32_t n,uint32_t *got)
{struct cd3_ctx *c=v;return F_READ(c->fil,(uintptr_t)b,n,got)==0;}
/* Decoder is existing boot-owned SRAM, synchronously borrowed under TOK_MAIN.
 * Validate descriptor/encoded stream before beginning the sequential raw part. */
__attribute__((used,noinline)) static uint32_t cd3_part(void *v,uint32_t part,uint32_t at,uint8_t *b,uint32_t n,uint32_t *got)
{
    struct cd3_ctx *c=v;
    *got=0;
    if (part>3) return 0;
    uint32_t size=part==0?CRBC3_C0_BYTES:part==1?CRBC3_SET_BYTES:
                  part==2?CRBC3_C1_BYTES:part==3?CRBC3_EDIT_BYTES:0;
    if (at>size || n>size-at) return 0;
#if DSP_C1_LZ
    if (part==0 || part==2) {
        uint32_t crc,ptr=part==0?c->ptr:c->c1;
        if (!at && (!c01lz_check(ptr,~0u,&crc) || !c01lz_begin(ptr))) return 0;
        if (!C0LZ->busy || C0LZ->source!=ptr || C0LZ->count!=at) return c0lz_fail();
#else
    if (!part) {
        uint32_t crc;
        if (!at && (!c0lz_check(c->ptr,&crc) || !c0lz_begin())) return 0;
        if (!C0LZ->busy || C0LZ->count!=at) return c0lz_fail();
#endif
        for (uint32_t i=0;i<n;i++) if (c0lz_next(b+i)!=1) {C0LZ->busy=0;return c0lz_fail();}
        if (at+n==size) {
            uint8_t extra;uint32_t ok=c0lz_next(&extra)==0;
            C0LZ->busy=0;
            if (!ok) return c0lz_fail();
        }
    } else {
        if (part==2 && c->c1!=0x080101F0u) return 0;
        const volatile uint8_t *p=(const volatile uint8_t *)(part==1?crbc3_set():part==2?c->c1:crbc3_edits());
        for (uint32_t i=0;i<n;i++) b[i]=p[at+i];
    }
    *got=n;return 1;
}
__attribute__((used,noinline)) static uint32_t cd3_prefix(void *v,uint32_t words)
{
    struct cd3_ctx *c=v;uint32_t n=CD_BASE+words;
#if ABI3_FM_ACCEPTANCE
    if (!cd3_trial_candidate()) return 0;
#endif
    cd3_health_reset();
    struct eo *e=eo();
    volatile uint32_t *m=CD_MARK;
    m[3]=((e->fl&EF_TRIES)?1u:0u)<<8 | (uint32_t)e->rgen<<16;
    m[7]=0;m[4]=CD_PAUSE;m[0]=CD_TRY;m[1]=~CD_TRY;m[2]=CD3->candidate.body_crc;
    CD_IDLE(0,0,0,0);CD_SEL(0,0,0,0);
    CD_FRAME(n>>16&255,n>>8&255,n&255,0);CD_FRAME(0,0,0,0);
    cd_pfx(c->ptr);
    return !c0lz_failed();
}
__attribute__((used,noinline)) static uint32_t cd3_emit(void *v,const uint8_t *b,uint32_t n)
{
    (void)v;
    for (uint32_t i=0;i<n;i+=3) CD_FRAME(b[i],b[i+1],b[i+2],0);
    return !c0lz_failed();
}
__attribute__((used,noinline)) static void cd3_abort(void *v)
{
    struct cd3_ctx *c=v;c->aborted=1;
    CD_IDLE(0,0,0,0);CD_DESEL(0,0,0,0);CD_MARK[0]=0;
    if (!c0lz_failed()) cd_restart(c->ptr);
}
#if defined(__arm__) || defined(__thumb__)
_Static_assert(sizeof(struct cre3l_io)==40,"ARM callback binder layout");
_Static_assert(__builtin_offsetof(struct cre3l_io,size)==12 &&
               __builtin_offsetof(struct cre3l_io,abort)==36,"ARM callback binder offsets");
__attribute__((naked,used,noinline)) static void cd3_bind(struct cre3l_io *io)
{
    __asm__ volatile(
        "adr r1, cd3_size\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #12]\n\t"
        "adr r1, cd3_seek\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #16]\n\t"
        "adr r1, cd3_read\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #20]\n\t"
        "adr r1, cd3_part\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #24]\n\t"
        "adr r1, cd3_prefix\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #28]\n\t"
        "adr r1, cd3_emit\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #32]\n\t"
        "adr r1, cd3_abort\n\t" "orr r1, r1, #1\n\t" "str r1, [r0, #36]\n\t"
        "bx lr");
}
#endif
static struct cre3l_io cd3_io(struct cd3_ctx *c)
{
    struct cre3l_io io;
    io.fs=c;io.composition=c;io.tx=c;
#if defined(__arm__) || defined(__thumb__)
    cd3_bind(&io); /* PC-relative callback addresses; no absolute relocations */
#else
    io.size=cd3_size;io.seek=cd3_seek;io.read=cd3_read;io.part=cd3_part;
    io.prefix=cd3_prefix;io.emit=cd3_emit;io.abort=cd3_abort;
#endif
    return io;
}
/* Lookup/pass1 use the same full predicate; pass1 binds the lookup key again. */
static uint32_t cd3_check_path(uint32_t fil,const char *path,uint32_t ptr,uint32_t match)
{
    struct cre3l_key expected=CD3->candidate;
    uint32_t valid=CD3->candidate_valid;
    struct cd3_ctx c={fil,ptr,*(volatile uint32_t *)0x08031660u,0};
    struct cre3l_io io=cd3_io(&c);struct cre3_platform p=cd3_platform();
    CD3->candidate_valid=0;
    if (F_OPEN(fil,path,FA_READ)) return cre3l_result(CD3,18);
#if ABI3_FM_ACCEPTANCE
    p.qualified=1; /* parser context only; cd3_platform stays unqualified */
    uint32_t e=cre3l_check(&io,&p,CD3);
    if (!e && !cd3_trial_candidate()) e=4;
#else
    uint32_t e=cre3l_check(&io,&p,CD3);
#endif
    if (F_CLOSE(fil) && !e) e=18;
    if (!e && match && (!valid || !cre3l_equal(&expected,&CD3->candidate))) e=CRE3L_IDENTITY;
    if (e) CD3->candidate_valid=0;
    if (e==CRE3L_COMPOSITION) c0lz_fail();
    return cre3l_result(CD3,e);
}
static uint32_t cd3_stream(uint32_t fil,const char *path,uint32_t ptr)
{
    struct cd3_ctx c={fil,ptr,*(volatile uint32_t *)0x08031660u,0};
    struct cre3l_io io=cd3_io(&c);struct cre3_platform p=cd3_platform();
    if (F_OPEN(fil,path,FA_READ)) {CD3->transmitted=0;cre3l_result(CD3,18);return 0;}
#if ABI3_FM_ACCEPTANCE
    uint32_t e=4;
    if (cd3_trial_candidate()) {p.qualified=1;e=cre3l_stream(&io,&p,CD3);}
    else {CD3->candidate_valid=0;CD3->transmitted=0;}
#else
    uint32_t e=cre3l_stream(&io,&p,CD3);
#endif
    if (F_CLOSE(fil) && !e) {CD3->transmitted=0;cd3_abort(&c);e=18;}
    cre3l_result(CD3,e);
    if (e==CRE3L_COMPOSITION) c0lz_fail();
    if (c0lz_failed()) return 0;
    if (e) return c.aborted?2:0;
    CD_IDLE(0,0,0,0);CD_DESEL(0,0,0,0);return 1;
}
static uint32_t cd3_core1(uint32_t ptr)
{
    struct cd3_ctx c={0,*(volatile uint32_t *)CD_C0_PTR,ptr,0};
    struct cre3l_io io=cd3_io(&c);
    if (!CD3->transmitted) {cre3l_result(CD3,CRE3L_NO_CANDIDATE);return 0;}
    uint32_t ok=cre3l_composition(&io);
    cre3l_result(CD3,ok?0:CRE3L_COMPOSITION);return ok;
}
/* Safe throughout lookup/pass1: candidate_valid is cleared before facts change. */
static uint32_t abi3_engine_eligible(void)
{
    return region() &&
#if ABI3_FM_ACCEPTANCE
        cd3_trial_candidate() &&
#else
        cd3_platform().qualified &&
#endif
        CD3->candidate_valid && CD3->resident_valid
        && cre3l_equal(&CD3->candidate,&CD3->resident);
}
static const struct cre3_facts *abi3_resident_facts(void)
{return abi3_engine_eligible()?&CD3->facts:0;}
static const uint8_t *abi3_engine_header(void)
{const struct cre3_facts *f=abi3_resident_facts();return f?f->h:0;}
static uint8_t *abi3_current_values(void)
{return CD3_STATE->current;}
static uint32_t cd3_ready(void)
{
    uint32_t r[8];q0_rd(Q0_MAGIC_AT,8,r);
    if (!CD3->transmitted) return 0;
#if ABI3_FM_ACCEPTANCE
    if (!cd3_trial_candidate()) {cre3l_result(CD3,4);return 0;}
#endif
    if (!cre3_ready(&CD3->facts,r)) {cre3l_result(CD3,14);return 0;}
    cre3l_result(CD3,0); /* Ready14 passed; a later arm refusal has its own health reason. */
    if (!cd3_health_arm() || cre3l_ready(CD3,r)) return 0;
    abi3_controls_commit();return 1;
}
#if ABI3_FM_ACCEPTANCE || ABI3_GENERAL_BENCH
#include "cre3_trial_snapshot.h"
#endif
#endif
