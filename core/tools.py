"""Всё, что умеет Харви: функции, их описания для ИИ и execute_tool."""

from __future__ import annotations

import inspect
from typing import Callable

from config import (
    CURRENCY_HOME_NAME,
    FEMALE_VOICE,
    FOLDERS,
    SITES,
)
from phrases import (
    CURRENCY_SPOKEN,
)
from core.util import (
    FAIL,
    log,
)
from core.audio import (
    change_volume,
    mute,
    output_device_info,
    set_output_device,
    set_volume,
)
from core.apps import (
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
    close_all_apps,
    request_close_all,
    restore_windows,
    screenshot,
    set_brightness,
    show_desktop,
    side_by_side,
    window_state,
)
from core.media import (
    app_volume,
    media,
    now_playing,
    play_app,
    rate_track,
)
from core.daily import (
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
    remind,
    request_power,
    set_timer,
    sleep_mode,
    tell_date,
    tell_datefull,
    tell_time,
    tell_weekday,
    weather,
)
from core.tray import (
    restart_self,
)
from core import browser, calc, files, game, steam, system, uia, undo
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
    "restore_windows": restore_windows,
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
    "launch_game": steam.launch,
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
    "copy_link": browser.copy_link,
    "restart_self": restart_self,
    "app_volume": app_volume,
    "play_app": play_app,
    "add_reminder": add_reminder,
    "remind": remind,                                  # то же для ИИ: время строкой, а не unix-временем
    "list_reminders": list_reminders,
    "cancel_reminders": cancel_reminders,
    "set_brightness": set_brightness,
    "change_brightness": change_brightness,
    "shortcut": system.shortcut,
    "click": uia.click,
    "press_keys": system.press_keys,
    "create_file": files.create_file,
    "open_file": files.open_file,
    "select_all": files.select_all,
    "select_file": files.select_file,
    "delete_file": files.delete_file,
    "delete_selected": files.delete_selected,
    "recycle": files.recycle,                          # только после «да», ИИ его не видит
    "close_folder": files.close_folder,
    "request_close_all": request_close_all,
    "close_all_apps": close_all_apps,                  # только после «да», ИИ его не видит
    "open_drive": system.open_drive,
    "system_status": system.system_status,
    "gpu_status": system.gpu_status,
    "microphone": system.microphone,
    "game_mode": game.set_game_mode,
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
_WHERE = {"type": "string", "enum": list(FOLDERS), "description": "Только если папку назвали: «на рабочем столе»"}

