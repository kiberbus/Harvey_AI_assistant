"""Слух: Whisper, VAD, слово-активатор, запись фраз с микрофона."""

from __future__ import annotations

import difflib
import numpy as np
import os
import queue
import re
import sounddevice as sd
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Callable

from config import (
    BASE_DIR,
    BLOCK_SIZE,
    COMMAND_TIMEOUT,
    EARLY_MAX_SEC,
    EARLY_SILENCE,
    MAX_LAG_SEC,
    MAX_UTTERANCE_SEC,
    MIN_UTTERANCE_SEC,
    PRE_BUFFER_SEC,
    SAMPLE_RATE,
    SILENCE_DURATION,
    SLEEP_MAX_SEC,
    TAIL_KEEP_BLOCKS,
    VAD_ENABLED,
    VAD_THRESHOLD,
    WAKE_COLLECT,
    WAKE_GATE,
    WAKE_MODEL,
    WAKE_SAMPLES_DIR,
    WAKE_SAMPLES_MAX,
    WAKE_THRESHOLD,
    WHISPER_COMPUTE,
    WHISPER_CPU_MODEL,
    WHISPER_DEVICE,
    WHISPER_HOTWORDS,
    WHISPER_MODEL_SIZE,
    WHISPER_PROMPT,
)
from core.util import (
    HALLUCINATIONS,
    PUNCT,
    log,
)


PRE_BUFFER_BLOCKS = int(PRE_BUFFER_SEC * SAMPLE_RATE / BLOCK_SIZE)
SILENCE_BLOCKS = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
EARLY_SILENCE_BLOCKS = max(1, round(EARLY_SILENCE * SAMPLE_RATE / BLOCK_SIZE)) if EARLY_SILENCE else 0
EARLY_MAX_BLOCKS = int(EARLY_MAX_SEC * SAMPLE_RATE / BLOCK_SIZE)
MIN_SPEECH_BLOCKS = int(MIN_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
MAX_UTTERANCE_BLOCKS = int(MAX_UTTERANCE_SEC * SAMPLE_RATE / BLOCK_SIZE)
COMMAND_WAIT_BLOCKS = int(COMMAND_TIMEOUT * SAMPLE_RATE / BLOCK_SIZE)
MAX_LAG_BLOCKS = int(MAX_LAG_SEC * SAMPLE_RATE / BLOCK_SIZE)
LAG_KEEP_BLOCKS = int(2.0 * SAMPLE_RATE / BLOCK_SIZE)     # сбросив отставание, последние 2 с оставляю: вдруг там начало фразы
SLOW_STT_SEC = 2.0             # распознавание дольше этого - пишу в лог: видеокарта, скорее всего, занята


_whisper_model = None
_whisper_lock = threading.Lock()
_whisper_ready = threading.Event()


def _add_nvidia_dll_dirs() -> None:
    """Подключаю cuBLAS/cuDNN из pip-пакетов nvidia-*-cu12: CTranslate2 нужна CUDA 12,
    а системная CUDA 13 ему не подходит."""
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
    """Грузит Whisper и сразу прогоняет секунду тишины: ошибка "cublas64_12.dll not found"
    вылезает только на первом распознавании, поэтому ловлю её здесь и перехожу на процессор."""
    global _whisper_model
    try:
        _add_nvidia_dll_dirs()
        attempts = [(WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE)]
        if WHISPER_DEVICE != "cpu":      # turbo на процессоре медленная - там берём модель поменьше
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
                log("STT", f"{device}: не получилось - {e}")
        if VAD_ENABLED:
            has_speech(np.zeros(SAMPLE_RATE, dtype=np.float32))     # прогрев VAD
    except Exception as e:
        log("STT", f"Ошибка загрузки Whisper: {e}")
    finally:
        _whisper_ready.set()


_vad_skipped = 0


def has_speech(audio: np.ndarray) -> bool:
    """Silero VAD (идёт с faster-whisper): есть ли в куске речь. Шум и музыку Whisper не отдаю."""
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
    """Вырезает имя из "Харви, ..." по громкости, без Whisper: имя - первый кусок звука перед паузой.
    Если паузы нет (имя слито с командой) - None, лучше меньше образцов, но чистых.
    По краям оставляю тишину, иначе плеер Windows не доигрывает конец короткого файла."""
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
    if not 0.15 <= voiced <= 0.7:                                    # длиннее - скорее «Харви открой» без паузы
        return None
    start = max(0, (first - 12) * hop)                               # «Х» тихий - 120 мс запаса перед речью
    clip = audio[start:min(len(audio), (last + 6) * hop)].copy()     # и 60 мс после (там уже тишина)
    pad = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)          # 0.3 с тишины - чтобы плеер доиграл
    return np.concatenate([clip, pad])


