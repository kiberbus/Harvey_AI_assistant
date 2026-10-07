"""Быстрый путь без ИИ: разбор фраз по правилам из phrases.py.
Каждая функция возвращает действие (lambda с tools.execute_tool) или None."""

from __future__ import annotations

import re
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Callable

from config import (
    BRIGHTNESS_STEP,
    CURRENCY_HOME,
    MEDIA_FALLBACK_APP,
    VOLUME_STEP,
)
from phrases import (
    AUDIO_OUTPUT,
    AUDIO_OUTPUT_SHORT,
    BARE_FOLDERS,
    CAL_AGENDA,
    CAL_COLORS,
    CAL_DAY,
    CAL_EVENT_ADD,
    CAL_EVENT_DELETE,
    CAL_NEXT,
    CURRENCY_WORDS,
    DRIVE_LETTERS,
    FOLDER_ALIASES,
    FORWARD_OR_NEXT,
    GOOGLE,
    HEARING_FIXES,
    MUSIC_APP,
    SIDE_BY_SIDE,
    TAB_CLOSE_NAMED,
    TAB_CONTEXT_NEXT,
    TAB_CONTEXT_PREV,
    TAB_SWITCH_NAMED,
    TAB_SWITCH_SITE,
    TASK_ADD,
    TASK_DONE,
    TASK_DUE_WORDS,
    TASK_LIST,
    TASK_NO_DUE,
    WINDOW_PLACE,
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
from core import browser, calc, system  # noqa: F401
from core import tools
from core.audio import (  # noqa: F401
    device_alias,
)


def _currency_codes(seg: str) -> list[str]:
    if not R["currency_trigger"].search(seg):
        return []
    codes = [code for code, pattern in CURRENCY_WORDS.items() if re.search(pattern, seg) and code != CURRENCY_HOME]
    if not codes and "валют" in seg:
        codes = ["USD", "EUR", "RUB"]
    return codes


WINDOW_PLACE_RE = re.compile(WINDOW_PLACE)
SIDE_BY_SIDE_RES = [re.compile(p) for p in SIDE_BY_SIDE]


def _known_app(name: str) -> bool:
    name = name.strip()
    return name in APP_ALIASES or find_app(name) is not None


def parse_side_by_side(low: str) -> Callable[[], str] | None:
    """«Рядом хром и телеграм» - одна команда, хотя в ней есть «и»."""
    for rx in SIDE_BY_SIDE_RES:
        m = rx.match(low)
        if m and _known_app(m.group("a")) and _known_app(m.group("b")):
            return lambda a=m.group("a"), b=m.group("b"): tools.execute_tool("side_by_side", {"left": a, "right": b})
    return None


def parse_local(segment: str) -> Callable[[], str] | None:
    """Разбирает одну простую команду. None - нужен ИИ."""
    seg = segment.strip(PUNCT)
    if not seg:
        return None
    num = parse_number(seg)

    if R["self_restart"].search(seg):
        return lambda: tools.execute_tool("restart_self", {})

    # "Открой" без названия - переспрашиваю, ответить можно без имени
    # Голые "включи"/"запусти" сюда не доходят, это play в parse_media
    if seg == "открой":
        return lambda: f"{RAW}Что открыть?"

    # Питание - всегда с подтверждением
    if R["cancel_power"].search(seg):
        return lambda: tools.execute_tool("cancel_power", {})
    for key, action in (("shutdown", "shutdown"), ("restart", "restart"), ("pc_sleep", "sleep")):
        if R[key].search(seg):
            return lambda a=action: tools.execute_tool("request_power", {"action": a})

    # Сон самой Харви
    if R["sleep_mode"].search(seg):
        return lambda: tools.execute_tool("sleep_mode", {})

    # Игровой режим - раньше «включи X» (приложение) и медиа. «Выключи» раньше: «игровой режим» есть в обоих
    if R["game_mode_off"].search(seg):
        return lambda: tools.execute_tool("game_mode", {"state": False})
    if R["game_mode_on"].search(seg):
        return lambda: tools.execute_tool("game_mode", {"state": True})

    # Лайки в Яндекс Музыке раньше клавиш и медиа: «сохрани эту песню» - не Ctrl+S.
    # Дизлайк и «убери лайк» раньше лайка, в них тоже есть «лайк»
    for key, action in (("track_dislike", "dislike"), ("track_unlike", "unlike"), ("track_like", "like")):
        if R[key].search(seg):
            return lambda a=action: tools.execute_tool("rate_track", {"action": a})

    # Отмена раньше клавиш и «открой X»: «отмени» - не всегда Ctrl+Z, «открой обратно» - не приложение
    for key, kind in (("undo_volume", "volume"), ("undo_brightness", "brightness"), ("undo_close", "close"),
                      ("undo_text", "text")):
        if R[key].search(seg):
            return lambda k=kind: tools.execute_tool("undo", {"kind": k})
    if R["undo_last"].search(seg):
        return lambda: tools.execute_tool("undo", {})

    # Вкладки и голые «назад»/«вперёд» - раньше клавиш, «закрой X» и медиа
    tab_action = parse_browser(seg)
    if tab_action:
        return tab_action

    # Клавиши. Должны идти раньше "закрой X", медиа и "открой X"
    for action, rx in SHORTCUT_RES:
        if rx.search(seg):
            return lambda a=action: tools.execute_tool("shortcut", {"action": a})

    output_action = parse_audio_output(seg)     # «включи звук в наушниках» - не «включи звук»
    if output_action:
        return output_action

    # Микрофон раньше "заглуши" (это общий звук), состояние ПК раньше погоды
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

    # Недавние файлы - раньше «открой X»: «открой последний документ» не приложение
    if R["recent_list"].search(seg):
        return lambda: tools.execute_tool("open_recent", {"show_all": True})
    if R["recent_file"].search(seg):
        return lambda: tools.execute_tool("open_recent", {})

    # Закрыть активное окно - раньше обычного "закрой X"
    if ACTIVE_CLOSE_RE.match(seg):
        return lambda: tools.execute_tool("close_active", {"window_only": "окно" in seg})

    if R["timer_cancel"].search(seg):
        return lambda: tools.execute_tool("cancel_timers", {})
    if R["timer_set"].search(seg):
        secs = parse_duration(seg)
        if secs:
            return lambda: tools.execute_tool("set_timer", {"seconds": secs})

    for key, tool in (("datefull", "tell_datefull"), ("weekday", "tell_weekday"),
                      ("date", "tell_date"), ("time", "tell_time")):
        if R[key].search(seg):
            return lambda t=tool: tools.execute_tool(t, {})

    if R["notes_open"].search(seg):
        return lambda: tools.execute_tool("open_notes", {})
    if R["notes_read"].search(seg):
        return lambda: tools.execute_tool("read_notes", {})
    if R["weather"].search(seg):
        return lambda: tools.execute_tool("weather", {})
    codes = _currency_codes(seg)
    if codes:
        return lambda: tools.execute_tool("currency_rate", {"codes": codes})

    if R["show_desktop"].search(seg):
        return lambda: tools.execute_tool("show_desktop", {})
    if R["restore_windows"].search(seg):
        return lambda: tools.execute_tool("restore_windows", {})
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

    # «Телеграм влево», «хром на второй монитор» (без названия - «окно влево» - это клавиши выше)
    m = WINDOW_PLACE_RE.match(seg)
    if m and _known_app(m.group("name")):
        position = next(k for k in ("left", "right", "monitor") if m.group(k))
        return lambda n=m.group("name"), p=position: tools.execute_tool("arrange_window", {"position": p, "name": n})

    # Свернуть / развернуть приложение
    m = re.match(r"(?:сверни|свернуть|спрячь)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: tools.execute_tool("minimize_app", {"name": target})
    # «Разверни» - как кнопка «Развернуть» в Windows: на весь экран. Из лога: после «telegram и браузер рядом»
    # «разверни браузер» только показывал окно на половине экрана
    m = re.match(r"(?:разверни|развернуть)\s+(?:приложение\s+|программу\s+|окно\s+)?(.+)$", seg)
    if m and (m.group(1).strip() in APP_ALIASES or find_app(m.group(1).strip())):
        return lambda t=m.group(1).strip(): _open_maximized(t)
    m = re.match(r"верни\s+(.+)$", seg)       # «верни стим» после «сверни стим» - показать окно как было
    if m and m.group(1).strip() in APP_ALIASES:
        return lambda t=m.group(1).strip(): tools.execute_tool("open_app", {"name": t})

    # «Браузер, полный экран», «браузер вверх» (как Win+↑) - из лога, уходило в ИИ
    m = re.fullmatch(r"(?:(?:разверни|открой|сделай)\s+)?(.+?),?\s+(?:на весь экран|на полный экран|во весь экран|"
                     r"в полный экран|полный экран|вверх|наверх)", seg)
    if m and (m.group(1) in APP_ALIASES or find_app(m.group(1))):
        return lambda t=m.group(1): _open_maximized(t)

    # "открой музыку" - приложение (папка - "открой папку музыка")
    if re.fullmatch(r"(?:открой|запусти)\s+(?:музыку|музыка|музыкальное приложение)", seg):
        return lambda: tools.execute_tool("play_app", {"name": MEDIA_FALLBACK_APP["music"]})

    m = re.match(r"(?:закрой|закрыть|заверши|завершить)\s+(?:приложение\s+|программу\s+)?(.+)$", seg)
    if m:
        target = m.group(1).strip()
        return lambda: tools.execute_tool("close_app", {"name": target})

    if R["now_playing"].search(seg):
        return lambda: tools.execute_tool("now_playing", {})

    volume_action = parse_app_volume(seg)
    if volume_action:
        return volume_action

    media_action = parse_media(seg)
    if media_action:
        return media_action

    search = parse_search(seg)
    if search:
        return search

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

    # Медиа. Порядок важен: "сними с паузы" не должно стать паузой
    if R["media_next"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "next"})
    if R["media_prev"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "previous"})
    if R["media_play"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "play"})
    if R["media_pause"].search(seg):
        return lambda: tools.execute_tool("media", {"action": "pause"})

    if re.fullmatch(rf"(?:{OPEN_VERBS}\s+(?:мне\s+)?)?(?:браузер|browser|в браузере)", seg):
        return lambda: tools.execute_tool("open_browser", {})

    # Короткие формы без глагола: "ютуб", "телеграм"
    if re.fullmatch(r"музык[аиу]|музычку|песню|песенку", seg):
        return lambda: tools.execute_tool("media", {"action": "play", "target": "music"})
    for site, pattern in SITE_PATTERNS:
        if pattern.fullmatch(seg):
            return lambda s=site: tools.execute_tool("open_browser", {"site": s})
    if seg in APP_ALIASES:
        return lambda: tools.execute_tool("open_app", {"name": seg})
    if seg in BARE_FOLDERS:
        return lambda: tools.execute_tool("open_folder", {"name": FOLDER_ALIASES[seg]})

    m = re.match(rf"{OPEN_VERBS}\s+(?:сайт\s+)?(.+)$", seg)
    if m and len(m.group(1).split()) <= 3:
        site = detect_site(m.group(1))
        if site:
            return lambda: tools.execute_tool("open_browser", {"site": site})

    m = re.match(r"(?:открой|покажи|запусти)\s+(папку\s+)?(.+)$", seg)
    if m:
        alias = m.group(2).strip()
        folder = FOLDER_ALIASES.get(alias)
        if folder and (m.group(1) or alias in BARE_FOLDERS):
            return lambda: tools.execute_tool("open_folder", {"name": folder})

    # Открыть приложение. Музыку, звук и видео "включи" уже разобрало выше
    m = re.match(r"(?:открой|запусти|открыть|запустить|включи|вруби)\s+(.+)", seg)
    if m and (m.group(1).strip() in APP_ALIASES or not any(marker in seg for marker in COMPLEX_MARKERS)):
        target = m.group(1).strip()
        if find_app(target):
            return lambda: tools.execute_tool("open_app", {"name": target})

    return None


