"""Браузер: вкладки и переходы.

Раньше клавиши (Ctrl+W, Ctrl+Tab) уходили в то окно, что сейчас активно. Если в фокусе был не браузер,
«закрой вкладку» закрывала что-то в другой программе или не делала ничего. Теперь:
- полосу вкладок Firefox и Chrome Windows отдаёт через UI Automation: у каждой вкладки есть название,
  признак «выбрана» и кнопка «Закрыть». По ним закрываю соседнюю вкладку или вкладку по названию,
  не трогая клавиатуру и фокус («закрой предыдущую вкладку», «закрой ютуб», «перейди на вкладку гитхаб»);
- для клавиш браузер сначала выводится вперёд, если в фокусе другое окно."""

from __future__ import annotations

import ctypes
import gc
import re
import time
import urllib.parse
from ctypes import wintypes

from config import (
    BROWSER_EXES,
    SITES,
)
from phrases import (
    SITE_ALIASES,
)
from core.util import (  # noqa: F401
    END,
    FAIL,
    INFO,
    _plural,
    log,
    psutil,
)
from core.winapi import (  # noqa: F401
    SW_RESTORE,
    _find_procs,
    _force_foreground,
    _user32,
    _windows_of,
)

TAB_CONTEXT_SECONDS = 30      # столько после «следующая вкладка» голое «следующая» тоже про вкладки
_last_tab_action = 0.0


def mark_tab_action() -> None:
    global _last_tab_action
    _last_tab_action = time.time()


def in_tab_context() -> bool:
    """Только что переключали вкладки? Тогда «предыдущая», «ещё» - тоже про вкладки, а не про трек."""
    return time.time() - _last_tab_action < TAB_CONTEXT_SECONDS


