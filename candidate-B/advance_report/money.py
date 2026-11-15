"""Деньги: только Decimal с точностью до копейки, никаких float."""

from __future__ import annotations

import re
from decimal import Decimal

KOPECK = Decimal("0.01")
ZERO = Decimal("0.00")

# Неотрицательная сумма в рублях: ASCII-цифры, до 15 цифр целой части (заведомо больше
# любой реальной суммы, и Decimal не переполняется) и не больше двух знаков после точки.
_MONEY_RE = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,2})?")


def parse_money(text: str) -> Decimal:
    """Разбирает сумму из выгрузки ("600000.00") в Decimal с копейками.

    Отказывает во всём, что нельзя прочитать однозначно: пустое значение,
    минус, больше двух знаков после точки, запятая, пробелы внутри, экспонента.
    """
    value = text.strip()
    if not _MONEY_RE.fullmatch(value):
        raise ValueError(f"некорректная сумма {text!r}: ожидается вида 12345.67")
    return Decimal(value).quantize(KOPECK)


def fmt_ru(amount: Decimal) -> str:
    """Для консоли: 1234567.8 -> '1 234 567,80'."""
    _check(amount)
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def fmt_csv(amount: Decimal, decimal_comma: bool) -> str:
    """Для CSV: без разделителей тысяч, с запятой или точкой."""
    _check(amount)
    text = f"{amount:.2f}"
    return text.replace(".", ",") if decimal_comma else text


def _check(amount: Decimal) -> None:
    if not isinstance(amount, Decimal):  # защита от случайного float
        raise TypeError(f"деньги должны быть Decimal, получено {type(amount).__name__}")
