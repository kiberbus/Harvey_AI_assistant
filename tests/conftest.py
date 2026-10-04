"""Общие заготовки для тестов: Харви импортируется без микрофона, моделей и PowerShell."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harvey  # noqa: E402

# Список приложений из меню «Пуск» подменяем: тесты не должны зависеть от того, что установлено
harvey._apps_cache = [
    {"Name": "Telegram", "AppID": "telegram"},
    {"Name": "Яндекс Музыка", "AppID": "ru.yandex.desktop.music"},
    {"Name": "Claude", "AppID": "Claude_pzs8sxrjxfjjc!Claude"},
    {"Name": "Notepad", "AppID": "notepad"},
]


@pytest.fixture
def calls(monkeypatch):
    """Вместо настоящих действий записываем, какой инструмент с какими аргументами вызван бы."""
    recorded: list[tuple[str, dict]] = []

    def fake_execute(name, args):
        recorded.append((name, dict(args)))
        return "ok"

    monkeypatch.setattr(harvey, "execute_tool", fake_execute)
    return recorded


@pytest.fixture
def run(calls):
    """run("музыка стоп") → [("media", {...})] — что Харви сделала бы на эту фразу без ИИ.
    None — фраза ушла бы в ИИ."""
    def _run(text: str):
        calls.clear()
        actions = harvey.parse_all(text.lower().strip(harvey.PUNCT))
        if actions is None:
            return None
        results = [action() for action in actions]
        return list(calls) or [r for r in results if r != "ok"]
    return _run
