#!/usr/bin/env python3
"""Приёмник плиток карт «Памяти народа» и сборка большого изображения.

Зачем: просмотрщик отдаёт карту плитками (`.../tiles/<шифр>/<лист>.jpg_tiles/<z>/<x>/<y>.png`),
а целиком файл не скачивается. Браузерная консоль (tools/pn_map_grab.js) забирает плитки
в сессии пользователя и присылает их сюда; этот скрипт склеивает их в PNG и GeoTIFF.

Запуск:
    python3 tools/pn_tiles_stitch.py serve --dir ~/.cache/pn-tiles

Дальше в консоли браузера выполнять tools/pn_map_grab.js с SERVER = "http://127.0.0.1:8770".

Схема плиток: XYZ с перевёрнутой осью Y (TMS) в Web Mercator (EPSG:3857),
плитка 256 px — то же, что использует SAS.Planet-скрипт сообщества.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TILE = 256


def tile_bounds_3857(z: int, x: int, y_tms: int) -> tuple[float, float, float, float]:
    """Границы плитки в EPSG:3857 (метры). y — в схеме TMS (снизу вверх)."""
    world = 2 * math.pi * 6378137.0
    span = world / (2 ** z)
    left = -world / 2 + x * span
    # переводим TMS → XYZ: y_xyz = 2^z - 1 - y_tms
    y_xyz = (2 ** z - 1) - y_tms
    top = world / 2 - y_xyz * span
    return left, top - span, left + span, top


class Collector:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.count = 0

    def save(self, z: int, x: int, y: int, payload: bytes) -> pathlib.Path:
        folder = self.root / str(z) / str(x)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{y}.png"
        with self.lock:
            path.write_bytes(payload)
            self.count += 1
        return path

    def tiles(self) -> dict[int, list[tuple[int, int, pathlib.Path]]]:
        found: dict[int, list[tuple[int, int, pathlib.Path]]] = {}
        for zdir in sorted(p for p in self.root.iterdir() if p.is_dir() and p.name.isdigit()):
            z = int(zdir.name)
            for xdir in zdir.iterdir():
                if not xdir.is_dir() or not xdir.name.isdigit():
                    continue
                for file in xdir.glob("*.png"):
                    if file.name.split(".")[0].isdigit():
                        found.setdefault(z, []).append((int(xdir.name), int(file.stem), file))
        return found

    def mosaic(self, zoom: int | None = None) -> dict:
        tiles = self.tiles()
        if not tiles:
            raise SystemExit("плиток нет: сначала пришли их из браузера")
        z = zoom if zoom is not None else max(tiles)
        items = sorted(tiles.get(z, []))
        if not items:
            raise SystemExit(f"нет плиток уровня {z}")
        xs = [x for x, _, _ in items]
        ys = [y for _, y, _ in items]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        width = (max_x - min_x + 1) * TILE
        height = (max_y - min_y + 1) * TILE

        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        gaps = 0
        for x, y, path in items:
            try:
                with Image.open(path) as tile:
                    tile = tile.convert("RGB")
                    left = (x - min_x) * TILE
                    # TMS: больший y — ниже (в схеме снизу вверх), поэтому инвертируем
                    top = (max_y - y) * TILE
                    canvas.paste(tile, (left, top))
            except Exception:
                gaps += 1
        boxes = [tile_bounds_3857(z, x, y) for x, y, _ in items]
        west = min(b[0] for b in boxes)
        south = min(b[1] for b in boxes)
        east = max(b[2] for b in boxes)
        north = max(b[3] for b in boxes)
        meta = {
            "zoom": z, "tiles": len(items), "broken": gaps,
            "width": width, "height": height,
            "bbox_3857": [west, south, east, north],
            "scheme": "XYZ/TMS, EPSG:3857, tile 256",
        }
        return {"image": canvas, "meta": meta}


def write_outputs(result: dict, outdir: pathlib.Path, name: str, geotiff: bool) -> list[pathlib.Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    meta = result["meta"]
    image = result["image"]
    png = outdir / f"{name}.png"
    image.save(png, optimize=False)
    written = [png]
    (outdir / f"{name}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written.append(outdir / f"{name}.json")

    if geotiff:
        try:
            from osgeo import gdal, osr
        except ImportError:
            print("GDAL (osgeo) недоступен: GeoTIFF не записан, PNG + JSON остались")
            return written
        tif = outdir / f"{name}.tif"
        west, south, east, north = meta["bbox_3857"]
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(str(tif), image.width, image.height, 3, gdal.GDT_Byte,
                           options=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
        for band in range(3):
            ds.GetRasterBand(band + 1).WriteArray(
                __import__("numpy").array(image.split()[band]))
        ds.SetGeoTransform((west, (east - west) / image.width, 0.0, north, 0.0,
                            -(north - south) / image.height))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        ds.SetProjection(srs.ExportToWkt())
        ds.FlushCache()
        ds = None
        written.append(tif)
    return written


class Handler(BaseHTTPRequestHandler):
    collector: Collector
    allow_origin = "*"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.allow_origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            body = json.dumps({"tiles": self.collector.count}).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b""
        if parsed.path != "/tile":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        try:
            z = int(query["z"][0])
            x = int(query["x"][0])
            y = int(query["y"][0])
        except (KeyError, ValueError):
            self.send_response(400)
            self._cors()
            self.end_headers()
            return
        if payload[:4] != b"\x89PNG":
            self.send_response(415)
            self._cors()
            self.end_headers()
            return
        self.collector.save(z, x, y, payload)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - имя из базового класса
        if re.search(r"^POST", format % args):
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="принимать плитки из браузера")
    serve.add_argument("--dir", default=str(pathlib.Path.home() / ".cache/pn-tiles"))
    serve.add_argument("--port", type=int, default=8770)

    build = sub.add_parser("build", help="склеить принятые плитки в PNG/GeoTIFF")
    build.add_argument("--dir", default=str(pathlib.Path.home() / ".cache/pn-tiles"))
    build.add_argument("--zoom", type=int, default=None, help="уровень (по умолчанию максимальный)")
    build.add_argument("--out", required=True, help="файл без расширения")
    build.add_argument("--no-geotiff", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        collector = Collector(pathlib.Path(args.dir).expanduser())
        Handler.collector = collector
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"приёмник: http://127.0.0.1:{args.port}/  (плитки → {collector.root})")
        print("в консоли браузера: tools/pn_map_grab.js с SERVER = http://127.0.0.1:%d" % args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print(f"\nпринято плиток: {collector.count}")
        return 0

    collector = Collector(pathlib.Path(args.dir).expanduser())
    result = collector.mosaic(args.zoom)
    written = write_outputs(result, pathlib.Path(args.out).expanduser().parent,
                            pathlib.Path(args.out).expanduser().name, not args.no_geotiff)
    print(f"собрано плиток: {result['meta']['tiles']}, битых: {result['meta']['broken']}")
    print(f"размер: {result['meta']['width']}x{result['meta']['height']}, zoom {result['meta']['zoom']}")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
