/* Arrhythmia 0.1 ARM root. One source, two profiles, selected by tools/build_lightshow.py from the target's
 * "profile" entry, which passes:
 *   -DSYNTH=0|1    1: track-8 synth: the settings page (synthpage.c, TM_EDIT, patch controls hidden) and the note tap
 *                     (notetap.c: note tap, chord engine, voice table, ring admission) with its fixed 0x300-byte block
 *                     below struct pg (NOTETAP; heap limit 0x2004F0E0). The target also lists the tap's hook sites.
 *                  0: track 8 unchanged: no page, no tap, no struct pg / struct a1 (heap limit 0x2004F400), no tap
 *                     hook site.
 *   -DCMD08=1|0    1: SysEx cmd 08 serves the DSP meter's X windows only (CMD08_METER).
 *                  0: every cmd 08 gets the defined refusal (status 0, request echoed; NO_CMD08 in devkit_abi3.c), the
 *                     DSP read path is compiled out.
 *   -DDUMP_ALLOW=1|0  1 (default): SysEx 7D reads only the module SDK's ITCM 0x00000100-0x00003FFF and refuses every
 *                     other address, the bootloader and the stock application flash included (devkit_abi3.c dump()).
 *                     0: the previous 7D (internal flash or ITCM); only the SCALES=0 check targets use it.
 *   -DSCALES=0|1   1: scales for the sample tracks 1..7 (scales.c): Note-view keyboard tables, a sub-record in the
 *                     track-mode block, struct sc below the lowest block (heap limit 32 B lower), SysEx 0E, one
 *                     keyboard hook site; with SYNTH=1 also the page controls and the chord engine's project-wide
 *                     scale. Every 0.1 profile builds with 1. 0: none of it is compiled and the site is not patched.
 * Common to all: track modes, the p-lock fix, bounded DSP waits (o1wait.c, SysEx 0D), the module SDK
 * (SysEx 04/05/06/0A/0B, host table abi 3).
 * Profiles: arrhythmia-devkit-arm (7241 -> image 7228) SYNTH=0 CMD08=1; arrhythmia-firmware-arm (7242 -> 7229) SYNTH=1
 * CMD08=0; arrhythmia-devkit-synth-arm (7243, emulation only) SYNTH=1 CMD08=1. */
#if !defined(SYNTH) || !defined(CMD08)
#error "build through tools/build_lightshow.py: the target's profile defines SYNTH and CMD08"
#endif
#define TRACKMODES 1
#define TM_BUSY_FIRST 1
#define PLOCK 1
#if SYNTH
#define TM_EDIT 1
#define SYNTHPAGE 1
#define PG_HIDE_PATCH 1
#define NOTETAP 1
#endif
#if CMD08
#define CMD08_METER 1
#else
#define NO_CMD08 1
#endif
#ifndef BENCH_METER_READ                /* -DBENCH_METER_READ=1 (bench ARM profile arrhythmia-core0-bankview-meter-arm,
                                           label 7305, only; never 7304, a public or a release build): SysEx cmd 08 is
                                           the restricted core-0 meter readout (core0feed.c q0_meter):
                                           X:$5C00..$5E5F, 1..16 words, the 0F idle rule
                                           before every DSP access, a nonce, no write; the general cmd 08 read
                                           (dsp_read) is compiled out */
#define BENCH_METER_READ 0
#endif
#ifndef SCALES
#define SCALES 0
#endif
#ifndef CORE0_SYNTH                     /* -DCORE0_SYNTH=1: the track-8 synth fed to the core-0 queue (patches/src/core0feed.c,
                                           the core-0 profiles, 0.2 included); 0 everywhere else */
#define CORE0_SYNTH 0
#endif
#ifndef CORE0_BULK_EXCLUDE              /* -DCORE0_BULK_EXCLUDE=1 (bench): any bulk interval closes the synth path until a
                                           DSP load (no bulk preservation claim) */
#define CORE0_BULK_EXCLUDE 0
#endif
#ifndef CORE0_BENCH                     /* -DCORE0_BENCH=1 (bench, with CORE0_BULK_EXCLUDE): pacing 4 entries / one hold per
                                           Main pass, silence before bulk, startup bulk before OPEN, SysEx 0F telemetry */
