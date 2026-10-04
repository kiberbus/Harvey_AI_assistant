"""Голос Харви: синтез речи (Silero / Piper), сигналы, очередь фраз."""

from __future__ import annotations

import ctypes
import numpy as np
import os
import queue
import re
import sounddevice as sd
import threading
import time
from pathlib import Path

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
from core.util import (  # noqa: F401
    PIPER_DIR,
    PIPER_MODEL,
    PiperVoice,
    SynthesisConfig,
    address,
    log,
    normalize_for_tts,
)
from core.audio import (  # noqa: F401
    _ducker,
)


PREWARM_PHRASES = (
    "Да, господин?",
    "Секунду, господин.",
    "Слушаюсь, господин, готово.",
    "Простите, господин, произошла ошибка.",
) if not QUIET_MODE else ("Произошла ошибка.", "Отключаюсь.")


# ───────────────────────── PIPER TTS ─────────────────────────
_piper_voice: PiperVoice | None = None
_silero_model = None
_engine = "piper"            # какой голос реально загружен: "piper" или "silero"
_piper_lock = threading.Lock()
_tts_queue: queue.Queue = queue.Queue()    # (вид "text"/"sound", текст или имя звука, поколение) → синтез
_play_queue: queue.Queue = queue.Queue()   # ((аудио, частота), поколение) → динамики
_tts_cache: dict[str, tuple[np.ndarray, int]] = {}
TTS_CACHE_MAX = 200
_inflight = 0                  # фраз/звуков в очереди, в синтезе или в динамиках
_inflight_lock = threading.Lock()
_speaking = threading.Event()   # пока set — микрофон «глохнет», чтобы не слушать саму себя


def _ensure_piper() -> None:
    global _piper_voice
    if PiperVoice is None:
        raise RuntimeError("piper-tts не установлен")
    PIPER_DIR.mkdir(exist_ok=True)

    if not PIPER_MODEL.exists():
        log("TTS", f"Скачиваю русскую модель {PIPER_VOICE}-medium (~60 MB)...")
        import urllib.request

        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/{PIPER_VOICE}/medium"
        for fname in (PIPER_MODEL.name, PIPER_MODEL.name + ".json"):
            dest = PIPER_DIR / fname
            if not dest.exists():
                log("TTS", f"  -> {fname}...")
                urllib.request.urlretrieve(f"{base}/{fname}", dest)

    # espeakbridge.pyd ищет данные по захардкоженному пути сборки — копируем туда espeak-ng-data.
    import shutil

    espeak_src = BASE_DIR
    try:
        import piper.phonemize_espeak as _pe

        espeak_src = Path(_pe.__file__).parent / "espeak-ng-data"
    except Exception:
        pass

    hardcoded = Path(
        r"D:\a\piper1-gpl\piper1-gpl\_skbuild\win-amd64-3.9\cmake-build"
        r"\espeak_ng-install\share\espeak-ng-data"
    )
    if espeak_src.exists() and not hardcoded.exists():
        log("TTS", "Настраиваю espeak-ng-data...")
        hardcoded.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(espeak_src), str(hardcoded), dirs_exist_ok=True)

    has_cuda = False
    try:
        ctypes.CDLL("cublasLt64_13.dll")
        has_cuda = True
    except Exception:
        pass

    _piper_voice = PiperVoice.load(str(PIPER_MODEL), use_cuda=has_cuda)
    log("TTS", f"TTS готов ({'GPU (CUDA)' if has_cuda else 'CPU'}).")


_tts_gen = 0   # растёт при каждом прерывании: устаревшие фразы из очереди не произносятся


def _silero_model_file() -> Path:
    """Модель лежит рядом со скриптом (папка silero). Качаем сами, а не через пакет silero:
    пути с кириллицей (C:\\Слуга\\...) PyTorch на Windows открыть не может."""
    path = BASE_DIR / "silero" / f"{SILERO_MODEL}.pt"
    if path.exists() and path.stat().st_size > 10_000_000:
        return path
    import urllib.request

    url = f"https://models.silero.ai/models/tts/ru/{SILERO_MODEL}.pt"
    log("TTS", f"Скачиваю модель Silero: {url}")
    path.parent.mkdir(exist_ok=True)
    part = path.with_suffix(".part")
    urllib.request.urlretrieve(url, part)
    part.replace(path)
    return path


