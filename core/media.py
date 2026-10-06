"""Плееры через Windows SMTC: пауза у нужного плеера, "что играет", громкость приложений."""

from __future__ import annotations

import asyncio
import ctypes
import re
import threading
import time
from ctypes import wintypes

from config import (
    BROWSER_EXES,
    FEMALE_VOICE,
    MEDIA_FALLBACK_APP,
    MEDIA_FALLBACK_SITE,
    MUSIC_APPS,
    MUSIC_APP_PLAY_BUTTONS,
    MUSIC_SITES,
    VIDEO_APPS,
)
from core.util import (  # noqa: F401
    END,
    FAIL,
    HAS_PYCAW,
    INFO,
    _clamp,
    log,
    psutil,
)
from core.winapi import (  # noqa: F401
    VK_MEDIA_NEXT,
    VK_MEDIA_PLAY_PAUSE,
    VK_MEDIA_PREV,
    WNDENUMPROC,
    _find_procs,
    _user32,
    _windows_of,
    press_key,
)
from core.uia import press_button
from core.audio import (  # noqa: F401
    _audio_sessions,
    _ducker,
    _foreign,
    _session_name,
    _session_peak,
    audio_is_playing,
)
from core.apps import (  # noqa: F401
    _target_exes,
    open_app,
    open_browser,
)


# Через SMTC видно каждый плеер отдельно: приложение, трек, играет или на паузе.
# Поэтому "музыка стоп" ставит на паузу именно музыку, и результат можно проверить.
# Без пакета winrt остаются только медиа-клавиши.
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SMTCManager,
    )

    HAS_SMTC = True
except Exception:
    HAS_SMTC = False

_PLAYING, _PAUSED = 4, 5
_smtc_loop: asyncio.AbstractEventLoop | None = None
_smtc_lock = threading.Lock()


def _smtc_run(coro, timeout: float = 20.0):
    """Все вызовы SMTC идут через один поток MTA со своим циклом asyncio.
    Сначала я делал asyncio.run() из главного потока, но там comtypes включил STA, и WinRT
    на каждом шаге падал с RPC_E_WRONG_THREAD (0x8001010e)."""
    global _smtc_loop
    with _smtc_lock:
        if _smtc_loop is None:
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                try:
                    from winrt.runtime import ApartmentType, init_apartment

                    init_apartment(ApartmentType.MULTI_THREADED)
                except Exception as e:
                    log("Медиа", f"не удалось включить MTA для SMTC: {e}")
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            threading.Thread(target=run, name="smtc", daemon=True).start()
            ready.wait()
            _smtc_loop = loop
    return asyncio.run_coroutine_threadsafe(coro, _smtc_loop).result(timeout)
