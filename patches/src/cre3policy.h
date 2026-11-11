/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT. */
/* Production execution policy. The test context is a separate build shape. */
#ifndef CRE3POLICY_H
#define CRE3POLICY_H
#ifndef ABI3_PRODUCTION_RC
#define ABI3_PRODUCTION_RC 0
#endif
#if ABI3_PRODUCTION_RC
#if ABI3_GENERAL_BENCH || ABI3_FM_ACCEPTANCE || DEV_NOPLAY || ABI3_C1_PROBE
#error "production policy refuses private bench, trial, gate or probe defines"
#endif
#ifndef ABI3_PRODUCTION_E
#error "production policy requires an explicitly bound engine cycle envelope"
#endif
#if ABI3_PRODUCTION_E < 1 || ABI3_PRODUCTION_E > 0xFFFFFFu
#error "production engine cycle envelope must be a positive 24-bit value"
#endif
#define CRE3_PRODUCTION_WINDOWS 7u
#endif
#endif
