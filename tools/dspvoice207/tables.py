# SPDX-License-Identifier: MIT
"""Own VERSION 207 coefficient tables; no firmware or sampled data is included.

The resident layout remains 1217 u24 words. Shared sine, filter, ratio, decay,
sustain and depth curves come from the published VERSION 206 mathematical tables. Attack
and release use the VERSION 207 laws below; their complete digests refuse arithmetic drift.
"""
from fractions import Fraction
import hashlib
import math
from dspvoice206 import tables as shared

FS = 48046.875
Q23 = 1 << 23
MAX = Q23 - 1
WORDS = 1217
U24BE_SHA256 = '7983e39f6a9381887a8d909d2a3b39f6dd7865a13d6433dda0ebae0805095fc4'
ATTACK_SHA256 = 'e8666e762d26ff083e4d5b5a499c36704a998137375b9862b26b9f360381bbda'
RELEASE_SHA256 = '4a611d66e1302575478de24ce0b8061934060941f017c303e2169b9af1b40672'
COSINE_SHA256 = '4b5ec61f443a566f4025bd8cab6dfbb869c35f0de4ba1e6c9750f92eeaf44b99'
ENTRY_SAMPLES = 288
ROTATION_C, ROTATION_S = 8388108, 91503
BANKS = shared.BANKS


def digest(words):
    return hashlib.sha256(b''.join(x.to_bytes(3, 'big') for x in words)).hexdigest()


def attack_table():
    """Linear phase: 10 ms floor, 8 s upper endpoint; integer block quantization."""
    result = []
    for i in range(128):
        seconds = (Fraction(10, 1000) + Fraction(1990, 1000)*Fraction(i, 127)**2
                   + 6*max(Fraction(0), Fraction(i-64, 63))**2)
        blocks = max(3, round(seconds*Fraction(48046875, 1000)/32))
        result.append((MAX+blocks-1)//blocks)
    if digest(result) != ATTACK_SHA256:
        raise ValueError('VERSION207 attack arithmetic drift')
    return result


def entry_rates():
    """Exact integer cosine-rate rotation used by the release entry."""
    c, s = MAX, 0
    rates = []
    for _ in range(ENTRY_SAMPLES):
        rates.append((MAX-c)//2)
        c, s = ((c*ROTATION_C-s*ROTATION_S+Q23//2)//Q23,
                (s*ROTATION_C+c*ROTATION_S+Q23//2)//Q23)
    return rates


def release_table():
    """D32 = 32 * Q23 * (1 - k_sample), fitted including the 288-sample entry.

    The quadratic knob spacing runs from 28.5659545 ms T60 to 8 s. The sample
    update keeps 48-bit precision; this table is not the VERSION 206 block multiplier.
    Binary64/log1p and nearest coefficient selection preserve the pinned law.
    """
    rates = entry_rates()
    floor = .005*1600**(30/127)

    def timing(d):
        loss = sum(-math.log1p(-((d*r+Q23//2)//Q23)/(32*Q23)) for r in rates)
        return (ENTRY_SAMPLES+(math.log(1000)-loss)/(-math.log1p(-d/(32*Q23))))/FS

    result = []
    for i in range(128):
        target = floor+(8-floor)*(i/127)**2
        lo, hi = 1, MAX
        while hi-lo > 1:
            mid = (lo+hi)//2
            if timing(mid) > target:
                lo = mid
            else:
                hi = mid
        result.append(min((lo, hi), key=lambda d: abs(timing(d)-target)))
    if digest(result) != RELEASE_SHA256:
        raise ValueError('VERSION207 release arithmetic drift')
    return result


def cosine32():
    """Own 32-point cosine endpoint weights shared by release, KILL and S0."""
    result = [round(MAX*.5*(1+math.cos(math.pi*i/31))) for i in range(32)]
    result[-1] = 0
    if digest(result) != COSINE_SHA256:
        raise ValueError('VERSION207 terminal-weight arithmetic drift')
    return result


def table_data():
    result = shared.table_data()
    result[:128] = attack_table()
    result[384:512] = release_table()
    if len(result) != WORDS or digest(result) != U24BE_SHA256:
        raise ValueError('VERSION207 resident table arithmetic drift')
    return result
