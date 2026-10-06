"""Нажатие кнопок в окнах через UI Automation.

Нужно для Яндекс Музыки: пока в ней ни разу не нажали play, Windows не видит её как плеер,
и ни медиа-клавиши, ни SMTC на неё не действуют. А кнопку в окне нажать можно всегда."""

from __future__ import annotations

import gc
import time

from core.util import log
from core.winapi import _find_procs, _windows_of


def _press(exes: set[str], names: tuple[str, ...], timeout: float) -> str | None:
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen.UIAutomationClient import (
        CUIAutomation, IUIAutomation, IUIAutomationInvokePattern, TreeScope_Descendants,
        UIA_ButtonControlTypeId, UIA_ControlTypePropertyId, UIA_InvokePatternId,
    )

    pids = {p.pid for p in _find_procs({e.lower() for e in exes}, set())}
    windows = _windows_of(pids) if pids else []
    if not windows:
        return None
    uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
    root = uia.ElementFromHandle(windows[0][0])
    condition = uia.CreatePropertyCondition(UIA_ControlTypePropertyId, UIA_ButtonControlTypeId)
    deadline = time.time() + timeout
    while True:
        # Chromium/Electron включают доступность только после первого запроса, первые разы кнопок нет
        found = root.FindAll(TreeScope_Descendants, condition)
        buttons = {}
        for i in range(found.Length):
            element = found.GetElement(i)
            buttons.setdefault(element.CurrentName, element)
        for name in names:
            if name in buttons:
                pattern = buttons[name].GetCurrentPattern(UIA_InvokePatternId)
                pattern.QueryInterface(IUIAutomationInvokePattern).Invoke()
                return name
        if time.time() > deadline:
            log("Кнопки", f"не нашла {names[0]!r}; есть: {', '.join(list(buttons)[:20]) or 'ничего'}")
            return None
        time.sleep(0.5)


def press_button(exes: set[str], names: tuple[str, ...], timeout: float = 3.0) -> str | None:
    """Нажимает первую найденную кнопку из names в окне процесса exes, возвращает её имя или None.
    COM открывается и закрывается здесь же, а объекты освобождаются раньше - иначе падение в _ctypes."""
    import comtypes

    comtypes.CoInitialize()
    try:
        return _press(exes, names, timeout)
    except Exception as e:
        log("Кнопки", f"UI Automation не сработал: {e}")
        return None
    finally:
        gc.collect()                          # объекты из _press уже не нужны - освобождаем до выхода из COM
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