def _open_maximized(name: str) -> str:
    """Показывает и разворачивает окно. Только что запущенное не трогаю - окна ещё нет."""
    result = tools.execute_tool("open_app", {"name": name})
    if result.startswith(("развернул", "показал")):
        time.sleep(0.3)
        tools.execute_tool("window_state", {"action": "maximize"})
    return result


_NUMBER_WORD_RE = re.compile(r"\b(?:" + "|".join(sorted({*_UNITS, *_TENS, "сто"}, key=len, reverse=True)) + r")\b")


def _volume_target(seg: str) -> tuple[str | None, str]:
    """Чью громкость менять (music / youtube / video / app:имя) и остаток фразы."""
    for name, rx in MEDIA_TARGET_RES:
        if rx.search(seg):
            return name, rx.sub(" ", seg)
    for alias in sorted(APP_ALIASES, key=len, reverse=True):
        rx = re.compile(rf"\b{re.escape(alias)}\w*" if len(alias) >= 4 else rf"\b{re.escape(alias)}\b")
        if rx.search(seg):
            return "app:" + alias, rx.sub(" ", seg)
    return None, seg


AUDIO_OUTPUT_RE = re.compile(AUDIO_OUTPUT)
AUDIO_OUTPUT_SHORT_RE = re.compile(AUDIO_OUTPUT_SHORT)
_NOT_DEVICE_RE = re.compile(r"\d|процент|громкост|максимум|полную|минимум")   # «переключи звук на 50»


