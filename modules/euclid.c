/* euclid.mod: four Euclidean rows of up to 32 steps, ABI 3. Press Play to run.
 * Plays like a four-track drum machine: rows 1..4 default to tracks 1..4.
 * Stock firmware owns Play/Stop; this module never uses them as controls.
 * Before START and after STOP the rows and the Play hint pulse dimly. Only
 * genuine stock clock events advance notes and rhythm; frames are cosmetic.
 *
 * Two views: overview (four rows of eight steps) and selected-row detail (32 steps).
 * Track 1..4 press opens detail; the same Track returns to overview. Releases do
 * nothing. Side buttons Note/Velocity/Gate/Pattern toggle rows 1..4. Track 5..8
 * and 1-16 stay dark and inert. Overview knobs edit four hits/rotation pairs;
 * detail knobs 1..8 edit hits, rotation, target, note, velocity, accent, chance and length.
 * Detail value positions/bars and operation outcomes expire after 1000 qualified
 * stock RTOS ticks; sequencer pulses and LED callback counts are never timers.
 *
 * Kept settings: every input saves the rows, the on/off state and overview page,
 * the selected row and the project generation to a 52-byte record at ITCM 0x3DC0
 * (below the top 512 bytes that the host keeps free, above the request block). init
 * takes them back when the record checks (magic, FNV-1a, every field in range); else
 * the defaults. Module RAM is zeroed at every start, ITCM above the image is not, so
 * the settings last until power off or until a larger file is loaded into ITCM.
 *
 * Patterns at open (host abi 4): the first frame offers a READ of the rows' target
 * tracks (one entry per track, the upper row). Without a record, or when the project
 * generation differs from the record's (a project load since), each read row whose
 * edit pattern is one Euclid can write takes its values from it: at least one hit,
 * every hit microstep 1 with one note of the track's keyboard (degree 0..23), one
 * probability, one velocity (the first hit of the cycle may differ: the accent), a
 * Euclidean spread at some rotation over the pattern's length. The other rows keep
 * their values. Same generation, or any input before the answer: nothing changes.
 * A refused or unanswered READ changes nothing; the Rec light shows it only if the
 * outcome is unknown.
 *
 * Write to pattern (host abi 4 only; older hosts: Rec and Clear dark and inert):
 * Rec writes each row into the current pattern of its target track (hits: note,
 * velocity or accent, chance as the stock probability, microstep 1; rests: step
 * off; length = the row's steps from the pattern's start step). Gate, flip, p-locks,
 * rate and direction stay. Two rows on one track: refused (red), one row per track
 * to write. Clear undoes the last write. Rec: dim red ready, amber blink while the
 * host works, green briefly written, red briefly refused, red/amber outcome unknown (wait).
 * While Euclid runs, the target tracks' own patterns play no notes and no sample
 * flips (their p-locks still run). A hit is skipped on a track whose pattern
 * still holds a note (host busy word), and in the first step after the track
 * becomes a target, so Euclid never plays over a held sequencer note.
 * Mailbox 0x3F4 (handshake), 0x3F8 (silence), 0x3FC (busy, written by the host).
 * Defaults: eight steps, hits 1/2/3/4, degree 0 (first live keyboard pitch at or
 * above 60), velocity 96, accent off, chance 8/8. Each row's note is taken from
 * its target track's checked live keyboard; slice/unsupported layouts stay silent.
 *
 * Polymeter: one step counter runs across bars; each row plays counter mod its
 * own length. START sets the counter to the bar step (0 on Play, the bar position
 * on a START midbar); a pulse index that jumps back without passing step 16 is a
 * restart and does the same. Chance uses a xorshift generator in module RAM:
 * deterministic from init, one draw per candidate hit below 8/8 only.
 *
 * One step per six stock clock pulses; each note ends three pulses later.
 * At most four ons OR four offs per callback. Notes use the pad path (rec = 1):
 * sound, MIDI out on the track's channel, and the recorder while Rec is armed.
 * Refused ons are not held. Missing gate-off pulses release and skip one step.
 * STOP/restart release all accepted notes. A frame watchdog only releases notes
 * if external clock events vanish; it never advances rhythm. Upper row wins a
 * shared track on one step; a hit that chance drops leaves the track free.
 */
#define MC_NOTE_REC 1u
#include "module_common.h"
#ifndef EUCLID_COARSE
#define EUCLID_COARSE 1 /* 1: sixteen velocity levels; 0: rev7 numeric KEEP */
#endif
struct viewport {
    const volatile uint8_t *key;
    uint32_t fingerprint;
    uint8_t target,octave; /* octave255 invalid; no geometry/palette state */
};
struct row { uint8_t hits,steps,rotation,track,degree,velocity,accent,chance; };
struct ram {
    uint8_t idx[96];
    struct mc_notes notes;
    struct mc_transport transport;
    struct row row[4];
    uint8_t selected,step,quiet_frames,mode,held,page[3];
    int8_t encoder[8];
    uint8_t ext[36];          /* LED bytes: track buttons 1..4, macros 1..8 (R,G,B) */
    uint8_t colour[12];       /* row colours (R,G,B) */
    uint32_t count,seed;
    uint16_t ew_wait;         /* frames the current request has waited */
    uint8_t ew_op,ew_seq,ew_flash,ew_green,ew_undo,ew_taken,ew_dark,ew_unknown,ew_row[4];
    uint8_t ew_mask,ew_fresh;  /* silenced tracks; newly silenced, not yet one step old */
    uint8_t on;               /* rows on (bit i = row i) */
    uint8_t ext2[12];         /* LED bytes: Note, Velocity, Gate, Pattern Settings */
    uint8_t look,kept,gk,touched;   /* open READ: 1 to send, 2 in flight; a record was taken; gen known; input
                                       before the READ's answer (the player's values win) */
    uint32_t result_at,overlay_at;
    uint8_t overlay,reason,reason_rows,operation,last_knob,result,buttons; /* host result or local 0x80..0x85 */
    uint32_t gen;             /* project generation of the rows (valid if gk) */
    struct viewport viewport[4];
};
_Static_assert(MC_RAM+sizeof(struct ram)<=0x3F4u,"module RAM overflow (mailbox at 0x3F4)");
#define S ((struct ram *)MC_RAM)
#define EW_MB ((volatile uint32_t *)0x3F4u)  /* [0] handshake, [1] silence, [2] busy (host) */
MC_CAPABILITY(3, 0x3F4);

