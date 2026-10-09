"""Фраза -> действие. Каждая строка - то, что уже ломалось или легко сломать."""

import pytest

M = "media"

PHRASE_CASES = [
    # медиа с целью
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

    # лайки в Яндекс Музыке
    ("поставь лайк", [("rate_track", {"action": "like"})]),
    ("лайк", [("rate_track", {"action": "like"})]),
    ("лайкни", [("rate_track", {"action": "like"})]),
    ("лайкни эту песню", [("rate_track", {"action": "like"})]),
    ("залайкай трек", [("rate_track", {"action": "like"})]),
    ("поставь лайк этой песне", [("rate_track", {"action": "like"})]),
    ("поставь сердечко", [("rate_track", {"action": "like"})]),
    ("добавь в нравится", [("rate_track", {"action": "like"})]),
    ("добавь песню в нравится", [("rate_track", {"action": "like"})]),
    ("добавь эту песню в мне нравится", [("rate_track", {"action": "like"})]),
    ("добавь этот трек в любимые", [("rate_track", {"action": "like"})]),
    ("сохрани в избранное", [("rate_track", {"action": "like"})]),
    ("сохрани эту песню", [("rate_track", {"action": "like"})]),
    ("мне нравится эта песня", [("rate_track", {"action": "like"})]),
    ("эта песня мне очень нравится", [("rate_track", {"action": "like"})]),
    ("в избранное", [("rate_track", {"action": "like"})]),
    ("убери лайк", [("rate_track", {"action": "unlike"})]),
    ("сними лайк", [("rate_track", {"action": "unlike"})]),
    ("убери эту песню из нравится", [("rate_track", {"action": "unlike"})]),
    ("удали из избранного", [("rate_track", {"action": "unlike"})]),
    ("дизлайк", [("rate_track", {"action": "dislike"})]),
    ("поставь дизлайк", [("rate_track", {"action": "dislike"})]),
    ("мне не нравится эта песня", [("rate_track", {"action": "dislike"})]),
    ("лайкни и следующий трек", [("rate_track", {"action": "like"}), (M, {"action": "next", "target": "music"})]),

    # то, что НЕ должно стать медиа
    ("включи звук", [("mute", {"state": False})]),
    ("выключи звук", [("mute", {"state": True})]),
    ("переключи окно", [("alt_tab", {})]),
    ("выключи компьютер", [("request_power", {"action": "shutdown"})]),
    ("перезагрузись", [("request_power", {"action": "restart"})]),

    # устройство вывода: «переключи» - не следующий трек, «включи звук в …» - не просто «включи звук»
    ("переключи звук на наушники", [("audio_output", {"target": "наушники"})]),
    ("переключи звук на колонки", [("audio_output", {"target": "колонки"})]),
    ("выведи звук через монитор", [("audio_output", {"target": "монитор"})]),
    ("включи звук в наушниках", [("audio_output", {"target": "наушниках"})]),
    ("звук на колонки", [("audio_output", {"target": "колонки"})]),
    ("переключи на наушники", [("audio_output", {"target": "наушники"})]),
    ("переключи звук на fifine", [("audio_output", {"target": "fifine"})]),
    ("переключи звук", [("audio_output", {})]),
    ("смени устройство вывода", [("audio_output", {})]),
    ("куда идёт звук", [("audio_output_info", {})]),
    ("переключи звук на наушники и громкость 30",
     [("audio_output", {"target": "наушники"}), ("set_volume", {"level": 30})]),
    ("звук на 50", [("set_volume", {"level": 50})]),
    ("переключи на следующий трек", [(M, {"action": "next", "target": "music"})]),

    # громкость
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

    # сайты и поиск
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

    # приложения и окна
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

    # сон и перезапуск
    ("спать", [("sleep_mode", {})]),
    ("засыпай", [("sleep_mode", {})]),
    ("иди спать", [("sleep_mode", {})]),
    ("спокойной ночи", [("sleep_mode", {})]),
    ("переведи компьютер в спящий режим", [("request_power", {"action": "sleep"})]),
    ("перезапустись", [("restart_self", {})]),
    ("рестарт", [("restart_self", {})]),

    # из лога: уходило в ИИ
    ("obsidian", [("open_app", {"name": "obsidian"})]),             # название из Пуска без глагола
    # OBS: как его говорят и как его слышит Whisper («добавь ФБС» - из лога 9 октября)
    ("открой obs", [("open_app", {"name": "obs"})]),
    ("открой обс", [("open_app", {"name": "обс"})]),
    ("запусти о би эс", [("open_app", {"name": "о би эс"})]),
    ("открой обэс", [("open_app", {"name": "обэс"})]),
    ("открой фбс", [("open_app", {"name": "obs"})]),
    ("открой o.b.s.", [("open_app", {"name": "obs"})]),
    ("закрой обс", [("close_app", {"name": "обс"})]),
    ("сверни obs", [("minimize_app", {"name": "obs"})]),
    ("обс", [("open_app", {"name": "обс"})]),
    ("пролистни вниз", [("shortcut", {"action": "page_down"})]),
    ("листай", [("shortcut", {"action": "page_down"})]),
    ("прокрути вверх", [("shortcut", {"action": "page_up"})]),
    ("в начало страницы", [("shortcut", {"action": "page_top"})]),
    ("пролистай в самый низ", [("shortcut", {"action": "page_bottom"})]),
    ("вниз", [("shortcut", {"action": "down"})]),                    # голое «вниз» - по-прежнему стрелка
    ("этот компьютер", [("open_folder", {"name": "computer"})]),
    ("открой мой компьютер", [("open_folder", {"name": "computer"})]),
    ("открой мою почту", [("open_browser", {"site": "mail"})]),
    ("почта", [("open_browser", {"site": "mail"})]),

    # игры из Steam (библиотека подменена в conftest.py)
    ("запусти игру marvel rivals", [("launch_game", {"name": "marvel rivals"})]),
    ("давай поиграем в риск оф рейн", [("launch_game", {"name": "риск оф рейн"})]),
    ("запусти марвел ривалс", [("open_app", {"name": "марвел ривалс"})]),
    ("открой дивинити", [("open_app", {"name": "дивинити"})]),

    # игровой режим: «включи X» - не приложение, «выключи» - не медиа
    ("игровой режим", [("game_mode", {"state": True})]),
    ("включи игровой режим", [("game_mode", {"state": True})]),
    ("режим игры", [("game_mode", {"state": True})]),
    ("выключи игровой режим", [("game_mode", {"state": False})]),
    ("игровой режим выключи", [("game_mode", {"state": False})]),
    ("обычный режим", [("game_mode", {"state": False})]),

    # время, таймеры, напоминания
    ("сколько времени", [("tell_time", {})]),
    ("поставь таймер на 5 минут", [("set_timer", {"seconds": 300})]),
    ("какие напоминания", [("list_reminders", {})]),
    ("отмени все напоминания", [("cancel_reminders", {})]),

    # из лога: ослышки и непонятые фразы
    ("музыка, столб", [(M, {"action": "pause", "target": "music"})]),
    ("просто открой браузер", [("open_browser", {})]),
    ("открой browser", [("open_browser", {})]),
    ("открой в браузере", [("open_browser", {})]),
    ("открой, пожалуйста, claude", [("open_app", {"name": "claude"})]),
    ("открой клауды", [("open_app", {"name": "клауды"})]),
    ("включи claude", [("open_app", {"name": "claude"})]),          # из лога: уходило в ИИ и не открывалось
    ("включи телеграм", [("open_app", {"name": "телеграм"})]),
    ("открой станахаб", [("open_browser", {"site": "astanahub"})]),
    ("переводчик", [("open_browser", {"site": "translate"})]),
    ("браузер, переводчик", [("open_browser", {"site": "translate"})]),
    ("открой в браузере google collab", [("open_browser", {"site": "colab"})]),
    ("открой музыку", [("play_app", {"name": "яндекс музыка"})]),
    ("диск д", [("open_drive", {"letter": "D"})]),
    ("открой диск c", [("open_drive", {"letter": "C"})]),
    ("открой то, что я недавно редактировал", [("open_recent", {})]),
    ("открой файл, который я последним редактировал", [("open_recent", {})]),
    ("открой последний документ", [("open_recent", {})]),
    ("последний файл", [("open_recent", {})]),
    ("покажи недавние файлы", [("open_recent", {"show_all": True})]),
    ("перезапуск", [("restart_self", {})]),
    ("открой telegram, а потом закрою его", [("open_app", {"name": "telegram"}), ("close_app", {"name": "telegram"})]),
    ("доллар к тенге", [("calculate", {"text": "доллар к тенге"})]),
    ("доллар, king'e", [("calculate", {"text": "доллар, в тенге"})]),
    ("пет долларов тенге", [("calculate", {"text": "пять долларов тенге"})]),
    ("видели текст", [("shortcut", {"action": "select_all"})]),

    # редактирование
    ("скопируй", [("shortcut", {"action": "copy"})]),
    ("скопируй всё", [("shortcut", {"action": "copy_all"})]),
    ("вставь", [("shortcut", {"action": "paste"})]),
    ("отмени", [("undo", {})]),
    ("верни как было", [("undo", {})]),
    ("отмени последнее действие", [("undo", {})]),
    ("верни громкость как было", [("undo", {"kind": "volume"})]),
    ("верни прежнюю яркость", [("undo", {"kind": "brightness"})]),
    ("открой обратно", [("undo", {"kind": "close"})]),
    ("открой то, что закрыла", [("undo", {"kind": "close"})]),
    ("верни закрытое окно", [("undo", {"kind": "close"})]),
    ("убери то, что вставила", [("undo", {"kind": "text"})]),
    ("отмени таймер", [("cancel_timers", {})]),
    ("верни звук", [("mute", {"state": False})]),
    ("верни закрытую вкладку", [("shortcut", {"action": "reopen_tab"})]),
    ("сохрани", [("shortcut", {"action": "save"})]),
    ("выдели всё", [("select_all", {})]),                 # в проводнике - файлы, в остальных окнах - Ctrl+A
    ("очисти поле", [("shortcut", {"action": "clear_field"})]),
    ("нажми enter", [("shortcut", {"action": "enter"})]),
    ("нажми энтер", [("shortcut", {"action": "enter"})]),
    ("отправь", [("shortcut", {"action": "enter"})]),

    # браузер и окна
    ("новая вкладка", [("shortcut", {"action": "new_tab"})]),
    ("открой новую вкладку", [("shortcut", {"action": "new_tab"})]),
    ("закрой вкладку", [("close_tab", {"which": "current"})]),
    ("закрой эту вкладку", [("close_tab", {"which": "current"})]),
    ("закрой вклад", [("close_tab", {"which": "current"})]),                     # из лога
    ("закрой предыдущую вкладку", [("close_tab", {"which": "previous"})]),       # из лога: уходило в close_app
    ("закрой педующую вкладку", [("close_tab", {"which": "previous"})]),         # так Whisper слышит
    ("закрой прошлую вкладку", [("close_tab", {"which": "previous"})]),
    ("закрой следующую вкладку", [("close_tab", {"which": "next"})]),
    ("закрой правую вкладку", [("close_tab", {"which": "next"})]),
    ("закрой остальные вкладки", [("close_tab", {"which": "others"})]),
    ("закрой все вкладки кроме этой", [("close_tab", {"which": "others"})]),
    ("закрой вкладку ютуб", [("close_tab", {"which": "name", "name": "ютуб"})]),
    ("закрой вкладку с гитхабом", [("close_tab", {"which": "name", "name": "гитхабом"})]),
    ("закрой ютуб вкладку", [("close_tab", {"which": "name", "name": "ютуб"})]),
    ("перейди на вкладку ютуб", [("switch_tab", {"name": "ютуб"})]),
    ("переключись на ютуб", [("switch_tab", {"name": "ютуб"})]),
    ("вернись на вкладку переводчик", [("switch_tab", {"name": "переводчик"})]),
    ("какие вкладки открыты", [("list_tabs", {})]),
    ("сколько вкладок открыто", [("list_tabs", {})]),
    ("скопируй ссылку", [("copy_link", {})]),
    ("скопируй последнюю ссылку", [("copy_link", {})]),
    ("скопируй адрес страницы", [("copy_link", {})]),
    ("скопируй ссылку на эту страницу", [("copy_link", {})]),
    ("дай ссылку на вкладку", [("copy_link", {})]),
    ("скопируй", [("shortcut", {"action": "copy"})]),
    ("первая вкладка", [("shortcut", {"action": "first_tab"})]),
    ("перейди на последнюю вкладку", [("shortcut", {"action": "last_tab"})]),
    ("закрой вкладку и открой ютуб", [("close_tab", {"which": "current"}), ("open_browser", {"site": "youtube"})]),
    ("открой ютуб музыку", [("open_browser", {"site": "youtube_music"})]),       # из лога: открывался YouTube
    ("открой youtube-музыку", [("open_browser", {"site": "youtube_music"})]),
    ("следующая вкладка", [("shortcut", {"action": "next_tab"})]),
    ("предыдущая вкладка", [("shortcut", {"action": "prev_tab"})]),
    ("обнови страницу", [("shortcut", {"action": "refresh"})]),
    ("страницу назад", [("shortcut", {"action": "back"})]),                      # из лога: переключало трек
    ("страницу вперёд", [("shortcut", {"action": "forward"})]),
    ("вперёд в браузере", [("shortcut", {"action": "forward"})]),
    ("полный экран", [("shortcut", {"action": "fullscreen"})]),
    ("окно влево", [("shortcut", {"action": "window_left"})]),
    ("перемести окно вправо", [("shortcut", {"action": "window_right"})]),
    ("окно вверх", [("shortcut", {"action": "window_up"})]),
    ("окно вниз", [("shortcut", {"action": "window_down"})]),
    ("окно на второй монитор", [("shortcut", {"action": "window_monitor"})]),
    ("перенеси окно на другой экран", [("shortcut", {"action": "window_monitor"})]),
    ("телеграм влево", [("arrange_window", {"position": "left", "name": "телеграм"})]),
    ("перемести телеграм вправо", [("arrange_window", {"position": "right", "name": "телеграм"})]),
    ("хром на второй монитор", [("arrange_window", {"position": "monitor", "name": "хром"})]),
    ("рядом хром и телеграм", [("side_by_side", {"left": "хром", "right": "телеграм"})]),
    ("рядом chrome и telegram", [("side_by_side", {"left": "chrome", "right": "telegram"})]),
    ("поставь телеграм и блокнот рядом", [("side_by_side", {"left": "телеграм", "right": "блокнот"})]),
    ("нажми стрелку влево", [("shortcut", {"action": "left"})]),
    ("страница назад", [("shortcut", {"action": "back"})]),
    ("предыдущая страница", [("shortcut", {"action": "back"})]),
    ("назад в браузере", [("shortcut", {"action": "back"})]),
    ("нажми назад", [("shortcut", {"action": "back"})]),
    ("следующая страница", [("shortcut", {"action": "forward"})]),
    ("вперед в складку", [("shortcut", {"action": "next_tab"})]),      # так Whisper слышит «вкладку»
    ("назад вкладку", [("shortcut", {"action": "prev_tab"})]),

    # система
    ("загрузка процессора", [("system_status", {})]),
    ("сколько свободно памяти", [("system_status", {})]),
    ("температура видеокарты", [("gpu_status", {})]),
    ("какая температура видеокарты", [("gpu_status", {})]),
    ("выключи микрофон", [("microphone", {"state": False})]),
    ("заглуши микрофон", [("microphone", {"state": False})]),
    ("включи микрофон", [("microphone", {"state": True})]),

    # то, что новые команды не должны перехватить
    ("перезагрузи", [("request_power", {"action": "restart"})]),
    ("какая погода", [("weather", {})]),
    ("закрой телеграм", [("close_app", {"name": "телеграм"})]),

    # из отчёта по логу (раньше уходили в ИИ)
    ("закрой диспетер задачи", [("close_app", {"name": "диспетчер задачи"})]),
    ("закрой, браузер и яндекс музыку", [("close_app", {"name": "браузер"}),
                                         ("close_app", {"name": "яндекс музыку"})]),
    ("открой глауд", [("open_app", {"name": "глауд"})]),
    ("перезапустить", [("restart_self", {})]),
    ("открой вкладку", [("shortcut", {"action": "new_tab"})]),
    ("неполный экран", [("shortcut", {"action": "fullscreen"})]),
    ("полный кран", [("shortcut", {"action": "fullscreen"})]),
    ("открой stepik в браузере", [("open_browser", {"site": "stepik"})]),
    ("открой obsidian в браузере", [("open_browser", {"site": "google", "query": "obsidian"})]),
    ("открой в браузере, астана хаб хакатон", [("open_browser", {"site": "google", "query": "астана хаб хакатон"})]),
    ("открой астана хаб", [("open_browser", {"site": "astanahub"})]),
    ("степик", [("open_browser", {"site": "stepik"})]),
    ("открой", ["=Что открыть?"]),
    ("суверин браузер", [("minimize_app", {"name": "браузер"})]),
    ("сырни яндекс музыку", [("minimize_app", {"name": "яндекс музыку"})]),
    ("сырний браузер", [("minimize_app", {"name": "браузер"})]),
    ("середине все окна", [("show_desktop", {})]),
    ("отлично, сверни яндекс музыку", [("minimize_app", {"name": "яндекс музыку"})]),
    ("разверни все окна", [("restore_windows", {})]),
    ("верни стим", [("open_app", {"name": "стим"})]),
    ("браузер, полный экран", [("open_app", {"name": "браузер"})]),     # + развернуть, см. test_unfold_maximizes
    ("браузер полный грант", [("open_app", {"name": "браузер"})]),
    ("браузер вверх", [("open_app", {"name": "браузер"})]),
    ("скопирую ссылку сайта", [("copy_link", {})]),
    ("скопируй ссылку с сайта", [("copy_link", {})]),
    ("закрой вкладку с google календарем", [("close_tab", {"which": "name", "name": "google календарем"})]),
    ("добавь песню «нравится»", [("rate_track", {"action": "like"})]),
    ("предыдущие действия", [("undo", {})]),                            # переключало трек
    ("пятьдесят тысяч рублей в деньге", [("calculate", {"text": "пятьдесят тысяч рублей в тенге"})]),
    ("пятьдесят тысяч рублей кинги", [("calculate", {"text": "пятьдесят тысяч рублей в тенге"})]),
    ("астрой клауд", [("open_app", {"name": "клауд"})]),
    ("верни трек", [(M, {"action": "previous", "target": "music"})]),   # «верни X» - только приложения

    # «нажми X» - найти надпись на экране и нажать
    ("нажми подписаться", [("click", {"name": "подписаться"})]),
    ("нажми на кнопку войти", [("click", {"name": "войти"})]),
    ("нажми кнопку «далее»", [("click", {"name": "далее"})]),
    ("кликни на настройки", [("click", {"name": "настройки"})]),
    ("щёлкни по ссылке скачать", [("click", {"name": "скачать"})]),
    ("нажми, ок", [("click", {"name": "ок"})]),
    ("нажми принять и продолжить", [("click", {"name": "принять и продолжить"})]),   # одна кнопка
    ("нажми ок и закрой телеграм", [("click", {"name": "ок"}), ("close_app", {"name": "телеграм"})]),
    ("открой телеграм и нажми поиск", [("open_app", {"name": "телеграм"}), ("click", {"name": "поиск"})]),
    ("нажми выход", [("click", {"name": "выход"})]),                   # кнопка, а не выключение Харви
    ("нажми спящий режим", [("click", {"name": "спящий режим"})]),     # кнопка в «Пуске», а не сон Харви
    ("нажми следующий трек", [("click", {"name": "следующий трек"})]),  # нет на экране - трек, см. ниже
    # а это не надписи на экране: клавиши, лайк и плеер - как раньше
    ("нажми пробел", [("shortcut", {"action": "space"})]),
    ("нажми паузу", [(M, {"action": "pause"})]),
    ("нажми на паузу", [(M, {"action": "pause"})]),
    ("нажми плей", [(M, {"action": "play"})]),
    ("нажми play", [(M, {"action": "play"})]),
    ("нажми лайк", [("rate_track", {"action": "like"})]),
    ("нажми таблицу", [("click", {"name": "таблицу"})]),                 # не клавиша Tab

    # любые клавиши: «и» не делит сочетание на две команды
    ("нажми ctrl g", [("press_keys", {"keys": "ctrl+g"})]),
    ("нажми ctrl и g", [("press_keys", {"keys": "ctrl+g"})]),
    ("нажми контрол и джи", [("press_keys", {"keys": "ctrl+g"})]),
    ("нажми ctrl+shift+t", [("press_keys", {"keys": "ctrl+shift+t"})]),
    ("нажми w и d", [("press_keys", {"keys": "w+d"})]),
    ("нажми дабл ю", [("press_keys", {"keys": "w"})]),
    ("нажми alt f4", [("press_keys", {"keys": "alt+f4"})]),
    ("нажми клавишу f5", [("press_keys", {"keys": "f5"})]),
    ("нажми win d", [("press_keys", {"keys": "win+d"})]),
    ("нажми шифт таб", [("press_keys", {"keys": "shift+tab"})]),
    ("нажми ctrl c ctrl v", [("press_keys", {"keys": "ctrl+c, ctrl+v"})]),
    ("нажми ctrl a потом delete", [("press_keys", {"keys": "ctrl+a, delete"})]),
    ("нажми enter три раза", [("press_keys", {"keys": "enter", "times": 3})]),
    ("нажми стрелку вниз два раза", [("press_keys", {"keys": "down", "times": 2})]),
    ("три раза нажми пробел", [("press_keys", {"keys": "space", "times": 3})]),   # из лога
    ("дважды нажми enter", [("press_keys", {"keys": "enter", "times": 2})]),
    ("нажми бэк спейс", [("press_keys", {"keys": "backspace"})]),
    ("нажмите backspace", [("shortcut", {"action": "backspace"})]),       # из лога
    ("нажми delete", [("shortcut", {"action": "delete"})]),
    ("сотри", [("shortcut", {"action": "backspace"})]),                   # из лога: после «выдели всё»

    # файлы и папки в проводнике
    ("выдели все файлы", [("select_all", {"folder": True})]),
    ("выдели всё в папке", [("select_all", {"folder": True})]),
    ("видели все", [("select_all", {})]),
    ("удали", [("delete_selected", {})]),                                 # из лога
    ("удали выделенное", [("delete_selected", {})]),                      # из лога
    ("удалите выделенные файлы", [("delete_selected", {})]),
    ("удали всё", [("delete_selected", {"everything": True})]),
    ("создай текстовый файл", [("create_file", {"ext": "txt"})]),
    ("создай python-файл", [("create_file", {"ext": "py"})]),
    ("создай питон файл main", [("create_file", {"ext": "py", "name": "main"})]),
    ("создай файл питон с названием бот", [("create_file", {"ext": "py", "name": "бот"})]),
    ("создай файл с названием список покупок", [("create_file", {"ext": "txt", "name": "список покупок"})]),
    ("создай новый текстовый документ на рабочем столе", [("create_file", {"ext": "txt", "where": "desktop"})]),
    ("создай файл main.py", [("create_file", {"ext": "txt", "name": "main.py"})]),   # расширение из названия - в files
    ("создай ворд документ отчёт", [("create_file", {"ext": "docx", "name": "отчёт"})]),
    ("создай презентацию", [("create_file", {"ext": "pptx"})]),
    ("создай папку проекты", [("create_file", {"ext": "", "name": "проекты"})]),
    ("создай текстовый файл и назови его заметки", [("create_file", {"ext": "txt", "name": "заметки"})]),
    ("создай текстовый файл и открой его", [("create_file", {"ext": "txt"}), ("open_file", {})]),
    ("открой файл отчёт", [("open_file", {"name": "отчёт"})]),
    ("открой файл main точка py", [("open_file", {"name": "main точка py"})]),
    ("открой папку проекты", [("open_file", {"name": "проекты", "folder": True})]),
    ("открой папку загрузки", [("open_folder", {"name": "downloads"})]),
    ("зайди в папку загрузки", [("open_folder", {"name": "downloads"})]),
    ("удали файл отчёт", [("delete_file", {"name": "отчёт"})]),
    ("удали файл список покупок с рабочего стола", [("delete_file", {"name": "список покупок", "where": "desktop"})]),
    ("удали папку старое", [("delete_file", {"name": "старое", "folder": True})]),
    ("найди файл main", [("select_file", {"name": "main"})]),             # не поиск в Google
    ("выдели файл отчёт", [("select_file", {"name": "отчёт"})]),
    ("закрой проводник", [("close_folder", {"everything": True})]),
    ("закрой все папки", [("close_folder", {"everything": True})]),
    ("закрой папку", [("close_folder", {})]),
    ("закрой эту папку", [("close_folder", {})]),
    ("закрой папку загрузки", [("close_folder", {"name": "загрузки"})]),
    ("закрой папку загрузки и телеграм", [("close_folder", {"name": "загрузки"}), ("close_app", {"name": "телеграм"})]),
    ("закрой телеграм и проводник", [("close_app", {"name": "телеграм"}), ("close_folder", {"everything": True})]),
    ("на уровень выше", [("shortcut", {"action": "folder_up"})]),
    ("верни удалённый файл", [("undo", {"kind": "delete"})]),
    ("открой файл, который я последним редактировал", [("open_recent", {})]),

    # из лога за 7 октября
    ("закрой все приложения", [("request_close_all", {})]),
    ("разве не браузер", [("open_app", {"name": "браузер"})]),             # «разверни браузер»
    ("там, не знаю, запусти яндекс музыку", [("play_app", {"name": "яндекс музыка"})]),

    # из лога за 7-9 октября: уходили в ИИ
    ("вторая вкладка", [("switch_tab", {"index": 2})]),                  # ИИ звал switch_tab(tab_index=1)
    ("вторая кладка", [("switch_tab", {"index": 2})]),
    ("перейди на третью вкладку", [("switch_tab", {"index": 3})]),
    ("вкладка номер 4", [("switch_tab", {"index": 4})]),
    ("закрой вторую вкладку", [("close_tab", {"which": "index", "index": 2})]),   # не close_app «вторую вкладку»
    ("вкладка с youtube", [("switch_tab", {"name": "youtube"})]),
    ("вкладку назад", [("shortcut", {"action": "prev_tab"})]),            # не вкладка с названием «назад»
    ("музыка стопа", [(M, {"action": "pause", "target": "music"})]),
    ("сверние – активное окно", [("window_state", {"action": "minimize"})]),
    ("создаю папку урок 4", [("create_file", {"ext": "", "name": "урок 4"})]),
    ("открой gemini", [("open_browser", {"site": "gemini"})]),
    ("открой оbs", [("open_app", {"name": "obs"})]),                      # «о» - русская буква
    ("плаузер", [("open_browser", {})]),
    ("перезагрузить", [("request_power", {"action": "restart"})]),     # всё равно спросит «да/нет»
]