def _ensure_silero() -> None:
    global _silero_model, _engine
    import io

    import torch
    from torch.package import PackageImporter

    torch.set_num_threads(min(4, os.cpu_count() or 4))
    data = _silero_model_file().read_bytes()                  # читаем средствами Python (юникод-пути работают)
    model = PackageImporter(io.BytesIO(data)).load_pickle("tts_models", "model")
    model.to(torch.device(SILERO_DEVICE))
    _silero_model, _engine = model, "silero"
    log("TTS", f"Silero готов (голос {SILERO_SPEAKER}, {SILERO_DEVICE}).")


def _ensure_tts() -> None:
    """Грузит голос из config.TTS_ENGINE; если Silero не взлетел — откатывается на Piper."""
    global _engine
    if TTS_ENGINE == "silero":
        try:
            _ensure_silero()
            return
        except Exception as e:
            log("TTS", f"Silero недоступен ({e}) — использую Piper.")
            if isinstance(e, ModuleNotFoundError) and e.name:
                log("TTS", f"Не хватает библиотеки, поставьте её: pip install {e.name.split('.')[0]}")
    _ensure_piper()
    _engine = "piper"


def _tts_ready() -> bool:
    return (_engine == "silero" and _silero_model is not None) or (_engine == "piper" and _piper_voice is not None)


def _synthesize_piper(text: str) -> tuple[np.ndarray, int] | None:
    with _piper_lock:
        sr = _piper_voice.config.sample_rate
        rate = sr
        try:
            if SynthesisConfig is None:
                raise TypeError("SynthesisConfig недоступен")
            cfg = SynthesisConfig(length_scale=1.0 / TTS_SPEED)      # <1 — быстрее, голос не искажается
            chunks = [c.audio_float_array for c in _piper_voice.synthesize(text, syn_config=cfg)]
        except TypeError:
            chunks = [c.audio_float_array for c in _piper_voice.synthesize(text)]
            rate = int(sr * TTS_SPEED)                               # запасной вариант: быстрее проигрываем
    return (np.concatenate(chunks), rate) if chunks else None


def _split_sentences(text: str, limit: int = 300) -> list[str]:
    """Silero плохо переносит длинные тексты — режем по предложениям."""
    chunks: list[str] = []
    current = ""
    for part in re.split(r"(?<=[.!?;])\s+", text):
        if current and len(current) + len(part) + 1 > limit:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)
    return chunks


def _synthesize_silero(text: str) -> tuple[np.ndarray, int] | None:
    from xml.sax.saxutils import escape

    rate_name = "x-fast" if TTS_SPEED >= 1.5 else "fast" if TTS_SPEED >= 1.15 else "medium"
    pieces: list[np.ndarray] = []
    with _piper_lock:
        for chunk in _split_sentences(normalize_for_tts(text)):
            kwargs = dict(speaker=SILERO_SPEAKER, sample_rate=SILERO_SAMPLE_RATE)
            ssml = f'<speak><prosody rate="{rate_name}">{escape(chunk)}</prosody></speak>'
            for attempt in (dict(ssml_text=ssml, put_accent=True, put_yo=True),
                            dict(text=chunk, put_accent=True, put_yo=True),
                            dict(text=chunk)):
                try:
                    audio = _silero_model.apply_tts(**attempt, **kwargs)
                    break
                except Exception as e:
                    last_error = e
            else:
                raise last_error
            pieces.append(audio.detach().cpu().numpy().astype("float32"))
    return (np.concatenate(pieces), SILERO_SAMPLE_RATE) if pieces else None


def _synthesize(text: str) -> tuple[np.ndarray, int] | None:
    cached = _tts_cache.get(text)
    if cached:
        return cached
    result = _synthesize_silero(text) if _engine == "silero" else _synthesize_piper(text)
    if result and len(text) <= 80:
        if len(_tts_cache) >= TTS_CACHE_MAX:
            _tts_cache.pop(next(iter(_tts_cache)))          # выбрасываем самую старую фразу
        _tts_cache[text] = result
    return result


SOUND_RATE = 44100


