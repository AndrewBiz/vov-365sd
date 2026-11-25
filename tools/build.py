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
    paths = [
        "data/registry.json", "data/sources.json", "data/units.json",
        "data/places.json", "data/documents.json", "data/people.json", "data/rosters.json",
        "data/front_lines/1941.json",
    ]
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


def _requisites_label(doc: dict) -> str:
    req = doc.get("requisites")
    if not req:
        return "реквизиты не установлены"
    if "fund" in req:
        label = f"ф. {req['fund']}, оп. {req['inventory']}, д. {req['file']}"
        if req.get("sheets"):
            label += f", л. {req['sheets']}"
        return label
    label = f"{req['edition']}, т. {req['volume']}, с. {req['page']}"
    if req.get("entry"):
        label += f", {req['entry']}"
    return label


def _document_label(doc: dict | None) -> str:
    if not doc:
        return "неизвестный документ"
    date_label = _period_label(doc["date"])
    type_label = doc.get("_type_label") or doc["type"]
    return f"{type_label} от {date_label} ({_requisites_label(doc)}) — {_value_label(doc['title'])}"



def render_chronology(ds: vov.Dataset, events: list[dict], digest: str, places: dict[str, dict],
                      documents: dict[str, dict] | None = None) -> str:
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
    type_labels = ds.registry_types.get("event_types", {})
    for ev in events:
        type_label = type_labels.get(ev["type"], ev["type"])
        lines.append(f"### {_period_label(ev['period'])} — {type_label}")
        lines.append("")
        lines.append(f"**{_value_label(ev['title'])}**")
        lines.append("")
        lines.append(f"- Части: {', '.join(ev['units'])}")
        lines.append(f"- Статус: {_fact_status(ev['period'])}")
        place_id = ev.get("place")
        if place_id:
            place = places.get(place_id, {})
            state = place.get("state", "?")
            label = place.get("names", {}).get("modern", place_id)
            suffix = " — место не локализовано" if state in {"unfound", "tract"} else ""
            lines.append(f"- Место: {label} (`{place_id}`, состояние `{state}`){suffix}")
        for loc in _locators(ev["period"]):
            lines.append(f"- Период: {loc}")
        for loc in _locators(ev["title"]):
            lines.append(f"- Название: {loc}")
        for fact in (ev.get("facts") or {}).values():
            lines.append(f"- {_value_label(fact)} — статус {_fact_status(fact)}")
            for loc in _locators(fact):
                lines.append(f"  - источник: {loc}")
        for doc_id in ev.get("documents") or []:
            lines.append(f"- Документ: {_document_label(documents.get(doc_id))}")
        if ev.get("note"):
            lines.append(f"- Примечание: {ev['note']}")
        lines.append("")

    documents = documents or {}
    if documents:
        used = {doc_id for ev in events for doc_id in (ev.get("documents") or [])}
        lines += ["## Документы, привязанные к событиям", ""]
        linked_ids = [d for d in sorted(documents) if d in used]
        if linked_ids:
            for doc_id in linked_ids:
                doc = documents[doc_id]
                lines.append(f"- `{doc_id}` — {_document_label(doc)}; носитель: {doc['carrier']['source_id']}")
                if doc.get("note"):
                    lines.append(f"  - примечание: {doc['note']}")
        else:
            lines.append("Ни один документ пока не привязан к событию.")
        lines.append("")

        unlinked = [d for d in sorted(documents) if d not in used]
        lines += ["## Документы без привязки к событиям", ""]
        if unlinked:
            lines.append(
                f"{len(unlinked)} документов внесены, но ни одно событие на них не ссылается — "
                "это очередь на разбор, а не утверждение, что они не нужны."
            )
            lines.append("")
            by_type: dict[str, list[str]] = {}
            for doc_id in unlinked:
                by_type.setdefault(documents[doc_id].get("_type_label", "?"), []).append(doc_id)
            for type_label in sorted(by_type):
                ids = by_type[type_label]
                lines.append(f"### {type_label} ({len(ids)})")
                lines.append("")
                for doc_id in ids:
                    doc = documents[doc_id]
                    date_label = _period_label(doc["date"]) if doc.get("date") else "дата не установлена"
                    lines.append(f"- {date_label} — {_value_label(doc['title'])}; `{doc_id}`")
                lines.append("")
        else:
            lines.append("Все документы привязаны к событиям.")
            lines.append("")

    if ds.people["people"]:
        lines += ["## Люди и списки", ""]
        lines.append(
            f"Персон: {len(ds.people['people'])}. Записей списков: {len(ds.rosters['entries'])}."
        )
        conflicted = [p for p in ds.people["people"] if p.get("conflict")]
        if conflicted:
            lines.append("")
            lines.append("Персоны с расходящимися записями: " + ", ".join(f"`{p['id']}`" for p in conflicted))
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


