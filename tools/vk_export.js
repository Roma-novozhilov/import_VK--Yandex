/*
 * Выгрузка «Моей музыки» ВКонтакте в текстовый файл без API.
 *
 * 1. Открой https://vk.com/audios (или страницу нужного плейлиста) в браузере, где ты залогинен.
 * 2. Открой консоль: F12 → вкладка Console. В Chrome при первой вставке может понадобиться
 *    ввести «allow pasting» и нажать Enter.
 * 3. Вставь весь этот скрипт и нажми Enter. Не трогай вкладку, пока он прокручивает страницу.
 * 4. Скачается vk_music.txt — его можно вставить во вкладку «Вставить список» или передать в cli.py --from-file.
 */
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const decode = (s) => { const t = document.createElement("textarea"); t.innerHTML = s ?? ""; return t.value.trim(); };
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const tracks = new Map();
  let method = "";

  const add = (artist, title, how) => {
    artist = clean(decode(artist)); title = clean(decode(title));
    if (!title) return;
    const key = (artist + "|" + title).toLowerCase();
    if (!tracks.has(key)) tracks.set(key, { artist, title });
    method = method || how;
  };

  const pick = (root, selectors) => {
    for (const sel of selectors) {
      // Берём самый «глубокий» элемент: обёртка вида .title-wrapper может содержать и название, и исполнителя
      const els = [...root.querySelectorAll(sel)].filter((el) => clean(el.textContent) && !el.querySelector(sel));
      if (els.length) return clean(els[0].textContent);
    }
    return "";
  };

  function collect() {
    // 1) Классическая разметка: JSON в атрибуте data-audio ([id, owner, url, title, artist, duration, ...])
    document.querySelectorAll("[data-audio]").forEach((el) => {
      try {
        const a = JSON.parse(el.getAttribute("data-audio"));
        if (Array.isArray(a) && a.length > 4) add(a[4], a[3], "data-audio");
      } catch (_) { /* не JSON — пропускаем */ }
    });
    // 2) Классические классы строк
    document.querySelectorAll(".audio_row, .audio_row__inner").forEach((row) => {
      const title = pick(row, [".audio_row__title_inner", ".audio_row__title"]);
      const artist = pick(row, [".audio_row__performers", ".audio_row__performer"]);
      if (title) add(artist, title, "audio_row");
    });
    // 3) Новая разметка (VKUI): ищем строки по data-testid / class и внутри — название и исполнителя
    const rowSel = '[data-testid*="track" i], [data-testid*="audio" i], [class*="AudioRow" i], [class*="TrackRow" i], [class*="MusicTrack" i]';
    document.querySelectorAll(rowSel).forEach((row) => {
      if (row.parentElement && row.parentElement.closest(rowSel)) return; // берём только внешнюю строку
      const title = pick(row, ['[data-testid*="title" i]', '[class*="Title" i]', '[class*="title" i]']);
      const artist = pick(row, ['[data-testid*="artist" i]', '[data-testid*="performer" i]', '[class*="Artist" i]',
        '[class*="Performer" i]', '[class*="subtitle" i]', '[class*="Subtitle" i]', 'a[href*="artist" i]']);
      if (title && title !== artist) add(artist, title, "vkui");
    });
  }

  // Прокрутка: и окно, и самый большой прокручиваемый контейнер (в новом интерфейсе список может скроллиться внутри блока)
  function scrollers() {
    const list = [document.scrollingElement || document.documentElement];
    let best = null;
    document.querySelectorAll("div, main, section").forEach((el) => {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 200) {
        if (!best || el.scrollHeight > best.scrollHeight) best = el;
      }
    });
    if (best) list.push(best);
    return list;
  }

  console.log("%cВК → Яндекс: собираю треки, не закрывай вкладку…", "color:#3d8bff;font-weight:bold");
  let stable = 0, last = -1;
  for (let i = 0; i < 2000 && stable < 6; i++) {
    collect();
    scrollers().forEach((el) => el.scrollTo(0, el.scrollHeight));
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(900);
    if (tracks.size === last) stable++; else { stable = 0; last = tracks.size; }
    if (i % 5 === 0) console.log(`Найдено треков: ${tracks.size}`);
  }
  collect();

  if (!tracks.size) {
    const sample = document.querySelector('[data-audio], .audio_row, [data-testid*="track" i], [class*="audio" i]');
    console.warn("Не удалось найти треки. Пришли разработчику этот фрагмент разметки:");
    console.log(sample ? sample.outerHTML.slice(0, 3000) : document.body.innerHTML.slice(0, 3000));
    return;
  }

  const lines = [...tracks.values()].map((t) => (t.artist ? `${t.artist} - ${t.title}` : t.title));
  const text = lines.join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = "vk_music.txt";
  document.body.appendChild(a); a.click(); a.remove();
  try { await navigator.clipboard.writeText(text); } catch (_) { /* буфер может быть недоступен — файл всё равно скачан */ }

  console.log(`%cГотово: ${lines.length} треков (способ: ${method}). Файл vk_music.txt скачан и скопирован в буфер.`,
    "color:#3ccf8e;font-weight:bold");
  console.log("Первые строки для проверки:\n" + lines.slice(0, 10).join("\n"));
})();
