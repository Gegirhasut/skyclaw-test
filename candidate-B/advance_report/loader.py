"""Чтение data/*.csv и проверка выгрузки.

Все ошибки собираются в один список и показываются разом: лучше сразу увидеть
всё, что не так в выгрузке, чем чинить по одной строке за запуск.
Неоднозначные данные программа не «чинит» молча — она останавливается.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from .models import (
    PAY_PAID,
    PAY_QUEUED,
    PAY_REFUNDED,
    PAY_STATUSES,
    WORK_CANCELLED,
    WORK_DONE,
    WORK_PLANNED,
    WORK_STATUSES,
    Contract,
    Dataset,
    Payment,
    Work,
)
from .money import ZERO, parse_money

CONTRACT_COLUMNS = ("contract_id", "contractor", "amount", "signed_at")
WORK_COLUMNS = ("work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at")
PAYMENT_COLUMNS = (
    "payment_id", "contract_id", "work_id", "purpose", "amount",
    "status", "created_at", "paid_at", "refunded_at",
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class DataError(Exception):
    """Выгрузка не прошла проверку. В .errors — все найденные проблемы."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def parse_date(text: str) -> date:
    """Строго ГГГГ-ММ-ДД, как в выгрузке."""
    value = text.strip()
    if not _DATE_RE.fullmatch(value):
        raise ValueError(f"некорректная дата {text!r}: ожидается ГГГГ-ММ-ДД")
    return date.fromisoformat(value)


class _Collector:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def money(self, where: str, row: dict, column: str):
        try:
            return parse_money(row[column])
        except ValueError as exc:
            self.add(where, f"{column}: {exc}")
            return None

    def date(self, where: str, row: dict, column: str, required: bool):
        value = row[column].strip()
        if not value:
            if required:
                self.add(where, f"{column}: дата не заполнена")
            return None
        try:
            return parse_date(value)
        except ValueError as exc:
            self.add(where, f"{column}: {exc}")
            return None

    def forbid(self, where: str, row: dict, column: str, reason: str) -> None:
        if row[column].strip():
            self.add(where, f"{column} заполнено ({row[column].strip()}), но {reason}")


def _read_rows(path: Path, columns: tuple[str, ...], errors: _Collector) -> list[tuple[str, dict]]:
    if not path.is_file():
        errors.add(path.name, "файл не найден")
        return []
    # utf-8-sig: выгрузку могли пересохранить в Excel с BOM.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [c for c in columns if c not in header]
        if missing:
            errors.add(path.name, f"нет колонок: {', '.join(missing)}")
            return []
        rows = []
        for row in reader:
            where = f"{path.name}, строка {reader.line_num}"
            if None in row or any(row[c] is None for c in columns):
                errors.add(where, "число полей не совпадает с заголовком")
                continue
            if not any(v.strip() for v in row.values() if isinstance(v, str)):
                continue  # пустая строка в конце файла
            rows.append((where, row))
        return rows


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    errors = _Collector()
    if not data_dir.is_dir():
        raise DataError([f"{data_dir}: папка с данными не найдена"])

    contracts = _load_contracts(_read_rows(data_dir / "contracts.csv", CONTRACT_COLUMNS, errors), errors)
    works = _load_works(_read_rows(data_dir / "works.csv", WORK_COLUMNS, errors), errors)
    payments = _load_payments(_read_rows(data_dir / "payments.csv", PAYMENT_COLUMNS, errors), errors)

    _check_links(contracts, works, payments, errors)
    if errors.errors:
        raise DataError(errors.errors)
    return Dataset(contracts=contracts, works=works, payments=payments)


def _id(errors: _Collector, where: str, row: dict, column: str, seen: set[str]) -> str | None:
    value = row[column].strip()
    if not value:
        errors.add(where, f"{column}: пустой номер")
        return None
    if value in seen:
        errors.add(where, f"{column}: номер {value} повторяется")
        return None
    seen.add(value)
    return value


def _load_contracts(rows, errors: _Collector) -> list[Contract]:
    result, seen = [], set()
    for where, row in rows:
        cid = _id(errors, where, row, "contract_id", seen)
        amount = errors.money(where, row, "amount")
        signed = errors.date(where, row, "signed_at", required=True)
        if cid and amount is not None and signed:
            result.append(Contract(cid, row["contractor"].strip(), amount, signed))
    return result


