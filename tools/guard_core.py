#!/usr/bin/env python3
"""Защита правила «ядро — в ветке, версия — вместе со слиянием, данные — в мастер».

  python3 tools/guard_core.py --staged               # pre-commit: по индексу git
  python3 tools/guard_core.py --merge-base master    # перед слиянием ветки ядра

Исходы:
  0 — можно: либо это релизный коммит, либо меняются только данные;
  1 — отказ: меняется ядро, а tools/VERSION не менялся.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

CORE_SUFFIXES = (".py", ".html")
VERSION_PATH = "tools/VERSION"
CHANGELOG_PATH = "tools/CHANGELOG.md"


def git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()[:200]}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def classify(files: list[str]) -> dict:
    """Разложить изменённые файлы по группам: ядро, версия, данные."""
    core = [f for f in files if f.startswith("tools/") and f.endswith(CORE_SUFFIXES)]
    version_changed = VERSION_PATH in files
    changelog_changed = CHANGELOG_PATH in files
    content = [f for f in files if f.startswith(("data/", "docs/", "openspec/"))]
    return {"core": core, "version": version_changed, "changelog": changelog_changed, "content": content,
            "all": files}


def decide(files: list[str]) -> tuple[int, str]:
    c = classify(files)
    if not files:
        return 0, "изменений нет"
    if c["core"] and not c["version"]:
        names = ", ".join(c["core"][:5]) + ("…" if len(c["core"]) > 5 else "")
        return 1, (f"меняется ядро ({names}), а tools/VERSION не изменён.\n"
                   "  Правило: изменение инструмента коммитится вместе со сменой версии.\n"
                   "  Сделай: python3 tools/release_core.py --bump patch")
    if c["core"] and c["version"]:
        if not c["changelog"]:
            return 0, "ядро и версия меняются, записи в CHANGELOG нет — допустимо для первого релиза"
        return 0, "релизный коммит: ядро + версия + CHANGELOG"
    if c["version"]:
        return 0, "коммит версии без изменений ядра"
    if c["content"]:
        return 0, "контент: коммитится в мастер напрямую"
    return 0, "служебные файлы"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="файлы в индексе git")
    group.add_argument("--merge-base", metavar="BRANCH",
                       help="изменения ветки относительно указанной базы, например master")
    group.add_argument("--files", nargs="*", help="проверить произвольный список файлов (для тестов)")
    args = ap.parse_args(argv)

    if args.staged:
        files = git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
        branch = git_lines(["rev-parse", "--abbrev-ref", "HEAD"])[0]
    elif args.merge_base:
        files = git_lines(["diff", "--name-only", "--diff-filter=ACMR", f"{args.merge_base}...HEAD"])
        branch = git_lines(["rev-parse", "--abbrev-ref", "HEAD"])[0]
    else:
        files = list(args.files or [])
        branch = "(список файлов)"

    code, message = decide(files)
    if code != 0:
        print(f"ОТКАЗ ({branch}): {message}", file=sys.stderr)
        return code
    print(f"проверка пройдена ({branch}): {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
