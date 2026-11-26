#!/usr/bin/env python3
"""OCR сканов «Памяти народа» в кэш, с оценкой разборчивости.

  python3 tools/ocr_scan.py --month 1941-12 --types combat-order,operational-summary
  python3 tools/ocr_scan.py --doc doc-pn-354-0005806-0010-p00000195 --psm 6 --scale 2

Сканы — фотокопии вторых экземпляров, часть из них выцвела: OCR на них даёт
поток согласных. Скрипт считает долю «похожих на слово» токенов и пишет её
рядом с документом, чтобы сразу видеть, что читать можно, а что бесполезно.

Текст кладётся в data/cache/ocr/<doc-id>_psm<psm>_x<scale>.txt (в репозиторий
попадает производная, а не скан). Русский traineddata берётся из
~/.hermes/tools/tessdata (или --tessdata-dir).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None  # сканы бывают 160–280 Мп

ROOT_DEFAULT = str(pathlib.Path(__file__).resolve().parent.parent)
VAULT_DEFAULT = "~/Documents/Obsidian Vault"
TESSDATA_DEFAULT = "~/.hermes/tools/tessdata"
VOWELS = "аеиоуыэюяё"


def quality(text: str) -> tuple[int, int, int]:
    """(слов, похожих на слово, процент): маркер «согласные подряд» ловит мусор OCR."""
    words = re.findall(r"[А-Яа-яЁё]{3,}", text)
    good = [w for w in words
            if re.search(f"[{VOWELS}]", w, re.I) and not re.search(f"[^{VOWELS}]{{5,}}", w, re.I)]
    return len(words), len(good), round(len(good) / max(len(words), 1) * 100)


def ocr_page(path: pathlib.Path, psm: int, scale: float, tessdata: str) -> str:
    image = Image.open(path).convert("L")
    image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    tmp = pathlib.Path("/tmp") / f"ocr_{path.stem}_{psm}.png"
    image.save(tmp)
    result = subprocess.run(
        ["tesseract", str(tmp), "-", "--tessdata-dir", tessdata, "-l", "rus", "--psm", str(psm)],
        capture_output=True, text=True,
    )
    tmp.unlink(missing_ok=True)
    if result.returncode != 0:
        raise SystemExit(f"tesseract: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--vault", default=VAULT_DEFAULT)
    ap.add_argument("--tessdata-dir", default=TESSDATA_DEFAULT)
    ap.add_argument("--doc", action="append", help="id документа (можно несколько раз)")
    ap.add_argument("--month", help="все документы за месяц, например 1941-12")
    ap.add_argument("--types", default="combat-order,operational-summary,combat-journal")
    ap.add_argument("--psm", type=int, default=6)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--out", default="data/cache/ocr")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root)
    vault = pathlib.Path(os.path.expanduser(args.vault))
    tessdata = os.path.expanduser(args.tessdata_dir)
    out_dir = root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = json.load(open(root / "data/documents.json", encoding="utf-8"))["documents"]
    if args.doc:
        selected = [d for d in docs if d["id"] in set(args.doc)]
    else:
        types = set(args.types.split(","))
        selected = [d for d in docs
                    if d.get("carrier", {}).get("file_path")
                    and d["type"] in types
                    and (not args.month or (d["date"]["value"].get("from") or "").startswith(args.month))]
    if not selected:
        raise SystemExit("не нашлось документов под условие")

    print(f"{'документ':46s} {'симв':>5s} {'слов':>5s} {'ок':>5s} {'%':>4s}  заголовок")
    summary = []
    for doc in selected:
        path = vault / doc["carrier"]["file_path"]
        if not path.exists():
            print(f"{doc['id']:46s} — файла нет: {doc['carrier']['file_path']}")
            continue
        target = out_dir / f"{doc['id']}_psm{args.psm}_x{args.scale:g}.txt"
        if target.exists() and not args.force:
            text = target.read_text(encoding="utf-8")
        else:
            text = ocr_page(path, args.psm, args.scale, tessdata)
            target.write_text(text + "\n", encoding="utf-8")
        words, good, pct = quality(text)
        summary.append({"id": doc["id"], "chars": len(text), "words": words, "good": good,
                        "pct": pct, "date": doc["date"]["value"].get("from"), "type": doc["type"]})
        print(f"{doc['id']:46s} {len(text):5d} {words:5d} {good:5d} {pct:4d}  {doc['title']['value'][:40]}")

    index = out_dir / f"index_psm{args.psm}_x{args.scale:g}.json"
    index.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    readable = [s for s in summary if s["pct"] >= 85 and s["words"] >= 60]
    print(f"\nвсего {len(summary)}, разборчивых (≥85 % и ≥60 слов): {len(readable)}")
    print(f"кэш: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
