"""
Работа с официальным Instagram API (вариант «Instagram API with Instagram Login»).

Этот вариант не требует страницы Facebook: нужен профессиональный аккаунт
Instagram (Business или Creator) и приложение в Meta for Developers.

Токен берётся из data/ig_token.json (его обновляет бот) или из переменной
IG_ACCESS_TOKEN в .env. Модуль используют и бот (публикация, обновление токена),
и консольная утилита tools/ig.py (чтение статистики для ассистента).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
TOKEN_PATH = DATA_DIR / "ig_token.json"
PENDING_DIR = DATA_DIR / "workspace" / "pending"

GRAPH_HOST = os.environ.get("IG_GRAPH_HOST", "graph.instagram.com")
GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v25.0")
UPLOAD_HOST = "rupload.facebook.com"
TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class InstagramError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Токен
# ---------------------------------------------------------------------------

def load_token() -> str | None:
    if TOKEN_PATH.exists():
        try:
            token = json.loads(TOKEN_PATH.read_text(encoding="utf-8")).get("access_token")
            if token:
                return token
        except (OSError, json.JSONDecodeError):
            pass
    return os.environ.get("IG_ACCESS_TOKEN") or None


def save_token(token: str, expires_in: int | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {"access_token": token, "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
    if expires_in:
        data["expires_at"] = (dt.datetime.now() + dt.timedelta(seconds=expires_in)).isoformat(timespec="seconds")
    tmp = TOKEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_PATH)


def is_configured() -> bool:
    return bool(load_token())


def refresh_token() -> dict:
    """Продлевает долгоживущий токен ещё на 60 дней. Токен должен быть старше суток."""
    token = _require_token()
    r = httpx.get(
        f"https://{GRAPH_HOST}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=TIMEOUT,
    )
    data = _parse(r)
    save_token(data["access_token"], data.get("expires_in"))
    return data


# ---------------------------------------------------------------------------
# Запросы
# ---------------------------------------------------------------------------

def _require_token() -> str:
    token = load_token()
    if not token:
        raise InstagramError("Instagram не подключён: нет токена. Инструкция в README, раздел «Подключение Instagram».")
    return token


def _parse(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise InstagramError(f"Instagram ответил не JSON (HTTP {r.status_code}): {r.text[:300]}")
    if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        err = data.get("error", {}) if isinstance(data, dict) else {}
        msg = err.get("message") or r.text[:300]
        raise InstagramError(f"Ошибка Instagram API (HTTP {r.status_code}, код {err.get('code')}): {msg}")
    return data


def _url(path: str) -> str:
    return f"https://{GRAPH_HOST}/{GRAPH_VERSION}/{path.lstrip('/')}"


def get(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["access_token"] = _require_token()
    return _parse(httpx.get(_url(path), params=params, timeout=TIMEOUT))


def post(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["access_token"] = _require_token()
    return _parse(httpx.post(_url(path), data=params, timeout=TIMEOUT))


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------

def me() -> dict:
    return get("me", {"fields": "user_id,username,name,account_type,followers_count,follows_count,media_count"})


def user_id() -> str:
    uid = os.environ.get("IG_USER_ID")
    if uid:
        return uid
    data = me()
    return str(data.get("user_id") or data["id"])


def recent_media(limit: int = 12) -> list[dict]:
    fields = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
    return get(f"{user_id()}/media", {"fields": fields, "limit": limit}).get("data", [])


def media_insights(media_id: str, metrics: str = "reach,views,likes,comments,shares,saved,total_interactions") -> dict:
    return get(f"{media_id}/insights", {"metric": metrics})


# ---------------------------------------------------------------------------
# Публикация Reels
# ---------------------------------------------------------------------------

def publish_reel(
    video_path: str | Path,
    caption: str = "",
    share_to_feed: bool = True,
    thumb_offset_ms: int | None = None,
    on_status=None,
    max_wait_s: int = 600,
) -> dict:
    """Загружает видео напрямую с сервера (resumable upload) и публикует как Reels."""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise InstagramError(f"Файл не найден: {video_path}")
    token = _require_token()
    uid = user_id()
    say = on_status or (lambda _msg: None)

    params = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption,
        "share_to_feed": "true" if share_to_feed else "false",
    }
    if thumb_offset_ms is not None:
        params["thumb_offset"] = str(thumb_offset_ms)
    say("Создаю контейнер в Instagram")
    container = post(f"{uid}/media", params)
    cid = container["id"]

    say("Загружаю видео")
    size = video_path.stat().st_size
    with video_path.open("rb") as f:
        # Instagram сам возвращает адрес загрузки в поле uri; если нет, собираем по документации
        upload_url = container.get("uri") or f"https://{UPLOAD_HOST}/ig-api-upload/{GRAPH_VERSION}/{cid}"
        r = httpx.post(
            upload_url,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            content=f.read(),
            timeout=httpx.Timeout(600.0, connect=15.0),
        )
    up = _parse(r)
    if up.get("success") is False:
        raise InstagramError(f"Загрузка не удалась: {up}")

    say("Instagram обрабатывает видео")
    deadline = time.monotonic() + max_wait_s
    while True:
        st = get(cid, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram не принял видео: {st.get('status') or code}")
        if time.monotonic() > deadline:
            raise InstagramError("Instagram слишком долго обрабатывает видео. Попробуйте позже.")
        time.sleep(5)

    say("Публикую")
    pub = post(f"{uid}/media_publish", {"creation_id": cid})
    media_id = pub["id"]
    try:
        info = get(media_id, {"fields": "permalink"})
    except InstagramError:
        info = {}
    return {"id": media_id, "permalink": info.get("permalink")}


# ---------------------------------------------------------------------------
# Заявки на публикацию: ассистент готовит, человек подтверждает кнопкой
# ---------------------------------------------------------------------------

def create_pending(video: Path, caption: str) -> dict:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pid = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    item = {
        "id": pid,
        "file": str(video.resolve()),
        "caption": caption,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (PENDING_DIR / f"{pid}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


def list_pending() -> list[dict]:
    if not PENDING_DIR.exists():
        return []
    items = []
    for p in sorted(PENDING_DIR.glob("*.json")):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return items


def pop_pending(pid: str) -> dict | None:
    p = PENDING_DIR / f"{Path(pid).name}.json"
    if not p.exists():
        return None
    item = json.loads(p.read_text(encoding="utf-8"))
    p.unlink(missing_ok=True)
    return item
