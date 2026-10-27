; SPDX-License-Identifier: MIT
; Copyright (c) 2026 km
; Four-voice FM instrument, envelopes, filter and command handling.

candidate_alpha_begin:
move x:>$5218,a
move a1,x:>$5229
rts
candidate_mix_begin:
move x:>$5218,a
move x:>$5229,b
sub b,a
tst a
jge @candidate_positive_step
add #>$1f,a
candidate_positive_step:
asr #5,a,a
move a1,x:>$522a
move x:>$5218,a
move x:>$5229,b
add b,a
rts
candidate_alpha_step:
move x:>$5229,a
move x:>$522a,b
add b,a
move a1,x:>$5229
move a1,y0
rts
candidate_mix_end:
move x:>$5218,a
move a1,x:>$5229
tst a
jne @candidate_mix_end_done
move a1,x:>$5200
move a1,x:>$5201
move a1,x:>$5202
move a1,x:>$5203
candidate_mix_end_done:
rts
candidate_state_guard:
move #>$400000,b
asl b
cmp b,a
jlt @candidate_state_lower
move #>$7fffff,a
move #>$ffffff,a0
jmp @candidate_state_count
candidate_state_lower:
neg b
cmp b,a
jge @candidate_state_done
move #>$800000,a
move #>$0,a0
candidate_state_count:
move x:>$522e,b
add #>$1,b
cmp #>$7fffff,b
jle @candidate_state_count_store
move #>$7fffff,b
candidate_state_count_store:
move b1,x:>$522e
candidate_state_done:
rts
candidate_wet_limit:
asl #5,a,a
tst a
jge @candidate_positive
neg a
move #>$1,b
move b1,x:>$522b
jmp @candidate_abs_ready
candidate_positive:
clr b
move b1,x:>$522b
candidate_abs_ready:
cmp #>$400000,a
jle @candidate_signed
move x:>$522c,b
add #>$1,b
cmp #>$7fffff,b
jle @candidate_compression_count
move #>$7fffff,b
candidate_compression_count:
move b1,x:>$522c
move #>$600000,b
asl b
cmp b,a
jgt @candidate_second_knee
sub #>$400000,a
asr #2,a,a
add #>$400000,a
jmp @candidate_signed
candidate_second_knee:
sub b,a
asr #4,a,a
add #>$600000,a
cmp #>$7c0000,a
jle @candidate_signed
move #>$7c0000,a
move x:>$522d,b
add #>$1,b
cmp #>$7fffff,b
jle @candidate_cap_count
move #>$7fffff,b
candidate_cap_count:
move b1,x:>$522d
candidate_signed:
move x:>$522b,b
tst b
jeq @candidate_limit_done
neg a
candidate_limit_done:
rnd a
rts

release_render:
jsr @fm_render_voice
move x:(r1+$7),a
cmp #>$5,a
jne @release_other_terminal
; Seven words per voice: Ehi, Elo, G0, C, S, count, E0. Indices 0..3.
move r1,a
sub #>$4e00,a
lsr #4,a
move a1,x0
asl #3,a,a
sub x0,a
add #>$5a00,a
move a1,r5
move #>$ffffff,m5
move #>$3f00,r3
move #>$ffffff,m3
move x:(r5+$2),r7
move #>$0,n7
move x:(r1+$e),a
move a1,x:>$5a1e
nop
do #>$20,>@release_samples_end
move x:(r5+$5),a
cmp #>$120,a
jgt @release_terminal
jeq @release_full_rate
release_entry_rate:
; rate = (MAX - C) / 2; rotation constants floor(Q*cos(pi/288)), floor(Q*sin(pi/288)).
move #>$7fffff,a
move x:(r5+$3),b
sub b,a
asr a
move a1,x0
move x:>$5a1e,y0
mpyr y0,x0,a
move a1,x:>$5a20
move x:(r5+$3),x0
move x:(r5+$4),x1
move #>$7ffe0c,y0
move #>$1656f,y1
mpy x0,y0,a
mac -x1,y1,a
rnd a
move a,x:(r5+$3)
mpy x1,y0,b
mac x0,y1,b
rnd b
move b,x:(r5+$4)
move x:(r5+$5),a
add #>$1,a
move a1,x:(r5+$5)
move x:>$5a20,y0
release_entry_rate_done:
jmp @release_apply_rate
release_full_rate:
move x:>$5a1e,y0
release_apply_rate:
release_step48:
; The E48 fractional product is shifted by 5 after the signed low-word correction.
move x:(r5+$1),a
eor #>$800000,a
move a1,x0
mpy x0,y0,a
move y0,b
add b,a
asr #24,a,a
move a,b
move a0,b0
move x:(r5),x0
mpy x0,y0,a
add b,a
asr #5,a,a
move x:(r5),b
move x:(r5+$1),b0
sub a,b
move b1,x:(r5)
move b0,x:(r5+$1)
release_step48_done:
move x:(r5),a
cmp #>$400,a
jge @release_regular_weight
move #>$121,a
move a1,x:(r5+$5)
jmp @release_terminal
release_regular_weight:
move a1,y0
jmp @release_store_weight
release_terminal:
move x:(r5+$5),a
cmp #>$140,a
jgt @release_zero
sub #>$121,a
add #>@release_cosine_weights,a
move a1,r4
move #>$ffffff,m4
nop
move p:(r4),y0
move x:(r5),x0
mpyr y0,x0,a
move a1,y0
move x:(r5+$5),a
add #>$1,a
move a1,x:(r5+$5)
jmp @release_store_weight
release_zero:
clr a
move a1,y0
release_store_weight:
move y0,x:>$5a1f
move y:(r3),x0
move y0,a
cmp #>$7fffff,a
jeq @release_unity
mpyr y0,x0,a
jmp @release_write
release_unity:
move x0,a
release_write:
move a,y:(r3)+
release_samples_end:
nop
move x:(r5+$5),a
cmp #>$140,a
jgt @release_complete
move #>$5,a
move a,x:(r1+$a)
move x:>$5a1f,y0
move x:(r5+$2),x0
mpyr y0,x0,a
move a,x:(r1+$9)
move x:(r5+$6),x0
mpyr y0,x0,a
move a,x:(r1+$4)
rts
release_complete:
clr a
move a,x:(r1+$a)
move a,x:(r1+$9)
move a,x:(r1+$4)
rts

release_capture:
; Called only at the existing accepted stage store. Stale generations and RELEASE during KILL are already excluded.
cmp #>$5,a
jne @release_capture_store
move x:(r0+$7),a
cmp #>$5,a
jeq @release_capture_store
move r4,x:>$5a1c
move m4,x:>$5a1d
move #>$ffffff,m4
move r0,a
sub #>$4e00,a
lsr #4,a
move a1,x0
asl #3,a,a
sub x0,a
add #>$5a00,a
move a1,r4
move #>$7fffff,a
move a,x:(r4)
move a,x:(r4+$3)
clr a
move a,x:(r4+$1)
move a,x:(r4+$4)
move a,x:(r4+$5)
move x:(r0+$5),a
move a,x:(r4+$2)
move x:(r0+$4),a
move a,x:(r4+$6)
move x:>$5a1d,m4
move x:>$5a1c,r4
release_capture_store:
move x1,a
move a,x:(r0+$7)
rts

