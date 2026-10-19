"""Консольная версия переноса.

Примеры:
    python cli.py                                 # всё спросит интерактивно
    python cli.py --playlist "Из ВК"              # лайки + плейлист «Из ВК»
    python cli.py --no-like --playlist "Из ВК"    # только плейлист
    python cli.py --from-file tracks.txt          # из текстового списка «Исполнитель - Название»
    python cli.py --vk-playlist                   # выбрать плейлист ВК вместо «Моей музыки»
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from vk2ym import vk as vkmod
from vk2ym.transfer import MatchCache, run_transfer, write_report
from vk2ym.yandex import YANDEX_TOKEN_URL, YandexMusic

DATA_DIR = Path(__file__).parent / "data"


def ask(prompt: str, secret: bool = False) -> str:
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def get_vk_tracks(args) -> list:
    if args.from_file:
        text = Path(args.from_file).read_text(encoding="utf-8")
        return vkmod.parse_text_list(text)

    token = args.vk_token or os.getenv("VK_TOKEN", "")
    client = args.vk_client or os.getenv("VK_CLIENT", "vk")
    if not token:
        print("Вход в ВК. Можно ввести логин/пароль или вставить готовый токен.")
        choice = ask("1 — логин и пароль, 2 — токен [1]: ") or "1"
        if choice == "2":
            print(f"Токен Kate Mobile можно получить здесь:\n  {vkmod.KATE_TOKEN_URL}\n"
                  "После входа скопируй адрес страницы целиком.")
            token = vkmod.clean_token(ask("Токен или ссылка: ", secret=True))
            client = "kate"
        else:
            token = vkmod.login(
                ask("Логин (телефон/почта): "), ask("Пароль: ", secret=True),
                on_2fa=lambda: ask("Код подтверждения: "),
                on_captcha=lambda url: ask(f"Капча {url}\nВведите текст с картинки: "),
            )
            client = "vk"
            print("Совет: сохрани токен в .env как VK_TOKEN, чтобы не входить каждый раз.")

    vk = vkmod.VkMusic(token, client=client)
    print(f"ВК: {vk.user_name()}")

    if args.vk_playlist:
        playlists = vk.get_playlists()
        if not playlists:
            sys.exit("В ВК нет плейлистов")
        for i, p in enumerate(playlists, 1):
            print(f"  {i}. {p['title']} ({p['count']})")
        p = playlists[int(ask("Номер плейлиста: ")) - 1]
        return vk.get_playlist_tracks(p["owner_id"], p["id"], p["access_key"])
    return vk.get_my_tracks()


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Перенос музыки из ВК в Яндекс Музыку")
    ap.add_argument("--vk-token")
    ap.add_argument("--vk-client", choices=["vk", "kate"], help="чей это токен (по умолчанию vk)")
    ap.add_argument("--ym-token")
    ap.add_argument("--from-file", help="txt со строками «Исполнитель - Название» вместо ВК")
    ap.add_argument("--vk-playlist", action="store_true", help="взять треки из плейлиста ВК")
    ap.add_argument("--playlist", help="название плейлиста в Яндексе, куда добавить треки")
    ap.add_argument("--no-like", action="store_true", help="не ставить лайки")
    ap.add_argument("--no-order", action="store_true",
                    help="не соблюдать порядок ВК в лайках (быстрее; уже лайкнутые треки не трогаются)")
    ap.add_argument("--min-score", type=float, default=0.62, help="порог совпадения 0..1 (по умолчанию 0.62)")
    ap.add_argument("--dry-run", action="store_true", help="только найти и сделать отчёт, ничего не добавлять")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    tracks = get_vk_tracks(args)
    if not tracks:
        sys.exit("Треков не найдено")
    print(f"Треков к переносу: {len(tracks)}")

    ym_token = args.ym_token or os.getenv("YM_TOKEN", "")
    if not ym_token:
        print(f"Токен Яндекс Музыки: открой ссылку, войди и скопируй адрес страницы:\n  {YANDEX_TOKEN_URL}")
        ym_token = ask("Токен или ссылка: ", secret=True)
    ym = YandexMusic(ym_token, min_score=args.min_score)
    print(f"Яндекс: {ym.user_name()}")

    like = not args.no_like and not args.dry_run
    playlist = None if args.dry_run else args.playlist
    if not like and not playlist and not args.dry_run:
        sys.exit("Нечего делать: выключены лайки и не задан --playlist")

    cache = MatchCache(DATA_DIR / "match_cache.json")

    def progress(stats, r):
        if r is not None:
            mark = {"found": "+", "not_found": "-", "error": "!"}[r.status]
            sys.stdout.write(f"\r[{stats.processed}/{stats.total}] найдено {stats.found}, "
                             f"нет {stats.not_found}  {mark} {str(r.source)[:50]:<50}")
            sys.stdout.flush()
        elif stats.stage == "writing":
            if stats.write_total:
                sys.stdout.write(f"\rЛайки по порядку: {stats.write_done}/{stats.write_total}   ")
                sys.stdout.flush()
            else:
                print("\nДобавляю в Яндекс Музыку…")

    try:
        stats = run_transfer(tracks, ym, like=like, playlist_title=playlist, keep_order=not args.no_order,
                             cache=cache, on_progress=progress)
    except KeyboardInterrupt:
        cache.save()
        sys.exit("\nПрервано. Прогресс поиска сохранён — при следующем запуске продолжит с кеша.")

    report = write_report(stats.results, DATA_DIR / "report.csv")
    print(f"\n\nГотово. Найдено {stats.found} из {stats.total}, не найдено {stats.not_found}, "
          f"ошибок {stats.errors}.")
    if like:
        print(f"Новых лайков: {stats.liked}")
    if playlist:
        print(f"Добавлено в плейлист «{playlist}»: {stats.playlisted}")
    print(f"Отчёт: {report}")


if __name__ == "__main__":
    main()
