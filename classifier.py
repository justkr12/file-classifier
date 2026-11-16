"""Filename-based file auto classifier.

Moves files in a single folder (non-recursive) into sub-folders according to
keyword / regular-expression rules defined in a JSON file. Rules are evaluated
in order and the first matching rule wins. Files that match no rule are left
in place unless "default_target" is set.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Sequence, Set, Tuple

DEFAULT_RULES_NAME = "rules.json"

DEFAULT_RULES = {
    "default_target": None,
    "rules": [
        {
            "type": "keyword",
            "patterns": ["invoice", "receipt", "영수증"],
            "target": "Receipts",
        },
        {
            "type": "regex",
            "patterns": [r"^IMG_\d+", r"^Screenshot"],
            "target": "Images",
        },
        {
            "type": "regex",
            "patterns": [r"\.(zip|7z|rar)$"],
            "target": "Archives",
        },
    ],
}

_INVALID_NAME_CHARS = set('<>:"/\\|?*')
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {
    "com%d" % i for i in range(1, 10)
} | {"lpt%d" % i for i in range(1, 10)}


class RuleError(ValueError):
    """Raised when the rules definition is invalid."""


@dataclass(frozen=True)
class Rule:
    """A single classification rule."""

    target: str
    patterns: Tuple[Pattern[str], ...]

    def matches(self, filename: str) -> bool:
        """Return True if any pattern of this rule matches the filename."""
        return any(p.search(filename) for p in self.patterns)


@dataclass(frozen=True)
class Move:
    """A planned move of one file into a target sub-folder."""

    src: Path
    target: str


def validate_target(target: object) -> str:
    """Validate a target folder name and return the normalized name.

    The name must be a single, safe path component (no separators, no
    parent references, no characters illegal on Windows, no reserved names).
    """
    if not isinstance(target, str):
        raise RuleError("target must be a string")
    name = target.strip()
    if not name or name in (".", ".."):
        raise RuleError("invalid target folder name: %r" % target)
    if any(c in _INVALID_NAME_CHARS or ord(c) < 32 for c in name):
        raise RuleError("target contains illegal characters: %r" % target)
    if name.endswith("."):
        raise RuleError("target must not end with a dot: %r" % target)
    if name.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise RuleError("target is a reserved name: %r" % target)
    return name


def parse_rules(data: object) -> Tuple[List[Rule], Optional[str]]:
    """Parse and validate a rules definition (already loaded from JSON).

    Returns a tuple of (rules, default_target). Keyword and regex matching are
    both case-insensitive. Keywords are matched as plain substrings.
    """
    if not isinstance(data, dict):
        raise RuleError("rules file root must be an object")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RuleError("'rules' must be a non-empty list")

    rules: List[Rule] = []
    for index, raw in enumerate(raw_rules, start=1):
        label = "rule #%d" % index
        if not isinstance(raw, dict):
            raise RuleError("%s must be an object" % label)
        rule_type = raw.get("type")
        if rule_type not in ("keyword", "regex"):
            raise RuleError("%s: type must be 'keyword' or 'regex'" % label)
        raw_patterns = raw.get("patterns")
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise RuleError("%s: 'patterns' must be a non-empty list" % label)
        compiled: List[Pattern[str]] = []
        for pattern in raw_patterns:
            if not isinstance(pattern, str) or not pattern:
                raise RuleError("%s: patterns must be non-empty strings" % label)
            source = re.escape(pattern) if rule_type == "keyword" else pattern
            try:
                compiled.append(re.compile(source, re.IGNORECASE))
            except re.error as exc:
                raise RuleError("%s: invalid regex %r (%s)" % (label, pattern, exc))
        try:
            target = validate_target(raw.get("target"))
        except RuleError as exc:
            raise RuleError("%s: %s" % (label, exc))
        rules.append(Rule(target=target, patterns=tuple(compiled)))

    default_target = data.get("default_target")
    if default_target is not None:
        default_target = validate_target(default_target)
    return rules, default_target


def load_rules(path: Path) -> Tuple[List[Rule], Optional[str]]:
    """Load rules from a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise RuleError("rules file is not valid JSON: %s" % exc)
    except OSError as exc:
        raise RuleError("cannot read rules file: %s" % exc)
    return parse_rules(data)


def classify(
    filename: str, rules: Sequence[Rule], default_target: Optional[str] = None
) -> Optional[str]:
    """Return the target folder for a filename, or None if it stays in place."""
    for rule in rules:
        if rule.matches(filename):
            return rule.target
    return default_target