@pytest.mark.parametrize("phrase,expected", PHRASE_CASES, ids=[c[0] for c in PHRASE_CASES])
def test_phrase(run, phrase, expected):
    assert run(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "что думаешь о жизни",
    "я хочу спать",              # обычный разговор не должен усыплять
    "мне нравится твой голос",   # и ставить лайки
    "сколько лайков у этого видео",
    "рядом дом и магазин",       # не приложения - не раскладка окон
    "в каком режиме игры лучше играть",   # разговор об игре - не команда
    "запусти что-нибудь весёлое",        # не игра и не приложение
    "марвел ривалс",                     # игру без глагола не запускаю: о ней могли просто говорить
])
def test_goes_to_llm(run, phrase):
    assert run(phrase) is None


@pytest.mark.parametrize("phrase,kind,expected", [
    ("назад", "browser", [("shortcut", {"action": "back"})]),
    ("назад", "explorer", [("shortcut", {"action": "back"})]),
    ("назад", "", [("media", {"action": "previous"})]),
    ("вперёд", "browser", [("shortcut", {"action": "forward"})]),
    ("вперёд", "", [("media", {"action": "next"})]),
    ("закрой", "browser", [("close_tab", {"which": "current"})]),
    ("закрой", "", ["=Что закрыть?"]),           # из лога: ИИ закрыл окно Firefox со всеми вкладками
])
def test_depends_on_window(run, monkeypatch, phrase, kind, expected):
    """«Назад», «вперёд», «закрой» в браузере - про страницу и вкладку, в остальных окнах - трек или вопрос."""
    from core import browser
    monkeypatch.setattr(browser, "foreground_kind", lambda: kind)
    assert run(phrase) == expected