release_other_terminal:
; Shared terminal shaping follows the single FM rendering call.
move x:(r1+$7),a
cmp #>$3,a
jeq @release_short_terminal
cmp #>$2,a
jne @release_done
move x:(r1+$a),a
tst a
jne @release_done
move x:(r1+$d),a
tst a
jne @release_done
release_short_terminal:
move #>$0,n7
move #>$3f00,r3
move #>@release_cosine_weights,r4
move #>$ffffff,m3
move #>$ffffff,m4
nop
do #>$20,>@release_short_end
move y:(r3),x0
move p:(r4)+,y0
move y0,a
cmp #>$7fffff,a
jeq @release_short_unity
mpyr y0,x0,a
jmp @release_short_store
release_short_unity:
move x0,a
release_short_store:
move a,y:(r3)+
release_short_end:
nop
release_done:
rts
release_cosine_weights:

; The following own cosine weights are inserted by tables.cosine32().
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
; Reserved zero padding keeps entry at P:$2200.
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop
nop


; FM instrument body.

entry:
move sr,x:>$4f00
move a2,x:>$4f01
move a1,x:>$4f02
move a0,x:>$4f03
move b2,x:>$4f04
move b1,x:>$4f05
move b0,x:>$4f06
move x0,x:>$4f07
move x1,x:>$4f08
move y0,x:>$4f09
move y1,x:>$4f0a
move r0,x:>$4f0b
move r1,x:>$4f0c
move r2,x:>$4f0d
move r3,x:>$4f0e
move r4,x:>$4f0f
move r5,x:>$4f10
move r6,x:>$4f11
move r7,x:>$4f12
move n0,x:>$4f13
move n1,x:>$4f14
move n2,x:>$4f15
move n3,x:>$4f16
move n4,x:>$4f17
move n5,x:>$4f18
move n6,x:>$4f19
move n7,x:>$4f1a
move m0,x:>$4f1b
move m1,x:>$4f1c
move m2,x:>$4f1d
move m3,x:>$4f1e
move m4,x:>$4f1f
move m5,x:>$4f20
move m6,x:>$4f21
move m7,x:>$4f22
move la,x:>$4f23
move lc,x:>$4f24
move x:>$4e90,a
cmp #>$c0a10d,a
jne @init_tables
move x:>$4e91,a
cmp #>$207,a
jne @init_tables
move x:>$4e92,a
cmp #>$524459,a
jne @init_tables
jmp @init_complete
init_tables:
clr a
move a1,x:>$4e92
move x:>$4e93,a
add #>$1,a
move a1,x:>$4e93
move #>$ffffff,m0
move #>$ffffff,m1
move #>$4d00,r1
clr a
do #>$50,>@init_queue_end
move a1,x:(r1)+
init_queue_end:
move #>$4e00,r1
do #>$76,>@init_state_end
move a1,x:(r1)+
init_state_end:
move #>$2da0,r0
move #>$5000,r1
do #>$100,>@init_ad_end
move p:(r0)+,x0
move x0,x:(r1)+
init_ad_end:
move #>$5100,r1
do #>$80,>@init_sustain_end
move p:(r0)+,x0
move x0,x:(r1)+
init_sustain_end:
move #>$5180,r1
do #>$80,>@init_release_end
move p:(r0)+,x0
move x0,x:(r1)+
init_release_end:
move #>$3400,r1
do #>$101,>@cf_quarter_0
move p:(r0)+,x0
move x0,y:(r1)+
cf_quarter_0:
move #>$309f,r0
do #>$100,>@cf_quarter_1
move p:(r0)-,x0
move x0,y:(r1)+
cf_quarter_1:
move #>$2fa1,r0
do #>$100,>@cf_quarter_2
move p:(r0)+,a
neg a
move a1,y:(r1)+
cf_quarter_2:
move #>$309f,r0
do #>$100,>@cf_quarter_3
move p:(r0)-,a
neg a
move a1,y:(r1)+
cf_quarter_3:
move #>$30a1,r0
init_saw_end:
move #>$5500,r1
do #>$100,>@tone_init_mapping_end
move p:(r0)+,x0
move x0,x:(r1)+
tone_init_mapping_end:
move #>$5700,r1
do #>$80,>@tone_init_depth_end
move p:(r0)+,x0
move x0,x:(r1)+
tone_init_depth_end:
move #>$5600,r1
clr a
do #>$d,>@tone_init_scratch_end
move a1,x:(r1)+
tone_init_scratch_end:
move #>$ffffff,a
move a1,x:>$4e95
move a1,x:>$4e96
jsr @fm_reset_all
move #>$5200,r1
clr a
do #>$2f,>@instrument_init_filter_end
move a1,x:(r1)+
instrument_init_filter_end:
move a1,x:>$4e94
jsr @filter_reset
move #>$88889,a
move a1,x:>$4e80
move #>$7fb4b6,a
move a1,x:>$4e81
move #>$408102,a
move a1,x:>$4e82
move #>$16fa5e,a
move a1,x:>$4e83
move #>$c0a10d,a
move a1,x:>$4e90
move #>$207,a
move a1,x:>$4e91
move #>$524459,a
move a1,x:>$4e92
init_complete:
jsr @queue_poll
jsr @d_handoff
jsr @filter_prepare
jsr @fm_block_begin
movep y:<<$ffffd0,a
and #<$1,a
asl #5,a,a
add #>$306b0,a
move a,r0
move #>$ffffff,m0
nop
    move #>$ffffff,m0
    move #>$ffffff,m1
    move #>$ffffff,m7
    move #>$ff,m4
    move r0,r2
    clr a
    do #<$20,>@v_clear_end
    move a,y:(r0)+
v_clear_end:
    move #>$4e00,r1
    move #<$10,n1
    do #<$4,>@v_done
    move r2,r0
    move x:(r1+$7),a
    tst a
    jeq @v_idle
    cmp #<$1,a
    jeq @v_attack
    cmp #<$2,a
    jeq @v_decay
    cmp #<$4,a
    jeq @v_sustain
    cmp #<$5,a
    jeq @v_release
    clr a
    move a,x:(r1+$a)
    jmp @v_envelope_done
v_sustain:
    move x:(r1+$d),a
    move #>$4,x0
    move x0,x:(r1+$a)
    jmp @v_envelope_done
v_release:
    move x:(r1+$4),a
    move #>$5,x0
    move x0,x:(r1+$a)
    jmp @v_envelope_done
v_attack:
    move x:(r1+$4),a
    move x:(r1+$b),x0
    add x0,a
    cmp #>$7fffff,a
    jlt @v_attack_continue
    move #>$7fffff,a
    move #>$2,x0
    move x0,x:(r1+$a)
    jmp @v_envelope_done
