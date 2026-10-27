/* VERSION 000207 (FM_207 builds; notetap.c includes this instead of ad_life.h).
 * Mathematically generated from the 207 attack table: A = .010 + 1.990 (i/127)^2 + 6 max(0, (i-64)/63)^2 s,
 * lifetime ceil(0x7FFFFF / step) blocks; decay unchanged from ad_life.h. */
/* Generated envelope lifetime table; render blocks; age ranking only. Do not edit.
 * 256 entries: the AD lifetime of each 7-bit index in DSP render blocks (32 samples), 128 attack, then
 * 128 decay (the larger of the DSP's two rounding modes). Defaults A 0 + D 64 = 15 + 3921 = 3936
 * blocks; maximum 12001 + 15597 = 27598.
 * Lifetimes SHA-256 (u16 LE, attack then decay): 3142f1a68ebaf71b780f8e9fa13eddf6f2e3fa9e4c38f08f6dddb2c434805bda.
 * Data in .text (the hook build keeps .text only; PC-relative, no relocation; noinline: one copy). Used for voice-age
 * RANKING only. */
#ifndef AD_LIFE_H
#define AD_LIFE_H
#include <stdint.h>

#define AD_ATTACK_MAX_BLOCKS 12001u
#define AD_DECAY_MAX_BLOCKS  15597u

__attribute__((noinline)) static const uint16_t *ad_life_tab(void)
{
    const uint16_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t"
            ".hword 15,15,16,17,18,20,22,24,27,30,34,37,42,46,51,57\n\t"   /* attack 0.. */
            ".hword 62,69,75,82,89,97,105,113,122,131,140,150,160,171,182,193\n\t"   /* attack 16.. */
            ".hword 205,217,229,242,255,269,283,297,311,326,342,358,374,390,407,424\n\t"   /* attack 32.. */
            ".hword 442,460,478,497,516,535,555,575,596,617,638,660,682,704,727,750\n\t"   /* attack 48.. */
            ".hword 774,800,831,867,908,954,1004,1060,1121,1186,1256,1332,1412,1497,1587,1682\n\t"   /* attack 64.. */
            ".hword 1782,1886,1996,2111,2230,2354,2484,2618,2757,2901,3050,3203,3361,3525,3694,3868\n\t"   /* attack 80.. */
            ".hword 4047,4229,4418,4610,4808,5012,5217,5430,5649,5871,6097,6332,6569,6809,7056,7308\n\t"   /* attack 96.. */
            ".hword 7565,7826,8090,8364,8640,8925,9209,9501,9800,10095,10408,10714,11024,11352,11668,12001\n\t"   /* attack 112.. */
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
