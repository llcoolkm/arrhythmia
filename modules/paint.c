/* Module: paint. Interactive (needs devkit-mode): pads light where pressed, in a hue chosen with macro 1, brightness
 * from the press velocity, then fade; macro 2 sets the trail length (clockwise = longer), from about 0.5 s to about
 * 16 s in 28 steps of x8/7 at the measured ~94 LED frames/s. Shows the input entry of the module ABI.
 * Pad LED indices are docs/led-map.json, written into module RAM by init with plain stores (no .rodata).
 * The 28 non-pad buttons stay dimly lit (white, DIM), set in init and kept every frame, so the unit never looks dead.
 * Sound (needs devkit-play, label 6008): a pad press plays track = pad column (0..7) at note 60 (unshifted sample),
 * velocity from the press halved as the stock UI does; its release sends the note off (one-shot samples play out).
 * Notes go through the host table's note(); without the table paint stays silent.
 * RAM at MOD_RAM: struct ram below. */
#include <stdint.h>

#define MOD_RAM 0x00000100u
#define LEDS    224u
#define EV_PAD      1u
#define EV_ENCODER  5u
#define TRAILS      28u         /* trail positions; 0 = shortest */
#define DIM         0x10u       /* button brightness (bits 1..7 are the LED bit planes) */
#define HOST_TABLE  0x0806A570u /* devkit-play host table: magic, abi, note(), built-in module */
#define HOST_MAGIC  0x31485243u /* "CRH1" */

typedef uint32_t (*note_fn)(uint32_t track, uint32_t note, uint32_t velocity);

struct ram {
    uint8_t idx[96];            /* R, G, B byte index per pad (pad = row * 8 + column, docs/input-map.json) */
    uint16_t level[32];         /* brightness 0..127 in 8.8 fixed point */
    uint8_t rgb[96];            /* colour per pad, 0..255 per channel */
    uint16_t step;              /* level lost per frame, 8.8 */
    uint8_t hue, trail, pad;
};

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

static void buttons(volatile uint8_t *l)      /* non-pad button LEDs, docs/led-map.json */
{
    l[23] = DIM; l[8] = DIM; l[9] = DIM;   /* btn_PatternSettings */
    l[17] = DIM; l[18] = DIM; l[19] = DIM;   /* btn_Gate */
    l[27] = DIM; l[28] = DIM; l[29] = DIM;   /* btn_Velocity */
    l[47] = DIM; l[32] = DIM; l[33] = DIM;   /* btn_2 */
    l[34] = DIM; l[35] = DIM; l[36] = DIM;   /* btn_Note */
    l[54] = DIM; l[55] = DIM; l[40] = DIM;   /* btn_Up */
    l[41] = DIM; l[42] = DIM; l[43] = DIM;   /* btn_Sample */
    l[44] = DIM; l[45] = DIM; l[46] = DIM;   /* btn_1 */
    l[48] = DIM; l[49] = DIM; l[50] = DIM;   /* btn_SampleRec */
    l[51] = DIM; l[52] = DIM; l[53] = DIM;   /* btn_Down */
    l[110] = DIM; l[111] = DIM; l[96] = DIM;   /* btn_3 */
    l[97] = DIM; l[98] = DIM; l[99] = DIM;   /* btn_4 */
    l[104] = DIM; l[105] = DIM; l[106] = DIM;   /* btn_1-16 */
    l[107] = DIM; l[108] = DIM; l[109] = DIM;   /* btn_Tempo */
    l[166] = DIM; l[167] = DIM; l[152] = DIM;   /* btn_5 */
    l[153] = DIM; l[154] = DIM; l[155] = DIM;   /* btn_6 */
    l[160] = DIM; l[161] = DIM; l[162] = DIM;   /* btn_Clear */
    l[163] = DIM; l[164] = DIM; l[165] = DIM;   /* btn_Duplicate */
    l[178] = DIM; l[179] = DIM; l[180] = DIM;   /* btn_Play */
    l[198] = DIM; l[199] = DIM; l[184] = DIM;   /* btn_FX */
    l[188] = DIM; l[189] = DIM; l[190] = DIM;   /* btn_Rec */
    l[192] = DIM; l[193] = DIM; l[194] = DIM;   /* btn_Mixer */
    l[215] = DIM; l[200] = DIM; l[201] = DIM;   /* btn_Patterns */
    l[222] = DIM; l[223] = DIM; l[208] = DIM;   /* btn_Shift */
    l[209] = DIM; l[210] = DIM; l[211] = DIM;   /* btn_7 */
    l[212] = DIM; l[213] = DIM; l[214] = DIM;   /* btn_8 */
    l[216] = DIM; l[217] = DIM; l[218] = DIM;   /* btn_Save */
    l[219] = DIM; l[220] = DIM; l[221] = DIM;   /* btn_Projects */
}

