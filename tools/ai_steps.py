#!/usr/bin/env python3
"""Шаги с моделью: предложения вместо правок.

  python3 tools/ai_steps.py propose <slug> --step extract-facts
  python3 tools/ai_steps.py propose <slug> --step extract-facts --emit-request
  python3 tools/ai_steps.py propose <slug> --step extract-facts --ingest answer.json
  python3 tools/ai_steps.py review <slug>
  python3 tools/ai_steps.py apply-proposal <slug> --step extract-facts --min-confidence 0.7

Модель не пишет в данные. Она отдаёт позиции с цитатами и уверенностью; они
проверяются (цитата обязана найтись во входном тексте, место — в справочнике,
дата — в допустимом виде) и только потом применяются отдельной командой.

Если модель не настроена (VOV_AI_BASE_URL/VOV_AI_API_KEY/VOV_AI_MODEL), propose
пишет бандл запроса в data/proposals/<slug>/<step>-request.json: его можно отдать
любой модели, а ответ забрать через --ingest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import unicodedata

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DEFAULT, "tools"))

import coremeta  # noqa: E402
import intake  # noqa: E402

PROPOSALS = "data/proposals"
SKILLS = "tools/skills"
OCR_CACHE = "data/cache/ocr"


class AiStepError(RuntimeError):
    pass


# ---------------------------------------------------------------- скилы

def _yaml():
    import yaml
    return yaml


def load_skill(root: pathlib.Path, name: str) -> dict:
    path = root / SKILLS / f"{name}.yaml"
    if not path.exists():
        known = sorted(p.stem for p in (root / SKILLS).glob("*.yaml"))
        raise AiStepError(f"нет скила «{name}»; есть: {', '.join(known)}")
    skill = _yaml().safe_load(path.read_text(encoding="utf-8"))
    for key in ("name", "input", "output", "prompt"):
        if not skill.get(key):
            raise AiStepError(f"{path.name}: нет обязательного поля «{key}»")
    if skill["name"] != name:
        raise AiStepError(f"{path.name}: name «{skill['name']}» не совпадает с именем файла")
    if skill["input"].get("kind") not in {"text", "image"}:
        raise AiStepError(f"{path.name}: input.kind должен быть text или image")
    return skill


def skill_names(root: pathlib.Path) -> list[str]:
    return sorted(p.stem for p in (root / SKILLS).glob("*.yaml"))


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- вход шага

def artifact_text(root: pathlib.Path, slug: str, manifest: dict) -> tuple[str, str]:
    """Текст листа: из кэша OCR, иначе распознаётся заново и кладётся в предложение."""
    cache = root / OCR_CACHE
    document_id = intake.derive_document_id(manifest)
    for candidate in sorted(cache.glob(f"{document_id}_psm6_x2.txt")):
        return candidate.read_text(encoding="utf-8"), f"кэш OCR {candidate.name}"
    if document_id.startswith("doc-pn-"):
        for candidate in sorted(cache.glob("*.txt")):
            if document_id in candidate.name:
                return candidate.read_text(encoding="utf-8"), f"кэш OCR {candidate.name}"

    source = root / intake.INBOX / slug / manifest["files"][0]
    reading = intake.readability(source)
    if not reading or reading.get("readability_pct") is None:
        raise AiStepError("текста нет: скан не распознан (проверь tesseract и tessdata)")
    import ocr_scan
    text = ocr_scan.ocr_page(source, psm=6, scale=2.0,
                             tessdata=os.path.expanduser("~/.hermes/tools/tessdata"))
    target = root / PROPOSALS / slug
    target.mkdir(parents=True, exist_ok=True)
    (target / "ocr.txt").write_text(text + "\n", encoding="utf-8")
    return text, "OCR выполнен сейчас"


def build_request(skill: dict, meta: dict, text: str | None, image: pathlib.Path | None) -> dict:
    if skill["input"]["kind"] == "text":
        limit = int(skill["input"].get("max_chars", 20000))
        body = (text or "")[:limit]
        prompt = skill["prompt"].replace("{meta}", json.dumps(meta, ensure_ascii=False, indent=1))
        prompt = prompt.replace("{text}", body)
        return {"messages": [{"role": "user", "content": prompt}],
                "temperature": skill.get("model", {}).get("temperature", 0),
                "response_format": {"type": "json_object"}}
    import base64
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    return {"messages": [{"role": "user", "content": [
                {"type": "text", "text": skill["prompt"]},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}]}],
            "temperature": skill.get("model", {}).get("temperature", 0),
            "response_format": {"type": "json_object"}}


# ---------------------------------------------------------------- модель

def ai_endpoint() -> tuple[str, str, str] | None:
    base = os.environ.get("VOV_AI_BASE_URL")
    key = os.environ.get("VOV_AI_API_KEY")
    model = os.environ.get("VOV_AI_MODEL")
    if base and key and model:
        return base.rstrip("/"), key, model
    return None


def call_model(endpoint: tuple[str, str, str], request: dict) -> tuple[str, str]:
    import urllib.request
    base, key, model = endpoint
    payload = dict(request, model=model)
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return content, model


# ---------------------------------------------------------------- проверки предложения

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().lower()


def load_json_loose(raw: str) -> dict:
    """Ответ модели может прийти в ```json-блоке или с преамбулой."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise AiStepError("ответ модели не разобрался как JSON")


def validate_output(root: pathlib.Path, skill: dict, payload: dict) -> list[str]:
    import jsonschema
    problems = []
    validator = jsonschema.Draft202012Validator(skill["output"])
    for error in validator.iter_errors(payload):
        where = "/".join(str(part) for part in error.absolute_path) or "(корень)"
        problems.append(f"{where}: {error.message}")
    return problems


def places_index(root: pathlib.Path) -> dict[str, str]:
    places = json.loads((root / "data/places.json").read_text(encoding="utf-8"))["places"]
    index: dict[str, str] = {}
    for place in places:
        for value in (place.get("names") or {}).values():
            if isinstance(value, str) and value.strip():
                index.setdefault(normalize(value), place["id"])
    return index


def check_items(root: pathlib.Path, skill: dict, payload: dict, text: str) -> list[dict]:
    """Каждая позиция получает вердикт: цитата найдена? место? дубликат?"""
    index = places_index(root)
    haystack = normalize(text)
    events = []
    for path in sorted((root / "data/events").glob("*.json")):
        events += json.loads(path.read_text(encoding="utf-8"))["events"]
    existing = {(e["period"]["value"].get("from"), normalize(e["title"]["value"])) for e in events
                if (e.get("period") or {}).get("value")}

    checked = []
    for position, item in enumerate(payload.get("items") or [], start=1):
        verdict = {"n": position, "item": item, "problems": []}
        quote = item.get("quote") or ""
        if quote and normalize(quote) not in haystack:
            verdict["problems"].append("цитата не найдена во входном тексте")
        place_name = item.get("place_name")
        if place_name:
            place_id = index.get(normalize(place_name))
            verdict["place_id"] = place_id
            if not place_id:
                verdict["problems"].append(f"места «{place_name}» нет в справочнике")
        if not item.get("date_from"):
            verdict["problems"].append("нет даты")
        key = (item.get("date_from"), normalize(item.get("summary") or ""))
        if key in existing:
            verdict["problems"].append("похожее событие уже есть в базе")
        if (item.get("confidence") or 0) < 0.6:
            verdict["problems"].append("низкая уверенность модели")
        verdict["acceptable"] = not verdict["problems"]
        checked.append(verdict)
    return checked


# ---------------------------------------------------------------- команды

def proposal_dir(root: pathlib.Path, slug: str) -> pathlib.Path:
    path = root / PROPOSALS / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_propose(args) -> int:
    root = pathlib.Path(args.root)
    slug = args.slug
    skill = load_skill(root, args.step)
    manifest = intake.read_manifest(root, slug)
    problems = intake.validate_manifest(root, manifest, slug)
    if problems:
        raise AiStepError("манифест не готов: " + "; ".join(problems))

    meta = {"slug": slug, "title": manifest.get("title"), "unit": manifest.get("unit"),
            "date": (manifest.get("date") or {}).get("from"),
            "requisites": manifest.get("requisites")}
    text = None
    image = None
    source_note = ""
    if skill["input"]["kind"] == "text":
        text, source_note = artifact_text(root, slug, manifest)
    else:
        image = root / intake.INBOX / slug / manifest["files"][0]
        source_note = f"изображение {image.name}"

    request = build_request(skill, meta, text, image)
    target = proposal_dir(root, slug)
    digest = prompt_hash(str(request))

    endpoint = ai_endpoint()
    if args.ingest:
        raw = pathlib.Path(args.ingest).read_text(encoding="utf-8")
        model = args.model_name or "external"
    elif endpoint and not args.emit_request:
        raw, model = call_model(endpoint, request)
    else:
        raw = None
        model = None

    if raw is None:
        bundle = {"skill": skill["name"], "prompt_sha": digest, "source": source_note,
                  "request": request}
        path = target / f"{skill['name']}-request.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{slug}: модель не настроена — записан бандл запроса {path.relative_to(root)}")
        print("  ответ положи в файл и подай: --ingest <файл>")
        intake.journal_append(root, slug, f"propose:{skill['name']}", "request",
                              prompt_sha=digest, source=source_note)
        return 0

    payload = load_json_loose(raw)
    problems = validate_output(root, skill, payload)
    if problems:
        raise AiStepError("ответ не соответствует контракту скила: " + "; ".join(problems[:5]))

    checked = check_items(root, skill, payload, text or "") if skill["input"]["kind"] == "text" else []
    result = {"slug": slug, "skill": skill["name"], "model": args.model_name or model,
              "prompt_sha": digest, "source": source_note,
              "core_version": coremeta.version_or_default(),
              "payload": payload, "checked": checked}
    path = target / f"{skill['name']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    good = sum(1 for c in checked if c["acceptable"])
    print(f"{slug}: предложение {path.relative_to(root)} — позиций {len(checked)}, "
          f"пригодных {good}" if checked else f"{slug}: предложение {path.relative_to(root)}")
    intake.journal_append(root, slug, f"propose:{skill['name']}", "proposed",
                          prompt_sha=digest, model=result["model"], items=len(checked) or None)
    return 0


def cmd_review(args) -> int:
    root = pathlib.Path(args.root)
    path = root / PROPOSALS / args.slug / f"{args.step}.json"
    if not path.exists():
        raise AiStepError(f"нет предложения: {path.relative_to(root)}")
    result = json.loads(path.read_text(encoding="utf-8"))
    checked = result.get("checked") or []
    if not checked:
        print(json.dumps(result.get("payload"), ensure_ascii=False, indent=1)[:2000])
        return 0
    print(f"{args.slug} · {result['skill']} · модель {result.get('model')} · "
          f"prompt {result.get('prompt_sha')}")
    for verdict in checked:
        item = verdict["item"]
        mark = "ОК " if verdict["acceptable"] else "НЕТ"
        print(f"\n[{mark}] {verdict['n']}. {item.get('kind')} · {item.get('date_from') or '— б/д'} · "
              f"уверенность {item.get('confidence')}")
        print(f"      {item.get('summary')}")
        if item.get("place_name"):
            print(f"      место: {item['place_name']} → {verdict.get('place_id') or 'нет в справочнике'}")
        for problem in verdict["problems"]:
            print(f"      ! {problem}")
        print(f"      цитата: «{(item.get('quote') or '')[:160]}»")
    accepted = sum(1 for v in checked if v["acceptable"])
    print(f"\nитого: {len(checked)}, пригодных {accepted}, отклонено {len(checked) - accepted}")
    return 0 if accepted else 1


def cmd_apply(args) -> int:
    root = pathlib.Path(args.root)
    path = root / PROPOSALS / args.slug / f"{args.step}.json"
    if not path.exists():
        raise AiStepError(f"нет предложения: {path.relative_to(root)}")
    result = json.loads(path.read_text(encoding="utf-8"))
    manifest = intake.read_manifest(root, args.slug)

    chosen = []
    skipped = []
    for verdict in result.get("checked") or []:
        item = verdict["item"]
        if (item.get("confidence") or 0) < args.min_confidence:
            skipped.append((item.get("summary"), f"уверенность ниже {args.min_confidence}"))
            continue
        if not verdict.get("acceptable"):
            skipped.append((item.get("summary"), "; ".join(verdict["problems"])))
            continue
        chosen.append((verdict, item))

    if not chosen:
        print(f"{args.slug}: нечего применять (пригодных позиций нет)")
        for summary, reason in skipped:
            print(f"  пропущено: {summary} — {reason}")
        return 1

    source_id = manifest["source_id"]
    locator = {}
    origin = manifest.get("origin") or {}
    if origin.get("portal_url"):
        locator["url"] = origin["portal_url"]

    events_paths = {}
    added = []
    for verdict, item in chosen:
        date_from = item["date_from"]
        year = date_from[:4]
        events_path = root / f"data/events/{year}.json"
        if not events_path.exists():
            skipped.append((item.get("summary"), f"нет файла событий за {year} год"))
            continue
        payload = events_paths.setdefault(events_path, json.loads(events_path.read_text(encoding="utf-8")))
        event_id = "ev-{}-ai-{}".format(
            date_from, hashlib.sha1(normalize(item["summary"]).encode("utf-8")).hexdigest()[:6])
        if any(e["id"] == event_id for e in payload["events"]):
            skipped.append((item.get("summary"), "уже применено"))
            continue
        skill_locator = dict(locator)
        skill_locator["quote"] = item["quote"]
        period = {"value": {"from": date_from, "to": item.get("date_to") or date_from,
                            "precision": item.get("precision") or ("range" if item.get("date_to") else "day")},
                  "provenance": {"source_id": source_id, "locator": skill_locator},
                  "status": "черновик"}
        event = {
            "id": event_id,
            "type": item.get("event_type") or item.get("kind") or "combat",
            "title": {"value": item["summary"],
                      "provenance": {"source_id": source_id, "locator": skill_locator},
                      "status": "черновик"},
            "units": item.get("units") or ["365sd"],
            "period": period,
            "localization": "approximate",
            "place": verdict.get("place_id"),
            "documents": [intake.derive_document_id(manifest)] if any(
                d["id"] == intake.derive_document_id(manifest)
                for d in json.loads((root / "data/documents.json").read_text(encoding="utf-8"))["documents"]) else [],
            "note": f"предложение скила {result['skill']}, модель {result.get('model')}, "
                    f"уверенность {item.get('confidence')}; проверено человеком",
        }
        payload["events"].append(event)
        payload["events"].sort(key=lambda e: (((e.get("period") or {}).get("value") or {}).get("from") or "", e["id"]))
        added.append(event_id)

    if args.dry_run:
        print(f"[dry-run] добавил бы событий: {len(added)}")
        for event_id in added:
            print(f"  {event_id}")
        return 0

    backups = {}
    for events_path, payload in events_paths.items():
        backups[events_path] = events_path.read_text(encoding="utf-8")
        events_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    import validate as vov
    errors = vov.run(str(root), None)
    if errors:
        for events_path, backup in backups.items():
            events_path.write_text(backup, encoding="utf-8")
        intake.journal_append(root, args.slug, f"apply-proposal:{args.step}", "error",
                             events=added, problems=errors[:5])
        print("ОТКАТ: данные не прошли проверку")
        for error in errors[:5]:
            print(f"  {error}")
        return 1

    intake.journal_append(root, args.slug, f"apply-proposal:{args.step}", "applied", events=added)
    print(f"{args.slug}: добавлено событий {len(added)}")
    for event_id in added:
        print(f"  {event_id}")
    for summary, reason in skipped:
        print(f"  пропущено: {summary} — {reason}")
    print("дальше: python3 tools/build.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=ROOT_DEFAULT)
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose", help="получить предложение от модели по скилу")
    propose.add_argument("slug")
    propose.add_argument("--step", required=True, help="имя скила из tools/skills/")
    propose.add_argument("--emit-request", action="store_true", help="только записать бандл запроса")
    propose.add_argument("--ingest", help="JSON-ответ модели из файла")
    propose.add_argument("--model-name", help="какой моделью получен ответ (для журнала)")
    propose.set_defaults(func=cmd_propose)

    review = sub.add_parser("review", help="разобрать предложение построчно")
    review.add_argument("slug")
    review.add_argument("--step", default="extract-facts")
    review.set_defaults(func=cmd_review)

    apply_cmd = sub.add_parser("apply-proposal", help="применить пригодные позиции в события")
    apply_cmd.add_argument("slug")
    apply_cmd.add_argument("--step", default="extract-facts")
    apply_cmd.add_argument("--min-confidence", type=float, default=0.7)
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AiStepError as exc:
        print(f"ОШИБКА  {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
