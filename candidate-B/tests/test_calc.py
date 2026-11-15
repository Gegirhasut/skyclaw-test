import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from advance_report.calc import (
    CANCELLED, DONE, LATER, PLANNED, WARN, build_report,
)
from advance_report.loader import load_dataset
from advance_report.models import Contract, Dataset, Payment, Work

M = Decimal
DAY = date(2026, 11, 10)


def contract(cid="D-1", amount="1000.00"):
    return Contract(cid, "Подрядчик", M(amount), date(2026, 1, 1))


def work(wid, price, status="planned", completed=None, cancelled=None, cid="D-1"):
    return Work(wid, cid, f"Работа {wid}", M(price), status, completed, cancelled)


def pay(pid, amount, status="paid", work_id=None, created=date(2026, 2, 1), paid=date(2026, 2, 1),
        refunded=None, cid="D-1"):
    if status == "queued":
        paid = None
    return Payment(pid, cid, work_id, f"Платёж {pid}", M(amount), status, created, paid, refunded)


def one(works, payments, as_of=DAY, c=None):
    report = build_report(Dataset([c or contract()], works, payments), as_of)
    return report.contracts[0]


def by_id(cr):
    return {w.work.work_id: w for w in cr.works}


# --- реальные данные: эталон, посчитанный вручную ----------------------------

def test_real_data_on_10_11_2026(real_data):
    report = build_report(load_dataset(real_data), DAY)
    d1, d2 = report.contracts

    expected_d1 = {  # work: (state, paid_linked, paid_shared, remaining, due_now, queued)
        "W-11": (DONE, "0", "100000", "0", "0", "0"),
        "W-12": (DONE, "0", "100000", "0", "0", "0"),
        "W-13": (CANCELLED, "0", "0", "0", "0", "0"),
        "W-14": (DONE, "15000", "85000", "0", "0", "0"),
        "W-15": (PLANNED, "0", "15000", "85000", "0", "85000"),
        "W-16": (PLANNED, "0", "0", "100000", "0", "65000"),
    }
    for wid, (state, linked, shared, remaining, due, queued) in expected_d1.items():
        w = by_id(d1)[wid]
        assert (w.state, w.paid_linked, w.paid_shared, w.remaining, w.due_now, w.queued) == \
               (state, M(linked), M(shared), M(remaining), M(due), M(queued)), wid
    assert d1.contract.amount == M("600000.00")
    assert d1.price_total == M("500000.00")
    assert d1.cancelled_total == M("100000.00")
    assert d1.paid_total == M("315000.00")
    assert d1.remaining == M("185000.00")
    assert d1.due_now == M("0.00")
    assert d1.queued_total == M("150000.00")
    assert d1.remaining_after_queue == M("35000.00")
    assert d1.refunded_total == M("50000.00")
    assert d1.overpaid == M("0.00")

    expected_d2 = {
        "W-21": (DONE, "20000", "0"),
        "W-22": (DONE, "30000", "0"),
        "W-23": (PLANNED, "25000", "15000"),  # принята только 11.11
    }
    for wid, (state, paid, remaining) in expected_d2.items():
        w = by_id(d2)[wid]
        assert (w.state, w.paid, w.remaining) == (state, M(paid), M(remaining)), wid
    assert d2.paid_total == M("75000.00")  # P-8 от 12.11 не учтён
    assert d2.remaining == M("15000.00")
    assert d2.overpaid == M("0.00")
    later = [n.text for n in d2.notes if n.level == LATER]
    assert any("W-23" in t for t in later) and any("P-8" in t for t in later)


def test_real_data_after_all_events_shows_d2_overpayment(real_data):
    d2 = build_report(load_dataset(real_data), date(2026, 11, 12)).contracts[1]
    assert d2.paid_total == M("95000.00")
    assert d2.remaining == M("0.00")
    assert d2.overpaid == M("5000.00")
    assert all(w.remaining == 0 for w in d2.works)
    assert any(n.level == WARN and "Переплата 5 000,00" in n.text for n in d2.notes)


def test_real_data_refund_window(real_data):
    ds = load_dataset(real_data)
    # 05.09: P-2 ушёл, но ещё не вернулся — считается выплаченным.
    assert build_report(ds, date(2026, 9, 5)).contracts[0].paid_total == M("350000.00")
    # 10.09 — день возврата: уже не считается.
    assert build_report(ds, date(2026, 9, 10)).contracts[0].paid_total == M("300000.00")


# --- правила по датам ---------------------------------------------------------

def test_dates_are_inclusive():
    w = work("W-1", "100.00", "done", completed=DAY)
    p = pay("P-1", "30.00", created=DAY, paid=DAY)
    cr = one([w], [p])
    assert cr.works[0].state == DONE
    assert cr.paid_total == M("30.00")
    assert cr.due_now == M("70.00")


