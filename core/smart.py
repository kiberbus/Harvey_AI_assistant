"""ИИ для текста и экрана: вопросы, выделенный текст, умная запись, «что на экране».

Всё здесь — запросы к модели БЕЗ инструментов: так она не пытается «открыть» что-нибудь
в ответ на «что такое ютуб» и отвечает быстрее."""

from __future__ import annotations

import difflib
import io
import re
import time
from typing import Callable

import ollama

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
from core import llm
from core.util import (  # noqa: F401
    END,
    FAIL,
    INFO,
    PUNCT,
    _rx,
    log,
)
from core.apps import (  # noqa: F401
    foreground_is_mine,
    last_dictation,
)
from core.speech import (  # noqa: F401
    speak_stream,
)
from core.winapi import (  # noqa: F401
    _set_clipboard_text,
    _user32,
    copy_selection,
    foreground_rect,
    paste_text,
    select_back,
)

# (фраза для ответа в обычном формате — или None, если уже сказано вслух; что запомнить для контекста)
Result = tuple[str | None, str]

ADDRESS_RULE = (f'К пользователю обращайся "{HONORIFIC}".' if USE_HONORIFIC
                else "К пользователю обращайся на «вы», без обращений вроде «господин».")
ANSWER_PROMPT = f"""Ты — голосовой ассистент по имени {ASSISTANT_NAME}. Твой ответ прочитают вслух.
Отвечай по-русски одним-двумя короткими предложениями, без списков, разметки, ссылок и эмодзи. {ADDRESS_RULE}
Если просят перевести — назови только перевод."""
SCREEN_PROMPT = ANSWER_PROMPT + """
К вопросу приложен снимок экрана пользователя. Отвечай по нему. Если на нём ошибка — коротко скажи,
в чём она и как её исправить. Если просят прочитать текст — перескажи главное."""
EDIT_PROMPT = """Ты — редактор текста. Верни ТОЛЬКО итоговый текст: без кавычек, пояснений, заголовков и разметки.
Сохраняй язык оригинала (если не просят перевести), переносы строк и смысл."""

FIX = "Исправь орфографические, пунктуационные и грамматические ошибки. Слова и стиль не меняй."
PUNCTUATE = "Расставь знаки препинания и заглавные буквы. Слова не меняй, не добавляй и не убирай."
DEFAULT_STYLE = "грамотнее и красивее"

QUESTION_RE = _rx(QUESTION)
SCREEN_RE = _rx(SCREEN)
SELECTION_WORDS_RE = re.compile(SELECTION_WORDS)
SELECTION_THIS_RE = re.compile(SELECTION_THIS)
SELECTION_ACTION_RES = tuple((name, re.compile(p)) for name, p in SELECTION_ACTIONS.items())
STYLE_RES = tuple((re.compile(rf"\b(?:{p})"), instruction) for p, instruction in REWRITE_STYLES.items())
LANG_RES = tuple((re.compile(rf"\b(?:{p})"), lang) for p, lang in TRANSLATE_LANGS.items())
REPLACE_RE = re.compile(r"\b(?:вставь|замени)\b")
# Слова, которые остаются от «переведи на английский», «исправь ошибки», если текста в команде нет
_BARE_FILLER_RE = re.compile(
    r"\b(?:на|в|во|по|мне|пожалуйста|язык\w*|стил\w*|текст\w*|ошибк\w*|орфографи\w*|грамматик\w*|"
    r"пунктуаци\w*|и|вставь|замени|его|её|ее|кратко|коротко)\b")
# «запиши вежливо: …», «запиши в деловом стиле …» — стиль в начале диктовки
_DICTATE_STYLE_RE = re.compile(rf"^(?:в\s+)?(?:{'|'.join(REWRITE_STYLES)})(?:\s+стил\w*)?[\s,:—-]+(?P<text>.+)$",
                               re.IGNORECASE | re.DOTALL)


# ───────────────────────── РАЗБОР КОМАНДЫ ─────────────────────────
def _style(low: str) -> str | None:
    for rx, instruction in STYLE_RES:
        if rx.search(low):
            return instruction
    return None


def _bare(low: str, verb: re.Pattern) -> bool:
    """В команде нет своего текста — только глагол, язык, стиль: «переведи на английский», «исправь ошибки»."""
    rest = verb.sub(" ", low)
    for rx, _ in (*LANG_RES, *STYLE_RES):
        rest = rx.sub(" ", rest)
    return not _BARE_FILLER_RE.sub(" ", rest).strip(PUNCT + " ")


