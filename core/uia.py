"""Нажатие кнопок в окнах через UI Automation.

Нужно для Яндекс Музыки: пока в ней ни разу не нажали play, Windows не видит её как плеер,
и ни медиа-клавиши, ни SMTC на неё не действуют. А кнопку в окне нажать можно всегда
(свёрнутое или закрытое другими окно на полсекунды показываю - см. _shown).
Так же ставится лайк: у Windows нет команды «нравится», а кнопка «Нравится» в окне есть.

И голосом: «нажми подписаться», «кликни на настройки» - click() ищет надпись в активном окне и нажимает."""

from __future__ import annotations

import ctypes
import difflib
import re
import time
from contextlib import contextmanager
from ctypes import wintypes

from core.util import (
    END,
    FAIL,
    _LATIN,
    _translit,
    log,
)
from core.browser import (
    _CASE_ENDING_RE,
    _class_of,
)
from core.winapi import (
    GW_OWNER,
    GWL_EXSTYLE,
    SWP_NOACTIVATE,
    SWP_NOZORDER,
    WNDENUMPROC,
    _DpiAware,
    _find_procs,
    _user32,
    _windows_of,
    com_call,
)


def _uia():
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient

    return UIAutomationClient


SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 0, 4, 7
HWND_TOPMOST, HWND_NOTOPMOST = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
GW_HWNDPREV, WS_EX_TOPMOST = 3, 0x0008
_KEEP = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE         # только место в стопке окон, фокус не трогаю


def _main_window(exes: set[str]) -> int | None:
    pids = {p.pid for p in _find_procs({e.lower() for e in exes}, set())}
    windows = _windows_of(pids) if pids else []
    return windows[0][0] if windows else None


def _topmost(hwnd: int) -> bool:
    return bool(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def _above(hwnd: int) -> int | None:
    """Ближайшее видимое обычное окно над hwnd: потом верну hwnd под него, на старое место."""
    h = _user32.GetWindow(hwnd, GW_HWNDPREV)
    while h:
        if _user32.IsWindowVisible(h) and not _topmost(h):
            return h
        h = _user32.GetWindow(h, GW_HWNDPREV)
    return None


def _nudge(hwnd: int) -> None:
    """Сдвиг на пиксель и обратно: Chromium пересчитывает, видно ли окно, только по событиям окна,
    а смену места в стопке («поверх всех») не замечает - закрытое раньше окно так и считалось скрытым."""
    rect = wintypes.RECT()
    with _DpiAware():                         # те же пиксели туда и обратно, без округления масштаба
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        _user32.SetWindowPos(hwnd, None, rect.left + 1, rect.top, 0, 0, flags)
        _user32.SetWindowPos(hwnd, None, rect.left, rect.top, 0, 0, flags)


@contextmanager
def _shown(hwnd: int):
    """Electron (Яндекс Музыка) отдаёт UI Automation кнопки страницы, только пока окно видно на экране.
    Свёрнутое или целиком закрытое другими окна он считает скрытым: в дереве 8 элементов, и «поставь лайк»
    не находил «Нравится», если окно не активное (из лога 9 октября). Поднять окно наверх Windows не даёт,
    пока активна другая программа, поэтому на полсекунды ставлю его «поверх всех» - без активации, фокус
    остаётся, где был, - а потом возвращаю как было: сворачиваю, прячу в трей или кладу на старое место."""
    if hwnd == _user32.GetForegroundWindow() or _topmost(hwnd):
        yield
        return
    iconic, visible = _user32.IsIconic(hwnd), _user32.IsWindowVisible(hwnd)
    above = None if iconic or not visible else _above(hwnd)
    if iconic or not visible:
        _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, _KEEP)
    _nudge(hwnd)
    try:
        yield
    finally:
        time.sleep(0.2)                       # нажатие должно дойти до страницы, пока она ещё видна
        _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, _KEEP)
        if iconic:
            _user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        elif not visible:
            _user32.ShowWindow(hwnd, SW_HIDE)
        elif above:
            _user32.SetWindowPos(hwnd, above, 0, 0, 0, 0, _KEEP)