v_attack_continue:
    move #>$1,x0
    move x0,x:(r1+$a)
    jmp @v_envelope_done
v_decay:
    move x:(r1+$4),x0
    move x:(r1+$c),y0
    mpyr y0,x0,a
    move x:(r1+$d),b
    tst b
    jne @v_decay_target
    cmp #>$400,a
    jge @v_decay_target
    clr a
    move a,x:(r1+$a)
    jmp @v_envelope_done
v_decay_target:
    cmp b,a
    jgt @v_decay_continue
    move b1,a
    move #>$4,x0
    move x0,x:(r1+$a)
    jmp @v_envelope_done
v_decay_continue:
    move #>$2,x0
    move x0,x:(r1+$a)
v_envelope_done:
    move a,x:(r1+$4)
    move a1,x0
    move x:(r1+$6),y0
    mpyr y0,x0,a
    move a,x:(r1+$9)
    move x:(r1+$5),b
    move b1,r7
    sub b,a
    asr #5,a,a
    move a1,n7
    move #>$ffffff,m7
    jsr @release_render
    move #>$3f00,r3
    move #>$ffffff,m3
    nop
    do #<$20,>@instrument_sample_end
    move y:(r3)+,y0
    move r7,y1
    move (r7)+n7
    mpyr y1,y0,a
    move y:(r0),y0
    add y0,a
    move a,y:(r0)+
instrument_sample_end:
v_finished:
    move x:(r1+$9),a
    move a,x:(r1+$5)
    move x:(r1+$a),a
    move a,x:(r1+$7)
    tst a
    jne @v_voice_done
    clr a
    move a,x:(r1+$4)
    move a,x:(r1+$5)
    jmp @v_voice_done
v_idle:
    nop
v_voice_done:
    move (r1)+n1
v_poly_last:
    nop
v_done:
    nop
    move r2,r0
    jsr @filter_process
    move x:>$4e94,a
    tst a
    jeq @restore
    ; Forced cut ramps the final filtered mix, then erases its residual state.
    ; Per-voice KILL deliberately does not reset a shared post-sum filter.
    movep y:<<$ffffd0,a
    and #<$1,a
    asl #5,a,a
    add #>$306b0,a
    move a,r0
    move #>$ffffff,m0
    move #>$7c0000,r3
    move #>$ffffff,m3
    move #>$fc0000,n3
    nop
    do #<$20,>@instrument_cut_end
    move r3,y1
    move (r3)+n3
    move y:(r0),y0
    mpyr y1,y0,a
    move a,y:(r0)+
instrument_cut_end:
    clr a
    move a1,x:>$4e94
    jsr @filter_reset
