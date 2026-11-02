"""Change the text of the scroll module without a compiler.

    uv run python -I tools/scrolltext.py IN.mod "YOUR TEXT" -o OUT.mod
    uv run python -I tools/scrolltext.py IN.mod --show

The module keeps its text in a fixed field in the code: the first 4 bytes "TXT:" in the file, then 124 bytes. The text is 1 to 120
characters, followed by NUL bytes up to the end of the field. Characters: A to Z, 0 to 9, space and . , ! ? ' - : / &.
Lower-case letters become capitals. The tool writes the new text, sets the rest of the field to NUL and writes a new
CRC-32 into the header (offset 8, zlib CRC-32 of the whole file with that field set to zero). The file size does not
change. The format: docs/user/developers.md, "Text field of the scroll module".

FONT is the reference font of modules/scroll.c: 4 rows, row 1 first, "#" = lit. font_words() gives the glyph
table of the module source.
"""
import argparse
import struct
import sys
import zlib

MAGIC = b"TXT:"
FIELD = 124                 # bytes after the magic
MAX_TEXT = 120
GAP = 1                     # dark columns after each character
END_GAP = 8                 # dark columns after the text, before it repeats
SPACE = 2                   # columns of a space (plus GAP)
MOD_MAGICS = (0x314D5243, 0x324D5243)
MOD_MIN, MOD_MAX = 26, 0x4000 - 0x400