def test_cancelled_after_date_is_still_active():
    w = work("W-1", "100.00", "cancelled", cancelled=DAY + timedelta(days=1))
    cr = one([w], [pay("P-1", "40.00")])
    assert cr.works[0].state == PLANNED
    assert cr.price_total == M("100.00")
    assert cr.works[0].paid == M("40.00")


def test_cancelled_work_excluded_from_price():
    cr = one([work("W-1", "100.00", "cancelled", cancelled=DAY), work("W-2", "50.00")],
             [pay("P-1", "120.00")])
    w1, w2 = cr.works
    assert w1.price == 0 and w1.paid == 0 and w1.remaining == 0
    assert w2.paid == M("50.00")
    assert cr.overpaid == M("70.00")


def test_paid_after_date_but_created_before_is_queued():
    p = pay("P-1", "10.00", created=DAY, paid=DAY + timedelta(days=2))
    cr = one([work("W-1", "100.00")], [p])
    assert cr.paid_total == 0
    assert cr.queued_total == M("10.00")


def test_payment_created_after_date_is_ignored():
    p = pay("P-1", "10.00", "queued", created=DAY + timedelta(days=1))
    cr = one([work("W-1", "100.00")], [p])
    assert cr.queued_total == 0 and cr.paid_total == 0
    assert any(n.level == LATER for n in cr.notes)


# --- раскладка ----------------------------------------------------------------

def test_linked_payments_go_first():
    # Общие 300 не должны «занять» место, которое уже покрыто привязанным платежом.
    works = [work("W-1", "100.00", "done", completed=date(2026, 3, 1)),
             work("W-2", "100.00", "done", completed=date(2026, 3, 2)),
             work("W-3", "100.00", "done", completed=date(2026, 3, 3)),
             work("W-4", "100.00")]
    payments = [pay("P-1", "300.00"), pay("P-2", "15.00", work_id="W-1")]
    w = by_id(one(works, payments))
    assert (w["W-1"].paid_linked, w["W-1"].paid_shared) == (M("15.00"), M("85.00"))
    assert w["W-2"].paid == w["W-3"].paid == M("100.00")
    assert w["W-4"].paid == M("15.00")


def test_done_works_first_by_acceptance_date_then_planned_by_number():
    works = [work("W-1", "100.00"),  # запланирована
             work("W-2", "100.00", "done", completed=date(2026, 5, 1)),
             work("W-3", "100.00", "done", completed=date(2026, 4, 1))]
    w = by_id(one(works, [pay("P-1", "150.00")]))
    assert w["W-3"].paid == M("100.00")  # принята раньше всех
    assert w["W-2"].paid == M("50.00")
    assert w["W-1"].paid == M("0.00")
    assert w["W-2"].due_now == M("50.00")


def test_planned_sorted_naturally():
    works = [work("W-10", "100.00"), work("W-9", "100.00")]
    w = by_id(one(works, [pay("P-1", "100.00")]))
    assert w["W-9"].paid == M("100.00") and w["W-10"].paid == 0


def test_linked_payment_over_price_spills_to_contract():
    works = [work("W-1", "100.00"), work("W-2", "100.00")]
    cr = one(works, [pay("P-1", "130.00", work_id="W-1")])
    w = by_id(cr)
    assert (w["W-1"].paid_linked, w["W-2"].paid_shared) == (M("100.00"), M("30.00"))
    assert any(n.level == WARN and "P-1" in n.text for n in cr.notes)


def test_linked_payment_to_cancelled_work_spills_to_contract():
    works = [work("W-1", "100.00", "cancelled", cancelled=date(2026, 3, 1)), work("W-2", "100.00")]
    cr = one(works, [pay("P-1", "40.00", work_id="W-1")])
    assert by_id(cr)["W-2"].paid == M("40.00")
    assert any(n.level == WARN and "отменена" in n.text for n in cr.notes)


def test_queue_is_laid_over_paid_and_excess_reported():
    works = [work("W-1", "100.00", "done", completed=date(2026, 3, 1)), work("W-2", "100.00")]
    cr = one(works, [pay("P-1", "60.00"), pay("P-2", "200.00", "queued")])
    w = by_id(cr)
    assert w["W-1"].paid == M("60.00") and w["W-1"].queued == M("40.00")
    assert w["W-2"].queued == M("100.00")
    assert cr.remaining_after_queue == 0
    assert cr.queued_excess == M("60.00")
    assert any(n.level == WARN and "Очередь больше остатка" in n.text for n in cr.notes)