restore:
move x:>$4f24,lc
move x:>$4f23,la
move x:>$4f22,m7
move x:>$4f21,m6
move x:>$4f20,m5
move x:>$4f1f,m4
move x:>$4f1e,m3
move x:>$4f1d,m2
move x:>$4f1c,m1
move x:>$4f1b,m0
move x:>$4f1a,n7
move x:>$4f19,n6
move x:>$4f18,n5
move x:>$4f17,n4
move x:>$4f16,n3
move x:>$4f15,n2
move x:>$4f14,n1
move x:>$4f13,n0
move x:>$4f12,r7
move x:>$4f11,r6
move x:>$4f10,r5
move x:>$4f0f,r4
move x:>$4f0e,r3
move x:>$4f0d,r2
move x:>$4f0c,r1
move x:>$4f0b,r0
move x:>$4f0a,y1
move x:>$4f09,y0
move x:>$4f08,x1
move x:>$4f07,x0
move x:>$4f06,b0
move x:>$4f05,b1
move x:>$4f04,b2
move x:>$4f03,a0
move x:>$4f02,a1
move x:>$4f01,a2
move x:>$4f00,sr
jsr $367
rts
queue_reset:
move #>$ffffff,m0
move #>$4e00,r0
clr a
do #>$76,>@reset_state_end
move a1,x:(r0)+
reset_state_end:
move #>$ffffff,b
move b1,x:>$4e95
move b1,x:>$4e96
move a1,x:>$4e94
jsr @fm_reset_all
jsr @filter_reset
move #>$88889,a
move a1,x:>$4e80
move #>$7fb4b6,a
move a1,x:>$4e81
move #>$408102,a
move a1,x:>$4e82
move #>$16fa5e,a
move a1,x:>$4e83
rts
queue_dispatch:
move y1,a
cmp #>$a09000,a
jeq @cf_patch_fm
cmp #>$a0a000,a
jeq @cf_mod_ad
move y1,a
cmp #>$a08000,a
jeq @tone_dispatch
move y1,a
and #>$fc0000,a
move a1,a
cmp #>$a40000,a
jeq @d_onset
move y1,a
and #>$ff0000,a
move a1,a
cmp #>$a00000,a
jne @d_fallback
move y1,a
and #>$f000,a
move a1,a
cmp #>$1000,a
jeq @d_kill
cmp #>$2000,a
jeq @d_release
cmp #>$3000,a
jeq @d_alloff
cmp #>$6000,a
jeq @d_patch_all
cmp #>$7000,a
jeq @d_patch_sr
jmp @d_fallback
d_onset:
jclr #15,y1,>@d_fallback
move y0,a
lsr #11,a
and #>$1f,a
move a1,a
cmp #>$18,a
jge @d_fallback
move y0,a
lsr #4,a
and #>$7f,a
move a1,a
tst a
jeq @d_fallback
move #>$1,b
move y1,a
lsr #16,a
and #>$3,a
move a1,a
jmp @d_select
d_release:
move #>$5,b
jmp @d_off_validate
d_kill:
move #>$3,b
d_off_validate:
move y1,a
and #>$3ff,a
move a1,a
tst a
jne @d_fallback
move y0,a
and #>$fffff0,a
move a1,a
tst a
jne @d_fallback
move y1,a
lsr #10,a
and #>$3,a
move a1,a
d_select:
move r1,x:>$4e71
move a1,x0
lsl #3,a
add x0,a
add x0,a
add #>$4e40,a
move a1,r1
move x0,a
lsl #4,a
add #>$4e00,a
move a1,r0
move m0,x:>$4e74
move #>$ffffff,m0
move b1,a
cmp #>$1,a
jeq @d_onset_selected
jmp @d_off_selected
d_onset_selected:
move y0,a
and #>$f,a
move a1,x1
move x:(r1),a
add #>$1,a
and #>$f,a
move a1,a
cmp x1,a
jne @d_restore_fallback
move x1,x:(r1)
move y1,a
and #>$ffff,a
move a1,a
lsl #8,a
move a1,x1
move y0,a
lsr #16,a
and #>$ff,a
move a1,a
add x1,a
move a1,x1
move y0,a
lsr #11,a
and #>$1f,a
move a1,x0
clr a
move x1,a0
move x0,b
tst b
jeq @d_unshifted
rep x0
asl a
d_unshifted:
move a1,x:(r1+$2)
move a0,x:(r1+$3)
clr b
move #>$88888,b1
move #>$888889,b0
cmp b,a
jlt @d_bank15
move #>$124924,b1
move #>$924925,b0
cmp b,a
jlt @d_bank7
move #>$2aaaaa,b1
move #>$aaaaab,b0
cmp b,a
jlt @d_bank3
move #>$3300,x1
jmp @d_bank_done
d_bank15:
move #>$3000,x1
jmp @d_bank_done
d_bank7:
move #>$3100,x1
jmp @d_bank_done
d_bank3:
move #>$3200,x1
d_bank_done:
move x1,x:(r1+$5)
move y0,a
lsr #4,a
and #>$7f,a
move a1,a
lsl #16,a
move a1,y0
move #>$100000,x0
mpyr y0,x0,a
move a,x:(r1+$4)
move x:>$4e80,a
move a,x:(r1+$6)
move x:>$4e81,a
move a,x:(r1+$7)
move x:>$4e82,a
move a,x:(r1+$8)
move x:>$4e83,a
move a,x:(r1+$9)
move #>$1,a
move a,x:(r1+$1)
jsr @me_snapshot_pending
jsr @cf_snapshot_fm
move x:(r0+$7),a
tst a
jeq @d_onset_idle
move #>$3,a
move a,x:(r0+$7)
jmp @d_restore_accept
d_onset_idle:
jsr @d_install
jmp @d_restore_accept
d_off_selected:
move y0,a
and #>$f,a
move a1,x1
move x:(r1),a
cmp x1,a
jne @d_off_check_active
clr a
move a,x:(r1+$1)
d_off_check_active:
move x:(r0+$f),a
cmp x1,a
jne @d_restore_accept
move x:(r0+$7),a
tst a
jeq @d_restore_accept
move b1,x1
cmp #>$5,b
jne @d_off_store_stage
cmp #<$3,a
jeq @d_restore_accept
d_off_store_stage:
move x1,a
jsr @release_capture
jmp @d_restore_accept
d_alloff:
move y1,a
and #>$fff,a
move a1,a
tst a
jne @d_fallback
move y0,a
tst a
jne @d_fallback
move #>$1,a
move a1,x:>$4e94
move r1,x:>$4e71
move n1,x:>$4e72
move m0,x:>$4e74
move m1,x:>$4e75
move #>$ffffff,m0
move #>$ffffff,m1
move #>$4e00,r0
move #>$4e40,r1
move #<$10,n0
move #<$a,n1
do #<$4,>@d_all_end
move x:(r0+$7),a
tst a
jeq @d_all_idle
move #>$3,a
move a,x:(r0+$7)
d_all_idle:
clr a
move a,x:(r1)
move a,x:(r1+$1)
move (r0)+n0
move (r1)+n1
d_all_end:
nop
move x:>$4e75,m1
move x:>$4e72,n1
jmp @d_restore_accept
d_restore_accept:
move x:>$4e71,r1
move x:>$4e74,m0
rts
d_restore_fallback:
move x:>$4e71,r1
move x:>$4e74,m0
d_fallback:
rts
d_handoff:
move n0,x:>$4e70
move m0,x:>$4e74
move m1,x:>$4e75
move #>$ffffff,m0
move #>$ffffff,m1
move #>$4e00,r0
move #>$4e40,r1
move #<$10,n0
move #<$a,n1
do #<$4,>@d_handoff_end
move x:(r1+$1),a
tst a
jeq @d_handoff_next
move x:(r0+$7),a
tst a
jne @d_handoff_next
jsr @d_install
d_handoff_next:
move (r0)+n0
move (r1)+n1
d_handoff_end:
nop
move x:>$4e75,m1
move x:>$4e74,m0
move x:>$4e70,n0
rts
d_install:
clr a
move a,x:(r0+$0)
move a,x:(r0+$1)
move a,x:(r0+$4)
move a,x:(r0+$5)
move a,x:(r1+$1)
move x:(r1+$2),a
move a,x:(r0+$2)
move x:(r1+$3),a
move a,x:(r0+$3)
move x:(r1+$4),a
move a,x:(r0+$6)
move x:(r1+$5),a
move a,x:(r0+$8)
move x:(r1+$6),a
move a,x:(r0+$b)
move x:(r1+$7),a
move a,x:(r0+$c)
move x:(r1+$8),a
move a,x:(r0+$d)
move x:(r1+$9),a
move a,x:(r0+$e)
move x:(r1),a
move a,x:(r0+$f)
move #>$1,a
move a,x:(r0+$7)
jsr @fm_note_start
rts
d_cancel_pending:
clr a
move a,x:>$4e41
move a,x:>$4e4b
move a,x:>$4e55
move a,x:>$4e5f
rts
d_patch_all:
d_patch_sr:
move y1,a
and #>$fff,a
move a1,a
tst a
jne @d_fallback
move y0,a
and #>$ffc000,a
move a1,a
tst a
jne @d_fallback
move m0,x:>$4e74
move #>$ffffff,m0
move y1,a
and #>$f000,a
move a1,a
cmp #>$6000,a
jne @d_sr_lookup
move y0,a
and #>$7f,a
move a1,a
add #>$5000,a
move a1,r0
nop
move x:(r0),a
move a,x:>$4e80
move y0,a
lsr #7,a
and #>$7f,a
move a1,a
add #>$5080,a
move a1,r0
nop
move x:(r0),a
move a,x:>$4e81
move x:>$4e74,m0
rts
d_sr_lookup:
move y0,a
and #>$7f,a
move a1,a
add #>$5100,a
move a1,r0
nop
move x:(r0),a
move a,x:>$4e82
move y0,a
lsr #7,a
and #>$7f,a
move a1,a
add #>$5180,a
move a1,r0
nop
move x:(r0),a
move a,x:>$4e83
move x:>$4e74,m0
rts

queue_poll:
    move x:>$4d07,a
    add #>$1,a
    move a1,x:>$4d07
    move x:>$4d00,a
    move x:>$4d01,b
    cmp b,a
    jne @queue_new_epoch
    move x:>$4d04,a
    tst a
    jne @queue_done
    move x:>$4d02,a
    move x:>$4d03,b
    sub b,a
    and #>$ffffff,a
    move a1,x1
    tst a
    jeq @queue_done
    ; Unsigned 24-bit distance: high bits set means >31, never a negative gap.
    move x1,a
    and #>$ffffe0,a
    tst a
    jne @queue_fault
    move x1,a
    and #>$1f,a
    cmp #>$10,a
    jgt @queue_fault
    move x:>$4d03,a
    and #>$f,a
    lsl #2,a
    add #>$4d10,a
    move a1,r0
    move #>$ffffff,m0
    nop
    ; Publication order is payload/epoch, commitseq, then PROD. Read metadata
    ; before either command word, and call the decoder only after both checks.
    move x:>$4d03,b
    add #>$1,b
    and #>$ffffff,b
    move b1,b
    move x:(r0+$3),a
    cmp b,a
    jne @queue_fault
    move x:(r0),a
    move x:>$4d01,b
    cmp b,a
    jne @queue_fault
    move x:(r0+$1),y1
    move x:(r0+$2),y0
    jsr @queue_dispatch
    move x:>$4d03,a
    add #>$1,a
    move a1,x:>$4d03
    move x:>$4d05,a
    add #>$1,a
    move a1,x:>$4d05
