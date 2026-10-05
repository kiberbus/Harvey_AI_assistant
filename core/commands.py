"""Обработка команды: заметки и диктовка, правила без ИИ (core/parse.py), вопросы (core/smart.py),
иначе — один запрос к Ollama с инструментами (core/tools.py)."""

from __future__ import annotations

import json
import ollama
import time
from collections import deque
from typing import Callable

from config import (
    ASSISTANT_NAME,
    DIALOG_LOCAL_ONLY,
    KEEP_ALIVE,
    LLM_STREAM,
    MEMORY_TTL,
    MEMORY_TURNS,
    MODEL,
    NUM_CTX,
    QUIET_MODE,
)
from core.util import (  # noqa: F401
    DICTATE_RE,
    END,
    FAIL,
    INFO,
    LAST_ACTION_RE,
    NOTE_ADD_RE,
    PUNCT,
    R,
    RAW,
    REPEAT_RE,
    SILENCE_RE,
    compose,
    compose_quiet,
    log,
)
from core.speech import (  # noqa: F401
    play_sound,
    speak,
    speak_stream,
    speak_sync,
    stop_speaking,
)
from core.daily import (  # noqa: F401
    clear_pending,
)
from core.parse import (
    fix_hearing,
    parse_all,
)
from core.tools import (
    TOOLS,
    execute_tool_dictate,
)
from core import llm, smart, tools


SYSTEM_PROMPT = f"""Ты — голосовой ассистент по имени {ASSISTANT_NAME}, управляющий компьютером с Windows 11.
Выполняй просьбы пользователя ТОЛЬКО через инструменты. {smart.ADDRESS_RULE}
- Если нужно несколько действий — вызови все инструменты сразу.
- YouTube-поиск: open_browser(site="youtube", query="...").
- Громкость в процентах — set_volume, "громче/тише" — change_volume.
- Пауза, продолжить, следующий или предыдущий трек — media. Если сказано, что именно (музыка, ютуб, видео), передай target.
- Яркость экрана — set_brightness / change_brightness.
- Закрыть приложение — close_app. Свернуть приложение — minimize_app (НЕ закрывай, если просят свернуть).
- Слова «его», «это», «то же» относятся к последнему упомянутому в диалоге.
- Название приложения передавай на английском, как в меню Пуск.
- Если инструмент не нужен, ответь одним-двумя короткими предложениями по-русски, без списков и разметки.
"""


# ───────────────────────── АГЕНТ (OLLAMA) ─────────────────────────
def _run_tools(tool_calls, user_text: str) -> None:
    phrases: list[str] = []
    seen: set[str] = set()
    for call in tool_calls:
        args = dict(call.function.arguments or {})
        key = call.function.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        log("Инструмент", f"{call.function.name}({args})")
        phrases.append(tools.execute_tool(call.function.name, args))
    _reply(phrases, user_text)


def run_llm(user_text: str) -> None:
    """Один запрос к модели. Инструменты выполняем и отвечаем сами; обычный текст
    озвучиваем по предложениям прямо во время генерации (LLM_STREAM)."""
    global _last_reply
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(),
        {"role": "user", "content": user_text},
    ]
    not_understood = f"{FAIL}не понял{END} команду"

    if not LLM_STREAM:
        msg = llm.chat(messages, tools=TOOLS).message
        if msg.tool_calls:
            _run_tools(msg.tool_calls, user_text)
        elif (msg.content or "").strip():
            _say(msg.content.strip(), user_text)
        else:
            _reply([not_understood], user_text)
        return

    tool_calls: list = []

    def texts():
        for chunk in llm.chat_stream(messages, tools=TOOLS):
            msg = chunk.message
            if msg.tool_calls:
                tool_calls.extend(msg.tool_calls)
            if not tool_calls and msg.content:
                yield msg.content

    spoken = speak_stream(texts())
    if tool_calls:
        _run_tools(tool_calls, user_text)
        return
    if not spoken:
        _reply([not_understood], user_text)
        return
    _last_reply = spoken
    _remember(user_text, _last_reply)


def _warmup_llm(wait_sec: float = 180.0) -> None:
    """Загружаем модель в VRAM заранее, чтобы первая команда не ждала холодного старта.
    При входе в Windows Харви и Ollama стартуют одновременно — ждём, пока Ollama поднимется."""
    deadline = time.time() + wait_sec
    waited = False
    while True:
        try:
            ollama.chat(model=MODEL, messages=[{"role": "user", "content": "ок"}],
                        options={"num_predict": 1, "num_ctx": NUM_CTX}, keep_alive=KEEP_ALIVE)
            log("LLM", "модель прогрета." + (" (дождалась запуска Ollama)" if waited else ""))
            return
        except ollama.ResponseError as e:          # Ollama работает, но ответил ошибкой (нет модели и т.п.)
            log("LLM", f"не удалось прогреть модель: {e}")
            return
        except Exception as e:                     # Ollama ещё не запущен — подождём
            if time.time() > deadline:
                log("LLM", f"Ollama так и не ответил за {wait_sec:.0f} с: {e}")
                return
            if not waited:
                log("LLM", "Ollama ещё не запущен — жду...")
                waited = True
            time.sleep(3)


