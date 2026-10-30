/* euclid.mod: four eight-step Euclidean rows, ABI 3. Press Play to run.
 * Stock firmware owns Play/Stop; this module never uses them as controls.
 * Before START and after STOP the pattern and Play hint pulse dimly. Only
 * genuine stock clock events advance notes and rhythm; frames are cosmetic.
 * Pads select a row. Macros 1..6 edit that row: hits (0..steps), steps (1..8),
 * rotation, track (1..8), scale degree (0..20), velocity (1..127). Turning a
 * control releases held notes only when its value changes. Small controls integrate
 * four encoder counts per step. Defaults: tracks 1..4, degree 0 (first live
 * keyboard pitch at or above 60, unshifted on chromatic drum tracks), hits
 * 1/2/3/4, eight steps, velocity 96. Each row's note is taken from its target
 * track's checked live keyboard; slice/unsupported layouts stay silent.
 *
 * One step per six stock clock pulses; each note ends three pulses later.
 * At most four ons OR four offs per callback, engine-only (no recording/MIDI).
 * Refused ons are not held. Missing gate-off pulses release and skip one step.
 * STOP/restart release all accepted notes. A frame watchdog only releases notes
 * if external clock events vanish; it never advances rhythm. Rows follow bar pulse
 * position, including a START midbar. Pads show hits, current step and selection.
 * Wall-clock playback and 32 simultaneous notes are deliberately not promised.
 */
#include "module_common.h"
struct row { uint8_t hits,steps,rotation,track,degree,velocity; };
struct ram {
    uint8_t idx[96];
    struct mc_notes notes;
    struct mc_transport transport;
    struct row row[4];
    uint8_t selected,step,quiet_frames;
    int8_t encoder[6];
};
_Static_assert(sizeof(struct ram)<=768,"module RAM overflow");
#define S ((struct ram *)MC_RAM)

static uint32_t hit(const struct row *r,uint32_t step) {
    uint32_t p=(step+r->steps-r->rotation)%r->steps;
    return (p*r->hits)%r->steps<r->hits;
}
void init(uint8_t *leds) {
    struct ram *s=S;
    mc_led_table(s->idx);mc_clear(leds);mc_transport_init(&s->transport);
    for(uint32_t i=0;i<4;i++) {
        s->row[i].hits=i+1;s->row[i].steps=8;s->row[i].rotation=0;
        s->row[i].track=i;s->row[i].degree=0;s->row[i].velocity=96;
    }
    s->selected=0;s->step=255;s->quiet_frames=0;
}
void input(uint8_t *leds,uint32_t kind,uint32_t index,uint32_t value) {
    struct ram *s=S;
    (void)leds;
    if(kind==1&&index<32&&(value&0x100)) {
        s->selected=index/8;
        for(uint32_t i=0;i<6;i++)s->encoder[i]=0;
    }
    if(kind!=5||index>5)return;
    struct row *r=&s->row[s->selected];
    uint8_t *field=&r->hits;uint32_t lo=0,hi=r->steps;
    if(index==1) { field=&r->steps;lo=1;hi=8; }
    if(index==2) { field=&r->rotation;hi=r->steps-1; }
    if(index==3) { field=&r->track;hi=7; }
    if(index==4) { field=&r->degree;hi=20; }
    if(index==5) { field=&r->velocity;lo=1;hi=127; }
    uint32_t next=mc_encoder(&s->encoder[index],*field,value,lo,hi);
    if(next==*field)return;
    mc_release(&s->notes);*field=next;
    if(r->hits>r->steps)r->hits=r->steps;
    if(r->rotation>=r->steps)r->rotation=r->steps-1;
}
static void tick(uint32_t pulse) {
    struct ram *s=S;
    s->quiet_frames=0;
    if(pulse%6==3)mc_release(&s->notes);
    if(pulse%6)return;
    s->step=pulse/6;
    for(uint32_t i=0;i<8;i++)if(s->notes.held[i]) {
        mc_release(&s->notes);return; /* missed off: do not add another onset burst */
    }
    uint32_t used=0;
    for(uint32_t i=0;i<4;i++) {
        struct row *r=&s->row[i];
        /* The host is monophonic per track: first active row wins duplicates. */
        if(hit(r,s->step)&&!(used&(1u<<r->track))) {
            used|=1u<<r->track;
            mc_note_on(&s->notes,r->track,r->degree,r->velocity);
        }
    }
}
void clock(uint8_t *leds,uint32_t event,uint32_t pos,uint32_t bpm) {
    struct ram *s=S;(void)leds;(void)bpm;
    uint32_t pulse=mc_transport_event(&s->transport,&s->notes,event,pos);
    if(pulse!=MC_NO_PULSE)tick(pulse);
}
uint32_t frame(uint8_t *leds,uint32_t frame_no) {
    struct ram *s=S;(void)frame_no;
    (void)mc_transport_frame(&s->transport); /* cosmetic idle phase only */
    if(s->quiet_frames<120)s->quiet_frames++;
    if(s->quiet_frames==120)mc_release(&s->notes);
    mc_clear(leds);
    for(uint32_t i=0;i<4;i++)for(uint32_t j=0;j<8;j++) {
        const struct row *r=&s->row[i];
        uint32_t lit=j<r->steps&&hit(r,j),current=s->transport.external.running&&s->step!=255&&j==s->step%r->steps;
        if(s->transport.external.running) {
            mc_rgb(s->idx,leds,8*i+j,current?127:lit?48:0,
                   current?127:lit?80:8,current?127:(i==s->selected?96:0));
        } else {
            uint32_t phase=s->transport.blink<94?s->transport.blink:188-s->transport.blink;
            mc_rgb(s->idx,leds,8*i+j,lit?3:0,lit?4+phase/16:1,
                   i==s->selected?16+phase/8:0);
        }
    }
    mc_transport_led(&s->transport,leds);
    return 1;
}
