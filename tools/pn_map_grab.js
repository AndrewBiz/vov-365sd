/* Скачивание карты с «Памяти народа» — скрипт для консоли браузера (v3).
 *
 * Зачем: просмотрщик отдаёт карту плитками, кнопки «скачать целиком» нет.
 * Скрипт находит адрес плитки в журнале запросов, <img> или инлайн-стилях, поднимается
 * по родителям до мелкого уровня, спускается до максимального, склеивает PNG/GeoTIFF-пару
 * и печатает bbox в EPSG:3857.
 *
 * Загрузка плиток идёт тремя способами по очереди, потому что у CDN политика CORS меняется:
 *   1) fetch без cookie  → 2) fetch с cookie  → 3) <img crossOrigin="anonymous">  → 4) <img> без CORS.
 * Первый сработавший способ запоминается и печатается. Если сработал только 4-й, canvas
 * «испачкан» и PNG не выгрузить — тогда в консоли будет предупреждение (запускать в Chrome).
 *
 * Как пользоваться:
 *   1. Открыть карту и подвигать/помысштабировать её 2–3 раза.
 *   2. F12 → Console → вставить весь файл → Enter.
 *
 * Ничего не обходит: те же запросы, что делает просмотрщик.
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
  const lastErr = {};

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
    console.log('Что делать: подвигать карту и запустить снова, либо взять шифр и лист из адреса плитки');
    console.log('(F12 → Network → фильтр «png») и заполнить DEAL/PAGE в настройках.');
    return;
  }

  const {deal, page, origin} = anchor;
  const tileUrl = (z, x, y) => `${origin}/tiles/${deal}/${page}_tiles/${z}/${x}/${y}.png`;
  log('шаблон плитки:', tileUrl('{z}', '{x}', '{y}'));

  // 3. Загрузка плитки: сначала fetch, потом <img>. Возвращает {blob} | {img} | {missing} | {off}
  let method = null;

  async function byFetch(z, x, y) {
    const url = tileUrl(z, x, y);
    for (const init of [{mode: 'cors'}, {mode: 'cors', credentials: 'include'}]) {
      try {
        const r = await fetch(url, init);
        if (!r.ok) return {missing: true};
        const blob = await r.blob();
        return blob.size > 400 ? {blob} : {missing: true};
      } catch (e) {
        lastErr.fetch = (e && (e.message || e.name)) || String(e) || 'fetch отклонён';
      }
    }
    return null;
  }

  function byImage(z, x, y, cross) {
    const url = tileUrl(z, x, y);
    return new Promise(resolve => {
      const img = new Image();
      if (cross) img.crossOrigin = 'anonymous';
      img.decoding = 'async';
      let done = false;
      const finish = value => { if (!done) { done = true; resolve(value); } };
      img.onload = () => finish(img.naturalWidth > 0 ? {img} : {missing: true});
      img.onerror = () => {
        lastErr[cross ? 'img-cors' : 'img'] = `onerror: ${url}`;
        finish(null);
      };
      setTimeout(() => finish(null), 20000);
      img.src = url;
    });
  }

  async function loadTile(z, x, y) {
    if (method === null || method === 'fetch') {
      const got = await byFetch(z, x, y);
      if (got) { if (got.blob) method = 'fetch'; return got; }
    }
    if (method === null || method === 'img-cors') {
      const got = await byImage(z, x, y, true);
      if (got) { method = 'img-cors'; return got; }
    }
    if (method === null || method === 'img') {
      const got = await byImage(z, x, y, false);
      if (got) { method = 'img'; return got; }
    }
    return {off: true};
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
    chain.reverse();
    for (const [cz, cx, cy] of chain) {
      const tile = await loadTile(cz, cx, cy);
      if (tile.off) {
        log('плитка не отдалась. Причины:', lastErr);
        log('если в lastErr есть onerror — CDN не пускает по CORS даже через <img>; тогда нужно');
        log('скачивать в Chromium (там та же политика мягче) или через приёмник SERVER в Chrome.');
        return;
      }
      if (tile.missing) continue;
      level = new Map([[`${cx}:${cy}`, tile]]);
      levelZ = cz;
      break;
    }
  }
  if (levelZ === null) {
    const z = anchor.z > 0 ? anchor.z : 4;
    if (anchor.z > 0) {
      const tile = await loadTile(anchor.z, anchor.x, anchor.y);
      if (tile.off || tile.missing) { log('затравка не загрузилась. Причины:', lastErr); return; }
      level = new Map([[`${anchor.x}:${anchor.y}`, tile]]); levelZ = anchor.z;
    } else {
      const side = 2 ** z;
      for (let x = 0; x < side; x++) for (let y = 0; y < side; y++) {
        const tile = await loadTile(z, x, y);
        if (tile && !tile.off && !tile.missing) level.set(`${x}:${y}`, tile);
      }
      levelZ = z;
      log(`уровень ${z}: найдено ${level.size}`);
      if (!level.size) { log('ничего не найдено — проверить шифр дела и номер листа'); return; }
    }
  }
  log(`затравка: уровень ${levelZ}, плиток ${level.size}, способ загрузки: ${method}`);

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
      const tile = await loadTile(levelZ + 1, x, y);
      if (tile && !tile.off && !tile.missing) next.set(`${x}:${y}`, tile);
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

  const tileCanvas = document.createElement('canvas');
  tileCanvas.width = TILE; tileCanvas.height = TILE;
  const tileCtx = tileCanvas.getContext('2d');

  function imgToBlob(img) {
    tileCtx.clearRect(0, 0, TILE, TILE);
    tileCtx.drawImage(img, 0, 0, TILE, TILE);
    return new Promise(resolve => {
      try { tileCanvas.toBlob(resolve, 'image/png'); } catch (e) { resolve(null); }
    });
  }

  if (SERVER) {
    let sent = 0;
    await pool([...best.tiles.entries()], async ([key, tile]) => {
      const [x, y] = key.split(':').map(Number);
      const blob = tile.blob || await imgToBlob(tile.img);
      if (!blob) return;
      const r = await fetch(`${SERVER}/tile?z=${best.z}&x=${x}&y=${y}`, {
        method: 'POST', body: blob, headers: {'Content-Type': 'image/png'}
      });
      if (r.ok) sent++;
    });
    log(`отправлено в приёмник: ${sent} из ${best.tiles.size}`);
    log('дальше: /usr/bin/python3 tools/pn_tiles_stitch.py build --out ~/карты/<имя>');
    return;
  }

  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
  let drawn = 0;
  for (const [key, tile] of best.tiles) {
    const [x, y] = key.split(':').map(Number);
    const dx = (x - minX) * TILE, dy = (maxY - y) * TILE;   // Y в схеме TMS
    if (tile.blob) {
      try {
        const bmp = await createImageBitmap(tile.blob);
        ctx.drawImage(bmp, dx, dy);
        bmp.close?.();
      } catch (e) {
        log('плитка не раскодировалась', x, y, e.message);
      }
    } else {
      ctx.drawImage(tile.img, dx, dy);
    }
    if (++drawn % 200 === 0) log(`нарисовано ${drawn}/${best.tiles.size}`);
  }

  // 7. Метаданные привязки
  const world = 2 * Math.PI * 6378137;
  const span = world / (2 ** best.z);
  const meta = {
    deal, page, zoom: best.z, tiles: best.tiles.size, width: w, height: h,
    bbox_3857: [-world / 2 + minX * span,
                world / 2 - (2 ** best.z - 1 - minY + 1) * span,
                -world / 2 + (maxX + 1) * span,
                world / 2 - (2 ** best.z - 1 - maxY) * span],
    scheme: 'XYZ/TMS, EPSG:3857, tile 256',
    fetch_method: method
  };
  console.log('%c[карта] метаданные', 'color:#08f', meta);
  console.log('JSON:', JSON.stringify(meta));

  try {
    canvas.toBlob(blob => {
      if (!blob) { log('canvas не отдал PNG (слишком большой?) — включить SERVER-режим'); return; }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${deal}_${page}_z${best.z}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      log('готово:', a.download, `${(blob.size / 1e6).toFixed(1)} МБ`);
    }, 'image/png');
  } catch (e) {
    log('canvas «испачкан» (плитки пришли без CORS): PNG не выгрузить.');
    log('решение: запустить этот же скрипт в Chromium или собрать через SERVER-приёмник.');
  }
})();
