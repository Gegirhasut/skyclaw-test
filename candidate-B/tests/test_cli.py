import csv
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from advance_report.cli import main

PROJECT = Path(__file__).resolve().parent.parent


def read_csv(path: Path, delimiter=";", encoding="utf-8-sig") -> list[dict]:
    with path.open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def money(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def test_report_on_real_data(real_data, tmp_path, capsys):
    assert main([str(real_data), "2026-11-10", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ОТЧЁТ ОБ ОПЛАТЕ РАБОТ НА 10.11.2026" in out
    for fragment in ["315 000,00", "185 000,00", "150 000,00", "75 000,00", "15 000,00",
                     "Итого                               690 000,00"]:
        assert fragment in out

    works = read_csv(tmp_path / "works_2026-11-10.csv")
    contracts = read_csv(tmp_path / "contracts_2026-11-10.csv")
    notes = read_csv(tmp_path / "notes_2026-11-10.csv")
    assert len(works) == 9
    assert [c["Договор"] for c in contracts] == ["D-1", "D-2", "Итого"]

    w14 = next(w for w in works if w["Работа"] == "W-14")
    assert (w14["Выплачено"], w14["в т.ч. платежами с привязкой"], w14["в т.ч. из платежей без привязки"]) == \
           ("100000,00", "15000,00", "85000,00")
    d1 = contracts[0]
    assert (d1["Выплачено"], d1["Осталось оплатить"], d1["В очереди"], d1["Останется после очереди"]) == \
           ("315000,00", "185000,00", "150000,00", "35000,00")
    assert any(n["Тип"] == "проверить" and "P-3" in n["Текст"] for n in notes)

    # Строки работ сходятся с итогами договоров до копейки.
    for c in contracts[:-1]:
        rows = [w for w in works if w["Договор"] == c["Договор"]]
        for wcol, ccol in [("Выплачено", "Выплачено в счёт работ"), ("Осталось оплатить", "Осталось оплатить"),
                           ("Цена к оплате", "Цена работ к оплате"), ("К оплате сейчас", "К оплате сейчас")]:
            assert sum(money(r[wcol]) for r in rows) == money(c[ccol]), (c["Договор"], wcol)
    total = contracts[-1]
    assert money(total["Выплачено"]) == sum(money(c["Выплачено"]) for c in contracts[:-1])


def test_russian_date_format_gives_same_report(real_data, tmp_path, capsys):
    main([str(real_data), "2026-11-10", "--no-csv"])
    iso = capsys.readouterr().out
    main([str(real_data), "10.11.2026", "--no-csv"])
    assert capsys.readouterr().out == iso


def test_plain_csv(real_data, tmp_path):
    assert main([str(real_data), "2026-11-12", "--out", str(tmp_path), "--csv-format", "plain"]) == 0
    raw = (tmp_path / "contracts_2026-11-12.csv").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    rows = read_csv(tmp_path / "contracts_2026-11-12.csv", delimiter=",", encoding="utf-8")
    d2 = rows[1]
    assert d2["Подрядчик"] == "Иван Петров, дизайнер"  # запятая в имени экранирована
    assert (d2["Выплачено"], d2["Переплата"]) == ("95000.00", "5000.00")


def test_no_csv_writes_nothing(real_data, tmp_path):
    out = tmp_path / "out"
    assert main([str(real_data), "2026-11-10", "--no-csv", "--out", str(out)]) == 0
    assert not out.exists()


@pytest.mark.parametrize("bad", ["2026-13-01", "31.02.2026", "10/11/2026", "завтра", "20261110"])
def test_bad_date_is_usage_error(real_data, bad, capsys):
    with pytest.raises(SystemExit) as info:
        main([str(real_data), bad])
    assert info.value.code == 2
    assert "некорректная дата" in capsys.readouterr().err


def test_bad_data_exit_code_and_message(make_data, capsys):
    path = make_data(contracts=[{"contract_id": "D-1", "contractor": "X", "amount": "1.5.0",
                                 "signed_at": "2026-01-01"}])
    assert main([str(path), "2026-11-10", "--no-csv"]) == 1
    err = capsys.readouterr().err
    assert "Отчёт не построен" in err and "некорректная сумма" in err


def test_runs_as_module_with_redirected_output(real_data, tmp_path):
    # Вывод в пайп: на Windows без перенастройки здесь падало на кириллице.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    result = subprocess.run(
        [sys.executable, "-m", "advance_report", str(real_data), "10.11.2026", "--out", str(tmp_path)],
        cwd=PROJECT, capture_output=True, env=env,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    text = result.stdout.decode("utf-8")
    assert "Студия «Свет»" in text and "—" in text