def collect_name_sample(audio: np.ndarray) -> None:
    """В фоне сохраняет имя из фраз в WAKE_SAMPLES_DIR - так копятся мои настоящие произношения
    для обучения модели."""
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


_last_heard: tuple[np.ndarray, str] | None = None     # последний распознанный звук и его текст


def transcribe(audio: np.ndarray) -> str:
    """float32 16 кГц -> текст. Если VAD не нашёл речь - пустая строка.
    Тот же звук второй раз не распознаю. После досрочной проверки (record_utterance) фраза обычно так
    и кончается, и запись совпадает с проверенной до сэмпла, а Whisper с temperature=0 ответил бы то же самое.
    Так короткая фраза стоит одного прогона на видеокарте, а не двух."""
    global _last_heard
    if _whisper_model is None:
        return ""
    last = _last_heard
    if last is not None and len(last[0]) == len(audio) and np.array_equal(last[0], audio):
        return last[1]
    started = time.time()
    text = _transcribe(audio)
    _note_slow(time.time() - started, len(audio))
    _last_heard = (audio, text)
    return text


_slow_logged_at = 0.0


def _note_slow(took: float, samples: int) -> None:
    """Обычно фраза распознаётся за 0.2-0.5 с. Намного дольше - видеокарту, скорее всего, заняла игра.
    Пишу не чаще раза в 30 с, чтобы за вечер игры не забить лог."""
    global _slow_logged_at
    if took > SLOW_STT_SEC and time.time() - _slow_logged_at > 30:
        _slow_logged_at = time.time()
        log("STT", f"распознавание заняло {took:.1f} с на {samples / SAMPLE_RATE:.1f} с звука - видеокарта занята?")


def drop_stale(q: queue.Queue, keep_blocks: int) -> int:
    """Выбрасывает из очереди микрофона всё, кроме последних keep_blocks блоков. Сколько выбросил.
    Звук копится, пока Харви распознаёт и выполняет; если она не успевает, лучше пропустить старое,
    чем отвечать на то, что сказали полчаса назад."""
    dropped = 0
    while q.qsize() > keep_blocks:
        try:
            q.get_nowait()
        except queue.Empty:
            break
        dropped += 1
    return dropped


def too_long_to_wake(audio: np.ndarray, heard_name: bool) -> bool:
    """Во сне жду только «Харви, проснись» - фраза короткая. Длинный разговор рядом не распознаю
    (если модель имени в нём не услышала): в игре каждая лишняя фраза стоила видеокарте ~20 с."""
    return not heard_name and len(audio) > SLEEP_MAX_SEC * SAMPLE_RATE


def _transcribe(audio: np.ndarray) -> str:
    global _vad_skipped
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


LOOP_REPEATS = 20               # человек повторяет слово до ~14 раз («так, так, так…»), зациклившийся Whisper - 40-112


def looped(low: str) -> bool:
    """Whisper зациклился на шуме, смехе или крике и повторяет одно и то же до упора: «открой, слава, слава,
    слава…», «кхе-кхе-кхе…», «ооооо…». Из лога 9 октября: такая фраза с именем ушла в ИИ на 11 с."""
    words = re.findall(r"\w+", low)
    if words:
        top = Counter(words).most_common(1)[0][1]
        if top >= LOOP_REPEATS and top >= 0.6 * len(words):
            return True
    letters = [c for c in low if c.isalpha()]
    run = max((len(m.group()) for m in re.finditer(r"(\w)\1*", low)), default=0)
    return run >= LOOP_REPEATS and run >= 0.6 * len(letters)


def is_noise(low: str) -> bool:
    """Отсеивает галлюцинации Whisper на тишине, включая эхо собственного промпта, и зацикленные фразы."""
    if len(low) < 3 or any(h in low for h in HALLUCINATIONS) or looped(low):
        return True
    return difflib.SequenceMatcher(None, low, WHISPER_PROMPT.lower()).ratio() > 0.8


_WAKE_OK_FILE = BASE_DIR / ".wake_ok"    # версию модели уже проверял - не проверяю при каждом запуске


