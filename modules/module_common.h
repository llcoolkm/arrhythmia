/* Shared ABI-3 module helpers. No firmware writes or stock calls. Keyboard lookup is
 * the guarded stock-5706 chain used by scales.c: actual table pitches, not C major.
 * Unsupported/slice layouts fail silent. Output uses engine-only note_port(rec=0).
 * One accepted note per track; release on STOP, restart and before changing tracks.
 * Host ring is eight entries: keep callers to a few events per pulse. Clocked
 * modules follow stock transport only: press Play to run. Never use Play as a module control.
 * START may occur midbar. State belongs in the module's 768-byte RAM reservation.
 */
#ifndef MODULE_COMMON_H
#define MODULE_COMMON_H
#include <stdint.h>
#define MC_RAM 0x100u
#define MC_LEDS 224u
#define MC_NO_NOTE 255u
static inline void mc_led_table(volatile uint8_t *t) {
    t[0] = 37; t[1] = 38; t[2] = 39;      t[3] = 24; t[4] = 25; t[5] = 26;
    t[6] = 100; t[7] = 101; t[8] = 102;   t[9] = 103; t[10] = 88; t[11] = 89;
    t[12] = 156; t[13] = 157; t[14] = 158;   t[15] = 159; t[16] = 144; t[17] = 145;
    t[18] = 202; t[19] = 203; t[20] = 204;   t[21] = 205; t[22] = 206; t[23] = 207;
    t[24] = 30; t[25] = 31; t[26] = 16;   t[27] = 90; t[28] = 91; t[29] = 92;
    t[30] = 93; t[31] = 94; t[32] = 95;   t[33] = 80; t[34] = 81; t[35] = 82;
    t[36] = 146; t[37] = 147; t[38] = 148;   t[39] = 149; t[40] = 150; t[41] = 151;
    t[42] = 136; t[43] = 137; t[44] = 138;   t[45] = 195; t[46] = 196; t[47] = 197;
    t[48] = 20; t[49] = 21; t[50] = 22;   t[51] = 83; t[52] = 84; t[53] = 85;
    t[54] = 86; t[55] = 87; t[56] = 72;   t[57] = 73; t[58] = 74; t[59] = 75;
    t[60] = 139; t[61] = 140; t[62] = 141;   t[63] = 142; t[64] = 143; t[65] = 128;
    t[66] = 129; t[67] = 130; t[68] = 131;   t[69] = 185; t[70] = 186; t[71] = 187;
    t[72] = 10; t[73] = 11; t[74] = 12;   t[75] = 76; t[76] = 77; t[77] = 78;
    t[78] = 79; t[79] = 64; t[80] = 65;   t[81] = 66; t[82] = 67; t[83] = 68;
    t[84] = 132; t[85] = 133; t[86] = 134;   t[87] = 135; t[88] = 120; t[89] = 121;
    t[90] = 122; t[91] = 123; t[92] = 124;   t[93] = 191; t[94] = 176; t[95] = 177;
}
static inline void mc_clear(uint8_t *l) { for(uint32_t i=0;i<224;i++) l[i]=0; }
static inline void mc_rgb(const uint8_t *idx,uint8_t *l,uint32_t p,uint32_t r,uint32_t g,uint32_t b) {
    if(p<32) { l[idx[3*p]]=r; l[idx[3*p+1]]=g; l[idx[3*p+2]]=b; }
}
static inline uint32_t mc_random(uint32_t *s) { uint32_t v=*s; if(!v)v=0x13579bdu; v^=v<<13;v^=v>>17;v^=v<<5;return *s=v; }
static inline uint32_t mc_clamp(uint32_t v,uint32_t encoder,uint32_t lo,uint32_t hi) {
    int32_t n=(int32_t)v+(int16_t)encoder; return n<(int32_t)lo?lo:n>(int32_t)hi?hi:(uint32_t)n;
}
static inline uint32_t mc_link(uint32_t p,uint32_t off) {
    if((p&3)||p<0x20000000u||off>0x4fffcu||p>0x20050000u-4-off)return 0;
    return *(volatile uint32_t *)(p+off);
}
static inline uint32_t mc_keyboard(uint32_t t) {
    if(t>7||mc_link(0x20006e90u,0)!=0x080295d1u)return 0;
    uint32_t c=mc_link(mc_link(0x20006e90u,4),0),g=mc_link(c,0x3c);
    if(mc_link(c,0)!=0x080672e4u||mc_link(g,0)!=0x080678dcu)return 0;
    uint32_t n=mc_link(g,0x54+4*t),k=mc_link(n,0x4c);
    if(mc_link(n,0)!=0x08067248u||mc_link(k,0)!=0x080696f4u||k>0x20050000u-0x1a0)return 0;
    uint32_t rows=*(volatile uint8_t *)(k+0x19c); return rows==1||rows==2?k:0;
}
/* Sorted unique engine pitches 60..83 found in the actual 128-byte keyboard.
 * Degree wraps over that finite set. No valid pitches -> MC_NO_NOTE. */
