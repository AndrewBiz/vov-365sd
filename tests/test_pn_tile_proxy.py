"""Прокси плиток: подстановка сессии, кэш, отказ при 401 — без сети, на локальном «CDN»."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(REPO, "tools"))

import pn_tile_proxy  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


class FakeCdn(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits = 0

    def log_message(self, *args, **kwargs) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        FakeCdn.hits += 1
        cookie = self.headers.get("Cookie") or ""
        referer = self.headers.get("Referer") or ""
        if "sid=good" in cookie and "pamyat-naroda.ru" in referer:
            body, code = PNG, 200
        else:
            body, code = b"<html>401</html>", 401
        self.send_response(code)
        self.send_header("Content-Type", "image/png" if code == 200 else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestTileProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cdn = ThreadingHTTPServer(("127.0.0.1", 0), FakeCdn)
        threading.Thread(target=cls.cdn.serve_forever, daemon=True).start()
        cls.cdn_url = f"http://127.0.0.1:{cls.cdn.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.cdn.shutdown()

    def setUp(self) -> None:
        FakeCdn.hits = 0
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.cookie = root / "cookie.txt"
        self.cookie.write_text("sid=good; other=1", encoding="utf-8")
        self.cookie.chmod(0o600)
        self.cfg = {
            "deal": "384-0008529-0207", "page": "00000002",
            "cookie_file": str(self.cookie), "referer": "https://pamyat-naroda.ru/",
            "user_agent": "test-agent", "cache_dir": str(root / "cache"),
            "concurrency": 2, "delay_ms": 0,
        }
        self._old_cdn = pn_tile_proxy.CDN
        pn_tile_proxy.CDN = self.cdn_url
        stats: dict = {}
        self.proxy = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            pn_tile_proxy.make_handler(self.cfg, "sid=good; other=1", stats))
        self.stats = stats
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.proxy.server_address[1]}"

    def tearDown(self) -> None:
        self.proxy.shutdown()
        pn_tile_proxy.CDN = self._old_cdn
        self.tmp.cleanup()

    def _get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_serves_tile_and_caches(self) -> None:
        code, body = self._get("/6/38/44.png")
        self.assertEqual((code, body[:4]), (200, b"\x89PNG"))
        code2, body2 = self._get("/6/38/44.png")
        self.assertEqual((code2, body2), (200, body))
        self.assertEqual(FakeCdn.hits, 1, "вторая выдача должна прийти из кэша")

    def test_cookie_and_referer_are_injected(self) -> None:
        self._get("/7/76/88.png")
        self.assertEqual(FakeCdn.hits, 1)
        status, body = pn_tile_proxy.fetch(self.cfg, "sid=bad", 7, 76, 89)[0:2]
        self.assertEqual(status, 401, "без валидной сессии CDN отвечает 401")

    def test_auth_failure_is_reported(self) -> None:
        stats: dict = {}
        proxy = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            pn_tile_proxy.make_handler(self.cfg, "sid=bad", stats))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{proxy.server_address[1]}/9/1/2.png", timeout=10)
            self.assertEqual(ctx.exception.code, 401)
            self.assertIn("сессия не принята", ctx.exception.read().decode())
            self.assertEqual(stats.get("auth_failed"), 1)
        finally:
            proxy.shutdown()

    def test_bad_route(self) -> None:
        code, _ = self._get("/nonsense")
        self.assertEqual(code, 404)

    def test_status_endpoint(self) -> None:
        self._get("/6/38/44.png")
        code, body = self._get("/status")
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertEqual(payload["deal"], self.cfg["deal"])
        self.assertGreaterEqual(payload["from_cdn"], 1)


if __name__ == "__main__":
    unittest.main()
