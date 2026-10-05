"""Вычисления без ИИ: проценты, арифметика, перевод единиц и валют.

«сколько будет 15 процентов от 3200», «2 плюс 2 умножить на 3», «корень из 144»,
«5 миль в километрах», «сколько сантиметров в дюйме», «100 фаренгейт в цельсии»,
«5 долларов в тенге», «доллар к тенге»."""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Callable

from config import *      # noqa: F401,F403
from phrases import *     # noqa: F401,F403
from core.util import (  # noqa: F401
    FAIL,
    INFO,
    _TENS,
    _UNITS,
    _plural,
    log,
)

NUM = r"-?\d+(?:\.\d+)?"

# ───────────────────────── ЧИСЛА ПРОПИСЬЮ → ЦИФРЫ ─────────────────────────
_HUNDREDS = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
             "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900}
_EXTRA = {"одна": 1, "одной": 1, "одного": 1, "одном": 1, "полтора": 1.5, "полторы": 1.5,
          # «15 процентов от трёх тысяч двухсот» — числа в родительном падеже
          "двух": 2, "трех": 3, "четырех": 4, "пяти": 5, "шести": 6, "семи": 7, "восьми": 8, "девяти": 9,
          "десяти": 10, "одиннадцати": 11, "двенадцати": 12, "тринадцати": 13, "четырнадцати": 14,
          "пятнадцати": 15, "шестнадцати": 16, "семнадцати": 17, "восемнадцати": 18, "девятнадцати": 19,
          "двадцати": 20, "тридцати": 30, "сорока": 40, "пятидесяти": 50, "шестидесяти": 60,
          "семидесяти": 70, "восьмидесяти": 80, "девяноста": 90, "ста": 100, "двухсот": 200, "трехсот": 300,
          "четырехсот": 400, "пятисот": 500, "шестисот": 600, "семисот": 700, "восьмисот": 800, "девятисот": 900}
_SCALES = (("тысяч", 1000), ("миллион", 10 ** 6), ("миллиард", 10 ** 9))
_SCALE_AFTER_DIGIT_RE = re.compile(rf"({NUM})\s+(тысяч\w*|миллион\w*|миллиард\w*)")


def _word_value(word: str) -> float | None:
    for table in (_HUNDREDS, _TENS, _UNITS, _EXTRA):
        if word in table:
            return table[word]
    return None


def _scale(word: str) -> int | None:
    for stem, value in _SCALES:
        if word.startswith(stem):
            return value
    return None


def _scale_after_digit(m: re.Match) -> str:
    return plain(float(m.group(1)) * _scale(m.group(2)))


def words_to_digits(text: str) -> str:
    """«три тысячи двести» → «3200», «пятнадцать процентов» → «15 процентов», «2 тысячи» → «2000»."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        total, group, used, j = 0.0, 0.0, 0, i
        while j < len(tokens):
            value, scale = _word_value(tokens[j]), _scale(tokens[j])
            if value is None and not used and j + 1 < len(tokens) and _scale(tokens[j + 1]) \
                    and re.fullmatch(NUM, tokens[j]):
                value = float(tokens[j])             # «2 миллиона», «2 тысячи триста» — цифра перед словом
            if value is not None:
                group += value
            elif scale is not None:
                total += (group or 1) * scale        # «тысяча» без числа — это 1000
                group = 0
            else:
                break
            used += 1
            j += 1
        if used:
            out.append(plain(total + group))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return _SCALE_AFTER_DIGIT_RE.sub(_scale_after_digit, " ".join(out))


def normalize(text: str) -> str:
    """Нижний регистр, числа цифрами, «3,5» → «3.5», «3 200» → «3200», «15%» → «15 процентов»."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"[,!?;:]", " ", text)
    text = re.sub(r"(?<=\d) (?=\d{3}\b)", "", text)
    text = re.sub(r"(?<=\d)\s*%", " процентов", text)
    return " ".join(words_to_digits(" ".join(text.split())).split())


# ───────────────────────── ВЫВОД ЧИСЕЛ ─────────────────────────
def plain(x: float) -> str:
    """Для разбора: 3200.0 → «3200», 0.5 → «0.5»."""
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else repr(x)


