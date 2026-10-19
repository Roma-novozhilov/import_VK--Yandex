"""Получение музыки из ВКонтакте.

Аудио-методы VK API закрыты для обычных приложений, поэтому используется
токен официального клиента (VK Android или Kate Mobile) через библиотеку vkpymusic.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Iterable, Optional

from vkpymusic import KateMobileClient, Service, TokenReceiver, VkMobileClient

from .models import SourceTrack

log = logging.getLogger(__name__)

CLIENTS = {"vk": VkMobileClient, "kate": KateMobileClient}

# Ссылка для получения токена Kate Mobile вручную (токен будет в адресной строке после входа)
KATE_TOKEN_URL = (
    "https://oauth.vk.com/authorize?client_id=2685278&scope=audio,offline"
    "&redirect_uri=https://oauth.vk.com/blank.html&display=page&response_type=token&revoke=1"
)

_TOKEN_IN_URL_RE = re.compile(r"access_token=([^&\s]+)")


def clean_token(raw: str) -> str:
    """Принимает как сам токен, так и целиком скопированную ссылку blank.html#access_token=..."""
    raw = (raw or "").strip()
    m = _TOKEN_IN_URL_RE.search(raw)
    return m.group(1) if m else raw


def login(login: str, password: str,
          on_2fa: Optional[Callable[[], str]] = None,
          on_captcha: Optional[Callable[[str], str]] = None) -> str:
    """Авторизация по логину/паролю. Возвращает токен (клиент VK Android)."""
    receiver = TokenReceiver(login=login, password=password, client=VkMobileClient)
    kwargs = {}
    if on_2fa:
        kwargs["on_2fa"] = on_2fa
    if on_captcha:
        kwargs["on_captcha"] = on_captcha
    if not receiver.auth(**kwargs):
        raise RuntimeError("Не удалось войти в ВК: проверь логин, пароль и код подтверждения")
    token = receiver.get_token()
    if not token:
        raise RuntimeError("ВК не вернул токен")
    return token


class VkMusic:
    def __init__(self, token: str, client: str = "vk"):
        if client not in CLIENTS:
            raise ValueError(f"Неизвестный клиент {client!r}, допустимо: {', '.join(CLIENTS)}")
        self.service = Service(CLIENTS[client].user_agent, clean_token(token))
        self._user_id: Optional[int] = None

    @property
    def user_id(self) -> int:
        if self._user_id is None:
            self._user_id = int(self.service.get_user_info().userid)
        return self._user_id

    def user_name(self) -> str:
        info = self.service.get_user_info()
        self._user_id = int(info.userid)
        return f"{info.first_name} {info.last_name}".strip()

    def get_my_tracks(self, owner_id: Optional[int] = None) -> list[SourceTrack]:
        """Все аудиозаписи пользователя в порядке ВК (сверху — последние добавленные)."""
        owner_id = owner_id or self.user_id
        total = self.service.get_count_by_user_id(owner_id)
        tracks: list[SourceTrack] = []
        offset = 0
        while offset < total:
            batch = self.service.get_songs_by_userid(owner_id, count=100, offset=offset)
            if not batch:
                break
            tracks.extend(_to_source(batch))
            offset += len(batch)
        log.info("ВК: получено %s из %s треков", len(tracks), total)
        return tracks

    def get_playlists(self, owner_id: Optional[int] = None) -> list[dict]:
        owner_id = owner_id or self.user_id
        result, offset = [], 0
        while True:
            batch = self.service.get_playlists_by_userid(owner_id, count=100, offset=offset)
            if not batch:
                break
            for p in batch:
                result.append({
                    "id": p.playlist_id, "owner_id": p.owner_id, "access_key": p.access_key,
                    "title": p.title, "count": p.count,
                })
            if len(batch) < 100:
                break
            offset += len(batch)
        return result

    def get_playlist_tracks(self, owner_id: int, playlist_id: int, access_key: str = "") -> list[SourceTrack]:
        tracks: list[SourceTrack] = []
        offset = 0
        while True:
            batch = self.service.get_songs_by_playlist_id(
                owner_id, playlist_id, access_key or "", count=100, offset=offset
            )
            if not batch:
                break
            tracks.extend(_to_source(batch))
            if len(batch) < 100:
                break
            offset += len(batch)
        return tracks


def _to_source(songs: Iterable) -> list[SourceTrack]:
    out = []
    for s in songs:
        out.append(SourceTrack(artist=str(s.artist or "").strip(),
                               title=str(s.title or "").strip(),
                               duration=int(s.duration or 0)))
    return out


_LINE_RE = re.compile(r"^\s*(?:\d+[\.\)]\s*)?(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+?)\s*$")


def parse_text_list(text: str) -> list[SourceTrack]:
    """Запасной вариант: список строк вида «Исполнитель - Название» (по одной на строку)."""
    tracks = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            tracks.append(SourceTrack(artist=m["artist"], title=m["title"]))
        else:
            tracks.append(SourceTrack(artist="", title=line))
    return tracks
