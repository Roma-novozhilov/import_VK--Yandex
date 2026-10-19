"""Работа с Яндекс Музыкой: поиск треков, лайки, плейлисты."""
from __future__ import annotations

import json
import logging
import re
import time
from yandex_music import Client
from yandex_music.exceptions import NetworkError, TimedOutError, YandexMusicError

from . import matcher
from .models import MatchResult, SourceTrack

log = logging.getLogger(__name__)

# OAuth-ссылка официального приложения Яндекс Музыки. После входа токен будет в адресе страницы.
YANDEX_TOKEN_URL = (
    "https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)

_TOKEN_IN_URL_RE = re.compile(r"access_token=([^&\s]+)")


def clean_token(raw: str) -> str:
    raw = (raw or "").strip()
    m = _TOKEN_IN_URL_RE.search(raw)
    return m.group(1) if m else raw


class YandexMusic:
    def __init__(self, token: str, min_score: float = 0.62, delay: float = 0.25):
        self.client = Client(clean_token(token)).init()
        if not self.client.me or not self.client.me.account or not self.client.me.account.uid:
            raise RuntimeError("Токен Яндекс Музыки недействителен")
        self.min_score = min_score
        self.delay = delay

    def user_name(self) -> str:
        acc = self.client.me.account
        return acc.display_name or acc.login or str(acc.uid)

    # ---------- поиск ----------

    def _search(self, query: str):
        for attempt in range(4):
            try:
                res = self.client.search(query, type_="track", nocorrect=False)
                time.sleep(self.delay)
                return (res.tracks.results if res and res.tracks else []) or []
            except (NetworkError, TimedOutError) as e:
                wait = 2 * (attempt + 1)
                log.warning("Ошибка сети при поиске (%s), повтор через %s c", e, wait)
                time.sleep(wait)
        raise RuntimeError("Яндекс не отвечает на поиск")

    def find(self, src: SourceTrack) -> MatchResult:
        queries = []
        base = f"{src.artist} {src.title}".strip()
        queries.append(base)
        cleaned = f"{' '.join(matcher.split_artists(src.artist)[:1])} {matcher.normalize(src.title)}".strip()
        if cleaned and cleaned.lower() != base.lower():
            queries.append(cleaned)

        best, best_score = None, 0.0
        try:
            for q in queries:
                for cand in self._search(q)[:8]:
                    if not cand.available and not cand.albums:
                        continue
                    cand_artists = [a.name for a in (cand.artists or [])]
                    if src.artist:
                        s = matcher.score(
                            src.artist, src.title, src.duration,
                            cand_artists, _full_title(cand), int((cand.duration_ms or 0) / 1000),
                        )
                    else:  # строка из списка без разделителя «-» — сравниваем целиком
                        s = matcher.loose_score(src.title, cand_artists, _full_title(cand))
                    if s > best_score:
                        best, best_score = cand, s
                if best_score >= 0.9:
                    break
        except Exception as e:  # noqa: BLE001
            return MatchResult(source=src, status="error", error=str(e))

        if best is None or best_score < self.min_score or not best.albums:
            r = MatchResult(source=src, status="not_found", score=round(best_score, 3))
            if best is not None:  # сохраняем лучшего кандидата: пригодится при смене порога и в отчёте
                r.ym_artist = ", ".join(a.name for a in (best.artists or []))
                r.ym_title = _full_title(best)
                if best.albums:
                    r.ym_track_id, r.ym_album_id = str(best.id), str(best.albums[0].id)
            return r

        return MatchResult(
            source=src, status="found",
            ym_track_id=str(best.id), ym_album_id=str(best.albums[0].id),
            ym_artist=", ".join(a.name for a in (best.artists or [])),
            ym_title=_full_title(best), score=round(best_score, 3),
        )

    # ---------- запись ----------

    def liked_ids(self) -> set[str]:
        likes = self.client.users_likes_tracks()
        return {str(t.id).split(":")[0] for t in (likes.tracks if likes else [])}

    def like(self, matches: list[MatchResult], skip_existing: bool = True) -> int:
        """Ставит лайки. Добавляем с конца списка, чтобы порядок «Мне нравится» совпал с ВК."""
        existing = self.liked_ids() if skip_existing else set()
        todo = [m for m in matches if m.ym_track_id and m.ym_track_id not in existing]
        todo = _dedupe(todo)
        added = 0
        for m in reversed(todo):
            self._retry(self.client.users_likes_tracks_add, f"{m.ym_track_id}:{m.ym_album_id}")
            added += 1
            time.sleep(self.delay)
        return added

    def get_or_create_playlist(self, title: str):
        for p in self.client.users_playlists_list() or []:
            if p.title == title:
                return p
        return self.client.users_playlists_create(title, visibility="private")

    def add_to_playlist(self, title: str, matches: list[MatchResult]) -> int:
        """Добавляет найденные треки в конец плейлиста (без дублей), порядок как в ВК."""
        playlist = self.get_or_create_playlist(title)
        full = self.client.users_playlists(playlist.kind)
        existing = {str(t.id).split(":")[0] for t in (full.tracks or [])} if full else set()
        revision = full.revision if full else playlist.revision
        count = full.track_count if full else 0

        todo = [m for m in _dedupe(matches) if m.ym_track_id and m.ym_track_id not in existing]
        added = 0
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            diff = json.dumps([{
                "op": "insert", "at": count,
                "tracks": [{"id": m.ym_track_id, "albumId": m.ym_album_id} for m in chunk],
            }])
            updated = self._retry(self.client.users_playlists_change, playlist.kind, diff, revision)
            revision = updated.revision
            count += len(chunk)
            added += len(chunk)
            time.sleep(self.delay)
        return added

    @staticmethod
    def _retry(fn, *args):
        for attempt in range(4):
            try:
                return fn(*args)
            except (NetworkError, TimedOutError):
                time.sleep(2 * (attempt + 1))
            except YandexMusicError:
                if attempt == 3:
                    raise
                time.sleep(2)
        return fn(*args)


def _full_title(track) -> str:
    title = track.title or ""
    if getattr(track, "version", None):
        title = f"{title} ({track.version})"
    return title


def _dedupe(matches: list[MatchResult]) -> list[MatchResult]:
    seen, out = set(), []
    for m in matches:
        if m.ym_track_id and m.ym_track_id not in seen:
            seen.add(m.ym_track_id)
            out.append(m)
    return out