@pytest.mark.parametrize("phrase,expected", [
    ("предыдущая", [("shortcut", {"action": "prev_tab"})]),
    ("следующую", [("shortcut", {"action": "next_tab"})]),
    ("ещё", [("shortcut", {"action": "next_tab"})]),
])
def test_tab_context(run, monkeypatch, phrase, expected):
    """Из лога: после «следующая вкладка» голое «предыдущая» переключало трек, а не вкладку."""
    from core import browser
    monkeypatch.setattr(browser, "in_tab_context", lambda: True)
    assert run(phrase) == expected


def test_no_tab_context_means_track(run):
    assert run("предыдущая") == [("media", {"action": "previous", "target": None})]


@pytest.mark.parametrize("phrase", ["теле... ничего не закрывай", "забудь", "ой, не то", "ладно, проехали",
                                    "открой, забей", "не, ничего, забей"])      # последние два - из лога
def test_cancel_does_nothing(calls, monkeypatch, phrase):
    """Передумали на полуслове - ничего не выполняем, только звук отмены."""
    from core import commands
    sounds = []
    monkeypatch.setattr(commands, "play_sound", sounds.append)
    assert commands.handle_command(phrase) is True
    assert calls == [] and sounds == ["cancel"]


def test_unfold_maximizes(calls, monkeypatch):
    """Из лога: после «telegram и браузер рядом» «разверни браузер» оставлял окно на половине экрана."""
    from core import parse, tools
    monkeypatch.setattr(tools, "execute_tool", lambda name, args: calls.append((name, dict(args))) or "показала Firefox")
    monkeypatch.setattr(parse.time, "sleep", lambda seconds: None)
    for phrase in ("разверни браузер", "браузер полный экран", "браузер вверх"):
        calls.clear()
        [action] = parse.parse_all(phrase)
        action()
        assert calls == [("open_app", {"name": "браузер"}), ("window_state", {"action": "maximize"})], phrase


