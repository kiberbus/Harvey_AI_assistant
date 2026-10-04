"""
Локальная голосовая помощница «Харви» для Windows 11 на базе Ollama.

STT:  faster-whisper (локально)
TTS:  piper-tts или Silero (локально, выбирается в config.py)
Wake: непрерывное слушание, «Харви + команда» одной фразой.

Зависимости:
    pip install ollama sounddevice numpy faster-whisper piper-tts pycaw comtypes psutil screen-brightness-control
    (по желанию, для голоса Silero)  pip install silero torch

Файлы: config.py — все настройки, phrases.py — варианты фраз-команд, harvey.py — код.

Как устроена скорость:
    1. Простые команды (громкость, пауза, яркость, запись текста, открыть приложение)
       выполняются напрямую регулярками, без обращения к ИИ.
    2. К Ollama уходит только то, что не удалось разобрать локально, и ровно ОДИН запрос
       (ответ собирается кодом, а не вторым проходом модели).
    3. Частые фразы озвучиваются из кэша, модель Ollama прогревается на старте.
"""

from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # torch (Silero) + ctranslate2 (Whisper) в одном процессе

import ctypes
import difflib
import json
import logging
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import numpy as np
import ollama
import sounddevice as sd
try:
    from piper.voice import PiperVoice
except Exception:  # без piper можно работать на Silero
    PiperVoice = None

try:
    from piper import SynthesisConfig
except Exception:  # старая версия piper — скорость речи тогда регулируем частотой воспроизведения
    SynthesisConfig = None

try:
    import psutil
except Exception:  # psutil ставится вместе с pycaw
    psutil = None

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation

    HAS_PYCAW = True
except Exception:  # pycaw не установлен — громкость и приглушение будут недоступны
    HAS_PYCAW = False

# ───────────────────────── НАСТРОЙКИ И ФРАЗЫ ─────────────────────────
# Настройки лежат в config.py, варианты фраз-команд — в phrases.py
from config import *      # noqa: E402,F401,F403
from phrases import *     # noqa: E402,F401,F403

END = "а" if FEMALE_VOICE else ""
WOKE = "проснулась" if FEMALE_VOICE else "проснулся"
PIPER_DIR = BASE_DIR / "piper"
PIPER_MODEL = PIPER_DIR / f"ru_RU-{PIPER_VOICE}-medium.onnx"

_ADDRESS_RULE = (f'К пользователю обращайся "{HONORIFIC}".' if USE_HONORIFIC
                 else "К пользователю обращайся на «вы», без обращений вроде «господин».")
SYSTEM_PROMPT = f"""Ты — голосовой ассистент по имени {ASSISTANT_NAME}, управляющий компьютером с Windows 11.
Выполняй просьбы пользователя ТОЛЬКО через инструменты. {_ADDRESS_RULE}
- Если нужно несколько действий — вызови все инструменты сразу.
- YouTube-поиск: open_browser(site="youtube", query="...").
- Громкость в процентах — set_volume, "громче/тише" — change_volume.
- Пауза, продолжить, следующий или предыдущий трек — media. Если сказано, что именно (музыка, ютуб, видео), передай target.
- Яркость экрана — set_brightness / change_brightness.
- Закрыть приложение — close_app. Свернуть приложение — minimize_app (НЕ закрывай, если просят свернуть).
- Слова «его», «это», «то же» относятся к последнему упомянутому в диалоге.
- Название приложения передавай на английском, как в меню Пуск.
- Если инструмент не нужен, ответь одним-двумя короткими предложениями по-русски, без списков и разметки.
"""

# ───────────────────────── СЛУЖЕБНОЕ ─────────────────────────
OWN_PID = os.getpid()
FAIL = "!"                            # префикс фразы-ошибки внутри инструментов
PUNCT = " ,.!?:;-—…"

WAKE_PATTERN = re.compile(r"\b(" + "|".join(sorted(set(WAKE_WORDS))) + r")\b", re.IGNORECASE)


def _rx(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


R = {name: _rx(patterns) for name, patterns in PHRASES.items()}      # варианты фраз из phrases.py
NOTE_ADD_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in NOTE_ADD]
DICTATE_RE = re.compile(
    r"^(?:запиш\w*|записи|записать|напечатай|набери|введи)\b[\s,:—-]*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
VERB_RE = re.compile(r"(открой|запусти|закрой|закрыть|заверши|завершить)\b")
OPEN_VERBS = r"(?:открой|запусти|включи|зайди на|зайди в)"
SEARCH_VERB_RE = re.compile(r"\b(?:найди|поищи|ищи|покажи|напиши|набери|введи|загугли)\b")

SITE_PATTERNS = tuple((site, re.compile(p)) for site, p in SITE_ALIASES.items())
SITE_MENTION_RE = re.compile(rf"\b(?:(?:на|в|во)\s+)?(?:{YT}|{GOOGLE}|интернет\w*)(?=\s|$)")
# Короткие формы: «ютуб котики», «гугл погода в лондоне», «найди рецепт борща»
SHORT_SEARCH_RE = re.compile(rf"^(?:(?:на|в|во)\s+)?(?P<site>{YT}|{GOOGLE})[\s,:—-]+(?P<query>.+)$")
# «открой в браузере калькулятор матриц», «калькулятор матриц в браузере» — поиск в Google
IN_BROWSER_RE = re.compile(r"^(?:(?:открой|найди|поищи|покажи|набери)\s+)?(?:(?:в|во)\s+(?:браузере|интернете)[\s,:—-]+"
                           r"(?P<q1>.+)|(?P<q2>.+?)[\s,]+(?:в|во)\s+(?:браузере|интернете))$")
BARE_SEARCH_RE = re.compile(r"^(?:найди|поищи)\s+(?:(?:в|во)\s+интернете?\s+)?(?P<query>.+)$")
MEDIA_TARGET_RES = tuple((name, re.compile(p)) for name, p in MEDIA_TARGETS.items())
MEDIA_VERB_RES = tuple((name, re.compile(p)) for name, p in MEDIA_VERBS.items())
MEDIA_UNPAUSE_RE = re.compile(MEDIA_UNPAUSE)
MEDIA_FILLER_RE = re.compile(MEDIA_FILLER)
APP_VOLUME_UP_RE = re.compile(APP_VOLUME_UP)
APP_VOLUME_DOWN_RE = re.compile(APP_VOLUME_DOWN)
APP_VOLUME_FILLER_RE = re.compile(APP_VOLUME_FILLER)
REMIND_VERB_RE = re.compile(REMIND_VERB)
REMIND_LIST_RE = re.compile("|".join(REMIND_LIST))
REMIND_CANCEL_RE = re.compile("|".join(REMIND_CANCEL))
APP_ALIASES = set().union(*(names for names, _ in NAME_GROUPS))   # «телеграм» без «открой»

INFO = "~"                            # префикс «информационной» фразы: её произносим даже в кратком режиме
RAW = "="                             # префикс фразы, которую говорим как есть, без «Слушаюсь, господин»

# Прерывание речи: «стоп», «заткнись», «хватит» ...
SILENCE_RE = re.compile(
    r"^(?:(?:ну|так|всё|все|эй)\s+)*(?:заткнись|замолчи|помолчи|молчи|тихо|хватит|достаточно|довольно|"
    r"прекрати|перестань|хорош)(?:\s+(?:болтать|говорить|уже|пожалуйста))*$"
)
STOP_RE = re.compile(r"^(?:(?:ну|так|эй)\s+)*(?:стоп|стой)(?:\s+(?:стоп|уже|пожалуйста))*$")
REPEAT_RE = re.compile(r"^(?:повтори(?:\s+пожалуйста)?|что ты (?:сказала|сказал)|ещё раз|еще раз)$")
# Закрыть окно диалога: «всё», «спасибо», «отбой»
DIALOG_END_RE = re.compile(r"^(?:(?:всё|все|ладно|ну)\s+)*(?:всё|все|спасибо|отбой|пока|свободна|свободен)"
                           r"(?:\s+(?:спасибо|пока))*$")
ACTIVE_CLOSE_RE = re.compile(
    r"^(?:закрой|закрыть|заверши|завершить)\s+"
    r"(?:(?:это|эту|текущее|текущую|активное|активную|открытое|открытую|данное|данную)"
    r"(?:\s+(?:приложение|программу|окно))?|приложение|программу|окно)$"
)
SPLIT_RE = re.compile(r"\s+(?:и|а потом|потом|затем|а также)\s+")
HALLUCINATIONS = ("субтитр", "продолжение следует", "спасибо за просмотр", "редактор")
COMPLEX_MARKERS = (
    "поиск", "найди", "youtube", "ютуб", "гугл", "яндекс", "музык",
    "видео", "песн", "браузер", "сайт", "папк",
)

PRE_BUFFER_BLOCKS = int(PRE_BUFFER_SEC * SAMPLE_RATE / BLOCK_SIZE)
SILENCE_BLOCKS = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
MIN_SPEECH_BLOCKS = int(MIN_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
MAX_UTTERANCE_BLOCKS = int(MAX_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
COMMAND_WAIT_BLOCKS = int(COMMAND_TIMEOUT * SAMPLE_RATE / BLOCK_SIZE)

OPENERS = ("Слушаюсь, господин", "Хорошо, господин", "Да, господин")
PREWARM_PHRASES = (
    "Да, господин?",
    "Секунду, господин.",
    "Слушаюсь, господин, готово.",
    "Простите, господин, произошла ошибка.",
) if not QUIET_MODE else ("Произошла ошибка.", "Отключаюсь.")


_logger = logging.getLogger("harvey")


def setup_logging() -> None:
    """Пишет всё в harvey.log (с ротацией) — по нему удобно разбирать, что она не поняла."""
    if not LOG_ENABLED or _logger.handlers:
        return
    handler = RotatingFileHandler(LOG_FILE, maxBytes=int(LOG_MAX_MB * 1024 * 1024),
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(tag)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log(tag: str, message: str) -> None:
    print(f"[{tag}] {message}", flush=True)
    if _logger.handlers:
        _logger.info(message, extra={"tag": tag})


def compose(phrases: list[str]) -> str:
    """Собирает итоговую фразу из результатов инструментов без повторного обращения к ИИ."""
    for p in phrases:
        if p.startswith(RAW):                       # вопрос-подтверждение и т.п. — без «Слушаюсь, господин»
            return p[1:]
    failed = any(p.startswith(FAIL) for p in phrases)
    clean = [p.lstrip(FAIL + INFO) for p in phrases]
    if SHORT_REPLIES and not failed:
        clean = [p.lstrip(INFO) for p in phrases if p.startswith(INFO)]   # только то, что нужно услышать
        if not clean:
            return f"{random.choice(OPENERS)}."
    body = ", ".join(clean) or "готово"
    opener = "Простите, господин" if failed else random.choice(OPENERS)
    return f"{opener}, {body}."


def compose_quiet(phrases: list[str]) -> tuple[str | None, str | None]:
    """Тихий режим: (что сказать вслух или None, какой звук сыграть или None).
    Обычное действие — только звук «готово»; вслух — сведения, ошибки и вопросы."""
    for p in phrases:
        if p.startswith(RAW):
            return p[1:], None
    failed = [p[1:] for p in phrases if p.startswith(FAIL)]
    info = [p[1:] for p in phrases if p.startswith(INFO)]
    spoken = info + (failed if SPEAK_ERRORS else [])
    text = _cap(", ".join(spoken)) + "." if spoken else None
    sound = "error" if failed else None if info else "done"
    return text, sound


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


_HONORIFIC_RE = re.compile(r"\bгосподин\b", re.IGNORECASE)


def address(text: str) -> str:
    """Подставляет обращение из config.py (или убирает его): фразы в коде пишутся с «господин»."""
    if USE_HONORIFIC:
        if HONORIFIC == "господин":
            return text
        return _HONORIFIC_RE.sub(lambda m: _cap(HONORIFIC) if m.group()[0].isupper() else HONORIFIC, text)
    text = re.sub(r"^\s*господин\b[,!]?\s*", "", text, flags=re.IGNORECASE)    # «Господин, таймер …»
    text = re.sub(r",\s*господин\b", "", text, flags=re.IGNORECASE)            # «Да, господин?» → «Да?»
    text = _HONORIFIC_RE.sub("", text)
    return _cap(re.sub(r"\s{2,}", " ", text).strip())


def _clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(float(value)))))


# ───────────────────────── ЧИСЛА ПРОПИСЬЮ ─────────────────────────
_UNITS = {
    "ноль": 0, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}


def parse_number(text: str) -> int | None:
    """«50», «пятьдесят», «двадцать пять» → число. Нет числа → None."""
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total: int | None = None
    for word in re.findall(r"[а-яё]+", text):
        value = 100 if word == "сто" else _TENS.get(word, _UNITS.get(word))
        if value is not None:
            total = (total or 0) + value
    return total


# ───────────────────────── ЧИСЛА И ЛАТИНИЦА ДЛЯ ГОЛОСА (нужно Silero) ─────────────────────────
_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
_TENS_W = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]

_LATIN = {
    "youtube": "ютуб", "telegram": "телеграм", "discord": "дискорд", "steam": "стим", "spotify": "спотифай",
    "firefox": "фаерфокс", "chrome": "хром", "google": "гугл", "github": "гитхаб", "word": "ворд",
    "excel": "эксель", "obs": "обс", "notepad": "блокнот", "windows": "виндовс", "paint": "пейнт",
    "edge": "эдж", "yandex": "яндекс", "music": "музыка", "code": "код", "terminal": "терминал",
    "calculator": "калькулятор", "settings": "параметры", "explorer": "проводник", "desktop": "десктоп",
    "ollama": "олама", "claude": "клод", "cloud": "клауд", "whisper": "виспер", "python": "пайтон", "vscode": "ви эс код",
}
_LAT2CYR = dict(zip("abcdefghijklmnopqrstuvwxyz",
                    ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м",
                     "н", "о", "п", "к", "р", "с", "т", "у", "в", "в", "кс", "и", "з"]))
_NUM_RE = re.compile(r"(?<![\w.])(-?)(\d+)(?:[.,](\d+))?(\s+)?([A-Za-zА-Яа-яЁё]+)?")


def _gender(word: str, fem: bool) -> str:
    return {"один": "одна", "два": "две"}.get(word, word) if fem else word


def _below_thousand(n: int, fem: bool = False) -> list[str]:
    words: list[str] = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if rest < 20:
        if rest:
            words.append(_gender(_ONES[rest], fem))
    else:
        tens, ones = divmod(rest, 10)
        words.append(_TENS_W[tens])
        if ones:
            words.append(_gender(_ONES[ones], fem))
    return words


def int_to_words(n: int, fem: bool = False, acc: bool = False) -> str:
    """21 → «двадцать один»; fem — женский род («две минуты»); acc — «одну минуту»."""
    if n == 0:
        return "ноль"
    parts: list[str] = []
    for scale, forms in ((10 ** 9, ("миллиард", "миллиарда", "миллиардов")),
                         (10 ** 6, ("миллион", "миллиона", "миллионов")),
                         (1000, ("тысяча", "тысячи", "тысяч"))):
        q, n = divmod(n, scale)
        if q:
            parts += _below_thousand(q, fem=(scale == 1000)) + [_plural(q, *forms)]
    if n:
        parts += _below_thousand(n, fem)
    text = " ".join(parts)
    return re.sub(r"одна$", "одну", text) if acc else text


