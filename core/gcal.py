"""Google Календарь и Google Задачи: «что у меня сегодня», «добавь встречу завтра в 15:00 красным»,
«добавь задачу сдать отчёт до пятницы».

Вход в Google - один раз: при первой команде открывается браузер, токен сохраняется в GCAL_TOKEN_FILE.
Войти заранее можно так: .venv/Scripts/python -m core.gcal
Библиотеки Google импортируются внутри функций: без них Харви работает, просто без календаря."""

from __future__ import annotations

import difflib
import functools
import threading
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Callable

from config import (
    GCAL_AUTH_TIMEOUT,
    GCAL_CALENDAR_ID,
    GCAL_CREDENTIALS_FILE,
    GCAL_EVENT_MINUTES,
    GCAL_LOOKAHEAD_DAYS,
    GCAL_TASKLIST_ID,
    GCAL_TOKEN_FILE,
)
from core.util import (
    END,
    FAIL,
    INFO,
    RAW,
    _plural,
    log,
)
from core import daily


SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/tasks"]

# Цвета событий Google (colorId). Ключи те же, что в phrases.CAL_COLORS
COLOR_IDS = {
    "lavender": "1", "light_green": "2", "purple": "3", "pink": "4", "yellow": "5", "orange": "6",
    "turquoise": "7", "gray": "8", "blue": "9", "green": "10", "red": "11",
}
COLOR_SPOKEN = {    # «добавила встречу …, красным»
    "lavender": "лавандовым", "light_green": "светло-зелёным", "purple": "фиолетовым", "pink": "розовым",
    "yellow": "жёлтым", "orange": "оранжевым", "turquoise": "голубым", "gray": "серым", "blue": "синим",
    "green": "зелёным", "red": "красным",
}

_services: dict[str, object] = {}
_lock = threading.Lock()


class NotConnected(Exception):
    """Нет ключа google_credentials.json - календарь не настроен."""


