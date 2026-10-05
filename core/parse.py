"""Быстрый путь: разбор фраз правилами из phrases.py, без ИИ.
Каждая функция возвращает действие (lambda, которое вызовет tools.execute_tool) или None."""

from __future__ import annotations

import re
import time
from datetime import datetime
from datetime import timedelta
from typing import Callable

from config import (
    BRIGHTNESS_STEP,
    BROWSER_EXES,
    CURRENCY_HOME,
    MEDIA_FALLBACK_APP,
    VOLUME_STEP,
)
from phrases import (
    BARE_FOLDERS,
    CURRENCY_WORDS,
    DRIVE_LETTERS,
    FOLDER_ALIASES,
    GOOGLE,
    HEARING_FIXES,
    MUSIC_APP,
    YT,
)
from core.util import (  # noqa: F401
    ACTIVE_CLOSE_RE,
    APP_ALIASES,
    APP_VOLUME_DOWN_RE,
    APP_VOLUME_FILLER_RE,
    APP_VOLUME_UP_RE,
    BACK_OR_PREVIOUS_RE,
    BARE_SEARCH_RE,
    COMPLEX_MARKERS,
    DRIVE_RE,
    INFO,
    IN_BROWSER_RE,
    MEDIA_FILLER_RE,
    MEDIA_TARGET_RES,
    MEDIA_UNPAUSE_RE,
    MEDIA_VERB_RES,
    OPEN_VERBS,
    PUNCT,
    R,
    RAW,
    REMIND_CANCEL_RE,
    REMIND_LIST_RE,
    REMIND_VERB_RE,
    SEARCH_VERB_RE,
    SHORTCUT_RES,
    SHORT_SEARCH_RE,
    SILENCE_RE,
    SITE_MENTION_RE,
    SITE_PATTERNS,
    SPLIT_RE,
    STOP_RE,
    VERB_RE,
    WAKE_PATTERN,
    _TENS,
    _UNITS,
    parse_number,
)
from core.apps import (  # noqa: F401
    find_app,
)
from core.daily import (  # noqa: F401
    parse_duration,
)
from core import calc, system
from core import tools


