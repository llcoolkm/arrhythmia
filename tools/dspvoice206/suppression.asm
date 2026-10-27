; SPDX-License-Identifier: MIT
; Copyright (c) 2026 km
; Reserve the synthesizer track in the sample renderer.

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
jeq @reserved
clr a
jmp $50a
pending:
move #>$7,a
and y1,a
cmp #<$7,a
jeq @reserved
move y1,n3
jmp $2fe
reserved:
clr a
move a,x:>$b4
move a,x:>$ba
move a,y:>$307c5
move a,y:>$307c6
rts