queue_done:
    rts
queue_new_epoch:
    ; ARM keeps admission closed and REQ stable until it observes this ACK.
    jsr @queue_reset
    move x:>$4d02,a
    move a1,x:>$4d03
    clr a
    move a1,x:>$4d04
    move x:>$4d00,a
    move a1,x:>$4d01
    rts
queue_fault:
    ; Sticky fault: reset once, refuse all entries until a fresh REQ epoch.
    jsr @queue_reset
    move #>$1,a
    move a1,x:>$4d04
    move x:>$4d06,a
    add #>$1,a
    move a1,x:>$4d06
    rts


fm_reset_all:
move #>$5400,r5
move #>$ffffff,m5
clr a
do #>$80,>@fm_reset_voices_done
move a1,x:(r5)+
fm_reset_voices_done:
move #>$5300,r5
do #>$d,>@fm_reset_globals_done
move a1,x:(r5)+
fm_reset_globals_done:
move #>$88889,a
move a1,x:>$5800
move #>$7fb4b6,a
move a1,x:>$5801
move #>$7fffff,a
move a1,x:>$5802
move #>$5810,r4
move #>$ffffff,m4
clr a
do #>$10,>@me_reset_pending_done
move a1,x:(r4)+
me_reset_pending_done:
move #>$5820,r4
clr a
do #>$4,>@cf_reset_pending_done
move a1,x:(r4)+
cf_reset_pending_done:
move a1,x:>$5808
move #>$5a00,r4
move #>$ffffff,m4
clr a
nop
do #>$21,>@release_reset_done
move a1,x:(r4)+
release_reset_done:
rts
fm_note_start:
jsr @cf_load_pending_fm
move r0,a
sub #>$4e00,a
lsr #4,a
move a1,x0
asl #5,a,a
add #>$5400,a
move a1,r5
move r5,r3
move #>$ffffff,m5
clr a
do #>$20,>@fm_note_clear_done
move a1,x:(r5)+
fm_note_clear_done:
move r3,r5
jsr @study_increments
fm_increments_ready:
clr b
move x:(r5+$8),x1
move x:(r5+$9),x0
sub x,b
move b1,x:(r5+$0)
move b0,x:(r5+$1)
clr b
move x:(r5+$a),x1
move x:(r5+$b),x0
sub x,b
move b1,x:(r5+$2)
move b0,x:(r5+$3)
clr b
move x:(r5+$c),x1
move x:(r5+$d),x0
sub x,b
move b1,x:(r5+$4)
move b0,x:(r5+$5)
clr b
move x:(r5+$e),x1
move x:(r5+$f),x0
sub x,b
move b1,x:(r5+$6)
move b0,x:(r5+$7)
jsr @study_levels

move x:>$5302,a
move a1,x:(r5+$16)
move x:>$5303,x0
move #>$10204,y0
mpy x0,y0,a
asl #23,a,a
move a1,x:(r5+$17)
jsr @ks_note_cap
jsr @me_note_start
rts
fm_block_begin:
move x:>$5301,a
move a1,x:>$5305
move x:>$5300,b
sub a,b
asr #5,b,b
move b1,x:>$5304
move x:>$5300,a
move a1,x:>$5301
rts
fm_render_voice:
move r1,a
sub #>$4e00,a
lsr #4,a
move a1,x0
asl #5,a,a
add #>$5400,a
move a1,r5
move #>$ffffff,m5
move #>$3f00,r3
move #>$ffffff,m3
jsr @ks_prepare_voice_depth
jsr @me_prepare
do #<$20,>@fm_samples_done
move x:(r5+$6),b1
move x:(r5+$7),b0
move x:(r5+$e),x1
move x:(r5+$f),x0
add x,b
move b1,x:(r5+$6)
move b0,x:(r5+$7)
move x:(r5+$17),a
tst a
jeq @fm_pure_3
move x:(r5+$14),a
move x:(r5+$15),x0
add x0,a
asr #3,a,a
move a1,x0
move x:(r5+$17),y0
mpyr y0,x0,a
move a1,x0
move b1,a
add x0,a
jmp @fm_phase_3
fm_pure_3:
move b1,a
fm_phase_3:
jsr @fm_lookup
move x:(r5+$13),x0
mpyr y0,x0,a
move a1,x:>$530b
move x:(r5+$15),a
move a1,x:(r5+$14)
move x:>$530b,a
move a1,x:(r5+$15)
move x:(r5+$4),b1
move x:(r5+$5),b0
move x:(r5+$c),x1
move x:(r5+$d),x0
add x,b
move b1,x:(r5+$4)
move b0,x:(r5+$5)
move x:>$530b,y0
move x:>$5306,x0
mpyr y0,x0,a
asl a
move a1,x0
move b1,a
add x0,a
jsr @fm_lookup
move x:(r5+$12),x0
mpyr y0,x0,a
move a1,x:>$530a
move x:(r5+$16),a
tst a
jne @study_dual_op1
move x:(r5+$2),b1
move x:(r5+$3),b0
move x:(r5+$a),x1
move x:(r5+$b),x0
add x,b
move b1,x:(r5+$2)
move b0,x:(r5+$3)
move x:>$530a,y0
move x:>$5306,x0
mpyr y0,x0,a
asl a
move a1,x0
move b1,a
add x0,a
jsr @fm_lookup
move x:(r5+$11),x0
mpyr y0,x0,a
move a1,x:>$5309
jmp @study_op1_done
study_dual_op1:
move x:(r5+$2),b1
move x:(r5+$3),b0
move x:(r5+$a),x1
move x:(r5+$b),x0
add x,b
move b1,x:(r5+$2)
move b0,x:(r5+$3)
move b1,a
jsr @fm_lookup
move x:(r5+$11),x0
mpyr y0,x0,a
move a1,x:>$5309
study_op1_done:
move x:(r5+$0),b1
move x:(r5+$1),b0
move x:(r5+$8),x1
move x:(r5+$9),x0
add x,b
move b1,x:(r5+$0)
move b0,x:(r5+$1)
move x:>$5309,y0
move x:>$5306,x0
mpyr y0,x0,a
asl a
move a1,x0
move b1,a
add x0,a
jsr @fm_lookup
move x:(r5+$10),x0
mpyr y0,x0,a
move a1,x:>$5308
move x:>$5308,a
move x:(r5+$16),b
tst b
jeq @study_write
move x:>$530a,x0
add x0,a
jmp @study_write
study_write:
move a1,y:(r3)+
move x:>$5306,a
move x:>$5307,x0
add x0,a
move a1,x:>$5306
fm_samples_done:
rts
fm_lookup:
move a1,x1
lsr #14,a
and #>$3ff,a
add #>$3400,a
move a1,r4
move #>$ffffff,m4
nop
move y:(r4)+,a
move a1,y1
move y:(r4),b
sub a,b
move b1,x0
move x1,a
and #>$3fff,a
asl #9,a,a
move a1,y0
mpyr y0,x0,b
move y1,a
add a,b
move b1,y0
rts
ks_note_cap:
move x:>$5804,a
tst a
jne @candidate_zero_cap
move x:>$5805,x0
move #>$2aaaab,y0
mpyr y0,x0,a
move x:(r0+$2),b
cmp b,a
jge @candidate_cap_ready
move b1,a
candidate_cap_ready:
cmp #>$2c9b7,a
jle @ks_full
cmp #>$5936e,a
jle @ks_segment_0
cmp #>$b26dc,a
jle @ks_segment_1
cmp #>$164db8,a
jle @ks_segment_2
cmp #>$2a1a84,a
jle @ks_segment_3
clr b
jmp @ks_store
ks_full:
move #>$7fffff,b
jmp @ks_store
ks_segment_0:
sub #>$2c9b7,a
asl #5,a,a
move a1,x0
move #>$5bd2e4,y0
mpyr y0,x0,a
move #>$7fffff,b
sub a,b
jmp @ks_store
ks_segment_1:
sub #>$5936e,a
asl #3,a,a
move a1,x0
move #>$5bd2e4,y0
mpyr y0,x0,a
move #>$3fffff,b
sub a,b
jmp @ks_store
ks_segment_2:
sub #>$b26dc,a
asl #1,a,a
move a1,x0
move #>$5bd2e4,y0
mpyr y0,x0,a
move #>$1fffff,b
sub a,b
jmp @ks_store
ks_segment_3:
sub #>$164db8,a
move a1,x0
move #>$676f35,y0
mpyr y0,x0,a
move #>$fffff,b
sub a,b
jmp @ks_store
ks_store:
move b1,y1
move x:>$5805,a
cmp #>$2aaaab,a
jle @candidate_taper_done
move a1,x0
move #>$7fffff,a
sub x0,a
add #>$1,a
move a1,x0
asl a
add x0,a
asr a
move a1,x0
move a1,y0
mpyr y0,x0,a
move a1,x0
move y1,y0
mpyr y0,x0,b
candidate_taper_done:
tst b
jgt @ks_positive
clr b
ks_positive:
move b1,x:(r5+$18)
rts
ks_prepare_voice_depth:
move x:(r5+$18),y0
move y0,a
cmp #>$7fffff,a
jeq @ks_unity_depth
move x:>$5305,x0
mpyr y0,x0,a
move a1,x:>$5306
move x:>$5304,x0
mpyr y0,x0,a
move a1,x:>$5307
rts
ks_unity_depth:
move x:>$5305,a
move a1,x:>$5306
move x:>$5304,a
move a1,x:>$5307
rts

