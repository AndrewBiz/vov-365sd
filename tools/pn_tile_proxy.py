#!/usr/bin/env python3
"""Локальный прокси плиток «Памяти народа» для QGIS.

Зачем: плитки карт отдаются по сессии (без неё CDN отвечает 401), а QGIS-слой
XYZ не умеет «войти на сайт». Прокси берёт вашу же сессию из отдельного файла,
подставляет её в запрос к CDN, кэширует плитки на диск и отдаёт QGIS по
localhost — так в проекте QGIS не появляется ни куки, ни токенов.

    python3 tools/pn_tile_proxy.py init      # шаблон настроек и файла куки
    python3 tools/pn_tile_proxy.py check     # проверить сессию на одной плитке
    python3 tools/pn_tile_proxy.py serve     # поднять прокси для QGIS
    python3 tools/pn_tile_proxy.py url       # готовый URL-шаблон для слоя XYZ

Настройки: ~/.config/365sd/pn.json (права 600).
Куки: отдельный файл, указанный в настройках (права 600), формат — значение
заголовка `Cookie` целиком, как его показывает браузер. В репозиторий и в файл
проекта QGIS куки не попадают.

Скрипт не обходит проверку безопасности и капчу: если сессии нет, он получает
401 и говорит об этом. Он лишь переиспользует уже открытую вами сессию в другом
клиенте — это может расходиться с правилами сайта, поэтому по умолчанию
включены ограничение одновременных запросов и пауза между ними.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CDN = "https://cdn.pamyat-naroda.ru"
CONFIG = pathlib.Path.home() / ".config/365sd/pn.json"
CACHE = pathlib.Path.home() / ".cache/365sd/pn-tiles"

TEMPLATE = {
    "deal": "384-0008529-0207",
    "page": "00000002",
    "cookie_file": str(pathlib.Path.home() / ".config/365sd/pn_cookie.txt"),
    "referer": "https://pamyat-naroda.ru/",
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "cache_dir": str(CACHE),
    "concurrency": 2,
    "delay_ms": 150,
}


def load_config(path: pathlib.Path = CONFIG) -> dict:
    if not path.exists():
        raise SystemExit(f"нет настроек: {path}\nсначала: python3 tools/pn_tile_proxy.py init")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("deal", "page", "cookie_file") if not cfg.get(key)]
    if missing:
        raise SystemExit(f"в {path} не хватает полей: {', '.join(missing)}")
    return {**TEMPLATE, **cfg}


def read_cookie(cfg: dict) -> str:
    path = pathlib.Path(os.path.expanduser(cfg["cookie_file"]))
    if not path.exists():
        raise SystemExit(f"нет файла с куки: {path}\n"
                         "скопировать заголовок Cookie из браузера (F12 → Network → любой запрос → Cookie)")
    mode = oct(path.stat().st_mode & 0o777)
    if mode != "0o600":
        print(f"  ПРЕДУПРЕЖДЕНИЕ  у {path} права {mode}, лучше 600: chmod 600", file=sys.stderr)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(f"файл с куки пуст: {path}")
    return value


def tile_url(cfg: dict, z: int, x: int, y: int) -> str:
    return f"{CDN}/tiles/{cfg['deal']}/{cfg['page']}_tiles/{z}/{x}/{y}.png"


def fetch(cfg: dict, cookie: str, z: int, x: int, y: int) -> tuple[int, bytes, str]:
    request = urllib.request.Request(tile_url(cfg, z, x, y), headers={
        "Cookie": cookie,
        "Referer": cfg["referer"],
        "User-Agent": cfg["user_agent"],
        "Accept": "image/avif,image/webp,*/*",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:2048], error.headers.get("Content-Type", "")


def cache_path(cfg: dict, z: int, x: int, y: int) -> pathlib.Path:
    root = pathlib.Path(os.path.expanduser(cfg["cache_dir"]))
    return root / cfg["deal"] / cfg["page"] / str(z) / str(x) / f"{y}.png"


class Throttle:
    """Не больше N одновременных запросов и пауза между ними."""

    def __init__(self, concurrency: int, delay_ms: int) -> None:
        self.sem = threading.Semaphore(max(1, concurrency))
        self.delay = max(0, delay_ms) / 1000
        self.lock = threading.Lock()
        self.last = 0.0

    def __enter__(self) -> None:
        self.sem.acquire()
        with self.lock:
            wait = self.last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()

    def __exit__(self, *exc) -> None:
        self.sem.release()


def make_handler(cfg: dict, cookie: str, stats: dict) -> type[BaseHTTPRequestHandler]:
    throttle = Throttle(int(cfg["concurrency"]), int(cfg["delay_ms"]))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 — имя из базового класса
            stats["requests"] = stats.get("requests", 0) + 1

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — имя метода задано BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path in ("/status", "/status.json"):
                payload = {"deal": cfg["deal"], "page": cfg["page"], **stats}
                body = json.dumps(payload, ensure_ascii=False).encode()
                return self._send(200, body, "application/json; charset=utf-8")
            parts = [p for p in path.split("/") if p]
            if len(parts) != 3 or not parts[2].endswith(".png"):
                return self._send(404, b"no such tile route", "text/plain; charset=utf-8")
            try:
                z, x, y = int(parts[0]), int(parts[1]), int(parts[2][:-4])
            except ValueError:
                return self._send(400, b"z/x/y must be integers", "text/plain; charset=utf-8")

            cached = cache_path(cfg, z, x, y)
            if cached.exists() and cached.stat().st_size > 0:
                stats["from_cache"] = stats.get("from_cache", 0) + 1
                return self._send(200, cached.read_bytes(), "image/png")

            with throttle:
                status, body, ctype = fetch(cfg, cookie, z, x, y)
            stats["from_cdn"] = stats.get("from_cdn", 0) + 1
            if status == 200 and body[:4] == b"\x89PNG":
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(body)
                return self._send(200, body, "image/png")
            if status in (401, 403):
                stats["auth_failed"] = stats.get("auth_failed", 0) + 1
                message = (f"CDN ответил {status}: сессия не принята. "
                           "Обновите куки из браузера и перезапустите прокси.").encode()
                return self._send(status, message, "text/plain; charset=utf-8")
            return self._send(status if status >= 400 else 502, body[:2048],
                              ctype or "text/plain; charset=utf-8")

    return Handler


def cmd_init() -> int:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        print(f"настройки уже есть: {CONFIG}")
    else:
        CONFIG.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        CONFIG.chmod(0o600)
        print(f"создано: {CONFIG}")
    cookie = pathlib.Path(os.path.expanduser(TEMPLATE["cookie_file"]))
    if not cookie.exists():
        cookie.write_text("", encoding="utf-8")
        cookie.chmod(0o600)
        print(f"создан пустой файл куки: {cookie} (вписать значение заголовка Cookie)")
    print("\nдальше:\n"
          "  1. открыть сайт в браузере и найти нужную карту (шифр дела и номер листа — в URL плиток)\n"
          "  2. F12 → Network → фильтр png → любой запрос → Request Headers → скопировать Cookie целиком\n"
          f"  3. вставить в {cookie}\n"
          "  4. поправить deal/page в настройках, затем: python3 tools/pn_tile_proxy.py check")
    return 0


def cmd_check() -> int:
    cfg = load_config()
    cookie = read_cookie(cfg)
    status, body, ctype = fetch(cfg, cookie, 6, 38, 44)
    print(f"плитка {tile_url(cfg, 6, 38, 44)}")
    print(f"ответ: {status} {ctype} {len(body)} байт")
    if status == 200 and body[:4] == b"\x89PNG":
        print("итог: сессия принята, можно поднимать прокси и подключать слой в QGIS")
        return 0
    if status in (401, 403):
        print("итог: сессия не принята — обновить куки (срок жизни истёк или сессия привязана к браузеру)")
        return 1
    print(f"итог: неожиданный ответ; первые байты: {body[:80]!r}")
    return 1


def cmd_serve(port: int, host: str) -> int:
    cfg = load_config()
    cookie = read_cookie(cfg)
    stats: dict = {"deal": cfg["deal"], "page": cfg["page"]}
    server = ThreadingHTTPServer((host, port), make_handler(cfg, cookie, stats))
    print(f"прокси: http://{host}:{port}/{{z}}/{{x}}/{{y}}.png")
    print(f"QGIS: слой XYZ → URL http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png")
    print("остановка: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nзапросов: {stats.get('requests', 0)}, из кэша: {stats.get('from_cache', 0)}, "
              f"с CDN: {stats.get('from_cdn', 0)}, отказов сессии: {stats.get('auth_failed', 0)}")
    return 0


def cmd_url(port: int) -> int:
    cfg = load_config()
    print(f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png")
    print(f"# дело {cfg['deal']}, лист {cfg['page']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["init", "check", "serve", "url"])
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init()
    if args.command == "check":
        return cmd_check()
    if args.command == "serve":
        return cmd_serve(args.port, args.host)
    return cmd_url(args.port)


if __name__ == "__main__":
    sys.exit(main())
