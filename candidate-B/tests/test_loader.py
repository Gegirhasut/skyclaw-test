from datetime import date
from decimal import Decimal

import pytest

from advance_report.loader import DataError, load_dataset
from advance_report.models import natural_key

C1 = {"contract_id": "D-1", "contractor": "Подрядчик, ООО", "amount": "1000.00", "signed_at": "2026-01-01"}


def errors_of(path) -> list[str]:
    with pytest.raises(DataError) as info:
        load_dataset(path)
    return info.value.errors


def test_real_data_loads(real_data):
    ds = load_dataset(real_data)
    assert [c.contract_id for c in ds.contracts] == ["D-1", "D-2"]
    assert len(ds.works) == 9
    assert len(ds.payments) == 8
    d2 = ds.contracts[1]
    assert d2.contractor == "Иван Петров, дизайнер"  # запятая внутри кавычек
    assert d2.amount == Decimal("90000.00")
    p3 = next(p for p in ds.payments if p.payment_id == "P-3")
    assert p3.work_id == "W-14" and p3.amount == Decimal("15000.00")
    p4 = next(p for p in ds.payments if p.payment_id == "P-4")
    assert p4.work_id is None and p4.paid_at is None and p4.status == "queued"
    p2 = next(p for p in ds.payments if p.payment_id == "P-2")
    assert p2.paid_at == date(2026, 9, 2) and p2.refunded_at == date(2026, 9, 10)


def test_bom_is_accepted(make_data):
    path = make_data(contracts=[C1])
    raw = (path / "contracts.csv").read_bytes()
    (path / "contracts.csv").write_bytes(b"\xef\xbb\xbf" + raw)
    assert load_dataset(path).contracts[0].contract_id == "D-1"


def test_missing_dir(tmp_path):
    assert "не найдена" in errors_of(tmp_path / "nope")[0]


def test_missing_file(make_data):
    path = make_data(contracts=[C1])
    (path / "works.csv").unlink()
    assert any("works.csv: файл не найден" in e for e in errors_of(path))


def test_missing_column(make_data):
    path = make_data(contracts=[C1])
    (path / "payments.csv").write_text("payment_id,contract_id\n", encoding="utf-8")
    assert any("нет колонок" in e for e in errors_of(path))


def test_all_errors_reported_at_once(make_data):
    path = make_data(
        contracts=[C1, C1],  # дубль
        works=[
            {"work_id": "W-1", "contract_id": "D-1", "price": "10,5", "status": "planned"},
            {"work_id": "W-2", "contract_id": "D-9", "price": "1.00", "status": "done"},  # нет даты и договора
            {"work_id": "W-3", "contract_id": "D-1", "price": "1.00", "status": "paused"},
        ],
        payments=[
            {"payment_id": "P-1", "contract_id": "D-1", "work_id": "W-404", "amount": "1.00",
             "status": "paid", "created_at": "2026-01-01", "paid_at": "2026-01-02"},
            {"payment_id": "P-2", "contract_id": "D-1", "amount": "1.00",
             "status": "refunded", "created_at": "2026-01-01", "paid_at": "2026-01-05",
             "refunded_at": "2026-01-03"},
            {"payment_id": "P-3", "contract_id": "D-1", "amount": "0.00",
             "status": "queued", "created_at": "2026-01-01"},
            {"payment_id": "P-4", "contract_id": "D-1", "amount": "1.00",
             "status": "queued", "created_at": "01.01.2026"},
        ],
    )
    errors = "\n".join(errors_of(path))
    for fragment in [
        "номер D-1 повторяется",
        "price: некорректная сумма",
        "completed_at: дата не заполнена",
        "неизвестный статус 'paused'",
        "работа W-404 не найдена",
        "refunded_at раньше paid_at",
        "нулевой платёж",
        "created_at: некорректная дата",
    ]:
        assert fragment in errors


def test_payment_work_from_other_contract(make_data):
    c2 = dict(C1, contract_id="D-2")
    path = make_data(
        contracts=[C1, c2],
        works=[{"work_id": "W-1", "contract_id": "D-1", "price": "10.00", "status": "planned"}],
        payments=[{"payment_id": "P-1", "contract_id": "D-2", "work_id": "W-1", "amount": "1.00",
                   "status": "paid", "created_at": "2026-01-01", "paid_at": "2026-01-01"}],
    )
    assert any("относится к договору D-1" in e for e in errors_of(path))