def parse_audio_output(seg: str) -> Callable[[], str] | None:
    """«Переключи звук на наушники», «переключи звук» (по кругу), «куда идёт звук».
    Проверяется раньше медиа («переключи» - следующий трек) и «включи звук»."""
    seg = seg.strip(PUNCT)
    if R["audio_output_next"].search(seg):
        return lambda: tools.execute_tool("audio_output", {})
    if R["audio_output_which"].search(seg):
        return lambda: tools.execute_tool("audio_output_info", {})
    m = AUDIO_OUTPUT_RE.match(seg)
    if m and (device_alias(m.group("target")) or m.group("verb") and not _NOT_DEVICE_RE.search(m.group("target"))):
        return lambda t=m.group("target"): tools.execute_tool("audio_output", {"target": t})
    m = AUDIO_OUTPUT_SHORT_RE.match(seg)
    if m and device_alias(m.group("target")):
        return lambda t=m.group("target"): tools.execute_tool("audio_output", {"target": t})
    return None


def _whole_audio_output(low: str) -> Callable[[], str] | None:
    """Целиком - только без «и»: «переключи звук на наушники и громкость 30» режется на части,
    иначе громкость попала бы в название устройства."""
    return None if SPLIT_RE.search(low) else parse_audio_output(low)


def parse_app_volume(seg: str) -> Callable[[], str] | None:
    """"музыку тише", "ютуб на 30", "громкость телеграма 50"."""
    seg = seg.strip(PUNCT)
    target, rest = _volume_target(seg)
    if target is None:
        return None
    up, down = APP_VOLUME_UP_RE.search(rest), APP_VOLUME_DOWN_RE.search(rest)
    num = parse_number(rest)
    explicit = re.search(r"громкост|\bзвук|\bна\s+\S|процент|%", rest)   # "ютуб 30" без "на" - это поиск
    if up and down or not (up or down or (num is not None and explicit)):
        return None
    leftover = APP_VOLUME_FILLER_RE.sub(" ", _NUMBER_WORD_RE.sub(" ", re.sub(r"\d+", " ", rest)))
    leftover = APP_VOLUME_DOWN_RE.sub(" ", APP_VOLUME_UP_RE.sub(" ", leftover))
    if leftover.strip(PUNCT + " "):
        return None                       # лишние слова - не это правило
    if up or down:
        delta = (num or VOLUME_STEP) * (1 if up else -1)
        return lambda: tools.execute_tool("app_volume", {"target": target, "delta": delta})
    return lambda: tools.execute_tool("app_volume", {"target": target, "level": num})


