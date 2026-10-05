"""Фраза → действие. Каждая строка — то, что уже однажды ломалось или легко сломать.
Новая формулировка в phrases.py? Добавьте её сюда — и запустите:  .venv\\Scripts\\python -m pytest"""

import pytest

M = "media"

PHRASE_CASES = [
    # ── медиа с целью ──
    ("музыка стоп", [(M, {"action": "pause", "target": "music"})]),
    ("музыка, стоп", [(M, {"action": "pause", "target": "music"})]),
    ("ютуб стоп", [(M, {"action": "pause", "target": "youtube"})]),
    ("стоп, youtube", [(M, {"action": "pause", "target": "youtube"})]),
    ("стопи youtube", [(M, {"action": "pause", "target": "youtube"})]),
    ("поставь ютуб на паузу", [(M, {"action": "pause", "target": "youtube"})]),
    ("останови видео", [(M, {"action": "pause", "target": "video"})]),
    ("включи музыку", [(M, {"action": "play", "target": "music"})]),
    ("музыку продолжи", [(M, {"action": "play", "target": "music"})]),
    ("включи музыку в яндекс музыке", [(M, {"action": "play", "target": "music"})]),
    ("включи ютуб", [(M, {"action": "play", "target": "youtube"})]),
    ("youtube, продолжим", [(M, {"action": "play", "target": "youtube"})]),
    ("сними с паузы", [(M, {"action": "play", "target": None})]),
    ("пауза", [(M, {"action": "pause", "target": None})]),
    ("стоп", [(M, {"action": "pause", "target": None})]),
    ("следующий трек", [(M, {"action": "next", "target": "music"})]),
    ("следующее видео", [(M, {"action": "next", "target": "video"})]),
    ("что играет", [("now_playing", {})]),
    ("что за песня", [("now_playing", {})]),
    ("кто поёт", [("now_playing", {})]),

    # ── то, что НЕ должно стать медиа ──
    ("включи звук", [("mute", {"state": False})]),
    ("выключи звук", [("mute", {"state": True})]),
    ("переключи окно", [("alt_tab", {})]),
    ("выключи компьютер", [("request_power", {"action": "shutdown"})]),
    ("перезагрузись", [("request_power", {"action": "restart"})]),

    # ── громкость: общая и по приложениям ──
    ("громкость 30", [("set_volume", {"level": 30})]),
    ("тише", [("change_volume", {"delta": -10})]),
    ("громкость 30 и пауза", [("set_volume", {"level": 30}), (M, {"action": "pause", "target": None})]),
    ("музыку тише", [("app_volume", {"target": "music", "delta": -10})]),
    ("приглуши музыку", [("app_volume", {"target": "music", "delta": -10})]),
    ("ютуб на 30", [("app_volume", {"target": "youtube", "level": 30})]),
    ("громкость ютуба 40", [("app_volume", {"target": "youtube", "level": 40})]),
    ("сделай видео потише на 20", [("app_volume", {"target": "video", "delta": -20})]),
    ("громкость музыки на пятьдесят", [("app_volume", {"target": "music", "level": 50})]),
    ("телеграм на 50", [("app_volume", {"target": "app:телеграм", "level": 50})]),

    # ── сайты, поиск, короткие формы ──
    ("ютуб котики", [("open_browser", {"site": "youtube", "query": "котики"})]),
    ("ютуб 30", [("open_browser", {"site": "youtube", "query": "30"})]),
    ("гугл погода в лондоне", [("open_browser", {"site": "google", "query": "погода в лондоне"})]),
    ("найди рецепт борща", [("open_browser", {"site": "google", "query": "рецепт борща"})]),
    ("загугли кошки", [("open_browser", {"site": "google", "query": "кошки"})]),
    ("открой ютуб и найди котиков", [("open_browser", {"site": "youtube", "query": "котиков"})]),
    ("открой ютуб", [("open_browser", {"site": "youtube"})]),
    ("ютуб", [("open_browser", {"site": "youtube"})]),
    ("браузер", [("open_browser", {})]),
    ("открой в браузере калькулятор матриц", [("open_browser", {"site": "google", "query": "калькулятор матриц"})]),
    ("открой калькулятор матриц в браузере", [("open_browser", {"site": "google", "query": "калькулятор матриц"})]),
    ("в браузере astana hub", [("open_browser", {"site": "astanahub"})]),
    ("музыка", [(M, {"action": "play", "target": "music"})]),
    ("спим", [("sleep_mode", {})]),

    # ── приложения и окна ──
    ("телеграм", [("open_app", {"name": "телеграм"})]),
    ("открой яндекс музыку", [("play_app", {"name": "яндекс музыка"})]),
    ("запусти яндекс музыку", [("play_app", {"name": "яндекс музыка"})]),
    ("включи яндекс музыку", [("play_app", {"name": "яндекс музыка"})]),
    ("яндекс музыку на паузу", [(M, {"action": "pause", "target": "music"})]),
    ("яндекс музыка", [("play_app", {"name": "яндекс музыка"})]),
    ("открой cloud", [("open_app", {"name": "cloud"})]),
    ("открой клод", [("open_app", {"name": "клод"})]),
    ("закрой яндекс музыку", [("close_app", {"name": "яндекс музыку"})]),
    ("сверни яндекс музыку", [("minimize_app", {"name": "яндекс музыку"})]),
    ("сверни яндекс.музыка", [("minimize_app", {"name": "яндекс.музыка"})]),
    ("разверни яндекс музыку", [("open_app", {"name": "яндекс музыку"})]),
    ("сверни", [("window_state", {"action": "minimize"})]),
    ("сверни все", [("show_desktop", {})]),
    ("загрузки", [("open_folder", {"name": "downloads"})]),

    # ── сон и перезапуск ──
    ("спать", [("sleep_mode", {})]),
    ("засыпай", [("sleep_mode", {})]),
    ("иди спать", [("sleep_mode", {})]),
    ("спокойной ночи", [("sleep_mode", {})]),
    ("переведи компьютер в спящий режим", [("request_power", {"action": "sleep"})]),
    ("перезапустись", [("restart_self", {})]),
    ("рестарт", [("restart_self", {})]),

    # ── время, таймеры, напоминания ──
    ("сколько времени", [("tell_time", {})]),
    ("поставь таймер на 5 минут", [("set_timer", {"seconds": 300})]),
    ("какие напоминания", [("list_reminders", {})]),
    ("отмени все напоминания", [("cancel_reminders", {})]),

    # ── из лога: ошибки слуха и непонятые формулировки ──
    ("музыка, столб", [(M, {"action": "pause", "target": "music"})]),
    ("просто открой браузер", [("open_browser", {})]),
    ("открой browser", [("open_browser", {})]),
    ("открой в браузере", [("open_browser", {})]),
    ("открой, пожалуйста, claude", [("open_app", {"name": "claude"})]),
    ("открой клауды", [("open_app", {"name": "клауды"})]),
    ("открой станахаб", [("open_browser", {"site": "astanahub"})]),
    ("переводчик", [("open_browser", {"site": "translate"})]),
    ("браузер, переводчик", [("open_browser", {"site": "translate"})]),
    ("открой в браузере google collab", [("open_browser", {"site": "colab"})]),
    ("открой музыку", [("play_app", {"name": "яндекс музыка"})]),
    ("диск д", [("open_drive", {"letter": "D"})]),
    ("открой диск c", [("open_drive", {"letter": "C"})]),
    ("перезапуск", [("restart_self", {})]),
    ("открой telegram, а потом закрою его", [("open_app", {"name": "telegram"}), ("close_app", {"name": "telegram"})]),
    ("доллар к тенге", [("calculate", {"text": "доллар к тенге"})]),
    ("доллар, king'e", [("calculate", {"text": "доллар, в тенге"})]),
    ("пет долларов тенге", [("calculate", {"text": "пять долларов тенге"})]),
    ("видели текст", [("shortcut", {"action": "select_all"})]),

    # ── редактирование ──
    ("скопируй", [("shortcut", {"action": "copy"})]),
    ("скопируй всё", [("shortcut", {"action": "copy_all"})]),
    ("вставь", [("shortcut", {"action": "paste"})]),
    ("отмени", [("shortcut", {"action": "undo"})]),
    ("сохрани", [("shortcut", {"action": "save"})]),
    ("выдели всё", [("shortcut", {"action": "select_all"})]),
    ("очисти поле", [("shortcut", {"action": "clear_field"})]),
    ("нажми enter", [("shortcut", {"action": "enter"})]),
    ("нажми энтер", [("shortcut", {"action": "enter"})]),
    ("отправь", [("shortcut", {"action": "enter"})]),

    # ── браузер и окна ──
    ("новая вкладка", [("shortcut", {"action": "new_tab"})]),
    ("открой новую вкладку", [("shortcut", {"action": "new_tab"})]),
    ("закрой вкладку", [("shortcut", {"action": "close_tab"})]),
    ("следующая вкладка", [("shortcut", {"action": "next_tab"})]),
    ("предыдущая вкладка", [("shortcut", {"action": "prev_tab"})]),
    ("обнови страницу", [("shortcut", {"action": "refresh"})]),
    ("вперёд", [("shortcut", {"action": "forward"})]),
    ("полный экран", [("shortcut", {"action": "fullscreen"})]),
    ("окно влево", [("shortcut", {"action": "window_left"})]),
    ("перемести окно вправо", [("shortcut", {"action": "window_right"})]),

    # ── система ──
    ("загрузка процессора", [("system_status", {})]),
    ("сколько свободно памяти", [("system_status", {})]),
    ("температура видеокарты", [("gpu_status", {})]),
    ("какая температура видеокарты", [("gpu_status", {})]),
    ("выключи микрофон", [("microphone", {"state": False})]),
    ("заглуши микрофон", [("microphone", {"state": False})]),
    ("включи микрофон", [("microphone", {"state": True})]),

    # ── то, что новые команды не должны перехватить ──
    ("перезагрузи", [("request_power", {"action": "restart"})]),
    ("какая погода", [("weather", {})]),
    ("закрой телеграм", [("close_app", {"name": "телеграм"})]),
]


@pytest.mark.parametrize("phrase,expected", PHRASE_CASES, ids=[c[0] for c in PHRASE_CASES])
def test_phrase(run, phrase, expected):
    assert run(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "что думаешь о жизни",
    "я хочу спать",              # обычный разговор не должен усыплять
])
def test_goes_to_llm(run, phrase):
    assert run(phrase) is None


@pytest.mark.parametrize("exe,expected", [
    ("firefox.exe", [("shortcut", {"action": "back"})]),
    ("telegram.exe", [("media", {"action": "previous"})]),
])
def test_back_depends_on_window(run, monkeypatch, exe, expected):
    """«Назад» в браузере — страница назад, в остальных окнах — предыдущий трек."""
    from core import system
    monkeypatch.setattr(system, "foreground_exe", lambda: exe)
    assert run("назад") == expected
