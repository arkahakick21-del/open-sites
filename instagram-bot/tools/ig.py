#!/usr/bin/env python3
"""
Instagram для ассистента: чтение статистики и подготовка публикации.

Публиковать напрямую эта утилита не умеет. Команда prepare-reel только создаёт
заявку, а публикует бот после того, как специалист нажмёт кнопку в Telegram.

Примеры:
  ig.py status                       подключён ли аккаунт, кто владелец
  ig.py posts --limit 12             последние публикации с лайками и комментариями
  ig.py insights <media_id>          статистика одной публикации
  ig.py get <path> -p key=value ...  любой GET-запрос к API (только чтение)
  ig.py prepare-reel out/reel.mp4 --caption "Текст подписи"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import instagram_api as ig  # noqa: E402

MAX_CAPTION = 2200
MAX_HASHTAGS = 30


def out(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_status(_a) -> None:
    if not ig.is_configured():
        out({"connected": False, "hint": "Instagram не подключён. Работать можно, но без статистики и публикации."})
        return
    out({"connected": True, "account": ig.me()})


def cmd_posts(a) -> None:
    out(ig.recent_media(a.limit))


def cmd_insights(a) -> None:
    out(ig.media_insights(a.media_id, a.metrics))


def cmd_get(a) -> None:
    params = {}
    for kv in a.param or []:
        k, _, v = kv.partition("=")
        params[k] = v
    out(ig.get(a.path, params))


def cmd_prepare(a) -> None:
    video = Path(a.video)
    if not video.is_file():
        sys.exit(f"Нет файла {video}")
    caption = a.caption
    if a.caption_file:
        caption = Path(a.caption_file).read_text(encoding="utf-8").strip()
    if len(caption) > MAX_CAPTION:
        sys.exit(f"Подпись длиннее {MAX_CAPTION} символов ({len(caption)}). Сократите.")
    if caption.count("#") > MAX_HASHTAGS:
        sys.exit(f"Больше {MAX_HASHTAGS} хештегов. Instagram такое не примет.")
    size_mb = video.stat().st_size / 1024 / 1024
    if size_mb > 300:
        sys.exit(f"Видео {size_mb:.0f} МБ, Instagram принимает до 300 МБ.")
    item = ig.create_pending(video, caption)
    out({"pending": item, "note": "Заявка создана. Бот покажет её специалисту с кнопкой «Опубликовать»."})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("posts")
    s.add_argument("--limit", type=int, default=12)
    s.set_defaults(fn=cmd_posts)
    s = sub.add_parser("insights")
    s.add_argument("media_id")
    s.add_argument("--metrics", default="reach,views,likes,comments,shares,saved,total_interactions")
    s.set_defaults(fn=cmd_insights)
    s = sub.add_parser("get")
    s.add_argument("path")
    s.add_argument("-p", "--param", action="append")
    s.set_defaults(fn=cmd_get)
    s = sub.add_parser("prepare-reel")
    s.add_argument("video")
    s.add_argument("--caption", default="")
    s.add_argument("--caption-file")
    s.set_defaults(fn=cmd_prepare)
    a = p.parse_args()
    try:
        a.fn(a)
    except ig.InstagramError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
