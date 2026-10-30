/* pulse.mod: silent light show, ABI 3 / CRM2. No input callback: Play, Stop,
 * pads, knobs and Tempo stay with the stock interface for live performance.
 * Press Play to run; stopped transport shows only the dim idle breath.
 * Pattern cycles every four bars, colour every bar. Clock positions select
 * whole-grid beat flash / eighth-note column chase / sixteenth-note row wave.
 * While stopped a dim two-second breath makes the module visibly alive.
 * START paints midbar, STOP returns to breath, duplicate pulses do nothing.
 * Shift+Sample Rec exits. No host services or audio output are needed.
 */
#include <stdint.h>
#include "module_common.h"

struct ram {
    uint8_t idx[96];
    struct mc_clock transport;
    uint8_t pattern, intensity, hue, bars, idle;
    uint8_t glow[32];
};
_Static_assert(sizeof(struct ram) <= 768, "module RAM");
#define R ((struct ram *)0x100u)

static void light(struct ram *r)
{
    uint32_t pulse = r->transport.pulse;
    for (uint32_t p = 0; p < 32; ++p) {
        uint32_t on = r->pattern == 0 ||
            (r->pattern == 1 && p % 8 == (pulse / 12) % 8) ||
            (r->pattern == 2 && p / 8 == (pulse / 6) % 4);
        r->glow[p] = on ? 254 : 0;
    }
}

void init(uint8_t *leds)
{
    struct ram *r = R;
    mc_led_table(r->idx);
    r->transport.running = r->transport.seen = r->transport.pulse = 0;
    r->pattern = 0; r->intensity = 192; r->hue = r->bars = r->idle = 0;
    for (uint32_t p = 0; p < 32; ++p) r->glow[p] = 0;
    mc_clear(leds);
}

void clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm)
{
    struct ram *r = R;
    uint32_t previous = r->transport.pulse;
    uint32_t fresh = mc_clock_event(&r->transport, 0, event, pos);
    if (event == 3) {
        for (uint32_t p = 0; p < 32; ++p) r->glow[p] = 0;
        mc_clear(leds);
    } else if (event == 1 && (pos & 255) < 96) {
        r->transport.pulse = pos & 255;
        light(r);
    } else if (fresh) {
        uint32_t pulse = r->transport.pulse;
        if (pulse < previous) {
            r->bars = (r->bars + 1) % 24;
            r->hue = r->bars % 6; r->pattern = r->bars / 4 % 3;
        }
        if (r->pattern || pulse % 24 == 0) light(r);
    }
    (void)bpm;
}

uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = R;
    mc_clear(leds);
    for (uint32_t p = 0; p < 32; ++p) {
        uint8_t v = ((uint32_t)r->glow[p] * r->intensity / 254) & 254;
        if (!r->transport.running) {
            uint32_t phase = r->idle < 94 ? r->idle : 188 - r->idle;
            v = (4 + phase / 4) & 254;
        }
        uint8_t red = r->hue == 0 || r->hue == 1 || r->hue == 5 ? v : 0;
        uint8_t green = r->hue == 1 || r->hue == 2 || r->hue == 3 ? v : 0;
        uint8_t blue = r->hue == 3 || r->hue == 4 || r->hue == 5 ? v : 0;
        mc_rgb(r->idx, leds, p, red, green, blue);
        r->glow[p] = r->glow[p] > 8 ? r->glow[p] - 8 : 0;
    }
    r->idle = (r->idle + 1) % 188;
    (void)frame_no;
    return 1;
}
