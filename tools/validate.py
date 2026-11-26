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

import coremeta  # noqa: E402

SCHEMA_FILES = {
    "fact": "data/schemas/fact.schema.json",
    "event": "data/schemas/event.schema.json",
    "unit": "data/schemas/unit.schema.json",
    "source": "data/schemas/source.schema.json",
    "registry": "data/schemas/registry.schema.json",
    "place": "data/schemas/place.schema.json",
    "document": "data/schemas/document.schema.json",
    "person": "data/schemas/person.schema.json",
    "roster": "data/schemas/roster.schema.json",
    "frontline": "data/schemas/frontline.schema.json",
}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Dataset:
    """Загруженные данные проекта."""

    def __init__(self, root: str, vault: str | None, deep: bool = False):
        self.root = os.path.abspath(root)
        self.deep = deep
        self.vault = vault or os.environ.get("VOV_VAULT", os.path.expanduser("~/Documents/Obsidian Vault"))
        self.registry_types = _load(os.path.join(self.root, "data/registry.json"))
        self.sources = _load(os.path.join(self.root, "data/sources.json"))
        self.units = _load(os.path.join(self.root, "data/units.json"))
        self.places = _load(os.path.join(self.root, "data/places.json"))
        self.documents = _load(os.path.join(self.root, "data/documents.json"))
        self.people = _load(os.path.join(self.root, "data/people.json"))
        self.rosters = _load(os.path.join(self.root, "data/rosters.json"))
        self.front_lines = _load(os.path.join(self.root, "data/front_lines/1941.json"))
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
        ("data/documents.json", ds.documents, "document"),
        ("data/people.json", ds.people, "person"),
        ("data/rosters.json", ds.rosters, "roster"),
        ("data/front_lines/1941.json", ds.front_lines, "frontline"),
    ]
    checks += [(path, payload, "event") for path, payload in ds.events]
    for path, payload, schema_name in checks:
        validator = jsonschema.Draft202012Validator(ds.schemas[schema_name], registry=reg)
        for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
            location = "/".join(str(p) for p in err.absolute_path) or "<корень>"
            errors.append(f"{path}:{location}: схема {schema_name}: {err.message}")