def test_answer_to_question(calls, monkeypatch):
    """«Открой» - «Что открыть?» - «Claude». Из лога: ответ без имени не брала, с именем отдавала ИИ."""
    from core import browser, commands
    monkeypatch.setattr(commands, "_history", commands.deque(maxlen=5))
    monkeypatch.setattr(commands, "_say", lambda text, user=None: None)
    monkeypatch.setattr(commands, "play_sound", lambda name: None)
    monkeypatch.setattr(browser, "foreground_kind", lambda: "")
    commands.handle_command("открой")
    assert commands.dialog_accepts("Claude.")
    commands.handle_command("claude")
    commands.handle_command("закрой")             # не в браузере - переспрашивает «Что закрыть?»
    commands.handle_command("телеграм")           # значит, закрыть, а не открыть
    assert calls == [("open_app", {"name": "claude"}), ("close_app", {"name": "телеграм"})]


def test_what_did_you_do(monkeypatch):
    from core import commands
    said = []
    monkeypatch.setattr(commands, "speak", said.append)
    monkeypatch.setattr(commands, "_history", commands.deque(maxlen=5))
    commands.handle_command("что ты сделала")
    commands._remember("закрой телеграм", "закрыла Telegram")
    commands.handle_command("а что ты сделала")
    assert said[0].startswith("Я пока ничего не делал")
    assert said[1] == "На «закрой телеграм»: закрыла Telegram."


