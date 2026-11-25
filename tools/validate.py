#!/usr/bin/env python3
"""Проверка набора данных боевого пути: JSON-схемы + семантика провенанса.

Код возврата: 0 — нарушений нет, 1 — есть нарушения (сборка блокируется),
2 — не найдены обязательные файлы.

Корень проекта задаётся --root, корень Obsidian — --vault (по умолчанию
переменная окружения VOV_VAULT).
"""
from __future__ import annotations

import argparse
import calendar
import glob
import hashlib
import json
import os
import re
import sys
from datetime import date

import jsonschema
from referencing import Registry, Resource

SCHEMA_FILES = {
    "fact": "data/schemas/fact.schema.json",
    "event": "data/schemas/event.schema.json",
    "unit": "data/schemas/unit.schema.json",
    "source": "data/schemas/source.schema.json",
    "registry": "data/schemas/registry.schema.json",
}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Dataset:
    """Загруженные данные проекта."""

    def __init__(self, root: str, vault: str | None):
        self.root = os.path.abspath(root)
        self.vault = vault or os.environ.get("VOV_VAULT", "/home/anb/Documents/Obsidian Vault")
        self.registry_types = _load(os.path.join(self.root, "data/registry.json"))
        self.sources = _load(os.path.join(self.root, "data/sources.json"))
        self.units = _load(os.path.join(self.root, "data/units.json"))
        self.events = [
            (path, _load(os.path.join(self.root, path)))
            for path in sorted(
                os.path.relpath(p, self.root)
                for p in glob.glob(os.path.join(self.root, "data/events/*.json"))
            )
        ]
        self.schemas = {
            name: _load(os.path.join(self.root, path)) for name, path in SCHEMA_FILES.items()
        }

    # --- полезные срезы -------------------------------------------------
    def source_ids(self) -> dict[str, dict]:
        return {s["id"]: s for s in self.sources["sources"]}

    def unit_ids(self) -> set[str]:
        return {u["id"] for u in self.units["units"]}

    def event_list(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for path, payload in self.events:
            for idx, ev in enumerate(payload.get("events", [])):
                out.append((f"{path}:events[{idx}]", ev))
        return out


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _schema_registry(ds: Dataset) -> Registry:
    resources = []
    for schema in ds.schemas.values():
        if "$id" in schema:
            resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _iter_facts(event: dict):
    """Все факты события как (путь, объект)."""
    for key in ("title", "period"):
        if key in event:
            yield key, event[key]
    for name, fact in (event.get("facts") or {}).items():
        yield f"facts.{name}", fact


def _source_ids_of(fact: dict) -> list[str]:
    prov = fact.get("provenance")
    if not prov:
        return []
    entries = prov if isinstance(prov, list) else [prov]
    return [e.get("source_id", "") for e in entries]


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _check_period_value(where: str, value: dict, ds: Dataset, errors: list[str]) -> None:
    precision = value.get("precision")
    if precision not in ds.registry_types["date_precision"]:
        errors.append(f"{where}: неизвестная точность даты '{precision}'")
        return
    start, end = value.get("from"), value.get("to")
    for label, raw in (("from", start), ("to", end)):
        if raw is not None and not ISO_DATE_RE.match(raw):
            errors.append(f"{where}.{label}: дата '{raw}' не в формате YYYY-MM-DD")
            return
    if start and end and _parse_date(start) > _parse_date(end):
        errors.append(f"{where}: начало интервала ({start}) позже конца ({end})")
    if precision == "day" and (start is None or end is None or start != end):
        errors.append(f"{where}: точность 'day' требует одинаковых from и to, получено {start}..{end}")
    if precision == "month":
        if start is None or end is None:
            errors.append(f"{where}: точность 'month' требует обеих границ, получено {start}..{end}")
        else:
            first, last = _parse_date(start), _parse_date(end)
            ok = first.day == 1 and (first.year, first.month) == (last.year, last.month)
            ok = ok and last.day == calendar.monthrange(last.year, last.month)[1]
            if not ok:
                errors.append(
                    f"{where}: точность 'month' требует интервала вида YYYY-MM-01..последний день месяца, "
                    f"получено {start}..{end}"
                )
    if precision == "year":
        if start is None or end is None:
            errors.append(f"{where}: точность 'year' требует обеих границ, получено {start}..{end}")
        else:
            ok = start.endswith("-01-01") and end.endswith("-12-31") and start[:4] == end[:4]
            if not ok:
                errors.append(
                    f"{where}: точность 'year' требует интервала YYYY-01-01..YYYY-12-31, получено {start}..{end}"
                )


def _check_period(where: str, fact: dict, ds: Dataset, errors: list[str]) -> None:
    if fact.get("conflict") is True:
        for i, variant in enumerate(fact.get("variants", [])):
            _check_period_value(f"{where}.variants[{i}].value", variant.get("value", {}), ds, errors)
    else:
        _check_period_value(f"{where}.value", fact.get("value", {}), ds, errors)


def _check_single(
    where: str, fact: dict, ds: Dataset, known_sources: dict, errors: list[str]
) -> None:
    ids = _source_ids_of(fact)
    if not ids:
        errors.append(f"{where}: факт без провенанса")
    for sid in ids:
        if sid and sid not in known_sources:
            errors.append(f"{where}: неизвестный источник '{sid}'")
    if fact.get("status") == "выверено" and len(set(ids)) < 2:
        errors.append(
            f"{where}: статус «выверено» требует двух независимых источников, указано {len(set(ids))}"
        )


def _check_fact(
    where: str, fact: dict, ds: Dataset, known_sources: dict, errors: list[str]
) -> None:
    if fact.get("conflict") is True:
        variants = fact.get("variants", [])
        if len(variants) < 2:
            errors.append(f"{where}: конфликт без двух вариантов")
        if "status" in fact:
            errors.append(f"{where}: у конфликта не должно быть статуса выверки")
        for i, variant in enumerate(variants):
            if variant.get("status") == "выверено":
                errors.append(f"{where}.variants[{i}]: вариант конфликта не может быть «выверено»")
            _check_single(f"{where}.variants[{i}]", variant, ds, known_sources, errors)
    else:
        _check_single(where, fact, ds, known_sources, errors)


def check_schemas(ds: Dataset, errors: list[str]) -> None:
    reg = _schema_registry(ds)
    checks = [
        ("data/registry.json", ds.registry_types, "registry"),
        ("data/sources.json", ds.sources, "source"),
        ("data/units.json", ds.units, "unit"),
    ]
    checks += [(path, payload, "event") for path, payload in ds.events]
    for path, payload, schema_name in checks:
        validator = jsonschema.Draft202012Validator(ds.schemas[schema_name], registry=reg)
        for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
            location = "/".join(str(p) for p in err.absolute_path) or "<корень>"
            errors.append(f"{path}:{location}: схема {schema_name}: {err.message}")


def check_sources(ds: Dataset, errors: list[str]) -> None:
    seen: set[str] = set()
    for src in ds.sources["sources"]:
        sid = src["id"]
        if sid in seen:
            errors.append(f"data/sources.json: повторяющийся id источника '{sid}'")
        seen.add(sid)

        derivative = os.path.join(ds.root, src["derivative_path"])
        if not os.path.exists(derivative):
            errors.append(f"{sid}: производная {src['derivative_path']} не найдена в репозитории")

        original = os.path.join(ds.vault, src["vault_path"])
        if not os.path.exists(original):
            errors.append(f"{sid}: исходник {src['vault_path']} не найден в Obsidian")
            continue
        digest = hashlib.sha256()
        with open(original, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != src["sha256"]:
            errors.append(
                f"{sid}: sha256 исходника не совпадает с записанным "
                "(файл заменён или переименован — завести новую версию источника)"
            )


def check_units(ds: Dataset, errors: list[str]) -> None:
    known = ds.unit_ids()
    known_sources = ds.source_ids()
    seen: set[str] = set()
    for idx, unit in enumerate(ds.units["units"]):
        uid = unit["id"]
        where = f"data/units.json:units[{idx}] ({uid})"
        if uid in seen:
            errors.append(f"{where}: повторяющийся id формирования")
        seen.add(uid)
        _check_fact(f"{where}.name", unit["name"], ds, known_sources, errors)
        parent = unit.get("parent")
        if parent is not None and parent not in known:
            errors.append(f"{where}: родительское формирование '{parent}' отсутствует")
        for j, sub in enumerate(unit.get("subordination", [])):
            if sub["unit_id"] not in known:
                errors.append(f"{where}.subordination[{j}]: неизвестное формирование '{sub['unit_id']}'")
            _check_fact(f"{where}.subordination[{j}].period", sub["period"], ds, known_sources, errors)
            _check_period(f"{where}.subordination[{j}].period", sub["period"], ds, errors)


def check_events(ds: Dataset, errors: list[str]) -> None:
    known_units = ds.unit_ids()
    known_sources = ds.source_ids()
    seen: dict[str, str] = {}
    for path, payload in ds.events:
        file_year = payload.get("year")
        match = re.search(r"(\d{4})\.json$", path)
        if match and int(match.group(1)) != file_year:
            errors.append(f"{path}: год в файле ({file_year}) не совпадает с именем файла")

    for where, event in ds.event_list():
        eid = event["id"]
        if eid in seen:
            errors.append(f"{where}: id события '{eid}' уже встречался в {seen[eid]}")
        seen[eid] = where

        if event["type"] not in ds.registry_types["event_types"]:
            errors.append(f"{where}: неизвестный тип события '{event['type']}'")
        if event["localization"] not in ds.registry_types["localization"]:
            errors.append(f"{where}: неизвестный уровень локализации '{event['localization']}'")

        geometry = event.get("geometry")
        if geometry is None and event["localization"] != "approximate":
            errors.append(
                f"{where}: геометрия не задана, но уровень локализации '{event['localization']}' "
                "(без геометрии допустимо только 'approximate')"
            )
        if geometry is not None:
            coords = geometry.get("coordinates")
            if not coords:
                errors.append(f"{where}: геометрия без координат")

        for uid in event["units"]:
            if uid not in known_units:
                errors.append(f"{where}: неизвестное формирование '{uid}'")

        for key, fact in _iter_facts(event):
            _check_fact(f"{where}.{key}", fact, ds, known_sources, errors)
        _check_period(f"{where}.period", event["period"], ds, errors)


def run(root: str, vault: str | None = None) -> list[str]:
    """Полный прогон проверок. Возвращает список нарушений."""
    errors: list[str] = []
    try:
        ds = Dataset(root, vault)
    except FileNotFoundError as exc:
        return [f"не найден обязательный файл: {exc.filename}"]
    check_schemas(ds, errors)
    check_sources(ds, errors)
    check_units(ds, errors)
    check_events(ds, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--vault", default=None)
    parser.add_argument("--quiet", action="store_true", help="печатать только итог")
    args = parser.parse_args(argv)

    errors = sorted(run(args.root, args.vault))
    if errors:
        if not args.quiet:
            for err in errors:
                print(f"ОШИБКА  {err}")
        print(f"Проверка не пройдена: {len(errors)} нарушений")
        return 1
    if not args.quiet:
        print("Проверка пройдена: нарушений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
