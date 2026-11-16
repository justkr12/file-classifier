"""Conversion and persistence helpers between GUI rows and the rules JSON.

A GUI row is a dict with the keys "type" ("keyword" or "regex"), "pattern"
and "target". Each row becomes exactly one rule holding one pattern. Rules
files that group several patterns under one target are expanded into one row
per pattern when loaded.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from classifier import DEFAULT_RULES, Rule, RuleError, parse_rules, validate_target

Row = Dict[str, str]
RULE_TYPES = ("keyword", "regex")


def _normalize_default(default_target: Optional[str]) -> Optional[str]:
    """Blank or whitespace-only default targets mean 'leave unmatched files'."""
    if default_target is None:
        return None
    stripped = default_target.strip()
    return stripped or None


def rows_to_data(rows: Sequence[Row], default_target: Optional[str]) -> dict:
    """Build the rules JSON structure from GUI rows."""
    return {
        "default_target": _normalize_default(default_target),
        "rules": [
            {
                "type": row.get("type", ""),
                "patterns": [row.get("pattern", "").strip()],
                "target": row.get("target", "").strip(),
            }
            for row in rows
        ],
    }


def validate_rows(
    rows: Sequence[Row], default_target: Optional[str]
) -> Tuple[List[Rule], Optional[str]]:
    """Validate rows and return compiled rules plus the normalized default."""
    return parse_rules(rows_to_data(rows, default_target))


def data_to_rows(data: object) -> Tuple[List[Row], Optional[str]]:
    """Convert a loaded rules JSON structure into (rows, default_target)."""
    if isinstance(data, dict) and data.get("rules") == []:
        default = data.get("default_target")
        if default is not None:
            default = validate_target(default)
        return [], default
    _, default = parse_rules(data)
    rows: List[Row] = []
    for rule in data["rules"]:  # type: ignore[index]
        target = validate_target(rule["target"])
        for pattern in rule["patterns"]:
            rows.append({"type": rule["type"], "pattern": pattern, "target": target})
    return rows, default


def load_rows(path: Path) -> Tuple[List[Row], Optional[str]]:
    """Load rows from a rules file; a missing file yields the default rules."""
    if not Path(path).exists():
        return data_to_rows(DEFAULT_RULES)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise RuleError("rules file is not valid JSON: %s" % exc)
    except OSError as exc:
        raise RuleError("cannot read rules file: %s" % exc)
    return data_to_rows(data)


def save_rows(path: Path, rows: Sequence[Row], default_target: Optional[str]) -> None:
    """Validate and atomically write rows to the rules file.

    An empty rule list is allowed here so the user can clear all rules.
    """
    default = _normalize_default(default_target)
    if rows:
        validate_rows(rows, default)
    elif default is not None:
        validate_target(default)
    data = rows_to_data(rows, default)

    path = Path(path)
    handle_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
