#!/usr/bin/env python3
"""
Распознавание речи и субтитры для Reels.

Речь распознаёт OpenAI Whisper с точным временем каждого слова. Нужен
OPENAI_API_KEY в .env. Аудио из видео достаётся через ffmpeg.

Примеры:
  transcribe.py work/clip.mp4 --text work/clip.txt
  transcribe.py work/clip.mp4 --srt work/clip.srt --words work/clip.words.json
  transcribe.py work/clip.mp4 --ass work/clip.ass --words-per-line 3 --font-size 80 --margin-v 520
  transcribe.py --from-words work/clip.words.json --ass work/clip.ass   (пересобрать без нового распознавания)

Готовый .ass вжигается так:
  ffmpeg -i in.mp4 -vf "ass=work/clip.ass" -c:a copy out.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
END_PUNCT = (".", "!", "?", "…", ":", ";")


# ---------------------------------------------------------------------------
# Распознавание
# ---------------------------------------------------------------------------

def extract_audio(src: Path, dst: Path) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(dst)]
    subprocess.run(cmd, check=True)


def transcribe(path: Path, language: str | None = "ru", prompt: str | None = None) -> dict:
    """Возвращает {"text": str, "words": [{word,start,end}], "segments": [{text,start,end}]}."""
    from openai import OpenAI

    client = OpenAI()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.mp3"
        extract_audio(path, audio)
        if audio.stat().st_size > 24 * 1024 * 1024:
            raise SystemExit("Аудио длиннее примерно часа. Разрежьте видео на части и распознайте по очереди.")
        kwargs = dict(
            model=WHISPER_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        with audio.open("rb") as f:
            res = client.audio.transcriptions.create(file=f, **kwargs)
    words = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)} for w in (res.words or [])]
    segments = [{"text": s.text.strip(), "start": float(s.start), "end": float(s.end)} for s in (res.segments or [])]
    text = (res.text or "").strip()
    attach_punctuation(words, text)
    return {"text": text, "words": words, "segments": segments}


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def attach_punctuation(words: list[dict], text: str) -> None:
    """Whisper отдаёт слова без знаков препинания. Берём их из полного текста,
    чтобы субтитры переносились по концу фразы и выглядели грамотно."""
    tokens = text.split()
    ti = 0
    for w in words:
        target = _norm(w["word"])
        if not target:
            continue
        for look in range(ti, min(ti + 4, len(tokens))):
            if _norm(tokens[look]) == target:
                tok = tokens[look]
                # оставляем ведущие и завершающие знаки (кавычки, «!», «,»), само слово берём из токена
                w["word"] = tok
                ti = look + 1
                break


def transcribe_text(path: Path, language: str | None = "ru") -> str:
    """Только текст. Используется ботом для голосовых сообщений."""
    from openai import OpenAI

    client = OpenAI()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.mp3"
        extract_audio(path, audio)
        with audio.open("rb") as f:
            kwargs = {"model": os.environ.get("VOICE_MODEL", "gpt-4o-transcribe"), "file": f}
            if language:
                kwargs["language"] = language
            res = client.audio.transcriptions.create(**kwargs)
    return (res.text or "").strip()


# ---------------------------------------------------------------------------
# Форматы
# ---------------------------------------------------------------------------

def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def chunk_words(words: list[dict], max_words: int, max_chars: int, max_gap: float) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            text_len = len(" ".join(x["word"] for x in cur + [w]))
            gap = w["start"] - cur[-1]["end"]
            if len(cur) >= max_words or text_len > max_chars or gap > max_gap or cur[-1]["word"].endswith(END_PUNCT):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def to_srt(chunks: list[list[dict]]) -> str:
    lines = []
    for i, ch in enumerate(chunks, 1):
        text = " ".join(w["word"] for w in ch)
        lines += [str(i), f"{_ts_srt(ch[0]['start'])} --> {_ts_srt(ch[-1]['end'])}", text, ""]
    return "\n".join(lines)


def _ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> &H00BBGGRR (формат ASS)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        raise SystemExit(f"Цвет должен быть в виде #RRGGBB, получено {hex_rgb}")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def to_ass(chunks: list[list[dict]], a) -> str:
    primary = _ass_color(a.highlight if a.karaoke else a.color)
    secondary = _ass_color(a.color)
    outline = _ass_color(a.outline_color)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {a.width}
PlayResY: {a.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reel,{a.font},{a.font_size},{primary},{secondary},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{a.outline},2,2,80,80,{a.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for i, ch in enumerate(chunks):
        start = ch[0]["start"]
        # держим строку до начала следующей, но не дольше 0.4 с после последнего слова
        end = ch[-1]["end"] + 0.4
        if i + 1 < len(chunks):
            end = min(end, chunks[i + 1][0]["start"])
        end = max(end, ch[-1]["end"])
        if a.karaoke:
            parts = []
            for j, w in enumerate(ch):
                nxt = ch[j + 1]["start"] if j + 1 < len(ch) else w["end"]
                dur = max(1, int(round((nxt - w["start"]) * 100)))
                parts.append(f"{{\\k{dur}}}{_esc(w['word'])}")
            text = " ".join(parts)
        else:
            text = " ".join(_esc(w["word"]) for w in ch)
        if a.upper:
            text = text.upper().replace("\\K", "\\k")
        events.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Reel,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


def _esc(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")


# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help="видео или аудио")
    p.add_argument("--from-words", help="взять готовый words.json вместо распознавания")
    p.add_argument("--lang", default="ru", help="язык речи, например ru или en; 'auto' для автоопределения")
    p.add_argument("--prompt", help="подсказка распознаванию: имена, бренды, термины")
    p.add_argument("--text", help="сохранить текст")
    p.add_argument("--words", help="сохранить слова с таймингами (JSON)")
    p.add_argument("--srt", help="сохранить субтитры SRT")
    p.add_argument("--ass", help="сохранить субтитры ASS в стиле Reels")
    g = p.add_argument_group("оформление ASS")
    g.add_argument("--words-per-line", type=int, default=3)
    g.add_argument("--max-chars", type=int, default=24)
    g.add_argument("--max-gap", type=float, default=0.6)
    g.add_argument("--font", default="Montserrat")
    g.add_argument("--font-size", type=int, default=78)
    g.add_argument("--color", default="#FFFFFF", help="цвет текста")
    g.add_argument("--highlight", default="#FFE500", help="цвет произнесённого слова")
    g.add_argument("--outline-color", default="#000000")
    g.add_argument("--outline", type=int, default=5)
    g.add_argument("--margin-v", type=int, default=520, help="отступ снизу в пикселях; 520 держит текст выше кнопок Instagram")
    g.add_argument("--no-karaoke", dest="karaoke", action="store_false", help="без подсветки слов")
    g.add_argument("--upper", action="store_true", help="ЗАГЛАВНЫМИ")
    g.add_argument("--width", type=int, default=1080)
    g.add_argument("--height", type=int, default=1920)
    a = p.parse_args()

    if a.from_words:
        data = {"words": json.loads(Path(a.from_words).read_text(encoding="utf-8"))}
        data["text"] = " ".join(w["word"] for w in data["words"])
    elif a.input:
        data = transcribe(Path(a.input), None if a.lang == "auto" else a.lang, a.prompt)
    else:
        p.error("укажите файл или --from-words")

    chunks = chunk_words(data["words"], a.words_per_line, a.max_chars, a.max_gap)
    if a.text:
        Path(a.text).write_text(data["text"] + "\n", encoding="utf-8")
    if a.words:
        Path(a.words).write_text(json.dumps(data["words"], ensure_ascii=False, indent=1), encoding="utf-8")
    if a.srt:
        Path(a.srt).write_text(to_srt(chunks), encoding="utf-8")
    if a.ass:
        Path(a.ass).write_text(to_ass(chunks, a), encoding="utf-8")
    if not any((a.text, a.words, a.srt, a.ass)):
        print(data["text"])
    else:
        dur = data["words"][-1]["end"] if data["words"] else 0
        print(f"Готово: {len(data['words'])} слов, {len(chunks)} строк субтитров, {dur:.1f} с речи.")


if __name__ == "__main__":
    main()
