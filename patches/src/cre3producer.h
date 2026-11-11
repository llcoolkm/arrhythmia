/* SPDX-License-Identifier: MIT
 * ABI-3 minor-0 producer core. Main exclusively owns N and publication.
 * Capture adapters must serialize transport() with Main using a bounded atomic
 * handoff/critical section; the context flags detect a missed handoff, not lock it.
 * Emit must atomically report batch success after words/epoch/commit and PROD,
 * in that order. On failure the adapter must close/reconcile before retrying if
 * any DSP write might have landed. This core provides no partial-I/O recovery.
 * N is a replacement staging store, not an additional copy of existing N.
 */
#ifndef CRE3PRODUCER_H
#define CRE3PRODUCER_H
#include <stdint.h>
#define C3P_MASK 0xFFFFFFu
#define C3P_WANTED 0x3FCFFFu
#define C3P_N 32u
#define C3P_RING 16u
#define C3P_RESERVE 4u
#define C3P_STOCK 0x4000000u /* explicitly routed stock word, never a CRE3 command */
#define C3P_SNAPSHOT 0x2000000u /* press/barrier values remain immutable */
#define C3P_PLAY 0x1000000u /* internal metadata; never sent on the wire */
struct c3p_entry { uint32_t w0, w1; };
struct c3p_note { uint32_t mantissa; uint8_t lane, exponent, velocity; };
struct c3p {
    struct c3p_entry n[C3P_N];
    uint32_t issued[22];
    uint32_t valid, wanted, epoch, transport_latest, refused;
    uint8_t lanes, slots, count, priority_count;
    uint8_t generation[4], kill_generation[4];
    uint8_t kills, all_off, waiting, open, resync, play_sequence, hello_left, transport_pending;
};
typedef uint32_t (*c3p_emit)(void *, const struct c3p_entry *, uint32_t,
                            uint32_t /* old PROD */, uint32_t /* epoch */);
static uint32_t c3p_onset(uint32_t w) { return !(w&C3P_STOCK) && (w&0xF80000u)==0xA80000u; }
static uint32_t c3p_source_ok(const struct c3p *p, uint32_t id, uint32_t v)
{
    if (id>21 || !(C3P_WANTED&(1u<<id)) || v>C3P_MASK) return 0;
    if (id<12 || (id>=17 && id<=20)) return v<=127 && (id!=8 || !p->slots || v<p->slots);
    if (id==14) return !v || (v>=320 && v<=4080);
    if (id==15) return !(v&~0x8303FFu) && (v&1023u)<768;
    return 1;
}
static uint32_t c3p_init(struct c3p *p, uint32_t wanted, uint32_t lanes, uint32_t slots)
{
    if (wanted&~C3P_WANTED || lanes<1 || lanes>4 || slots>32) return 0;
    for (uint32_t i=0;i<sizeof(*p)/4;i++) ((volatile uint32_t *)p)[i]=0;
    p->wanted=wanted; p->lanes=lanes; p->slots=slots;
    return 1;
}
static void c3p_latch(struct c3p *p)
{ p->resync=1; p->valid&=~(1u<<15); }
/* Epoch request owns the queue flush, but never clears a transport resync latch.
 * Only a matching ACK plus a fully staged fresh opening barrier can clear it. */
static uint32_t c3p_epoch_begin(struct c3p *p, uint32_t epoch)
{
    epoch&=C3P_MASK;
    if (epoch==p->epoch) return 0;
    uint32_t keep=0;
    for (uint32_t i=0;i<p->count;i++) if (p->n[i].w0&C3P_STOCK) p->n[keep++]=p->n[i];
    p->epoch=epoch; p->count=keep; p->valid=0; p->kills=0; p->all_off=0;
    p->priority_count=0; p->open=0; p->waiting=1; p->hello_left=0;
    for (uint32_t i=0;i<4;i++) p->generation[i]=0;
    return 1;
}
static uint32_t c3p_values_ok(const struct c3p *p, const uint32_t values[22])
{
    for (uint32_t id=0;id<22;id++)
        if ((p->wanted&(1u<<id)) && !c3p_source_ok(p,id,values[id])) return 0;
    return 1;
}
/* Greedy sparse PARAM3, keeping the frozen id<=10 and presence-bit grammar. */
static uint32_t c3p_parameters(struct c3p_entry *out, uint32_t changed, const uint32_t values[22])
{
    uint32_t count=0;
    while (changed) {
        uint32_t id=0;
        while (!(changed&(1u<<id))) id++;
        if (id<12) {
            uint32_t base=id>10?10:id, word=0;
            for (uint32_t j=0;j<3;j++) if (changed&(1u<<(base+j))) {
                word|=values[base+j]<<(7*j); word|=1u<<(21+j);
                changed&=~(1u<<(base+j));
            }
            out[count++]=(struct c3p_entry){C3P_SNAPSHOT|0xA20000u|base,word};
        } else {
            out[count++]=(struct c3p_entry){C3P_SNAPSHOT|0xA10000u|(id<<8),values[id]};
            changed&=~(1u<<id);
        }
    }
    return count;
}
static void c3p_mirror(struct c3p *p, const uint32_t values[22])
{
    for (uint32_t id=0;id<22;id++) if (p->wanted&(1u<<id)) p->issued[id]=values[id];
    p->valid=p->wanted;
}
/* hello[0..3] are already negotiated immutable host facts; this minor-0 core
 * sends them before all wanted sources. Source15 always uses captured latest. */
