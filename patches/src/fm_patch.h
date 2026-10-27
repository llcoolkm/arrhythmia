/* FM patch latches (FM_PATCH=1 only).
 * THE ONLY FILE THAT KNOWS THE FM LATCH ENCODINGS, THE HEADER VERSION AND THE DSP DEFAULTS. The packets and their
 * snapshot semantics are the DSP command contract r1 (2026-10-21) for the DSP side's VERSION 000206: NOT a frozen
 * hardware ABI. Change them here and nowhere else.
 *
 * Wire (core-0 queue, class A, word1 reserved bits zero):
 *   PATCH_FM word0 = 0xA09000 exactly, word1 = algorithm[1:0] | bank[5:2] | feedback[12:6], bits 13..23 zero.
 *            algorithm 0 = serial4, 1 = dual_stack (2, 3 not accepted); bank 0..15 (the ratios live on the DSP);
 *            feedback 0..127.
 *   MOD_AD   word0 = 0xA0A000 exactly, word1 = attack[6:0] | decay[13:7] | floor[20:14], bits 21..23 zero.
 *   Both are LATCHES: the DSP copies the current values into a voice's record when it accepts that voice's ONSET, so
 *   they must precede the ONSETs that depend on them. A latch may be reused unchanged by later notes; a changed value
 *   is never coalesced across a note (a queued latch with an ONSET behind it is never rewritten).
 *   VERSION 000206 (strict: the ARM admits no other header on this profile). FM_207 (the 0.2 profiles: 7304,
 *   arrhythmia-firmware-arm-fm, gated 7304): VERSION 000207 strict, 000206 and older refused (the changed release
 *   representation, contract frozen; the wire A/D/S/R index fields are unchanged). After an epoch reset the DSP holds
 *   the defaults below; q0_open sets the ARM's published mirrors to them.
 *
 * ARM FIFO (N): the latches ride in the envelope-group entry (kind VK_AD, one N entry per press), next to the A/D/S/R
 * snapshot in word1 bits 0..27. Packing (this header only):
 *   w0 = MOD_AD word1 (21 bits) | PATCH_FM word1 bits 0..10 << 21
 *   w1 = A | D << 7 | S << 14 | R << 21 | PATCH_FM word1 bits 11..12 << 28 | barrier << 30
 * At publication the group expands to: the ENV AD + SR pair if the envelope differs from the published one, PATCH_FM
 * if it differs, MOD_AD if it differs (in that order); a barrier group (marked) sends all four. 0 to 4 queue entries,
 * never split across two PROD publications. */
#ifndef FM_PATCH_H
#define FM_PATCH_H

#if defined(FM_207) && FM_207
#define FM_Q0_VERSION   0x000207u       /* header VERSION of the 0.2 profiles (FM_207; frozen) */
#else
#define FM_Q0_VERSION   0x000206u       /* header VERSION of the FM_PATCH profile (placeholder until the DSP contract is frozen) */
#endif
#define FM_PATCH_W0     0xA09000u
#define FM_MOD_W0       0xA0A000u
#define FM_PATCH_MASK   0x001FFFu
#define FM_MOD_MASK     0x1FFFFFu
#define FM_ALGOS        2u              /* serial4, dual_stack */
#define FM_BANKS        16u
#define FM_DEF_ALGO     0u              /* DSP defaults after an epoch reset (2026-10-21, subject to listening) */
#define FM_DEF_BANK     0u
#define FM_DEF_FB       0u
#define FM_DEF_MA       0u
#define FM_DEF_MD       64u
#define FM_DEF_MF       127u
#define FM_GROUP_MAX    4u              /* queue entries of one expanded group: ENV AD, ENV SR, PATCH_FM, MOD_AD */
#define FMN_BAR         (1u << 30)      /* N word1: a barrier group (sends every latch) */

static inline uint32_t fm_patch_w1(uint32_t algo, uint32_t bank, uint32_t fb)
{
    return (algo & 3u) | (bank & 15u) << 2 | (fb & 127u) << 6;
}

static inline uint32_t fm_mod_w1(uint32_t a, uint32_t d, uint32_t f)
{
    return (a & 127u) | (d & 127u) << 7 | (f & 127u) << 14;
}

#define FM_DEF_PATCH    (FM_DEF_ALGO | FM_DEF_BANK << 2 | FM_DEF_FB << 6)
#define FM_DEF_MOD      (FM_DEF_MA | FM_DEF_MD << 7 | FM_DEF_MF << 14)

/* the N packing of one group: env = A | D << 7 | S << 14 | R << 21, fm = PATCH_FM word1, md = MOD_AD word1 */
static inline uint32_t fmn_w0(uint32_t fm, uint32_t md)
{
    return (md & FM_MOD_MASK) | (fm & 0x7FFu) << 21;
}

static inline uint32_t fmn_w1(uint32_t env, uint32_t fm, uint32_t bar)
{
    return (env & 0x0FFFFFFFu) | ((fm >> 11) & 3u) << 28 | (bar ? FMN_BAR : 0u);
}

static inline uint32_t fmn_fm(uint32_t w0, uint32_t w1)
{
    return w0 >> 21 | ((w1 >> 28) & 3u) << 11;
}

static inline uint32_t fmn_md(uint32_t w0)
{
    return w0 & FM_MOD_MASK;
}
#endif
