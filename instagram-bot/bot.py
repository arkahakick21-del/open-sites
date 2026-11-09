#!/usr/bin/env python3
"""
Telegram-бот «Ассистент по Instagram».

Специалист пишет задачу текстом или голосом, присылает видео и фото.
Бот передаёт задачу Claude Code (agent.py), показывает ход работы,
а потом присылает ответ и готовые файлы. Публикация в Instagram только
по кнопке «Опубликовать».

Настройки в файле .env рядом со скриптом, список в .env.example.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
load_dotenv(ENV_PATH)

import agent  # noqa: E402
import instagram_api as ig  # noqa: E402
from tools import transcribe  # noqa: E402

DATA_DIR = HERE / "data"
STATE_PATH = DATA_DIR / "state.json"

TG_TEXT_LIMIT = 4000
TG_CAPTION_LIMIT = 1000
CLOUD_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # облачный Bot API не отдаёт ботам файлы больше 20 МБ
UPLOAD_LIMIT = 50 * 1024 * 1024           # и не принимает от ботов больше 50 МБ
BATCH_DELAY = 2.5                         # секунд ждём, пока придут все файлы альбома
STATUS_EVERY = 3.0                        # не чаще, чем раз в столько секунд, обновляем статус

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}

log = logging.getLogger("insta-bot")


# ---------------------------------------------------------------------------
# Настройки и состояние
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        print(f"В файле {ENV_PATH} не хватает: {', '.join(missing)}", file=sys.stderr)
        print("Скопируйте .env.example в .env и заполните значения.", file=sys.stderr)
        sys.exit(1)
    allowed = {int(x) for x in re.split(r"[,;\s]+", os.environ.get("ALLOWED_USERS", "")) if x.strip().isdigit()}
    budget = os.environ.get("MAX_TASK_USD", "5").strip()
    return {
        "token": os.environ["TELEGRAM_BOT_TOKEN"],
        "allowed": allowed,
        "local_api": os.environ.get("TELEGRAM_API_URL", "").rstrip("/"),
        "max_task_usd": float(budget) if budget else None,
        "show_cost": os.environ.get("SHOW_COST", "0") == "1",
    }


@dataclass
class State:
    session_id: str | None = None
    fresh_files: list[str] = field(default_factory=list)
    announced: list[str] = field(default_factory=list)
    total_cost: float = 0.0
    tasks_done: int = 0

    @classmethod
    def load(cls) -> "State":
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)


@dataclass
class Batch:
    texts: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    timer: asyncio.Task | None = None


@dataclass
class Runtime:
    settings: dict
    state: State
    batches: dict[int, Batch] = field(default_factory=dict)
    running: asyncio.Task | None = None


def rt(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    return context.application.bot_data["rt"]


def allowed(context: ContextTypes.DEFAULT_TYPE, update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in rt(context).settings["allowed"])


async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else "?"
    if not rt(context).settings["allowed"]:
        text = (f"Бот ещё не настроен. Ваш Telegram ID: {uid}\n"
                "Впишите его в ALLOWED_USERS в файле .env на сервере и перезапустите бота.")
    else:
        text = f"Этот бот личный. Ваш Telegram ID: {uid}"
    if update.effective_message:
        await update.effective_message.reply_text(text)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def split_text(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text.strip():
        parts.append(text)
    return parts


def human_size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} МБ"


def human_time(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60} мин {sec % 60} с" if sec >= 60 else f"{sec} с"


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return name[:80] or "file"


def next_inbox_path(original: str) -> Path:
    agent.ensure_workspace()
    n = len(list(agent.INBOX.iterdir())) + 1
    path = agent.INBOX / f"{n:03d}_{safe_name(original)}"
    while path.exists():
        n += 1
        path = agent.INBOX / f"{n:03d}_{safe_name(original)}"
    return path


def snapshot_out() -> dict[str, tuple[float, int]]:
    agent.ensure_workspace()
    return {str(p): (p.stat().st_mtime, p.stat().st_size) for p in agent.OUT.rglob("*") if p.is_file()}


async def safe_edit(msg: Message, text: str) -> None:
    try:
        await msg.edit_text(text)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.debug("edit failed: %s", e)
    except TelegramError as e:
        log.debug("edit failed: %s", e)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

HELP = """Привет! Я ваш ассистент по Instagram.

