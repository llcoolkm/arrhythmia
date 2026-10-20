/* Generated envelope lifetime table; render blocks; age ranking only. Do not edit.
 * 256 entries: the AD lifetime of each 7-bit index in DSP render blocks (32 samples), 128 attack, then
 * 128 decay (the larger of the DSP's two rounding modes). Defaults A 0 + D 64 = 3 + 3921 = 3924
 * blocks; maximum 3004 + 15597 = 18601.
 * Lifetimes SHA-256 (u16 LE, attack then decay): e4cda0f538e1bc3126940f584a5892e0bebaa569eab386bb5b9e9b8c6b5b4f00.
 * Data in .text (the hook build keeps .text only; PC-relative, no relocation; noinline: one copy). Used for voice-age
 * RANKING only. */
#ifndef AD_LIFE_H
#define AD_LIFE_H
#include <stdint.h>

#define AD_ATTACK_MAX_BLOCKS 3004u
#define AD_DECAY_MAX_BLOCKS  15597u

__attribute__((noinline)) static const uint16_t *ad_life_tab(void)
{
    const uint16_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t"
            ".hword 3,4,4,4,4,4,5,5,5,5,6,6,6,7,7,7\n\t"   /* attack 0.. */
            ".hword 8,8,8,9,9,10,10,11,12,12,13,14,14,15,16,17\n\t"   /* attack 16.. */
            ".hword 18,19,20,21,22,23,24,26,27,28,30,32,33,35,37,39\n\t"   /* attack 32.. */
            ".hword 41,44,46,49,51,54,57,60,64,67,71,75,79,83,88,93\n\t"   /* attack 48.. */
            ".hword 98,103,109,115,122,129,136,143,151,160,169,178,188,198,209,221\n\t"   /* attack 64.. */
            ".hword 233,246,260,275,290,306,323,341,360,381,402,424,448,473,499,527\n\t"   /* attack 80.. */
            ".hword 557,588,621,655,692,730,771,814,860,908,959,1012,1069,1128,1192,1258\n\t"   /* attack 96.. */
            ".hword 1328,1403,1481,1564,1651,1743,1841,1944,2053,2167,2288,2416,2551,2693,2844,3004\n\t"   /* attack 112.. */
            ".hword 98,104,110,117,124,131,139,147,156,165,175,185,196,208,220,233\n\t"   /* decay 0.. */
            ".hword 247,261,277,293,311,329,348,369,391,414,439,465,492,522,552,585\n\t"   /* decay 16.. */
            ".hword 620,657,696,737,781,827,876,928,983,1041,1103,1168,1237,1311,1389,1471\n\t"   /* decay 32.. */
            ".hword 1559,1651,1749,1853,1962,2078,2203,2333,2471,2616,2774,2939,3114,3298,3492,3696\n\t"   /* decay 48.. */
            ".hword 3921,4009,4100,4193,4287,4383,4481,4581,4683,4786,4892,5000,5111,5223,5338,5454\n\t"   /* decay 64.. */
            ".hword 5573,5695,5819,5945,6074,6211,6356,6504,6656,6810,6967,7127,7292,7458,7628,7802\n\t"   /* decay 80.. */
            ".hword 7979,8159,8343,8531,8723,8918,9118,9321,9527,9738,9954,10173,10397,10625,10858,11096\n\t"   /* decay 96.. */
            ".hword 11338,11585,11837,12093,12355,12623,12894,13171,13454,13744,14037,14339,14643,14955,15274,15597\n\t"   /* decay 112.. */
            ".p2align 1\n2:" : "=r"(t));
    return t;
}

/* Render blocks of a complete attack + decay at indices a, d (0..127). */
static uint32_t ad_life_blocks(uint32_t a, uint32_t d)
{
    const uint16_t *t = ad_life_tab();
    return (uint32_t)t[a & 127u] + t[128u + (d & 127u)];
}
#endif
