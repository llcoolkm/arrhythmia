/* devkit-a2 (ARM dev label 7102, emulation only, never flashed): devkit-plock (6015: devkit-tm + the p-lock carry-over
 * fix + TM_BUSY_FIRST) plus
 *   - the track-8 settings page (release item A2): patches/src/synthpage.c, codec patches/src/synth_settings.h,
 *     tm_edit (TM_EDIT) in trackmodes.c; route()/hook_frame() SYNTHPAGE blocks in devkit_abi3.c;
 *     32 B of new RAM below the p-lock block (heap limit 0x2004F3E0);
 *   - release item D3: SysEx cmd 08 reads only the DSP meter's X windows (CMD08_METER in devkit_abi3.c).
 * devkit-plock here is 6015 (the p-lock fix, PLOCK: struct plx, hook_macro, hook_fx, pl_host).
 * RAM: struct pg (32 B) below struct plx (64 B) below struct tm; heap limit 0x2004F400 -> 0x2004F3E0.
 * Without these defines devkit_abi3.c and trackmodes.c build 6012 / 6013 / 6015 byte for byte. */
#define TRACKMODES 1
#define TM_BUSY_FIRST 1
#define PLOCK 1
#define TM_EDIT 1
#define SYNTHPAGE 1
#define CMD08_METER 1
#define PG_HIDE_PATCH 1       /* v0.1: one fixed DSP patch; attack/decay/level and waveform hidden on the page */
#include "devkit_abi3.c"
#include "trackmodes.c"
#include "plockfix.c"
#include "synthpage.c"
