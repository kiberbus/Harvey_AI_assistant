"""Общее: журнал, сборка ответа, обращение, числа прописью, скомпилированные фразы."""

from __future__ import annotations

import logging
import os
import random
import re
from logging.handlers import RotatingFileHandler

from config import (
    BASE_DIR,
    FEMALE_VOICE,
    HONORIFIC,
    LOG_ENABLED,
    LOG_FILE,
    LOG_MAX_MB,
    NAME_GROUPS,
    PIPER_VOICE,
    SHORT_REPLIES,
    SPEAK_ERRORS,
    USE_HONORIFIC,
    WAKE_WORDS,
)
from phrases import (
    APP_VOLUME_DOWN,
    APP_VOLUME_FILLER,
    APP_VOLUME_UP,
    BACK_OR_PREVIOUS,
    DRIVE,
    GOOGLE,
    MEDIA_FILLER,
    MEDIA_TARGETS,
    MEDIA_UNPAUSE,
    MEDIA_VERBS,
    NOTE_ADD,
    PHRASES,
    REMIND_CANCEL,
    REMIND_LIST,
    REMIND_VERB,
    SHORTCUTS,
    SITE_ALIASES,
    YT,
)


try:
    from piper.voice import PiperVoice
except Exception:  # без piper можно работать на Silero
    PiperVoice = None

try:
    from piper import SynthesisConfig
except Exception:  # старая версия piper - скорость речи тогда регулируем частотой воспроизведения
    SynthesisConfig = None

try:
    import psutil
except Exception:  # psutil ставится вместе с pycaw
    psutil = None

try:
    from comtypes import CLSCTX_ALL
    from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation

    HAS_PYCAW = True
except Exception:  # pycaw не установлен - громкость и приглушение будут недоступны
    HAS_PYCAW = False


END = "а" if FEMALE_VOICE else ""
WOKE = "проснулась" if FEMALE_VOICE else "проснулся"
PIPER_DIR = BASE_DIR / "piper"
PIPER_MODEL = PIPER_DIR / f"ru_RU-{PIPER_VOICE}-medium.onnx"

OWN_PID = os.getpid()
FAIL = "!"                            # префикс фразы-ошибки внутри инструментов
PUNCT = " ,.!?:;-—–…"

WAKE_PATTERN = re.compile(r"\b(" + "|".join(sorted(set(WAKE_WORDS))) + r")\b", re.IGNORECASE)


def _rx(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


R = {name: _rx(patterns) for name, patterns in PHRASES.items()}      # варианты фраз из phrases.py
NOTE_ADD_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in NOTE_ADD]
DICTATE_RE = re.compile(
    r"^(?:запиш\w*|записи|записать|напечатай|набери|введи)\b"
    # «запиши задачу…», «запиши встречу…», «запиши в календарь…» - это календарь, а не диктовка
    r"(?![\s,:—-]*(?:мне\s+)?(?:(?:в|во)\s+(?:мой\s+|мои\s+)?(?:календар|задач|список (?:задач|дел))|"
    r"(?:нов\w+\s+)?(?:задач|встреч|событи|созвон)))"
    r"[\s,:—-]*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
VERB_RE = re.compile(r"(открой|запусти|закрой|закрыть|заверши|завершить)\b")
OPEN_VERBS = r"(?:открой|запусти|включи|зайди на|зайди в)"
SEARCH_VERB_RE = re.compile(r"\b(?:найди|поищи|ищи|покажи|напиши|набери|введи|загугли)\b")

SITE_PATTERNS = tuple((site, re.compile(p)) for site, p in SITE_ALIASES.items())
SITE_MENTION_RE = re.compile(rf"\b(?:(?:на|в|во)\s+)?(?:{YT}|{GOOGLE}|интернет\w*)(?=\s|$)")
# Короткие формы: «ютуб котики», «гугл погода в лондоне», «найди рецепт борща»
SHORT_SEARCH_RE = re.compile(rf"^(?:(?:на|в|во)\s+)?(?P<site>{YT}|{GOOGLE})[\s,:—-]+(?P<query>.+)$")
# «открой в браузере калькулятор матриц», «калькулятор матриц в браузере» - поиск в Google
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
SHORTCUT_RES = tuple((action, _rx(patterns)) for action, patterns in SHORTCUTS.items())
BACK_OR_PREVIOUS_RE = re.compile(BACK_OR_PREVIOUS)
DRIVE_RE = re.compile(DRIVE)

INFO = "~"                            # префикс фразы, которую говорю даже в тихом режиме
RAW = "="                             # префикс фразы, которую говорю как есть

# Прерывание речи: «стоп», «заткнись», «хватит» ...
SILENCE_RE = re.compile(
    r"^(?:(?:ну|так|всё|все|эй)\s+)*(?:заткнись|замолчи|помолчи|молчи|тихо|хватит|достаточно|довольно|"
    r"прекрати|перестань|хорош)(?:\s+(?:болтать|говорить|уже|пожалуйста))*$"
)
STOP_RE = re.compile(r"^(?:(?:ну|так|эй)\s+)*(?:стоп|стой)(?:\s+(?:стоп|уже|пожалуйста))*$")
REPEAT_RE = re.compile(r"^(?:повтори(?:\s+пожалуйста)?|что ты (?:сказала|сказал)|ещё раз|еще раз)$")
LAST_ACTION_RE = re.compile(r"^(?:а\s+)?что (?:ты )?(?:только что |сейчас )?(?:сделала|сделал)$")
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

OPENERS = ("Слушаюсь, господин", "Хорошо, господин", "Да, господин")


_logger = logging.getLogger("harvey")


def setup_logging() -> None:
    """Пишет в harvey.log с ротацией - по нему я разбираю, что она не поняла."""
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
    """Собирает итоговую фразу из результатов инструментов, без второго запроса к ИИ."""
    for p in phrases:
        if p.startswith(RAW):                       # вопрос-подтверждение и т.п. - без «Слушаюсь, господин»
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
    """Тихий режим: (что сказать или None, какой звук или None)."""
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
    """Подставляет обращение из config.py или убирает его. В коде везде пишу "господин"."""
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


# Числа и латиница словами (Silero их не читает)
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
    """21 → «двадцать один»; fem - женский род («две минуты»); acc - «одну минуту»."""
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
    """Silero не читает цифры и латиницу - превращаем их в русские слова."""
    text = _NUM_RE.sub(_num_repl, text)
    text = re.sub(r"[A-Za-z][A-Za-z']*", lambda m: _LATIN.get(m.group().lower()) or _translit(m.group()), text)
    return text.replace("%", " процентов")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