def test_scene_runs_steps_in_order(calls, monkeypatch):
    """Сценарий из config.py: шаги - обычные фразы. Непонятный шаг - ошибка, а не запрос к ИИ."""
    from core import parse, util
    monkeypatch.setattr(parse, "SCENES", {"Рабочий режим": ["открой claude", "громкость 30", "сделай что-нибудь"]})
    for phrase in ("рабочий режим", "включи рабочий режим", "давай рабочий режим"):
        calls.clear()
        results = [action() for action in parse.parse_all(phrase)]
        assert calls == [("open_app", {"name": "claude"}), ("set_volume", {"level": 30})]
        assert results[-1].startswith(util.FAIL) and "сделай что-нибудь" in results[-1]
    assert parse.parse_all("рабочий") is None


@pytest.mark.parametrize("phrase,missed,then", [
    ("нажми сохранить", "NOT_FOUND", ("shortcut", {"action": "save"})),
    ("нажми следующий трек", "NOT_FOUND", (M, {"action": "next", "target": "music"})),
    ("нажми закрыть вкладку", "AMBIGUOUS", ("close_tab", {"which": "current"})),   # крестик на каждой вкладке
])
def test_click_falls_back_to_command(monkeypatch, phrase, missed, then):
    """На экране такой надписи нет (или их несколько) - выполняю то, что значит фраза без «нажми»."""
    from core import parse, tools, uia
    recorded = []

    def fake(name, args):
        recorded.append((name, dict(args)))
        return f"{getattr(uia, missed)} «x»" if name == "click" else "ok"

    monkeypatch.setattr(tools, "execute_tool", fake)
    [action] = parse.parse_all(phrase)
    assert action() == "ok"
    assert recorded[1:] == [then]


