"""Вопросы к ИИ, выделенный текст, умная запись, «что на экране»: куда уходит фраза (без модели и клавиатуры)."""

import pytest

from core import commands, smart


def route(text: str):
    """("ask" | "screen" | "selection:<действие>" | None) — что Харви сделала бы с фразой."""
    action = smart.parse(text)
    if action is None:
        return None
    names = action.__code__.co_names
    if "ask_screen" in names:
        return "screen"
    if "on_selection" in names:
        return "selection:" + action.__defaults__[0]
    return "ask"


@pytest.mark.parametrize("phrase,expected", [
    # ── вопросы ──
    ("что такое квантовая запутанность", "ask"),
    ("кто такой Пушкин", "ask"),
    ("почему небо голубое", "ask"),
    ("объясни теорию относительности", "ask"),
    ("переведи на английский как дела", "ask"),
    ("как будет кошка по-английски", "ask"),
    ("подробнее", "ask"),
    # ── выделенный текст ──
    ("объясни выделенное", "selection:explain"),
    ("объясни это", "selection:explain"),
    ("что это значит", "selection:explain"),
    ("переведи выделенное", "selection:translate"),
    ("переведи на английский", "selection:translate"),
    ("переведи это на немецкий", "selection:translate"),
    ("перескажи выделенное", "selection:summary"),
    ("исправь ошибки в выделенном", "selection:fix"),
    ("исправь ошибки", "selection:fix"),
    ("исправь в деловом стиле", "selection:rewrite"),
    ("перепиши вежливее", "selection:rewrite"),
    ("сделай официальнее", "selection:rewrite"),
    ("сократи выделенное", "selection:rewrite"),
    # ── экран ──
    ("что тут написано", "screen"),
    ("что это за ошибка", "screen"),
    ("что на экране", "screen"),
    ("посмотри на экран и скажи как исправить", "screen"),
    # ── не наше ──
    ("что думаешь о жизни", None),
    ("проверь почту", None),
    ("сделай громче", None),
])
def test_route(phrase, expected):
    assert route(phrase) == expected


def test_commands_win_over_questions():
    """«Переведи компьютер в спящий режим» — команда, а не перевод: обычные команды проверяются раньше."""
    assert commands.parse_all("переведи компьютер в спящий режим") is not None


def test_screen_off_without_vision(monkeypatch):
    monkeypatch.setattr(smart, "VISION_ENABLED", False)
    assert route("что тут написано") is None


def test_dialog_accepts_questions():
    assert commands.dialog_accepts("что такое фотосинтез")
    assert commands.dialog_accepts("переведи выделенное")


@pytest.mark.parametrize("raw,clean", [
    ("Привет, мир.", "Привет, мир."),
    ("«Привет, мир.»", "Привет, мир."),
    ("Вот исправленный текст:\nПривет, мир.", "Привет, мир."),
    ("```\nПривет, мир.\n```", "Привет, мир."),
    ("«Да» и «нет»", "«Да» и «нет»"),            # кавычки внутри текста не трогаем
])
def test_clean_output(raw, clean):
    assert smart.clean_output(raw) == clean


@pytest.mark.parametrize("before,after,same", [
    ("привет как дела я завтра не приду", "Привет, как дела? Я завтра не приду.", True),
    ("ну я короче завтра не приду", "Я завтра не смогу прийти.", False),
    ("привет", "", False),
])
def test_same_words(before, after, same):
    assert smart.same_words(before, after) is same


def test_dictation_punctuation(monkeypatch):
    monkeypatch.setattr(smart, "edit", lambda instruction, text: "Привет, как дела?")
    assert smart.prepare_dictation("привет как дела") == "Привет, как дела?"


def test_dictation_keeps_words(monkeypatch):
    """Модель «улучшила» текст вместо расстановки знаков — пишем как услышали."""
    monkeypatch.setattr(smart, "edit", lambda instruction, text: "Здравствуйте, как поживаете?")
    assert smart.prepare_dictation("привет как дела") == "привет как дела"


def test_dictation_style(monkeypatch):
    seen = {}

    def fake_edit(instruction, text):
        seen.update(instruction=instruction, text=text)
        return "Уважаемый коллега, я задержусь."

    monkeypatch.setattr(smart, "edit", fake_edit)
    assert smart.prepare_dictation("вежливо, скажи что я опоздаю") == "Уважаемый коллега, я задержусь."
    assert "вежливее" in seen["instruction"] and seen["text"] == "скажи что я опоздаю"


def test_dictation_model_down(monkeypatch):
    def boom(instruction, text):
        raise ConnectionError("ollama не запущена")

    monkeypatch.setattr(smart, "edit", boom)
    assert smart.prepare_dictation("привет как дела") == "привет как дела"


@pytest.mark.parametrize("low,text,lang", [
    ("переведи выделенное", "Привет", "английский"),
    ("переведи выделенное", "Hello world", "русский"),
    ("переведи на немецкий", "Привет", "немецкий"),
])
def test_target_lang(low, text, lang):
    assert smart._target_lang(low, text) == lang


def test_long_text_gets_bigger_context():
    assert smart._ctx_for("коротко") is None
    assert smart._ctx_for("а" * 3000) >= 2500
