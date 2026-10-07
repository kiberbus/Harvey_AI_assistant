"""Логика без микрофона: ответы, обращение, числа, напоминания, классификация плееров, целостность файлов."""

import re
from datetime import datetime
from pathlib import Path

import pytest

import harvey
import phrases
from core import commands, media, parse, speech, tools, util

ROOT = Path(harvey.__file__).resolve().parent
I, F, R = util.INFO, util.FAIL, util.RAW
SOURCES = ["harvey.py", "phrases.py", "config.py"] + [f"core/{p.name}" for p in (ROOT / "core").glob("*.py")]


# целостность: то, что уже ломалось

@pytest.mark.parametrize("name", SOURCES)
def test_no_control_chars_in_source(name):
    """Однажды обратный слеш с b в регулярке превратился в символ Backspace, и фразы перестали работать."""
    data = (ROOT / name).read_bytes()
    assert b"\x08" not in data and b"\r[" not in data.replace(b"\r\n", b"")


def test_all_phrase_patterns_compile():
    for key, patterns in phrases.PHRASES.items():
        for pattern in patterns:
            re.compile(pattern)


def test_tools_match_functions():
    """Каждый инструмент, который видит ИИ, существует; каждая функция вызывается."""
    for tool in tools.TOOLS:
        assert tool["function"]["name"] in tools.FUNCTIONS
    for name, fn in tools.FUNCTIONS.items():
        assert callable(fn), name


# тихий режим и обращение

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


# числа

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
    assert parse._numbers_to_digits(text) == expected


def test_tts_normalization():
    assert util.normalize_for_tts("Открыла Claude") == "Открыла клод"
    assert util.normalize_for_tts("21 минута") == "двадцать одна минута"


# напоминания

def _reminder(calls, text):
    calls.clear()
    action = parse.parse_reminder(text)
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
    result = parse.parse_reminder("напомни позвонить маме")()
    assert result.startswith(I) and "когда" in result and not calls


# диалог

@pytest.mark.parametrize("text,accepted", [
    ("Пауза.", True), ("Следующий трек", True), ("Запиши привет", True),
    ("ну мы вчера ходили в кино", False),
    ("Отлично, сверни Яндекс Музыку.", True),                                       # из лога: не брала
    ("На, тогда унеси это. Можешь макбук дальше посмотреть, если хочешь.", False),  # из лога: листала трек
])
def test_dialog_accepts(text, accepted):
    assert commands.dialog_accepts(text) is accepted


def test_whisper_series_hallucination_is_noise():
    from core import stt
    assert stt.is_noise("смотрите продолжение в следующей серии.")    # из лога: переключало трек
    assert not stt.is_noise("следующая серия")


@pytest.mark.parametrize("text,ends", [("всё спасибо", True), ("спасибо", True), ("пауза", False)])
def test_dialog_end(text, ends):
    assert bool(util.DIALOG_END_RE.match(text)) is ends


def test_llm_stream_sentence_split(monkeypatch):
    said = []
    monkeypatch.setattr(speech, "speak", said.append)
    pieces = ["Столица Франции ", "— Париж. Это ", "красивый город! А ", "ещё там Лувр"]
    assert speech.speak_stream(pieces) == "Столица Франции — Париж. Это красивый город! А ещё там Лувр"
    assert said == ["Столица Франции — Париж.", "Это красивый город!", "А ещё там Лувр"]


# чей плеер: музыка / YouTube / видео

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
    """Окно ПРИЛОЖЕНИЯ «Яндекс Музыка» - не вкладка браузера: ролик из браузера остаётся видео."""
    video = {"app": FIREFOX, "title": "Какой-то ролик", "artist": "Канал", "album": ""}
    assert _kinds(monkeypatch, video, ["Яндекс Музыка"], browser_titles=["Colab — Mozilla Firefox"]) == {"video", "youtube"}


def test_kinds_music_app(monkeypatch):
    track = {"app": "ru.yandex.desktop.music", "title": "Встреча", "artist": "x", "album": ""}
    assert _kinds(monkeypatch, track, []) == {"music"}


# ослышки и досрочное распознавание

