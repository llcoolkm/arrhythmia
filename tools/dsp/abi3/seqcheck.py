# SPDX-License-Identifier: MIT
"""Static instruction-sequence check over an assembled listing (DSP56300 Family Manual rev. 5, A.3).

This check looks for selected instruction sequences that can behave differently
on silicon and in an emulator. It reads assembled listing rows (address, words,
decoded text, ...) and reports matching patterns (A.3 terms: MOVE is any MOVE, MOVEC, MOVEM, MOVEP;
MOVEM is any move to or from P space; the control registers are LA, LC, SP, SC, SSH, SSL, SZ, VBA, OMR):

A.3.1, for every DO, DO FOREVER, DOR and DOR FOREVER (LA = the loop's last address, from the second
        word: absolute for DO, relative to the DOR's address for DOR):
  LA-5, LA-4  MOVE to, BCHG/BSET/BCLR on a control register.
  LA-3        the same; MOVE from SSH/SSL; two-word JMP/Jcc/JSR/JScc; JSET/JCLR/JSSET/JSCLR;
              two-word MOVEM.
  LA-2, LA-1  DO/DOR; MOVE to or from, BCHG/BSET/BCLR/BTST on a control register; any jump, branch
              or call (incl. the bit-test forms); MOVEM; ANDI/ORI on MR; BRKcc/ENDDO/REP;
              STOP/WAIT/DEBUG(cc)/TRAP(cc)/ILLEGAL.
  LA          any two-word instruction; MOVE to a control register; MOVE from SSH/SSL;
              BCHG/BSET/BCLR on a control register; BTST on SSH; JMP/JSR/BRA/BSR/Jcc/JScc/Bcc/BScc;
              MOVEM and P-space MOVEP; RESET; RTI/RTS; ANDI/ORI on MR; BRKcc/ENDDO/REP;
              STOP/WAIT/DEBUG(cc)/TRAP(cc)/ILLEGAL. LA must be the last word of an instruction; a
              two-word last instruction starts at LA-1 and meets both the LA-1 and the LA list.
A.3.2  every exit from a loop body (a jump, branch or fall-through out of [DO+2, LA]) must, on every
       static path, come back into the body or end in ENDDO/BRKcc before an RTS/RTI (calls are taken
       to return; an unresolved target is reported); no BCHG/BSET/BCLR of SR bit 15 (LF) or 16 (FV)
       and no ANDI/ORI on MR/EOM that changes them (a MOVE to SR is listed by reviews(), not decided); SSH is not a loop count; within four words before a DO no
       write to SSH/SSL/SP/SC; immediately before a DO no MOVE from SSH, no BTST on SSH, no write to
       LA/LC/SP/SC/SSH/SSL, and no call whose target is the LA of a loop.
A.3.3  within four words before ENDDO no write to SSH/SSL/SP/SC; immediately before it no ANDI/ORI on
       MR, MOVE from SSH, BTST on SSH, write to a control register, WAIT or STOP.
A.3.4  immediately before BRKcc no arithmetic instruction, IFcc, Tcc or write to a control register.
A.3.5  immediately before RTS/RTI no MOVE/BCHG/BSET/BCLR on SSH/SSL/SP/SC, no MOVE/BTST from SSH, no
       ENDDO; before RTI no ANDI/ORI on MR/CCR.
A.3.6  no write to SP/SC within four instructions (in address order) before an SSH/SSL access.
A.3.8  REP repeats a single-word instruction that is not REP, DO, ENDDO, BRKcc, a jump, branch, call,
       RTS, RTI, TRAP(cc), WAIT or STOP.

Not encoded: A.2 (sequence delays); A.3.7 (fast interrupts); A.3.9 and
A.3.10 (stack extension); A.4 (peripheral pipeline); A.5 (16-bit mode); and
the A.3.8 external-memory REP hazard. The A.3.2 walk uses the listing and
assumes calls return. Review executed paths and processor state separately.
"""
import re

CC = ('cc', 'hs', 'cs', 'lo', 'ec', 'eq', 'es', 'ge', 'gt', 'lc', 'le', 'ls', 'lt', 'mi', 'ne', 'nr',
      'pl', 'nn')
ALU = {'add', 'sub', 'cmp', 'cmpm', 'cmpu', 'tst', 'clr', 'neg', 'abs', 'asl', 'asr', 'lsl', 'lsr', 'and',
       'or', 'eor', 'not', 'rnd', 'mpy', 'mpyr', 'mac', 'macr', 'mpysu', 'macsu', 'mpyuu', 'macuu', 'mpyi',
       'mpyri', 'maci', 'macri', 'tfr', 'adc', 'sbc', 'addl', 'addr', 'subl', 'subr', 'max', 'maxm', 'norm',
       'normf', 'rol', 'ror', 'sat', 'div', 'dmac', 'extract', 'extractu', 'insert', 'merge', 'clb', 'inc',
       'dec', 'vsl', 'cmpu'}
