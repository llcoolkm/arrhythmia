"""Read the user's pinned firmware and preserve its local DSP stream records.

No firmware content is included in this module. Addresses are integration points
for the supported input only; another input must not be silently accepted.
"""
import hashlib
import struct

BASE, FLASH_END = 0x08010000, 0x08080000
STOCK_SHA256 = 'd5b9ff5dadf2e284f80d69ecb7087486cf0df315a697bed8272af9de0e90df15'
STOCK_SYX_SHA256 = '11d604a6b6bef0d41378da03718658c7fe9c959ac24e7fef6563796b9932afaf'
PTR_CORE0, PTR_CORE1 = 0x08031658, 0x08031660
STREAM0, STREAM1 = 0x0801A838, 0x080101F0
STOCK_CORE0_WORDS, STOCK_CORE1_WORDS = 8173, 14187
TERMINATOR = 0x100
SPACES = 'PXY'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def w24(word):
    if type(word) is not int or not 0 <= word <= 0xFFFFFF:
        raise ValueError('word outside unsigned 24-bit range')
    return word.to_bytes(3, 'big')


def u24(data, index):
    start = 3 * index
    if index < 0 or start + 3 > len(data):
        raise ValueError('truncated 24-bit word')
    return int.from_bytes(data[start:start + 3], 'big')


def parse_payload(payload):
    if len(payload) % 3:
        raise ValueError('unaligned scatter payload')
    records, index, length = [], 0, len(payload) // 3
    while index < length:
        tag = u24(payload, index)
        if tag == TERMINATOR:
            return records, index, payload[index * 3:]
        if tag not in (0, 1, 2) or index + 3 > length:
            raise ValueError('invalid scatter record header')
        count, address = u24(payload, index + 1), u24(payload, index + 2)
        end = index + 3 + count
        if end > length or address + count > (0x1000 if tag == 0 else 0x40000):
            raise ValueError('scatter record outside its bounds')
        records.append((SPACES[tag], address, tuple(
            u24(payload, offset) for offset in range(index + 3, end))))
        index = end
    raise ValueError('scatter terminator missing')


def memory_map(records):
    result = {}
    for space, address, words in records:
        for offset, word in enumerate(words):
            key = space, address + offset
            if key in result:
                raise ValueError('overlapping scatter writes')
            result[key] = word
    return result


def encode_records(records):
    result = bytearray()
    for space, address, words in records:
        if space not in SPACES:
            raise ValueError('invalid memory space')
        result += w24(SPACES.index(space)) + w24(len(words)) + w24(address)
        result += b''.join(w24(word) for word in words)
    return bytes(result)


def contiguous_records(space, words):
    records = []
    for address in sorted(words):
        if records and address == records[-1][1] + len(records[-1][2]):
            records[-1][2].append(words[address])
        else:
            records.append((space, address, [words[address]]))
    return [(space, address, tuple(data)) for space, address, data in records]


def pinned_streams(image):
    if sha(image) != STOCK_SHA256:
        raise ValueError('input image is not the pinned stock firmware')
    pointers = [struct.unpack_from('<I', image, p - BASE)[0]
                for p in (PTR_CORE0, PTR_CORE1)]
    if pointers != [STREAM0, STREAM1]:
        raise ValueError('input stream pointers differ')
    streams = []
    for address, count in ((STREAM0, STOCK_CORE0_WORDS), (STREAM1, STOCK_CORE1_WORDS)):
        offset, size = address - BASE, 6 + count * 3
        stream = image[offset:offset + size]
        if len(stream) != size or stream[:6] != w24(count) + w24(0):
            raise ValueError('input stream header or bounds differ')
        streams.append(stream)
    return tuple(streams)