def _load_works(rows, errors: _Collector) -> list[Work]:
    result, seen = [], set()
    for where, row in rows:
        wid = _id(errors, where, row, "work_id", seen)
        where = f"{where} ({wid})" if wid else where
        price = errors.money(where, row, "price")
        status = row["status"].strip()
        completed = cancelled = None
        ok = True
        if status not in WORK_STATUSES:
            errors.add(where, f"status: неизвестный статус {status!r}")
            ok = False
        elif status == WORK_DONE:
            completed = errors.date(where, row, "completed_at", required=True)
            errors.forbid(where, row, "cancelled_at", "работа в статусе done")
            ok = completed is not None
        elif status == WORK_CANCELLED:
            cancelled = errors.date(where, row, "cancelled_at", required=True)
            # Отменённая работа могла быть до этого принята — это не ошибка.
            completed = errors.date(where, row, "completed_at", required=False)
            ok = cancelled is not None
            if completed and cancelled and completed > cancelled:
                errors.add(where, "completed_at позже cancelled_at")
        elif status == WORK_PLANNED:
            errors.forbid(where, row, "completed_at", "работа в статусе planned")
            errors.forbid(where, row, "cancelled_at", "работа в статусе planned")
        contract_id = row["contract_id"].strip()
        if not contract_id:
            errors.add(where, "contract_id: не заполнен")
        if wid and price is not None and ok and contract_id:
            result.append(Work(wid, contract_id, row["title"].strip(), price, status, completed, cancelled))
    return result


def _load_payments(rows, errors: _Collector) -> list[Payment]:
    result, seen = [], set()
    for where, row in rows:
        pid = _id(errors, where, row, "payment_id", seen)
        where = f"{where} ({pid})" if pid else where
        amount = errors.money(where, row, "amount")
        if amount is not None and amount == ZERO:
            errors.add(where, "amount: нулевой платёж")
            amount = None
        status = row["status"].strip()
        created = errors.date(where, row, "created_at", required=True)
        paid = refunded = None
        ok = True
        if status not in PAY_STATUSES:
            errors.add(where, f"status: неизвестный статус {status!r}")
            ok = False
        elif status == PAY_PAID:
            paid = errors.date(where, row, "paid_at", required=True)
            errors.forbid(where, row, "refunded_at", "платёж в статусе paid")
            ok = paid is not None
        elif status == PAY_REFUNDED:
            paid = errors.date(where, row, "paid_at", required=True)
            refunded = errors.date(where, row, "refunded_at", required=True)
            ok = paid is not None and refunded is not None
            if ok and refunded < paid:
                errors.add(where, "refunded_at раньше paid_at")
                ok = False
        elif status == PAY_QUEUED:
            errors.forbid(where, row, "paid_at", "платёж ещё в очереди (queued)")
            errors.forbid(where, row, "refunded_at", "платёж ещё в очереди (queued)")
        contract_id = row["contract_id"].strip()
        if not contract_id:
            errors.add(where, "contract_id: не заполнен")
        work_id = row["work_id"].strip() or None
        if pid and amount is not None and created and ok and contract_id:
            result.append(Payment(pid, contract_id, work_id, row["purpose"].strip(), amount,
                                  status, created, paid, refunded))
    return result


def _check_links(contracts: list[Contract], works: list[Work], payments: list[Payment],
                 errors: _Collector) -> None:
    contract_ids = {c.contract_id for c in contracts}
    work_by_id = {w.work_id: w for w in works}
    for w in works:
        if w.contract_id not in contract_ids:
            errors.add(f"works.csv ({w.work_id})", f"договор {w.contract_id} не найден в contracts.csv")
    for p in payments:
        where = f"payments.csv ({p.payment_id})"
        if p.contract_id not in contract_ids:
            errors.add(where, f"договор {p.contract_id} не найден в contracts.csv")
        if p.work_id is not None:
            work = work_by_id.get(p.work_id)
            if work is None:
                errors.add(where, f"работа {p.work_id} не найдена в works.csv")
            elif work.contract_id != p.contract_id:
                errors.add(where, f"работа {p.work_id} относится к договору {work.contract_id}, "
                                  f"а платёж — к {p.contract_id}")