study_increments:
move x:>$530c,a
and #>$f,a
asl #2,a,a
add #>$3221,a
move a1,r6
move #>$ffffff,m6
clr a
move a1,x:>$5804
move a1,x:>$5805
move x:(r0+$2),x1
move x:(r0+$3),x0
move p:(r6)+,y0
clr b
rep y0
add x,b
asr b
move b2,a
tst a
jne @study_over_0
move b1,a
tst a
jlt @study_over_0
move x:>$5805,a
cmp b,a
jge @study_max_0
move b1,x:>$5805
study_max_0:
jmp @study_store_0
study_over_0:
move #>$1,a
move a1,x:>$5804
move #>$7fffff,a
move a1,x:>$5805
study_store_0:
move b1,x:(r5+$8)
move b0,x:(r5+$9)
move p:(r6)+,y0
study_ratio_1:
clr b
rep y0
add x,b
asr b
move b2,a
tst a
jne @study_over_1
move b1,a
tst a
jlt @study_over_1
move x:>$5805,a
cmp b,a
jge @study_max_1
move b1,x:>$5805
study_max_1:
jmp @study_store_1
study_over_1:
move #>$1,a
move a1,x:>$5804
move #>$7fffff,a
move a1,x:>$5805
study_store_1:
move b1,x:(r5+$a)
move b0,x:(r5+$b)
move p:(r6)+,y0
move x:>$5302,a
cmp #>$1,a
jeq @study_unity_2
jmp @study_ratio_2
study_unity_2:
move #>$2,y0
study_ratio_2:
clr b
rep y0
add x,b
asr b
move b2,a
tst a
jne @study_over_2
move b1,a
tst a
jlt @study_over_2
move x:>$5805,a
cmp b,a
jge @study_max_2
move b1,x:>$5805
study_max_2:
jmp @study_store_2
study_over_2:
move #>$1,a
move a1,x:>$5804
move #>$7fffff,a
move a1,x:>$5805
study_store_2:
move b1,x:(r5+$c)
move b0,x:(r5+$d)
move p:(r6)+,y0
study_ratio_3:
clr b
rep y0
add x,b
asr b
move b2,a
tst a
jne @study_over_3
move b1,a
tst a
jlt @study_over_3
move x:>$5805,a
cmp b,a
jge @study_max_3
move b1,x:>$5805
study_max_3:
jmp @study_store_3
study_over_3:
move #>$1,a
move a1,x:>$5804
move #>$7fffff,a
move a1,x:>$5805
study_store_3:
move b1,x:(r5+$e)
move b0,x:(r5+$f)
rts
study_levels:
move x:>$5302,a
cmp #>$0,a
jeq @study_levels_0
cmp #>$1,a
jeq @study_levels_1
jmp @study_levels_1
study_levels_0:
move #>$7fffff,a
move a1,x:(r5+$10)
move #>$400000,a
move a1,x:(r5+$11)
move #>$80000,a
move a1,x:(r5+$12)
move #>$400000,a
move a1,x:(r5+$13)
rts
study_levels_1:
move #>$3fffff,a
move a1,x:(r5+$10)
move #>$400000,a
move a1,x:(r5+$11)
move #>$3fffff,a
move a1,x:(r5+$12)
move #>$400000,a
move a1,x:(r5+$13)
rts
candidate_zero_cap:
clr b
jmp @ks_store

