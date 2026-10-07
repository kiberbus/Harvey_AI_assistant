"""Файлы в проводнике и любые клавиши - без настоящего проводника и клавиатуры."""

import os

import pytest

from core import files, parse, smart, system, util


@pytest.fixture
def folder(tmp_path):
    for name in ("main.py", "Отчёт за сентябрь.docx", "список покупок.txt"):
        (tmp_path / name).write_text("")
    (tmp_path / "проекты").mkdir()
    return tmp_path


@pytest.mark.parametrize("spoken,found", [
    ("main.py", "main.py"),
    ("main точка py", "main.py"),
    ("мейн", "main.py"),                         # Whisper написал по-русски
    ("Список покупок", "список покупок.txt"),
    ("список-покупок", "список покупок.txt"),
    ("отчет", "Отчёт за сентябрь.docx"),          # начало названия, «е» вместо «ё»
    ("проекты", "проекты"),
    ("котики", None),
])
def test_find_file_by_spoken_name(folder, spoken, found):
    path = files._find(str(folder), spoken)
    assert (os.path.basename(path) if path else None) == found


def test_find_only_folders(folder):
    assert files._find(str(folder), "main", want_dir=True) is None


@pytest.fixture
def place(folder, monkeypatch):
    """Работаю в folder, как будто это рабочий стол без окна проводника."""
    monkeypatch.setattr(files, "_with_shell", lambda fn, *args: fn(None, *args))
    monkeypatch.setattr(files, "_place", lambda shell, where="": files._Place(str(folder), None, "рабочий стол",
                                                                              "на рабочем столе"))
    monkeypatch.setattr(files, "_template", lambda ext: None)
    return folder


def test_create_file_names(place):
    assert files.create_file("txt") == f"{util.INFO}создал{util.END} файл «Новый текстовый документ» на рабочем столе"
    files.create_file("txt")
    files.create_file("txt", "бот.py")                        # расширение из названия
    files.create_file("", "проекты")                         # папка уже есть - «проекты (2)»
    names = set(os.listdir(place))
    assert {"Новый текстовый документ.txt", "Новый текстовый документ (2).txt", "бот.py", "проекты (2)"} <= names
    assert files.recent_created() == str(place / "проекты (2)")


def test_create_office_file_from_template(place, monkeypatch, tmp_path_factory):
    template = tmp_path_factory.mktemp("shellnew") / "word.docx"
    template.write_bytes(b"PK")
    monkeypatch.setattr(files, "_template", lambda ext: str(template) if ext == "docx" else None)
    files.create_file("docx", "отчёт")
    assert (place / "отчёт.docx").read_bytes() == b"PK"


def test_delete_asks_first_and_names_the_file(place, monkeypatch):
    asked = []
    monkeypatch.setattr(files, "ask_confirm", lambda name, args, question: asked.append((name, args, question)) or "=?")
    assert files.delete_file("список покупок") == "=?"
    assert asked == [("recycle", {"paths": [str(place / "список покупок.txt")]},
                      "Удалить в корзину файл «список покупок»?")]
    assert files.delete_file("котики").startswith(util.FAIL)
    assert (place / "список покупок.txt").exists()             # без «да» ничего не удалено


def test_open_script_in_editor_not_run(place, monkeypatch):
    launched = []
    monkeypatch.setattr(files.subprocess, "Popen", lambda cmd: launched.append(cmd))
    monkeypatch.setattr(files.os, "startfile", lambda path: launched.append(path), raising=False)
    monkeypatch.setattr(files, "_editor", lambda: "code")
    files.open_file("main")
    assert launched == [["code", str(place / "main.py")]]


def test_open_it_right_after_create(run, monkeypatch):
    monkeypatch.setattr(files, "recent_created", lambda: r"C:\x\Новый файл.py")
    assert run("открой его") == [("open_file", {})]
    monkeypatch.setattr(files, "recent_created", lambda: None)
    assert run("открой его") != [("open_file", {})]           # давно - «его» не про файл


@pytest.fixture
def pressed(monkeypatch):
    chords = []
    monkeypatch.setattr(system, "_chord", lambda *vks: chords.append(vks))
    monkeypatch.setattr("core.apps.foreground_is_mine", lambda: False)
    return chords


def test_press_keys(pressed):
    assert system.press_keys("ctrl+g") == f"нажал{util.END} Ctrl+G"
    assert pressed == [(system.CTRL, ord("G"))]
    pressed.clear()
    assert system.press_keys("Control+C, ctrl+v") == f"нажал{util.END} Ctrl+C, Ctrl+V"
    assert pressed == [(system.CTRL, ord("C")), (system.CTRL, ord("V"))]
    pressed.clear()
    assert system.press_keys("enter", times=3).endswith("Enter 3 раза")
    assert len(pressed) == 3
    pressed.clear()
    assert system.press_keys("ctrl+щ").startswith(util.FAIL) and pressed == []


def test_press_keys_not_in_own_console(monkeypatch, pressed):
    """Ctrl+C в консоли Харви остановил бы её саму."""
    monkeypatch.setattr("core.apps.foreground_is_mine", lambda: True)
    assert system.press_keys("ctrl+c").startswith(util.FAIL) and pressed == []


def test_select_all_quick_and_keys_wait():
    assert parse.is_quick_command("Харви, выдели все.")
    assert not parse.is_quick_command("Харви, нажми ctrl")      # сочетание ещё договаривают


def test_translate_to_japanese_is_selection():
    """Из лога: «переведи на японский» уходило вопросом в ИИ, и ответ по-японски не озвучивался."""
    assert smart._target_lang("переведи на японский", "привет") == "японский"
    assert smart._bare("переведи на японский", smart.SELECTION_ACTION_RES[1][1])
