"""Нажатие кнопок в окнах через UI Automation.

Нужно для Яндекс Музыки: пока в ней ни разу не нажали play, Windows не видит её как плеер,
и ни медиа-клавиши, ни SMTC на неё не действуют. А кнопку в окне нажать можно всегда.
Так же ставится лайк: у Windows нет команды «нравится», а кнопка «Нравится» в окне есть."""

from __future__ import annotations

import gc
import time

from core.util import log
from core.winapi import _find_procs, _windows_of


def _uia():
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient

    return UIAutomationClient


def _find_buttons(exes: set[str], names: tuple[str, ...], timeout: float) -> list | None:
    """Кнопки окна процесса exes в порядке дерева, как только среди них появится одна из names.
    None - окна нет или нужной кнопки так и не появилось."""
    import comtypes.client

    ua = _uia()
    pids = {p.pid for p in _find_procs({e.lower() for e in exes}, set())}
    windows = _windows_of(pids) if pids else []
    if not windows:
        return None
    uia = comtypes.client.CreateObject(ua.CUIAutomation, interface=ua.IUIAutomation)
    root = uia.ElementFromHandle(windows[0][0])
    condition = uia.CreatePropertyCondition(ua.UIA_ControlTypePropertyId, ua.UIA_ButtonControlTypeId)
    deadline = time.time() + timeout
    while True:
        # Chromium/Electron включают доступность только после первого запроса, первые разы кнопок нет
        found = root.FindAll(ua.TreeScope_Descendants, condition)
        elements = [found.GetElement(i) for i in range(found.Length)]
        buttons = [(element.CurrentName, element) for element in elements]
        if any(name in names for name, _ in buttons):
            return buttons
        if time.time() > deadline:
            log("Кнопки", f"не нашла {names[0]!r}; есть: {', '.join(n for n, _ in buttons[:20]) or 'ничего'}")
            return None
        time.sleep(0.5)


def _press(exes: set[str], names: tuple[str, ...], timeout: float) -> str | None:
    ua = _uia()
    buttons = dict(reversed(_find_buttons(exes, names, timeout) or []))    # первая кнопка с таким именем
    for name in names:
        if name in buttons:
            pattern = buttons[name].GetCurrentPattern(ua.UIA_InvokePatternId)
            pattern.QueryInterface(ua.IUIAutomationInvokePattern).Invoke()
            return name
    return None


def _toggle(exes: set[str], name: str, state: bool, timeout: float) -> bool | None:
    ua = _uia()
    buttons = [element for n, element in _find_buttons(exes, (name,), timeout) or [] if n == name]
    if not buttons:
        return None
    # Такая же кнопка бывает и в списке треков на странице; панель плеера в дереве идёт последней
    toggle = buttons[-1].GetCurrentPattern(ua.UIA_TogglePatternId).QueryInterface(ua.IUIAutomationTogglePattern)
    if bool(toggle.CurrentToggleState) == state:
        return False
    toggle.Toggle()
    time.sleep(0.3)
    log("Кнопки", f"«{name}»: {'вкл' if toggle.CurrentToggleState else 'выкл'} после нажатия")
    return True


def _with_com(fn, *args):
    """COM открывается и закрывается здесь же, а объекты освобождаются раньше - иначе падение в _ctypes."""
    import comtypes

    comtypes.CoInitialize()
    try:
        return fn(*args)
    except Exception as e:
        log("Кнопки", f"UI Automation не сработал: {e}")
        return None
    finally:
        gc.collect()                          # объекты из fn уже не нужны - освобождаем до выхода из COM
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def press_button(exes: set[str], names: tuple[str, ...], timeout: float = 3.0) -> str | None:
    """Нажимает первую найденную кнопку из names в окне процесса exes, возвращает её имя или None."""
    return _with_com(_press, exes, names, timeout)


def toggle_button(exes: set[str], name: str, state: bool, timeout: float = 3.0) -> bool | None:
    """Ставит кнопку-переключатель name («Нравится») в положение state.
    True - нажала, False - она уже так стояла, None - кнопки нет."""
    return _with_com(_toggle, exes, name, state, timeout)
