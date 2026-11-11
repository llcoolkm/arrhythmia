/* SPDX-License-Identifier: MIT
 * Main input/UI frame adapters. Callers hold the existing event/frame
 * serialization; project and producer changes go only through engctl_edit. */
#ifndef ENGCTL_UI_H
#define ENGCTL_UI_H
#if ENGINE_ABI3 && !FAR_PART
#include "cre3controls.h"
static void abi3_sample_frame(struct state *s,uint8_t *leds); /* notetap.c: checked Sample-view binding */
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
/* RGB overlay on a checked live224-byte frame, preserving everything except
 * physical pads16..31 and a pending refused-edit Track8 blink. Caller renders
 * the stock frame first. */
static void engctl_sample_render(struct state *s,uint32_t page,uint32_t sound,uint8_t *leds);
#endif
#endif
