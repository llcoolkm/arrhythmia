#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate repository-hosted engine entries and community contribution files.

Static eligibility is not a unit qualification or permission to list a submission.
Maintainers review listings and source before merging catalogue changes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cre3_check
import engine_file

FORMAT = 'engine-catalogue/1'
FIELDS = {'name', 'file', 'sha256', 'size', 'kind', 'version', 'author', 'licence', 'status'}
CATALOGUE_PATH = Path('web/engines/index.json')
FILE_NAME = re.compile(r'[a-z0-9]{1,8}\.dse')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, name, limit=64):
    need(isinstance(value, str) and value.strip() == value and 1 <= len(value) <= limit
         and all(32 <= ord(c) < 127 for c in value), f'{name}: printable text required')
    return value


def validate_catalogue(value):
    """Return entries after validating only the shared catalogue data schema."""
    need(isinstance(value, dict) and set(value) == {'format', 'engines'}
         and value['format'] == FORMAT, 'unknown engine catalogue format')
    rows = value['engines']
    need(isinstance(rows, list) and len(rows) <= 127, 'catalogue engines must be a bounded list')
    names, files, kinds = set(), set(), set()
    for row in rows:
        need(isinstance(row, dict) and set(row) == FIELDS, 'catalogue entry fields differ')
        name = text(row['name'], 'name')
        file = row['file']
        need(isinstance(file, str) and FILE_NAME.fullmatch(file)
             and file not in ('default.dse', 'none.dse'), 'file must be a local lowercase engine basename')
        need(isinstance(row['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', row['sha256']), 'invalid engine SHA-256')
        cre3_check.integer(row['size'], 64, cre3_check.policy()['max_bytes'], 'engine size')
        cre3_check.integer(row['kind'], 1, 127, 'engine kind')
        need(isinstance(row['version'], str) and re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', row['version']),
             'engine version must be major.minor.patch')
        text(row['author'], 'author')
        licence = text(row['licence'], 'licence', 32)
        need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]*', licence), 'licence must be an SPDX identifier')
        need(row['status'] in ('official', 'community'), 'catalogue status must be official or community; listing still requires maintainer review')
        need(name.casefold() not in names and file not in files and row['kind'] not in kinds,
             'duplicate catalogue name, file or kind')
        names.add(name.casefold()); files.add(file); kinds.add(row['kind'])
    return rows


def check_asset(entry, blob):
    """Check catalogue binding plus the one public CRE3/platform checker."""
    validate_catalogue({'format': FORMAT, 'engines': [entry]})
    need(len(blob) == entry['size'], 'engine size differs from catalogue')
    need(hashlib.sha256(blob).hexdigest() == entry['sha256'], 'engine SHA-256 differs from catalogue')
    info = cre3_check.check(blob)
    need(info['kind'] == entry['kind'], 'engine kind differs from catalogue')
    need(engine_file.decode_semver(info['version']) == entry['version'], 'engine version differs from catalogue')
    return info


def local_file(root, relative):
    """Accept ordinary in-repository files, without symlink or URL escape."""
    root = Path(root).resolve()
    relative = Path(relative)
    need(not relative.is_absolute() and '..' not in relative.parts, 'file escapes repository')
    path = root / relative
    need(all(not (root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)),
         'symlinked contribution files are not allowed')
    need(path.is_file() and path.resolve().is_relative_to(root), 'repository file is missing or escapes root')
    return path


def check_listing(entry, blob, listing):
    """Run the canonical author checker on an address/word-bound sidecar.

    Decoded text is supplied by the author, not independently disassembled here.
    Warnings and unresolved manual review sites remain visible to maintainers.
    """
    sys.path.insert(0, str(HERE / 'dsp/abi3'))
    import sequence_warning
    rows = sequence_warning.read_listing(listing)
    info = cre3_check.check(blob)
    words = [int.from_bytes(info['body'][i:i+3], 'big') for i in range(0, len(info['body']), 3)]
    covered = set()
    for address, encoded, _ in rows:
        offset = address - 0x2000
        need(0 <= offset and offset+len(encoded) <= len(words), 'listing address outside engine file')
        need(words[offset:offset+len(encoded)] == encoded, 'listing words differ from engine file')
        covered.update(range(address, address+len(encoded)))
    need(set(range(0x2180, 0x2000+len(words))) <= covered, 'listing omits engine-body words')
    findings = sequence_warning.check(rows)
    need(not findings, 'instruction-sequence findings require correction before listing: '+json.dumps(findings))
    return dict(listing=listing.name, warnings=findings,
                review_sites=sequence_warning.reviews(rows), manual_review_required=True,
                scope='author listing words bound to file; decoded text not independently disassembled; author warnings only')