def test_click_without_fallback_says_not_found(monkeypatch):
    from core import parse, tools, uia
    monkeypatch.setattr(tools, "execute_tool", lambda name, args: f"{uia.NOT_FOUND} «подписаться»")
    [action] = parse.parse_all("нажми подписаться")
    assert action().startswith(uia.NOT_FOUND)


def test_click_exit_button_does_not_exit(calls, monkeypatch):
    """«Нажми выход» - кнопка на экране; раньше на слово «выход» Харви выключилась бы."""
    from core import commands
    monkeypatch.setattr(commands, "_history", commands.deque(maxlen=5))
    monkeypatch.setattr(commands, "_say", lambda text, user=None: None)
    monkeypatch.setattr(commands, "play_sound", lambda name: None)
    assert commands.handle_command("нажми выход") is True
    assert calls == [("click", {"name": "выход"})]


def test_whole_click_only_for_button_names():
    """«И» внутри надписи - только перед неопределённой формой; числа - это клавиши («нажми alt и пять»)."""
    from core import parse
    assert parse._whole_click("нажми сохранить и закрыть")
    assert parse._whole_click("нажми alt и пять") is None
    assert parse._whole_click("нажми ctrl и шесть") is None
    assert parse._whole_click("нажми ок и закрой телеграм") is None