def _chime(notes: tuple[float, ...], step_ms: int = 110, decay_ms: int = 260, volume: float = 0.3) -> np.ndarray:
    """Мягкий «колокольчик»: плавная атака, естественное затухание, тёплые обертоны.
    Ноты идут с шагом step_ms и звучат внахлёст. По краям — тишина: звуковая карта
    «съедает» самое начало и конец буфера, из-за этого простой писк казался обрезанным."""
    rate = SOUND_RATE
    lead, tail = int(rate * 0.003), int(rate * 0.03)              # поток открыт заранее — запас почти не нужен
    n = int(rate * (decay_ms * 2.2) / 1000)                      # пока затухание почти не уйдёт в ноль
    t = np.arange(n) / rate
    attack = np.minimum(1.0, t / 0.008)                          # 8 мс — без щелчка, но не «вяло»
    out = np.zeros(lead + int(rate * step_ms / 1000) * (len(notes) - 1) + n + tail, dtype=np.float64)
    for i, f in enumerate(notes):
        tone = (np.sin(2 * np.pi * f * t) * np.exp(-t / (decay_ms / 1000))
                + 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / (decay_ms / 2500))    # обертон гаснет быстрее
                + 0.08 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / (decay_ms / 5000)))
        start = lead + int(rate * step_ms / 1000) * i
        out[start:start + n] += tone * attack
    out *= volume / max(1e-9, np.abs(out).max())
    out[-tail - int(rate * 0.02):-tail] *= np.linspace(1, 0, int(rate * 0.02))  # гарантированно гаснет в ноль
    out[-tail:] = 0
    return out.astype(np.float32)


SOUNDS: dict[str, tuple[np.ndarray, int]] = {
    "done": (_chime((523.3, 784.0), volume=SOUND_VOLUME), SOUND_RATE),                  # до → соль, вверх
    "error": (_chime((440.0, 329.6), step_ms=150, decay_ms=300, volume=SOUND_VOLUME), SOUND_RATE),  # ля → ми, вниз
    "cancel": (_chime((392.0,), decay_ms=200, volume=SOUND_VOLUME * 0.7), SOUND_RATE),  # одна тихая соль
    "ready": (_chime((523.3, 659.3, 784.0), step_ms=90, volume=SOUND_VOLUME), SOUND_RATE),  # до-ми-соль
}
_WAKE_SOUND = _chime((BEEP_FREQ,), decay_ms=BEEP_MS, volume=SOUND_VOLUME)


