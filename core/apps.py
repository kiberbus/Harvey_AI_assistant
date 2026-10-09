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
    SITE_ALIASES,
)
from core.util import (
    END,
    FAIL,
    INFO,
    MEDIA_TARGET_RES,
    OWN_PID,
    _cap,
    _clamp,
    _plural,
    log,
    psutil,
)
from core.winapi import (
    GW_OWNER,
    GWL_EXSTYLE,
    KEYEVENTF_KEYUP,
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    VK_D,
    VK_LWIN,
    VK_MENU,
    VK_SNAPSHOT,
    VK_TAB,
    WM_CLOSE,
    WS_EX_TOOLWINDOW,
    _find_procs,
    _force_foreground,
    _top_windows,
    _user32,
    _windows_of,
    bring_to_front,
    com_call,
    foreground_title,
    half,
    is_cloaked,
    move_to_next_monitor,
    paste_text,
    place_window,
    type_text,
    window_area,
)
from core.speech import (
    play_sound,
    speak,
)
from core.daily import (
    ask_confirm,
)
from core import browser, files, steam


def set_brightness(level: int) -> str:
    import screen_brightness_control as sbc

    level = _clamp(level)
    sbc.set_brightness(level)
    return f"установил{END} яркость на {level} процентов"


def change_brightness(delta: int) -> str:
    import screen_brightness_control as sbc

    delta = int(float(delta))                   # от ИИ может прийти и строка «10»
    current = sbc.get_brightness()
    current = current[0] if isinstance(current, list) else current
    new = _clamp(current + delta)
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


def exact_app(name: str) -> dict | None:
    """Приложение ровно с таким названием, как в Пуске: «obsidian» без глагола. Без нечёткого поиска
    find_app: иначе любое слово в разговоре открывало бы что-нибудь похожее."""
    low = name.strip().lower()
    return next((a for a in _load_start_apps() if a["Name"].lower() == low), None)


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
        # ИИ просит сайт и игру как приложение (из лога: «Google Translate», «Colab» - «не найдено»)
        site = _site_key(name)
        if site:
            return open_browser(site)
        if steam.find_game(name):
            return steam.launch(name)
        return f"{FAIL}приложение «{name}» не найдено"

    # Если уже запущено - не запускаю второй экземпляр, а показываю окно
    try:
        exes = _target_exes(name)
        _wait_closed(exes)
        state = bring_to_front(exes)
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
    """Куда ведут ярлыки (WScript.Shell в своём COM: команду могут выполнить и не из главного потока)."""
    def read() -> list[str]:
        import comtypes.client

        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        targets = []
        for lnk in links:
            try:
                targets.append(shell.CreateShortcut(str(lnk)).TargetPath or "")
            except Exception:
                targets.append("")
        return targets

    return com_call(read)


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
_closing: dict[str, float] = {}     # процесс → до какого времени он ещё может закрываться в фоне


def _wait_closed(exes: set[str]) -> None:
    """«Закрой телеграм и открой его»: закрытие доделывается в фоне, и окно уходящего процесса ещё есть -
    open_app показывал его («показала Telegram»), а потом Telegram закрывался (из лога 9 октября).
    Жду, пока закрываемый процесс завершится, и запускаю заново."""
    deadline = max((_closing.get(exe, 0.0) for exe in exes), default=0.0)
    while time.time() < deadline and _find_procs(exes, set()):
        time.sleep(0.1)


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
    _closing.update(dict.fromkeys(targets, time.time() + CLOSE_GRACE + 1.5))
    try:
        _, alive = psutil.wait_procs(procs, timeout=CLOSE_QUICK_WAIT)
    except psutil.AccessDenied:          # из лога: диспетчер задач запущен от администратора
        return f"{FAIL}«{label}» запущен от имени администратора, закрыть его я не могу"
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
    """"закрой телеграм" - приложение, "закрой загрузки" - папка, "закрой проводник" - все папки,
    "закрой ютуб" - вкладка."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    folder = files.close_named(name)
    if folder:
        return folder
    targets = _target_exes(name)
    if not _find_procs(targets, set()) and _site_keywords(name):
        # «закрой ютуб» - вкладка, причём любая, а не только активная (её ищу в полосе вкладок)
        tab = browser.close_tab(name=name)
        if not tab.startswith((INFO, FAIL)):
            return tab
    return _close_exes(targets, name)


def _site_key(name: str) -> str | None:
    """Ключ из SITES, если name - название сайта целиком: «google translate» → translate."""
    low = name.strip().lower()
    return next((site for site, pattern in SITE_ALIASES.items() if re.fullmatch(pattern, low)), None)


def _site_keywords(name: str) -> set[str]:
    site = _site_key(name)
    if site is None:
        return set()
    host = urllib.parse.urlparse(SITES[site]).hostname or ""
    return {name.strip().lower(), site, host.removeprefix("www.").split(".")[0]}


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


_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _app_windows() -> list[tuple[int, str]]:
    """Окна программ, как на панели задач: (hwnd, процесс). Без самой Харви, её консоли и рабочего стола."""
    skip_pids, ancestor_exes = _protected()
    found = []
    for hwnd, _title, cls, pid in _top_windows():
        if (cls in _SHELL_CLASSES or pid in skip_pids or _user32.GetWindow(hwnd, GW_OWNER)
                or _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or is_cloaked(hwnd)):
            continue
        try:
            exe = psutil.Process(pid).name().lower()
        except Exception:
            continue
        folder = exe == "explorer.exe" and cls == "CabinetWClass"
        if not folder and (exe in PROTECTED_EXES or exe in ancestor_exes):
            continue
        found.append((hwnd, exe))
    return found


def request_close_all() -> str:
    """«Закрой все приложения» - только после «да»."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    count = len(_app_windows())
    if not count:
        return f"{INFO}открытых окон нет"
    return ask_confirm("close_all_apps", {}, f"Закрыть все программы, господин? Открыто "
                                             f"{count} {_plural(count, 'окно', 'окна', 'окон')}.")


