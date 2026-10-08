"""Вопросы к ИИ, выделенный текст, умная запись, «что на экране»: куда уходит фраза (без модели и клавиатуры)."""

import pytest

from core import commands, parse, smart


def route(text: str):
    """("ask" | "screen" | "selection:<действие>" | None) - что Харви сделала бы с фразой."""
    action = smart.parse(text)
    if action is None:
        return None
    names = action.__code__.co_names
    if "ask_screen" in names:
        return "screen"
    if "translate_aloud" in names:
        return "translate"
    if "on_selection" in names:
        return "selection:" + action.__defaults__[0]
    if "wiki" in names:
        return "wiki"
    return "ask"


@pytest.mark.parametrize("phrase,expected", [
    # вопросы
    ("что такое квантовая запутанность", "wiki"),
    ("кто такой пушкин", "wiki"),
    ("а кто такая анна ахматова", "wiki"),
    ("что такое с тобой", "ask"),               # не предмет статьи - сразу к модели
    ("что такое это", "ask"),
    ("почему небо голубое", "ask"),
    ("объясни теорию относительности", "ask"),
    ("переведи на английский как дела", "translate"),
    ("как будет кошка по-английски", "translate"),
    ("подробнее", "ask"),
    ("как дела", "ask"),
    ("алё", "ask"),
    ("что ты умеешь", "ask"),
    ("сделаю вежливым", None),                  # без исправления слуха - не наше…
    ("сделай текст более вежливым", "selection:rewrite"),
    # выделенный текст
    ("объясни выделенное", "selection:explain"),
    ("объясни это", "selection:explain"),
    ("что это значит", "selection:explain"),
    ("переведи выделенное", "selection:translate"),
    ("переведи на английский", "selection:translate"),
    ("переведи это на немецкий", "selection:translate"),
    ("переведи на английский и вставь", "selection:translate"),
    ("переведи на английский и ставь текст", "selection:translate"),     # так Whisper слышит «вставь»
    ("переведи фразу на русский", "selection:translate"),        # из лога 9 октября: переводило слово «фразу»
    ("как переводится выделенная фраза", "selection:translate"), # из лога: уходило в ИИ без выделенного текста
    ("как переводится это", "selection:translate"),
    ("переведи эту фразу", "selection:translate"),
    ("переведи слово кошка на немецкий", "translate"),          # своё слово - перевожу его, а не выделенное
    ("как перевести кошку на английский", "translate"),
    ("перескажи выделенное", "selection:summary"),
    ("исправь ошибки в выделенном", "selection:fix"),
    ("исправь ошибки", "selection:fix"),
    ("исправь в деловом стиле", "selection:rewrite"),
    ("перепиши вежливее", "selection:rewrite"),
    ("сделай официальнее", "selection:rewrite"),
    ("сократи выделенное", "selection:rewrite"),
    # экран
    ("что тут написано", "screen"),
    ("что это за ошибка", "screen"),
    ("что на экране", "screen"),
    ("что видишь на экране", "screen"),
    ("что видишь", "screen"),
    ("что ты видишь", "screen"),
    ("что у меня на экране", "screen"),
    ("посмотри на экран и скажи как исправить", "screen"),
    # не наше
    ("что думаешь о жизни", None),
    ("проверь почту", None),
    ("сделай громче", None),
])
def test_route(phrase, expected):
    assert route(phrase) == expected


def test_spelling_mishearing():
    assert route(parse.fix_hearing("исправь архографические ошибки")) == "selection:fix"     # из лога


def test_commands_win_over_questions():
    """«Переведи компьютер в спящий режим» - команда, а не перевод: обычные команды проверяются раньше."""
    assert parse.parse_all("переведи компьютер в спящий режим") is not None


@pytest.mark.parametrize("phrase,expected", [
    ("как по-английски «добрый вечер»", ("добрый вечер", "английский")),
    ("как по английски добрый вечер", ("добрый вечер", "английский")),     # Whisper без дефиса
    ("как будет по-английски спасибо", ("спасибо", "английский")),
    ("как по-английски будет кошка", ("кошка", "английский")),
    ("как сказать я тебя люблю по-английски", ("я тебя люблю", "английский")),
    ("а как по-немецки спасибо", ("спасибо", "немецкий")),
    ("переведи спасибо на французский", ("спасибо", "французский")),
    ("переведи спасибо на немецкий", ("спасибо", "немецкий")),         # «-цкий» раньше не узнавался
    ("как будет кошка на турецком", ("кошка", "турецкий")),
    ("скажи по-английски доброе утро", ("доброе утро", "английский")),
    ("как по-русски thank you", ("thank you", "русский")),
    ("как по-человечески сказать что я занят", None),
    ("переведи на английский", None),
])
def test_translate_request(phrase, expected):
    assert smart.translate_request(phrase) == expected


def test_translate_aloud_uses_language_voice(monkeypatch):
    """Перевод звучит голосом своего языка, а в контекст попадает сам перевод."""
    said = []
    monkeypatch.setattr(smart, "warm_foreign", lambda lang: None)
    monkeypatch.setattr(smart, "edit", lambda instruction, text: "«Good evening.»")
    monkeypatch.setattr(smart, "speak_foreign", lambda text, lang: said.append((text, lang)))
    assert smart.translate_aloud("добрый вечер", "английский") == (None, "Good evening.")
    assert said == [("Good evening.", "английский")]


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
    """Модель «улучшила» текст вместо расстановки знаков - пишем как услышали."""
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


