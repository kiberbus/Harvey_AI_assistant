"""
Отчёт по harvey.log: что Харви не поняла сама (ушло в ИИ) и что не получилось.
Самые частые «непонятые» фразы — первые кандидаты в phrases.py: они начнут выполняться
мгновенно и без ошибок ИИ.

Запуск:  .venv\\Scripts\\python log_report.py        (или пункт «Отчёт по логу» в меню трея)
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from config import LOG_FILE

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) [\d:]+ \[([^\]]+)\] (.*)$")


def _log_lines() -> list[str]:
    files = [Path(f"{LOG_FILE}.{i}") for i in (2, 1)] + [Path(LOG_FILE)]      # от старых к новым
    lines: list[str] = []
    for path in files:
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines


def build_report(top: int = 25) -> str:
    heard = 0
    to_llm: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    days: set[str] = set()
    for line in _log_lines():
        m = LINE_RE.match(line)
        if not m:
            continue
        day, tag, text = m.groups()
        days.add(day)
        if tag in ("Без ИИ", "К ИИ"):
            heard += 1
        if tag == "К ИИ":
            to_llm[text.strip()] += 1
        elif tag == "Результат" and ": !" in text:
            failed[text.replace(": !", ": ", 1)] += 1
        elif tag == "Ошибка":
            errors[re.sub(r"\(.*?\)", "(…)", text)[:120]] += 1

    out = [f"Отчёт по логу Харви ({len(days)} дн., команд: {heard})", ""]
    share = f"{100 * sum(to_llm.values()) / heard:.0f}%" if heard else "—"
    out += [f"── Ушло в ИИ (не поняла сама): {sum(to_llm.values())} из {heard} ({share}) ──",
            "   Частые — добавьте их формулировки в phrases.py:"]
    out += [f"   {n:>3} × {text}" for text, n in to_llm.most_common(top)] or ["   нет"]
    out += ["", f"── Не получилось: {sum(failed.values())} ──"]
    out += [f"   {n:>3} × {text}" for text, n in failed.most_common(top)] or ["   нет"]
    out += ["", f"── Ошибки в коде: {sum(errors.values())} ──"]
    out += [f"   {n:>3} × {text}" for text, n in errors.most_common(top)] or ["   нет"]
    return "\n".join(out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(build_report())
