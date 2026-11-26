/* Скачивание карты с «Памяти народа» — скрипт для консоли браузера (v2).
 *
 * Зачем: просмотрщик отдаёт карту плитками, кнопки «скачать целиком» нет.
 * Скрипт находит адрес плитки в уже загруженных запросах страницы (или в <img>),
 * поднимается по родителям до мелкого уровня, обходит пирамиду вниз до максимального,
 * склеивает PNG и печатает bbox в EPSG:3857.
 *
 * Как пользоваться:
 *   1. Открыть карту и подвигать/помысштабировать её 2–3 раза.
 *   2. F12 → Console → вставить весь файл → Enter.
 *   3. Если написало «схема не найдена» — ниже в консоли будут примеры адресов: пришли их.
 *
 * Если шифр известен заранее, заполнить DEAL и PAGE — поиск не понадобится.
 * Ничего не обходит: запросы идут из твоей сессии, те же, что делает просмотрщик.
 */

(async () => {
  // ── настройки ───────────────────────────────────────────────────────────────
  const DEAL = '';          // '' = искать автоматически; иначе '384-0008529-0207'
  const PAGE = '';          // '' = искать автоматически; иначе '00000002'
  const SERVER = '';        // '' = склеить в браузере; 'http://127.0.0.1:8770' = отправить плитки приёмнику
  const ZMIN = 6;           // докуда подниматься по родителям (затравка обхода)
  const ZMAX = 17;          // докуда спускаться (реальный предел сервис найдёт сам)
  const CONC = 6;           // одновременных запросов
  const MAX_TILES = 8000;   // предохранитель
  const TILE = 256;
  // ────────────────────────────────────────────────────────────────────────────

  const log = (...a) => console.log('%c[карта]', 'color:#0a0', ...a);
  // deal = сегмент перед листом, лист = имя файла без расширения
  const TILE_RE = /([^/]+)\/([^/]+?)\.(?:jpe?g|png)_tiles\/(\d+)\/(\d+)\/(\d+)\.(?:png|jpe?g)/;

  try { performance.setResourceTimingBufferSize?.(10000); } catch {}

  // 1. Собрать все адреса, где может быть плитка
  const candidates = new Set();
  for (const e of performance.getEntriesByType('resource')) candidates.add(e.name);
  for (const img of document.images) {
    const src = img.currentSrc || img.src;
    if (src) candidates.add(src);
  }
  for (const el of document.querySelectorAll('[style*="url("]')) {
    const m = (el.getAttribute('style') || '').match(/url\(["']?([^"')]+)/);
    if (m) candidates.add(m[1]);
  }
  log('адресов в журнале страницы:', candidates.size);

  // 2. Найти схему и «якорную» плитку
  let anchor = null;
  for (const url of candidates) {
    const m = url.match(TILE_RE);
    if (m) {
      let origin = 'https://cdn.pamyat-naroda.ru';
      try { origin = new URL(url).origin; } catch {}
      anchor = {deal: m[1], page: m[2], z: +m[3], x: +m[4], y: +m[5], origin};
      break;
    }
  }
  if (anchor) {
    log('схема найдена:', anchor.deal, anchor.page, `z${anchor.z} x${anchor.x} y${anchor.y}`);
  } else if (DEAL && PAGE) {
    anchor = {deal: DEAL, page: PAGE, z: 0, x: 0, y: 0, origin: 'https://cdn.pamyat-naroda.ru'};
    log('схема задана вручную:', DEAL, PAGE);
  } else {
    const shapes = new Map();
    for (const url of candidates) {
      if (!/\.(png|jpe?g)(\?|$)/i.test(url)) continue;
      let host = '';
      try { host = new URL(url).host; } catch { continue; }
      const path = url.replace(/^https?:\/\/[^/]+/, '').replace(/\d+/g, 'N').split('?')[0];
      const key = host + path;
      shapes.set(key, (shapes.get(key) || 0) + 1);
    }
    console.warn('%c[карта] схема плиток не найдена', 'color:#c00;font-weight:bold');
    console.log('Примеры адресов изображений (числа заменены на N):');
    [...shapes.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15)
      .forEach(([k, n]) => console.log(`  ${n}×  ${k}`));
    console.log('Что делать: 1) подвигать карту и запустить снова; 2) или F12 → Network → фильтр «png» →');
    console.log('скопировать адрес любой плитки, взять из него шифр и лист, заполнить DEAL и PAGE.');
    console.log('Ожидаемый вид: /tiles/<шифр>/<лист>.jpg_tiles/<z>/<x>/<y>.png');
    return;
  }

  const {deal, page, origin} = anchor;
  const tileUrl = (z, x, y) => `${origin}/tiles/${deal}/${page}_tiles/${z}/${x}/${y}.png`;
  log('шаблон плитки:', tileUrl('{z}', '{x}', '{y}'));

  // 3. Запрос плитки: blob | null (нет такой) | 'off' (защита/CORS)
  async function getTile(z, x, y) {
    try {
      const r = await fetch(tileUrl(z, x, y), {credentials: 'include'});
      if (!r.ok) return null;
      const blob = await r.blob();
      return blob.size > 400 ? blob : null;
    } catch (e) {
      return 'off';
    }
  }

  async function pool(items, worker) {
    let i = 0;
    await Promise.all(Array.from({length: CONC}, async () => {
      while (i < items.length) await worker(items[i++]);
    }));
  }

  // 4. Затравка: подъём по родителям до первого существующего мелкого уровня
  let level = new Map(), levelZ = null;
  if (anchor.z > 0) {
    const chain = [];
    let z = anchor.z, x = anchor.x, y = anchor.y;
    while (z > ZMIN) { z -= 1; x = Math.floor(x / 2); y = Math.floor(y / 2); chain.push([z, x, y]); }
    chain.reverse();                       // от мелкого к крупному
    for (const [cz, cx, cy] of chain) {
      const blob = await getTile(cz, cx, cy);
      if (blob === 'off') { log('плитка не отдалась: защита или CORS — см. подсказку в конце'); break; }
      if (blob) { level = new Map([[`${cx}:${cy}`, blob]]); levelZ = cz; break; }
    }
  }
  if (levelZ === null) {
    // шифр задан вручную или родители не нашлись: перебрать мелкую сетку
    const z = anchor.z > 0 ? anchor.z : ZMIN;
    if (anchor.z > 0) {
      const blob = await getTile(anchor.z, anchor.x, anchor.y);
      if (!blob || blob === 'off') { log('плитка не отдалась: защита или CORS'); return; }
      level = new Map([[`${anchor.x}:${anchor.y}`, blob]]); levelZ = anchor.z;
    } else {
      const side = 2 ** z;
      for (let x = 0; x < side; x++) for (let y = 0; y < side; y++) {
        const blob = await getTile(z, x, y);
        if (blob && blob !== 'off') level.set(`${x}:${y}`, blob);
      }
      levelZ = z;
      log(`уровень ${z}: найдено ${level.size}`);
      if (!level.size) { log('ничего не найдено — проверить шифр дела и номер листа'); return; }
    }
  }
  log(`затравка: уровень ${levelZ}, плиток ${level.size}`);

  // 5. Спуск по детям до максимального уровня
  let best = {z: levelZ, tiles: level};
  while (levelZ < ZMAX && level.size) {
    const children = [];
    for (const key of level.keys()) {
      const [x, y] = key.split(':').map(Number);
      for (let dx = 0; dx < 2; dx++) for (let dy = 0; dy < 2; dy++) children.push([x * 2 + dx, y * 2 + dy]);
    }
    if (children.length > MAX_TILES) {
      log(`на уровне ${levelZ + 1} было бы ${children.length} плиток — останавливаюсь`);
      break;
    }
    const next = new Map();
    await pool(children, async ([x, y]) => {
      const blob = await getTile(levelZ + 1, x, y);
      if (blob && blob !== 'off') next.set(`${x}:${y}`, blob);
    });
    if (!next.size) break;
    levelZ += 1;
    level = next;
    best = {z: levelZ, tiles: level};
    log(`уровень ${levelZ}: ${level.size} плиток`);
  }

  // 6. Склейка
  const keys = [...best.tiles.keys()].map(k => k.split(':').map(Number));
  const xs = keys.map(([x]) => x), ys = keys.map(([, y]) => y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = (maxX - minX + 1) * TILE, h = (maxY - minY + 1) * TILE;
  log(`склейка: ${w}x${h} px, zoom ${best.z}, плиток ${best.tiles.size}`);

  if (SERVER) {
    let sent = 0;
    await pool([...best.tiles.entries()], async ([key, blob]) => {
      const [x, y] = key.split(':').map(Number);
      const r = await fetch(`${SERVER}/tile?z=${best.z}&x=${x}&y=${y}`, {
        method: 'POST', body: blob, headers: {'Content-Type': 'image/png'}
      });
      if (r.ok) sent++;
    });
    log(`отправлено в приёмник: ${sent}`);
    log('дальше: /usr/bin/python3 tools/pn_tiles_stitch.py build --out ~/карты/<имя>');
    return;
  }

  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
  let drawn = 0;
  for (const [key, blob] of best.tiles) {
    const [x, y] = key.split(':').map(Number);
    let bmp;
    try {
      bmp = await createImageBitmap(blob);
    } catch (e) {
      log('не удалось раскодировать плитку', x, y, e.message);
      continue;
    }
    // Y в схеме TMS: большее y — ниже по карте
    ctx.drawImage(bmp, (x - minX) * TILE, (maxY - y) * TILE);
    bmp.close?.();
    if (++drawn % 200 === 0) log(`нарисовано ${drawn}/${best.tiles.size}`);
  }

  // 7. Метаданные привязки (Web Mercator)
  const world = 2 * Math.PI * 6378137;
  const span = world / (2 ** best.z);
  const meta = {
    deal, page, zoom: best.z, tiles: best.tiles.size, width: w, height: h,
    bbox_3857: [-world / 2 + minX * span,
                world / 2 - (2 ** best.z - 1 - minY + 1) * span,
                -world / 2 + (maxX + 1) * span,
                world / 2 - (2 ** best.z - 1 - maxY) * span],
    scheme: 'XYZ/TMS, EPSG:3857, tile 256'
  };
  console.log('%c[карта] метаданные', 'color:#08f', meta);
  console.log('JSON:', JSON.stringify(meta));

  canvas.toBlob(blob => {
    if (!blob) { log('canvas не отдал PNG (слишком большой?) — включить режим приёмника SERVER'); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${deal}_${page}_z${best.z}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    log('готово:', a.download, `${(blob.size / 1e6).toFixed(1)} МБ`);
  }, 'image/png');
})();
