/* SPDX-License-Identifier: MIT
 * Main input/UI frame adapters. Callers hold the existing event/frame
 * serialization; project and producer changes go only through engctl_edit. */
#ifndef ENGCTL_UI_H
#define ENGCTL_UI_H
#if ENGINE_ABI3 && !FAR_PART
#include "cre3controls.h"
struct engctl_lights { uint8_t *leds; uint32_t base; };
static const struct cre3_facts *engctl_visible_facts(void);
static void engctl_pad_light(void *ctx,uint32_t pad,uint32_t hue,uint32_t intensity);
static void abi3_sample_frame(struct state *s,uint8_t *leds); /* notetap.c: checked Sample-view binding */
static void engctl_feedback(uint32_t result);
static uint32_t engctl_is_fm(void);
static uint32_t engctl_chords(void);
static uint32_t engctl_poly_mask(void);
static uint32_t engctl_poly_default(void);
static uint32_t engctl_macro_declared(uint32_t source);
static void engctl_config_macros(struct state *s,const volatile uint8_t *patch);
static uint32_t engctl_synth_pages(void);
static void engctl_synth_pad(struct state *s,uint32_t pad,uint32_t clear);
static void engctl_synth_encoder(struct state *s,uint32_t knob,uint32_t delta,uint32_t clear);
static void engctl_synth_render(struct state *s);
static uint32_t engctl_sample_pages(void);
static uint32_t engctl_sample_selected_page(uint32_t sound);
/* 0: engine has no custom Sample surface; 1: consumed, no audition;
 * 2: validated sound slot returned for the existing stock audition route.
 * Input pad is physical lower-grid index0..15; page is logical0..1. */
static uint32_t engctl_sample_pad(struct state *s,uint32_t page,uint32_t pad,
                                 uint32_t clear,uint32_t *sound);

#endif
#endif