def test_dictation_style_after_period(monkeypatch):
    """Whisper пишет «запиши вежливо. Привет…» - слово «вежливо» не должно попасть в текст."""
    seen = {}
    monkeypatch.setattr(smart, "edit", lambda instruction, text: seen.update(instruction=instruction, text=text)
                        or "Здравствуйте! Как ваши дела?")
    assert smart.prepare_dictation("вежливо. Привет, как твои дела?") == "Здравствуйте! Как ваши дела?"
    assert "вежливее" in seen["instruction"] and seen["text"] == "Привет, как твои дела?"


def test_translation_replaces_selection(monkeypatch):
    """Перевод встаёт вместо выделенного и вслух не читается."""
    pasted, spoken = [], []
    monkeypatch.setattr(smart, "foreground_is_mine", lambda: False)
    monkeypatch.setattr(smart, "copy_selection", lambda: "Привет, мир")
    monkeypatch.setattr(smart, "edit", lambda instruction, text: "Hello, world")
    monkeypatch.setattr(smart, "paste_text", pasted.append)
    monkeypatch.setattr(smart, "speak_stream", lambda pieces: spoken.append(list(pieces)) or "")
    monkeypatch.setattr(smart._user32, "GetForegroundWindow", lambda: 1, raising=False)
    phrase, _ = smart.on_selection("translate", "переведи на английский", None)
    assert pasted == ["Hello, world"] and not spoken
    assert not phrase.startswith((smart.INFO, smart.FAIL))        # тихий режим: только звук «готово»


@pytest.mark.parametrize("low,selected,spoken,pasted", [
    ("переведи выделенное", "audio callback", True, False),       # чужой текст - перевод вслух, код не трогаю
    ("переведи на русский", "Hallo, wie geht es dir?", True, False),
    ("переведи на русский и вставь", "Hallo", False, True),        # попросили вставить - вставляю
    ("переведи выделенное", "Привет", False, True),                # русский текст - на английский, как раньше
])
def test_translation_to_russian_is_spoken(monkeypatch, low, selected, spoken, pasted):
    said, put = [], []
    monkeypatch.setattr(smart, "foreground_is_mine", lambda: False)
    monkeypatch.setattr(smart, "copy_selection", lambda: selected)
    monkeypatch.setattr(smart, "edit", lambda instruction, text: "перевод")
    monkeypatch.setattr(smart, "paste_text", put.append)
    monkeypatch.setattr(smart, "_speak_answer",
                        lambda messages, num_predict=150, num_ctx=None: said.append(messages[-1]["content"]) or (None, "перевод"))
    monkeypatch.setattr(smart._user32, "GetForegroundWindow", lambda: 1, raising=False)
    smart.on_selection("translate", low, None)
    assert bool(said) is spoken and bool(put) is pasted
    if spoken:
        assert "на русский" in said[0] and selected in said[0]


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


def _fake_wiki(monkeypatch, pages: dict, search: list[str] | None = None):
    """Подменяю сеть: pages - название -> ответ summary; search - что вернёт поиск."""
    def http_json(url, timeout=6.0):
        if "list=search" in url:
            return {"query": {"search": [{"title": t} for t in (search or [])]}}
        title = smart.urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")
        if title not in pages:
            raise OSError("HTTP Error 404")
        return pages[title]
    monkeypatch.setattr(smart, "_http_json", http_json)
    monkeypatch.setattr(smart, "ask", lambda question, history: (None, "модель"))


def test_wiki_reads_first_sentences(monkeypatch):
    extract = ("Фотоси́нтез (от др.-греч. φῶς — свет) — процесс образования органических веществ. "
               "Идёт на свету. Третье предложение.")
    _fake_wiki(monkeypatch, {"фотосинтез": {"type": "standard", "title": "Фотосинтез", "extract": extract}})
    phrase, said = smart.wiki("фотосинтез", "что такое фотосинтез", [])
    assert said == "Фотосинтез — процесс образования органических веществ. Идёт на свету."
    assert phrase == smart.RAW + said


def test_wiki_initials_are_not_sentence_end(monkeypatch):
    extract = "А. С. Пушкин — русский поэт. Второе. Третье."
    _fake_wiki(monkeypatch, {"Пушкин, Александр Сергеевич": {"title": "Пушкин", "extract": extract}},
               search=["Пушкин, Александр Сергеевич"])
    assert smart.wiki_text("пушкин") == "А. С. Пушкин — русский поэт. Второе."


def test_wiki_disambiguation_uses_search(monkeypatch):
    _fake_wiki(monkeypatch, {
        "пушкин": {"type": "disambiguation", "extract": "Пушкин — фамилия."},
        "Пушкин, Александр Сергеевич": {"type": "standard", "extract": "Русский поэт."},
    }, search=["Пушкин, Александр Сергеевич"])
    assert smart.wiki_text("пушкин") == "Русский поэт."


def test_wiki_unrelated_search_result_goes_to_model(monkeypatch):
    """Поиск находит что-нибудь почти на любую фразу - без общего слова это не ответ."""
    _fake_wiki(monkeypatch, {"Список чего-то": {"extract": "Не то."}}, search=["Список чего-то"])
    assert smart.wiki("квазибряк", "что такое квазибряк", []) == (None, "модель")


def test_wiki_offline_goes_to_model(monkeypatch):
    _fake_wiki(monkeypatch, {})
    assert smart.wiki("фотосинтез", "что такое фотосинтез", []) == (None, "модель")


def test_wiki_off(monkeypatch):
    monkeypatch.setattr(smart, "WIKI_ENABLED", False)
    assert route("кто такой пушкин") == "ask"