def _save_token(creds) -> None:
    GCAL_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if GCAL_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GCAL_TOKEN_FILE), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception as e:                 # отозвали доступ или сменили пароль - войти заново
            log("Календарь", f"токен не обновился: {e}")
    if not GCAL_CREDENTIALS_FILE.exists():
        raise NotConnected
    from google_auth_oauthlib.flow import InstalledAppFlow

    log("Календарь", "открываю браузер для входа в Google")
    flow = InstalledAppFlow.from_client_secrets_file(str(GCAL_CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, timeout_seconds=GCAL_AUTH_TIMEOUT, open_browser=True,
                                  authorization_prompt_message="", success_message=(
                                      "Харви подключена к Google Календарю. Это окно можно закрыть."))
    _save_token(creds)
    return creds


def _service(name: str):
    """name: calendar или tasks. Клиент создаю один раз, токен он дальше обновляет сам."""
    with _lock:
        if name not in _services:
            from googleapiclient.discovery import build

            version = {"calendar": "v3", "tasks": "v1"}[name]
            _services[name] = build(name, version, credentials=_credentials(), cache_discovery=False)
        return _services[name]


def _google(fn: Callable[..., str]) -> Callable[..., str]:
    """Понятные ответы вместо исключений: нет ключа, нет библиотек, нет интернета."""
    @functools.wraps(fn)                         # tools._fit_args видит настоящие аргументы fn
    def wrapper(*args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except NotConnected:
            return f"{FAIL}календарь не подключён: положите ключ google_credentials.json в папку Харви"
        except ImportError as e:
            log("Календарь", f"нет библиотеки: {e}")
            return f"{FAIL}не установлены библиотеки Google, нужен pip install -r requirements.txt"
        except Exception as e:
            log("Календарь", f"ошибка: {type(e).__name__}: {e}")
            if "invalid_grant" in str(e) or type(e).__name__ == "RefreshError":
                _services.clear()
                GCAL_TOKEN_FILE.unlink(missing_ok=True)
                return f"{FAIL}нужно заново войти в Google, повторите команду"
            return f"{FAIL}не удалось связаться с Google Календарём"
    return wrapper


# даты и время: разбор и произношение

def _local(dt: datetime) -> datetime:
    """Во времени этого компьютера; время без пояса считаю местным."""
    return dt.astimezone()


def _parse_when(value: str) -> tuple[datetime, bool]:
    """'2026-10-08' - (полночь, без времени); '2026-10-08T15:00' - (15:00, со временем)."""
    value = str(value).strip()
    if value in ("today", "сегодня"):
        return datetime.combine(date.today(), dtime.min).astimezone(), False
    if value in ("tomorrow", "завтра"):
        return datetime.combine(date.today() + timedelta(days=1), dtime.min).astimezone(), False
    has_time = "T" in value or " " in value
    return _local(datetime.fromisoformat(value)), has_time


def _clock(dt: datetime) -> str:
    text = f"{dt.hour} {_plural(dt.hour, 'час', 'часа', 'часов')}"
    if dt.minute:
        text += f" {dt.minute} {_plural(dt.minute, 'минута', 'минуты', 'минут')}"
    return text


def _day_words(d: date) -> str:
    days = (d - date.today()).days
    named = {-1: "вчера", 0: "сегодня", 1: "завтра", 2: "послезавтра"}
    if days in named:
        return named[days]
    text = f"{d.day} {daily._MONTHS[d.month - 1]}"
    if 0 < days < 7:                                   # «в пятницу, 9 октября»
        text = f"{_WEEKDAY_ACC[d.weekday()]}, {text}"
    return text


_WEEKDAY_ACC = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")


def _event_start(event: dict) -> tuple[datetime, bool]:
    """Начало события и есть ли у него время (у событий на весь день - только дата)."""
    start = event.get("start", {})
    if "dateTime" in start:
        return _local(datetime.fromisoformat(start["dateTime"])), True
    return datetime.combine(date.fromisoformat(start["date"]), dtime.min).astimezone(), False


def _title(item: dict) -> str:
    return (item.get("summary") or item.get("title") or "без названия").strip()


def _task_due(task: dict) -> date | None:
    return date.fromisoformat(task["due"][:10]) if task.get("due") else None


# календарь

def _events(start: datetime, end: datetime, query: str | None = None, limit: int = 20) -> list[dict]:
    request = _service("calendar").events().list(
        calendarId=GCAL_CALENDAR_ID, timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=limit, **({"q": query} if query else {}))
    return request.execute().get("items", [])


def _open_tasks() -> list[dict]:
    request = _service("tasks").tasks().list(tasklist=GCAL_TASKLIST_ID, showCompleted=False, maxResults=100)
    return [t for t in request.execute().get("items", []) if t.get("title", "").strip()]


def agenda_text(day: date, events: list[dict], tasks: list[dict]) -> str:
    """Что сказать на «что у меня завтра»: встречи по порядку, потом задачи со сроком на этот день."""
    when = _day_words(day)
    due = [t for t in tasks if _task_due(t) == day]
    overdue = [t for t in tasks if day == date.today() and (_task_due(t) or day) < day]
    if not events and not due and not overdue:
        return f"{INFO}{when} в календаре ничего нет"
    parts = []
    if events:
        items = []
        for event in events:
            start, timed = _event_start(event)
            if timed and start.date() == day:
                items.append(f"в {_clock(start)} {_title(event)}")
            else:
                items.append(f"весь день {_title(event)}")
        n = len(events)
        parts.append(f"{when} {n} {_plural(n, 'событие', 'события', 'событий')}: " + "; ".join(items))
    else:
        parts.append(f"{when} встреч нет")
    if due:
        parts.append(f"{'задача' if len(due) == 1 else 'задачи'}: " + ", ".join(_title(t) for t in due))
    if overdue:
        parts.append("просрочено: " + ", ".join(_title(t) for t in overdue))
    return f"{INFO}" + ". ".join(parts)


@_google
def calendar_agenda(day: str = "today") -> str:
    """День - 'YYYY-MM-DD', 'today' или 'tomorrow'."""
    start, _ = _parse_when(day)
    events = _events(start, start + timedelta(days=1))
    return agenda_text(start.date(), events, _open_tasks())


@_google
def calendar_next() -> str:
    now = datetime.now().astimezone()
    events = [e for e in _events(now, now + timedelta(days=GCAL_LOOKAHEAD_DAYS), limit=10)
              if _event_start(e)[1]]                   # события на весь день не «следующая встреча»
    if not events:
        return f"{INFO}ближайших встреч нет"
    start, _ = _event_start(events[0])
    if start <= now:
        return f"{INFO}сейчас идёт {_title(events[0])}"
    return f"{INFO}следующая встреча {_day_words(start.date())} в {_clock(start)}: {_title(events[0])}"


@_google
def calendar_add_event(title: str, start: str, duration_min: int | None = None, color: str | None = None) -> str:
    """start - 'YYYY-MM-DDTHH:MM' (встреча) или 'YYYY-MM-DD' (на весь день)."""
    title = title.strip() or "Встреча"
    begin, timed = _parse_when(start)
    body: dict = {"summary": title}
    if timed:
        end = begin + timedelta(minutes=int(duration_min or GCAL_EVENT_MINUTES))
        body["start"], body["end"] = {"dateTime": begin.isoformat()}, {"dateTime": end.isoformat()}
    else:
        day = begin.date()
        body["start"], body["end"] = {"date": day.isoformat()}, {"date": (day + timedelta(days=1)).isoformat()}
    if color in COLOR_IDS:
        body["colorId"] = COLOR_IDS[color]
    _service("calendar").events().insert(calendarId=GCAL_CALENDAR_ID, body=body).execute()
    when = f"{_day_words(begin.date())} в {_clock(begin)}" if timed else f"{_day_words(begin.date())}, на весь день"
    text = f"{INFO}добавил{END} {title} {when}"
    if duration_min and timed:
        text += f", на {_duration_words(int(duration_min))}"
    if color in COLOR_SPOKEN:
        text += f", {COLOR_SPOKEN[color]}"
    return text


def _duration_words(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    parts = [f"{h} {_plural(h, 'час', 'часа', 'часов')}"] if h else []
    if m:
        parts.append(f"{m} {_plural(m, 'минуту', 'минуты', 'минут')}")
    return " ".join(parts)


def _matches(title: str, query: str) -> float:
    """Насколько название похоже на сказанное: «встречу с врачом» ~ «Врач, Иванов»."""
    title, query = title.lower().replace("ё", "е"), query.lower().replace("ё", "е")
    if query in title:
        return 1.0
    words = [w[:max(4, len(w) - 2)] for w in query.split() if len(w) > 2]   # грубая основа слова
    hits = sum(1 for w in words if w in title)
    return max(hits / len(words) if words else 0.0, difflib.SequenceMatcher(None, title, query).ratio())


@_google
def calendar_delete(title: str = "", when: str = "") -> str:
    """Найти встречу по названию и/или дню-времени и спросить «да/нет» перед удалением."""
    if not title.strip() and not when:
        return f"{RAW}Какую встречу удалить? Скажите, например: удали встречу с врачом."
    if when:
        moment, timed = _parse_when(when)
        start = datetime.combine(moment.date(), dtime.min).astimezone()
        events = _events(start, start + timedelta(days=1))
        if timed:
            events = [e for e in events if _event_start(e)[1] and _event_start(e)[0] == moment] or events
    else:
        now = datetime.now().astimezone()
        events = _events(now - timedelta(hours=12), now + timedelta(days=GCAL_LOOKAHEAD_DAYS), limit=100)
    if title.strip():
        scored = sorted(((_matches(_title(e), title), i) for i, e in enumerate(events)), key=lambda p: (-p[0], p[1]))
        events = [events[i] for score, i in scored if score >= 0.5]
    if not events:
        return f"{FAIL}такая встреча не найдена"
    event = events[0]
    start, timed = _event_start(event)
    spoken = f"{_title(event)} {_day_words(start.date())}" + (f" в {_clock(start)}" if timed else "")
    return daily.ask_confirm("calendar_delete_id", {"event_id": event["id"], "title": spoken},
                             f"Удалить {spoken}?")


@_google
def calendar_delete_id(event_id: str, title: str = "") -> str:
    """Вызывается после «да»."""
    _service("calendar").events().delete(calendarId=GCAL_CALENDAR_ID, eventId=event_id).execute()
    return f"удалил{END} {title}".strip()


# задачи

@_google
def task_add(title: str, due: str | None = None) -> str:
    """due - 'YYYY-MM-DD' или 'YYYY-MM-DDTHH:MM'. Google Задачи хранят только дату срока,
    поэтому время пишу в заметку к задаче."""
    title = title.strip()
    if not title:
        return f"{RAW}Какую задачу добавить?"
    body: dict = {"title": title[:1].upper() + title[1:]}
    spoken = ""
    if due:
        moment, timed = _parse_when(due)
        body["due"] = f"{moment.date().isoformat()}T00:00:00.000Z"
        spoken = f", срок {_day_words(moment.date())}"
        if timed:
            body["notes"] = f"Срок: {moment:%H:%M}"
            spoken += f" до {_clock(moment)}"
    _service("tasks").tasks().insert(tasklist=GCAL_TASKLIST_ID, body=body).execute()
    return f"{INFO}добавил{END} задачу {title}{spoken}"


@_google
def task_list() -> str:
    tasks = _open_tasks()
    if not tasks:
        return f"{INFO}задач нет"
    # сначала со сроком, по сроку; без срока - в конце
    tasks.sort(key=lambda t: (_task_due(t) is None, _task_due(t) or date.max))
    items = []
    for task in tasks[:7]:
        due = _task_due(task)
        late = due is not None and due < date.today()
        items.append(_title(task) + (f", {'просрочено, ' if late else ''}срок {_day_words(due)}" if due else ""))
    n = len(tasks)
    text = f"{n} {_plural(n, 'задача', 'задачи', 'задач')}: " + "; ".join(items)
    if n > 7:
        text += f" и ещё {n - 7}"
    return f"{INFO}{text}"


@_google
def task_done(title: str) -> str:
    tasks = _open_tasks()
    scored = sorted(((_matches(_title(t), title), i) for i, t in enumerate(tasks)), key=lambda p: (-p[0], p[1]))
    if not scored or scored[0][0] < 0.5:
        return f"{FAIL}задача «{title}» не найдена"
    task = tasks[scored[0][1]]
    _service("tasks").tasks().patch(tasklist=GCAL_TASKLIST_ID, task=task["id"],
                                    body={"status": "completed"}).execute()
    return f"отметил{END} задачу {_title(task)} выполненной"


if __name__ == "__main__":                       # войти в Google заранее, не дожидаясь первой команды
    result = calendar_agenda("today")
    print(result)
    if not result.startswith(FAIL):
        print(f"Готово, токен сохранён: {GCAL_TOKEN_FILE}")
