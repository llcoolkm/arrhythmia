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
#ifndef SCALES
#define SCALES 0
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
