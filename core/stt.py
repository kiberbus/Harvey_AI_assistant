"""Слух: Whisper, VAD, слово-активатор (openWakeWord), запись фраз с микрофона."""

from __future__ import annotations

import difflib
import numpy as np
import os
import queue
import sounddevice as sd
import threading
import time
from collections import deque
import subprocess
import sys
from pathlib import Path
from typing import Callable

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
from core.util import (  # noqa: F401
    HALLUCINATIONS,
    PUNCT,
    WAKE_PATTERN,
    log,
)


PRE_BUFFER_BLOCKS = int(PRE_BUFFER_SEC * SAMPLE_RATE / BLOCK_SIZE)
SILENCE_BLOCKS = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
EARLY_SILENCE_BLOCKS = max(1, round(EARLY_SILENCE * SAMPLE_RATE / BLOCK_SIZE)) if EARLY_SILENCE else 0
EARLY_MAX_BLOCKS = int(EARLY_MAX_SEC * SAMPLE_RATE / BLOCK_SIZE)
MIN_SPEECH_BLOCKS = int(MIN_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
MAX_UTTERANCE_BLOCKS = int(MAX_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
COMMAND_WAIT_BLOCKS = int(COMMAND_TIMEOUT * SAMPLE_RATE / BLOCK_SIZE)


# ───────────────────────── WHISPER ─────────────────────────
_whisper_model = None
_whisper_lock = threading.Lock()
_whisper_ready = threading.Event()


def _add_nvidia_dll_dirs() -> None:
    """Подключает cuBLAS/cuDNN из pip-пакетов nvidia-*-cu12: CTranslate2 ищет именно CUDA 12,
    а системная CUDA 13 (cublas64_13.dll) ему не подходит."""
    try:
        import nvidia  # noqa: F401  (namespace-пакет, ставится вместе с nvidia-cublas-cu12 и др.)
    except ImportError:
        return
    dirs: list[str] = []
    for base in getattr(nvidia, "__path__", []):
        for sub in Path(base).iterdir():
            if (sub / "bin").is_dir():
                dirs.append(str(sub / "bin"))
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def _create_whisper(size: str, device: str, compute: str):
    from faster_whisper import WhisperModel

    return WhisperModel(
        size,
        device=device,
        compute_type=compute,
        cpu_threads=min(8, os.cpu_count() or 4),
    )


def _load_whisper() -> None:
    """Грузит Whisper и сразу прогоняет секунду тишины: именно на первом распознавании
    вылезает «cublas64_12.dll not found», поэтому ошибку ловим здесь и откатываемся на процессор."""
    global _whisper_model
    try:
        _add_nvidia_dll_dirs()
        attempts = [(WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE)]
        if WHISPER_DEVICE != "cpu":      # turbo на процессоре медленная — там берём модель поменьше
            attempts.append((WHISPER_CPU_MODEL, "cpu", "int8"))
        for size, device, compute in attempts:
            try:
                log("STT", f"Загружаю Whisper '{size}' ({device}, {compute})...")
                model = _create_whisper(size, device, compute)
                segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                               language="ru", without_timestamps=True)
                list(segments)                      # прогрев
                _whisper_model = model
                log("STT", f"Whisper готов ({size}, {device}).")
                break
            except Exception as e:
                log("STT", f"{device}: не получилось — {e}")
        if VAD_ENABLED:
            has_speech(np.zeros(SAMPLE_RATE, dtype=np.float32))     # прогрев VAD
    except Exception as e:
        log("STT", f"Ошибка загрузки Whisper: {e}")
    finally:
        _whisper_ready.set()


_vad_skipped = 0


def has_speech(audio: np.ndarray) -> bool:
    """Silero VAD (идёт вместе с faster-whisper, работает за миллисекунды на процессоре):
    есть ли во фрагменте речь. Стук, кашель, музыку и шум не отдаём тяжёлому Whisper."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=VAD_THRESHOLD, min_speech_duration_ms=150, speech_pad_ms=100)
    return bool(get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE))


_collect_lock = threading.Lock()


def _save_wav(path: Path, audio: np.ndarray) -> None:
    import wave

    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def cut_name(audio: np.ndarray) -> np.ndarray | None:
    """Вырезает имя из фразы «Харви, …» по громкости, без Whisper: имя — первый «островок» звука,
    после которого идёт пауза. Паузы нет (имя слито с командой) — None: лучше меньше образцов, но чистых.
    По краям — запас тишины: без него плеер Windows не успевает доиграть конец короткого файла,
    и кажется, что «-ви» обрезано."""
    hop = 160                                                        # 10 мс
    frames = len(audio) // hop
    if frames < 20:
        return None
    env = np.sqrt((audio[:frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    quiet = max(float(np.percentile(env, 10)) * 3, float(env.max()) * 0.06)
    loud = env > quiet
    first = next((i for i in range(frames - 3) if loud[i:i + 3].all()), None)   # начало речи: 30 мс подряд
    if first is None:
        return None
    last = None
    run = 0
    for i in range(first, min(frames, first + 100)):                # имя ищем в первую секунду речи
        run = run + 1 if not loud[i] else 0
        if run >= 6 and i - run + 1 - first >= 15:                    # 60 мс тишины после ≥150 мс звука
            last = i - run + 1
            break
    if last is None:
        return None
    voiced = (last - first) * hop / SAMPLE_RATE
    if not 0.15 <= voiced <= 0.7:                                    # длиннее — скорее «Харви открой» без паузы
        return None
    start = max(0, (first - 12) * hop)                               # «Х» тихий — 120 мс запаса перед речью
    clip = audio[start:min(len(audio), (last + 6) * hop)].copy()     # и 60 мс после (там уже тишина)
    pad = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)          # 0.3 с тишины — чтобы плеер доиграл
    return np.concatenate([clip, pad])


def collect_name_sample(audio: np.ndarray) -> None:
    """В фоне вырезает из фразы «Харви, …» само имя и сохраняет в WAKE_SAMPLES_DIR —
    так копятся ваши настоящие произношения имени для обучения своей модели. Видеокарту не трогает."""
    if not WAKE_COLLECT:
        return

    def work() -> None:
        if not _collect_lock.acquire(blocking=False):      # одна вырезка за раз
            return
        try:
            WAKE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
            count = sum(1 for _ in WAKE_SAMPLES_DIR.glob("*.wav"))
            if count >= WAKE_SAMPLES_MAX:
                return
            clip = cut_name(audio)
            if clip is None:
                return
            _save_wav(WAKE_SAMPLES_DIR / f"harvey_{time.strftime('%Y%m%d_%H%M%S')}.wav", clip)
            if (count + 1) % 10 == 0 or count == 0:
                log("Wake", f"образцов вашего «Харви» накоплено: {count + 1} (папка {WAKE_SAMPLES_DIR.name})")
        except Exception as e:
            log("Wake", f"не удалось сохранить образец имени: {e}")
        finally:
            _collect_lock.release()

    threading.Thread(target=work, daemon=True).start()


def transcribe(audio: np.ndarray) -> str:
    """float32 16 кГц → текст (с исходным регистром). Без речи (по VAD) — сразу пустая строка."""
    global _vad_skipped
    if _whisper_model is None:
        return ""
    if VAD_ENABLED:
        try:
            if not has_speech(audio):
                _vad_skipped += 1
                print(f"\r[VAD] шум пропущен без распознавания ({_vad_skipped})", end="", flush=True)
                return ""
        except Exception as e:
            log("VAD", f"ошибка, распознаю без проверки: {e}")
    with _whisper_lock:
        segments, _ = _whisper_model.transcribe(
            audio,
            language="ru",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            initial_prompt=WHISPER_PROMPT,
            hotwords=WHISPER_HOTWORDS or None,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()


def is_noise(low: str) -> bool:
    """Отсеивает галлюцинации Whisper на тишине/шуме, включая эхо собственного промпта."""
    if len(low) < 3 or any(h in low for h in HALLUCINATIONS):
        return True
    return difflib.SequenceMatcher(None, low, WHISPER_PROMPT.lower()).ratio() > 0.8


# ───────────────────────── СЛОВО-АКТИВАТОР (openWakeWord) ─────────────────────────
_WAKE_OK_FILE = BASE_DIR / ".wake_ok"    # «эту версию модели уже проверяли» — чтобы не проверять при каждом запуске


def _wake_data_file() -> Path | None:
    """Файл с весами, на который ссылается модель (новый PyTorch кладёт веса рядом: harvey.onnx.data)."""
    data = WAKE_MODEL.read_bytes()
    i = data.find(b"location\x12")                 # запись external_data: key="location", value=<имя файла>
    if i < 0:
        return None
    length = data[i + 9]
    return WAKE_MODEL.parent / data[i + 10:i + 10 + length].decode("utf-8", "replace")


def _check_wake_model() -> str | None:
    """Проверяет модель имени ДО загрузки. Битая модель роняет onnxruntime вместе со всей программой
    (это не исключение, его не поймать), поэтому пробная загрузка идёт в отдельном процессе.
    None — всё хорошо, иначе — понятная причина."""
    data_file = _wake_data_file()
    if data_file is not None and not data_file.exists():
        return (f"у модели {WAKE_MODEL.name} нет файла с весами {data_file.name} — "
                f"скопируйте его из результатов обучения в папку {WAKE_MODEL.parent.name}")
    files = [WAKE_MODEL] + ([data_file] if data_file else [])
    key = ";".join(f"{f.name}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files)
    try:
        if _WAKE_OK_FILE.read_text(encoding="utf-8") == key:
            return None
    except OSError:
        pass
    code = ("import sys\nfrom openwakeword.model import Model\n"
            "Model(wakeword_models=[sys.argv[1]], inference_framework='onnx')\nprint('ok')")
    try:
        result = subprocess.run([sys.executable, "-c", code, str(WAKE_MODEL)], capture_output=True, text=True,
                                timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return f"не удалось проверить {WAKE_MODEL.name}: {e}"
    if result.returncode != 0 or "ok" not in result.stdout:
        tail = (result.stderr or "").strip().splitlines()[-1:] or [f"код {result.returncode}"]
        return f"модель {WAKE_MODEL.name} не загружается ({tail[0][:150]})"
    _WAKE_OK_FILE.write_text(key, encoding="utf-8")
    return None


class WakeDetector:
    """Маленькая нейросеть слушает имя на каждом блоке звука (~2 мс на 0.1 с).
    Whisper запускается, только если имя прозвучало, — остальное время видеокарта отдыхает,
    а разговоры рядом и телевизор не распознаются вовсе."""

    def __init__(self) -> None:
        self._model = None
        self._hit = False
        self._peak = 0.0             # наибольшая уверенность в имени с прошлой проверки — для подбора порога
        self.last_peak = 0.0         # то же для последней фразы (после take)

    @property
    def enabled(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if not WAKE_MODEL.exists():
            log("Wake", f"модели {WAKE_MODEL.name} нет — имя ищет Whisper (как раньше). "
                        f"Как обучить свою — см. папку {WAKE_MODEL.parent.name}")
            return
        problem = _check_wake_model()
        if problem:
            log("Wake", f"{problem} — имя ищет Whisper, как раньше")
            return
        try:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=[str(WAKE_MODEL)], inference_framework="onnx")
            log("Wake", f"слушаю имя моделью {WAKE_MODEL.name} (порог {WAKE_THRESHOLD})")
        except Exception as e:
            log("Wake", f"не удалось загрузить {WAKE_MODEL.name}: {e} — имя ищет Whisper")

    def feed(self, block: np.ndarray) -> None:
        if self._model is None:
            return
        try:
            scores = self._model.predict((np.clip(block, -1, 1) * 32767).astype(np.int16))
            score = float(max(scores.values(), default=0))
            self._peak = max(self._peak, score)
            if score >= WAKE_THRESHOLD:
                self._hit = True
        except Exception as e:
            log("Wake", f"ошибка: {e} — отключаю, имя будет искать Whisper")
            self._model = None

    def peek(self) -> bool:
        """Звучало ли имя (флаг не сбрасывается)."""
        return self._hit

    def take(self) -> bool:
        """Звучало ли имя с прошлой проверки (флаг сбрасывается)."""
        hit, self._hit = self._hit, False
        if self._peak >= 0.1:              # похоже на имя хоть немного — пишем, чтобы подобрать WAKE_THRESHOLD
            log("Wake", f"уверенность в имени {self._peak:.2f} (порог {WAKE_THRESHOLD}) — {'услышала' if hit else 'мимо'}")
        self.last_peak, self._peak = self._peak, 0.0
        return hit


_wake = WakeDetector()


def _get_block(audio_q: queue.Queue, timeout: float | None = None) -> np.ndarray:
    """Следующий блок с микрофона; заодно отдаём его детектору имени."""
    block = audio_q.get(timeout=timeout).flatten()
    _wake.feed(block)
    return block


def _strip_name_like(text: str) -> str:
    """Имя услышала нейросеть, а Whisper записал его иначе («Гарри, пауза») — убираем первое слово,
    если оно похоже на имя."""
    words = text.split(maxsplit=1)
    if words and difflib.SequenceMatcher(None, words[0].lower().strip(PUNCT), "харви").ratio() >= 0.5:
        return words[1] if len(words) > 1 else ""
    return text


# ───────────────────────── ЗАПИСЬ РЕЧИ ─────────────────────────
def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block ** 2)))


def calibrate_silence(duration: float = 0.5) -> float:
    """Измеряет фоновый шум и считает порог чувствительности."""
    recording = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return max(0.005, min(0.02, _rms(recording.flatten()) * 1.8))


def drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def record_utterance(audio_q: queue.Queue, pre_buffer: deque, first_block: np.ndarray,
                     threshold: float, early_check: Callable[[np.ndarray], bool] | None = None) -> np.ndarray | None:
    """Пишет фразу от первого громкого блока до паузы; лишнюю тишину в конце обрезает (быстрее STT).
    early_check: после короткой паузы (EARLY_SILENCE) спрашиваем, не законченная ли это команда —
    если да, не ждём полную паузу SILENCE_DURATION («пауза», «громкость 30» срабатывают на ~0.5 с раньше)."""
    chunks = list(pre_buffer) + [first_block]
    silent = checks = 0
    while silent < SILENCE_BLOCKS and len(chunks) < MAX_UTTERANCE_BLOCKS:
        block = _get_block(audio_q)
        chunks.append(block)
        silent = silent + 1 if _rms(block) < threshold else 0
        if (early_check and silent == EARLY_SILENCE_BLOCKS and checks < 2
                and MIN_SPEECH_BLOCKS <= len(chunks) <= EARLY_MAX_BLOCKS):
            checks += 1
            trim = silent - TAIL_KEEP_BLOCKS
            candidate = np.concatenate(chunks[:-trim] if trim > 0 else chunks)
            if early_check(candidate):
                return candidate
    if silent > TAIL_KEEP_BLOCKS:
        chunks = chunks[:-(silent - TAIL_KEEP_BLOCKS)]
    if len(chunks) < MIN_SPEECH_BLOCKS:
        return None
    return np.concatenate(chunks)


def record_command(audio_q: queue.Queue, threshold: float) -> np.ndarray | None:
    """Ждёт команду после одиночного «Харви»."""
    pre: deque = deque(maxlen=3)
    chunks: list[np.ndarray] = []
    started = False
    waited = silent = 0
    while True:
        block = _get_block(audio_q)
        waited += 1
        loud = _rms(block) > threshold
        if not started:
            if loud:
                started = True
                chunks = list(pre) + [block]
            else:
                pre.append(block)
                if waited > COMMAND_WAIT_BLOCKS:
                    return None
            continue
        chunks.append(block)
        silent = 0 if loud else silent + 1
        if silent >= SILENCE_BLOCKS or len(chunks) >= MAX_UTTERANCE_BLOCKS:
            break
    if silent > TAIL_KEEP_BLOCKS:
        chunks = chunks[:-(silent - TAIL_KEEP_BLOCKS)]
    return np.concatenate(chunks)