static uint32_t hit(const struct row *r,uint32_t step) {
    uint32_t p=(step+r->steps-r->rotation)%r->steps;
    return (p*r->hits)%r->steps<r->hits;
}
static uint32_t first_hit(const struct row *r) {
    for(uint32_t j=0;j<r->steps;j++)if(hit(r,j))return j;
    return 255;
}
static void ext(uint8_t *e,uint32_t i,uint32_t r,uint32_t g,uint32_t b) { e[3*i]=r;e[3*i+1]=g;e[3*i+2]=b; }
/* ---- kept settings: a checked record in ITCM above the request block ---- */
#define EK_AT 0x3DC0u
#define EK_LEN 52u
#define EK_MAGIC 0x314B5545u      /* 'EUK1' */
#define EK ((volatile uint8_t *)EK_AT)
static uint32_t ek_room(void) { return (uint32_t)mc_pw_block()+sizeof(struct mc_pw_req)<=EK_AT; }
static uint32_t ek_sum(void) {
    uint32_t h=0x811C9DC5u;
    for(uint32_t i=4;i<EK_LEN-4;i++) { h^=EK[i];h*=0x01000193u; }
    return h;
}
static uint32_t ek_word(uint32_t o) { return EK[o]|EK[o+1]<<8|EK[o+2]<<16|(uint32_t)EK[o+3]<<24; }
static void ek_put(uint32_t o,uint32_t v) { for(uint32_t i=0;i<4;i++)EK[o+i]=(uint8_t)(v>>8*i); }
/* Layout: magic, row[4] (8 bytes each, as struct row), on, mode, selected, page[3], gen known, zero, gen, check. */
static void ek_save(const struct ram *s) {
    if(!ek_room())return;
    const uint8_t *r=(const uint8_t *)s->row;
    for(uint32_t i=0;i<32;i++)EK[4+i]=r[i];
    EK[36]=s->on;EK[37]=s->mode;EK[38]=s->selected;
    for(uint32_t i=0;i<3;i++)EK[39+i]=s->page[i];
    EK[42]=s->gk;EK[43]=0;ek_put(44,s->gen);
    ek_put(0,EK_MAGIC);ek_put(48,ek_sum());
}
static uint32_t row_ok(const volatile uint8_t *b) {   /* hits steps rotation track degree velocity accent chance */
    return b[1]-1u<32u&&b[0]<=b[1]&&b[2]<b[1]&&b[3]<8u&&b[4]<=23u&&b[5]-1u<127u&&b[6]<128u&&b[7]-1u<8u;
}
static void ek_load(struct ram *s) {
    if(!ek_room()||ek_word(0)!=EK_MAGIC||ek_word(48)!=ek_sum())return;
    for(uint32_t i=0;i<4;i++)if(!row_ok(EK+4+8*i))return;
    if(EK[36]>15u||EK[37]>2u||EK[38]>3u||EK[39]>3u||EK[40]>1u||EK[41]||EK[42]>1u||EK[43])return;
    uint8_t *r=(uint8_t *)s->row;
    for(uint32_t i=0;i<32;i++)r[i]=EK[4+i];
    s->on=EK[36];s->mode=0;s->selected=EK[38];s->page[0]=EK[39];s->page[1]=0;
    s->kept=1;s->gk=EK[42];s->gen=ek_word(44);
}
/* ---- write to pattern ---- */
/* Tick contract is enabled only by the exact qualified stock/host pin below. */
static uint32_t flash_fingerprint(uint32_t at,uint32_t len) {
    uint32_t h=0x811c9dc5u;
    for(uint32_t i=0;i<len;i++) { h^=*(volatile uint8_t *)(at+i);h*=0x01000193u; }
    return h;
}
static uint32_t timer_host(void) {
    /* Qualified 7357/7358 stock-5706 profiles only. ABI magic alone
     * never licenses the absolute RTOS read. The host service pointers, tick
     * getter and tick increment instructions must match the offline pins. */
    const volatile uint32_t *h=(const volatile uint32_t *)0x0806a570u;
    uint32_t label=*(volatile uint16_t *)0x080101cau;
#if defined(EUCLID_COMPRESSION_7367) && EUCLID_COMPRESSION_7367
    /* 7367: exact measured SDK table, no ABI3 grant. */
    return label==7367&&
        h[0]==0x31485243u&&h[1]==4&&h[2]==0x0806cb63u&&!h[3]&&
        h[4]==0x0806cc2bu&&h[5]==0x0806cc33u&&h[6]==0x0806cce1u&&h[7]==0x0806cd1du&&
#elif defined(EUCLID_COMPRESSION_7366) && EUCLID_COMPRESSION_7366
    /* Exact SDK table binding. This does not qualify engine ABI3. */
    return label==7366&&
        h[0]==0x31485243u&&h[1]==4&&h[2]==0x0806ce13u&&!h[3]&&
        h[4]==0x0806cedbu&&h[5]==0x0806cee3u&&h[6]==0x0806cf91u&&h[7]==0x0806cfcdu&&
#elif defined(EUCLID_COMPRESSION_7364) && EUCLID_COMPRESSION_7364
    /* Exact qualified host table for compressed streams.
     * Default builds retain the released 7357/7358 qualification unchanged. */
    return label==7364&&
        h[0]==0x31485243u&&h[1]==4&&h[2]==0x0806d463u&&!h[3]&&
        h[4]==0x0806d52bu&&h[5]==0x0806d533u&&h[6]==0x0806d5e1u&&h[7]==0x0806d61du&&
#else
    return (label==7357||label==7358)&&
        h[0]==0x31485243u&&h[1]==4&&h[2]==0x0806d433u&&!h[3]&&
        h[4]==0x0806d4fbu&&h[5]==0x0806d503u&&h[6]==0x0806d5b1u&&h[7]==0x0806d5edu&&
#endif
        flash_fingerprint(0x08024068u,12)==0x5315a080u&&
        flash_fingerprint(0x08066bfcu,16)==0x2d5b1d35u;
}
static uint32_t now(void) { return timer_host()?*(volatile uint32_t *)0x20000820u:0; }
static uint32_t ew_ready(const struct ram *s) { return mc_abi()>=4&&!s->ew_dark&&ek_room()&&timer_host(); }
static void expire(struct ram *s) {
    uint32_t t=now();
    if(s->ew_flash&&(uint32_t)(t-s->result_at)>=1000u) { s->ew_flash=0;s->reason=0;s->reason_rows=0; }
    if(s->overlay&&(uint32_t)(t-s->overlay_at)>=1000u)s->overlay=0;
}
static void ew_silence(struct ram *s) {
    uint32_t m=0;
    for(uint32_t i=0;i<4;i++)m|=1u<<s->row[i].track;
    if(mc_abi()<4)return;                          /* no host service: the mailbox stays unused */
    s->ew_fresh|=m&~s->ew_mask;                     /* new targets wait one step (the host's busy bit) */
    s->ew_mask=m;EW_MB[1]=MC_SE_MAGIC<<16|m;
}
static void ew_end(struct ram *s,uint32_t green) { s->ew_op=0;s->ew_flash=1;s->ew_green=green;s->ew_unknown=0;s->result_at=now(); }
static void ew_send(struct ram *s,uint32_t op) {
    volatile struct mc_pw_req *q=mc_pw_block();
    q->magic=MC_PW_MAGIC;q->op=op;q->seq=++s->ew_seq;q->zero=0;q->zero2=0;
    mc_dmb();
    EW_MB[0]=MC_PW_P<<24|(uint32_t)s->ew_seq<<16;
    s->ew_op=op;s->ew_wait=0;
}
/* Rec selects enabled rows now; ew_fill uses their current values when READ completes. Turning a row off later
 * does not cancel this request. Read the target tracks (note known); a shared track refuses. Every refusal is decided
 * before the block or the undo state changes, so a refused Rec keeps the previous undo intact. */