def check_sources(ds: Dataset, errors: list[str]) -> None:
    seen: set[str] = set()
    files_index: dict[str, list[str]] = {}
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

        if src.get("kind") == "archive-portal":
            cache_dir = os.path.join(ds.root, src.get("cache_path", ""))
            if not os.path.isdir(cache_dir):
                errors.append(f"{sid}: каталог выгрузки {src.get('cache_path', '')} не найден")
            elif not os.path.exists(os.path.join(cache_dir, "catalogue.json")):
                errors.append(f"{sid}: в каталоге выгрузки нет catalogue.json")
            if not src.get("portal"):
                errors.append(f"{sid}: у портального источника нет адреса портала")
            if not src.get("fetched_at"):
                errors.append(f"{sid}: у портального источника нет даты выгрузки")
            if not src.get("query"):
                errors.append(f"{sid}: не записан запрос отбора — происхождение выборки не восстановить")
            continue

        if src.get("kind") in {"gazetteer", "historical-map"}:
            cache_dir = os.path.join(ds.root, src["cache_path"])
            if not os.path.isdir(cache_dir):
                errors.append(f"{sid}: каталог кэша {src['cache_path']} не найден")
            continue

        files = src.get("files") or []
        seen_paths: list[str] = []
        for index, item in enumerate(files):
            rel = item.get("path", "")
            spot = f"{sid}.files[{index}] ({rel})"
            if not rel:
                errors.append(f"{spot}: пустой путь файла")
                continue
            if rel in seen_paths:
                errors.append(f"{spot}: этот файл уже указан в источнике — дубль пути")
                continue
            seen_paths.append(rel)
            role = item.get("role")
            if role is not None and role not in ds.registry_types.get("file_roles", {}):
                known_roles = ", ".join(sorted(ds.registry_types.get("file_roles", {})))
                errors.append(f"{spot}: неизвестная роль файла '{role}' (допустимо: {known_roles})")
            original = os.path.join(ds.vault, rel)
            if not os.path.exists(original):
                errors.append(f"{sid}: файл {rel} не найден в Obsidian — источник неполон")
                continue
            digest = hashlib.sha256()
            with open(original, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item.get("sha256"):
                errors.append(
                    f"{spot}: sha256 файла не совпадает с записанным "
                    "(файл заменён или переименован — завести новую версию источника)"
                )
            owners = files_index.setdefault(rel, [])
            if sid not in owners:
                owners.append(sid)
        if not files and src.get("kind") not in {"gazetteer", "historical-map", "archive-portal", "website"}:
            errors.append(f"{sid}: у источника нет файлов")

    for rel, owners in sorted(files_index.items()):
        if len(owners) > 1:
            WARNINGS.append(
                f"файл {rel} указан в нескольких источниках ({', '.join(owners)}) — "
                "проверить, не дубль ли это"
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


_PORTAL_CACHE: dict[str, dict] = {}


def _portal_catalogue(ds: Dataset, source: dict) -> dict:
    """Индекс «путь файла в хранилище» → запись каталога выгрузки."""
    sid = source["id"]
    if sid in _PORTAL_CACHE:
        return _PORTAL_CACHE[sid]
    prefix = source.get("vault_prefix", "Хобби/365сд/Исходники/ЦАМО — Память народа/")
    path = os.path.join(ds.root, source.get("cache_path", ""), "catalogue.json")
    index: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        for entry in payload.get("files") or []:
            index[prefix + entry["file"]] = entry
    _PORTAL_CACHE[sid] = index
    return index


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_name(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


WARNINGS: list[str] = []


def _haversine_km(lon1, lat1, lon2, lat2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_to_segment_km(pt, a, b):
    """Расстояние от точки до отрезка в километрах (плоская аппроксимация на малой площади)."""
    import math
    lon0, lat0 = pt
    k = math.cos(math.radians(lat0)) * 111.32
    ax, ay = (a[0] - lon0) * k, (a[1] - lat0) * 110.57
    bx, by = (b[0] - lon0) * k, (b[1] - lat0) * 110.57
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def check_front_lines(ds: Dataset, errors: list[str]) -> None:
    known_sources = ds.source_ids()
    box = ds.registry_types.get("front_bbox") or {}
    levels = ds.registry_types.get("front_accuracy", {})
    seen: set[str] = set()
    for idx, line in enumerate(ds.front_lines.get("front_lines", [])):
        lid = line["id"]
        where = f"data/front_lines/1941.json:{idx} ({lid})"
        if lid in seen:
            errors.append(f"{where}: повторяющийся идентификатор линии")
        seen.add(lid)

        _check_fact(f"{where}.date", line["date"], ds, known_sources, errors)
        _check_period(f"{where}.date", line["date"], ds, errors)

        prov = line["provenance"]
        sid = prov["source_id"]
        source = known_sources.get(sid)
        if source is None:
            errors.append(f"{where}: основание — неизвестный источник '{sid}'")
        elif source.get("kind") not in {"historical-map", "map", "website", "archive-portal"}:
            errors.append(f"{where}: основание не картографическое — вид источника '{source.get('kind')}'")

        sheet_date = prov["locator"].get("sheet_date")
        line_date = (line["date"].get("value") or {}).get("from")
        if sheet_date != line_date:
            errors.append(
                f"{where}: дата линии {line_date} не совпадает с датой листа-основания {sheet_date}"
            )

        level = line["accuracy_level"]
        if level not in levels:
            errors.append(f"{where}: неизвестный уровень точности '{level}'")
        if level == "sheet-1m" and line["accuracy_km"] < 5:
            errors.append(
                f"{where}: лист 1:1 000 000 не даёт точности лучше ±5 км, указано {line['accuracy_km']}"
            )

        coords = line["geometry"]["coordinates"]
        if len(coords) < 2:
            errors.append(f"{where}: точек в линии {len(coords)} — это не линия")
        for i, pair in enumerate(coords):
            lon, lat = pair[0], pair[1]
            if not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
                errors.append(f"{where}: точка {i} вне допустимых координат ({lon}, {lat})")
            elif box and not (box["west"] <= lon <= box["east"] and box["south"] <= lat <= box["north"]):
                errors.append(f"{where}: точка {i} ({lat}, {lon}) за границами театра")
            if i and coords[i - 1] == coords[i]:
                errors.append(f"{where}: точки {i - 1} и {i} дублируются")
        for i in range(len(coords) - 1):
            for j in range(i + 2, len(coords) - 1):
                if _segments_cross(coords[i], coords[i + 1], coords[j], coords[j + 1]):
                    errors.append(f"{where}: линия самопересекается (отрезки {i} и {j})")


def check_front_line_against_events(ds: Dataset) -> None:
    """Предупреждения: событие на дату линии далеко от неё — повод разобраться."""
    place_by_id = {p["id"]: p for p in ds.places["places"]}
    for line in ds.front_lines.get("front_lines", []):
        line_date = (line["date"].get("value") or {}).get("from")
        if not line_date:
            continue
        coords = line["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        for _path, payload in ds.events:
            for ev in payload["events"]:
                v = ev["period"].get("value") or {}
                start, end = v.get("from"), v.get("to") or v.get("from")
                if not start or start > line_date or (end and end < line_date):
                    continue
                place = place_by_id.get(ev.get("place") or "")
                coords_value = (place or {}).get("coords") or {}
                if not coords_value.get("value"):
                    continue
                pt = (coords_value["value"]["lon"], coords_value["value"]["lat"])
                dist = min(_point_to_segment_km(pt, coords[i], coords[i + 1]) for i in range(len(coords) - 1))
                if dist > 25:
                    WARNINGS.append(
                        f"{line['id']}: событие {ev['id']} на ту же дату в {dist:.0f} км от линии "
                        f"(место {place['id']}) — проверить оцифровку или дату события"
                    )


def check_documents(ds: Dataset, errors: list[str]) -> None:
    known_sources = ds.source_ids()
    seen: set[str] = set()
    for idx, doc in enumerate(ds.documents["documents"]):
        did = doc["id"]
        where = f"data/documents.json:documents[{idx}] ({did})"
        if did in seen:
            errors.append(f"{where}: повторяющийся id документа")
        seen.add(did)

        if doc["type"] not in ds.registry_types.get("document_types", {}):
            errors.append(f"{where}: неизвестный тип документа '{doc['type']}'")

        _check_fact(f"{where}.title", doc["title"], ds, known_sources, errors)
        _check_fact(f"{where}.date", doc["date"], ds, known_sources, errors)
        _check_period(f"{where}.date", doc["date"], ds, errors)

        requisites = doc.get("requisites")
        if requisites is None:
            if not doc.get("note"):
                errors.append(
                    f"{where}: реквизиты не установлены, но нет примечания, объясняющего это"
                )
        else:
            archive = ("fund", "inventory", "file")
            publication = ("edition", "volume", "page")
            if any(f in requisites for f in archive):
                missing = [f for f in archive if not str(requisites.get(f, "")).strip()]
                if missing:
                    errors.append(
                        f"{where}: неполные архивные реквизиты — отсутствуют {', '.join(missing)}"
                    )
                if any(f in requisites for f in publication):
                    errors.append(f"{where}: реквизиты смешивают архивный и публикационный виды")
            elif any(f in requisites for f in publication):
                missing = [f for f in publication if not str(requisites.get(f, "")).strip()]
                if missing:
                    errors.append(
                        f"{where}: неполные публикационные реквизиты — отсутствуют {', '.join(missing)}"
                    )
            else:
                errors.append(f"{where}: реквизиты не архивные и не публикационные")

        carrier = doc["carrier"]
        sid = carrier["source_id"]
        if sid not in known_sources:
            errors.append(f"{where}: носитель ссылается на неизвестный источник '{sid}'")
            continue
        source = known_sources[sid]
        kind = source.get("kind")
        if kind == "archive-portal":
            portal_url = carrier.get("portal_url")
            if not portal_url:
                errors.append(f"{where}: у портального носителя нет ссылки на карточку портала")
            if not carrier.get("portal_id"):
                errors.append(f"{where}: у портального носителя нет идентификатора карточки")
            file_path = carrier.get("file_path")
            if not file_path:
                errors.append(f"{where}: у портального носителя нет пути к файлу листа")
                continue
            abs_path = os.path.join(ds.vault, file_path)
            if not os.path.exists(abs_path):
                errors.append(f"{where}: файл листа {file_path} не найден в хранилище")
                continue
            catalog = _portal_catalogue(ds, source)
            entry = catalog.get(file_path)
            size = os.path.getsize(abs_path)
            if entry is not None:
                if carrier.get("bytes") is not None and carrier["bytes"] != entry.get("bytes"):
                    errors.append(
                        f"{where}: размер файла {size} не совпадает с каталогом ({entry.get('bytes')})"
                    )
            elif carrier.get("bytes") is not None and carrier["bytes"] != size:
                errors.append(f"{where}: размер файла {size} не совпадает с записанным ({carrier['bytes']})")
            if ds.deep:
                digest = _sha256_file(abs_path)
                expected = (entry or {}).get("sha256") or carrier.get("sha256")
                if expected and digest != expected:
                    errors.append(f"{where}: sha256 файла {file_path} не совпадает с каталогом")
            continue

        if kind == "website":
            url = carrier.get("url")
            if not url:
                errors.append(f"{where}: у веб-носителя нет url страницы")
            elif url not in ds.snapshot_pages(sid):
                errors.append(f"{where}: url носителя '{url}' не найден в снимке источника '{sid}'")
        else:
            path = carrier.get("derivative_path")
            if not path:
                errors.append(f"{where}: у носителя нет ни url, ни пути к производной")
            elif not os.path.exists(os.path.join(ds.root, path)):
                errors.append(f"{where}: производная носителя {path} не найдена")


def check_people(ds: Dataset, errors: list[str]) -> None:
    seen_ids: set[str] = set()
    by_key: dict[tuple[str, object], str] = {}
    for idx, person in enumerate(ds.people["people"]):
        pid = person["id"]
        where = f"data/people.json:people[{idx}] ({pid})"
        if pid in seen_ids:
            errors.append(f"{where}: повторяющийся id персоны")
        seen_ids.add(pid)
        key = (_norm_name(person["name"]), person["birth_year"])
        if key in by_key:
            errors.append(
                f"{where}: возможный дубль персоны — совпадают ФИО и год рождения с '{by_key[key]}'"
            )
        else:
            by_key[key] = pid


def _claim_values(claim: dict) -> set[str]:
    variants = claim.get("variants") if claim.get("conflict") is True else [claim]
    return {
        json.dumps(v.get("value"), sort_keys=True, ensure_ascii=False)
        for v in variants
        if isinstance(v, dict)
    }


def _claim_bases(claim: dict) -> list[object]:
    variants = claim.get("variants") if claim.get("conflict") is True else [claim]
    return [v.get("basis") for v in variants if isinstance(v, dict)]


def _is_period_claim(claim: dict) -> bool:
    value = claim.get("value")
    return isinstance(value, dict) and "precision" in value


def check_person_claims(ds: Dataset, errors: list[str]) -> None:
    known_sources = ds.source_ids()
    keys = ds.registry_types.get("person_fact_keys", {})
    bases = ds.registry_types.get("evidence_basis", {})
    for person in ds.people["people"]:
        pid = person["id"]
        for key, claims in (person.get("claims") or {}).items():
            where = f"data/people.json ({pid}).claims.{key}"
            if key not in keys:
                errors.append(f"{where}: неизвестное имя утверждения '{key}'")
            if not isinstance(claims, list) or not claims:
                errors.append(f"{where}: утверждение должно быть непустым массивом")
                continue
            for index, claim in enumerate(claims):
                spot = f"{where}[{index}]"
                _check_fact(spot, claim, ds, known_sources, errors)
                if _is_period_claim(claim):
                    _check_period(spot, claim, ds, errors)
                claim_bases = _claim_bases(claim)
                if not claim_bases or any(not b for b in claim_bases):
                    errors.append(f"{spot}: утверждение без вида свидетельства (basis)")
                for basis in claim_bases:
                    if basis and basis not in bases:
                        errors.append(f"{spot}: неизвестный вид свидетельства '{basis}'")


SEQUENTIAL_CLAIM_KEYS = {"correspondence"}


def check_person_conflicts(ds: Dataset, errors: list[str]) -> None:
    entries_by_person: dict[str, list[dict]] = {}
    for entry in ds.rosters["entries"]:
        entries_by_person.setdefault(entry["person"], []).append(entry)

    for person in ds.people["people"]:
        pid = person["id"]
        entries = entries_by_person.get(pid, [])
        claims = person.get("claims") or {}
        where = f"data/people.json ({pid})"

        if not entries and not claims:
            errors.append(f"{where}: персона пуста — ни записей списка, ни утверждений")

        divergence: list[str] = []
        values = {str(e["fate"].get("value")) for e in entries}
        dates = {
            json.dumps(e.get("fate_date", {}).get("value"), sort_keys=True, ensure_ascii=False)
            for e in entries
            if e.get("fate_date")
        }
        if len(entries) > 1 and (len(values) > 1 or len(dates) > 1):
            divergence.append("записи списков")

        for key, claim_list in claims.items():
            if key in SEQUENTIAL_CLAIM_KEYS:
                continue
            seen: set[str] = set()
            for claim in claim_list:
                seen |= _claim_values(claim)
            if key == "birth_year" and person["birth_year"] != "неизвестен":
                seen.add(json.dumps(person["birth_year"]))
            if len(seen) > 1:
                divergence.append(f"утверждение '{key}'")

        flag = person.get("conflict", False)
        if divergence and not flag:
            errors.append(
                f"{where}: расхождение ({'; '.join(divergence)}), но персона не помечена conflict"
            )
        if flag and not divergence:
            errors.append(f"{where}: флаг conflict без расхождений")


def check_rosters(ds: Dataset, errors: list[str]) -> None:
    known_sources = ds.source_ids()
    doc_ids = {d["id"] for d in ds.documents["documents"]}
    person_ids = {p["id"] for p in ds.people["people"]}
    unit_ids = ds.unit_ids()
    fates = ds.registry_types.get("fate_outcomes", {})
    seen: set[str] = set()
    by_person: dict[str, list[dict]] = {}

    for idx, entry in enumerate(ds.rosters["entries"]):
        rid = entry["id"]
        where = f"data/rosters.json:entries[{idx}] ({rid})"
        if rid in seen:
            errors.append(f"{where}: повторяющийся id записи")
        seen.add(rid)

        if entry["document"] not in doc_ids:
            errors.append(f"{where}: неизвестный документ '{entry['document']}'")
        if entry["person"] not in person_ids:
            errors.append(f"{where}: неизвестная персона '{entry['person']}'")
        if entry["unit"] not in unit_ids:
            errors.append(f"{where}: неизвестное формирование '{entry['unit']}'")

        _check_fact(f"{where}.fate", entry["fate"], ds, known_sources, errors)
        value = (entry["fate"].get("value") or "") if isinstance(entry["fate"], dict) else ""
        if isinstance(value, str) and value not in fates:
            errors.append(f"{where}: неизвестная формулировка судьбы '{value}'")

        fate_date = entry.get("fate_date")
        if fate_date:
            _check_fact(f"{where}.fate_date", fate_date, ds, known_sources, errors)
            _check_period(f"{where}.fate_date", fate_date, ds, errors)

        for name, fact in (entry.get("facts") or {}).items():
            _check_fact(f"{where}.facts.{name}", fact, ds, known_sources, errors)

        by_person.setdefault(entry["person"], []).append(entry)




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

        doc_ids = {d["id"] for d in ds.documents["documents"]}
        for doc_id in event.get("documents") or []:
            if doc_id not in doc_ids:
                errors.append(f"{where}: ссылка на несуществующий документ '{doc_id}'")

        for key, fact in _iter_facts(event):
            _check_fact(f"{where}.{key}", fact, ds, known_sources, errors)
        _check_period(f"{where}.period", event["period"], ds, errors)


def run(root: str, vault: str | None = None, deep: bool = False) -> list[str]:
    """Полный прогон проверок. Возвращает список нарушений."""
    errors: list[str] = []
    _PORTAL_CACHE.clear()
    try:
        ds = Dataset(root, vault, deep=deep)
    except FileNotFoundError as exc:
        return [f"не найден обязательный файл: {exc.filename}"]
    WARNINGS.clear()
    check_schemas(ds, errors)
    check_sources(ds, errors)
    check_units(ds, errors)
    check_places(ds, errors)
    check_documents(ds, errors)
    check_people(ds, errors)
    check_person_claims(ds, errors)
    check_person_conflicts(ds, errors)
    check_rosters(ds, errors)
    check_events(ds, errors)
    check_front_lines(ds, errors)
    check_front_line_against_events(ds)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--vault", default=None)
    parser.add_argument("--quiet", action="store_true", help="печатать только итог")
    parser.add_argument("--deep", action="store_true", help="сверять sha256 файлов листов с каталогом")
    args = parser.parse_args(argv)

    try:
        core = coremeta.version(os.path.join(args.root, "tools", "VERSION"))
    except coremeta.CoreVersionError as exc:
        print(f"ОШИБКА  {exc}")
        return 1
    if not args.quiet:
        print(f"ядро {core}")
    errors = sorted(run(args.root, args.vault, deep=args.deep))
    if errors:
        if not args.quiet:
            for err in errors:
                print(f"ОШИБКА  {err}")
        print(f"Проверка не пройдена: {len(errors)} нарушений")
        return 1
    if not args.quiet:
        for warning in WARNINGS:
            print(f"ПРЕДУПРЕЖДЕНИЕ  {warning}")
        print("Проверка пройдена: нарушений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