def spoken(x: float) -> str:
    """Для ответа: 8.04672 → «8,05», 0.000254 → «0,000254», 1e6 → «1000000»."""
    if abs(x - round(x)) < 1e-9 or abs(x) >= 1e6:
        return str(int(round(x)))
    if abs(x) >= 100:
        s = f"{x:.1f}"
    elif abs(x) >= 1:
        s = f"{x:.2f}"
    else:
        s = f"{x:.{min(9, 2 - math.floor(math.log10(abs(x))))}f}"   # три значащие цифры
    return s.rstrip("0").rstrip(".").replace(".", ",")


PERCENT = ("процент", "процента", "процентов")


def with_unit(x: float, forms: tuple[str, str, str]) -> str:
    """«1 километр», «2 километра», «5 километров», «8,05 километра» (дробь — как «двух»)."""
    text = spoken(x)
    if "," in text:
        return f"{text} {forms[1]}"
    return f"{text} {_plural(abs(int(text)), *forms)}"


# ───────────────────────── ПРОЦЕНТЫ И АРИФМЕТИКА ─────────────────────────
_PERCENT_OF_RE = re.compile(rf"^(?P<p>{NUM}) процент\w* (?:от|из) (?P<n>{NUM})$")
_PERCENT_ADD_RE = re.compile(rf"^(?P<n>{NUM}) (?P<op>плюс|минус) (?P<p>{NUM}) процент\w*$")
_PERCENT_SHARE_RE = re.compile(rf"^(?:сколько )?процент\w* (?:составляет |будет )?(?P<a>{NUM}) (?:от|из) (?P<b>{NUM})$")
_OPERATOR_RES = [   # у слов — границы слова («х» не должен срабатывать внутри «хорошо»), у знаков — нет
    (re.compile("|".join(rf"(?<!\w){alt}(?!\w)" if alt[0].isalpha() else alt for alt in words.split("|"))), sign)
    for words, sign in CALC_OPERATORS.items()
]
# Степень пишем как «^», чтобы замена «*» на умножение её не разорвала; в «**» превращаем в конце
_POWER_RES = [
    (re.compile(r"\s*в квадрате\b"), "^2"),
    (re.compile(r"\s*в кубе\b"), "^3"),
    (re.compile(rf"\s*в (?P<e>{NUM}) степени\b|\s*в степени (?P<e2>{NUM})"), None),
]
_ROOT_RE = re.compile(rf"(?:квадратный )?корень (?:из )?(?P<n>{NUM})")
_EXPR_RE = re.compile(r"^[\d.\s+\-*/^()]+$")
_ALLOWED = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("слишком большая степень")
        return _ALLOWED[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED:
        return _ALLOWED[type(node.op)](_eval(node.operand))
    raise ValueError("не арифметика")


def _expression(text: str) -> str | None:
    """«2 плюс 2 умножить на 3» → «2 + 2 * 3». Остались посторонние слова — None."""
    text = _ROOT_RE.sub(lambda m: f"({m.group('n')})^0.5", text)
    for rx, repl in _POWER_RES:
        text = rx.sub(lambda m, r=repl: r or f"^{m.group('e') or m.group('e2')}", text)
    for rx, sign in _OPERATOR_RES:
        text = rx.sub(f" {sign} ", text)
    text = " ".join(text.split())
    if not _EXPR_RE.match(text) or not re.search(r"\d", text) or not re.search(r"[+\-*/^]", text.lstrip("-")):
        return None
    return text.replace("^", "**")


def _percent(text: str) -> Callable[[], str] | None:
    m = _PERCENT_OF_RE.match(text)
    if m:
        p, n = float(m.group("p")), float(m.group("n"))
        return lambda: f"{INFO}{with_unit(p, PERCENT)} от {spoken(n)} — это {spoken(p * n / 100)}"
    m = _PERCENT_ADD_RE.match(text)
    if m:
        n, p, sign = float(m.group("n")), float(m.group("p")), 1 if m.group("op") == "плюс" else -1
        op = m.group("op")
        return lambda: f"{INFO}{spoken(n)} {op} {with_unit(p, PERCENT)} — это {spoken(n * (1 + sign * p / 100))}"
    m = _PERCENT_SHARE_RE.match(text)
    if m:
        a, b = float(m.group("a")), float(m.group("b"))
        if b == 0:
            return None
        return lambda: f"{INFO}{spoken(a)} от {spoken(b)} — это {with_unit(a * 100 / b, PERCENT)}"
    return None


def _arithmetic(text: str) -> Callable[[], str] | None:
    expr = _expression(text)
    if expr is None:
        return None
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except ZeroDivisionError:
        return lambda: f"{FAIL}на ноль делить нельзя"
    except Exception:
        return None
    if isinstance(value, complex) or math.isnan(value) or math.isinf(value):
        return None
    return lambda: f"{INFO}{spoken(value)}"


# ───────────────────────── ЕДИНИЦЫ ИЗМЕРЕНИЯ ─────────────────────────
_UNIT_TABLE = [(re.compile(pattern), category, factor, forms) for pattern, category, factor, forms in UNITS]
_U = "|".join(f"(?:{p})" for p, *_ in sorted(UNITS, key=lambda u: -len(u[0])))
_UNIT_A_RE = re.compile(rf"^(?:(?P<n>{NUM}) )?(?P<u1>{_U}) (?:в|во|на) (?P<u2>{_U})$")
_UNIT_B_RE = re.compile(rf"^(?P<u2>{_U}) (?:в|во) (?:(?P<n>{NUM}) )?(?P<u1>{_U})$")     # «сколько см в дюйме»


def _unit(word: str):
    for rx, category, factor, forms in _UNIT_TABLE:
        if rx.fullmatch(word):
            return category, factor, forms
    return None


def _temperature(value: float, src: str, dst: str) -> float:
    celsius = {"C": value, "F": (value - 32) * 5 / 9, "K": value - 273.15}[src]
    return {"C": celsius, "F": celsius * 9 / 5 + 32, "K": celsius + 273.15}[dst]


def _units(text: str, how_many: bool) -> Callable[[], str] | None:
    m = (_UNIT_B_RE.match(text) if how_many else None) or _UNIT_A_RE.match(text)
    if not m:
        return None
    src, dst = _unit(m.group("u1")), _unit(m.group("u2"))
    if not src or not dst or src[0] != dst[0] or src[2] == dst[2]:
        return None
    n = float(m.group("n") or 1)
    if src[0] == "temp":
        result = _temperature(n, src[1], dst[1])
    else:
        result = n * src[1] / dst[1]
    return lambda: f"{INFO}{with_unit(n, src[2])} — это {with_unit(result, dst[2])}"


# ───────────────────────── ВАЛЮТЫ ─────────────────────────
_CURRENCY_CONVERT_RE = re.compile(CURRENCY_CONVERT)


def _currency(word: str) -> str | None:
    for code, pattern in CURRENCY_WORDS.items():
        if re.match(pattern, word):
            return code
    return None


def _money(x: float) -> str:
    return str(round(x)) if abs(x) >= 100 else spoken(round(x, 2))


def _currency_convert(text: str) -> Callable[[], str] | None:
    m = _CURRENCY_CONVERT_RE.match(text)
    if not m:
        return None
    n = (m.group("n") or "").strip()
    if n and not re.fullmatch(NUM, n):
        return None
    src, dst = _currency(m.group("src")), _currency(m.group("dst"))
    if not src or not dst or src == dst:
        return None
    amount = float(n or 1)

    def run() -> str:
        from core.daily import _rates

        try:
            rates = _rates()
            value = amount * rates[dst] / rates[src]
        except Exception as e:
            log("Курс", f"ошибка: {e}")
            return f"{FAIL}не удалось узнать курс, проверьте интернет"
        money = _money(value)
        dst_word = CURRENCY_FORMS[dst][1] if "," in money else _plural(abs(int(money)), *CURRENCY_FORMS[dst])
        return f"{INFO}{with_unit(amount, CURRENCY_FORMS[src])} — это {money} {dst_word}"

    return run


# ───────────────────────── ВХОД ─────────────────────────
_PREFIX_RE = re.compile(CALC_PREFIX)


def parse(text: str) -> Callable[[], str] | None:
    """Вычисление, которое можно сделать без ИИ? Возвращает действие (строка ответа) или None."""
    text = normalize(text)
    m = _PREFIX_RE.match(text)
    how_many = bool(m) and m.group().strip().endswith("сколько")
    body = text[m.end():] if m else text
    return (_currency_convert(text) or _percent(body) or _units(body, how_many) or _arithmetic(body)
            or (_units(text, False) if m else None))


def calculate(text: str) -> str:
    action = parse(text)
    return action() if action else f"{FAIL}не смог{'ла' if FEMALE_VOICE else ''} посчитать"