@pytest.mark.parametrize("payment, fragment", [
    ({"status": "paid"}, "paid_at: дата не заполнена"),
    ({"status": "paid", "paid_at": "2026-01-02", "refunded_at": "2026-01-03"}, "refunded_at заполнено"),
    ({"status": "queued", "paid_at": "2026-01-02"}, "paid_at заполнено"),
    ({"status": "refunded", "paid_at": "2026-01-02"}, "refunded_at: дата не заполнена"),
])
def test_payment_status_vs_dates(make_data, payment, fragment):
    row = {"payment_id": "P-1", "contract_id": "D-1", "amount": "1.00", "created_at": "2026-01-01"}
    row.update(payment)
    path = make_data(contracts=[C1], payments=[row])
    assert any(fragment in e for e in errors_of(path))


@pytest.mark.parametrize("work, fragment", [
    ({"status": "planned", "completed_at": "2026-01-02"}, "completed_at заполнено"),
    ({"status": "done", "completed_at": "2026-01-02", "cancelled_at": "2026-01-03"}, "cancelled_at заполнено"),
    ({"status": "cancelled"}, "cancelled_at: дата не заполнена"),
])
def test_work_status_vs_dates(make_data, work, fragment):
    row = {"work_id": "W-1", "contract_id": "D-1", "price": "1.00"}
    row.update(work)
    path = make_data(contracts=[C1], works=[row])
    assert any(fragment in e for e in errors_of(path))


def test_ragged_row(make_data):
    path = make_data(contracts=[C1])
    with (path / "contracts.csv").open("a", encoding="utf-8") as fh:
        fh.write("D-2,X,1.00\n")
    assert any("число полей" in e for e in errors_of(path))


def test_natural_key():
    assert sorted(["W-10", "W-9", "W-100", "W-2"], key=natural_key) == ["W-2", "W-9", "W-10", "W-100"]
    assert sorted(["A", "W-1", "1"], key=natural_key)  # разные форматы не падают


# --- регрессии ревизии, цикл 1 ---------------------------------------------------

def test_cp1251_file_gives_clear_error(make_data):
    path = make_data(contracts=[C1])
    text = (path / "contracts.csv").read_text(encoding="utf-8")
    (path / "contracts.csv").write_bytes(text.encode("cp1251"))
    errors = errors_of(path)
    assert any("не в кодировке UTF-8" in e for e in errors)
    assert not any("не найден в contracts.csv" in e for e in errors)  # без каскада


def test_semicolon_delimiter_hint_without_cascade(make_data):
    path = make_data(
        contracts=[C1],
        works=[{"work_id": "W-1", "contract_id": "D-1", "price": "1.00", "status": "planned"}],
        payments=[{"payment_id": "P-1", "contract_id": "D-1", "amount": "1.00", "status": "queued",
                   "created_at": "2026-01-01"}],
    )
    text = (path / "contracts.csv").read_text(encoding="utf-8")
    (path / "contracts.csv").write_text(text.replace(",", ";"), encoding="utf-8")
    errors = errors_of(path)
    assert len(errors) == 1 and "разделитель «;»" in errors[0]


def test_bad_work_row_does_not_cascade_to_payments(make_data):
    path = make_data(
        contracts=[C1],
        works=[{"work_id": "W-1", "contract_id": "D-1", "price": "abc", "status": "planned"}],
        payments=[{"payment_id": f"P-{i}", "contract_id": "D-1", "work_id": "W-1", "amount": "1.00",
                   "status": "queued", "created_at": "2026-01-01"} for i in range(3)],
    )
    errors = errors_of(path)
    assert len(errors) == 1 and "price" in errors[0]


@pytest.mark.parametrize("amount", ["1" * 16, "1" * 30 + ".00", "١٠٠.00"])
def test_huge_or_non_ascii_amount_rejected(make_data, amount):
    path = make_data(contracts=[dict(C1, amount=amount)])
    assert any("некорректная сумма" in e for e in errors_of(path))
