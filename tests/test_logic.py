"""Логика без микрофона: ответы, обращение, числа, напоминания, классификация плееров, целостность файлов."""

import re
from datetime import datetime
from pathlib import Path

import pytest

import harvey
import phrases
from core import commands, media, speech, util

ROOT = Path(harvey.__file__).resolve().parent
I, F, R = util.INFO, util.FAIL, util.RAW
SOURCES = ["harvey.py", "phrases.py", "config.py"] + [f"core/{p.name}" for p in (ROOT / "core").glob("*.py")]


# ── целостность: то, что уже ломалось ──

@pytest.mark.parametrize("name", SOURCES)
def test_no_control_chars_in_source(name):
    """Однажды «\\b» в регулярке превратился в невидимый символ Backspace — и фразы перестали работать."""
    data = (ROOT / name).read_bytes()
    assert b"\x08" not in data and b"\r[" not in data.replace(b"\r\n", b"")


def test_all_phrase_patterns_compile():
    for key, patterns in phrases.PHRASES.items():
        for pattern in patterns:
            re.compile(pattern)


def test_tools_match_functions():
    """Каждый инструмент, который видит ИИ, существует; каждая функция вызывается."""
    for tool in commands.TOOLS:
        assert tool["function"]["name"] in commands.FUNCTIONS
    for name, fn in commands.FUNCTIONS.items():
        assert callable(fn), name


# ── тихий режим и обращение ──

@pytest.mark.parametrize("phrases_in,expected", [
    (["открыла x"], (None, "done")),
    ([I + "сейчас 5 часов"], ("Сейчас 5 часов.", None)),
    ([F + "приложение «x» не найдено"], ("Приложение «x» не найдено.", "error")),
    (["a", I + "таймер на 5 минут"], ("Таймер на 5 минут.", None)),
    ([R + "Вы уверены, господин?"], ("Вы уверены, господин?", None)),
])
def test_compose_quiet(phrases_in, expected):
    assert util.compose_quiet(phrases_in) == expected


@pytest.mark.parametrize("text,expected", [
    ("Да, господин?", "Да?"),
    ("Господин, таймер на 5 минут сработал.", "Таймер на 5 минут сработал."),
    ("Вы уверены, господин, что нужно выключить?", "Вы уверены, что нужно выключить?"),
    ("Простите, господин, произошла ошибка.", "Простите, произошла ошибка."),
])
def test_address_without_honorific(monkeypatch, text, expected):
    monkeypatch.setattr(util, "USE_HONORIFIC", False)
    assert util.address(text) == expected


def test_address_custom_honorific(monkeypatch):
    monkeypatch.setattr(util, "HONORIFIC", "сэр")
    assert util.address("Господин, да, господин.") == "Сэр, да, сэр."


# ── числа ──

@pytest.mark.parametrize("text,expected", [
    ("громкость 50", 50), ("пятьдесят", 50), ("двадцать пять", 25), ("сто", 100), ("ничего", None),
])
def test_parse_number(text, expected):
    assert util.parse_number(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("шесть вечера", "6 вечера"),
    ("восемнадцать тридцать", "18 30"),
    ("двадцать пять минут", "25 минут"),
])
def test_numbers_to_digits(text, expected):
    assert commands._numbers_to_digits(text) == expected


def test_tts_normalization():
    assert util.normalize_for_tts("Открыла Claude") == "Открыла клод"
    assert util.normalize_for_tts("21 минута") == "двадцать одна минута"


# ── напоминания ──

def _reminder(calls, text):
    calls.clear()
    action = commands.parse_reminder(text)
    assert action is not None, text
    action()
    (name, args), = calls
    assert name == "add_reminder"
    return datetime.fromtimestamp(args["at"]), args["text"]


@pytest.mark.parametrize("text,hour,minute,message", [
    ("напомни в 18:00 позвонить маме", 18, 0, "позвонить маме"),
    ("напомни мне в 6 вечера, что надо купить хлеб", 18, 0, "надо купить хлеб"),
    ("в 18.30 напомни забрать посылку", 18, 30, "забрать посылку"),
    ("напомни в восемнадцать тридцать полить цветы", 18, 30, "полить цветы"),
    ("разбуди в 7 утра", 7, 0, "пора вставать"),
    ("напомни в полдень пообедать и выпить таблетку", 12, 0, "пообедать и выпить таблетку"),
])
def test_reminder_at_time(calls, text, hour, minute, message):
    at, msg = _reminder(calls, text)
    assert (at.hour, at.minute, msg) == (hour, minute, message)
    assert at > datetime.now()                    # прошедшее время уходит на завтра


def test_reminder_tomorrow(calls):
    at, msg = _reminder(calls, "напомни завтра в 9 утра про встречу")
    assert (at.date() - datetime.now().date()).days == 1 and at.hour == 9


def test_reminder_in_minutes(calls):
    at, msg = _reminder(calls, "напомни через 20 минут выключить плиту")
    assert abs((at - datetime.now()).total_seconds() - 1200) < 5 and msg == "выключить плиту"


def test_reminder_without_time_asks_when(calls):
    result = commands.parse_reminder("напомни позвонить маме")()
    assert result.startswith(I) and "когда" in result and not calls


# ── диалог ──

@pytest.mark.parametrize("text,accepted", [
    ("Пауза.", True), ("Следующий трек", True), ("Запиши привет", True),
    ("ну мы вчера ходили в кино", False),
])
def test_dialog_accepts(text, accepted):
    assert commands.dialog_accepts(text) is accepted


@pytest.mark.parametrize("text,ends", [("всё спасибо", True), ("спасибо", True), ("пауза", False)])
def test_dialog_end(text, ends):
    assert bool(util.DIALOG_END_RE.match(text)) is ends


