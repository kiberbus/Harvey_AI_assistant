"""Отмена: «отмени», «верни как было». Перед действием запоминаю, как было, после - кладу в стек
обратное действие. Если Харви недавно ничего обратимого не делала, «отмени» - это обычный Ctrl+Z."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import (
    UNDO_DEPTH,
    UNDO_TTL,
)
from core.util import (  # noqa: F401
    END,
    FAIL,
    INFO,
    RAW,
    log,
)
from core.winapi import (  # noqa: F401
    _force_foreground,
    _user32,
    foreground_title,
)


@dataclass
class Entry:
    kind: str                       # volume, brightness, close, text
    what: str                       # для лога: на что отвечает отмена
    revert: Callable[[], str]
    at: float


_stack: deque[Entry] = deque(maxlen=UNDO_DEPTH)
_lock = threading.Lock()

# Клавиши, после которых Ctrl+Z в том же окне возвращает текст
TEXT_SHORTCUTS = {"paste", "cut", "clear_field"}
NOTHING = {"volume": f"громкость я недавно не менял{END}", "brightness": f"яркость я недавно не менял{END}",
           "close": f"я недавно ничего не закрывал{END}"}


def push(kind: str, what: str, revert: Callable[[], str]) -> None:
    with _lock:
        _stack.append(Entry(kind, what, revert, time.time()))


def clear() -> None:
    with _lock:
        _stack.clear()


def _pop(kind: str = "") -> Entry | None:
    """Последнее свежее действие (нужного вида). Старше UNDO_TTL не отменяю: «отмени» через пять минут
    после «громче» скорее про текст в редакторе."""
    now = time.time()
    with _lock:
        for entry in reversed(_stack):
            if now - entry.at > UNDO_TTL:
                break
            if not kind or entry.kind == kind:
                _stack.remove(entry)
                return entry
    return None


def undo(kind: str = "") -> str:
    """«Отмени» - последнее действие; kind - только громкость, яркость, закрытое окно или текст."""
    from core import system

    entry = _pop(kind)
    if entry is None:
        if kind in ("", "text"):
            return system.shortcut("undo")
        return f"{INFO}{NOTHING.get(kind, 'нечего отменять')}"
    log("Отмена", entry.what)
    return entry.revert()


# --- снимок «как было» перед действием ---

def before(name: str, args: dict) -> object:
    """Состояние до действия. Ошибки не мешают самому действию."""
    try:
        if name in ("set_volume", "change_volume", "mute"):
            from core import audio
            return audio.volume_state()
        if name in ("set_brightness", "change_brightness"):
            import screen_brightness_control as sbc
            current = sbc.get_brightness()
            return current[0] if isinstance(current, list) else current
        if name == "close_active":
            return _active_window_info()
    except Exception as e:
        log("Отмена", f"не запомнил{END}, как было до {name}: {e}")
    return None


def after(name: str, args: dict, state: object, result: str) -> None:
    """Действие удалось - кладу в стек обратное."""
    if not isinstance(result, str) or result.startswith((FAIL, INFO, RAW)):
        return
    try:
        revert = _revert_for(name, args, state, result)
    except Exception as e:
        log("Отмена", f"{name}: {e}")
        return
    if revert:
        push(revert[0], f"{name}: {result}", revert[1])


def _revert_for(name: str, args: dict, state, result: str) -> tuple[str, Callable[[], str]] | None:
    if name in ("set_volume", "change_volume", "mute") and state is not None:
        return "volume", lambda: _restore_volume(*state)
    if name in ("set_brightness", "change_brightness") and state is not None:
        return "brightness", lambda: _restore_brightness(int(state))
    if name == "close_app":
        return "close", _reopen_closed(args.get("name", ""), result)
    if name == "close_active" and state is not None:
        return "close", lambda: _reopen_window(state)
    if name == "shortcut" and args.get("action") == "close_tab":
        return "close", _reopen_tab
    if (name == "shortcut" and args.get("action") in TEXT_SHORTCUTS) or name == "dictate":
        hwnd = _user32.GetForegroundWindow()
        return "text", lambda: _undo_text(hwnd)
    return None


# --- обратные действия ---

def _restore_volume(level: int, muted: bool) -> str:
    from core import audio
    audio.restore_volume(level, muted)
    return f"вернул{END} громкость {level} процентов" + (", звук выключен" if muted else "")


def _restore_brightness(level: int) -> str:
    from core import apps
    apps.set_brightness(level)
    return f"вернул{END} яркость {level} процентов"


def _reopen_tab() -> str:
    from core import system
    return system.shortcut("reopen_tab")


def _reopen_closed(name: str, result: str) -> Callable[[], str]:
    """close_app закрывает и вкладки («закрой ютуб»), и папки, и приложения - смотрю по ответу."""
    from core import apps
    if "вкладк" in result:
        return _reopen_tab
    if "папку" in result:
        return lambda: apps.open_folder(name)
    if name.strip().lower() in ("браузер", "browser"):
        return lambda: apps.open_browser()
    return lambda: apps.open_app(name)


def _active_window_info() -> dict | None:
    """Чем открыть заново то, что сейчас на переднем плане: путь к программе или папка проводника."""
    import psutil
    import ctypes
    from ctypes import wintypes

    hwnd = _user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc = psutil.Process(pid.value)
    exe = proc.name().lower()
    info = {"title": foreground_title(), "exe": proc.exe(), "folder": None}
    if exe == "explorer.exe":
        info["folder"] = _explorer_folder(hwnd)
        if not info["folder"]:
            return None
    elif exe == "applicationframehost.exe":       # приложения из Store: общий процесс-рамка, путь не тот
        return None
    return info


def _explorer_folder(hwnd: int) -> str | None:
    """Путь папки в окне проводника - через Shell.Application."""
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        windows = shell.Windows()
        for i in range(windows.Count):
            w = windows.Item(i)
            try:
                if w is not None and int(w.HWND) == hwnd:
                    return str(w.Document.Folder.Self.Path)
            except Exception:
                continue
        return None
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def _reopen_window(info: dict) -> str:
    if info["folder"]:
        os.startfile(info["folder"])
    elif "\\windowsapps\\" in info["exe"].lower():     # из Store: напрямую не запустить, только через Пуск
        from core import apps
        return apps.open_app(Path(info["exe"]).stem)
    else:
        subprocess.Popen([info["exe"]])
    return f"открыл{END} обратно «{info['title']}»"


def _undo_text(hwnd: int) -> str:
    """Ctrl+Z в том окне, куда вставляли текст, даже если человек уже переключился."""
    from core import system
    if hwnd and _user32.IsWindow(hwnd) and _user32.GetForegroundWindow() != hwnd:
        _force_foreground(hwnd)
        time.sleep(0.15)
    system.shortcut("undo")
    return f"убрал{END} вставленный текст"