def _numbers_to_digits(text: str) -> str:
    """Числа словами для времени: "шесть вечера" -> "6 вечера"."""
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
# Время суток: «18:00», «18.30», «18 30», «6 часов 15 минут», «3 часа дня»
_CLOCK = (r"(\d{1,2})(?:\s*[:.]\s*(\d{2})|\s+(\d{2})(?=\s|$))?(?:\s*час\w*)?"
          r"(?:\s*(\d{1,2})\s*минут\w*)?(?:\s+(утра|дня|вечера|ночи))?")
_REMIND_AT_RE = re.compile(r"\b(?:в|на|к)\s+" + _CLOCK)
_REMIND_NOON_RE = re.compile(r"\b(?:в|к)\s+(полдень|полночь)\b")
_REMIND_DAY_RE = re.compile(r"\b(сегодня|завтра|послезавтра)\b")


def parse_reminder(low: str) -> Callable[[], str] | None:
    """"напомни в 18:00 позвонить маме", "напомни через 20 минут выключить плиту"."""
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
        clock = _clock_value(m) if m else None
        if clock:
            hour, minute = clock
            moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=shift or 0)
            if shift is None and moment <= now:           # "в 9", а уже 10 - значит завтра
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


def _clock_value(m: re.Match, guess_pm: bool = False) -> tuple[int, int] | None:
    """Час и минута из _REMIND_NOON_RE / _CLOCK. guess_pm: «встреча в 3» - это 15:00, а не ночь."""
    if m.re is _REMIND_NOON_RE:
        return (12 if m.group(1) == "полдень" else 0), 0
    groups = m.groups()[-5:]                     # группы _CLOCK всегда последние
    hour, minute, part = int(groups[0]), int(groups[1] or groups[2] or groups[3] or 0), groups[4]
    if part in ("дня", "вечера") and hour < 12:
        hour += 12
    elif part in ("ночи", "утра") and hour == 12:
        hour = 0
    elif part is None and guess_pm and 1 <= hour <= 6:
        hour += 12
    return (hour, minute) if hour <= 23 and minute <= 59 else None


# Google Календарь и Google Задачи.
# Из фразы по очереди вынимаю длительность («на 2 часа»), день («в пятницу»), время («в 15:00»)
# и цвет («красным»); что осталось - название. Длительность раньше времени: «на 2 часа» - не 2 ночи.

def _any(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_CAL_DAY_RE = re.compile(r"(?:\b(?:на|в|во|до|к|ко|с|со)\s+)?\b(?P<day>" + CAL_DAY + r")\b")
# «в 15:00», а промежуток можно и без предлога: «красным цветом 17.00 до 17.30» (из лога)
_CAL_AT_RE = re.compile(r"(?:\b(?:в|во|на|к|до|с|со)\s+|(?<![\d.:])(?=\d{1,2}[:.]\d{2}\s*(?:до|-)\s*\d))" + _CLOCK)
_CAL_UNTIL_RE = re.compile(r"\s*(?:до|по|-)\s*" + _CLOCK)        # «с 15 до 17» - конец встречи
_CAL_DURATION_RE = re.compile(
    r"\b(?:на|длительностью|продолжительностью)\s+(?P<d>полчаса|полтора\s+часа|час|"
    r"\d+\s*(?:час\w*|минут\w*)(?:\s*(?:и\s+)?\d+\s*минут\w*)?)(?!\s*(?:утра|дня|вечера|ночи|\d))")
_CAL_ALL_DAY_RE = re.compile(r"\b(?:на\s+)?(?:весь|целый)\s+день\b")
_CAL_IN_CALENDAR_RE = re.compile(r"[\s,]*\b(?:в|во)\s+(?:мой\s+)?календар\w*")
# «пометь её красным цветом» - глагол тоже убираю, иначе он попадёт в название
_CAL_COLOR_RES = [(key, re.compile(r"(?:\b(?:пометь|отметь|выдели|покрась|сделай)\s+(?:(?:ее|его)\s+)?)?"
                                   r"(?:\b(?:в\s+)?цвет\w*\s+)?\b" + stem + r"(?:\s+цвет\w*)?"))
                  for key, stem in CAL_COLORS.items()]
_CAL_AGENDA_RE = _any(CAL_AGENDA)
_CAL_NEXT_RE = _any(CAL_NEXT)
_CAL_EVENT_ADD_RE = re.compile(CAL_EVENT_ADD)
_CAL_EVENT_DELETE_RE = re.compile(CAL_EVENT_DELETE)
_TASK_ADD_RES = [re.compile(p) for p in TASK_ADD]
_TASK_DONE_RES = [re.compile(p) for p in TASK_DONE]
_TASK_LIST_RE = _any(TASK_LIST)
_TASK_NO_DUE_RE = re.compile(TASK_NO_DUE)
_TASK_DUE_EDGE_RE = re.compile(r"^(?:" + TASK_DUE_WORDS + r")(?:\s+|$)|(?:^|\s+)(?:" + TASK_DUE_WORDS + r")$")
_MONTH_STEMS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр",
                "ноябр", "декабр")             # «март» раньше «ма»: «марта» - не май
