"""
Ядро совещания: подсказки моделям, вызовы Claude и ChatGPT, ход обсуждения.

Используется и консольной программой meeting.py, и Telegram-ботом bot.py.
Весь код асинхронный, чтобы бот мог обслуживать несколько чатов сразу.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import anthropic
import openai

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_GPT_MODEL = "gpt-5"
DEFAULT_ROUNDS = 3

# ---------------------------------------------------------------------------
# Роли участников
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
- Не упоминай «круги» и внутреннюю кухню больше, чем нужно. Человек хочет решение, а не протокол.
- Не используй markdown-разметку (звёздочки, решётки). Структуру показывай нумерацией и пустыми строками."""

VERDICT_SYSTEM = """Ты ChatGPT. Прочитай итоговое решение Claude. Ответь по-русски, не больше пяти предложений:
согласен ли ты с итогом, и если остались возражения, назови их прямо. Без вступлений и без markdown-разметки."""


# ---------------------------------------------------------------------------
# Участники
# ---------------------------------------------------------------------------

OnChunk = Callable[[str], None] | None


class Claude:
    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL):
        self.client = anthropic.AsyncAnthropic()
        self.model = model

    async def say(self, system: str, prompt: str, on_chunk: OnChunk = None) -> str:
        parts: list[str] = []
        async with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            async for chunk in stream.text_stream:
                parts.append(chunk)
                if on_chunk:
                    on_chunk(chunk)
            final = await stream.get_final_message()
        if final.stop_reason == "refusal":
            detail = ""
            if final.stop_details is not None and getattr(final.stop_details, "explanation", None):
                detail = f" ({final.stop_details.explanation})"
            raise MeetingError(f"Claude отказался отвечать на этот запрос{detail}.")
        return "".join(parts).strip()


class ChatGPT:
    def __init__(self, model: str = DEFAULT_GPT_MODEL):
        self.client = openai.AsyncOpenAI()
        self.model = model

    async def say(self, system: str, prompt: str, on_chunk: OnChunk = None) -> str:
        parts: list[str] = []
        async with self.client.responses.stream(
            model=self.model,
            instructions=system,
            input=prompt,
            max_output_tokens=16000,
        ) as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    parts.append(event.delta)
                    if on_chunk:
                        on_chunk(event.delta)
            await stream.get_final_response()
        return "".join(parts).strip()


class MeetingError(Exception):
    """Ошибка хода совещания, понятная человеку."""


# ---------------------------------------------------------------------------
# Ход совещания
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """Один шаг совещания: кто говорил, на каком круге, что сказал."""
    kind: str          # proposal | critique | summary | verdict
    speaker: str       # Claude | ChatGPT
    round: int         # 0 для итога и вердикта
    text: str

    def title(self, rounds: int) -> str:
        if self.kind == "proposal":
            action = "предлагает решение" if self.round == 1 else "дорабатывает решение"
            return f"Круг {self.round} из {rounds}. Claude {action}"
        if self.kind == "critique":
            return f"Круг {self.round} из {rounds}. ChatGPT критикует"
        if self.kind == "summary":
            return "Общий итог (Claude)"
        return "Вердикт ChatGPT"


@dataclass
class Meeting:
    question: str
    rounds: int
    steps: list[Step] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return next((s.text for s in self.steps if s.kind == "summary"), "")

    @property
    def verdict(self) -> str:
        return next((s.text for s in self.steps if s.kind == "verdict"), "")

    def protocol_markdown(self, claude_model: str, gpt_model: str) -> str:
        lines = [
            "# Протокол совещания",
            "",
            f"Дата: {dt.datetime.now():%d.%m.%Y %H:%M}",
            "",
            f"Участники: Claude ({claude_model}), ChatGPT ({gpt_model})",
            "",
            "## Вопрос",
            "",
            self.question,
            "",
        ]
        for s in self.steps:
            lines += [f"## {s.title(self.rounds)}", "", s.text, ""]
        return "\n".join(lines)


# Обратный вызов: вызывается в начале каждого шага и после его завершения.
# phase = "start" | "done"
OnStep = Callable[[str, Step], Awaitable[None]] | None


def context_block(history: list[tuple[str, str]]) -> str:
    """Предыдущие вопросы и итоги, чтобы разговор был связным."""
    if not history:
        return ""
    chunks = ["Контекст: ранее уже обсуждалось и было решено следующее."]
    for i, (q, a) in enumerate(history, 1):
        chunks.append(f"\n--- Вопрос {i} ---\n{q}\n--- Итог {i} ---\n{a}")
    chunks.append("\nУчитывай это. Новый вопрос может на это опираться.\n")
    return "\n".join(chunks)


