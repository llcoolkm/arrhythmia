/* Module: stars. Sparse twinkles that swell up and fade out slowly, instead of glitter's instant flash.
 * Without the LED map the colour channels are still independent, so stars are tinted until the map exists.
 * RAM at MOD_RAM: u32 rng, then one phase byte per LED (0 = dark, 1..127 rising, 128..255 falling). */
#include <stdint.h>

#define MOD_RAM 0x00000100u
#define LEDS    224u

struct ram {
    uint32_t rng;
    uint8_t phase[LEDS];
};

static uint32_t rnd(struct ram *r)
{
    uint32_t x = r->rng ? r->rng : 0x2545F491u;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return r->rng = x;
}

void init(uint8_t *leds)
{
    for (uint32_t i = 0; i < LEDS; i++)
        leds[i] = 0;
}

uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = (struct ram *)MOD_RAM;
    if ((rnd(r) & 7) == 0) {                        /* a new star every ~8 frames */
        uint32_t i = (r->rng >> 8) % LEDS;
        if (!r->phase[i])
            r->phase[i] = 1;
    }
    for (uint32_t i = 0; i < LEDS; i++) {
        uint32_t p = r->phase[i];
        if (!p)
            continue;
        p += 2;
        if (p > 255)
            p = 0;
        r->phase[i] = (uint8_t)p;
        uint32_t level = p < 128 ? p : 255 - p;      /* triangle 0..127..0 */
        leds[i] = (uint8_t)((level * level >> 7) << 1);   /* squared for a softer start */
    }
    (void)frame_no;
    return 1;
}
