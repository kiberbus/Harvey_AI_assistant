"""Файлы и папки в проводнике: создать, открыть, выделить, удалить в корзину, закрыть окна папок.

Работаю с папкой из окна проводника на переднем плане, иначе - из самого верхнего окна проводника,
иначе - с рабочим столом. Окна и вкладки беру из Shell.Application: у каждой вкладки свой путь.
Удаляю только в корзину и только после «да», а «отмени» возвращает удалённое из корзины."""

from __future__ import annotations

import ctypes
import difflib
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from config import (
    FOLDERS,
    TEXT_EDITOR,
)
from phrases import (
    FOLDER_ALIASES,
)
from core.util import (
    END,
    FAIL,
    INFO,
    _plural,
    log,
)
from core.winapi import (
    SW_RESTORE,
    WM_CLOSE,
    _force_foreground,
    _top_windows,
    _user32,
    com_call,
    window_class,
)
from core.daily import (
    ask_confirm,
)
from core import steam, system

EXPLORER = "CabinetWClass"
DESKTOP_CLASSES = ("Progman", "WorkerW")
EXPLORER_NAMES = {"проводник", "explorer", "file explorer"}       # «закрой проводник» - все окна папок
SVSI_SELECT, SVSI_DESELECTOTHERS, SVSI_ENSUREVISIBLE, SVSI_FOCUSED = 1, 4, 8, 16
_SHOW = SVSI_SELECT | SVSI_DESELECTOTHERS | SVSI_ENSUREVISIBLE | SVSI_FOCUSED
CSIDL_DESKTOP, CSIDL_BITBUCKET = 0, 10
RECENT_SEC = 120          # «открой его» после «создай файл» - столько секунд это про созданный файл
# «Открыть» такой файл - значит запустить. Открываю его в редакторе, а запускает пусть человек сам
SCRIPT_EXTS = {".py", ".pyw", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".sh"}
LAUNCH_EXTS = {".lnk", ".url", ".exe", ".msi"}      # ярлыки и программы открываются как двойным щелчком
# Названия, как у пункта «Создать» в проводнике
DEFAULT_NAMES = {"": "Новая папка", "txt": "Новый текстовый документ", "docx": "Новый документ Microsoft Word",
                 "xlsx": "Новый лист Microsoft Excel", "pptx": "Новая презентация Microsoft PowerPoint"}


@dataclass
class _Tab:
    window: object        # окно из Shell.Application: у каждой вкладки проводника своё
    hwnd: int
    path: str
    name: str


@dataclass
class _Place:
    """Где работаю: путь, вкладка проводника с этой папкой (если она открыта) и как сказать «где»."""
    path: str
    tab: _Tab | None
    label: str
    at: str               # «в папке «Загрузки»», «на рабочем столе»


def _with_shell(fn, *args):
    """fn(shell, *args) с Shell.Application внутри своего COM (winapi.com_call)."""
    def run():
        import comtypes.client

        return fn(comtypes.client.CreateObject("Shell.Application", dynamic=True), *args)

    return com_call(run)


def _explorer_windows() -> list[tuple[int, str]]:
    """Окна проводника (hwnd, заголовок) сверху вниз, свёрнутые - в конце."""
    found = [(hwnd, title) for hwnd, title, cls, _pid in _top_windows() if cls == EXPLORER]
    return sorted(found, key=lambda w: bool(_user32.IsIconic(w[0])))


def _tabs(shell) -> list[_Tab]:
    tabs = []
    windows = shell.Windows()
    for i in range(windows.Count):
        try:
            w = windows.Item(i)
            if w is not None and str(w.FullName).lower().endswith("explorer.exe"):
                tabs.append(_Tab(w, int(w.HWND), str(w.Document.Folder.Self.Path), str(w.LocationName)))
        except Exception:                  # окно как раз закрывается или ещё грузится
            continue
    return tabs


def _active_tab(tabs: list[_Tab], hwnd: int, title: str) -> _Tab | None:
    """Из вкладок одного окна активна та, чьё имя в заголовке: «Загрузки — проводник»."""
    mine = [t for t in tabs if t.hwnd == hwnd]
    for tab in mine:
        if title == tab.name or title.startswith((tab.name + " — ", tab.name + " - ")):
            return tab
    return mine[0] if mine else None