TOOLS = [
    _tool("open_app", "Открыть установленное приложение.",
          {"name": {"type": "string", "description": "Название на английском"}}, ["name"]),
    _tool("launch_game", "Запустить игру из библиотеки Steam.",
          {"name": {"type": "string", "description": "Название игры, как его сказали"}}, ["name"]),
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
    _tool("close_tab", "Закрыть вкладку браузера: текущую, соседнюю, все кроме текущей, по названию сайта "
                       "или по номеру слева.",
          {"which": {"type": "string", "enum": ["current", "previous", "next", "others", "name", "index"]},
           "name": {"type": "string", "description": "Название сайта или вкладки, если which=name"},
           "index": {"type": "integer", "description": "Номер вкладки слева, с 1, если which=index"}}, ["which"]),
    _tool("switch_tab", "Перейти на открытую вкладку браузера: по названию сайта или по номеру слева (index).",
          {"name": {"type": "string", "description": "Название сайта или вкладки"},
           "index": {"type": "integer", "description": "Номер вкладки слева, с 1: «вторая вкладка» - 2"}}, []),
    _tool("list_tabs", "Сказать, какие вкладки открыты в браузере.", {}, []),
    _tool("copy_link", "Скопировать в буфер обмена адрес (ссылку) открытой страницы браузера.", {}, []),
    _tool("rate_track", "Лайк текущей песне в Яндекс Музыке (добавить в «Мне нравится»), снять лайк или дизлайк.",
          {"action": {"type": "string", "enum": ["like", "unlike", "dislike"]}}, ["action"]),
    _tool("app_volume", "Громкость одного приложения (не общая): музыки, ютуба, видео или программы по названию.",
          {"target": {"type": "string", "description": "music, youtube, video или название приложения"},
           "level": _PERCENT, "delta": _DELTA}, ["target"]),
    _tool("set_brightness", "Установить яркость экрана в процентах.", {"level": _PERCENT}, ["level"]),
    _tool("change_brightness", "Изменить яркость экрана на указанное число процентов.", {"delta": _DELTA}, ["delta"]),
    _tool("shortcut", "Нажать сочетание клавиш в активном окне: копировать, вставить, отменить, сохранить, "
                      "выделить всё, очистить поле, Enter, прокрутить страницу (page_down/page_up, в начало, "
                      "в конец), вкладки браузера, обновить, назад, полный экран, окно влево/вправо, "
                      "окно на другой монитор.",
          {"action": {"type": "string", "enum": list(system.SHORTCUT_KEYS)}}, ["action"]),
    _tool("click", "Нажать то, что видно в активном окне: кнопку, ссылку, пункт меню или вкладку - по надписи на ней.",
          {"name": {"type": "string", "description": "Надпись, как её сказали: «подписаться», «войти»"}}, ["name"]),
    _tool("press_keys", "Нажать любые клавиши в активном окне: «ctrl+g», «alt+f4», «w+d»; несколько сочетаний "
                        "по очереди - через запятую: «ctrl+c, ctrl+v».",
          {"keys": {"type": "string"}, "times": {"type": "integer", "description": "Сколько раз, если просили"}},
          ["keys"]),
    _tool("create_file", "Создать файл или папку в открытой папке проводника (или в where).",
          {"ext": {"type": "string", "description": "Расширение: txt, py, docx, xlsx, pptx, md…; пустое - папка"},
           "name": {"type": "string", "description": "Название, если его сказали"}, "where": _WHERE}, ["ext"]),
    _tool("open_file", "Открыть файл или папку по названию в открытой папке проводника (или в where).",
          {"name": {"type": "string"}, "where": _WHERE, "folder": {"type": "boolean"}}, ["name"]),
    _tool("select_file", "Выделить и показать файл в папке по названию.",
          {"name": {"type": "string"}, "where": _WHERE, "folder": {"type": "boolean"}}, ["name"]),
    _tool("select_all", "Выделить всё: в проводнике - все файлы папки, иначе Ctrl+A. folder=true - именно файлы папки.",
          {"folder": {"type": "boolean"}}, []),
    _tool("delete_file", "Удалить файл или папку по названию в корзину (спросит подтверждение).",
          {"name": {"type": "string"}, "where": _WHERE, "folder": {"type": "boolean"}}, ["name"]),
    _tool("delete_selected", "Удалить выделенное: в проводнике - файлы в корзину (спросит подтверждение), "
                             "в других окнах - клавиша Delete.", {"everything": {"type": "boolean"}}, []),
    _tool("close_folder", "Закрыть окно папки (проводника): текущее, по названию или все (everything).",
          {"name": {"type": "string"}, "everything": {"type": "boolean"}}, []),
    _tool("undo", "Отменить последнее действие ассистента: вернуть громкость или яркость, открыть заново "
                  "закрытое окно, убрать вставленный текст, вернуть удалённое из корзины. "
                  "Без kind - самое последнее, иначе Ctrl+Z.",
          {"kind": {"type": "string", "enum": ["volume", "brightness", "close", "text", "delete"]}}, []),
    _tool("show_desktop", "Свернуть все окна (показать рабочий стол).", {}, []),
    _tool("restore_windows", "Развернуть обратно все свёрнутые окна.", {}, []),
    _tool("window_state", "Свернуть или развернуть на весь экран активное окно.",
          {"action": {"type": "string", "enum": ["minimize", "maximize"]}}, ["action"]),
    _tool("screenshot", "Сделать снимок экрана (сохраняется в «Изображения»).", {}, []),
    _tool("lock_pc", "Заблокировать компьютер.", {}, []),
    _tool("request_power", "Выключить, перезагрузить компьютер или перевести его в сон (спросит подтверждение).",
          {"action": {"type": "string", "enum": ["shutdown", "restart", "sleep"]}}, ["action"]),
    _tool("cancel_power", "Отменить запланированное выключение или перезагрузку.", {}, []),
    _tool("set_timer", "Поставить таймер.", {"seconds": {"type": "integer", "description": "Длительность в секундах"}},
          ["seconds"]),
    _tool("cancel_timers", "Отменить все таймеры.", {}, []),
    _tool("remind", "Напомнить в указанное время.",
          {"when": {"type": "string", "description": "ГГГГ-ММ-ДДTЧЧ:ММ"},
           "text": {"type": "string", "description": "О чём напомнить"}}, ["when"]),
    _tool("add_note", "Записать заметку в файл заметок.", {"text": {"type": "string"}}, ["text"]),
    _tool("weather", "Погода сейчас и на сегодня.", {}, []),
    _tool("currency_rate", f"Курс валют: сколько они стоят в {CURRENCY_HOME_NAME}.",
          {"codes": {"type": "array", "items": {"type": "string", "enum": list(CURRENCY_SPOKEN)},
                     "description": "Коды валют: USD, EUR, RUB…"}}, ["codes"]),
    _tool("system_status", "Загрузка процессора и оперативной памяти.", {}, []),
    _tool("gpu_status", "Температура и загрузка видеокарты.", {}, []),
    _tool("microphone", "Включить (true) или выключить (false) микрофон.", {"state": {"type": "boolean"}}, ["state"]),
    _tool("game_mode", "Включить (true) или выключить (false) игровой режим: ассистент освобождает видеокарту "
                       "и процессор для игры.", {"state": {"type": "boolean"}}, ["state"]),
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


def _fit_args(name: str, fn: Callable[..., str], args: dict) -> dict:
    """Аргументы, которые функция принимает. ИИ иногда придумывает свои: switch_tab(tab_index=1) падал
    с TypeError (из лога 7 октября). Похожее имя подставляю (tab_index → index), остальное отбрасываю."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return args
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return args
    fitted = {key: value for key, value in args.items() if key in params}
    for key, value in args.items():
        if key in params:
            continue
        similar = [p for p in params if p not in fitted and len(p) >= 4 and len(key) >= 4 and (p in key or key in p)]
        if len(similar) == 1:
            fitted[similar[0]] = value
        else:
            log("Инструмент", f"{name}: нет аргумента {key}={value!r} - пропускаю")
    return fitted


def execute_tool(name: str, args: dict) -> str:
    fn = FUNCTIONS.get(name)
    if fn is None:
        return f"{FAIL}не знаю инструмент {name}"
    args = _fit_args(name, fn, args)
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
