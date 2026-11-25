#!/usr/bin/env python3
"""Сборка набора данных боевого пути: проверка, хронология, GeoJSON.

Сборка блокируется при любом нарушении провенанса или схемы (см. tools/validate.py).
Артефакты:
  data/dist/features.geojson                                  — FeatureCollection для карты
  docs/research/Боевой путь — хронология (сгенерировано).md    — хронология для человека

Код возврата: 0 — успех, 1 — нарушения или расхождение с --check, 2 — нет файлов.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import validate as vov  # noqa: E402

CHRONO_PATH = "docs/research/Боевой путь — хронология (сгенерировано).md"
GEOJSON_PATH = "data/dist/features.geojson"
FAR_FUTURE = "9999-12-31"


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _fact_status(fact: dict) -> str:
    if fact.get("conflict") is True:
        return f"конфликт ({len(fact.get('variants', []))} варианта)"
    return fact.get("status", "?")


def _period_bounds(fact: dict) -> tuple[str, str]:
    """Возвращает (начало, конец) для сортировки, беря минимум по вариантам."""
    if fact.get("conflict") is True:
        starts = [v.get("value", {}).get("from") for v in fact.get("variants", [])]
        ends = [v.get("value", {}).get("to") for v in fact.get("variants", [])]
    else:
        value = fact.get("value", {})
        starts, ends = [value.get("from")], [value.get("to")]
    starts = [s for s in starts if s]
    ends = [e for e in ends if e]
    return (min(starts) if starts else FAR_FUTURE, max(ends) if ends else "")


def _period_label(fact: dict) -> str:
    if fact.get("conflict") is True:
        return "конфликт дат: " + " / ".join(
            _period_label_single(v.get("value", {})) for v in fact.get("variants", [])
        )
    return _period_label_single(fact.get("value", {}))


def _period_label_single(value: dict) -> str:
    start, end, precision = value.get("from"), value.get("to"), value.get("precision")
    if start is None and end is None:
        return "дата не установлена"
    if start == end:
        return f"{start} ({precision})"
    return f"{start or '…'} — {end or '…'} ({precision})"


def _value_label(fact: dict) -> str:
    if fact.get("conflict") is True:
        return "; ".join(str(v.get("value")) for v in fact.get("variants", []))
    return str(fact.get("value"))


def _source_ids(fact: dict) -> set[str]:
    if fact.get("conflict") is True:
        ids: set[str] = set()
        for variant in fact.get("variants", []):
            ids |= set(vov._source_ids_of(variant))
        return ids
    return set(vov._source_ids_of(fact))


def dataset_hash(root: str) -> str:
    digest = hashlib.sha256()
    paths = ["data/registry.json", "data/sources.json", "data/units.json"]
    paths += sorted(
        os.path.relpath(p, root)
        for p in __import__("glob").glob(os.path.join(root, "data/events/*.json"))
    )
    for rel in paths:
        digest.update(rel.encode("utf-8"))
        with open(os.path.join(root, rel), "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()


def collect_events(ds: vov.Dataset) -> list[dict]:
    events = [event for _, event in ds.event_list()]
    events.sort(key=lambda ev: (_period_bounds(ev["period"])[0], ev["id"]))
    return events


def _locator_label(entry: dict) -> str:
    loc = entry.get("locator") or {}
    if "page" in loc:
        where = f"с. {loc['page']}"
    elif "slide" in loc:
        where = f"слайд {loc['slide']}"
    elif "paragraph" in loc:
        where = f"абзац «{loc['paragraph']}»"
    elif "quote" in loc:
        where = f"цитата «{loc['quote'][:40]}…»"
    else:
        where = "локатор не указан"
    return f"{entry.get('source_id', '?')} ({where})"


def _locators(fact: dict) -> list[str]:
    if fact.get("conflict") is True:
        out: list[str] = []
        for variant in fact.get("variants", []):
            out.extend(_locators(variant))
        return out
    prov = fact.get("provenance") or {}
    entries = prov if isinstance(prov, list) else [prov]
    return [_locator_label(e) for e in entries]


def render_chronology(ds: vov.Dataset, events: list[dict], digest: str) -> str:
    lines = [
        "<!-- СГЕНЕРИРОВАНО tools/build.py. Не редактировать вручную: изменения будут перезаписаны. -->",
        "",
        "# Боевой путь 365-й стрелковой дивизии — хронология (сгенерировано)",
        "",
        f"Событий: {len(events)}. Формирований: {len(ds.units['units'])}. "
        f"Источников: {len(ds.sources['sources'])}. Хеш набора данных: `{digest}`",
        "",
        "Ручной разбор и оценка противоречий — в файле "
        "`docs/research/Бизяев ЕМ — сводка и расхождения.md`.",
        "",
        "## Хронология",
        "",
    ]
    for ev in events:
        lines.append(f"### {_period_label(ev['period'])} — {ev['type']}")
        lines.append("")
        lines.append(f"**{_value_label(ev['title'])}**")
        lines.append("")
        lines.append(f"- Части: {', '.join(ev['units'])}")
        lines.append(f"- Статус: {_fact_status(ev['period'])}")
        for loc in _locators(ev["period"]):
            lines.append(f"- Период: {loc}")
        for loc in _locators(ev["title"]):
            lines.append(f"- Название: {loc}")
        for key, fact in (ev.get("facts") or {}).items():
            lines.append(f"- {key}: {_value_label(fact)} — статус {_fact_status(fact)}")
            for loc in _locators(fact):
                lines.append(f"  - источник: {loc}")
        if ev.get("note"):
            lines.append(f"- Примечание: {ev['note']}")
        lines.append("")

    conflicts: list[str] = []
    for ev in events:
        pairs = [("период", ev["period"]), ("название", ev["title"])]
        pairs += list((ev.get("facts") or {}).items())
        for key, fact in pairs:
            if fact.get("conflict") is not True:
                continue
            conflicts.append(f"### {ev['id']} — {key}")
            conflicts.append("")
            if fact.get("note"):
                conflicts.append(f"{fact['note']}")
                conflicts.append("")
            for i, variant in enumerate(fact.get("variants", []), start=1):
                value = variant.get("value")
                if key == "период":
                    shown = _period_label_single(value)
                elif isinstance(value, dict):
                    shown = json.dumps(value, ensure_ascii=False)
                else:
                    shown = str(value)
                srcs = ", ".join(_locators(variant))
                conflicts.append(f"{i}. **{shown}**")
                conflicts.append(f"   - источник: {srcs}")
            conflicts.append("")
    if conflicts:
        lines += ["## Конфликты", ""] + conflicts
    return "\n".join(lines) + "\n"



def _source_ids_of_variant(variant: dict) -> list[str]:
    return vov._source_ids_of(variant)


def render_geojson(events: list[dict]) -> str:
    features = []
    for ev in events:
        geometry = ev.get("geometry")
        if geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": ev["id"],
                "geometry": geometry,
                "properties": {
                    "type": ev["type"],
                    "title": _value_label(ev["title"]),
                    "units": ev["units"],
                    "localization": ev["localization"],
                    "period": ev["period"].get("value"),
                    "status": _fact_status(ev["period"]),
                },
            }
        )
    payload = {"type": "FeatureCollection", "features": features}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def build(root: str, vault: str | None, check: bool = False) -> tuple[int, list[str]]:
    errors = vov.run(root, vault)
    if errors:
        return 1, [f"ОШИБКА  {err}" for err in sorted(errors)]

    ds = vov.Dataset(root, vault)
    events = collect_events(ds)
    digest = dataset_hash(root)

    artifacts = {
        os.path.join(root, CHRONO_PATH): render_chronology(ds, events, digest),
        os.path.join(root, GEOJSON_PATH): render_geojson(events),
    }

    messages: list[str] = []
    if check:
        stale = [
            path
            for path, content in artifacts.items()
            if not os.path.exists(path) or _read_text(path) != content
        ]
        if stale:
            return 1, [f"РАСХОЖДЕНИЕ  {os.path.relpath(p, root)}" for p in stale]
        return 0, [f"Проверено: артефактов {len(artifacts)}, расхождений нет"]

    for path, content in artifacts.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        messages.append(f"записан {os.path.relpath(path, root)}")
    return 0, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--vault", default=None)
    parser.add_argument("--check", action="store_true", help="не писать, а сравнить артефакты с данными")
    args = parser.parse_args(argv)

    code, messages = build(args.root, args.vault, check=args.check)
    for msg in messages:
        print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