CONTROL = ('la', 'lc', 'sp', 'sc', 'ssh', 'ssl', 'sz', 'vba', 'omr', 'ep')
STACK = {'ssh', 'ssl', 'sp', 'sc'}
BITOPS = ('bset', 'bclr', 'bchg', 'btst')
BITJUMPS = ('jset', 'jclr', 'jsset', 'jsclr', 'brset', 'brclr', 'bsset', 'bsclr')
HALT = ('stop', 'wait', 'debug', 'trap', 'illegal', 'reset') + tuple('debug' + c for c in CC) + tuple(
    'trap' + c for c in CC)


def mnemonic(text):
    return text.split(None, 1)[0].lower()


def operands(text):
    parts = text.split(None, 1)
    return parts[1].lower() if len(parts) > 1 else ''


def flow(text):
    """None, or (family, unconditional): family 'jump', 'branch', 'call', 'return'."""
    m = mnemonic(text)
    if m in ('rts', 'rti'):
        return 'return', True
    if m in ('jmp', 'bra'):
        return ('jump' if m == 'jmp' else 'branch'), True
    if m in ('jsr', 'bsr'):
        return 'call', True
    if m in BITJUMPS:
        family = {'jset': 'jump', 'jclr': 'jump', 'brset': 'branch', 'brclr': 'branch'}.get(m, 'call')
        return family, False
    for head, family in (('js', 'call'), ('bs', 'call'), ('j', 'jump'), ('b', 'branch')):
        if m.startswith(head) and m[len(head):] in CC:
            return family, False
    return None


def kind(text):
    """'flow' (jump, branch, call, return), 'loop' (DO, BRKcc, ENDDO, REP), 'alu', 'tcc', 'ifcc',
    'movem' or ''."""
    m = mnemonic(text)
    if flow(text):
        return 'flow'
    if m in ('do', 'dor', 'enddo', 'rep') or (m.startswith('brk') and m[3:] in CC):
        return 'loop'
    if m.startswith('t') and m[1:] in CC:
        return 'tcc'
    if re.search(r'\bif(' + '|'.join(CC) + r')(\.u)?\b', text.lower()):
        return 'ifcc'
    if m in ALU:
        return 'alu'
    if m == 'movem' or re.search(r'\bp:', operands(text)):
        return 'movem'
    return ''


def _regs(field, names=CONTROL):
    return set(re.findall(r'\b(' + '|'.join(names) + r')\b', field))


def control(text):
    """(read, written) control registers (and SR) of one instruction."""
    m, ops = mnemonic(text), operands(text)
    names = CONTROL + ('sr',)
    if m in BITOPS:
        regs = _regs(ops, names)
        return regs, (set() if m == 'btst' else regs)
    if m in BITJUMPS:
        return _regs(ops.rsplit(',', 1)[0], names), set()
    if m.startswith('move') or m == 'lua':
        read, written = set(), set()
        for source, dest in re.findall(r'(\S+?),(\S+)', ops):
            read |= _regs(source, names)
            written |= _regs(dest, names)
        return read, written
    if m in ('do', 'dor', 'rep'):
        return _regs(ops.split(',')[0], names), set()
    return _regs(ops, names), set()


def on_mr(text, regs=('mr',)):
    return mnemonic(text) in ('andi', 'ori') and any(re.search(rf'\b{r}\b', operands(text)) for r in regs)


def _immediate(ops):
    found = re.match(r'#[<>]?\$([0-9a-f]+)', ops)
    return int(found[1], 16) if found else None


def lf_fv_change(text):
    """True when the instruction can change LF (SR bit 15 = MR bit 7) or FV (SR bit 16 = EMR bit 0):
    BCHG/BSET/BCLR of bit 15 or 16 on SR, ANDI #n,MR with bit 7 clear, ORI #n,MR with bit 7 set,
    ANDI/ORI on EOM/EMR likewise for bit 0. A MOVE to SR is not decidable here (see reviews)."""
    m, ops = mnemonic(text), operands(text)
    if m in ('bset', 'bclr', 'bchg') and re.search(r',\s*sr\b', ops):
        bit = re.match(r'#[<>]?\$?([0-9a-f]+)', ops)
        if bit is None:
            return True
        number = int(bit[1], 16) if '$' in ops.split(',')[0] else int(bit[1])
        return number in (15, 16)
    if m in ('andi', 'ori'):
        value = _immediate(ops)
        for reg, bit in (('mr', 7), ('eom', 0), ('emr', 0)):
            if re.search(rf',\s*{reg}\b', ops):
                if value is None:
                    return True
                return not value >> bit & 1 if m == 'andi' else bool(value >> bit & 1)
    return False


