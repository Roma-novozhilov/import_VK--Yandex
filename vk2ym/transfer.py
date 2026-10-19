"""Оркестрация переноса: поиск треков в Яндексе с кешем, затем лайки / плейлист, отчёт."""
from __future__ import annotations

import csv
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from .models import MatchResult, SourceTrack, TransferStats
from .yandex import YandexMusic

log = logging.getLogger(__name__)

ProgressCb = Callable[[TransferStats, Optional[MatchResult]], None]


class MatchCache:
    """JSON-кеш результатов поиска: повторный запуск не ищет уже найденное заново."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("Кеш %s повреждён, начинаю заново", self.path)

    def get(self, src: SourceTrack, min_score: float) -> Optional[MatchResult]:
        d = self.data.get(src.key)
        if not d or d.get("status") == "error":
            return None
        r = MatchResult.from_dict(d)
        r.source = src
        # Порог мог измениться с прошлого запуска — пересчитываем статус по сохранённой оценке
        if r.ym_track_id:
            r.status = "found" if r.score >= min_score else "not_found"
        return r

    def put(self, r: MatchResult) -> None:
        with self._lock:
            self.data[r.source.key] = r.to_dict()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


def run_transfer(
    tracks: list[SourceTrack],
    ym: YandexMusic,
    *,
    like: bool = True,
    playlist_title: Optional[str] = None,
    keep_order: bool = True,
    cache: Optional[MatchCache] = None,
    on_progress: Optional[ProgressCb] = None,
    stop_event: Optional[threading.Event] = None,
) -> TransferStats:
    stats = TransferStats(total=len(tracks))
    results: list[MatchResult] = []

    for i, src in enumerate(tracks):
        if stop_event and stop_event.is_set():
            log.info("Остановлено пользователем")
            break
        r = cache.get(src, getattr(ym, "min_score", 0.0)) if cache else None
        if r is None:
            r = ym.find(src)
            if cache:
                cache.put(r)
                if i % 20 == 0:
                    cache.save()
        results.append(r)
        stats.processed += 1
        if r.status == "found":
            stats.found += 1
        elif r.status == "not_found":
            stats.not_found += 1
        else:
            stats.errors += 1
        if on_progress:
            on_progress(stats, r)

    if cache:
        cache.save()
    stats.results = results

    if stop_event and stop_event.is_set():
        stats.stage = "stopped"
        return stats

    found = [r for r in results if r.status == "found"]
    stats.stage = "writing"
    if on_progress:
        on_progress(stats, None)
    if like and found:
        def step(done, total):
            stats.write_done, stats.write_total = done, total
            if on_progress and done % 5 == 0:
                on_progress(stats, None)
        stats.liked = ym.like(found, keep_order=keep_order, on_step=step)
    if playlist_title and found:
        stats.playlisted = ym.add_to_playlist(playlist_title, found)
    stats.stage = "done"
    if on_progress:
        on_progress(stats, None)
    return stats


def write_report(results: list[MatchResult], path: Path | str) -> Path:
    """CSV-отчёт (открывается в Excel): что нашлось, что нет и с какой уверенностью."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Статус", "ВК: исполнитель", "ВК: название", "Яндекс: исполнитель",
                    "Яндекс: название", "Уверенность", "Ссылка", "Ошибка"])
        status_ru = {"found": "найден", "not_found": "не найден", "error": "ошибка"}
        for r in results:
            link = (f"https://music.yandex.ru/album/{r.ym_album_id}/track/{r.ym_track_id}"
                    if r.ym_track_id else "")
            w.writerow([status_ru.get(r.status, r.status), r.source.artist, r.source.title,
                        r.ym_artist, r.ym_title, f"{r.score:.2f}", link, r.error])
    return path
