# Скилы (шаги с моделью)

Скил — контракт одного шага с моделью: что подаём, что требуем на выходе, какая модель годится и какие правила соблюдать. Скилы лежат в репозитории, версионируются вместе с ядром и не зависят от конкретного провайдера.

```yaml
name: extract-facts          # имя шага, оно же имя файла
title: Извлечение фактов из текста документа
version: 1
input:
  kind: text                 # text | image
  from: ocr                  # ocr | files
  max_chars: 20000
model:
  hint: "модель с длинным контекстом, температура 0"
  temperature: 0
output:
  type: object
  required: [items]
  ...                        # JSON Schema (YAML-записью)
prompt: |
  ...
rules:
  - ...
```

Правила, общие для всех скилов:

- Модель **не пишет в данные**. Выход шага — предложение в `data/proposals/<slug>/`, применение отдельной командой.
- Каждая позиция обязана нести дословную цитату из входа. Цитата проверяется на вхождение в текст: не нашлась — позиция отклоняется при применении.
- Пустое значение лучше догадки: если даты или места в тексте нет — `null`, а не «примерно».
- У каждой позиции есть `confidence` 0..1; ниже порога — только на ручной просмотр.

Запуск:

```bash
python3 tools/ai_steps.py propose <slug> --step extract-facts          # если модель настроена
python3 tools/ai_steps.py propose <slug> --step extract-facts --emit-request   # только бандл
python3 tools/ai_steps.py propose <slug> --step extract-facts --ingest response.json
python3 tools/ai_steps.py review <slug>
python3 tools/ai_steps.py apply-proposal <slug> --step extract-facts --min-confidence 0.7
```

Модель настраивается переменными окружения `VOV_AI_BASE_URL`, `VOV_AI_API_KEY`, `VOV_AI_MODEL` (OpenAI-совместимый `/chat/completions`). Если они не заданы, `propose` пишет бандл запроса в `data/proposals/<slug>/<step>-request.json` — его можно отдать любой модели вручную, а ответ забрать через `--ingest`.
