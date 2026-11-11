# SPDX-License-Identifier: MIT
"""Static file and platform checks for a CRE3 engine over USB."""
from pathlib import Path
import json
import re
import struct
import zlib

MAGIC = b'CRE3'
WINDOW_SIZES = {0: 144, 1: 3072, 2: 3072, 3: 176, 4: 96, 5: 192}
FILE_CHECKS = (1, 2, 5, 7, 8, 10, 11, 12, 15, 16, 17)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low, high, name):
    need(type(value) is int and low <= value <= high, f'{name}: integer {low}..{high} required')
    return value


def source_entries(wanted):
    """Match the producer's greedy sparse PARAM3 packing; id is at most 10."""
    todo = {i for i in range(13) if wanted & (1 << i)}
    entries = (wanted >> 13).bit_count()
    while todo:
        base = min(min(todo), 10)
        todo.difference_update(range(base, base + 3))
        entries += 1
    return entries


def macro0_projection(value):
    """Platform macro-1 storage has 121 states; preserve its existing projection."""
    integer(value, 0, 127, 'source 0 default')
    raw = 4 + (value * 120 + 63) // 127
    return raw, ((raw - 4) * 127 + 60) // 120


def validate_controls(records, wanted, sound_slots):
    need(len(records) <= 16, 'check 17: control count')
    masks, continuous, macros, sounds, keys = {}, set(), set(), [], []
    decoded, positions = [], {}
    for i, raw in enumerate(records):
        need(len(raw) == 8, 'check 17: record length')
        code, source, offset, width, count, default, colour, flags = raw
        kind, surface = code & 15, code >> 4 & 3
        need(code & 0xc0 == 0 and 1 <= kind <= 6 and surface != 3, 'check 17: kind/surface')
        decoded.append((kind, surface, source, count, flags))
        if kind == 5:
            need(surface == 0 and source in (0, 6) and source not in macros, 'check 17: macro source')
            need(all(v <= 127 for v in raw[2:]) and (source != 6 or raw[4] == 0), 'check 17: macro defaults')
            need(wanted & (0x3f << source) == 0x3f << source, 'check 17: macro sources wanted')
            macros.add(source)
            continue
        if kind == 6:
            need(surface == 0 and any(raw[1:5]) and raw[5:] == bytes(3), 'check 17: identity')
            keys.append(i)
            continue
        need(wanted & (1 << source), 'check 17: record source not wanted')
        need(flags & 0x80 == 0, 'check 17: flags')
        reference = flags & 31
        need(reference != 0 or flags & 32 == 0, 'check 17: inversion without reference')
        if reference:
            need(reference - 1 < i and decoded[reference - 1][0] == 3 and decoded[reference - 1][4] & 31 == 0,
                 'check 17: enable reference')
        need(colour & 15 <= 7 and colour >> 4 <= 7, 'check 17: hue')
        if kind == 1:
            need(surface == 1 and source == 8 and offset == width == 0 and count == sound_slots > 0 and default < count,
                 'check 17: sounds')
            sounds.append(i)
        elif kind in (2, 3):
            need(surface in (1, 2) and source in (16, 21) and offset + width <= 24, 'check 17: bank field')
            if kind == 2:
                need(1 <= width <= 3 and 2 <= count <= min(8, 1 << width) and default < count, 'check 17: choice')
            else:
                need(width == count == 1 and default <= 1, 'check 17: toggle')
            mask = ((1 << width) - 1) << offset
            need(not masks.get(source, 0) & mask, 'check 17: overlapping bank masks')
            masks[source] = masks.get(source, 0) | mask
        else:
            need(surface == 2 and 17 <= source <= 20 and source not in continuous and offset == width == count == 0
                 and default <= 127 and colour <= 7, 'check 17: continuous')
            positions[i] = {'knob': len(continuous) + 1, 'surface': 2}
            continuous.add(source)
    need(len(sounds) == bool(sound_slots) and len(keys) <= 1, 'check 17: sounds/identity count')
    need(not any(k in (2, 3, 4) for k, *_ in decoded) or len(keys) == 1, 'check 17: settings key required')
    for source in (16, 21):
        need(not wanted & (1 << source) or source in masks, 'check 17: bank has no fields')
    for source in range(17, 21):
        need(not wanted & (1 << source) or source in continuous, 'check 17: source has no knob')
    sample_choices = any(k in (2, 3) and s == 1 for k, s, *_ in decoded)
    need(sound_slots <= 32 or not sample_choices, 'check 17: slots over 32 must be alone')
    need(not (sample_choices and sound_slots) or sound_slots <= 16, 'check 17: sounds category overflow')
    for surface in (1, 2):
        row = col = 0
        last = 1 if surface == 1 and sample_choices and sound_slots else 3
        for i, (kind, surf, _, count, flags) in enumerate(decoded):
            if kind not in (2, 3) or surf != surface:
                continue
            size = count if kind == 2 else 1
            if flags & 64 and col or col + size > 8:
                row, col = row + 1, 0
            need(row <= last, 'check 17: placement overflow')
            positions[i] = {'surface': surface, 'row': row, 'column': col, 'pads': size}
            col += size
    if sounds:
        positions[sounds[0]] = {'surface': 1, 'first_row': 2 if sample_choices else 0, 'slots': sound_slots}
    return {'positions': positions, 'bank_masks': masks, 'settings_key': int.from_bytes(records[keys[0]][1:5], 'little') if keys else None}


