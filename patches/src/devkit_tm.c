/* devkit-tm (ARM base label 7013; flashable as 6013 = this + the DSP meter): devkit-abi3 (6012) plus track modes
 * milestone T1, persistence only (patches/src/trackmodes.c). devkit_abi3.c is compiled
 * unchanged except for its TRACKMODES blocks (heap floor TM_AT, tm_boot from hook_boot, SysEx cmd 0C); without
 * TRACKMODES it builds 6012 byte for byte. */
#define TRACKMODES 1
#include "devkit_abi3.c"
#include "trackmodes.c"
