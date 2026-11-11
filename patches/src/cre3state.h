/* SPDX-License-Identifier: MIT
 * Shared loader-state layout; no I/O, platform grant or mutable duplicate. */
#ifndef CRE3STATE_H
#define CRE3STATE_H
#include "cre3.h"
struct cre3l_key { uint32_t platform, engine_class, body_crc, header_crc; };
struct cre3l_state {
    struct cre3_facts facts;
    struct cre3l_key candidate, resident;
    uint8_t candidate_valid, resident_valid, transmitted, last_check;
};
#if ABI3_GENERAL_BENCH || ABI3_PRODUCTION_RC
/* Share the same key comparison across admitted loader/observer callers.
 * Explicit production outlining funds the unchanged gate-only test twin. */
__attribute__((noinline))
#endif
static uint32_t cre3l_equal(const struct cre3l_key *a,const struct cre3l_key *b)
{
    return a->platform==b->platform && a->engine_class==b->engine_class
        && a->body_crc==b->body_crc && a->header_crc==b->header_crc;
}
#endif
