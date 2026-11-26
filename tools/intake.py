#!/usr/bin/env python3
"""Входной конвейер: артефакт в inbox → офлайн-разбор → запись в базу.

  python3 tools/intake.py drop files/p00000251.jpg --slug pn-354-10-l251 --kind archive-document
  python3 tools/intake.py status
  python3 tools/intake.py scan pn-354-10-l251
  python3 tools/intake.py apply pn-354-10-l251 --vault "~/Documents/Obsidian Vault" --dry-run

Шаг `scan` — только офлайн: хеши, формат, размеры, текстовый слой, OCR с оценкой
разборчивости, дедуп, проверка реквизит и источника. Сеть и модели в нём не
используются. Запись в данные делает только `apply`, отдельной командой.

Состояние артефакта не хранится в манифесте: оно выводится из журнала
`data/intake/journal.jsonl` (append-only) и наличия предложения.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DEFAULT, "tools"))

import coremeta  # noqa: E402

INBOX = "inbox"
JOURNAL = "data/intake/journal.jsonl"
PROPOSALS = "data/proposals"
KINDS = ("archive-document", "map-sheet", "book-page", "presentation", "web-page", "scan-set")
REQUIRED = ("slug", "kind", "source_id", "received", "files")
MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"%PDF-": "pdf",
    b"PK\x03\x04": "zip",          # pptx/docx/xlsx — уточняется по содержимому
    b"<!DOCTYPE": "html",
    b"<html": "html",
}


class IntakeError(RuntimeError):
    pass


# ---------------------------------------------------------------- манифест

def _yaml():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - зависит от системы
        raise IntakeError("нужен PyYAML: python3 -m pip install --user pyyaml") from exc
    return yaml


def manifest_path(root: pathlib.Path, slug: str) -> pathlib.Path:
    return root / INBOX / slug / "artifact.yaml"


def read_manifest(root: pathlib.Path, slug: str) -> dict:
    path = manifest_path(root, slug)
    if not path.exists():
        raise IntakeError(f"нет манифеста: {path.relative_to(root)}")
    data = _yaml().safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise IntakeError(f"{path.name}: манифест должен быть словарём YAML")
    data.setdefault("slug", slug)
    return data


def validate_manifest(root: pathlib.Path, manifest: dict, slug: str) -> list[str]:
    """Проверки манифеста: без них артефакт не уходит в разбор."""
    problems: list[str] = []
    for key in REQUIRED:
        if not manifest.get(key):
            problems.append(f"нет обязательного поля «{key}»")
    if manifest.get("slug") and manifest["slug"] != slug:
        problems.append(f"slug в манифесте ({manifest['slug']}) не совпадает с именем папки ({slug})")
    kind = manifest.get("kind")
    if kind and kind not in KINDS:
        problems.append(f"неизвестный вид артефакта «{kind}»; допустимы: {', '.join(KINDS)}")

    source_id = manifest.get("source_id")
    if source_id:
        sources = json.loads((root / "data/sources.json").read_text(encoding="utf-8"))["sources"]
        known = {s["id"] for s in sources}
        if source_id not in known:
            problems.append(f"источник «{source_id}» не зарегистрирован в data/sources.json")

    requisites = manifest.get("requisites")
    if requisites is not None:
        if not isinstance(requisites, dict):
            problems.append("requisites должен быть словарём или null")
        else:
            missing = [k for k in ("fund", "inventory", "file") if not requisites.get(k)]
            if missing:
                problems.append(
                    "реквизиты частичные (" + ", ".join(missing) + "); нужен полный блок "
                    "фонд+опись+дело или requisites: null с пояснением в заметках"
                )

    date = manifest.get("date")
    if date:
        if not isinstance(date, dict) or not date.get("from"):
            problems.append("date должен содержать from (и to/ precision, если известны)")
        elif date.get("precision") not in {"day", "month", "year", "range"}:
            problems.append("date.precision должен быть day|month|year|range")

    files = manifest.get("files") or []
    if not files:
        problems.append("в манифесте нет файлов")
    for rel in files:
        if not (root / INBOX / slug / rel).exists():
            problems.append(f"файл из манифеста не найден: {rel}")

    if kind == "archive-document" and not manifest.get("document_type"):
        problems.append("для archive-document нужно поле document_type")
    return problems


# ---------------------------------------------------------------- файлы

def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_format(path: pathlib.Path) -> str:
    head = path.read_bytes()[:16]
    for magic, name in MAGIC.items():
        if head.startswith(magic):
            if name == "zip":
                return "pptx" if path.suffix.lower() in {".pptx", ".ppt"} else "zip"
            return name
    if path.suffix.lower() in {".txt", ".md"}:
        return "text"
    return "unknown"


def describe_file(path: pathlib.Path) -> dict:
    """Метаданные без сети: формат, размеры, страницы, текстовый слой, разборчивость."""
    info: dict = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
                  "format": detect_format(path)}
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        if info["format"] in {"jpeg", "png"}:
            with Image.open(path) as im:
                info["width"], info["height"] = im.size
    except Exception as exc:  # pragma: no cover - зависит от файла
        info["image_error"] = str(exc)[:120]

    if info["format"] == "pdf":
        try:
            out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30)
            match = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
            if match:
                info["pages"] = int(match.group(1))
            text = subprocess.run(["pdftotext", "-q", "-l", "3", str(path), "-"],
                                  capture_output=True, text=True, timeout=60)
            info["text_layer_chars"] = len(text.stdout.strip())
        except FileNotFoundError:
            info["pdf_tools"] = "нет pdfinfo/pdftotext"

    if info["format"] == "pptx":
        try:
            import zipfile
            with zipfile.ZipFile(path) as zf:
                info["slides"] = len([n for n in zf.namelist()
                                      if re.match(r"ppt/slides/slide\d+\.xml$", n)])
        except Exception as exc:  # pragma: no cover
            info["pptx_error"] = str(exc)[:120]
    return info


def readability(path: pathlib.Path) -> dict | None:
    """Оценка разборчивости скана тем же OCR, что и ocr_scan.py."""
    fmt = detect_format(path)
    if fmt not in {"jpeg", "png", "pdf"}:
        return None
    image_path = path
    if fmt == "pdf":
        try:
            tmp = pathlib.Path("/tmp") / f"intake_{path.stem}.png"
            subprocess.run(["pdftoppm", "-r", "200", "-png", "-f", "1", "-l", "1",
                            str(path), str(tmp.with_suffix(""))], check=True, timeout=120)
            candidates = sorted(tmp.parent.glob(tmp.stem + "*.png"))
            if not candidates:
                return {"note": "не удалось отрендерить первую страницу PDF"}
            image_path = candidates[0]
        except (FileNotFoundError, subprocess.CalledProcessError):
            return {"note": "нет pdftoppm для рендера PDF"}
    try:
        import ocr_scan
        text = ocr_scan.ocr_page(image_path, psm=6, scale=2.0,
                                 tessdata=os.path.expanduser("~/.hermes/tools/tessdata"))
        words, good, pct = ocr_scan.quality(text)
        return {"words": words, "plausible": good, "readability_pct": pct,
                "text_chars": len(text)}
    except SystemExit as exc:
        return {"error": str(exc)[:160]}
    except Exception as exc:  # pragma: no cover
        return {"error": str(exc)[:160]}


# ---------------------------------------------------------------- журнал

def journal_read(root: pathlib.Path) -> list[dict]:
    path = root / JOURNAL
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise IntakeError(f"{JOURNAL}:{number}: битая строка журнала ({exc.msg})") from exc
    return entries


def journal_append(root: pathlib.Path, slug: str, step: str, state: str, **extra) -> None:
    import datetime as dt
    path = root / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "slug": slug, "step": step, "state": state,
             "core_version": coremeta.version_or_default()}
    entry.update({k: v for k, v in extra.items() if v is not None})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def state_of(root: pathlib.Path, slug: str, entries: list[dict] | None = None) -> str:
    entries = entries if entries is not None else journal_read(root)
    mine = [e for e in entries if e.get("slug") == slug]
    if any(e.get("step") == "reject" for e in mine):
        return "rejected"
    if any(e.get("step") == "apply" and e.get("state") == "applied" for e in mine):
        return "applied"
    if any(e.get("step") == "scan" and e.get("state") == "duplicate" for e in mine):
        return "duplicate"
    if (root / PROPOSALS / slug / "extraction.json").exists():
        return "proposed"
    if any(e.get("step") == "scan" and e.get("state") == "ok" for e in mine):
        return "proposed"
    return "new"


NEXT_ACTION = {"new": "scan", "proposed": "apply", "applied": "—",
               "duplicate": "разобраться вручную", "rejected": "—"}


# ---------------------------------------------------------------- предложение

def existing_documents(root: pathlib.Path) -> list[dict]:
    return json.loads((root / "data/documents.json").read_text(encoding="utf-8"))["documents"]


def derive_document_id(manifest: dict) -> str:
    if manifest.get("document_id"):
        return manifest["document_id"]
    requisites = manifest.get("requisites") or {}
    sheet = (requisites.get("sheet") or "").strip()
    if manifest.get("source_id") == "src-pamyat-naroda" and requisites and sheet.isdigit():
        fund = requisites["fund"].strip()
        inventory = int(str(requisites["inventory"]).strip())
        f = int(str(requisites["file"]).strip())
        return f"doc-pn-{fund}-{inventory:07d}-{f:04d}-p{int(sheet):08d}"
    return f"doc-{manifest['slug']}"


def find_duplicates(root: pathlib.Path, manifest: dict, files: list[dict]) -> list[dict]:
    documents = existing_documents(root)
    hashes = {f["sha256"]: f["name"] for f in files}
    requisites = manifest.get("requisites") or {}
    duplicates = []
    for doc in documents:
        carrier = doc.get("carrier") or {}
        if carrier.get("sha256") in hashes:
            duplicates.append({"by": "sha256", "document": doc["id"], "file": hashes[carrier["sha256"]]})
            continue
        doc_req = doc.get("requisites") or {}
        if requisites and all(doc_req.get(k) == str(requisites.get(k)) for k in ("fund", "inventory", "file")):
            if str(doc_req.get("sheets") or "").strip("л. ").strip() == str(requisites.get("sheet") or "").strip():
                duplicates.append({"by": "requisites", "document": doc["id"],
                                   "note": "тот же лист, возможно лучшее качество скана"})
    return duplicates


def suggest_document(manifest: dict, files: list[dict]) -> dict:
    """Черновик записи документа — без записи в базу."""
    source_id = manifest["source_id"]
    locator = {}
    origin = manifest.get("origin") or {}
    if origin.get("portal_url"):
        locator["url"] = origin["portal_url"]
    if manifest.get("expectations"):
        locator["quote"] = manifest["expectations"]
    locator.setdefault("paragraph", "манифест артефакта")
    date = manifest.get("date") or {}
    record = {
        "id": derive_document_id(manifest),
        "type": manifest.get("document_type") or ("map" if manifest["kind"] == "map-sheet" else "other"),
        "title": {"value": manifest.get("title") or manifest["slug"],
                  "provenance": {"source_id": source_id, "locator": locator},
                  "status": "черновик"},
        "date": {"value": {"from": date.get("from"), "to": date.get("to") or date.get("from"),
                           "precision": date.get("precision") or "day"},
                 "provenance": {"source_id": source_id, "locator": locator},
                 "status": "черновик"},
        "carrier": {"source_id": source_id,
                    "sha256": files[0]["sha256"] if files else None,
                    "bytes": files[0]["bytes"] if files else None},
    }
    if manifest.get("requisites"):
        req = manifest["requisites"]
        record["requisites"] = {"fund": str(req["fund"]), "inventory": str(req["inventory"]),
                                "file": str(req["file"])}
        if req.get("sheet"):
            record["requisites"]["sheets"] = str(req["sheet"])
    else:
        record["requisites"] = None
        record["note"] = "реквизиты не установлены: см. заметки артефакта"
    if origin.get("portal_url"):
        record["carrier"]["portal_url"] = origin["portal_url"]
    if manifest.get("unit"):
        record["note"] = (record.get("note", "") + f" Часть: {manifest['unit']}.").strip()
    return record


# ---------------------------------------------------------------- команды

def cmd_drop(args) -> int:
    root = pathlib.Path(args.root)
    slug = args.slug
    folder = root / INBOX / slug
    if folder.exists() and not args.force:
        raise IntakeError(f"папка уже существует: {folder.relative_to(root)} (перезапись — --force)")
    files_dir = folder / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    rels = []
    for src in args.files:
        source = pathlib.Path(src).expanduser()
        if not source.exists():
            raise IntakeError(f"файл не найден: {src}")
        target = files_dir / source.name
        if target.resolve() != source.resolve():
            shutil.copy2(source, target)
        rels.append(f"files/{source.name}")
    manifest = {
        "slug": slug,
        "kind": args.kind,
        "title": args.title or "TODO: название документа",
        "source_id": args.source,
        "received": args.received,
        "origin": {"portal_url": args.url} if args.url else {},
        "requisites": None,
        "date": {"from": None, "to": None, "precision": "day"},
        "unit": "",
        "files": rels,
        "original_location": args.location or "TODO: папка в Obsidian",
        "expectations": args.comment or "",
    }
    if args.kind == "archive-document":
        manifest["document_type"] = args.document_type or "other"
    manifest_path(root, slug).write_text(
        _yaml().safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    journal_append(root, slug, "drop", "new", files=rels)
    print(f"создан артефакт: {folder.relative_to(root)}")
    print("дальше: заполни поля с TODO в artifact.yaml, затем python3 tools/intake.py scan", slug)
    return 0


def cmd_status(args) -> int:
    root = pathlib.Path(args.root)
    inbox = root / INBOX
    if not inbox.exists():
        print("инбокс пуст")
        return 0
    entries = journal_read(root)
    rows = []
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir()):
        state = state_of(root, folder.name, entries)
        rows.append((folder.name, state, NEXT_ACTION.get(state, "?")))
    if not rows:
        print("инбокс пуст")
        return 0
    width = max(len(r[0]) for r in rows)
    print(f"{'артефакт'.ljust(width)}  состояние  следующий шаг")
    for slug, state, action in rows:
        print(f"{slug.ljust(width)}  {state:10s} {action}")
    return 0


def cmd_scan(args) -> int:
    root = pathlib.Path(args.root)
    slugs = args.slug or [p.name for p in sorted((root / INBOX).iterdir()) if p.is_dir()]
    if not slugs:
        print("инбокс пуст")
        return 0
    exit_code = 0
    for slug in slugs:
        try:
            code = scan_one(root, slug)
            exit_code = max(exit_code, code)
        except IntakeError as exc:
            print(f"ОШИБКА  {slug}: {exc}")
            exit_code = 1
    return exit_code


def scan_one(root: pathlib.Path, slug: str) -> int:
    manifest = read_manifest(root, slug)
    problems = validate_manifest(root, manifest, slug)
    if problems:
        journal_append(root, slug, "scan", "error", problems=problems)
        print(f"ОШИБКА  {slug}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    folder = root / INBOX / slug
    files = [describe_file(folder / rel) for rel in manifest["files"]]
    digests = sorted(f["sha256"] for f in files)

    proposal_dir = root / PROPOSALS / slug
    proposal_file = proposal_dir / "extraction.json"
    if proposal_file.exists():
        previous = json.loads(proposal_file.read_text(encoding="utf-8"))
        if previous.get("digests") == digests:
            print(f"{slug}: уже разобрано (файлы не менялись)")
            journal_append(root, slug, "scan", "unchanged", digests=digests)
            return 0

    warnings: list[str] = []
    duplicates = find_duplicates(root, manifest, files)
    for dup in duplicates:
        warnings.append(f"дубликат по {dup['by']}: документ {dup['document']}")

    if len(files) == 1:
        reading = readability(folder / manifest["files"][0])
        if reading and reading.get("readability_pct") is not None:
            if reading["readability_pct"] < 85 or reading.get("words", 0) < 60:
                warnings.append(
                    f"низкая разборчивость OCR: {reading['readability_pct']}% "
                    f"({reading.get('words', 0)} слов) — текст не гарантирован")
            files[0]["ocr"] = reading

    if (manifest.get("date") or {}).get("from") in (None, "TODO"):
        warnings.append("дата документа не заполнена в манифесте")

    extraction = {
        "slug": slug,
        "core_version": coremeta.version_or_default(),
        "kind": manifest["kind"],
        "digests": digests,
        "files": files,
        "duplicates": duplicates,
        "warnings": warnings,
        "suggested_document": suggest_document(manifest, files),
        "ready": not duplicates and not any("не заполнена" in w for w in warnings),
    }
    proposal_dir.mkdir(parents=True, exist_ok=True)
    proposal_file.write_text(json.dumps(extraction, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    journal_append(root, slug, "scan", "duplicate" if duplicates else "ok",
                   digests=digests, warnings=warnings or None)

    print(f"{slug}: разобрано, файлов {len(files)}, формат {files[0]['format']}")
    for warning in warnings:
        print(f"  ПРЕДУПРЕЖДЕНИЕ  {warning}")
    print(f"  предложение: {proposal_file.relative_to(root)}")
    return 0


def validate_record(root: pathlib.Path, record: dict) -> list[str]:
    """Проверка одной записи по схеме документа — без обхода всего набора.

    Схемы ссылаются друг на друга по абсолютным $id, поэтому нужен реестр:
    без него jsonschema полез бы за fact.schema.json в сеть.
    """
    import jsonschema
    from referencing import Registry, Resource

    schemas = {}
    for path in sorted((root / "data/schemas").glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if "$id" in schema:
            schemas[schema["$id"]] = schema
    registry = Registry().with_resources(
        [(sid, Resource.from_contents(s)) for sid, s in schemas.items()])
    validator = jsonschema.Draft202012Validator(
        schemas["https://vov-365sd.local/schemas/document.schema.json"], registry=registry)
    errors = []
    try:
        for error in validator.iter_errors({"documents": [record]}):
            where = "/".join(str(part) for part in error.absolute_path) or "(корень)"
            errors.append(f"{record.get('id', '?')}: {where}: {error.message}")
    except Exception as exc:  # ссылка на несуществующую схему и т.п.
        errors.append(f"схема документа не проверяется: {str(exc)[:160]}")
    return errors


def cmd_apply(args) -> int:
    root = pathlib.Path(args.root)
    slug = args.slug
    proposal_file = root / PROPOSALS / slug / "extraction.json"
    if not proposal_file.exists():
        raise IntakeError(f"нет предложения для {slug}; сначала python3 tools/intake.py scan {slug}")
    proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
    manifest = read_manifest(root, slug)
    if proposal.get("duplicates") and not args.force:
        raise IntakeError("артефакт помечен как дубликат; применение — только с --force и после ручной сверки")

    record = proposal["suggested_document"]
    documents_path = root / "data/documents.json"
    documents = json.loads(documents_path.read_text(encoding="utf-8"))
    if any(d["id"] == record["id"] for d in documents["documents"]):
        journal_append(root, slug, "apply", "already", document=record["id"])
        print(f"{slug}: уже применено (документ {record['id']})")
        return 0

    vault = pathlib.Path(os.path.expanduser(args.vault)) if args.vault else None
    if vault is None:
        raise IntakeError("укажи --vault <путь к Obsidian vault>: данные не должны ссылаться "
                          "на файл, которого нет в хранилище")
    location = manifest.get("original_location") or ""
    if not location or "TODO" in location:
        raise IntakeError("в манифесте не заполнено original_location (папка в Obsidian)")

    source_file = root / INBOX / slug / manifest["files"][0]
    target_dir = vault / "Хобби/365сд/Исходники" / location
    target = target_dir / source_file.name
    copied_now = False
    if not target.exists():
        if args.dry_run:
            print(f"[dry-run] скопировал бы {source_file.name} → {target_dir}")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            copied_now = True
            if sha256(target) != sha256(source_file):
                target.unlink()
                raise IntakeError("sha256 копии не совпал с исходником — копия удалена")
    record["carrier"]["file_path"] = str(target.relative_to(vault))
    record["carrier"]["bytes"] = target.stat().st_size if target.exists() else record["carrier"].get("bytes")

    if args.dry_run:
        print(f"[dry-run] документ {record['id']} был бы добавлен в data/documents.json")
        print(json.dumps(record, ensure_ascii=False, indent=1))
        return 0

    errors = validate_record(root, record)
    if errors:
        if copied_now and target.exists():
            target.unlink()
        journal_append(root, slug, "apply", "error", document=record["id"], problems=errors[:5])
        print("ОТКАТ: документ не прошёл схему")
        for err in errors[:5]:
            print(f"  {err}")
        return 1

    documents["documents"].append(record)
    documents["documents"].sort(key=lambda d: d["id"])
    documents_path.write_text(json.dumps(documents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    journal_append(root, slug, "apply", "applied", document=record["id"],
                   file_path=record["carrier"]["file_path"])
    print(f"{slug}: применено, документ {record['id']} → {record['carrier']['file_path']}")
    print("дальше: python3 tools/build.py   (артефакты пересоберутся с новой версией ядра)")
    return 0


def cmd_reject(args) -> int:
    root = pathlib.Path(args.root)
    journal_append(root, args.slug, "reject", "rejected", reason=args.reason)
    print(f"{args.slug}: отклонён ({args.reason})")
    return 0


def main(argv: list[str] | None = None) -> int:
    import datetime as dt
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=ROOT_DEFAULT)
    sub = parser.add_subparsers(dest="command", required=True)

    drop = sub.add_parser("drop", help="положить артефакт в инбокс")
    drop.add_argument("files", nargs="+")
    drop.add_argument("--slug", required=True)
    drop.add_argument("--kind", required=True, choices=KINDS)
    drop.add_argument("--source", required=True, help="id источника из data/sources.json")
    drop.add_argument("--title")
    drop.add_argument("--url")
    drop.add_argument("--location", help="папка в Obsidian, где лежит оригинал")
    drop.add_argument("--document-type", choices=("combat-order", "combat-journal", "operational-summary",
                                                  "loss-report", "name-list", "map", "other",
                                                  "memorial-card"))
    drop.add_argument("--comment")
    drop.add_argument("--received", default=dt.date.today().isoformat())
    drop.add_argument("--force", action="store_true")
    drop.set_defaults(func=cmd_drop)

    status = sub.add_parser("status", help="состояние инбокса")
    status.set_defaults(func=cmd_status)

    scan = sub.add_parser("scan", help="офлайн-разбор (без сети и моделей)")
    scan.add_argument("slug", nargs="*")
    scan.set_defaults(func=cmd_scan)

    apply_cmd = sub.add_parser("apply", help="перенести файлы в Obsidian и внести документ")
    apply_cmd.add_argument("slug")
    apply_cmd.add_argument("--vault", default=os.path.expanduser("~/Documents/Obsidian Vault"))
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("--force", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    reject = sub.add_parser("reject", help="отклонить артефакт с причиной")
    reject.add_argument("slug")
    reject.add_argument("--reason", required=True)
    reject.set_defaults(func=cmd_reject)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except IntakeError as exc:
        print(f"ОШИБКА  {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