def _translit(word: str) -> str:
    w = word.lower()
    for a, b in (("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("ck", "к"),
                 ("ee", "и"), ("oo", "у"), ("zh", "ж")):
        w = w.replace(a, b)
    return "".join(_LAT2CYR.get(ch, ch) for ch in w)


def _num_repl(m: re.Match) -> str:
    neg, whole, frac, space, word = m.groups()
    if len(whole) > 11:
        return m.group(0)
    low = (word or "").lower()
    fem = low.startswith(("минут", "секунд", "недел"))
    out = ("минус " if neg else "") + int_to_words(int(whole), fem, acc=fem and low.endswith("у"))
    if frac:
        zeros = len(frac) - len(frac.lstrip("0"))
        out += " запятая " + "ноль " * zeros + (int_to_words(int(frac)) if frac.strip("0") else "")
    return out.rstrip() + (space or "") + (word or "")


def normalize_for_tts(text: str) -> str:
    """Silero не читает цифры и латиницу — превращаем их в русские слова."""
    text = _NUM_RE.sub(_num_repl, text)
    text = re.sub(r"[A-Za-z][A-Za-z']*", lambda m: _LATIN.get(m.group().lower()) or _translit(m.group()), text)
    return text.replace("%", " процентов")


# ───────────────────────── WINDOWS: КЛАВИАТУРА ─────────────────────────
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if _user32:
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT


def press_key(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def type_text(text: str) -> None:
    """Печатает текст в активное поле ввода (юникод напрямую, буфер обмена не трогаем)."""
    text = text.replace("\r", " ").replace("\n", " ")
    raw = text.encode("utf-16-le")
    codes = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    events = []
    for code in codes:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            ev = INPUT(type=INPUT_KEYBOARD)
            ev.ki = KEYBDINPUT(0, code, flags, 0, 0)
            events.append(ev)
    for i in range(0, len(events), 100):
        chunk = events[i:i + 100]
        sent = _user32.SendInput(len(chunk), (INPUT * len(chunk))(*chunk), ctypes.sizeof(INPUT))
        if sent != len(chunk):
            raise OSError(f"SendInput отправил {sent} из {len(chunk)} (код {ctypes.get_last_error()})")
        time.sleep(0.005)


# ───────────────────────── WINDOWS: БУФЕР ОБМЕНА И АКТИВНОЕ ОКНО ─────────────────────────
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_CONTROL, VK_V = 0x11, 0x56

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
if _user32:
    _kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)


def _open_clipboard() -> bool:
    for _ in range(10):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    data = (text + "\0").encode("utf-16-le")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = _kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _open_clipboard():
        raise OSError("не удалось открыть буфер обмена")
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise OSError(f"SetClipboardData (код {ctypes.get_last_error()})")
    finally:
        _user32.CloseClipboard()


def paste_text(text: str) -> None:
    """Вставляет текст через Ctrl+V и возвращает прежний текст буфера обмена."""
    previous = _get_clipboard_text()
    _set_clipboard_text(text)
    time.sleep(0.05)
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)                      # даём приложению успеть вставить
    if previous is not None:
        _set_clipboard_text(previous)


def foreground_title() -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(_user32.GetForegroundWindow(), buf, 256)
    return buf.value or "без названия"


# ───────────────────────── WINDOWS: ОКНА ─────────────────────────
GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 4, -20, 0x00000080
SW_SHOW, SW_RESTORE = 5, 9
VK_MENU = 0x12
VK_LWIN, VK_TAB, VK_D, VK_SNAPSHOT = 0x5B, 0x09, 0x44, 0x2C
WM_CLOSE = 0x0010
SW_MINIMIZE, SW_MAXIMIZE = 6, 3

if _user32:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
    _user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _user32.IsIconic.argtypes = (wintypes.HWND,)
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, wintypes.LPDWORD)
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongW.restype = wintypes.LONG
    _user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    _user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _windows_of(pids: set[int]) -> list[tuple[int, bool]]:
    """Окна верхнего уровня процессов: (hwnd, видимо ли). Видимые (в т.ч. свёрнутые) идут первыми."""
    found: list[tuple[int, bool]] = []

    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or _user32.GetWindow(hwnd, GW_OWNER):
            return True
        if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        visible = bool(_user32.IsWindowVisible(hwnd))
        if not visible:
            # скрытые окна (приложение в трее) берём, только если это настоящее окно, а не служебное
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left < 200 or rect.bottom - rect.top < 150:
                return True
        found.append((hwnd, visible))
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return sorted(found, key=lambda w: not w[1])


def _force_foreground(hwnd: int) -> None:
    """Windows не любит, когда фоновая программа забирает фокус, поэтому действуем в два приёма."""
    fg = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    this_thread = _kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != this_thread
                    and _user32.AttachThreadInput(this_thread, fg_thread, True))
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        if _user32.GetForegroundWindow() != hwnd:      # не вышло — «будим» систему нажатием Alt
            _user32.keybd_event(VK_MENU, 0, 0, 0)
            _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(this_thread, fg_thread, False)


def bring_to_front(exes: set[str]) -> str | None:
    """Если у приложения уже есть окно — разворачивает его и выводит вперёд.
    Возвращает "restored" (было свёрнуто/скрыто), "shown" (просто показано) или None (окна нет)."""
    if psutil is None or not exes:
        return None
    pids = {p.pid for p in _find_procs(exes, set())}
    if not pids:
        return None
    windows = _windows_of(pids)
    if not windows:
        return None
    hwnd, visible = windows[0]
    minimized = bool(_user32.IsIconic(hwnd))
    _user32.ShowWindow(hwnd, SW_RESTORE if minimized else SW_SHOW)
    _force_foreground(hwnd)
    return "restored" if (minimized or not visible) else "shown"


# ───────────────────────── WINDOWS: ГРОМКОСТЬ И ИСТОЧНИКИ ЗВУКА ─────────────────────────
def _endpoint_volume():
    if not HAS_PYCAW:
        raise RuntimeError("pycaw не установлен")
    device = AudioUtilities.GetSpeakers()
    endpoint = getattr(device, "EndpointVolume", None)  # новые версии pycaw
    if endpoint is not None:
        return endpoint
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def _audio_sessions() -> list:
    if not HAS_PYCAW:
        return []
    try:
        return list(AudioUtilities.GetAllSessions())
    except Exception:
        return []


def _session_peak(session) -> float:
    try:
        return float(session._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
    except Exception:
        return 0.0


def _session_name(session) -> str:
    try:
        return session.Process.name() if session.Process else "?"
    except Exception:
        return "?"


def _foreign(session) -> bool:
    """Источник, которым можно управлять: не мы сами, не системные звуки и не из NO_VOLUME_CONTROL."""
    if session.ProcessId in (0, OWN_PID):
        return False
    return _session_name(session).lower() not in NO_VOLUME_CONTROL


def active_audio_sessions() -> list:
    """Источники звука, которые сейчас активны (кроме самой помощницы)."""
    return [s for s in _audio_sessions() if _foreign(s) and (s.State == 1 or _session_peak(s) > 0.001)]


def audio_is_playing(window: float = 0.25) -> bool:
    """True, если какое-то приложение реально выдаёт звук (проверяем пиковый уровень)."""
    end = time.time() + window
    while True:
        if any(_foreign(s) and _session_peak(s) > 0.002 for s in _audio_sessions()):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


DUCK_STATE_FILE = BASE_DIR / ".duck_state.json"   # на случай, если программу закроют, пока музыка приглушена


class Ducker:
    """Приглушает фоновые источники звука на время речи Харви, затем возвращает громкость.
    Громкость запоминается по ПРИЛОЖЕНИЮ, а не по аудиосеансу: браузеры пересоздают сеансы
    (например, после паузы), и Windows выдаёт новому сеансу приглушённую громкость — раньше
    из-за этого музыка иногда так и оставалась тихой."""

    def __init__(self) -> None:
        self._saved: dict[str, float] = {}     # имя процесса → исходная громкость
        self._since = 0.0

    @property
    def active(self) -> bool:
        return bool(self._saved)

    @property
    def stale(self) -> bool:
        """Приглушено слишком долго — возвращаем громкость, что бы ни происходило."""
        return bool(self._saved) and time.time() - self._since > DUCK_MAX_SEC

    def duck(self) -> None:
        if self._saved or not HAS_PYCAW:
            return
        for s in active_audio_sessions():
            name = _session_name(s).lower()
            try:
                vol = s.SimpleAudioVolume
                original = self._saved.setdefault(name, vol.GetMasterVolume())
                vol.SetMasterVolume(max(0.0, original * DUCK_FACTOR), None)
                log("Звук", f"приглушён источник: {name}")
            except Exception:
                pass
        if self._saved:
            self._since = time.time()
            try:
                DUCK_STATE_FILE.write_text(json.dumps(self._saved), encoding="utf-8")
            except Exception:
                pass

    @staticmethod
    def _volumes_of(names) -> list[tuple[object, str]]:
        """Все ТЕКУЩИЕ сеансы этих приложений — включая пересозданные после приглушения."""
        result = []
        for s in _audio_sessions():
            name = _session_name(s).lower()
            if name in names:
                try:
                    result.append((s.SimpleAudioVolume, name))
                except Exception:
                    pass
        return result

    def restore(self) -> None:
        if not self._saved:
            return
        saved, self._saved = self._saved, {}
        volumes = self._volumes_of(saved)
        for step in range(1, FADE_STEPS + 1):
            k = step / FADE_STEPS
            for vol, name in volumes:
                try:
                    vol.SetMasterVolume(min(1.0, saved[name] * (DUCK_FACTOR + (1 - DUCK_FACTOR) * k)), None)
                except Exception:
                    pass
            time.sleep(0.04)
        DUCK_STATE_FILE.unlink(missing_ok=True)

    def set_app(self, name: str, session, value: float) -> None:
        """Новая громкость приложения. Если оно сейчас приглушено — меняем «исходную» громкость,
        иначе после речи Харви вернула бы старую."""
        value = max(0.0, min(1.0, value))
        if name in self._saved:
            self._saved[name] = value
            try:
                DUCK_STATE_FILE.write_text(json.dumps(self._saved), encoding="utf-8")
            except Exception:
                pass
            value *= DUCK_FACTOR
        session.SimpleAudioVolume.SetMasterVolume(value, None)

    def original(self, name: str, session) -> float:
        """Громкость приложения без учёта временного приглушения."""
        return self._saved.get(name, session.SimpleAudioVolume.GetMasterVolume())

    def recover(self) -> None:
        """При запуске: если в прошлый раз программа закрылась с приглушённой музыкой — возвращаем."""
        try:
            saved = json.loads(DUCK_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        for vol, name in self._volumes_of(saved):
            try:
                vol.SetMasterVolume(min(1.0, float(saved[name])), None)
                log("Звук", f"вернул{END} громкость после прошлого запуска: {name}")
            except Exception:
                pass
        DUCK_STATE_FILE.unlink(missing_ok=True)


_ducker = Ducker()


# ───────────────────────── ИНСТРУМЕНТЫ: ЗВУК / МЕДИА / ЯРКОСТЬ ─────────────────────────
def set_volume(level: int) -> str:
    level = _clamp(level)
    ev = _endpoint_volume()
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(level / 100, None)
    return f"установил{END} громкость на {level} процентов"


def change_volume(delta: int) -> str:
    current = round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)
    new = _clamp(current + int(float(delta)))
    set_volume(new)
    return f"{'прибавил' if delta > 0 else 'убавил'}{END} громкость до {new} процентов"


def mute(state: bool = True) -> str:
    _endpoint_volume().SetMute(1 if state else 0, None)
    return f"{'выключил' if state else 'включил'}{END} звук"


# ───────────────────────── МЕДИА: КОНКРЕТНЫЕ ПЛЕЕРЫ (Windows SMTC) ─────────────────────────
# Через системные медиасеансы (виджет с плеером рядом с громкостью) видно каждый плеер отдельно:
# приложение, название, исполнителя, играет он или на паузе. Так «музыка стоп» ставит на паузу
# именно музыку, а результат проверяется. Без пакета winrt — старые медиа-клавиши.
try:
    import asyncio

    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SMTCManager,
    )

    HAS_SMTC = True
except Exception:
    HAS_SMTC = False

