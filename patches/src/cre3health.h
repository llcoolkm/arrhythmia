/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* Bounded ABI3 meter verdicts. No platform/timer/stack qualification is implied.
 * One Main-owned snapshot per DSP load; never write/reset a shim counter.
 * Counter differences are modulo24. Arrival is diagnostic, never a verdict.
 */
#ifndef CRE3HEALTH_H
#define CRE3HEALTH_H
#include <stdint.h>
#include "cre3policy.h"
#define C3H_MASK 0xFFFFFFu
enum {C3H_OK, C3H_READ, C3H_MISMATCH, C3H_OVERRUN, C3H_BUDGET};
struct c3h {
    uint32_t mismatch, overrun, poll_at;
    uint8_t armed, reason, started, reserved;
#if ABI3_PRODUCTION_RC
    uint32_t normal_max;
#endif
};
_Static_assert(sizeof(struct c3h)==(ABI3_PRODUCTION_RC?20:16),"explicit health reservation");
static void c3h_reset(struct c3h *s)
{
    s->mismatch=s->overrun=s->poll_at=0;
    s->armed=s->reason=s->started=s->reserved=0;
#if ABI3_PRODUCTION_RC
    s->normal_max=0;
#endif
}
/* Startup mismatch evidence is never erased by taking a fresh baseline. */
static uint32_t c3h_start(struct c3h *s,uint32_t mismatch,uint32_t overrun,uint32_t now)
{
    s->mismatch=mismatch;s->overrun=overrun;s->poll_at=now;
    s->reason=mismatch?C3H_MISMATCH:C3H_OK;
    s->armed=!s->reason;return s->reason;
}
static uint32_t c3h_observe(struct c3h *s,uint32_t mismatch,uint32_t overrun,uint32_t trying)
{
    uint32_t bad=(mismatch-s->mismatch)&C3H_MASK;
    uint32_t late=(overrun-s->overrun)&C3H_MASK;
    s->mismatch=mismatch;s->overrun=overrun;
    if (bad) s->reason=C3H_MISMATCH;
    else if (late && trying && !s->reason) s->reason=C3H_OVERRUN;
    return s->reason;
}
#endif
