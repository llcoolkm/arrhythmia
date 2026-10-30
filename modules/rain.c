/* rain.mod: falling scale notes, ABI 3 / CRM2. Press Play to run.
 * Play/Stop remain stock transport controls. Only real transport pulses make
 * rain fall or sound notes. Before START and after STOP, a dim breathing hint
 * shows that the module is waiting. STOP clears drops and releases notes.
 * Pads add drops. Macros 1..4: spawn interval 6/12/24/48 pulses, scale range
 * 1..24 degrees, fall interval 3/6/12/24 pulses, target track 1..8.
 * Four encoder counts make one step. Multiple landings coalesce to one note:
 * leftmost column spans selected degrees in the actual track scale, pitch >=60.
 * Gate is half a gravity tick, at least six LED frames (~64 ms at 94 fps).
 * Further landings during that gate flash but do not retrigger the note.
 * START/STOP/track changes release notes. Jumps skip missed updates; duplicate
 * pulses ignored. Frame drives note-off timers and idle visuals only.
 * Shift+Sample Rec exits. No raw MIDI, recording or firmware/device writes.
 */
#include <stdint.h>
#include "module_common.h"
struct ram {
    uint8_t idx[96];
    uint32_t drops, seed;
    struct mc_notes notes;
    struct mc_transport transport;
    uint8_t rate, range, speed, track, flash, brightness, gate, idle;
    int8_t encoder[4];
};
_Static_assert(sizeof(struct ram) <= 768, "module RAM");
#define R ((struct ram *)MC_RAM)
void init(uint8_t *leds)
{
    struct ram *r = R;
    mc_led_table(r->idx);
    r->drops = 0; r->seed = 0x22ace197;
    for (uint32_t t = 0; t < 8; ++t) r->notes.held[t] = r->notes.note[t] = 0;
    mc_transport_init(&r->transport);
    r->gate = r->idle = 0;
    for (uint32_t i = 0; i < 4; ++i) r->encoder[i] = 0;
    r->rate = r->speed = 1; r->range = 8; r->track = 0;
    r->flash = 255; r->brightness = 0;
    mc_clear(leds);
}
static void tick(struct ram *r, uint32_t pulse, uint32_t bpm)
{
    if (pulse % (3u << r->speed) == 0) {
        uint32_t landed = r->drops >> 24;
        r->drops <<= 8;
        if (landed) {
            uint32_t col = 0;
            while (!((landed >> col) & 1)) ++col;
            r->flash = col; r->brightness = 254;
            if (!r->gate && mc_note_on(&r->notes, r->track, col * (r->range - 1) / 7, 100)) {
                if (bpm < 40 || bpm > 240) bpm = 120;
                uint32_t frames = ((3u << r->speed) * 94u * 60u + bpm * 48u - 1) / (bpm * 48u);
                r->gate = frames < 6 ? 6 : frames;
            }
        }
    }
    if (pulse % (6u << r->rate) == 0) r->drops |= 1u << (mc_random(&r->seed) & 7);
}
void clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm)
{
    struct ram *r = R;
    uint32_t pulse = mc_transport_event(&r->transport, &r->notes, event, pos);
    if (event == 1 || event == 3) r->gate = 0;
    if (event == 3) {
        r->drops = 0; r->flash = 255; r->brightness = 0; mc_clear(leds);
    }
    if (pulse != MC_NO_PULSE) tick(r, pulse, bpm);
}
void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = R;
    if (kind == 1 && index < 32 && (value & 256)) r->drops |= 1u << index;
    if (kind == 5) {
        if (index == 0) r->rate = mc_encoder(&r->encoder[0], r->rate, value, 0, 3);
        else if (index == 1) r->range = mc_encoder(&r->encoder[1], r->range, value, 1, 24);
        else if (index == 2) r->speed = mc_encoder(&r->encoder[2], r->speed, value, 0, 3);
        else if (index == 3) {
            uint32_t before = r->track;
            r->track = mc_encoder(&r->encoder[3], r->track, value, 0, 7);
            if (r->track != before) { mc_release(&r->notes); r->gate = 0; }
        }
    }
    (void)leds;
}
uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = R;
    if (r->gate && !--r->gate) mc_release(&r->notes);
    mc_transport_frame(&r->transport); /* idle indicator only, never a pulse */
    mc_clear(leds);
    for (uint32_t p = 0; p < 32; ++p) {
        uint32_t drop = (r->drops >> p) & 1;
        uint32_t flash = p / 8 == 3 && p % 8 == r->flash ? r->brightness : 0;
        uint32_t phase = r->transport.blink < 94 ? r->transport.blink : 188 - r->transport.blink;
        uint32_t hint = !r->drops && p == 27 ? (4 + phase / 4) & 254 : 0;
        mc_rgb(r->idx, leds, p, flash, drop ? 90 : flash, drop ? 254 : flash > hint ? flash : hint);
    }
    r->brightness = r->brightness > 8 ? r->brightness - 8 : 0;
    if (++r->idle >= 192) r->idle = 0;
    mc_transport_led(&r->transport, leds);
    (void)frame_no;
    return 1;
}