def _currency_codes(seg: str) -> list[str]:
    if not R["currency_trigger"].search(seg):
        return []
    codes = [code for code, pattern in CURRENCY_WORDS.items() if re.search(pattern, seg) and code != CURRENCY_HOME]
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
        return lambda: tools.execute_tool("restart_self", {})

    # «Открой» без названия — переспрашиваем (ответ можно дать без «Харви», в окне диалога).
    # Голые «включи» / «запусти» сюда не доходят: это «продолжи воспроизведение» (parse_media)
    if seg == "открой":
        return lambda: f"{RAW}Что открыть?"

    # Питание компьютера — всегда с подтверждением
    if R["cancel_power"].search(seg):
        return lambda: tools.execute_tool("cancel_power", {})
    for key, action in (("shutdown", "shutdown"), ("restart", "restart"), ("pc_sleep", "sleep")):
        if R[key].search(seg):
            return lambda a=action: tools.execute_tool("request_power", {"action": a})

    # Режим сна самой помощницы
    if R["sleep_mode"].search(seg):
        return lambda: tools.execute_tool("sleep_mode", {})

    # Клавиши: копировать, вставить, вкладки, окно влево (раньше «закрой X», медиа и «открой X»)
    for action, rx in SHORTCUT_RES:
        if rx.search(seg):
            return lambda a=action: tools.execute_tool("shortcut", {"action": a})
    if BACK_OR_PREVIOUS_RE.match(seg):          # «назад»: в браузере — страница, иначе — трек
        return lambda: (tools.execute_tool("shortcut", {"action": "back"}) if system.foreground_exe() in BROWSER_EXES
                        else tools.execute_tool("media", {"action": "previous"}))

    # Микрофон (раньше «заглуши» — это общий звук), состояние компьютера (раньше погоды)
    if R["mic_off"].search(seg):
        return lambda: tools.execute_tool("microphone", {"state": False})
    if R["mic_on"].search(seg):
        return lambda: tools.execute_tool("microphone", {"state": True})
    if R["gpu_status"].search(seg):
        return lambda: tools.execute_tool("gpu_status", {})
    if R["system_status"].search(seg):
        return lambda: tools.execute_tool("system_status", {})

    # Диски: «диск Д», «открой диск C»
    m = DRIVE_RE.match(seg)
    if m and m.group("letter") in DRIVE_LETTERS:
        return lambda letter=DRIVE_LETTERS[m.group("letter")]: tools.execute_tool("open_drive", {"letter": letter})

    # Закрыть активное окно/приложение (раньше обычного «закрой X»)
    if ACTIVE_CLOSE_RE.match(seg):
        return lambda: tools.execute_tool("close_active", {"window_only": "окно" in seg})

    # Таймеры
    if R["timer_cancel"].search(seg):
        return lambda: tools.execute_tool("cancel_timers", {})
    if R["timer_set"].search(seg):
        secs = parse_duration(seg)
        if secs:
            return lambda: tools.execute_tool("set_timer", {"seconds": secs})

    # Время, дата, день недели
    for key, tool in (("datefull", "tell_datefull"), ("weekday", "tell_weekday"),
                      ("date", "tell_date"), ("time", "tell_time")):
        if R[key].search(seg):
            return lambda t=tool: tools.execute_tool(t, {})

    # Заметки, погода, курс валют
    if R["notes_open"].search(seg):
        return lambda: tools.execute_tool("open_notes", {})
    if R["notes_read"].search(seg):
        return lambda: tools.execute_tool("read_notes", {})
    if R["weather"].search(seg):
        return lambda: tools.execute_tool("weather", {})
    codes = _currency_codes(seg)
    if codes:
        return lambda: tools.execute_tool("currency_rate", {"codes": codes})

    # Окна и система
    if R["show_desktop"].search(seg):
        return lambda: tools.execute_tool("show_desktop", {})
    if R["minimize"].search(seg):
        return lambda: tools.execute_tool("window_state", {"action": "minimize"})
    if R["maximize"].search(seg):
        return lambda: tools.execute_tool("window_state", {"action": "maximize"})
    if R["alt_tab"].search(seg):
        return lambda: tools.execute_tool("alt_tab", {})
    if R["screenshot"].search(seg):
        return lambda: tools.execute_tool("screenshot", {})
    if R["lock"].search(seg):
        return lambda: tools.execute_tool("lock_pc", {})

    # Свернуть / развернуть конкретное приложение: «сверни яндекс музыку», «разверни телеграм»
    m = re.match(r"(?:сверни|свернуть|спрячь)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: tools.execute_tool("minimize_app", {"name": target})
    m = re.match(r"(?:разверни|развернуть)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m and (m.group(1).strip() in APP_ALIASES or find_app(m.group(1).strip())):
        target = m.group(1).strip()
        return lambda: tools.execute_tool("open_app", {"name": target})      # уже запущено — развернёт окно

    # «клауд на весь экран», «разверни телеграм на весь экран»
    m = re.fullmatch(r"(?:(?:разверни|открой|сделай)\s+)?(.+?)\s+(?:на весь экран|на полный экран|во весь экран)", seg)
    if m and (m.group(1) in APP_ALIASES or find_app(m.group(1))):
        return lambda t=m.group(1): _open_maximized(t)

    # «открой музыку» — приложение для музыки (папка — «открой папку музыка»)
    if re.fullmatch(r"(?:открой|запусти)\s+(?:музыку|музыка|музыкальное приложение)", seg):
        return lambda: tools.execute_tool("play_app", {"name": MEDIA_FALLBACK_APP["music"]})

    # Закрыть приложение
    m = re.match(r"(?:закрой|закрыть|заверши|завершить)\s+(?:приложение\s+|программу\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: tools.execute_tool("close_app", {"name": target})

    if R["now_playing"].search(seg):
        return lambda: tools.execute_tool("now_playing", {})

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
        return lambda: tools.execute_tool("mute", {"state": True})
    if R["unmute"].search(seg):
        return lambda: tools.execute_tool("mute", {"state": False})
    if R["volume_up"].search(seg):
        return lambda: tools.execute_tool("change_volume", {"delta": num or VOLUME_STEP})
    if R["volume_down"].search(seg):
        return lambda: tools.execute_tool("change_volume", {"delta": -(num or VOLUME_STEP)})
    if R["volume_set"].search(seg):
        if num is not None:
            return lambda: tools.execute_tool("set_volume", {"level": num})
        if R["volume_max"].search(seg):
            return lambda: tools.execute_tool("set_volume", {"level": 100})

    # Яркость
    if R["bright_up"].search(seg):
        return lambda: tools.execute_tool("change_brightness", {"delta": num or BRIGHTNESS_STEP})
    if R["bright_down"].search(seg):
        return lambda: tools.execute_tool("change_brightness", {"delta": -(num or BRIGHTNESS_STEP)})
    if R["bright_set"].search(seg):
        if num is not None:
            return lambda: tools.execute_tool("set_brightness", {"level": num})
        if R["bright_max"].search(seg):
            return lambda: tools.execute_tool("set_brightness", {"level": 100})
        if R["bright_min"].search(seg):
            return lambda: tools.execute_tool("set_brightness", {"level": 10})

    # Медиа (порядок важен: «сними с паузы» не должно срабатывать как «пауза»)
    if R["media_next"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "next"})
    if R["media_prev"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "previous"})
    if R["media_play"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "play"})
    if R["media_pause"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "pause"})

    # Пустой браузер
    if re.fullmatch(rf"(?:{OPEN_VERBS}\s+(?:мне\s+)?)?(?:браузер|browser|в браузере)", seg):
        return lambda: tools.execute_tool("open_browser", {})

    # Короткие формы без глагола: «музыка», «ютуб», «яндекс музыка», «телеграм»
    if re.fullmatch(r"музык[аиу]|музычку|песню|песенку", seg):
        return lambda: tools.execute_tool("media", {"action": "play", "target": "music"})
    for site, pattern in SITE_PATTERNS:
        if pattern.fullmatch(seg):
            return lambda s=site: tools.execute_tool("open_browser", {"site": s})
    if seg in APP_ALIASES:
        return lambda: tools.execute_tool("open_app", {"name": seg})
    if seg in BARE_FOLDERS:
        return lambda: tools.execute_tool("open_folder", {"name": FOLDER_ALIASES[seg]})

    # Сайты: «открой ютуб», «включи яндекс музыку»
    m = re.match(rf"{OPEN_VERBS}\s+(?:сайт\s+)?(.+)$", seg)
    if m and len(m.group(1).split()) <= 3:
        site = detect_site(m.group(1))
        if site:
            return lambda: tools.execute_tool("open_browser", {"site": site})

    # Папки: «открой загрузки», «открой папку музыка»
    m = re.match(r"(?:открой|покажи|запусти)\s+(папку\s+)?(.+)$", seg)
    if m:
        alias = m.group(2).strip()
        folder = FOLDER_ALIASES.get(alias)
        if folder and (m.group(1) or alias in BARE_FOLDERS):
            return lambda: tools.execute_tool("open_folder", {"name": folder})

    # Открыть приложение: «открой телеграм», «включи Claude» (музыку, звук, видео «включи» разобрало раньше)
    m = re.match(r"(?:открой|запусти|открыть|запустить|включи|вруби)\s+(.+)", seg)
    if m and (m.group(1).strip() in APP_ALIASES or not any(marker in seg for marker in COMPLEX_MARKERS)):
        target = m.group(1).strip()
        if find_app(target):
            return lambda: tools.execute_tool("open_app", {"name": target})

    return None


