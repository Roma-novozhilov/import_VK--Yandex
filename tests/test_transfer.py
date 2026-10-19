from vk2ym.models import MatchResult, SourceTrack
from vk2ym.transfer import MatchCache, run_transfer, write_report


class FakeYM:
    min_score = 0.62

    def __init__(self):
        self.searched, self.liked, self.playlist = [], [], []

    def find(self, src):
        self.searched.append(src.key)
        if "нет" in src.title:
            return MatchResult(source=src, status="not_found", score=0.5, ym_track_id="77", ym_album_id="9")
        return MatchResult(source=src, status="found", ym_track_id=str(len(self.searched)),
                           ym_album_id="9", ym_artist=src.artist, ym_title=src.title, score=0.97)

    def like(self, matches, **kw):
        self.liked += [m.ym_track_id for m in matches]
        return len(matches)

    def add_to_playlist(self, title, matches):
        self.playlist += [m.ym_track_id for m in matches]
        return len(matches)


def test_transfer_and_cache(tmp_path):
    tracks = [SourceTrack("A", "one"), SourceTrack("B", "нет такого"), SourceTrack("C", "three")]
    ym = FakeYM()
    cache = MatchCache(tmp_path / "c.json")
    stats = run_transfer(tracks, ym, like=True, playlist_title="Из ВК", cache=cache)
    assert (stats.found, stats.not_found, stats.liked, stats.playlisted) == (2, 1, 2, 2)
    assert stats.stage == "done"

    # Повторный запуск берёт результаты из кеша и не ищет заново
    ym2 = FakeYM()
    run_transfer(tracks, ym2, like=False, cache=MatchCache(tmp_path / "c.json"))
    assert ym2.searched == []

    # Снижение порога превращает сохранённого кандидата в «найден» без повторного поиска
    ym3 = FakeYM()
    ym3.min_score = 0.45
    st3 = run_transfer(tracks, ym3, like=True, cache=MatchCache(tmp_path / "c.json"))
    assert ym3.searched == [] and st3.found == 3

    report = write_report(stats.results, tmp_path / "r.csv")
    text = report.read_text(encoding="utf-8-sig")
    assert "не найден" in text and "music.yandex.ru/album/9/track/" in text


def test_like_order(monkeypatch):
    """Лайки ставятся с конца списка ВК; уже лайкнутые снимаются и ставятся заново на своё место."""
    from types import SimpleNamespace
    from vk2ym import yandex

    calls = []
    client = SimpleNamespace(
        users_likes_tracks=lambda: SimpleNamespace(tracks=[SimpleNamespace(id="2:9")]),
        users_likes_tracks_add=lambda tid: calls.append(("add", tid.split(":")[0])),
        users_likes_tracks_remove=lambda tid: calls.append(("remove", tid)),
    )
    monkeypatch.setattr(yandex, "ORDER_GAP", 0)
    ym = object.__new__(yandex.YandexMusic)
    ym.client, ym.delay = client, 0
    ms = [MatchResult(source=SourceTrack("a", str(i)), status="found", ym_track_id=str(i), ym_album_id="9")
          for i in (1, 2, 3)]

    assert ym.like(ms, keep_order=True) == 2
    assert calls == [("add", "3"), ("remove", "2"), ("add", "2"), ("add", "1")]

    calls.clear()
    assert ym.like(ms, keep_order=False) == 2
    assert calls == [("add", "3"), ("add", "1")]