_PLAYING, _PAUSED = 4, 5
_MUSIC_SITES_RE = re.compile(MUSIC_SITES, re.IGNORECASE)
_TARGET_NAMES = {"music": "музыка", "youtube": "ютуб", "video": "видео"}
_paused_by_me: list[tuple[str, str]] = []     # (приложение, название) — что Харви поставила на паузу последним


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _window_titles(browsers_only: bool = False) -> list[str]:
    """Заголовки видимых окон: по ним видно сайт («… - YouTube — Mozilla Firefox»).
    browsers_only — только окна браузеров (окно приложения «Яндекс Музыка» — не вкладка браузера)."""
    titles: list[str] = []
    buf = ctypes.create_unicode_buffer(512)
    browser_pids = ({p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in BROWSER_EXES}
                    if browsers_only and psutil else None)

    def callback(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextW(hwnd, buf, 512):
            if browser_pids is not None:
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in browser_pids:
                    return True
            titles.append(buf.value)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return titles


async def _smtc_snapshot() -> tuple[list[dict], str | None]:
    manager = await _SMTCManager.request_async()
    sessions: list[dict] = []
    for session in manager.get_sessions():
        try:
            props = await session.try_get_media_properties_async()
            title, artist, album = props.title or "", props.artist or "", props.album_title or ""
        except Exception:
            title = artist = album = ""
        sessions.append({
            "session": session, "app": session.source_app_user_model_id or "",
            "title": title, "artist": artist, "album": album,
            "status": int(session.get_playback_info().playback_status),
        })
    current = manager.get_current_session()
    return sessions, (current.source_app_user_model_id if current else None)


_kind_memory: dict[tuple[str, str], set[str]] = {}   # что уже удалось определить точно (по заголовку окна)


def _site_kinds(title: str) -> set[str]:
    """На какой сайт указывает заголовок окна."""
    kinds: set[str] = set()
    if "youtube music" in title:
        kinds |= {"youtube", "music"}
    elif "youtube" in title:
        kinds |= {"youtube", "video"}
    if _MUSIC_SITES_RE.search(title):
        kinds.add("music")
    return kinds


def _session_kinds(info: dict, titles: list[str]) -> set[str]:
    """Что это за плеер: music / youtube / video (может быть несколько сразу).
    Firefox отдаёт Windows ОДИН плеер на весь браузер — последнюю вкладку со звуком, а заголовок
    окна показывает только активную вкладку. Поэтому, если трека нет ни в одном заголовке,
    смотрим, какие сайты вообще открыты: открыта Яндекс Музыка — это может быть музыка."""
    app, title = info["app"].lower(), _norm(info["title"])
    kinds: set[str] = set()
    if any(a in app for a in MUSIC_APPS) or info["album"]:
        kinds.add("music")
    if any(a in app for a in VIDEO_APPS):
        kinds.add("video")
    if kinds:
        return kinds
    key = (info["app"], title)
    page = next((_norm(t) for t in titles if title and title in _norm(t)), "")   # окно с этим роликом/треком
    if page:
        _kind_memory[key] = _site_kinds(page) or {"video"}
        return _kind_memory[key]
    if key in _kind_memory:
        return _kind_memory[key]
    # Звук из браузера без музыкального сайта — почти всегда YouTube или другое видео
    return set().union(*(_site_kinds(_norm(t)) for t in _window_titles(browsers_only=True))) or {"video", "youtube"}


def _label(info: dict) -> str:
    return f"«{info['title']}»" if info["title"] else "воспроизведение"


def _is_browser_session(info: dict) -> bool:
    app = info["app"].lower()
    return not any(a in app for a in (*MUSIC_APPS, *VIDEO_APPS))


def _browser_silent() -> bool:
    """Браузер сейчас не выдаёт звук (по пиковому уровню его аудиосеансов)."""
    peaks = [_session_peak(s) for s in _audio_sessions() if _session_name(s).lower() in BROWSER_EXES]
    return bool(peaks) and max(peaks) < 0.001


async def _wait_status(infos: list[dict], wanted: int, timeout: float = 4.0) -> list[dict]:
    """Ждёт, пока плееры реально сменят состояние. Возвращает тех, кто так и не сменил.
    Firefox сообщает о паузе с задержкой (на YouTube — бывает дольше 2.5 с), поэтому:
    статус каждый раз читаем заново, а для браузера паузой считаем и честную тишину ~0.3 с."""
    started = time.time()
    manager = await _SMTCManager.request_async()
    pending = list(infos)
    quiet = 0
    while pending and time.time() - started < timeout:
        await asyncio.sleep(0.1)
        fresh = {sess.source_app_user_model_id: sess for sess in manager.get_sessions()}
        quiet = quiet + 1 if wanted == _PAUSED and _browser_silent() else 0
        still = []
        for info in pending:
            session = fresh.get(info["app"], info["session"])
            if int(session.get_playback_info().playback_status) == wanted:
                continue
            if quiet >= 3 and _is_browser_session(info):
                continue
            still.append(info)
        pending = still
    log("Медиа", f"{'подтверждено' if not pending else 'не подтвердилось'} за {time.time() - started:.1f} с")
    return pending


async def _smtc_media(action: str, target: str | None) -> str | None:
    """Управляет нужным плеером. None — сеансов нет (пусть сработает запасной способ)."""
    global _paused_by_me
    sessions, current_app = await _smtc_snapshot()
    if not sessions:
        return None
    titles = _window_titles()
    for info in sessions:
        info["kinds"] = _session_kinds(info, titles)
    log("Медиа", f"{action} {target or '—'}: " + "; ".join(
        f"«{i['title'][:40]}» {'/'.join(sorted(i['kinds']))} {'играет' if i['status'] == _PLAYING else i['status']}"
        for i in sessions))
    matching = [i for i in sessions if target is None or target in i["kinds"]]
    mine = lambda i: (i["app"], i["title"]) in _paused_by_me
    is_current = lambda i: i["app"] == current_app
    what = _TARGET_NAMES.get(target, "")

    if action == "pause":
        playing = [i for i in sessions if i["status"] == _PLAYING]
        chosen = [i for i in playing if i in matching]
        if not chosen and target:   # цель не узнана — берём только «неясные» плееры браузера, музыкальное приложение не трогаем
            chosen = [i for i in playing if _is_browser_session(i)] if target != "music" else playing
        elif not chosen:
            chosen = playing
        if not chosen:
            return f"{INFO}сейчас ничего не играет"
        for info in chosen:
            await info["session"].try_pause_async()
        failed = await _wait_status(chosen, _PAUSED)
        done = [i for i in chosen if i not in failed]
        _paused_by_me = [(i["app"], i["title"]) for i in done] or _paused_by_me
        if failed:
            return f"{FAIL}не получилось поставить на паузу {_label(failed[0])}"
        return f"поставил{END} на паузу " + ", ".join(_label(i) for i in done)

    if action == "play":
        if any(i["status"] == _PLAYING for i in matching):
            return f"{INFO}{what} уже играет" if what else f"{INFO}воспроизведение уже идёт"
        paused = [i for i in sessions if i["status"] == _PAUSED]
        candidates = ([i for i in paused if i in matching and mine(i)]
                      or [i for i in paused if i in matching and is_current(i)]
                      or [i for i in paused if i in matching]
                      or [i for i in paused if mine(i)])          # «включи музыку» после «музыка стоп» на ролике
        if not candidates:
            return None if target is None else ""
        info = candidates[0]
        await info["session"].try_play_async()
        if await _wait_status([info], _PLAYING):
            return f"{FAIL}не получилось включить {_label(info)}"
        return f"включил{END} {_label(info)}"

    # next / previous — у того, что играет (или у текущего плеера)
    playing = [i for i in matching if i["status"] == _PLAYING]
    info = (playing or [i for i in matching if is_current(i)] or matching or [None])[0]
    if info is None:
        return f"{INFO}не нашл{'а' if FEMALE_VOICE else 'ёл'}, что переключить"
    old_title = info["title"]
    ok = await (info["session"].try_skip_next_async() if action == "next"
                else info["session"].try_skip_previous_async())
    if not ok:
        return f"{FAIL}плеер не дал переключить {_label(info)}"
    return f"переключил{END} {'на следующий' if action == 'next' else 'на предыдущий'} трек" + (
        f", было {_label(info)}" if old_title else "")


async def _now_playing() -> str:
    sessions, _ = await _smtc_snapshot()
    playing = [i for i in sessions if i["status"] == _PLAYING]
    shown = playing or [i for i in sessions if i["status"] == _PAUSED]
    if not shown:
        return f"{INFO}сейчас ничего не играет"
    titles = _window_titles()
    parts = []
    for info in shown[:2]:
        title, artist = " ".join(info["title"].split()), " ".join(info["artist"].split())
        kinds = _session_kinds(info, titles)
        who = "канал" if "youtube" in kinds and "music" not in kinds else "исполнитель"
        parts.append(f"«{title}»" + (f", {who} {artist}" if artist else ""))
    return f"{INFO}{'сейчас играет' if playing else 'на паузе'} " + "; ".join(parts)


def now_playing() -> str:
    """«Что играет?» — название и исполнитель (или канал на YouTube)."""
    if not HAS_SMTC:
        return f"{FAIL}не вижу плееры, не установлен пакет winrt"
    return asyncio.run(_now_playing())


def _media_keys(action: str) -> str:
    """Запасной способ: системные медиа-клавиши (без проверки, какой плеер их получит)."""
    if action == "next":
        press_key(VK_MEDIA_NEXT)
        return f"переключил{END} на следующий трек"
    if action == "previous":
        press_key(VK_MEDIA_PREV)
        return f"вернул{END} предыдущий трек"
    if action == "pause":
        if HAS_PYCAW and not audio_is_playing():
            return f"{INFO}сейчас ничего не играет"
        press_key(VK_MEDIA_PLAY_PAUSE)
        return f"поставил{END} воспроизведение на паузу"
    if action == "play":
        if HAS_PYCAW and audio_is_playing():
            return f"{INFO}воспроизведение уже идёт"
        press_key(VK_MEDIA_PLAY_PAUSE)
        return f"возобновил{END} воспроизведение"
    return f"{FAIL}неизвестное действие {action}"


def _autoplay_when_ready(target: str, timeout: float = 20.0) -> None:
    """После запуска плеера ждём, пока он появится в Windows, и нажимаем «играть»
    (Яндекс Музыка при запуске восстанавливает последний трек, но сама не играет)."""
    async def run() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            sessions, _ = await _smtc_snapshot()
            titles = _window_titles()
            for info in sessions:
                if target in _session_kinds(info, titles) and any(a in info["app"].lower() for a in MUSIC_APPS):
                    if info["status"] != _PLAYING:
                        await info["session"].try_play_async()
                        log("Медиа", f"автозапуск после открытия: «{info['title']}»")
                    return
        log("Медиа", "плеер открылся, но так и не появился в Windows — включать нечего")

    try:
        asyncio.run(run())
    except Exception as e:
        log("Медиа", f"автозапуск не удался: {e}")


def media(action: str, target: str | None = None) -> str:
    """Пауза / продолжить / следующий / предыдущий — у нужного плеера.
    target: "music", "youtube", "video" или None (что угодно)."""
    action = (action or "").strip().lower()
    target = (target or "").strip().lower() or None
    if target not in (None, *_TARGET_NAMES):
        target = None
    result = None
    if HAS_SMTC:
        try:
            result = asyncio.run(_smtc_media(action, target))
        except Exception as e:
            log("Медиа", f"SMTC не сработал, использую медиа-клавиши: {e}")
    if result == "":                 # «включи музыку», а включать нечего — открываем приложение / сайт из config.py
        app = MEDIA_FALLBACK_APP.get(target)
        if app:
            opened = open_app(app)
            if not opened.startswith(FAIL):
                threading.Thread(target=_autoplay_when_ready, args=(target,), daemon=True).start()
            return opened
        if any(target in _site_kinds(_norm(t)) for t in _window_titles()):
            return f"{INFO}вкладка уже открыта, но включать там нечего, запустите трек один раз вручную"
        site = MEDIA_FALLBACK_SITE.get(target)
        if site:
            return open_browser(site=site)
        return f"{INFO}нечего включать, {_TARGET_NAMES[target]} не открыто"
    return result if result is not None else _media_keys(action)


def set_brightness(level: int) -> str:
    import screen_brightness_control as sbc

    level = _clamp(level)
    sbc.set_brightness(level)
    return f"установил{END} яркость на {level} процентов"


def change_brightness(delta: int) -> str:
    import screen_brightness_control as sbc

    current = sbc.get_brightness()
    current = current[0] if isinstance(current, list) else current
    new = _clamp(current + int(float(delta)))
    sbc.set_brightness(new)
    return f"{'повысил' if delta > 0 else 'понизил'}{END} яркость до {new} процентов"


def dictate(text: str) -> str:
    """Записывает текст в активное окно. Если выбранный способ не сработал — пробует второй."""
    text = text.strip()
    text = text[:1].upper() + text[1:]
    log("Запись", f"в окно «{foreground_title()}»: {text}")
    time.sleep(0.15)
    methods = [paste_text, type_text] if DICTATION_MODE == "paste" else [type_text, paste_text]
    error: Exception | None = None
    for method in methods:
        try:
            method(text)
            return f"записал{END} текст"
        except Exception as e:
            error = e
            log("Запись", f"{method.__name__} не сработал: {e}")
    raise error  # type: ignore[misc]


# ───────────────────────── ПОИСК ПРИЛОЖЕНИЙ ─────────────────────────
_apps_cache: list[dict] | None = None


def _load_start_apps() -> list[dict]:
    global _apps_cache
    if _apps_cache is not None:
        return _apps_cache
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
    ]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    data = json.loads(out) if out else []
    if isinstance(data, dict):
        data = [data]
    _apps_cache = [a for a in data if a.get("Name") and a.get("AppID")]
    return _apps_cache


def _candidates(query: str) -> set[str]:
    q = query.strip().lower()
    result = {q}
    for group, _ in NAME_GROUPS:
        if q in group:
            result |= group
    return result


def find_app(query: str) -> dict | None:
    apps = _load_start_apps()
    names = {a["Name"].lower(): a for a in apps}
    cands = _candidates(query)
    for c in cands:
        if c in names:
            return names[c]
    hits = [n for n in names if any(c in n for c in cands)]
    if hits:
        return names[min(hits, key=len)]
    for c in cands:
        close = difflib.get_close_matches(c, list(names), n=1, cutoff=0.75)
        if close:
            return names[close[0]]
    return None


# ───────────────────────── ИНСТРУМЕНТЫ: ПРИЛОЖЕНИЯ / БРАУЗЕР / ПАПКИ ─────────────────────────
def default_browser_exe() -> str | None:
    if BROWSER_EXE:
        return BROWSER_EXE
    try:
        import winreg

        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as k:
            command = winreg.QueryValueEx(k, "")[0]
        m = re.match(r'\s*"([^"]+)"', command) or re.match(r"\s*(\S+)", command)
        return m.group(1) if m else None
    except Exception:
        return None


def open_app(name: str) -> str:
    app = find_app(name)
    if not app:
        return f"{FAIL}приложение «{name}» не найдено"

    # Уже запущено — не открываем второй раз, а разворачиваем и показываем окно
    try:
        state = bring_to_front(_target_exes(name))
    except Exception as e:
        log("Окно", f"не удалось развернуть: {e}")
        state = None
    if state == "restored":
        return f"развернул{END} {app['Name']}"
    if state == "shown":
        return f"показал{END} {app['Name']}"

    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["AppID"]])
    return f"открыл{END} {app['Name']}"


def open_browser(site: str = "", query: str = "") -> str:
    site = (site or "").strip().lower()
    query = (query or "").strip()
    url = None
    if site:
        if site not in SITES:
            return f"{FAIL}не знаю сайт «{site}»"
        url = SITES[site]
        if site == "youtube" and query:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
        elif site == "google" and query:
            url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    elif query:
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query)

    exe = default_browser_exe()
    if not url and exe:
        try:
            state = bring_to_front({Path(exe).name.lower()})
        except Exception:
            state = None
        if state:
            return f"{'развернул' if state == 'restored' else 'показал'}{END} браузер"
    if exe and os.path.exists(exe):
        subprocess.Popen([exe, url] if url else [exe])
    elif url:
        import webbrowser

        webbrowser.open(url)
    else:
        return f"{FAIL}не удалось определить браузер"

    detail = f"{site} и нашёл{END} «{query}»" if (site and query) else (site or query or "браузер")
    return f"открыл{END} {detail}"


def open_folder(name: str) -> str:
    path = FOLDERS.get((name or "").strip().lower())
    if not path:
        return f"{FAIL}не знаю папку «{name}»"
    os.startfile(path)
    return f"открыл{END} папку {name}"


def _protected() -> tuple[set[int], set[str]]:
    """PID и имена процессов, которые закрывать нельзя: сама помощница и окно, откуда она запущена."""
    pids, exes = {OWN_PID}, set()
    try:
        me = psutil.Process(OWN_PID)
        exes.add(me.name().lower())
        for parent in me.parents():
            pids.add(parent.pid)
            exes.add(parent.name().lower())
    except Exception:
        pass
    return pids, exes


def _target_exes(query: str) -> set[str]:
    q = query.strip().lower()
    if q in ("браузер", "browser"):
        exe = default_browser_exe()
        return {Path(exe).name.lower()} if exe else set()
    exes: set[str] = set()
    for names, procs in NAME_GROUPS:
        if q in names:
            exes |= {p.lower() for p in procs}
    if exes:
        return exes

    # Общий случай: сверяем имя из меню Пуск / сказанное слово с именами запущенных процессов
    norm = lambda x: x.lower().replace(" ", "").removesuffix(".exe")
    stems = {norm(q)}
    app = find_app(q)
    if app:
        stems.add(norm(app["Name"]))
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        proc = norm(name)
        if len(proc) >= 4 and any(len(st) >= 3 and (st in proc or proc in st) for st in stems):
            exes.add(name)
    return exes


def _find_procs(exes: set[str], skip_pids: set[int]) -> list:
    return [p for p in psutil.process_iter(["pid", "name"])
            if (p.info.get("name") or "").lower() in exes and p.info["pid"] not in skip_pids]


def _taskkill(pids: list[int], force: bool = False, wait: bool = True) -> None:
    if not pids:
        return
    cmd = ["taskkill"]
    for pid in pids:
        cmd += ["/PID", str(pid)]
    if force:
        cmd.append("/F")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if wait:
        subprocess.run(cmd, capture_output=True, creationflags=flags)
    else:                                   # запуск taskkill занимает ~0.1 с — не ждём его
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


CLOSE_QUICK_WAIT = 0.3     # столько ждём честного закрытия, прежде чем ответить «готово»
CLOSE_GRACE = 1.5          # столько даём приложению закрыться самому, прежде чем завершить принудительно


def _report_late_failure(text: str) -> None:
    """Ошибка, выяснившаяся уже после ответа «готово» (закрытие доделывалось в фоне)."""
    log("Ошибка", text)
    if QUIET_MODE:
        play_sound("error")
        if SPEAK_ERRORS:
            speak(_cap(text) + ".")
    else:
        speak(f"Простите, господин, {text}.")


def _finish_close(alive: list, targets: set[str], label: str) -> None:
    """Доделывает закрытие в фоне: Telegram, Discord и т.п. на просьбу закрыться уходят в трей."""
    _, alive = psutil.wait_procs(alive, timeout=CLOSE_GRACE - CLOSE_QUICK_WAIT)
    if not alive:
        return
    if targets & DOCUMENT_EXES:
        _report_late_failure(f"«{label}» не закрылось, возможно, просит сохранить файл")
        return
    _taskkill([p.pid for p in alive], force=True)
    _, alive = psutil.wait_procs(alive, timeout=1.0)
    if alive:
        _report_late_failure(f"«{label}» не удалось закрыть")


def _close_exes(targets: set[str], label: str) -> str:
    """Сначала вежливо просит окно закрыться, и только если не вышло — завершает принудительно.
    Для редакторов и офиса принудительно не завершает, чтобы не потерять несохранённое.
    Отвечает сразу; если приложение упирается, добивает его в фоне и сообщает только о неудаче."""
    skip_pids, ancestor_exes = _protected()
    if targets & (PROTECTED_EXES | ancestor_exes):
        return f"{FAIL}«{label}» закрывать нельзя"
    procs = _find_procs(targets, skip_pids)
    if not procs:
        return f"{FAIL}«{label}» не запущено"

    _taskkill([p.pid for p in procs], wait=False)
    _, alive = psutil.wait_procs(procs, timeout=CLOSE_QUICK_WAIT)    # возвращается, как только процессы вышли
    if alive:
        threading.Thread(target=_finish_close, args=(alive, targets, label), daemon=True).start()
    return f"закрыл{END} {label}"


def _app_exes(name: str) -> set[str]:
    """Процессы приложения; «ютуб», «видео» — это браузер, «музыка» — музыкальный плеер."""
    low = (name or "").strip().lower()
    for target, rx in MEDIA_TARGET_RES:
        if rx.fullmatch(low):
            if target == "music":
                running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
                return {n for n in running if any(a in n for a in MUSIC_APPS)}
            return _target_exes("браузер")
    return _target_exes(low)


