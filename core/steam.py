"""Игры из библиотеки Steam: «запусти Marvel Rivals», «давай поиграем в риск оф рейн».

Список беру из файлов самого Steam: appmanifest_*.acf во всех папках библиотеки (libraryfolders.vdf).
Запускаю через steam://rungameid, как ярлык игры. Названия сравниваю в латинице: Whisper пишет
то «Marvel Rivals», то «марвел ривалс»."""

from __future__ import annotations

import difflib
import os
import re
import time
from pathlib import Path

from core.util import (  # noqa: F401
    END,
    FAIL,
    log,
)

_SKIP_APPIDS = {"228980"}          # Steamworks Common Redistributables - не игра
_CACHE_SEC = 60                    # новую игру замечу через минуту после установки
_cache: dict = {"at": 0.0, "games": []}

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


_NUMBERS = {"один": "1", "одна": "1", "два": "2", "две": "2", "три": "3", "четыре": "4", "пять": "5",
            "шесть": "6", "семь": "7", "восемь": "8", "девять": "9", "десять": "10"}
# Английские буквы, которые по-русски слышатся иначе: Wallpaper - «валлпейпер», Max - «макс»
_SOUNDS = (("ph", "f"), ("w", "v"), ("x", "ks"), ("c", "k"), ("j", "dzh"), ("y", "i"))


def _norm(name: str) -> str:
    """«Darkest Dungeon®» → «darkest dungeon», «марвел ривалс» → «marvel rivals», «риск оф рейн два» → «… 2».
    Обе стороны сравнения проходят одно и то же, поэтому грубая транслитерация не мешает."""
    text = name.lower().replace("'", "").replace("’", "")
    text = " ".join(_NUMBERS.get(word, word) for word in text.split()).translate(_TRANSLIT)
    for letters, sound in _SOUNDS:
        text = text.replace(letters, sound)
    text = re.sub(r"(\w)\1", r"\1", text)                    # «валлпейпер» - двойные буквы на слух не слышны
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _steam_dir() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
        return default if default.exists() else None


def _libraries(steam: Path) -> list[Path]:
    """Папки библиотеки: сама папка Steam и пути из libraryfolders.vdf (там \\ удвоены)."""
    libraries = [steam]
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    except OSError:
        pass
    return list(dict.fromkeys(libraries))       # папка Steam есть и в списке - без повторов


def _scan() -> list[dict]:
    steam = _steam_dir()
    if steam is None:
        return []
    found = []
    for library in _libraries(steam):
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]+)"', text)
            if appid and name and appid.group(1) not in _SKIP_APPIDS:
                title = re.sub(r"[®™©]", "", name.group(1)).strip()
                found.append({"appid": appid.group(1), "name": title, "key": _norm(title)})
    return found


def games() -> list[dict]:
    """Установленные игры: {"appid", "name", "key" - название для сравнения}."""
    if time.time() - _cache["at"] > _CACHE_SEC:
        try:
            _cache["games"] = _scan()
        except Exception as e:
            log("Steam", f"не удалось прочитать библиотеку: {e}")
            _cache["games"] = []
        _cache["at"] = time.time()
    return _cache["games"]


def find_game(query: str) -> dict | None:
    """Игра по названию. Хватит и начала: «запусти дивинити» - Divinity: Original Sin 2."""
    q = _norm(query)
    if len(q) < 3:
        return None
    best, best_score = None, 0.0
    for game in games():
        start = " ".join(game["key"].split()[:len(q.split())])
        score = max(difflib.SequenceMatcher(None, q, variant).ratio() for variant in (game["key"], start))
        if score > best_score:
            best, best_score = game, score
    return best if best_score >= 0.75 else None


def launch(name: str) -> str:
    game = find_game(name)
    if game is None:
        return f"{FAIL}игры «{name}» нет в библиотеке Steam"
    os.startfile(f"steam://rungameid/{game['appid']}")
    return f"запустил{END} {game['name']}"
