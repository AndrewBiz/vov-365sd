#!/usr/bin/env python3
"""Расчёт промежуточных положений частей для анимации (производная величина).

Наблюдения лежат в `data/positions/`; здесь из них строятся траектории: между
соседними по времени положениями одного формирования добавляются расчётные точки.
Каждая расчётная вершина помечена: `derived`, `between` (два наблюдения), `method`,
`speed_km_day`. В исходные данные расчёт не попадает никогда.

Правила (см. openspec/changes/add-unit-positions/design.md):
  * интерполируются только соседние наблюдения одного формирования;
  * если скорость выше верхней границы типа (реестр `movement_rates`) — сегмент
    не интерполируется, в отчёт идёт предупреждение;
  * разрыв больше 10 суток не заполняется;
  * путь — прямая между центрами геометрий: дороги и рельеф не моделируются.

Запуск:
    python3 tools/trajectories.py                # пересчитать и записать артефакты
    python3 tools/trajectories.py --check        # ничего не писать, только проверка
    python3 tools/trajectories.py --step-hours 3 --max-gap-days 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EARTH_KM = 6371.0088


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def centroid(geometry: dict) -> tuple[float, float] | None:
    """Центр геометрии: точка, средняя по вершинам линия, центр полигона."""
    if not geometry:
        return None
    coords = geometry.get("coordinates") or []
    kind = geometry.get("type")
    if kind == "Point" and len(coords) >= 2:
        return (float(coords[0]), float(coords[1]))
    if kind == "LineString":
        pts = coords
    elif kind == "Polygon":
        pts = [c for ring in coords for c in ring]
    else:
        return None
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def moment(as_of: dict) -> dt.datetime:
    date = dt.date.fromisoformat(as_of["date"])
    time = as_of.get("time") or "00:00"
    hour, minute = (int(x) for x in time.split(":"))
    return dt.datetime.combine(date, dt.time(hour, minute))


def rate_for(unit: dict, rates: dict, mapping: dict, override: str | None) -> tuple[str, float]:
    """Тип перемещения и верхняя скорость для формирования."""
    name = override or mapping.get(unit.get("kind", ""), "infantry")
    if name not in rates:
        name = "infantry"
    return name, float(rates[name]["km_per_day"])


def build(root: pathlib.Path, step_hours: int, max_gap_days: float,
          override: str | None) -> tuple[dict, list[str]]:
    registry = load(root / "data/registry.json")
    rates = registry.get("movement_rates") or {}
    mapping = registry.get("movement_by_unit_kind") or {}
    units = {u["id"]: u for u in load(root / "data/units.json")["units"]}
    places = {p["id"]: p for p in load(root / "data/places.json")["places"]}

    observations: list[dict] = []
    for path in sorted((root / "data/positions").glob("*.json")):
        for pos in load(path).get("positions") or []:
            observations.append(pos)

    def point_of(pos: dict) -> tuple[float, float] | None:
        c = centroid(pos.get("geometry") or {})
        if c:
            return c
        place = places.get(pos.get("place") or "")
        coords = (place or {}).get("coords")
        if isinstance(coords, dict) and coords.get("value"):
            return (coords["value"]["lon"], coords["value"]["lat"])
        return None

    by_unit: dict[str, list[dict]] = {}
    for pos in observations:
        by_unit.setdefault(pos["unit"], []).append(pos)

    warnings: list[str] = []
    features: list[dict] = []

    for unit_id, items in sorted(by_unit.items()):
        items.sort(key=lambda p: moment(p["as_of"]))
        unit = units.get(unit_id, {"kind": ""})
        rate_name, rate_km_day = rate_for(unit, rates, mapping, override)

        for pos in items:
            pt = point_of(pos)
            features.append({
                "type": "Feature",
                "properties": {
                    "unit": unit_id, "pos_id": pos["id"], "state": "observed",
                    "kind": pos.get("kind"), "basis": pos.get("basis"),
                    "as_of": pos["as_of"], "label": pos.get("label"),
                    "accuracy_m": pos.get("accuracy_m"),
                },
                "geometry": ({"type": "Point", "coordinates": pt} if pt else None),
            })
            if pt is None:
                warnings.append(f"{pos['id']}: нет координат (пункт без привязки) — в анимацию не попадёт")

        for left, right in zip(items, items[1:]):
            a, b = point_of(left), point_of(right)
            if not a or not b:
                warnings.append(f"{left['id']} → {right['id']}: нет координат у одного из положений, переход не считается")
                continue
            t0, t1 = moment(left["as_of"]), moment(right["as_of"])
            hours = (t1 - t0).total_seconds() / 3600
            if hours <= 0:
                warnings.append(f"{left['id']} → {right['id']}: время не растёт, переход не считается")
                continue
            days = hours / 24
            if days > max_gap_days:
                warnings.append(
                    f"{left['id']} → {right['id']}: разрыв {days:.1f} сут > {max_gap_days:g} — не интерполируется")
                continue
            dist = haversine_km(a, b)
            speed = dist / days
            if speed > rate_km_day:
                warnings.append(
                    f"{left['id']} → {right['id']}: неправдоподобно быстро — {dist:.0f} км за {hours:.0f} ч "
                    f"({speed:.0f} км/сут > {rate_name} {rate_km_day:.0f}); сегмент не интерполируется")
                continue

            steps = max(1, int(round(hours / step_hours)))
            for i in range(1, steps):
                frac = i / steps
                lon = a[0] + (b[0] - a[0]) * frac
                lat = a[1] + (b[1] - a[1]) * frac
                at = t0 + dt.timedelta(hours=(t1 - t0).total_seconds() / 3600 * frac)
                features.append({
                    "type": "Feature",
                    "properties": {
                        "unit": unit_id, "state": "derived", "derived": True,
                        "between": [left["id"], right["id"]], "method": "linear",
                        "speed_km_day": round(speed, 1), "movement": rate_name,
                        "as_of": {"date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                                  "precision": "timestamp"},
                    },
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                })

    collection = {
        "type": "FeatureCollection",
        "name": "trajectories",
        "note": ("Производная величина: features со state=derived рассчитаны линейно между "
                 "наблюдениями и не являются источником. В data/positions их нет."),
        "features": features,
    }
    return collection, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=ROOT_DEFAULT)
    parser.add_argument("--step-hours", type=int, default=6)
    parser.add_argument("--max-gap-days", type=float, default=10.0)
    parser.add_argument("--rate", help="тип перемещения для всех частей (иначе по реестру)")
    parser.add_argument("--check", action="store_true", help="не писать артефакт, только отчёт")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    collection, warnings = build(root, args.step_hours, args.max_gap_days, args.rate)
    observed = sum(1 for f in collection["features"] if f["properties"]["state"] == "observed")
    derived = len(collection["features"]) - observed

    if not args.quiet:
        print(f"наблюдений: {observed}, расчётных точек: {derived}")
        for warning in warnings:
            print(f"  ПРЕДУПРЕЖДЕНИЕ  {warning}")

    if args.check:
        return 0 if not warnings else 0

    out = root / "data/dist/trajectories.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(collection, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not args.quiet:
        print(f"записано: {os.path.relpath(out, root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