static inline uint32_t mc_scale_note(uint32_t track,uint32_t degree) {
    uint32_t k=mc_keyboard(track),a=0,count=0;
    if(!k)return MC_NO_NOTE;
    for(uint32_t i=0;i<128;i++) {
        uint32_t v=*(volatile uint8_t *)(k+8+i);
        if(v>=72&&v<=95) a|=1u<<(v-72);
    }
    for(uint32_t i=0;i<24;i++)if((a>>i)&1)count++;
    if(!count)return MC_NO_NOTE;
    degree%=count;
    for(uint32_t i=0;i<24;i++)if((a>>i)&1) { if(!degree)return 60+i;degree--; }
    return MC_NO_NOTE;
}
static inline uint32_t mc_note_port(uint32_t t,uint32_t n,uint32_t v) {
    const volatile uint32_t *h=(const volatile uint32_t *)0x0806a570u;
    if(t>7||n>115||v>127||h[0]!=0x31485243u||h[1]<3)return 0;
    uint32_t fn=h[4]; if(!(fn&1)||fn<0x08000001u||fn>=0x08080000u)return 0;
    return ((uint32_t(*)(uint32_t,uint32_t,uint32_t,uint32_t))fn)(t,n,v,0);
}
struct mc_notes { uint8_t held[8],note[8]; };
static inline void mc_note_off(struct mc_notes *s,uint32_t t) {
    if(t<8&&s->held[t]) { mc_note_port(t,s->note[t],0);s->held[t]=0; }
}
static inline void mc_release(struct mc_notes *s) { for(uint32_t t=0;t<8;t++)mc_note_off(s,t); }
static inline uint32_t mc_note_on(struct mc_notes *s,uint32_t t,uint32_t degree,uint32_t vel) {
    if(t>7||!vel||vel>127)return 0;
    uint32_t n=mc_scale_note(t,degree);if(n==MC_NO_NOTE)return 0;
    mc_note_off(s,t);
    if(!mc_note_port(t,n,vel))return 0;
    s->held[t]=1;s->note[t]=n;return 1;
}
struct mc_clock { uint8_t running,seen,pulse; };
static inline uint32_t mc_clock_event(struct mc_clock *c,struct mc_notes *notes,uint32_t event,uint32_t pos) {
    if(event==1||event==3) {
        if(notes)mc_release(notes);
        c->running=event==1;c->seen=0;return 0;
    }
    uint32_t p=pos&255;
    if(event!=2||!c->running||p>=96||(c->seen&&c->pulse==p))return 0;
    c->pulse=p;c->seen=1;return 1;
}
/* Counts are raw signed encoder transitions. Small ranges integrate four counts
 * per step, so +1/-1 bounce cancels. Clamp discards residual into the limit. */
static inline uint32_t mc_encoder(int8_t *residual,uint32_t current,uint32_t value,uint32_t lo,uint32_t hi) {
    int32_t sum=*residual+(int16_t)value,step=sum/4;
    int32_t next=(int32_t)current+step;
    *residual=(int8_t)(sum-step*4);
    if(next<(int32_t)lo) { *residual=0;return lo; }
    if(next>(int32_t)hi) { *residual=0;return hi; }
    if((next==(int32_t)lo&&sum<0)||(next==(int32_t)hi&&sum>0))*residual=0;
    return (uint32_t)next;
}
#define MC_NO_PULSE 255u
struct mc_transport {
    struct mc_clock external;
    uint8_t enabled,pulse,accumulator,blink;
};
static inline void mc_transport_init(struct mc_transport *t) {
    t->external.running=0;t->external.seen=0;t->external.pulse=0;
    t->enabled=0;t->pulse=0;t->accumulator=0;t->blink=0;
}
/* Only genuine host events enter here. Never feed synthetic pulses into it. */
static inline uint32_t mc_transport_event(struct mc_transport *t,struct mc_notes *notes,uint32_t event,uint32_t pos) {
    if(event==1||event==3) { t->pulse=0;t->accumulator=0; }
    return mc_clock_event(&t->external,notes,event,pos)?pos&255:MC_NO_PULSE;
}
/* frame() supplies cosmetic idle phase only. Musical ticks NEVER come from it.
 * Keep the old API shape so modules cannot accidentally generate local beats. */
static inline uint32_t mc_transport_frame(struct mc_transport *t) {
    if(++t->blink>=188)t->blink=0;
    return MC_NO_PULSE;
}
/* Play/Stop belong to stock firmware; no module input consumes them. */
static inline uint32_t mc_transport_input(struct mc_transport *t,struct mc_notes *notes,uint32_t kind,uint32_t index,uint32_t value) {
    (void)t;(void)notes;(void)kind;(void)index;(void)value;return 0;
}
static inline void mc_transport_led(const struct mc_transport *t,uint8_t *leds) {
    if(t->external.running)return; /* leave running Play indication to firmware */
    uint32_t phase=t->blink<94?t->blink:188-t->blink;
    leds[179]=(uint8_t)(2+phase/8); /* slow, dim 'press Play to run' hint */
}
#endif