_MUSIC_SITES_RE = re.compile(MUSIC_SITES, re.IGNORECASE)
_TARGET_NAMES = {"music": "музыка", "youtube": "ютуб", "video": "видео"}
_paused_by_me: list[tuple[str, str]] = []     # (приложение, название) - что Харви поставила на паузу последним


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _window_titles(browsers_only: bool = False) -> list[str]:
    """Заголовки видимых окон - по ним видно сайт ("... - YouTube - Mozilla Firefox").
    browsers_only: только браузеры (у Яндекс Музыки своё окно, это не вкладка)."""
    titles: list[str] = []
    buf = ctypes.create_unicode_buffer(512)
    browser_pids = ({p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in BROWSER_EXES}
                    if browsers_only and psutil else None)

    def callback(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextW(hwnd, buf, 512):
            if browser_pids is not None:
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in browser_pids:
                    return True
            titles.append(buf.value)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return titles


async def _smtc_snapshot() -> tuple[list[dict], str | None]:
    manager = await _SMTCManager.request_async()
    sessions: list[dict] = []
    for session in manager.get_sessions():
        try:
            props = await session.try_get_media_properties_async()
            title, artist, album = props.title or "", props.artist or "", props.album_title or ""
        except Exception:
            title = artist = album = ""
        sessions.append({
            "session": session, "app": session.source_app_user_model_id or "",
            "title": title, "artist": artist, "album": album,
            "status": int(session.get_playback_info().playback_status),
        })
    current = manager.get_current_session()
    return sessions, (current.source_app_user_model_id if current else None)


_kind_memory: dict[tuple[str, str], set[str]] = {}   # что уже удалось определить точно (по заголовку окна)


def _site_kinds(title: str) -> set[str]:
    kinds: set[str] = set()
    if "youtube music" in title:
        kinds |= {"youtube", "music"}
    elif "youtube" in title:
        kinds |= {"youtube", "video"}
    if _MUSIC_SITES_RE.search(title):
        kinds.add("music")
    return kinds


def _session_kinds(info: dict, titles: list[str]) -> set[str]:
    """Что это за плеер: music / youtube / video.
    Firefox отдаёт Windows один плеер на весь браузер, а заголовок показывает только активную
    вкладку. Поэтому, если трека нет ни в одном заголовке, смотрю, какие сайты вообще открыты."""
    app, title = info["app"].lower(), _norm(info["title"])
    kinds: set[str] = set()
    if any(a in app for a in MUSIC_APPS) or info["album"]:
        kinds.add("music")
    if any(a in app for a in VIDEO_APPS):
        kinds.add("video")
    if kinds:
        return kinds
    key = (info["app"], title)
    page = next((_norm(t) for t in titles if title and title in _norm(t)), "")   # окно с этим роликом/треком
    if page:
        _kind_memory[key] = _site_kinds(page) or {"video"}
        return _kind_memory[key]
    if key in _kind_memory:
        return _kind_memory[key]
    # Звук из браузера без музыкального сайта - почти всегда видео
    return set().union(*(_site_kinds(_norm(t)) for t in _window_titles(browsers_only=True))) or {"video", "youtube"}


def _label(info: dict) -> str:
    return f"«{info['title']}»" if info["title"] else "воспроизведение"


def _is_browser_session(info: dict) -> bool:
    app = info["app"].lower()
    return not any(a in app for a in (*MUSIC_APPS, *VIDEO_APPS))


def _browser_silent() -> bool:
    peaks = [_session_peak(s) for s in _audio_sessions() if _session_name(s).lower() in BROWSER_EXES]
    return bool(peaks) and max(peaks) < 0.001


async def _wait_status(infos: list[dict], wanted: int, timeout: float = 4.0) -> list[dict]:
    """Ждёт, пока плееры сменят состояние, и возвращает тех, кто не сменил.
    Firefox сообщает о паузе с задержкой (на YouTube бывает больше 2.5 с), поэтому статус
    каждый раз читаю заново, а для браузера паузой считаю и тишину ~0.3 с."""
    started = time.time()
    manager = await _SMTCManager.request_async()
    pending = list(infos)
    quiet = 0
    while pending and time.time() - started < timeout:
        await asyncio.sleep(0.1)
        fresh = {sess.source_app_user_model_id: sess for sess in manager.get_sessions()}
        quiet = quiet + 1 if wanted == _PAUSED and _browser_silent() else 0
        still = []
        for info in pending:
            session = fresh.get(info["app"], info["session"])
            if int(session.get_playback_info().playback_status) == wanted:
                continue
            if quiet >= 3 and _is_browser_session(info):
                continue
            still.append(info)
        pending = still
    log("Медиа", f"{'подтверждено' if not pending else 'не подтвердилось'} за {time.time() - started:.1f} с")
    return pending


async def _smtc_media(action: str, target: str | None) -> str | None:
    """Управляет нужным плеером. None - сеансов нет, пусть сработает запасной способ."""
    global _paused_by_me
    sessions, current_app = await _smtc_snapshot()
    if not sessions:
        return None
    titles = _window_titles()
    for info in sessions:
        info["kinds"] = _session_kinds(info, titles)
    log("Медиа", f"{action} {target or '—'}: " + "; ".join(
        f"«{i['title'][:40]}» {'/'.join(sorted(i['kinds']))} {'играет' if i['status'] == _PLAYING else i['status']}"
        for i in sessions))
    matching = [i for i in sessions if target is None or target in i["kinds"]]
    mine = lambda i: (i["app"], i["title"]) in _paused_by_me
    is_current = lambda i: i["app"] == current_app
    what = _TARGET_NAMES.get(target, "")

    if action == "pause":
        playing = [i for i in sessions if i["status"] == _PLAYING]
        chosen = [i for i in playing if i in matching]
        if not chosen and target:   # цель не понял - беру только плееры браузера, музыкальное приложение не трогаю
            chosen = [i for i in playing if _is_browser_session(i)] if target != "music" else playing
        elif not chosen:
            chosen = playing
        if not chosen:
            return f"{INFO}сейчас ничего не играет"
        for info in chosen:
            await info["session"].try_pause_async()
        failed = await _wait_status(chosen, _PAUSED)
        if (len(failed) == 1 and failed[0]["app"] == current_app and _is_browser_session(failed[0])
                and not _browser_silent()):
            # Firefox иногда игнорирует паузу от Windows, но слушается медиа-клавишу. Клавиша уходит
            # текущему плееру, а это он и есть, так что она точно поставит паузу
            log("Медиа", "браузер не принял паузу - нажимаю медиа-клавишу")
            press_key(VK_MEDIA_PLAY_PAUSE)
            failed = await _wait_status(failed, _PAUSED, timeout=2.0)
        done = [i for i in chosen if i not in failed]
        _paused_by_me = [(i["app"], i["title"]) for i in done] or _paused_by_me
        if failed:
            return f"{FAIL}не получилось поставить на паузу {_label(failed[0])}"
        return f"поставил{END} на паузу " + ", ".join(_label(i) for i in done)

    if action == "play":
        if any(i["status"] == _PLAYING for i in matching):
            return f"{INFO}{what} уже играет" if what else f"{INFO}воспроизведение уже идёт"
        paused = [i for i in sessions if i["status"] == _PAUSED]
        candidates = ([i for i in paused if i in matching and mine(i)]
                      or [i for i in paused if i in matching and is_current(i)]
                      or [i for i in paused if i in matching]
                      or [i for i in paused if mine(i)])          # «включи музыку» после «музыка стоп» на ролике
        if not candidates:
            return None if target is None else ""
        info = candidates[0]
        await info["session"].try_play_async()
        if await _wait_status([info], _PLAYING):
            return f"{FAIL}не получилось включить {_label(info)}"
        return f"включил{END} {_label(info)}"

    # next / previous - тому, что играет
    playing = [i for i in matching if i["status"] == _PLAYING]
    info = (playing or [i for i in matching if is_current(i)] or matching or [None])[0]
    if info is None:
        return f"{INFO}не нашл{'а' if FEMALE_VOICE else 'ёл'}, что переключить"
    old_title = info["title"]
    ok = await (info["session"].try_skip_next_async() if action == "next"
                else info["session"].try_skip_previous_async())
    if not ok:
        return f"{FAIL}плеер не дал переключить {_label(info)}"
    return f"переключил{END} {'на следующий' if action == 'next' else 'на предыдущий'} трек" + (
        f", было {_label(info)}" if old_title else "")


async def _now_playing() -> str:
    sessions, _ = await _smtc_snapshot()
    playing = [i for i in sessions if i["status"] == _PLAYING]
    shown = playing or [i for i in sessions if i["status"] == _PAUSED]
    if not shown:
        return f"{INFO}сейчас ничего не играет"
    titles = _window_titles()
    parts = []
    for info in shown[:2]:
        title, artist = " ".join(info["title"].split()), " ".join(info["artist"].split())
        kinds = _session_kinds(info, titles)
        who = "канал" if "youtube" in kinds and "music" not in kinds else "исполнитель"
        parts.append(f"«{title}»" + (f", {who} {artist}" if artist else ""))
    return f"{INFO}{'сейчас играет' if playing else 'на паузе'} " + "; ".join(parts)


def now_playing() -> str:
    if not HAS_SMTC:
        return f"{FAIL}не вижу плееры, не установлен пакет winrt"
    return _smtc_run(_now_playing())


def _media_keys(action: str) -> str:
    """Запасной способ: медиа-клавиши (не знаю, какой плеер их получит)."""
    if action == "next":
        press_key(VK_MEDIA_NEXT)
        return f"переключил{END} на следующий трек"
    if action == "previous":
        press_key(VK_MEDIA_PREV)
        return f"вернул{END} предыдущий трек"
    if action == "pause":
        if HAS_PYCAW and not audio_is_playing():
            return f"{INFO}сейчас ничего не играет"
        press_key(VK_MEDIA_PLAY_PAUSE)
        return f"поставил{END} воспроизведение на паузу"
    if action == "play":
        if HAS_PYCAW and audio_is_playing():
            return f"{INFO}воспроизведение уже идёт"
        press_key(VK_MEDIA_PLAY_PAUSE)
        return f"возобновил{END} воспроизведение"
    return f"{FAIL}неизвестное действие {action}"


def _autoplay_when_ready(target: str, timeout: float = 25.0) -> None:
    """Включает музыку после открытия плеера. Яндекс Музыку Windows не видит, пока в ней
    ни разу не нажали play, поэтому жму кнопку в окне через UI Automation."""
    exes = _target_exes(MEDIA_FALLBACK_APP.get(target, "")) if psutil else set()

    def sounding() -> bool:
        """Играет ли плеер на самом деле (статусу сразу после запуска верить нельзя)."""
        return any(_session_peak(s) > 0.001 for s in _audio_sessions() if _session_name(s).lower() in exes)

    async def via_smtc() -> bool:
        sessions, _ = await _smtc_snapshot()
        titles = _window_titles()
        for info in sessions:
            if target in _session_kinds(info, titles) and any(a in info["app"].lower() for a in MUSIC_APPS):
                await info["session"].try_play_async()
                return True
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if sounding():
            return
        how = None
        try:
            if HAS_SMTC and _smtc_run(via_smtc()):
                how = "«играть» в Windows"
        except Exception as e:
            log("Медиа", f"SMTC при автозапуске: {e}")
        if how is None and exes and _windows_of({p.pid for p in _find_procs(exes, set())}):
            pressed = press_button(exes, MUSIC_APP_PLAY_BUTTONS, timeout=min(8.0, max(1.0, deadline - time.time())))
            how = f"кнопку «{pressed}» в окне" if pressed else None
        if how is None:
            continue
        # Пока приложение грузится, нажатие может не сработать - проверяю, появился ли звук
        for _ in range(8):
            time.sleep(0.4)
            if sounding():
                log("Медиа", f"автозапуск после открытия: {how}, через {timeout - (deadline - time.time()):.1f} с")
                return
    log("Медиа", "плеер открылся, но включить в нём музыку не удалось")


def play_app(name: str) -> str:
    opened = open_app(name)
    if not opened.startswith(FAIL):
        threading.Thread(target=_autoplay_when_ready, args=("music",), daemon=True).start()
    return opened


def media(action: str, target: str | None = None) -> str:
    """Пауза / play / next / prev у нужного плеера. target: music, youtube, video или None."""
    action = (action or "").strip().lower()
    target = (target or "").strip().lower() or None
    if target not in (None, *_TARGET_NAMES):
        target = None
    result = None
    if HAS_SMTC:
        try:
            result = _smtc_run(_smtc_media(action, target))
        except Exception as e:
            log("Медиа", f"SMTC не сработал, использую медиа-клавиши: {e}")
    if result == "":                 # играть нечего - открываю приложение или сайт из config.py
        app = MEDIA_FALLBACK_APP.get(target)
        if app:
            opened = open_app(app)
            if not opened.startswith(FAIL):
                threading.Thread(target=_autoplay_when_ready, args=(target,), daemon=True).start()
            return opened
        if any(target in _site_kinds(_norm(t)) for t in _window_titles()):
            return f"{INFO}вкладка уже открыта, но включать там нечего, запустите трек один раз вручную"
        site = MEDIA_FALLBACK_SITE.get(target)
        if site:
            return open_browser(site=site)
        return f"{INFO}нечего включать, {_TARGET_NAMES[target]} не открыто"
    return result if result is not None else _media_keys(action)


_VOLUME_LABELS = {"music": "музыки", "youtube": "ютуба", "video": "видео"}


def _target_audio_sessions(target: str) -> list:
    sessions = [s for s in _audio_sessions() if _foreign(s)]
    name = lambda s: _session_name(s).lower()
    if target.startswith("app:"):
        exes = _target_exes(target[4:]) if psutil else set()
        return [s for s in sessions if name(s) in exes]
    if target == "music":
        apps = [s for s in sessions if any(a in name(s) for a in MUSIC_APPS)]
        if apps:
            return apps
        if any("music" in _site_kinds(_norm(t)) for t in _window_titles()):   # музыка во вкладке браузера
            return [s for s in sessions if name(s) in BROWSER_EXES]
        return []
    found = [s for s in sessions if name(s) in BROWSER_EXES]     # YouTube и видео - в браузере
    if target == "video":
        found += [s for s in sessions if any(a in name(s) for a in VIDEO_APPS)]
    return found


def app_volume(target: str, level: int | None = None, delta: int | None = None) -> str:
    """Громкость одного приложения ("музыку тише", "ютуб на 30").
    У браузера громкость одна на все вкладки, так устроен Windows."""
    target = (target or "").strip().lower()
    if target not in _VOLUME_LABELS and not target.startswith("app:"):
        target = "app:" + target
    label = _VOLUME_LABELS.get(target, target[4:])
    sessions = _target_audio_sessions(target)
    if not sessions:
        return f"{FAIL}не нашл{'а' if FEMALE_VOICE else 'ёл'} {label} среди источников звука"
    first = sessions[0]
    current = round(_ducker.original(_session_name(first).lower(), first) * 100)
    new = _clamp(level if level is not None else current + int(delta or 0))
    for session in sessions:
        _ducker.set_app(_session_name(session).lower(), session, new / 100)
    return f"громкость {label} {new} процентов"