def reviews(listing):
    """Sites a value proof must clear: a MOVE to SR keeps LF/FV only if the value carries their current state."""
    return [dict(rule='A.3.2 MOVE to SR: LF/FV must keep their current values', address=f'{row[0]:04X}', text=row[2])
            for row in sorted(listing) if mnemonic(row[2]).startswith('move') and 'sr' in control(row[2])[1]]


def loop_address(row):
    """LA of a DO, DO FOREVER, DOR or DOR FOREVER row. DO and DO FOREVER hold LA in the extension word;
    DOR and DOR FOREVER hold LA minus the DOR's own address (FM 13-61: PC + xxxx -> LA; the pinned
    disassembler shows extension 0x10 at P:100 as >*+$11, the address after LA)."""
    at, words, text = row
    return (at + words[1]) & 0xFFFFFF if mnemonic(text) == 'dor' else words[1]


def target(row):
    """Absolute target of a jump, branch or call, or None when it cannot be read from the row."""
    at, words, text = row
    m = mnemonic(text)
    relative = m.startswith('b')
    if len(words) == 2:
        value = words[1]
    else:
        found = re.findall(r'\$([0-9a-f]+)\s*$', text.lower())
        if not found or relative:
            return None
        value = int(found[0], 16)
    return (at + value) & 0xFFFFFF if relative else value