def _open_maximized(name: str) -> str:
    """Показать приложение и развернуть его окно. Только что запущенное — не трогаем: окна ещё нет."""
    result = tools.execute_tool("open_app", {"name": name})
    if result.startswith(("развернул", "показал")):
        time.sleep(0.3)
        tools.execute_tool("window_state", {"action": "maximize"})
    return result


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
        return lambda: tools.execute_tool("app_volume", {"target": target, "delta": delta})
    return lambda: tools.execute_tool("app_volume", {"target": target, "level": num})


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
        return lambda: tools.execute_tool("cancel_reminders", {})
    if REMIND_LIST_RE.search(low):
        return lambda: tools.execute_tool("list_reminders", {})
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
    return lambda: tools.execute_tool("add_reminder", {"at": at, "text": message})


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
    return lambda: tools.execute_tool("media", {"action": action, "target": target})


def detect_site(text: str) -> str | None:
    for site, pattern in SITE_PATTERNS:
        if pattern.search(text):
            return site
    return None


BROWSER_SITE_RE = re.compile(
    rf"(?:{OPEN_VERBS}\s+)?(?:в\s+)?(?:браузер\w*|browser)[\s,]+(?:{OPEN_VERBS}\s+)?(?P<rest>.+)"
    rf"|(?:{OPEN_VERBS}\s+)?(?P<rest2>.+?)[\s,]+(?:в\s+)?(?:браузер\w*|browser)")


