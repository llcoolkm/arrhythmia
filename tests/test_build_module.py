"""Builder and host-check rules for ITCM modules.

    uv run python -I tests/test_build_module.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import build_module  # noqa: E402

BAD = {
    "data": "static int x = 3; unsigned frame(unsigned char *l, unsigned n) { return ++x; }",
    "rodata": "static const char t[] = \"abcdefgh\"; unsigned frame(unsigned char *l, unsigned n) "
              "{ l[0] = t[n & 7]; return 1; }",
    "other-section": "void init(unsigned char *l) { l[0] = 1; }\n"
                     "__attribute__((section(\".other\"))) unsigned frame(unsigned char *l, unsigned n) "
                     "{ l[1] = 2; return 1; }",
    "no-frame": "void init(unsigned char *l) { l[0] = 1; }",
}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for name, src in BAD.items():
            c = os.path.join(tmp, name + ".c")
            open(c, "w").write(src + "\n")
            r = subprocess.run([sys.executable, "-I", "tools/build_module.py", c, os.path.join(tmp, name + ".mod")],
                               capture_output=True, text=True)
            assert r.returncode != 0 and not os.path.exists(os.path.join(tmp, name + ".mod")), (name, r.stdout)
    for blob, why in ((b"", "empty"), (b"\0" * 25, "zero header")):
        try:
            build_module.check(blob)
            raise AssertionError(f"accepted {why}")
        except ValueError:
            pass
    build_module.check(build_module.build("modules/paint.c", os.devnull))      # published with the export
    print("build_module: bad modules refused, paint accepted")


if __name__ == "__main__":
    main()