static uint32_t c3p_epoch_ack(struct c3p *p, uint32_t ack, const uint32_t hello[4], const uint32_t values[22])
{
    if (!p->waiting || (ack&C3P_MASK)!=p->epoch || !c3p_values_ok(p,values)) return 0;
    uint32_t snapshot[22];
    for (uint32_t i=0;i<22;i++) snapshot[i]=values[i];
    snapshot[15]=p->transport_latest;
    for (uint32_t i=0;i<4;i++) if (hello[i]>C3P_MASK) return 0;
    /* Maximum opening barrier is 16 entries; refuse rather than overwrite stock. */
    struct c3p_entry parameters[12];
    uint32_t n=c3p_parameters(parameters,p->wanted,snapshot);
    if (p->count+4+n>C3P_N) return 0;
    for (uint32_t i=0;i<4;i++) {
        p->n[p->count++]=(struct c3p_entry){0xA0C000u|i,hello[i]};
    }
    for (uint32_t i=0;i<n;i++) p->n[p->count++]=parameters[i];
    c3p_mirror(p,snapshot); p->waiting=0; p->open=1; p->resync=0; p->hello_left=4; p->transport_pending=0;
    return 1;
}
/* Pitch is already normalized m (implicit high mantissa bit omitted), exponent
 * and velocity. Generations are assigned only at successful publication. */
static uint32_t c3p_press(struct c3p *p, const uint32_t values[22], const struct c3p_note *notes, uint32_t count)
{
    if (!p->open || p->resync || count>p->lanes || !c3p_values_ok(p,values)) return 0;
    uint32_t lanes=0, changed=0;
    for (uint32_t i=0;i<count;i++) {
        const struct c3p_note *v=notes+i;
        if (v->lane>=p->lanes || lanes&(1u<<v->lane) || v->mantissa>0x7FFFFFu || v->exponent>23 || !v->velocity || v->velocity>127) return 0;
        lanes|=1u<<v->lane;
    }
    uint32_t snapshot[22];
    for (uint32_t id=0;id<22;id++) {
        snapshot[id]=id==15?p->transport_latest:values[id];
        if ((p->wanted&(1u<<id)) && (!(p->valid&(1u<<id)) || p->issued[id]!=snapshot[id] || (id==15 && p->transport_pending))) changed|=1u<<id;
    }
    struct c3p_entry params[12];
    uint32_t n=c3p_parameters(params,changed,snapshot);
    if (n+count>24 || p->count+n+count>C3P_N) { p->refused++; return 0; }
    for (uint32_t i=0;i<n;i++) p->n[p->count++]=params[i];
    for (uint32_t lane=0;lane<p->lanes;lane++) for (uint32_t i=0;i<count;i++) if (notes[i].lane==lane) {
        const struct c3p_note *v=notes+i;
        p->n[p->count++]=(struct c3p_entry){0xA80000u|(lane<<16)|(v->mantissa>>7),
            ((v->mantissa&127)<<17)|((uint32_t)v->exponent<<12)|((uint32_t)v->velocity<<5)};
    }
    c3p_mirror(p,snapshot); p->transport_pending=0; return 1;
}
/* Ordinary normalized control capture; source15 must use transport() below.
 * Return 1 accepted, 0 invalid/closed/refused. Coalesce only behind last ONSET
 * and before no intervening source snapshot. Published entries are untouched. */