def exact_site(text: str) -> str | None:
    """Текст целиком — название сайта: «переводчик», «google collab»."""
    text = text.strip(PUNCT)
    for site, pattern in SITE_PATTERNS:
        if pattern.fullmatch(text):
            return site
    return None


def parse_search(low: str) -> Callable[[], str] | None:
    """«найди котиков на ютубе», «открой ютуб и напиши в поиске котики», «загугли ...»,
    короткие формы: «ютуб котики», «гугл погода в лондоне», «найди рецепт борща»."""
    m = BROWSER_SITE_RE.fullmatch(low)           # «браузер, переводчик», «открой в браузере google collab»
    if m:
        named = exact_site(m.group("rest") or m.group("rest2"))
        if named:
            return lambda: tools.execute_tool("open_browser", {"site": named})
    site = detect_site(low)
    if site not in ("youtube", "google"):
        m = IN_BROWSER_RE.match(low)                  # «открой в браузере астана хаб хакатон» — ищем в Google
        if m:
            query = (m.group("q1") or m.group("q2") or "").strip(PUNCT)
            if query and query not in ("открой", "найди", "поищи"):
                return lambda: tools.execute_tool("open_browser", {"site": "google", "query": query})
    if site is None:
        m = BARE_SEARCH_RE.match(low)                 # «найди X» без сайта — ищем в Google
        if m and m.group("query").strip(PUNCT):
            query = m.group("query").strip(PUNCT)
            return lambda: tools.execute_tool("open_browser", {"site": "google", "query": query})
        return None
    if site not in ("youtube", "google"):
        return None
    m = SEARCH_VERB_RE.search(low)
    if not m:
        short = SHORT_SEARCH_RE.match(low)            # «ютуб котики»
        if short:
            query = short.group("query").strip(PUNCT)
            if query and not re.match(rf"{OPEN_VERBS}\b", query) and not SEARCH_VERB_RE.match(query):
                return lambda: tools.execute_tool("open_browser", {"site": site, "query": query})
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
    return lambda: tools.execute_tool("open_browser", {"site": site, "query": query})


_HEARING_FIXES = [(re.compile(pattern), replacement) for pattern, replacement in HEARING_FIXES]


def fix_hearing(low: str) -> str:
    """Исправляет типичные ошибки Whisper из phrases.HEARING_FIXES: «напомнив 6» → «напомни в 6»."""
    for pattern, replacement in _HEARING_FIXES:
        low = pattern.sub(replacement, low)
    return " ".join(low.split())


# Команды, после которых продолжения не бывает: их можно выполнять после короткой паузы
_QUICK_KEYS = ("time", "date", "weekday", "datefull", "sleep_mode", "now_playing", "mute", "unmute",
               "media_next", "media_prev", "media_pause", "media_play",
               "volume_up", "volume_down", "bright_up", "bright_down")