def resolve_geometry(event: dict, places: dict[str, dict]) -> tuple[dict | None, str, str | None, str | None]:
    """Возвращает (геометрия, уровень локализации, id места, состояние места).

    Собственная геометрия события имеет приоритет над местом.
    """
    place_id = event.get("place")
    place = places.get(place_id) if place_id else None
    if event.get("geometry") is not None:
        return event["geometry"], event["localization"], place_id, (place or {}).get("state")
    if place:
        coords = place.get("coords")
        if isinstance(coords, dict) and coords.get("value"):
            value = coords["value"]
            return (
                {"type": "Point", "coordinates": [value["lon"], value["lat"]]},
                place["accuracy"],
                place_id,
                place["state"],
            )
        return None, event["localization"], place_id, place.get("state")
    return None, event["localization"], None, None


PEOPLE_PATH = "docs/research/Люди — что известно (сгенерировано).md"


def _claim_label(claim: dict) -> str:
    if isinstance(claim.get("value"), dict) and "precision" in claim["value"]:
        return _period_label(claim)
    return _value_label(claim)


def _claim_basis_label(ds: vov.Dataset, claim: dict) -> str:
    labels = ds.registry_types.get("evidence_basis", {})
    variants = claim.get("variants") if claim.get("conflict") is True else [claim]
    names = []
    for variant in variants:
        basis = variant.get("basis")
        if basis:
            label = labels.get(basis, basis)
            if label not in names:
                names.append(label)
    return ", ".join(names) if names else "вид не указан"


def _claim_sources(ds: vov.Dataset, claim: dict) -> str:
    source_titles = {s["id"]: s.get("title") or s["id"] for s in ds.sources["sources"]}
    variants = claim.get("variants") if claim.get("conflict") is True else [claim]
    parts: list[str] = []
    for variant in variants:
        entries = variant.get("provenance")
        entries = entries if isinstance(entries, list) else ([entries] if entries else [])
        for entry in entries:
            sid = entry.get("source_id", "")
            locator = entry.get("locator") or {}
            spot = locator.get("slide") and f"слайд {locator['slide']}" or (
                locator.get("page") and f"с. {locator['page']}"
            ) or locator.get("url", "")
            label = f"`{sid}`" + (f", {spot}" if spot else "")
            if label not in parts:
                parts.append(label)
    return "; ".join(parts)


def render_people(ds: vov.Dataset, digest: str) -> str:
    keys = ds.registry_types.get("person_fact_keys", {})
    entries_by_person: dict[str, list[dict]] = {}
    for entry in ds.rosters["entries"]:
        entries_by_person.setdefault(entry["person"], []).append(entry)
    documents = {doc["id"]: doc for doc in ds.documents["documents"]}

    people = sorted(ds.people["people"], key=lambda p: (p["name"], p["id"]))
    with_claims = [p for p in people if p.get("claims")]
    conflicted = [p for p in people if p.get("conflict")]
    single = [p for p in with_claims if sum(len(v) for v in p["claims"].values()) == 1]

    lines = [
        "# Люди — что известно",
        "",
        "<!-- Сгенерировано tools/build.py из data/people.json, data/rosters.json, data/documents.json. "
        "Ручная правка не имеет силы. -->",
        "",
        f"Набор данных: `{digest[:16]}…`",
        "",
        f"Персон в реестре: {len(people)}. С утверждениями из свидетельств: {len(with_claims)}. "
        f"С расхождениями: {len(conflicted)}. С единственным сведением: {len(single)}.",
        "",
        "## Утверждения по людям",
        "",
    ]

    if not with_claims:
        lines += ["Пока ни у одной персоны нет утверждений из свидетельств.", ""]

    for person in with_claims:
        mark = " ⚠ конфликт" if person.get("conflict") else ""
        birth = person["birth_year"] if person["birth_year"] != "неизвестен" else "год рождения неизвестен"
        lines.append(f"### {person['name']} ({birth}){mark}")
        lines.append("")
        lines.append(f"`{person['id']}`")
        lines.append("")
        for key in sorted(person["claims"]):
            label = keys.get(key, key)
            for claim in person["claims"][key]:
                basis = _claim_basis_label(ds, claim)
                sources = _claim_sources(ds, claim)
                lines.append(f"- **{label}**: {_claim_label(claim)} — {basis}; {sources}")
        for entry in sorted(entries_by_person.get(person["id"], []), key=lambda e: e["id"]):
            doc = documents.get(entry["document"], {})
            doc_label = _document_label(doc) if doc else entry["document"]
            date_label = _period_label(entry["fate_date"]) if entry.get("fate_date") else "дата не указана"
            lines.append(
                f"- **Запись списка**: {entry['fate']['value']} ({date_label}) — {entry['unit']}; {doc_label}"
            )
        lines.append("")

    lines += ["## Расхождения", ""]
    if conflicted:
        for person in conflicted:
            keys_with = []
            for key, claims in (person.get("claims") or {}).items():
                values = {_claim_label(c) for c in claims}
                if len(values) > 1:
                    keys_with.append(f"{keys.get(key, key)}: " + " / ".join(sorted(values)))
            if not keys_with:
                keys_with.append("записи списков")
            lines.append(f"- {person['name']} (`{person['id']}`) — " + "; ".join(keys_with))
    else:
        lines.append("Расхождений нет.")
    lines.append("")

    lines += ["## Известно одно сведение", ""]
    if single:
        for person in single:
            key, claims = next(iter(person["claims"].items()))
            lines.append(
                f"- {person['name']} (`{person['id']}`) — {keys.get(key, key)}: {_claim_label(claims[0])}"
            )
    else:
        lines.append("Нет людей с единственным сведением.")
    lines.append("")

    lines += ["## Люди без свидетельств", ""]
    bare = [p for p in people if not p.get("claims")]
    lines.append(
        f"{len(bare)} персон известны только по именным спискам — об их жизни, призыве и родных "
        "сведений в данных нет. Это очередь на поиск, а не утверждение, что сведений не существует."
    )
    lines.append("")
    return "\n".join(lines)


