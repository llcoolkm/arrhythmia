"""Generate the two-field attack/decay coefficient tables and defaults."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib

ATTACK_BASE, DECAY_BASE, GLOBAL_BASE = 0x2B00, 0x2B80, 0x2CA0
ENV_MAX, ENV_FLOOR, MAX_DECAY = 0x7FFFFF, 0x400, 0x7FEFFF
FS, BLOCK = Decimal('48046.875'), 32
ONSET = 0x100000
DEFAULT_INDICES = (0, 64)


def _nearest(value):
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _tables():
    with localcontext() as context:
        context.prec = 70
        attack = tuple(_nearest(Decimal(ENV_MAX) / (Decimal(3) *
                       ((Decimal(2)*FS/BLOCK/3) ** (Decimal(i)/127))))
                       for i in range(128))
        decay = []
        for i in range(128):
            seconds = (Decimal('0.05') * (Decimal(40) ** (Decimal(i)/64)) if i <= 64
                       else Decimal(2) * (Decimal(4) ** (Decimal(i-64)/63)))
            decay.append(_nearest(Decimal(2**23) *
                         (Decimal(10) ** (-Decimal(3)*BLOCK/(seconds*FS)))))
    return (0x2AAAAB,) + attack[1:], tuple(decay)


ATTACK_TABLE, DECAY_TABLE = _tables()


def initial_words():
    """Return 256 table entries and two default coefficients in X memory."""
    words = {base+i: word for base, table in
             ((ATTACK_BASE, ATTACK_TABLE), (DECAY_BASE, DECAY_TABLE))
             for i, word in enumerate(table)}
    words.update({GLOBAL_BASE+i: table[index] for i, (table, index) in
                  enumerate(zip((ATTACK_TABLE, DECAY_TABLE), DEFAULT_INDICES))})
    return words


def describe():
    """Describe the generated data and the two-field parameter wire layout."""
    digest = hashlib.sha256(b''.join(word.to_bytes(3, 'little') for table in
                            (ATTACK_TABLE, DECAY_TABLE) for word in table)).hexdigest()
    return {'attack_base': ATTACK_BASE, 'decay_base': DECAY_BASE,
            'global_parameter_base': GLOBAL_BASE, 'parameter_table_words': 256,
            'parameter_defaults': list(DEFAULT_INDICES),
            'parameter_tables_u24le_sha256': digest,
            'parameter_wire_word0': 0xA06000,
            'parameter_wire_fields': {'attack': [0, 7], 'decay': [7, 7]},
            'parameter_wire_reserved_bits': [14, 23], 'onset': ONSET}