static void ew_rec(struct ram *s) {
    volatile struct mc_pw_req *q=mc_pw_block();
    uint32_t n=0,rows=0,bad=0,conflict=0;
    for(uint32_t i=0;i<4;i++) {
        const struct row *r=&s->row[i];
        if(!(s->on>>i&1u))continue;
        if(mc_scale_note(r->track,r->degree)==MC_NO_NOTE) { bad|=1u<<i;continue; }
        rows|=1u<<i;
        for(uint32_t j=0;j<i;j++)if((rows>>j&1u)&&s->row[j].track==r->track)conflict|=(1u<<i)|(1u<<j);
    }
    if(conflict||!rows) {
        s->result=conflict?0x80:!s->on?0x81:0x82;
        ew_end(s,0);s->reason=conflict?1:!s->on?2:3;s->reason_rows=conflict?conflict:bad;return;
    }
    s->ew_undo=0;                 /* the read reuses the block: the old undo ends */
    for(uint32_t i=0;i<4;i++)
        if(rows>>i&1u) { q->e[n].track=s->row[i].track;q->e[n].flags=0;s->ew_row[n++]=i; }
    q->n=n;
    ew_send(s,MC_PW_READ);
}
/* The read is back: the new bytes of every entry (fields: note, mask, velocity, probability, byte 6). */
static uint32_t ew_fill(struct ram *s) {
    volatile struct mc_pw_req *q=mc_pw_block();
    for(uint32_t i=0;i<q->n;i++) {
        volatile struct mc_pw_ent *e=&q->e[i];
        const struct row *r=&s->row[s->ew_row[i]];
        uint32_t note=mc_scale_note(r->track,r->degree),st=e->old_set>>8,fh=first_hit(r);
        if(r->track!=e->track||note==MC_NO_NOTE)return 0;
        for(uint32_t k=0;k<r->steps;k++) {
            uint32_t p=(st+k)&31u;
            volatile uint8_t *b=e->neu[p];
            if(!hit(r,k)) { b[1]=0;continue; }
            b[0]=note+12;b[1]=1;b[2]=r->accent&&k==fh?r->accent:r->velocity;b[3]=r->chance-1;
            if(!e->old[p][1])b[4]&=0xC1u;           /* a new step plays one note: chord key cleared */
        }
        e->new_set=((st+r->steps-1u)&31u)|st<<8;
    }
    return 1;
}
/* Smallest degree 0..23 whose note is pitch n on track t's keyboard (mc_scale_note's set), or 255. */
static uint32_t degree_of(uint32_t t,uint32_t n) {
    uint32_t k=mc_keyboard(t),a=0,d=0;
    if(!k||n-60u>23u)return 255;
    for(uint32_t i=0;i<128;i++) {
        uint32_t v=*(volatile uint8_t *)(k+8+i);
        if(v>=72&&v<=95)a|=1u<<(v-72);
    }
    if(!(a>>(n-60)&1u))return 255;
    for(uint32_t i=0;i<n-60;i++)d+=a>>i&1u;
    return d<=23?d:255;
}
/* Open: READ the rows' target tracks, one entry per track (the upper row). */
static void ew_look(struct ram *s) {
    volatile struct mc_pw_req *q=mc_pw_block();
    uint32_t n=0,used=0;
    for(uint32_t i=0;i<4;i++) {
        uint32_t t=s->row[i].track;
        if(used>>t&1u)continue;
        used|=1u<<t;q->e[n].track=t;q->e[n].flags=0;s->ew_row[n++]=i;
    }
    q->n=n;s->look=2;
    ew_send(s,MC_PW_READ);
}
/* The open READ is back: rows whose pattern Euclid can write take its values, unless the project is the same. */
static void ew_adopt(struct ram *s) {
    volatile struct mc_pw_req *q=mc_pw_block();
    uint32_t g=q->gen;
    if(!s->gk||g!=s->gen)for(uint32_t i=0;i<4;i++)s->viewport[i].octave=255;
    if(!s->touched&&(!s->kept||(s->gk&&g!=s->gen)))
        for(uint32_t i=0;i<q->n;i++) {
            volatile struct mc_pw_ent *e=&q->e[i];
            struct row *r=&s->row[s->ew_row[i]],t=*r;
            uint32_t st=e->old_set>>8,on=0,note=0,prob=0,h=0;
            if(e->status!=MC_PW_OK||e->track!=r->track||st>31u||(e->old_set&0xFFu)>31u)continue;
            t.steps=(((e->old_set&0xFFu)-st)&31u)+1u;
            for(uint32_t k=0;k<t.steps;k++) {
                volatile uint8_t *b=e->old[(st+k)&31u];
                if(!b[1])continue;
                /* READ success only reports a copy, not field validity. Check before narrowing to row bytes. */
                if(b[0]>127u||b[1]!=1||b[2]-1u>=127u||b[3]>7u||
                   (h&&(b[0]!=note||b[3]!=prob)))goto next;
                note=b[0];prob=b[3];on|=1u<<k;h++;
            }
            t.hits=h;
            if(!h||(t.degree=degree_of(r->track,note-12))==255)continue;
            for(t.rotation=0;t.rotation<t.steps;t.rotation++) {
                uint32_t m=0;
                for(uint32_t k=0;k<t.steps;k++)m|=hit(&t,k)<<k;
                if(m==on)break;
            }
            if(t.rotation==t.steps)continue;
            uint32_t fh=first_hit(&t),v0=e->old[(st+fh)&31u][2],v=0;
            for(uint32_t k=0;k<t.steps;k++) {          /* the other hits: one velocity */
                uint32_t x=e->old[(st+k)&31u][2];
                if(k==fh||!(on>>k&1u))continue;
                if(v&&x!=v)goto next;
                v=x;
            }
            if(!v)v=v0;                                 /* one hit */
            t.velocity=v;t.accent=v!=v0?v0:0;t.chance=prob+1;
            if(row_ok((const volatile uint8_t *)&t)) { *r=t;s->viewport[s->ew_row[i]].octave=255; }
            next:;
        }
    s->gen=g;s->gk=1;s->kept=1;
    mc_release(&s->notes);
    ek_save(s);
}
/* Each frame: follow the handshake of the request in flight. */
static void ew_poll(struct ram *s) {
    uint32_t w=EW_MB[0],tag=w>>24,res=w&0xFFu;
    if(!s->ew_op||(w>>16&0xFFu)!=s->ew_seq)return;
    if(tag==MC_PW_P) {                               /* offered, not taken: withdraw after 94 frames */
        if(++s->ew_wait<94)return;
        EW_MB[0]=0;
        if(s->look) { s->look=0;s->ew_op=0; }
        else { s->result=0x84;ew_end(s,0); }
        if(!s->ew_taken)s->ew_dark=1;               /* nobody ever answered: no write on this host */
        return;
    }
    s->ew_taken=1;
    if(tag==MC_PW_T) {                               /* taken: wait; after 94 frames the outcome is unknown */
        if(s->ew_wait<94)s->ew_wait++;
        else s->ew_unknown=1;
        return;
    }
    if(tag!=MC_PW_D)return;
    mc_dmb();s->result=res;
    uint32_t op=s->ew_op;
    if(s->look) {                                    /* the open READ: no light, no undo change */
        EW_MB[0]=0;s->look=0;s->ew_op=0;s->ew_unknown=0;
        if(res==MC_PW_OK)ew_adopt(s);
        return;
    }
    if(op==MC_PW_READ&&res==MC_PW_OK&&ew_fill(s)) { ew_send(s,MC_PW_WRITE);return; }
    EW_MB[0]=0;
    if(op==MC_PW_WRITE)s->ew_undo=res==MC_PW_OK;
    if(op==MC_PW_UNDO&&res!=MC_PW_BUSY)s->ew_undo=0;
    s->result=op==MC_PW_READ&&res==MC_PW_OK?0x85:res;
    ew_end(s,res==MC_PW_OK&&op!=MC_PW_READ);
}
static void ew_leds(const struct ram *s,uint8_t *leds) {
    if(!ew_ready(s))return;
    uint32_t pending=s->ew_op&&(!s->look||s->ew_unknown),r=0,g=0;
    uint32_t which=s->operation==23?160:188;
    leds[188]=20;
    if(s->ew_undo&&!s->ew_op)leds[160]=leds[161]=leds[162]=24;
    if(pending) { r=96;g=s->ew_unknown&&s->transport.blink%24>=12?0:40; }
    else if(s->ew_flash) { r=s->ew_green?0:96;g=s->ew_green?96:0; }
    else return;
    leds[which]=r;leds[which+1]=g;leds[which+2]=0;
}
void init(uint8_t *leds) {
    struct ram *s=S;
    mc_led_table(s->idx);mc_clear(leds);mc_transport_init(&s->transport);
    for(uint32_t i=0;i<4;i++) {
        struct row *r=&s->row[i];
        r->hits=i+1;r->steps=8;r->rotation=0;r->track=i;r->degree=0;
        r->velocity=96;r->accent=0;r->chance=8;s->viewport[i].octave=255;
    }
    s->selected=0;s->step=255;s->quiet_frames=0;s->on=15;
    ek_load(s);
    s->look=mc_abi()>=4;
    ew_silence(s);
    uint8_t *e=s->ext;
    ext(e,0,44,45,46);ext(e,1,47,32,33);ext(e,2,110,111,96);ext(e,3,97,98,99);
    ext(e,4,0,1,2);ext(e,5,3,4,5);ext(e,6,56,57,58);ext(e,7,59,60,61);
    ext(e,8,112,113,114);ext(e,9,115,116,117);ext(e,10,168,169,170);ext(e,11,171,172,173);
    e=s->ext2;
    ext(e,0,34,35,36);ext(e,1,27,28,29);ext(e,2,17,18,19);ext(e,3,23,8,9);
    e=s->colour;
    ext(e,0,120,0,0);ext(e,1,120,72,0);ext(e,2,0,120,48);ext(e,3,48,0,120);
}
/* Pad -> row and step in the current view and page. */
static uint32_t last_page(const struct ram *s) {
    uint32_t n=1;
    for(uint32_t i=0;i<4;i++)if(s->row[i].steps>n)n=s->row[i].steps;
    return (n-1)/8;
}
static void clamp_page(struct ram *s) { uint32_t p=last_page(s);if(s->page[0]>p)s->page[0]=p; }
static uint32_t pad_row(const struct ram *s,uint32_t i) { return s->mode?s->selected:i/8; }
static uint32_t pad_step(const struct ram *s,uint32_t i) { return s->mode?i:8*s->page[0]+i%8; }
/* Count the actual reachable unique pitches, within the existing two-octave played range. */
static uint32_t last_degree(uint32_t track) {
    uint32_t k=mc_keyboard(track),bits=0,n=0;
    if(!k)return 0;
    for(uint32_t i=0;i<128;i++) { uint32_t v=*(volatile uint8_t *)(k+8+i);if(v>=72&&v<=95)bits|=1u<<(v-72); }
    for(uint32_t i=0;i<24;i++)n+=bits>>i&1u;
    return n?n-1:0;
}
/* Read-only stock Note-view metadata. One mapping serves both the ordinary
 * keyboard and selected marker. Scaled Note view uses physical rows 3..4;
 * chromatic uses all four. Never write the project's octave observable. */