def parse(low: str) -> Callable[[list[dict]], Result] | None:
    """Вопрос, работа с выделенным или «что на экране»? Возвращает действие (ему передаётся
    история диалога) или None. Обычные команды проверяются раньше — сюда приходит остальное."""
    low = " ".join(low.strip(PUNCT).split())
    if not low:
        return None
    if VISION_ENABLED and SCREEN_RE.search(low):
        return lambda history: ask_screen(low)
    style = _style(low)
    for action, verb in SELECTION_ACTION_RES:
        if not verb.search(low):
            continue
        if action == "fix" and style:
            action = "rewrite"                       # «исправь в деловом стиле» — это переписать
        pointed = SELECTION_WORDS_RE.search(low) or SELECTION_THIS_RE.match(low)
        if pointed or (action == "rewrite" and style) or (action != "rewrite" and _bare(low, verb)):
            return lambda history, a=action: on_selection(a, low, style)
        break
    if QUESTION_RE.search(low):
        return lambda history: ask(low, history)
    return None


# ───────────────────────── ЗАПРОСЫ К МОДЕЛИ ─────────────────────────
def _ctx_for(text: str) -> int | None:
    """Длинному тексту нужен контекст больше обычного (модель перезагрузится — это пара секунд)."""
    need = len(text) * 2 // 3 + 500
    if need <= NUM_CTX:
        return None
    return min(16384, 1 << (need - 1).bit_length())


def _texts(messages: list[dict], num_predict: int, num_ctx: int | None = None):
    for chunk in llm.chat_stream(messages, num_predict=num_predict, num_ctx=num_ctx):
        if chunk.message.content:
            yield chunk.message.content


_FENCE_RE = re.compile(r"^```\w*\s*|\s*```$")
_INTRO_RE = re.compile(r"^(?:вот|исправленный|переписанный|перевод|итоговый)[^\n]{0,40}:\s*\n", re.IGNORECASE)
_QUOTES = ("«»", '""', "“”", "„“")


def clean_output(text: str) -> str:
    """Убирает то, что модель иногда добавляет вокруг текста: ```, «Вот исправленный текст:», кавычки."""
    text = _INTRO_RE.sub("", _FENCE_RE.sub("", text.strip())).strip()
    for left, right in _QUOTES:
        if len(text) > 1 and text[0] == left and text[-1] == right and text.count(left) == 1:
            text = text[1:-1].strip()
    return text


