"""Unit tests for classifier.py (run: python -m unittest -v)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import classifier as c


def make_rules():
    return c.parse_rules(
        {
            "rules": [
                {"type": "keyword", "patterns": ["invoice", "영수증"], "target": "Receipts"},
                {"type": "regex", "patterns": [r"^IMG_\d+"], "target": "Images"},
                {"type": "keyword", "patterns": ["IMG"], "target": "Never"},
            ]
        }
    )


class ParseRulesTest(unittest.TestCase):
    def test_valid(self):
        rules, default = make_rules()
        self.assertEqual(len(rules), 3)
        self.assertIsNone(default)

    def test_invalid_structures(self):
        bad = [
            [],
            {},
            {"rules": []},
            {"rules": [{"type": "glob", "patterns": ["a"], "target": "x"}]},
            {"rules": [{"type": "regex", "patterns": [], "target": "x"}]},
            {"rules": [{"type": "regex", "patterns": ["("], "target": "x"}]},
            {"rules": [{"type": "keyword", "patterns": [""], "target": "x"}]},
            {"rules": [{"type": "keyword", "patterns": ["a"]}]},
        ]
        for data in bad:
            with self.subTest(data=data):
                with self.assertRaises(c.RuleError):
                    c.parse_rules(data)

    def test_unsafe_targets_rejected(self):
        for target in ["../x", "a/b", "a\\b", "..", ".", "", "  ", "con", "NUL.txt", "a:b", "x."]:
            with self.subTest(target=target):
                with self.assertRaises(c.RuleError):
                    c.validate_target(target)

    def test_valid_target_trimmed(self):
        self.assertEqual(c.validate_target("  영수증 "), "영수증")


class ClassifyTest(unittest.TestCase):
    def test_keyword_case_insensitive(self):
        rules, _ = make_rules()
        self.assertEqual(c.classify("My INVOICE 01.pdf", rules), "Receipts")
        self.assertEqual(c.classify("3월 영수증.png", rules), "Receipts")

    def test_regex_and_first_match_wins(self):
        rules, _ = make_rules()
        self.assertEqual(c.classify("IMG_0012.jpg", rules), "Images")
        self.assertEqual(c.classify("holiday_IMG.jpg", rules), "Never")

    def test_keyword_is_literal_not_regex(self):
        rules, _ = c.parse_rules(
            {"rules": [{"type": "keyword", "patterns": ["a.b"], "target": "T"}]}
        )
        self.assertEqual(c.classify("a.b", rules), "T")
        self.assertIsNone(c.classify("axb", rules))

    def test_no_match_and_default(self):
        rules, _ = make_rules()
        self.assertIsNone(c.classify("notes.txt", rules))
        self.assertEqual(c.classify("notes.txt", rules, "Misc"), "Misc")


class FileOpsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def touch(self, name, text="x"):
        (self.root / name).write_text(text, encoding="utf-8")

    def test_plan_skips_dirs_and_excluded(self):
        self.touch("invoice1.pdf")
        self.touch("rules.json")
        self.touch("invoice_rules.txt")
        (self.root / "invoice_dir").mkdir()
        rules, default = make_rules()
        moves = c.plan_moves(self.root, rules, default, [self.root / "rules.json"])
        names = sorted(m.src.name for m in moves)
        self.assertEqual(names, ["invoice1.pdf", "invoice_rules.txt"])

    def test_unique_destination(self):
        (self.root / "a.txt").write_text("1")
        (self.root / "a (1).txt").write_text("2")
        self.assertEqual(c.unique_destination(self.root, "a.txt").name, "a (2).txt")
        self.assertEqual(c.unique_destination(self.root, "b.txt").name, "b.txt")

    def test_apply_moves_with_conflict(self):
        self.touch("invoice1.pdf", "new")
        (self.root / "Receipts").mkdir()
        (self.root / "Receipts" / "invoice1.pdf").write_text("old")
        rules, default = make_rules()
        moves = c.plan_moves(self.root, rules, default)
        done, errors = c.apply_moves(self.root, moves)
        self.assertEqual(len(done), 1)
        self.assertEqual(errors, [])
        self.assertFalse((self.root / "invoice1.pdf").exists())
        self.assertEqual((self.root / "Receipts" / "invoice1.pdf").read_text(), "old")
        self.assertEqual((self.root / "Receipts" / "invoice1 (1).pdf").read_text(), "new")

    def test_apply_moves_reports_error_and_continues(self):
        self.touch("invoice1.pdf")
        self.touch("IMG_1.jpg")
        self.touch("Receipts")  # a file blocks creating the folder "Receipts"
        rules, default = make_rules()
        moves = c.plan_moves(self.root, rules, default)
        done, errors = c.apply_moves(self.root, moves)
        self.assertEqual([p.name for p, _ in errors], ["invoice1.pdf"])
        self.assertEqual(len(done), 1)
        self.assertTrue((self.root / "Images" / "IMG_1.jpg").exists())


class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.rules_path = self.root / "rules.json"
        self.rules_path.write_text(
            json.dumps(
                {"rules": [{"type": "keyword", "patterns": ["invoice"], "target": "Receipts"}]}
            ),
            encoding="utf-8",
        )
        (self.work / "invoice1.pdf").write_text("x")

    def run_main(self, *extra):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = c.main([str(self.work), "--rules", str(self.rules_path), *extra])
        return code, buffer.getvalue()

    def test_dry_run_does_not_move(self):
        code, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue((self.work / "invoice1.pdf").exists())

    def test_yes_moves_file(self):
        code, _ = self.run_main("--yes")
        self.assertEqual(code, 0)
        self.assertTrue((self.work / "Receipts" / "invoice1.pdf").exists())

    def test_missing_folder_returns_error(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = c.main([str(self.root / "nope"), "--rules", str(self.rules_path)])
        self.assertEqual(code, 1)

    def test_bad_rules_returns_error(self):
        self.rules_path.write_text("{not json", encoding="utf-8")
        code, _ = self.run_main("--yes")
        self.assertEqual(code, 1)
        self.assertTrue((self.work / "invoice1.pdf").exists())

    def test_missing_rules_creates_default_and_exits(self):
        missing = self.root / "new_rules.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = c.main([str(self.work), "--rules", str(missing), "--yes"])
        self.assertEqual(code, 0)
        self.assertTrue(missing.exists())
        self.assertTrue((self.work / "invoice1.pdf").exists())
        c.load_rules(missing)


if __name__ == "__main__":
    unittest.main()