_WEEKDAY_STEMS = ("понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен")
_EVENT_NOUNS = {"встреч": "Встреча", "созвон": "Созвон", "событи": "Событие", "мероприяти": "Мероприятие"}


def _cal_date(word: str, today: date) -> date | None:
    """«завтра», «следующую пятницу», «7 октября», «07.10.26» -> дата. Прошедшее число без года -
    следующего года."""
    shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}.get(word)
    if shift is not None:
        return today + timedelta(days=shift)
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b", word)
    if m:
        year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        try:
            return date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:                     # «31.09.26»
            return None
    m = re.match(r"(\d{1,2})\D*?\s+(\w+)$", word)
    if m:
        month = next(i + 1 for i, stem in enumerate(_MONTH_STEMS) if m.group(2).startswith(stem))
        try:
            found = date(today.year, month, int(m.group(1)))
        except ValueError:                     # «31 сентября»
            return None
        return found if found >= today else found.replace(year=today.year + 1)
    weekday = next(i for i, stem in enumerate(_WEEKDAY_STEMS) if stem in word)
    if "следующ" in word:                      # «в следующую пятницу» - на следующей неделе
        return today + timedelta(days=7 - today.weekday() + weekday)
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _cut(text: str, m: re.Match) -> str:
    """Убираю найденное, не сдвигая остальное: позиции в text и в его копии без «ё» совпадают."""
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def _cal_when(text: str) -> dict:
    """{"day": date | None, "clock": (ч, м) | None, "minutes": длительность | None,
    "all_day": bool, "color": ключ | None, "rest": остаток фразы}."""
    text = _numbers_to_digits(text)
    norm = text.replace("ё", "е")
    info: dict = {"day": None, "clock": None, "minutes": None, "all_day": False, "color": None}

    def cut(m: re.Match) -> None:
        nonlocal text, norm
        text, norm = _cut(text, m), _cut(norm, m)

    for m in _CAL_DURATION_RE.finditer(norm):
        spoken = m.group("d")
        minutes = 90 if spoken.startswith("полтора") else (parse_duration(spoken) or 0) // 60
        if 0 < minutes <= 12 * 60:             # «на 15 часов» - это время, а не длительность
            info["minutes"] = minutes
            cut(m)
            break
    m = _CAL_ALL_DAY_RE.search(norm)
    if m:
        info["all_day"] = True
        cut(m)
    m = _CAL_DAY_RE.search(norm)
    if m:
        info["day"] = _cal_date(m.group("day"), date.today())
        cut(m)
    m = _REMIND_NOON_RE.search(norm) or _CAL_AT_RE.search(norm)
    if m:
        info["clock"] = _clock_value(m, guess_pm=True)
        until = _CAL_UNTIL_RE.match(norm, m.end()) if m.re is _CAL_AT_RE else None
        cut(m)
        if until and info["clock"]:            # «с 15 до 17»
            end = _clock_value(until, guess_pm=True)
            if end and end > info["clock"]:
                info["minutes"] = (end[0] - info["clock"][0]) * 60 + end[1] - info["clock"][1]
            cut(until)
    for key, rx in _CAL_COLOR_RES:
        m = rx.search(norm)
        if m and ("цвет" in m.group() or m.group().endswith(("ым", "им"))):   # «красным», «цвет красный»
            info["color"] = key
            cut(m)
            break
    info["rest"] = " ".join(text.split()).strip(PUNCT)
    return info


def _cal_moment(info: dict) -> str | None:
    """'2026-10-08T15:00' или '2026-10-08' (весь день). «В 9», а уже 10 - значит завтра."""
    day, clock = info["day"], info["clock"]
    if clock is None or info["all_day"]:
        return day.isoformat() if day else None
    moment = datetime.combine(day or date.today(), datetime.min.time()).replace(hour=clock[0], minute=clock[1])
    if day is None and moment <= datetime.now():
        moment += timedelta(days=1)
    return moment.strftime("%Y-%m-%dT%H:%M")


