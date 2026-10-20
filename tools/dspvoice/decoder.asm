move y1,a
and #>$fc0000,a
move a1,a
cmp #>$a40000,a
jeq @onset
move y1,a
and #>$ff0000,a
move a1,a
cmp #>$a00000,a
jne @fallback
move y1,a
and #>$f000,a
move a1,a
cmp #>$1000,a
jeq @kill
cmp #>$3000,a
jeq @alloff
cmp #>$6000,a
jeq @patch_all
jmp @fallback
onset:
jclr #15,y1,>@fallback
move y0,a
lsr #11,a
and #>$1f,a
move a1,a
cmp #>$18,a
jge @fallback
move y0,a
lsr #4,a
and #>$7f,a
move a1,a
tst a
jeq @fallback
move #>$1,b
move y1,a
lsr #16,a
and #>$3,a
move a1,a
jmp @select
kill:
move y1,a
and #>$3ff,a
move a1,a
tst a
jne @fallback
move y0,a
and #>$fffff0,a
move a1,a
tst a
jne @fallback
move #>$2,b
move y1,a
lsr #10,a
and #>$3,a
move a1,a
select:
move r1,x:>$2feb
move a1,x0
lsl #2,a
add x0,a
add x0,a
add #>$2fd0,a
move a1,r1
move x0,a
lsl #3,a
add x0,a
add x0,a
add x0,a
add #>$2e40,a
move a1,r0
move m0,x:>$2fee
move #>$ffffff,m0
move b1,a
cmp #>$1,a
jeq @onset_selected
jmp @kill_selected
onset_selected:
move y0,a
and #>$f,a
move a1,x1
move x:(r1),a
add #>$1,a
and #>$f,a
move a1,a
cmp x1,a
jne @restore_fallback
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
jeq @unshifted
rep x0
asl a
unshifted:
move a1,x:(r1+$2)
move a0,x:(r1+$3)
clr b
move #>$088888,b1
move #>$888889,b0
cmp b,a
jlt @bank15
move #>$124924,b1
move #>$924925,b0
cmp b,a
jlt @bank7
move #>$2aaaaa,b1
move #>$aaaaab,b0
cmp b,a
jlt @bank3
move #>$1800,x1
jmp @bank_done
bank15:
move #>$1500,x1
jmp @bank_done
bank7:
move #>$1600,x1
jmp @bank_done
bank3:
move #>$1700,x1
bank_done:
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
move #>$1,a
move a,x:(r1+$1)
move x:(r0+$7),a
tst a
jeq @onset_idle
move #>$3,a
move a,x:(r0+$7)
jmp @restore_accept
onset_idle:
jsr @install
jmp @restore_accept
kill_selected:
move y0,a
and #>$f,a
move a1,x1
move x:(r1),a
cmp x1,a
jne @restore_accept
clr a
move a,x:(r1+$1)
move x:(r0+$7),a
tst a
jeq @restore_accept
move #>$3,a
move a,x:(r0+$7)
jmp @restore_accept
alloff:
move y1,a
and #>$fff,a
move a1,a
tst a
jne @fallback
move y0,a
tst a
jne @fallback
move r1,x:>$2feb
move n1,x:>$2fec
move m0,x:>$2fee
move m1,x:>$2fef
move #>$ffffff,m0
move #>$ffffff,m1
move #>$2e40,r0
move #>$2fd0,r1
move #<$b,n0
move #<$6,n1
do #<$4,>@all_end
move x:(r0+$7),a
tst a
jeq @all_idle
move #>$3,a
move a,x:(r0+$7)
all_idle:
clr a
move a,x:(r1)
move a,x:(r1+$1)
move (r0)+n0
move (r1)+n1
all_end:
nop
move x:>$2fef,m1
move x:>$2fec,n1
jmp @restore_accept
restore_accept:
move x:>$2feb,r1
move x:>$2fee,m0
rts
restore_fallback:
move x:>$2feb,r1
move x:>$2fee,m0
fallback:
jmp $4cd
handoff:
move n0,x:>$2fea
move m0,x:>$2fee
move m1,x:>$2fef
move #>$ffffff,m0
move #>$ffffff,m1
move #>$2e40,r0
move #>$2fd0,r1
move #<$b,n0
move #<$6,n1
do #<$4,>@handoff_end
move x:(r1+$1),a
tst a
jeq @handoff_next
move x:(r0+$7),a
tst a
jne @handoff_next
jsr @install
handoff_next:
move (r0)+n0
move (r1)+n1
handoff_end:
nop
move x:>$2fef,m1
move x:>$2fee,m0
move x:>$2fea,n0
rts
install:
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
move #>$1,a
move a,x:(r0+$7)
rts
cancel_pending:
clr a
move a,x:>$2fd1
move a,x:>$2fd7
move a,x:>$2fdd
move a,x:>$2fe3
rts
patch_all:
move y1,a
and #>$fff,a
move a1,a
tst a
jne @fallback
move y0,a
and #>$ffc000,a
move a1,a
tst a
jne @fallback
move m0,x:>$2fee
move #>$ffffff,m0
move y0,a
and #>$7f,a
move a1,a
add #>$2b00,a
move a1,r0
nop
move x:(r0),a
move a,x:>$2ca0
move y0,a
lsr #7,a
and #>$7f,a
move a1,a
add #>$2b80,a
move a1,r0
nop
move x:(r0),a
move a,x:>$2ca1
move x:>$2fee,m0
rts