def _find_buttons(hwnd: int, names: tuple[str, ...], timeout: float) -> list | None:
    """Кнопки окна hwnd в порядке дерева, как только среди них появится одна из names.
    None - нужной кнопки так и не появилось."""
    import comtypes.client

    ua = _uia()
    uia = comtypes.client.CreateObject(ua.CUIAutomation, interface=ua.IUIAutomation)
    root = uia.ElementFromHandle(hwnd)
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
    hwnd = _main_window(exes)
    if hwnd is None:
        return None
    with _shown(hwnd):
        buttons = dict(reversed(_find_buttons(hwnd, names, timeout) or []))    # первая кнопка с таким именем
        for name in names:
            if name in buttons:
                pattern = buttons[name].GetCurrentPattern(ua.UIA_InvokePatternId)
                pattern.QueryInterface(ua.IUIAutomationInvokePattern).Invoke()
                return name
    return None


def _toggle(exes: set[str], name: str, state: bool, timeout: float) -> bool | None:
    ua = _uia()
    hwnd = _main_window(exes)
    if hwnd is None:
        return None
    with _shown(hwnd):
        buttons = [element for n, element in _find_buttons(hwnd, (name,), timeout) or [] if n == name]
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
    """UI Automation в своём COM (winapi.com_call). Ошибка - значит, кнопки нет: None."""
    try:
        return com_call(fn, *args)
    except Exception as e:
        log("Кнопки", f"UI Automation не сработал: {e}")
        return None


def press_button(exes: set[str], names: tuple[str, ...], timeout: float = 3.0) -> str | None:
    """Нажимает первую найденную кнопку из names в окне процесса exes, возвращает её имя или None."""
    return _with_com(_press, exes, names, timeout)


def toggle_button(exes: set[str], name: str, state: bool, timeout: float = 3.0) -> bool | None:
    """Ставит кнопку-переключатель name («Нравится») в положение state.
    True - нажала, False - она уже так стояла, None - кнопки нет."""
    return _with_com(_toggle, exes, name, state, timeout)


# --- «Нажми подписаться»: найти надпись в активном окне и нажать ---

NOT_FOUND = f"{FAIL}не вижу на экране"
AMBIGUOUS = f"{FAIL}на экране несколько похожих на"
MISSED = (NOT_FOUND, AMBIGUOUS)               # так ничего и не нажала
_CONTROLS = ("Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "CheckBox", "RadioButton",
             "SplitButton", "TreeItem", "ComboBox", "DataItem")
# Кнопки заголовка окна и крестики вкладок браузера не нажимаю: в Firefox они первые в дереве, и «нажми закрыть»
# закрыло бы браузер, а не окошко на странице. Для окон есть «сверни», «закрой окно», «закрой вкладку»
_CAPTION = {"закрыть", "свернуть", "развернуть", "восстановить", "свернуть в окно", "система"}
_CAPTION_BAND = 80                            # высота заголовка и полосы вкладок, px
_HOTKEY_RE = re.compile(r"\s*\([^()]{1,25}\)\s*$")      # «Пауза (k)», «Открыть новую вкладку (Ctrl+T)»
_WORD_RE = re.compile(r"[a-zа-я0-9]+")
# Chromium, Electron и Firefox строят дерево элементов только после первого запроса: сначала в нём 3 кнопки
# заголовка, через полсекунды - вся страница
_LAZY_CLASSES = ("Chrome_WidgetWin", "MozillaWindowClass")
_LAZY_WAIT = 1.5
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004


def words(text: str) -> list[str]:
    """Слова надписи для сравнения: нижний регистр, латиница по-русски («YouTube» - «ютуб»),
    без падежных окончаний («нажми на корзину» - «Корзина»)."""
    text = _HOTKEY_RE.sub("", text or "").lower().replace("ё", "е")
    found: list[str] = []
    for word in _WORD_RE.findall(text):
        if word.isascii() and word.isalpha():
            word = _LATIN.get(word) or _translit(word)
        found += [_CASE_ENDING_RE.sub("", w) for w in word.split()]
    return found


