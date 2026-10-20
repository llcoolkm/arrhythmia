"""Assemble Arrhythmia's own labelled DSP source with the line assembler."""
import re
import subprocess


def encode(executable, records):
    result = subprocess.run([str(executable)], input=''.join(
        f'{address:x}\t{source}\n' for address, source in records),
        text=True, capture_output=True, check=True)
    rows = []
    for row in result.stdout.splitlines():
        fields = row.split('\t')
        if len(fields) != 4 or fields[0] == 'ERR':
            raise ValueError('assembler rejected or malformed an instruction')
        words = [int(word, 16) for word in fields[1].split()]
        if not words or any(not 0 <= word <= 0xFFFFFF for word in words):
            raise ValueError('assembler produced invalid words')
        rows.append((int(fields[0], 16), words, fields[3]))
    if len(rows) != len(records) or [row[0] for row in rows] != [r[0] for r in records]:
        raise ValueError('assembler omitted or reordered instructions')
    return rows


def assemble_text(executable, source, origin):
    records = [line.split(';', 1)[0].strip() for line in source.splitlines()]
    records = [line for line in records if line]
    instructions = [line for line in records if not line.endswith(':')]
    sizes = [len(words) for _, words, _ in encode(executable, [
        (0, re.sub(r'@\w+', '$0', line)) for line in instructions])]
    labels, pc, index = {}, origin, 0
    for line in records:
        if line.endswith(':'):
            label = line[:-1]
            if not re.fullmatch(r'\w+', label) or label in labels:
                raise ValueError('invalid or duplicate source label')
            labels[label] = pc
        else:
            pc += sizes[index]
            index += 1
    resolved, pc, index = [], origin, 0
    for line in instructions:
        resolved.append((pc, re.sub(r'@(\w+)', lambda m: f'${labels[m[1]]:x}', line)))
        pc += sizes[index]
        index += 1
    listing = encode(executable, resolved)
    if [len(words) for _, words, _ in listing] != sizes:
        raise ValueError('assembler changed instruction lengths during resolution')
    return [word for _, words, _ in listing for word in words], listing, labels