#define CORE0_BENCH 0
#endif
#ifndef CORE0_KEYSCALE                  /* -DCORE0_KEYSCALE=1 (with CORE0_TONE): admit header VERSION 000205 instead */
#define CORE0_KEYSCALE 0
#endif
#ifndef CORE0_TONE                      /* -DCORE0_TONE=1 (with CORE0_SYNTH): the live tone packet, header VERSION 000204 */
#define CORE0_TONE 0
#endif
#ifndef CORE0_ONLY                      /* -DCORE0_ONLY=1 (0.2, with CORE0_SYNTH): core 0 is the only synth; the core-1 legacy
                                           voice is retired: no ENABLE X:2E70 write, no core-1 frame from our code */
#define CORE0_ONLY 0
#endif
#ifndef FM_BANKVIEW                     /* -DFM_BANKVIEW=1 (profile arrhythmia-core0-bankview-arm, label 7304, only): the 7302
                                           configuration plus the FM ratio family and algorithm read from track 8's
                                           sample slot (Sample view pages 0 and 1), the synth page's FM view removed
                                           (its macros on the single view).
                                           It implies the 7302 defines below instead of listing them, so the profile
                                           tables keep PAD_POLY / FM_PATCH / TRIM_* on their own labels and every other
                                           profile preprocesses exactly as before */
#define FM_BANKVIEW 0
#endif
#ifndef FM_207                          /* -DFM_207=1 (0.2 profiles only: arrhythmia-core0-bankview-arm 7304,
                                           arrhythmia-firmware-arm-fm, and the gated 7304 built from it): header VERSION
                                           000207 and the 207 R127 release reference (fm_patch.h, core0feed.h). 7302
                                           (206 delivery, image 7303) and the 7305 meter profiles keep 000206 */
#define FM_207 0
#endif
#if FM_207 && !FM_BANKVIEW
#error "FM_207=1 builds on the FM_BANKVIEW configuration (label 7304)"
#endif
#if FM_BANKVIEW
#define PAD_POLY 1
#define FM_PATCH 1
#define TRIM_RING 1
#define TRIM_HIDE 1
#define TRIM_LEDS 1
#endif
#ifndef PAD_POLY                        /* -DPAD_POLY=1 (with CORE0_SYNTH; profile 7288 only): front-panel pads follow the
                                           MIDI poly rule (notes per press 1, POLY on: each press its own instance) */
#define PAD_POLY 0
#endif
#ifndef FM_PATCH                        /* -DFM_PATCH=1 (profile arrhythmia-core0-fmpatch-arm, label 7302, with CORE0_ONLY and
                                           PAD_POLY): per-note FM settings latched ahead of the ONSET (patches/src/fm_patch.h),
                                           patch slot 7 codec 2, the synth page's FM view */
#define FM_PATCH 0
#endif
#ifndef TRIM_RING                       /* flash trims (FM_PATCH profile only): C1 the stock-ring
                                           write keeps only stock-word handling (CORE0_ONLY: ours go to the core-0 queue) */
#define TRIM_RING 0
#endif
#ifndef TRIM_HIDE                       /* C2: the hidden subtractive wave pads and macros 1-3 compiled out (PG_HIDE_PATCH) */
#define TRIM_HIDE 0
#endif
#ifndef TRIM_LEDS                       /* C3: the settings getters out of line (one copy for every reader) */
#define TRIM_LEDS 0
#endif
#if BENCH_METER_READ && !(CORE0_BENCH && !CMD08)
#error "BENCH_METER_READ=1 is a bench profile option (CORE0_BENCH, CMD08=0)"
#endif
#if FM_BANKVIEW && !(CORE0_ONLY && CORE0_BENCH && SCALES)
#error "FM_BANKVIEW=1 builds on the 7302 configuration (CORE0_ONLY, CORE0_BENCH, SCALES)"
#endif
#if FM_PATCH && !(CORE0_ONLY && PAD_POLY && CORE0_BENCH)
#error "FM_PATCH=1 builds on the 7288 configuration (CORE0_ONLY, PAD_POLY, CORE0_BENCH)"
#endif
#ifndef CORE0_RECOVER                   /* -DCORE0_RECOVER=1 (0.2.1 profiles *-recover only, on FM_207): runtime
                                           content-transfer recovery (core0feed.c): after a silent runtime bulk, the own
                                           DSP reinit through READY with INIT_COUNT confirmation, a new epoch and the
                                           barrier instead of exclusion; the back-to-back rule; the CLOSED-drop late-off
                                           gate; the cross-task guard under BUS_SEM; allocator validation; the 0F tail.
                                           0 everywhere else: every existing profile, the FM_207 ones included, keeps
                                           its bytes */