def version_tuple(version):
    return tuple(int(value) for value in version.split('.'))


def kind_assignments():
    """Allocation metadata, separate from runtime CRE3 admission."""
    value = json.loads((HERE / 'engine_kind_registry.json').read_text())
    need(isinstance(value, dict) and set(value) == {'format', 'assignments'}
         and value['format'] == 'engine-kind-registry/1' and isinstance(value['assignments'], dict),
         'maintained kind allocation metadata is invalid')
    for key, row in value['assignments'].items():
        need(re.fullmatch(r'[1-9][0-9]*', key) and 1 <= int(key) <= 127, 'invalid assigned kind')
        need(isinstance(row, dict) and set(row) == {'name', 'file'}, 'invalid kind assignment')
        text(row['name'], 'assigned kind name')
        need(row['file'] is None or FILE_NAME.fullmatch(row['file']), 'invalid assigned engine file')
    return value['assignments']


def check_repository(root, catalogue=CATALOGUE_PATH, *, base_dir=None):
    root = Path(root).resolve()
    path = local_file(root, catalogue)
    need(path.relative_to(root) == CATALOGUE_PATH, 'catalogue must be web/engines/index.json')
    entries = validate_catalogue(json.loads(path.read_text()))
    base = Path(base_dir).resolve() if base_dir is not None else None
    previous = {}
    if base and (base / CATALOGUE_PATH).is_file():
        previous = {row['file']: row for row in validate_catalogue(json.loads(local_file(base, CATALOGUE_PATH).read_text()))}
    allocation = kind_assignments() if base else {}
    old_kinds = {row['kind']: row['file'] for row in previous.values()}
    reports = []
    for entry in entries:
        relative = CATALOGUE_PATH.parent / entry['file']
        asset = local_file(root, relative)
        need(asset.stat().st_size <= cre3_check.policy()['max_bytes'], 'engine exceeds installable limit')
        blob = asset.read_bytes(); info = check_asset(entry, blob)
        old = previous.get(entry['file'])
        incumbent = base / relative if base else None
        incumbent_blob = local_file(base, relative).read_bytes() if incumbent and incumbent.exists() else None
        changed = base is not None and blob != incumbent_blob
        if base:
            incumbent_info = cre3_check.check(incumbent_blob) if incumbent_blob is not None else None
            if incumbent_info is not None:
                need(incumbent_info['kind'] == entry['kind'], 'an existing repository engine file cannot change kind')
                if changed and old is None:
                    old_version = engine_file.decode_semver(incumbent_info['version'])
                    need(old_version is not None and version_tuple(entry['version']) > version_tuple(old_version),
                         'changed incumbent engine must increase its version')
            if old:
                need(entry['kind'] == old['kind'], 'an existing engine file cannot change kind')
                if blob != incumbent_blob:
                    need(version_tuple(entry['version']) > version_tuple(old['version']), 'changed engine must increase its version')
            else:
                need(entry['kind'] not in old_kinds, 'new engine collides with an existing kind')
                assigned = allocation.get(str(entry['kind']))
                if assigned is not None:
                    need(incumbent_info is not None and incumbent_info['kind'] == entry['kind']
                         and assigned.get('file') == entry['file'], 'new engine kind is already assigned or reserved')
        report = dict(file=entry['file'],kind=entry['kind'],version=entry['version'],static_eligible=True,
                      runtime_check_14='NOT_RUN',device_admissible=False)
        if changed:
            listing = local_file(root, relative.with_suffix('.lst'))
            report['sequence'] = check_listing(entry, blob, listing)
        reports.append(report)
    if base:
        listed = {CATALOGUE_PATH.parent / entry['file'] for entry in entries}
        for file in root.rglob('*'):
            if file.suffix.lower() == '.dse' and '.git' not in file.relative_to(root).parts:
                relative = file.relative_to(root)
                file = local_file(root, relative)
                need(file.stat().st_size <= cre3_check.policy()['max_bytes'], 'DSE contribution exceeds installable limit')
                old = base / relative
                if not old.is_file() or file.read_bytes() != local_file(base, relative).read_bytes():
                    need(relative in listed, 'new or changed DSE file has no catalogue entry')
    return dict(status='PASS',engines=reports,maintainer_review_required=True,
                listing_scope='CI never adds a catalogue entry or establishes hardware qualification')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalogue', nargs='?', type=Path, default=CATALOGUE_PATH)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--base-dir', type=Path, help='trusted base checkout for contribution checks')
    args = parser.parse_args()
    try:
        result = check_repository(args.repo_root, args.catalogue, base_dir=args.base_dir)
    except (OSError, ValueError, UnicodeError) as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)})); return 1
    print(json.dumps(result, indent=2)); return 0

if __name__ == '__main__':
    sys.exit(main())
