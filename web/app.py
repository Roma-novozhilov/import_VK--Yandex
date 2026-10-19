"""Веб-интерфейс переноса. Запуск: python web/app.py → http://127.0.0.1:5000

Рассчитан на локальное использование одним человеком: токены хранятся только в памяти процесса.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vk2ym import vk as vkmod  # noqa: E402
from vk2ym.transfer import MatchCache, run_transfer, write_report  # noqa: E402
from vk2ym.yandex import YANDEX_TOKEN_URL, YandexMusic  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
log = logging.getLogger("web")

app = Flask(__name__)


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.vk: vkmod.VkMusic | None = None
        self.vk_name = ""
        self.ym: YandexMusic | None = None
        self.ym_name = ""
        # вход по логину
        self.login_state = "idle"  # idle | pending | need_2fa | need_captcha | ok | error
        self.login_error = ""
        self.captcha_url = ""
        self.answers: queue.Queue[str] = queue.Queue()
        # перенос
        self.job: threading.Thread | None = None
        self.stop = threading.Event()
        self.stats = None
        self.results: list = []
        self.job_error = ""
        self.loading_tracks = False


S = State()


def err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


@app.get("/")
def index():
    return render_template("index.html", kate_url=vkmod.KATE_TOKEN_URL, ym_url=YANDEX_TOKEN_URL)


@app.get("/vk_export.js")
def vk_export_script():
    return send_file(Path(__file__).resolve().parent.parent / "tools" / "vk_export.js",
                     mimetype="text/javascript; charset=utf-8")


# ---------------- ВК ----------------

def _vk_connected(vk: vkmod.VkMusic):
    name = vk.user_name()
    with S.lock:
        S.vk, S.vk_name = vk, name
    return name


@app.post("/api/vk/token")
def vk_token():
    data = request.get_json(force=True)
    try:
        vk = vkmod.VkMusic(data.get("token", ""), client=data.get("client", "kate"))
        name = _vk_connected(vk)
    except Exception as e:  # noqa: BLE001
        return err(f"ВК не принял токен: {e}")
    return jsonify({"ok": True, "name": name})


@app.post("/api/vk/login")
def vk_login():
    data = request.get_json(force=True)
    login, password = data.get("login", "").strip(), data.get("password", "")
    if not login or not password:
        return err("Введи логин и пароль")
    S.answers = queue.Queue()
    S.login_state, S.login_error, S.captcha_url = "pending", "", ""

    def wait_answer(state: str, captcha_url: str = "") -> str:
        S.captcha_url = captcha_url
        S.login_state = state
        value = S.answers.get(timeout=300)
        S.login_state = "pending"
        return value

    def worker():
        try:
            token = vkmod.login(login, password,
                                on_2fa=lambda: wait_answer("need_2fa"),
                                on_captcha=lambda url: wait_answer("need_captcha", url))
            _vk_connected(vkmod.VkMusic(token, client="vk"))
            S.login_state = "ok"
        except queue.Empty:
            S.login_state, S.login_error = "error", "Время ожидания кода истекло"
        except Exception as e:  # noqa: BLE001
            S.login_state, S.login_error = "error", str(e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/vk/login/status")
def vk_login_status():
    return jsonify({"state": S.login_state, "error": S.login_error,
                    "captcha_url": S.captcha_url, "name": S.vk_name})


@app.post("/api/vk/login/answer")
def vk_login_answer():
    S.answers.put(request.get_json(force=True).get("value", "").strip())
    return jsonify({"ok": True})


@app.get("/api/vk/playlists")
def vk_playlists():
    if not S.vk:
        return err("Сначала подключи ВК")
    try:
        return jsonify({"ok": True, "playlists": S.vk.get_playlists()})
    except Exception as e:  # noqa: BLE001
        return err(str(e))


# ---------------- Яндекс ----------------

@app.post("/api/ym/token")
def ym_token():
    try:
        ym = YandexMusic(request.get_json(force=True).get("token", ""))
    except Exception as e:  # noqa: BLE001
        return err(f"Яндекс не принял токен: {e}")
    with S.lock:
        S.ym, S.ym_name = ym, ym.user_name()
    return jsonify({"ok": True, "name": S.ym_name})


@app.get("/api/session")
def session():
    return jsonify({"vk": S.vk_name if S.vk else "", "ym": S.ym_name if S.ym else "",
                    "running": bool(S.job and S.job.is_alive())})


# ---------------- Перенос ----------------

@app.post("/api/start")
def start():
    if S.job and S.job.is_alive():
        return err("Перенос уже идёт")
    if not S.ym:
        return err("Подключи Яндекс Музыку")
    data = request.get_json(force=True)
    source = data.get("source", "my")
    if source != "text" and not S.vk:
        return err("Подключи ВК или выбери вставку списком")
    like = bool(data.get("like", True))
    keep_order = bool(data.get("keep_order", True))
    playlist_title = (data.get("playlist_title") or "").strip() or None
    if not like and not playlist_title:
        return err("Включи лайки или укажи название плейлиста")
    S.ym.min_score = float(data.get("min_score", 0.62))

    S.stop.clear()
    S.stats, S.results, S.job_error = None, [], ""

    def progress(stats, r):
        S.stats = stats
        if r is not None:
            S.results.append(r)

    def worker():
        try:
            S.loading_tracks = True
            if source == "text":
                tracks = vkmod.parse_text_list(data.get("text", ""))
            elif source == "playlist":
                tracks = S.vk.get_playlist_tracks(int(data["owner_id"]), int(data["playlist_id"]),
                                                  data.get("access_key", ""))
            else:
                tracks = S.vk.get_my_tracks()
            S.loading_tracks = False
            if not tracks:
                S.job_error = "Список треков пуст"
                return
            cache = MatchCache(DATA_DIR / "match_cache.json")
            S.stats = run_transfer(tracks, S.ym, like=like, playlist_title=playlist_title, keep_order=keep_order,
                                   cache=cache, on_progress=progress, stop_event=S.stop)
            write_report(S.results, DATA_DIR / "report.csv")
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка переноса")
            S.job_error = str(e)
            if S.results:
                write_report(S.results, DATA_DIR / "report.csv")
        finally:
            S.loading_tracks = False

    S.job = threading.Thread(target=worker, daemon=True)
    S.job.start()
    return jsonify({"ok": True})


@app.post("/api/stop")
def stop():
    S.stop.set()
    return jsonify({"ok": True})


@app.get("/api/status")
def status():
    since = int(request.args.get("since", 0))
    st = S.stats
    stats = ({k: getattr(st, k) for k in ("total", "processed", "found", "not_found", "errors",
                                          "liked", "playlisted", "stage", "write_done", "write_total")} if st else None)
    new = [r.to_dict() for r in S.results[since:since + 500]]
    return jsonify({
        "running": bool(S.job and S.job.is_alive()),
        "loading_tracks": S.loading_tracks,
        "stats": stats,
        "results": new,
        "next": since + len(new),
        "error": S.job_error,
        "has_report": (DATA_DIR / "report.csv").exists(),
    })


@app.get("/api/report")
def report():
    path = DATA_DIR / "report.csv"
    if not path.exists():
        return err("Отчёта пока нет", 404)
    return send_file(path, as_attachment=True, download_name="vk2yandex_report.csv")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"Открой http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
