/* Snake: silent ABI-3-compatible SD-card module, board = 8 columns x 4 rows.
 * Tap a pad toward the direction you want to travel, relative to the bright green head:
 * the larger row/column distance wins (ties choose vertical). One turn is queued per
 * movement; direct reversal is ignored. Pad release/pressure and other controls do nothing.
 * Macro 1 clockwise = faster, counterclockwise = slower: 6..48 LED frames per move,
 * default 30. Four encoder counts change one speed step. This follows the host LED frame cadence, not the sequencer tempo.
 * Walls and the snake's body are fatal; moving into its vacating tail is allowed.
 * Orange = food, green = snake. Eating grows the snake. Game over flashes red;
 * count the green pads for the score (foods eaten, 0..29). A full board wins (blue
 * background). After 47 frames (about half a second), press a pad to restart,
 * preserving speed. The game starts paused; the first pad press starts it. Shift + Sample Rec remains
 * the host's exit gesture. No note, parameter, MIDI, storage or firmware services are used.
 * State stays in module RAM 0x100..0x3ff; the host zeroes it before init. */
#include <stdint.h>
#include "module_common.h"
#define MOD_RAM 0x100u
#define LEDS 224u
#define EV_PAD 1u
#define EV_ENCODER 5u
struct ram {
    uint8_t idx[96];
    uint8_t body[32];                 /* head first */
    uint32_t rng;
    uint8_t len, dir, next_dir, queued; /* right, down, left, up */
    uint8_t food, period, ticks, dead;  /* dead: 0 playing, 1 lost, 2 won */
    uint8_t paused, cooldown;
    int8_t encoder;
};
_Static_assert(sizeof(struct ram) <= 0x300u, "module RAM");
static void table(volatile uint8_t *t)
{
    t[0] = 37; t[1] = 38; t[2] = 39;   /* pad_r0c0 */
    t[3] = 24; t[4] = 25; t[5] = 26;   /* pad_r0c1 */
    t[6] = 100; t[7] = 101; t[8] = 102;   /* pad_r0c2 */
    t[9] = 103; t[10] = 88; t[11] = 89;   /* pad_r0c3 */
    t[12] = 156; t[13] = 157; t[14] = 158;   /* pad_r0c4 */
    t[15] = 159; t[16] = 144; t[17] = 145;   /* pad_r0c5 */
    t[18] = 202; t[19] = 203; t[20] = 204;   /* pad_r0c6 */
    t[21] = 205; t[22] = 206; t[23] = 207;   /* pad_r0c7 */
    t[24] = 30; t[25] = 31; t[26] = 16;   /* pad_r1c0 */
    t[27] = 90; t[28] = 91; t[29] = 92;   /* pad_r1c1 */
    t[30] = 93; t[31] = 94; t[32] = 95;   /* pad_r1c2 */
    t[33] = 80; t[34] = 81; t[35] = 82;   /* pad_r1c3 */
    t[36] = 146; t[37] = 147; t[38] = 148;   /* pad_r1c4 */
    t[39] = 149; t[40] = 150; t[41] = 151;   /* pad_r1c5 */
    t[42] = 136; t[43] = 137; t[44] = 138;   /* pad_r1c6 */
    t[45] = 195; t[46] = 196; t[47] = 197;   /* pad_r1c7 */
    t[48] = 20; t[49] = 21; t[50] = 22;   /* pad_r2c0 */
    t[51] = 83; t[52] = 84; t[53] = 85;   /* pad_r2c1 */
    t[54] = 86; t[55] = 87; t[56] = 72;   /* pad_r2c2 */
    t[57] = 73; t[58] = 74; t[59] = 75;   /* pad_r2c3 */
    t[60] = 139; t[61] = 140; t[62] = 141;   /* pad_r2c4 */
    t[63] = 142; t[64] = 143; t[65] = 128;   /* pad_r2c5 */
    t[66] = 129; t[67] = 130; t[68] = 131;   /* pad_r2c6 */
    t[69] = 185; t[70] = 186; t[71] = 187;   /* pad_r2c7 */
    t[72] = 10; t[73] = 11; t[74] = 12;   /* pad_r3c0 */
    t[75] = 76; t[76] = 77; t[77] = 78;   /* pad_r3c1 */
    t[78] = 79; t[79] = 64; t[80] = 65;   /* pad_r3c2 */
    t[81] = 66; t[82] = 67; t[83] = 68;   /* pad_r3c3 */
    t[84] = 132; t[85] = 133; t[86] = 134;   /* pad_r3c4 */
    t[87] = 135; t[88] = 120; t[89] = 121;   /* pad_r3c5 */
    t[90] = 122; t[91] = 123; t[92] = 124;   /* pad_r3c6 */
    t[93] = 191; t[94] = 176; t[95] = 177;   /* pad_r3c7 */
}