def test_refunded_never_counts_as_paid():
    p = pay("P-1", "50.00", "refunded", paid=date(2026, 2, 1), refunded=date(2026, 2, 5))
    cr = one([work("W-1", "100.00")], [p])
    assert cr.paid_total == 0 and cr.refunded_total == M("50.00")


def test_contract_amount_mismatch_is_flagged():
    cr = one([work("W-1", "100.00")], [], c=contract(amount="150.00"))
    assert any(n.level == WARN and "не совпадает" in n.text for n in cr.notes)


def test_kopecks_are_exact():
    works = [work(f"W-{i}", "33333.33") for i in range(1, 4)]
    cr = one(works, [pay("P-1", "0.01"), pay("P-2", "99999.98")], c=contract(amount="99999.99"))
    assert cr.paid_total == M("99999.99")
    assert cr.remaining == 0 and cr.overpaid == 0


def test_contract_without_works_and_payments():
    cr = one([], [])
    assert cr.price_total == cr.paid_total == cr.remaining == 0


def test_other_contract_data_not_mixed():
    ds = Dataset(
        [contract("D-1"), contract("D-2")],
        [work("W-1", "100.00"), work("W-2", "100.00", cid="D-2")],
        [pay("P-1", "70.00", cid="D-2")],
    )
    d1, d2 = build_report(ds, DAY).contracts
    assert d1.paid_total == 0 and d2.paid_total == M("70.00")


# --- свойства на случайных данных -----------------------------------------------

@pytest.mark.parametrize("seed", range(300))
def test_random_datasets_balance_to_the_kopeck(seed):
    rnd = random.Random(seed)
    base = date(2026, 1, 1)

    def some_day():
        return base + timedelta(days=rnd.randint(0, 120))

    def money():
        return M(rnd.randint(1, 5_000_000)) / 100

    works = []
    for i in range(rnd.randint(0, 6)):
        status = rnd.choice(["planned", "done", "cancelled"])
        completed = some_day() if status == "done" else None
        cancelled = some_day() if status == "cancelled" else None
        works.append(Work(f"W-{i}", "D-1", "t", money(), status, completed, cancelled))
    payments = []
    for i in range(rnd.randint(0, 8)):
        status = rnd.choice(["paid", "queued", "refunded"])
        created = some_day()
        paid = some_day() if status != "queued" else None
        refunded = paid + timedelta(days=rnd.randint(0, 30)) if status == "refunded" else None
        link = rnd.choice([None] + [w.work_id for w in works])
        payments.append(Payment(f"P-{i}", "D-1", link, "p", money(), status, created, paid, refunded))
    as_of = some_day()

    cr = one(works, payments, as_of=as_of)  # внутри — проверка инвариантов
    assert cr.paid_to_works + cr.overpaid == cr.paid_total
    assert cr.remaining + cr.paid_to_works == cr.price_total
    assert cr.queued_to_works + cr.queued_excess == cr.queued_total
    for w in cr.works:
        assert 0 <= w.paid <= w.price
        assert 0 <= w.queued <= w.remaining
        assert w.paid.as_tuple().exponent == -2
    # переплата возможна, только если всё, что подлежит оплате, оплачено
    if cr.overpaid:
        assert cr.remaining == 0


# --- регрессии по итогам ревизии ---------------------------------------------------

def test_paid_before_created_counts_by_payment_date_and_is_flagged():
    p = pay("P-1", "60.00", created=date(2026, 2, 1), paid=date(2026, 1, 2))
    cr = one([work("W-1", "100.00")], [p], as_of=date(2026, 1, 4))
    assert cr.paid_total == M("60.00")
    assert any(n.level == WARN and "раньше, чем заведён" in n.text for n in cr.notes)


def test_linked_to_cancelled_work_has_no_contradicting_note():
    works = [work("W-1", "100.00", "cancelled", cancelled=date(2026, 3, 1)), work("W-2", "100.00")]
    cr = one(works, [pay("P-1", "40.00", work_id="W-1")])
    texts = [n.text for n in cr.notes]
    assert not any("засчитан в её цену" in t for t in texts)
    assert any("не поместились" in t for t in texts)


def test_queued_linked_payment_is_flagged():
    cr = one([work("W-1", "100.00")], [pay("P-1", "10.00", "queued", work_id="W-1")])
    assert any(n.level == WARN and "P-1" in n.text and "Подтвердите" in n.text for n in cr.notes)


def test_accepted_then_cancelled_work():
    w = work("W-1", "100.00", "cancelled", completed=date(2026, 3, 1), cancelled=date(2026, 4, 1))
    before = one([w], [], as_of=date(2026, 3, 15)).works[0]
    after = one([w], [], as_of=date(2026, 4, 1)).works[0]
    assert (before.state, before.due_now) == (DONE, M("100.00"))
    assert (after.state, after.price) == (CANCELLED, M("0.00"))
