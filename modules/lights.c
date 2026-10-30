/* lights.mod: silent Lights Out on the 8 x 4 pad grid (ABI 3).
 * Press a pad to flip it and its orthogonal neighbours; edges do not wrap.
 * Clear starts a new, nonempty puzzle made by legal moves from the solved board.
 * Hold Rec to see the decimal move count, 00..99. Red Rec means 99 or more.
 * A win alternates green pads and the count; it persists until Clear.
 * Launch-frame timing varies the puzzle; presses flash white for six frames.
 * Pad releases, pressure, other buttons and knobs are ignored. The host's
 * Shift + SampleRec exit remains available. No notes or peripheral I/O.
 */
#include <stdint.h>
#include "module_common.h"
#define MOD_RAM 0x100u
struct ram {
    uint8_t idx[96];
    uint32_t board, seed, scramble;
    uint8_t moves, overflow, won, show_count;
    uint32_t last_frame;
    uint8_t seeded, flash_pad, flash_left;
};
_Static_assert(sizeof(struct ram) <= 0x300, "module RAM");

static uint32_t mask(uint32_t p)
{
    uint32_t m = 1u << p;
    if ((p & 7) != 0) m |= 1u << (p - 1);
    if ((p & 7) != 7) m |= 1u << (p + 1);
    if (p >= 8) m |= 1u << (p - 8);
    if (p < 24) m |= 1u << (p + 8);
    return m;
}
static void restart(struct ram *r)
{
    r->board = 0; r->scramble = 0;
    for (uint32_t i = 0; i < 12; i++) {
        uint32_t p = mc_random(&r->seed) & 31;
        r->board ^= mask(p); r->scramble ^= 1u << p;
    }
    if (!r->board) { r->board = mask(0); r->scramble ^= 1; }
    r->moves = 0; r->overflow = 0; r->won = 0; r->show_count = 0;
}
/* Three columns by four rows, row-major bits; constants stay in code. */
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
void init(uint8_t *leds)
{
    struct ram *r = (struct ram *)MOD_RAM;
    mc_led_table(r->idx);
    r->seed = 0x4C494748u; r->seeded = 0; r->last_frame = 0;
    r->flash_left = 0; r->flash_pad = 0;
    restart(r);
    mc_clear(leds);
}
void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = (struct ram *)MOD_RAM;
    if ((kind==1 && index<32 && (value>>8)==1) ||
        (kind==2 && index==23 && value==1)) r->seeded=1;
    if (kind == 2 && index == 23 && value == 1) {
        r->seed ^= r->last_frame; restart(r); r->flash_left = 0;
    }
    else if (kind == 2 && index == 16 && value <= 1) r->show_count = value;
    else if (kind == 1 && index < 32 && (value >> 8) == 1 && !r->won) {
        r->flash_pad = index; r->flash_left = 6;
        r->board ^= mask(index);
        if (r->moves < 99) r->moves++;
        if (r->moves == 99) r->overflow = 1;
        if (!r->board) r->won = 1;
    }
    (void)leds;
}
uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = (struct ram *)MOD_RAM;
    r->last_frame = frame_no;
    if (!r->seeded) { r->seed ^= frame_no; restart(r); r->seeded = 1; }
    mc_clear(leds);
    uint32_t count = r->show_count || (r->won && ((frame_no >> 5) & 1));
    uint32_t tens = digit(r->moves / 10), units = digit(r->moves % 10);
    for (uint32_t p = 0; p < 32; p++) {
        uint32_t on;
        if (count) {
            uint32_t x = p & 7, y = p >> 3;
            on = x < 3 ? ((tens >> (y * 3 + x)) & 1) :
                 x >= 4 && x < 7 ? ((units >> (y * 3 + x - 4)) & 1) : 0;
        } else on = r->won || ((r->board >> p) & 1);
        if (on) mc_rgb(r->idx, leds, p, count || r->won ? 0 : 254, count || r->won ? 254 : 96, 0);
    }
    if (r->flash_left) {
        mc_rgb(r->idx, leds, r->flash_pad, 254, 254, 254); r->flash_left--;
    }
    leds[161] = 64; /* Clear: new puzzle */
    leds[r->overflow ? 188 : 189] = 32; /* Rec: count */
    return 1;
}