def _wake_data_file() -> Path | None:
    """Файл весов рядом с моделью (новый PyTorch сохраняет их отдельно: harvey.onnx.data)."""
    data = WAKE_MODEL.read_bytes()
    i = data.find(b"location\x12")                 # запись external_data: key="location", value=<имя файла>
    if i < 0:
        return None
    length = data[i + 9]
    return WAKE_MODEL.parent / data[i + 10:i + 10 + length].decode("utf-8", "replace")


def _check_wake_model() -> str | None:
    """Проверяю модель имени до загрузки в отдельном процессе: битая модель роняет onnxruntime
    вместе со всей программой, и это не исключение, его не поймать. None - всё в порядке."""
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
    """Маленькая сеть слушает имя на каждом блоке (~2 мс на 0.1 с). Whisper запускается только
    после имени, а разговоры рядом и телевизор не распознаются вовсе."""

    def __init__(self) -> None:
        self._model = None
        self._hit = False
        self._peak = 0.0             # наибольшая уверенность в имени с прошлой проверки - для подбора порога
        self.last_peak = 0.0         # то же для последней фразы (после take)
        self.last_hit = False        # услышала ли модель имя в последней фразе, даже если решает не она

    @property
    def enabled(self) -> bool:
        return self._model is not None

    @property
    def trusted(self) -> bool:
        """Решает ли модель, звучало ли имя. В режиме наблюдения (WAKE_GATE = False) она только пишет в лог:
        её промах не должен ни отбрасывать фразу, ни отключать досрочное распознавание («Харви, пауза»)."""
        return self.enabled and WAKE_GATE

    def load(self) -> None:
        if not WAKE_MODEL.exists():
            log("Wake", f"модели {WAKE_MODEL.name} нет - имя ищет Whisper (как раньше). "
                        f"Как обучить свою - см. папку {WAKE_MODEL.parent.name}")
            return
        problem = _check_wake_model()
        if problem:
            log("Wake", f"{problem} - имя ищет Whisper, как раньше")
            return
        try:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=[str(WAKE_MODEL)], inference_framework="onnx")
            # Дата файла - чтобы отчёт по логу считал каждую модель отдельно (log_report.wake_stats)
            stamp = time.strftime("%d.%m %H:%M", time.localtime(WAKE_MODEL.stat().st_mtime))
            log("Wake", f"слушаю имя моделью {WAKE_MODEL.name} от {stamp} (порог {WAKE_THRESHOLD})")
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
        """Решила ли модель, что с прошлой проверки звучало имя (флаг сбрасывается).
        В режиме наблюдения - никогда: что она услышала, видно только в логе и в last_hit.
        Раньше её «услышала» и тут делало фразу обращением: «Найс», «Да», «Ха?» и крики в игре
        уходили в ИИ, и Харви отвечала на чужой разговор (из лога 8 октября - 10 раз за вечер)."""
        hit, self._hit = self._hit, False
        if self._peak >= 0.1:              # похоже на имя - пишу в лог, чтобы подобрать порог
            log("Wake", f"уверенность в имени {self._peak:.2f} (порог {WAKE_THRESHOLD}) — {'услышала' if hit else 'мимо'}")
        self.last_peak, self._peak = self._peak, 0.0
        self.last_hit = hit
        return hit and self.trusted


_wake = WakeDetector()


def _get_block(audio_q: queue.Queue, timeout: float | None = None) -> np.ndarray:
    block = audio_q.get(timeout=timeout).flatten()
    _wake.feed(block)
    return block


def _strip_name_like(text: str) -> str:
    """Имя услышала сеть, а Whisper записал иначе ("Гарри, пауза") - убираю первое слово."""
    words = text.split(maxsplit=1)
    if words and difflib.SequenceMatcher(None, words[0].lower().strip(PUNCT), "харви").ratio() >= 0.5:
        return words[1] if len(words) > 1 else ""
    return text


def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block ** 2)))


def calibrate_silence(duration: float = 0.5) -> float:
    # Свой поток, а не sd.rec: тот закрывает общий поток sounddevice, в который может говорить напоминание
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        recording, _overflow = stream.read(int(duration * SAMPLE_RATE))
    return max(0.005, min(0.02, _rms(recording.flatten()) * 1.8))


def drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def record_utterance(audio_q: queue.Queue, pre_buffer: deque, first_block: np.ndarray,
                     threshold: float, early_check: Callable[[np.ndarray], bool] | None = None) -> np.ndarray | None:
    """Пишет фразу от первого громкого блока до паузы, лишнюю тишину в конце обрезает.
    early_check: после короткой паузы проверяю, не законченная ли это команда, и тогда не жду
    полную паузу ("пауза" срабатывает на ~0.5 с раньше)."""
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
