"""Понимание команд: разбор фраз без ИИ, инструменты, запрос к Ollama, обработка команды."""

from __future__ import annotations

import json
import ollama
import re
import time
from collections import deque
from datetime import datetime
from datetime import timedelta
from typing import Callable

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
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
    DICTATE_RE,
    END,
    FAIL,
    INFO,
    IN_BROWSER_RE,
    MEDIA_FILLER_RE,
    MEDIA_TARGET_RES,
    MEDIA_UNPAUSE_RE,
    MEDIA_VERB_RES,
    NOTE_ADD_RE,
    OPEN_VERBS,
    PUNCT,
    R,
    RAW,
    REMIND_CANCEL_RE,
    REMIND_LIST_RE,
    REMIND_VERB_RE,
    REPEAT_RE,
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
    compose,
    compose_quiet,
    log,
    parse_number,
)
from core.audio import (  # noqa: F401
    change_volume,
    mute,
    set_volume,
)
from core.speech import (  # noqa: F401
    play_sound,
    speak,
    speak_stream,
    speak_sync,
    stop_speaking,
)
from core.apps import (  # noqa: F401
    alt_tab,
    change_brightness,
    close_active,
    close_app,
    dictate,
    find_app,
    lock_pc,
    minimize_app,
    open_app,
    open_browser,
    open_folder,
    screenshot,
    set_brightness,
    show_desktop,
    window_state,
)
from core.media import (  # noqa: F401
    app_volume,
    media,
    now_playing,
)
from core.daily import (  # noqa: F401
    add_note,
    add_reminder,
    cancel_power,
    cancel_reminders,
    cancel_timers,
    clear_pending,
    currency_rate,
    do_power,
    list_reminders,
    open_notes,
    parse_duration,
    read_notes,
    request_power,
    set_timer,
    sleep_mode,
    tell_date,
    tell_datefull,
    tell_time,
    tell_weekday,
    weather,
)
from core.tray import (  # noqa: F401
    restart_self,
)
from core import calc, llm, smart, system


