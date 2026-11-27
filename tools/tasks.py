#!/usr/bin/env python3
"""Задачи движка для исследователя — обратный канал к инбоксу.

Движок сам говорит, что нужно добыть: привязать лист, снять координаты пунктов,
обвести полосы. Задача — не пожелание, а контракт: зачем, что именно, чем,
что ждём во входящих и по чему она закроется.

    python3 tools/tasks.py sync            # породить задачи из состояния данных
    python3 tools/tasks.py sync --dry-run  # показать, что появится
    python3 tools/tasks.py list            # сводка
    python3 tools/tasks.py close <id>      # закрыть (только при наличии файлов)
    python3 tools/tasks.py check           # контракт задач: обязательные поля

Задачи лежат в `tasks/` (репозиторий) и зеркалятся в Obsidian
`Хобби/365сд/Задачи для исследователя`. Статусы вне `data/`: это состояние
работы, а не свидетельство истории.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys

import yaml

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBSIDIAN_DEFAULT = os.path.expanduser("~/Documents/Obsidian Vault/Хобби/365сд/Задачи для исследователя")
INBOX = "Хобби/365сд/Входящие для разбора"

REQUIRED_FIELDS = ("id", "kind", "status", "priority", "created", "about",
                   "tool", "estimate_min", "expects", "done_when")


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(text: str, limit: int = 44) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return base[:limit].strip("-") or "sheet"


def sheet_key(path: str) -> str:
    """Короткий ключ листа: из имени файла, без серии и расширения."""
    name = os.path.splitext(os.path.basename(path))[0]
    if "100K_" in name:
        name = name.split("100K_", 1)[1]
    name = name.replace("_Ost", "").replace("_", "-")
    return slug(name, 40)


def short_key(path: str) -> str:
    """Человеческий ключ листа для имён файлов: «klin-vi-1942», «rshew-ix-1942»."""
    name = os.path.splitext(os.path.basename(path))[0]
    if "100K_" in name:
        name = name.split("100K_", 1)[1]
    parts = [part for part in name.replace("_Ost", "").split("_") if part]
    return slug("-".join(parts[-2:]), 28)


def read_task(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: нет YAML-шапки")
    _, head, body = text.split("---\n", 2)
    data = yaml.safe_load(head) or {}
    data["_body"] = body.lstrip("\n")
    data["_path"] = str(path)
    return data


def render_task(task: dict) -> str:
    head = {key: value for key, value in task.items()
            if not key.startswith("_") and key != "body"}
    text = yaml.safe_dump(head, allow_unicode=True, sort_keys=False, width=100)
    body = task.get("body") or task.get("_body") or ""
    return f"---\n{text}---\n\n{body.strip()}\n"


def all_tasks(root: pathlib.Path) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    for path in sorted((root / "tasks").glob("task-*.md")):
        try:
            task = read_task(path)
        except Exception as exc:  # noqa: BLE001 — битый файл не должен ронять остальные
            print(f"  ПРЕДУПРЕЖДЕНИЕ  {exc}")
            continue
        tasks[task["id"]] = task
    return tasks


# ---------------------------------------------------------------- состояние

def calibration_state(root: pathlib.Path) -> dict[tuple[str, str], dict]:
    path = root / "data/calibration/index.json"
    if not path.exists():
        return {}
    data = load(path)
    return {(sheet["source_id"], sheet["file"]): sheet for sheet in data.get("sheets", [])}


def positions_and_events(root: pathlib.Path) -> tuple[list[dict], list[dict]]:
    positions: list[dict] = []
    for path in sorted((root / "data/positions").glob("*.json")):
        positions.extend(load(path).get("positions") or [])
    events: list[dict] = []
    for path in sorted((root / "data/events").glob("*.json")):
        events.extend(load(path).get("events") or [])
    return positions, events


# ---------------------------------------------------------------- генераторы

def georef_task(source: dict, file_entry: dict, priority: str, today: str) -> dict:
    fname = os.path.basename(file_entry["path"])
    key = sheet_key(file_entry["path"])
    tid = f"task-georef-{source['id'][4:]}-{key}"
    human = short_key(file_entry["path"])
    points = f"{INBOX}/{human}.points"
    tif = f"{INBOX}/{human}.tif"
    body = f"""# Привязать лист к координатам

## Зачем

Пока лист не привязан, с него нельзя снять ни одной координаты: ни деревни, ни полосы
частей. Все положения дивизии за этот период остаются текстом без геометрии, а значит
не попадают ни на карту, ни в анимацию. Это первая задача цепочки — остальные ждут её.

