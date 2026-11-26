#!/usr/bin/env python3
"""Версия ядра проекта: единственный источник — tools/VERSION.

Версия нужна, чтобы артефакт можно было связать с инструментом, которым он
собран: валидатор её печатает, сборка вписывает в заголовки сгенерированных
файлов, `build.py --check` сравнивает и ругается на артефакт, собранный другой
версией.
"""
from __future__ import annotations

import os
import pathlib
import re

VERSION_FILE = pathlib.Path(__file__).resolve().parent / "VERSION"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class CoreVersionError(RuntimeError):
    pass


def version(path: pathlib.Path | str | None = None) -> str:
    """Строка версии из tools/VERSION; ошибка, если файла нет или формат не semver."""
    target = pathlib.Path(path) if path is not None else VERSION_FILE
    if not target.exists():
        raise CoreVersionError(f"нет файла версии ядра: {target}")
    raw = target.read_text(encoding="utf-8").strip()
    if not SEMVER.match(raw):
        raise CoreVersionError(
            f"версия ядра в {target.name} должна быть в формате МАЖОР.МИНОР.ПАТЧ, получено: {raw!r}"
        )
    return raw


def version_or_default(default: str = "0.0.0", path: pathlib.Path | str | None = None) -> str:
    """Мягкий вариант: не падает, если версии нет (нужен для проверки самого наличия файла)."""
    try:
        return version(path)
    except CoreVersionError:
        return default


def stamp(comment: str = "<!--") -> str:
    """Строка версии для заголовка артефакта: `<!-- ядро: 1.0.0 -->`."""
    value = version()
    return f"{comment} ядро: {value} -->" if comment == "<!--" else f"ядро {value}"


def changelog_has(value: str, changelog: pathlib.Path | str | None = None) -> bool:
    """Есть ли в CHANGELOG запись для этой версии."""
    target = pathlib.Path(changelog) if changelog is not None else VERSION_FILE.parent / "CHANGELOG.md"
    if not target.exists():
        return False
    text = target.read_text(encoding="utf-8")
    return re.search(rf"^##\s+{re.escape(value)}\b", text, re.M) is not None


if __name__ == "__main__":
    print(version())
    if not changelog_has(version()):
        print(f"внимание: в CHANGELOG.md нет записи для {os.path.basename(str(version()))}")
