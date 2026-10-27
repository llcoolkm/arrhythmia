# SPDX-License-Identifier: MIT
"""Original VERSION 206 resident tables, generated without stock firmware.

Layout (1217 big-endian u24 words): attack128, decay128, sustain128,
release128, quarter-wave sine257, cutoff128, resonance128, depth128,
then sixteen four-operator ratio banks64. These are our mathematical tables
and curated ratios, not samples or coefficients extracted from stock code.

Attack/decay use Decimal precision70 and nearest, ties upward, preserving
our earlier two-field envelope. Sustain/depth use exact integer arithmetic.
Release/sine/filter preserve the original Python binary64 expressions and
round() (nearest, ties even). Their complete serialized digest is checked:
a different libm result must fail instead of silently changing firmware.

The sine is sin(2*pi*i/1024), scaled to positive signed Q23; the boot code
reconstructs its other quadrants. Filter entries are factored tan(pi*f/fs)/4
and 1/(2*Q), not complete filter coefficients. Their curve is our logarithmic
20Hz..20kHz cutoff and sqrt(.5)..4 resonance. Index127 repeats the index126
cutoff coefficient; true bypass is selected separately by the DSP.

Google msfa was an external comparison oracle for the FM kernel. No msfa
source, sine table, envelope table, or Yamaha table is copied here. These
curves are the Arrhythmia curves, not a claim to implement DX envelope laws.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import math

FS = 48046.875
Q23 = 1 << 23
MAX = Q23 - 1
WORDS = 1217
U24BE_SHA256 = 'b3e5dc3efea197b0db27a7bb09b78a892e539bb771d9595b7ba7fabf08bea03c'
# Ratios are stored as twice the frequency ratio, allowing half-integers.
# Original curated timbres; bank order and values form part of VERSION206.
BANKS = (
    ('Unity', (2, 2, 2, 2)), ('Brass', (2, 4, 2, 6)),
    ('Warm', (2, 2, 4, 4)), ('Bright', (2, 4, 6, 8)),
    ('Hollow', (2, 6, 10, 14)), ('Octaves', (2, 4, 4, 4)),
    ('Organ', (2, 4, 8, 16)), ('Fifths', (2, 3, 4, 6)),
    ('Bell', (2, 5, 7, 11)), ('Metal', (2, 7, 10, 13)),
    ('Chime', (2, 9, 13, 17)), ('Sub', (2, 1, 4, 6)),
    ('Frost', (2, 1, 4, 3)), ('Wood', (2, 6, 6, 2)),
    ('Deep bell', (2, 11, 16, 23)), ('High metal', (2, 16, 24, 32)),
)


def envelope_tables():
    """Attack/decay/sustain/release coefficients, each128 words."""
    def nearest(x):
        return int(x.to_integral_value(rounding=ROUND_HALF_UP))
    with localcontext() as context:
        context.prec = 70
        fs = Decimal('48046.875')
        attack = [nearest(Decimal(MAX) / (Decimal(3) *
                  ((Decimal(2)*fs/32/3) ** (Decimal(i)/127)))) for i in range(128)]
        attack[0] = 0x2AAAAB
        decay = []
        for i in range(128):
            seconds = (Decimal('0.05') * Decimal(40) ** (Decimal(i)/64) if i <= 64
                       else Decimal(2) * Decimal(4) ** (Decimal(i-64)/63))
            decay.append(nearest(Decimal(Q23) * Decimal(10) **
                                 (-Decimal(3)*32/(seconds*fs))))
    sustain = [(i*MAX+63)//127 for i in range(128)]
    release = [round(Q23*10**(-3*32/(FS*.005*1600**(i/127)))) for i in range(128)]
    return attack, decay, sustain, release


def table_data():
    """Return the exact delivered resident P words; refuse arithmetic drift."""
    attack, decay, sustain, release = envelope_tables()
    sine = [round(MAX*math.sin(2*math.pi*i/1024)) for i in range(257)]
    cutoff = [round(Q23*math.tan(math.pi*(20*1000**(min(i,126)/126))/FS)/4)
              for i in range(128)]
    low = math.sqrt(.5)
    resonance = [round(Q23/(2*(low*(4/low)**(i/127)))) for i in range(128)]
    depth = [(MAX*i+63)//127 for i in range(128)]
    ratio = [n for _, bank in BANKS for n in bank]
    words = attack + decay + sustain + release + sine + cutoff + resonance + depth + ratio
    if len(words) != WORDS or any(not 0 <= word <= 0xFFFFFF for word in words):
        raise ValueError('VERSION206 resident table layout/range changed')
    digest = hashlib.sha256(b''.join(word.to_bytes(3, 'big') for word in words)).hexdigest()
    if digest != U24BE_SHA256:
        raise ValueError(f'VERSION206 resident table arithmetic drift: {digest}')
    return words
