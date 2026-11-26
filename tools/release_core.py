#!/usr/bin/env python3
"""Релиз ядра: поднять версию, дописать запись в CHANGELOG, показать порядок слияния.

  python3 tools/release_core.py --bump patch        # 1.0.0 → 1.0.1
  python3 tools/release_core.py --bump minor --dry-run

Скрипт не коммитит: перечень изменений пишет человек, иначе CHANGELOG
превращается в список «изменения». Коммит версии и CHANGELOG делается вместе
со слиянием ветки ядра в мастер — это и есть правило проекта.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from coremeta import VERSION_FILE, CoreVersionError, version  # noqa: E402

CHANGELOG = VERSION_FILE.parent / "CHANGELOG.md"
TEMPLATE_HEADER = "# История версий ядра"


def bump(value: str, part: str) -> str:
    major, minor, patch = (int(x) for x in value.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"неизвестная часть версии: {part}")


def changelog_text(path: pathlib.Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (TEMPLATE_HEADER + "\n\nВерсия ядра — в `VERSION`. Запись добавляет `release_core.py --bump`, "
            "перечень изменений пишет человек по-русски.\n\n")


def insert_entry(text: str, new_version: str, today: str) -> str:
    entry = f"## {new_version} — {today}\n\n- (перечисли изменения инструмента: что и зачем)\n\n"
    if re.search(rf"^##\s+{re.escape(new_version)}\b", text, re.M):
        raise SystemExit(f"в CHANGELOG уже есть запись для {new_version}")
    # новые записи сверху, сразу после шапки файла (первый раздел «## »)
    match = re.search(r"^##\s+\d", text, re.M)
    if match:
        return text[:match.start()] + entry + text[match.start():]
    return text.rstrip("\n") + "\n\n" + entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bump", choices=("patch", "minor", "major"), required=True)
    ap.add_argument("--date", default=None, help="дата записи, по умолчанию сегодня")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    current = version()
    new_version = bump(current, args.bump)
    today = args.date or dt.date.today().isoformat()

    if args.dry_run:
        print(f"версия: {current} → {new_version} (dry-run, файлы не меняются)")
        return 0

    VERSION_FILE.write_text(new_version + "\n", encoding="utf-8")
    CHANGELOG.write_text(insert_entry(changelog_text(CHANGELOG), new_version, today), encoding="utf-8")

    branch = "(текущая ветка)"
    print(f"версия ядра: {current} → {new_version}")
    print(f"запись добавлена в {CHANGELOG.relative_to(VERSION_FILE.parent.parent)}")
    print()
    print("дальше:")
    print("  1. заполнить перечень изменений в CHANGELOG (что и зачем)")
    print(f"  2. git add tools/VERSION tools/CHANGELOG.md tools/*.py")
    print(f"  3. git commit -m \"ядро {new_version}: <кратко>\"")
    print(f"  4. git checkout master && git merge --no-ff {branch}")
    print("  5. python3 tools/build.py && python3 tools/build.py --check")
    print("  6. git push github master && git push gitverse master")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CoreVersionError as exc:
        raise SystemExit(str(exc))
