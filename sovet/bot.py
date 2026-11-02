#!/usr/bin/env python3
"""
Telegram-бот «Совещание Claude и ChatGPT».

Пользователь пишет вопрос. Бот проводит совещание: Claude предлагает,
ChatGPT критикует, Claude дорабатывает, три круга. Потом присылает общий
итог на русском, вердикт ChatGPT и файл с полным протоколом.

Настройки берутся из файла .env рядом со скриптом:
  TELEGRAM_BOT_TOKEN  токен от @BotFather (обязательно)
  ANTHROPIC_API_KEY   ключ Anthropic (обязательно)
  OPENAI_API_KEY      ключ OpenAI (обязательно)
  ALLOWED_USERS       Telegram ID через запятую, кому разрешено (очень желательно)
  CLAUDE_MODEL, GPT_MODEL, ROUNDS  необязательно
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import anthropic
import openai
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import core

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
DATA_DIR = HERE / "data"
STATE_PATH = DATA_DIR / "state.json"

TG_LIMIT = 4000          # лимит Telegram 4096, оставляем запас
MAX_HISTORY = 5          # сколько прошлых итогов помнить в чате
MAX_ROUNDS = 5

log = logging.getLogger("sovet-bot")


# ---------------------------------------------------------------------------
# Настройки и состояние
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    load_dotenv(ENV_PATH)
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not os.environ.get(k)]
    if missing:
        print(f"В файле {ENV_PATH} не хватает: {', '.join(missing)}", file=sys.stderr)
        print("Скопируйте .env.example в .env и заполните значения.", file=sys.stderr)
        sys.exit(1)
    allowed = {int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(";", ",").split(",") if x.strip().isdigit()}
    return {
        "token": os.environ["TELEGRAM_BOT_TOKEN"],
        "allowed": allowed,
        "claude_model": os.environ.get("CLAUDE_MODEL", core.DEFAULT_CLAUDE_MODEL),
        "gpt_model": os.environ.get("GPT_MODEL", core.DEFAULT_GPT_MODEL),
        "rounds": int(os.environ.get("ROUNDS", core.DEFAULT_ROUNDS)),
    }


@dataclass
class ChatState:
    history: list[tuple[str, str]] = field(default_factory=list)
    rounds: int = core.DEFAULT_ROUNDS
    full: bool = False          # присылать ли ход спора целиком
    busy: bool = False          # идёт ли совещание сейчас


class Store:
    """Состояние чатов в памяти с сохранением в JSON."""

    def __init__(self, default_rounds: int):
        self.default_rounds = default_rounds
        self.chats: dict[int, ChatState] = {}
        self._load()

    def get(self, chat_id: int) -> ChatState:
        if chat_id not in self.chats:
            self.chats[chat_id] = ChatState(rounds=self.default_rounds)
        return self.chats[chat_id]

    def _load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            for cid, st in raw.items():
                self.chats[int(cid)] = ChatState(
                    history=[tuple(x) for x in st.get("history", [])],
                    rounds=int(st.get("rounds", self.default_rounds)),
                    full=bool(st.get("full", False)),
                )
        except (OSError, ValueError) as e:
            log.warning("Не удалось прочитать %s: %s", STATE_PATH, e)

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        raw = {
            str(cid): {"history": st.history, "rounds": st.rounds, "full": st.full}
            for cid, st in self.chats.items()
        }
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Разбить длинный текст на части по абзацам, не ломая слова."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


async def send_long(update: Update, text: str) -> None:
    for part in split_text(text):
        await update.effective_chat.send_message(part)


async def keep_typing(chat, stop: asyncio.Event) -> None:
    """Показывать «печатает…», пока идёт совещание."""
    while not stop.is_set():
        try:
            await chat.send_action(ChatAction.TYPING)
        except TelegramError:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


def allowed(update: Update, settings: dict) -> bool:
    if not settings["allowed"]:
        return True
    user = update.effective_user
    return user is not None and user.id in settings["allowed"]


HELP = (
    "Я провожу совещание двух моделей по вашему вопросу.\n\n"
    "Как это работает: Claude предлагает решение, ChatGPT критикует, Claude дорабатывает. "
    "Так три круга. Потом Claude пишет общий итог, а ChatGPT даёт вердикт. "
    "В конце я пришлю файл с полным протоколом.\n\n"
    "Просто напишите вопрос обычным сообщением.\n\n"
    "Команды:\n"
    "/full — показывать ход спора целиком (вкл/выкл)\n"
    "/rounds 2 — число кругов (от 1 до 5)\n"
    "/reset — забыть прошлые итоги в этом чате\n"
    "/status — настройки и состояние\n"
    "/id — ваш Telegram ID\n\n"
    "Одно совещание занимает несколько минут и стоит денег: восемь запросов к моделям при трёх кругах."
)


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

class Bot:
    def __init__(self, settings: dict):
        self.s = settings
        self.store = Store(settings["rounds"])
        self.claude = core.Claude(settings["claude_model"])
        self.gpt = core.ChatGPT(settings["gpt_model"])

    # --- команды ---

    async def cmd_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        await update.effective_chat.send_message("Здравствуйте. " + HELP)

    async def cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        await update.effective_chat.send_message(HELP)

    async def cmd_id(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_chat.send_message(f"Ваш Telegram ID: {update.effective_user.id}")

    async def cmd_full(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        st = self.store.get(update.effective_chat.id)
        st.full = not st.full
        self.store.save()
        await update.effective_chat.send_message(
            "Теперь буду присылать ход спора целиком." if st.full
            else "Теперь буду присылать только короткие отметки о ходе и общий итог."
        )

    async def cmd_rounds(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        st = self.store.get(update.effective_chat.id)
        if not context.args or not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= MAX_ROUNDS:
            await update.effective_chat.send_message(f"Сейчас кругов: {st.rounds}. Чтобы изменить: /rounds 2 (от 1 до {MAX_ROUNDS}).")
            return
        st.rounds = int(context.args[0])
        self.store.save()
        await update.effective_chat.send_message(f"Кругов теперь: {st.rounds}.")

    async def cmd_reset(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        st = self.store.get(update.effective_chat.id)
        st.history.clear()
        self.store.save()
        await update.effective_chat.send_message("Прошлые итоги забыты. Следующий вопрос начнётся с чистого листа.")

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        st = self.store.get(update.effective_chat.id)
        await update.effective_chat.send_message(
            f"Claude: {self.s['claude_model']}\nChatGPT: {self.s['gpt_model']}\n"
            f"Кругов: {st.rounds}\nПолный ход спора: {'да' if st.full else 'нет'}\n"
            f"Итогов в памяти: {len(st.history)}\n"
            f"Совещание сейчас: {'идёт' if st.busy else 'нет'}"
        )

    async def deny(self, update: Update) -> None:
        await update.effective_chat.send_message(
            f"Этот бот частный. Ваш Telegram ID: {update.effective_user.id}. "
            "Попросите владельца добавить его в ALLOWED_USERS."
        )

    # --- вопрос ---

    async def on_question(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update, self.s):
            await self.deny(update)
            return
        chat = update.effective_chat
        st = self.store.get(chat.id)
        question = (update.message.text or "").strip()
        if not question:
            return
        if st.busy:
            await chat.send_message("Совещание ещё идёт. Дождитесь итога, потом задайте следующий вопрос.")
            return

        st.busy = True
        rounds = st.rounds
        stop = asyncio.Event()
        typing = asyncio.create_task(keep_typing(chat, stop))
        started = dt.datetime.now()

        async def on_step(phase: str, step: core.Step) -> None:
            if phase == "start":
                if step.kind in ("proposal", "critique"):
                    await chat.send_message(f"⏳ {step.title(rounds)}…")
                elif step.kind == "summary":
                    await chat.send_message("⏳ Claude пишет общий итог…")
                return
            # phase == "done"
            if step.kind in ("proposal", "critique"):
                if st.full:
                    await send_long(update, f"{step.speaker}:\n\n{step.text}")
            elif step.kind == "summary":
                await send_long(update, "ОБЩИЙ ИТОГ\n\n" + step.text)
            elif step.kind == "verdict":
                await send_long(update, "Вердикт ChatGPT:\n\n" + step.text)

        try:
            await chat.send_message(f"Начинаю совещание. Кругов: {rounds}. Это займёт несколько минут.")
            meeting = await core.hold_meeting(
                question, self.claude, self.gpt, rounds, list(st.history), on_step,
            )
        except (anthropic.AnthropicError, openai.OpenAIError, core.MeetingError) as e:
            log.exception("Ошибка совещания в чате %s", chat.id)
            await chat.send_message("Совещание не удалось. " + core.explain_error(e))
            return
        except Exception as e:  # noqa: BLE001  бот не должен падать от одного вопроса
            log.exception("Неожиданная ошибка в чате %s", chat.id)
            await chat.send_message(f"Совещание не удалось: {type(e).__name__}: {e}")
            return
        finally:
            stop.set()
            await typing
            st.busy = False

        st.history.append((question, meeting.summary))
        del st.history[:-MAX_HISTORY]
        self.store.save()

        protocol = meeting.protocol_markdown(self.s["claude_model"], self.s["gpt_model"])
        buf = BytesIO(protocol.encode("utf-8"))
        buf.name = f"sovet_{started:%Y-%m-%d_%H-%M}.md"
        minutes = (dt.datetime.now() - started).total_seconds() / 60
        await chat.send_document(buf, caption=f"Полный протокол. Совещание заняло {minutes:.0f} мин.")

    # --- запуск ---

    async def post_init(self, app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("start", "что умеет бот"),
            BotCommand("full", "показывать ход спора целиком (вкл/выкл)"),
            BotCommand("rounds", "число кругов, например /rounds 2"),
            BotCommand("reset", "забыть прошлые итоги"),
            BotCommand("status", "настройки и состояние"),
            BotCommand("id", "мой Telegram ID"),
        ])
        me = await app.bot.get_me()
        log.info("Бот @%s запущен. Claude: %s, ChatGPT: %s, кругов: %s, разрешённых пользователей: %s",
                 me.username, self.s["claude_model"], self.s["gpt_model"], self.s["rounds"],
                 len(self.s["allowed"]) or "все")
        if not self.s["allowed"]:
            log.warning("ALLOWED_USERS пуст: ботом может пользоваться любой, кто его найдёт. Это тратит ваши деньги.")

    def run(self) -> None:
        app = (
            ApplicationBuilder()
            .token(self.s["token"])
            .post_init(self.post_init)
            .concurrent_updates(True)
            .build()
        )
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("id", self.cmd_id))
        app.add_handler(CommandHandler("full", self.cmd_full))
        app.add_handler(CommandHandler("rounds", self.cmd_rounds))
        app.add_handler(CommandHandler("reset", self.cmd_reset))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_question))
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    Bot(load_settings()).run()


if __name__ == "__main__":
    main()