def _front_tab(shell, tabs: list[_Tab] | None = None) -> _Tab | None:
    """Вкладка проводника на переднем плане, иначе - в самом верхнем окне проводника."""
    tabs = _tabs(shell) if tabs is None else tabs
    fg = _user32.GetForegroundWindow()
    for hwnd, title in sorted(_explorer_windows(), key=lambda w: w[0] != fg):
        tab = _active_tab(tabs, hwnd, title)
        if tab:
            return tab
    return None


def _same(a: str, b: str) -> bool:
    return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _place(shell, where: str = "") -> _Place:
    """Названная папка (where - ключ FOLDERS), иначе вкладка проводника впереди или сверху.
    Если папки не открыты или впереди рабочий стол - рабочий стол."""
    tabs = _tabs(shell)
    desktop = str(shell.NameSpace(CSIDL_DESKTOP).Self.Path)
    if where:
        path = desktop if where == "desktop" else FOLDERS.get(where, "")
        tab = next((t for t in tabs if _same(t.path, path)), None)
        if _same(path, desktop):
            return _Place(path, tab, "рабочий стол", "на рабочем столе")
        label = tab.name if tab else Path(path).name
        return _Place(path, tab, label, f"в папке «{label}»")
    if window_class(_user32.GetForegroundWindow()) not in DESKTOP_CLASSES:
        tab = _front_tab(shell, tabs)
        if tab:
            return _Place(tab.path, tab, tab.name, f"в папке «{tab.name}»")
    return _Place(desktop, None, "рабочий стол", "на рабочем столе")


def _bring(hwnd: int) -> None:
    if _user32.GetForegroundWindow() != hwnd:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _force_foreground(hwnd)


# --- как найти файл по названию ---

_SPOKEN_DOT_RE = re.compile(r"\s*\bточка\s+")
_QUOTES = " .,!?;:«»\"'“”„"


def _spoken_name(name: str) -> str:
    """«main точка py» → «main.py», без кавычек и знаков по краям."""
    return _SPOKEN_DOT_RE.sub(".", name.strip()).strip(_QUOTES)


def _spoken(path: str) -> str:
    """Как назвать вслух: имя без расширения («отчёт», а не «отчёт.docx»)."""
    p = Path(path)
    return p.name if os.path.isdir(path) else p.stem


def _key(text: str) -> str:
    """Для сравнения латиницей, без регистра и пробелов: «мейн» ≈ «main», «Список-покупок» = «список покупок»."""
    return steam._norm(text).replace(" ", "")


def _find(folder: str, spoken: str, want_dir: bool | None = None) -> str | None:
    """Файл или папка в folder по названию, как его сказали: сначала точно, потом похоже.
    Похожее подходит и для удаления: перед ним всё равно спрашиваю, назвав файл."""
    try:
        entries = [e for e in os.scandir(folder) if want_dir is None or e.is_dir() == want_dir]
    except OSError:
        return None
    spoken = _spoken_name(spoken)
    low = spoken.lower().replace("ё", "е")
    names = {e.path: (e.name, e.name if e.is_dir() else Path(e.name).stem) for e in entries}
    for path, (full, stem) in names.items():
        if low in (full.lower().replace("ё", "е"), stem.lower().replace("ё", "е")):
            return path
    key = _key(spoken)
    if not key:
        return None
    best, score = None, 0.0
    for path, (full, stem) in names.items():
        full_key, stem_key = _key(full), _key(stem)
        if key in (full_key, stem_key):
            return path
        ratio = max(difflib.SequenceMatcher(None, key, stem_key).ratio(),
                    difflib.SequenceMatcher(None, key, full_key).ratio())
        if len(key) >= 3 and stem_key.startswith(key):        # «отчёт» - «Отчёт за сентябрь»
            ratio = max(ratio, 0.8)
        if ratio > score:
            best, score = path, ratio
    return best if score >= 0.75 else None


def _not_found(name: str, folder: bool, place: _Place) -> str:
    return f"{FAIL}{'папки' if folder else 'файла'} «{_spoken_name(name)}» {place.at} нет"


