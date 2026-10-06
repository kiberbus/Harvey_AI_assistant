"""Приложения, браузер, папки, окна, яркость, диктовка."""

from __future__ import annotations

import ctypes
import difflib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path

from config import (
    BROWSER_EXE,
    BROWSER_EXES,
    DICTATION_MODE,
    DOCUMENT_EXES,
    FOLDERS,
    MUSIC_APPS,
    NAME_GROUPS,
    PROTECTED_EXES,
    QUIET_MODE,
    SITES,
    SPEAK_ERRORS,
)
from phrases import (
    FOLDER_ALIASES,
    SITE_ALIASES,
)
from core.util import (  # noqa: F401
    END,
    FAIL,
    INFO,
    MEDIA_TARGET_RES,
    OWN_PID,
    _cap,
    _clamp,
    log,
    psutil,
)
from core.winapi import (  # noqa: F401
    KEYEVENTF_KEYUP,
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    VK_CONTROL,
    VK_D,
    VK_LWIN,
    VK_MENU,
    VK_SNAPSHOT,
    VK_TAB,
    WM_CLOSE,
    WNDENUMPROC,
    _find_procs,
    _force_foreground,
    _user32,
    _windows_of,
    bring_to_front,
    foreground_title,
    paste_text,
    type_text,
)
from core.speech import (  # noqa: F401
    play_sound,
    speak,
)


def set_brightness(level: int) -> str:
    import screen_brightness_control as sbc

    level = _clamp(level)
    sbc.set_brightness(level)
    return f"установил{END} яркость на {level} процентов"


def change_brightness(delta: int) -> str:
    import screen_brightness_control as sbc

    current = sbc.get_brightness()
    current = current[0] if isinstance(current, list) else current
    new = _clamp(current + int(float(delta)))
    sbc.set_brightness(new)
    return f"{'повысил' if delta > 0 else 'понизил'}{END} яркость до {new} процентов"


last_dictation: dict = {}     # что и куда записал последним - для "перепиши вежливее"


def dictate(text: str) -> str:
    """Печатает текст в активное окно; если способ не сработал, пробует второй."""
    text = text.strip()
    text = text[:1].upper() + text[1:]
    log("Запись", f"в окно «{foreground_title()}»: {text}")
    time.sleep(0.15)
    methods = [paste_text, type_text] if DICTATION_MODE == "paste" else [type_text, paste_text]
    error: Exception | None = None
    for method in methods:
        try:
            method(text)
            last_dictation.update(text=text.replace("\r", " ").replace("\n", " "),
                                  hwnd=_user32.GetForegroundWindow(), at=time.time())
            return f"записал{END} текст"
        except Exception as e:
            error = e
            log("Запись", f"{method.__name__} не сработал: {e}")
    raise error  # type: ignore[misc]


_apps_cache: list[dict] | None = None


def _load_start_apps() -> list[dict]:
    global _apps_cache
    if _apps_cache is not None:
        return _apps_cache
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
    ]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    data = json.loads(out) if out else []
    if isinstance(data, dict):
        data = [data]
    _apps_cache = [a for a in data if a.get("Name") and a.get("AppID")]
    return _apps_cache


def _candidates(query: str) -> set[str]:
    q = query.strip().lower()
    result = {q}
    for group, _ in NAME_GROUPS:
        if q in group:
            result |= group
    return result


def find_app(query: str) -> dict | None:
    apps = _load_start_apps()
    names = {a["Name"].lower(): a for a in apps}
    cands = _candidates(query)
    for c in cands:
        if c in names:
            return names[c]
    hits = [n for n in names if any(c in n for c in cands)]
    if hits:
        return names[min(hits, key=len)]
    for c in cands:
        close = difflib.get_close_matches(c, list(names), n=1, cutoff=0.75)
        if close:
            return names[close[0]]
    return None


def default_browser_exe() -> str | None:
    if BROWSER_EXE:
        return BROWSER_EXE
    try:
        import winreg

        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as k:
            command = winreg.QueryValueEx(k, "")[0]
        m = re.match(r'\s*"([^"]+)"', command) or re.match(r"\s*(\S+)", command)
        return m.group(1) if m else None
    except Exception:
        return None


