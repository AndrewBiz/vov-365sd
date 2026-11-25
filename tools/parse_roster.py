#!/usr/bin/env python3
"""Разбор табличных именных списков из снимка сайта в черновик записей.

  python3 tools/parse_roster.py --cache data/cache/site-365sd --url <страница> [--apply]

Черновик кладётся в data/drafts/<slug>.json вместе с отчётом о нераспознанном.
В данные (people/rosters) попадает только по явному --apply: ошибка разбора
не должна молча становиться «судьбой» человека.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFTS = "data/drafts"

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}

HEADER_MAP = {
    "№": "row",
    "п/п": "row",
    "номер": "row",
    "фамилия": "name",
    "фио": "name",
    "партийность": "party",
    "состав": "rank",
    "вус": "vus",
    "военно-учетная": "vus",
    "примечания": "note",
    "примечание": "note",
    "год рождения": "birth_year",
    "г.р.": "birth_year",
}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def translit(text: str) -> str:
    out = []
    for char in text.lower():
        out.append(TRANSLIT.get(char, char))
    slug = re.sub(r"[^a-z0-9]+", "-", "".join(out)).strip("-")
    return slug or "x"


def find_html(root: str, cache: str, url: str, date: str | None) -> str:
    base = os.path.join(root, cache, date) if date else None
    if base is None:
        dirs = sorted(d for d in os.listdir(os.path.join(root, cache)) if os.path.isdir(os.path.join(root, cache, d)))
        if not dirs:
            raise SystemExit(f"снимков нет в {cache}")
        base = os.path.join(root, cache, dirs[-1])
    with open(os.path.join(base, "pages.json"), encoding="utf-8") as fh:
        mapping = json.load(fh)["pages"]
    entry = mapping.get(url)
    if entry is None:
        raise SystemExit(f"url не найден в снимке: {url}")
    html_rel = entry.get("html") if isinstance(entry, dict) else None
    if not html_rel:
        raise SystemExit(f"у страницы нет разметки (нет таблиц или снимок старый): {url}")
    return os.path.join(base, html_rel)


def map_columns(header: list[str]) -> dict[int, str]:
    columns: dict[int, str] = {}
    for index, cell in enumerate(header):
        key = cell.strip().lower().rstrip(".")
        for marker, field in HEADER_MAP.items():
            if marker in key:
                columns[index] = field
                break
    return columns


def build_draft(html_path: str, url: str, document_id: str, unit: str) -> dict:
    with open(html_path, encoding="utf-8") as fh:
        parser = TableParser()
        parser.feed(fh.read())
    if not parser.tables:
        raise SystemExit("таблиц не найдено")
    table = max(parser.tables, key=len)
    header = table[0]
    columns = map_columns(header)
    rows: list[dict] = []
    empty_cells = 0
    no_name = 0
    for row in table[1:]:
        record: dict[str, object] = {"raw": row}
        for index, field in columns.items():
            value = row[index] if index < len(row) else ""
            if not value:
                empty_cells += 1
            record[field] = value
        name = str(record.get("name") or "").strip()
        if not name or len(name) < 5:
            no_name += 1
            continue
        rows.append(record)
    report = {
        "url": url,
        "document": document_id,
        "unit": unit,
        "columns": columns,
        "header": header,
        "rows_total": len(table) - 1,
        "rows_kept": len(rows),
        "rows_without_name": no_name,
        "empty_cells": empty_cells,
        "unmapped_columns": [c for i, c in enumerate(header) if i not in columns and c.strip()],
    }
    return {"report": report, "rows": rows}


def _fact(value: str, url: str, quote: str, source_id: str) -> dict:
    return {
        "value": value,
        "provenance": {"source_id": source_id, "locator": {"url": url, "quote": quote}},
        "status": "черновик",
    }


def apply_draft(root: str, draft: dict, url: str, source_id: str, doc_slug: str) -> tuple[int, int]:
    people_path = os.path.join(root, "data/people.json")
    roster_path = os.path.join(root, "data/rosters.json")
    with open(people_path, encoding="utf-8") as fh:
        people = json.load(fh)
    with open(roster_path, encoding="utf-8") as fh:
        rosters = json.load(fh)

    known = {p["name"].strip().lower(): p["id"] for p in people["people"]}
    used_ids = {p["id"] for p in people["people"]}
    entries_added = persons_added = 0

    for row in draft["rows"]:
        name = row["name"].strip()
        key = name.lower()
        person_id = known.get(key)
        if person_id is None:
            base = f"p-{translit(name)}"
            person_id = base
            counter = 2
            while person_id in used_ids:
                person_id = f"{base}-{counter}"
                counter += 1
            used_ids.add(person_id)
            known[key] = person_id
            people["people"].append({"id": person_id, "name": name, "birth_year": "неизвестен"})
            persons_added += 1

        entry_id = f"rec-{doc_slug}-{row.get('row') or len(rosters['entries']) + 1}"
        if any(e["id"] == entry_id for e in rosters["entries"]):
            continue
        entry = {
            "id": entry_id,
            "document": draft["report"]["document"],
            "person": person_id,
            "unit": draft["report"]["unit"],
            "row": int(row["row"]) if (row.get("row") or "").isdigit() else None,
            "fate": _fact("неизвестно", url, f"строка списка: {name}", source_id),
            "fate_date": None,
            "facts": {},
        }
        for field, label in (("party", "партийность"), ("rank", "состав"), ("vus", "ВУС"), ("note", "примечание")):
            value = (row.get(field) or "").strip()
            if value:
                entry["facts"][field] = _fact(value, url, f"{label}: {value}", source_id)
        if entry["row"] is None:
            entry.pop("row")
        rosters["entries"].append(entry)
        entries_added += 1

    with open(people_path, "w", encoding="utf-8") as fh:
        json.dump(people, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(roster_path, "w", encoding="utf-8") as fh:
        json.dump(rosters, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return persons_added, entries_added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=ROOT_DEFAULT)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--document", required=True, help="id документа-списка")
    parser.add_argument("--unit", required=True, help="id формирования, к которому относится список")
    parser.add_argument("--source", required=True, help="id источника-носителя")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    html_path = find_html(args.root, args.cache, args.url, args.date)
    draft = build_draft(html_path, args.url, args.document, args.unit)
    slug = translit(args.url.rstrip("/").rsplit("/", 1)[-1])
    drafts_dir = os.path.join(args.root, DRAFTS)
    os.makedirs(drafts_dir, exist_ok=True)
    draft_path = os.path.join(drafts_dir, f"{slug}.json")
    with open(draft_path, "w", encoding="utf-8") as fh:
        json.dump(draft, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    report = draft["report"]
    print(f"страница: {args.url}")
    print(f"колонки: {report['columns']}")
    print(f"строк в таблице: {report['rows_total']}, принято: {report['rows_kept']}, без ФИО: {report['rows_without_name']}")
    print(f"пустых ячеек: {report['empty_cells']}, нераспознанных колонок: {report['unmapped_columns']}")
    print(f"черновик: {os.path.relpath(draft_path, args.root)}")

    if args.apply:
        doc_slug = args.document.removeprefix("doc-")
        persons, entries = apply_draft(args.root, draft, args.url, args.source, doc_slug)
        print(f"применено: персон +{persons}, записей +{entries}")
    else:
        print("в данные не записано (нужен --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
