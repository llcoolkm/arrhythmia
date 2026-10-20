"""Generate four 256-sample bandlimited saw banks for linear interpolation."""
import math

TABLE_BASE = 0x1500
HARMONICS = (15, 7, 3, 1)


def _bank(harmonics):
    coefficients = [(h, (-2/math.pi)*((-1)**h)/h)
                    for h in range(1, harmonics+1)]
    # Compensate the interpolator's baseband sinc-squared response.
    adjusted = [(h, c/(math.sin(math.pi*h/256)/(math.pi*h/256))**2)
                for h, c in coefficients]
    raw = [sum(c*math.sin(2*math.pi*h*i/256) for h, c in adjusted)
           for i in range(256)]
    scale = .98/max(abs(value) for value in raw)
    return [round(value*scale*0x7FFFFF) for value in raw]


def saw_words():
    """Return unsigned 24-bit Y-memory initialization for the saw banks."""
    return {TABLE_BASE+bank*256+i: word & 0xFFFFFF
            for bank, harmonics in enumerate(HARMONICS)
            for i, word in enumerate(_bank(harmonics))}
