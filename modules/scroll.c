/* scroll.mod: a text scroller on the 8 x 4 pads, right to left, one column per step. Silent. ABI 3 / CRM2.
 * The text is a plain ASCII field in the code: "TXT:" then 124 bytes, the text (1..120 characters) and NUL bytes.
 * tools/scrolltext.py rewrites it and the CRC without a compiler (format: docs/user/developers.md, "Text field of
 * the scroll module").
 * Font: 4 rows, A..Z, 0..9, space and . , ! ? ' - : / & (reference: tools/scrolltext.py FONT). One dark column
 * after each character, eight after the text, then it repeats. a..z draw as A..Z; other bytes draw as a space.
 * Transport running: one step every 1/2, 1/4, 1/8, 1/16 (start value) or 1/32 note, on the sequencer pulses.
 * Transport stopped: the same step lengths at 2 LED frames per pulse (approx. 118 BPM at 94 frames per second).
 * Macro 1: speed. Macro 2: colour (12 colours, white, rainbow). A pad push starts the text again from the right.
 * Shift + Sample Rec exits. No notes, parameters or MIDI.
 */
#include <stdint.h>
#include "module_common.h"

#define MAX_TEXT 120u
#define FIELD 124u
#define END_GAP 8u
#define MAX_COLS (MAX_TEXT * 6u + END_GAP)     /* widest glyph 5 columns + 1 gap */
#define SPEEDS 5u
#define COLOURS 14u                            /* 0..11 hues, 12 white, 13 rainbow */
#define RAINBOW 13u

struct ram {
    uint8_t idx[96];
    uint8_t cols[(MAX_COLS + 1) / 2];          /* 4 bits per column, bit r = row r lit; even column low nibble */
    uint16_t len, pos;
    uint8_t speed, colour, tick, spare;
    int8_t enc[2];
    struct mc_clock transport;
};
_Static_assert(sizeof(struct ram) <= 768, "module RAM");
#define R ((struct ram *)0x100u)

/* The text field. Exactly one "TXT:" in the module: never inline this function. */
static __attribute__((noinline)) const uint8_t *text_field(void)
{
    const uint8_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n\t.ascii \"TXT:\"\n1:\n\t"
            ".ascii \"CIRCUS ARRHYTHMIA FM\"\n\t.fill 104, 1, 0\n\t"
            ".p2align 1\n2:" : "=r"(t));
    return t;
}

/* Glyphs of ASCII 0x20..0x5A, one word each: column c in bits 4c..4c+3, bit 4c+r = row r lit. 0 = not in the font
 * (space is 0 too, drawn 2 columns wide). Generated from tools/scrolltext.py FONT (font_words()). */
static __attribute__((noinline)) const uint32_t *font(void)
{
    const uint32_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t"
            ".word 0x00000, 0x0000B, 0x00000, 0x00000, 0x00000, 0x00000, 0x04A5A, 0x00003\n\t"   /*   ! - - - - & ' */
            ".word 0x00000, 0x00000, 0x00000, 0x00000, 0x00048, 0x00022, 0x00008, 0x00168\n\t"   /* - - - - , - . / */
            ".word 0x00F9F, 0x008FA, 0x00BD9, 0x00FB9, 0x00F23, 0x00DBB, 0x00CDF, 0x003D1\n\t"   /* 0 1 2 3 4 5 6 7 */
            ".word 0x00FBF, 0x00FB3, 0x0000A, 0x00000, 0x00000, 0x00000, 0x00000, 0x00251\n\t"   /* 8 9 : - - - - ? */
            ".word 0x00000, 0x00E5E, 0x006BF, 0x00996, 0x0069F, 0x009BF, 0x0013F, 0x00D96\n\t"   /* - A B C D E F G */
            ".word 0x00F2F, 0x009F9, 0x00784, 0x0096F, 0x0088F, 0xF242F, 0x0F42F, 0x06996\n\t"   /* H I J K L M N O */
            ".word 0x0075F, 0x0A596, 0x00B5F, 0x0059A, 0x001F1, 0x00F8F, 0x00787, 0x78687\n\t"   /* P Q R S T U V W */
            ".word 0x09669, 0x003C3, 0x009BD\n\t"                                                 /* X Y Z */
            ".p2align 1\n2:" : "=r"(t));
    return t;
}

/* R, G, B of the 12 hues and white: amber, orange, red, pink, magenta, violet, blue, sky, cyan, mint, green, lime. */
static __attribute__((noinline)) const uint8_t *palette(void)
{
    const uint8_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t"
            ".byte 254,190,0, 254,96,0, 254,0,0, 254,0,96, 254,0,254, 128,0,254\n\t"
            ".byte 0,0,254, 0,128,254, 0,254,254, 0,254,96, 0,254,0, 128,254,0, 254,254,254\n\t"
            ".p2align 1\n2:" : "=r"(t));
    return t;
}