static void play(uint32_t track, uint32_t velocity)     /* velocity 0 = note off */
{
    const volatile uint32_t *h = (const volatile uint32_t *)HOST_TABLE;
    if (h[0] == HOST_MAGIC && h[1] >= 1)
        ((note_fn)h[2])(track, 60, velocity);
}

static void set_step(struct ram *r)            /* 650 * (7/8)^trail: 0.5 s at trail 0 */
{
    uint32_t st = 650;
    for (uint32_t i = 0; i < r->trail; i++)
        st -= st >> 3;
    r->step = (uint16_t)st;
}

void init(uint8_t *leds)
{
    struct ram *r = (struct ram *)MOD_RAM;
    table(r->idx);
    r->trail = 8;                                   /* about 1.5 s */
    set_step(r);
    for (uint32_t i = 0; i < LEDS; i++)
        leds[i] = 0;
    buttons(leds);
}

static void hue_rgb(uint32_t h, uint8_t *c)        /* h 0..191, six sectors of 32 */
{
    uint32_t sector = h >> 5, x = (h & 31) * 8;
    uint32_t r = 0, g = 0, b = 0;
    if (sector == 0) { r = 255; g = x; }
    else if (sector == 1) { r = 255 - x; g = 255; }
    else if (sector == 2) { g = 255; b = x; }
    else if (sector == 3) { g = 255 - x; b = 255; }
    else if (sector == 4) { r = x; b = 255; }
    else { r = 255; b = 255 - x; }
    c[0] = (uint8_t)r; c[1] = (uint8_t)g; c[2] = (uint8_t)b;
}

void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = (struct ram *)MOD_RAM;
    if (kind == EV_PAD && index < 32 && (value >> 8)) {
        uint32_t v = value & 0xFF;
        r->level[index] = (uint16_t)((v < 64 ? 64 : v >> 1) << 8);
        hue_rgb(r->hue, &r->rgb[3 * index]);
        play(index & 7, v >= 2 ? v >> 1 : 1);
    } else if (kind == EV_PAD && index < 32)
        play(index & 7, 0);
    else if (kind == EV_ENCODER) {
        int32_t d = (int16_t)value;
        if (index == 0)
            r->hue = (uint8_t)(((int32_t)r->hue + d * 2 + 192 * 4) % 192);
        else if (index == 1) {
            int32_t t = (int32_t)r->trail + d;    /* clockwise = longer trail */
            r->trail = (uint8_t)(t < 0 ? 0 : t > (int32_t)TRAILS - 1 ? TRAILS - 1 : t);
            set_step(r);
        }
    }
    (void)leds;
}

uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = (struct ram *)MOD_RAM;
    for (uint32_t p = 0; p < 32; p++) {
        uint32_t l = r->level[p];
        for (uint32_t k = 0; k < 3; k++)
            leds[r->idx[3 * p + k]] = (uint8_t)(((r->rgb[3 * p + k] * (l >> 8)) >> 8) & 0xFE);
        r->level[p] = (uint16_t)(l > r->step ? l - r->step : 0);
    }
    buttons(leds);
    (void)frame_no;
    return 1;
}
