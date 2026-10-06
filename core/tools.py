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
    output_device_info,
    set_output_device,
    set_volume,
)
from core.apps import (  # noqa: F401
    alt_tab,
    arrange_window,
    change_brightness,
    close_active,
    close_app,
    dictate,
    lock_pc,
    minimize_app,
    open_app,
    open_browser,
    open_folder,
    open_recent,
    screenshot,
    set_brightness,
    show_desktop,
    side_by_side,
    window_state,
)
from core.media import (  # noqa: F401
    app_volume,
    media,
    now_playing,
    play_app,
    rate_track,
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
from core import browser, calc, system, undo
from core import gcal


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
    "arrange_window": arrange_window,
    "side_by_side": side_by_side,
    "screenshot": screenshot,
    "lock_pc": lock_pc,
    "tell_time": tell_time,
    "tell_date": tell_date,
    "set_timer": set_timer,
    "cancel_timers": cancel_timers,
    "open_app": open_app,
    "open_browser": open_browser,
    "open_folder": open_folder,
    "open_recent": open_recent,
    "set_volume": set_volume,
    "change_volume": change_volume,
    "mute": mute,
    "audio_output": set_output_device,
    "audio_output_info": output_device_info,
    "media": media,
    "now_playing": now_playing,
    "rate_track": rate_track,
    "close_tab": browser.close_tab,
    "switch_tab": browser.switch_tab,
    "list_tabs": browser.list_tabs,
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
    "calendar_agenda": gcal.calendar_agenda,
    "calendar_next": gcal.calendar_next,
    "calendar_add_event": gcal.calendar_add_event,
    "calendar_delete": gcal.calendar_delete,
    "calendar_delete_id": gcal.calendar_delete_id,     # только после «да», ИИ его не видит
    "task_add": gcal.task_add,
    "task_list": gcal.task_list,
    "task_done": gcal.task_done,
    "undo": undo.undo,
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
_DAY = {"type": "string", "description": "Дата ГГГГ-ММ-ДД (сегодняшняя дата есть в системной подсказке)"}

TOOLS = [
    _tool("open_app", "Открыть установленное приложение.",
          {"name": {"type": "string", "description": "Название на английском"}}, ["name"]),
    _tool("open_browser",
          "Открыть сайт или браузер. Для поиска на YouTube передай site='youtube' и query.",
          {"site": {"type": "string", "enum": list(SITES)}, "query": {"type": "string"}}, []),
    _tool("open_folder", "Открыть папку в проводнике.",
          {"name": {"type": "string", "enum": list(FOLDERS)}}, ["name"]),
    _tool("open_recent", "Открыть файл, который недавно редактировали (последний из «Недавних»), "
                         "или show_all=true - всю папку недавних файлов.",
          {"show_all": {"type": "boolean"}}, []),
    _tool("minimize_app", "Свернуть окно приложения (не закрывая его).",
          {"name": {"type": "string", "description": "Название приложения"}}, ["name"]),
    _tool("arrange_window", "Поставить окно приложения на левую или правую половину экрана "
                            "или перенести на другой монитор.",
          {"position": {"type": "string", "enum": ["left", "right", "monitor"]},
           "name": {"type": "string", "description": "Название приложения; не указывай для активного окна"}},
          ["position"]),
    _tool("side_by_side", "Поставить два приложения рядом: первое слева, второе справа.",
          {"left": {"type": "string", "description": "Приложение слева"},
           "right": {"type": "string", "description": "Приложение справа"}}, ["left", "right"]),
    _tool("close_app", "Закрыть запущенное приложение. Только если явно просят закрыть, а не свернуть.",
          {"name": {"type": "string", "description": "Название приложения"}}, ["name"]),
    _tool("close_active", "Закрыть активное (текущее) окно или приложение.",
          {"window_only": {"type": "boolean", "description": "true — только окно, false — приложение целиком"}}, []),
    _tool("set_volume", "Установить громкость компьютера в процентах.", {"level": _PERCENT}, ["level"]),
    _tool("change_volume", "Изменить громкость на указанное число процентов.", {"delta": _DELTA}, ["delta"]),
    _tool("mute", "Выключить (true) или включить (false) звук.", {"state": {"type": "boolean"}}, ["state"]),
    _tool("audio_output", "Переключить вывод звука на другое устройство: наушники, колонки, монитор. "
                          "Без target - на следующее по кругу.",
          {"target": {"type": "string", "description": "Куда: наушники, колонки, монитор или название устройства"}},
          []),
    _tool("media", "Управление музыкой/видео, в том числе фоновым плеером.",
          {"action": {"type": "string", "enum": ["pause", "play", "next", "previous"]},
           "target": {"type": "string", "enum": ["music", "youtube", "video"],
                      "description": "Чем управлять; не указывай, если пользователь не уточнил"}},
          ["action"]),
    _tool("now_playing", "Сказать, что сейчас играет: название и исполнителя.", {}, []),
    _tool("close_tab", "Закрыть вкладку браузера: текущую, соседнюю, все кроме текущей или по названию сайта.",
          {"which": {"type": "string", "enum": ["current", "previous", "next", "others", "name"]},
           "name": {"type": "string", "description": "Название сайта или вкладки, если which=name"}}, ["which"]),
    _tool("switch_tab", "Перейти на открытую вкладку браузера по названию сайта.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("list_tabs", "Сказать, какие вкладки открыты в браузере.", {}, []),
    _tool("rate_track", "Лайк текущей песне в Яндекс Музыке (добавить в «Мне нравится»), снять лайк или дизлайк.",
          {"action": {"type": "string", "enum": ["like", "unlike", "dislike"]}}, ["action"]),
    _tool("app_volume", "Громкость одного приложения (не общая): музыки, ютуба, видео или программы по названию.",
          {"target": {"type": "string", "description": "music, youtube, video или название приложения"},
           "level": _PERCENT, "delta": _DELTA}, ["target"]),
    _tool("set_brightness", "Установить яркость экрана в процентах.", {"level": _PERCENT}, ["level"]),
    _tool("change_brightness", "Изменить яркость экрана на указанное число процентов.", {"delta": _DELTA}, ["delta"]),
    _tool("shortcut", "Нажать сочетание клавиш в активном окне: копировать, вставить, отменить, сохранить, "
                      "выделить всё, очистить поле, Enter, вкладки браузера, обновить, назад, полный экран, "
                      "окно влево/вправо, окно на другой монитор.",
          {"action": {"type": "string", "enum": list(system.SHORTCUT_KEYS)}}, ["action"]),
    _tool("undo", "Отменить последнее действие ассистента: вернуть громкость или яркость, открыть заново "
                  "закрытое окно, убрать вставленный текст. Без kind - самое последнее, иначе Ctrl+Z.",
          {"kind": {"type": "string", "enum": ["volume", "brightness", "close", "text"]}}, []),
    _tool("system_status", "Загрузка процессора и оперативной памяти.", {}, []),
    _tool("gpu_status", "Температура и загрузка видеокарты.", {}, []),
    _tool("microphone", "Включить (true) или выключить (false) микрофон.", {"state": {"type": "boolean"}}, ["state"]),
    _tool("calendar_agenda", "Что запланировано в Google Календаре на день: встречи и задачи со сроком.",
          {"day": _DAY}, []),
    _tool("calendar_next", "Когда следующая встреча в календаре.", {}, []),
    _tool("calendar_add_event", "Добавить встречу/событие в Google Календарь.",
          {"title": {"type": "string", "description": "Название по-русски, например «Встреча с врачом»"},
           "start": {"type": "string", "description": "ГГГГ-ММ-ДДTЧЧ:ММ; только ГГГГ-ММ-ДД - событие на весь день"},
           "duration_min": {"type": "integer", "description": "Длительность в минутах, если сказали"},
           "color": {"type": "string", "enum": list(gcal.COLOR_IDS)}}, ["title", "start"]),
    _tool("calendar_delete", "Удалить встречу из календаря (спросит подтверждение).",
          {"title": {"type": "string"}, "when": _DAY}, []),
    _tool("task_add", "Добавить задачу в Google Задачи, со сроком или без.",
          {"title": {"type": "string"},
           "due": {"type": "string", "description": "Срок ГГГГ-ММ-ДД или ГГГГ-ММ-ДДTЧЧ:ММ; не указывай, если срока нет"}},
          ["title"]),
    _tool("task_list", "Прочитать список невыполненных задач.", {}, []),
    _tool("task_done", "Отметить задачу выполненной.", {"title": {"type": "string"}}, ["title"]),
]


def execute_tool(name: str, args: dict) -> str:
    fn = FUNCTIONS.get(name)
    if fn is None:
        return f"{FAIL}не знаю инструмент {name}"
    try:
        state = undo.before(name, args)          # как было - для «отмени»
        result = fn(**args)
        log("Результат", f"{name}: {result}")
        undo.after(name, args, state, result)
        return result
    except Exception as e:
        log("Ошибка", f"{name}({args}): {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} выполнить «{name}»"


def execute_tool_dictate(text: str) -> str:
    try:
        result = dictate(text)
        undo.after("dictate", {}, None, result)
        return result
    except Exception as e:
        log("Ошибка", f"dictate: {e}")
        return f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} записать текст"
