"""Записи выгрузки в том виде, как они лежат в data/*.csv."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Статусы работ (works.csv) — на момент выгрузки.
WORK_PLANNED = "planned"
WORK_DONE = "done"
WORK_CANCELLED = "cancelled"
WORK_STATUSES = (WORK_PLANNED, WORK_DONE, WORK_CANCELLED)

# Статусы платежей (payments.csv) — на момент выгрузки.
PAY_PAID = "paid"
PAY_QUEUED = "queued"
PAY_REFUNDED = "refunded"
PAY_STATUSES = (PAY_PAID, PAY_QUEUED, PAY_REFUNDED)


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Dataset:
    contracts: list[Contract]
    works: list[Work]
    payments: list[Payment]


def natural_key(identifier: str) -> tuple:
    """'W-9' < 'W-10': сравниваем числа как числа, а не как строки."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", identifier)
    )