def open_app(name: str) -> str:
    app = find_app(name)
    if not app:
        return f"{FAIL}приложение «{name}» не найдено"

    # Если уже запущено - не запускаю второй экземпляр, а показываю окно
    try:
        state = bring_to_front(_target_exes(name))
    except Exception as e:
        log("Окно", f"не удалось развернуть: {e}")
        state = None
    if state == "restored":
        return f"развернул{END} {app['Name']}"
    if state == "shown":
        return f"показал{END} {app['Name']}"

    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["AppID"]])
    return f"открыл{END} {app['Name']}"


def open_browser(site: str = "", query: str = "") -> str:
    site = (site or "").strip().lower()
    query = (query or "").strip()
    url = None
    if site:
        if site not in SITES:
            return f"{FAIL}не знаю сайт «{site}»"
        url = SITES[site]
        if site == "youtube" and query:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
        elif site == "google" and query:
            url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    elif query:
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query)

    exe = default_browser_exe()
    if not url and exe:
        try:
            state = bring_to_front({Path(exe).name.lower()})
        except Exception:
            state = None
        if state:
            return f"{'развернул' if state == 'restored' else 'показал'}{END} браузер"
    if exe and os.path.exists(exe):
        subprocess.Popen([exe, url] if url else [exe])
    elif url:
        import webbrowser

        webbrowser.open(url)
    else:
        return f"{FAIL}не удалось определить браузер"

    detail = f"{site} и нашёл{END} «{query}»" if (site and query) else (site or query or "браузер")
    return f"открыл{END} {detail}"


def open_folder(name: str) -> str:
    path = FOLDERS.get((name or "").strip().lower())
    if not path:
        return f"{FAIL}не знаю папку «{name}»"
    os.startfile(path)
    return f"открыл{END} папку {name}"


RECENT_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"