#define CORE0_RECOVER 0
#endif
#if CORE0_RECOVER && !(FM_207 && FM_PATCH && CORE0_BULK_EXCLUDE && CORE0_BENCH && CORE0_TONE)
#error "CORE0_RECOVER=1 builds on the FM_207 configuration (0xC0 Q0 block, CORE0_BULK_EXCLUDE, CORE0_BENCH)"
#endif
#ifndef FB_MACRO1                       /* -DFB_MACRO1=1 (0.2.1 profiles *-021): track 8's main macro 1 (channel
                                           parameter 6, CC 21, stored 4..124) is FM feedback 0..127 at note start; the
                                           synth page's macro 1 is Tune (patch slot +4, codec 2 version 3, default 64);
                                           older slots convert once at load or boot (notetap.c ad_fb_convert). 0
                                           everywhere else: every existing profile keeps its bytes */
#define FB_MACRO1 0
#endif
#if FB_MACRO1 && !(FM_207 && defined(AD_LIVE) && AD_LIVE)
#error "FB_MACRO1=1 builds on the FM_207 configuration (AD_LIVE)"
#endif
#ifndef STEP_CHORDS                     /* -DSTEP_CHORDS=1 (0.2.1 profiles *-021): track-8 steps hold up to 4 notes (byte 6
                                           key into the project's shape table; notetap.c). 0 everywhere else */
#define STEP_CHORDS 0
#endif
#ifndef FAR_PART                        /* 1: the far-blob pass (tools/farblob.py) of a STEP_CHORDS build */
#define FAR_PART 0
#endif
#ifndef FAR_KEY                         /* the far blob's key, from its pass (tools/build_lightshow.py) */
#define FAR_KEY 0u
#endif
#ifndef BOOT_PULSE                      /* -DBOOT_PULSE=1 (0.2.1 profiles *-021, #5): the ECG pulse line over the boot pack-load
                                           progress (notetap.c, in the far blob). 0 everywhere else */
#define BOOT_PULSE 0
#endif
#if BOOT_PULSE && !STEP_CHORDS
#error "BOOT_PULSE=1 needs the far blob (STEP_CHORDS)"
#endif
#if STEP_CHORDS && !(FM_BANKVIEW && SCALES)
#error "STEP_CHORDS=1 builds on the FM_BANKVIEW configuration (SCALES: tm_edit)"
#endif
#ifndef CORE0_RECOVER_TIMING            /* -DCORE0_RECOVER_TIMING=1 (OPTIONAL, profile *-recover-timing only): ACK latency
                                           and reinit duration counters and c_init_late in struct q0 and the 0F tail
                                           (Q0 block 0xD0, heap limit 16 B lower, 0F reply 185 bytes) */
#define CORE0_RECOVER_TIMING 0
#endif
#if CORE0_RECOVER_TIMING && !CORE0_RECOVER
#error "CORE0_RECOVER_TIMING=1 needs CORE0_RECOVER=1"
#endif
#if PAD_POLY && !CORE0_SYNTH
#error "PAD_POLY=1 needs CORE0_SYNTH=1"
#endif
#if CORE0_ONLY && !CORE0_SYNTH
#error "CORE0_ONLY=1 needs CORE0_SYNTH=1"
#endif
#if SCALES
#define TM_EDIT 1
#endif
#define O1WAIT 1
#ifndef DUMP_ALLOW
#define DUMP_ALLOW 1
#endif
#include "devkit_abi3.c"
#include "trackmodes.c"
#include "plockfix.c"
#if SCALES
#include "scales.c"
#endif
#if SYNTH
#include "synthpage.c"
#include "notetap.c"
#endif
#include "o1wait.c"
