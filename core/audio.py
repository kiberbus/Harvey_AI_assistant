"""Звук Windows: источники звука, приглушение музыки на время речи, общая громкость."""

from __future__ import annotations

import ctypes
import json
import re
import time

from config import (
    AUDIO_DEVICES,
    AUDIO_DEVICES_SKIP,
    BASE_DIR,
    DUCK_FACTOR,
    DUCK_MAX_SEC,
    FADE_STEPS,
    NO_VOLUME_CONTROL,
)
from core.util import (
    AudioUtilities,
    CLSCTX_ALL,
    DEVICE_STATE,
    EDataFlow,
    END,
    ERole,
    FAIL,
    HAS_PYCAW,
    INFO,
    IAudioEndpointVolume,
    IAudioMeterInformation,
    OWN_PID,
    _clamp,
    log,
)


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
    """Можно ли управлять источником: не сама Харви, не системные звуки, не NO_VOLUME_CONTROL."""
    if session.ProcessId in (0, OWN_PID):
        return False
    return _session_name(session).lower() not in NO_VOLUME_CONTROL


def active_audio_sessions() -> list:
    return [s for s in _audio_sessions() if _foreign(s) and (s.State == 1 or _session_peak(s) > 0.001)]


def audio_is_playing(window: float = 0.25) -> bool:
    """Проверяю по пиковому уровню, реально ли что-то звучит."""
    end = time.time() + window
    while True:
        if any(_foreign(s) and _session_peak(s) > 0.002 for s in _audio_sessions()):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.05)


DUCK_STATE_FILE = BASE_DIR / ".duck_state.json"   # если программу закроют, пока музыка приглушена


class Ducker:
    """Приглушает фоновый звук, пока Харви говорит.
    Громкость запоминаю по приложению, а не по сеансу: браузеры пересоздают сеансы после паузы,
    и Windows выдаёт новому сеансу уже приглушённую громкость. Из-за этого у меня музыка
    иногда так и оставалась тихой."""

    def __init__(self) -> None:
        self._saved: dict[str, float] = {}     # имя процесса → исходная громкость
        self._since = 0.0

    @property
    def active(self) -> bool:
        return bool(self._saved)

    @property
    def stale(self) -> bool:
        """Страховка: слишком долго приглушено - возвращаю громкость."""
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
        """Все текущие сеансы этих приложений, включая пересозданные."""
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
        """Если приложение сейчас приглушено, меняю сохранённую громкость, иначе после речи
        вернётся старая."""
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
        return self._saved.get(name, session.SimpleAudioVolume.GetMasterVolume())

    def recover(self) -> None:
        """Если в прошлый раз программа упала с приглушённой музыкой - возвращаю громкость."""
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


def set_volume(level: int) -> str:
    level = _clamp(level)
    ev = _endpoint_volume()
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(level / 100, None)
    return f"установил{END} громкость на {level} процентов"


def change_volume(delta: int) -> str:
    delta = int(float(delta))                   # от ИИ может прийти и строка «10»
    current = round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)
    new = _clamp(current + delta)
    set_volume(new)
    return f"{'прибавил' if delta > 0 else 'убавил'}{END} громкость до {new} процентов"


def volume_state() -> tuple[int, bool]:
    """(громкость в процентах, выключен ли звук) - чтобы потом вернуть как было."""
    ev = _endpoint_volume()
    return round(ev.GetMasterVolumeLevelScalar() * 100), bool(ev.GetMute())


def restore_volume(level: int, muted: bool) -> None:
    ev = _endpoint_volume()
    ev.SetMasterVolumeLevelScalar(_clamp(level) / 100, None)
    ev.SetMute(1 if muted else 0, None)


def mute(state: bool = True) -> str:
    _endpoint_volume().SetMute(1 if state else 0, None)
    return f"{'выключил' if state else 'включил'}{END} звук"


def device_alias(target: str) -> str | None:
    """«в наушниках» → «наушники» (ключ AUDIO_DEVICES). Сравниваю по первым пяти буквам,
    чтобы подходили любые падежи."""
    words = re.findall(r"\w+", target.lower())
    for alias in AUDIO_DEVICES:
        if any(len(w) >= 5 and w[:5] == alias[:5] for w in words):
            return alias
    return None


def _output_devices() -> list:
    """Включённые устройства вывода без виртуальных (AUDIO_DEVICES_SKIP)."""
    if not HAS_PYCAW:
        raise RuntimeError("pycaw не установлен")
    devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    return [d for d in devices
            if d.FriendlyName and not any(s in d.FriendlyName.lower() for s in AUDIO_DEVICES_SKIP)]


def _find_output(target: str, devices: list):
    """Устройство по словам из фразы: сначала псевдоним из AUDIO_DEVICES, потом само название («fifine»)."""
    alias = device_alias(target)
    if alias:
        for part in AUDIO_DEVICES[alias]:
            for d in devices:
                if part in d.FriendlyName.lower():
                    return d
        return None
    words = [w for w in re.findall(r"\w+", target.lower()) if len(w) >= 3]
    for d in devices:
        if any(w in d.FriendlyName.lower() for w in words):
            return d
    return None


def _device_label(device) -> str:
    """Как назвать устройство вслух: «наушники», а не «Speakers (fifine SC3)»."""
    name = device.FriendlyName.lower()
    for alias, parts in AUDIO_DEVICES.items():
        if any(part in name for part in parts):
            return alias
    inner = re.search(r"\(([^()]+)\)", device.FriendlyName)    # «Speakers (fifine SC3)» → «fifine SC3»
    return inner.group(1) if inner else device.FriendlyName


def set_output_device(target: str = "") -> str:
    """«Переключи звук на наушники». Без цели - на следующее устройство по кругу."""
    devices = _output_devices()
    current = AudioUtilities.GetSpeakers().id
    if target:
        device = _find_output(target, devices)
        if device is None:
            log("Звук", f"нет устройства «{target}», есть: {', '.join(d.FriendlyName for d in devices)}")
            return f"{FAIL}{'не нашла' if END else 'не нашёл'} устройство {target}"
    else:
        if len(devices) < 2:
            return f"{INFO}другого устройства вывода нет"
        ids = [d.id for d in devices]
        device = devices[(ids.index(current) + 1) % len(devices)] if current in ids else devices[0]
    label = device_alias(target) or _device_label(device)       # «в наушниках» → «наушники»
    if device.id == current:
        return f"{INFO}звук уже идёт через {label}"
    # Все три роли: иначе звонки (Discord, Telegram) остались бы на старом устройстве
    AudioUtilities.SetDefaultDevice(device.id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
    log("Звук", f"устройство вывода: {device.FriendlyName}")
    from core import speech           # speech импортирует audio, поэтому не наверху
    speech.follow_output(device.FriendlyName)
    return f"переключил{END} звук на {label}"


def output_device_info() -> str:
    """«Куда идёт звук»."""
    return f"{INFO}звук идёт через {_device_label(AudioUtilities.GetSpeakers())}"
