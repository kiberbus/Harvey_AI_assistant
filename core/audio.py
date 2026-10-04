"""Звук Windows: источники звука, приглушение музыки на время речи, общая громкость."""

from __future__ import annotations

import ctypes
import json
import time

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
from core.util import (  # noqa: F401
    AudioUtilities,
    CLSCTX_ALL,
    END,
    HAS_PYCAW,
    IAudioEndpointVolume,
    IAudioMeterInformation,
    OWN_PID,
    _clamp,
    log,
)


# ───────────────────────── WINDOWS: ГРОМКОСТЬ И ИСТОЧНИКИ ЗВУКА ─────────────────────────
def _endpoint_volume():
    if not HAS_PYCAW:
        raise RuntimeError("pycaw не установлен")
    device = AudioUtilities.GetSpeakers()
    endpoint = getattr(device, "EndpointVolume", None)  # новые версии pycaw
    if endpoint is not None:
        return endpoint
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def _audio_sessions() -> list:
    if not HAS_PYCAW:
        return []
    try:
        return list(AudioUtilities.GetAllSessions())
    except Exception:
        return []


def _session_peak(session) -> float:
    try:
        return float(session._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
    except Exception:
        return 0.0


def _session_name(session) -> str:
    try:
        return session.Process.name() if session.Process else "?"
    except Exception:
        return "?"


def _foreign(session) -> bool:
    """Источник, которым можно управлять: не мы сами, не системные звуки и не из NO_VOLUME_CONTROL."""
    if session.ProcessId in (0, OWN_PID):
        return False
    return _session_name(session).lower() not in NO_VOLUME_CONTROL


def active_audio_sessions() -> list:
    """Источники звука, которые сейчас активны (кроме самой помощницы)."""
    return [s for s in _audio_sessions() if _foreign(s) and (s.State == 1 or _session_peak(s) > 0.001)]


def audio_is_playing(window: float = 0.25) -> bool:
    """True, если какое-то приложение реально выдаёт звук (проверяем пиковый уровень)."""
    end = time.time() + window
    while True:
        if any(_foreign(s) and _session_peak(s) > 0.002 for s in _audio_sessions()):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


DUCK_STATE_FILE = BASE_DIR / ".duck_state.json"   # на случай, если программу закроют, пока музыка приглушена


class Ducker:
    """Приглушает фоновые источники звука на время речи Харви, затем возвращает громкость.
    Громкость запоминается по ПРИЛОЖЕНИЮ, а не по аудиосеансу: браузеры пересоздают сеансы
    (например, после паузы), и Windows выдаёт новому сеансу приглушённую громкость — раньше
    из-за этого музыка иногда так и оставалась тихой."""

    def __init__(self) -> None:
        self._saved: dict[str, float] = {}     # имя процесса → исходная громкость
        self._since = 0.0

    @property
    def active(self) -> bool:
        return bool(self._saved)

    @property
    def stale(self) -> bool:
        """Приглушено слишком долго — возвращаем громкость, что бы ни происходило."""
        return bool(self._saved) and time.time() - self._since > DUCK_MAX_SEC

    def duck(self) -> None:
        if self._saved or not HAS_PYCAW:
            return
        for s in active_audio_sessions():
            name = _session_name(s).lower()
            try:
                vol = s.SimpleAudioVolume
                original = self._saved.setdefault(name, vol.GetMasterVolume())
                vol.SetMasterVolume(max(0.0, original * DUCK_FACTOR), None)
                log("Звук", f"приглушён источник: {name}")
            except Exception:
                pass
        if self._saved:
            self._since = time.time()
            try:
                DUCK_STATE_FILE.write_text(json.dumps(self._saved), encoding="utf-8")
            except Exception:
                pass

    @staticmethod
    def _volumes_of(names) -> list[tuple[object, str]]:
        """Все ТЕКУЩИЕ сеансы этих приложений — включая пересозданные после приглушения."""
        result = []
        for s in _audio_sessions():
            name = _session_name(s).lower()
            if name in names:
                try:
                    result.append((s.SimpleAudioVolume, name))
                except Exception:
                    pass
        return result

    def restore(self) -> None:
        if not self._saved:
            return
        saved, self._saved = self._saved, {}
        volumes = self._volumes_of(saved)
        for step in range(1, FADE_STEPS + 1):
            k = step / FADE_STEPS
            for vol, name in volumes:
                try:
                    vol.SetMasterVolume(min(1.0, saved[name] * (DUCK_FACTOR + (1 - DUCK_FACTOR) * k)), None)
                except Exception:
                    pass
            time.sleep(0.04)
        DUCK_STATE_FILE.unlink(missing_ok=True)

    def set_app(self, name: str, session, value: float) -> None:
        """Новая громкость приложения. Если оно сейчас приглушено — меняем «исходную» громкость,
        иначе после речи Харви вернула бы старую."""
        value = max(0.0, min(1.0, value))
        if name in self._saved:
            self._saved[name] = value
            try:
                DUCK_STATE_FILE.write_text(json.dumps(self._saved), encoding="utf-8")
            except Exception:
                pass
            value *= DUCK_FACTOR
        session.SimpleAudioVolume.SetMasterVolume(value, None)

    def original(self, name: str, session) -> float:
        """Громкость приложения без учёта временного приглушения."""
        return self._saved.get(name, session.SimpleAudioVolume.GetMasterVolume())

    def recover(self) -> None:
        """При запуске: если в прошлый раз программа закрылась с приглушённой музыкой — возвращаем."""
        try:
            saved = json.loads(DUCK_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        for vol, name in self._volumes_of(saved):
            try:
                vol.SetMasterVolume(min(1.0, float(saved[name])), None)
                log("Звук", f"вернул{END} громкость после прошлого запуска: {name}")
            except Exception:
                pass
        DUCK_STATE_FILE.unlink(missing_ok=True)


_ducker = Ducker()


# ───────────────────────── ИНСТРУМЕНТЫ: ЗВУК / МЕДИА / ЯРКОСТЬ ─────────────────────────
def set_volume(level: int) -> str:
    level = _clamp(level)
    ev = _endpoint_volume()
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(level / 100, None)
    return f"установил{END} громкость на {level} процентов"


def change_volume(delta: int) -> str:
    current = round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)
    new = _clamp(current + int(float(delta)))
    set_volume(new)
    return f"{'прибавил' if delta > 0 else 'убавил'}{END} громкость до {new} процентов"


def mute(state: bool = True) -> str:
    _endpoint_volume().SetMute(1 if state else 0, None)
    return f"{'выключил' if state else 'включил'}{END} звук"