static void put(struct ram *r, uint32_t n, uint32_t v)
{
    uint8_t *b = &r->cols[n >> 1];
    *b = (uint8_t)(n & 1 ? (*b & 0x0F) | v << 4 : (*b & 0xF0) | v);
}

static uint32_t col(const struct ram *r, uint32_t n)
{
    return r->cols[n >> 1] >> ((n & 1) * 4) & 15u;
}

/* Lay the text out as columns: glyph, 1 dark column; END_GAP dark columns after the text. */
static void layout(struct ram *r)
{
    const uint8_t *t = text_field();
    const uint32_t *f = font();
    uint32_t n = 0;
    for (uint32_t i = 0; i < MAX_TEXT && t[i]; i++) {
        uint32_t c = t[i];
        if (c >= 'a' && c <= 'z')
            c -= 32;
        uint32_t g = c >= 0x20 && c <= 0x5A ? f[c - 0x20] : 0;
        uint32_t w = 0;
        for (uint32_t k = 0; k < 5; k++)
            if (g >> (4 * k) & 15u)
                w = k + 1;
        if (!g)
            w = 2;                                /* space, and bytes outside the font */
        for (uint32_t k = 0; k < w; k++)
            put(r, n++, g >> (4 * k) & 15u);
        put(r, n++, 0);
    }
    for (uint32_t k = 0; k < END_GAP; k++)
        put(r, n++, 0);
    r->len = (uint16_t)n;
}

static void restart(struct ram *r)
{
    r->pos = (uint16_t)(r->len - END_GAP);        /* dark window; the next step brings column 0 in at the right */
    r->tick = 0;
}

static uint32_t pulses(const struct ram *r)
{
    return 48u >> r->speed;                       /* 1/2, 1/4, 1/8, 1/16, 1/32 note */
}

static void step(struct ram *r)
{
    r->pos = (uint16_t)(r->pos + 1u >= r->len ? 0 : r->pos + 1u);
}

void init(uint8_t *leds)
{
    struct ram *r = R;
    mc_led_table(r->idx);
    r->speed = 3; r->colour = 0; r->enc[0] = r->enc[1] = 0;
    r->transport.running = r->transport.seen = r->transport.pulse = 0;
    layout(r);
    restart(r);
    mc_clear(leds);
}

void input(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value)
{
    struct ram *r = R;
    if (kind == 5 && index == 0) {
        r->speed = (uint8_t)mc_encoder(&r->enc[0], r->speed, value, 0, SPEEDS - 1);
    } else if (kind == 5 && index == 1) {
        r->colour = (uint8_t)mc_encoder(&r->enc[1], r->colour, value, 0, COLOURS - 1);
    } else if (kind == 1 && index < 32 && (value >> 8) == 1) {
        restart(r);
    }
    (void)leds;
}

void clock(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm)
{
    struct ram *r = R;
    if (mc_clock_event(&r->transport, 0, event, pos) && r->transport.pulse % pulses(r) == 0)
        step(r);
    if (event != 2)
        r->tick = 0;
    (void)leds; (void)bpm;
}

uint32_t frame(uint8_t *leds, uint32_t frame_no)
{
    struct ram *r = R;
    const uint8_t *pal = palette();
    if (!r->transport.running && ++r->tick >= 2 * pulses(r)) {
        r->tick = 0;
        step(r);
    }
    mc_clear(leds);
    for (uint32_t x = 0; x < 8; x++) {
        uint32_t n = r->pos + x;
        if (n >= r->len)
            n -= r->len;
        uint32_t v = col(r, n);
        const uint8_t *c = &pal[3 * (r->colour == RAINBOW ? n % 12u : r->colour)];
        for (uint32_t y = 0; y < 4; y++)
            if (v >> y & 1)
                mc_rgb(r->idx, leds, y * 8 + x, c[0], c[1], c[2]);
    }
    leds[1] = (uint8_t)(0x20u + r->speed * 0x30u);                     /* macro 1, green: speed */
    const uint8_t *k = &pal[3 * (r->colour == RAINBOW ? (frame_no >> 4) % 12u : r->colour)];
    leds[3] = k[0] >> 1 & 0xFE; leds[4] = k[1] >> 1 & 0xFE; leds[5] = k[2] >> 1 & 0xFE;   /* macro 2: colour */
    return 1;
}