async def hold_meeting(
    question: str,
    claude: Claude,
    gpt: ChatGPT,
    rounds: int = DEFAULT_ROUNDS,
    history: list[tuple[str, str]] | None = None,
    on_step: OnStep = None,
    on_chunk: OnChunk = None,
) -> Meeting:
    """Провести совещание целиком и вернуть все шаги."""
    history = history or []
    ctx = context_block(history)
    meeting = Meeting(question=question, rounds=rounds)
    log: list[str] = []

    async def run(step: Step, system: str, prompt: str, who: Claude | ChatGPT) -> None:
        if on_step:
            await on_step("start", step)
        step.text = await who.say(system, prompt, on_chunk)
        meeting.steps.append(step)
        if on_step:
            await on_step("done", step)

    for r in range(1, rounds + 1):
        if r == 1:
            prompt = f"{ctx}Вопрос от человека:\n\n{question}\n\nПредложи решение."
        else:
            prompt = (
                f"{ctx}Вопрос от человека:\n\n{question}\n\n"
                f"Ход обсуждения до этого момента:\n\n" + "\n\n".join(log) +
                f"\n\nУчти последнюю критику ChatGPT и дай доработанную версию решения (круг {r} из {rounds})."
            )
        step = Step("proposal", "Claude", r, "")
        await run(step, CLAUDE_SYSTEM, prompt, claude)
        log.append(f"[Claude, круг {r}]\n{step.text}")

        prompt = (
            f"{ctx}Вопрос от человека:\n\n{question}\n\n"
            f"Ход обсуждения:\n\n" + "\n\n".join(log) +
            f"\n\nРазбери последнее предложение Claude (круг {r} из {rounds})."
        )
        step = Step("critique", "ChatGPT", r, "")
        await run(step, GPT_SYSTEM, prompt, gpt)
        log.append(f"[ChatGPT, круг {r}]\n{step.text}")

    prompt = (
        f"{ctx}Вопрос от человека:\n\n{question}\n\n"
        f"Полный ход совещания:\n\n" + "\n\n".join(log) +
        "\n\nНапиши общий итог."
    )
    summary = Step("summary", "Claude", 0, "")
    await run(summary, FINAL_SYSTEM, prompt, claude)

    prompt = (
        f"Вопрос от человека:\n\n{question}\n\n"
        f"Ход совещания:\n\n" + "\n\n".join(log) +
        f"\n\nИтоговое решение Claude:\n\n{summary.text}\n\nТвой вердикт."
    )
    await run(Step("verdict", "ChatGPT", 0, ""), VERDICT_SYSTEM, prompt, gpt)

    return meeting


# ---------------------------------------------------------------------------
# Ошибки API человеческим языком
# ---------------------------------------------------------------------------

def explain_error(e: Exception) -> str:
    if isinstance(e, MeetingError):
        return str(e)
    who = "Anthropic" if isinstance(e, anthropic.AnthropicError) else "OpenAI" if isinstance(e, openai.OpenAIError) else "API"
    key = "ANTHROPIC_API_KEY" if who == "Anthropic" else "OPENAI_API_KEY"
    if isinstance(e, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return f"{who}: ключ не принят. Проверьте {key} в файле .env."
    if isinstance(e, (anthropic.PermissionDeniedError, openai.PermissionDeniedError)):
        return f"{who}: доступ запрещён. Возможно, у ключа нет прав на модель, не пополнен баланс или сервис недоступен из этой страны."
    if isinstance(e, (anthropic.NotFoundError, openai.NotFoundError)):
        return f"{who}: модель не найдена. Поменяйте CLAUDE_MODEL или GPT_MODEL в .env."
    if isinstance(e, (anthropic.RateLimitError, openai.RateLimitError)):
        return f"{who}: превышен лимит запросов или закончился баланс. Подождите минуту или проверьте оплату."
    if isinstance(e, (anthropic.APIConnectionError, openai.APIConnectionError)):
        return f"{who}: нет связи с сервером. Проверьте интернет."
    if isinstance(e, (anthropic.APIStatusError, openai.APIStatusError)):
        return f"{who}: ошибка {e.status_code}: {getattr(e, 'message', e)}"
    return f"{who}: {e}"