def minimize_app(name: str) -> str:
    """Свернуть окна приложения (НЕ закрывать)."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    pids = {p.pid for p in _find_procs(_app_exes(name), set())}
    if not pids:
        return f"{FAIL}«{name}» не запущено"
    windows = [hwnd for hwnd, visible in _windows_of(pids) if visible and not _user32.IsIconic(hwnd)]
    if not windows:
        return f"{INFO}«{name}» уже свёрнуто"
    for hwnd in windows:
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
    return f"свернул{END} {name}"


def close_app(name: str) -> str:
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    return _close_exes(_target_exes(name), name)


def close_active(window_only: bool = False) -> str:
    """Закрывает то, что сейчас на переднем плане: окно (WM_CLOSE) или приложение целиком."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return f"{FAIL}нет активного окна"
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = psutil.Process(pid.value).name().lower()
    skip_pids, ancestor_exes = _protected()
    if exe in PROTECTED_EXES:
        return f"{FAIL}активное окно — системное, его закрывать нельзя"
    if pid.value in skip_pids or exe in ancestor_exes:
        return f"{FAIL}это окно, из которого запущена я, закрывать нельзя"
    title = foreground_title()
    if window_only or exe == "applicationframehost.exe":    # у приложений из магазина PID общий
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return f"закрыл{END} окно «{title}»"
    return _close_exes({exe}, title)


def _chord(*vks: int) -> None:
    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def window_state(action: str) -> str:
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return f"{FAIL}нет активного окна"
    if action == "minimize":
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
        return f"свернул{END} окно"
    _user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return f"развернул{END} окно на весь экран"


def show_desktop() -> str:
    _chord(VK_LWIN, VK_D)
    return f"свернул{END} все окна"


def alt_tab() -> str:
    _chord(VK_MENU, VK_TAB)
    return f"переключил{END} окно"


def screenshot() -> str:
    _chord(VK_LWIN, VK_SNAPSHOT)          # сохраняется в Изображения\Снимки экрана
    return f"сделал{END} снимок экрана"


def lock_pc() -> str:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    return f"заблокировал{END} компьютер"


# ───────────────────────── ВРЕМЯ, ДАТА, ТАЙМЕРЫ ─────────────────────────
_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")
_timers: list[threading.Timer] = []


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def tell_time() -> str:
    now = datetime.now()
    if now.minute == 0:
        return f"{INFO}сейчас ровно {now.hour} {_plural(now.hour, 'час', 'часа', 'часов')}"
    return (f"{INFO}сейчас {now.hour} {_plural(now.hour, 'час', 'часа', 'часов')} "
            f"{now.minute} {_plural(now.minute, 'минута', 'минуты', 'минут')}")


_DAY_ORD = (
    "первое", "второе", "третье", "четвёртое", "пятое", "шестое", "седьмое", "восьмое", "девятое", "десятое",
    "одиннадцатое", "двенадцатое", "тринадцатое", "четырнадцатое", "пятнадцатое", "шестнадцатое",
    "семнадцатое", "восемнадцатое", "девятнадцатое", "двадцатое", "двадцать первое", "двадцать второе",
    "двадцать третье", "двадцать четвёртое", "двадцать пятое", "двадцать шестое", "двадцать седьмое",
    "двадцать восьмое", "двадцать девятое", "тридцатое", "тридцать первое",
)


def _date_words(now: datetime) -> str:
    return f"{_DAY_ORD[now.day - 1]} {_MONTHS[now.month - 1]}"


def tell_date() -> str:
    return f"{INFO}сегодня {_date_words(datetime.now())}"


def tell_weekday() -> str:
    return f"{INFO}сегодня {_WEEKDAYS[datetime.now().weekday()]}"


def tell_datefull() -> str:
    now = datetime.now()
    return f"{INFO}сегодня {_WEEKDAYS[now.weekday()]}, {_date_words(now)}"


def _duration_text(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, sec = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} {_plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {_plural(m, 'минуту', 'минуты', 'минут')}")
    if sec:
        parts.append(f"{sec} {_plural(sec, 'секунду', 'секунды', 'секунд')}")
    return " ".join(parts)


def parse_duration(text: str) -> int | None:
    """«5 минут», «пять минут», «2 часа 30 минут», «полчаса» → секунды."""
    if re.search(r"пол\s?часа", text):
        return 1800
    factor = lambda unit: 1 if unit.startswith("сек") else 60 if unit.startswith("мин") else 3600
    pairs = re.findall(r"(\d+)\s*(секунд\w*|сек\b|минут\w*|мин\b|час\w*)", text)
    if pairs:
        seconds = sum(int(n) * factor(u) for n, u in pairs)
    else:
        unit = re.search(r"секунд\w*|\bсек\b|минут\w*|\bмин\b|\bчас\w*", text)
        if not unit:
            return None
        seconds = (parse_number(text) or 1) * factor(unit.group())
    return seconds if 1 <= seconds <= 86400 else None


def set_timer(seconds: int) -> str:
    seconds = int(seconds)
    label = _duration_text(seconds)

    def ring() -> None:
        play_sound("ready")
        speak(f"Господин, таймер на {label} сработал.")

    timer = threading.Timer(seconds, ring)
    timer.daemon = True
    timer.start()
    _timers.append(timer)
    return f"{INFO}таймер на {label}"


def cancel_timers() -> str:
    active = [t for t in _timers if t.is_alive()]
    for t in active:
        t.cancel()
    _timers.clear()
    return f"отменил{END} таймеры" if active else f"{INFO}активных таймеров нет"


# ───────────────────────── РЕЖИМ СНА, ПОДТВЕРЖДЕНИЯ, ПИТАНИЕ ─────────────────────────
_sleeping = False                  # в режиме сна слушаем только «Харви, проснись»
_pending: dict | None = None       # ожидающее подтверждения действие: {"action": callable, "deadline": float}


def set_sleeping(value: bool) -> None:
    global _sleeping
    _sleeping = value


def clear_pending() -> None:
    global _pending
    _pending = None


def sleep_mode() -> str:
    set_sleeping(True)
    return f"{INFO}перехожу в режим сна, чтобы разбудить, скажите: {ASSISTANT_NAME}, проснись"


_POWER_LABELS = {
    "shutdown": "выключить компьютер",
    "restart": "перезагрузить компьютер",
    "sleep": "перевести компьютер в спящий режим",
}


def do_power(action: str) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", str(POWER_DELAY_SEC)], creationflags=flags)
        return (f"{INFO}выключу компьютер через {POWER_DELAY_SEC} секунд, "
                f"чтобы остановить, скажите: отмени выключение")
    if action == "restart":
        subprocess.run(["shutdown", "/r", "/t", str(POWER_DELAY_SEC)], creationflags=flags)
        return (f"{INFO}перезагружу компьютер через {POWER_DELAY_SEC} секунд, "
                f"чтобы остановить, скажите: отмени перезагрузку")
    if action == "sleep":
        # Если в системе включена гибернация, Windows уйдёт именно в неё — это поведение самой системы
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=flags)
        return f"{INFO}перевожу компьютер в спящий режим"
    return f"{FAIL}неизвестное действие {action}"


def request_power(action: str) -> str:
    """Выключение/перезагрузка/сон — только после «да» (если CONFIRM_DANGEROUS включён)."""
    global _pending
    if not CONFIRM_DANGEROUS:
        return do_power(action)
    _pending = {
        "action": lambda: execute_tool("do_power", {"action": action}),
        "deadline": time.time() + CONFIRM_TIMEOUT,
    }
    return f"{RAW}Вы уверены, господин, что нужно {_POWER_LABELS[action]}? Скажите «да» или «нет»."


def cancel_power() -> str:
    result = subprocess.run(["shutdown", "/a"], capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        return f"{INFO}сейчас ничего не запланировано"
    return f"отменил{END} выключение"


# ───────────────────────── ЗАМЕТКИ ─────────────────────────
def add_note(text: str) -> str:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {text.strip()}\n")
    return f"записал{END} в заметки"


def read_notes(count: int = 3) -> str:
    try:
        lines = [ln.strip() for ln in NOTES_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except FileNotFoundError:
        lines = []
    if not lines:
        return f"{INFO}заметок пока нет"
    last = [re.sub(r"^\[[^\]]*\]\s*", "", ln) for ln in lines[-count:]]
    return f"{INFO}последние заметки: " + "; ".join(last)


def open_notes() -> str:
    NOTES_FILE.touch(exist_ok=True)
    os.startfile(str(NOTES_FILE))
    return f"открыл{END} заметки"


# ───────────────────────── ПОГОДА И КУРС ВАЛЮТ (без ИИ, нужен интернет) ─────────────────────────
def _http_json(url: str, timeout: float = 6.0) -> dict:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Harvey-assistant/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


_geo_cache: dict[str, tuple[float, float]] = {}
_WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман",
    48: "изморозь", 51: "лёгкая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось",
    57: "ледяная морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
    67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "небольшие ливни", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
}


def _geocode(city: str) -> tuple[float, float]:
    if city not in _geo_cache:
        query = urllib.parse.urlencode({"name": city, "count": 1, "language": "ru"})
        found = _http_json("https://geocoding-api.open-meteo.com/v1/search?" + query)["results"][0]
        _geo_cache[city] = (found["latitude"], found["longitude"])
    return _geo_cache[city]


def _temp(n: int) -> str:
    return f"{'минус ' if n < 0 else ''}{abs(n)} {_plural(abs(n), 'градус', 'градуса', 'градусов')}"


def weather() -> str:
    try:
        lat, lon = _geocode(WEATHER_CITY)
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        })
        data = _http_json("https://api.open-meteo.com/v1/forecast?" + query)
        cur, day = data["current"], data["daily"]
    except Exception as e:
        log("Погода", f"ошибка: {e}")
        return f"{FAIL}не удалось узнать погоду, проверьте интернет"
    temp, feels = round(cur["temperature_2m"]), round(cur["apparent_temperature"])
    low, high = round(day["temperature_2m_min"][0]), round(day["temperature_2m_max"][0])
    wind = round(cur["wind_speed_10m"])
    text = (f"сейчас {_temp(temp)}, {_WMO.get(cur['weather_code'], 'без осадков')}, "
            f"ощущается как {_temp(feels)}. Сегодня минимум {_temp(low)}, максимум {_temp(high)}, "
            f"ветер {wind} {_plural(wind, 'метр', 'метра', 'метров')} в секунду")
    rain = (day.get("precipitation_probability_max") or [0])[0] or 0
    if rain >= 30:
        text += f", вероятность осадков {rain} {_plural(rain, 'процент', 'процента', 'процентов')}"
    return f"{INFO}{text}"


_rates_cache: dict = {"ts": 0.0, "rates": {}}


def _rates() -> dict:
    if not _rates_cache["rates"] or time.time() - _rates_cache["ts"] > 3600:
        # open.er-api.com: бесплатно, без ключа, обновляется раз в сутки (нужна ссылка на exchangerate-api.com)
        data = _http_json("https://open.er-api.com/v6/latest/USD")
        _rates_cache.update(ts=time.time(), rates=data["rates"])
    return _rates_cache["rates"]


def _money(value: float) -> str:
    return str(round(value)) if value >= 100 else f"{value:.2f}".replace(".", ",")


def currency_rate(codes: list[str]) -> str:
    try:
        rates = _rates()
        home = rates[CURRENCY_HOME]
    except Exception as e:
        log("Курс", f"ошибка: {e}")
        return f"{FAIL}не удалось узнать курс, проверьте интернет"
    parts = [f"{CURRENCY_SPOKEN.get(c, c)} стоит {_money(home / rates[c])} {CURRENCY_HOME_NAME}"
             for c in codes if c in rates]
    return f"{INFO}" + ", ".join(parts) if parts else f"{FAIL}не знаю такую валюту"


# ───────────────────────── ГРОМКОСТЬ ОТДЕЛЬНЫХ ПРИЛОЖЕНИЙ ─────────────────────────
_VOLUME_LABELS = {"music": "музыки", "youtube": "ютуба", "video": "видео"}


def _target_audio_sessions(target: str) -> list:
    """Источники звука для цели: music / youtube / video или «app:<название>»."""
    sessions = [s for s in _audio_sessions() if _foreign(s)]
    name = lambda s: _session_name(s).lower()
    if target.startswith("app:"):
        exes = _target_exes(target[4:]) if psutil else set()
        return [s for s in sessions if name(s) in exes]
    if target == "music":
        apps = [s for s in sessions if any(a in name(s) for a in MUSIC_APPS)]
        if apps:
            return apps
        if any("music" in _site_kinds(_norm(t)) for t in _window_titles()):   # музыка во вкладке браузера
            return [s for s in sessions if name(s) in BROWSER_EXES]
        return []
    found = [s for s in sessions if name(s) in BROWSER_EXES]     # YouTube и видео — в браузере
    if target == "video":
        found += [s for s in sessions if any(a in name(s) for a in VIDEO_APPS)]
    return found


def app_volume(target: str, level: int | None = None, delta: int | None = None) -> str:
    """Громкость одного приложения, не трогая общую: «музыку тише», «ютуб на 30».
    У браузера громкость общая на все вкладки — так устроен Windows."""
    target = (target or "").strip().lower()
    if target not in _VOLUME_LABELS and not target.startswith("app:"):
        target = "app:" + target
    label = _VOLUME_LABELS.get(target, target[4:])
    sessions = _target_audio_sessions(target)
    if not sessions:
        return f"{FAIL}не нашл{'а' if FEMALE_VOICE else 'ёл'} {label} среди источников звука"
    first = sessions[0]
    current = round(_ducker.original(_session_name(first).lower(), first) * 100)
    new = _clamp(level if level is not None else current + int(delta or 0))
    for session in sessions:
        _ducker.set_app(_session_name(session).lower(), session, new / 100)
    return f"громкость {label} {new} процентов"


# ───────────────────────── НАПОМИНАНИЯ ПО ВРЕМЕНИ ─────────────────────────
_reminders: list[dict] = []        # {"at": unix-время, "text": что напомнить}
_reminders_lock = threading.Lock()