static uint32_t random_word(struct ram *r)
{
    uint32_t x = r->rng ? r->rng : 0x2545F491u;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return r->rng = x;
}

static void food(struct ram *r)
{
    if (r->len == 32) { r->food = 255; r->dead = 2; return; }
    uint32_t pick = random_word(r) % (32u - r->len);
    for (uint32_t p = 0; p < 32; p++) {
        uint32_t used = 0;
        for (uint32_t i = 0; i < r->len; i++)
            used |= r->body[i] == p;
        if (!used) {
            if (!pick) { r->food = (uint8_t)p; return; }
            pick--;
        }
    }
}

static void reset(struct ram *r)
{
    r->body[0] = 8; r->body[1] = 16; r->body[2] = 24;
    r->len = 3; r->dir = r->next_dir = 0; r->queued = 0;
    r->ticks = 0; r->dead = 0; r->paused = 1; r->cooldown = 0;
    food(r);
}

static void rgb(struct ram *r, uint8_t *leds, uint32_t p, uint32_t red, uint32_t green, uint32_t blue)
{
    leds[r->idx[p * 3]] = (uint8_t)red;
    leds[r->idx[p * 3 + 1]] = (uint8_t)green;
    leds[r->idx[p * 3 + 2]] = (uint8_t)blue;
}

static void buttons(volatile uint8_t *l)      /* non-pad button LEDs, docs/led-map.json */
{
    l[23] = 16; l[8] = 16; l[9] = 16;   /* btn_PatternSettings */
    l[17] = 16; l[18] = 16; l[19] = 16;   /* btn_Gate */
    l[27] = 16; l[28] = 16; l[29] = 16;   /* btn_Velocity */
    l[47] = 16; l[32] = 16; l[33] = 16;   /* btn_2 */
    l[34] = 16; l[35] = 16; l[36] = 16;   /* btn_Note */
    l[54] = 16; l[55] = 16; l[40] = 16;   /* btn_Up */
    l[41] = 16; l[42] = 16; l[43] = 16;   /* btn_Sample */
    l[44] = 16; l[45] = 16; l[46] = 16;   /* btn_1 */
    l[48] = 16; l[49] = 16; l[50] = 16;   /* btn_SampleRec */
    l[51] = 16; l[52] = 16; l[53] = 16;   /* btn_Down */
    l[110] = 16; l[111] = 16; l[96] = 16;   /* btn_3 */
    l[97] = 16; l[98] = 16; l[99] = 16;   /* btn_4 */
    l[104] = 16; l[105] = 16; l[106] = 16;   /* btn_1-16 */
    l[107] = 16; l[108] = 16; l[109] = 16;   /* btn_Tempo */
    l[166] = 16; l[167] = 16; l[152] = 16;   /* btn_5 */
    l[153] = 16; l[154] = 16; l[155] = 16;   /* btn_6 */
    l[160] = 16; l[161] = 16; l[162] = 16;   /* btn_Clear */
    l[163] = 16; l[164] = 16; l[165] = 16;   /* btn_Duplicate */
    l[178] = 16; l[179] = 16; l[180] = 16;   /* btn_Play */
    l[198] = 16; l[199] = 16; l[184] = 16;   /* btn_FX */
    l[188] = 16; l[189] = 16; l[190] = 16;   /* btn_Rec */
    l[192] = 16; l[193] = 16; l[194] = 16;   /* btn_Mixer */
    l[215] = 16; l[200] = 16; l[201] = 16;   /* btn_Patterns */
    l[222] = 16; l[223] = 16; l[208] = 16;   /* btn_Shift */
    l[209] = 16; l[210] = 16; l[211] = 16;   /* btn_7 */
    l[212] = 16; l[213] = 16; l[214] = 16;   /* btn_8 */
    l[216] = 16; l[217] = 16; l[218] = 16;   /* btn_Save */
    l[219] = 16; l[220] = 16; l[221] = 16;   /* btn_Projects */
}