Пишите задачу текстом или голосом, присылайте видео и фото. Например:
1. Пришлите видео и напишите: «сделай рилс с субтитрами, вырежи паузы»
2. «Напиши 5 вариантов подписи к этому ролику и хештеги»
3. «Составь контент-план на неделю для кофейни»
4. «Какие ролики за месяц зашли лучше всего и почему» (если Instagram подключён)
5. «Опубликуй последний ролик» — пришлю превью и кнопку, публикую только по нажатию

Я помню наш разговор, поэтому можно уточнять: «сделай субтитры крупнее», «обрежь первые 2 секунды».

Команды:
/new — начать новую тему (долгие заметки об аккаунте сохраняются)
/cancel — остановить текущую задачу
/files — что лежит в папках
/clean — удалить старые файлы
/status — состояние бота и Instagram"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    await update.effective_message.reply_text(HELP)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    r = rt(context)
    r.state.session_id = None
    r.state.fresh_files = []
    r.state.save()
    await update.effective_message.reply_text("Начинаем с чистого листа. Заметки об аккаунте я помню.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    r = rt(context)
    if r.running and not r.running.done():
        r.running.cancel()
        await update.effective_message.reply_text("Останавливаю задачу.")
    else:
        await update.effective_message.reply_text("Сейчас ничего не выполняется.")


async def cmd_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    agent.ensure_workspace()
    lines = []
    for title, folder in (("Присланные", agent.INBOX), ("Готовые", agent.OUT)):
        files = sorted((p for p in folder.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        lines.append(f"{title}: {len(files)}")
        for p in files[-10:]:
            lines.append(f"  {p.name} ({human_size(p.stat().st_size)})")
    total, used, free = shutil.disk_usage(agent.WORKSPACE)
    lines.append(f"\nСвободно на диске: {free / 1024**3:.1f} ГБ")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Старше 7 дней", callback_data="clean:7"),
        InlineKeyboardButton("Всё", callback_data="clean:0"),
        InlineKeyboardButton("Отмена", callback_data="clean:no"),
    ]])
    await update.effective_message.reply_text(
        "Удалить присланные, черновые и готовые файлы? Заметки об аккаунте останутся.", reply_markup=kb)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    r = rt(context)
    lines = []
    busy = r.running and not r.running.done()
    lines.append("Сейчас выполняю задачу." if busy else "Свободна, жду задач.")
    lines.append(f"Задач выполнено: {r.state.tasks_done}")
    lines.append(f"Расход на Claude: ${r.state.total_cost:.2f}")
    if ig.is_configured():
        try:
            info = await asyncio.to_thread(ig.me)
            lines.append(f"Instagram: подключён @{info.get('username')}, подписчиков {info.get('followers_count')}")
        except ig.InstagramError as e:
            lines.append(f"Instagram: ошибка подключения. {e}")
    else:
        lines.append("Instagram: не подключён. Монтаж и тексты работают и без него.")
    await update.effective_message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Входящие сообщения
# ---------------------------------------------------------------------------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(context, update):
        return await deny(update, context)
    msg = update.effective_message
    r = rt(context)
    chat_id = msg.chat_id

    text = (msg.text or msg.caption or "").strip()
    saved: Path | None = None

    if msg.voice:
        text = await transcribe_voice(msg, context)
        if not text:
            return
    else:
        saved = await save_attachment(msg, context, r)
        if saved is None and not text:
            return

    batch = r.batches.setdefault(chat_id, Batch())
    if text:
        batch.texts.append(text)
    if saved:
        batch.files.append(str(saved.relative_to(agent.WORKSPACE)))
    if batch.timer and not batch.timer.done():
        batch.timer.cancel()
    batch.timer = asyncio.create_task(flush_batch_later(chat_id, context))


