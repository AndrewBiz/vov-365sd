/* Скачивание карты с «Памяти народа» — скрипт для консоли браузера.
 *
 * Зачем: просмотрщик отдаёт карту плитками, кнопки «скачать целиком» нет.
 * Скрипт находит схему плиток по уже загруженным запросам страницы, обходит
 * пирамиду до максимального уровня, склеивает всё в один PNG и отдаёт на скачивание.
 *
 * Как пользоваться:
 *   1. Открыть карту на pamyat-naroda.ru и подвигать/помысштабировать её 2–3 раза,
 *      чтобы в журнале появились запросы плиток.
 *   2. F12 → Console → вставить весь этот файл → Enter.
 *   3. Дождаться строки «готово»: браузер скачает <шифр>_<лист>_z<уровень>.png.
 *
 * Ничего не обходит: запросы идут из твоей сессии, ровно те же, что делает просмотрщик.
 */

(async () => {
  // ── настройки ───────────────────────────────────────────────────────────────
  const DEAL = '';          // '' = определить по загруженным запросам страницы
  const PAGE = '';          //     (иначе подставить шифр дела и номер листа)
  const SERVER = '';        // '' = собрать в браузере; 'http://127.0.0.1:8770' = отправить плитки в pn_tiles_stitch.py
  const ZMIN = 5;           // с какого уровня начинать обход
  const ZMAX = 17;          // до какого (реальный предел сервиса скрипт найдёт сам)
  const CONC = 8;           // одновременных запросов
  const MAX_TILES = 8000;   // предохранитель
  const TILE = 256;
  // ────────────────────────────────────────────────────────────────────────────

  const log = (...a) => console.log('%c[карта]', 'color:#0a0', ...a);

  // 1. Найти схему плиток по ресурсам, которые уже загрузил просмотрщик
  const seen = performance.getEntriesByType('resource')
    .map(e => e.name)
    .filter(u => u.includes('/tiles/') && u.includes('_tiles/'));
  let deal = DEAL, page = PAGE;
  if (seen.length) {
    const m = seen[0].match(/\/tiles\/([^/]+)\/([^/]+?)_tiles\//);
    if (m) {
      deal = deal || m[1];
      page = page || m[2];
      log('схема найдена в журнале:', m[1], m[2]);
    }
  } else {
    log('в журнале нет запросов плиток — подвигать карту и запустить снова,');
    log('или получить шифр из DevTools → Network → любая плитка.');
  }
  if (!deal || !page) throw new Error('не определён шифр дела/лист — заполнить DEAL и PAGE в скрипте');

  const tileUrl = (z, x, y) => `https://cdn.pamyat-naroda.ru/tiles/${deal}/${page}_tiles/${z}/${x}/${y}.png`;

  // 2. Запрос одной плитки: null = нет такой, 'off' = сеть/CORS не пускает
  let corsBroken = false;
  async function getTile(z, x, y) {
    try {
      const r = await fetch(tileUrl(z, x, y), {credentials: 'include', cache: 'force-cache'});
      if (!r.ok) return null;
      const blob = await r.blob();
      if (blob.size < 400) return null;
      return blob;
    } catch (e) {
      corsBroken = true;
      return 'off';
    }
  }

  const probe = await getTile(0, 0, 0);
  if (probe === 'off') {
    log('браузер не отдал плитку: CORS или защита. Проверить F12 → Network → плитка → 200?');
    log('Если 200, но fetch падает — включить режим приёмника: запустить');
    log('  python3 tools/pn_tiles_stitch.py serve   и поставить SERVER = "http://127.0.0.1:8770"');
    return;
  }

  // 3. Обход пирамиды: от мелкого уровня вниз, по детям найденных плиток
  async function pool(items, worker) {
    let i = 0;
    const runners = Array.from({length: CONC}, async () => {
      while (i < items.length) {
        const item = items[i++];
        await worker(item);
      }
    });
    await Promise.all(runners);
  }

  let level = new Map();          // "x:y" -> blob
  let z = ZMIN;
  const started = Date.now();
  const countAt = n => `${n} плиток, ${((Date.now() - started) / 1000).toFixed(0)} с`;

  // стартовый уровень: перебрать всю сетку (на z=5 это 1024 плитки — быстро)
  const side = 2 ** z;
  const seeds = [];
  for (let x = 0; x < side; x++) for (let y = 0; y < side; y++) seeds.push([x, y]);
  log(`уровень ${z}: проверяю ${seeds.length} плиток`);
  await pool(seeds, async ([x, y]) => {
    const blob = await getTile(z, x, y);
    if (blob && blob !== 'off') level.set(`${x}:${y}`, blob);
  });
  log(`уровень ${z}: найдено ${level.size}`);

  let best = {z, tiles: level};
  while (z < ZMAX && level.size) {
    const next = new Map();
    const children = [];
    for (const key of level.keys()) {
      const [x, y] = key.split(':').map(Number);
      for (let dx = 0; dx < 2; dx++) for (let dy = 0; dy < 2; dy++) {
        children.push([x * 2 + dx, y * 2 + dy]);
      }
    }
    if (children.length > MAX_TILES) {
      log(`на уровне ${z + 1} было бы ${children.length} плиток — останавливаюсь`);
      break;
    }
    await pool(children, async ([x, y]) => {
      const blob = await getTile(z + 1, x, y);
      if (blob && blob !== 'off') next.set(`${x}:${y}`, blob);
    });
    if (!next.size) break;
    z += 1;
    level = next;
    best = {z, tiles: level};
    log(`уровень ${z}: ${level.size} плиток (${countAt(level.size)})`);
  }

  // 4. Склеить и отдать файл
  const keys = [...best.tiles.keys()].map(k => k.split(':').map(Number));
  const xs = keys.map(([x]) => x), ys = keys.map(([, y]) => y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = (maxX - minX + 1) * TILE, h = (maxY - minY + 1) * TILE;
  log(`склейка: ${w}x${h} px, плиток ${best.tiles.size}, zoom ${best.z}`);

  if (SERVER) {
    let sent = 0;
    await pool([...best.tiles.entries()], async ([key, blob]) => {
      const [x, y] = key.split(':').map(Number);
      const r = await fetch(`${SERVER}/tile?z=${best.z}&x=${x}&y=${y}`, {
        method: 'POST', body: blob, headers: {'Content-Type': 'image/png'}
      });
      if (r.ok) sent++;
    });
    log(`отправлено плиток в приёмник: ${sent}`);
    log(`дальше: python3 tools/pn_tiles_stitch.py build --out ~/путь/карта`);
    return;
  }

  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
  let drawn = 0;
  for (const [key, blob] of best.tiles) {
    const [x, y] = key.split(':').map(Number);
    const bmp = await createImageBitmap(blob);
    // Y в схеме TMS: большее y — ниже по карте
    ctx.drawImage(bmp, (x - minX) * TILE, (maxY - y) * TILE);
    bmp.close?.();
    if (++drawn % 200 === 0) log(`нарисовано ${drawn}/${best.tiles.size}`);
  }

  // 5. Метаданные: границы в EPSG:3857 — пригодятся для привязки
  const world = 2 * Math.PI * 6378137;
  const span = world / (2 ** best.z);
  const west = -world / 2 + minX * span;
  const east = -world / 2 + (maxX + 1) * span;
  const north = world / 2 - (2 ** best.z - 1 - maxY) * span;
  const south = world / 2 - (2 ** best.z - 1 - minY + 1) * span;
  const meta = {deal, page, zoom: best.z, tiles: best.tiles.size, width: w, height: h,
                bbox_3857: [west, south, east, north], scheme: 'XYZ/TMS, EPSG:3857'};
  console.log('%c[карта] метаданные', 'color:#08f', meta);
  console.log('JSON:', JSON.stringify(meta));

  canvas.toBlob(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${deal}_${page}_z${best.z}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    log('готово:', a.download, `${(blob.size / 1e6).toFixed(1)} МБ`);
  }, 'image/png');
})();
