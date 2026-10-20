boot:
clr a
move a,x:>$b4
move a,x:>$ba
jmp $2db
note_on:
move #>$7,a
and y1,a
cmp #<$7,a
jeq @reserved
clr a
jmp $4f5
note_off:
move #>$7,a
and y1,a
cmp #<$7,a
jeq @reserved_off
clr a
jmp $50a
pending:
move #>$7,a
and y1,a
cmp #<$7,a
jeq @reserved
move y1,n3
jmp $2fe
reserved_off:
move y0,a
cmp #<$3,a
jne @reserved
move m4,x:>$2ec3
move #>$ffffff,m4
move #>$2e47,r4
move #<$b,n4
move #>$3,b
do #<$4,>@choke_end
move x:(r4),a
tst a
tne b,a
move a,x:(r4)+n4
choke_end:
jsr $fae
move x:>$2ec3,m4
reserved:
clr a
move a,x:>$b4
move a,x:>$ba
move a,y:>$307c5
move a,y:>$307c6
rts
