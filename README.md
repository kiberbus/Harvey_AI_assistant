<div align="center">

# 🎙️ Harvey: an offline voice assistant for Windows

**Say "Harvey, ..." and your PC does it. No cloud, no subscriptions, everything runs locally.**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![Whisper](https://img.shields.io/badge/STT-faster--whisper-412991)
![Ollama](https://img.shields.io/badge/LLM-Ollama%20·%20Gemma-000000?logo=ollama&logoColor=white)
![Tests](https://img.shields.io/badge/tests-348%20passed-brightgreen?logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-blue)

[English](#english) · [Русский](#русский)

</div>

---

<a id="english"></a>

## English

Harvey is a Russian-speaking voice assistant for Windows 11 that works completely offline. It waits for
its name, transcribes speech with faster-whisper, and runs about 50 system actions: apps, media players,
volume, windows, timers, reminders, calculations and more. Most commands are handled by a fast rule-based
parser in about a millisecond. Only phrases the rules don't understand go to a local LLM (Ollama + Gemma)
with tool calling. Replies are spoken with Silero or Piper TTS. Nothing leaves the machine.

### Features

| | Example phrases (translated) |
|---|---|
| 🚀 **Apps and sites** | "open Telegram", "close the browser and Yandex Music", "YouTube cats", "find a borscht recipe" |
| 🎵 **Media** | "music stop", "pause YouTube", "next track", "what's playing", "unpause" |
| 🔊 **Sound and screen** | "volume 30", "music quieter", "Telegram to 50", "brighter", "mute" |
| 🪟 **Windows and keys** | "minimize all", "switch window", "close tab", "take a screenshot", "lock the PC" |
| ⏰ **Time and tasks** | "timer for 5 minutes", "remind me at 18:00 to call mom", "note: buy bread", "what's the date" |
| 📅 **Google Calendar** | "what do I have tomorrow", "add a meeting tomorrow at 3 pm in red", "add task: send the report by Friday", "mark the task done" |
| 🌦️ **Info** | "what's the weather", "dollar rate", "what is 15% of 2400", "CPU load" |
| ⌨️ **Dictation** | "write ..." types text into the active window, the LLM adds punctuation |
| 🧠 **AI** | "what is quantum entanglement", "translate the selection", "rewrite it politer", "what's on the screen" |
| ⚡ **Power** | "shut down the PC" → "Are you sure?" → "yes" (with a delay you can cancel by voice) |
| 🎮 **Games** | "launch Marvel Rivals" (any installed Steam game); game mode turns on by itself when a game is fullscreen: the LLM leaves video memory right after each answer |
| 🎬 **Scenes** | "work mode" opens Claude, Obsidian, Telegram and starts music; your own phrase lists in `config.py` |

Commands can be chained ("volume 30 and pause"). After a reply Harvey keeps listening for a few seconds
without the wake word (dialog mode).

### How it works

```
 Mic ──► VAD ──► wake word "Harvey"? ──► faster-whisper ──► fix common mishearings
                (openWakeWord / Whisper)                              │
                                                                      ▼
                          ┌─────────── parse_all: regex rules, ~1 ms ───────────┐
                          │  understood? ── yes ──► tools.execute_tool(...)      │
                          │      │                                               │
                          │      no ──► Ollama (Gemma) + tool calling, 1 request │
                          └──────────────────────────────────────────────────────┘
                                                                      │
                          Silero / Piper ◄── reply (or just a "done" chime)
```

Key decisions:

- **Fast path without the LLM.** Most commands are matched by regex rules from `phrases.py` in
  milliseconds. Only what the rules miss goes to the model, and only one request: the reply is built by
  code, not by a second model pass.
- **One way to run actions.** Both the rules and the LLM call actions only through `tools.execute_tool`,
  which logs results and catches exceptions. Tests replace exactly this function and check which action
  *would* have run.
- **Quiet mode.** After a normal action you hear a short chime. Speech is used only for things you need
  to hear: time, weather, errors, questions. Background music is ducked while Harvey speaks.
- **Per-player control** through Windows SMTC: "YouTube stop" pauses the browser, not Spotify. WinRT is
  called from a dedicated MTA thread with its own asyncio loop.
- **Log-driven improvements.** `log_report.py` shows phrases that went to the LLM, errors and latency,
  and replays every logged phrase through the current rules, so progress and regressions are visible.

### Installation

You need **Windows 10/11**, **Python 3.11** and [Ollama](https://ollama.com). An NVIDIA GPU with CUDA 12 is
recommended: Whisper `large-v3-turbo` then recognizes a phrase in a fraction of a second. Without it a
smaller model runs on the CPU.

```bash
git clone https://github.com/kiberbus/Harvey_AI_assistant.git
cd Harvey_AI_assistant
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
ollama pull gemma4:e4b
.venv\Scripts\python harvey.py
```

Whisper and voice models (Silero / Piper) download automatically on first run. A tray icon appears next
to the clock: sleep, restart, autostart, log report, exit.

### Configuration

All settings live in [`config.py`](config.py): name, voice and grammatical gender of replies, honorific,
Ollama and Whisper models, device (`cuda` / `cpu`), weather city, home currency, sites, folders, and
`NAME_GROUPS` (how apps can be called by voice and which processes they have). Command wording and typical
Whisper mishearings are in [`phrases.py`](phrases.py). A custom wake word model can be trained by
following [`wake/КАК ОБУЧИТЬ.txt`](wake/КАК%20ОБУЧИТЬ.txt).

**Google Calendar (optional).** In [Google Cloud Console](https://console.cloud.google.com/) create a project,
enable *Google Calendar API* and *Google Tasks API*, configure the OAuth consent screen (add yourself as a test
user) and create an OAuth client ID of type *Desktop app*. Save the downloaded JSON as `google_credentials.json`
next to `harvey.py` and run `.venv\Scripts\python -m core.gcal` once to sign in (or just ask Harvey about
your calendar - the browser opens by itself). The token is stored in `google_token.json`; both files are
git-ignored.

### Project structure

```
harvey.py        main loop: mic → wake word → recognition → command; dialog, yes/no, sleep
config.py        all settings
phrases.py       command wording (regex) and mishearing fixes
log_report.py    log report: what went to the LLM, errors, latency, regressions
core/
  parse.py       fast rule-based parsing
  commands.py    routing: notes/dictation → rules → LLM
  tools.py       action registry and tool descriptions for the LLM
  llm.py smart.py      Ollama: commands, questions, text and screenshots
  stt.py speech.py     Whisper, VAD, openWakeWord; TTS and chimes
  apps.py media.py audio.py system.py daily.py calc.py   the actions themselves
  game.py        game mode: frees video memory while a game is running
  steam.py       Steam library: launch installed games by voice
  winapi.py uia.py tray.py                               ctypes, UI Automation, tray
tests/           348 tests, < 1 s; no mic, models or PowerShell needed
```

### Tests

```bash
.venv\Scripts\python -m pytest -q
```

`tests/test_phrases.py` holds 150 "phrase → expected calls" pairs. Every real-life failure gets a test.

### Privacy

Speech recognition, synthesis and the LLM all run locally. Harvey goes online only for what you ask
(weather via Open-Meteo, exchange rates, Google Calendar if you connect it) and for models on first run. The log with recognized speech stays
on your computer and can be turned off with `LOG_ENABLED`.

---

<a id="русский"></a>

## Русский

Харви - голосовая помощница для Windows 11, которая работает полностью без интернета. Она ждёт своё имя,
распознаёт речь через faster-whisper и умеет около 50 действий: приложения, плееры, громкость, окна,
таймеры, напоминания, вычисления и другое. Большинство команд разбирают правила примерно за миллисекунду.
В локальную языковую модель (Ollama + Gemma) с вызовом инструментов уходят только фразы, которые правила
не поняли. Отвечает голосом через Silero или Piper. Ничего не уходит с компьютера.

### Что умеет

| | Примеры фраз |
|---|---|
| 🚀 **Приложения и сайты** | «открой телеграм», «закрой браузер и яндекс музыку», «ютуб котики», «найди рецепт борща» |
| 🎵 **Медиа** | «музыка стоп», «ютуб на паузу», «следующий трек», «что играет», «сними с паузы» |
| 🔊 **Звук и экран** | «громкость 30», «музыку тише», «телеграм на 50», «ярче», «выключи звук» |
| 🪟 **Окна и клавиши** | «сверни всё», «переключи окно», «закрой вкладку», «сделай скриншот», «заблокируй компьютер» |
| ⏰ **Время и дела** | «таймер на 5 минут», «напомни в 18:00 позвонить маме», «запиши: купить хлеб», «какое сегодня число» |
| 📅 **Google Календарь** | «что у меня завтра», «добавь встречу завтра в 15:00 красным цветом», «добавь задачу сдать отчёт до пятницы», «отметь задачу купить молоко выполненной», «удали встречу с врачом» |
| 🌦️ **Справки** | «какая погода», «курс доллара», «сколько будет 15% от 2400», «загрузка процессора» |
| ⌨️ **Диктовка** | «запиши ...» печатает текст в активное окно, ИИ расставляет знаки препинания |
| 🧠 **ИИ** | «что такое квантовая запутанность», «переведи выделенное», «перепиши вежливее», «что на экране» |
| ⚡ **Питание** | «выключи компьютер» → «Вы уверены?» → «да» (с задержкой, можно отменить голосом) |
| 🎮 **Игры** | «запусти марвел ривалс» (любая игра из Steam); игровой режим включается сам, когда игра на весь экран: модель ИИ уходит из видеопамяти сразу после ответа |
| 🎬 **Сценарии** | «рабочий режим» открывает Claude, Obsidian, Telegram и включает музыку; свои списки фраз - в `config.py` |

Команды можно соединять: «громкость 30 и пауза». После ответа Харви ещё несколько секунд слушает
продолжение без имени (режим диалога).

### Как это устроено

Схема та же, что выше в английской части. Главные решения:

- **Быстрый путь без ИИ.** Большинство команд разбирается регулярками из `phrases.py` за миллисекунды.
  В модель уходит только то, что правила не поняли, и ровно один запрос: ответ собирает код, а не второй
  проход модели.
- **Один путь для действий.** И правила, и ИИ выполняют действия только через `tools.execute_tool`. Он
  пишет результат в лог и ловит исключения, а тесты подменяют именно его и проверяют, какое действие
  *было бы* выполнено.
- **Тихий режим.** После обычного действия звучит короткий сигнал, вслух говорится только то, что нужно
  услышать: время, погода, ошибка, вопрос. Музыка приглушается, пока Харви говорит.
- **Управление конкретным плеером** через Windows SMTC: «ютуб стоп» ставит на паузу браузер, а не
  Spotify. WinRT вызывается из отдельного MTA-потока со своим циклом asyncio.
- **Улучшения по логу.** `log_report.py` показывает фразы, ушедшие в ИИ, ошибки и задержки, и заново
  прогоняет фразы из лога через текущие правила. Так видно и прогресс, и регрессии.

### Установка

Нужны **Windows 10/11**, **Python 3.11** и [Ollama](https://ollama.com). Желательна видеокарта NVIDIA с
CUDA 12: на ней Whisper `large-v3-turbo` распознаёт фразу за доли секунды. Без неё на процессоре работает
модель поменьше.

```bash
git clone https://github.com/kiberbus/Harvey_AI_assistant.git
cd Harvey_AI_assistant
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
ollama pull gemma4:e4b
.venv\Scripts\python harvey.py
```

Модели Whisper и голоса скачиваются сами при первом запуске. У часов появится значок в трее: сон,
перезапуск, автозапуск, отчёт по логу, выход.

### Настройка

Все настройки лежат в [`config.py`](config.py): имя, голос и род ответов («открыла» / «открыл»),
обращение, модели Ollama и Whisper, устройство (`cuda` / `cpu`), город для погоды, валюта, сайты, папки и
`NAME_GROUPS` (как называть приложения голосом и какие у них процессы). Формулировки команд и типичные
ослышки Whisper лежат в [`phrases.py`](phrases.py). Своё слово-активатор можно обучить по инструкции в
[`wake/КАК ОБУЧИТЬ.txt`](wake/КАК%20ОБУЧИТЬ.txt).

**Google Календарь (по желанию).** В [Google Cloud Console](https://console.cloud.google.com/) создайте проект,
включите *Google Calendar API* и *Google Tasks API*, настройте экран согласия OAuth (добавьте себя в тестовые
пользователи) и создайте OAuth-клиент типа *Desktop app*. Скачанный JSON сохраните как `google_credentials.json`
рядом с `harvey.py` и один раз выполните `.venv\Scripts\python -m core.gcal`, чтобы войти (или просто спросите
Харви про календарь - браузер откроется сам). Токен сохранится в `google_token.json`; оба файла не попадают в git.
Цвета встреч: красный, оранжевый, жёлтый, зелёный, светло-зелёный, голубой, синий, фиолетовый, лавандовый,
розовый, серый. У задач Google хранит только дату срока, поэтому время («до 18:00») пишется в заметку к задаче.

### Тесты

```bash
.venv\Scripts\python -m pytest -q
```

В `tests/test_phrases.py` 150 пар «фраза → ожидаемые вызовы». Каждая неудача из жизни закрепляется
тестом, например «сними с паузы» не должно стать паузой.

### Приватность

Распознавание, синтез и языковая модель работают локально. В сеть Харви ходит только за тем, что вы
спросили (погода через Open-Meteo, курс валют, Google Календарь, если вы его подключили), и за моделями при первом запуске. Лог с распознанной
речью хранится только у вас и отключается настройкой `LOG_ENABLED`.

---

## License

[MIT](LICENSE)
