; SPDX-License-Identifier: MIT
; Copyright (c) 2026 km
; Reset stack state and invalidate readiness before initialization.

move #$0,sp
move #$0,sc
move r0,x:>$4f0b
move #>$0,r0
move r0,x:>$4e92
move x:>$4f0b,r0
jmp $102