def _exe_of(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower() if psutil else ""
    except Exception:
        return ""


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    _user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def foreground_kind() -> str:
    """Что в фокусе: "browser", "explorer" (окно проводника - у него тоже назад/вперёд и вкладки) или ""."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    exe = _exe_of(hwnd)
    if exe in BROWSER_EXES:
        return "browser"
    if exe == "explorer.exe" and _class_of(hwnd) == "CabinetWClass":     # не рабочий стол и не панель задач
        return "explorer"
    return ""


def browser_windows() -> list[int]:
    """Окна браузеров сверху вниз (EnumWindows идёт от верхнего окна к нижнему)."""
    if psutil is None:
        return []
    pids = {p.pid for p in _find_procs(BROWSER_EXES, set())}
    return [hwnd for hwnd, visible in _windows_of(pids) if visible] if pids else []


def focus_browser() -> bool:
    """Выводит браузер вперёд, если в фокусе он не стоит. False - браузер не открыт или не дался."""
    if foreground_kind() == "browser":
        return True
    windows = browser_windows()
    if not windows:
        return False
    hwnd = windows[0]
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    _force_foreground(hwnd)
    time.sleep(0.15)
    return _user32.GetForegroundWindow() == hwnd


# --- вкладки через UI Automation ---

_CLOSE_NAMES = re.compile(r"^(?:закрыть|close)\b", re.IGNORECASE)


class _Tab:
    def __init__(self, hwnd: int, element, close_button, ua) -> None:
        self.hwnd, self.element, self.close_button, self._ua = hwnd, element, close_button, ua
        self.title = element.CurrentName or ""

    @property
    def selected(self) -> bool:
        try:
            pattern = self.element.GetCurrentPattern(self._ua.UIA_SelectionItemPatternId)
            return bool(pattern.QueryInterface(self._ua.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        except Exception:
            return False

    def close(self) -> None:
        pattern = self.close_button.GetCurrentPattern(self._ua.UIA_InvokePatternId)
        pattern.QueryInterface(self._ua.IUIAutomationInvokePattern).Invoke()

    def select(self) -> None:
        pattern = self.element.GetCurrentPattern(self._ua.UIA_SelectionItemPatternId)
        pattern.QueryInterface(self._ua.IUIAutomationSelectionItemPattern).Select()


def _window_tabs(uia, ua, hwnd: int) -> list[_Tab]:
    """Вкладки окна браузера по порядку. Вкладками UIA бывают и элементы страницы (фильтры на YouTube),
    настоящие вкладки браузера отличаю по кнопке «Закрыть» внутри."""
    root = uia.ElementFromHandle(hwnd)
    found = root.FindAll(ua.TreeScope_Descendants,
                         uia.CreatePropertyCondition(ua.UIA_ControlTypePropertyId, ua.UIA_TabItemControlTypeId))
    button = uia.CreatePropertyCondition(ua.UIA_ControlTypePropertyId, ua.UIA_ButtonControlTypeId)
    tabs = []
    for i in range(found.Length):
        element = found.GetElement(i)
        buttons = element.FindAll(ua.TreeScope_Children, button)
        close = next((buttons.GetElement(j) for j in range(buttons.Length)
                      if _CLOSE_NAMES.match(buttons.GetElement(j).CurrentName or "")), None)
        if close is not None:
            tabs.append(_Tab(hwnd, element, close, ua))
    return tabs


def _with_tabs(fn):
    """Вызывает fn(список окон с вкладками) внутри своего COM, как core/uia.py: иначе падение в _ctypes."""
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as ua

        uia = comtypes.client.CreateObject(ua.CUIAutomation, interface=ua.IUIAutomation)
        return fn([(hwnd, _window_tabs(uia, ua, hwnd)) for hwnd in browser_windows()])
    except Exception as e:
        log("Вкладки", f"UI Automation не сработал: {e}")
        return None
    finally:
        gc.collect()
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def _keywords(name: str) -> set[str]:
    """По каким словам искать вкладку: «ютуб» → youtube, «гитхаб» → github."""
    low = " ".join((name or "").lower().split())
    words = {low}
    for site, pattern in SITE_ALIASES.items():
        if re.fullmatch(pattern, low):
            host = urllib.parse.urlparse(SITES[site]).hostname or ""
            words |= {site, host.removeprefix("www.").split(".")[0]}
    return {w for w in words if len(w) >= 3}


def _matches(tab: _Tab, words: set[str]) -> bool:
    title = tab.title.lower()
    return any(w in title for w in words)


def _short(title: str) -> str:
    title = re.sub(r"^\(\d+\)\s*", "", title)             # «(2416) YouTube» - счётчик уведомлений
    return f"«{title[:50]}»" if title else "вкладку"


def _tabs_word(n: int) -> str:
    return f"{n} {_plural(n, 'вкладку', 'вкладки', 'вкладок')}"


def close_tab(which: str = "current", name: str = "") -> str:
    """Закрывает вкладку: current, previous, next, others (все, кроме текущей) или по названию (name).
    Кнопкой «Закрыть» на самой вкладке, поэтому фокус и клавиатура не нужны."""
    which = (which or "current").strip().lower()
    words = _keywords(name) if name else set()

    def act(windows):
        if not windows:
            return f"{FAIL}браузер не открыт"
        if words:
            found = [t for _, tabs in windows for t in tabs if _matches(t, words)]
            if not found:
                return f"{INFO}вкладки «{name}» нет"
            for tab in reversed(found):
                tab.close()
            return f"закрыл{END} " + (f"вкладку {_short(found[0].title)}" if len(found) == 1
                                      else f"{_tabs_word(len(found))} {name}")
        _, tabs = windows[0]                              # верхнее окно браузера
        if not tabs:
            return None
        current = next((i for i, t in enumerate(tabs) if t.selected), None)
        if current is None:
            return None
        if which == "others":
            others = [t for i, t in enumerate(tabs) if i != current]
            if not others:
                return f"{INFO}других вкладок нет"
            for tab in reversed(others):
                tab.close()
            return f"закрыл{END} {_tabs_word(len(others))}, осталась {_short(tabs[current].title)}"
        index = {"previous": current - 1, "next": current + 1}.get(which, current)
        if not 0 <= index < len(tabs):
            return f"{INFO}{'слева' if which == 'previous' else 'справа'} вкладок нет"
        tabs[index].close()
        return f"закрыл{END} вкладку {_short(tabs[index].title)}"

    result = _with_tabs(act)
    if result is None and not words and which == "current":
        # UI Automation не видит вкладки (другой браузер) - тогда клавишами
        if not focus_browser():
            return f"{FAIL}браузер не открыт"
        from core import system
        return system.press_chords("close_tab")
    return result if result is not None else f"{FAIL}не вижу вкладки браузера"


def switch_tab(name: str) -> str:
    """«Перейди на вкладку ютуб»: выбирает вкладку по названию и выводит её окно вперёд."""
    words = _keywords(name)

    def act(windows):
        if not windows:
            return f"{FAIL}браузер не открыт"
        found = next((t for _, tabs in windows for t in tabs if _matches(t, words)), None)
        if found is None:
            return ""
        found.select()
        if _user32.IsIconic(found.hwnd):
            _user32.ShowWindow(found.hwnd, SW_RESTORE)
        _force_foreground(found.hwnd)
        mark_tab_action()
        return f"переключил{END} на {_short(found.title)}"

    result = _with_tabs(act)
    if result == "":
        return f"{INFO}вкладки «{name}» нет"
    return result if result is not None else f"{FAIL}не вижу вкладки браузера"


def list_tabs() -> str:
    """«Какие вкладки открыты»: сколько их и первые названия."""
    def act(windows):
        tabs = [t for _, ts in windows for t in ts]
        if not tabs:
            return f"{INFO}браузер не открыт" if not windows else None
        titles = [_short(t.title) for t in tabs[:5]]
        more = f" и ещё {len(tabs) - 5}" if len(tabs) > 5 else ""
        return f"{INFO}вкладок открыто: {len(tabs)}. {', '.join(titles)}{more}"

    result = _with_tabs(act)
    return result if result is not None else f"{FAIL}не вижу вкладки браузера"