def parse_metadata(data, controls, wanted, slots, minor):
    result, warnings, seen = [], [], set()
    at = 0
    while at < len(data):
        tag = data[at]
        at += 1
        if tag == 255:
            need(not any(data[at:]), 'metadata: end padding')
            return result, warnings
        need(tag != 0 and at < len(data), 'metadata: tag/length')
        size = data[at]
        at += 1
        need(at + size <= len(data), 'metadata: truncated record')
        value = data[at:at + size]
        at += size
        ascii_ok = lambda b: all(32 <= v <= 126 for v in b)
        if tag == 0x12:
            valid = 2 <= size <= 13 and value[0] < controls and ascii_ok(value[1:]) and (tag, value[0]) not in seen
            if not valid:
                warnings.append('ignored invalid optional control label')
                continue
            seen.add((tag, value[0]))
        elif tag in (1, 2, 3, 4, 0x10, 0x11, 0x20, 0x30, 0x40, 0x50, 0x60):
            need(tag != 0x40, 'metadata: retired table tag')
            bounds = {1:(1,24),2:(1,32),3:(1,32),4:(1,128),0x10:(2,13),0x11:(3,3),0x20:(11,22),0x30:(2,13),0x50:(21,52),0x60:(10,10)}
            lo, hi = bounds[tag]
            need(lo <= size <= hi, 'metadata: known tag length')
            key = (tag, value[0]) if tag in (0x10,0x11,0x20,0x30) else (tag,)
            need(tag == 0x60 or key not in seen, 'metadata: duplicate tag')
            seen.add(key)
            if tag in (1,2,3,4):
                need(ascii_ok(value), 'metadata: printable ASCII')
            if tag == 3:
                need(re.fullmatch(rb'[A-Za-z0-9][A-Za-z0-9.+-]*', value) is not None, 'metadata: SPDX identifier')
            if tag in (0x10,0x11):
                need(value[0] <= 21 and wanted & (1 << value[0]), 'metadata: source id')
                need(ascii_ok(value[1:]) if tag == 0x10 else value[1] <= 1 and value[2] <= 127, 'metadata: source detail')
            if tag == 0x20:
                need(minor >= 2 and value[0] >= 32 and ascii_ok(value[10:]), 'metadata: parameter')
                vals = [int.from_bytes(value[x:x+3], 'little') for x in (1,4,7)]
                need(vals[0] <= vals[2] <= vals[1], 'metadata: parameter bounds')
            if tag == 0x30:
                need(value[0] < slots and ascii_ok(value[1:]), 'metadata: sound name')
            if tag == 0x50:
                need(ascii_ok(value[20:]), 'metadata: assembler identity')
        result.append({'tag': tag, 'data': value.hex()})
    need(not data, 'metadata: missing end marker')
    return result, warnings


