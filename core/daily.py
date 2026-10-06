"""Время, таймеры, напоминания, заметки, погода, курс, сон, питание компьютера."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime

from config import (
    CONFIRM_DANGEROUS,
    CONFIRM_TIMEOUT,
    CURRENCY_HOME,
    CURRENCY_HOME_NAME,
    NOTES_FILE,
    POWER_DELAY_SEC,
    REMINDERS_FILE,
    WEATHER_CITY,
)
from phrases import (
    CURRENCY_SPOKEN,
)
from core.util import (  # noqa: F401
    END,
    FAIL,
    INFO,
    RAW,
    _plural,
    log,
    parse_number,
)
from core.speech import (  # noqa: F401
    play_sound,
    speak,
)


_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")
_timers: list[threading.Timer] = []


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
    """"5 минут", "2 часа 30 минут", "полчаса" -> секунды."""
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
        speak(f"Господин, таймер на {label} сработал.", mood="urgent")

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
    return f"{RAW}Хорошо."             # RAW: говорю ровно эту фразу в любом режиме


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
        # Если включена гибернация, Windows уйдёт в неё - это уже поведение системы
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=flags)
        return f"{INFO}перевожу компьютер в спящий режим"
    return f"{FAIL}неизвестное действие {action}"


def request_power(action: str) -> str:
    """Выключение / перезагрузка / сон - только после подтверждения."""
    global _pending
    if not CONFIRM_DANGEROUS:
        return do_power(action)
    _pending = {
        "action": lambda: _execute("do_power", {"action": action}),
        "deadline": time.time() + CONFIRM_TIMEOUT,
    }
    return f"{RAW}Вы уверены, господин, что нужно {_POWER_LABELS[action]}? Скажите «да» или «нет»."


def cancel_power() -> str:
    result = subprocess.run(["shutdown", "/a"], capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        return f"{INFO}сейчас ничего не запланировано"
    return f"отменил{END} выключение"


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
        # open.er-api.com: бесплатно и без ключа, курс обновляется раз в сутки
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


_reminders: list[dict] = []        # {"at": unix-время, "text": что напомнить}
_reminders_lock = threading.Lock()


def _save_reminders() -> None:
    try:
        REMINDERS_FILE.write_text(json.dumps(_reminders, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log("Напоминания", f"не удалось сохранить: {e}")


def _when_words(at: float) -> str:
    """Время в удобном для произношения виде: "сегодня в 18 часов 30 минут"."""
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
    speak(f"Господин, {'пропущенное напоминание' if missed else 'напоминаю'}: {what}.", mood="urgent")


def _reminder_loop() -> None:
    """Раз в секунду проверяю напоминания. Пропущенные, пока ПК был выключен, говорю при запуске."""
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


def _execute(name: str, args: dict) -> str:
    """Импорт внутри функции: tools импортирует этот модуль, иначе будет циклический импорт."""
    from core.tools import execute_tool

    return execute_tool(name, args)