# --- выделение в окне проводника ---

def _select(tab: _Tab, names: list[str], timeout: float = 1.0) -> int:
    """Выделяет элементы по именам и прокручивает к первому. Новый файл появляется в окне не сразу,
    поэтому пробую до timeout секунд. Возвращает, сколько выделено."""
    doc = tab.window.Document
    deadline = time.monotonic() + timeout
    while True:
        try:
            items = [item for item in (doc.Folder.ParseName(name) for name in names) if item is not None]
            for i, item in enumerate(items):
                doc.SelectItem(item, _SHOW if i == 0 else SVSI_SELECT)
            count = doc.SelectedItems().Count
        except Exception:
            count = 0
        if count >= len(names) or time.monotonic() > deadline:
            return count
        time.sleep(0.1)


def _selected_paths(tab: _Tab) -> list[str]:
    items = tab.window.Document.SelectedItems()
    return [str(items.Item(i).Path) for i in range(items.Count)]


def select_all(folder: bool = False) -> str:
    """«Выдели всё»: в проводнике - все файлы папки, в остальных окнах - Ctrl+A.
    folder=True («выдели все файлы») - в окне проводника, даже если впереди другое окно."""
    if not folder and window_class(_user32.GetForegroundWindow()) != EXPLORER:
        return system.shortcut("select_all")
    return _with_shell(_select_all)


def _select_all(shell) -> str:
    """Ctrl+A в проводнике выделяет текст, если фокус в адресной строке, поэтому выделяю через окно папки."""
    tab = _front_tab(shell)
    if tab is None:
        return f"{FAIL}папка не открыта"
    _bring(tab.hwnd)
    doc = tab.window.Document
    items = doc.Folder.Items()
    if not items.Count:
        return f"{INFO}папка «{tab.name}» пустая"
    for i in range(items.Count):
        doc.SelectItem(items.Item(i), SVSI_SELECT | (SVSI_DESELECTOTHERS if i == 0 else 0))
    return f"выделил{END} всё в папке «{tab.name}»"


def select_file(name: str, where: str = "", folder: bool = False) -> str:
    """«Выдели файл отчёт», «найди файл main»: показываю файл в окне проводника."""
    return _with_shell(_select_file, name or "", where or "", bool(folder))


def _select_file(shell, name: str, where: str, folder: bool) -> str:
    place = _place(shell, where)
    path = _find(place.path, name, True if folder else None)
    if path is None:
        return _not_found(name, folder, place)
    if place.tab is None:                  # папка не открыта - открываю её с этим файлом, как «Показать в папке»
        subprocess.Popen(["explorer.exe", "/select,", path])
        return f"показал{END} «{Path(path).name}»"
    _bring(place.tab.hwnd)
    _select(place.tab, [Path(path).name])
    return f"выделил{END} «{Path(path).name}»"


# --- создать ---

_created: dict = {}       # последний созданный файл {"path", "at"} - для «открой его»
SHCNE_CREATE, SHCNE_MKDIR, SHCNF_PATHW, SHCNF_FLUSH = 0x2, 0x8, 0x5, 0x1000
_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXT_RE = re.compile(r"\.([a-z][a-z0-9]{0,4})$", re.IGNORECASE)


def recent_created() -> str | None:
    """Файл, созданный только что («создай файл, открой его»), или None."""
    path = _created.get("path")
    if path and time.time() - _created["at"] <= RECENT_SEC and os.path.exists(path):
        return path
    return None


def _template(ext: str) -> str | None:
    """Пустой файл из пункта «Создать» проводника: пустой docx Word не откроет, ему нужен шаблон."""
    import winreg

    key = f".{ext}"
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key) as k:
            prog = winreg.QueryValueEx(k, "")[0]
    except OSError:
        prog = ""
    for sub in ([f"{key}\\{prog}\\ShellNew"] if prog else []) + [f"{key}\\ShellNew"]:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as k:
                name = winreg.QueryValueEx(k, "FileName")[0]
        except OSError:
            continue
        path = name if os.path.isabs(name) else os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "ShellNew", name)
        if os.path.isfile(path):
            return path
    return None


