"""Unit tests for rules_io.py."""
import json
import tempfile
import unittest
from pathlib import Path

import rules_io as r
from classifier import DEFAULT_RULES, RuleError


def row(kind, pattern, target):
    return {"type": kind, "pattern": pattern, "target": target}


class RowsDataTest(unittest.TestCase):
    def test_rows_to_data_one_rule_per_row_and_strips(self):
        data = r.rows_to_data([row("keyword", " a ", " X "), row("regex", "^b", "Y")], "  ")
        self.assertIsNone(data["default_target"])
        self.assertEqual(data["rules"][0], {"type": "keyword", "patterns": ["a"], "target": "X"})
        self.assertEqual(len(data["rules"]), 2)

    def test_data_to_rows_expands_multi_pattern(self):
        rows, default = r.data_to_rows(DEFAULT_RULES)
        self.assertIsNone(default)
        self.assertEqual(len(rows), 3 + 2 + 1)
        self.assertEqual(rows[0], row("keyword", "invoice", "Receipts"))

    def test_data_to_rows_empty_rules_allowed(self):
        rows, default = r.data_to_rows({"rules": [], "default_target": "Misc"})
        self.assertEqual((rows, default), ([], "Misc"))

    def test_data_to_rows_invalid_raises(self):
        with self.assertRaises(RuleError):
            r.data_to_rows({"rules": [{"type": "x", "patterns": ["a"], "target": "T"}]})
        with self.assertRaises(RuleError):
            r.data_to_rows([])

    def test_validate_rows_reports_row_number(self):
        rows = [row("keyword", "ok", "T"), row("regex", "(", "T")]
        with self.assertRaises(RuleError) as ctx:
            r.validate_rows(rows, None)
        self.assertIn("#2", str(ctx.exception))

    def test_validate_rows_rejects_empty_and_unsafe_target(self):
        with self.assertRaises(RuleError):
            r.validate_rows([], None)
        with self.assertRaises(RuleError):
            r.validate_rows([row("keyword", "a", "../x")], None)
        with self.assertRaises(RuleError):
            r.validate_rows([row("keyword", "a", "T")], "a/b")


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "rules.json"

    def test_missing_file_gives_defaults(self):
        rows, default = r.load_rows(self.path)
        self.assertEqual(len(rows), 6)
        self.assertIsNone(default)

    def test_roundtrip(self):
        rows = [row("keyword", "영수증", "영수증"), row("regex", r"^IMG_\d+", "사진")]
        r.save_rows(self.path, rows, "기타")
        loaded, default = r.load_rows(self.path)
        self.assertEqual(loaded, rows)
        self.assertEqual(default, "기타")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["rules.json"])

    def test_save_empty_rows_then_load(self):
        r.save_rows(self.path, [], None)
        self.assertEqual(r.load_rows(self.path), ([], None))

    def test_invalid_save_keeps_old_file(self):
        r.save_rows(self.path, [row("keyword", "a", "T")], None)
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(RuleError):
            r.save_rows(self.path, [row("regex", "(", "T")], None)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["rules.json"])

    def test_load_invalid_json(self):
        self.path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(RuleError):
            r.load_rows(self.path)

    def test_load_legacy_file_format(self):
        self.path.write_text(json.dumps(DEFAULT_RULES), encoding="utf-8")
        rows, _ = r.load_rows(self.path)
        self.assertEqual(len(rows), 6)


if __name__ == "__main__":
    unittest.main()
