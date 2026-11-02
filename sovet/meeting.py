#!/usr/bin/env python3
"""
Совещание Claude и ChatGPT в консоли.

Вы задаёте вопрос. Claude предлагает решение, ChatGPT его критикует,
Claude дорабатывает. Так три круга. Потом Claude пишет общий итог на русском,
ChatGPT даёт короткий вердикт.

Ключи хранятся в файле .env рядом с этим скриптом. Если ключей нет,
программа спросит их и сохранит сама. Версия для Telegram: bot.py.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import os
import sys
import time
from pathlib import Path

import anthropic
import openai
from dotenv import load_dotenv

import core

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
LOG_DIR = HERE / "logs"

KEY_HINTS = {
    "ANTHROPIC_API_KEY": "ключ Anthropic (начинается с sk-ant-...), взять: https://console.anthropic.com/settings/keys",
    "OPENAI_API_KEY": "ключ OpenAI (начинается с sk-...), взять: https://platform.openai.com/api-keys",
}


def save_env_value(name: str, value: str) -> None:
    """Записать или обновить одну переменную в .env, не трогая остальные."""
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[i] = f"{name}={value}"
            found = True
    if not found:
        lines.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def ensure_keys() -> None:
    """Загрузить .env и спросить недостающие ключи."""
    load_dotenv(ENV_PATH)
    for name, hint in KEY_HINTS.items():
        if os.environ.get(name):
            continue
        print(f"\nНужен {hint}")
        while True:
            value = getpass.getpass(f"Введите {name} (ввод скрыт): ").strip()
            if value:
                break
            print("Пустое значение. Попробуйте ещё раз.")
        os.environ[name] = value
        save_env_value(name, value)
        print(f"Сохранено в {ENV_PATH}")


def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


async def run_meeting(question, claude, gpt, rounds, history, quiet) -> core.Meeting:
    async def on_step(phase: str, step: core.Step) -> None:
        show = (not quiet) or step.kind in ("summary", "verdict")
        if not show:
            return
        if phase == "start":
            banner(step.title(rounds))
        else:
            print()

    def on_chunk(text: str) -> None:
        print(text, end="", flush=True)

    # В тихом режиме поток текста показываем только для итога и вердикта.
    chunk_cb = on_chunk
    if quiet:
        state = {"show": False}

        async def on_step_quiet(phase: str, step: core.Step) -> None:
            state["show"] = step.kind in ("summary", "verdict")
            await on_step(phase, step)

        def on_chunk_quiet(text: str) -> None:
            if state["show"]:
                print(text, end="", flush=True)

        return await core.hold_meeting(question, claude, gpt, rounds, history, on_step_quiet, on_chunk_quiet)

    return await core.hold_meeting(question, claude, gpt, rounds, history, on_step, chunk_cb)


def save_log(meeting: core.Meeting, claude_model: str, gpt_model: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = LOG_DIR / f"{stamp}.md"
    path.write_text(meeting.protocol_markdown(claude_model, gpt_model), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Совещание Claude и ChatGPT: предложение, критика, доработка, общий итог.",
    )
    parser.add_argument("question", nargs="*", help="вопрос (если не указан, программа спросит)")
    parser.add_argument("-r", "--rounds", type=int, default=None, help=f"число кругов (по умолчанию {core.DEFAULT_ROUNDS})")
    parser.add_argument("-q", "--quiet", action="store_true", help="не показывать ход обсуждения, только итог")
    parser.add_argument("--once", action="store_true", help="один вопрос и выход, без продолжения диалога")
    args = parser.parse_args()

    ensure_keys()
    claude_model = os.environ.get("CLAUDE_MODEL", core.DEFAULT_CLAUDE_MODEL)
    gpt_model = os.environ.get("GPT_MODEL", core.DEFAULT_GPT_MODEL)
    rounds = args.rounds or int(os.environ.get("ROUNDS", core.DEFAULT_ROUNDS))

    claude = core.Claude(claude_model)
    gpt = core.ChatGPT(gpt_model)

    print(f"\nСовещание. Claude: {claude_model}. ChatGPT: {gpt_model}. Кругов: {rounds}.")
    print("Пустая строка или «выход» завершает программу.\n")

    history: list[tuple[str, str]] = []
    pending = " ".join(args.question).strip()

    while True:
        if pending:
            question, pending = pending, ""
            print(f"Ваш вопрос: {question}")
        else:
            try:
                question = input("Ваш вопрос: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
        if not question or question.lower() in {"выход", "exit", "quit", "q"}:
            break

        started = time.time()
        try:
            meeting = asyncio.run(run_meeting(question, claude, gpt, rounds, history, args.quiet))
        except KeyboardInterrupt:
            print("\n\nПрервано.")
            return 130
        except (anthropic.AnthropicError, openai.OpenAIError, core.MeetingError) as e:
            print(f"\n\nОшибка. {core.explain_error(e)}", file=sys.stderr)
            return 1

        history.append((question, meeting.summary))
        path = save_log(meeting, claude_model, gpt_model)
        print(f"\nГотово за {time.time() - started:.0f} с. Протокол сохранён: {path}\n")

        if args.once:
            break

    print("До свидания.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