def _free_path(folder: str, stem: str, ext: str) -> str:
    """«Новый текстовый документ (2).txt», если такой уже есть - как в проводнике."""
    suffix = f".{ext}" if ext else ""
    path, n = os.path.join(folder, stem + suffix), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{stem} ({n}){suffix}"), n + 1
    return path


def create_file(ext: str = "txt", name: str = "", where: str = "") -> str:
    """«Создай текстовый файл», «создай питон файл main», «создай папку проекты» (ext="" - папка).
    Новый файл выделяю в окне проводника, как это делает сам проводник."""
    return _with_shell(_create, (ext or "").strip(". ").lower(), name or "", where or "")


def _create(shell, ext: str, name: str, where: str) -> str:
    place = _place(shell, where)
    if not os.path.isdir(place.path):
        return f"{FAIL}в «{place.label}» нельзя создать файл, откройте обычную папку"
    name = _BAD_CHARS_RE.sub("", _spoken_name(name)).strip(" .")
    m = _EXT_RE.search(name) if ext else None         # «main.py» - расширение уже в названии
    if m:
        name, ext = name[:m.start()].strip(" ."), m.group(1).lower()
    path = _free_path(place.path, name or DEFAULT_NAMES.get(ext, "Новый файл"), ext)
    template = _template(ext) if ext else None
    if not ext:
        os.mkdir(path)
    elif template:
        shutil.copyfile(template, path)
    else:
        open(path, "x", encoding="utf-8").close()
    # Без этого окно проводника замечает новый файл через секунды, и выделить его нечем
    ctypes.windll.shell32.SHChangeNotify(SHCNE_CREATE if ext else SHCNE_MKDIR, SHCNF_PATHW | SHCNF_FLUSH,
                                         ctypes.c_wchar_p(path), None)
    _created.update(path=path, at=time.time())
    what = "файл" if ext else "папку"
    if place.tab is None:                  # окна с этой папкой нет - говорю, где искать
        return f"{INFO}создал{END} {what} «{_spoken(path)}» {place.at}"
    if not _select(place.tab, [Path(path).name]):
        log("Файлы", f"не выделил{END} новый «{Path(path).name}» в окне")
    return f"создал{END} {what} «{Path(path).name}»"


# --- открыть ---

def _has_program(path: str) -> bool:
    """Есть ли программа для файла (иначе Windows покажет окно «Каким образом вы хотите открыть»)."""
    ext = Path(path).suffix
    if not ext:
        return False
    size, buf = wintypes.DWORD(520), ctypes.create_unicode_buffer(520)
    if ctypes.windll.shlwapi.AssocQueryStringW(0, 2, ext, "open", buf, ctypes.byref(size)) != 0:   # 2 - программа
        return False
    return not buf.value.lower().endswith("openwith.exe")


def _editor() -> str:
    """Чем открыть код: TEXT_EDITOR из config.py, иначе VS Code, если он установлен, иначе Блокнот."""
    if TEXT_EDITOR:
        return TEXT_EDITOR
    for base in (os.environ.get("LOCALAPPDATA", "") + r"\Programs", os.environ.get("ProgramFiles", "")):
        code = Path(base) / "Microsoft VS Code" / "Code.exe"
        if base and code.is_file():
            return str(code)
    return "notepad.exe"


def _launch(path: str, tab: _Tab | None) -> str:
    """Папку открываю в том же окне проводника, код и файлы без программы - в редакторе, остальное -
    как двойным щелчком."""
    if os.path.isdir(path):
        if tab is not None:
            tab.window.Navigate2(path)
        else:
            os.startfile(path)
        return f"открыл{END} папку «{Path(path).name}»"
    suffix = Path(path).suffix.lower()
    if suffix in SCRIPT_EXTS or (suffix not in LAUNCH_EXTS and not _has_program(path)):
        subprocess.Popen([_editor(), path])
    else:
        os.startfile(path)
    return f"открыл{END} «{Path(path).name}»"


def open_file(name: str = "", where: str = "", folder: bool = False) -> str:
    """«Открой файл отчёт», «открой папку проекты». Без названия - только что созданный файл
    или выделенный в проводнике."""
    return _with_shell(_open, name or "", where or "", bool(folder))