## Что именно

- Лист: `{fname}`
- Источник: `{source['id']}` — {source.get('title', '')}
- Требуемая точность привязки: **среднеквадратичная ошибка ≤ 250 м** (масштаб 1:100 000;
  лучше 100–150 м). Больше 250 м — привязку принимать нельзя: на карте это уже полкилометра,
  то есть соседняя деревня.

## Чем и как

QGIS (уже установлен), модуль **Растр → Georeferencer**.

1. Открыть растр: `{fname}` из хранилища.
2. Опорные точки — **только координатная сетка** (на листе есть градусная рамка с
   подписями широты и долготы). Внутренние ориентиры — перекрёстки, церкви — не годятся:
   их координат у нас нет, и тогда точка опоры превращается в догадку.
3. Взять **6–8 точек**. Если градусная сетка прочерчена по полю — брать её пересечения
   (четыре ближних к углам и две-три в середине листа). Если прочерчена только рамка —
   брать выходы меридианов и параллелей на рамку: четыре угла рамки плюс по одной-две
   точки на каждую сторону, чтобы преобразование держалось не только на краях.
   Подписи на рамке читаются: на угле листа видно `37° 00′ ostw. Greenw.`
4. Преобразование — **аффинное (первого порядка)**. Полином второго порядка на один
   ровный скан не брать: степеней свободы больше, чем смысла, остатки «улучшаются» обманом.
5. Выход: GeoTIFF, сжатие LZW, EPSG:3857 (Web Mercator — как у подложки).
6. Сохранить файл точек (`.points`) отдельно: по нему видно, какими ориентирами привязан лист.
7. Проверка: открыть привязанный лист поверх подложки OSM и убедиться, что сетка и
   характерные дороги совпадают; расхождение по краям тоже посмотреть — аффинное
   преобразование не исправляет перспективу, и это нормально, если оно меньше 250 м.

## Что ждём во входящих

`{points}` — файл точек Georeferencer
`{tif}` — привязанный GeoTIFF (LZW, EPSG:3857)

Положить в `{INBOX}`. Имя — как указано: по нему задача и закрывается.

## Когда закрыто

- в `data/calibration/index.json` у листа стоит `status: georef`, `gcp` и `rms_m`;
- `python3 tools/validate.py` и `python3 tools/build.py --check` зелёные.

Если лист не привязывается по градусной рамке (подписи не читаются, рамка обрезана) —
написать об этом в задаче и не подставлять координаты по памяти: тогда нужен другой
экземпляр листа, и это отдельная задача.
"""
    return {
        "id": tid, "kind": "georeference-sheet", "status": "open", "priority": priority,
        "created": today,
        "about": {"source_id": source["id"], "file": file_entry["path"]},
        "tool": "qgis-georeferencer", "estimate_min": 60, "depends_on": [],
        "expects": [{"path": points, "why": "GCP: чем привязан лист", "required": True},
                    {"path": tif, "why": "привязанный GeoTIFF — основа для съёмки координат", "required": True}],
        "done_when": ["в data/calibration/index.json status=georef с rms_m",
                      "validate и build --check зелёные"],
        "body": body,
    }


def places_task(source: dict, sheet: dict, places: list[dict], georef_id: str, today: str) -> dict:
    key = sheet_key(sheet["file"])
    human = short_key(sheet["file"])
    tid = f"task-places-{source['id'][4:]}-{key}"
    rows = "\n".join(f"| `{p['id']}` | {p['names']['modern']} | `{p.get('region', '—')}` | | |"
                     for p in places)
    geo = f"{INBOX}/places-{human}.geojson"
    body = f"""# Снять координаты населённых пунктов с привязанного листа

## Зачем

Эти пункты названы в оперсводках как места частей, но у них нет координат: строгий
геокодер их не нашёл (деревни переименованы или исчезли). Пока координат нет, положения
частей остаются строкой без геометрии — на карту не ложатся, анимация не строится.
Геокодер ослаблять нельзя: «похожее» село в другом районе — это ложный факт.

## Что именно

Координаты **центра населённого пункта**, допуск **≤ 250 м**. Если пункт на листе
подписан, но название читается иначе (старая орфография, немецкая транслитерация) —
ставить точку и писать в графе «что видно на листе» написание с листа.