def close_all_apps() -> str:
    """Папки закрываю как окна, программы - как «закрой X» (вместе с их помощниками: Steam - и steamwebhelper)."""
    windows = _app_windows()
    exes = {exe for _, exe in windows if exe != "explorer.exe"}
    if any(exe == "explorer.exe" for _, exe in windows):
        files.close_folder(everything=True)
    failed = []
    for exe in sorted(exes):
        group = next((set(procs) for _, procs in NAME_GROUPS if exe in procs), {exe})
        if _close_exes(group, Path(exe).stem).startswith(FAIL):
            failed.append(Path(exe).stem)
    if failed:
        return f"{FAIL}не закрылись: {', '.join(failed)}"
    return f"закрыл{END} все программы"


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


def restore_windows() -> str:
    """«Разверни все окна». Второй Win+D не годится: если после «сверни все окна» что-то открывали,
    он свернёт всё снова, поэтому разворачиваю свёрнутые окна сама."""
    minimized = [hwnd for hwnd, _title, _cls, pid in _top_windows() if pid != OWN_PID and _user32.IsIconic(hwnd)]
    for hwnd in reversed(minimized):        # снизу вверх: верхнее окно останется сверху
        _user32.ShowWindow(hwnd, SW_RESTORE)
    return f"развернул{END} все окна" if minimized else f"{INFO}свёрнутых окон нет"


def alt_tab() -> str:
    _chord(VK_MENU, VK_TAB)
    return f"переключил{END} окно"


def _app_window(name: str) -> int | None:
    """Главное окно приложения: видимое раньше скрытого."""
    pids = {p.pid for p in _find_procs(_app_exes(name), set())}
    windows = _windows_of(pids) if pids else []
    return windows[0][0] if windows else None


def _wait_app_window(name: str, timeout: float = 6.0) -> int | None:
    """Окно приложения; если оно не запущено - запускаю и жду, пока появится окно."""
    hwnd = _app_window(name)
    if hwnd:
        return hwnd
    if open_app(name).startswith(FAIL):
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.3)
        hwnd = _app_window(name)
        if hwnd:
            time.sleep(0.5)          # окно только создано - даю ему дорисоваться, иначе оно само сдвинется
            return hwnd
    return None


def arrange_window(position: str, name: str = "") -> str:
    """«Телеграм влево», «хром на второй монитор». Без названия - активное окно."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    hwnd = _app_window(name) if name else _user32.GetForegroundWindow()
    if not hwnd:
        return f"{FAIL}«{name}» не запущено" if name else f"{FAIL}нет активного окна"
    what = name or "окно"
    if position == "monitor":
        if not move_to_next_monitor(hwnd):
            return f"{FAIL}монитор всего один"
        _force_foreground(hwnd)
        return f"{'перенесла' if END else 'перенёс'} {what} на другой монитор"
    if position not in ("left", "right"):
        return f"{FAIL}не знаю положение «{position}»"
    place_window(hwnd, half(window_area(hwnd), position))
    _force_foreground(hwnd)
    return f"поставил{END} {what} {'слева' if position == 'left' else 'справа'}"


def side_by_side(left: str, right: str) -> str:
    """«Рядом Chrome и Telegram»: первое приложение на левую половину экрана, второе на правую.
    Экран - тот, где сейчас активное окно. Не запущенное приложение сначала открываю."""
    if psutil is None:
        raise RuntimeError("psutil не установлен")
    area = window_area(_user32.GetForegroundWindow())
    windows = []
    for name in (left, right):
        hwnd = _wait_app_window(name)
        if not hwnd:
            return f"{FAIL}окно «{name}» не появилось"
        windows.append(hwnd)
    if windows[0] == windows[1]:
        return f"{FAIL}это одно и то же окно"
    for hwnd, side in zip(windows, ("left", "right")):
        place_window(hwnd, half(area, side))
        _force_foreground(hwnd)
    return f"поставил{END} рядом {left} и {right}"


def screenshot() -> str:
    _chord(VK_LWIN, VK_SNAPSHOT)          # сохраняется в Изображения\Снимки экрана
    return f"сделал{END} снимок экрана"


def lock_pc() -> str:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    return f"заблокировал{END} компьютер"