def _open(shell, name: str, where: str, folder: bool) -> str:
    if not name.strip():
        path = recent_created()
        if path:
            return _launch(path, _front_tab(shell))
        tab = _front_tab(shell)
        selected = _selected_paths(tab) if tab else []
        if not selected:
            return f"{INFO}скажите, какой файл открыть, например: открой файл отчёт"
        return _launch(selected[0], tab)
    place = _place(shell, where)
    path = _find(place.path, name, True if folder else None)
    if path is None:
        return _not_found(name, folder, place)
    return _launch(path, place.tab)


# --- удалить: только в корзину и после «да» ---

FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
FOF_WANTNUKEWARNING = 0x4000       # корзины на диске нет - Windows сама спросит, удалять ли насовсем


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def _ask_recycle(paths: list[str]) -> str:
    if len(paths) == 1:
        what = f"{'папку' if os.path.isdir(paths[0]) else 'файл'} «{_spoken(paths[0])}»"
    else:
        forms = (("файл", "файла", "файлов") if all(os.path.isfile(p) for p in paths)
                 else ("элемент", "элемента", "элементов"))
        what = f"{len(paths)} {_plural(len(paths), *forms)}"
    return ask_confirm("recycle", {"paths": paths}, f"Удалить в корзину {what}?")


def delete_file(name: str = "", where: str = "", folder: bool = False) -> str:
    """«Удали файл отчёт»: спрашиваю «да/нет», на «да» - в корзину. Без названия - выделенное в проводнике."""
    return _with_shell(_delete, name or "", where or "", bool(folder))


def _delete(shell, name: str, where: str, folder: bool) -> str:
    if not name.strip():
        tab = _front_tab(shell)
        paths = _selected_paths(tab) if tab else []
        return _ask_recycle(paths) if paths else f"{INFO}ничего не выделено"
    place = _place(shell, where)
    path = _find(place.path, name, True if folder else None)
    if path is None:
        return _not_found(name, folder, place)
    return _ask_recycle([path])


def delete_selected(everything: bool = False) -> str:
    """«Удали», «удали выделенное»: в проводнике - выделенные файлы в корзину после «да», в остальных
    окнах - клавиша Delete. everything («удали всё») - вся папка, а в остальных окнах - очистить поле."""
    if window_class(_user32.GetForegroundWindow()) != EXPLORER:
        return system.shortcut("clear_field" if everything else "delete")
    return _with_shell(_delete_selected, bool(everything))


def _delete_selected(shell, everything: bool) -> str:
    tab = _front_tab(shell)
    if tab is None:
        return f"{FAIL}папка не открыта"
    if everything:
        items = tab.window.Document.Folder.Items()
        paths = [str(items.Item(i).Path) for i in range(items.Count)]
    else:
        paths = _selected_paths(tab)
    paths = [p for p in paths if os.path.exists(p)]       # у «Этого компьютера» и корзины путей нет
    if not paths:
        return f"{INFO}папка пустая" if everything else f"{INFO}ничего не выделено"
    return _ask_recycle(paths)


def recycle(paths: list[str]) -> str:
    """В корзину - после «да» на вопрос из _ask_recycle."""
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return f"{FAIL}удалять уже нечего"
    names = "\0".join(paths) + "\0\0"                 # список путей кончается двумя нулями
    buf = (ctypes.c_wchar * len(names))(*names)
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, ctypes.addressof(buf), None,
                          FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING,
                          False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code:
        log("Файлы", f"SHFileOperation: код {code:#x}")
        return f"{FAIL}не получилось удалить"
    if op.fAnyOperationsAborted:
        return f"{INFO}удаление отменено"
    what = (f"«{Path(paths[0]).stem}»" if len(paths) == 1
            else f"{len(paths)} {_plural(len(paths), 'элемент', 'элемента', 'элементов')}")
    return f"удалил{END} в корзину {what}"


def restore(paths: list[str]) -> str:
    """«Отмени» после удаления: возвращаю из корзины то, что удалила Харви."""
    return _with_shell(_restore, list(paths))


