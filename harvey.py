"""Харви - локальная голосовая помощница для Windows.

Цепочка: микрофон -> имя -> faster-whisper -> правила из phrases.py -> (если не понял) Ollama -> Silero / Piper.
Простые команды разбираю регулярками без ИИ, так они срабатывают за миллисекунды. В Ollama
уходит только то, что правила не поняли, и ровно один запрос."""

from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # до импорта torch и ctranslate2

import faulthandler
import logging
import numpy as np
import os
import queue
import re
import sounddevice as sd
import sys
import threading
import time
from collections import deque

from config import (
    ASSISTANT_NAME,
    BASE_DIR,
    BLOCK_SIZE,
    DIALOG_MODE,
    DIALOG_TIMEOUT,
    EARLY_SILENCE,
    ECHO_THRESHOLD_FACTOR,
    FEMALE_VOICE,
    LOG_MAX_MB,
    QUIET_MODE,
    SAMPLE_RATE,
    WAKE_BEEP,
    WAKE_GATE,
    WAKE_THRESHOLD,
)
from core.util import (  # noqa: F401
    DIALOG_END_RE,
    END,
    PUNCT,
    R,
    SILENCE_RE,
    STOP_RE,
    WAKE_PATTERN,
    WOKE,
    log,
    setup_logging,
)
from core.audio import (  # noqa: F401
    _ducker,
)
from core.speech import (  # noqa: F401
    _chimes,
    _ensure_tts,
    _play_worker,
    _prewarm_tts,
    _speaking,
    _synth_worker,
    play_beep,
    play_sound,
    speak,
    speak_sync,
    stop_speaking,
    wait_silence,
)
from core.apps import (  # noqa: F401
    _load_start_apps,
)
from core.daily import (  # noqa: F401
    clear_pending,
    set_sleeping,
    start_reminders,
)
from core.stt import (  # noqa: F401
    collect_name_sample,
    PRE_BUFFER_BLOCKS,
    _get_block,
    _load_whisper,
    _rms,
    _strip_name_like,
    _wake,
    _whisper_ready,
    calibrate_silence,
    drain,
    is_noise,
    record_command,
    record_utterance,
    transcribe,
)
from core.tray import (  # noqa: F401
    _quit_event,
    _single_instance,
    start_tray,
)
from core.commands import (  # noqa: F401
    _reply,
    _say,
    _warmup_llm,
    dialog_accepts,
    handle_command,
    unload_model,
)
from core.parse import is_quick_command
import core.daily as daily
import core.stt as stt
import core.system as system
import core.tray as tray

