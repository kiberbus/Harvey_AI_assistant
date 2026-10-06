"""Отчёт по harvey.log: что ушло в ИИ, ошибки и задержки.

Самые частые непонятые фразы - первые кандидаты в phrases.py. Каждую фразу из лога заново
прогоняю через текущие правила (ничего не выполняя):
  ✓ - тогда ушла в ИИ, теперь понимается сама;
  регрессии - раньше понималась, теперь нет.

Запуск: python log_report.py (или пункт трея)."""

from __future__ import annotations

import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from config import LOG_FILE

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) [\d:]+ \[([^\]]+)\] (.*)$")
TIME_RE = re.compile(r"распознавание ([\d.]+) с, команда ([\d.]+) с")
ROUTES = {"Без ИИ": "без ИИ", "К ИИ": "через ИИ", "ИИ: текст": "вопрос к ИИ"}


def _log_lines() -> list[str]:
    files = [Path(f"{LOG_FILE}.{i}") for i in (2, 1)] + [Path(LOG_FILE)]      # от старых к новым
    lines: list[str] = []
    for path in files:
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines


def _local_checker() -> Callable[[str], bool] | None:
    """Понимает ли фразу текущий код без ИИ. None - код не импортировался."""
    try:
        from core import parse, smart
        from core.util import LAST_ACTION_RE, PUNCT, REPEAT_RE, R, SILENCE_RE
    except Exception:
        return None

    def understood(text: str) -> bool:
        low = text.lower().strip(PUNCT)
        try:
            if (R["exit"].search(low) or R["cancel_command"].search(low) or SILENCE_RE.match(low)
                    or REPEAT_RE.match(low) or LAST_ACTION_RE.match(low)):    # их handle_command берёт до правил
                return True
            return parse.parse_all(low) is not None or smart.parse(parse.fix_hearing(low)) is not None
        except Exception:
            return False
    return understood


def _percentiles(values: list[float]) -> str:
    if not values:
        return "—"
    values = sorted(values)
    p90 = values[min(len(values) - 1, int(len(values) * 0.9))]
    return f"медиана {statistics.median(values):.2f} с, 90% быстрее {p90:.2f} с"


def build_report(top: int = 25) -> str:
    heard = 0
    to_llm: Counter[str] = Counter()
    local: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    stt: list[float] = []
    run: dict[str, list[float]] = {route: [] for route in ROUTES.values()}
    days: set[str] = set()
    route = None
    for line in _log_lines():
        m = LINE_RE.match(line)
        if not m:
            continue
        day, tag, text = m.groups()
        days.add(day)
        if tag in ROUTES:
            route = ROUTES[tag]
        if tag in ("Без ИИ", "К ИИ"):
            heard += 1
        if tag == "К ИИ":
            to_llm[text.strip()] += 1
        elif tag == "Без ИИ":
            local[text.strip()] += 1
        elif tag == "Результат" and ": !" in text:
            failed[text.replace(": !", ": ", 1)] += 1
        elif tag == "Ошибка":
            errors[re.sub(r"\(.*?\)", "(…)", text)[:120]] += 1
        elif tag == "Время":
            t = TIME_RE.search(text)
            if t:
                stt.append(float(t.group(1)))
                if route:
                    run[route].append(float(t.group(2)))
                route = None

    understood = _local_checker()
    fixed = {text for text in to_llm if understood and understood(text)}
    broken = [text for text in local if understood and not understood(text)]

    out = [f"Отчёт по логу Харви ({len(days)} дн., команд: {heard})", ""]
    share = f"{100 * sum(to_llm.values()) / heard:.0f}%" if heard else "—"
    out += [f"── Ушло в ИИ (не поняла сама): {sum(to_llm.values())} из {heard} ({share}) ──"]
    if understood:
        left = sum(n for text, n in to_llm.items() if text not in fixed)
        out += [f"   ✓ - теперь понимает сама; без ✓ осталось {left}."]
    out += ["   Частые - добавьте их формулировки в phrases.py:"]
    rows = sorted(to_llm.items(), key=lambda kv: (kv[0] in fixed, -kv[1]))   # сначала то, что ещё не исправлено
    out += [f"   {n:>3} × {'✓ ' if text in fixed else ''}{text}" for text, n in rows[:top]] or ["   нет"]
    if understood is None:
        out += ["   (проверить по текущим правилам не удалось: код Харви не импортировался)"]
    if broken:
        out += ["", f"── Регрессии: раньше понимала сама, теперь нет: {len(broken)} ──"]
        out += [f"   {local[text]:>3} × {text}" for text in broken[:top]]
    out += ["", f"── Не получилось: {sum(failed.values())} ──"]
    out += [f"   {n:>3} × {text}" for text, n in failed.most_common(top)] or ["   нет"]
    out += ["", f"── Ошибки в коде: {sum(errors.values())} ──"]
    out += [f"   {n:>3} × {text}" for text, n in errors.most_common(top)] or ["   нет"]
    if stt:
        out += ["", "── Задержки (от конца фразы) ──", f"   распознавание: {_percentiles(stt)}"]
        out += [f"   выполнение {name}: {_percentiles(values)}" for name, values in run.items() if values]
        out += ["   (выполнение через ИИ включает и время, пока Харви произносит ответ)"]
    return "\n".join(out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(build_report())