def _save_reminders() -> None:
    try:
        REMINDERS_FILE.write_text(json.dumps(_reminders, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log("Напоминания", f"не удалось сохранить: {e}")


def _when_words(at: float) -> str:
    """«сегодня в 18 часов 30 минут» — так, чтобы удобно было произнести."""
    moment, today = datetime.fromtimestamp(at), datetime.now().date()
    days = (moment.date() - today).days
    day = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(days, f"{moment.day} {_MONTHS[moment.month - 1]}")
    text = f"{day} в {moment.hour} {_plural(moment.hour, 'час', 'часа', 'часов')}"
    if moment.minute:
        text += f" {moment.minute} {_plural(moment.minute, 'минута', 'минуты', 'минут')}"
    return text


def add_reminder(at: float, text: str = "") -> str:
    with _reminders_lock:
        _reminders.append({"at": float(at), "text": text.strip()})
        _reminders.sort(key=lambda r: r["at"])
        _save_reminders()
    return f"{INFO}напомню {_when_words(at)}" + (f": {text.strip()}" if text.strip() else "")


def list_reminders() -> str:
    with _reminders_lock:
        items = list(_reminders)
    if not items:
        return f"{INFO}напоминаний нет"
    return f"{INFO}" + "; ".join(f"{_when_words(r['at'])}: {r['text'] or 'без текста'}" for r in items[:5])


def cancel_reminders() -> str:
    with _reminders_lock:
        count = len(_reminders)
        _reminders.clear()
        _save_reminders()
    return f"отменил{END} напоминания" if count else f"{INFO}напоминаний нет"


def _fire_reminder(reminder: dict, missed: bool) -> None:
    play_sound("ready")
    what = reminder["text"] or "время пришло"
    speak(f"Господин, {'пропущенное напоминание' if missed else 'напоминаю'}: {what}.")


def _reminder_loop() -> None:
    """Раз в секунду проверяет, не пора ли напомнить. Пропущенные, пока программа была
    выключена, произносятся при запуске с пометкой «пропущенное»."""
    while True:
        now = time.time()
        with _reminders_lock:
            due = [r for r in _reminders if r["at"] <= now]
            if due:
                _reminders[:] = [r for r in _reminders if r["at"] > now]
                _save_reminders()
        for reminder in due:
            _fire_reminder(reminder, missed=now - reminder["at"] > 60)
        time.sleep(1)


def start_reminders() -> None:
    try:
        loaded = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        with _reminders_lock:
            _reminders[:] = sorted((r for r in loaded if "at" in r), key=lambda r: r["at"])
        if _reminders:
            log("Напоминания", f"загружено: {len(_reminders)}")
    except FileNotFoundError:
        pass
    except Exception as e:
        log("Напоминания", f"не удалось прочитать {REMINDERS_FILE.name}: {e}")
    threading.Thread(target=_reminder_loop, daemon=True).start()


FUNCTIONS: dict[str, Callable[..., str]] = {
    "close_app": close_app,
    "minimize_app": minimize_app,
    "close_active": close_active,
    "sleep_mode": sleep_mode,
    "request_power": request_power,
    "do_power": do_power,
    "cancel_power": cancel_power,
    "add_note": add_note,
    "read_notes": read_notes,
    "open_notes": open_notes,
    "weather": weather,
    "currency_rate": currency_rate,
    "tell_weekday": tell_weekday,
    "tell_datefull": tell_datefull,
    "window_state": window_state,
    "show_desktop": show_desktop,
    "alt_tab": alt_tab,
    "screenshot": screenshot,
    "lock_pc": lock_pc,
    "tell_time": tell_time,
    "tell_date": tell_date,
    "set_timer": set_timer,
    "cancel_timers": cancel_timers,
    "open_app": open_app,
    "open_browser": open_browser,
    "open_folder": open_folder,
    "set_volume": set_volume,
    "change_volume": change_volume,
    "mute": mute,
    "media": media,
    "now_playing": now_playing,
    "restart_self": lambda: restart_self(),       # объявлена ниже, рядом с треем
    "app_volume": app_volume,
    "add_reminder": add_reminder,
    "list_reminders": list_reminders,
    "cancel_reminders": cancel_reminders,
    "set_brightness": set_brightness,
    "change_brightness": change_brightness,
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_PERCENT = {"type": "integer", "description": "Значение 0-100"}
_DELTA = {"type": "integer", "description": "Изменение: положительное — больше, отрицательное — меньше"}

TOOLS = [
    _tool("open_app", "Открыть установленное приложение.",
          {"name": {"type": "string", "description": "Название на английском"}}, ["name"]),
    _tool("open_browser",
          "Открыть сайт или браузер. Для поиска на YouTube передай site='youtube' и query.",
          {"site": {"type": "string", "enum": list(SITES)}, "query": {"type": "string"}}, []),
    _tool("open_folder", "Открыть папку в проводнике.",
          {"name": {"type": "string", "enum": list(FOLDERS)}}, ["name"]),
    _tool("minimize_app", "Свернуть окно приложения (не закрывая его).",
          {"name": {"type": "string", "description": "Название приложения"}}, ["name"]),
    _tool("close_app", "Закрыть запущенное приложение. Только если явно просят закрыть, а не свернуть.",
          {"name": {"type": "string", "description": "Название приложения"}}, ["name"]),
    _tool("close_active", "Закрыть активное (текущее) окно или приложение.",
          {"window_only": {"type": "boolean", "description": "true — только окно, false — приложение целиком"}}, []),
    _tool("set_volume", "Установить громкость компьютера в процентах.", {"level": _PERCENT}, ["level"]),
    _tool("change_volume", "Изменить громкость на указанное число процентов.", {"delta": _DELTA}, ["delta"]),
    _tool("mute", "Выключить (true) или включить (false) звук.", {"state": {"type": "boolean"}}, ["state"]),
    _tool("media", "Управление музыкой/видео, в том числе фоновым плеером.",
          {"action": {"type": "string", "enum": ["pause", "play", "next", "previous"]},
           "target": {"type": "string", "enum": ["music", "youtube", "video"],
                      "description": "Чем управлять; не указывай, если пользователь не уточнил"}},
          ["action"]),
    _tool("now_playing", "Сказать, что сейчас играет: название и исполнителя.", {}, []),
    _tool("app_volume", "Громкость одного приложения (не общая): музыки, ютуба, видео или программы по названию.",
          {"target": {"type": "string", "description": "music, youtube, video или название приложения"},
           "level": _PERCENT, "delta": _DELTA}, ["target"]),
    _tool("set_brightness", "Установить яркость экрана в процентах.", {"level": _PERCENT}, ["level"]),
    _tool("change_brightness", "Изменить яркость экрана на указанное число процентов.", {"delta": _DELTA}, ["delta"]),
]


def execute_tool(name: str, args: dict) -> str:
    fn = FUNCTIONS.get(name)
    if fn is None:
        return f"{FAIL}не знаю инструмент {name}"
    try:
        result = fn(**args)
        log("Результат", f"{name}: {result}")
        return result
    except Exception as e:
        log("Ошибка", f"{name}({args}): {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} выполнить «{name}»"


# ───────────────────────── БЫСТРЫЙ ПУТЬ (БЕЗ ИИ) ─────────────────────────
def _currency_codes(seg: str) -> list[str]:
    if not R["currency_trigger"].search(seg):
        return []
    codes = [code for code, pattern in CURRENCY_WORDS.items() if re.search(pattern, seg)]
    if not codes and "валют" in seg:
        codes = ["USD", "EUR", "RUB"]
    return codes


def parse_local(segment: str) -> Callable[[], str] | None:
    """Пытается разобрать одну простую команду. Возвращает действие или None (тогда нужен ИИ).
    Все варианты формулировок лежат в phrases.py."""
    seg = segment.strip(PUNCT)
    if not seg:
        return None
    num = parse_number(seg)

    if R["self_restart"].search(seg):
        return lambda: execute_tool("restart_self", {})

    # Питание компьютера — всегда с подтверждением
    if R["cancel_power"].search(seg):
        return lambda: execute_tool("cancel_power", {})
    for key, action in (("shutdown", "shutdown"), ("restart", "restart"), ("pc_sleep", "sleep")):
        if R[key].search(seg):
            return lambda a=action: execute_tool("request_power", {"action": a})

    # Режим сна самой помощницы
    if R["sleep_mode"].search(seg):
        return lambda: execute_tool("sleep_mode", {})

    # Закрыть активное окно/приложение (раньше обычного «закрой X»)
    if ACTIVE_CLOSE_RE.match(seg):
        return lambda: execute_tool("close_active", {"window_only": "окно" in seg})

    # Таймеры
    if R["timer_cancel"].search(seg):
        return lambda: execute_tool("cancel_timers", {})
    if R["timer_set"].search(seg):
        secs = parse_duration(seg)
        if secs:
            return lambda: execute_tool("set_timer", {"seconds": secs})

    # Время, дата, день недели
    for key, tool in (("datefull", "tell_datefull"), ("weekday", "tell_weekday"),
                      ("date", "tell_date"), ("time", "tell_time")):
        if R[key].search(seg):
            return lambda t=tool: execute_tool(t, {})

    # Заметки, погода, курс валют
    if R["notes_open"].search(seg):
        return lambda: execute_tool("open_notes", {})
    if R["notes_read"].search(seg):
        return lambda: execute_tool("read_notes", {})
    if R["weather"].search(seg):
        return lambda: execute_tool("weather", {})
    codes = _currency_codes(seg)
    if codes:
        return lambda: execute_tool("currency_rate", {"codes": codes})

    # Окна и система
    if R["show_desktop"].search(seg):
        return lambda: execute_tool("show_desktop", {})
    if R["minimize"].search(seg):
        return lambda: execute_tool("window_state", {"action": "minimize"})
    if R["maximize"].search(seg):
        return lambda: execute_tool("window_state", {"action": "maximize"})
    if R["alt_tab"].search(seg):
        return lambda: execute_tool("alt_tab", {})
    if R["screenshot"].search(seg):
        return lambda: execute_tool("screenshot", {})
    if R["lock"].search(seg):
        return lambda: execute_tool("lock_pc", {})

    # Свернуть / развернуть конкретное приложение: «сверни яндекс музыку», «разверни телеграм»
    m = re.match(r"(?:сверни|свернуть|спрячь)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: execute_tool("minimize_app", {"name": target})
    m = re.match(r"(?:разверни|развернуть)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m and (m.group(1).strip() in APP_ALIASES or find_app(m.group(1).strip())):
        target = m.group(1).strip()
        return lambda: execute_tool("open_app", {"name": target})      # уже запущено — развернёт окно

    # Закрыть приложение
    m = re.match(r"(?:закрой|закрыть|заверши|завершить)\s+(?:приложение\s+|программу\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: execute_tool("close_app", {"name": target})

    if R["now_playing"].search(seg):
        return lambda: execute_tool("now_playing", {})

    # Громкость отдельного приложения: «музыку тише», «ютуб на 30»
    volume_action = parse_app_volume(seg)
    if volume_action:
        return volume_action

    # Медиа с целью: «музыка стоп», «ютуб пауза», «включи видео», «следующий трек»
    media_action = parse_media(seg)
    if media_action:
        return media_action

    # Поиск на YouTube / в Google
    search = parse_search(seg)
    if search:
        return search

    # Звук
    if R["mute"].search(seg):
        return lambda: execute_tool("mute", {"state": True})
    if R["unmute"].search(seg):
        return lambda: execute_tool("mute", {"state": False})
    if R["volume_up"].search(seg):
        return lambda: execute_tool("change_volume", {"delta": num or VOLUME_STEP})
    if R["volume_down"].search(seg):
        return lambda: execute_tool("change_volume", {"delta": -(num or VOLUME_STEP)})
    if R["volume_set"].search(seg):
        if num is not None:
            return lambda: execute_tool("set_volume", {"level": num})
        if R["volume_max"].search(seg):
            return lambda: execute_tool("set_volume", {"level": 100})

    # Яркость
    if R["bright_up"].search(seg):
        return lambda: execute_tool("change_brightness", {"delta": num or BRIGHTNESS_STEP})
    if R["bright_down"].search(seg):
        return lambda: execute_tool("change_brightness", {"delta": -(num or BRIGHTNESS_STEP)})
    if R["bright_set"].search(seg):
        if num is not None:
            return lambda: execute_tool("set_brightness", {"level": num})
        if R["bright_max"].search(seg):
            return lambda: execute_tool("set_brightness", {"level": 100})
        if R["bright_min"].search(seg):
            return lambda: execute_tool("set_brightness", {"level": 10})

    # Медиа (порядок важен: «сними с паузы» не должно срабатывать как «пауза»)
    if R["media_next"].search(seg):
        return lambda: execute_tool("media", {"action": "next"})
    if R["media_prev"].search(seg):
        return lambda: execute_tool("media", {"action": "previous"})
    if R["media_play"].search(seg):
        return lambda: execute_tool("media", {"action": "play"})
    if R["media_pause"].search(seg):
        return lambda: execute_tool("media", {"action": "pause"})

    # Пустой браузер
    if re.fullmatch(rf"(?:{OPEN_VERBS}\s+(?:мне\s+)?)?браузер", seg):
        return lambda: execute_tool("open_browser", {})

    # Короткие формы без глагола: «музыка», «ютуб», «яндекс музыка», «телеграм»
    if re.fullmatch(r"музык[аиу]|музычку|песню|песенку", seg):
        return lambda: execute_tool("media", {"action": "play", "target": "music"})
    for site, pattern in SITE_PATTERNS:
        if pattern.fullmatch(seg):
            return lambda s=site: execute_tool("open_browser", {"site": s})
    if seg in APP_ALIASES:
        return lambda: execute_tool("open_app", {"name": seg})
    if seg in BARE_FOLDERS:
        return lambda: execute_tool("open_folder", {"name": FOLDER_ALIASES[seg]})

    # Сайты: «открой ютуб», «включи яндекс музыку»
    m = re.match(rf"{OPEN_VERBS}\s+(?:сайт\s+)?(.+)$", seg)
    if m and len(m.group(1).split()) <= 3:
        site = detect_site(m.group(1))
        if site:
            return lambda: execute_tool("open_browser", {"site": site})

    # Папки: «открой загрузки», «открой папку музыка»
    m = re.match(r"(?:открой|покажи|запусти)\s+(папку\s+)?(.+)$", seg)
    if m:
        alias = m.group(2).strip()
        folder = FOLDER_ALIASES.get(alias)
        if folder and (m.group(1) or alias in BARE_FOLDERS):
            return lambda: execute_tool("open_folder", {"name": folder})

    # Открыть приложение
    m = re.match(r"(?:открой|запусти|открыть|запустить)\s+(.+)", seg)
    if m and (m.group(1).strip() in APP_ALIASES or not any(marker in seg for marker in COMPLEX_MARKERS)):
        target = m.group(1).strip()
        if find_app(target):
            return lambda: execute_tool("open_app", {"name": target})

    return None


_NUMBER_WORD_RE = re.compile(r"\b(?:" + "|".join(sorted({*_UNITS, *_TENS, "сто"}, key=len, reverse=True)) + r")\b")


def _volume_target(seg: str) -> tuple[str | None, str]:
    """Чью громкость менять: music / youtube / video / «app:<название>» и остаток фразы."""
    for name, rx in MEDIA_TARGET_RES:
        if rx.search(seg):
            return name, rx.sub(" ", seg)
    for alias in sorted(APP_ALIASES, key=len, reverse=True):
        rx = re.compile(rf"\b{re.escape(alias)}\w*" if len(alias) >= 4 else rf"\b{re.escape(alias)}\b")
        if rx.search(seg):
            return "app:" + alias, rx.sub(" ", seg)
    return None, seg


def parse_app_volume(seg: str) -> Callable[[], str] | None:
    """«музыку тише», «ютуб на 30», «громкость телеграма 50», «сделай видео погромче на 20»."""
    seg = seg.strip(PUNCT)
    target, rest = _volume_target(seg)
    if target is None:
        return None
    up, down = APP_VOLUME_UP_RE.search(rest), APP_VOLUME_DOWN_RE.search(rest)
    num = parse_number(rest)
    explicit = re.search(r"громкост|\bзвук|\bна\s+\S|процент|%", rest)   # «ютуб 30» без «на» — это поиск
    if up and down or not (up or down or (num is not None and explicit)):
        return None
    leftover = APP_VOLUME_FILLER_RE.sub(" ", _NUMBER_WORD_RE.sub(" ", re.sub(r"\d+", " ", rest)))
    leftover = APP_VOLUME_DOWN_RE.sub(" ", APP_VOLUME_UP_RE.sub(" ", leftover))
    if leftover.strip(PUNCT + " "):
        return None                       # лишние слова — пусть разбирается кто-то другой
    if up or down:
        delta = (num or VOLUME_STEP) * (1 if up else -1)
        return lambda: execute_tool("app_volume", {"target": target, "delta": delta})
    return lambda: execute_tool("app_volume", {"target": target, "level": num})


def _numbers_to_digits(text: str) -> str:
    """«шесть вечера» → «6 вечера», «восемнадцать тридцать» → «18 30», «двадцать пять» → «25»."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        value = 100 if w == "сто" else _TENS.get(w, _UNITS.get(w))
        if value is None:
            out.append(w)
            i += 1
            continue
        if w in _TENS and i + 1 < len(words) and _UNITS.get(words[i + 1], 99) < 10:
            value += _UNITS[words[i + 1]]
            i += 1
        out.append(str(value))
        i += 1
    return " ".join(out)


_REMIND_IN_RE = re.compile(
    r"\bчерез\s+((?:пол\s?часа|час|минуту|\d+\s*(?:час\w*|минут\w*|мин\b|секунд\w*|сек\b))"
    r"(?:\s*(?:и\s+)?\d+\s*(?:минут\w*|мин\b|секунд\w*|сек\b))?)")
_REMIND_AT_RE = re.compile(
    r"\b(?:в|на|к)\s+(\d{1,2})(?:\s*[:.]\s*(\d{2})|\s+(\d{2})(?=\s|$))?(?:\s*час\w*)?"
    r"(?:\s*(\d{1,2})\s*минут\w*)?(?:\s+(утра|дня|вечера|ночи))?")
_REMIND_NOON_RE = re.compile(r"\b(?:в|к)\s+(полдень|полночь)\b")
_REMIND_DAY_RE = re.compile(r"\b(сегодня|завтра|послезавтра)\b")


def parse_reminder(low: str) -> Callable[[], str] | None:
    """«напомни в 18:00 позвонить маме», «напомни завтра в 9 утра про встречу»,
    «напомни через 20 минут выключить плиту», «разбуди в 7»."""
    if REMIND_CANCEL_RE.search(low):
        return lambda: execute_tool("cancel_reminders", {})
    if REMIND_LIST_RE.search(low):
        return lambda: execute_tool("list_reminders", {})
    verb = REMIND_VERB_RE.search(low)
    if not verb:
        return None
    text = _numbers_to_digits(low.replace("ё", "е"))
    now = datetime.now()
    day = _REMIND_DAY_RE.search(text)
    shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[day.group(1)] if day else None
    if day:
        text = text[:day.start()] + " " + text[day.end():]
    at: float | None = None
    m = _REMIND_IN_RE.search(text)
    if m:
        seconds = parse_duration(m.group(1))
        if seconds:
            at = now.timestamp() + seconds
    else:
        m = _REMIND_NOON_RE.search(text) or _REMIND_AT_RE.search(text)
        if m:
            if m.re is _REMIND_NOON_RE:
                hour, minute = (12 if m.group(1) == "полдень" else 0), 0
            else:
                hour = int(m.group(1))
                minute = int(m.group(2) or m.group(3) or m.group(4) or 0)
                part = m.group(5)
                if part in ("дня", "вечера") and hour < 12:
                    hour += 12
                elif part in ("ночи", "утра") and hour == 12:
                    hour = 0
            if hour <= 23 and minute <= 59:
                moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=shift or 0)
                if shift is None and moment <= now:           # «в 9», а уже 10 — значит, завтра
                    moment += timedelta(days=1)
                at = moment.timestamp()
    if at is None:
        return lambda: f"{INFO}скажите, когда напомнить, например: напомни в 18 часов позвонить маме"

    message = REMIND_VERB_RE.sub(" ", text[:m.start()] + " " + text[m.end():])
    words = message.replace(",", " ").split()
    while words and words[0] in ("мне", "нам", "пожалуйста", "что", "чтобы", "про", "о", "об", "том"):
        words.pop(0)
    message = " ".join(words).strip(PUNCT)
    if not message and verb.group().startswith("разбуд"):
        message = "пора вставать"
    return lambda: execute_tool("add_reminder", {"at": at, "text": message})


def parse_media(seg: str) -> Callable[[], str] | None:
    """Фраза только из глагола, цели и связок: «музыка на стоп», «поставь ютуб на паузу»,
    «включи видео», «следующий трек». Цель — music / youtube / video (или никакой)."""
    seg = seg.strip(PUNCT)
    if MEDIA_UNPAUSE_RE.search(seg):
        action, rest = "play", MEDIA_UNPAUSE_RE.sub(" ", seg)
    else:
        found = [(name, rx) for name, rx in MEDIA_VERB_RES if rx.search(seg)]
        if len(found) != 1:                 # нет глагола или их два («выключи и включи») — не наш случай
            return None
        action, rx = found[0]
        rest = rx.sub(" ", seg)
    target = None
    for name, rx in MEDIA_TARGET_RES:
        if rx.search(rest):
            target = target or name
            rest = rx.sub(" ", rest)
    if MEDIA_FILLER_RE.sub(" ", rest).strip(PUNCT + " "):
        return None                         # остались другие слова: «включи звук», «переключи окно»
    return lambda: execute_tool("media", {"action": action, "target": target})


def detect_site(text: str) -> str | None:
    for site, pattern in SITE_PATTERNS:
        if pattern.search(text):
            return site
    return None


def parse_search(low: str) -> Callable[[], str] | None:
    """«найди котиков на ютубе», «открой ютуб и напиши в поиске котики», «загугли ...»,
    короткие формы: «ютуб котики», «гугл погода в лондоне», «найди рецепт борща»."""
    site = detect_site(low)
    if site is None:
        m = IN_BROWSER_RE.match(low)
        if m:
            query = (m.group("q1") or m.group("q2") or "").strip(PUNCT)
            if query and query not in ("открой", "найди", "поищи"):
                return lambda: execute_tool("open_browser", {"site": "google", "query": query})
        m = BARE_SEARCH_RE.match(low)                 # «найди X» без сайта — ищем в Google
        if m and m.group("query").strip(PUNCT):
            query = m.group("query").strip(PUNCT)
            return lambda: execute_tool("open_browser", {"site": "google", "query": query})
        return None
    if site not in ("youtube", "google"):
        return None
    m = SEARCH_VERB_RE.search(low)
    if not m:
        short = SHORT_SEARCH_RE.match(low)            # «ютуб котики»
        if short:
            query = short.group("query").strip(PUNCT)
            if query and not re.match(rf"{OPEN_VERBS}\b", query) and not SEARCH_VERB_RE.match(query):
                return lambda: execute_tool("open_browser", {"site": site, "query": query})
        # «включи на ютубе котики» — глагол без слова «найди» допустим только с предлогом «на/в ютубе»
        m = re.search(r"\b(?:включи|запусти)\b", low)
        if not m or not re.search(rf"\b(?:на|в|во)\s+(?:{YT}|{GOOGLE})", low):
            return None

    # Всё, что было ДО глагола поиска, должно быть лишь «открой ютуб и» — иначе там другая команда
    leftover = re.sub(rf"{OPEN_VERBS}|{YT}|{GOOGLE}|\b(?:сайт|и|на|в|во)\b", " ", low[:m.start()])
    if leftover.strip(PUNCT):
        return None

    query = SITE_MENTION_RE.sub(" ", low[m.end():])
    query = re.sub(r"\b(?:в\s+поиск\w*|поиск\w*)\b", " ", query)
    query = re.sub(r"\s+", " ", query).strip(PUNCT)
    query = re.sub(r"^(?:и\s+)?(?:(?:видео|ролик\w*)\s+)?", "", query).strip(PUNCT)
    if not query or re.match(OPEN_VERBS, query):
        return None
    return lambda: execute_tool("open_browser", {"site": site, "query": query})


def parse_all(low: str) -> list[Callable[[], str]] | None:
    """Разбирает всю команду без ИИ. Если хоть одна часть не разобралась — None (всё уйдёт в ИИ)."""
    # «ютуб стоп» — пауза, «ютуб на 30» — громкость, а не поиск; напоминание не режем по «и»
    whole = parse_reminder(low) or parse_app_volume(low) or parse_media(low) or parse_search(low)
    if whole:
        return [whole]
    if R["timer_set"].search(low) or R["datefull"].search(low) or _currency_codes(low):   # их нельзя резать по «и»
        whole = parse_local(low)
        if whole:
            return [whole]
    actions: list[Callable[[], str]] = []
    last_verb = ""
    for segment in SPLIT_RE.split(low):
        segment = segment.strip(PUNCT)
        if not segment:
            continue
        action = parse_local(segment)
        if action is None and last_verb:                # «открой телеграм и браузер» → «открой браузер»
            action = parse_local(f"{last_verb} {segment}")
        if action is None:
            return None
        actions.append(action)
        verb = VERB_RE.match(segment)
        if verb:
            last_verb = verb.group(1)
    return actions or None


# ───────────────────────── АГЕНТ (OLLAMA) ─────────────────────────
def _chat_kwargs(messages: list[dict]) -> dict:
    return dict(
        model=MODEL,
        messages=messages,
        tools=TOOLS,
        options={"num_ctx": NUM_CTX, "temperature": 0, "num_predict": 120},
        keep_alive=KEEP_ALIVE,
    )


def _chat(messages: list[dict]):
    kwargs = _chat_kwargs(messages)
    try:
        return ollama.chat(think=False, **kwargs)
    except (TypeError, ollama.ResponseError):
        return ollama.chat(**kwargs)


def _chat_stream(messages: list[dict]):
    """Потоковый ответ модели. Ошибка «think не поддерживается» вылезает при первом чанке —
    тогда повторяем без think (только если ещё ничего не успели получить)."""
    kwargs = _chat_kwargs(messages)
    started = False
    try:
        for chunk in ollama.chat(think=False, stream=True, **kwargs):
            started = True
            yield chunk
    except (TypeError, ollama.ResponseError):
        if started:
            raise
        yield from ollama.chat(stream=True, **kwargs)


# Граница, по которой отдаём кусок ответа в озвучку: конец предложения или перевод строки
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_STREAM_COMMA_AT = 120          # длинное предложение без точки режем по последней запятой


def _run_tools(tool_calls, user_text: str) -> None:
    phrases: list[str] = []
    seen: set[str] = set()
    for call in tool_calls:
        args = dict(call.function.arguments or {})
        key = call.function.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        log("Инструмент", f"{call.function.name}({args})")
        phrases.append(execute_tool(call.function.name, args))
    _reply(phrases, user_text)


def run_llm(user_text: str) -> None:
    """Один запрос к модели. Инструменты выполняем и отвечаем сами; обычный текст
    озвучиваем по предложениям прямо во время генерации (LLM_STREAM)."""
    global _last_reply
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(),
        {"role": "user", "content": user_text},
    ]
    not_understood = f"{FAIL}не понял{END} команду"

    if not LLM_STREAM:
        msg = _chat(messages).message
        if msg.tool_calls:
            _run_tools(msg.tool_calls, user_text)
        elif (msg.content or "").strip():
            _say(msg.content.strip(), user_text)
        else:
            _reply([not_understood], user_text)
        return

    tool_calls: list = []
    buffer = ""
    spoken: list[str] = []

    def say_part(part: str) -> None:
        part = part.strip()
        if part:
            spoken.append(part)
            speak(part)

    for chunk in _chat_stream(messages):
        msg = chunk.message
        if msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
        if tool_calls or not msg.content:
            continue
        buffer += msg.content
        *ready, buffer = _SENTENCE_END_RE.split(buffer)
        for sentence in ready:
            say_part(sentence)
        if len(buffer) > _STREAM_COMMA_AT and "," in buffer:
            head, buffer = buffer.rsplit(",", 1)
            say_part(head + ",")

    if tool_calls:
        _run_tools(tool_calls, user_text)
        return
    say_part(buffer)
    if not spoken:
        _reply([not_understood], user_text)
        return
    _last_reply = " ".join(spoken)
    _remember(user_text, _last_reply)


def _warmup_llm() -> None:
    """Загружаем модель в VRAM заранее, чтобы первая команда не ждала холодного старта."""
    try:
        ollama.chat(model=MODEL, messages=[{"role": "user", "content": "ок"}],
                    options={"num_predict": 1, "num_ctx": NUM_CTX}, keep_alive=KEEP_ALIVE)
        log("LLM", "модель прогрета.")
    except Exception as e:
        log("LLM", f"не удалось прогреть модель: {e}")


def unload_model() -> None:
    try:
        ollama.generate(model=MODEL, keep_alive=0)
        log("Система", "Модель выгружена из видеокарты.")
    except Exception:
        pass


# ───────────────────────── WHISPER ─────────────────────────
_whisper_model = None
_whisper_lock = threading.Lock()
_whisper_ready = threading.Event()


def _add_nvidia_dll_dirs() -> None:
    """Подключает cuBLAS/cuDNN из pip-пакетов nvidia-*-cu12: CTranslate2 ищет именно CUDA 12,
    а системная CUDA 13 (cublas64_13.dll) ему не подходит."""
    try:
        import nvidia  # noqa: F401  (namespace-пакет, ставится вместе с nvidia-cublas-cu12 и др.)
    except ImportError:
        return
    dirs: list[str] = []
    for base in getattr(nvidia, "__path__", []):
        for sub in Path(base).iterdir():
            if (sub / "bin").is_dir():
                dirs.append(str(sub / "bin"))
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def _create_whisper(size: str, device: str, compute: str):
    from faster_whisper import WhisperModel

    return WhisperModel(
        size,
        device=device,
        compute_type=compute,
        cpu_threads=min(8, os.cpu_count() or 4),
    )


def _load_whisper() -> None:
    """Грузит Whisper и сразу прогоняет секунду тишины: именно на первом распознавании
    вылезает «cublas64_12.dll not found», поэтому ошибку ловим здесь и откатываемся на процессор."""
    global _whisper_model
    try:
        _add_nvidia_dll_dirs()
        attempts = [(WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE)]
        if WHISPER_DEVICE != "cpu":      # turbo на процессоре медленная — там берём модель поменьше
            attempts.append((WHISPER_CPU_MODEL, "cpu", "int8"))
        for size, device, compute in attempts:
            try:
                log("STT", f"Загружаю Whisper '{size}' ({device}, {compute})...")
                model = _create_whisper(size, device, compute)
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                               language="ru", without_timestamps=True)
                list(segments)                      # прогрев
                _whisper_model = model
                log("STT", f"Whisper готов ({size}, {device}).")
                break
            except Exception as e:
                log("STT", f"{device}: не получилось — {e}")
        if VAD_ENABLED:
            has_speech(np.zeros(SAMPLE_RATE, dtype=np.float32))     # прогрев VAD
    except Exception as e:
        log("STT", f"Ошибка загрузки Whisper: {e}")
    finally:
        _whisper_ready.set()


_vad_skipped = 0


def has_speech(audio: np.ndarray) -> bool:
    """Silero VAD (идёт вместе с faster-whisper, работает за миллисекунды на процессоре):
    есть ли во фрагменте речь. Стук, кашель, музыку и шум не отдаём тяжёлому Whisper."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=VAD_THRESHOLD, min_speech_duration_ms=150, speech_pad_ms=100)
    return bool(get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE))


def transcribe(audio: np.ndarray) -> str:
    """float32 16 кГц → текст (с исходным регистром). Без речи (по VAD) — сразу пустая строка."""
    global _vad_skipped
    if _whisper_model is None:
        return ""
    if VAD_ENABLED:
        try:
            if not has_speech(audio):
                _vad_skipped += 1
                print(f"\r[VAD] шум пропущен без распознавания ({_vad_skipped})", end="", flush=True)
                return ""
        except Exception as e:
            log("VAD", f"ошибка, распознаю без проверки: {e}")
    with _whisper_lock:
        segments, _ = _whisper_model.transcribe(
            audio,
            language="ru",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            initial_prompt=WHISPER_PROMPT,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()


def is_noise(low: str) -> bool:
    """Отсеивает галлюцинации Whisper на тишине/шуме, включая эхо собственного промпта."""
    if len(low) < 3 or any(h in low for h in HALLUCINATIONS):
        return True
    return difflib.SequenceMatcher(None, low, WHISPER_PROMPT.lower()).ratio() > 0.8


# ───────────────────────── PIPER TTS ─────────────────────────
_piper_voice: PiperVoice | None = None
_silero_model = None
_engine = "piper"            # какой голос реально загружен: "piper" или "silero"
_piper_lock = threading.Lock()
_tts_queue: queue.Queue = queue.Queue()    # (вид "text"/"sound", текст или имя звука, поколение) → синтез
_play_queue: queue.Queue = queue.Queue()   # ((аудио, частота), поколение) → динамики
_tts_cache: dict[str, tuple[np.ndarray, int]] = {}
TTS_CACHE_MAX = 200
_inflight = 0                  # фраз/звуков в очереди, в синтезе или в динамиках
_inflight_lock = threading.Lock()
_speaking = threading.Event()   # пока set — микрофон «глохнет», чтобы не слушать саму себя


def _ensure_piper() -> None:
    global _piper_voice
    if PiperVoice is None:
        raise RuntimeError("piper-tts не установлен")
    PIPER_DIR.mkdir(exist_ok=True)

    if not PIPER_MODEL.exists():
        log("TTS", f"Скачиваю русскую модель {PIPER_VOICE}-medium (~60 MB)...")
        import urllib.request

        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/{PIPER_VOICE}/medium"
        for fname in (PIPER_MODEL.name, PIPER_MODEL.name + ".json"):
            dest = PIPER_DIR / fname
            if not dest.exists():
                log("TTS", f"  -> {fname}...")
                urllib.request.urlretrieve(f"{base}/{fname}", dest)

    # espeakbridge.pyd ищет данные по захардкоженному пути сборки — копируем туда espeak-ng-data.
    import shutil

    espeak_src = Path(__file__).parent.parent
    try:
        import piper.phonemize_espeak as _pe

        espeak_src = Path(_pe.__file__).parent / "espeak-ng-data"
    except Exception:
        pass

    hardcoded = Path(
        r"D:\a\piper1-gpl\piper1-gpl\_skbuild\win-amd64-3.9\cmake-build"
        r"\espeak_ng-install\share\espeak-ng-data"
    )
    if espeak_src.exists() and not hardcoded.exists():
        log("TTS", "Настраиваю espeak-ng-data...")
        hardcoded.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(espeak_src), str(hardcoded), dirs_exist_ok=True)

    has_cuda = False
    try:
        ctypes.CDLL("cublasLt64_13.dll")
        has_cuda = True
    except Exception:
        pass

    _piper_voice = PiperVoice.load(str(PIPER_MODEL), use_cuda=has_cuda)
    log("TTS", f"TTS готов ({'GPU (CUDA)' if has_cuda else 'CPU'}).")


_tts_gen = 0   # растёт при каждом прерывании: устаревшие фразы из очереди не произносятся


def _silero_model_file() -> Path:
    """Модель лежит рядом со скриптом (папка silero). Качаем сами, а не через пакет silero:
    пути с кириллицей (C:\\Слуга\\...) PyTorch на Windows открыть не может."""
    path = BASE_DIR / "silero" / f"{SILERO_MODEL}.pt"
    if path.exists() and path.stat().st_size > 10_000_000:
        return path
    import urllib.request

    url = f"https://models.silero.ai/models/tts/ru/{SILERO_MODEL}.pt"
    log("TTS", f"Скачиваю модель Silero: {url}")
    path.parent.mkdir(exist_ok=True)
    part = path.with_suffix(".part")
    urllib.request.urlretrieve(url, part)
    part.replace(path)
    return path


def _ensure_silero() -> None:
    global _silero_model, _engine
    import io

    import torch
    from torch.package import PackageImporter

    torch.set_num_threads(min(4, os.cpu_count() or 4))
    data = _silero_model_file().read_bytes()                  # читаем средствами Python (юникод-пути работают)
    model = PackageImporter(io.BytesIO(data)).load_pickle("tts_models", "model")
    model.to(torch.device(SILERO_DEVICE))
    _silero_model, _engine = model, "silero"
    log("TTS", f"Silero готов (голос {SILERO_SPEAKER}, {SILERO_DEVICE}).")


def _ensure_tts() -> None:
    """Грузит голос из config.TTS_ENGINE; если Silero не взлетел — откатывается на Piper."""
    global _engine
    if TTS_ENGINE == "silero":
        try:
            _ensure_silero()
            return
        except Exception as e:
            log("TTS", f"Silero недоступен ({e}) — использую Piper.")
            if isinstance(e, ModuleNotFoundError) and e.name:
                log("TTS", f"Не хватает библиотеки, поставьте её: pip install {e.name.split('.')[0]}")
    _ensure_piper()
    _engine = "piper"


def _tts_ready() -> bool:
    return (_engine == "silero" and _silero_model is not None) or (_engine == "piper" and _piper_voice is not None)


def _synthesize_piper(text: str) -> tuple[np.ndarray, int] | None:
    with _piper_lock:
        sr = _piper_voice.config.sample_rate
        rate = sr
        try:
            if SynthesisConfig is None:
                raise TypeError("SynthesisConfig недоступен")
            cfg = SynthesisConfig(length_scale=1.0 / TTS_SPEED)      # <1 — быстрее, голос не искажается
            chunks = [c.audio_float_array for c in _piper_voice.synthesize(text, syn_config=cfg)]
        except TypeError:
            chunks = [c.audio_float_array for c in _piper_voice.synthesize(text)]
            rate = int(sr * TTS_SPEED)                               # запасной вариант: быстрее проигрываем
    return (np.concatenate(chunks), rate) if chunks else None


def _split_sentences(text: str, limit: int = 300) -> list[str]:
    """Silero плохо переносит длинные тексты — режем по предложениям."""
    chunks: list[str] = []
    current = ""
    for part in re.split(r"(?<=[.!?;])\s+", text):
        if current and len(current) + len(part) + 1 > limit:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks


def _synthesize_silero(text: str) -> tuple[np.ndarray, int] | None:
    from xml.sax.saxutils import escape

    rate_name = "x-fast" if TTS_SPEED >= 1.5 else "fast" if TTS_SPEED >= 1.15 else "medium"
    pieces: list[np.ndarray] = []
    with _piper_lock:
        for chunk in _split_sentences(normalize_for_tts(text)):
            kwargs = dict(speaker=SILERO_SPEAKER, sample_rate=SILERO_SAMPLE_RATE)
            ssml = f'<speak><prosody rate="{rate_name}">{escape(chunk)}</prosody></speak>'
            for attempt in (dict(ssml_text=ssml, put_accent=True, put_yo=True),
                            dict(text=chunk, put_accent=True, put_yo=True),
                            dict(text=chunk)):
                try:
                    audio = _silero_model.apply_tts(**attempt, **kwargs)
                    break
                except Exception as e:
                    last_error = e
            else:
                raise last_error
            pieces.append(audio.detach().cpu().numpy().astype("float32"))
    return (np.concatenate(pieces), SILERO_SAMPLE_RATE) if pieces else None


def _synthesize(text: str) -> tuple[np.ndarray, int] | None:
    cached = _tts_cache.get(text)
    if cached:
        return cached
    result = _synthesize_silero(text) if _engine == "silero" else _synthesize_piper(text)
    if result and len(text) <= 80:
        if len(_tts_cache) >= TTS_CACHE_MAX:
            _tts_cache.pop(next(iter(_tts_cache)))          # выбрасываем самую старую фразу
        _tts_cache[text] = result
    return result


SOUND_RATE = 44100


def _chime(notes: tuple[float, ...], step_ms: int = 110, decay_ms: int = 260, volume: float = 0.3) -> np.ndarray:
    """Мягкий «колокольчик»: плавная атака, естественное затухание, тёплые обертоны.
    Ноты идут с шагом step_ms и звучат внахлёст. По краям — тишина: звуковая карта
    «съедает» самое начало и конец буфера, из-за этого простой писк казался обрезанным."""
    rate = SOUND_RATE
    lead, tail = int(rate * 0.003), int(rate * 0.03)              # поток открыт заранее — запас почти не нужен
    n = int(rate * (decay_ms * 2.2) / 1000)                      # пока затухание почти не уйдёт в ноль
    t = np.arange(n) / rate
    attack = np.minimum(1.0, t / 0.008)                          # 8 мс — без щелчка, но не «вяло»
    out = np.zeros(lead + int(rate * step_ms / 1000) * (len(notes) - 1) + n + tail, dtype=np.float64)
    for i, f in enumerate(notes):
        tone = (np.sin(2 * np.pi * f * t) * np.exp(-t / (decay_ms / 1000))
                + 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / (decay_ms / 2500))    # обертон гаснет быстрее
                + 0.08 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / (decay_ms / 5000)))
        start = lead + int(rate * step_ms / 1000) * i
        out[start:start + n] += tone * attack
    out *= volume / max(1e-9, np.abs(out).max())
    out[-tail - int(rate * 0.02):-tail] *= np.linspace(1, 0, int(rate * 0.02))  # гарантированно гаснет в ноль
    out[-tail:] = 0
    return out.astype(np.float32)


SOUNDS: dict[str, tuple[np.ndarray, int]] = {
    "done": (_chime((523.3, 784.0), volume=SOUND_VOLUME), SOUND_RATE),                  # до → соль, вверх
    "error": (_chime((440.0, 329.6), step_ms=150, decay_ms=300, volume=SOUND_VOLUME), SOUND_RATE),  # ля → ми, вниз
    "cancel": (_chime((392.0,), decay_ms=200, volume=SOUND_VOLUME * 0.7), SOUND_RATE),  # одна тихая соль
    "ready": (_chime((523.3, 659.3, 784.0), step_ms=90, volume=SOUND_VOLUME), SOUND_RATE),  # до-ми-соль
}
_WAKE_SOUND = _chime((BEEP_FREQ,), decay_ms=BEEP_MS, volume=SOUND_VOLUME)


class Chimes:
    """Отдельный, заранее открытый поток вывода для сигналов. sd.play() каждый раз заново
    открывает звуковое устройство (на Windows это 0.1–0.3 с) — отсюда была задержка «колокольчика».
    Поток открываем, как только вы начали говорить, и закрываем после CHIME_IDLE_SEC тишины:
    постоянно открытый аудиопоток мешал бы компьютеру уходить в сон."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: list[list] = []          # [сэмплы, позиция] — звуки могут накладываться
        self._stream = None
        self._last_used = 0.0

    def warm(self) -> None:
        """Открыть поток заранее (вызывается при начале речи)."""
        with self._lock:
            self._last_used = time.time()
            if self._stream is not None:
                return
            # Сначала WASAPI (~20 мс задержки), если не вышло — системный по умолчанию MME (~90 мс)
            for device, extra in ((self._wasapi_device(), sd.WasapiSettings(auto_convert=True)), (None, None)):
                if device is None and extra is not None:
                    continue
                try:
                    self._stream = sd.OutputStream(device=device, samplerate=SOUND_RATE, channels=1, dtype="float32",
                                                   latency="low", callback=self._callback, extra_settings=extra)
                    self._stream.start()
                    return
                except Exception as e:
                    self._stream = None
                    log("Звук", f"не удалось открыть поток для сигналов ({'WASAPI' if extra else 'MME'}): {e}")

    @staticmethod
    def _wasapi_device() -> int | None:
        """Те же колонки, что выбраны в Windows по умолчанию, но через WASAPI."""
        try:
            default = sd.query_devices(kind="output")["name"]
            for i, d in enumerate(sd.query_devices()):
                api = sd.query_hostapis(d["hostapi"])["name"]
                if d["max_output_channels"] and "WASAPI" in api and d["name"].startswith(default[:28]):
                    return i
        except Exception:
            pass
        return None

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            for voice in self._voices:
                chunk = voice[0][voice[1]:voice[1] + frames]
                out[:len(chunk)] += chunk
                voice[1] += frames
            self._voices = [v for v in self._voices if v[1] < len(v[0])]
        outdata[:, 0] = out

    def play(self, samples: np.ndarray) -> bool:
        self.warm()
        with self._lock:
            if self._stream is None:
                return False
            self._voices.append([samples, 0])
            return True

    def close_if_idle(self) -> None:
        with self._lock:
            if self._stream is None or self._voices or time.time() - self._last_used < CHIME_IDLE_SEC:
                return
            stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


_chimes = Chimes()


def play_beep() -> None:
    """Короткий сигнал «слушаю» — мгновенно, без ожидания синтеза речи."""
    if _chimes.play(_WAKE_SOUND):
        time.sleep(len(_WAKE_SOUND) / SOUND_RATE * 0.6)   # ждём основную часть: дальше выбросим её эхо
        return
    try:
        sd.play(_WAKE_SOUND, samplerate=SOUND_RATE)
        sd.wait()
    except Exception:
        pass


def _prewarm_tts() -> None:
    for phrase in PREWARM_PHRASES:
        try:
            _synthesize(address(phrase))
        except Exception:
            return


def _enqueue(kind: str, payload: str) -> None:
    global _inflight
    with _inflight_lock:
        _inflight += 1
    if kind == "text":      # сигналы микрофон не «глушат»: их эхо отсеет VAD, а диалог не прервётся
        _speaking.set()
    _tts_queue.put((kind, payload, _tts_gen))


def _item_done(count: int = 1, tail: bool = True) -> None:
    """Фраза закончилась (или выброшена). Когда очередь пуста — микрофон снова обычной чувствительности."""
    global _inflight
    with _inflight_lock:
        _inflight = max(0, _inflight - count)
        idle = _inflight == 0
    if idle and tail:
        time.sleep(0.25)                    # хвост эха от динамиков
        with _inflight_lock:
            if _inflight == 0:
                _speaking.clear()


def _synth_worker() -> None:
    """Синтезирует фразы заранее: пока звучит одно предложение, следующее уже готовится."""
    while True:
        kind, payload, gen = _tts_queue.get()
        audio = None
        if gen == _tts_gen:
            try:
                if kind == "sound":
                    audio = SOUNDS.get(payload)
                elif _tts_ready():
                    audio = _synthesize(payload)
                else:
                    log("TTS", "Голосовая модель не загружена")
            except Exception as e:
                log("TTS", f"Ошибка синтеза: {e}")
        if audio is not None and gen == _tts_gen:
            _play_queue.put((audio, gen))
        else:
            _item_done()


def _play_worker() -> None:
    while True:
        (samples, rate), gen = _play_queue.get()
        try:
            if gen == _tts_gen:
                sd.play(samples, samplerate=rate)
                sd.wait()
        except Exception as e:
            log("TTS", f"Ошибка воспроизведения: {e}")
        finally:
            _item_done()


def _drop_queued(q: queue.Queue) -> int:
    dropped = 0
    while True:
        try:
            q.get_nowait()
            dropped += 1
        except queue.Empty:
            return dropped


def stop_speaking() -> None:
    """Мгновенно обрывает речь и очищает очередь фраз."""
    global _tts_gen
    _tts_gen += 1
    _item_done(_drop_queued(_tts_queue) + _drop_queued(_play_queue), tail=False)
    try:
        sd.stop()
    except Exception:
        pass
    _speaking.clear()


def speak(text: str) -> None:
    """Говорит в фоне: управление возвращается сразу, можно давать следующую команду."""
    text = address(text)
    if not text:
        return
    print(f"\n{ASSISTANT_NAME}: {text}", flush=True)
    if threading.current_thread() is threading.main_thread():
        _ducker.duck()                      # приглушаем музыку только когда реально говорим
    _enqueue("text", text)


def play_sound(name: str) -> None:
    """Короткий звук «готово» / «ошибка» / … — через ту же очередь, чтобы не перебивать речь."""
    sound = SOUNDS.get(name)
    if sound is not None and _chimes.play(sound[0]):
        return                              # мгновенно, через заранее открытый поток
    _enqueue("sound", name)                 # запасной путь — через общую очередь


def wait_silence() -> None:
    while _speaking.is_set() or _inflight:
        time.sleep(0.02)


def speak_sync(text: str) -> None:
    """Говорит и ждёт конца фразы (нужно только для коротких вопросов и прощания)."""
    speak(text)
    wait_silence()


# ───────────────────────── СЛОВО-АКТИВАТОР (openWakeWord) ─────────────────────────
class WakeDetector:
    """Маленькая нейросеть слушает имя на каждом блоке звука (~2 мс на 0.1 с).
    Whisper запускается, только если имя прозвучало, — остальное время видеокарта отдыхает,
    а разговоры рядом и телевизор не распознаются вовсе."""

    def __init__(self) -> None:
        self._model = None
        self._hit = False

    @property
    def enabled(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if not WAKE_MODEL.exists():
            log("Wake", f"модели {WAKE_MODEL.name} нет — имя ищет Whisper (как раньше). "
                        f"Как обучить свою — см. папку {WAKE_MODEL.parent.name}")
            return
        try:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=[str(WAKE_MODEL)], inference_framework="onnx")
            log("Wake", f"слушаю имя моделью {WAKE_MODEL.name} (порог {WAKE_THRESHOLD})")
        except Exception as e:
            log("Wake", f"не удалось загрузить {WAKE_MODEL.name}: {e} — имя ищет Whisper")

    def feed(self, block: np.ndarray) -> None:
        if self._model is None:
            return
        try:
            scores = self._model.predict((np.clip(block, -1, 1) * 32767).astype(np.int16))
            if max(scores.values(), default=0) >= WAKE_THRESHOLD:
                self._hit = True
        except Exception as e:
            log("Wake", f"ошибка: {e} — отключаю, имя будет искать Whisper")
            self._model = None

    def take(self) -> bool:
        """Звучало ли имя с прошлой проверки (флаг сбрасывается)."""
        hit, self._hit = self._hit, False
        return hit


_wake = WakeDetector()


def _get_block(audio_q: queue.Queue, timeout: float | None = None) -> np.ndarray:
    """Следующий блок с микрофона; заодно отдаём его детектору имени."""
    block = audio_q.get(timeout=timeout).flatten()
    _wake.feed(block)
    return block


def _strip_name_like(text: str) -> str:
    """Имя услышала нейросеть, а Whisper записал его иначе («Гарри, пауза») — убираем первое слово,
    если оно похоже на имя."""
    words = text.split(maxsplit=1)
    if words and difflib.SequenceMatcher(None, words[0].lower().strip(PUNCT), "харви").ratio() >= 0.5:
        return words[1] if len(words) > 1 else ""
    return text


# ───────────────────────── ЗНАЧОК В ТРЕЕ ─────────────────────────
_quit_event = threading.Event()        # «Выход» из меню трея
_tray_icon = None
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "HarveyAssistant"


def _autostart_command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")       # без окна консоли
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass
    log("Система", f"автозапуск с Windows {'включён' if enabled else 'выключен'}")


def _tray_image(sleeping: bool, speaking: bool):
    from PIL import Image, ImageDraw

    color = (130, 130, 140) if sleeping else (70, 140, 255) if speaking else (60, 185, 110)
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=color)
    draw.text((23, 17), "H", fill=(255, 255, 255))
    return image.resize((64, 64))


def open_log_report() -> None:
    """Отчёт по логу (log_report.py): что ушло в ИИ и что не получилось — открывается в Блокноте."""
    from log_report import build_report

    path = BASE_DIR / "отчёт по логу.txt"
    path.write_text(build_report(), encoding="utf-8")
    os.startfile(str(path))


def start_tray() -> None:
    """Значок у часов: состояние, спать / проснуться, заметки, лог, автозапуск, выход."""
    if not TRAY_ENABLED:
        return
    try:
        import pystray
    except Exception as e:
        log("Трей", f"pystray не установлен ({e}) — значка не будет")
        return

    def status(_item) -> str:
        return f"{ASSISTANT_NAME}: {'спит' if _sleeping else 'слушает'}"

    def go_sleep(_icon, _item) -> None:
        set_sleeping(True)
        play_sound("cancel")

    def wake(_icon, _item) -> None:
        set_sleeping(False)
        play_sound("ready")

    def toggle_autostart(_icon, item) -> None:
        try:
            set_autostart(not item.checked)
        except Exception as e:
            log("Трей", f"не удалось изменить автозапуск: {e}")

    def quit_app(icon, _item) -> None:
        _quit_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status, None, enabled=False),
        pystray.MenuItem("Спать", go_sleep, visible=lambda _i: not _sleeping),
        pystray.MenuItem("Проснуться", wake, visible=lambda _i: _sleeping),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Заметки", lambda _i, _t: open_notes()),
        pystray.MenuItem("Лог", lambda _i, _t: os.startfile(str(LOG_FILE)), visible=lambda _i: LOG_ENABLED),
        pystray.MenuItem("Отчёт: что Харви не поняла", lambda _i, _t: open_log_report(), visible=lambda _i: LOG_ENABLED),
        pystray.MenuItem("Запускать вместе с Windows", toggle_autostart, checked=lambda _i: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Перезапустить (применить изменения)", lambda _i, _t: restart_self()),
        pystray.MenuItem("Выход", quit_app),
    )
    global _tray_icon
    icon = _tray_icon = pystray.Icon("harvey", _tray_image(False, False), ASSISTANT_NAME, menu)

    def refresh() -> None:            # цвет значка: зелёный — слушает, синий — говорит, серый — спит
        last = None
        while not _quit_event.is_set():
            state = (_sleeping, _speaking.is_set())
            if state != last:
                last = state
                try:
                    icon.icon = _tray_image(*state)
                    icon.title = f"{ASSISTANT_NAME}: {'спит' if state[0] else 'говорит' if state[1] else 'слушает'}"
                    icon.update_menu()
                except Exception:
                    pass
            time.sleep(0.3)

    threading.Thread(target=icon.run, daemon=True).start()
    threading.Thread(target=refresh, daemon=True).start()


def restart_self() -> str:
    """Перезапуск помощницы: так подхватываются изменения в harvey.py / config.py / phrases.py.
    Сначала отпускаем «замок» единственного экземпляра, иначе новый процесс сразу закроется."""
    def later() -> None:
        time.sleep(0.6)                     # даём прозвучать сигналу «готово»
        log("Система", "Перезапуск...")
        if _instance_mutex:
            _kernel32.CloseHandle(_instance_mutex)
        console = not Path(sys.executable).name.lower().startswith("pythonw")
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(BASE_DIR),
                         creationflags=subprocess.CREATE_NEW_CONSOLE if console else 0)
        _quit_event.set()
        if _tray_icon is not None:
            try:
                _tray_icon.stop()
            except Exception:
                pass

    threading.Thread(target=later, daemon=True).start()
    return f"перезапускаюсь"


def _single_instance() -> bool:
    """Второй экземпляр (например, автозапуск + ручной запуск) сразу выходит."""
    global _instance_mutex
    _instance_mutex = _kernel32.CreateMutexW(None, False, "Local\\HarveyAssistantSingleInstance")
    return ctypes.get_last_error() != 183       # ERROR_ALREADY_EXISTS


_instance_mutex = None


# ───────────────────────── ЗАПИСЬ РЕЧИ ─────────────────────────
def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block ** 2)))


def calibrate_silence(duration: float = 0.5) -> float:
    """Измеряет фоновый шум и считает порог чувствительности."""
    recording = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return max(0.005, min(0.02, _rms(recording.flatten()) * 1.8))


def drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def record_utterance(audio_q: queue.Queue, pre_buffer: deque, first_block: np.ndarray,
                     threshold: float) -> np.ndarray | None:
    """Пишет фразу от первого громкого блока до паузы; лишнюю тишину в конце обрезает (быстрее STT)."""
    chunks = list(pre_buffer) + [first_block]
    silent = 0
    while silent < SILENCE_BLOCKS and len(chunks) < MAX_UTTERANCE_BLOCKS:
        block = _get_block(audio_q)
        chunks.append(block)
        silent = silent + 1 if _rms(block) < threshold else 0
    if silent > TAIL_KEEP_BLOCKS:
        chunks = chunks[:-(silent - TAIL_KEEP_BLOCKS)]
    if len(chunks) < MIN_SPEECH_BLOCKS:
        return None
    return np.concatenate(chunks)


def record_command(audio_q: queue.Queue, threshold: float) -> np.ndarray | None:
    """Ждёт команду после одиночного «Харви»."""
    pre: deque = deque(maxlen=3)
    chunks: list[np.ndarray] = []
    started = False
    waited = silent = 0
    while True:
        block = _get_block(audio_q)
        waited += 1
        loud = _rms(block) > threshold
        if not started:
            if loud:
                started = True
                chunks = list(pre) + [block]
            else:
                pre.append(block)
                if waited > COMMAND_WAIT_BLOCKS:
                    return None
            continue
        chunks.append(block)
        silent = 0 if loud else silent + 1
        if silent >= SILENCE_BLOCKS or len(chunks) >= MAX_UTTERANCE_BLOCKS:
            break
    if silent > TAIL_KEEP_BLOCKS:
        chunks = chunks[:-(silent - TAIL_KEEP_BLOCKS)]
    return np.concatenate(chunks)


# ───────────────────────── ОБРАБОТКА КОМАНД ─────────────────────────
_last_reply = ""
_history: deque = deque(maxlen=MEMORY_TURNS)       # (время, команда, ответ) — контекст для ИИ


def _remember(user: str, reply: str) -> None:
    _history.append((time.time(), user, reply))


def _history_messages() -> list[dict]:
    """Последние реплики, чтобы ИИ понимал «закрой его», «а теперь в гугле»."""
    now = time.time()
    messages: list[dict] = []
    for stamp, user, reply in _history:
        if now - stamp <= MEMORY_TTL:
            messages += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
    return messages


def _say(text: str, user: str | None = None) -> None:
    global _last_reply
    _last_reply = text
    if user:
        _remember(user, text)
    speak(text)


def _reply(phrases: list[str], user: str | None = None) -> None:
    """Отвечает на выполненную команду. В тихом режиме — звук «готово»/«ошибка»
    и вслух только то, что нужно услышать (время, погода, ошибка, вопрос)."""
    if user:   # ИИ помнит, что именно было сделано, — так он понимает «закрой его»
        _remember(user, ", ".join(p.lstrip(FAIL + INFO + RAW) for p in phrases) or "готово")
    if not QUIET_MODE:
        _say(compose(phrases))
        return
    text, sound = compose_quiet(phrases)
    if sound:
        play_sound(sound)
    if text:
        _say(text)


def dialog_accepts(command: str) -> bool:
    """Фраза без «Харви» в окне диалога: берём её, только если это явно команда."""
    stripped = command.strip().lstrip(PUNCT)
    low = stripped.lower().strip(PUNCT)
    if not low:
        return False
    if DICTATE_RE.match(stripped) or any(p.match(stripped) for p in NOTE_ADD_RE) or REPEAT_RE.match(low):
        return True
    return not DIALOG_LOCAL_ONLY or parse_all(low) is not None


def handle_command(command: str) -> bool:
    """Выполняет команду. Речь идёт в фоне, поэтому следующую команду можно давать сразу.
    Возвращает False, если нужно завершить работу."""
    command = command.strip()
    low = command.lower().strip(PUNCT)
    clear_pending()                              # новая команда отменяет ожидание «да/нет»
    stripped = command.lstrip(PUNCT)

    # Заметки и диктовку проверяем первыми: в тексте может быть любое слово, даже «выход»
    for pattern in NOTE_ADD_RE:
        m = pattern.match(stripped)
        if m:
            _reply([execute_tool("add_note", {"text": m.group("text")})], low)
            return True
    m = DICTATE_RE.match(stripped)
    if m:
        _reply([execute_tool_dictate(m.group(1))], low)
        return True

    if R["exit"].search(low):
        speak_sync("Отключаюсь." if QUIET_MODE else "Слушаюсь, господин. Я отключаюсь.")
        return False

    if SILENCE_RE.match(low):                  # «замолчи» после одиночного «Харви» — просто молчим
        stop_speaking()
        return True

    if REPEAT_RE.match(low):
        speak(_last_reply or "Мне пока нечего повторять, господин.")
        return True

    # Всё, что можно разобрать правилами (включая цепочки «тише и пауза»), выполняем без ИИ
    actions = parse_all(low)
    if actions:
        log("Без ИИ", low)
        _reply([a() for a in actions], low)
        return True

    # Всё остальное — в Ollama (в лог попадает, чтобы потом добавить фразу в phrases.py)
    log("К ИИ", low)
    if not QUIET_MODE:
        speak("Секунду, господин.")
    try:
        run_llm(low)
    except Exception as e:
        log("Ошибка", str(e))
        _reply([f"{FAIL}произошла ошибка"])
    return True


def execute_tool_dictate(text: str) -> str:
    try:
        return dictate(text)
    except Exception as e:
        log("Ошибка", f"dictate: {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} записать текст"


# ───────────────────────── ГЛАВНЫЙ ЦИКЛ ─────────────────────────
def main() -> None:
    if sys.platform != "win32":
        print("Скрипт рассчитан на Windows.")
        return
    if sys.stdout is None or sys.stderr is None:       # запуск без консоли (pythonw, ярлык, автозапуск)
        sys.stdout = sys.stdout or open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stderr or open(os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging()
    if not _single_instance():
        log("Система", f"{ASSISTANT_NAME} уже запущен{'а' if FEMALE_VOICE else ''} — второй экземпляр не нужен.")
        return

    _ducker.recover()                        # музыка осталась тихой после прошлого запуска? вернём
    threading.Thread(target=_synth_worker, daemon=True).start()
    threading.Thread(target=_play_worker, daemon=True).start()
    threading.Thread(target=_load_start_apps, daemon=True).start()
    threading.Thread(target=_load_whisper, daemon=True).start()
    threading.Thread(target=_warmup_llm, daemon=True).start()

    try:
        _ensure_tts()
        threading.Thread(target=_prewarm_tts, daemon=True).start()
    except Exception as e:
        log("TTS", f"Не удалось подготовить голос: {e}")

    _wake.load()
    start_reminders()
    start_tray()

    log("Система", "Жду загрузки Whisper...")
    _whisper_ready.wait()
    if _whisper_model is None:
        log("STT", "Whisper не загрузился ни на видеокарте, ни на процессоре — выхожу.")
        return

    log("Система", "Калибрую микрофон...")
    threshold = calibrate_silence()
    log("Система", f"Порог чувствительности микрофона: {threshold:.4f}")

    if QUIET_MODE:
        play_sound("ready")
        wait_silence()
    else:
        speak_sync("Система запущена. Ожидаю вашей команды, господин.")
    print(f"\n[ Режим постоянного слушания (Wake word: «{ASSISTANT_NAME}») ]")

    audio_q: queue.Queue = queue.Queue()
    pre_buffer: deque = deque(maxlen=PRE_BUFFER_BLOCKS)
    ducker = _ducker
    dialog_until = 0.0                       # до какого момента слушаем без «Харви» (0 — окно закрыто)

    def audio_callback(indata, frames, time_info, status):
        audio_q.put(indata.copy())          # слушаем всегда: так Харви можно перебить на полуслове

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=BLOCK_SIZE, callback=audio_callback):
            while not _quit_event.is_set():
                # Музыку возвращаем на место, как только Харви договорила
                if ducker.active and (not _speaking.is_set() or ducker.stale):
                    ducker.restore()
                _chimes.close_if_idle()
                # Окно диалога отсчитываем с момента, когда Харви замолчала
                if dialog_until:
                    if _speaking.is_set():
                        dialog_until = max(dialog_until, time.time() + DIALOG_TIMEOUT)
                    elif time.time() > dialog_until:
                        dialog_until = 0.0
                        log("Диалог", "окно закрыто")
                try:
                    block = _get_block(audio_q, timeout=0.1)
                except queue.Empty:
                    continue

                speaking = _speaking.is_set()
                limit = threshold * ECHO_THRESHOLD_FACTOR if speaking else threshold
                if _rms(block) <= limit:
                    pre_buffer.append(block)
                    continue

                # Без имени слушаем, только пока Харви молчит: иначе она услышит саму себя
                dialog_open = bool(dialog_until) and not speaking
                _chimes.warm()                       # пока вы говорите, готовим звук «готово» — он прозвучит без задержки
                audio = record_utterance(audio_q, pre_buffer, block, limit)
                pre_buffer.clear()
                heard_name = _wake.take()
                if audio is None:
                    continue
                # Детектор имени включён: без имени Whisper нужен только в диалоге, при «да/нет»
                # и чтобы услышать «стоп», пока Харви говорит
                if _wake.enabled and not heard_name and not (
                        (dialog_open or _pending is not None or speaking) and not _sleeping):
                    continue

                text = transcribe(audio)
                low = text.lower()
                if not low or is_noise(low):
                    continue
                log("Распознано", text)

                m = WAKE_PATTERN.search(low)
                named = m is not None or heard_name
                if m:
                    after_name = text[m.end():]
                elif heard_name:                    # имя услышала нейросеть, Whisper записал его иначе
                    after_name = _strip_name_like(text)
                else:
                    after_name = text
                body = " ".join(re.sub(r"[^\w\s]", " ", after_name.lower()).split())

                # Режим сна: слушаем только «Харви, проснись»
                if _sleeping:
                    dialog_until = 0.0
                    if named and R["wake_up"].search(low):
                        set_sleeping(False)
                        drain(audio_q)
                        if QUIET_MODE:
                            play_sound("ready")
                        else:
                            play_beep()
                            _say(f"Я {WOKE}, господин.")
                    continue

                # «Стоп», «заткнись», «хватит» — она сразу замолкает (имя можно не называть)
                if (speaking or _speaking.is_set()) and (SILENCE_RE.match(body) or STOP_RE.match(body)):
                    stop_speaking()
                    log("Система", "Речь прервана.")
                    continue

                # Ответ на «Вы уверены?» — имя называть не нужно
                if _pending is not None:
                    if time.time() > _pending["deadline"]:
                        clear_pending()
                    elif R["yes"].search(body):
                        action = _pending["action"]
                        clear_pending()
                        stop_speaking()
                        _reply([action()])
                        continue
                    elif R["no"].search(body):
                        clear_pending()
                        if QUIET_MODE:
                            play_sound("cancel")
                        else:
                            _say("Хорошо, господин, отменяю.")
                        continue

                # «Всё», «спасибо», «отбой» — закрыть окно диалога
                if (named or dialog_open) and DIALOG_END_RE.match(body):
                    if dialog_until:
                        dialog_until = 0.0
                        play_sound("cancel")
                    continue

                if named:
                    if SILENCE_RE.match(body):
                        continue
                    command = after_name.lstrip(PUNCT)
                elif dialog_open and dialog_accepts(text):
                    command = text.strip().lstrip(PUNCT)    # режим диалога: имя можно не называть
                    log("Диалог", command)
                else:
                    continue

                if speaking or _speaking.is_set():
                    stop_speaking()                  # новая команда важнее: не ждём конца прошлой фразы

                if not command:
                    ducker.duck()                    # приглушаем музыку, пока вы говорите команду
                    if WAKE_BEEP:
                        play_beep()                  # сигнал «слушаю» вместо фразы — реакция мгновенная
                    else:
                        speak_sync("Да, господин?")
                    drain(audio_q)                   # выбросить эхо собственного сигнала
                    cmd_audio = record_command(audio_q, threshold)
                    command = transcribe(cmd_audio).strip() if cmd_audio is not None else ""
                    log("Команда", command)

                if not command:
                    if QUIET_MODE:
                        play_sound("cancel")
                    else:
                        speak(f"Я ничего не услышал{END}, господин.")
                    continue
                if not handle_command(command):
                    break
                if DIALOG_MODE and not _sleeping:
                    dialog_until = time.time() + DIALOG_TIMEOUT
    finally:
        ducker.restore()
        unload_model()
        if _tray_icon is not None:
            try:
                _tray_icon.stop()
            except Exception:
                pass

if __name__ == "__main__":
    main()