def main() -> None:
    if sys.platform != "win32":
        print("Скрипт рассчитан на Windows.")
        return
    if sys.stdout is None or sys.stderr is None:       # запуск без консоли (pythonw, ярлык, автозапуск)
        sys.stdout = sys.stdout or open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stderr or open(os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging()
    try:      # падение в C-коде (ctypes, CUDA) в лог не попадает, так хотя бы будет стек
        crash_log = BASE_DIR / "crash.log"
        if crash_log.exists() and crash_log.stat().st_size > LOG_MAX_MB * 1024 * 1024:
            crash_log.replace(crash_log.with_name("crash.log.1"))     # храним одну прошлую копию
        faulthandler.enable(open(crash_log, "a", encoding="utf-8"), all_threads=True)
    except Exception:
        pass
    if not _single_instance():
        log("Система", f"{ASSISTANT_NAME} уже запущен{'а' if FEMALE_VOICE else ''} - второй экземпляр не нужен.")
        return

    _ducker.recover()                        # если в прошлый раз музыка осталась тихой - возвращаю
    threading.Thread(target=_synth_worker, daemon=True).start()
    threading.Thread(target=_play_worker, daemon=True).start()
    threading.Thread(target=_load_start_apps, daemon=True).start()
    threading.Thread(target=_load_whisper, daemon=True).start()
    threading.Thread(target=_warmup_llm, daemon=True).start()

    try:
        _ensure_tts()
        threading.Thread(target=_prewarm_tts, daemon=True).start()
    except Exception as e:
        log("TTS", f"Не удалось подготовить голос: {e}")

    _wake.load()
    start_reminders()
    if system.mic_muted():                   # проверяю в главном потоке: трей к микрофону не обращается
        log("Система", "Микрофон выключен - включите его в меню значка у часов.")
    start_tray()

    log("Система", "Жду загрузки Whisper...")
    _whisper_ready.wait()
    if stt._whisper_model is None:
        log("STT", "Whisper не загрузился ни на видеокарте, ни на процессоре - выхожу.")
        return

    log("Система", "Калибрую микрофон...")
    threshold = calibrate_silence()
    log("Система", f"Порог чувствительности микрофона: {threshold:.4f}")

    if QUIET_MODE:
        play_sound("ready")
        wait_silence()
    else:
        speak_sync("Система запущена. Ожидаю вашей команды, господин.")
    print(f"\n[ Режим постоянного слушания (Wake word: «{ASSISTANT_NAME}») ]")

    audio_q: queue.Queue = queue.Queue()
    pre_buffer: deque = deque(maxlen=PRE_BUFFER_BLOCKS)
    ducker = _ducker
    dialog_until = 0.0                       # до какого момента слушаем без «Харви» (0 - окно закрыто)

    def audio_callback(indata, frames, time_info, status):
        audio_q.put(indata.copy())          # слушаю всегда, чтобы Харви можно было перебить

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=BLOCK_SIZE, callback=audio_callback):
            while not _quit_event.is_set():
                # Возвращаю громкость музыки, как только Харви договорила
                if ducker.active and (not _speaking.is_set() or ducker.stale):
                    ducker.restore()
                _chimes.close_if_idle()
                if system.mic_on_requested.is_set():     # «Включить микрофон» в меню трея
                    system.mic_on_requested.clear()
                    try:
                        log("Система", system.microphone(True))
                        play_sound("ready")
                    except Exception as e:
                        log("Система", f"не удалось включить микрофон: {e}")
                # Окно диалога отсчитываю с момента, когда Харви замолчала
                if dialog_until:
                    if _speaking.is_set():
                        dialog_until = max(dialog_until, time.time() + DIALOG_TIMEOUT)
                    elif time.time() > dialog_until:
                        dialog_until = 0.0
                        log("Диалог", "окно закрыто")
                try:
                    block = _get_block(audio_q, timeout=0.1)
                except queue.Empty:
                    continue

                speaking = _speaking.is_set()
                limit = threshold * ECHO_THRESHOLD_FACTOR if speaking else threshold
                if _rms(block) <= limit:
                    pre_buffer.append(block)
                    continue

                # Без имени слушаю, только пока Харви молчит, иначе она услышит сама себя
                dialog_open = bool(dialog_until) and not speaking
                _chimes.warm()                       # пока человек говорит, готовлю звук готово, чтобы он прозвучал без задержки
                early: dict[str, str] = {}

                def early_check(candidate: np.ndarray) -> bool:
                    # Короткая пауза и законченная команда ("пауза", "громкость 30") - дальше не жду
                    if daily._sleeping or (_wake.enabled and not _wake.peek() and not dialog_open):
                        return False
                    guess = transcribe(candidate)
                    if guess and is_quick_command(guess, need_name=not dialog_open,
                                                  pending=daily._pending is not None):
                        early["text"] = guess
                        return True
                    return False

                audio = record_utterance(audio_q, pre_buffer, block, limit, early_check if EARLY_SILENCE else None)
                pre_buffer.clear()
                heard_name = _wake.take()
                if audio is None:
                    continue
                # С детектором имени Whisper нужен только в диалоге, при да/нет
                # и чтобы услышать "стоп", пока Харви говорит
                if _wake.enabled and WAKE_GATE and not heard_name and not (
                        (dialog_open or daily._pending is not None or speaking) and not daily._sleeping):
                    continue

                heard_at = time.time()               # фраза закончилась - отсюда считаем задержку
                text = early.get("text") or transcribe(audio)
                stt_sec = time.time() - heard_at
                low = text.lower()
                if not low or is_noise(low):
                    continue
                log("Распознано", text + (" (досрочно)" if early else ""))

                m = WAKE_PATTERN.search(low)
                named = m is not None or heard_name
                if m and m.start() <= 2:                     # сохраняю само имя для обучения модели
                    collect_name_sample(audio)
                if m and _wake.enabled and not heard_name:   # Whisper слышит имя, а модель нет - пишу в лог
                    log("Wake", f"модель не узнала имя (уверенность {_wake.last_peak:.2f}, порог {WAKE_THRESHOLD})")
                if m:
                    after_name = text[m.end():]
                elif heard_name:                    # имя услышала нейросеть, Whisper записал его иначе
                    after_name = _strip_name_like(text)
                else:
                    after_name = text
                body = " ".join(re.sub(r"[^\w\s]", " ", after_name.lower()).split())

                # Режим сна: слушаю только "Харви, проснись"
                if daily._sleeping:
                    dialog_until = 0.0
                    if named and R["wake_up"].search(low):
                        set_sleeping(False)
                        drain(audio_q)
                        if QUIET_MODE:
                            play_sound("ready")
                        else:
                            play_beep()
                            _say(f"Я {WOKE}, господин.")
                    continue

                # "стоп", "хватит" - замолкает сразу, имя не нужно
                if (speaking or _speaking.is_set()) and (SILENCE_RE.match(body) or STOP_RE.match(body)):
                    stop_speaking()
                    log("Система", "Речь прервана.")
                    continue

                # Ответ на "Вы уверены?" - без имени
                if daily._pending is not None:
                    if time.time() > daily._pending["deadline"]:
                        clear_pending()
                    elif R["yes"].search(body):
                        action = daily._pending["action"]
                        clear_pending()
                        stop_speaking()
                        _reply([action()])
                        continue
                    elif R["no"].search(body):
                        clear_pending()
                        if QUIET_MODE:
                            play_sound("cancel")
                        else:
                            _say("Хорошо, господин, отменяю.")
                        continue

                # "всё", "спасибо" - закрыть окно диалога
                if (named or dialog_open) and DIALOG_END_RE.match(body):
                    if dialog_until:
                        dialog_until = 0.0
                        play_sound("cancel")
                    continue

                if named:
                    if SILENCE_RE.match(body):
                        continue
                    command = after_name.lstrip(PUNCT)
                elif dialog_open and dialog_accepts(text):
                    command = text.strip().lstrip(PUNCT)    # режим диалога: имя можно не называть
                    log("Диалог", command)
                else:
                    continue

                if speaking or _speaking.is_set():
                    stop_speaking()                  # новая команда важнее: не ждём конца прошлой фразы

                if not command:
                    ducker.duck()                    # приглушаю музыку, пока человек говорит команду
                    if WAKE_BEEP:
                        play_beep()                  # сигнал «слушаю» вместо фразы - реакция мгновенная
                    else:
                        speak_sync("Да, господин?")
                    drain(audio_q)                   # выбрасываю эхо своего сигнала
                    cmd_audio = record_command(audio_q, threshold)
                    heard_at = time.time()
                    command = transcribe(cmd_audio).strip() if cmd_audio is not None else ""
                    stt_sec = time.time() - heard_at
                    log("Команда", command)

                if not command:
                    if QUIET_MODE:
                        play_sound("cancel")
                    else:
                        speak(f"Я ничего не услышал{END}, господин.")
                    continue
                started = time.time()
                if not handle_command(command):
                    break
                # Для отчёта: где теряется время - в распознавании или в ИИ
                log("Время", f"распознавание {stt_sec:.2f} с, команда {time.time() - started:.2f} с")
                if DIALOG_MODE and not daily._sleeping:
                    dialog_until = time.time() + DIALOG_TIMEOUT
    finally:
        ducker.restore()
        unload_model()
        if tray._tray_icon is not None:
            try:
                tray._tray_icon.stop()
            except Exception:
                pass

if __name__ == "__main__":
    main()
    # Обычный выход при выгрузке CUDA падает с 0xc0000409. Всё нужное уже сделано в main(),
    # поэтому выхожу сразу через os._exit.
    logging.shutdown()
    sys.stdout.flush()
    os._exit(0)