static uint32_t c3p_control(struct c3p *p, uint32_t id, uint32_t value)
{
    if (!p->open || p->resync || id==15 || id>21 || !(p->wanted&(1u<<id)) || !c3p_source_ok(p,id,value)) return 0;
    if ((p->valid&(1u<<id)) && p->issued[id]==value) return 1;
    for (uint32_t i=p->count;i;i--) {
        struct c3p_entry *e=p->n+i-1;
        if (e->w0&C3P_STOCK) continue;
        uint32_t w=e->w0&C3P_MASK;
        if (c3p_onset(w)) break;
        if ((w&0xFF0000u)==0xA10000u && ((w>>8)&255)==id) {
            if (e->w0&C3P_SNAPSHOT) break;
            e->w1=value; p->issued[id]=value; p->valid|=1u<<id; return 1;
        }
        if ((w&0xFF0000u)==0xA20000u) {
            uint32_t base=w&255;
            if (id>=base && id<base+3 && (e->w1&(1u<<(21+id-base)))) {
                if (e->w0&C3P_SNAPSHOT) break;
                uint32_t shift=7*(id-base);
                e->w1=(e->w1&~(127u<<shift))|(value<<shift);
                p->issued[id]=value; p->valid|=1u<<id; return 1;
            }
        }
    }
    if (p->count==C3P_N) { p->refused++; return 0; }
    p->n[p->count++]=(struct c3p_entry){0xA10000u|(id<<8),value};
    p->issued[id]=value; p->valid|=1u<<id; return 1;
}
/* Called at actual accepted Play events, not inferred running-state edges.
 * A foreign/contention capture latches reconciliation without touching N.
 * Inputs are always one coherently captured tuple, even when handoff_ok=0.
 * Failed acquisition without such a tuple calls c3p_latch() directly; it must
 * never pass torn values here. The caller serializes these fields with Main. */
/* Capture-only path is also used while metadata is ready but unpublished.
 * The pending bit preserves a Play boundary across wire-counter/value equality.
 * No N entry or issued mirror is touched by this helper. */
static uint32_t c3p_transport_observe(struct c3p *p,uint32_t running,uint32_t pulse,uint32_t play)
{
    if (running>1 || pulse>=768 || play>1) { c3p_latch(p); return 0; }
    if (play) p->play_sequence++;
    uint32_t value=(running<<23)|((uint32_t)(p->play_sequence&3)<<16)|pulse;
    if ((p->wanted&(1u<<15)) && (play || value!=p->transport_latest)) p->transport_pending=1;
    p->transport_latest=value;
    return 1;
}
static uint32_t c3p_transport(struct c3p *p, uint32_t running, uint32_t pulse,
                              uint32_t play, uint32_t main_owned, uint32_t handoff_ok)
{
    if (!c3p_transport_observe(p,running,pulse,play)) return 0;
    uint32_t value=p->transport_latest;
    if (!(p->wanted&(1u<<15))) return 1;
    if (!main_owned || !handoff_ok) { c3p_latch(p); return 0; }
    if (!p->open || p->resync || !(p->wanted&(1u<<15))) return 0;
    if (p->transport_pending) p->valid&=~(1u<<15);
    if (!play) {
        if ((p->valid&(1u<<15)) && p->issued[15]==value) return 1;
        for (uint32_t i=p->count;i;i--) {
            struct c3p_entry *e=p->n+i-1;
            if (e->w0&C3P_STOCK) continue;
            uint32_t w=e->w0&C3P_MASK;
            if (c3p_onset(w)) break;
            if (w==0xA10F00u) {
                if (e->w0&(C3P_PLAY|C3P_SNAPSHOT)) break;
                e->w1=value; p->issued[15]=value; p->valid|=1u<<15; p->transport_pending=0; return 1;
            }
        }
    }
    if (p->count==C3P_N) { p->refused++; c3p_latch(p); return 0; }
    p->n[p->count++]=(struct c3p_entry){0xA10F00u|(play?C3P_PLAY:0),value};
    p->issued[15]=value; p->valid|=1u<<15; p->transport_pending=0; return 1;
}
static uint32_t c3p_release(struct c3p *p, uint32_t lane, uint32_t generation)
{
    if (!p->open || p->resync || lane>=p->lanes || generation>15) return 0;
    if (p->count==C3P_N) { p->refused++; return 0; }
    p->n[p->count++]=(struct c3p_entry){0xA02000u|(lane<<9),generation}; return 1;
}
static void c3p_cancel(struct c3p *p, uint32_t lane)
{
    uint32_t n=0;
    for (uint32_t i=0;i<p->count;i++) {
        uint32_t w=p->n[i].w0;
        if (!(c3p_onset(w) && ((w>>16)&7)==lane)) p->n[n++]=p->n[i];
    }
    p->count=n; if (!n) p->priority_count=0;
}
static uint32_t c3p_kill(struct c3p *p, uint32_t lane, uint32_t generation)
{
    if (lane>=p->lanes || generation>15) return 0;
    p->kills|=1u<<lane; p->kill_generation[lane]=generation; return 1;
}
static void c3p_all_off(struct c3p *p) { p->all_off=1; }
/* Stock words share the same 32 slots but have an explicit routing tag. They
 * retain raw words even if a stock payload resembles a CRE3 opcode. Only Main
 * calls these queue helpers; an external stock adapter owns its original ring. */
