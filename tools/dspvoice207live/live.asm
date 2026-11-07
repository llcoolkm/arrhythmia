; SPDX-License-Identifier: MIT
; Copyright (c) 2026 km
; Additive FM TONE+feedback packet and per-sample feedback target smoothing.
; Fixed vector entries keep calls small and preserve the startup/table placement.
live_tone_vector:
jmp @live_tone
live_init_vector:
jmp @live_init
live_smooth_vector:
jmp @live_smooth
live_reset_vector:
jmp @live_reset

; Queue registers y1/y0 hold word0/word1. Unsupported/invalid commands
; remain unchanged and are rejected by the original dispatcher.
; A0B000|fb (bits7..11 zero), original 21-bit TONE payload.
live_tone:
move y1,a
and #>$ffff80,a
cmp #>$a0b000,a
jne @live_tone_done
move y0,a
and #>$e00000,a
tst a
jne @live_tone_done
move y1,a
and #>$7f,a
move a1,x0
move #>$10204,y1
mpy x0,y1,a
asl #23,a,a
; Four records, including idle voices; onset initializes its own target.
move a1,x:>$541e
move a1,x:>$543e
move a1,x:>$545e
move a1,x:>$547e
move a1,x:>$580a
move #>$1,a
move a1,x:>$5809
; Normalize only a fully valid command; y0 remains the original payload.
move #>$a08000,y1
live_tone_done:
rts

live_init:
move x:>$5809,a
tst a
jeq @live_init_snapshot
move x:>$580a,a
jmp @live_init_store
live_init_snapshot:
move x:(r5+$17),a
live_init_store:
move a1,x:(r5+$17)
move a1,x:(r5+$1e)
rts
live_reset:
clr a
move a1,x:>$5809
move a1,x:>$580a
move a1,x:>$541e
move a1,x:>$543e
move a1,x:>$545e
move a1,x:>$547e
rts

; First-order 1/64 per sample, exactly settle within 63 Q23 units.
; Caller supplies difference in A, current in X0 and skips exact equality.
; Only A/X0 clobbered, both dead at the top of the original sample loop.
live_smooth:
cmp #>$40,a
jge @live_smooth_step
cmp #>$ffffc0,a
jle @live_smooth_step
move x:(r5+$1e),a
move a1,x:(r5+$17)
rts
live_smooth_step:
asr #6,a,a
add x0,a
move a1,x:(r5+$17)
live_smooth_done:
rts
