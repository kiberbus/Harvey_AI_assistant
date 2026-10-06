"""Клавиши (копировать, вкладки, окна), диски, состояние компьютера, микрофон."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes

from config import (
    FEMALE_VOICE,
)
from core.util import (  # noqa: F401
    AudioUtilities,
    CLSCTX_ALL,
    END,
    FAIL,
    HAS_PYCAW,
    IAudioEndpointVolume,
    INFO,
    _plural,
    log,
    psutil,
)
from core.winapi import (  # noqa: F401
    KEYEVENTF_KEYUP,
    _user32,
)

KEYEVENTF_EXTENDEDKEY = 0x0001
CTRL, SHIFT, ALT, WIN = 0x11, 0x10, 0x12, 0x5B
TAB, ENTER, ESC, SPACE, BACK, DELETE = 0x09, 0x0D, 0x1B, 0x20, 0x08, 0x2E
LEFT, UP, RIGHT, DOWN = 0x25, 0x26, 0x27, 0x28
F5, F11 = 0x74, 0x7A
# Клавиши Browser Back / Forward: их понимают все браузеры и Проводник, и в отличие от
# Alt+Left они не открывают меню, если Alt проскочит отдельно
BROWSER_BACK, BROWSER_FORWARD = 0xA6, 0xA7
_EXTENDED = {LEFT, UP, RIGHT, DOWN, DELETE, BROWSER_BACK, BROWSER_FORWARD}   # без флага стрелки - цифровой блок


def _key(letter: str) -> int:
    return ord(letter.upper())


# действие → (сочетания по очереди, что сказать о сделанном)
SHORTCUT_KEYS: dict[str, tuple[list[tuple[int, ...]], str]] = {
    "copy": ([(CTRL, _key("c"))], f"скопировал{END}"),
    "copy_all": ([(CTRL, _key("a")), (CTRL, _key("c"))], f"скопировал{END} всё"),
    "paste": ([(CTRL, _key("v"))], f"вставил{END}"),
    "cut": ([(CTRL, _key("x"))], f"вырезал{END}"),
    "undo": ([(CTRL, _key("z"))], f"отменил{END}"),
    "redo": ([(CTRL, _key("y"))], f"вернул{END}"),
    "save": ([(CTRL, _key("s"))], f"сохранил{END}"),
    "select_all": ([(CTRL, _key("a"))], f"выделил{END} всё"),
    "clear_field": ([(CTRL, _key("a")), (DELETE,)], f"очистил{END} поле"),
    "enter": ([(ENTER,)], f"нажал{END} Enter"),
    "escape": ([(ESC,)], f"нажал{END} Escape"),
    "tab": ([(TAB,)], f"нажал{END} Tab"),
    "space": ([(SPACE,)], f"нажал{END} пробел"),
    "backspace": ([(BACK,)], f"стёр{'ла' if FEMALE_VOICE else ''} символ"),
    "delete": ([(DELETE,)], f"нажал{END} Delete"),
    "up": ([(UP,)], f"нажал{END} вверх"),
    "down": ([(DOWN,)], f"нажал{END} вниз"),
    "left": ([(LEFT,)], f"нажал{END} влево"),
    "right": ([(RIGHT,)], f"нажал{END} вправо"),
    "new_tab": ([(CTRL, _key("t"))], f"открыл{END} новую вкладку"),
    "close_tab": ([(CTRL, _key("w"))], f"закрыл{END} вкладку"),
    "reopen_tab": ([(CTRL, SHIFT, _key("t"))], f"вернул{END} закрытую вкладку"),
    "next_tab": ([(CTRL, TAB)], f"переключил{END} на следующую вкладку"),
    "prev_tab": ([(CTRL, SHIFT, TAB)], f"переключил{END} на предыдущую вкладку"),
    "refresh": ([(F5,)], f"обновил{END} страницу"),
    "back": ([(BROWSER_BACK,)], f"{'вернулась' if FEMALE_VOICE else 'вернулся'} назад"),
    "forward": ([(BROWSER_FORWARD,)], f"{'перешла' if FEMALE_VOICE else 'перешёл'} вперёд"),
    "fullscreen": ([(F11,)], f"переключил{END} полный экран"),
    "window_left": ([(WIN, LEFT)], f"прикрепил{END} окно влево"),
    "window_right": ([(WIN, RIGHT)], f"прикрепил{END} окно вправо"),
    "window_up": ([(WIN, UP)], f"развернул{END} окно"),
    "window_down": ([(WIN, DOWN)], f"уменьшил{END} окно"),
    "window_monitor": ([(WIN, SHIFT, RIGHT)], f"{'перенесла' if FEMALE_VOICE else 'перенёс'} окно на другой монитор"),
}


def _chord(*vks: int) -> None:
    for vk in vks:
        _user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | KEYEVENTF_KEYUP, 0)


def shortcut(action: str) -> str:
    """Нажимает сочетание клавиш в активном окне."""
    entry = SHORTCUT_KEYS.get(action)
    if entry is None:
        return f"{FAIL}не знаю действие «{action}»"
    chords, done = entry
    for i, chord in enumerate(chords):
        if i:
            time.sleep(0.05)
        _chord(*chord)
    return done


def foreground_exe() -> str:
    if psutil is None:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""


def open_drive(letter: str) -> str:
    letter = (letter or "").strip().upper()[:1]
    path = f"{letter}:\\"
    if not letter.isalpha() or not os.path.exists(path):
        return f"{FAIL}диска {letter} нет"
    os.startfile(path)
    return f"открыл{END} диск {letter}"


def _gb(value: float) -> str:
    return f"{value / 1024 ** 3:.1f}".replace(".", ",")


def _pct(n: int) -> str:
    return f"{n} {_plural(n, 'процент', 'процента', 'процентов')}"


def system_status() -> str:
    if psutil is None:
        return f"{FAIL}не могу узнать загрузку: нет psutil"
    cpu = round(psutil.cpu_percent(interval=0.5))
    mem = psutil.virtual_memory()
    return (f"{INFO}процессор загружен на {_pct(cpu)}, "
            f"память — на {_pct(round(mem.percent))}: занято {_gb(mem.used)} из {_gb(mem.total)} гигабайта")


def gpu_status() -> str:
    """Температура и загрузка NVIDIA через nvidia-smi."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()[0]
        temp, load, used, total = (int(float(x)) for x in out.split(","))
    except Exception as e:
        log("Видеокарта", f"nvidia-smi: {e}")
        return f"{FAIL}не могу узнать температуру видеокарты — нужна видеокарта NVIDIA"
    return (f"{INFO}видеокарта: {temp} {_plural(temp, 'градус', 'градуса', 'градусов')}, "
            f"загружена на {_pct(load)}, видеопамять занята на {_pct(used * 100 // max(total, 1))}")


def _mic_volume():
    if not HAS_PYCAW:
        raise RuntimeError("pycaw не установлен")
    device = AudioUtilities.GetMicrophone()
    if device is None:
        raise RuntimeError("микрофон не найден")
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


# Состояние микрофона для меню трея. Сам трей к микрофону не обращается: COM из его
# потока ронял весь процесс в _ctypes.
mic_is_muted = False
mic_on_requested = threading.Event()        # «Включить микрофон» в трее - включает главный цикл


def mic_muted() -> bool:
    """Спрашивает систему (только из главного потока) и запоминает ответ для трея."""
    global mic_is_muted
    try:
        mic_is_muted = bool(_mic_volume().GetMute())
    except Exception:
        pass
    return mic_is_muted


def microphone(state: bool) -> str:
    """Включает или выключает микрофон по умолчанию для всех программ.
    Выключенный микрофон Харви тоже не слышит - включить обратно можно из трея."""
    global mic_is_muted
    _mic_volume().SetMute(0 if state else 1, None)
    mic_is_muted = not state
    if state:
        return f"включил{END} микрофон"
    return (f"{INFO}микрофон выключен. Теперь я вас тоже не слышу — "
            f"включить его можно в меню моего значка у часов")
