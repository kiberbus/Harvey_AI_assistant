"""Windows: клавиатура, буфер обмена, окна и процессы."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from core.util import (  # noqa: F401
    psutil,
)


# ───────────────────────── WINDOWS: КЛАВИАТУРА ─────────────────────────
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if _user32:
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT


def press_key(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def type_text(text: str) -> None:
    """Печатает текст в активное поле ввода (юникод напрямую, буфер обмена не трогаем)."""
    text = text.replace("\r", " ").replace("\n", " ")
    raw = text.encode("utf-16-le")
    codes = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    events = []
    for code in codes:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            ev = INPUT(type=INPUT_KEYBOARD)
            ev.ki = KEYBDINPUT(0, code, flags, 0, 0)
            events.append(ev)
    for i in range(0, len(events), 100):
        chunk = events[i:i + 100]
        sent = _user32.SendInput(len(chunk), (INPUT * len(chunk))(*chunk), ctypes.sizeof(INPUT))
        if sent != len(chunk):
            raise OSError(f"SendInput отправил {sent} из {len(chunk)} (код {ctypes.get_last_error()})")
        time.sleep(0.005)


# ───────────────────────── WINDOWS: БУФЕР ОБМЕНА И АКТИВНОЕ ОКНО ─────────────────────────
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_CONTROL, VK_V = 0x11, 0x56

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
if _user32:
    _kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)


def _open_clipboard() -> bool:
    for _ in range(10):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    data = (text + "\0").encode("utf-16-le")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = _kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _open_clipboard():
        raise OSError("не удалось открыть буфер обмена")
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise OSError(f"SetClipboardData (код {ctypes.get_last_error()})")
    finally:
        _user32.CloseClipboard()


def paste_text(text: str) -> None:
    """Вставляет текст через Ctrl+V и возвращает прежний текст буфера обмена."""
    previous = _get_clipboard_text()
    _set_clipboard_text(text)
    time.sleep(0.05)
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, 0, 0)
    _user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)                      # даём приложению успеть вставить
    if previous is not None:
        _set_clipboard_text(previous)


VK_C, VK_SHIFT, VK_LEFT = 0x43, 0x10, 0x25
if _user32:
    _user32.GetClipboardSequenceNumber.restype = wintypes.DWORD


def copy_selection(timeout: float = 0.6) -> str:
    """Копирует выделенный текст активного окна (Ctrl+C) и возвращает прежний буфер обмена.
    Ничего не выделено — пустая строка."""
    previous = _get_clipboard_text()
    seq = _user32.GetClipboardSequenceNumber()
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_C, 0, 0, 0)
    _user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    deadline = time.time() + timeout
    while _user32.GetClipboardSequenceNumber() == seq:       # буфер не изменился — копировать нечего
        if time.time() > deadline:
            return ""
        time.sleep(0.02)
    time.sleep(0.05)                     # приложение может класть данные в несколько форматов по очереди
    text = _get_clipboard_text() or ""
    if previous is not None:
        _set_clipboard_text(previous)
    return text


def select_back(chars: int) -> None:
    """Выделяет chars символов левее курсора (Shift+←) — так можно заменить только что записанный текст."""
    _user32.keybd_event(VK_SHIFT, 0, 0, 0)
    try:
        for _ in range(chars):
            _user32.keybd_event(VK_LEFT, 0, 0, 0)
            _user32.keybd_event(VK_LEFT, 0, KEYEVENTF_KEYUP, 0)
    finally:
        _user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


DWMWA_EXTENDED_FRAME_BOUNDS = 9
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_dwmapi = ctypes.WinDLL("dwmapi") if sys.platform == "win32" else None
if _user32:
    _user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    _user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)


def foreground_rect() -> tuple[int, int, int, int] | None:
    """Границы активного окна в настоящих пикселях экрана (как их видит снимок Pillow) или None."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or _user32.IsIconic(hwnd):
        return None
    old = _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    try:
        rect = wintypes.RECT()
        if _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                         ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    finally:
        if old:
            _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    if rect.right - rect.left < 200 or rect.bottom - rect.top < 150:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def foreground_title() -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(_user32.GetForegroundWindow(), buf, 256)
    return buf.value or "без названия"


# ───────────────────────── WINDOWS: ОКНА ─────────────────────────
GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 4, -20, 0x00000080
SW_SHOW, SW_RESTORE = 5, 9
VK_MENU = 0x12
VK_LWIN, VK_TAB, VK_D, VK_SNAPSHOT = 0x5B, 0x09, 0x44, 0x2C
WM_CLOSE = 0x0010
SW_MINIMIZE, SW_MAXIMIZE = 6, 3

if _user32:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
    _user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _user32.IsIconic.argtypes = (wintypes.HWND,)
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, wintypes.LPDWORD)
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongW.restype = wintypes.LONG
    _user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    _user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _windows_of(pids: set[int]) -> list[tuple[int, bool]]:
    """Окна верхнего уровня процессов: (hwnd, видимо ли). Видимые (в т.ч. свёрнутые) идут первыми."""
    found: list[tuple[int, bool]] = []

    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or _user32.GetWindow(hwnd, GW_OWNER):
            return True
        if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        visible = bool(_user32.IsWindowVisible(hwnd))
        if not visible:
            # скрытые окна (приложение в трее) берём, только если это настоящее окно, а не служебное
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left < 200 or rect.bottom - rect.top < 150:
                return True
        found.append((hwnd, visible))
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return sorted(found, key=lambda w: not w[1])


def _force_foreground(hwnd: int) -> None:
    """Windows не любит, когда фоновая программа забирает фокус, поэтому действуем в два приёма."""
    fg = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    this_thread = _kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != this_thread
                    and _user32.AttachThreadInput(this_thread, fg_thread, True))
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        if _user32.GetForegroundWindow() != hwnd:      # не вышло — «будим» систему нажатием Alt
            _user32.keybd_event(VK_MENU, 0, 0, 0)
            _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(this_thread, fg_thread, False)


def bring_to_front(exes: set[str]) -> str | None:
    """Если у приложения уже есть окно — разворачивает его и выводит вперёд.
    Возвращает "restored" (было свёрнуто/скрыто), "shown" (просто показано) или None (окна нет)."""
    if psutil is None or not exes:
        return None
    pids = {p.pid for p in _find_procs(exes, set())}
    if not pids:
        return None
    windows = _windows_of(pids)
    if not windows:
        return None
    hwnd, visible = windows[0]
    minimized = bool(_user32.IsIconic(hwnd))
    _user32.ShowWindow(hwnd, SW_RESTORE if minimized else SW_SHOW)
    _force_foreground(hwnd)
    return "restored" if (minimized or not visible) else "shown"


def _find_procs(exes: set[str], skip_pids: set[int]) -> list:
    return [p for p in psutil.process_iter(["pid", "name"])
            if (p.info.get("name") or "").lower() in exes and p.info["pid"] not in skip_pids]
