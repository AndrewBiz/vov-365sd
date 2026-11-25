#!/usr/bin/env python3
"""Черновая вытяжка линии фронта с листа Lage Ost по цвету.

  python3 tools/front_line_extract.py --image "лист.jpg" --calib "пиксели.json" \
      --date 1941-12-06 --sheet ca000124 --region 1500,4800,2700,6000 \
      --preview /tmp/line.png [--dry-run]

Нужна только калибровка (её даёт tools/trace_front_line.html). Скрипт ищет
красную линию фронта, идёт по колонкам слева направо и в каждой берёт красный
штрих, ближайший к предыдущему: так линия не перескакивает на соседний участок
фронта, символы и надписи.

--region обязателен для нашего участка: на листе весь Восточный фронт, и
колоночный проход без ограничения собирает прыгающую ломаную по Ленинграду,
Туле и Курску одновременно.

Результат — ЧЕРНОВИК: точность привязки не лучше масштаба листа, а разрывы
(подписи, врезки) могли быть пройдены по прямой. --preview сохраняет картинку с
наложенной линией, её надо посмотреть глазами.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DEFAULT, "tools"))
from front_line_trace import affine, collect_points, similarity  # noqa: E402


def color_mask(arr: np.ndarray, color: str) -> np.ndarray:
    """Маска цвета линии.

    На листах Lage Ost линия фронта нанесена тёмно-синим, а не красным, — но тем
    же тёмно-синим подписаны номера немецких соединений. Цвет поэтому не отделяет
    линию от подписей: маска честно выделяет и то и другое, дальше нужен глаз.
    Красный вариант оставлен для листов другой серии.
    """
    R = arr[:, :, 0].astype(np.int16)
    G = arr[:, :, 1].astype(np.int16)
    B = arr[:, :, 2].astype(np.int16)
    if color == "red":
        return (R > 105) & (R - G > 32) & (R - B > 32)
    V = arr.max(axis=2).astype(np.int16)
    return (V < 175) & (B - R > 10) & (B > 55)


def runs(column: np.ndarray, min_len: int = 3) -> list[tuple[float, int]]:
    """Непрерывные красные отрезки в колонке: (центр, длина)."""
    idx = np.nonzero(column)[0]
    if idx.size == 0:
        return []
    breaks = np.nonzero(np.diff(idx) > 2)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks + 1, [idx.size]))
    out = []
    for s, e in zip(starts, ends):
        length = e - s
        if length >= min_len:
            out.append(((idx[s] + idx[e - 1]) / 2.0, int(length)))
    return out


def trace_columns(mask: np.ndarray, step: int = 4, max_jump: float = 90.0) -> list[dict]:
    """Слева направо, в каждой колонке — красный штрих, ближайший к предыдущему."""
    h, w = mask.shape
    points: list[dict] = []
    prev = None
    for x in range(0, w, step):
        found = runs(mask[:, x])
        if not found:
            continue
        center: float
        if prev is None:
            # старт: самый длинный штрих в первой непустой колонке
            center = max(found, key=lambda r: r[1])[0]
        else:
            assert prev is not None
            center, length = min(found, key=lambda r: abs(r[0] - prev))
            if abs(center - prev) > max_jump:
                continue
        points.append({"x": float(x), "y": float(center)})
        prev = center
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


def write_preview(image: Image.Image, region: tuple[int, int, int, int],
                  points: list[dict], path: str, max_side: int = 1400) -> None:
    x0, y0, x1, y1 = region
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    draw = ImageDraw.Draw(crop)
    if len(points) > 1:
        draw.line([(p["x"] - x0, p["y"] - y0) for p in points], fill=(0, 200, 255), width=3)
    for p in points[::10]:
        draw.ellipse([p["x"] - x0 - 3, p["y"] - y0 - 3, p["x"] - x0 + 3, p["y"] - y0 + 3],
                     outline=(0, 200, 255))
    scale = min(1.0, max_side / max(crop.size))
    if scale < 1.0:
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)),
                           Image.Resampling.LANCZOS)
    crop.save(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--image", required=True)
    ap.add_argument("--calib", required=True, help="JSON из tools/trace_front_line.html")
    ap.add_argument("--date", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--region", help="окно вытяжки в пикселях листа: x0,y0,x1,y1")
    ap.add_argument("--source", default="src-loc-lage-ost")
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--eps-px", type=float, default=6.0)
    ap.add_argument("--max-jump-px", type=float, default=90.0)
    ap.add_argument("--color", choices=("blue", "red"), default="blue",
                    help="цвет линии на листе: blue (Lage Ost) или red (другая серия)")
    ap.add_argument("--accept", action="store_true",
                    help="разрешить запись в данные; без флага работает как --dry-run: "
                         "цвет не отделяет линию от подписей, результат надо смотреть глазами")
    ap.add_argument("--accuracy-km", type=float, default=10.0)
    ap.add_argument("--preview", help="куда сохранить картинку с наложенной линией")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    calib = collect_points(json.load(open(args.calib, encoding="utf-8")))
    if len(calib) < 2:
        raise SystemExit("в файле калибровки меньше двух точек")

    image = Image.open(args.image).convert("RGB")
    if args.region:
        x0, y0, x1, y1 = (int(v) for v in args.region.split(","))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(image.width, x1), min(image.height, y1)
        if x1 - x0 < 100 or y1 - y0 < 100:
            raise SystemExit(f"окно вытяжки слишком маленькое: {x1 - x0}×{y1 - y0}")
        arr = np.asarray(image.crop((x0, y0, x1, y1)))
    else:
        x0 = y0 = 0
        x1, y1 = image.width, image.height
        arr = np.asarray(image)

    mask = color_mask(arr, args.color)
    share = mask.mean()
    if share < 0.0002:
        raise SystemExit(f"пикселей цвета {args.color} в окне {share * 100:.3f}% — линия не найдена")

    local = trace_columns(mask, step=args.step, max_jump=args.max_jump_px)
    if len(local) < 5:
        raise SystemExit(f"точек линии получилось {len(local)} — слишком мало, нужна ручная обводка")
    local = simplify(local, args.eps_px)
    points = [{"x": p["x"] + x0, "y": p["y"] + y0} for p in local]

    if len(calib) >= 3:
        coords, stats = affine(points, calib)
        calib_note = f"аффинная подгонка по {len(calib)} точкам"
        if len(calib) == 3:
            calib_note += " (три точки определяют преобразование точно — остаток нулевой, ошибку привязки оценить нечем)"
        else:
            calib_note += f", RMS {stats['rms_m'] / 1000:.1f} км"
    else:
        coords, residual_m = similarity(points, calib[0], calib[1])
        calib_note = f"подобие по двум точкам, остаток {residual_m / 1000:.1f} км"

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
                       "traced_by": "автотрассировка по цвету, окно "
                                    f"{x0},{y0}—{x1},{y1}"},
        "note": (f"ЧЕРНОВИК: красная линия на листе {args.sheet}, окно "
                 f"{x0},{y0}—{x1},{y1}; {calib_note}; точек {len(coords)}. "
                 "Проверить глазами: разрывы от подписей пройдены по прямой."),
    }

    if args.preview:
        write_preview(image, (x0, y0, x1, y1), points, args.preview)
        print(f"проверочная картинка: {args.preview}")

    path = os.path.join(args.root, "data/front_lines/1941.json")
    registry = json.load(open(path, encoding="utf-8"))
    registry["front_lines"] = sorted(
        [x for x in registry.get("front_lines", []) if x["id"] != line["id"]] + [line],
        key=lambda x: x["id"],
    )
    if not args.dry_run and not args.accept:
        print("ВНИМАНИЕ: цвет не отличает линию фронта от подписей — включи --accept только "
              "после просмотра --preview")
        args.dry_run = True
    if not args.dry_run:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    print(f"{line['id']}: точек {len(coords)}, {calib_note}")
    print(f"  долгота {min(lons):.3f}…{max(lons):.3f}, широта {min(lats):.3f}…{max(lats):.3f}")
    print("записано" if not args.dry_run else "(dry-run: не записано)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
