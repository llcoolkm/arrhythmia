# SPDX-License-Identifier: GPL-3.0-only
"""Engine ABI 3.0 wire encoders, as the engine page (docs/user/engines-dev.md) defines them.

Each command is a pair of 24-bit words (word0, word1) in a ring slot of the queue at X:4D10.
"""
MASK = 0xFFFFFF
FS = 48046.875                     # nominal sample rate, rate code 1
REQ, ACK, PROD, CONS, FAULT, CONSUMED, REJECTED, TICK = range(0x4D00, 0x4D08)
RING = 0x4D10                      # 16 slots of 4 words: epoch, word0, word1, commit
HEADER = 0x4E90                    # ready header, X:4E90..4E9F
LANES = 4


def pitch(freq):
    """(m, e) with M = 1 << 23 | m and a phase increment M << e per sample."""
    inc = round(freq / FS * (1 << 48))
    e = inc.bit_length() - 24
    if not 0 <= e <= 23:
        raise ValueError('pitch outside the ONSET range')
    return (inc >> e) & 0x7FFFFF, e


def onset(lane, gen, m, e, velocity):
    return (0xA80000 | lane << 16 | (m >> 7) & 0xFFFF,
            (m & 0x7F) << 17 | (e & 0x1F) << 12 | (velocity & 0x7F) << 5 | gen & 0xF)


def onset_freq(lane, gen, freq, velocity):
    m, e = pitch(freq)
    return onset(lane, gen, m, e, velocity)


def kill(lane, gen):
    return 0xA01000 | lane << 9, gen & 0xF


def release(lane, gen):
    return 0xA02000 | lane << 9, gen & 0xF


def all_off():
    return 0xA03000, 0


def hello(n, word):
    return 0xA0C000 | n, word & MASK


def param(source, value):
    return 0xA10000 | source << 8, value & MASK


def param3(source, v0=None, v1=None, v2=None):
    word, present = 0, 0
    for k, v in enumerate((v0, v1, v2)):
        if v is not None:
            word |= (v & 0x7F) << 7 * k
            present |= 1 << 21 + k
    return 0xA20000 | source, word | present


def hello_words(lanes=LANES, build=7400):
    """HELLO 0..3 as the opening phase of the firmware sends them."""
    return [hello(0, 3 << 16 | 0 << 8 | 4), hello(1, 0), hello(2, 32 | 0 << 8 | lanes << 12 | 1 << 16),
            hello(3, build)]


def opening_sources(values):
    """Sources 0..11 as the firmware packs them: PARAM3 at ids 0, 3, 6 and 9."""
    return [param3(i, *values[i:i + 3]) for i in (0, 3, 6, 9)]