class Chimes:
    """Отдельный, заранее открытый поток вывода для сигналов. sd.play() каждый раз заново
    открывает звуковое устройство (на Windows это 0.1–0.3 с) — отсюда была задержка «колокольчика».
    Поток открываем, как только вы начали говорить, и закрываем после CHIME_IDLE_SEC тишины:
    постоянно открытый аудиопоток мешал бы компьютеру уходить в сон."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voices: list[list] = []          # [сэмплы, позиция] — звуки могут накладываться
        self._stream = None
        self._last_used = 0.0

    def warm(self) -> None:
        """Открыть поток заранее (вызывается при начале речи)."""
        with self._lock:
            self._last_used = time.time()
            if self._stream is not None:
                return
            # Сначала WASAPI (~20 мс задержки), если не вышло — системный по умолчанию MME (~90 мс)
            for device, extra in ((self._wasapi_device(), sd.WasapiSettings(auto_convert=True)), (None, None)):
                if device is None and extra is not None:
                    continue
                try:
                    self._stream = sd.OutputStream(device=device, samplerate=SOUND_RATE, channels=1, dtype="float32",
                                                   latency="low", callback=self._callback, extra_settings=extra)
                    self._stream.start()
                    return
                except Exception as e:
                    self._stream = None
                    log("Звук", f"не удалось открыть поток для сигналов ({'WASAPI' if extra else 'MME'}): {e}")

    @staticmethod
    def _wasapi_device() -> int | None:
        """Те же колонки, что выбраны в Windows по умолчанию, но через WASAPI."""
        try:
            default = sd.query_devices(kind="output")["name"]
            for i, d in enumerate(sd.query_devices()):
                api = sd.query_hostapis(d["hostapi"])["name"]
                if d["max_output_channels"] and "WASAPI" in api and d["name"].startswith(default[:28]):
                    return i
        except Exception:
            pass
        return None

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            for voice in self._voices:
                chunk = voice[0][voice[1]:voice[1] + frames]
                out[:len(chunk)] += chunk
                voice[1] += frames
            self._voices = [v for v in self._voices if v[1] < len(v[0])]
        outdata[:, 0] = out

    def play(self, samples: np.ndarray) -> bool:
        self.warm()
        with self._lock:
            if self._stream is None:
                return False
            self._voices.append([samples, 0])
            return True

    def close_if_idle(self) -> None:
        with self._lock:
            if self._stream is None or self._voices or time.time() - self._last_used < CHIME_IDLE_SEC:
                return
            stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


_chimes = Chimes()


def play_beep() -> None:
    """Короткий сигнал «слушаю» — мгновенно, без ожидания синтеза речи."""
    if _chimes.play(_WAKE_SOUND):
        time.sleep(len(_WAKE_SOUND) / SOUND_RATE * 0.6)   # ждём основную часть: дальше выбросим её эхо
        return
    try:
        sd.play(_WAKE_SOUND, samplerate=SOUND_RATE)
        sd.wait()
    except Exception:
        pass


def _prewarm_tts() -> None:
    for phrase in PREWARM_PHRASES:
        try:
            _synthesize(address(phrase))
        except Exception:
            return


def _enqueue(kind: str, payload: str) -> None:
    global _inflight
    with _inflight_lock:
        _inflight += 1
    if kind == "text":      # сигналы микрофон не «глушат»: их эхо отсеет VAD, а диалог не прервётся
        _speaking.set()
    _tts_queue.put((kind, payload, _tts_gen))


def _item_done(count: int = 1, tail: bool = True) -> None:
    """Фраза закончилась (или выброшена). Когда очередь пуста — микрофон снова обычной чувствительности."""
    global _inflight
    with _inflight_lock:
        _inflight = max(0, _inflight - count)
        idle = _inflight == 0
    if idle and tail:
        time.sleep(0.25)                    # хвост эха от динамиков
        with _inflight_lock:
            if _inflight == 0:
                _speaking.clear()


def _synth_worker() -> None:
    """Синтезирует фразы заранее: пока звучит одно предложение, следующее уже готовится."""
    while True:
        kind, payload, gen = _tts_queue.get()
        audio = None
        if gen == _tts_gen:
            try:
                if kind == "sound":
                    audio = SOUNDS.get(payload)
                elif _tts_ready():
                    audio = _synthesize(payload)
                else:
                    log("TTS", "Голосовая модель не загружена")
            except Exception as e:
                log("TTS", f"Ошибка синтеза: {e}")
        if audio is not None and gen == _tts_gen:
            _play_queue.put((audio, gen))
        else:
            _item_done()


def _play_worker() -> None:
    while True:
        (samples, rate), gen = _play_queue.get()
        try:
            if gen == _tts_gen:
                sd.play(samples, samplerate=rate)
                sd.wait()
        except Exception as e:
            log("TTS", f"Ошибка воспроизведения: {e}")
        finally:
            _item_done()


def _drop_queued(q: queue.Queue) -> int:
    dropped = 0
    while True:
        try:
            q.get_nowait()
            dropped += 1
        except queue.Empty:
            return dropped


def stop_speaking() -> None:
    """Мгновенно обрывает речь и очищает очередь фраз."""
    global _tts_gen
    _tts_gen += 1
    _item_done(_drop_queued(_tts_queue) + _drop_queued(_play_queue), tail=False)
    try:
        sd.stop()
    except Exception:
        pass
    _speaking.clear()


def speak(text: str) -> None:
    """Говорит в фоне: управление возвращается сразу, можно давать следующую команду."""
    text = address(text)
    if not text:
        return
    print(f"\n{ASSISTANT_NAME}: {text}", flush=True)
    if threading.current_thread() is threading.main_thread():
        _ducker.duck()                      # приглушаем музыку только когда реально говорим
    _enqueue("text", text)


# Граница, по которой отдаём кусок ответа в озвучку: конец предложения или перевод строки
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_STREAM_COMMA_AT = 120          # длинное предложение без точки режем по последней запятой


def speak_stream(pieces) -> str:
    """Озвучивает текст, который приходит кусками (ответ ИИ), по предложениям — не дожидаясь конца.
    Возвращает всё сказанное."""
    spoken: list[str] = []
    buffer = ""

    def say_part(part: str) -> None:
        part = part.strip()
        if part:
            spoken.append(part)
            speak(part)

    for piece in pieces:
        buffer += piece
        *ready, buffer = _SENTENCE_END_RE.split(buffer)
        for sentence in ready:
            say_part(sentence)
        if len(buffer) > _STREAM_COMMA_AT and "," in buffer:
            head, buffer = buffer.rsplit(",", 1)
            say_part(head + ",")
    say_part(buffer)
    return " ".join(spoken)


def play_sound(name: str) -> None:
    """Короткий звук «готово» / «ошибка» / … — через ту же очередь, чтобы не перебивать речь."""
    sound = SOUNDS.get(name)
    if sound is not None and _chimes.play(sound[0]):
        return                              # мгновенно, через заранее открытый поток
    _enqueue("sound", name)                 # запасной путь — через общую очередь


def wait_silence() -> None:
    while _speaking.is_set() or _inflight:
        time.sleep(0.02)


def speak_sync(text: str) -> None:
    """Говорит и ждёт конца фразы (нужно только для коротких вопросов и прощания)."""
    speak(text)
    wait_silence()