static uint32_t keyboard_position(uint32_t key,uint32_t rows,uint32_t octave,uint32_t pad) {
    if((rows==1&&pad<16)||pad>=32||octave>5)return 0;
    uint32_t index=pad+8*rows*(5-octave);
    return *(volatile uint8_t *)(key+8+index);
}
static uint32_t keyboard_octave(struct viewport *v,uint32_t track,uint32_t key,uint32_t note) {
    uint32_t rows=*(volatile uint8_t *)(key+0x19c);
    /* Qualified mc_keyboard metadata only. FNV detects same-pointer pitch edits;
     * 32-bit collisions and concurrent IRQ edits are not an identity guarantee. */
    uint32_t fingerprint=0x811c9dc5u^rows;
    for(uint32_t i=0;i<128;i++)fingerprint=(fingerprint^*(volatile uint8_t *)(key+8+i))*0x01000193u;
    if(v->key!=(const volatile uint8_t *)key||v->target!=track||v->fingerprint!=fingerprint)v->octave=255;
    v->key=(const volatile uint8_t *)key;v->target=track;v->fingerprint=fingerprint;
    uint32_t c=mc_link(mc_link(0x20006e90u,4),0),g=mc_link(c,0x3c);
    uint32_t h=mc_link(g,0x54+4*track),obs=mc_link(h,0x48);
    uint32_t slot=mc_link(obs,4)&1u,p=mc_link(obs,12+4*slot),octave=3;
    if(p>=0x20000000u&&p<0x20050000u) {
        uint32_t v=*(volatile uint8_t *)p;if(v<=5)octave=v;
    }
    /* Seed once using rev7 selection (project0 may initially use the upper
     * active row). Thereafter retain while visible, shift relative to DISPLAY. */
    if(v->octave<=5)octave=v->octave;
    uint32_t chosen=octave,distance=7;
    for(uint32_t o=0;o<6;o++)for(uint32_t pad=0;pad<32;pad++) {
        if(keyboard_position(key,rows,o,pad)!=note)continue;
        uint32_t d=o>octave?o-octave:octave-o;
        if(d<distance) { distance=d;chosen=o; }
    }
    if(distance<7)v->octave=chosen;
    else v->octave=255;
    return chosen;
}
/* Qualified stock metadata: Note-view base/class selects a colour ID; the
 * stock runtime palette holds packed 0x00RRGGBB. Root partners include self
 * partners at keyboard edges, so this honours the actual scale/root table. */
