/* mole.mod: clocked, silent whack-a-mole (ABI 3).
 * Press Play to run. Firmware passes Play/Stop to the stock transport.
 * Before START and after STOP, a dim pulsing Play hint remains visible.
 * Hit the yellow pad before the next target. Each hit scores once; a wrong pad
 * or an unhit expired target counts a miss. Releases/pressure are ignored.
 * Four signed encoder counts per step (bounce cancels). Macro 1 clockwise: half, quarter (default), eighth, sixteenth-note targets.
 * Clear resets score/misses. Hold Rec for decimal score or Duplicate for misses,
 * 00..99 (capped). Green/red count LEDs identify the selected counter.
 * START/STOP discard the pending target without a penalty. Shift+SampleRec
 * remains the host exit. No note output, firmware writes or peripheral I/O.
 */
#include <stdint.h>
#include "module_common.h"
#define MOD_RAM 0x100u
struct ram {
    uint8_t idx[96];
    uint32_t seed;
    struct mc_transport transport;
    uint8_t division, active, target, score, misses, view;
    int8_t encoder;
};
_Static_assert(sizeof(struct ram) <= 0x300, "module RAM");
static uint32_t period(uint32_t d) { return 48u >> d; }
static void reset(struct ram *r) {
    r->active=0; r->target=255; r->score=0; r->misses=0; r->view=0;
}
static uint32_t digit(uint32_t n)
{
    if (n == 0) return 0xF6F;
    if (n == 1) return 0xE9A;
    if (n == 2) return 0xEA7;
    if (n == 3) return 0xF37;
    if (n == 4) return 0x93D;
    if (n == 5) return 0xF1F;
    if (n == 6) return 0xFCB;
    if (n == 7) return 0x4A7;
    if (n == 8) return 0xF7F;
    return 0xD3F;
}

void init(uint8_t *leds) {
    struct ram *r=(struct ram *)MOD_RAM;
    mc_led_table(r->idx); r->seed=0x4D4F4C45u;
    mc_transport_init(&r->transport); r->encoder=0;
    r->division=1; reset(r); mc_clear(leds);
}
void input(uint8_t *leds,uint32_t kind,uint32_t index,uint32_t value) {
    struct ram *r=(struct ram *)MOD_RAM;
    if(kind==2 && index==23 && value==1) reset(r);
    else if(kind==2 && value<=1 && (index==16 || index==24)) {
        uint32_t view=index==16?1:2;
        if(value) r->view=view;
        else if(r->view==view) r->view=0;
    } else if(kind==5 && index==0) r->division=mc_encoder(&r->encoder,r->division,value,0,3);
    else if(kind==1 && index<32 && (value>>8)==1 && r->active) {
        if(index==r->target) { if(r->score<99)r->score++; r->active=0; }
        else if(r->misses<99)r->misses++;
    }
    (void)leds;
}
static void pulse(struct ram *r,uint32_t p) {
    if(p!=MC_NO_PULSE && p%period(r->division)==0) {
        if(r->active && r->misses<99)r->misses++;
        uint32_t previous=r->target;
        uint32_t next=mc_random(&r->seed)&31;
        if(next==previous)next=(next+1)&31;
        r->target=next; r->active=1;
    }
}
void clock(uint8_t *leds,uint32_t event,uint32_t pos,uint32_t bpm) {
    struct ram *r=(struct ram *)MOD_RAM;
    uint32_t p=mc_transport_event(&r->transport,0,event,pos);
    if(event==1 || event==3) { r->active=0; r->target=255; }
    pulse(r,p); (void)leds; (void)bpm;
}
uint32_t frame(uint8_t *leds,uint32_t frame_no) {
    struct ram *r=(struct ram *)MOD_RAM;
    (void)mc_transport_frame(&r->transport);
    mc_clear(leds);
    if(r->view) {
        uint32_t n=r->view==1?r->score:r->misses;
        uint32_t a=digit(n/10),b=digit(n%10);
        for(uint32_t p=0;p<32;p++) {
            uint32_t x=p&7,y=p>>3;
            uint32_t on=x<3?((a>>(y*3+x))&1):x>=4 && x<7?((b>>(y*3+x-4))&1):0;
            if(on)mc_rgb(r->idx,leds,p,r->view==2?254:0,r->view==1?254:0,0);
        }
    } else if(r->active)mc_rgb(r->idx,leds,r->target,254,190,0);
    leds[161]=32; leds[189]=32; leds[163]=32;
    mc_transport_led(&r->transport,leds);
    (void)frame_no; return 1;
}