def is_quick_command(text: str, need_name: bool = True, pending: bool = False) -> bool:
    """«Харви, пауза», «Харви, громкость 30», «Харви, сколько времени» — законченная короткая команда.
    Поиск, напоминания, диктовка, открытие приложений и вопросы к ИИ сюда не попадают: у них
    после паузы часто идёт продолжение («найди рецепт… борща»)."""
    low = text.lower()
    m = WAKE_PATTERN.search(low)
    if m:
        low = low[m.end():]
    elif need_name:
        return False
    body = fix_hearing(" ".join(low.strip(PUNCT).split()))
    if not body or body.endswith((" и", " а", " потом", " затем")):
        return False
    if pending and (R["yes"].search(body) or R["no"].search(body)):
        return True
    if SILENCE_RE.match(body) or STOP_RE.match(body):
        return True
    if parse_all(body) is None:
        return False
    if parse_media(body) or parse_app_volume(body):
        return True
    if any(rx.search(body) for _, rx in SHORTCUT_RES):
        return True
    if (R["volume_set"].search(body) or R["bright_set"].search(body)) and parse_number(body) is not None:
        return True
    return any(R[key].search(body) for key in _QUICK_KEYS)


MUSIC_APP_RE = re.compile(MUSIC_APP)
PRONOUN_RE = re.compile(r"^(открой|закрой|сверни|разверни|запусти)\s+(?:его|её|ее|него|неё|нее|это|их)$")


CLOSE_VERBS = ("закрой", "закрыть", "заверши", "завершить")


def _bare_name(segment: str) -> bool:
    """Сегмент — просто название без глагола: приложение, сайт, папка, «браузер»."""
    return (segment in APP_ALIASES or segment in FOLDER_ALIASES or exact_site(segment) is not None
            or segment in ("браузер", "browser"))


def parse_all(low: str) -> list[Callable[[], str]] | None:
    """Разбирает всю команду без ИИ. Если хоть одна часть не разобралась — None (всё уйдёт в ИИ)."""
    low = fix_hearing(low)
    if BACK_OR_PREVIOUS_RE.match(low) or any(rx.search(low) for _, rx in SHORTCUT_RES):
        return [parse_local(low)]                       # «назад», «назад в браузере» — до медиа и поиска
    if MUSIC_APP_RE.fullmatch(low):                     # «запусти яндекс музыку» — открыть и сразу включить
        return [lambda: tools.execute_tool("play_app", {"name": MEDIA_FALLBACK_APP["music"]})]
    # «ютуб стоп» — пауза, «ютуб на 30» — громкость, а не поиск; напоминание не режем по «и»
    whole = parse_reminder(low) or parse_app_volume(low) or parse_media(low) or parse_search(low)
    if whole:
        return [whole]
    if calc.parse(low):                                 # «2 плюс 2», «5 миль в километрах», «доллар к тенге»
        return [lambda: tools.execute_tool("calculate", {"text": low})]
    if R["timer_set"].search(low) or R["datefull"].search(low) or _currency_codes(low):   # их нельзя резать по «и»
        whole = parse_local(low)
        if whole:
            return [whole]
    actions: list[Callable[[], str]] = []
    last_verb = last_target = ""
    for segment in SPLIT_RE.split(low):
        segment = segment.strip(PUNCT)
        if not segment:
            continue
        pronoun = PRONOUN_RE.match(segment)             # «открой телеграм, а потом закрой его»
        if pronoun and last_target:
            segment = f"{pronoun.group(1)} {last_target}"
        action = None
        if last_verb in CLOSE_VERBS and _bare_name(segment):   # «закрой браузер и яндекс музыку» — закрыть обе
            action = parse_local(f"{last_verb} {segment}")
        action = action or parse_local(segment)
        if action is None and last_verb:                # «открой телеграм и браузер» → «открой браузер»
            action = parse_local(f"{last_verb} {segment}")
        if action is None:
            return None
        actions.append(action)
        verb = VERB_RE.match(segment)
        if verb:
            last_verb = verb.group(1)
            last_target = segment[verb.end():].strip(PUNCT) or last_target
    return actions or None