def test_click_is_not_quick_command():
    """«Нажми под…» после короткой паузы не выполняю: надпись могут ещё договаривать."""
    from core import parse
    assert not parse.is_quick_command("Харви, нажми подписаться")
    assert parse.is_quick_command("Харви, нажми паузу")


@pytest.mark.parametrize("said,shown,control,score", [
    ("подписаться", "Подписаться", True, 4),
    ("корзину", "Корзина", True, 4),                          # падеж: «нажми на корзину»
    ("ютуб", "YouTube", True, 4),                             # латиница по-русски
    ("пауза", "Пауза (k)", True, 4),                          # подсказка с клавишей не мешает
    ("подписаться", "Подписаться на канал «Вася»", True, 3),
    ("настройки", "Открыть настройки", True, 2),
    ("пропустить рекламу", "Пропустить", True, 1),
    ("войти", "Как войти в аккаунт, если забыл пароль", True, 0),   # длинная ссылка, слово в середине
    ("войти", "Войти", False, 4),                             # простой текст - только целиком
    ("войти", "Нажмите здесь, чтобы войти", False, 0),
])
def test_click_match_score(said, shown, control, score):
    from core import uia
    assert uia.match_score(uia.words(said), uia.words(shown), control) == score


def test_click_misheard_and_ambiguous():
    from core import uia
    near = uia.match_score(uia.words("закрыть вкладку"), uia.words("Закрыть 1 вкладку"))
    assert 0 < near < 1
    tab = (near, True, "Закрыть 1 вкладку", None, None, 0)
    exact = (4, True, "Ответить", None, None, 0)
    assert uia._ambiguous([tab, tab])                         # неточное у нескольких - не угадываю
    assert not uia._ambiguous([exact, exact])                 # точное - ближайшее к середине окна
    assert not uia._ambiguous([tab])


def test_scene_calling_itself_does_not_loop(calls, monkeypatch):
    from core import parse, util
    monkeypatch.setattr(parse, "SCENES", {"петля": ["петля", "пауза"]})
    results = [action() for action in parse.parse_all("петля")]
    assert results[0].startswith(util.FAIL) and calls == [("media", {"action": "pause", "target": None})]
