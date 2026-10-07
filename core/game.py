"""Игровой режим: пока идёт игра, модель ИИ не занимает видеопамять, а Харви уступает процессор.

Игру узнаю по окну: активное окно закрывает весь монитор (полный экран или «окно без рамки»),
и это не браузер, не плеер и не рабочий стол. Свернул игру, чтобы ответить в Discord, - режим держится,
пока игра запущена, но не дольше GAME_LINGER_SEC без неё на экране.
Ручной выбор («Харви, игровой режим», пункт в трее) действует, пока игра не начнётся или не закончится."""

from __future__ import annotations

import threading
import time

from config import (
    BROWSER_EXES,
    GAME_CHECK_SEC,
    GAME_KEEP_ALIVE,
    GAME_LINGER_SEC,
    GAME_LOW_PRIORITY,
    GAME_MODE_AUTO,
    GAME_NOT_GAMES,
    KEEP_ALIVE,
    VIDEO_APPS,
)
from core.util import (  # noqa: F401
    END,
    log,
    psutil,
)
from core.winapi import (  # noqa: F401
    foreground_fullscreen,
)
from core import llm


_lock = threading.RLock()            # проверяют поток наблюдения, главный цикл (голос) и трей
_game: dict | None = None            # последняя замеченная игра: {"pid", "exe", "seen"}
_auto = False                        # что видит автоопределение
_forced: bool | None = None          # выбор голосом или в трее
_active = False


def active() -> bool:
    return _active


def is_game(exe: str) -> bool:
    """На весь экран бывают не только игры: видео в браузере, плеер, рабочий стол, экран блокировки."""
    return (bool(exe) and exe not in GAME_NOT_GAMES and exe not in BROWSER_EXES
            and not any(app in exe for app in VIDEO_APPS))


def _exe(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def _alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _detect(now: float) -> bool:
    global _game
    pid = foreground_fullscreen()
    exe = _exe(pid) if pid else ""
    if is_game(exe):
        _game = {"pid": pid, "exe": exe, "seen": now}
        return True
    if _game and now - _game["seen"] < GAME_LINGER_SEC and _alive(_game["pid"]):
        return True                  # свернул игру, но она ещё запущена
    _game = None
    return False


def update(now: float | None = None) -> None:
    """Включаю или выключаю режим, если что-то поменялось."""
    global _auto, _forced, _active
    with _lock:
        auto = GAME_MODE_AUTO and _detect(time.time() if now is None else now)
        if auto != _auto:
            _auto, _forced = auto, None      # игра началась или кончилась - ручной выбор больше не действует
        wanted = _auto if _forced is None else _forced
        if wanted != _active:
            _active = wanted
            _apply(wanted)


def _apply(on: bool) -> None:
    llm.set_keep_alive(GAME_KEEP_ALIVE if on else KEEP_ALIVE)
    if GAME_LOW_PRIORITY and psutil is not None:
        try:
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if on else psutil.NORMAL_PRIORITY_CLASS)
        except Exception as e:
            log("Игра", f"не удалось сменить приоритет: {e}")
    game = f" ({_game['exe']})" if on and _game else ""
    log("Игра", f"игровой режим {'включён' + game if on else 'выключен'}")
    if on:
        _unload_model()


def _unload_model() -> None:
    """В фоне: если Ollama завис, проверка игры и голосовая команда не должны его ждать."""
    def work() -> None:
        try:
            llm.unload()
            log("Игра", "модель ИИ выгружена из видеокарты")
        except Exception as e:           # Ollama не запущен - выгружать нечего
            log("Игра", f"модель ИИ не выгрузила: {e}")

    threading.Thread(target=work, daemon=True).start()


def set_game_mode(state: bool) -> str:
    """«Харви, игровой режим», «выключи игровой режим», пункт в трее."""
    global _forced
    with _lock:
        _forced = bool(state)
        update()
    return f"включил{END} игровой режим" if state else f"выключил{END} игровой режим"


def _watch() -> None:
    while True:
        time.sleep(GAME_CHECK_SEC)
        try:
            update()
        except Exception as e:
            log("Игра", f"ошибка проверки: {e}")


def start() -> None:
    """Первая проверка сразу: если Харви запустили посреди игры, модель ИИ не прогреваю."""
    if not GAME_MODE_AUTO:
        return
    try:
        update()
    except Exception as e:
        log("Игра", f"ошибка проверки: {e}")
    threading.Thread(target=_watch, daemon=True).start()
