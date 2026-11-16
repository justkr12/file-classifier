"""Tkinter GUI for the filename-based file auto classifier.

The user edits a list of rules (type, pattern, target folder), previews the
planned moves and runs them. Rules are stored in rules.json next to the
program and saved automatically on every change.
"""
from __future__ import annotations

import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from classifier import DEFAULT_RULES, Move, RuleError, apply_moves, plan_moves
from rules_io import data_to_rows, load_rows, save_rows, validate_rows

RULES_FILE_NAME = "rules.json"
TYPE_LABELS = {"keyword": "키워드", "regex": "정규식"}
LABEL_TO_TYPE = {label: key for key, label in TYPE_LABELS.items()}

Row = Dict[str, str]


def app_dir() -> Path:
    """Directory of the executable (frozen) or of this script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class App(tk.Tk):
    """Main window."""

    def __init__(self, rules_path: Optional[Path] = None) -> None:
        super().__init__()
        self.title("파일 자동 분류기")
        self.minsize(780, 640)
        self.rules_path = Path(rules_path) if rules_path else app_dir() / RULES_FILE_NAME
        self.rows: List[Row] = []

        self.folder_var = tk.StringVar()
        self.type_var = tk.StringVar(value=TYPE_LABELS["keyword"])
        self.pattern_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.default_var = tk.StringVar()

        self._build_widgets()
        self._load()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)

        folder_frame = ttk.LabelFrame(self, text="대상 폴더")
        folder_frame.grid(row=0, column=0, sticky="ew", **pad)
        folder_frame.columnconfigure(0, weight=1)
        ttk.Entry(folder_frame, textvariable=self.folder_var).grid(
            row=0, column=0, sticky="ew", padx=6, pady=6
        )
        ttk.Button(folder_frame, text="찾아보기", command=self.browse).grid(
            row=0, column=1, padx=6, pady=6
        )

        rules_frame = ttk.LabelFrame(
            self, text="규칙 (위에서부터 먼저 일치하는 규칙이 적용됩니다)"
        )
        rules_frame.grid(row=1, column=0, sticky="nsew", **pad)
        rules_frame.columnconfigure(0, weight=1)
        rules_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            rules_frame,
            columns=("no", "type", "pattern", "target"),
            show="headings",
            selectmode="browse",
            height=9,
        )
        for name, title, width, stretch in (
            ("no", "번호", 50, False),
            ("type", "유형", 80, False),
            ("pattern", "패턴", 340, True),
            ("target", "이동할 폴더", 180, True),
        ):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, stretch=stretch, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scrollbar = ttk.Scrollbar(rules_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        edit = ttk.Frame(rules_frame)
        edit.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        edit.columnconfigure(1, weight=1)
        ttk.Label(edit, text="유형").grid(row=0, column=0, sticky="w")
        ttk.Label(edit, text="패턴").grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(edit, text="이동할 폴더").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            edit,
            textvariable=self.type_var,
            values=list(TYPE_LABELS.values()),
            state="readonly",
            width=8,
        ).grid(row=1, column=0, sticky="w")
        pattern_entry = ttk.Entry(edit, textvariable=self.pattern_var)
        pattern_entry.grid(row=1, column=1, sticky="ew", padx=6)
        target_entry = ttk.Entry(edit, textvariable=self.target_var, width=22)
        target_entry.grid(row=1, column=2, sticky="w")
        pattern_entry.bind("<Return>", lambda _e: self.add_rule())
        target_entry.bind("<Return>", lambda _e: self.add_rule())

        buttons = ttk.Frame(edit)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for text, command in (
            ("추가", self.add_rule),
            ("선택 규칙 수정", self.update_rule),
            ("삭제", self.delete_rule),
            ("위로", lambda: self.move_rule(-1)),
            ("아래로", lambda: self.move_rule(1)),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Label(
            edit,
            text="키워드: 파일명에 포함된 글자 / 정규식 예: ^IMG_\\d+  (대소문자 구분 안 함)",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        default_frame = ttk.LabelFrame(self, text="규칙에 맞지 않는 파일")
        default_frame.grid(row=2, column=0, sticky="ew", **pad)
        default_frame.columnconfigure(1, weight=1)
        ttk.Label(default_frame, text="이동할 폴더 (비우면 이동하지 않음)").grid(
            row=0, column=0, padx=6, pady=6
        )
        ttk.Entry(default_frame, textvariable=self.default_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=6
        )

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", **pad)
        ttk.Button(actions, text="미리보기", command=self.preview).pack(side="left")
        ttk.Button(actions, text="실행", command=self.run).pack(side="left", padx=8)

        log_frame = ttk.LabelFrame(self, text="결과")
        log_frame.grid(row=4, column=0, sticky="nsew", **pad)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=9, state="disabled", wrap="none")
        self.log.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))
        self.log.configure(yscrollcommand=log_scroll.set)

    # ------------------------------------------------------------- helpers
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _error(self, message: str) -> None:
        self._log("오류: " + message)
        messagebox.showerror("오류", message)

    def _confirm(self, message: str) -> bool:
        return bool(messagebox.askyesno("확인", message))

    def report_callback_exception(self, exc, val, tb) -> None:  # noqa: D401
        """Show unexpected errors instead of failing silently."""
        detail = "".join(traceback.format_exception(exc, val, tb))
        self._log(detail)
        messagebox.showerror("예기치 않은 오류", str(val))

    def _default(self) -> Optional[str]:
        return self.default_var.get().strip() or None

    def _row_from_inputs(self) -> Row:
        return {
            "type": LABEL_TO_TYPE.get(self.type_var.get(), "keyword"),
            "pattern": self.pattern_var.get().strip(),
            "target": self.target_var.get().strip(),
        }

    def _selected_index(self) -> Optional[int]:
        selection = self.tree.selection()
        return self.tree.index(selection[0]) if selection else None

    def _refresh(self, select: Optional[int] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.rows):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(index + 1, TYPE_LABELS[row["type"]], row["pattern"], row["target"]),
            )
        if select is not None and 0 <= select < len(self.rows):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def _save(self) -> None:
        try:
            save_rows(self.rules_path, self.rows, self.default_var.get())
        except (RuleError, OSError) as exc:
            self._log("규칙 저장 실패: %s" % exc)

    def _load(self) -> None:
        try:
            self.rows, default = load_rows(self.rules_path)
        except RuleError as exc:
            self._log("규칙 파일을 읽지 못해 기본 규칙을 사용합니다: %s" % exc)
            self.rows, default = data_to_rows(DEFAULT_RULES)
        self.default_var.set(default or "")
        self._refresh()

    def _on_close(self) -> None:
        self._save()
        self.destroy()

    # ------------------------------------------------------------ handlers
    def _on_select(self, _event: object = None) -> None:
        index = self._selected_index()
        if index is None:
            return
        row = self.rows[index]
        self.type_var.set(TYPE_LABELS[row["type"]])
        self.pattern_var.set(row["pattern"])
        self.target_var.set(row["target"])

    def browse(self) -> None:
        chosen = filedialog.askdirectory(title="분류할 폴더 선택")
        if chosen:
            self.folder_var.set(chosen)

    def add_rule(self) -> bool:
        """Append the rule from the input fields. Returns True on success."""
        row = self._row_from_inputs()
        try:
            validate_rows(self.rows + [row], None)
        except RuleError as exc:
            self._error(str(exc))
            return False
        self.rows.append(row)
        self._refresh(select=len(self.rows) - 1)
        self.pattern_var.set("")
        self._save()
        return True

    def update_rule(self) -> bool:
        """Replace the selected rule with the input fields."""
        index = self._selected_index()
        if index is None:
            self._error("수정할 규칙을 목록에서 선택하십시오.")
            return False
        row = self._row_from_inputs()
        candidate = self.rows[:index] + [row] + self.rows[index + 1:]
        try:
            validate_rows(candidate, None)
        except RuleError as exc:
            self._error(str(exc))
            return False
        self.rows = candidate
        self._refresh(select=index)
        self._save()
        return True

    def delete_rule(self) -> bool:
        """Delete the selected rule."""
        index = self._selected_index()
        if index is None:
            self._error("삭제할 규칙을 목록에서 선택하십시오.")
            return False
        del self.rows[index]
        self._refresh(select=min(index, len(self.rows) - 1))
        self._save()
        return True

    def move_rule(self, delta: int) -> bool:
        """Move the selected rule up (-1) or down (+1) to change its priority."""
        index = self._selected_index()
        if index is None:
            self._error("이동할 규칙을 목록에서 선택하십시오.")
            return False
        new_index = index + delta
        if not 0 <= new_index < len(self.rows):
            return False
        self.rows[index], self.rows[new_index] = self.rows[new_index], self.rows[index]
        self._refresh(select=new_index)
        self._save()
        return True

    def _build_plan(self) -> Optional[Tuple[Path, List[Move]]]:
        folder = Path(self.folder_var.get().strip().strip('"'))
        if not self.folder_var.get().strip() or not folder.is_dir():
            self._error("대상 폴더를 올바르게 선택하십시오.")
            return None
        if not self.rows:
            self._error("규칙을 하나 이상 추가하십시오.")
            return None
        try:
            rules, default = validate_rows(self.rows, self.default_var.get())
        except RuleError as exc:
            self._error(str(exc))
            return None
        self._save()
        exclude = [self.rules_path]
        if getattr(sys, "frozen", False):
            exclude.append(Path(sys.executable))
        try:
            folder = folder.resolve()
            return folder, plan_moves(folder, rules, default, exclude)
        except OSError as exc:
            self._error("폴더를 읽을 수 없습니다: %s" % exc)
            return None

    def _log_plan(self, moves: List[Move]) -> None:
        self._log("이동 예정 파일 %d개:" % len(moves))
        for move in moves:
            self._log("  %s  ->  %s/" % (move.src.name, move.target))

    def preview(self) -> None:
        """List planned moves without changing anything."""
        self._clear_log()
        plan = self._build_plan()
        if plan is None:
            return
        _folder, moves = plan
        if not moves:
            self._log("이동할 파일이 없습니다.")
            return
        self._log_plan(moves)
        self._log("미리보기입니다. 실제 이동은 '실행'을 누르십시오.")

    def run(self) -> None:
        """Move files after the user confirms."""
        self._clear_log()
        plan = self._build_plan()
        if plan is None:
            return
        folder, moves = plan
        if not moves:
            self._log("이동할 파일이 없습니다.")
            return
        self._log_plan(moves)
        if not self._confirm("%d개 파일을 이동합니다. 진행하시겠습니까?" % len(moves)):
            self._log("취소했습니다.")
            return
        done, errors = apply_moves(folder, moves)
        self._log("완료: %d개 이동, %d개 실패" % (len(done), len(errors)))
        for src, message in errors:
            self._log("  실패: %s (%s)" % (src.name, message))


def main() -> None:
    """Start the GUI."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
