#!/usr/bin/env python3
"""Черновая вытяжка линии фронта с листа Lage Ost по цвету.

  python3 tools/front_line_extract.py --image "лист.jpg" --calib "обводка.json" \
      --date 1941-12-06 --sheet ca000124 [--dry-run]

Нужна только калибровка: две точки с координатами (их даёт
tools/trace_front_line.html). Дальше скрипт сам находит красную линию фронта,
упорядочивает её по оси X и сохраняет как линию в data/front_lines/1941.json.

Ограничения честные: метод работает, когда линия на участке однозначна по X и
не перекрыта подписями. Результат помечается как черновик с точностью 10 км —
его надо смотреть глазами и при необходимости править обводкой вручную.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DEFAULT, "tools"))
from front_line_trace import similarity  # noqa: E402


def red_mask(arr: np.ndarray) -> np.ndarray:
    R = arr[:, :, 0].astype(np.int16)
    G = arr[:, :, 1].astype(np.int16)
    B = arr[:, :, 2].astype(np.int16)
    return (R > 110) & (R - G > 35) & (R - B > 35)


def polyline_by_columns(mask: np.ndarray, bins: int, min_pixels: int = 6) -> list[dict]:
    """Медиана Y красных пикселей в каждой колонке — грубая, но устойчивая ось линии."""
    h, w = mask.shape
    points = []
    step = max(1, w // bins)
    for x0 in range(0, w, step):
        block = mask[:, x0:x0 + step]
        ys = np.nonzero(block)[0]
        if ys.size < min_pixels:
            continue
        points.append({"x": float(x0 + step / 2), "y": float(np.median(ys))})
    return points


def simplify(points: list[dict], eps_px: float) -> list[dict]:
    """Дуглас–Пекер: убирает дрожание, оставляя форму."""
    if len(points) < 3:
        return points

    def rdp(pts):
        if len(pts) < 3:
            return pts
        x1, y1 = pts[0]["x"], pts[0]["y"]
        x2, y2 = pts[-1]["x"], pts[-1]["y"]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        worst, index = 0.0, 0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]["x"] - x1, pts[i]["y"] - y1
            dist = abs(px * dy - py * dx) / norm
            if dist > worst:
                worst, index = dist, i
        if worst > eps_px:
            left = rdp(pts[:index + 1])
            right = rdp(pts[index:])
            return left[:-1] + right
        return [pts[0], pts[-1]]

    return rdp(points)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--image", required=True)
    ap.add_argument("--calib", required=True, help="JSON из tools/trace_front_line.html (нужны только калибровочные точки)")
    ap.add_argument("--date", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--source", default="src-loc-lage-ost")
    ap.add_argument("--bins", type=int, default=60)
    ap.add_argument("--eps-px", type=float, default=8.0)
    ap.add_argument("--accuracy-km", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    calibration = json.load(open(args.calib, encoding="utf-8")).get("calibration") or {}
    a, b = calibration.get("A"), calibration.get("B")
    if not a or not b:
        raise SystemExit("в файле калибровки нет точек A и B")

    image = Image.open(args.image).convert("RGB")
    mask = red_mask(np.asarray(image))
    if mask.mean() < 0.0005:
        raise SystemExit("красных пикселей почти нет — линия другого цвета или скан бледный")

    points = polyline_by_columns(mask, args.bins)
    if len(points) < 5:
        raise SystemExit(f"точек линии получилось {len(points)} — слишком мало, нужна ручная обводка")
    points = simplify(points, args.eps_px)

    coords, residual = similarity(points, a, b)
    line = {
        "id": f"fl-{args.date}",
        "date": {
            "value": {"from": args.date, "to": args.date, "precision": "day"},
            "provenance": {"source_id": args.source,
                           "locator": {"sheet": args.sheet, "sheet_date": args.date,
                                       "quote": f"Lage Ost, лист {args.sheet}, вечер {args.date}"}},
            "status": "черновик",
        },
        "geometry": {"type": "LineString", "coordinates": coords},
        "accuracy_km": args.accuracy_km,
        "accuracy_level": "sheet-1m",
        "provenance": {"source_id": args.source,
                       "locator": {"sheet": args.sheet, "sheet_date": args.date},
                       "traced_by": "автотрассировка по цвету + калибровка по двум точкам"},
        "note": (f"ЧЕРНОВИК: вытянуто по красной линии на листе {args.sheet}, "
                 f"остаток калибровки {residual / 1000:.1f} км, точек {len(coords)}. "
                 "Требует просмотра: подписи и врезки могли разорвать линию."),
    }

    path = os.path.join(args.root, "data/front_lines/1941.json")
    registry = json.load(open(path, encoding="utf-8"))
    registry["front_lines"] = sorted(
        [x for x in registry.get("front_lines", []) if x["id"] != line["id"]] + [line],
        key=lambda x: x["id"],
    )
    if not args.dry_run:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    print(f"{line['id']}: точек {len(coords)}, остаток калибровки {residual / 1000:.1f} км")
    print("записано" if not args.dry_run else "(dry-run: не записано)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
