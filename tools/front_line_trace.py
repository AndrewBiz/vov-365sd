#!/usr/bin/env python3
"""Перевод обводки линии фронта из пикселей листа в координаты.

  python3 tools/front_line_trace.py --input pixels.json --date 1941-12-06 \
      --sheet ca000124 --sheet-date 1941-12-06 --accuracy-km 10 [--dry-run]

Вход — JSON из tools/trace_front_line.html: пиксели вдоль линии фронта и
калибровочные точки с известными координатами.

Две точки — подобие (сдвиг, поворот, масштаб), ровно четыре параметра.
Три и больше — аффинное преобразование по методу наименьших квадратов: оно
использует все точки и компенсирует перекос скана, а по остаткам видно, какая
подпись опознана неверно.

Инструмент не додумывает: без двух точек или при больших остатках он
отказывается писать данные.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINES_PATH = "data/front_lines/1941.json"

M_PER_DEG_LAT = 110570.0
M_PER_DEG_LON_EQ = 111320.0


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def collect_points(payload: dict) -> list[dict]:
    """Калибровка: список произвольной длины либо пара A/B в старом формате."""
    points = payload.get("calibration_points")
    if points:
        return [p for p in points
                if all(p.get(k) is not None for k in ("x", "y", "lon", "lat"))]
    cal = payload.get("calibration") or {}
    return [p for p in (cal.get("A"), cal.get("B")) if p]


def similarity(points: list[dict], a: dict, b: dict) -> tuple[list[list[float]], float]:
    """Пиксели → (lon, lat) через подобие; возвращает координаты и остаток калибровки, м."""
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    dlon, dlat = b["lon"] - a["lon"], b["lat"] - a["lat"]
    dist_px = math.hypot(dx, dy)
    if dist_px == 0 or (dlon == 0 and dlat == 0):
        raise SystemExit("калибровочные точки совпадают — преобразование невозможно")
    kx = math.cos(math.radians(a["lat"])) * M_PER_DEG_LON_EQ
    dist_m = math.hypot(dlon * kx, dlat * M_PER_DEG_LAT)
    scale = dist_m / dist_px
    theta = math.atan2(dlat * M_PER_DEG_LAT, dlon * kx) - math.atan2(dy, dx)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def to_lonlat(px: float, py: float) -> tuple[float, float]:
        rx, ry = px - a["x"], py - a["y"]
        east = scale * (rx * cos_t - ry * sin_t)
        north = scale * (rx * sin_t + ry * cos_t)
        return a["lon"] + east / kx, a["lat"] + north / M_PER_DEG_LAT

    coords = [[round(lon, 5), round(lat, 5)]
              for lon, lat in (to_lonlat(p["x"], p["y"]) for p in points)]
    lon_b, lat_b = to_lonlat(b["x"], b["y"])
    residual = math.hypot((lon_b - b["lon"]) * kx, (lat_b - b["lat"]) * M_PER_DEG_LAT)
    return coords, residual


def solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Решение СЛАУ методом Гаусса с выбором главного элемента."""
    n = len(matrix)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise SystemExit("калибровка вырождена: точки совпадают или лежат на одной прямой")
        a[col], a[pivot] = a[pivot], a[col]
        lead = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= lead
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if factor:
                for j in range(col, n + 1):
                    a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def affine(points: list[dict], calib: list[dict]) -> tuple[list[list[float]], dict]:
    """Аффинная подгонка по всем точкам; координаты и отчёт по остаткам."""
    if len(calib) < 3:
        raise SystemExit("для аффинной подгонки нужно не меньше трёх точек")
    lon0 = sum(c["lon"] for c in calib) / len(calib)
    lat0 = sum(c["lat"] for c in calib) / len(calib)
    kx = math.cos(math.radians(lat0)) * M_PER_DEG_LON_EQ

    sxx = sum(c["x"] * c["x"] for c in calib)
    sxy = sum(c["x"] * c["y"] for c in calib)
    syy = sum(c["y"] * c["y"] for c in calib)
    sx = sum(c["x"] for c in calib)
    sy = sum(c["y"] for c in calib)
    n = len(calib)
    m = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]

    tx = [(c["lon"] - lon0) * kx for c in calib]
    ty = [(c["lat"] - lat0) * M_PER_DEG_LAT for c in calib]
    vx = [sum(c["x"] * t for c, t in zip(calib, tx)),
          sum(c["y"] * t for c, t in zip(calib, tx)),
          sum(tx)]
    vy = [sum(c["x"] * t for c, t in zip(calib, ty)),
          sum(c["y"] * t for c, t in zip(calib, ty)),
          sum(ty)]
    ax, ay, az = solve_linear(m, vx)
    bx, by, bz = solve_linear(m, vy)

    def to_lonlat(px: float, py: float) -> tuple[float, float]:
        east = ax * px + ay * py + az
        north = bx * px + by * py + bz
        return lon0 + east / kx, lat0 + north / M_PER_DEG_LAT

    residuals = []
    for c in calib:
        lon, lat = to_lonlat(c["x"], c["y"])
        d = math.hypot((lon - c["lon"]) * kx, (lat - c["lat"]) * M_PER_DEG_LAT)
        residuals.append({"label": c.get("label") or f"({c['x']:.0f},{c['y']:.0f})",
                          "residual_m": round(d)})

    rms = math.sqrt(sum(r["residual_m"] ** 2 for r in residuals) / len(residuals))
    coords = [[round(lon, 5), round(lat, 5)]
              for lon, lat in (to_lonlat(p["x"], p["y"]) for p in points)]
    return coords, {"rms_m": round(rms), "max_m": max(r["residual_m"] for r in residuals),
                    "points": residuals}


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
    calib = collect_points(payload)
    if len(calib) < 2:
        raise SystemExit("нужно не меньше двух калибровочных точек")
    points = payload.get("pixels") or []
    if len(points) < 2:
        raise SystemExit(f"точек в обводке {len(points)} — меньше двух")

    if len(calib) == 2:
        coords, residual = similarity(points, calib[0], calib[1])
        if residual > args.max_residual_m:
            raise SystemExit(
                f"остаток калибровки {residual / 1000:.1f} км больше допуска "
                f"{args.max_residual_m / 1000:.1f} км — проверь точки"
            )
        report = f"подобие по двум точкам, остаток {residual / 1000:.1f} км"
    else:
        coords, stats = affine(points, calib)
        print(f"аффинная подгонка по {len(calib)} точкам: "
              f"RMS {stats['rms_m'] / 1000:.1f} км, максимум {stats['max_m'] / 1000:.1f} км")
        worst = max(stats["points"], key=lambda r: r["residual_m"])
        if worst["residual_m"] > 500:
            print(f"  хуже всех: {worst['label']} — {worst['residual_m'] / 1000:.1f} км: "
                  "вероятно, подпись опознана неверно")
        if stats["rms_m"] > args.max_residual_m:
            raise SystemExit(
                f"средний остаток {stats['rms_m'] / 1000:.1f} км больше допуска "
                f"{args.max_residual_m / 1000:.1f} км — калибровка не годится"
            )
        report = (f"аффинная подгонка по {len(calib)} точкам, "
                  f"RMS {stats['rms_m'] / 1000:.1f} км, максимум {stats['max_m'] / 1000:.1f} км")

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
        "note": f"оцифровано по листу {args.sheet}: {report}, точек {len(coords)}",
    }

    path = os.path.join(args.root, LINES_PATH)
    registry = load(path)
    registry["front_lines"] = sorted(
        [x for x in registry.get("front_lines", []) if x["id"] != line["id"]] + [line],
        key=lambda x: x["id"],
    )
    if not args.dry_run:
        save(path, registry)

    print(f"линия {line['id']}: точек {len(coords)}")
    print("записано" if not args.dry_run else "(dry-run: не записано)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