def _restore(shell, paths: list[str]) -> str:
    # если в проводнике скрыты расширения, в корзине имя без них - сравниваю и так, и так
    wanted = {os.path.normcase(p): p for p in paths} | {os.path.normcase(os.path.splitext(p)[0]): p for p in paths}
    found: dict[str, tuple] = {}
    items = shell.NameSpace(CSIDL_BITBUCKET).Items()
    for i in range(items.Count):
        item = items.Item(i)
        try:
            origin = os.path.normcase(os.path.join(str(item.ExtendedProperty("System.Recycle.DeletedFrom")),
                                                   str(item.Name)))
            deleted = str(item.ExtendedProperty("System.Recycle.DateDeleted"))
        except Exception:
            continue
        path = wanted.get(origin)
        if path and (path not in found or deleted >= found[path][0]):   # удаляли не раз - беру последнее
            found[path] = (deleted, item)
    if not found:
        return f"{FAIL}в корзине этого уже нет"
    for _deleted, item in found.values():
        item.InvokeVerb("undelete")
    first = next(iter(found))
    what = f"«{Path(first).stem}»" if len(found) == 1 else f"{len(found)} {_plural(len(found), 'элемент', 'элемента', 'элементов')}"
    return f"вернул{END} из корзины {what}"


# --- закрыть окна папок ---

last_closed: list[str] = []      # пути закрытых папок - для «отмени»


def _norm_name(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower().replace("ё", "е"))


def _folder_key(low: str) -> str | None:
    return FOLDER_ALIASES.get(low) or (low if low in FOLDERS else None)


def _is_folder(tab: _Tab, name: str) -> bool:
    """Вкладка с папкой name? «Загрузки» узнаю по пути и всем её названиям, свою папку - по имени."""
    low = name.strip().lower()
    names = {_norm_name(low)}
    key = _folder_key(low)
    if key:
        if _same(tab.path, FOLDERS[key]):
            return True
        names |= {_norm_name(alias) for alias, k in FOLDER_ALIASES.items() if k == key} | {_norm_name(key)}
    return bool(names & {_norm_name(tab.name), _norm_name(Path(tab.path).name)})


def close_folder(name: str = "", everything: bool = False) -> str:
    """«Закрой папку» - окно проводника впереди или верхнее, «закрой папку загрузки» - по названию,
    «закрой проводник» (everything) - все окна проводника. Окно закрываю вместе со всеми вкладками."""
    if not _explorer_windows():
        return f"{INFO}папка «{name}» не открыта" if name else f"{INFO}окна проводника не открыты"
    return _with_shell(_close, name or "", bool(everything))


def _close(shell, name: str, everything: bool) -> str:
    tabs = _tabs(shell)
    if everything:
        hwnds = [hwnd for hwnd, _title in _explorer_windows()]
    elif name:
        hwnds = list(dict.fromkeys(t.hwnd for t in tabs if _is_folder(t, name)))
    else:
        tab = _front_tab(shell, tabs)
        hwnds, name = ([tab.hwnd], tab.name) if tab else ([], "")
    if not hwnds:
        return f"{INFO}папка «{name}» не открыта"
    last_closed[:] = [t.path for t in tabs if t.hwnd in hwnds]
    for hwnd in hwnds:
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return f"закрыл{END} проводник" if everything else f"закрыл{END} папку {name}"


def close_named(name: str) -> str | None:
    """Для «закрой X»: «закрой загрузки», «закрой проводник». None - это не папка (приложение или сайт)."""
    low = name.strip().lower()
    if low in EXPLORER_NAMES:
        return close_folder(everything=True)
    if _folder_key(low) is None:           # своя папка («закрой Слуга») - только если такое окно открыто
        if not _explorer_windows() or not _with_shell(lambda shell: any(_is_folder(t, name) for t in _tabs(shell))):
            return None
    return close_folder(name)


def reopen(paths: list[str]) -> str:
    """«Отмени» после «закрой папку»: открываю закрытые папки снова."""
    if not paths:
        return f"{FAIL}не знаю, какие папки открыть"
    for path in paths:
        if os.path.isdir(path):
            os.startfile(path)
        else:                              # «Этот компьютер» - ::{GUID}
            subprocess.Popen(["explorer.exe", path])
    return f"открыл{END} обратно {'папку' if len(paths) == 1 else 'папки'}"