FONT = {
    "A": (".#.", "#.#", "###", "#.#"),
    "B": ("##.", "###", "#.#", "##."),
    "C": (".##", "#..", "#..", ".##"),
    "D": ("##.", "#.#", "#.#", "##."),
    "E": ("###", "##.", "#..", "###"),
    "F": ("###", "##.", "#..", "#.."),
    "G": (".##", "#..", "#.#", ".##"),
    "H": ("#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", "###"),
    "J": ("..#", "..#", "#.#", ".#."),
    "K": ("#.#", "##.", "##.", "#.#"),
    "L": ("#..", "#..", "#..", "###"),
    "M": ("#...#", "##.##", "#.#.#", "#...#"),
    "N": ("#..#", "##.#", "#.##", "#..#"),
    "O": (".##.", "#..#", "#..#", ".##."),
    "P": ("###", "#.#", "###", "#.."),
    "Q": (".##.", "#..#", "#.#.", ".#.#"),
    "R": ("###", "#.#", "##.", "#.#"),
    "S": (".##", "#..", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "###"),
    "V": ("#.#", "#.#", "#.#", ".#."),
    "W": ("#...#", "#.#.#", "#.#.#", ".#.#."),
    "X": ("#..#", ".##.", ".##.", "#..#"),
    "Y": ("#.#", "#.#", ".#.", ".#."),
    "Z": ("###", ".#.", "#..", "###"),
    "0": ("###", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", "###"),
    "2": ("###", "..#", ".#.", "###"),
    "3": ("###", ".##", "..#", "###"),
    "4": ("#.#", "###", "..#", "..#"),
    "5": ("###", "##.", "..#", "###"),
    "6": ("##.", "#..", "###", "###"),
    "7": ("###", "..#", ".#.", ".#."),
    "8": ("###", "###", "#.#", "###"),
    "9": ("###", "###", "..#", ".##"),
    " ": ("." * SPACE,) * 4,
    ".": (".", ".", ".", "#"),
    ",": ("..", "..", ".#", "#."),
    "!": ("#", "#", ".", "#"),
    "?": ("##.", "..#", ".#.", "..."),
    "'": ("#", "#", ".", "."),
    "-": ("..", "##", "..", ".."),
    ":": (".", "#", ".", "#"),
    "/": ("..#", ".#.", ".#.", "#.."),
    "&": (".#..", "#.#.", ".#.#", "#.#."),
}
CHARSET = "".join(FONT)


def glyph(ch):
    """Columns of one character, left first; bit r of a column = row r + 1 lit."""
    rows = FONT[ch]
    return [sum(1 << r for r in range(4) if rows[r][c] == "#") for c in range(len(rows[0]))]


def font_words():
    """The glyph table of modules/scroll.c: one word per ASCII 0x20..0x5A, column c in bits 4c..4c+3, 0 = none."""
    out = []
    for code in range(0x20, 0x5B):
        ch = chr(code)
        out.append(sum(v << 4 * c for c, v in enumerate(glyph(ch))) if ch in FONT and ch != " " else 0)
    return out


def columns(text):
    """The whole strip that the module scrolls: each character, GAP dark columns, and END_GAP after the text."""
    out = []
    for ch in text:
        out += glyph(ch) + [0] * GAP
    return out + [0] * END_GAP


def clean(text):
    """The text as the module stores it, or ValueError with the reason."""
    text = text.upper()
    bad = sorted({ch for ch in text if ch not in FONT})
    if bad:
        raise ValueError("characters not in the font: " + " ".join(repr(ch) for ch in bad)
                         + f"; allowed: A to Z, 0 to 9, space and {' '.join(CHARSET[37:])}")
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError(f"the text has {len(text)} characters; allowed: 1 to {MAX_TEXT}")
    return text


def check_module(blob):
    """Header, size and CRC-32 as the unit checks them (tools/build_module.py check)."""
    if len(blob) < 24:
        raise ValueError("file shorter than a module header")
    magic, size, crc = struct.unpack_from("<3I", blob)
    if magic not in MOD_MAGICS:
        raise ValueError("not a module file (bad magic)")
    if size != len(blob) or not MOD_MIN <= size <= MOD_MAX:
        raise ValueError(f"size field {size} does not match the file ({len(blob)} bytes)")
    if zlib.crc32(blob[:8] + b"\0\0\0\0" + blob[12:]) != crc:
        raise ValueError("CRC mismatch")


def field(blob):
    """Offset of the first text byte (after the magic).

    The field is the first "TXT:" in the file: the code before the field never changes with the text, and the built
    module has the marker once. The text itself can contain "TXT:", so later markers are accepted only inside the
    field; any other marker refuses the file."""
    at = blob.find(MAGIC)
    if at < 0:
        raise ValueError("no text field: is this the scroll module?")
    if at + 4 + FIELD > len(blob):
        raise ValueError("the text field runs past the end of the file")
    end, o = at + 4 + FIELD, blob.find(MAGIC, at + 1)
    while o >= 0:
        if o + 4 > end:
            raise ValueError("a second text marker outside the text field: is this the scroll module?")
        o = blob.find(MAGIC, o + 1)
    return at + 4


def read_text(blob):
    check_module(blob)
    f = field(blob)
    return blob[f:f + FIELD].split(b"\0", 1)[0].decode("ascii")


def write_text(blob, text):
    """A copy of the module with the new text and a new CRC."""
    check_module(blob)
    text = clean(text)
    f = field(blob)
    out = bytearray(blob)
    out[f:f + FIELD] = text.encode("ascii").ljust(FIELD, b"\0")
    struct.pack_into("<I", out, 8, 0)
    struct.pack_into("<I", out, 8, zlib.crc32(out))
    check_module(bytes(out))
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("module", help="scroll module file (.mod)")
    ap.add_argument("text", nargs="?", help="the new text")
    ap.add_argument("-o", "--output", help="output file (required with a text)")
    ap.add_argument("--show", action="store_true", help="print the text in the module")
    a = ap.parse_args(argv)
    try:
        with open(a.module, "rb") as fh:
            blob = fh.read()
        if a.show or a.text is None:
            print(read_text(blob))
            return 0
        if not a.output:
            ap.error("give the output file with -o")
        out = write_text(blob, a.text)
    except (OSError, ValueError) as e:
        sys.exit(f"scrolltext: {e}")
    with open(a.output, "wb") as fh:
        fh.write(out)
    print(f"{a.output}: {len(out)} bytes, text {read_text(out)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
