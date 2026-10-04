"""
Настройки Харви. Меняйте значения здесь — в harvey.py лезть не нужно.
Варианты фраз-команд («который час», «сколько времени» и т.д.) лежат в phrases.py.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ───────────────────────── ИМЯ И ГОЛОС ─────────────────────────
ASSISTANT_NAME = "Харви"
WAKE_WORDS = [  # как Whisper может «услышать» имя
    "харви", "харвей", "харве", "хэрви", "херви", "харби", "гарви",
    "арви", "харвик", "харвис", "харвейс", "харвей", "harvey", "harvy", "harvi",
]
FEMALE_VOICE = True              # True — «открыла», False — «открыл» (для мужского голоса)
USE_HONORIFIC = True             # True — обращаться «господин», False — без обращения
HONORIFIC = "господин"           # само обращение (можно «сэр», «босс» …)

# ───────────────────────── ИИ (OLLAMA) ─────────────────────────
MODEL = "gemma4:e4b"
NUM_CTX = 2048                   # промпт короткий, меньший контекст = быстрее
KEEP_ALIVE = "5m"               # модель остаётся в видеопамяти, пока вы пользуетесь помощницей
MEMORY_TURNS = 5                 # сколько последних команд помнит ИИ («закрой его», «а теперь в гугле»)
MEMORY_TTL = 180                 # через сколько секунд бездействия контекст забывается
LLM_STREAM = True                # озвучивать ответ ИИ по предложениям, не дожидаясь конца генерации

# ───────────────────────── ИИ: ТЕКСТ И ЭКРАН ─────────────────────────
# «что такое…», «объясни / переведи / исправь выделенное», «запиши» с пунктуацией, «что на экране»
SMART_DICTATION = True           # «запиши …»: ИИ расставляет знаки препинания (слова не меняет)
SELECTION_MAX_CHARS = 4000       # выделенный текст длиннее этого не отправляем (долго и не влезет в контекст)
REWRITE_LAST_SEC = 120           # «перепиши вежливее» без выделения переделывает то, что Харви записала
                                 # за последние столько секунд (если окно то же самое)
TRANSLATE_TO_CLIPBOARD = True    # перевод выделенного ещё и кладётся в буфер обмена (Ctrl+V — вставить)
VISION_ENABLED = True            # «что тут написано», «что это за ошибка» — снимок экрана уходит в модель
                                 # (gemma4:e4b изображения понимает; для модели без зрения поставьте False)
SCREEN_AREA = "window"           # "window" — только активное окно, "screen" — весь экран
SCREEN_MAX_SIDE = 1280           # до какого размера уменьшать снимок (больше — точнее, но медленнее)

# ───────────────────────── МИКРОФОН ─────────────────────────
SAMPLE_RATE = 16000
BLOCK_SIZE = 1600                # 0.1 с
SILENCE_DURATION = 0.8           # пауза, после которой фраза считается законченной
EARLY_SILENCE = 0.35             # короткие команды («пауза», «громкость 30») выполнять уже после такой паузы (0 — выкл.)
EARLY_MAX_SEC = 4.0              # досрочно проверяем только фразы не длиннее этого
MIN_UTTERANCE_SEC = 0.4
MAX_UTTERANCE_SEC = 15.0
COMMAND_TIMEOUT = 7.0            # сколько ждать команду после одиночного «Харви»
TAIL_KEEP_BLOCKS = 3             # сколько блоков тишины оставлять в конце перед распознаванием
PRE_BUFFER_SEC = 0.8
ECHO_THRESHOLD_FACTOR = 3.0      # пока Харви говорит, порог микрофона выше — чтобы не ловить её эхо

# ───────────────────────── ПОВЕДЕНИЕ ─────────────────────────
WAKE_BEEP = True                 # после одиночного «Харви» — короткий сигнал вместо «Да, господин?»
BEEP_FREQ = 587                  # Гц, нота сигнала «слушаю» (587 — ре, мягче прежних 880)
BEEP_MS = 180                    # длина затухания сигнала, мс
QUIET_MODE = True                # после обычного действия — только звук «готово»/«ошибка»;
                                 # вслух — лишь то, что нужно услышать: время, погода, ошибка, вопрос
SPEAK_ERRORS = True              # в тихом режиме после звука ошибки коротко сказать, что не так
SOUND_VOLUME = 0.3               # громкость звуков «готово» / «ошибка» (0–1)
CHIME_IDLE_SEC = 30              # через сколько секунд тишины закрывать звуковой поток сигналов
SHORT_REPLIES = True             # (если QUIET_MODE = False) после действия просто «Слушаюсь, господин.», без пересказа

# Режим диалога: после ответа Харви ещё несколько секунд слушает БЕЗ слова «Харви»
DIALOG_MODE = True
DIALOG_TIMEOUT = 6.0             # сколько секунд ждать следующую команду (отсчёт — когда Харви замолчала)
DIALOG_LOCAL_ONLY = True         # без имени принимаются только команды, понятные без ИИ
                                 # (защита от случайной речи рядом); «Харви, …» работает как обычно
CONFIRM_DANGEROUS = True         # выключение / перезагрузка / сон компьютера — только после «да»
CONFIRM_TIMEOUT = 10.0           # сколько секунд ждать «да» или «нет»
POWER_DELAY_SEC = 15             # задержка перед выключением/перезагрузкой (можно отменить словами)
DICTATION_MODE = "paste"         # "paste" (Ctrl+V, самый совместимый) или "type" (посимвольный ввод)
VOLUME_STEP = 10                 # шаг «громче / тише»
BRIGHTNESS_STEP = 15             # шаг «ярче / темнее»

# Приглушение фоновой музыки, пока говорит Харви
DUCK_FACTOR = 0.25               # до какой доли исходной громкости приглушаем
FADE_STEPS = 5
# Приложения, чью громкость Харви не трогает: не приглушаются и не считаются «играющей музыкой»
NO_VOLUME_CONTROL = {"discord.exe", "discordptb.exe", "discordcanary.exe"}
DUCK_MAX_SEC = 30                # дольше этого музыка приглушённой не остаётся, что бы ни случилось

# Как отличать музыку от видео («музыка стоп» / «ютуб стоп» / «видео стоп»)
MUSIC_APPS = ("spotify", "zunemusic", "yandex", "яндекс", "aimp", "foobar", "winamp", "itunes", "applemusic",
              "deezer", "soundcloud", "vk")           # куски имён приложений-плееров музыки
VIDEO_APPS = ("vlc", "mpc", "potplayer", "zunevideo", "movies", "kodi", "mpv")
MUSIC_SITES = r"яндекс[\s.]*музык|yandex[\s.]*music|spotify|soundcloud|youtube music|deezer|apple music"
# Что открыть, если «включи музыку», а включать нечего: сначала приложение (и сразу включить в нём),
# иначе сайт (ключи — из SITES ниже)
MEDIA_FALLBACK_APP = {"music": "яндекс музыка"}
MEDIA_FALLBACK_SITE = {"youtube": "youtube"}
BROWSER_EXES = {"firefox.exe", "chrome.exe", "msedge.exe", "opera.exe", "browser.exe", "brave.exe", "vivaldi.exe"}

# ───────────────────────── СЛОВО-АКТИВАТОР (openWakeWord) ─────────────────────────
# Лёгкая нейросеть (~2 мс на 0.1 с звука) слушает имя постоянно, а тяжёлый Whisper запускается,
# только когда имя прозвучало (или идёт диалог / ждём «да-нет»). Нет файла модели — имя ищет Whisper, как раньше.
WAKE_MODEL = BASE_DIR / "wake" / "harvey.onnx"
WAKE_THRESHOLD = 0.5             # 0–1: выше — меньше ложных срабатываний, но может не услышать имя

# ───────────────────────── НАПОМИНАНИЯ И ТРЕЙ ─────────────────────────
REMINDERS_FILE = BASE_DIR / "reminders.json"   # напоминания переживают перезапуск
TRAY_ENABLED = True              # значок у часов: спать / проснуться / автозапуск / выход

# ───────────────────────── РАСПОЗНАВАНИЕ (WHISPER) ─────────────────────────
WHISPER_MODEL_SIZE = "large-v3-turbo"  # на видеокарте быстрее и точнее medium; на процессоре medium быстрее
WHISPER_CPU_MODEL = "small"      # какую модель брать, если CUDA не завелась (turbo на процессоре медленная)
VAD_ENABLED = True               # перед Whisper проверять, есть ли в звуке речь (стук, музыка, кашель — мимо)
VAD_THRESHOLD = 0.5              # 0–1: выше — строже отсеивает шум, но может пропустить тихую речь
WHISPER_DEVICE = "cuda"          # если CUDA 12 недоступна, сама переключится на процессор
WHISPER_COMPUTE = "int8_float16" # на cuda; для процессора автоматически берётся "int8"
WHISPER_PROMPT = ("Харви, открой YouTube, запусти Telegram, открой Claude, громкость пятьдесят процентов, "
                  "пауза, таймер, заметки, погода, запиши.")
# Слова, которые Whisper должен узнавать в первую очередь (на тесте: 49/50 команд против 47/50 без них)
WHISPER_HOTWORDS = "Харви Claude Telegram YouTube Яндекс Музыка громкость пауза трек"

# ───────────────────────── ГОЛОС (СИНТЕЗ РЕЧИ) ─────────────────────────
TTS_ENGINE = "silero"            # "silero" — естественный голос; "piper" — запасной (если Silero не загрузится, включится сам)
TTS_SPEED = 1.3                  # скорость речи: 1.0 — обычно, 1.3–1.5 — быстро

PIPER_VOICE = "irina"            # irina / denis / dmitri / ruslan

SILERO_MODEL = "v5_ru"
SILERO_SPEAKER = "xenia"         # женские: xenia, kseniya, baya; мужские: aidar, eugene
SILERO_DEVICE = "cpu"            # "cuda" — ещё быстрее, но занимает видеопамять
SILERO_SAMPLE_RATE = 24000       # 8000 / 24000 / 48000 (48000 — чище, но чуть медленнее)

# ───────────────────────── ЗАМЕТКИ, ПОГОДА, КУРС, ЛОГ ─────────────────────────
NOTES_FILE = BASE_DIR / "notes.txt"
WEATHER_CITY = "Астана"       # город для «какая погода» (определяется через Open-Meteo)
CURRENCY_HOME = "KZT"            # в какой валюте называть курс
CURRENCY_HOME_NAME = "тенге"

LOG_ENABLED = True               # в логе есть всё распознанное — отключите, если это нежелательно
LOG_FILE = BASE_DIR / "harvey.log"
LOG_MAX_MB = 1                   # размер одного файла лога (хранятся 2 архивных копии)

# ───────────────────────── БРАУЗЕР, САЙТЫ, ПАПКИ ─────────────────────────
BROWSER_EXE = None               # путь к браузеру; None — берётся браузер по умолчанию

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
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

# ───────────────────────── ПРИЛОЖЕНИЯ ─────────────────────────
NAME_GROUPS = [
    # (как приложение можно назвать голосом, имена его процессов — нужны для «закрой»)
    ({"telegram", "телеграм", "телеграмм", "тг"}, ("telegram.exe",)),
    ({"claude", "cloud", "клод", "клода", "клоду", "клауд", "клауде", "клаут", "клоуд"}, ("claude.exe",)),
    ({"яндекс музыка", "яндекс музыку", "яндекс музыке", "яндекс.музыка", "yandex music"}, ("яндекс музыка.exe",)),
    ({"notepad", "блокнот"}, ("notepad.exe",)),
    ({"calculator", "калькулятор"}, ("calculatorapp.exe", "calculator.exe")),
    ({"firefox", "мозилла", "фаерфокс", "браузер"}, ("firefox.exe",)),
    ({"discord", "дискорд"}, ("discord.exe",)),
    ({"steam", "стим"}, ("steam.exe", "steamwebhelper.exe")),
    ({"spotify", "спотифай"}, ("spotify.exe",)),
    ({"explorer", "file explorer", "проводник"}, ("explorer.exe",)),
    ({"settings", "параметры", "настройки"}, ("systemsettings.exe",)),
    ({"word", "ворд"}, ("winword.exe",)),
    ({"excel", "эксель"}, ("excel.exe",)),
    ({"paint", "пейнт"}, ("mspaint.exe", "paintstudio.view.exe")),
    ({"task manager", "диспетчер задач"}, ("taskmgr.exe",)),
    ({"terminal", "терминал", "windows terminal"}, ("windowsterminal.exe",)),
    ({"vscode", "visual studio code", "код", "вс код"}, ("code.exe",)),
    ({"obs", "obs studio", "обс"}, ("obs64.exe", "obs32.exe")),
]

# Эти процессы «закрой …» не трогает никогда
PROTECTED_EXES = {
    "explorer.exe", "system", "svchost.exe", "csrss.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "dwm.exe", "python.exe", "pythonw.exe", "ollama.exe", "ollama app.exe",
}
# Для этих приложений принудительное завершение не применяется (чтобы не потерять несохранённое)
DOCUMENT_EXES = {"winword.exe", "excel.exe", "powerpnt.exe", "notepad.exe", "code.exe", "mspaint.exe"}
