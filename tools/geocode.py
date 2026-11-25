#!/usr/bin/env python3
"""Геокодирование мест через Nominatim (OSM) с кэшем и журналом.

Единственное место в проекте, которое ходит в сеть. Проверки и сборка сети не касаются.

  python3 tools/geocode.py --all              # дозаполнить кэш для мест без координат
  python3 tools/geocode.py --all --offline    # только кэш, без сети
  python3 tools/geocode.py --all --apply      # перенести результат в data/places.json
  python3 tools/geocode.py --journal-only     # перегенерировать журнал из кэша

Кандидат принимается только если совпало название и район/область из поля `region`;
иначе место остаётся `unfound` с объяснением. Данные © OpenStreetMap contributors, ODbL 1.0.
Ограничение частоты: не чаще 1 запроса в секунду.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_AGENT = "vov-365sd/0.1 (family history research project)"
JOURNAL_PATH = "docs/sources/недат_Геокодирование Nominatim — журнал запросов.md"
CACHE_DIR = "data/cache/geocode"
SOURCE_ID = "src-osm-nominatim"
MIN_INTERVAL = 1.1
PREFIXES = ("малая ", "большая ", "ново-", "ново ", "старо-", "старо ")


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def slug_of(place_id: str) -> str:
    return place_id.removeprefix("pl-")


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("—", "-").strip()


def _strip_prefix(text: str) -> str:
    for prefix in PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def name_matches(place: dict, display_name: str) -> bool:
    """Только точное совпадение названия: «Малая Борщевка» — не «Борщёвка».

    Названия-двойники (малая/большая, ново-, старо-) попадают в `names.alternate`
    осознанно, а не подставляются автоматически.
    """
    first = _normalize(display_name.split(",")[0])
    variants = {_normalize(place["names"]["modern"])}
    variants |= {_normalize(item) for item in place["names"].get("alternate", [])}
    return first in variants


def region_matches(place: dict, display_name: str) -> bool:
    display = _normalize(display_name)
    for part in place["region"].split(","):
        part = part.strip()
        if not part:
            continue
        if _normalize(part)[:6] not in display:
            return False
    return True


def accept(place: dict, result: dict) -> tuple[bool, str]:
    display = result.get("display_name", "")
    if not name_matches(place, display):
        return False, "название не совпадает"
    if not region_matches(place, display):
        return False, "район/область не совпадает"
    return True, ""


def queries_for(place: dict) -> list[str]:
    name = place["names"]["modern"]
    primary = f"{name}, {place['region']}, Россия"
    broad = f"{name}, Россия"
    return [primary, broad] if primary != broad else [primary]


def cache_path(root: str, place: dict) -> str:
    return os.path.join(root, CACHE_DIR, f"{slug_of(place['id'])}.json")


def fetch(query: str) -> dict:
    params = {"format": "jsonv2", "limit": "3", "q": query, "accept-language": "ru"}
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))
    keep = {
        "place_id", "osm_type", "osm_id", "lat", "lon", "type", "category",
        "display_name", "importance", "boundingbox", "licence",
    }
    results = [{k: item.get(k) for k in keep if k in item} for item in raw]
    return {
        "query": query,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }


def ensure_cache(root: str, places: list[dict], offline: bool, verbose: bool = True) -> list[str]:
    """Дозаполняет кэш (по попытке на каждый вариант запроса). Возвращает места без кэша при offline."""
    missing: list[str] = []
    requests_made = 0
    for place in places:
        path = cache_path(root, place)
        if os.path.exists(path):
            continue
        if offline:
            missing.append(place["id"])
            continue
        attempts: list[dict] = []
        accepted_at: int | None = None
        for query in queries_for(place):
            if verbose:
                print(f"запрос: {query}")
            if requests_made:
                time.sleep(MIN_INTERVAL)
            payload = fetch(query)
            attempts.append(payload)
            requests_made += 1
            if any(accept(place, item)[0] for item in payload["results"]):
                accepted_at = len(attempts) - 1
                break
        _write_json(
            path,
            {
                "place_id": place["id"],
                "queries": queries_for(place),
                "attempts": attempts,
                "accepted_attempt": accepted_at,
            },
        )
    return missing


def _accuracy_for(osm_type: str) -> tuple[str, str]:
    if osm_type in {"locality", "abandoned_place"}:
        return "approximate", "tract"
    return "settlement", "found"


def apply_results(root: str, places: list[dict]) -> list[dict]:
    registry = _read_json(os.path.join(root, "data/registry.json"))
    bbox = registry.get("bbox")
    out: list[dict] = []
    for place in places:
        path = cache_path(root, place)
        if not os.path.exists(path):
            out.append(place)
            continue
        cached = _read_json(path)
        chosen = None
        rejected: list[str] = []
        for attempt in cached.get("attempts", []):
            for item in attempt.get("results", []):
                ok, reason = accept(place, item)
                if ok:
                    chosen = (attempt["query"], item)
                    break
                rejected.append(f"{item.get('display_name', '?')} — {reason}")
            if chosen:
                break

        place = dict(place)
        if chosen is None:
            found_any = any(a.get("results") for a in cached.get("attempts", []))
            place["state"] = "unfound"
            place["accuracy"] = "approximate"
            place["coords"] = None
            if found_any:
                place["note"] = (
                    "геокодер вернул объекты, но ни один не совпал по названию и району: "
                    + "; ".join(rejected[:3])
                )
            else:
                place["note"] = (
                    "геокодер не нашёл место; координаты появятся после геопривязки исторических карт"
                )
            out.append(place)
            continue

        query, best = chosen
        lat, lon = float(best["lat"]), float(best["lon"])
        if bbox and not (bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]):
            place["state"] = "unfound"
            place["coords"] = None
            place["note"] = f"координаты ({lat}, {lon}) вне ожидаемого района — не приняты"
            out.append(place)
            continue

        accuracy, state = _accuracy_for(best.get("type", ""))
        place["state"] = state
        place["accuracy"] = accuracy
        place["coords"] = {
            "value": {"lon": round(lon, 6), "lat": round(lat, 6)},
            "provenance": {
                "source_id": SOURCE_ID,
                "locator": {"paragraph": f"geo-{slug_of(place['id'])}"},
            },
            "status": "черновик",
        }
        place["note"] = f"принят кандидат по запросу «{query}»: {best.get('display_name', '')}"
        out.append(place)
    return out


def render_journal(root: str, places: list[dict]) -> str:
    lines = [
        "<!-- СГЕНЕРИРОВАНО tools/geocode.py. Не редактировать вручную: изменения будут перезаписаны. -->",
        "",
        "# Геокодирование мест — журнал запросов",
        "",
        "Источник координат: OpenStreetMap / Nominatim. Данные © OpenStreetMap contributors, ODbL 1.0.",
        "Ответы кэшируются в `data/cache/geocode/`, поэтому журнал и координаты воспроизводимы без сети.",
        "Кандидат принимается только при совпадении названия и района/области из реестра мест.",
        "",
    ]
    for place in sorted(places, key=lambda p: p["id"]):
        cache = os.path.join(root, CACHE_DIR, f"{slug_of(place['id'])}.json")
        entry_id = f"geo-{slug_of(place['id'])}"
        lines.append(f"## {entry_id} — {place['names']['modern']}")
        lines.append("")
        lines.append(f"- район по реестру: {place['region']}")
        if not os.path.exists(cache):
            lines.append("- запрос: не выполнялся")
            lines.append("- кэш: отсутствует")
            lines.append("")
            continue
        cached = _read_json(cache)
        for attempt in cached.get("attempts", []):
            lines.append(f"- запрос: `{attempt.get('query', '')}`")
            lines.append(f"- получено: {attempt.get('fetched_at', '?')}")
            results = attempt.get("results") or []
            lines.append(f"- кандидатов: {len(results)}")
            for index, item in enumerate(results[:3], start=1):
                ok, reason = accept(place, item)
                verdict = "принят" if ok else f"отклонён: {reason}"
                lines.append(
                    f"  - {index}. {item.get('display_name', '?')} "
                    f"({item.get('lat')}, {item.get('lon')}, {item.get('type')}) — {verdict}"
                )
        coords = place.get("coords")
        if isinstance(coords, dict) and coords.get("value"):
            lines.append(
                f"- в реестре: {coords['value']['lat']}, {coords['value']['lon']} "
                f"(точность `{place['accuracy']}`, состояние `{place['state']}`)"
            )
        else:
            lines.append(f"- в реестре: координат нет (состояние `{place['state']}`)")
        lines.append(f"- кэш: `{CACHE_DIR}/{slug_of(place['id'])}.json`")
        lines.append("")
    lines += [
        "## Атрибуция",
        "",
        "Координаты получены из OpenStreetMap через Nominatim и используются по лицензии ODbL 1.0;",
        "выдержки ответов хранятся в кэше как есть, без редактирования значений.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=ROOT_DEFAULT)
    parser.add_argument("--all", action="store_true", help="все места без координат")
    parser.add_argument("--offline", action="store_true", help="не ходить в сеть, только кэш")
    parser.add_argument("--apply", action="store_true", help="записать результат в data/places.json")
    parser.add_argument("--journal-only", action="store_true", help="только перегенерировать журнал")
    args = parser.parse_args(argv)

    payload = _read_json(os.path.join(args.root, "data/places.json"))
    places = payload["places"]
    targets = [p for p in places if not p.get("coords")] if args.all else []

    if not args.journal_only:
        missing = ensure_cache(args.root, targets, args.offline)
        if missing:
            print("ошибка: кэш отсутствует для мест: " + ", ".join(missing))
            print("запустите без --offline, чтобы заполнить кэш из Nominatim")
            return 1
        if not targets:
            print("нечего геокодировать: у всех мест есть координаты")

    if args.apply:
        payload["places"] = apply_results(args.root, places)
        _write_json(os.path.join(args.root, "data/places.json"), payload)
        found = sum(1 for p in payload["places"] if p.get("coords"))
        print(f"data/places.json обновлён: мест {len(payload['places'])}, с координатами {found}")

    journal = os.path.join(args.root, JOURNAL_PATH)
    os.makedirs(os.path.dirname(journal), exist_ok=True)
    with open(journal, "w", encoding="utf-8") as fh:
        fh.write(render_journal(args.root, payload["places"]))
    print(f"журнал: {JOURNAL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
