/* SPDX-License-Identifier: MIT
 * Main-owned engine control integration. */
#if ENGINE_ABI3 && !FAR_PART
static void abi3_controls_commit(void);
static uint32_t abi3_control_source(uint32_t id);
static uint32_t abi3_stage_control(uint32_t id,uint32_t value);
static void abi3_project_controls_apply(void);
static uint32_t engctl_edit(struct state *s,uint32_t record,uint32_t value);
#endif
