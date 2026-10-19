from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SourceTrack:
    """Трек из ВК (или из текстового списка)."""

    artist: str
    title: str
    duration: int = 0  # секунды, 0 = неизвестно

    @property
    def key(self) -> str:
        return f"{self.artist.strip().lower()} — {self.title.strip().lower()}"

    def __str__(self) -> str:
        return f"{self.artist} — {self.title}"


@dataclass
class MatchResult:
    source: SourceTrack
    status: str  # found | not_found | error
    ym_track_id: Optional[str] = None
    ym_album_id: Optional[str] = None
    ym_artist: str = ""
    ym_title: str = ""
    score: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = asdict(self.source)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MatchResult":
        d = dict(d)
        d["source"] = SourceTrack(**d["source"])
        return cls(**d)


@dataclass
class TransferStats:
    total: int = 0
    processed: int = 0
    found: int = 0
    not_found: int = 0
    errors: int = 0
    liked: int = 0
    playlisted: int = 0
    stage: str = "search"  # search | writing | done | stopped
    results: list = field(default_factory=list)
