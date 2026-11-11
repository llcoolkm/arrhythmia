# SPDX-License-Identifier: MIT
"""Report optional author warnings from an assembled DSP56300 listing."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seqcheck import check, reviews


# The FM build emits tab-separated address, words, decoded text and source line.
# The shim builder emits three columns separated by two spaces instead.
LISTING = re.compile(r'([0-9a-fA-F]{4,6})\s{1,}'
                     r'([0-9a-fA-F]{6}(?: [0-9a-fA-F]{6})?)\s{2,}([^\t]+)(?:\t\d+)?')


def read_listing(path):
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        if '\t' in line:
            fields = line.split('\t')
            if len(fields) not in (3, 4) or (len(fields) == 4 and not fields[3].isdigit()):
                raise ValueError(f'line {number}: invalid tab-separated listing row')
            address, words, text = fields[:3]
        else:
            match = LISTING.fullmatch(line)
            if not match:
                raise ValueError(f'line {number}: invalid assembled listing row')
            address, words, text = match.groups()
        encoded = words.split()
        if (not re.fullmatch(r'[0-9a-fA-F]{4,6}', address) or not 1 <= len(encoded) <= 2
                or any(not re.fullmatch(r'[0-9a-fA-F]{6}', word) for word in encoded)
                or not text.strip()):
            raise ValueError(f'line {number}: invalid program address, word or instruction')
        rows.append((int(address, 16), [int(word, 16) for word in encoded], text.strip()))
    if not rows:
        raise ValueError('empty listing')
    rows.sort()
    if any(at + len(words) > next_at for (at, words, _), (next_at, _, _) in zip(rows, rows[1:])):
        raise ValueError('overlapping listing addresses')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('listing', type=Path, help='assembled instruction listing from the author build')
    args = parser.parse_args()
    try:
        rows = read_listing(args.listing)
        warnings = check(rows)
        review_sites = reviews(rows)
    except (OSError, UnicodeError, ValueError) as error:
        print(f'cannot scan listing: {error}', file=sys.stderr)
        return 2
    print(json.dumps({
        'scope': 'author_warning_only',
        'manual_review_required': True,
        'warnings': warnings,
        'review_sites': review_sites,
    }, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