SYSTEM_PROMPT = f"""Ты — голосовой ассистент по имени {ASSISTANT_NAME}, управляющий компьютером с Windows 11.
Выполняй просьбы пользователя ТОЛЬКО через инструменты. {smart.ADDRESS_RULE}
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
    "restart_self": restart_self,
    "app_volume": app_volume,
    "add_reminder": add_reminder,
    "list_reminders": list_reminders,
    "cancel_reminders": cancel_reminders,
    "set_brightness": set_brightness,
    "change_brightness": change_brightness,
    "shortcut": system.shortcut,
    "open_drive": system.open_drive,
    "system_status": system.system_status,
    "gpu_status": system.gpu_status,
    "microphone": system.microphone,
    "calculate": calc.calculate,
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
    _tool("shortcut", "Нажать сочетание клавиш в активном окне: копировать, вставить, отменить, сохранить, "
                      "выделить всё, очистить поле, Enter, вкладки браузера, обновить, назад, полный экран, "
                      "окно влево/вправо.",
          {"action": {"type": "string", "enum": list(system.SHORTCUT_KEYS)}}, ["action"]),
    _tool("system_status", "Загрузка процессора и оперативной памяти.", {}, []),
    _tool("gpu_status", "Температура и загрузка видеокарты.", {}, []),
    _tool("microphone", "Включить (true) или выключить (false) микрофон.", {"state": {"type": "boolean"}}, ["state"]),
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

    # Клавиши: копировать, вставить, вкладки, окно влево (раньше «закрой X», медиа и «открой X»)
    for action, rx in SHORTCUT_RES:
        if rx.search(seg):
            return lambda a=action: execute_tool("shortcut", {"action": a})
    if BACK_OR_PREVIOUS_RE.match(seg):          # «назад»: в браузере — страница, иначе — трек
        return lambda: (execute_tool("shortcut", {"action": "back"}) if system.foreground_exe() in BROWSER_EXES
                        else execute_tool("media", {"action": "previous"}))

    # Микрофон (раньше «заглуши» — это общий звук), состояние компьютера (раньше погоды)
    if R["mic_off"].search(seg):
        return lambda: execute_tool("microphone", {"state": False})
    if R["mic_on"].search(seg):
        return lambda: execute_tool("microphone", {"state": True})
    if R["gpu_status"].search(seg):
        return lambda: execute_tool("gpu_status", {})
    if R["system_status"].search(seg):
        return lambda: execute_tool("system_status", {})

    # Диски: «диск Д», «открой диск C»
    m = DRIVE_RE.match(seg)
    if m and m.group("letter") in DRIVE_LETTERS:
        return lambda letter=DRIVE_LETTERS[m.group("letter")]: execute_tool("open_drive", {"letter": letter})

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

    # «клауд на весь экран», «разверни телеграм на весь экран»
    m = re.fullmatch(r"(?:(?:разверни|открой|сделай)\s+)?(.+?)\s+(?:на весь экран|на полный экран|во весь экран)", seg)
    if m and (m.group(1) in APP_ALIASES or find_app(m.group(1))):
        return lambda t=m.group(1): _open_maximized(t)

    # «открой музыку» — приложение для музыки (папка — «открой папку музыка»)
    if re.fullmatch(r"(?:открой|запусти)\s+(?:музыку|музыка|музыкальное приложение)", seg):
        return lambda: execute_tool("open_app", {"name": MEDIA_FALLBACK_APP["music"]})

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
    if re.fullmatch(rf"(?:{OPEN_VERBS}\s+(?:мне\s+)?)?(?:браузер|browser|в браузере)", seg):
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


def _open_maximized(name: str) -> str:
    """Показать приложение и развернуть его окно. Только что запущенное — не трогаем: окна ещё нет."""
    result = execute_tool("open_app", {"name": name})
    if result.startswith(("развернул", "показал")):
        time.sleep(0.3)
        execute_tool("window_state", {"action": "maximize"})
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
            return lambda: execute_tool("open_browser", {"site": named})
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


PRONOUN_RE = re.compile(r"^(открой|закрой|сверни|разверни|запусти)\s+(?:его|её|ее|него|неё|нее|это|их)$")


def parse_all(low: str) -> list[Callable[[], str]] | None:
    """Разбирает всю команду без ИИ. Если хоть одна часть не разобралась — None (всё уйдёт в ИИ)."""
    low = fix_hearing(low)
    if BACK_OR_PREVIOUS_RE.match(low):                  # «назад» — до медиа: в браузере это страница назад
        return [parse_local(low)]
    # «ютуб стоп» — пауза, «ютуб на 30» — громкость, а не поиск; напоминание не режем по «и»
    whole = parse_reminder(low) or parse_app_volume(low) or parse_media(low) or parse_search(low)
    if whole:
        return [whole]
    if calc.parse(low):                                 # «2 плюс 2», «5 миль в километрах», «доллар к тенге»
        return [lambda: execute_tool("calculate", {"text": low})]
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
        action = parse_local(segment)
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


# ───────────────────────── АГЕНТ (OLLAMA) ─────────────────────────
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
        msg = llm.chat(messages, tools=TOOLS).message
        if msg.tool_calls:
            _run_tools(msg.tool_calls, user_text)
        elif (msg.content or "").strip():
            _say(msg.content.strip(), user_text)
        else:
            _reply([not_understood], user_text)
        return

    tool_calls: list = []

    def texts():
        for chunk in llm.chat_stream(messages, tools=TOOLS):
            msg = chunk.message
            if msg.tool_calls:
                tool_calls.extend(msg.tool_calls)
            if not tool_calls and msg.content:
                yield msg.content

    spoken = speak_stream(texts())
    if tool_calls:
        _run_tools(tool_calls, user_text)
        return
    if not spoken:
        _reply([not_understood], user_text)
        return
    _last_reply = spoken
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
    if smart.parse(fix_hearing(low)) is not None:   # вопрос, «переведи выделенное», «что на экране»
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
        _reply([execute_tool_dictate(smart.prepare_dictation(m.group(1)))], low)
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

    # Вопросы, выделенный текст, «что на экране» — к ИИ без инструментов
    smart_action = smart.parse(fix_hearing(low))
    if smart_action:
        log("ИИ: текст", low)
        run_smart(smart_action, low)
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


def run_smart(action: Callable[[list[dict]], smart.Result], low: str) -> None:
    global _last_reply
    try:
        phrase, said = action(_history_messages())
    except Exception as e:
        log("Ошибка", f"ИИ: {e}")
        phrase, said = f"{FAIL}произошла ошибка", ""
    if phrase is not None:
        _reply([phrase], low)
    else:                                         # ответ уже прозвучал по ходу генерации
        _last_reply = said
        _remember(low, said)


def execute_tool_dictate(text: str) -> str:
    try:
        return dictate(text)
    except Exception as e:
        log("Ошибка", f"dictate: {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} записать текст"