def _strip_words(text: str, lead: tuple[str, ...], tail: tuple[str, ...]) -> str:
    words = text.replace(",", " ").split()
    while words and words[0] in lead:
        words.pop(0)
    while words and words[-1] in tail:
        words.pop()
    return " ".join(words).strip(PUNCT)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _parse_event_add(low: str) -> Callable[[], str] | None:
    m = _CAL_EVENT_ADD_RE.match(low)
    if not m or not (m.group("verb") or low.startswith("нов")):
        return None
    rest = (m.group("pre") or "") + m.group("rest")        # «добавь на среду встречу …» - день до слова-события
    in_calendar = m.group("cal") or _CAL_IN_CALENDAR_RE.search(rest)
    if not (m.group("noun") or in_calendar):
        return None                            # «поставь лайк», «запиши хлеб» - не календарь
    info = _cal_when(_CAL_IN_CALENDAR_RE.sub(" ", rest))
    title = _strip_words(info["rest"], ("и", "а", "мне", "новую", "новое"),
                         ("на", "в", "во", "к", "до", "с", "со", "и", "а", "по", "цветом"))
    noun = next((name for stem, name in _EVENT_NOUNS.items() if (m.group("noun") or "").startswith(stem)), None)
    if noun in ("Встреча", "Созвон"):          # «встречу с врачом» - «Встреча с врачом»
        title = f"{noun} {title}".strip()
    title = _capital(title or noun or "Событие")
    start = _cal_moment(info)
    if start is None:
        return lambda: f"{INFO}скажите, когда: например, добавь встречу завтра в 15 часов"
    args: dict = {"title": title, "start": start}
    if info["minutes"] and "T" in start:
        args["duration_min"] = info["minutes"]
    if info["color"]:
        args["color"] = info["color"]
    return lambda: tools.execute_tool("calendar_add_event", args)


def _parse_task_add(low: str) -> Callable[[], str] | None:
    m = next((rx.match(low) for rx in _TASK_ADD_RES if rx.match(low)), None)
    if not m:
        return None
    rest = _TASK_NO_DUE_RE.sub(" ", m.group("rest"))
    info = _cal_when(rest)
    title = info["rest"]
    while True:                                # «сдать отчёт со сроком до» - убираю слова срока по краям
        trimmed = _TASK_DUE_EDGE_RE.sub("", title).strip(PUNCT)
        if trimmed == title:
            break
        title = trimmed
    title = _strip_words(title, ("и", "а", "мне"), ("и", "а"))
    if not title:
        return lambda: f"{RAW}Какую задачу добавить?"
    args: dict = {"title": title}
    due = _cal_moment({**info, "all_day": False}) if (info["day"] or info["clock"]) else None
    if due:
        args["due"] = due
    return lambda: tools.execute_tool("task_add", args)


def parse_calendar(low: str) -> Callable[[], str] | None:
    """Google Календарь и Задачи: «что у меня завтра», «добавь встречу в пятницу в 10 утра синим цветом»,
    «добавь задачу сдать отчёт до пятницы», «отметь задачу купить молоко выполненной»."""
    for rx in _TASK_DONE_RES:                  # раньше добавления: «задача X выполнена» - не новая задача
        m = rx.match(low)
        if m:
            return lambda t=m.group("title").strip(PUNCT): tools.execute_tool("task_done", {"title": t})
    if _TASK_LIST_RE.search(low):
        return lambda: tools.execute_tool("task_list", {})
    task = _parse_task_add(low)
    if task:
        return task
    m = _CAL_EVENT_DELETE_RE.match(low)
    if m:
        info = _cal_when(m.group("rest"))
        title = _strip_words(info["rest"], ("и", "а", "мою", "эту"), ("на", "в", "во", "и", "а"))
        when = _cal_moment(info) or ""
        return lambda: tools.execute_tool("calendar_delete", {"title": title, "when": when})
    if _CAL_NEXT_RE.search(low):
        return lambda: tools.execute_tool("calendar_next", {})
    if _CAL_AGENDA_RE.search(low):
        day = _cal_when(low)["day"] or date.today()
        return lambda: tools.execute_tool("calendar_agenda", {"day": day.isoformat()})
    return _parse_event_add(low)


TAB_CLOSE_NAMED_RE = re.compile(TAB_CLOSE_NAMED)
TAB_SWITCH_NAMED_RE = re.compile(TAB_SWITCH_NAMED)
TAB_SWITCH_SITE_RE = re.compile(TAB_SWITCH_SITE)
TAB_CONTEXT_NEXT_RE = re.compile(TAB_CONTEXT_NEXT)
TAB_CONTEXT_PREV_RE = re.compile(TAB_CONTEXT_PREV)
FORWARD_OR_NEXT_RE = re.compile(FORWARD_OR_NEXT)
_NOT_TAB_NAME = re.compile(r"^(?:эт\w+|текущ\w+|активн\w+|все|всё|остальн\w+|други\w+|перв\w+|последн\w+|"
                           r"следующ\w+|предыдущ\w+|прошл\w+|нов\w+|закрыт\w+|лев\w+|прав\w+)\b"
                           r"|,|\b(?:и|а|потом|затем)\b")      # «закрой вкладку и открой ютуб» - это две команды


