"""Google Календарь и Задачи: разбор фраз (без сети) и ответы на подставном API Google."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from core import gcal, parse, util

I, F, R = util.INFO, util.FAIL, util.RAW
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def _next_weekday(weekday: int) -> date:
    return TODAY + timedelta(days=(weekday - TODAY.weekday()) % 7)


def _on(day: date, hour: int, minute: int = 0) -> str:
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}"


# встречи

@pytest.mark.parametrize("phrase,args", [
    ("добавь встречу завтра в 15:00", {"title": "Встреча", "start": _on(TOMORROW, 15)}),
    ("добавь встречу завтра в 3 часа дня", {"title": "Встреча", "start": _on(TOMORROW, 15)}),
    ("добавь встречу завтра в 3", {"title": "Встреча", "start": _on(TOMORROW, 15)}),   # днём, не ночью
    ("создай встречу с врачом завтра в 9 утра", {"title": "Встреча с врачом", "start": _on(TOMORROW, 9)}),
    ("запланируй созвон с командой в пятницу в 10:30",
     {"title": "Созвон с командой", "start": _on(_next_weekday(4), 10, 30)}),
    ("добавь в календарь обед с мамой завтра в 13", {"title": "Обед с мамой", "start": _on(TOMORROW, 13)}),
    ("добавь обед с мамой завтра в 13 в календарь", {"title": "Обед с мамой", "start": _on(TOMORROW, 13)}),
    ("добавь встречу завтра в 15:00 красным цветом",
     {"title": "Встреча", "start": _on(TOMORROW, 15), "color": "red"}),
    ("добавь встречу завтра в 15 цвет синий", {"title": "Встреча", "start": _on(TOMORROW, 15), "color": "blue"}),
    ("добавь встречу завтра в 15 зелёным", {"title": "Встреча", "start": _on(TOMORROW, 15), "color": "green"}),
    ("добавь встречу завтра в 15 светло-зелёным цветом",
     {"title": "Встреча", "start": _on(TOMORROW, 15), "color": "light_green"}),
    ("добавь встречу завтра в 15 на 2 часа", {"title": "Встреча", "start": _on(TOMORROW, 15), "duration_min": 120}),
    ("добавь встречу завтра на полчаса в 10 утра",
     {"title": "Встреча", "start": _on(TOMORROW, 10), "duration_min": 30}),
    ("добавь встречу завтра с 15 до 17", {"title": "Встреча", "start": _on(TOMORROW, 15), "duration_min": 120}),
    ("добавь событие день рождения мамы на весь день завтра", {"title": "День рождения мамы", "start": TOMORROW.isoformat()}),
    ("запиши встречу завтра в 18", {"title": "Встреча", "start": _on(TOMORROW, 18)}),
    ("новая встреча завтра в 12 с Алёной", {"title": "Встреча с алёной", "start": _on(TOMORROW, 12)}),
])
def test_event_add(run, phrase, args):
    assert run(phrase) == [("calendar_add_event", args)]


def test_event_on_date_without_time_is_all_day(run):
    (name, args), = run("добавь событие 25 декабря новый год")
    assert name == "calendar_add_event" and args["title"] == "Новый год"
    assert args["start"].endswith("-12-25") and "T" not in args["start"]


def test_event_without_time_asks_when(run):
    result = run("добавь встречу с врачом")
    assert len(result) == 1 and result[0].startswith(I) and "когда" in result[0]


def test_event_today_time_passed_goes_to_tomorrow(run):
    (name, args), = run("добавь встречу в 0:01")   # 00:01 уже прошло
    assert args["start"] == _on(TOMORROW, 0, 1)


# планы на день

@pytest.mark.parametrize("phrase,day", [
    ("что у меня сегодня", TODAY),
    ("что у меня завтра", TOMORROW),
    ("а что у меня на завтра", TOMORROW),
    ("что у меня запланировано на завтра", TOMORROW),
    ("что у меня в пятницу", _next_weekday(4)),
    ("какие у меня планы на завтра", TOMORROW),
    ("какие планы", TODAY),
    ("что в календаре", TODAY),
    ("расписание на завтра", TOMORROW),
    ("мои встречи на сегодня", TODAY),
    ("есть ли у меня встречи завтра", TOMORROW),
    ("я свободна в субботу", _next_weekday(5)),
])
def test_agenda(run, phrase, day):
    assert run(phrase) == [("calendar_agenda", {"day": day.isoformat()})]


@pytest.mark.parametrize("phrase", ["какая следующая встреча", "когда у меня следующая встреча", "ближайшая встреча"])
def test_next_event(run, phrase):
    assert run(phrase) == [("calendar_next", {})]


@pytest.mark.parametrize("phrase,args", [
    ("удали встречу с врачом", {"title": "с врачом", "when": ""}),
    ("отмени встречу завтра в 15", {"title": "", "when": _on(TOMORROW, 15)}),
    ("отмени созвон в пятницу", {"title": "", "when": _next_weekday(4).isoformat()}),
])
def test_event_delete(run, phrase, args):
    assert run(phrase) == [("calendar_delete", args)]


# задачи

@pytest.mark.parametrize("phrase,args", [
    ("добавь задачу купить молоко", {"title": "купить молоко"}),
    ("новая задача позвонить врачу", {"title": "позвонить врачу"}),
    ("задача: полить цветы", {"title": "полить цветы"}),
    ("запиши задачу забрать посылку", {"title": "забрать посылку"}),
    ("добавь задачу сдать отчёт до пятницы", {"title": "сдать отчёт", "due": _next_weekday(4).isoformat()}),
    ("добавь задачу сдать отчёт со сроком до пятницы", {"title": "сдать отчёт", "due": _next_weekday(4).isoformat()}),
    ("добавь задачу на завтра купить хлеб", {"title": "купить хлеб", "due": TOMORROW.isoformat()}),
    ("добавь задачу оплатить интернет с дедлайном завтра в 18:00",
     {"title": "оплатить интернет", "due": _on(TOMORROW, 18)}),
    ("добавь задачу прочитать книгу без срока", {"title": "прочитать книгу"}),
    ("добавь в задачи дойти до магазина", {"title": "дойти до магазина"}),
])
def test_task_add(run, phrase, args):
    assert run(phrase) == [("task_add", args)]


@pytest.mark.parametrize("phrase", ["какие у меня задачи", "мои задачи", "список задач", "что мне нужно сделать"])
def test_task_list(run, phrase):
    assert run(phrase) == [("task_list", {})]


@pytest.mark.parametrize("phrase,title", [
    ("отметь задачу купить молоко выполненной", "купить молоко"),
    ("задача купить молоко выполнена", "купить молоко"),
    ("я сделала задачу полить цветы", "полить цветы"),
])
def test_task_done(run, phrase, title):
    assert run(phrase) == [("task_done", {"title": title})]


# то, что календарь забирать не должен

@pytest.mark.parametrize("phrase,expected", [
    ("напомни завтра в 9 утра про встречу", "add_reminder"),
    ("открой календарь", "open_browser"),
    ("поставь лайк", "rate_track"),
])
def test_not_calendar(run, phrase, expected):
    assert run(phrase)[0][0] == expected


def test_dictation_does_not_take_calendar():
    assert util.DICTATE_RE.match("запиши задачу купить хлеб") is None
    assert util.DICTATE_RE.match("запиши в календарь встречу") is None
    assert util.DICTATE_RE.match("запиши привет всем").group(1) == "привет всем"


# ответы на подставном Google

def _event(title, day, hour=None):
    if hour is None:
        return {"summary": title, "start": {"date": day.isoformat()}}
    return {"summary": title, "start": {"dateTime": datetime(day.year, day.month, day.day, hour).astimezone().isoformat()}}


def test_agenda_text():
    events = [_event("Отпуск", TODAY), _event("Созвон", TODAY, 10)]
    tasks = [{"title": "Купить молоко", "due": f"{TODAY.isoformat()}T00:00:00.000Z"},
             {"title": "Старое", "due": f"{(TODAY - timedelta(days=3)).isoformat()}T00:00:00.000Z"},
             {"title": "Без срока"}]
    text = gcal.agenda_text(TODAY, events, tasks)
    assert text == f"{I}сегодня 2 события: весь день Отпуск; в 10 часов Созвон. задача: Купить молоко. просрочено: Старое"


def test_agenda_empty():
    assert gcal.agenda_text(TOMORROW, [], []) == f"{I}завтра в календаре ничего нет"


@pytest.fixture
def google(monkeypatch):
    services = {"calendar": MagicMock(), "tasks": MagicMock()}
    monkeypatch.setattr(gcal, "_service", lambda name: services[name])
    return services


def test_add_event_body(google):
    result = gcal.calendar_add_event("Встреча", _on(TOMORROW, 15), duration_min=90, color="red")
    body = google["calendar"].events().insert.call_args.kwargs["body"]
    assert body["summary"] == "Встреча" and body["colorId"] == "11"
    start, end = datetime.fromisoformat(body["start"]["dateTime"]), datetime.fromisoformat(body["end"]["dateTime"])
    assert (start.hour, (end - start).seconds) == (15, 5400)
    assert result.startswith(I) and "завтра в 15 часов" in result and "красным" in result


def test_add_all_day_event(google):
    gcal.calendar_add_event("Отпуск", TOMORROW.isoformat())
    body = google["calendar"].events().insert.call_args.kwargs["body"]
    assert body["start"] == {"date": TOMORROW.isoformat()}
    assert body["end"] == {"date": (TOMORROW + timedelta(days=1)).isoformat()}


def test_task_with_time(google):
    result = gcal.task_add("оплатить интернет", _on(TOMORROW, 18))
    body = google["tasks"].tasks().insert.call_args.kwargs["body"]
    assert body == {"title": "Оплатить интернет", "due": f"{TOMORROW.isoformat()}T00:00:00.000Z", "notes": "Срок: 18:00"}
    assert "срок завтра до 18 часов" in result


def test_task_done_fuzzy(google):
    google["tasks"].tasks().list().execute.return_value = {"items": [
        {"id": "a", "title": "Позвонить врачу"}, {"id": "b", "title": "Купить молоко и хлеб"}]}
    result = gcal.task_done("купить молоко")
    assert google["tasks"].tasks().patch.call_args.kwargs["task"] == "b"
    assert "Купить молоко и хлеб" in result


def test_delete_asks_confirmation(google, monkeypatch):
    from core import daily
    monkeypatch.setattr(daily, "CONFIRM_DANGEROUS", True)
    google["calendar"].events().list().execute.return_value = {"items": [
        {**_event("Встреча с врачом", TOMORROW, 15), "id": "e1"}]}
    result = gcal.calendar_delete("с врачом")
    assert result.startswith(R) and "Встреча с врачом завтра в 15 часов" in result
    assert daily._pending is not None
    google["calendar"].events().delete.assert_not_called()       # без «да» не удаляю
    daily.clear_pending()


def test_not_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(gcal, "_services", {})
    monkeypatch.setattr(gcal, "GCAL_TOKEN_FILE", tmp_path / "token.json")
    monkeypatch.setattr(gcal, "GCAL_CREDENTIALS_FILE", tmp_path / "нет.json")
    result = gcal.task_list()
    assert result.startswith(F) and "google_credentials.json" in result