def match_score(query: list[str], name: list[str], control: bool = True) -> float:
    """Насколько надпись name похожа на сказанное query, 0 - не она. 4 - совпала целиком; 3 - начало надписи
    («подписаться» - «Подписаться на канал»); 2 - внутри короткой надписи («настройки» - «Открыть настройки»);
    1 - сказали больше, чем написано («пропустить рекламу» - «Пропустить»); меньше 1 - похоже на ослышку.
    Простой текст (не кнопку) беру только целиком или по началу: иначе нажала бы абзац, где есть это слово."""
    if not query or not name:
        return 0
    q, n = len(query), len(name)
    if name == query:
        return 4
    if name[:q] == query:
        return 3 if control or n <= q + 4 else 0
    if not control:
        return 0
    if n <= q + 3 and any(name[i:i + q] == query for i in range(1, n - q + 1)):
        return 2
    if query[:n] == name:
        return 1
    said, shown = " ".join(query), " ".join(name)
    ratio = difflib.SequenceMatcher(None, said, shown).ratio()
    return ratio if len(said) >= 5 and ratio >= 0.8 else 0


def _popups(fg: int) -> list[int]:
    """Открытые меню и всплывающие окна активной программы. Они поверх окна, поэтому при равных надписях
    выигрывают они: «нажми копировать» при открытом контекстном меню - пункт меню, а не кнопка на ленте."""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    found: list[int] = []

    def callback(hwnd, _lparam):
        if hwnd != fg and _user32.IsWindowVisible(hwnd):
            owner = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid.value and (_class_of(hwnd) == "#32768" or _user32.GetWindow(hwnd, GW_OWNER) == fg):
                found.append(hwnd)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def _candidates(uia, ua, hwnd: int) -> list[tuple[str, bool, object, object]]:
    """(надпись, кнопка ли это, элемент, прямоугольник) - всё видимое и доступное, что можно нажать,
    и простой текст. Свойства берутся одним запросом (кэш): на странице бывают сотни ссылок."""
    window = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(window))
    cache = uia.CreateCacheRequest()
    for prop in (ua.UIA_NamePropertyId, ua.UIA_ControlTypePropertyId, ua.UIA_BoundingRectanglePropertyId):
        cache.AddProperty(prop)
    types = uia.CreatePropertyCondition(ua.UIA_ControlTypePropertyId, ua.UIA_TextControlTypeId)
    for control in _CONTROLS:
        types = uia.CreateOrCondition(types, uia.CreatePropertyCondition(
            ua.UIA_ControlTypePropertyId, getattr(ua, f"UIA_{control}ControlTypeId")))
    visible = uia.CreateAndCondition(uia.CreatePropertyCondition(ua.UIA_IsOffscreenPropertyId, False),
                                     uia.CreatePropertyCondition(ua.UIA_IsEnabledPropertyId, True))
    found = uia.ElementFromHandle(hwnd).FindAllBuildCache(
        ua.TreeScope_Descendants, uia.CreateAndCondition(visible, types), cache)
    result = []
    for i in range(found.Length):
        element = found.GetElement(i)
        name, rect = element.CachedName, element.CachedBoundingRectangle
        if not name or rect.right <= rect.left or rect.bottom <= rect.top:
            continue
        if name.strip().lower() in _CAPTION and rect.top < window.top + _CAPTION_BAND:
            continue
        result.append((name, element.CachedControlType != ua.UIA_TextControlTypeId, element, rect))
    return result