def _tab(tool: str, args: dict) -> Callable[[], str]:
    return lambda: tools.execute_tool(tool, args)


def parse_browser(seg: str) -> Callable[[], str] | None:
    """Вкладки: закрыть соседнюю / по названию / остальные, перейти на вкладку, список вкладок.
    И голые «назад», «вперёд», «следующая»: смысл зависит от окна и от того, что делали только что."""
    seg = seg.strip(PUNCT)
    # Только что листали вкладки - «следующая», «ещё», «назад» продолжают листать (из лога: «предыдущая»
    # после «следующая вкладка» переключала трек)
    if browser.in_tab_context():
        if TAB_CONTEXT_NEXT_RE.match(seg):
            return _tab("shortcut", {"action": "next_tab"})
        if TAB_CONTEXT_PREV_RE.match(seg):
            return _tab("shortcut", {"action": "prev_tab"})
    # «Назад», «вперёд»: в браузере и проводнике - страница, иначе трек
    for rx, page, track in ((BACK_OR_PREVIOUS_RE, "back", "previous"), (FORWARD_OR_NEXT_RE, "forward", "next")):
        if rx.match(seg):
            return lambda p=page, t=track: (tools.execute_tool("shortcut", {"action": p}) if browser.foreground_kind()
                                            else tools.execute_tool("media", {"action": t}))
    # Голое «закрой»: в браузере - вкладка; иначе переспрашиваю, а не закрываю всё окно
    # (из лога: ИИ на «закрой» закрыл окно Firefox со всеми вкладками)
    if seg in ("закрой", "закрыть"):
        return lambda: (tools.execute_tool("close_tab", {"which": "current"}) if browser.foreground_kind() == "browser"
                        else f"{RAW}Что закрыть?")

    for key, which in (("tab_close", "current"), ("tab_close_prev", "previous"),
                       ("tab_close_next", "next"), ("tab_close_others", "others")):
        if R[key].search(seg):
            return _tab("close_tab", {"which": which})
    if R["tab_list"].search(seg):
        return _tab("list_tabs", {})
    if R["copy_link"].search(seg):
        return _tab("copy_link", {})
    m = TAB_CLOSE_NAMED_RE.match(seg)
    if m:
        name = (m.group("name") or m.group("name2") or "").strip(PUNCT)
        if name and not _NOT_TAB_NAME.search(name):
            return _tab("close_tab", {"which": "name", "name": name})
    m = TAB_SWITCH_NAMED_RE.match(seg)
    if m and not _NOT_TAB_NAME.search(m.group("name")):
        return _tab("switch_tab", {"name": m.group("name").strip(PUNCT)})
    m = TAB_SWITCH_SITE_RE.match(seg)
    if m and exact_site(m.group("name")):
        return _tab("switch_tab", {"name": m.group("name").strip(PUNCT)})
    return None


def parse_media(seg: str) -> Callable[[], str] | None:
    """Фраза только из глагола, цели и связок: "поставь ютуб на паузу", "следующий трек"."""
    seg = seg.strip(PUNCT)
    if MEDIA_UNPAUSE_RE.search(seg):
        action, rest = "play", MEDIA_UNPAUSE_RE.sub(" ", seg)
    else:
        found = [(name, rx) for name, rx in MEDIA_VERB_RES if rx.search(seg)]
        if len(found) != 1:                 # нет глагола или их два ("выключи и включи")
            return None
        action, rx = found[0]
        rest = rx.sub(" ", seg)
    target = None
    for name, rx in MEDIA_TARGET_RES:
        if rx.search(rest):
            target = target or name
            rest = rx.sub(" ", rest)
    if MEDIA_FILLER_RE.sub(" ", rest).strip(PUNCT + " "):
        return None                         # есть другие слова: "включи звук", "переключи окно"
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
    text = text.strip(PUNCT)
    for site, pattern in SITE_PATTERNS:
        if pattern.fullmatch(text):
            return site
    return None