me_snapshot_pending:
move r0,a
sub #>$4e00,a
lsr #4,a
asl #2,a,a
add #>$5810,a
move a1,r4
move #>$ffffff,m4
move x:>$5800,a
move a1,x:(r4+$0)
move x:>$5801,a
move a1,x:(r4+$1)
move x:>$5802,a
move a1,x:(r4+$2)
rts
me_note_start:
clr a
move a1,x:(r5+$19)
move #>$1,a
move a1,x:(r5+$1a)
move r0,a
sub #>$4e00,a
lsr #4,a
asl #2,a,a
add #>$5810,a
move a1,r4
move #>$ffffff,m4
move x:(r4+$0),a
move a1,x:(r5+$1b)
move x:(r4+$1),a
move a1,x:(r5+$1c)
move x:(r4+$2),a
move a1,x:(r5+$1d)
rts
me_prepare:
move x:(r5+$19),a
move a1,x:>$5806
move x:(r5+$1a),b
cmp #<$1,b
jeq @me_attack
cmp #<$2,b
jeq @me_decay
jmp @me_scale
me_attack:
move x:(r5+$19),a
move x:(r5+$1b),x0
add x0,a
cmp #>$7fffff,a
jlt @me_attack_continue
move #>$7fffff,a
move #>$2,b
move b1,x:(r5+$1a)
me_attack_continue:
move a1,x:(r5+$19)
jmp @me_scale
me_decay:
move x:(r5+$19),y0
move x:(r5+$1c),x0
mpyr y0,x0,a
move x:(r5+$1d),b
cmp b,a
jgt @me_decay_continue
move b1,a
move #>$4,b
move b1,x:(r5+$1a)
me_decay_continue:
move a1,x:(r5+$19)
jmp @me_scale
me_scale:
move x:>$5806,y0
move x:>$5306,x0
mpyr y0,x0,a
move a1,x:>$5807
move x:>$5307,a
asl #5,a,a
move x:>$5306,x0
add x0,a
move a1,x0
move x:(r5+$19),y0
mpyr y0,x0,b
move x:>$5807,a
sub a,b
asr #5,b,b
move b1,x:>$5307
move a1,x:>$5306
rts

filter_reset:
clr a
move a1,x:>$5200
clr a
move a1,x:>$5201
clr a
move a1,x:>$5202
clr a
move a1,x:>$5203
clr a
move a1,x:>$5218
move a1,x:>$5229
move a1,x:>$522a
rts
filter_prepare:
jsr @candidate_alpha_begin
move x:>$5215,a
move a1,x:>$5221
and #>1,a
jne @filter_latch_done
move x:>$5217,b
move x:>$5221,a
cmp b,a
jeq @filter_latch_done
move x:>$5210,a
move a1,x:>$521a
move x:>$5211,a
move a1,x:>$521b
move x:>$5212,a
move a1,x:>$521c
move x:>$5213,a
move a1,x:>$521d
move x:>$5214,a
move a1,x:>$521e
move x:>$5215,a
move x:>$5221,b
cmp b,a
jne @filter_latch_done
move x:>$521a,a
move a1,x:>$5224
move x:>$521b,a
move a1,x:>$5225
move x:>$521c,a
move a1,x:>$5226
move x:>$521d,a
move a1,x:>$5227
move x:>$521e,a
move a1,x:>$5228
move x:>$5221,a
move a1,x:>$5217
filter_latch_done:
move x:>$5218,a
tst a
jne @filter_smooth_start
move x:>$5228,a
tst a
jeq @filter_smooth_start
move x:>$5224,a
move a1,x:>$5204
move x:>$5225,a
move a1,x:>$5205
move x:>$5226,a
move a1,x:>$5206
move x:>$5227,a
move a1,x:>$520f
jmp @filter_smoothing_done
filter_smooth_start:
move x:>$5224,a
move x:>$5204,b
sub b,a
move a1,x0
asr #2,a,a
move a1,x1
move x1,a
tst a
jne @filter_smooth_0_done
move x0,a
tst a
jne @filter_smooth_0_target
jmp @filter_smooth_0_done
filter_smooth_0_target:
move x:>$5224,a
move a1,x:>$5204
jmp @filter_smooth_0_end
filter_smooth_0_done:
move x:>$5204,a
add x1,a
move a1,x:>$5204
filter_smooth_0_end:
move x:>$5225,a
move x:>$5205,b
sub b,a
move a1,x0
asr #2,a,a
move a1,x1
move x1,a
tst a
jne @filter_smooth_1_done
move x0,a
tst a
jne @filter_smooth_1_target
jmp @filter_smooth_1_done
filter_smooth_1_target:
move x:>$5225,a
move a1,x:>$5205
jmp @filter_smooth_1_end
filter_smooth_1_done:
move x:>$5205,a
add x1,a
move a1,x:>$5205
filter_smooth_1_end:
move x:>$5226,a
move x:>$5206,b
sub b,a
move a1,x0
asr #2,a,a
move a1,x1
move x1,a
tst a
jne @filter_smooth_2_done
move x0,a
tst a
jne @filter_smooth_2_target
jmp @filter_smooth_2_done
filter_smooth_2_target:
move x:>$5226,a
move a1,x:>$5206
jmp @filter_smooth_2_end
filter_smooth_2_done:
move x:>$5206,a
add x1,a
move a1,x:>$5206
filter_smooth_2_end:
move x:>$5227,a
move x:>$520f,b
sub b,a
move a1,x0
asr #2,a,a
move a1,x1
move x1,a
tst a
jne @filter_smooth_3_done
move x0,a
tst a
jne @filter_smooth_3_target
jmp @filter_smooth_3_done
filter_smooth_3_target:
move x:>$5227,a
move a1,x:>$520f
jmp @filter_smooth_3_end
filter_smooth_3_done:
move x:>$520f,a
add x1,a
move a1,x:>$520f
filter_smooth_3_end:
filter_smoothing_done:
move x:>$5228,a
tst a
jeq @filter_fade_out
move x:>$5218,a
add #>$100000,a
cmp #>$7fffff,a
jlt @filter_alpha_store
move #>$7fffff,a
jmp @filter_alpha_store
filter_fade_out:
move x:>$5218,a
sub #>$100000,a
jgt @filter_alpha_store
clr a
filter_alpha_store:
move a1,x:>$5218
filter_prepare_done:
rts
filter_process:
move #>$ffffff,m0
jsr @candidate_mix_begin
tst a
jeq @filter_done
nop
nop
nop
do #<$20,>@filter_done
move y:(r0),x0
move x0,x:>$521f
move x:>$520f,y0
mpy x0,y0,a
move x:>$5202,b
move x:>$5203,b0
sub b,a
jsr @candidate_state_guard
move a1,x:>$5207
move a0,x:>$5208
move x:>$5201,a
eor #>$800000,a
move a1,x0
move x:>$5204,y0
mpy x0,y0,a
move y0,b
add b,a
asr #24,a,a
move a,b
move a0,b0
move x:>$5200,x0
mpy x0,y0,a
add b,a
jsr @candidate_state_guard
move a1,x:>$5209
move a0,x:>$520a
move x:>$5208,a
eor #>$800000,a
move a1,x0
move x:>$5205,y0
mpy x0,y0,a
move y0,b
add b,a
asr #24,a,a
move a,b
move a0,b0
move x:>$5207,x0
mpy x0,y0,a
add b,a
move x:>$5209,b
move x:>$520a,b0
add b,a
jsr @candidate_state_guard
move a1,x:>$520b
move a0,x:>$520c
move x:>$5201,a
eor #>$800000,a
move a1,x0
move x:>$5205,y0
mpy x0,y0,a
move y0,b
add b,a
asr #24,a,a
move a,b
move a0,b0
move x:>$5200,x0
mpy x0,y0,a
add b,a
jsr @candidate_state_guard
move a1,x:>$5209
move a0,x:>$520a
move x:>$5208,a
eor #>$800000,a
move a1,x0
move x:>$5206,y0
mpy x0,y0,a
move y0,b
add b,a
asr #24,a,a
move a,b
move a0,b0
move x:>$5207,x0
mpy x0,y0,a
add b,a
move x:>$5209,b
move x:>$520a,b0
add b,a
move x:>$5202,b
move x:>$5203,b0
add b,a
jsr @candidate_state_guard
move a1,x:>$520d
move a0,x:>$520e
move x:>$520b,a
move x:>$520c,a0
asl a
move x:>$5200,b
move x:>$5201,b0
sub b,a
jsr @candidate_state_guard
move a1,x:>$5200
move a0,x:>$5201
move x:>$520d,a
move x:>$520e,a0
asl a
move x:>$5202,b
move x:>$5203,b0
sub b,a
jsr @candidate_state_guard
move a1,x:>$5202
move a0,x:>$5203
move x:>$520d,a
move x:>$520e,a0
jsr @candidate_wet_limit
move a,x:>$5220
jsr @candidate_alpha_step
move #>$7fffff,a
sub y0,a
move a1,y1
move x:>$5220,x0
mpy x0,y0,a
move x:>$521f,x0
mac x0,y1,a
rnd a
move a,y:(r0)+
filter_done:
nop
jsr @candidate_mix_end
rts

