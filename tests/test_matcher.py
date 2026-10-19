from vk2ym import matcher
from vk2ym.vk import clean_token, parse_text_list


def test_normalize():
    assert matcher.normalize("Ёлка (feat. Кто-то) [Official Video]") == "елка"
    assert matcher.normalize("Numb (Remastered 2020)") == "numb"


def test_exact_match_high():
    assert matcher.score("Linkin Park", "Numb", 185, ["Linkin Park"], "Numb", 186) > 0.95


def test_feat_and_case():
    s = matcher.score("Скриптонит feat. 104", "Притон", 0, ["Скриптонит", "104"], "Притон", 0)
    assert s > 0.9


def test_remix_penalty():
    orig = matcher.score("Artist", "Song", 0, ["Artist"], "Song", 0)
    remix = matcher.score("Artist", "Song", 0, ["Artist"], "Song (Remix)", 0)
    assert remix < orig


def test_wrong_artist_low():
    assert matcher.score("Linkin Park", "Numb", 0, ["Some Cover Band"], "Numb", 0) < 0.75


def test_loose():
    assert matcher.loose_score("linkin park numb", ["Linkin Park"], "Numb") > 0.9


def test_parse_text_list():
    tracks = parse_text_list("1. Linkin Park - Numb\nМакс Корж — Мотылёк\n\n# комментарий\nпросто строка")
    assert [(t.artist, t.title) for t in tracks] == [
        ("Linkin Park", "Numb"), ("Макс Корж", "Мотылёк"), ("", "просто строка")]


def test_clean_token():
    url = "https://oauth.vk.com/blank.html#access_token=abc123&expires_in=0&user_id=1"
    assert clean_token(url) == "abc123"
    assert clean_token("  xyz ") == "xyz"
