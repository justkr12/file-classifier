"""GUI tests for gui.py. Skipped automatically when no display is available."""
import tempfile
import unittest
from pathlib import Path

try:
    import tkinter as tk
    import gui
    from rules_io import load_rows

    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except Exception:  # no tkinter or no display
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "tkinter display not available")
class AppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.rules_path = self.root / "rules.json"
        self.app = gui.App(rules_path=self.rules_path)
        self.addCleanup(self.app.destroy)
        self.errors = []
        self.app._error = self.errors.append
        self.app._confirm = lambda _msg: True
        self.app.rows = []
        self.app._refresh()

    def fill(self, kind, pattern, target):
        self.app.type_var.set(gui.TYPE_LABELS[kind])
        self.app.pattern_var.set(pattern)
        self.app.target_var.set(target)

    def test_defaults_loaded_when_no_file(self):
        fresh = gui.App(rules_path=self.root / "other.json")
        self.addCleanup(fresh.destroy)
        self.assertEqual(len(fresh.rows), 6)
        self.assertEqual(len(fresh.tree.get_children()), 6)

    def test_add_rule_saves_and_lists(self):
        self.fill("keyword", "invoice", "Receipts")
        self.assertTrue(self.app.add_rule())
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.assertEqual(self.app.pattern_var.get(), "")
        rows, _ = load_rows(self.rules_path)
        self.assertEqual(rows, [{"type": "keyword", "pattern": "invoice", "target": "Receipts"}])

    def test_invalid_rules_rejected(self):
        for kind, pattern, target in [
            ("regex", "(", "T"),
            ("keyword", "", "T"),
            ("keyword", "a", ""),
            ("keyword", "a", "../x"),
        ]:
            self.fill(kind, pattern, target)
            self.assertFalse(self.app.add_rule())
        self.assertEqual(self.app.rows, [])
        self.assertEqual(len(self.errors), 4)

    def test_select_update_delete_move(self):
        for pattern, target in [("a", "A"), ("b", "B"), ("c", "C")]:
            self.fill("keyword", pattern, target)
            self.app.add_rule()
        self.app.tree.selection_set("1")
        self.app.update()
        self.assertEqual(self.app.pattern_var.get(), "b")
        self.fill("regex", "^b", "B2")
        self.assertTrue(self.app.update_rule())
        self.assertEqual(self.app.rows[1], {"type": "regex", "pattern": "^b", "target": "B2"})

        self.assertTrue(self.app.move_rule(-1))
        self.assertEqual([r["target"] for r in self.app.rows], ["B2", "A", "C"])
        self.assertFalse(self.app.move_rule(-1))

        self.app.tree.selection_set("2")
        self.assertTrue(self.app.delete_rule())
        self.assertEqual([r["target"] for r in self.app.rows], ["B2", "A"])
        rows, _ = load_rows(self.rules_path)
        self.assertEqual([r["target"] for r in rows], ["B2", "A"])

    def test_actions_without_selection_report_error(self):
        self.assertFalse(self.app.update_rule())
        self.assertFalse(self.app.delete_rule())
        self.assertFalse(self.app.move_rule(1))
        self.assertEqual(len(self.errors), 3)

    def test_preview_does_not_move_and_run_moves(self):
        (self.work / "invoice1.pdf").write_text("x")
        (self.work / "notes.txt").write_text("x")
        self.fill("keyword", "invoice", "Receipts")
        self.app.add_rule()
        self.app.folder_var.set(str(self.work))

        self.app.preview()
        self.assertTrue((self.work / "invoice1.pdf").exists())
        self.assertIn("invoice1.pdf", self.app.log.get("1.0", "end"))

        self.app.run()
        self.assertTrue((self.work / "Receipts" / "invoice1.pdf").exists())
        self.assertTrue((self.work / "notes.txt").exists())

    def test_default_target_for_unmatched(self):
        (self.work / "notes.txt").write_text("x")
        self.fill("keyword", "invoice", "Receipts")
        self.app.add_rule()
        self.app.default_var.set("Misc")
        self.app.folder_var.set(str(self.work))
        self.app.run()
        self.assertTrue((self.work / "Misc" / "notes.txt").exists())
        _, default = load_rows(self.rules_path)
        self.assertEqual(default, "Misc")

    def test_run_validation_errors(self):
        self.app.run()  # no folder
        self.app.folder_var.set(str(self.work))
        self.app.run()  # no rules
        self.fill("keyword", "a", "A")
        self.app.add_rule()
        self.app.default_var.set("a/b")
        self.app.run()  # unsafe default
        self.assertEqual(len(self.errors), 3)

    def test_cancel_confirmation_keeps_files(self):
        (self.work / "invoice1.pdf").write_text("x")
        self.fill("keyword", "invoice", "Receipts")
        self.app.add_rule()
        self.app.folder_var.set(str(self.work))
        self.app._confirm = lambda _msg: False
        self.app.run()
        self.assertTrue((self.work / "invoice1.pdf").exists())

    def test_rules_file_is_never_moved(self):
        self.fill("keyword", "rules", "Cfg")
        self.app.add_rule()
        self.app.folder_var.set(str(self.root))
        self.app.rules_path = self.rules_path
        self.app.run()
        self.assertTrue(self.rules_path.exists())


if __name__ == "__main__":
    unittest.main()