def plan_moves(
    folder: Path,
    rules: Sequence[Rule],
    default_target: Optional[str] = None,
    exclude: Iterable[Path] = (),
) -> List[Move]:
    """Build the list of moves for regular files directly inside folder.

    Sub-directories, symbolic links and excluded paths are skipped.
    """
    excluded: Set[Path] = {Path(p).resolve() for p in exclude}
    moves: List[Move] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_symlink() or not entry.is_file():
            continue
        if entry.resolve() in excluded:
            continue
        target = classify(entry.name, rules, default_target)
        if target is not None:
            moves.append(Move(src=entry, target=target))
    return moves


def unique_destination(dst_dir: Path, name: str) -> Path:
    """Return a non-existing path in dst_dir, adding ' (n)' on conflicts."""
    candidate = dst_dir / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while True:
        candidate = dst_dir / ("%s (%d)%s" % (stem, counter, suffix))
        if not candidate.exists():
            return candidate
        counter += 1


def apply_moves(
    folder: Path, moves: Sequence[Move]
) -> Tuple[List[Tuple[Path, Path]], List[Tuple[Path, str]]]:
    """Execute moves. Returns (done, errors); one failure does not stop others."""
    done: List[Tuple[Path, Path]] = []
    errors: List[Tuple[Path, str]] = []
    for move in moves:
        dst_dir = folder / move.target
        try:
            dst_dir.mkdir(exist_ok=True)
            destination = unique_destination(dst_dir, move.src.name)
            shutil.move(str(move.src), str(destination))
            done.append((move.src, destination))
        except OSError as exc:
            errors.append((move.src, str(exc)))
    return done, errors


def _app_dir() -> Path:
    """Directory of the executable (frozen) or of this script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _run(args: argparse.Namespace, interactive: bool) -> int:
    rules_path = Path(args.rules) if args.rules else _app_dir() / DEFAULT_RULES_NAME
    if not rules_path.exists():
        with open(rules_path, "w", encoding="utf-8") as handle:
            json.dump(DEFAULT_RULES, handle, ensure_ascii=False, indent=2)
        print("규칙 파일이 없어 기본 규칙 파일을 만들었습니다: %s" % rules_path)
        print("규칙을 수정한 뒤 다시 실행하십시오.")
        return 0

    raw_folder = args.folder
    if raw_folder is None:
        raw_folder = input("분류할 폴더 경로: ").strip().strip('"')
    folder = Path(raw_folder)
    if not folder.is_dir():
        print("폴더를 찾을 수 없습니다: %s" % folder)
        return 1
    folder = folder.resolve()

    try:
        rules, default_target = load_rules(rules_path)
    except RuleError as exc:
        print("규칙 오류: %s" % exc)
        return 1

    exclude = [rules_path]
    if getattr(sys, "frozen", False):
        exclude.append(Path(sys.executable))
    moves = plan_moves(folder, rules, default_target, exclude)
    if not moves:
        print("이동할 파일이 없습니다.")
        return 0

    print("이동 예정 파일 %d개:" % len(moves))
    for move in moves:
        print("  %s -> %s%s" % (move.src.name, move.target, "/"))
    if args.dry_run:
        print("미리보기 모드: 실제 이동은 수행하지 않았습니다.")
        return 0
    if not args.yes and not _confirm("진행하시겠습니까? (y/N): "):
        print("취소했습니다.")
        return 0

    done, errors = apply_moves(folder, moves)
    print("완료: %d개 이동, %d개 실패" % (len(done), len(errors)))
    for src, message in errors:
        print("  실패: %s (%s)" % (src.name, message))
    return 1 if errors else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Program entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="파일명 규칙 기반 파일 자동 분류기")
    parser.add_argument("folder", nargs="?", help="분류할 폴더 (생략 시 입력 프롬프트)")
    parser.add_argument("--rules", help="규칙 JSON 경로 (기본: 실행 파일 옆 rules.json)")
    parser.add_argument("--yes", action="store_true", help="확인 없이 바로 실행")
    parser.add_argument("--dry-run", action="store_true", help="미리보기만 수행")
    args = parser.parse_args(argv)

    interactive = args.folder is None
    try:
        return _run(args, interactive)
    finally:
        if interactive:
            try:
                input("Enter 키를 누르면 종료합니다.")
            except EOFError:
                pass


if __name__ == "__main__":
    sys.exit(main())
