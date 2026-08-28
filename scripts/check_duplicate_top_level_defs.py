#!/usr/bin/env python3
"""Fail when a backend module shadows a top-level function or class definition."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for path in sorted((root / "backend").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: dict[str, list[int]] = defaultdict(list)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name].append(node.lineno)
        for name, lines in names.items():
            if len(lines) > 1:
                failures.append(
                    f"{path.relative_to(root)}: duplicate top-level {name} at lines "
                    + ", ".join(str(line) for line in lines)
                )
    if failures:
        print("\n".join(failures))
        return 1
    print("No duplicate top-level backend definitions found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