static uint32_t keyboard_colour(uint32_t track,uint32_t key,uint32_t rows,uint32_t octave,uint32_t pad) {
    if(*(volatile uint32_t *)0x0805dc84u!=0x08062ab0u)return 0;
    uint32_t c=mc_link(mc_link(0x20006e90u,4),0),g=mc_link(c,0x3c),h=mc_link(g,0x54+4*track);
    if(mc_link(h,0)!=0x08067248u)return 0;
    if(h>0x20050000u-0x78u)return 0;
    int32_t base=*(volatile int16_t *)(h+0x74);
    if(base<0||base>249)return 0;
    uint32_t index=pad+8*rows*(5-octave);
    uint32_t root=*(volatile uint8_t *)(key+0x114+index)!=255;
    uint32_t id=*(volatile uint16_t *)(0x08062ab0u+2*(base+(root?6:0)));
    if(id>=256)return 0;
    uint32_t rgb=*(volatile uint32_t *)(0x20003974u+4*id)&0x00ffffffu;
    /* Stock UI brightness observable element 2: only read the active backing
     * slot. All keyboard pads are among the compositor's dimmable lights. */
    uint32_t obs=mc_link(c,0x88),slot=mc_link(obs,4)>>2&1u;
    if(mc_link(mc_link(obs,12+4*slot),8)&0x400u) {
        uint32_t r=((rgb>>16)*0x8d00u)>>16,g=((rgb>>8&255)*0x8d00u)>>16,b=((rgb&255)*0x8d00u)>>16;
        rgb=r<<16|g<<8|b;
    }
    return rgb;
}
static void set(struct ram *s,struct row *r,uint8_t *field,uint32_t next) {
    if(next==*field)return;
    mc_release(&s->notes);*field=(uint8_t)next;
    if(r->hits>r->steps)r->hits=r->steps;
    if(r->rotation>=r->steps)r->rotation=r->steps-1;
}
static void layout(struct ram *s,uint32_t held) {
    s->held=held;
    for(uint32_t i=0;i<8;i++)s->encoder[i]=0;
}
#if EUCLID_COARSE
static uint32_t coarse_encoder(int8_t *residual,uint32_t current,uint32_t value,uint32_t accent) {
    int32_t sum=*residual+(int16_t)value;
    if(sum/4==0) {
        *residual=(int8_t)sum;
        if((current==(accent?0u:7u)&&sum<0)||(current==127&&sum>0))*residual=0;
        return current;
    }
    /* Nearest representative, positive ties upward; accent includes off0. */
    uint32_t level=accent&&current<=3?0:(current+5)/8;
    if(!accent&&!level)level=1;
    return (level=mc_encoder(residual,level,value,accent?0:1,16))?8*level-1:0;
}
#endif
static uint32_t control(struct ram *s,uint32_t kind,uint32_t index,uint32_t value) {
    if(kind==2&&(index<4||(index>=9&&index<=12))) {
        uint32_t bit=1u<<(index<4?index:index-5);
        if(!value)s->buttons&=~bit;
        else { if(s->buttons&bit)return 0;s->buttons|=bit; }
    }
    if(kind==1&&index<32&&(value&0x100)) {
        if(s->overlay) { s->overlay=0;return 1; }
        uint32_t row=pad_row(s,index),step=pad_step(s,index);
        s->selected=row;set(s,&s->row[row],&s->row[row].steps,step+1);clamp_page(s);return 1;
    }
    if(kind==2&&index<4) {
        if(value) { s->mode=!(s->mode&&s->selected==index);s->selected=index;s->overlay=0;layout(s,0);return 1; }
        return 0;
    }
    if(kind==2&&index>=9&&index<=12) {
        if(value) { uint32_t i=index-9;mc_note_off(&s->notes,s->row[i].track);s->on^=1u<<i;ew_silence(s);return 1; }
        return 0;
    }
    if(kind==2&&value&&(index==16||index==23)) {
        /* Unavailable Clear is inert before any operation or feedback mutation. */
        if(index==23&&!s->ew_undo)return 0;
        if(ew_ready(s)&&!s->look&&!s->ew_op) {
            s->overlay=0;s->operation=index;s->ew_flash=0;s->reason=0;s->reason_rows=0;
            if(index==16)ew_rec(s);
            else ew_send(s,MC_PW_UNDO);
            return 1;
        }
        return 0;
    }
    if(kind==2&&value) {
        if(!s->mode) {
            if(index==20&&s->page[0]<last_page(s)) { s->page[0]++;return 1; }
            if(index==19&&s->page[0]) { s->page[0]--;return 1; }
        }
        return 0;
    }
    if(kind!=5||index>7||!(int16_t)value)return 0;
    uint32_t k=s->mode?s->selected:index/2,f=s->mode?index:index&1u;
    struct row *r=&s->row[k];s->selected=k;s->last_knob=index+1;
    uint8_t *field=&r->hits;uint32_t lo=0,hi=r->steps;
    if(f==1) { field=&r->rotation;hi=r->steps-1; }
    if(f==2) { field=&r->track;hi=7; }
    if(f==3) { field=&r->degree;hi=last_degree(r->track); }
    if(f==4) { field=&r->velocity;lo=EUCLID_COARSE?7:1;hi=127; }
    if(f==5) { field=&r->accent;hi=127; }
    if(f==6) { field=&r->chance;lo=1;hi=8; }
    if(f==7) { field=&r->steps;lo=1;hi=32; }
    uint32_t previous=*field,next;
#if EUCLID_COARSE
    if(f==4||f==5)next=coarse_encoder(&s->encoder[index],*field,value,f==5);
    else
#endif
    next=mc_encoder(&s->encoder[index],*field,value,lo,hi);
    set(s,r,field,next);
    if(f==2&&previous!=*field)s->viewport[k].octave=255;
    if(f==2&&r->degree>last_degree(r->track))set(s,r,&r->degree,last_degree(r->track));
    if(s->mode&&f<2)s->overlay=0;
    else if(s->mode&&timer_host()) { s->overlay=index+1;s->overlay_at=now(); }
    clamp_page(s);ew_silence(s);return 1;
}
void input(uint8_t *leds,uint32_t kind,uint32_t index,uint32_t value) {
    struct ram *s=S;
    (void)leds;
    if(control(s,kind,index,value)) {
        if(s->look)s->touched=1;
        ek_save(s);
    }
}
static void tick(uint32_t pulse) {
    struct ram *s=S;
    s->quiet_frames=0;
    if(pulse%6==3)mc_release(&s->notes);
    if(pulse%6)return;
    uint32_t n=pulse/6;
    if(s->step==255||(n<s->step&&s->step!=15))s->count=n; /* START or restart */
    else s->count+=(n+16-s->step)&15;
    s->step=n;
    for(uint32_t i=0;i<8;i++)if(s->notes.held[i]) {
        mc_release(&s->notes);return; /* missed off: do not add another onset burst */
    }
    uint32_t used=0;
    for(uint32_t i=0;i<4;i++) {
        struct row *r=&s->row[i];
        uint32_t q=s->count%r->steps;
        /* The host is monophonic per track: first active row wins duplicates. */
        if(!(s->on>>i&1u)||!hit(r,q)||(used&(1u<<r->track))||((EW_MB[2]|s->ew_fresh)>>r->track&1u))continue;
        if(r->chance<8&&(mc_random(&s->seed)&7)>=r->chance)continue;
        used|=1u<<r->track;
        mc_note_on(&s->notes,r->track,r->degree,r->accent&&q==first_hit(r)?r->accent:r->velocity);
    }
    s->ew_fresh=0;                                  /* one step has passed: the busy bits are current */
}
void clock(uint8_t *leds,uint32_t event,uint32_t pos,uint32_t bpm) {
    struct ram *s=S;(void)leds;(void)bpm;
    if(event==1)s->step=255;
    if((event==1||event==3)&&mc_abi()>=4) { EW_MB[2]=0;s->ew_fresh=0; }   /* stock Stop released the sequencer notes */
    uint32_t pulse=mc_transport_event(&s->transport,&s->notes,event,pos);
    if(pulse!=MC_NO_PULSE)tick(pulse);
}
uint32_t frame(uint8_t *leds,uint32_t frame_no) {
    struct ram *s=S;(void)frame_no;
    (void)mc_transport_frame(&s->transport); /* cosmetic idle phase only */
    if(s->quiet_frames<120)s->quiet_frames++;
    if(s->quiet_frames==120)mc_release(&s->notes);
    mc_clear(leds);
    uint32_t running=s->transport.external.running&&s->step!=255;
    expire(s);clamp_page(s);
    uint32_t accent[4];
    for(uint32_t i=0;i<4;i++)accent[i]=s->row[i].accent?first_hit(&s->row[i]):255;
    for(uint32_t p=0;p<32;p++) {
        uint32_t i=pad_row(s,p),j=pad_step(s,p);
        const struct row *r=&s->row[i];const uint8_t *c=&s->colour[3*i];
        uint32_t in=j<r->steps,lit=in&&hit(r,j),acc=lit&&j==accent[i],on=s->on>>i&1u;
        uint32_t div=!in?40:lit?2:12;
        if(on&&running&&j==s->count%r->steps)mc_rgb(s->idx,leds,p,127,127,127);
        else if(on&&acc)mc_rgb(s->idx,leds,p,112,48,0);
        else if(!on&&in)mc_rgb(s->idx,leds,p,lit?10:3,lit?10:3,lit?10:3);
        else mc_rgb(s->idx,leds,p,c[0]/div,c[1]/div,c[2]/div);
    }
    /* Ordinary detail feedback only: never encode an operation refusal on pads. */
    if(s->overlay) {
        const struct row *r=&s->row[s->selected];
        uint32_t f=s->overlay-1,n=f==4?r->velocity:f==5?r->accent:f==6?r->chance:r->steps;
        if(f==4||f==5)n=(16*n+126)/127;
        uint32_t key=f==3&&timer_host()?mc_keyboard(r->track):0,note=key?mc_scale_note(r->track,r->degree):MC_NO_NOTE;
        uint32_t rows=key?*(volatile uint8_t *)(key+0x19c):0;
        if(f==3&&(!key||note==MC_NO_NOTE))s->viewport[s->selected].octave=255;
        uint32_t octave=key&&note!=MC_NO_NOTE?keyboard_octave(&s->viewport[s->selected],r->track,key,note+12):3,marker=32;
        if(f==3&&key&&note!=MC_NO_NOTE)for(uint32_t p=0;p<32;p++)
            if(keyboard_position(key,rows,octave,p)==note+12&&(marker==32||p/8>marker/8))marker=p;
        for(uint32_t p=0;p<32;p++) {
            uint32_t rr=0,g=0,b=0;
            if(f==2&&p<8) { rr=g=p==r->track?127:0;b=p==r->track?127:12; }
            if(f==3&&key) {
                uint32_t v=keyboard_position(key,rows,octave,p);
                if(v) {
                    uint32_t rgb=keyboard_colour(r->track,key,rows,octave,p);rr=rgb>>16;g=rgb>>8&255;b=rgb&255;
                    if(p==marker)rr=g=b=127;
                }
            }
            if(f>=4) {
                uint32_t yes=p<n,dim=f==6&&p<8;
                if(f==4&&p<16) { rr=yes?96:8;g=yes?48:4; }
                if(f==5&&p<16) { rr=yes?112:8;g=yes?24:2; }
                if(f==6) { rr=yes?96:dim?8:0;b=yes?48:dim?4:0; }
                if(f==7)rr=g=b=yes?(p+1==n?127:32):0;
            }
            mc_rgb(s->idx,leds,p,rr,g,b);
        }
    }
    /* One sampled stock-tick waveform keeps all refusal lights in phase. */
    uint32_t refusal_pulse=s->ew_flash&&(s->reason==1||s->reason==2)?(now()/125%2?24:96):96;
    for(uint32_t k=0;k<4;k++) {
        uint32_t div=k==s->selected?1:6;
        for(uint32_t c=0;c<3;c++) {
            leds[s->ext[3*k+c]]=s->mode&&k==s->selected?96:s->colour[3*k+c]/div;
            leds[s->ext2[3*k+c]]=s->colour[3*k+c]/(s->on>>k&1u?2:12);
        }
        if(s->ew_flash&&s->reason==2) { leds[s->ext2[3*k]]=refusal_pulse;leds[s->ext2[3*k+1]]=0;leds[s->ext2[3*k+2]]=0; }
        if(s->ew_flash&&(s->reason_rows>>k&1u)) {
            uint32_t pulse=s->reason==1?refusal_pulse:96;
            leds[s->ext[3*k]]=pulse;leds[s->ext[3*k+1]]=s->reason==3?40:0;leds[s->ext[3*k+2]]=0;
        }
    }
    for(uint32_t b=0;b<8;b++) {
        uint32_t rr=0,g=0,bb=0;
        if(!s->mode||b<2) { uint32_t k=s->mode?s->selected:b/2,div=s->last_knob==b+1?1:b%2?4:2;rr=s->colour[3*k]/div;g=s->colour[3*k+1]/div;bb=s->colour[3*k+2]/div; }
        else if(b>=2) {
            if(b==2)bb=80;
            if(b==3) { rr=40;bb=80; }
            if(b==4) { rr=80;g=40; }
            if(b==5) { rr=96;g=20; }
            if(b==6) { rr=80;bb=40; }
            if(b==7)rr=g=bb=64;
        }
        leds[s->ext[12+3*b]]=rr;leds[s->ext[13+3*b]]=g;leds[s->ext[14+3*b]]=bb;
    }
    if(!s->mode) {
        uint32_t pg=s->page[0],last=last_page(s),ph=s->count%s->row[s->selected].steps/8;
        for(uint32_t up=0;up<2;up++) {
            uint32_t at=up?54:51,available=up?pg<last:pg>0;
            if(available) {
                uint32_t cue=running&&(s->on>>s->selected&1u)&&(up?ph>pg:ph<pg);
                leds[at]=leds[up?40:53]=cue?0:12;leds[at+1]=cue?96:12;
            }
        }
    }
    if(s->look==1&&ew_ready(s)&&!s->ew_op)ew_look(s);
    ew_poll(s);
    ew_silence(s);
    ew_leds(s,leds);
    mc_transport_led(&s->transport,leds);
    return 1;
}