def check(listing):
    """Violations as dicts (rule, address, text, la)."""
    rows = sorted((row[0], list(row[1]), row[2]) for row in listing)
    starts = {row[0]: k for k, row in enumerate(rows)}
    out = []

    def bad(rule, row, la=None):
        out.append(dict(rule=rule, address=f'{row[0]:04X}', text=row[2],
                        la=None if la is None else f'{la:04X}'))

    def at_offset(la, back, do_at):
        row = rows[starts[la - back]] if la - back in starts else None
        return None if row is None or row[0] <= do_at + 1 else row

    def before(k, words):
        """Instructions that start within `words` words before row k."""
        at = rows[k][0]
        return [r for r in rows[max(0, k - words):k] if at - r[0] <= words]

    loops = [(row[0], loop_address(row)) for row in rows if mnemonic(row[2]) in ('do', 'dor') and len(row[1]) > 1]
    las = {la for _, la in loops}

    for do_at, la in loops:
        do_row = rows[starts[do_at]]
        last = rows[starts[la]] if la in starts else None
        if last is None:
            prev = rows[starts[la - 1]] if la - 1 in starts else None
            if prev is None or len(prev[1]) != 2:
                bad('A.3.1 LA is not the last word of an instruction', do_row, la)
            else:
                last = prev                  # a two-word last instruction starts at LA-1: both lists apply
        elif len(last[1]) != 1:
            bad('A.3.1 two-word instruction at LA', last, la)
        if last is not None:
            r, w = control(last[2])
            if (w or r & {'ssh', 'ssl'} or kind(last[2]) in ('flow', 'loop', 'movem')
                    or mnemonic(last[2]) in HALT or on_mr(last[2])):
                bad('A.3.1 flow, loop, MOVEM, control or halt instruction at LA', last, la)
        for back in (1, 2):
            prev = at_offset(la, back, do_at)
            if prev is None:
                continue
            r, w = control(prev[2])
            if (kind(prev[2]) in ('flow', 'loop', 'movem') or r or w or mnemonic(prev[2]) in HALT
                    or on_mr(prev[2])):
                bad(f'A.3.1 flow, loop, MOVEM, control or halt instruction starts at LA-{back}', prev, la)
        prev = at_offset(la, 3, do_at)
        if prev is not None:
            r, w = control(prev[2])
            pm, fl = mnemonic(prev[2]), flow(prev[2])
            if ((fl and fl[0] in ('jump', 'call') and pm[0] == 'j' and len(prev[1]) == 2)
                    or pm in ('jset', 'jclr', 'jsset', 'jsclr') or w or r & {'ssh', 'ssl'}
                    or (kind(prev[2]) == 'movem' and len(prev[1]) == 2)):
                bad('A.3.1 two-word jump or call, bit-test jump, two-word MOVEM or control move starts at LA-3',
                    prev, la)
        for back in (4, 5):
            prev = at_offset(la, back, do_at)
            if prev is not None and control(prev[2])[1] - {'sr'}:
                bad(f'A.3.1 control-register write starts at LA-{back}', prev, la)
        # A.3.2: every way out of the body comes back, or ends by ENDDO/BRKcc, before a return.
        body = (do_at + len(do_row[1]), la)
        for k in range(starts[body[0]] if body[0] in starts else len(rows), len(rows)):
            row = rows[k]
            if row[0] > la:
                break
            fl = flow(row[2])
            if fl and fl[0] == 'return':
                bad('A.3.2 RTS/RTI inside a DO loop body', row, la)
            if fl and fl[0] in ('jump', 'branch'):
                dest = target(row)
                if dest is None:
                    bad('A.3.2 jump in a DO loop body with an unresolved target', row, la)
                elif not body[0] <= dest <= la:
                    reason = _escape(rows, starts, dest, body)
                    if reason:
                        bad(f'A.3.2 jump out of a DO loop body ({reason})', row, la)

    for k, (at, words, text) in enumerate(rows):
        m, ops = mnemonic(text), operands(text)
        read, written = control(text)
        if lf_fv_change(text):
            bad('A.3.2 explicit LF/FV change (SR bit 15/16)', rows[k])
        if m in ('do', 'dor'):
            if 'ssh' in read:
                bad('A.3.2 SSH as a loop count', rows[k])
            for prev in before(k, 4):
                if control(prev[2])[1] & STACK:
                    bad('A.3.2 SSH/SSL/SP/SC write within four words before DO', prev)
            if k:
                p = rows[k - 1]
                r, w = control(p[2])
                fl = flow(p[2])
                if 'ssh' in r or w & {'la', 'lc', 'sp', 'sc', 'ssh', 'ssl'} or (
                        fl and fl[0] == 'call' and target(p) in las):
                    bad('A.3.2 SSH read, loop/stack register write or call to LA immediately before DO', p)
        if m == 'enddo':
            for prev in before(k, 4):
                if control(prev[2])[1] & STACK:
                    bad('A.3.3 SSH/SSL/SP/SC write within four words before ENDDO', prev)
            if k:
                p = rows[k - 1]
                r, w = control(p[2])
                if w - {'sr'} or 'ssh' in r or mnemonic(p[2]) in ('wait', 'stop') or on_mr(p[2]):
                    bad('A.3.3 control write, SSH read, ANDI/ORI on MR, WAIT or STOP immediately before ENDDO', p)
        if m.startswith('brk') and m[3:] in CC and k:
            p = rows[k - 1]
            if kind(p[2]) in ('alu', 'tcc', 'ifcc') or control(p[2])[1] - {'sr'}:
                bad('A.3.4 arithmetic, Tcc, IFcc or control write immediately before BRKcc', p)
        if m in ('rts', 'rti') and k:
            p = rows[k - 1]
            r, w = control(p[2])
            if (r | w) & STACK or mnemonic(p[2]) == 'enddo' or (m == 'rti' and on_mr(p[2], ('mr', 'ccr'))):
                bad(f'A.3.5 stack access, ENDDO or ANDI/ORI immediately before {m.upper()}', p)
        if (read | written) & {'ssh', 'ssl'}:
            for prev in rows[max(0, k - 4):k]:
                if control(prev[2])[1] & {'sp', 'sc'}:
                    bad('A.3.6 SP/SC write within four instructions before an SSH/SSL access', prev)
        if m == 'rep' and k + 1 < len(rows):
            nxt = rows[k + 1]
            if kind(nxt[2]) in ('flow', 'loop') or mnemonic(nxt[2]) in HALT or len(nxt[1]) != 1:
                bad('A.3.8 REP repeats a flow-changing, loop or two-word instruction', nxt)
    return out


def _escape(rows, starts, start, body, limit=4096):
    """None when every static path from `start` re-enters `body` or ends by ENDDO/BRKcc, else the reason."""
    seen, todo = set(), [start]
    while todo:
        at = todo.pop()
        if body[0] <= at <= body[1] or at in seen:
            continue
        seen.add(at)
        if len(seen) > limit:
            return 'path too long to follow'
        if at not in starts:
            return f'path reaches {at:04X}, not an instruction'
        row = rows[starts[at]]
        m = mnemonic(row[2])
        if m == 'enddo' or (m.startswith('brk') and m[3:] in CC):
            continue
        fl = flow(row[2])
        if fl and fl[0] == 'return':
            return f'path reaches {row[2]} at {at:04X}'
        if fl and fl[0] in ('jump', 'branch'):
            dest = target(row)
            if dest is None:
                return f'unresolved target at {at:04X}'
            todo.append(dest)
            if fl[1]:
                continue
        todo.append(at + len(row[1]))
    return None