def parse(data):
    need(len(data) >= 64, 'check 1: short header')
    magic, h, fmt, n, crc, platform, abi = struct.unpack_from('<4sHHIIII', data)
    cls, tables, count, reserved = data[24:28]
    version, caps, needs, wanted = struct.unpack_from('<4I', data, 28)
    lanes, k, slots, flags = data[44:48]
    release, cycles, claims, header_crc = struct.unpack_from('<4I', data, 48)
    need(magic == MAGIC and fmt == 0 and 64 + 8*tables + 8*count <= h <= 4096 and h % 4 == 0
         and len(data) == h + 3*n, 'check 1: magic/format/header/EOF')
    header = bytearray(data[:h]); header[60:64] = bytes(4)
    need(zlib.crc32(header) == header_crc, 'check 2: header CRC')
    # ABI support is platform check 3, but the file grammar is the ABI-3 family.
    need(0x300 <= abi <= 0x3ff, 'check 5: ABI-3 family required')
    minor = abi - 0x300
    cap_mask = 0x19 | (6 if minor >= 2 else 0)
    need_mask = 0x20 | (0x18 if minor >= 1 else 0) | (6 if minor >= 2 else 0)
    source_mask = 0x3fcfff | (0x3000 if minor >= 1 else 0)
    need(caps & 1 and caps & ~cap_mask == 0 and needs & ~need_mask == 0 and wanted & ~source_mask == 0,
         'check 5: undefined capability/need/source')
    need(1 <= lanes <= (8 if needs & 16 else 4) and 4 <= k <= 16 and slots <= (128 if minor >= 1 else 32), 'check 7: lanes/K/slots')
    need(flags & ~1 == 0 and (not flags & 1 or needs & 8), 'check 8: flags')
    body = data[h:]
    need(389 <= n <= 8192 and zlib.crc32(body) == crc, 'check 10: body bounds/CRC')
    words = [int.from_bytes(body[i:i+3], 'big') for i in range(1152, 1164, 3)]
    need(words[0] == 0x0af080 and 0x2184 <= words[1] < 0x2000+n and words[2:] == [0,0], 'check 11: engine vector')
    need(tables <= 15 and (not tables or minor >= 2), 'check 12: table count/minor')
    descriptors, previous, occupied = [], -1, {}
    for i in range(tables):
        tid, window, tf, zero, offset, length = struct.unpack_from('<4B2H', data, 64+8*i)
        need(previous < tid <= 15 and window in WINDOW_SIZES and claims & (1 << window) and tf & ~3 == 0 and zero == 0
             and 1 <= length <= WINDOW_SIZES[window] and offset+length <= WINDOW_SIZES[window], 'check 12: descriptor')
        need(all(offset+length <= lo or offset >= hi for lo,hi in occupied.get(window, [])), 'tool: overlapping tables')
        occupied.setdefault(window, []).append((offset, offset+length)); previous = tid
        descriptors.append({'id':tid,'window':window,'flags':tf,'offset':offset,'length':length})
    s = source_entries(wanted)
    need(s + lanes <= 24, 'check 15: press size')
    need(reserved == 0 and cls != 0, 'check 16: reserved/class')
    records = [data[64+8*tables+8*i:72+8*tables+8*i] for i in range(count)]
    layout = validate_controls(records, wanted, slots)
    metadata, warnings = parse_metadata(data[64+8*tables+8*count:h], count, wanted, slots, minor)
    labelled = {bytes.fromhex(r['data'])[0] for r in metadata if r['tag'] == 0x12}
    warnings += [f'control record {i}: missing optional label' for i in range(count) if i not in labelled]
    for i, record in enumerate(records):
        if record[0] == 5 and record[1] == 0:
            raw, actual = macro0_projection(record[2])
            if actual != record[2]:
                warnings.append(f'control record {i}: source 0 default {record[2]} projects through stored macro value {raw} to {actual}')
    if cycles > 0xFFFFFF:
        warnings.append('declared cycle budget exceeds the platform meter range (24-bit); load eligible, runtime unready')
    return dict(magic='CRE3', header_size=h, format_minor=fmt, words=n, body_crc=crc, platform=platform, abi=abi,
                engine_class=cls, version=version, capabilities=caps, needs=needs, wanted_sources=wanted, lanes=lanes,
                k=k, sound_slots=slots, flags=flags, release_tail=release, declared_cycles=cycles, memory_claims=claims,
                header_crc=header_crc, descriptors=descriptors, controls=[r.hex() for r in records], layout=layout,
                source_entries=s, press_entries=s+lanes, metadata=metadata, warnings=warnings, body=body)


POLICY_PATH = Path(__file__).with_name('abi3_usb_policy.json')


def policy():
    row = json.loads(POLICY_PATH.read_text())
    expected = dict(format='cre3-usb-policy/1', platform_id=1, queue_abi=0x300,
                    memory_grants=7, offered_capabilities=32, shim_crc32=0xB06FC4D4, max_bytes=15360)
    need(isinstance(row, dict) and all(row.get(key) == value for key, value in expected.items()),
         'unsupported CRE3 USB policy')
    version = row.get('release_version')
    need(isinstance(version, str) and re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version),
         'invalid release version')
    integer(row.get('minimum_app_build'), 6000, 9999, 'minimum application build')
    integer(row.get('cycle_limit'), 1, 0xFFFFFF, 'cycle limit')
    return row


def check(blob):
    """Check a file against the published USB limits and platform policy."""
    row = policy()
    need(64 <= len(blob) <= row['max_bytes'], f'CRE3 file size must be 64..{row["max_bytes"]} bytes')
    info = parse(blob)
    need(info['abi'] == row['queue_abi'], 'check 3: queue ABI')
    need(info['platform'] == row['platform_id'], 'check 4: platform id')
    need(not info['needs'] & ~row['offered_capabilities'], 'check 6: host needs')
    need(not info['memory_claims'] & ~row['memory_grants'], 'check 9: memory grants')
    need(zlib.crc32(info['body'][:1152]) == row['shim_crc32'], 'check 10: shim set')
    need(1 <= info['declared_cycles'] <= row['cycle_limit'], 'check 13: declared cycles')
    return dict(info, kind=info['engine_class'], build=info['version'], base=info['platform'], crc=info['body_crc'],
                engine_format='CRE3', static_eligible=True, device_admissible=False, runtime_check_14='NOT_RUN',
                usb_policy=row)
