#!/usr/bin/env python3
"""Разбор каталога выгрузки архивного портала в реестр документов.

  python3 tools/portal_intake.py --source src-pamyat-naroda [--dry-run]

Читает catalogue.json источника, выводит реквизиты из идентификатора дела,
преобразует типы карточек по portal_types реестра и дописывает документы в
data/documents.json. Повторный прогон ничего не дублирует и не переписывает.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def iso_date(ddmmyyyy: str | None) -> str | None:
    if not ddmmyyyy:
        return None
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", ddmmyyyy.strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def split_deal(deal: str) -> dict | None:
    m = re.fullmatch(r"(\d+)-(\d+)-(\d+)", deal or "")
    if not m:
        return None
    return {
        "fund": str(int(m.group(1))),
        "inventory": str(int(m.group(2))),
        "file": str(int(m.group(3))),
    }


def document_id(deal: str, page: str) -> str:
    return f"doc-pn-{deal}-p{page}"


def build_document(entry: dict, source_id: str, vault_prefix: str, portal_types: dict) -> dict:
    portal_type = entry.get("type") or ""
    if portal_type not in portal_types:
        raise SystemExit(f"неизвестный тип карточки портала: '{portal_type}' (документ {entry.get('name')})")
    doc_type = portal_types[portal_type]

    deal = entry["deal"]
    requisites = split_deal(deal)
    page = entry.get("page") or ""
    start, end = iso_date(entry.get("period_from")), iso_date(entry.get("period_to"))
    doc_date = iso_date(entry.get("doc_date"))
    if start and end and start != end:
        period, precision, note = {"from": start, "to": end, "precision": "range"}, "range", "период выведен из карточки портала"
    elif start:
        period, precision, note = {"from": start, "to": start, "precision": "day"}, "day", "период выведен из карточки портала"
    elif doc_date:
        period, precision, note = {"from": doc_date, "to": doc_date, "precision": "day"}, "day", "дата выведена из карточки портала"
    else:
        period, precision, note = {"from": None, "to": None, "precision": "range"}, "range", "дата на карточке не указана"

    url = entry.get("portal_url")
    quote = (entry.get("card_text") or entry.get("name") or "")[:300]
    carrier = {
        "source_id": source_id,
        "portal_url": url,
        "page": int(re.sub(r"\D", "", page) or 0) or None,
        "bytes": entry.get("bytes"),
        "sha256": entry.get("sha256"),
    }
    if entry.get("id"):
        carrier["portal_id"] = str(entry["id"])
    if entry.get("file"):
        carrier["file_path"] = vault_prefix + entry["file"]
    carrier = {k: v for k, v in carrier.items() if v is not None}

    doc = {
        "id": document_id(deal, page),
        "type": doc_type,
        "title": {
            "value": entry.get("name") or "документ портала",
            "provenance": {"source_id": source_id, "locator": {"url": url, "quote": quote}},
            "status": "черновик",
        },
        "date": {
            "value": period,
            "provenance": {"source_id": source_id, "locator": {"url": url, "quote": quote}},
            "status": "черновик",
            "note": note,
        },
        "requisites": requisites,
        "carrier": carrier,
        "note": "реквизиты выведены из пути портала ("
                + deal + "), а не прочитаны с титульного листа."
                + (f" Карточка: {entry['card_text'][:200]}" if entry.get("card_text") else ""),
    }
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--source", required=True)
    ap.add_argument("--vault-prefix", default="Хобби/365сд/Исходники/ЦАМО — Память народа/")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = args.root
    sources = load_json(os.path.join(root, "data/sources.json"))["sources"]
    source = next((s for s in sources if s["id"] == args.source), None)
    if source is None:
        raise SystemExit(f"источник {args.source} не найден")
    if source.get("kind") != "archive-portal":
        raise SystemExit(f"источник {args.source} имеет вид {source.get('kind')}, ожидался archive-portal")

    catalogue_path = os.path.join(root, source["cache_path"], "catalogue.json")
    if not os.path.exists(catalogue_path):
        raise SystemExit(f"каталог выгрузки не найден: {catalogue_path}")
    catalogue = load_json(catalogue_path)
    entries = catalogue.get("documents") or []
    registry = load_json(os.path.join(root, "data/registry.json"))
    portal_types = registry.get("portal_types") or {}

    documents_path = os.path.join(root, "data/documents.json")
    documents = load_json(documents_path)
    existing = {d["id"] for d in documents["documents"]}

    added = skipped_no_scan = already = failed = 0
    for entry in entries:
        if not entry.get("file"):
            skipped_no_scan += 1
            continue
        doc = build_document(entry, args.source, args.vault_prefix, portal_types)
        if doc["id"] in existing:
            already += 1
            continue
        if not args.dry_run:
            documents["documents"].append(doc)
            existing.add(doc["id"])
        added += 1

    if not args.dry_run:
        save_json(documents_path, documents)

    print(f"карточек в каталоге: {len(entries)}")
    print(f"добавлено документов: {added}")
    print(f"уже были: {already}")
    print(f"пропущено без сканов: {skipped_no_scan}")
    if args.dry_run:
        print("(dry-run: данные не записаны)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