def parse_search(low: str) -> Callable[[], str] | None:
    """Поиск: "найди котиков на ютубе", "ютуб котики", "загугли ..."."""
    m = BROWSER_SITE_RE.fullmatch(low)           # «браузер, переводчик», «открой в браузере google collab»
    if m:
        named = exact_site(m.group("rest") or m.group("rest2"))
        if named:
            return lambda: tools.execute_tool("open_browser", {"site": named})
    site = detect_site(low)
    if site not in ("youtube", "google"):
        m = IN_BROWSER_RE.match(low)                  # «открой в браузере астана хаб хакатон» - ищем в Google
        if m:
            query = (m.group("q1") or m.group("q2") or "").strip(PUNCT)
            if query and query not in ("открой", "найди", "поищи"):
                return lambda: tools.execute_tool("open_browser", {"site": "google", "query": query})
    if site is None:
        m = BARE_SEARCH_RE.match(low)                 # "найди X" без сайта - Google
        if m and m.group("query").strip(PUNCT):
            query = m.group("query").strip(PUNCT)
            return lambda: tools.execute_tool("open_browser", {"site": "google", "query": query})
        return None
    if site not in ("youtube", "google"):
        return None
    m = SEARCH_VERB_RE.search(low)
    if not m:
        short = SHORT_SEARCH_RE.match(low)
        if short:
            query = short.group("query").strip(PUNCT)
            if query and not re.match(rf"{OPEN_VERBS}\b", query) and not SEARCH_VERB_RE.match(query):
                return lambda: tools.execute_tool("open_browser", {"site": site, "query": query})
        # "включи на ютубе котики" - глагол без "найди" только с "на/в ютубе"
        m = re.search(r"\b(?:включи|запусти)\b", low)
        if not m or not re.search(rf"\b(?:на|в|во)\s+(?:{YT}|{GOOGLE})", low):
            return None

    # До глагола поиска может стоять только "открой ютуб и", иначе это другая команда
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
    """Исправляет типичные ослышки Whisper (HEARING_FIXES)."""
    for pattern, replacement in _HEARING_FIXES:
        low = pattern.sub(replacement, low)
    return " ".join(low.split())


# После этих команд продолжения не бывает, их можно выполнять после короткой паузы
_QUICK_KEYS = ("time", "date", "weekday", "datefull", "sleep_mode", "now_playing", "mute", "unmute",
               "media_next", "media_prev", "media_pause", "media_play",
               "track_like", "track_unlike", "track_dislike", "volume_up", "volume_down", "bright_up", "bright_down",
               "tab_close", "tab_close_prev", "tab_close_next", "tab_list")


def is_quick_command(text: str, need_name: bool = True, pending: bool = False) -> bool:
    """Законченная короткая команда ("пауза", "громкость 30").
    Поиск, напоминания, диктовку и вопросы сюда не беру: после паузы у них часто идёт продолжение."""
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
    if parse_media(body) or parse_app_volume(body) or parse_audio_output(body):
        return True
    if any(rx.search(body) for _, rx in SHORTCUT_RES) or FORWARD_OR_NEXT_RE.match(body):
        return True
    if (R["volume_set"].search(body) or R["bright_set"].search(body)) and parse_number(body) is not None:
        return True
    return any(R[key].search(body) for key in _QUICK_KEYS)


MUSIC_APP_RE = re.compile(MUSIC_APP)
PRONOUN_RE = re.compile(r"^(открой|закрой|сверни|разверни|запусти)\s+(?:его|её|ее|него|неё|нее|это|их)$")


CLOSE_VERBS = ("закрой", "закрыть", "заверши", "завершить")


def _bare_name(segment: str) -> bool:
    return (segment in APP_ALIASES or segment in FOLDER_ALIASES or exact_site(segment) is not None
            or segment in ("браузер", "browser"))


def parse_all(low: str) -> list[Callable[[], str]] | None:
    """Разбирает всю команду. Если хоть одна часть не разобралась - None, и всё уходит в ИИ."""
    low = fix_hearing(low)
    calendar = parse_calendar(low)                      # раньше вкладок и цепочек: «встреча с Машей и Петей» - одна
    if calendar:
        return [calendar]
    tab_action = parse_browser(low)                     # «назад», «закрой вкладку ютуб» - до медиа и поиска
    if tab_action:
        return [tab_action]
    if any(rx.search(low) for _, rx in SHORTCUT_RES):
        return [parse_local(low)]                       # «назад в браузере», «следующая вкладка»
    if MUSIC_APP_RE.fullmatch(low):                     # «запусти яндекс музыку» - открыть и сразу включить
        return [lambda: tools.execute_tool("play_app", {"name": MEDIA_FALLBACK_APP["music"]})]
    # "ютуб стоп" - пауза, "ютуб на 30" - громкость, а не поиск; напоминание по "и" не режу
    whole = parse_side_by_side(low) or parse_reminder(low) or _whole_audio_output(low) or parse_app_volume(low) or parse_media(low) or parse_search(low)
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
        if last_verb in CLOSE_VERBS and _bare_name(segment):   # «закрой браузер и яндекс музыку» - закрыть обе
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


def parse_answer(verb: str, answer: str) -> list[Callable[[], str]] | None:
    """Ответ на «Что открыть?»: «obsidian» - то же, что «открой obsidian». Ответом считаю только название:
    «ничего» или новая команда со своим глаголом - не ответ."""
    answer = fix_hearing(answer.strip(PUNCT))
    if VERB_RE.match(answer) or not (_bare_name(answer) or _known_app(answer)):
        return None
    return parse_all(f"{verb} {answer}")
