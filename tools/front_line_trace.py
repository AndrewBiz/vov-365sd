#!/usr/bin/env python3
"""Перевод обводки линии фронта из пикселей листа в координаты.

  python3 tools/front_line_trace.py --input pixels.json --date 1941-12-06 \
      --sheet ca000124 --sheet-date 1941-12-06 --accuracy-km 10 [--dry-run]

Вход — JSON из tools/trace_front_line.html: пиксели вдоль линии фронта и две
калибровочные точки с известными координатами. Преобразование — подобие
(сдвиг, поворот, масштаб), потому что лист 1:1 000 000 снят в одной проекции,
а две точки дают ровно четыре параметра.

Инструмент не додумывает: если калибровки нет или остаток больше допуска,
он отказывается писать данные.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINES_PATH = "data/front_lines/1941.json"


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def similarity(points: list[dict], a: dict, b: dict) -> tuple[list[list[float]], float]:
    """Пиксели → (lon, lat) через подобие; возвращает координаты и остаток калибровки, м."""
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    dlon, dlat = b["lon"] - a["lon"], b["lat"] - a["lat"]
    dist_px = math.hypot(dx, dy)
    if dist_px == 0 or (dlon == 0 and dlat == 0):
        raise SystemExit("калибровочные точки совпадают — преобразование невозможно")
    kx = math.cos(math.radians(a["lat"])) * 111320.0
    dist_m = math.hypot(dlon * kx, dlat * 110570.0)
    scale = dist_m / dist_px             # метров на пиксель
    theta = math.atan2(dlat * 110570.0, dlon * kx) - math.atan2(dy, dx)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    coords = []
    for p in points:
        px, py = p["x"] - a["x"], p["y"] - a["y"]
        east = scale * (px * cos_t - py * sin_t)
        north = scale * (px * sin_t + py * cos_t)
        lon = a["lon"] + east / kx
        lat = a["lat"] + north / 110570.0
        coords.append([round(lon, 5), round(lat, 5)])

    # остаток: пересчитываем точку B и смотрим, на сколько ошиблись
    px, py = b["x"] - a["x"], b["y"] - a["y"]
    east = scale * (px * cos_t - py * sin_t)
    north = scale * (px * sin_t + py * cos_t)
    lon_b = a["lon"] + east / kx
    lat_b = a["lat"] + north / 110570.0
    residual = math.hypot((lon_b - b["lon"]) * kx, (lat_b - b["lat"]) * 110570.0)
    return coords, residual


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--input", required=True, help="JSON от tools/trace_front_line.html")
    ap.add_argument("--date", required=True, help="дата обстановки, ГГГГ-ММ-ДД")
    ap.add_argument("--sheet", required=True, help="какой лист обводили, например ca000124")
    ap.add_argument("--sheet-date", required=True, help="дата листа, ГГГГ-ММ-ДД")
    ap.add_argument("--source", default="src-loc-lage-ost")
    ap.add_argument("--accuracy-km", type=float, default=10.0)
    ap.add_argument("--level", default="sheet-1m")
    ap.add_argument("--traced-by", default="вручную по скану листа")
    ap.add_argument("--max-residual-m", type=float, default=15000.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    payload = load(args.input)
    cal = payload.get("calibration") or {}
    a, b = cal.get("A"), cal.get("B")
    if not a or not b:
        raise SystemExit("в файле нет двух калибровочных точек — обводка не привязана к координатам")
    points = payload.get("pixels") or []
    if len(points) < 2:
        raise SystemExit(f"точек в обводке {len(points)} — меньше двух")

    coords, residual = similarity(points, a, b)
    if residual > args.max_residual_m:
        raise SystemExit(
            f"остаток калибровки {residual / 1000:.1f} км больше допуска "
            f"{args.max_residual_m / 1000:.1f} км — проверь точки A и B"
        )

    line = {
        "id": f"fl-{args.date}",
        "date": {
            "value": {"from": args.date, "to": args.date, "precision": "day"},
            "provenance": {
                "source_id": args.source,
                "locator": {"sheet": args.sheet, "sheet_date": args.sheet_date,
                            "quote": f"Lage Ost, лист {args.sheet}, обстановка на вечер {args.sheet_date}"},
            },
            "status": "черновик",
        },
        "geometry": {"type": "LineString", "coordinates": coords},
        "accuracy_km": args.accuracy_km,
        "accuracy_level": args.level,
        "provenance": {
            "source_id": args.source,
            "locator": {"sheet": args.sheet, "sheet_date": args.sheet_date},
            "traced_by": args.traced_by,
        },
        "note": f"оцифровано по листу {args.sheet}; остаток калибровки {residual / 1000:.1f} км, точек {len(coords)}",
    }

    path = os.path.join(args.root, LINES_PATH)
    registry = load(os.path.join(args.root, LINES_PATH))
    lines = [x for x in registry.get("front_lines", []) if x["id"] != line["id"]]
    lines.append(line)
    registry["front_lines"] = sorted(lines, key=lambda x: x["id"])
    if not args.dry_run:
        save(path, registry)

    print(f"линия {line['id']}: точек {len(coords)}, остаток калибровки {residual / 1000:.1f} км")
    print("записано" if not args.dry_run else "(dry-run: не записано)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
