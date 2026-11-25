#!/usr/bin/env python3
"""Снимок веб-источника: обход в ширину, карта URL → файл, режим сравнения.

  python3 tools/snapshot_site.py --start https://example.org/ \\
      --cache data/cache/example --date 2026-11-25
  python3 tools/snapshot_site.py --start https://example.org/ --cache data/cache/example --check

Снимок кладётся в <cache>/<YYYY-MM-DD>/: по файлу на страницу и карта pages.json
(отсортирована по URL, без отметок времени — повторный обход даёт тот же результат).
Сеть есть только здесь; проверки и сборка работают офлайн.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from datetime import date

UA = "vov-365sd-research/0.1 (family history project; polite crawl, 1 req/s)"
SKIP_PARTS = ("/wp-content/", "/wp-json/", "/feed", "/author/", "/comment", "/wp-admin", "/xmlrpc", "?", "#")
LISTING_MARKERS = ("/category/", "/page/")


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


class _Blocks(HTMLParser):
    """Собирает текст всех блоков, у которых class содержит entry-content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[str]] = []
        self._depth = 0
        self._buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._buf is not None:
            self._depth += 1
            if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
                self._buf.append("\n")
            return
        classes = dict(attrs).get("class") or ""
        if tag == "div" and "entry-content" in classes:
            self._buf, self._depth = [], 1

    def handle_endtag(self, tag: str) -> None:
        if self._buf is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            self.blocks.append(self._buf)
            self._buf = None

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)


def extract_text(page: str) -> str:
    """Текст страницы: все блоки entry-content по порядку (на форуме — посты темы)."""
    parser = _Blocks()
    parser.feed(page)
    if parser.blocks:
        parts = []
        for block in parser.blocks:
            text = "".join(block)
            text = re.sub(r"[ \t\u00a0]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text).strip()
            if text:
                parts.append(text)
        if parts:
            return "\n\n---\n\n".join(parts)
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    body = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\u00a0]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


def slug_of(url: str, used: dict[str, str]) -> str:
    """Детерминированное имя файла: путь URL, при коллизии — с хешем."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).strip("/")
    slug = re.sub(r"[^0-9A-Za-z_-]+", "-", path).strip("-").lower()[:80] or "index"
    if used.get(slug, url) != url:
        slug = f"{slug}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"
    used[slug] = url
    return slug


def links_of(page: str, base: str, allow_query: bool = False) -> list[str]:
    out = []
    host = urllib.parse.urlparse(base).netloc
    for href in re.findall(r'href=["\']([^"\']+)["\']', page):
        url = urllib.parse.urljoin(base, href).split("#")[0]
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != host:
            continue
        skip = [s for s in SKIP_PARTS if s != "?"] if allow_query else SKIP_PARTS
        if any(part in url for part in skip):
            continue
        out.append(url)
    return out


def is_listing(url: str, base: str) -> bool:
    path = urllib.parse.urlparse(url).path
    return path in ("", "/") or any(marker in path for marker in LISTING_MARKERS)


def crawl(starts: list[str], limit: int, delay: float, verbose: bool = True, allow_query: bool = False) -> dict[str, str]:
    """Возвращает карту URL → текст страницы (только не-листинги)."""
    base = starts[0]
    seen: set[str] = set()
    queue: list[str] = list(starts)
    pages: dict[str, str] = {}
    while queue and len(seen) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            page = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"ERR {url}: {exc}", file=sys.stderr)
            continue
        if verbose:
            print(f"{len(seen):3d} {url}")
        time.sleep(delay)  # вежливость к сайту: пауза между любыми запросами
        for link in links_of(page, base, allow_query):
            if link not in seen and link not in queue:
                queue.append(link)
        if is_listing(url, base):
            # страницы листинга — только те, что реально есть в разметке (ссылки «2 3 4 …»)
            for link in links_of(page, base, allow_query):
                if re.search(r"/page/\d+/?$", link) and link not in seen and link not in queue:
                    queue.append(link)
            continue
        pages[url] = extract_text(page)
    return pages


def classify_removed(urls: list[str], fetcher=None) -> tuple[list[str], list[str]]:
    """Делит «пропавшие» из обхода адреса на недостигнутые (живы) и удалённые (недоступны)."""
    fetcher = fetcher or fetch
    unreached: list[str] = []
    gone: list[str] = []
    for url in urls:
        try:
            fetcher(url)
            unreached.append(url)
        except Exception:  # noqa: BLE001
            gone.append(url)
    return unreached, gone


def write_snapshot(cache_dir: str, date_str: str, pages: dict[str, str]) -> str:
    """Пишет снимок; повторный проход в тот же день дополняет существующий снимок."""
    out_dir = os.path.join(cache_dir, date_str)
    pages_dir = os.path.join(out_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    map_path = os.path.join(out_dir, "pages.json")
    mapping: dict[str, str] = {}
    if os.path.exists(map_path):
        with open(map_path, encoding="utf-8") as fh:
            mapping = json.load(fh).get("pages", {})
    used = {os.path.basename(rel)[:-4]: url for url, rel in mapping.items()}
    for url in sorted(pages):
        rel = mapping.get(url) or f"pages/{slug_of(url, used)}.txt"
        mapping[url] = rel
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(url + "\n" + pages[url] + "\n")
    payload = {"count": len(mapping), "pages": dict(sorted(mapping.items()))}
    with open(map_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return out_dir


def load_map(cache_dir: str, date_str: str | None = None) -> dict:
    d = os.path.join(cache_dir, date_str) if date_str else None
    if d is None:
        candidates = sorted(
            name for name in os.listdir(cache_dir) if os.path.isdir(os.path.join(cache_dir, name))
        ) if os.path.isdir(cache_dir) else []
        if not candidates:
            return {"count": 0, "pages": {}}
        d = os.path.join(cache_dir, candidates[-1])
    path = os.path.join(d, "pages.json")
    if not os.path.exists(path):
        return {"count": 0, "pages": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="append", required=True, help="стартовый URL (можно несколько)")
    parser.add_argument("--cache", required=True, help="каталог снимков, относительно корня проекта")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--check", action="store_true", help="сравнить сайт с текущим снимком, не записывая")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--allow-query", action="store_true", help="не отбрасывать адреса с параметрами (?showtopic=…)")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(root, args.cache)
    pages = crawl(args.start, args.limit, args.delay, verbose=not args.quiet, allow_query=args.allow_query)

    if args.check:
        current = load_map(cache_dir)
        old = set(current.get("pages", {}))
        new = set(pages)
        added = sorted(new - old)
        missing = sorted(old - new)
        print(f"страниц в снимке: {len(old)}, обход нашёл: {len(new)}")
        for url in added:
            print(f"+ новая страница: {url}")
        unreached, gone = classify_removed(missing)
        for url in unreached:
            print(f"~ не достигнута обходом (страница жива): {url}")
        for url in gone:
            print(f"- страница недоступна: {url}")
        if added or gone:
            print("РАСХОЖДЕНИЕ: снимок устарел")
            return 1
        print("Снимок актуален: удалённых и новых страниц нет" if not missing else
              "Снимок актуален по содержанию: часть страниц не достигнута обходом (структура ссылок), обновите снимок полным прогоном")
        return 0

    out_dir = write_snapshot(cache_dir, args.date, pages)
    print(f"снимок записан: {os.path.relpath(out_dir, root)} ({len(pages)} страниц)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
