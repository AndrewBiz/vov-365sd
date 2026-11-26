# Инбокс артефактов

Сюда исследователь кладёт новый материал: папка на артефакт, внутри — манифест и файлы.

```
inbox/
  pn-354-10-l251/
    artifact.yaml      # манифест: что это, откуда, реквизиты, дата, ожидания
    files/             # сам скан/pdf/pptx/html — в git не попадает
    comments.md        # свободные заметки (необязательно)
```

Манифест и заметки коммитятся; файлы — нет (`inbox/*/files/` в `.gitignore`). Оригиналы живут в Obsidian, сюда кладётся рабочая копия.

## Как начать

```bash
python3 tools/intake.py drop files/p00000251.jpg \
    --slug pn-354-10-l251 --kind archive-document \
    --source src-pamyat-naroda --document-type operational-summary \
    --title "Оперативная сводка штаба 365 сд за 16.12.1941" \
    --location "ЦАМО — Память народа/Передача_001_КП097Р-С28/354-0005806-0010"
```

Дальше руками заполнить в `artifact.yaml` поля с `TODO` и прогнать разбор:

```bash
python3 tools/intake.py status            # что в инбоксе и на каком шаге
python3 tools/intake.py scan <slug>       # офлайн: без сети и без моделей
python3 tools/intake.py apply <slug> --dry-run
python3 tools/intake.py apply <slug>      # перенос файлов в Obsidian + запись документа
```

## Манифест

```yaml
slug: pn-354-10-l251
kind: archive-document        # archive-document | map-sheet | book-page | presentation | web-page | scan-set
title: "Оперативная сводка штаба 365 сд за 16.12.1941"
source_id: src-pamyat-naroda  # обязан быть в data/sources.json
received: 2026-11-26
origin:
  portal_url: https://pamyat-naroda.ru/documents/view/?id=document20175
requisites: {fund: "354", inventory: "5806", file: "10", sheet: "251"}   # либо null
date: {from: 1941-12-16, to: 1941-12-16, precision: day}
unit: "365 сд, штаб"
files: [files/p00000251.jpg]
original_location: "ЦАМО — Память народа/Передача_001_КП097Р-С28/354-0005806-0010"
document_type: operational-summary   # для archive-document обязательно
expectations: "что надеюсь найти в документе"
```

Правила: реквизиты — либо полный блок фонд+опись+дело (лист по возможности), либо `null` с пояснением в заметках. Дата — период с точностью (`day|month|year|range`), «примерно» не пишется строкой.

## Что происходит

- `scan` — офлайн: sha256, формат по сигнатуре, размеры, страницы и текстовый слой PDF, слайды PPTX, OCR с оценкой разборчивости, поиск дубликатов по sha и по реквизитам, черновик записи документа в `data/proposals/<slug>/extraction.json`. Журнал — `data/intake/journal.jsonl`.
- `apply` — копирует файл в Obsidian со сверкой sha, вставляет запись в `data/documents.json` и прогоняет валидатор; при нарушении откатывает.
- Повторный `scan` на неизменённых файлах ничего не меняет; повторный `apply` не дублирует документ.

ИИ-шаги (транскрипция, извлечение фактов) и коррекции фактов — следующие change'и, не этот.
