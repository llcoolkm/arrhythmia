wrapper:
move r3,x:>$2e9b
move m3,x:>$2e9c
move #>$ffffff,m3
move #>$2e80,r3
nop
move sr,x:(r3)+
move a2,x:(r3)+
move a1,x:(r3)+
move a0,x:(r3)+
move b2,x:(r3)+
move b1,x:(r3)+
move b0,x:(r3)+
move x0,x:(r3)+
move x1,x:(r3)+
move y0,x:(r3)+
move y1,x:(r3)+
move r0,x:(r3)+
move m0,x:(r3)+
move r1,x:(r3)+
move r2,x:(r3)+
move n1,x:(r3)+
move m1,x:(r3)+
move r7,x:(r3)+
move n7,x:(r3)+
move m7,x:(r3)+
move la,x:(r3)+
move lc,x:(r3)+
move r4,x:(r3)+
move r5,x:(r3)+
move r6,x:(r3)+
move n4,x:(r3)+
move m4,x:(r3)+
move x:>$2e70,a
tst a
jeq @restore
movep y:<<$ffffd0,a
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
do #<$20,>@clear_end
move a,y:(r0)+
clear_end:
move #>$2e40,r1
move #<$b,n1
do #<$4,>@done
move r2,r0
move x:(r1+$7),a
tst a
jeq @idle
cmp #<$1,a
jeq @attack
cmp #<$2,a
jeq @decay
clr a
move a,x:(r1+$a)
jmp @envelope_done
attack:
move x:(r1+$4),a
move x:>$2ca0,x0
add x0,a
cmp #>$7fffff,a
jlt @attack_continue
move #>$7fffff,a
move #>$2,x0
move x0,x:(r1+$a)
jmp @envelope_done
attack_continue:
move #>$1,x0
move x0,x:(r1+$a)
jmp @envelope_done
decay:
move x:(r1+$4),x0
move x:>$2ca1,y0
mpyr y0,x0,a
cmp #>$400,a
jge @decay_continue
clr a
move a,x:(r1+$a)
jmp @envelope_done
decay_continue:
move #>$2,x0
move x0,x:(r1+$a)
envelope_done:
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
move #<$0,b
move x:(r1+$0),b1
move x:(r1+$1),b0
move x:(r1+$2),x1
move x:(r1+$3),x0
move x:(r1+$8),r5
do #<$20,>@table_end
move r5,r4
move b1,a
lsr #16,a
move a1,n4
move b1,a
and #>$ffff,a
asl #7,a,a
move a1,y1
move (r4)+n4
move y:(r4)+,a
move a1,r6
move y:(r4),y0
move y0,a
move r6,y0
sub y0,a
move a1,y0
mpyr y1,y0,a
move r6,y0
add y0,a
move a,y0
add x,b
move r7,y1
move (r7)+n7
mpyr y1,y0,a
move y:(r0),y0
add y0,a
move a,y:(r0)+
table_end:
finished:
move b1,x:(r1+$0)
move b0,x:(r1+$1)
move x:(r1+$9),a
move a,x:(r1+$5)
move x:(r1+$a),a
move a,x:(r1+$7)
tst a
jne @voice_done
clr a
move a,x:(r1+$4)
move a,x:(r1+$5)
jmp @voice_done
idle:
nop
voice_done:
move (r1)+n1
poly_last:
nop
done:
nop
jsr $f69
restore:
move #>$2e9a,r3
nop
move x:(r3)-,m4
move x:(r3)-,n4
move x:(r3)-,r6
move x:(r3)-,r5
move x:(r3)-,r4
move x:(r3)-,lc
move x:(r3)-,la
move x:(r3)-,m7
move x:(r3)-,n7
move x:(r3)-,r7
move x:(r3)-,m1
move x:(r3)-,n1
move x:(r3)-,r2
move x:(r3)-,r1
move x:(r3)-,m0
move x:(r3)-,r0
move x:(r3)-,y1
move x:(r3)-,y0
move x:(r3)-,x1
move x:(r3)-,x0
move x:(r3)-,b0
move x:(r3)-,b1
move x:(r3)-,b2
move x:(r3)-,a0
move x:(r3)-,a1
move x:(r3)-,a2
move x:(r3)-,sr
move x:>$2e9c,m3
move x:>$2e9b,r3
tail:
jmp $8a6
