"""Всё, что умеет Харви: функции, их описания для ИИ и execute_tool."""

from __future__ import annotations

from typing import Callable

from config import (
    FEMALE_VOICE,
    FOLDERS,
    SITES,
)
from core.util import (  # noqa: F401
    FAIL,
    log,
)
from core.audio import (  # noqa: F401
    change_volume,
    mute,
    set_volume,
)
from core.apps import (  # noqa: F401
    alt_tab,
    change_brightness,
    close_active,
    close_app,
    dictate,
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
    play_app,
)
from core.daily import (  # noqa: F401
    add_note,
    add_reminder,
    cancel_power,
    cancel_reminders,
    cancel_timers,
    currency_rate,
    do_power,
    list_reminders,
    open_notes,
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
from core import calc, system


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
    "play_app": play_app,
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


def execute_tool_dictate(text: str) -> str:
    try:
        return dictate(text)
    except Exception as e:
        log("Ошибка", f"dictate: {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} записать текст"
