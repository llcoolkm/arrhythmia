/* balls.mod: up to four clocked bouncing balls on the 8 x 4 grid, ABI 3.
 * Press Play to run. Firmware passes Play/Stop to the stock transport.
 * Before START and after STOP, balls stay still with a dim pulsing Play hint.
 * Macro 1: speed half/quarter/eighth/sixteenth(default)-note physics ticks.
 * Four signed encoder counts per control step; bounce cancels.
 * Macro 2: gravity 0..4 (default 1). Macro 3: ball count 1..4 (default 2).
 * Macro 4: note target track 1..8 (default 1), using its current keyboard scale.
 * Pad presses add a ball at that cell; at four balls replace one round-robin.
 * Clear resets ball positions, preserving controls. Releases/pressure ignored.
 * Wall hits play one scale degree from the hit cell at velocity 96. Simultaneous
 * hits coalesce to the first ball: max one note-on and one off per received pulse.
 * Notes last half a physics tick; START/STOP/restart/track change release them.
 * Physics runs only on real stock clock subdivisions. Duplicate
 * pulses are ignored; skipped pulses are not replayed. Shift+SampleRec exits.
 */
#include <stdint.h>
#include "module_common.h"
#define MOD_RAM 0x100u
#define MAX_BALLS 4u
struct ball { int16_t x,y,vx,vy; };
struct ram {
    uint8_t idx[96];
    struct ball balls[MAX_BALLS];
    struct mc_notes notes;
    struct mc_transport transport;
    uint8_t speed,gravity,count,track,replace,gate;
    int8_t encoder[4];
    uint32_t seed;
};
_Static_assert(sizeof(struct ram)<=0x300,"module RAM");
static void spawn(struct ram *r,uint32_t i,uint32_t pad) {
    struct ball *b=&r->balls[i];
    b->x=(pad&7)*256; b->y=(pad>>3)*256;
    uint32_t v=mc_random(&r->seed);
    b->vx=v&1?128:-128; b->vy=v&2?64:-64;
}
static void reset(struct ram *r) {
    mc_release(&r->notes); r->gate=0;
    for(uint32_t i=0;i<r->count;i++)spawn(r,i,9+3*i);
    r->replace=0;
}
void init(uint8_t *leds) {
    struct ram *r=(struct ram *)MOD_RAM;
    mc_led_table(r->idx);
    for(uint32_t t=0;t<8;t++){r->notes.held[t]=0;r->notes.note[t]=0;}
    mc_transport_init(&r->transport);
    for(uint32_t i=0;i<4;i++)r->encoder[i]=0;
    r->seed=0x42414C4Cu;r->speed=3;r->gravity=1;r->count=2;r->track=0;
    reset(r);mc_clear(leds);
}
void input(uint8_t *leds,uint32_t kind,uint32_t index,uint32_t value) {
    struct ram *r=(struct ram *)MOD_RAM;
    if(kind==1 && index<32 && (value>>8)==1) {
        uint32_t i=r->count<4?r->count++:r->replace;
        spawn(r,i,index);r->replace=(i+1)&3;
    } else if(kind==2 && index==23 && value==1)reset(r);
    else if(kind==5) {
        if(index==0)r->speed=mc_encoder(&r->encoder[0],r->speed,value,0,3);
        else if(index==1)r->gravity=mc_encoder(&r->encoder[1],r->gravity,value,0,4);
        else if(index==2) {
            uint32_t n=mc_encoder(&r->encoder[2],r->count,value,1,4);
            for(uint32_t i=r->count;i<n;i++)spawn(r,i,9+3*i);
            r->count=n;
        } else if(index==3) {
            uint32_t track=mc_encoder(&r->encoder[3],r->track,value,0,7);
            if(track!=r->track) { mc_release(&r->notes);r->gate=0;r->track=track; }
        }
    }
    (void)leds;
}
static void pulse(struct ram *r,uint32_t p) {
    if(p==MC_NO_PULSE)return;
    if(r->gate && !--r->gate)mc_release(&r->notes);
    if(p%(48u>>r->speed))return;
    uint32_t hit=255;
    for(uint32_t i=0;i<r->count;i++) {
        struct ball *b=&r->balls[i];
        int32_t vy=b->vy+(r->gravity*16);
        if(vy>256)vy=256;
        int32_t x=b->x+b->vx,y=b->y+vy;
        uint32_t wall=0;
        if(x<=0){x=-x;b->vx=-b->vx;wall=1;}
        else if(x>=1792){x=3584-x;b->vx=-b->vx;wall=1;}
        if(y<0 || (y==0 && vy<0)){y=-y;vy=-vy;wall=1;}
        else if(y>768 || (y==768 && vy>0)){y=1536-y;vy=-vy;wall=1;}
        b->x=x;b->y=y;b->vy=vy;
        if(wall && hit==255)hit=((uint32_t)y>>8)*8+((uint32_t)x>>8);
    }
    if(hit!=255) {
        mc_note_on(&r->notes,r->track,hit,96);r->gate=(48u>>r->speed)/2;
    }
}
void clock(uint8_t *leds,uint32_t event,uint32_t pos,uint32_t bpm) {
    struct ram *r=(struct ram *)MOD_RAM;
    uint32_t p=mc_transport_event(&r->transport,&r->notes,event,pos);
    if(event==1 || event==3)r->gate=0;
    pulse(r,p);(void)leds;(void)bpm;
}
uint32_t frame(uint8_t *leds,uint32_t frame_no) {
    struct ram *r=(struct ram *)MOD_RAM;
    (void)mc_transport_frame(&r->transport);mc_clear(leds);
    for(uint32_t i=0;i<r->count;i++) {
        uint32_t p=((uint32_t)r->balls[i].y>>8)*8+((uint32_t)r->balls[i].x>>8);
        mc_rgb(r->idx,leds,p,i&1?254:32,i&2?64:254,128);
    }
    leds[161]=64;mc_transport_led(&r->transport,leds);
    (void)frame_no;return 1;
}