static void draw(struct ram *r, uint8_t *leds, uint32_t frame_no)
{
    for (uint32_t i = 0; i < LEDS; i++) leds[i] = 0;
    buttons(leds);
    if (r->dead) {
        uint32_t score = r->len - 3u, pulse = (frame_no >> 4 & 1u) ? 0x80u : 0x20u;
        for (uint32_t p = 0; p < 32; p++)
            rgb(r, leds, p, p < score || r->dead == 2 ? 0 : pulse,
                p < score ? 0xFEu : 0, p >= score && r->dead == 2 ? pulse : 0);
    } else {
        rgb(r, leds, r->food, 0xFE, 0x60, 0);
        for (uint32_t i = 0; i < r->len; i++)
            rgb(r, leds, r->body[i], 0, i ? 0x40 : (r->paused && ((frame_no >> 5) & 1) ? 0x60 : 0xFE), 0);
    }
    leds[1] = (uint8_t)(0x20u + (48u - r->period) * 4u); /* macro 1 green: speed */
}

void init(uint8_t *leds)
{
    struct ram *r = (struct ram *)MOD_RAM;
    table(r->idx);
    r->rng = 0x2545F491u;
    r->period = 30; r->encoder = 0;
    reset(r);
    draw(r, leds, 0);
}

void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = (struct ram *)MOD_RAM;
    if (kind == EV_ENCODER && index == 0) {
        uint32_t speed = mc_encoder(&r->encoder, 48-r->period, value, 0, 42);
        r->period = 48-speed;
    } else if (kind == EV_PAD && index < 32 && (value >> 8) == 1) {
        if (r->dead) { if (r->cooldown) return; reset(r); draw(r, leds, 0); return; }
        r->paused = 0;
        if (r->queued) return;
        int32_t dx = (int32_t)(index & 7u) - (int32_t)(r->body[0] & 7u);
        int32_t dy = (int32_t)(index >> 3) - (int32_t)(r->body[0] >> 3);
        int32_t ax = dx < 0 ? -dx : dx, ay = dy < 0 ? -dy : dy;
        if (!ax && !ay) return;
        uint32_t dir = ax > ay ? (dx > 0 ? 0u : 2u) : (dy > 0 ? 1u : 3u);
        if (dir != (r->dir ^ 2u)) { r->next_dir = (uint8_t)dir; r->queued = 1; }
    }
    (void)leds;
}

static void move(struct ram *r)
{
    r->dir = r->next_dir; r->queued = 0;
    uint32_t h = r->body[0], x = h & 7u, y = h >> 3;
    if ((r->dir == 0 && x == 7) || (r->dir == 2 && x == 0) ||
        (r->dir == 1 && y == 3) || (r->dir == 3 && y == 0)) { r->dead = 1; return; }
    uint32_t next = r->dir == 0 ? h + 1 : r->dir == 1 ? h + 8 : r->dir == 2 ? h - 1 : h - 8;
    uint32_t grow = next == r->food;
    for (uint32_t i = 0; i < r->len - (grow ? 0u : 1u); i++)
        if (r->body[i] == next) { r->dead = 1; return; }
    if (grow) r->len++;
    for (uint32_t i = r->len - 1; i; i--) r->body[i] = r->body[i - 1];
    r->body[0] = (uint8_t)next;
    if (grow) food(r);
}

uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = (struct ram *)MOD_RAM;
    if (r->dead && r->cooldown) r->cooldown--;
    if (!r->dead && !r->paused && ++r->ticks >= r->period) {
        r->ticks = 0; move(r); if (r->dead) r->cooldown = 47;
    }
    draw(r, leds, frame_no);
    return 1;
}
