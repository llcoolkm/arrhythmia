/* devkit-plock (ARM base label 7015; flashable as 6015 = this + the DSP meter): devkit-tm (6013) plus the p-lock
 * carry-over fix (patches/src/plockfix.c) and review item 1 of 6013 (TM_BUSY_FIRST in
 * trackmodes.c: the BUSY gate before the live-tail check). PLOCK adds, in devkit_abi3.c only, the 64-byte struct plx
 * block below struct tm (heap limit 0x2004F400) and the pl_host call in the module drain. devkit_abi3.c and
 * trackmodes.c are compiled unchanged otherwise; without TM_BUSY_FIRST and PLOCK they build 6013 byte for byte. */
#define TRACKMODES 1
#define TM_BUSY_FIRST 1
#define PLOCK 1
#include "devkit_abi3.c"
#include "trackmodes.c"
#include "plockfix.c"