async def save_attachment(msg: Message, context: ContextTypes.DEFAULT_TYPE, r: Runtime) -> Path | None:
    att, name = None, None
    if msg.video:
        att, name = msg.video, msg.video.file_name or "video.mp4"
    elif msg.video_note:
        att, name = msg.video_note, "circle.mp4"
    elif msg.animation:
        att, name = msg.animation, msg.animation.file_name or "animation.mp4"
    elif msg.document:
        att, name = msg.document, msg.document.file_name or "file"
    elif msg.photo:
        att, name = msg.photo[-1], "photo.jpg"
    elif msg.audio:
        att, name = msg.audio, msg.audio.file_name or "audio.mp3"
    if att is None:
        return None

    size = att.file_size or 0
    if not r.settings["local_api"] and size > CLOUD_DOWNLOAD_LIMIT:
        await msg.reply_text(
            f"Файл {human_size(size)}, а Telegram отдаёт ботам файлы только до 20 МБ.\n"
            "Что можно сделать:\n"
            "1. Пришлите ссылку на файл в Google Drive или Яндекс Диске с открытым доступом, я скачаю сам.\n"
            "2. Пришлите видео сжатым (обычной отправкой, не файлом).\n"
            "3. Попросите Аркадия включить локальный сервер Telegram, тогда лимит станет 2 ГБ.")
        return None

    path = next_inbox_path(name)
    try:
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        tg_file = await context.bot.get_file(att.file_id, read_timeout=120)
        await tg_file.download_to_drive(path, read_timeout=600, write_timeout=600)
    except TelegramError as e:
        log.warning("download failed: %s", e)
        await msg.reply_text(f"Не получилось скачать файл: {e}")
        return None
    r.state.fresh_files.append(str(path.relative_to(agent.WORKSPACE)))
    r.state.save()
    return path


