"""Нормализация названий и оценка совпадения треков."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_FEAT_RE = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|при уч\.?)\s+[^\)\]]*[\)\]]?", re.I)
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_JUNK_RE = re.compile(
    r"\b(official|video|audio|lyrics?|hd|hq|clip|клип|remaster(ed)?( \d{4})?|prod\.? by [\w\s]+)\b",
    re.I,
)
_NON_WORD_RE = re.compile(r"[^\w\s]", re.U)
_SPACES_RE = re.compile(r"\s+")
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\bx\b|\band\b|\bи\b|\bfeat\.?|\bft\.?|/|;)\s*", re.I)

# Пометки версий: если они есть только у одной из сторон — это, скорее всего, другой трек
_VERSION_WORDS = ("remix", "ремикс", "acoustic", "live", "instrumental", "speed up", "sped up",
                  "slowed", "cover", "karaoke", "минус", "nightcore", "mashup", "edit", "version")


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _FEAT_RE.sub(" ", text)
    text = _BRACKETS_RE.sub(" ", text)
    text = _JUNK_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    return _SPACES_RE.sub(" ", text).strip()


def split_artists(artist: str) -> list[str]:
    parts = [normalize(p) for p in _ARTIST_SPLIT_RE.split(artist or "")]
    return [p for p in parts if p]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _version_tags(text: str) -> set[str]:
    low = (text or "").lower()
    return {w for w in _VERSION_WORDS if w in low}


def loose_score(line: str, cand_artists: list[str], cand_title: str) -> float:
    """Для строки без явного исполнителя: сравниваем её с «исполнитель название» и «название исполнитель»."""
    src = normalize(line)
    a = normalize(" ".join(cand_artists))
    t = normalize(cand_title)
    best = max(_ratio(src, f"{a} {t}"), _ratio(src, f"{t} {a}"), _ratio(src, t) * 0.85)
    return best * 0.95


def score(src_artist: str, src_title: str, src_duration: int,
          cand_artists: list[str], cand_title: str, cand_duration: int) -> float:
    """Возвращает оценку 0..1, насколько кандидат из Яндекса похож на исходный трек."""
    # Название
    title_score = _ratio(normalize(src_title), normalize(cand_title))

    # Исполнитель: лучшая пара среди всех исполнителей с обеих сторон
    src_list = split_artists(src_artist) or [normalize(src_artist)]
    cand_list = [normalize(a) for a in cand_artists if a] or [""]
    artist_score = max(_ratio(s, c) for s in src_list for c in cand_list)
    # Целая строка исполнителей тоже может совпасть ("Макс Корж" vs "макс корж")
    artist_score = max(artist_score, _ratio(normalize(src_artist), normalize(" ".join(cand_artists))))

    total = 0.55 * title_score + 0.45 * artist_score

    # Штраф за несовпадающие пометки версий (remix / live / slowed ...)
    if _version_tags(src_title) != _version_tags(cand_title):
        total -= 0.15

    # Длительность: небольшой бонус/штраф, если известна у обоих
    if src_duration and cand_duration:
        diff = abs(src_duration - cand_duration)
        if diff <= 3:
            total += 0.05
        elif diff > 30:
            total -= 0.1

    return max(0.0, min(1.0, total))
