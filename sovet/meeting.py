#!/usr/bin/env python3
"""
Совещание Claude и ChatGPT.

Вы задаёте вопрос. Claude предлагает решение, ChatGPT его критикует,
Claude дорабатывает. Так три круга. Потом Claude пишет общий итог на русском:
один лучший вариант или два-три варианта, если однозначного лидера нет.
ChatGPT в конце даёт короткий вердикт: согласен или нет.

Ключи хранятся в файле .env рядом с этим скриптом. Если ключей нет,
программа спросит их и сохранит сама.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import sys
import time
from pathlib import Path

import anthropic
import openai
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
LOG_DIR = HERE / "logs"

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_GPT_MODEL = "gpt-5"
DEFAULT_ROUNDS = 3

# ---------------------------------------------------------------------------
# Ключи и настройки
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

class Transcript:
    """Собирает весь ход совещания для файла-протокола и печати."""

    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.parts: list[str] = []

    def header(self, text: str) -> None:
        self.parts.append(f"\n## {text}\n")
        if not self.quiet:
            print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")

    def body(self, text: str) -> None:
        self.parts.append(text.strip() + "\n")

    def note(self, text: str) -> None:
        if not self.quiet:
            print(text)

    def markdown(self) -> str:
        return "\n".join(self.parts)


def print_live(chunk: str, quiet: bool) -> None:
    if not quiet:
        print(chunk, end="", flush=True)


# ---------------------------------------------------------------------------
# Участники
# ---------------------------------------------------------------------------

CLAUDE_SYSTEM = """Ты Claude, участник рабочего совещания. Второй участник ChatGPT будет критиковать твои предложения.
Правила:
- Отвечай только по-русски.
- Пиши по делу, без воды и без комплиментов собеседнику.
- Предлагай конкретные, выполнимые решения с короткими обоснованиями.
- Когда получаешь критику, честно признавай верные замечания и исправляй, а с неверными спорь коротко и аргументированно.
- Не повторяй целиком то, что уже говорил. Показывай, что изменилось."""

GPT_SYSTEM = """Ты ChatGPT, критик на рабочем совещании. Claude предлагает решение, твоя задача найти в нём слабые места.
Правила:
- Отвечай только по-русски.
- Ищи реальные риски, ошибки, пропуски, нереалистичные допущения, скрытые затраты.
- Критика должна быть конкретной: что именно не так и чем это грозит.
- Если что-то сделано хорошо, скажи об этом одной строкой и не разжёвывай.
- В конце дай короткий список из 2-5 пунктов: что обязательно нужно исправить.
- Не предлагай полностью своё решение вместо решения Claude. Улучшай его."""

FINAL_SYSTEM = """Ты Claude. Совещание закончено. Напиши общий итог для человека, который задал вопрос.
Правила:
- Только по-русски. Понятным языком, без жаргона, если его не было в вопросе.
- Структура: 1) краткий ответ в двух-трёх предложениях; 2) рекомендуемый вариант с шагами; 3) если был спор без явного победителя, дай два или три варианта с плюсами и минусами каждого и скажи, какой ты бы выбрал и почему; 4) риски и что проверить; 5) какие замечания ChatGPT были учтены.
- Не упоминай «круги» и внутреннюю кухню больше, чем нужно. Человек хочет решение, а не протокол."""

VERDICT_SYSTEM = """Ты ChatGPT. Прочитай итоговое решение Claude. Ответь по-русски, не больше пяти предложений:
согласен ли ты с итогом, и если остались возражения, назови их прямо. Без вступлений."""


class Claude:
    def __init__(self, model: str):
        self.client = anthropic.Anthropic()
        self.model = model

    def say(self, system: str, prompt: str, quiet: bool) -> str:
        text_parts: list[str] = []
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for chunk in stream.text_stream:
                text_parts.append(chunk)
                print_live(chunk, quiet)
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            detail = ""
            if final.stop_details is not None and getattr(final.stop_details, "explanation", None):
                detail = f" ({final.stop_details.explanation})"
            raise RuntimeError(f"Claude отказался отвечать на этот запрос{detail}.")
        if not quiet:
            print()
        return "".join(text_parts).strip()


class ChatGPT:
    def __init__(self, model: str):
        self.client = openai.OpenAI()
        self.model = model

    def say(self, system: str, prompt: str, quiet: bool) -> str:
        text_parts: list[str] = []
        with self.client.responses.stream(
            model=self.model,
            instructions=system,
            input=prompt,
            max_output_tokens=16000,
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    text_parts.append(event.delta)
                    print_live(event.delta, quiet)
            stream.get_final_response()
        if not quiet:
            print()
        return "".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Совещание
# ---------------------------------------------------------------------------

def context_block(history: list[tuple[str, str]]) -> str:
    """Предыдущие вопросы и итоги этой сессии, чтобы разговор был связным."""
    if not history:
        return ""
    chunks = ["Контекст: ранее в этой сессии уже обсуждалось и было решено следующее."]
    for i, (q, a) in enumerate(history, 1):
        chunks.append(f"\n--- Вопрос {i} ---\n{q}\n--- Итог {i} ---\n{a}")
    chunks.append("\nУчитывай это. Новый вопрос может на это опираться.\n")
    return "\n".join(chunks)


def hold_meeting(
    question: str,
    claude: Claude,
    gpt: ChatGPT,
    rounds: int,
    history: list[tuple[str, str]],
    quiet: bool,
) -> tuple[str, str, Transcript]:
    tr = Transcript(quiet)
    ctx = context_block(history)

    tr.header("ВОПРОС")
    tr.body(question)
    if not quiet:
        print(question)

    proposal = ""
    critique = ""
    log: list[str] = []  # накопленный ход обсуждения для промптов

    for r in range(1, rounds + 1):
        # --- Claude предлагает или дорабатывает ---
        tr.header(f"КРУГ {r} ИЗ {rounds}. Claude {'предлагает решение' if r == 1 else 'дорабатывает решение'}")
        if r == 1:
            prompt = f"{ctx}Вопрос от человека:\n\n{question}\n\nПредложи решение."
        else:
            prompt = (
                f"{ctx}Вопрос от человека:\n\n{question}\n\n"
                f"Ход обсуждения до этого момента:\n\n" + "\n\n".join(log) +
                f"\n\nУчти последнюю критику ChatGPT и дай доработанную версию решения (круг {r} из {rounds})."
            )
        proposal = claude.say(CLAUDE_SYSTEM, prompt, quiet)
        tr.body(proposal)
        log.append(f"[Claude, круг {r}]\n{proposal}")

        # --- ChatGPT критикует ---
        tr.header(f"КРУГ {r} ИЗ {rounds}. ChatGPT критикует")
        prompt = (
            f"{ctx}Вопрос от человека:\n\n{question}\n\n"
            f"Ход обсуждения:\n\n" + "\n\n".join(log) +
            f"\n\nРазбери последнее предложение Claude (круг {r} из {rounds})."
        )
        critique = gpt.say(GPT_SYSTEM, prompt, quiet)
        tr.body(critique)
        log.append(f"[ChatGPT, круг {r}]\n{critique}")

    # --- Общий итог ---
    tr.header("ОБЩИЙ ИТОГ (Claude)")
    prompt = (
        f"{ctx}Вопрос от человека:\n\n{question}\n\n"
        f"Полный ход совещания:\n\n" + "\n\n".join(log) +
        "\n\nНапиши общий итог."
    )
    if quiet:
        print("\n" + "=" * 70 + "\nОБЩИЙ ИТОГ\n" + "=" * 70)
    summary = claude.say(FINAL_SYSTEM, prompt, quiet=False)
    tr.body(summary)

    tr.header("Вердикт ChatGPT")
    prompt = (
        f"Вопрос от человека:\n\n{question}\n\n"
        f"Ход совещания:\n\n" + "\n\n".join(log) +
        f"\n\nИтоговое решение Claude:\n\n{summary}\n\nТвой вердикт."
    )
    print("\n--- Вердикт ChatGPT ---")
    verdict = gpt.say(VERDICT_SYSTEM, prompt, quiet=False)
    tr.body(verdict)

    return summary, verdict, tr


def save_log(question: str, tr: Transcript, claude_model: str, gpt_model: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = LOG_DIR / f"{stamp}.md"
    head = (
        f"# Протокол совещания\n\n"
        f"Дата: {dt.datetime.now():%d.%m.%Y %H:%M}\n\n"
        f"Участники: Claude ({claude_model}), ChatGPT ({gpt_model})\n"
    )
    path.write_text(head + tr.markdown(), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Ошибки API человеческим языком
# ---------------------------------------------------------------------------

def explain_error(e: Exception) -> str:
    who = "Anthropic" if isinstance(e, anthropic.AnthropicError) else "OpenAI" if isinstance(e, openai.OpenAIError) else "API"
    if isinstance(e, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return (f"{who}: ключ не принят. Проверьте {('ANTHROPIC_API_KEY' if who == 'Anthropic' else 'OPENAI_API_KEY')} "
                f"в файле {ENV_PATH}. Удалите строку с ключом, и программа спросит его заново.")
    if isinstance(e, (anthropic.PermissionDeniedError, openai.PermissionDeniedError)):
        return f"{who}: доступ запрещён. Возможно, у ключа нет прав на эту модель или не пополнен баланс."
    if isinstance(e, (anthropic.NotFoundError, openai.NotFoundError)):
        return f"{who}: модель не найдена. Поменяйте CLAUDE_MODEL или GPT_MODEL в {ENV_PATH}."
    if isinstance(e, (anthropic.RateLimitError, openai.RateLimitError)):
        return f"{who}: превышен лимит запросов или закончился баланс. Подождите минуту или проверьте оплату."
    if isinstance(e, (anthropic.APIConnectionError, openai.APIConnectionError)):
        return f"{who}: нет связи с сервером. Проверьте интернет."
    if isinstance(e, (anthropic.APIStatusError, openai.APIStatusError)):
        return f"{who}: ошибка {e.status_code}: {getattr(e, 'message', e)}"
    return f"{who}: {e}"


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Совещание Claude и ChatGPT: предложение, критика, доработка, общий итог.",
    )
    parser.add_argument("question", nargs="*", help="вопрос (если не указан, программа спросит)")
    parser.add_argument("-r", "--rounds", type=int, default=None, help=f"число кругов (по умолчанию {DEFAULT_ROUNDS})")
    parser.add_argument("-q", "--quiet", action="store_true", help="не показывать ход обсуждения, только итог")
    parser.add_argument("--once", action="store_true", help="один вопрос и выход, без продолжения диалога")
    args = parser.parse_args()

    ensure_keys()
    claude_model = os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
    gpt_model = os.environ.get("GPT_MODEL", DEFAULT_GPT_MODEL)
    rounds = args.rounds or int(os.environ.get("ROUNDS", DEFAULT_ROUNDS))

    claude = Claude(claude_model)
    gpt = ChatGPT(gpt_model)

    print(f"\nСовещание. Claude: {claude_model}. ChatGPT: {gpt_model}. Кругов: {rounds}.")
    print("Пустая строка или «выход» завершает программу.\n")

    history: list[tuple[str, str]] = []
    pending = " ".join(args.question).strip()

    while True:
        if pending:
            question = pending
            pending = ""
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
            summary, verdict, tr = hold_meeting(question, claude, gpt, rounds, history, args.quiet)
        except KeyboardInterrupt:
            print("\n\nПрервано.")
            return 130
        except (anthropic.AnthropicError, openai.OpenAIError) as e:
            print(f"\n\nОшибка. {explain_error(e)}", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(f"\n\nОшибка. {e}", file=sys.stderr)
            return 1

        history.append((question, summary))
        path = save_log(question, tr, claude_model, gpt_model)
        print(f"\nГотово за {time.time() - started:.0f} с. Протокол сохранён: {path}\n")

        if args.once:
            break

    print("До свидания.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
