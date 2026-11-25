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
import urllib.parse
from datetime import date

import jsonschema
from referencing import Registry, Resource

SCHEMA_FILES = {
    "fact": "data/schemas/fact.schema.json",
    "event": "data/schemas/event.schema.json",
    "unit": "data/schemas/unit.schema.json",
    "source": "data/schemas/source.schema.json",
    "registry": "data/schemas/registry.schema.json",
    "place": "data/schemas/place.schema.json",
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
        self.places = _load(os.path.join(self.root, "data/places.json"))
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

    def place_ids(self) -> set[str]:
        return {p["id"] for p in self.places["places"]}

    def snapshot_pages(self, source_id: str) -> set[str]:
        """URL страниц снимка веб-источника (из pages.json)."""
        if not hasattr(self, "_snapshot_cache"):
            self._snapshot_cache = {}
        if source_id not in self._snapshot_cache:
            src = self.source_ids().get(source_id, {})
            pages: set[str] = set()
            if src.get("kind") == "website" and src.get("cache_path") and src.get("snapshot_at"):
                path = os.path.join(self.root, src["cache_path"], src["snapshot_at"], "pages.json")
                if os.path.exists(path):
                    pages = set(_load(path).get("pages", {}))
            self._snapshot_cache[source_id] = pages
        return self._snapshot_cache[source_id]

    def derived_graph(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {}
        for src in self.sources["sources"]:
            graph.setdefault(src["id"], set())
            for parent in src.get("derived_from") or []:
                graph[src["id"]].add(parent)
                graph.setdefault(parent, set()).add(src["id"])
        return graph

    def connected(self, a: str, b: str) -> bool:
        graph = self.derived_graph()
        seen, queue = {a}, [a]
        while queue:
            node = queue.pop()
            for nxt in graph.get(node, ()):
                if nxt == b:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return False

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
    prov = fact.get("provenance")
    entries = prov if isinstance(prov, list) else ([prov] if prov else [])
    ids = [e.get("source_id", "") for e in entries]
    if not ids:
        errors.append(f"{where}: факт без провенанса")

    for entry in entries:
        sid = entry.get("source_id", "")
        if sid and sid not in known_sources:
            errors.append(f"{where}: неизвестный источник '{sid}'")
            continue
        source = known_sources.get(sid, {})
        if source.get("kind") != "website":
            continue
        locator = entry.get("locator") or {}
        url = locator.get("url")
        if not url:
            errors.append(f"{where}: неполный провенанс веб-источника — нет поля url")
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("", "/") and not parsed.query:
            errors.append(f"{where}: url указывает на корень сайта, нужна конкретная страница")
            continue
        pages = ds.snapshot_pages(sid)
        if not pages:
            errors.append(f"{where}: снимок источника '{sid}' не найден (pages.json)")
        elif url not in pages:
            errors.append(f"{where}: url '{url}' не найден в снимке источника '{sid}' — обновите снимок")

    unique = set(ids)
    if fact.get("status") == "выверено":
        if len(unique) < 2:
            errors.append(
                f"{where}: статус «выверено» требует двух независимых источников, указано {len(unique)}"
            )
        else:
            ordered = sorted(unique)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    if ds.connected(a, b):
                        errors.append(
                            f"{where}: подтверждение одно — источник '{a}' связан связью derived_from с '{b}' (производная)"
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
        ("data/places.json", ds.places, "place"),
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

        for parent in src.get("derived_from") or []:
            if parent not in ds.source_ids():
                errors.append(f"{sid}: derived_from ссылается на неизвестный источник '{parent}'")

        if src.get("kind") == "website":
            cache_dir = os.path.join(ds.root, src.get("cache_path", ""))
            if not os.path.isdir(cache_dir):
                errors.append(f"{sid}: каталог снимков {src.get('cache_path', '')} не найден")
            snapshot = os.path.join(cache_dir, src.get("snapshot_at", ""), "pages.json")
            if not os.path.exists(snapshot):
                errors.append(
                    f"{sid}: снимок сайта не найден (ожидался {os.path.relpath(snapshot, ds.root)})"
                )

        if src.get("kind") in {"gazetteer", "historical-map"}:
            cache_dir = os.path.join(ds.root, src["cache_path"])
            if not os.path.isdir(cache_dir):
                errors.append(f"{sid}: каталог кэша {src['cache_path']} не найден")
            continue

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


def _journal_path(ds: Dataset) -> str | None:
    for src in ds.sources["sources"]:
        if src.get("kind") in {"gazetteer", "historical-map"}:
            return os.path.join(ds.root, src["derivative_path"])
    return None


def check_places(ds: Dataset, errors: list[str]) -> None:
    known_sources = ds.source_ids()
    bbox = ds.registry_types.get("bbox")
    journal_path = _journal_path(ds)
    journal_text = ""
    if journal_path and os.path.exists(journal_path):
        with open(journal_path, encoding="utf-8") as fh:
            journal_text = fh.read()

    seen: set[str] = set()
    for idx, place in enumerate(ds.places["places"]):
        pid = place["id"]
        where = f"data/places.json:places[{idx}] ({pid})"
        if pid in seen:
            errors.append(f"{where}: повторяющийся id места")
        seen.add(pid)

        coords = place.get("coords")
        entries: list[tuple[str, dict]] = []
        if isinstance(coords, dict) and coords.get("value") is not None:
            entries.append((f"{where}.coords", coords))
        elif isinstance(coords, dict) and coords.get("conflict") is True:
            variants = coords.get("variants", [])
            if len(variants) < 2:
                errors.append(f"{where}.coords: конфликт координат без двух вариантов")
            for i, variant in enumerate(variants):
                entries.append((f"{where}.coords.variants[{i}]", variant))

        if place["state"] in {"found", "tract"} and not entries:
            errors.append(f"{where}: состояние «{place['state']}», но координат нет")
        if place["state"] == "unfound" and entries:
            errors.append(f"{where}: состояние «unfound», но координаты заданы")

        for path, entry in entries:
            _check_single(path, entry, ds, known_sources, errors)
            value = entry.get("value") or {}
            lon, lat = value.get("lon"), value.get("lat")
            if lon is None or lat is None:
                errors.append(f"{path}: координата без lon/lat")
                continue
            if not (-180 <= lon <= 180):
                errors.append(f"{path}: долгота {lon} вне диапазона -180..180")
            if not (-90 <= lat <= 90):
                errors.append(f"{path}: широта {lat} вне диапазона -90..90")
            if bbox and not (
                bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]
            ):
                errors.append(
                    f"{path}: координата ({lat}, {lon}) вне ожидаемого района проекта "
                    "— вероятно, перепутаны широта и долгота или найден другой объект"
                )
            prov = entry.get("provenance") or {}
            prov_entries = prov if isinstance(prov, list) else [prov]
            for prov_entry in prov_entries:
                locator = prov_entry.get("locator") or {}
                paragraph = locator.get("paragraph", "")
                source = known_sources.get(prov_entry.get("source_id", ""), {})
                if source.get("kind") in {"gazetteer", "historical-map"}:
                    if not paragraph.startswith("geo-"):
                        errors.append(f"{path}: провенанс геоданных должен ссылаться на запись журнала geo-…")
                        continue
                    if f"## {paragraph} " not in journal_text:
                        errors.append(f"{path}: в журнале геокодирования нет записи «{paragraph}»")
                        continue
                    cache_rel = f"{source.get('cache_path', 'data/cache/geocode')}/{paragraph.removeprefix('geo-')}.json"
                    if not os.path.exists(os.path.join(ds.root, cache_rel)):
                        errors.append(f"{path}: сырой ответ кэша отсутствует: {cache_rel}")


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

        place_id = event.get("place")
        if place_id is not None and place_id not in ds.place_ids():
            errors.append(f"{where}: ссылка на несуществующее место '{place_id}'")

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
    check_places(ds, errors)
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