def edit(instruction: str, text: str) -> str:
    """Переделывает текст по инструкции и возвращает только результат."""
    messages = [{"role": "system", "content": EDIT_PROMPT},
                {"role": "user", "content": f"{instruction}\n\nТекст:\n{text}"}]
    msg = llm.chat(messages, num_predict=len(text) // 2 + 100, num_ctx=_ctx_for(text)).message
    return clean_output(msg.content or "")


def _speak_answer(messages: list[dict], num_predict: int = 150, num_ctx: int | None = None) -> Result:
    said = speak_stream(_texts(messages, num_predict, num_ctx))
    return (None, said) if said else (f"{FAIL}не получилось ответить", "")


def ask(question: str, history: list[dict]) -> Result:
    """«Что такое…», «объясни…», «переведи на английский…» — короткий ответ вслух."""
    messages = [{"role": "system", "content": ANSWER_PROMPT}, *history, {"role": "user", "content": question}]
    return _speak_answer(messages)


# ───────────────────────── ЧТО НА ЭКРАНЕ ─────────────────────────
def _screenshot() -> bytes:
    from PIL import ImageGrab

    bbox = foreground_rect() if SCREEN_AREA == "window" else None
    image = ImageGrab.grab(bbox=bbox, all_screens=bbox is not None)
    image.thumbnail((SCREEN_MAX_SIDE, SCREEN_MAX_SIDE))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def ask_screen(question: str) -> Result:
    """«Что тут написано», «что это за ошибка» — снимок активного окна уходит в модель вместе с вопросом."""
    image = _screenshot()
    log("Экран", f"снимок {len(image) // 1024} КБ")
    messages = [{"role": "system", "content": SCREEN_PROMPT},
                {"role": "user", "content": question, "images": [image]}]
    try:
        return _speak_answer(messages, num_predict=200)
    except ollama.ResponseError as e:
        if re.search(r"image|vision|multimodal", str(e), re.IGNORECASE):
            log("Экран", f"модель {MODEL} не понимает изображения: {e}")
            return f"{FAIL}моя модель не понимает изображения", ""
        raise


# ───────────────────────── ВЫДЕЛЕННЫЙ ТЕКСТ ─────────────────────────
def _target_lang(low: str, text: str) -> str:
    for rx, lang in LANG_RES:
        if rx.search(low):
            return lang
    cyrillic = len(re.findall(r"[а-яё]", text, re.IGNORECASE))
    latin = len(re.findall(r"[a-z]", text, re.IGNORECASE))
    return "английский" if cyrillic >= latin else "русский"


def _select_last_dictation() -> str:
    """Ничего не выделено, но Харви только что записала текст в это же окно — выделяем его."""
    if not last_dictation or time.time() - last_dictation["at"] > REWRITE_LAST_SEC:
        return ""
    if last_dictation["hwnd"] != _user32.GetForegroundWindow():
        return ""
    select_back(len(last_dictation["text"]))
    return last_dictation["text"]


def _replace_selection(text: str) -> None:
    paste_text(text)
    last_dictation.update(text=text.replace("\r", " ").replace("\n", " "),
                          hwnd=_user32.GetForegroundWindow(), at=time.time())


def on_selection(action: str, low: str, style: str | None) -> Result:
    """Ctrl+C → ИИ → ответ вслух (объяснение, пересказ, перевод) или текст вместо выделенного."""
    if foreground_is_mine():
        return f"{FAIL}сначала переключитесь на окно с текстом", ""
    text = copy_selection().strip()
    if not text and action in ("fix", "rewrite"):
        text = _select_last_dictation()
    if not text:
        if action != "rewrite" and action != "fix" and VISION_ENABLED:
            return ask_screen(low)                   # «объясни это» без выделения — смотрим на экран
        return f"{FAIL}сначала выделите текст", ""
    if len(text) > SELECTION_MAX_CHARS:
        return f"{FAIL}выделено слишком много текста, больше {SELECTION_MAX_CHARS} символов", ""
    log("Выделено", text if len(text) <= 200 else text[:200] + "…")
    num_ctx = _ctx_for(text)

    if action in ("explain", "summary"):
        instruction = ("Объясни простыми словами, что значит этот текст." if action == "explain"
                       else "Перескажи суть этого текста.")
        messages = [{"role": "system", "content": ANSWER_PROMPT},
                    {"role": "user", "content": f"{instruction}\n\nТекст:\n{text}"}]
        return _speak_answer(messages, num_ctx=num_ctx)

    if action == "translate":
        lang = _target_lang(low, text)
        if REPLACE_RE.search(low):
            result = edit(f"Переведи текст на {lang} язык.", text)
            if not result:
                return f"{FAIL}не получилось перевести", ""
            _replace_selection(result)
            return f"перевел{END} текст на {lang}", ""
        messages = [{"role": "system", "content": EDIT_PROMPT},
                    {"role": "user", "content": f"Переведи текст на {lang} язык.\n\nТекст:\n{text}"}]
        said = speak_stream(_texts(messages, len(text) // 2 + 100, num_ctx))
        if not said:
            return f"{FAIL}не получилось перевести", ""
        if TRANSLATE_TO_CLIPBOARD:
            _set_clipboard_text(clean_output(said))
        return None, said

    # fix / rewrite — новый текст встаёт на место выделенного
    instruction = FIX if action == "fix" else f"Перепиши текст {style or DEFAULT_STYLE}."
    result = edit(instruction, text)
    if not result:
        return f"{FAIL}не получилось {'исправить' if action == 'fix' else 'переписать'} текст", ""
    if result == text:
        return (f"{INFO}ошибок не {'нашла' if FEMALE_VOICE else 'нашёл'}" if action == "fix"
                else f"{INFO}текст и так хорош"), ""
    _replace_selection(result)
    return (f"исправил{END} текст" if action == "fix" else f"переписал{END} текст"), ""


# ───────────────────────── УМНАЯ ЗАПИСЬ ─────────────────────────
def _letters(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower().replace("ё", "е"))


def same_words(before: str, after: str) -> bool:
    """Модель только расставила знаки — или заодно поменяла слова? Сравниваем одни буквы."""
    a, b = _letters(before), _letters(after)
    return bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def prepare_dictation(text: str) -> str:
    """«запиши вежливо …» — переписать в этом стиле; иначе — расставить знаки препинания (SMART_DICTATION).
    Ошибка или модель поменяла слова — пишем как услышали."""
    m = _DICTATE_STYLE_RE.match(text.strip())
    if m:
        instruction, source = f"Перепиши текст {_style(text.lower()) or DEFAULT_STYLE}.", m.group("text")
    elif SMART_DICTATION:
        instruction, source = PUNCTUATE, text
    else:
        return text
    try:
        result = edit(instruction, source.strip())
    except Exception as e:
        log("Запись", f"ИИ не ответил, пишу как есть: {e}")
        return source
    if not result:
        return source
    if not m and not same_words(source, result):
        log("Запись", f"ИИ поменял слова («{result}») — пишу как услышала")
        return source
    return result