@pytest.mark.parametrize("heard,fixed", [
    ("напомнив 6 вечера позвонить маме", "напомни в 6 вечера позвонить маме"),
    ("напомнив, в шесть вечера", "напомни в шесть вечера"),
    ("откройте telegram", "открой telegram"),
    ("youtube stop", "youtube стоп"),
    ("ютуб штоп", "ютуб стоп"),
    ("следующий трик", "следующий трек"),
])
def test_fix_hearing(heard, fixed):
    assert parse.fix_hearing(heard) == fixed


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
    ("Харви.", False),                              # одно имя - ждём команду как обычно
    ("пауза", False),                               # без имени - только в диалоге
])
def test_is_quick_command(text, quick):
    assert parse.is_quick_command(text) is quick


def test_quick_without_name_in_dialog_and_yes_when_pending():
    assert parse.is_quick_command("пауза", need_name=False)
    assert parse.is_quick_command("да", need_name=False, pending=True)
    assert not parse.is_quick_command("да", need_name=False, pending=False)


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
    assert 38 - q2.qsize() == 8 + stt.SILENCE_BLOCKS                # команда не законченная - ждём как раньше


def test_early_check_audio_is_not_transcribed_again(monkeypatch):
    """Досрочная проверка не нашла команду, а после неё ничего не сказали: запись та же - Whisper второй раз не нужен."""
    import queue
    from collections import deque

    import numpy as np

    from core import stt
    runs = []

    class FakeWhisper:
        def transcribe(self, audio, **kwargs):
            runs.append(len(audio))
            return iter([type("Segment", (), {"text": f" фраза {len(runs)}"})()]), None

    monkeypatch.setattr(stt, "_whisper_model", FakeWhisper())
    monkeypatch.setattr(stt, "VAD_ENABLED", False)
    monkeypatch.setattr(stt, "_last_heard", None)
    loud = np.full((stt.BLOCK_SIZE, 1), 0.3, dtype=np.float32)
    quiet = np.zeros((stt.BLOCK_SIZE, 1), dtype=np.float32)

    def record(blocks):
        q = queue.Queue()
        for block in blocks:
            q.put(block)
        guesses = []
        audio = stt.record_utterance(q, deque(), loud.flatten(), 0.05,
                                     lambda a: guesses.append(stt.transcribe(a)))    # None - команда не готова
        return guesses, stt.transcribe(audio)

    guesses, text = record([loud] * 8 + [quiet] * 30)
    assert len(runs) == 1 and text == guesses[0] == "фраза 1"

    runs.clear()
    monkeypatch.setattr(stt, "_last_heard", None)     # начало фразы то же, что выше, - не из кэша
    # Две досрочные проверки, и после второй человек ещё договорил - распознаю всю фразу заново
    guesses, text = record([loud] * 8 + [quiet] * 5 + [loud] * 3 + [quiet] * 5 + [loud] * 3 + [quiet] * 30)
    assert len(runs) == 3 and len(guesses) == 2 and text not in guesses


# игровой режим

@pytest.mark.parametrize("exe,is_game", [
    ("cs2.exe", True),
    ("eldenring.exe", True),
    ("firefox.exe", False),        # видео на весь экран в браузере
    ("vlc.exe", False),
    ("explorer.exe", False),       # рабочий стол
    ("lockapp.exe", False),        # экран блокировки
    ("", False),
])
def test_game_is_not_browser_or_player(exe, is_game):
    from core import game
    assert game.is_game(exe) is is_game


@pytest.fixture
def game_state(monkeypatch):
    """Игровой режим без окон и процессов: screen["pid"] - что на весь экран, alive - какие процессы запущены."""
    from core import game, llm
    for name, value in (("_game", None), ("_auto", False), ("_forced", None), ("_active", False)):
        monkeypatch.setattr(game, name, value)
    monkeypatch.setattr(game, "GAME_MODE_AUTO", True)
    monkeypatch.setattr(game, "GAME_LOW_PRIORITY", False)
    monkeypatch.setattr(llm, "_keep_alive", llm._keep_alive)
    screen: dict = {"pid": None}
    alive: set[int] = set()
    unloads: list[int] = []
    monkeypatch.setattr(game, "foreground_fullscreen", lambda: screen["pid"])
    monkeypatch.setattr(game, "_exe", lambda pid: {1: "cs2.exe", 2: "firefox.exe"}.get(pid, ""))
    monkeypatch.setattr(game, "_alive", lambda pid: pid in alive)
    monkeypatch.setattr(game, "_unload_model", lambda: unloads.append(1))
    return game, llm, screen, alive, unloads


