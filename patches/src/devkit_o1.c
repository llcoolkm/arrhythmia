/* devkit-o1 (ARM base label 7103, not for flashing): devkit-tm (6013's ARM, 7013) plus the O1 bounded DSP waits
 * (patches/src/o1wait.c). devkit_abi3.c changes only under O1WAIT (SysEx cmd 0D); without
 * O1WAIT, devkit-tm still builds 7013 byte for byte. */
#define O1WAIT 1
#include "devkit_tm.c"
#include "o1wait.c"