def _lnk_targets(links: list[Path]) -> list[str]:
    """Куда ведут ярлыки. COM открываю здесь же: команду могут выполнить и не из главного потока."""
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        targets = []
        for lnk in links:
            try:
                targets.append(shell.CreateShortcut(str(lnk)).TargetPath or "")
            except Exception:
                targets.append("")
        del shell
        return targets
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def open_recent(show_all: bool = False) -> str:
    """Открывает последний изменённый файл из «Недавних» (%APPDATA%\\Microsoft\\Windows\\Recent).
    Там ярлыки и на папки, и на удалённые файлы - их пропускаю."""
    if show_all:
        os.startfile(str(RECENT_DIR))
        return f"открыл{END} недавние файлы"
    try:
        links = sorted(RECENT_DIR.glob("*.lnk"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    except OSError:
        links = []
    if not links:
        return f"{FAIL}недавних файлов нет: в Windows выключена история недавних файлов"
    for target in _lnk_targets(links):
        if target and os.path.isfile(target):
            os.startfile(target)
            return f"открыл{END} {Path(target).stem}"
    return f"{FAIL}недавние файлы уже удалены или перемещены"


def _protected() -> tuple[set[int], set[str]]:
    """PID, которые закрывать нельзя: сама Харви и консоль, из которой её запустили."""
    pids, exes = {OWN_PID}, set()
    try:
        me = psutil.Process(OWN_PID)
        exes.add(me.name().lower())
        for parent in me.parents():
            pids.add(parent.pid)
            exes.add(parent.name().lower())
    except Exception:
        pass
    return pids, exes


def foreground_is_mine() -> bool:
    """Активное окно - это сама Харви или её консоль? (Ctrl+C там её остановит)"""
    if psutil is None:
        return False
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
    skip_pids, ancestor_exes = _protected()
    if pid.value in skip_pids:
        return True
    try:
        exe = psutil.Process(pid.value).name().lower()
    except Exception:
        return False
    return exe != "explorer.exe" and exe in ancestor_exes


def _target_exes(query: str) -> set[str]:
    q = query.strip().lower()
    if q in ("браузер", "browser"):
        exe = default_browser_exe()
        return {Path(exe).name.lower()} if exe else set()
    exes: set[str] = set()
    for names, procs in NAME_GROUPS:
        if q in names:
            exes |= {p.lower() for p in procs}
    if not exes:     # ослышки в пару букв: "диспетер", "телеграмма"
        aliases = {alias: procs for names, procs in NAME_GROUPS for alias in names}
        close = difflib.get_close_matches(q, list(aliases), n=1, cutoff=0.8)
        if close:
            exes = {p.lower() for p in aliases[close[0]]}
    if exes:
        return exes

    # Общий случай: сравниваю название с именами запущенных процессов
    norm = lambda x: x.lower().replace(" ", "").removesuffix(".exe")
    stems = {norm(q)}
    app = find_app(q)
    if app:
        stems.add(norm(app["Name"]))
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        proc = norm(name)
        if len(proc) >= 4 and any(len(st) >= 3 and (st in proc or proc in st) for st in stems):
            exes.add(name)
    return exes


def _taskkill(pids: list[int], force: bool = False, wait: bool = True) -> None:
    if not pids:
        return
    cmd = ["taskkill"]
    for pid in pids:
        cmd += ["/PID", str(pid)]
    if force:
        cmd.append("/F")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if wait:
        subprocess.run(cmd, capture_output=True, creationflags=flags)
    else:                                   # taskkill стартует ~0.1 с, не жду его
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


CLOSE_QUICK_WAIT = 0.3     # сколько жду нормального закрытия до ответа
CLOSE_GRACE = 1.5          # сколько даю закрыться самому, потом завершаю принудительно


def _report_late_failure(text: str) -> None:
    """Ошибка, которая выяснилась уже после ответа."""
    log("Ошибка", text)
    if QUIET_MODE:
        play_sound("error")
        if SPEAK_ERRORS:
            speak(_cap(text) + ".")
    else:
        speak(f"Простите, господин, {text}.")


def _finish_close(alive: list, targets: set[str], label: str) -> None:
    """Доделывает закрытие в фоне: Telegram и Discord на WM_CLOSE просто уходят в трей."""
    _, alive = psutil.wait_procs(alive, timeout=CLOSE_GRACE - CLOSE_QUICK_WAIT)
    if not alive:
        return
    if targets & DOCUMENT_EXES:
        _report_late_failure(f"«{label}» не закрылось, возможно, просит сохранить файл")
        return
    _taskkill([p.pid for p in alive], force=True)
    _, alive = psutil.wait_procs(alive, timeout=1.0)
    if alive:
        _report_late_failure(f"«{label}» не удалось закрыть")


def _close_exes(targets: set[str], label: str) -> str:
    """Сначала прошу окно закрыться, если не вышло - завершаю процесс (кроме редакторов, там можно
    потерять несохранённое). Отвечаю сразу, а упрямые приложения добиваю в фоне."""
    skip_pids, ancestor_exes = _protected()
    if targets & (PROTECTED_EXES | ancestor_exes):
        return f"{FAIL}«{label}» закрывать нельзя"
    procs = _find_procs(targets, skip_pids)
    if not procs:
        return f"{FAIL}«{label}» не запущено"

    _taskkill([p.pid for p in procs], wait=False)
    _, alive = psutil.wait_procs(procs, timeout=CLOSE_QUICK_WAIT)
    if alive:
        threading.Thread(target=_finish_close, args=(alive, targets, label), daemon=True).start()
    return f"закрыл{END} {label}"


def _app_exes(name: str) -> set[str]:
    """Процессы приложения. "ютуб" и "видео" - это браузер, "музыка" - плеер."""
    low = (name or "").strip().lower()
    for target, rx in MEDIA_TARGET_RES:
        if rx.fullmatch(low):
            if target == "music":
                running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
                return {n for n in running if any(a in n for a in MUSIC_APPS)}
            return _target_exes("браузер")
    return _target_exes(low)


def minimize_app(name: str) -> str:
    """Сворачивает окна приложения (не закрывает)."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    pids = {p.pid for p in _find_procs(_app_exes(name), set())}
    if not pids:
        return f"{FAIL}«{name}» не запущено"
    windows = [hwnd for hwnd, visible in _windows_of(pids) if visible and not _user32.IsIconic(hwnd)]
    if not windows:
        return f"{INFO}«{name}» уже свёрнуто"
    for hwnd in windows:
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
    return f"свернул{END} {name}"


def close_app(name: str) -> str:
    """"закрой телеграм" - приложение, "закрой загрузки" - папка, "закрой ютуб" - вкладка."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    folder = _close_folder_window(name)
    if folder:
        return folder
    targets = _target_exes(name)
    if not _find_procs(targets, set()):
        tab = _close_site_tab(name)
        if tab:
            return tab
    return _close_exes(targets, name)


_VK_W = 0x57


def _top_windows() -> list[tuple[int, str, str, int]]:
    """(hwnd, заголовок, класс, pid) видимых окон верхнего уровня."""
    found: list[tuple[int, str, str, int]] = []
    title, cls = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(128)

    def callback(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextW(hwnd, title, 512):
            _user32.GetClassNameW(hwnd, cls, 128)
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, title.value, cls.value, pid.value))
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def _folder_names(name: str) -> set[str] | None:
    low = name.strip().lower()
    key = FOLDER_ALIASES.get(low) or (low if low in FOLDERS else None)
    if key is None:
        return None
    return ({low, key, Path(FOLDERS[key]).name.lower()}
            | {alias for alias, k in FOLDER_ALIASES.items() if k == key})


def _close_folder_window(name: str) -> str | None:
    """Закрывает окна проводника с этой папкой. None - это не папка."""
    names = _folder_names(name)
    if names is None:
        return None
    windows = [hwnd for hwnd, title, cls, _ in _top_windows()
               if cls == "CabinetWClass" and title.strip().lower() in names]
    if not windows:
        return f"{INFO}папка «{name}» не открыта"
    for hwnd in windows:
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return f"закрыл{END} папку {name}"


def _site_keywords(name: str) -> set[str]:
    low = name.strip().lower()
    for site, pattern in SITE_ALIASES.items():
        if re.fullmatch(pattern, low):
            host = urllib.parse.urlparse(SITES[site]).hostname or ""
            return {low, site, host.removeprefix("www.").split(".")[0]}
    return set()


def _close_site_tab(name: str) -> str | None:
    """Заголовок окна показывает только активную вкладку, поэтому вывожу окно вперёд и жму Ctrl+W.
    None - вкладку не нашёл."""
    keywords = _site_keywords(name)
    if not keywords or psutil is None:
        return None
    browser_pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in BROWSER_EXES}
    for hwnd, title, _, pid in _top_windows():
        if pid in browser_pids and any(k in title.lower() for k in keywords):
            if _user32.IsIconic(hwnd):
                _user32.ShowWindow(hwnd, SW_RESTORE)
            _force_foreground(hwnd)
            time.sleep(0.15)
            if _user32.GetForegroundWindow() != hwnd:
                return f"{FAIL}не получилось переключиться на вкладку {name}"
            _chord(VK_CONTROL, _VK_W)
            return f"закрыл{END} вкладку {name}"
    return None