def test_game_mode_follows_the_game(game_state):
    game, llm, screen, alive, unloads = game_state
    game.update(now=0)
    assert not game.active()

    screen["pid"] = 1                                        # игра на весь экран
    alive.add(1)
    game.update(now=10)
    assert game.active() and unloads == [1] and llm._keep_alive == game.GAME_KEEP_ALIVE

    screen["pid"] = None                                     # свернул игру в Discord - режим держится
    game.update(now=20)
    assert game.active()
    game.update(now=20 + game.GAME_LINGER_SEC)               # но не вечно, если игра так и не вернулась
    assert not game.active() and llm._keep_alive == game.KEEP_ALIVE

    screen["pid"] = 1
    game.update(now=1000)
    screen["pid"] = None
    alive.discard(1)                                         # игру закрыли - режим сразу выключается
    game.update(now=1001)
    assert not game.active()

    screen["pid"] = 2                                        # ютуб на весь экран - не игра
    game.update(now=1002)
    assert not game.active() and unloads == [1, 1]


def test_game_mode_manual_choice_lasts_until_game_changes(game_state):
    game, llm, screen, alive, unloads = game_state
    assert game.set_game_mode(True).endswith("игровой режим")
    game.update(now=0)
    assert game.active()                                     # включил сам - без игры не выключается

    screen["pid"] = 1
    alive.add(1)
    game.update(now=1)
    assert game.active()
    screen["pid"] = None
    alive.discard(1)
    game.update(now=2)
    assert not game.active()                                 # игра кончилась - снова автоматически

    screen["pid"] = 1
    alive.add(1)
    game.update(now=3)
    game.set_game_mode(False)                                # выключил посреди игры - не включаю обратно
    game.update(now=4)
    assert not game.active() and llm._keep_alive == game.KEEP_ALIVE


def test_sleep_reply_is_just_ok():
    from core import daily
    try:
        reply = daily.sleep_mode()
        assert util.compose_quiet([reply]) == ("Хорошо.", None)      # тихий режим
        assert util.compose([reply]) == "Хорошо."                      # обычный режим
    finally:
        daily.set_sleeping(False)


def _harvey_like(sr, pause_after: float, next_word: bool = True):
    """Огибающая как у живого «Харви,»: тишина, тихое «Х», громкое «а», провал «р», «в», затухающее «и»."""
    import numpy as np
    rng = np.random.default_rng(1)
    parts = [(0.30, 0.001), (0.08, 0.03), (0.15, 0.30), (0.04, 0.08), (0.06, 0.12), (0.15, 0.20)]
    if next_word:
        parts += [(pause_after, 0.001), (0.40, 0.30)]           # … пауза, следующее слово
    else:
        parts += [(0.5, 0.001)]
    return np.concatenate([rng.standard_normal(int(d * sr)) * a for d, a in parts]).astype(np.float32)


