"""Значок в трее, автозапуск, перезапуск, единственный экземпляр."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import (
    ASSISTANT_NAME,
    BASE_DIR,
    LOG_ENABLED,
    LOG_FILE,
    TRAY_ENABLED,
    WAKE_COLLECT,
    WAKE_SAMPLES_DIR,
)
from core.util import (  # noqa: F401
    log,
)
from core.winapi import (  # noqa: F401
    _kernel32,
)
from core.speech import (  # noqa: F401
    _speaking,
    play_sound,
)
from core.daily import (  # noqa: F401
    open_notes,
    set_sleeping,
)
import core.daily as daily
import core.game as game
import core.system as system


_quit_event = threading.Event()        # «Выход» из меню трея
_tray_icon = None
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "HarveyAssistant"


def _autostart_command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")       # без окна консоли
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{BASE_DIR / "harvey.py"}"'


def autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass
    log("Система", f"автозапуск с Windows {'включён' if enabled else 'выключен'}")


def _tray_image(sleeping: bool, speaking: bool):
    from PIL import Image, ImageDraw

    color = (130, 130, 140) if sleeping else (70, 140, 255) if speaking else (60, 185, 110)
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=color)
    draw.text((23, 17), "H", fill=(255, 255, 255))
    return image.resize((64, 64))


def open_log_report() -> None:
    """Отчёт по логу (log_report.py), открывается в Блокноте."""
    from log_report import build_report

    path = BASE_DIR / "отчёт по логу.txt"
    path.write_text(build_report(), encoding="utf-8")
    os.startfile(str(path))


def _sample_count() -> int:
    return sum(1 for _ in WAKE_SAMPLES_DIR.glob("*.wav")) if WAKE_SAMPLES_DIR.exists() else 0


def _open_samples() -> None:
    WAKE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    os.startfile(str(WAKE_SAMPLES_DIR))


def start_tray() -> None:
    if not TRAY_ENABLED:
        return
    try:
        import pystray
    except Exception as e:
        log("Трей", f"pystray не установлен ({e}) — значка не будет")
        return

    def status(_item) -> str:
        return f"{ASSISTANT_NAME}: {'спит' if daily._sleeping else 'слушает'}"

    def go_sleep(_icon, _item) -> None:
        set_sleeping(True)
        log("Трей", "сон")
        play_sound("cancel")

    def wake(_icon, _item) -> None:
        set_sleeping(False)                   # накопленный во сне звук сбросит главный цикл
        log("Трей", "проснуться")
        play_sound("ready")

    def toggle_autostart(_icon, item) -> None:
        try:
            set_autostart(not item.checked)
        except Exception as e:
            log("Трей", f"не удалось изменить автозапуск: {e}")

    def mic_on(_icon, _item) -> None:             # «выключи микрофон» голосом не отменить - Харви не слышит
        system.mic_on_requested.set()             # главный цикл включит сам: COM из потока трея роняет процесс

    def quit_app(icon, _item) -> None:
        _quit_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status, None, enabled=False),
        pystray.MenuItem("Спать", go_sleep, visible=lambda _i: not daily._sleeping),
        pystray.MenuItem("Проснуться", wake, visible=lambda _i: daily._sleeping),
        pystray.MenuItem("Включить микрофон", mic_on, visible=lambda _i: system.mic_is_muted),
        pystray.MenuItem("Игровой режим", lambda _i, _t: game.set_game_mode(not game.active()),
                         checked=lambda _i: game.active()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Заметки", lambda _i, _t: open_notes()),
        pystray.MenuItem("Лог", lambda _i, _t: os.startfile(str(LOG_FILE)), visible=lambda _i: LOG_ENABLED),
        pystray.MenuItem("Отчёт: что Харви не поняла", lambda _i, _t: open_log_report(), visible=lambda _i: LOG_ENABLED),
        pystray.MenuItem(lambda _i: f"Образцы «Харви» ({_sample_count()})", lambda _i, _t: _open_samples(),
                         visible=lambda _i: WAKE_COLLECT),
        pystray.MenuItem("Запускать вместе с Windows", toggle_autostart, checked=lambda _i: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Перезапустить (применить изменения)", lambda _i, _t: restart_self()),
        pystray.MenuItem("Выход", quit_app),
    )
    global _tray_icon
    icon = _tray_icon = pystray.Icon("harvey", _tray_image(False, False), ASSISTANT_NAME, menu)

    def refresh() -> None:            # цвет значка: зелёный - слушает, синий - говорит, серый - спит
        last = None
        while not _quit_event.is_set():
            state = (daily._sleeping, _speaking.is_set(), game.active())    # галочка «Игровой режим» - тоже
            if state != last:
                last = state
                try:
                    icon.icon = _tray_image(*state[:2])
                    icon.title = f"{ASSISTANT_NAME}: {'спит' if state[0] else 'говорит' if state[1] else 'слушает'}"
                    icon.update_menu()
                except Exception:
                    pass
            time.sleep(0.3)

    threading.Thread(target=icon.run, daemon=True).start()
    threading.Thread(target=refresh, daemon=True).start()


def restart_self() -> str:
    """Перезапуск, чтобы подхватить изменения в коде. Сначала отпускаю mutex,
    иначе новый процесс решит, что он второй, и закроется."""
    def later() -> None:
        time.sleep(0.6)                     # даём прозвучать сигналу «готово»
        log("Система", "Перезапуск...")
        if _instance_mutex:
            _kernel32.CloseHandle(_instance_mutex)
        console = not Path(sys.executable).name.lower().startswith("pythonw")
        subprocess.Popen([sys.executable, str(BASE_DIR / "harvey.py")], cwd=str(BASE_DIR),
                         creationflags=subprocess.CREATE_NEW_CONSOLE if console else 0)
        _quit_event.set()
        if _tray_icon is not None:
            try:
                _tray_icon.stop()
            except Exception:
                pass

    threading.Thread(target=later, daemon=True).start()
    return "перезапускаюсь"


def _single_instance() -> bool:
    """Второй экземпляр (автозапуск + ручной запуск) сразу выходит."""
    global _instance_mutex
    _instance_mutex = _kernel32.CreateMutexW(None, False, "Local\\HarveyAssistantSingleInstance")
    return ctypes.get_last_error() != 183       # ERROR_ALREADY_EXISTS


_instance_mutex = None
