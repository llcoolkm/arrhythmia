# SPDX-License-Identifier: MIT
"""Exercise the author warning command on synthetic assembled listings."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/dsp/abi3/sequence_warning.py"


class SequenceWarningTests(unittest.TestCase):
    def scan(self, listing):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assembled.lst"
            path.write_text(listing)
            return subprocess.run(
                [sys.executable, "-I", str(CLI), str(path)],
                capture_output=True, text=True, check=False,
            )

    def test_warning_keeps_exit_zero(self):
        result = self.scan("000100\t0604a0\trep #4\n000101\t00000c\trts\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "scope": "author_warning_only",
            "manual_review_required": True,
            "review_sites": [],
            "warnings": [{
                "rule": "A.3.8 REP repeats a flow-changing, loop or two-word instruction",
                "address": "0101",
                "text": "rts",
                "la": None,
            }],
        })

    def test_clean_still_requires_manual_review(self):
        result = self.scan("000100  000000  nop\n000101  00000c  rts\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "scope": "author_warning_only",
            "manual_review_required": True,
            "review_sites": [],
            "warnings": [],
        })

    def loop_listing(self, first_word, extension, head, final_instruction):
        lines = [f"000100\t{first_word:06x} {extension:06x}\t{head}"]
        lines.extend(f"{at:06x}\t000000\tnop" for at in range(0x102, 0x107))
        lines.append(f"000107\t{final_instruction}")
        lines.extend(("000108\t000000\tnop", "000109\t00000c\trts"))
        return "\n".join(lines)

    def test_do_and_dor_forms_flag_the_same_loop_end(self):
        forms = (
            (0x060180, 0x108, "do #<$1,>$109"),
            (0x000203, 0x108, "do forever, >$109"),
            (0x060190, 0x008, "dor #<$1,>*+$9"),
            (0x000202, 0x008, "dor forever, >*+$9"),
        )
        for first_word, extension, head in forms:
            with self.subTest(head=head):
                result = self.scan(self.loop_listing(first_word, extension, head, "07d884\tmove p:(r0)+,x0"))
                self.assertEqual(result.returncode, 0, result.stderr)
                warnings = json.loads(result.stdout)["warnings"]
                self.assertEqual([(entry["address"], entry["la"]) for entry in warnings], [("0107", "0108")])
                self.assertIn("starts at LA-1", warnings[0]["rule"])

    def test_relative_dor_reports_brk_before_loop_end(self):
        listing = self.loop_listing(0x060190, 0x008, "dor #<$1,>*+$9", "000000\tbrklt")
        result = self.scan(listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = json.loads(result.stdout)["warnings"]
        self.assertEqual([(entry["address"], entry["text"], entry["la"]) for entry in warnings],
                         [("0107", "brklt", "0108")])

    def test_lf_fv_changes_and_value_dependent_moves_are_distinct(self):
        listing = "\n".join((
            "000100\t000000\tandi #$b3,mr",
            "000101\t000000\tbclr #20,sr",
            "000102\t000000\tmove x0,sr",
            "000103\t000000\tori #$80,mr",
            "000104\t000000\tbset #16,sr",
        ))
        result = self.scan(listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([entry["address"] for entry in report["warnings"]], ["0103", "0104"])
        self.assertEqual(report["review_sites"], [{
            "rule": "A.3.2 MOVE to SR: LF/FV must keep their current values",
            "address": "0102",
            "text": "move x0,sr",
        }])

    def test_malformed_listing_refused(self):
        result = self.scan("not an instruction listing\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot scan listing", result.stderr)


if __name__ == "__main__":
    unittest.main()