async def transcribe_voice(msg: Message, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        await msg.reply_text("Голосовые пока не распознаю: на сервере не задан ключ OpenAI. Напишите текстом.")
        return ""
    agent.ensure_workspace()
    path = agent.WORK / f"voice_{msg.message_id}.ogg"
    try:
        tg_file = await context.bot.get_file(msg.voice.file_id)
        await tg_file.download_to_drive(path)
        text = await asyncio.to_thread(transcribe.transcribe_text, path)
    except Exception as e:  # noqa: BLE001
        log.exception("voice failed")
        await msg.reply_text(f"Не получилось распознать голосовое: {e}")
        return ""
    finally:
        path.unlink(missing_ok=True)
    if not text:
        await msg.reply_text("В голосовом не нашлось речи.")
        return ""
    await msg.reply_text(f"🎙 {text}")
    return text


async def flush_batch_later(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.sleep(BATCH_DELAY)
    except asyncio.CancelledError:
        return
    r = rt(context)
    batch = r.batches.pop(chat_id, None)
    if not batch:
        return
    if not batch.texts:
        names = ", ".join(Path(f).name for f in batch.files)
        await context.bot.send_message(chat_id, f"Получила: {names}\nНапишите, что с этим сделать.")
        return
    if r.running and not r.running.done():
        await context.bot.send_message(
            chat_id, "Я ещё работаю над прошлой задачей. Дождитесь ответа или остановите её командой /cancel, "
                     "потом повторите сообщение. Файлы я сохранила.")
        return
    task_text = "\n\n".join(batch.texts)
    r.running = asyncio.create_task(run_agent(chat_id, task_text, context))


# ---------------------------------------------------------------------------
# Выполнение задачи
# ---------------------------------------------------------------------------

def build_prompt(task_text: str, files: list[str]) -> str:
    parts = [f"Задача от специалиста:\n{task_text}"]
    if files:
        lines = []
        for f in files:
            p = agent.WORKSPACE / f
            size = human_size(p.stat().st_size) if p.exists() else "файл не найден"
            lines.append(f"- {f} ({size})")
        parts.append("Новые файлы, которые она прислала к этой задаче (в порядке получения):\n" + "\n".join(lines))
    parts.append(f"Сегодня {dt.date.today().strftime('%d.%m.%Y')}.")
    return "\n\n".join(parts)


async def run_agent(chat_id: int, task_text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    bot = context.bot
    files = list(r.state.fresh_files)
    before = snapshot_out()
    started = time.monotonic()
    status = await bot.send_message(chat_id, "⏳ Взяла в работу")
    steps: list[str] = []
    last_edit = 0.0

    async def on_step(step: str) -> None:
        nonlocal last_edit
        steps.append(step)
        now = time.monotonic()
        if now - last_edit < STATUS_EVERY:
            return
        last_edit = now
        body = "\n".join(steps[-5:])
        await safe_edit(status, f"⏳ Работаю {human_time(now - started)}\n\n{body}")

    async def keep_typing() -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramError:
                pass
            await asyncio.sleep(5)

    typing = asyncio.create_task(keep_typing())
    try:
        result = await agent.run_task(
            build_prompt(task_text, files),
            session_id=r.state.session_id,
            max_budget_usd=r.settings["max_task_usd"],
            on_step=on_step,
        )
    except asyncio.CancelledError:
        typing.cancel()
        await safe_edit(status, f"⏹ Остановлено через {human_time(time.monotonic() - started)}")
        await send_new_files(chat_id, before, context)
        return
    except Exception as e:  # noqa: BLE001
        typing.cancel()
        log.exception("agent failed")
        await safe_edit(status, "❌ Ошибка")
        await bot.send_message(chat_id, f"Что-то пошло не так: {e}\nПопробуйте ещё раз или начните заново командой /new.")
        return
    typing.cancel()

    if result.session_id:
        r.state.session_id = result.session_id
    r.state.fresh_files = [f for f in r.state.fresh_files if f not in files]
    r.state.total_cost += result.cost_usd or 0
    r.state.tasks_done += 1
    r.state.save()

    icon = "⚠️" if result.is_error else "✅"
    await safe_edit(status, f"{icon} Готово за {human_time(time.monotonic() - started)}, шагов: {len(steps)}")

    text = result.text or "Готово."
    if r.settings["show_cost"] and result.cost_usd is not None:
        text += f"\n\n💰 ${result.cost_usd:.2f}"
    for part in split_text(text):
        await bot.send_message(chat_id, part)

    await send_new_files(chat_id, before, context)
    await announce_pending(chat_id, context)


async def send_new_files(chat_id: int, before: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot
    after = snapshot_out()
    new = [Path(p) for p, meta in sorted(after.items(), key=lambda kv: kv[1][0]) if before.get(p) != meta]
    for p in new:
        size = p.stat().st_size
        if size > UPLOAD_LIMIT:
            await bot.send_message(
                chat_id, f"Файл {p.name} получился {human_size(size)}, Telegram не даёт ботам отправлять больше 50 МБ. "
                         "Попросите меня пересжать его.")
            continue
        ext = p.suffix.lower()
        try:
            await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
            with p.open("rb") as f:
                if ext in VIDEO_EXT:
                    await bot.send_video(chat_id, f, caption=p.name, supports_streaming=True,
                                         read_timeout=600, write_timeout=600)
                elif ext in PHOTO_EXT and size < 10 * 1024 * 1024:
                    await bot.send_photo(chat_id, f, caption=p.name, read_timeout=300, write_timeout=300)
                else:
                    await bot.send_document(chat_id, f, filename=p.name, read_timeout=600, write_timeout=600)
        except TelegramError as e:
            log.warning("send failed %s: %s", p, e)
            await bot.send_message(chat_id, f"Не получилось отправить {p.name}: {e}")


async def announce_pending(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    for item in ig.list_pending():
        if item["id"] in r.state.announced:
            continue
        r.state.announced.append(item["id"])
        r.state.announced = r.state.announced[-50:]
        r.state.save()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"pub:{item['id']}"),
            InlineKeyboardButton("✖️ Отменить", callback_data=f"unpub:{item['id']}"),
        ]])
        caption = item.get("caption") or "(без подписи)"
        head = "Готово к публикации в Instagram как Reels.\n\nПодпись:\n"
        video = Path(item["file"])
        if not ig.is_configured():
            await context.bot.send_message(
                chat_id, "Ролик готов к публикации, но Instagram ещё не подключён. Подключение описано в README.")
            ig.pop_pending(item["id"])
            continue
        await context.bot.send_message(chat_id, head + caption[:TG_TEXT_LIMIT - len(head)], reply_markup=kb)
        if not video.exists():
            await context.bot.send_message(chat_id, f"Внимание: файл {video.name} не найден.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not allowed(context, update):
        await q.answer("Нет доступа")
        return
    await q.answer()
    action, _, arg = (q.data or "").partition(":")

    if action == "clean":
        if arg == "no":
            await q.edit_message_text("Ничего не удаляла.")
            return
        days = int(arg)
        cutoff = time.time() - days * 86400
        removed = 0
        for folder in (agent.INBOX, agent.WORK, agent.OUT):
            for p in folder.rglob("*"):
                if p.is_file() and (days == 0 or p.stat().st_mtime < cutoff):
                    p.unlink(missing_ok=True)
                    removed += 1
        r = rt(context)
        r.state.fresh_files = [f for f in r.state.fresh_files if (agent.WORKSPACE / f).exists()]
        r.state.save()
        await q.edit_message_text(f"Удалено файлов: {removed}.")
        return

    if action == "unpub":
        ig.pop_pending(arg)
        await q.edit_message_text((q.message.text or "") + "\n\n✖️ Публикация отменена.")
        return

    if action == "pub":
        item = ig.pop_pending(arg)
        if not item:
            await q.edit_message_text((q.message.text or "") + "\n\nЭта заявка уже обработана.")
            return
        base = q.message.text or ""
        await q.edit_message_text(base + "\n\n⏳ Публикую, это займёт до нескольких минут…")
        try:
            res = await asyncio.to_thread(ig.publish_reel, item["file"], item.get("caption", ""))
        except Exception as e:  # noqa: BLE001
            log.exception("publish failed")
            await q.edit_message_text(base + f"\n\n❌ Не опубликовано: {e}")
            return
        link = res.get("permalink") or "ссылка появится в профиле"
        await q.edit_message_text(base + f"\n\n✅ Опубликовано: {link}")


# ---------------------------------------------------------------------------
# Фоновые задачи и запуск
# ---------------------------------------------------------------------------

async def refresh_ig_token_forever() -> None:
    """Долгоживущий токен Instagram живёт 60 дней. Продлеваем раз в сутки."""
    while True:
        await asyncio.sleep(24 * 3600)
        if not ig.is_configured():
            continue
        try:
            await asyncio.to_thread(ig.refresh_token)
            log.info("Instagram token refreshed")
        except Exception as e:  # noqa: BLE001
            log.warning("Instagram token refresh failed: %s", e)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("new", "Начать новую тему"),
        BotCommand("cancel", "Остановить задачу"),
        BotCommand("files", "Файлы в папках"),
        BotCommand("clean", "Удалить старые файлы"),
        BotCommand("status", "Состояние бота и Instagram"),
        BotCommand("id", "Мой Telegram ID"),
    ])
    app.bot_data["refresh_task"] = asyncio.create_task(refresh_ig_token_forever())


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    if not settings["allowed"]:
        log.warning("ALLOWED_USERS пуст: бот никого не пустит, только покажет ID. Впишите ID в .env.")
    agent.ensure_workspace()

    builder = ApplicationBuilder().token(settings["token"]).post_init(post_init)
    if settings["local_api"]:
        builder = (builder.base_url(f"{settings['local_api']}/bot")
                   .base_file_url(f"{settings['local_api']}/file/bot")
                   .local_mode(True))
    app = builder.build()
    app.bot_data["rt"] = Runtime(settings=settings, state=State.load())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("files", cmd_files))
    app.add_handler(CommandHandler("clean", cmd_clean))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(~filters.COMMAND, on_message))

    log.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
