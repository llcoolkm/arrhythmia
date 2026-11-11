/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT.
 * L8 cold query and pointer parsing bodies. Same task/IRQ snapshots and reply
 * bytes as Main; Far dispatch owns no RAM and introduces no DSP access. */
#if ABI3_FAR_MORE && FAR_PART
static int32_t ep_parse(volatile uint8_t *b, uint32_t len, volatile char *nm)
{
    if (len && b[len - 1] == '\n')
        len--;
    if (len && b[len - 1] == '\r')
        len--;
    if (len == 4 && (b[0] | 32) == 'n' && (b[1] | 32) == 'o' && (b[2] | 32) == 'n' && (b[3] | 32) == 'e')
        return 0;
    uint32_t i = 0;
    for (; i < len && b[i] != '.'; i++) {
        uint32_t c = b[i];
        if (c >= 'A' && c <= 'Z')
            c += 32;
        if (i >= 8 || !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return -1;
        nm[i] = (char)c;
    }
    if (!i || i + 4 != len || (b[i + 1] | 32) != 'd' || (b[i + 2] | 32) != 's' || (b[i + 3] | 32) != 'e')
        return -1;
    for (uint32_t k = i; k < 8; k++)
        nm[k] = 0;
    if (i == 7 && nm[0] == 'd' && nm[1] == 'e' && nm[2] == 'f' && nm[3] == 'a' && nm[4] == 'u' && nm[5] == 'l'
            && nm[6] == 't')
        return -1;                            /* DEFAULT is the card default's reserved basename */
    return 1;
}

static void ep_status(const struct state *s)
{
#if ABI3_PRODUCTION_RC
#define EP_STATUS_WORDS 47u
#define EP_STATUS_VERSION 5u
#else
#define EP_STATUS_WORDS 46u
#define EP_STATUS_VERSION 4u
#endif
    uint8_t r[18+5*EP_STATUS_WORDS];
    uint32_t w[EP_STATUS_WORDS], pm;
    SUSPEND_ALL();
    struct eo *e = eo();
    struct a1 *a = a1();
    uint32_t ok = itcm_ok();
    for (uint32_t i = 0; i < 12; i++)
        w[i] = e ? ((const volatile uint32_t *)e)[i] : 0;
    for (uint32_t i = 0; i < 16; i++)
        w[12 + i] = ok ? CD_MARK[i] : 0;
    uint32_t f = 1u | (ENGINE_FLASH ? 2u : 0u) | (ok ? 4u : 0u) | (e ? 8u : 0u) | (a ? 16u : 0u);
    uint32_t b = (a ? a->eng : 0) | (uint32_t)s->tok << 8 | (uint32_t)s->ld_phase << 16 | (uint32_t)Q0->ph << 24;
    uint32_t q = Q0->busy | (uint32_t)Q0->excl << 8;
    __asm__ volatile("mrs %0, primask\n\tcpsid i" : "=r"(pm) :: "memory");                             /* the tick and struct wd in one snapshot: no interrupt (SysTick, a feed)
                                                 between them, so the reply's tick is never older than its last feed */
    w[28] = TV();
    for (uint32_t i = 0; i < 8; i++)
        w[29 + i] = e ? ((const volatile uint32_t *)WD_AT)[i] : 0;
    for (uint32_t i = 0; i < 4; i++)
        w[37 + i] = ((volatile uint32_t *)PK)[i];
    /* Main-owned cached ABI3 health only. No DSP transaction in a readout. */
    for (uint32_t i = 0; i < 4; i++)
        w[41 + i] = e ? ((volatile uint32_t *)(CRE3L_STATE_AT + CRE3L_HEALTH_OFFSET))[i] : 0;
    w[45] = e ? *(volatile uint8_t *)(CRE3L_STATE_AT + CRE3L_LAST_CHECK_OFFSET) : 0;
#if ABI3_PRODUCTION_RC
    w[46] = e ? *(volatile uint32_t *)(CRE3L_STATE_AT + CRE3L_HEALTH_OFFSET + 16u) : 0;
#endif
    __asm__ volatile("msr primask, %0" :: "r"(pm) : "memory");
    RESUME_ALL();
    header(r, 0x10);
    r[7] = 0x0B;
    r[8] = 'E';
    r[9] = EP_STATUS_VERSION;
    r[10] = (uint8_t)f;
    for (uint32_t i = 0; i < EP_STATUS_WORDS; i++)
        put7(r + 11 + 5 * i + (i < 28 ? 0 : 6), w[i], 5);
    for (uint32_t i = 0; i < 6; i++)
        r[151 + i] = (uint8_t)(((i < 4 ? b >> 8 * i : q >> 8 * (i - 4))) & 0x7F);
    r[sizeof r-1] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
uint32_t far_ep_parse(uint32_t input,uint32_t length,uint32_t output,uint32_t zero)
{
    if(zero)return 0;
    return (uint32_t)(ep_parse((volatile uint8_t *)input,length,(volatile char *)output)+2);
}
uint32_t far_ep_status(uint32_t state,uint32_t zero,uint32_t zero2,uint32_t zero3)
{
    if(zero || zero2 || zero3)return 0;
    ep_status((const struct state *)state);
    return 1;
}
#endif