def test_llm_stream_sentence_split(monkeypatch):
    said = []
    monkeypatch.setattr(speech, "speak", said.append)
    pieces = ["Столица Франции ", "— Париж. Это ", "красивый город! А ", "ещё там Лувр"]
    assert speech.speak_stream(pieces) == "Столица Франции — Париж. Это красивый город! А ещё там Лувр"
    assert said == ["Столица Франции — Париж.", "Это красивый город!", "А ещё там Лувр"]


# ── чей это плеер: музыка / YouTube / видео ──

FIREFOX = "308046B0AF4A39CB"


def _kinds(monkeypatch, info, titles, browser_titles=None):
    monkeypatch.setattr(media, "_kind_memory", {})
    monkeypatch.setattr(media, "_window_titles",
                        lambda browsers_only=False: (browser_titles if browser_titles is not None else titles)
                        if browsers_only else titles)
    return media._session_kinds(info, titles)


def test_kinds_youtube_active_tab(monkeypatch):
    video = {"app": FIREFOX, "title": "Я Выжил 7 Дней В Арктике", "artist": "MrBeast", "album": ""}
    assert _kinds(monkeypatch, video, ["Я Выжил 7 Дней В Арктике - YouTube — Mozilla Firefox"]) == {"youtube", "video"}


def test_kinds_yandex_web_without_title(monkeypatch):
    track = {"app": FIREFOX, "title": "still save a seat for you", "artist": "otuka", "album": ""}
    title = "Яндекс Музыка — собираем музыку и подкасты для вас — Mozilla Firefox"
    assert _kinds(monkeypatch, track, [title]) == {"music"}


def test_kinds_yandex_app_window_does_not_make_video_music(monkeypatch):
    """Окно ПРИЛОЖЕНИЯ «Яндекс Музыка» — не вкладка браузера: ролик из браузера остаётся видео."""
    video = {"app": FIREFOX, "title": "Какой-то ролик", "artist": "Канал", "album": ""}
    assert _kinds(monkeypatch, video, ["Яндекс Музыка"], browser_titles=["Colab — Mozilla Firefox"]) == {"video", "youtube"}


def test_kinds_music_app(monkeypatch):
    track = {"app": "ru.yandex.desktop.music", "title": "Встреча", "artist": "x", "album": ""}
    assert _kinds(monkeypatch, track, []) == {"music"}


# ── ошибки слуха и досрочное распознавание ──

@pytest.mark.parametrize("heard,fixed", [
    ("напомнив 6 вечера позвонить маме", "напомни в 6 вечера позвонить маме"),
    ("напомнив, в шесть вечера", "напомни в шесть вечера"),
    ("откройте telegram", "открой telegram"),
    ("youtube stop", "youtube стоп"),
    ("ютуб штоп", "ютуб стоп"),
    ("следующий трик", "следующий трек"),
])
def test_fix_hearing(heard, fixed):
    assert commands.fix_hearing(heard) == fixed


@pytest.mark.parametrize("text,quick", [
    ("Харви, пауза.", True),
    ("Харви, громкость 30.", True),
    ("Харви, музыку тише.", True),
    ("Харви, ютуб стоп.", True),
    ("Харви, следующий трек.", True),
    ("Харви, сколько времени?", True),
    ("Харви, спать.", True),
    ("Харви, громкость", False),                    # число ещё не сказано
    ("Харви, найди рецепт", False),                 # у поиска бывает продолжение
    ("Харви, напомни в 6 вечера", False),
    ("Харви, открой телеграм", False),
    ("Харви, запиши привет", False),
    ("Харви, громкость 30 и", False),               # явно продолжение
    ("Харви.", False),                              # одно имя — ждём команду как обычно
    ("пауза", False),                               # без имени — только в диалоге
])
def test_is_quick_command(text, quick):
    assert commands.is_quick_command(text) is quick


def test_quick_without_name_in_dialog_and_yes_when_pending():
    assert commands.is_quick_command("пауза", need_name=False)
    assert commands.is_quick_command("да", need_name=False, pending=True)
    assert not commands.is_quick_command("да", need_name=False, pending=False)


def test_record_utterance_returns_early():
    """Короткая команда: запись заканчивается после EARLY_SILENCE, а не после SILENCE_DURATION."""
    import queue
    from collections import deque

    import numpy as np

    from core import stt
    loud = np.full((stt.BLOCK_SIZE, 1), 0.3, dtype=np.float32)
    quiet = np.zeros((stt.BLOCK_SIZE, 1), dtype=np.float32)
    q = queue.Queue()
    for block in [loud] * 8 + [quiet] * 30:
        q.put(block)
    checked = []
    audio = stt.record_utterance(q, deque(), loud.flatten(), 0.05, lambda a: checked.append(len(a)) or True)
    used = 38 - q.qsize()
    assert checked and used == 8 + stt.EARLY_SILENCE_BLOCKS        # не ждали полную паузу
    assert audio is not None and len(audio) == checked[0]

    q2 = queue.Queue()
    for block in [loud] * 8 + [quiet] * 30:
        q2.put(block)
    stt.record_utterance(q2, deque(), loud.flatten(), 0.05, lambda a: False)
    assert 38 - q2.qsize() == 8 + stt.SILENCE_BLOCKS                # команда не законченная — ждём как раньше


def test_sleep_reply_is_just_ok():
    from core import daily
    try:
        reply = daily.sleep_mode()
        assert util.compose_quiet([reply]) == ("Хорошо.", None)      # тихий режим
        assert util.compose([reply]) == "Хорошо."                      # обычный режим
    finally:
        daily.set_sleeping(False)