def close_active(window_only: bool = False) -> str:
    """Закрывает то, что на переднем плане."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return f"{FAIL}нет активного окна"
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = psutil.Process(pid.value).name().lower()
    skip_pids, ancestor_exes = _protected()
    if exe in PROTECTED_EXES:
        return f"{FAIL}активное окно — системное, его закрывать нельзя"
    if pid.value in skip_pids or exe in ancestor_exes:
        return f"{FAIL}это окно, из которого запущена я, закрывать нельзя"
    title = foreground_title()
    if window_only or exe == "applicationframehost.exe":    # у приложений из Store общий PID
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return f"закрыл{END} окно «{title}»"
    return _close_exes({exe}, title)


def _chord(*vks: int) -> None:
    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def window_state(action: str) -> str:
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return f"{FAIL}нет активного окна"
    if action == "minimize":
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
        return f"свернул{END} окно"
    _user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return f"развернул{END} окно на весь экран"


def show_desktop() -> str:
    _chord(VK_LWIN, VK_D)
    return f"свернул{END} все окна"


def alt_tab() -> str:
    _chord(VK_MENU, VK_TAB)
    return f"переключил{END} окно"


def screenshot() -> str:
    _chord(VK_LWIN, VK_SNAPSHOT)          # сохраняется в Изображения\Снимки экрана
    return f"сделал{END} снимок экрана"


def lock_pc() -> str:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    return f"заблокировал{END} компьютер"