def _center_distance(rect, hwnd: int) -> int:
    window = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(window))
    return (abs((rect.left + rect.right) - (window.left + window.right)) // 2
            + abs((rect.top + rect.bottom) - (window.top + window.bottom)) // 2)


def _best(uia, ua, roots: list[int], query: list[str]) -> tuple[list[tuple], list[str]]:
    """Подходящие надписи из окон roots, лучшие первыми: (очки, кнопка, надпись, элемент, прямоугольник, окно),
    и все надписи кнопок - для лога. При равных очках кнопка раньше текста, меню раньше окна,
    а из одинаковых надписей - та, что ближе к середине окна: туда человек и смотрит."""
    scored, names = [], []
    for order, hwnd in enumerate(roots):
        for name, control, element, rect in _candidates(uia, ua, hwnd):
            if control:
                names.append(name)
            score = match_score(query, words(name), control)
            if score:
                key = (score, control, -order, -_center_distance(rect, hwnd))
                scored.append((key, (score, control, name, element, rect, order)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [found for _, found in scored], names


def _ambiguous(found: list[tuple]) -> bool:
    """Неточное совпадение («закрыть вкладку» - «Закрыть 1 вкладку» на каждой вкладке) у нескольких
    одинаково подходящих - не угадываю."""
    if len(found) < 2:
        return False
    (score, control, _, _, _, order), (score2, control2, _, _, _, order2) = found[0], found[1]
    return score < 3 and (score, control, order) == (score2, control2, order2)


def _mouse_click(rect) -> None:
    """Щелчок мышью в середину элемента, потом курсор возвращаю на место."""
    old = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(old))
    _user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.05)
    _user32.SetCursorPos(old.x, old.y)


# Как элемент «нажимается» без мыши, по порядку: кнопка и ссылка, флажок, вкладка и пункт списка,
# раскрывающийся список и меню. «Действие по умолчанию» (LegacyIAccessible) не беру: у текста и пунктов
# списка на странице оно часто ничего не делает, а Харви сказала бы «нажала» - лучше честно мышью
_PRESS = (("Invoke", "Invoke"), ("Toggle", "Toggle"), ("SelectionItem", "Select"), ("ExpandCollapse", "Expand"))


def _press_element(ua, element, rect, control: bool) -> str:
    """Нажимает элемент: через UI Automation, а если он этого не умеет (простой текст) - мышью."""
    if control:
        for pattern, method in _PRESS:
            try:
                found = element.GetCurrentPattern(getattr(ua, f"UIA_{pattern}PatternId"))
                if found:
                    getattr(found.QueryInterface(getattr(ua, f"IUIAutomation{pattern}Pattern")), method)()
                    return pattern
            except Exception:                 # шаблон есть, но не сработал - пробую следующий
                continue
    _mouse_click(rect)
    return "мышь"


def _click(query: list[str]) -> str | None:
    """Надпись нажатого элемента; "" - подходят несколько, None - ничего не нашла."""
    import comtypes.client

    ua = _uia()
    uia = comtypes.client.CreateObject(ua.CUIAutomation, interface=ua.IUIAutomation)
    fg = _user32.GetForegroundWindow()
    if not fg:
        return None
    roots = _popups(fg) + [fg]
    deadline = time.time() + _LAZY_WAIT
    found, names = _best(uia, ua, roots, query)
    while not found and _class_of(fg).startswith(_LAZY_CLASSES) and time.time() < deadline:
        time.sleep(0.4)
        found, names = _best(uia, ua, roots, query)
    said = " ".join(query)
    if not found:
        log("Нажатие", f"не нашла {said!r}; есть: {', '.join(n[:30] for n in names[:15]) or 'ничего'}")
        return None
    if _ambiguous(found):
        log("Нажатие", f"{said!r}: несколько похожих - {', '.join(f[2][:30] for f in found[:5])}")
        return ""
    score, control, name, element, rect, _ = found[0]
    how = _press_element(ua, element, rect, control)
    log("Нажатие", f"{said!r} → «{name[:60]}» (совпадение {score:.2f}, {how})")
    return name


def click(name: str) -> str:
    """«Нажми подписаться», «кликни на настройки»: ищет надпись в активном окне и в его открытом меню -
    кнопку, ссылку, пункт меню, вкладку или просто текст - и нажимает."""
    query = words(name)
    if not query:
        return f"{FAIL}не понял{END}, что нажать"
    with _DpiAware():                         # координаты для мыши - в настоящих пикселях
        pressed = _with_com(_click, query)
    if pressed == "":
        return f"{AMBIGUOUS} «{name}», скажите точнее"
    if not pressed:
        return f"{NOT_FOUND} «{name}»"
    return f"нажал{END} «{_HOTKEY_RE.sub('', pressed)[:40]}»"
