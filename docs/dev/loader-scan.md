# Read-only card inventory (0.3.0)

How we know: DESIGN, source and emulation. This describes the final 0.3.0 protocol used by the
combined [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html). It does not establish hardware qualification.
Source of truth: `patches/src/enginelist.c`, `patches/src/devkit_abi3.c` and `tools/module.py` (`decode_inventory`,
`decode_card_row`). Available in arrhythmia-firmware; not arrhythmia-devkit.

The scan reads pack selections and engine files in Main-task steps. It does not recover helper files, create
folders, populate the pack table or load engines. It uses 576 bytes of owned SRAM, separate from module ITCM.
Only one card operation runs at a time. Keep playback stopped and requests sequential. The host accumulates the
stream; firmware retains one row rather than a full card listing.

## Requests and replies

All messages below start with `F0 00 20 29 00 60 10` and end with `F7`. `nonce` is two 7-bit bytes, high group first.
A host must match nonce, row index, length and framing before accepting a reply.

| Subcommand and request after the prefix | Reply after the prefix | Full reply length |
|---|---|---|
| `11 nonce_hi nonce_lo` | `11 answer nonce_hi nonce_lo state flags count` | 15 bytes |
| `12 nonce_hi nonce_lo index` | `12 answer nonce_hi nonce_lo state flags count index row_nibbles` | 208 bytes |

`row_nibbles` is 192 bytes: two nibbles per byte of the 96-byte row, high nibble first. Non-row replies retain the
same framing; interpret the row only when answer is 0.

Answers: 0 ready/OK, 1 busy, 2 stale, 3 range/end, 4 reserved/unavailable. States: 0 none, 1 active, 2 done, 3 failed.
Global flags: bit 0 incomplete, bit 1 I/O error, bit 2 cancellation. Any such flag means the listing is unknown;
it must not be converted into a Samples selection.

Send subcommand `11` once to begin. When no scan is active, it starts a fresh scan even with the same nonce.
While active, `11` with the same nonce is idempotent; a different nonce is busy. Poll only with `12`, including
completion and failure checks: sending `11` after completion starts another scan. Fetch indices sequentially from 0 to 63 with `12`: requesting the next row schedules its work and returns busy
until ready. Repeating the current index returns the cached row; older indices are stale. End is answer 3 with
state 2 and index at least count. A complete scan must reach that end, without global error flags.

The scan caps output at 64 rows, checks at most 512 directory entries per folder and reads engine files in bounded
CRC steps. Files of 32,768 bytes or more are unreadable to this scan; engine writes have their own stricter size
limit. Inactivity of 32.768 seconds or card abort (`10 7F`) requests closure in Main, including during playback. Cancelled or partial results
do not authorize writes.

## Row layout

All text fields are zero-padded. Seven 32-bit fields are little-endian.

| Offset | Size | Meaning |
|---|---|---|
| 0 | 1 | Type: 1 pack, 2 engine, 3 default selection |
| 1 | 1 | Raw selector; 127 when absent |
| 2 | 1 | Pad reported by the unit; 0 when no validated mapping |
| 3 | 1 | Row flags: bit 0 validated mapping, bit 1 ambiguity, bit 2 lossy/truncated name, bit 3 old `.cfo` fallback |
| 4 | 1 | Exact `.cfg` state: 0 absent, 1 none, 2 engine, 3 invalid, 4 I/O error |
| 5 | 1 | Effective source: 0 absent, 1 own selection, 2 default selection |
| 6 | 1 | Effective selection state, same values as offset 4 |
| 7 | 1 | Engine compatibility: 0 unknown, 1 compatible, 2 incompatible, 3 unreadable, 4 missing |
| 8 | 24 | Display name |
| 32 | 8 | Own engine basename |
| 40 | 8 | Effective engine basename |
| 48 | 12 | Engine filename |
| 60 | 28 | Size, whole-file CRC32, build/version word, kind, ABI, base, body CRC32 |
| 88 | 8 | Reserved |

The exact `.cfg` state stays separate from its effective `.cfo` fallback: an absent `.cfg` does not prove there is
no effective selection. Scanning reports that fallback without recovering or renaming it.

Names are exact ASCII only. Stock filesystem names use OEM bytes; non-ASCII or truncated names carry row flag 4
and expose an actual short FAT alias. Do not reinterpret those bytes as UTF-8 or authorize a pack write from a
lossy name. A missing, ambiguous or unvalidated mapping must remain disabled. Raw selector 0 is omitted because
its physical pad mapping is unproven; never invent a factory-pack row. Raw-to-pad mappings are source/emulation
results apart from the separately observed raw 30 / pad 31 case.

The engine build word is independent of firmware version. The high-bit marker encodes semantic version; legacy
unmarked words mean unknown version. A checksum difference proves no ordering. See
[engine metadata](../user/developers.md#engines-over-usb).

Engine removal uses `10 13` followed by eight basename bytes and the existing 26-byte card-job status. It operates
on the named `.dse` and its typed `.dst` / `.dso` helpers. Pack pointers remain intact; the next pack load uses
samples when its selected engine is missing.