| Пункт | Название | Район (по документу) | Широта | Долгота |
|---|---|---|---|---|
{rows}

## Чем и как

QGIS, привязанный лист из предыдущей задачи (`{georef_id}`).

1. Открыть GeoTIFF листа, включить подложку OSM для контроля.
2. Создать слой `Точки` (GeoJSON, EPSG:3857) с полями `place_id`, `name`, `note`.
3. Поставить точку в центр деревни; `place_id` — строго из таблицы выше.
4. Если пункт на листе **не подписан** — не ставить точку «примерно там»: написать в
   `note` «нет на листе» и оставить пункт незакрытым. Это нормальный результат, а не провал.
5. Экспорт слоя.

## Что ждём во входящих

`{geo}` — слой точек в EPSG:3857 (QGIS перепроецирует при экспорте, если попросить WGS84 —
тоже годится, поле `place_id` обязательно).

Положить в `{INBOX}`.

## Когда закрыто

- в `data/places.json` у этих пунктов `state: found`, координаты с провенансом «снято с листа»;
- `python3 tools/validate.py` зелёный;
- `python3 tools/trajectories.py` больше не пишет «нет координат» по этим положениям.
"""
    return {
        "id": tid, "kind": "digitize-places", "status": "open", "priority": "high",
        "created": today,
        "about": {"source_id": source["id"], "file": sheet["file"],
                  "place_ids": [p["id"] for p in places]},
        "tool": "qgis", "estimate_min": 45, "depends_on": [georef_id],
        "expects": [{"path": geo, "why": "координаты шести пунктов", "required": True}],
        "done_when": ["data/places.json: state=found с провенансом «снято с листа»",
                      "validate зелёный",
                      "trajectories не сообщает «нет координат» по этим положениям"],
        "body": body,
    }


def generate(root: pathlib.Path, today: str) -> list[dict]:
    sources = {s["id"]: s for s in load(root / "data/sources.json")["sources"]}
    places = {p["id"]: p for p in load(root / "data/places.json")["places"]}
    positions, events = positions_and_events(root)
    calibration = calibration_state(root)

    wanted_places: dict[str, set[str]] = {}
    for pos in positions:
        if pos.get("place") and not pos.get("geometry"):
            wanted_places.setdefault(pos["place"], set()).add("positions")
    for event in events:
        for place_id in event.get("places") or []:
            wanted_places.setdefault(place_id, set()).add("events")

    tasks: list[dict] = []
    georef_ids: dict[str, str] = {}  # ключ — путь к файлу листа

    for source_id, source in sorted(sources.items()):
        if source.get("kind") not in {"map", "historical-map"}:
            continue
        for file_entry in source.get("files") or []:
            state = calibration.get((source_id, file_entry["path"])) or {}
            if state.get("status") in {"georef", "checked"}:
                georef_ids[file_entry["path"]] = state.get("task_id") or ""
                continue
            priority = "high" if source_id == "src-mapywig-100k" and "Klin" in file_entry["path"] else "normal"
            task = georef_task(source, file_entry, priority, today)
            tasks.append(task)
            georef_ids[file_entry["path"]] = task["id"]

    missing = sorted(pid for pid in wanted_places
                     if pid in places and places[pid].get("state") == "unfound")
    if missing:
        pairs = [(s, f) for s in sources.values() if s.get("kind") in {"map", "historical-map"}
                 for f in (s.get("files") or [])]
        if pairs:
            # Для декабря 1941 подходит лист Клина: Высоковск, Ямуга, Селинское — его район.
            target, sheet_file = next(((s, f) for s, f in pairs if "Klin" in f["path"]), pairs[0])
            georef_id = georef_ids.get(sheet_file["path"], "")
            tasks.append(places_task(target, {"source_id": target["id"], "file": sheet_file["path"]},
                                     [places[pid] for pid in missing], georef_id, today))

    return tasks


# ---------------------------------------------------------------- запись

def sync(root: pathlib.Path, obsidian: pathlib.Path, today: str, dry_run: bool) -> int:
    existing = all_tasks(root)
    created, skipped = 0, 0
    generated = generate(root, today)
    (root / "tasks").mkdir(exist_ok=True)

    for task in generated:
        old = existing.get(task["id"])
        if old:
            if old.get("status") in {"done", "dropped"}:
                skipped += 1
                continue
            if old.get("status") == "in-work":
                skipped += 1
                print(f"  пропуск (in-work, есть заметки человека): {task['id']}")
                continue
            skipped += 1
            continue
        path = root / "tasks" / f"{task['id']}.md"
        if dry_run:
            print(f"  появится: {path.name} — {task['about']}")
        else:
            path.write_text(render_task(task), encoding="utf-8")
        created += 1
        existing[task["id"]] = task

    index = {"updated": today,
             "tasks": [{key: task.get(key) for key in
                        ("id", "kind", "status", "priority", "created", "depends_on", "about")}
                       for task in sorted(existing.values(), key=lambda t: (t["status"] != "open", t["id"]))]}
    if not dry_run:
        (root / "tasks/index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        obsidian.mkdir(parents=True, exist_ok=True)
        for task in existing.values():
            (obsidian / f"{task['id']}.md").write_text(render_task(task), encoding="utf-8")
        # зеркало не должно жить своей жизнью: убрать файлы, которых нет в репозитории
        for stale in obsidian.glob("task-*.md"):
            if stale.stem not in existing:
                stale.unlink()
                print(f"  убрано из зеркала: {stale.name}")

    print(f"задач: {len(existing)} (создано {created}, без изменений {skipped})")
    return 0


def list_tasks(root: pathlib.Path) -> int:
    tasks = all_tasks(root)
    if not tasks:
        print("задач нет — запустить sync")
        return 0
    for status in ("open", "in-work", "done", "dropped"):
        group = [t for t in tasks.values() if t.get("status") == status]
        if not group:
            continue
        print(f"\n{status} ({len(group)}):")
        for task in sorted(group, key=lambda t: (t.get("priority") != "high", t["id"])):
            deps = f" ← {', '.join(task['depends_on'])}" if task.get("depends_on") else ""
            print(f"  [{task.get('priority','normal'):6}] {task['id']}{deps}")
    return 0


def check(root: pathlib.Path) -> int:
    tasks = all_tasks(root)
    bad = 0
    for task in tasks.values():
        missing = [field for field in REQUIRED_FIELDS if task.get(field) in (None, "", [], {})]
        if missing:
            print(f"ОШИБКА  {task['id']}: нет полей {', '.join(missing)}")
            bad += 1
        if not task.get("expects"):
            print(f"ОШИБКА  {task['id']}: задача без ожидаемых файлов не может быть закрыта")
            bad += 1
        for dep in task.get("depends_on") or []:
            if dep and dep not in tasks:
                print(f"ОШИБКА  {task['id']}: зависимость '{dep}' не найдена")
                bad += 1
    print(f"задач: {len(tasks)}, нарушений: {bad}")
    return 1 if bad else 0


def find_expected(root: pathlib.Path, task: dict) -> tuple[list[str], list[str]]:
    found, missing = [], []
    vault = pathlib.Path.home() / "Documents/Obsidian Vault"
    for item in task.get("expects") or []:
        if not item.get("required", True):
            continue
        rel = item["path"]
        if (vault / rel).exists() or (root / rel).exists():
            found.append(rel)
        else:
            missing.append(rel)
    return found, missing


def close(root: pathlib.Path, task_id: str, force: bool = False) -> int:
    tasks = all_tasks(root)
    task = tasks.get(task_id)
    if not task:
        print(f"задача '{task_id}' не найдена")
        return 1
    found, missing = find_expected(root, task)
    if missing and not force:
        print("закрытие отклонено — нет ожидаемых файлов:")
        for path in missing:
            print(f"  нет: {path}")
        print("файл без приёма не закрывает задачу: положить во входящие и провести приём")
        return 1
    task["status"] = "done"
    task["closed"] = dt.date.today().isoformat()
    (root / "tasks" / f"{task_id}.md").write_text(render_task(task), encoding="utf-8")
    print(f"закрыто: {task_id} (файлов найдено: {len(found)}, отсутствует: {len(missing)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["sync", "list", "close", "check"])
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--root", default=ROOT_DEFAULT)
    parser.add_argument("--obsidian", default=OBSIDIAN_DEFAULT)
    parser.add_argument("--date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="закрыть без файлов (не рекомендуется)")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    today = args.date or dt.date.today().isoformat()
    if args.command == "sync":
        return sync(root, pathlib.Path(args.obsidian), today, args.dry_run)
    if args.command == "list":
        return list_tasks(root)
    if args.command == "check":
        return check(root)
    if not args.task_id:
        print("нужен id задачи")
        return 1
    return close(root, args.task_id, args.force)


if __name__ == "__main__":
    sys.exit(main())