filter_map:
move x0,x:>$5605
move x1,x:>$5606
move x0,a
add #>$5500,a
move a1,r1
move #>$ffffff,m1
nop
move x:(r1),x0
move x0,x:>$5607
move x1,a
add #>$5580,a
move a1,r2
move #>$ffffff,m2
nop
move x:(r2),y0
move y0,x:>$5608
mpy x0,x0,a
asr a
move a,b
move a0,b0
mpy x0,y0,a
asr #2,a,a
add b,a
move #>$040000,b
add b,a
move #>$0,b
move b1,x:>$5609
filter_map_normalize:
cmp #>$400000,a
jge @filter_map_normalized
asl a
move x:>$5609,b
add #>$1,b
move b1,x:>$5609
jmp @filter_map_normalize
filter_map_normalized:
rnd a
move a1,x:>$560a
move a1,x0
move #>$3c3c3c,y0
mpy x0,y0,a
move #>$5a5a5a,b
sub a,b
asl b
rnd b
move b1,x:>$560b
filter_map_seeded:
filter_map_newton_0:
move x:>$560a,x0
move x:>$560b,y0
mpy x0,y0,a
asl a
move #>$400000,b
asl b
sub a,b
rnd b
move b1,x:>$560c
move x:>$560c,x0
move x:>$560b,y0
mpy x0,y0,a
move y0,b
add b,a
rnd a
move a1,x:>$560b
filter_map_newton_0_done:
filter_map_newton_1:
move x:>$560a,x0
move x:>$560b,y0
mpy x0,y0,a
asl a
move #>$400000,b
asl b
sub a,b
rnd b
move b1,x:>$560c
move x:>$560c,x0
move x:>$560b,y0
mpy x0,y0,a
move y0,b
add b,a
rnd a
move a1,x:>$560b
filter_map_newton_1_done:
filter_map_newton_2:
move x:>$560a,x0
move x:>$560b,y0
mpy x0,y0,a
asl a
move #>$400000,b
asl b
sub a,b
rnd b
move b1,x:>$560c
move x:>$560c,x0
move x:>$560b,y0
mpy x0,y0,a
move y0,b
add b,a
rnd a
move a1,x:>$560b
filter_map_newton_2_done:
move x:>$560b,a
asr #4,a,a
move x:>$5609,b
filter_map_rescale:
tst b
jeq @filter_map_rescaled
asl a
sub #>$1,b
jmp @filter_map_rescale
filter_map_rescaled:
rnd a
move a1,x:>$5600
move x:>$5607,x0
move x:>$5600,y0
mpy x0,y0,a
asl #2,a,a
rnd a
move a1,x:>$5601
move x:>$5601,y0
mpy x0,y0,a
asl #2,a,a
rnd a
move a1,x:>$5602
move #>$040000,a
move a1,x:>$5603
move x:>$5605,a
cmp #>$7f,a
jeq @filter_map_bypass
move #>$1,a
move a1,x:>$5604
rts
filter_map_bypass:
clr a
move a1,x:>$5604
rts

tone_dispatch:
move y0,a
and #>$e00000,a
tst a
jne @d_fallback
move y0,a
move x:>$4e95,b
cmp b,a
jeq @tone_done
move a1,x:>$4e95
and #>$7f,a
add #>$5700,a
move a1,r1
move #>$ffffff,m1
nop
move x:(r1),a
move a1,x:>$5300
move x:>$4e95,a
lsr #7,a
move x:>$4e96,b
cmp b,a
jeq @tone_done
move a1,x:>$4e96
move a1,b
and #>$7f,a
move a1,x0
cmp #>$7f,a
jeq @tone_bypass
move b1,a
lsr #7,a
move a1,x1
jsr @filter_map
; Only the queue consumer writes these targets; publish the full group last.
move x:>$5215,a
add #>$1,a
move a1,x:>$5215
move #>$5600,r0
move #>$5210,r1
move #>$ffffff,m0
move #>$ffffff,m1
nop
do #<$5,>@tone_publish_end
move x:(r0)+,a
move a1,x:(r1)+
tone_publish_end:
move x:>$5215,a
add #>$1,a
move a1,x:>$5215
tone_done:
rts
tone_bypass:
; Keep the old wet coefficients throughout the fade to dry.
move x:>$5215,a
add #>$1,a
move a1,x:>$5215
clr a
move a1,x:>$5214
move x:>$5215,a
add #>$1,a
move a1,x:>$5215
rts

cf_patch_fm:
move y0,a
and #>$ffe000,a
tst a
jne @d_fallback
move y0,a
and #>$3,a
cmp #>$2,a
jge @d_fallback
move y0,a
move a1,x:>$5808
rts
cf_mod_ad:
move y0,a
and #>$e00000,a
tst a
jne @d_fallback
move y0,a
and #>$7f,a
add #>$5000,a
move a1,r4
move x:(r4),a
move a1,x:>$5800
move y0,a
lsr #7,a
and #>$7f,a
add #>$5080,a
move a1,r4
move x:(r4),a
move a1,x:>$5801
move y0,a
lsr #14,a
and #>$7f,a
add #>$5100,a
move a1,r4
move x:(r4),a
move a1,x:>$5802
rts
cf_snapshot_fm:
move r0,a
sub #>$4e00,a
lsr #4,a
add #>$5820,a
move a1,r4
move x:>$5808,a
move a1,x:(r4)
rts
cf_load_pending_fm:
move r0,a
sub #>$4e00,a
lsr #4,a
add #>$5820,a
move a1,r4
move x:(r4),b
move b1,a
and #>$3,a
move a1,x:>$5302
move b1,a
lsr #2,a
and #>$f,a
move a1,x:>$530c
move b1,a
lsr #6,a
and #>$7f,a
move a1,x:>$5303
rts
