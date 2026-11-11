/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT.
 * One typed Main/Far bridge for the unchanged pure file predicate.
 * Caller owns state, buffer, platform and the 12-byte argument object on ARM.
 * Zero means dispatch refusal; finish uses 1+check so check0 is unambiguous. */
#ifndef CRE3SCAN_FAR_H
#define CRE3SCAN_FAR_H
#include "cre3loader.h"
#include "cre3scan_file.h"
#if ABI3_FAR_SCAN
#if FAR_PART
uint32_t far_cre3_scan(uint32_t op,uint32_t state,uint32_t data,uint32_t args)
{
    struct cre3_scan_file *s=(struct cre3_scan_file *)state;
    const struct cre3_scan_args *a=(const struct cre3_scan_args *)args;
    if (!state || !data) return 0;
    if (op==1) {
        if (!args || !a->platform) return 0;
        cre3_scan_first(s,(const uint8_t *)data,a->first,a->second,a->platform);
    } else if (op==2) {
        if (!args || a->platform) return 0;
        cre3_scan_piece(s,(const uint8_t *)data,a->first,a->second);
    } else if (op==3) {
        if (args) return 0;
        return cre3_scan_finish(s,(const struct cre3_platform *)data)+1;
    } else return 0;
    return 1;
}
#else
static void cre3_scan_first(struct cre3_scan_file *s,const uint8_t *b,uint32_t got,
                            uint32_t size,const struct cre3_platform *p)
{
    struct cre3_scan_args a;
    a.first=got;a.second=size;a.platform=p;
    if (far_call(FAR_CRE3_SCAN,1,(uint32_t)s,(uint32_t)b,(uint32_t)&a)!=1)
        s->check=CRE3L_COMPOSITION;
}
static void cre3_scan_piece(struct cre3_scan_file *s,const uint8_t *b,uint32_t at,uint32_t n)
{
    struct cre3_scan_args a;
    a.first=at;a.second=n;a.platform=0;
    if (far_call(FAR_CRE3_SCAN,2,(uint32_t)s,(uint32_t)b,(uint32_t)&a)!=1)
        s->check=CRE3L_COMPOSITION;
}
static uint32_t cre3_scan_finish(struct cre3_scan_file *s,const struct cre3_platform *p)
{
    uint32_t r=far_call(FAR_CRE3_SCAN,3,(uint32_t)s,(uint32_t)p,0);
    if (!r) {s->check=CRE3L_COMPOSITION;return CRE3L_COMPOSITION;}
    return r-1;
}
#endif
#endif
#endif