def _voiced_span(clip, sr, level=0.02):
    import numpy as np
    env = np.sqrt((clip[:len(clip) // 160 * 160].reshape(-1, 160) ** 2).mean(axis=1))
    idx = np.nonzero(env > level)[0]
    return idx[0] * 160 / sr, (idx[-1] + 1) * 160 / sr, len(clip) / sr


@pytest.mark.parametrize("pause", [0.15, 0.08])
def test_cut_name_whole_name_and_silence_after(pause):
    from core import stt
    sr = stt.SAMPLE_RATE
    clip = stt.cut_name(_harvey_like(sr, pause))
    assert clip is not None
    first, last, total = _voiced_span(clip, sr)
    assert abs((last - first) - 0.48) < 0.03          # имя целиком (0.48 с звука), без следующего слова
    assert total - last >= 0.3                        # запас тишины - чтобы плеер доиграл «-ви»


def test_cut_name_bare_name():
    from core import stt
    clip = stt.cut_name(_harvey_like(stt.SAMPLE_RATE, 0, next_word=False))   # просто «Харви»
    assert clip is not None


def test_cut_name_skips_when_glued_to_command():
    from core import stt
    assert stt.cut_name(_harvey_like(stt.SAMPLE_RATE, 0.0)) is None         # «Харвигромкость» - не сохраняем


def test_collect_saves_without_whisper(monkeypatch, tmp_path):
    import time as _time

    from core import stt
    monkeypatch.setattr(stt, "WAKE_SAMPLES_DIR", tmp_path)
    monkeypatch.setattr(stt, "_whisper_model", None)                       # видеокарта не нужна
    monkeypatch.setattr(stt, "WAKE_COLLECT", False)
    stt.collect_name_sample(_harvey_like(stt.SAMPLE_RATE, 0.15))
    assert not list(tmp_path.glob("*.wav"))                                 # сбор выключен - ничего не пишу
    monkeypatch.setattr(stt, "WAKE_COLLECT", True)
    stt.collect_name_sample(_harvey_like(stt.SAMPLE_RATE, 0.15))
    _time.sleep(0.05)
    while stt._collect_lock.locked():
        _time.sleep(0.02)
    assert len(list(tmp_path.glob("*.wav"))) == 1


# закрытие папок и вкладок

def test_folder_and_site_names():
    from core import apps
    assert apps._folder_names("загрузки") == {"загрузки", "downloads"}
    assert apps._folder_names("телеграм") is None
    assert apps._site_keywords("ютуб") == {"ютуб", "youtube"}
    assert apps._site_keywords("телеграм") == set()


def test_open_app_falls_back_to_site_and_game(monkeypatch):
    """Из лога: ИИ просил «Google Translate» и «Colab» как приложения и получал «не найдено»."""
    from core import apps, steam
    opened = []
    monkeypatch.setattr(apps, "open_browser", lambda site="", query="": opened.append(site) or "ok")
    monkeypatch.setattr(steam, "launch", lambda name: opened.append("steam:" + name) or "ok")
    apps.open_app("Google Translate")
    apps.open_app("Colab")
    apps.open_app("Marvel Rivals")
    assert opened == ["translate", "colab", "steam:Marvel Rivals"]
    assert apps.open_app("Такого нет").startswith(F)


def test_exact_app_needs_whole_name():
    from core import apps
    assert apps.exact_app("Obsidian")["AppID"] == "md.obsidian"
    assert apps.exact_app("obsid") is None                    # find_app нашёл бы, а без глагола - нет
    assert apps.find_app("obsid") is not None


@pytest.mark.parametrize("spoken,game", [
    ("Marvel Rivals", "Marvel Rivals"),
    ("марвел ривалс", "Marvel Rivals"),
    ("риск оф рейн два", "Risk of Rain 2"),
    ("дивинити", "Divinity: Original Sin 2"),                 # начала названия хватит
    ("но мэнс скай", "No Man's Sky"),
    ("телеграм", None),
    ("что-нибудь весёлое", None),
    ("ри", None),
])
def test_find_steam_game(spoken, game):
    from core import steam
    found = steam.find_game(spoken)
    assert (found["name"] if found else None) == game


def test_steam_library_from_all_folders(monkeypatch, tmp_path):
    """Игры лежат в нескольких папках библиотеки (C: и D:), список папок - в libraryfolders.vdf."""
    from core import steam
    main, other = tmp_path / "Steam", tmp_path / "SteamLibrary"
    for folder in (main / "steamapps", other / "steamapps"):
        folder.mkdir(parents=True)
    escaped = str(other).replace("\\", "\\\\")
    (main / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{escaped}"\n\t}}\n}}\n', encoding="utf-8")
    manifests = {main: [("228980", "Steamworks Common Redistributables"), ("262060", "Darkest Dungeon®")],
                 other: [("2767030", "Marvel Rivals")]}
    for folder, apps_in in manifests.items():
        for appid, name in apps_in:
            (folder / "steamapps" / f"appmanifest_{appid}.acf").write_text(
                f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n}}\n', encoding="utf-8")
    monkeypatch.setattr(steam, "_steam_dir", lambda: main)
    assert sorted(g["name"] for g in steam._scan()) == ["Darkest Dungeon", "Marvel Rivals"]


@pytest.mark.parametrize("spoken,title,found", [
    ("google календарем", "Google Календарь - среда, 7 октября 2026, сегодня", True),   # из лога: «вкладки нет»
    ("гитхабом", "GitHub", True),
    ("яндекс музыкой", "Яндекс Музыка — собираем музыку для вас", True),
    ("почтой", "YouTube", False),
])
def test_tab_name_in_any_case(spoken, title, found):
    from types import SimpleNamespace
    from core import browser
    assert browser._matches(SimpleNamespace(title=title), browser._keywords(spoken)) is found


@pytest.mark.parametrize("spoken,exe", [("диспетчер задачи", "taskmgr.exe"), ("телеграмма", "telegram.exe")])
def test_close_target_fuzzy(spoken, exe):
    from core import apps
    assert apps._target_exes(spoken) == {exe}


@pytest.mark.parametrize("hour,night", [(23, True), (2, True), (6, True), (7, False), (15, False), (22, False)])
def test_night_hours_wrap_midnight(monkeypatch, hour, night):
    monkeypatch.setattr(speech, "NIGHT_HOURS", (23, 7))
    assert speech._is_night(hour) is night


def test_mood_explicit_beats_night_and_context(monkeypatch):
    monkeypatch.setattr(speech, "_is_night", lambda hour: True)
    assert speech._pick_mood(None) == "night"
    assert speech._pick_mood("urgent") == "urgent"          # срочное напоминание и ночью срочное
    with speech.speaking_mood("joke"):
        assert speech._pick_mood(None) == "joke"
        assert speech._pick_mood("urgent") == "urgent"
    assert speech._pick_mood(None) == "night"
    assert speech._pick_mood("нет такого") is None


def test_silero_ssml_by_mood():
    assert 'pitch="low"' in speech._silero_ssml("Спокойной ночи", "night")
    assert 'pitch="high"' in speech._silero_ssml("Ха", "joke")
    assert "pitch" not in speech._silero_ssml("Готово", None)
    assert speech._silero_ssml("a < b", None).count("&lt;") == 1


def test_night_is_quieter_and_urgent_does_not_clip():
    audio = speech.np.array([0.5, -1.0], dtype="float32")
    assert abs(speech._apply_volume(audio, "night")).max() < 0.5
    assert speech._apply_volume(audio, None) is audio


class _FakeSounddevice:
    """Записывает обращения к sounddevice. active - играет ли ещё фраза."""

    def __init__(self, active: bool) -> None:
        self.calls: list[str] = []
        self.active = active

    def play(self, *args, **kwargs):
        self.calls.append("play")

    def get_stream(self):
        return self

    def abort(self):
        self.calls.append("abort")

    def stop(self):
        self.calls.append("stop")


def test_stop_speaking_does_not_touch_sounddevice(monkeypatch):
    """7 октября: sd.stop() из главного потока, пока поток речи закрывал звук, ронял Харви (access violation)."""
    fake = _FakeSounddevice(active=True)
    monkeypatch.setattr(speech, "sd", fake)
    speech.stop_speaking()
    assert fake.calls == [] and speech._interrupt.is_set()
    speech._play(speech.np.zeros(10, dtype="float32"), 16000)    # поток речи сам видит просьбу и замолкает
    assert fake.calls == ["play", "abort", "stop"]
    speech._interrupt.clear()


def test_play_closes_sound_after_phrase(monkeypatch):
    fake = _FakeSounddevice(active=False)
    monkeypatch.setattr(speech, "sd", fake)
    speech._play(speech.np.zeros(10, dtype="float32"), 16000)
    assert fake.calls == ["play", "abort", "stop"]


@pytest.mark.parametrize("text,joke", [
    ("расскажи анекдот", True), ("пошути", True), ("расскажи что-нибудь смешное", True),
    ("расскажи про погоду", False),
])
def test_joke_mood_phrase(text, joke):
    assert bool(util.R["joke"].search(text)) is joke