static uint32_t c3p_stock_push(struct c3p *p, uint32_t w0, uint32_t w1)
{
    if (p->count==C3P_N) return 0;
    p->n[p->count++]=(struct c3p_entry){(w0&C3P_MASK)|C3P_STOCK,w1&C3P_MASK}; return 1;
}
static uint32_t c3p_stock_peek(const struct c3p *p, struct c3p_entry *entry)
{
    for (uint32_t i=0;i<p->count;i++) if (p->n[i].w0&C3P_STOCK) {
        *entry=p->n[i]; entry->w0&=C3P_MASK; return 1;
    }
    return 0;
}
static void c3p_stock_pop(struct c3p *p)
{
    for (uint32_t i=0;i<p->count;i++) if (p->n[i].w0&C3P_STOCK) {
        for (uint32_t j=i+1;j<p->count;j++) p->n[j-1]=p->n[j];
        p->count--; return;
    }
}
/* At most four entries and one PROD update per adapter call. cons/prod are
 * verified current-epoch 24-bit counts. -1 means invalid ring distance. */
static int32_t c3p_drain(struct c3p *p, uint32_t prod, uint32_t cons, c3p_emit emit, void *context)
{
    if (!p->open || p->resync) return 0;
    uint32_t depth=(prod-cons)&C3P_MASK;
    if (depth>C3P_RING) return -1;
    uint32_t free=C3P_RING-depth, used=0, take=0, selected=0, kills=p->kills, all=p->all_off, cap=p->priority_count, hello=p->hello_left;
    uint8_t generation[4];
    for (uint32_t i=0;i<4;i++) generation[i]=p->generation[i];
    struct c3p_entry batch[4];
    while (used<4 && used<free) {
        struct c3p_entry entry;
        while (take<p->count && (p->n[take].w0&C3P_STOCK)) take++;
        uint32_t waiting=take<p->count;
        if (!hello && (all || kills) && (!waiting || cap<(uint32_t)p->lanes+1)) {
            if (all) {
                entry=(struct c3p_entry){0xA03000u,0}; all=0;
                for (uint32_t i=0;i<4;i++) generation[i]=0;
            } else {
                uint32_t lane=0; while (!(kills&(1u<<lane))) lane++;
                entry=(struct c3p_entry){0xA01000u|(lane<<9),p->kill_generation[lane]}; kills&=~(1u<<lane);
            }
            if (waiting) cap++;
        } else if (waiting) {
            entry=p->n[take]; uint32_t w=entry.w0&C3P_MASK, onset_behind=0;
            for (uint32_t i=take;i<p->count;i++) onset_behind|=c3p_onset(p->n[i].w0);
            if (((w&0xFF0000u)==0xA10000u || (w&0xFF0000u)==0xA20000u) && !onset_behind && free-used<=C3P_RESERVE) break;
            selected|=1u<<take; take++; cap=0; if (hello) hello--;
            if (c3p_onset(w)) {
                uint32_t lane=(w>>16)&7;
                generation[lane]=(generation[lane]+1)&15;
                entry.w1|=generation[lane];
            }
        } else break;
        entry.w0&=C3P_MASK; batch[used++]=entry;
    }
    if (!used || !emit(context,batch,used,prod&C3P_MASK,p->epoch)) return 0;
    uint32_t keep=0;
    for (uint32_t i=0;i<p->count;i++) if (!(selected&(1u<<i))) p->n[keep++]=p->n[i];
    p->count=keep; p->hello_left=hello; p->kills=kills; p->all_off=all; p->priority_count=p->count?cap:0;
    for (uint32_t i=0;i<4;i++) p->generation[i]=generation[i];
    return (int32_t)used;
}
#endif
