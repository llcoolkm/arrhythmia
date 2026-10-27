# SPDX-License-Identifier: MIT
"""Assemble labelled own DSP source with fixed-width absolute branch forms.

The shared line assembler handles ordinary instructions. Local JMP/JSR labels
use the DSP56300 two-word absolute forms; local conditional labels use relative
Bcc forms. Both passes must agree on every instruction length.
"""
import re

from dspvoice.assemble import encode

LONG = {'jmp': 0x0AF080, 'jsr': 0x0BF080}
CONDITIONS = {'jeq': 'beq', 'jne': 'bne', 'jge': 'bge',
              'jgt': 'bgt', 'jle': 'ble', 'jlt': 'blt'}
LABEL = re.compile(r'@(\w+)')
NAME = re.compile(r'[A-Za-z_]\w*\Z')


def assemble(executable, source, origin):
    if type(origin) is not int or not 0 <= origin <= 0xFFFFFF:
        raise ValueError('invalid program origin')
    records = [line.split(';', 1)[0].strip() for line in source.splitlines()]
    records = [line for line in records if line]
    labels, sized, pc = {}, [], origin
    for line in records:
        if line.endswith(':'):
            name = line[:-1]
            if not NAME.fullmatch(name) or name in labels:
                raise ValueError('invalid or duplicate source label')
            labels[name] = pc
            continue
        head = line.split(None, 1)[0].lower()
        if head in LONG and re.fullmatch(rf'{head}\s+@\w+', line):
            size = 2
        else:
            probe = LABEL.sub('$0', line)
            if head in CONDITIONS and '@' in line:
                probe = CONDITIONS[head] + probe[len(head):]
            size = len(encode(executable, [(0, probe)])[0][1])
        if pc + size > 0x1000000:
            raise ValueError('program exceeds unsigned 24-bit address space')
        sized.append((pc, line, size))
        pc += size
    words, listing = [], []
    for at, line, size in sized:
        head = line.split(None, 1)[0].lower()
        if head in LONG and re.fullmatch(rf'{head}\s+@\w+', line):
            name = line.split('@', 1)[1]
            if name not in labels:
                raise ValueError('undefined source label')
            encoded = [LONG[head], labels[name]]
            final = line.replace('@' + name, f'${labels[name]:x}')
        else:
            def target(match):
                if match[1] not in labels:
                    raise ValueError('undefined source label')
                value = labels[match[1]]
                if head in CONDITIONS:
                    value = (value - at) & 0xFFFFFF
                return f'${value:x}'
            final = LABEL.sub(target, line)
            if head in CONDITIONS and '@' in line:
                final = CONDITIONS[head] + final[len(head):]
            encoded = encode(executable, [(at, final)])[0][1]
        if len(encoded) != size:
            raise ValueError('instruction length changed during label resolution')
        listing.append((at, encoded, final))
        words.extend(encoded)
    return words, listing, labels
