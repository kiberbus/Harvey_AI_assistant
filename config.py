"""Настройки Харви. Все параметры здесь, формулировки команд - в phrases.py."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Имя и голос
ASSISTANT_NAME = "Харви"
WAKE_WORDS = [  # как Whisper может услышать имя
    "харви", "харвей", "харве", "хэрви", "херви", "харби", "гарви",
    "арви", "харвик", "харвис", "харвейс", "харвей", "harvey", "harvy", "harvi",
]
FEMALE_VOICE = True              # True - "открыла", False - "открыл"
USE_HONORIFIC = True             # обращаться "господин" или без обращения
HONORIFIC = "господин"

# Ollama
MODEL = "gemma4:e4b"
NUM_CTX = 2048                   # промпт короткий, а с маленьким контекстом модель отвечает быстрее
KEEP_ALIVE = "5m"               # сколько модель держится в видеопамяти после запроса
MEMORY_TURNS = 5                 # сколько последних команд помнит ИИ (чтобы понимал "закрой его")
MEMORY_TTL = 180                 # через сколько секунд тишины контекст сбрасывается
UNDO_TTL = 120                   # «отмени» возвращает действие Харви не старше N секунд, иначе это Ctrl+Z
UNDO_DEPTH = 10                  # сколько действий подряд можно отменить
LLM_STREAM = True                # озвучиваю ответ по предложениям, не дожидаясь конца генерации

# ИИ: текст и экран
SMART_DICTATION = True           # ИИ только расставляет знаки, слова не трогает
SELECTION_MAX_CHARS = 4000       # длиннее не отправляю: долго и не влезает в контекст
REWRITE_LAST_SEC = 120           # "перепиши вежливее" без выделения берёт текст, записанный за последние N секунд
VISION_ENABLED = True            # снимок экрана для "что тут написано"
                                 # (gemma4:e4b понимает картинки; для модели без зрения поставить False)
SCREEN_AREA = "window"           # window - активное окно, screen - весь экран
SCREEN_MAX_SIDE = 1280           # больше - точнее, но медленнее
WIKI_ENABLED = True              # "кто такой…", "что такое…": сначала выжимка из Википедии, потом ИИ
WIKI_SENTENCES = 2               # сколько предложений статьи прочитать

# Микрофон
SAMPLE_RATE = 16000
BLOCK_SIZE = 1600                # 0.1 с
SILENCE_DURATION = 0.8           # пауза, после которой фраза считается законченной
EARLY_SILENCE = 0.35             # короткие команды выполняю уже после такой паузы (0 - выключено)
EARLY_MAX_SEC = 4.0              # досрочно проверяем только фразы не длиннее этого
MIN_UTTERANCE_SEC = 0.4
MAX_UTTERANCE_SEC = 15.0
COMMAND_TIMEOUT = 7.0            # сколько ждать команду после одиночного «Харви»
TAIL_KEEP_BLOCKS = 3             # сколько блоков тишины оставлять в конце перед распознаванием
PRE_BUFFER_SEC = 0.8
ECHO_THRESHOLD_FACTOR = 3.0      # пока Харви говорит, порог выше, чтобы не ловить своё эхо

# Поведение
WAKE_BEEP = True                 # после одиночного "Харви" - короткий сигнал вместо "Да, господин?"
BEEP_FREQ = 587                  # Гц; 587 - нота ре, 880 было слишком резко
BEEP_MS = 180                    # длина затухания сигнала, мс
QUIET_MODE = True                # после обычного действия только звук готово/ошибка,
                                 # вслух - только время, погода, ошибки и вопросы
SPEAK_ERRORS = True              # в тихом режиме после звука ошибки коротко сказать, что не так
SOUND_VOLUME = 0.3               # громкость звуков «готово» / «ошибка» (0–1)
CHIME_IDLE_SEC = 30              # через сколько секунд закрывать звуковой поток (открытый поток мешает ПК уснуть)
SHORT_REPLIES = True             # если QUIET_MODE выключен: просто "Слушаюсь", без пересказа

# Режим диалога: после ответа можно несколько секунд говорить без имени
DIALOG_MODE = True
DIALOG_TIMEOUT = 6.0             # сколько секунд ждать следующую команду (отсчёт - когда Харви замолчала)
DIALOG_LOCAL_ONLY = True         # без имени принимаю только команды, понятные без ИИ,
                                 # чтобы случайная речь рядом не срабатывала
CONFIRM_DANGEROUS = True         # выключение, перезагрузка и сон - только после "да"
CONFIRM_TIMEOUT = 10.0           # сколько секунд ждать «да» или «нет»
POWER_DELAY_SEC = 15             # задержка перед выключением/перезагрузкой (можно отменить словами)
DICTATION_MODE = "paste"         # paste - через Ctrl+V (работает почти везде), type - посимвольно
VOLUME_STEP = 10                 # шаг «громче / тише»
BRIGHTNESS_STEP = 15             # шаг «ярче / темнее»

# Приглушение музыки, пока Харви говорит
DUCK_FACTOR = 0.25               # до какой доли исходной громкости приглушаем
FADE_STEPS = 5
# Эти приложения не трогаю: не приглушаю и не считаю музыкой
NO_VOLUME_CONTROL = {"discord.exe", "discordptb.exe", "discordcanary.exe"}
DUCK_MAX_SEC = 30                # страховка: дольше этого музыка тихой не остаётся

# Чтобы отличать музыку от видео ("музыка стоп" / "ютуб стоп")
MUSIC_APPS = ("spotify", "zunemusic", "yandex", "яндекс", "aimp", "foobar", "winamp", "itunes", "applemusic",
              "deezer", "soundcloud", "vk")           # части имён процессов музыкальных плееров
VIDEO_APPS = ("vlc", "mpc", "potplayer", "zunevideo", "movies", "kodi", "mpv")
MUSIC_SITES = r"яндекс[\s.]*музык|yandex[\s.]*music|spotify|soundcloud|youtube music|deezer|apple music"
# Что открыть на "включи музыку", если играть нечего: сначала приложение, потом сайт из SITES
MEDIA_FALLBACK_APP = {"music": "яндекс музыка"}
# Яндекс Музыку Windows не видит как плеер, пока в ней ни разу не нажали play,
# поэтому жму кнопку в окне через UI Automation. Названия кнопок - как их видит Windows
MUSIC_APP_PLAY_BUTTONS = ("Воспроизведение", "Воспроизведение Моей волны")
# Кнопки-переключатели в панели плеера Яндекс Музыки: лайк («в Нравится») и дизлайк
MUSIC_APP_LIKE_BUTTON = "Нравится"
MUSIC_APP_DISLIKE_BUTTON = "Не нравится"
MEDIA_FALLBACK_SITE = {"youtube": "youtube"}
BROWSER_EXES = {"firefox.exe", "chrome.exe", "msedge.exe", "opera.exe", "browser.exe", "brave.exe", "vivaldi.exe"}

# Слово-активатор (openWakeWord)
# Маленькая сеть (~2 мс на блок) слушает имя постоянно, Whisper запускаю только после имени.
# Если файла модели нет, имя ищет Whisper.
WAKE_MODEL = BASE_DIR / "wake" / "harvey.onnx"
WAKE_THRESHOLD = 0.5             # выше - меньше ложных срабатываний, но может пропускать имя
WAKE_GATE = False                # True - Whisper только после имени (экономит видеокарту);
                                 # False - модель только пишет в лог, услышала ли имя.
                                 # Включаю, когда в логе имя стабильно распознаётся.
# Собираю образцы своего "Харви" для обучения модели: из фраз с именем
# вырезается само слово и кладётся в wake/samples (в git не идёт)
WAKE_COLLECT = True
WAKE_SAMPLES_DIR = BASE_DIR / "wake" / "samples"
WAKE_SAMPLES_MAX = 1000

# Напоминания и трей
REMINDERS_FILE = BASE_DIR / "reminders.json"   # чтобы напоминания пережили перезапуск
TRAY_ENABLED = True              # значок у часов

# Распознавание (Whisper)
WHISPER_MODEL_SIZE = "large-v3-turbo"  # на видеокарте быстрее и точнее medium
WHISPER_CPU_MODEL = "small"      # turbo на процессоре слишком медленная, поэтому без CUDA беру small
VAD_ENABLED = True               # VAD перед Whisper: стук, кашель и музыку не распознаю
VAD_THRESHOLD = 0.5              # выше - строже к шуму, но может пропустить тихую речь
WHISPER_DEVICE = "cuda"          # без CUDA 12 само переключится на процессор
WHISPER_COMPUTE = "int8_float16" # для процессора берётся int8
WHISPER_PROMPT = ("Харви, открой YouTube, запусти Telegram, открой Claude, громкость пятьдесят процентов, "
                  "пауза, таймер, заметки, погода, запиши.")
# Подсказки для Whisper. На моём тесте с ними 49/50 команд, без них 47/50
WHISPER_HOTWORDS = "Харви Claude Telegram YouTube Яндекс Музыка громкость пауза трек Obsidian Stepik"

# Синтез речи
TTS_ENGINE = "silero"            # silero звучит естественнее, piper - запасной (включается сам, если Silero не загрузился)
TTS_SPEED = 1.3                  # 1.0 - обычная скорость

PIPER_VOICE = "irina"            # irina / denis / dmitri / ruslan

SILERO_MODEL = "v5_ru"
SILERO_SPEAKER = "xenia"         # женские: xenia, kseniya, baya; мужские: aidar, eugene
SILERO_DEVICE = "cpu"            # cuda быстрее, но занимает видеопамять
SILERO_SAMPLE_RATE = 24000       # 48000 чище, но чуть медленнее
# Перевод вслух («как по-английски добрый вечер»): язык → (модель Silero, голос). Модель качается сама
# при первом переводе. Голоса в v3_en подобраны по высоте тона: en_21 женский, en_80 мужской.
SILERO_FOREIGN = {"английский": ("v3_en", "en_21" if FEMALE_VOICE else "en_80")}

# Заметки, погода, курс, лог
NOTES_FILE = BASE_DIR / "notes.txt"
WEATHER_CITY = "Астана"       # город для погоды (Open-Meteo)
CURRENCY_HOME = "KZT"            # в какой валюте называть курс
CURRENCY_HOME_NAME = "тенге"

LOG_ENABLED = True               # в лог пишется вся распознанная речь
LOG_FILE = BASE_DIR / "harvey.log"
LOG_MAX_MB = 1                   # храню ещё 2 старых файла

# Браузер, сайты, папки
BROWSER_EXE = None               # None - браузер по умолчанию

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "translate": "https://translate.google.com",
    "colab": "https://colab.research.google.com",
    "astanahub": "https://astanahub.com",
    "stepik": "https://stepik.org",
}

_HOME = os.path.expanduser("~")
FOLDERS = {
    "downloads": os.path.join(_HOME, "Downloads"),
    "documents": os.path.join(_HOME, "Documents"),
    "desktop": os.path.join(_HOME, "Desktop"),
    "pictures": os.path.join(_HOME, "Pictures"),
    "music": os.path.join(_HOME, "Music"),
    "videos": os.path.join(_HOME, "Videos"),
}

# Приложения
NAME_GROUPS = [
    # (как назвать голосом, процессы приложения - нужны для "закрой")
    ({"telegram", "телеграм", "телеграмм", "тг"}, ("telegram.exe",)),
    ({"claude", "cloud", "клод", "клода", "клоду", "клауд", "клауде", "клауды", "клаут", "клоуд", "глауд"}, ("claude.exe",)),
    ({"яндекс музыка", "яндекс музыку", "яндекс музыке", "яндекс.музыка", "yandex music"}, ("яндекс музыка.exe",)),
    ({"notepad", "блокнот"}, ("notepad.exe",)),
    ({"calculator", "калькулятор"}, ("calculatorapp.exe", "calculator.exe")),
    ({"firefox", "мозилла", "фаерфокс", "браузер"}, ("firefox.exe",)),
    ({"chrome", "google chrome", "хром", "гугл хром"}, ("chrome.exe",)),
    ({"discord", "дискорд"}, ("discord.exe",)),
    ({"steam", "стим"}, ("steam.exe", "steamwebhelper.exe")),
    ({"spotify", "спотифай"}, ("spotify.exe",)),
    ({"explorer", "file explorer", "проводник"}, ("explorer.exe",)),
    ({"settings", "параметры", "настройки"}, ("systemsettings.exe",)),
    ({"word", "ворд"}, ("winword.exe",)),
    ({"excel", "эксель"}, ("excel.exe",)),
    ({"paint", "пейнт"}, ("mspaint.exe", "paintstudio.view.exe")),
    ({"task manager", "диспетчер задач", "диспетчер задачи", "диспетчер"}, ("taskmgr.exe",)),
    ({"terminal", "терминал", "windows terminal"}, ("windowsterminal.exe",)),
    ({"vscode", "visual studio code", "код", "вс код"}, ("code.exe",)),
    ({"obs", "obs studio", "обс"}, ("obs64.exe", "obs32.exe")),
]

# Эти процессы "закрой" не трогает никогда
PROTECTED_EXES = {
    "explorer.exe", "system", "svchost.exe", "csrss.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "dwm.exe", "python.exe", "pythonw.exe", "ollama.exe", "ollama app.exe",
}
# Эти не завершаю принудительно, чтобы не потерять несохранённое
DOCUMENT_EXES = {"winword.exe", "excel.exe", "powerpnt.exe", "notepad.exe", "code.exe", "mspaint.exe"}
