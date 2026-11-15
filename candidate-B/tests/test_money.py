from decimal import Decimal

import pytest

from advance_report.money import fmt_csv, fmt_ru, parse_money


@pytest.mark.parametrize("text, expected", [
    ("600000.00", Decimal("600000.00")),
    ("15000", Decimal("15000.00")),
    ("0.1", Decimal("0.10")),
    (" 42.05 ", Decimal("42.05")),
    ("0", Decimal("0.00")),
])
def test_parse_money_ok(text, expected):
    value = parse_money(text)
    assert value == expected
    assert value.as_tuple().exponent == -2  # всегда ровно копейки


@pytest.mark.parametrize("text", [
    "", " ", "-100.00", "100.001", "1e5", "100,00", "1 000.00", "abc", ".50", "NaN", "Infinity",
])
def test_parse_money_rejects_ambiguous(text):
    with pytest.raises(ValueError):
        parse_money(text)


def test_no_float_drift():
    # 0.1 + 0.2 во float = 0.30000000000000004; у нас — ровно 0.30.
    assert parse_money("0.10") + parse_money("0.20") == Decimal("0.30")
    total = sum((parse_money("0.01") for _ in range(100_000)), Decimal("0"))
    assert total == Decimal("1000.00")


def test_formatting():
    assert fmt_ru(Decimal("1234567.80")) == "1 234 567,80"
    assert fmt_ru(Decimal("0.00")) == "0,00"
    assert fmt_ru(Decimal("-5000.00")) == "-5 000,00"
    assert fmt_csv(Decimal("300000.00"), decimal_comma=True) == "300000,00"
    assert fmt_csv(Decimal("300000.00"), decimal_comma=False) == "300000.00"


def test_formatting_refuses_float():
    with pytest.raises(TypeError):
        fmt_ru(1.5)  # type: ignore[arg-type]