def unload_model() -> None:
    try:
        ollama.generate(model=MODEL, keep_alive=0)
        log("Система", "Модель выгружена из видеокарты.")
    except Exception:
        pass


# ───────────────────────── ОБРАБОТКА КОМАНД ─────────────────────────
_last_reply = ""
_history: deque = deque(maxlen=MEMORY_TURNS)       # (время, команда, ответ) — контекст для ИИ


def _remember(user: str, reply: str) -> None:
    _history.append((time.time(), user, reply))


def _history_messages() -> list[dict]:
    """Последние реплики, чтобы ИИ понимал «закрой его», «а теперь в гугле»."""
    now = time.time()
    messages: list[dict] = []
    for stamp, user, reply in _history:
        if now - stamp <= MEMORY_TTL:
            messages += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
    return messages


def _last_action() -> str:
    """Что было сделано по последней команде: «На «закрой телеграм»: закрыла Telegram.»"""
    if not _history:
        return f"Я пока ничего не делал{END}, господин."
    _, user, reply = _history[-1]
    return f"На «{user}»: {reply.rstrip('.')}."


def _say(text: str, user: str | None = None) -> None:
    global _last_reply
    _last_reply = text
    if user:
        _remember(user, text)
    speak(text)


def _reply(phrases: list[str], user: str | None = None) -> None:
    """Отвечает на выполненную команду. В тихом режиме — звук «готово»/«ошибка»
    и вслух только то, что нужно услышать (время, погода, ошибка, вопрос)."""
    if user:   # ИИ помнит, что именно было сделано, — так он понимает «закрой его»
        _remember(user, ", ".join(p.lstrip(FAIL + INFO + RAW) for p in phrases) or "готово")
    if not QUIET_MODE:
        _say(compose(phrases))
        return
    text, sound = compose_quiet(phrases)
    if sound:
        play_sound(sound)
    if text:
        _say(text)


def dialog_accepts(command: str) -> bool:
    """Фраза без «Харви» в окне диалога: берём её, только если это явно команда."""
    stripped = command.strip().lstrip(PUNCT)
    low = stripped.lower().strip(PUNCT)
    if not low:
        return False
    if DICTATE_RE.match(stripped) or any(p.match(stripped) for p in NOTE_ADD_RE) or REPEAT_RE.match(low):
        return True
    if smart.parse(fix_hearing(low)) is not None:   # вопрос, «переведи выделенное», «что на экране»
        return True
    return not DIALOG_LOCAL_ONLY or parse_all(low) is not None


def handle_command(command: str) -> bool:
    """Выполняет команду. Речь идёт в фоне, поэтому следующую команду можно давать сразу.
    Возвращает False, если нужно завершить работу."""
    command = command.strip()
    low = command.lower().strip(PUNCT)
    clear_pending()                              # новая команда отменяет ожидание «да/нет»
    stripped = command.lstrip(PUNCT)

    # Заметки и диктовку проверяем первыми: в тексте может быть любое слово, даже «выход»
    for pattern in NOTE_ADD_RE:
        m = pattern.match(stripped)
        if m:
            _reply([tools.execute_tool("add_note", {"text": m.group("text")})], low)
            return True
    m = DICTATE_RE.match(stripped)
    if m:
        _reply([execute_tool_dictate(smart.prepare_dictation(m.group(1)))], low)
        return True

    if R["exit"].search(low):
        speak_sync("Отключаюсь." if QUIET_MODE else "Слушаюсь, господин. Я отключаюсь.")
        return False

    if SILENCE_RE.match(low):                  # «замолчи» после одиночного «Харви» — просто молчим
        stop_speaking()
        return True

    if REPEAT_RE.match(low):
        speak(_last_reply or "Мне пока нечего повторять, господин.")
        return True

    if LAST_ACTION_RE.match(low):              # «что ты сделала?» — последнее действие словами
        speak(_last_action())
        return True

    if R["cancel_command"].search(low):        # «теле… ничего не закрывай», «забудь» — передумали
        log("Отмена", low)
        play_sound("cancel")
        return True

    # Всё, что можно разобрать правилами (включая цепочки «тише и пауза»), выполняем без ИИ
    actions = parse_all(low)
    if actions:
        log("Без ИИ", low)
        _reply([a() for a in actions], low)
        return True

    # Вопросы, выделенный текст, «что на экране» — к ИИ без инструментов
    smart_action = smart.parse(fix_hearing(low))
    if smart_action:
        log("ИИ: текст", low)
        run_smart(smart_action, low)
        return True

    # Всё остальное — в Ollama (в лог попадает, чтобы потом добавить фразу в phrases.py)
    log("К ИИ", low)
    if not QUIET_MODE:
        speak("Секунду, господин.")
    try:
        run_llm(low)
    except Exception as e:
        log("Ошибка", str(e))
        _reply([f"{FAIL}произошла ошибка"])
    return True


def run_smart(action: Callable[[list[dict]], smart.Result], low: str) -> None:
    global _last_reply
    try:
        phrase, said = action(_history_messages())
    except Exception as e:
        log("Ошибка", f"ИИ: {e}")
        phrase, said = f"{FAIL}произошла ошибка", ""
    if phrase is not None:
        _reply([phrase], low)
    else:                                         # ответ уже прозвучал по ходу генерации
        _last_reply = said
        _remember(low, said)
