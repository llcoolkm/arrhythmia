/* life.mod: 8x4 toroidal Conway Life, B3/S23, ABI 3 / CRM2.
 * Press Play to run. Play/Stop remain stock transport controls. Idle before
 * START and after STOP; dim breathing lights show that the module is waiting.
 * Only real transport pulses evolve cells or sound notes.
 * Pads toggle seeds. Macro 1 generation period 6/12/24/48 pulses; macro 2 density
 * 1..75 percent, randomised once after the gesture settles; macro 3 track 1..8.
 * Four encoder counts make one step. Empty grids reseed immediately and static
 * grids after four generations. One living cell round-robin sounds per generation
 * in the actual target scale, starting at pitch >=60; gate is half a generation.
 * Duplicate pulses ignored; jumps skip missed steps, never replay a burst.
 * Shift+Sample Rec exits. No firmware/device writes; missing keyboard is silent.
 */
#include <stdint.h>
#include "module_common.h"
struct ram {
    uint8_t idx[96];
    uint32_t cells, seed;
    struct mc_notes notes;
    struct mc_transport transport;
    uint8_t rate, density, track, cursor, playing, stable, pending;
    int8_t encoder[3];
};
_Static_assert(sizeof(struct ram) <= 768, "module RAM");
#define R ((struct ram *)MC_RAM)
static void randomise(struct ram *r)
{
    r->cells = 0;
    for (uint32_t p = 0; p < 32; ++p)
        if (mc_random(&r->seed) % 100 < r->density) r->cells |= 1u << p;
    if (!r->cells) r->cells = (1u << 10) | (1u << 11) | (1u << 12);
    r->stable = 0;
}
static void evolve(struct ram *r)
{
    uint32_t next = 0;
    for (uint32_t p = 0; p < 32; ++p) {
        uint32_t n = 0;
        for (int32_t dy = -1; dy <= 1; ++dy)
            for (int32_t dx = -1; dx <= 1; ++dx) {
                if (!dx && !dy) continue;
                uint32_t q = (((p / 8 + dy) & 3) * 8) + ((p % 8 + dx) & 7);
                n += (r->cells >> q) & 1;
            }
        if (n == 3 || (n == 2 && ((r->cells >> p) & 1))) next |= 1u << p;
    }
    r->stable = next == r->cells ? r->stable + 1 : 0;
    r->cells = next;
    if (!next || r->stable >= 4) randomise(r);
}
static void sound(struct ram *r)
{
    r->playing = 255;
    for (uint32_t i = 0; i < 32; ++i) {
        uint32_t p = (r->cursor + i) & 31;
        if (!((r->cells >> p) & 1)) continue;
        r->cursor = (p + 1) & 31;
        if (mc_note_on(&r->notes, r->track, p, 100)) r->playing = p;
        return;
    }
}
void init(uint8_t *leds)
{
    struct ram *r = R;
    mc_led_table(r->idx);
    r->cells = (1u << 10) | (1u << 11) | (1u << 12);
    r->seed = 0x1937ace1;
    for (uint32_t t = 0; t < 8; ++t) r->notes.held[t] = r->notes.note[t] = 0;
    mc_transport_init(&r->transport);
    r->stable = r->pending = 0;
    for (uint32_t i = 0; i < 3; ++i) r->encoder[i] = 0;
    r->rate = 0; r->density = 30; r->track = r->cursor = 0; r->playing = 255;
    mc_clear(leds);
}
static void tick(struct ram *r, uint32_t pulse)
{
    uint32_t period = 6u << r->rate, phase = pulse % period;
    if (phase == 0) {
        mc_release(&r->notes); evolve(r); sound(r);
    } else if (phase >= period / 2) {
        mc_release(&r->notes); r->playing = 255;
    }
}
void clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm)
{
    struct ram *r = R;
    uint32_t pulse = mc_transport_event(&r->transport, &r->notes, event, pos);
    if (event == 1 || event == 3) r->playing = 255;
    if (pulse != MC_NO_PULSE) tick(r, pulse);
    (void)leds; (void)bpm;
}
void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = R;
    if (kind == 1 && index < 32 && (value & 256)) r->cells ^= 1u << index;
    if (kind == 5) {
        if (index == 0) {
            uint32_t before = r->rate;
            r->rate = mc_encoder(&r->encoder[0], r->rate, value, 0, 3);
            if (r->rate != before) { mc_release(&r->notes); r->playing = 255; }
        } else if (index == 1 && (int16_t)value) {
            uint32_t before = r->density;
            r->density = mc_encoder(&r->encoder[1], r->density, value, 1, 75);
            if (r->density != before) r->pending = 12;
        } else if (index == 2) {
            uint32_t before = r->track;
            r->track = mc_encoder(&r->encoder[2], r->track, value, 0, 7);
            if (r->track != before) { mc_release(&r->notes); r->playing = 255; }
        }
    }
    (void)leds;
}
uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = R;
    if (r->pending && !--r->pending) randomise(r);
    mc_transport_frame(&r->transport); /* idle indicator only, never a pulse */
    mc_clear(leds);
    for (uint32_t p = 0; p < 32; ++p) {
        uint32_t alive = (r->cells >> p) & 1, active = p == r->playing;
        uint32_t phase = r->transport.blink < 94 ? r->transport.blink : 188 - r->transport.blink;
        uint32_t level = r->transport.external.running ? 100 : (4 + phase / 4) & 254;
        mc_rgb(r->idx, leds, p, active ? 254 : 0, alive ? level : 0, active ? 254 : 0);
    }
    mc_transport_led(&r->transport, leds);
    (void)frame_no;
    return 1;
}
