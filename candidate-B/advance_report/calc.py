"""Расчёт отчёта на дату.

Порядок расчёта по каждому договору (обоснование — в DECISIONS.md):

1. Состояние каждой работы и каждого платежа восстанавливается на дату отчёта
   по датам из выгрузки (статус в выгрузке — «на сейчас», а не на дату).
2. Отменённые на дату работы в расчёт не входят: их цена не подлежит оплате.
3. Выплаченные на дату платежи раскладываются по работам:
   a) платёж с привязкой к работе — на эту работу (не больше её цены);
   b) всё остальное (платежи без привязки и то, что не поместилось в п. a) —
      по очереди: сначала принятые работы по дате приёмки, затем запланированные
      по номеру; каждой — не больше её непогашенной цены;
   c) что не поместилось никуда — переплата по договору.
4. Платежи в очереди раскладываются тем же способом поверх выплаченного —
   видно, что закроется, когда очередь оплатят. В «выплачено» они не входят.
5. Возвращённые на дату платежи не учитываются (деньги вернулись), но видны в примечаниях.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import (
    PAY_QUEUED,
    PAY_REFUNDED,
    WORK_CANCELLED,
    WORK_DONE,
    Contract,
    Dataset,
    Payment,
    Work,
    natural_key,
)
from .money import ZERO, fmt_ru

# Состояния на дату отчёта.
DONE = "done"
PLANNED = "planned"
CANCELLED = "cancelled"

PAID = "paid"
QUEUED = "queued"
REFUNDED = "refunded"
NOT_YET = None  # платёж на дату ещё не заведён — его нет

WARN = "warn"    # требует проверки бухгалтером
INFO = "info"    # пояснение к расчёту
LATER = "later"  # событие после даты отчёта, на дату не учтено


@dataclass(frozen=True)
class Note:
    level: str  # WARN / INFO / LATER
    text: str


@dataclass
class WorkLine:
    work: Work
    state: str                 # DONE / PLANNED / CANCELLED на дату отчёта
    completed_at: date | None  # дата приёмки, если на дату работа принята
    paid_linked: Decimal = ZERO   # выплачено платежами с привязкой к этой работе
    paid_shared: Decimal = ZERO   # выплачено из общих платежей по договору
    queued: Decimal = ZERO        # закроется платежами из очереди

    @property
    def price(self) -> Decimal:
        """Цена, которая подлежит оплате на дату: у отменённой — 0."""
        return ZERO if self.state == CANCELLED else self.work.price

    @property
    def paid(self) -> Decimal:
        return self.paid_linked + self.paid_shared

    @property
    def remaining(self) -> Decimal:
        return self.price - self.paid

    @property
    def due_now(self) -> Decimal:
        """Долг за уже принятую работу."""
        return self.remaining if self.state == DONE else ZERO

    @property
    def remaining_after_queue(self) -> Decimal:
        return self.remaining - self.queued


@dataclass
class ContractReport:
    contract: Contract
    works: list[WorkLine]
    paid_total: Decimal       # всё, что выплачено по договору на дату
    queued_total: Decimal     # всё, что стоит в очереди на дату
    refunded_total: Decimal   # выплачено и возвращено к дате (для справки)
    overpaid: Decimal         # выплачено сверх цены неотменённых работ
    queued_excess: Decimal    # очередь сверх остатка — будущая переплата
    notes: list[Note] = field(default_factory=list)

    @property
    def works_total(self) -> Decimal:
        return sum((w.work.price for w in self.works), ZERO)

    @property
    def cancelled_total(self) -> Decimal:
        return sum((w.work.price for w in self.works if w.state == CANCELLED), ZERO)

    @property
    def price_total(self) -> Decimal:
        return sum((w.price for w in self.works), ZERO)

    @property
    def paid_to_works(self) -> Decimal:
        return sum((w.paid for w in self.works), ZERO)

    @property
    def remaining(self) -> Decimal:
        return sum((w.remaining for w in self.works), ZERO)

    @property
    def due_now(self) -> Decimal:
        return sum((w.due_now for w in self.works), ZERO)

    @property
    def queued_to_works(self) -> Decimal:
        return sum((w.queued for w in self.works), ZERO)

    @property
    def remaining_after_queue(self) -> Decimal:
        return sum((w.remaining_after_queue for w in self.works), ZERO)


@dataclass
class Report:
    as_of: date
    contracts: list[ContractReport]


def d(value: date) -> str:
    return value.strftime("%d.%m.%Y")


# --- состояние на дату ------------------------------------------------------

def work_state_on(work: Work, as_of: date) -> tuple[str, date | None, Note | None]:
    """Состояние работы на дату: (состояние, дата приёмки, примечание)."""
    if work.status == WORK_CANCELLED:
        if work.cancelled_at <= as_of:
            return CANCELLED, None, None
        note = Note(LATER, f"{work.work_id} отменена {d(work.cancelled_at)} — после даты отчёта, "
                          f"на дату она ещё действует")
        if work.completed_at and work.completed_at <= as_of:
            return DONE, work.completed_at, note
        return PLANNED, None, note
    if work.status == WORK_DONE:
        if work.completed_at <= as_of:
            return DONE, work.completed_at, None
        return PLANNED, None, Note(LATER, f"{work.work_id} принята {d(work.completed_at)} — после даты "
                                         f"отчёта, на дату она ещё не принята")
    return PLANNED, None, None


def payment_state_on(p: Payment, as_of: date) -> tuple[str | None, Note | None]:
    """Состояние платежа на дату: PAID / QUEUED / REFUNDED / NOT_YET и примечание."""
    label = f"{p.payment_id} «{p.purpose}» {fmt_ru(p.amount)}"
    if p.status == PAY_QUEUED:
        if p.created_at <= as_of:
            return QUEUED, None
        return NOT_YET, Note(LATER, f"{label} заведён {d(p.created_at)} — после даты отчёта, не учтён")

    # paid или refunded: деньги уходили p.paid_at
    if p.paid_at <= as_of:
        if p.status == PAY_REFUNDED:
            if p.refunded_at <= as_of:
                return REFUNDED, None
            return PAID, Note(LATER, f"{label} возвращён {d(p.refunded_at)} — после даты отчёта, "
                                    f"на дату считается выплаченным")
        return PAID, None
    if p.created_at <= as_of:
        return QUEUED, Note(LATER, f"{label} оплачен {d(p.paid_at)} — после даты отчёта, "
                                  f"на дату он был в очереди")
    return NOT_YET, Note(LATER, f"{label} оплачен {d(p.paid_at)} — после даты отчёта, не учтён")


# --- раскладка денег по работам ---------------------------------------------

@dataclass
class _Allocation:
    linked: dict[str, Decimal]
    shared: dict[str, Decimal]
    leftover: Decimal
    linked_overflow: list[tuple[Payment, Decimal, str]]  # (платёж, не поместилось, причина)


def _allocate(order: list[WorkLine], capacity: dict[str, Decimal],
              payments: list[Payment]) -> _Allocation:
    """Раскладывает платежи по работам в пределах capacity (непогашенной цены)."""
    cap = dict(capacity)
    linked = {w.work.work_id: ZERO for w in order}
    shared = {w.work.work_id: ZERO for w in order}
    overflow: list[tuple[Payment, Decimal, str]] = []
    pool = ZERO

    for p in payments:
        if p.work_id is None:
            pool += p.amount
            continue
        if p.work_id not in cap:
            overflow.append((p, p.amount, f"работа {p.work_id} отменена"))
            pool += p.amount
            continue
        take = min(p.amount, cap[p.work_id])
        linked[p.work_id] += take
        cap[p.work_id] -= take
        if take < p.amount:
            overflow.append((p, p.amount - take, f"больше непогашенной цены {p.work_id}"))
            pool += p.amount - take

    for w in order:
        wid = w.work.work_id
        take = min(pool, cap[wid])
        shared[wid] += take
        cap[wid] -= take
        pool -= take

    return _Allocation(linked, shared, pool, overflow)


def _pay_order(lines: list[WorkLine]) -> list[WorkLine]:
    """Принятые — по дате приёмки, затем запланированные — по номеру."""
    done = sorted((w for w in lines if w.state == DONE),
                  key=lambda w: (w.completed_at, natural_key(w.work.work_id)))
    planned = sorted((w for w in lines if w.state == PLANNED), key=lambda w: natural_key(w.work.work_id))
    return done + planned


# --- отчёт --------------------------------------------------------------------

def build_report(ds: Dataset, as_of: date) -> Report:
    contracts = sorted(ds.contracts, key=lambda c: natural_key(c.contract_id))
    return Report(as_of=as_of, contracts=[_build_contract(c, ds, as_of) for c in contracts])


def _build_contract(contract: Contract, ds: Dataset, as_of: date) -> ContractReport:
    notes: list[Note] = []
    cid = contract.contract_id

    if contract.signed_at > as_of:
        notes.append(Note(WARN, f"Договор подписан {d(contract.signed_at)} — позже даты отчёта"))

    lines: list[WorkLine] = []
    for work in sorted((w for w in ds.works if w.contract_id == cid), key=lambda w: natural_key(w.work_id)):
        state, completed, note = work_state_on(work, as_of)
        lines.append(WorkLine(work=work, state=state, completed_at=completed))
        if note:
            notes.append(note)

    paid: list[Payment] = []
    queued: list[Payment] = []
    refunded: list[Payment] = []
    for p in sorted((p for p in ds.payments if p.contract_id == cid), key=lambda p: natural_key(p.payment_id)):
        state, note = payment_state_on(p, as_of)
        if note:
            notes.append(note)
        if state == PAID:
            paid.append(p)
        elif state == QUEUED:
            queued.append(p)
        elif state == REFUNDED:
            refunded.append(p)

    order = _pay_order(lines)

    # 1-й проход: выплаченное.
    first = _allocate(order, {w.work.work_id: w.price for w in order}, paid)
    for w in order:
        w.paid_linked = first.linked[w.work.work_id]
        w.paid_shared = first.shared[w.work.work_id]

    # 2-й проход: очередь — поверх выплаченного.
    second = _allocate(order, {w.work.work_id: w.remaining for w in order}, queued)
    for w in order:
        w.queued = second.linked[w.work.work_id] + second.shared[w.work.work_id]

    report = ContractReport(
        contract=contract,
        works=lines,
        paid_total=sum((p.amount for p in paid), ZERO),
        queued_total=sum((p.amount for p in queued), ZERO),
        refunded_total=sum((p.amount for p in refunded), ZERO),
        overpaid=first.leftover,
        queued_excess=second.leftover,
        notes=notes,
    )
    _check_invariants(report)
    _contract_notes(report, paid, queued, refunded, first, second)
    return report


def _contract_notes(r: ContractReport, paid, queued, refunded, first: _Allocation, second: _Allocation) -> None:
    notes = r.notes
    amount = r.contract.amount
    if r.works_total != amount:
        notes.append(Note(WARN, f"Сумма цен работ {fmt_ru(r.works_total)} не совпадает с суммой договора "
                                f"{fmt_ru(amount)}; расчёт ведётся по ценам работ"))
    for w in r.works:
        if w.state == CANCELLED:
            notes.append(Note(INFO, f"{w.work.work_id} «{w.work.title}» отменена {d(w.work.cancelled_at)}: "
                                    f"её цена {fmt_ru(w.work.price)} в расчёт не входит"))
    if r.cancelled_total:
        notes.append(Note(INFO, f"К оплате по договору с учётом отмен — {fmt_ru(r.price_total)} "
                                f"(сумма договора {fmt_ru(amount)})"))
    for p in refunded:
        notes.append(Note(INFO, f"{p.payment_id} «{p.purpose}» {fmt_ru(p.amount)} ушёл {d(p.paid_at)} "
                                f"и возвращён {d(p.refunded_at)} — не учитывается"))
    for p in paid:
        if p.work_id:
            notes.append(Note(WARN, f"{p.payment_id} «{p.purpose}» {fmt_ru(p.amount)} привязан к "
                                    f"{p.work_id} и засчитан в её цену. Подтвердите, что это оплата "
                                    f"работы, а не компенсация расходов сверх цены"))
    for p in queued:
        target = f" (привязан к {p.work_id})" if p.work_id else ""
        notes.append(Note(INFO, f"{p.payment_id} «{p.purpose}» {fmt_ru(p.amount)} в очереди на оплату{target} — "
                                f"в «выплачено» не входит"))
    for p, excess, reason in first.linked_overflow:
        notes.append(Note(WARN, f"{p.payment_id} привязан к {p.work_id}, но {fmt_ru(excess)} из него "
                                f"не поместились ({reason}) — разложены как общий платёж по договору"))
    for p, excess, reason in second.linked_overflow:
        notes.append(Note(WARN, f"{p.payment_id} (в очереди) привязан к {p.work_id}, но {fmt_ru(excess)} "
                                f"не помещаются ({reason}) — разложены как общий платёж"))
    if r.paid_total > amount and not r.overpaid:
        # Работы оплачены не сверх цены, но их цены в сумме больше договора.
        notes.append(Note(WARN, f"Выплачено {fmt_ru(r.paid_total)} — больше суммы договора {fmt_ru(amount)} "
                                f"на {fmt_ru(r.paid_total - amount)}"))
    if r.overpaid:
        notes.append(Note(WARN, f"Переплата {fmt_ru(r.overpaid)}: выплачено {fmt_ru(r.paid_total)}, а цена "
                                f"неотменённых работ {fmt_ru(r.price_total)}. Проверьте, нет ли задвоенного "
                                f"платежа или доплаты сверх договора"))
    if r.queued_excess:
        notes.append(Note(WARN, f"Очередь больше остатка к оплате на {fmt_ru(r.queued_excess)}: если оплатить "
                                f"её целиком, возникнет переплата"))


class InvariantError(AssertionError):
    """Внутренняя ошибка расчёта: суммы не сошлись. Отчёт не выдаётся."""


def _check_invariants(r: ContractReport) -> None:
    problems = []
    if r.paid_to_works + r.overpaid != r.paid_total:
        problems.append("выплачено по работам + переплата ≠ выплачено всего")
    if r.queued_to_works + r.queued_excess != r.queued_total:
        problems.append("очередь по работам + излишек ≠ очередь всего")
    if r.remaining != r.price_total - r.paid_to_works:
        problems.append("остаток ≠ цена − выплачено")
    for w in r.works:
        if not (ZERO <= w.paid <= w.price and ZERO <= w.queued <= w.remaining):
            problems.append(f"{w.work.work_id}: выплачено/очередь вне пределов цены")
    if r.overpaid < ZERO or r.queued_excess < ZERO:
        problems.append("отрицательный излишек")
    if problems:
        raise InvariantError(f"{r.contract.contract_id}: " + "; ".join(problems))
