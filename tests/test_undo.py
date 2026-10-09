"""Стек отмены: «отмени», «верни громкость как было», «открой обратно»."""

import pytest

from core import apps, audio, system, tools, undo


@pytest.fixture(autouse=True)
def fresh_stack():
    undo.clear()
    yield
    undo.clear()


@pytest.fixture
def keys(monkeypatch):
    """Вместо нажатий записываю, какие сочетания были бы нажаты."""
    pressed = []
    monkeypatch.setattr(system, "shortcut", lambda action: pressed.append(action) or f"нажал {action}")
    return pressed


@pytest.fixture
def volume(monkeypatch):
    """Громкость в памяти вместо настоящей."""
    state = {"level": 40, "muted": False}
    monkeypatch.setattr(audio, "volume_state", lambda: (state["level"], state["muted"]))
    monkeypatch.setattr(audio, "restore_volume", lambda level, muted: state.update(level=level, muted=muted))

    def set_volume(level):
        state.update(level=level, muted=False)
        return f"установил громкость на {level} процентов"

    monkeypatch.setitem(tools.FUNCTIONS, "set_volume", set_volume)
    return state


def test_undo_volume(volume):
    tools.execute_tool("set_volume", {"level": 90})
    assert volume["level"] == 90
    assert "40" in undo.undo()
    assert volume == {"level": 40, "muted": False}


def test_undo_without_actions_is_ctrl_z(keys):
    undo.undo()
    assert keys == ["undo"]


def test_undo_is_a_stack(volume, keys):
    tools.execute_tool("set_volume", {"level": 60})
    tools.execute_tool("set_volume", {"level": 80})
    undo.undo()
    assert volume["level"] == 60
    undo.undo()
    assert volume["level"] == 40
    undo.undo()                       # больше нечего - обычный Ctrl+Z
    assert keys == ["undo"]


def test_old_actions_are_not_undone(volume, keys, monkeypatch):
    tools.execute_tool("set_volume", {"level": 90})
    monkeypatch.setattr(undo.time, "time", lambda: 10 ** 12)
    undo.undo()
    assert volume["level"] == 90 and keys == ["undo"]


def test_failed_action_is_not_recorded(volume, keys, monkeypatch):
    monkeypatch.setitem(tools.FUNCTIONS, "set_volume", lambda level: "!не получилось")
    tools.execute_tool("set_volume", {"level": 90})
    undo.undo()
    assert keys == ["undo"]


def test_undo_by_kind(volume, monkeypatch):
    """«Верни громкость» - именно громкость, даже если потом что-то закрыли."""
    opened = []
    monkeypatch.setitem(tools.FUNCTIONS, "close_app", lambda name: f"закрыл {name}")
    monkeypatch.setattr(apps, "open_app", lambda name: opened.append(name) or f"открыл {name}")
    tools.execute_tool("set_volume", {"level": 90})
    tools.execute_tool("close_app", {"name": "telegram"})
    undo.undo("volume")
    assert volume["level"] == 40 and opened == []
    undo.undo()
    assert opened == ["telegram"]


def test_undo_kind_without_actions():
    assert undo.undo("brightness").startswith(undo.INFO)


DOWNLOADS = r"C:\Users\x\Downloads"


@pytest.mark.parametrize("result,reopened", [
    ("закрыл вкладку «YouTube»", ("key", "reopen_tab")),
    ("закрыл папку загрузки", ("folder", [DOWNLOADS])),     # по пути: «загрузки» - не ключ FOLDERS
    ("закрыл проводник", ("folder", [DOWNLOADS])),
    ("закрыл telegram", ("app", "telegram")),
])
def test_reopen_closed(monkeypatch, keys, result, reopened):
    from core import files
    done = []
    monkeypatch.setattr(files, "last_closed", [DOWNLOADS])
    monkeypatch.setattr(files, "reopen", lambda paths: done.append(("folder", paths)) or "ok")
    monkeypatch.setattr(apps, "open_app", lambda name: done.append(("app", name)) or "ok")
    name = {"key": "ютуб", "folder": "загрузки"}.get(reopened[0], reopened[1])
    undo.after("close_app", {"name": name}, None, result)
    undo.undo("close")
    assert (done or [("key", keys[0])]) == [reopened]


def test_deleted_file_comes_back_from_recycle_bin(monkeypatch):
    """«Удали файл отчёт» - «да» - «отмени»: файл возвращается из корзины."""
    from core import files
    restored = []
    monkeypatch.setattr(files, "restore", lambda paths: restored.append(paths) or "вернула")
    undo.after("recycle", {"paths": [r"C:\x\отчёт.txt"]}, None, "удалила в корзину «отчёт»")
    assert undo.undo() == "вернула" and restored == [[r"C:\x\отчёт.txt"]]
    assert undo.undo("delete").startswith(undo.INFO)        # больше ничего не удаляла


def test_pasted_text_undone_in_same_window(monkeypatch, keys):
    monkeypatch.setattr(undo._user32, "GetForegroundWindow", lambda: 0)
    undo.after("shortcut", {"action": "paste"}, None, "вставил")
    undo.after("shortcut", {"action": "copy"}, None, "скопировал")     # копирование не отменяется
    assert undo.undo("text") == f"убрал{undo.END} вставленный текст"
    assert keys == ["undo"]
    assert undo._stack == undo.deque(maxlen=undo.UNDO_DEPTH)


def test_closed_tabs_reopen(monkeypatch, keys):
    """«Закрой остальные вкладки» - «отмени»: раньше отмена не знала close_tab и жала Ctrl+Z."""
    monkeypatch.setitem(tools.FUNCTIONS, "close_tab",
                        lambda which="current", name="", index=0: "закрыла 3 вкладки, осталась «Почта»")
    monkeypatch.setattr(system, "press_chords", lambda action: keys.append(action) or "ок")
    tools.execute_tool("close_tab", {"which": "others"})
    undo.undo()
    assert keys == ["reopen_tab"] * 3
