/*
 * Выгрузка «Моей музыки» ВКонтакте в текстовый файл без API.
 *
 * 1. Открой в браузере, где ты залогинен, страницу со СПИСКОМ треков: «Моя музыка» → «Треки»
 *    (или страницу нужного плейлиста). Главная витрина музыки с подборками не подойдёт.
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
    // 3) Любая другая разметка: у каждой строки трека есть длительность «3:45».
    //    Находим её, поднимаемся до строки трека и берём строки текста: первая — название, вторая — исполнитель.
    if (!document.querySelector("[data-audio], .audio_row")) {
      document.querySelectorAll("body *").forEach((el) => {
        if (el.children.length || !DURATION.test(clean(el.textContent))) return;
        const row = trackRow(el);
        if (!row) return;
        const lines = row.innerText.split("\n").map(clean)
          .filter((l) => l.length > 1 && !DURATION.test(l) && !JUNK.test(l));
        if (lines.length >= 2) add(lines[1], lines[0].replace(/\s+E$/, ""), "duration"); // «E» — значок explicit
      });
    }
  }

  const DURATION = /^\d{1,2}:\d{2}(:\d{2})?$/;
  const JUNK = /^(показать все|слушать|e|18\+|hq|lossless|explicit)$/i;
  const countDurations = (el) => (el.innerText.match(/(^|\n)\s*\d{1,2}:\d{2}(:\d{2})?\s*(\n|$)/g) || []).length;

  // Строка трека — самый внешний предок, в котором всё ещё ровно одна длительность (не выше 10 уровней)
  function trackRow(el) {
    let row = null, cur = el.parentElement;
    for (let i = 0; cur && cur !== document.body && i < 10; i++, cur = cur.parentElement) {
      if (countDurations(cur) !== 1) break;
      row = cur;
    }
    return row;
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
    const sample = document.querySelector('[data-audio], .audio_row, [class*="audio" i], [class*="track" i]');
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