FRONT_GEOJSON_PATH = "data/dist/front_lines.geojson"
FRAMES_PATH = "data/dist/frames.json"


def render_front_lines(ds: vov.Dataset) -> str:
    features = []
    for line in sorted(ds.front_lines.get("front_lines", []), key=lambda x: x["id"]):
        value = line["date"].get("value") or {}
        features.append({
            "type": "Feature",
            "id": line["id"],
            "geometry": {"type": "LineString", "coordinates": line["geometry"]["coordinates"]},
            "properties": {
                "id": line["id"],
                "date_from": value.get("from"),
                "date_to": value.get("to"),
                "precision": value.get("precision"),
                "accuracy_km": line["accuracy_km"],
                "accuracy_level": line["accuracy_level"],
                "source_id": line["provenance"]["source_id"],
                "sheet": line["provenance"]["locator"].get("sheet"),
                "note": line.get("note"),
            },
        })
    payload = {"type": "FeatureCollection", "features": features}
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def render_frames(ds: vov.Dataset, events: list[dict]) -> str:
    frames: dict[str, dict] = {}
    for line in ds.front_lines.get("front_lines", []):
        value = line["date"].get("value") or {}
        date = value.get("from")
        if not date:
            continue
        related = []
        for ev in events:
            v = ev["period"].get("value") or {}
            start, end = v.get("from"), v.get("to") or v.get("from")
            if start and start <= date and (end is None or end >= date):
                related.append(ev["id"])
        frames[date] = {
            "date": date,
            "feature_id": line["id"],
            "accuracy_km": line["accuracy_km"],
            "accuracy_level": line["accuracy_level"],
            "events": sorted(set(related)),
        }
    ordered = [frames[d] for d in sorted(frames)]
    return json.dumps({"frames": ordered}, ensure_ascii=False, indent=1) + "\n"


def render_geojson(events: list[dict], places: dict[str, dict]) -> str:
    features = []
    for ev in events:
        geometry, localization, place_id, place_state = resolve_geometry(ev, places)
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
                    "localization": localization,
                    "place": place_id,
                    "place_state": place_state,
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

    places = {place["id"]: place for place in ds.places["places"]}
    type_labels = ds.registry_types.get("document_types", {})
    documents = {
        doc["id"]: {**doc, "_type_label": type_labels.get(doc["type"], doc["type"])}
        for doc in ds.documents["documents"]
    }
    artifacts = {
        os.path.join(root, CHRONO_PATH): render_chronology(ds, events, digest, places, documents),
        os.path.join(root, GEOJSON_PATH): render_geojson(events, places),
        os.path.join(root, PEOPLE_PATH): render_people(ds, digest),
        os.path.join(root, FRONT_GEOJSON_PATH): render_front_lines(ds),
        os.path.join(root, FRAMES_PATH): render_frames(ds, events),
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
