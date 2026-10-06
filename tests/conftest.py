"""Общие заготовки для тестов: Харви импортируется без микрофона, моделей и PowerShell."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harvey  # noqa: E402,F401  - точка входа тоже должна импортироваться без ошибок
from core import apps, parse, tools, util  # noqa: E402

# Подменяю список приложений из Пуска, чтобы тесты не зависели от установленного
apps._apps_cache = [
    {"Name": "Telegram", "AppID": "telegram"},
    {"Name": "Яндекс Музыка", "AppID": "ru.yandex.desktop.music"},
    {"Name": "Claude", "AppID": "Claude_pzs8sxrjxfjjc!Claude"},
    {"Name": "Notepad", "AppID": "notepad"},
]


@pytest.fixture
def calls(monkeypatch):
    """Вместо настоящих действий записываю, что было бы вызвано."""
    recorded: list[tuple[str, dict]] = []

    def fake_execute(name, args):
        recorded.append((name, dict(args)))
        return "ok"

    monkeypatch.setattr(tools, "execute_tool", fake_execute)
    return recorded


@pytest.fixture
def run(calls):
    """run("музыка стоп") → [("media", {...})] - что Харви сделала бы на эту фразу без ИИ.
    None - фраза ушла бы в ИИ."""
    def _run(text: str):
        calls.clear()
        actions = parse.parse_all(text.lower().strip(util.PUNCT))
        if actions is None:
            return None
        results = [action() for action in actions]
        return list(calls) or [r for r in results if r != "ok"]
    return _run
