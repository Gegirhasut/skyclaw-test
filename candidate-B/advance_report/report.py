"""Вывод отчёта: таблица в консоль и CSV-файлы."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .calc import CANCELLED, DONE, INFO, LATER, PLANNED, WARN, ContractReport, Report, WorkLine, fmt_date
from .money import ZERO, fmt_csv, fmt_ru

STATE_LABELS = {DONE: "принята", PLANNED: "запланирована", CANCELLED: "отменена"}
NOTE_TITLES = {
    WARN: "Проверить",
    INFO: "Пояснения",
    LATER: "События после даты отчёта (на дату не учтены)",
}
NOTE_CSV_LABELS = {WARN: "проверить", INFO: "пояснение", LATER: "после даты отчёта"}


def state_label(w: WorkLine) -> str:
    if w.state == DONE:
        return f"принята {fmt_date(w.completed_at)}"
    if w.state == CANCELLED:
        return f"отменена {fmt_date(w.work.cancelled_at)}"
    return STATE_LABELS[w.state]


# --- консоль --------------------------------------------------------------------

def _table(header: list[str], rows: list[list[str]], numeric_from: int,
           total: list[str] | None = None) -> list[str]:
    """Таблица с выравниванием; total — итоговая строка под чертой."""
    widths = [max(len(r[i]) for r in [header] + rows + ([total] if total else []))
              for i in range(len(header))]

    def line(cells: list[str]) -> str:
        parts = [c.rjust(widths[i]) if i >= numeric_from else c.ljust(widths[i]) for i, c in enumerate(cells)]
        return "  ".join(parts).rstrip()

    sep = "  ".join("-" * w for w in widths)
    out = [line(header), sep]
    out += [line(r) for r in rows]
    if total:
        out += [sep, line(total)]
    return out


def _money_or_dash(w: WorkLine, value: Decimal) -> str:
    return "—" if w.state == CANCELLED else fmt_ru(value)


def render_console(report: Report) -> str:
    out = [
        f"ОТЧЁТ ОБ ОПЛАТЕ РАБОТ НА {fmt_date(report.as_of)}",
        "Суммы в рублях. «Выплачено» — деньги, которые ушли подрядчику к дате отчёта и не вернулись.",
        "«К оплате сейчас» — долг за уже принятые работы. «В очереди» — платежи, которые ещё не ушли.",
        "Цена в скобках — работа отменена, в итоги не входит.",
        "",
    ]
    for cr in report.contracts:
        out += _render_contract(cr)
        out.append("")
    out += _render_summary(report)
    return "\n".join(out) + "\n"


def _render_contract(cr: ContractReport) -> list[str]:
    c = cr.contract
    out = [f"Договор {c.contract_id} · {c.contractor} · подписан {fmt_date(c.signed_at)} · "
           f"сумма договора {fmt_ru(c.amount)}"]
    header = ["Работа", "Название", "Статус на дату", "Цена", "Выплачено", "Осталось",
              "К оплате сейчас", "В очереди"]
    rows = []
    for w in cr.works:
        rows.append([
            w.work.work_id, w.work.title, state_label(w),
            fmt_ru(w.work.price) if w.state != CANCELLED else f"({fmt_ru(w.work.price)})",
            _money_or_dash(w, w.paid), _money_or_dash(w, w.remaining),
            _money_or_dash(w, w.due_now), _money_or_dash(w, w.queued),
        ])
    # Итог строки — сумма колонки; переплата и излишек очереди показаны отдельной строкой ниже.
    total = ["Итого", "без отменённых", "", fmt_ru(cr.price_total), fmt_ru(cr.paid_to_works),
             fmt_ru(cr.remaining), fmt_ru(cr.due_now), fmt_ru(cr.queued_to_works)]
    if cr.works:
        out += ["  " + line for line in _table(header, rows, numeric_from=3, total=total)]
    else:
        out.append("  Работ по договору нет.")

    out.append(f"  Выплачено по договору: {fmt_ru(cr.paid_total)}"
               + (f", из них переплата {fmt_ru(cr.overpaid)}" if cr.overpaid else "")
               + f". Осталось оплатить: {fmt_ru(cr.remaining)}"
               + f", после оплаты очереди останется {fmt_ru(cr.remaining_after_queue)}"
               + (f", излишек очереди {fmt_ru(cr.queued_excess)}" if cr.queued_excess else "")
               + ".")
    shared = [w for w in cr.works if w.paid_shared]
    if shared:
        parts = ", ".join(f"{w.work.work_id} — {fmt_ru(w.paid_shared)}" for w in shared)
        out.append(f"  Из общих денег договора засчитано: {parts}.")
    for level in (WARN, INFO, LATER):
        notes = [n for n in cr.notes if n.level == level]
        if notes:
            out.append(f"  {NOTE_TITLES[level]}:")
            out += [f"    {'!' if level == WARN else '·'} {n.text}" for n in notes]
    return out


def _render_summary(report: Report) -> list[str]:
    header = ["Договор", "Подрядчик", "Сумма договора", "Цена работ", "Выплачено", "Осталось",
              "К оплате сейчас", "В очереди", "Переплата"]
    rows = [[cr.contract.contract_id, cr.contract.contractor, fmt_ru(cr.contract.amount),
             fmt_ru(cr.price_total), fmt_ru(cr.paid_total), fmt_ru(cr.remaining), fmt_ru(cr.due_now),
             fmt_ru(cr.queued_total), fmt_ru(cr.overpaid)] for cr in report.contracts]
    total = ["Итого", "", *[fmt_ru(v) for v in _totals(report)]] if report.contracts else None
    return ["ИТОГИ ПО ДОГОВОРАМ"] + _table(header, rows, numeric_from=2, total=total)


def _totals(report: Report) -> list[Decimal]:
    def total(getter):
        return sum((getter(cr) for cr in report.contracts), ZERO)
    return [total(lambda c: c.contract.amount), total(lambda c: c.price_total), total(lambda c: c.paid_total),
            total(lambda c: c.remaining), total(lambda c: c.due_now), total(lambda c: c.queued_total),
            total(lambda c: c.overpaid)]


# --- CSV ------------------------------------------------------------------------

CSV_FORMATS = {
    # Excel с русской локалью: «;» и десятичная запятая, BOM — чтобы не было кракозябр.
    "excel": {"delimiter": ";", "decimal_comma": True, "encoding": "utf-8-sig"},
    "plain": {"delimiter": ",", "decimal_comma": False, "encoding": "utf-8"},
}

WORK_HEADER = ["Дата отчёта", "Договор", "Подрядчик", "Работа", "Название", "Статус на дату", "Дата статуса",
               "Цена в выгрузке", "Цена к оплате", "Выплачено", "в т.ч. платежами с привязкой",
               "в т.ч. из общих денег договора", "Осталось оплатить", "К оплате сейчас", "В очереди",
               "Останется после очереди"]
CONTRACT_HEADER = ["Дата отчёта", "Договор", "Подрядчик", "Сумма договора", "Сумма цен работ",
                   "Цена отменённых работ", "Цена работ к оплате", "Выплачено", "Выплачено в счёт работ",
                   "Переплата", "Осталось оплатить", "К оплате сейчас", "В очереди",
                   "Останется после очереди", "Излишек очереди", "Возвращено подрядчиком"]
NOTE_HEADER = ["Дата отчёта", "Договор", "Тип", "Текст"]


_CONTRACT_SUM_KEYS = ["amount", "works_total", "cancelled_total", "price_total", "paid_total", "paid_to_works",
                      "overpaid", "remaining", "due_now", "queued_total", "remaining_after_queue",
                      "queued_excess", "refunded_total"]


def _contract_value(cr: ContractReport, key: str) -> Decimal:
    return cr.contract.amount if key == "amount" else getattr(cr, key)


def write_csv(report: Report, out_dir: Path, fmt: str = "excel") -> list[Path]:
    spec = CSV_FORMATS[fmt]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of.isoformat()

    def m(value: Decimal) -> str:
        return fmt_csv(value, spec["decimal_comma"])

    def dt(value) -> str:
        return fmt_date(value) if value else ""

    as_of = fmt_date(report.as_of)
    works_rows, contract_rows, note_rows = [], [], []
    for cr in report.contracts:
        c = cr.contract
        for w in cr.works:
            status_date = w.completed_at if w.state == DONE else (w.work.cancelled_at if w.state == CANCELLED else None)
            works_rows.append([as_of, c.contract_id, c.contractor, w.work.work_id, w.work.title,
                               STATE_LABELS[w.state], dt(status_date), m(w.work.price), m(w.price), m(w.paid),
                               m(w.paid_linked), m(w.paid_shared), m(w.remaining), m(w.due_now), m(w.queued),
                               m(w.remaining_after_queue)])
        contract_rows.append([as_of, c.contract_id, c.contractor, m(c.amount), m(cr.works_total),
                              m(cr.cancelled_total), m(cr.price_total), m(cr.paid_total), m(cr.paid_to_works),
                              m(cr.overpaid), m(cr.remaining), m(cr.due_now), m(cr.queued_total),
                              m(cr.remaining_after_queue), m(cr.queued_excess), m(cr.refunded_total)])
        note_rows += [[as_of, c.contract_id, NOTE_CSV_LABELS[n.level], n.text] for n in cr.notes]
    if report.contracts:
        sums = [sum((_contract_value(cr, key) for cr in report.contracts), ZERO) for key in _CONTRACT_SUM_KEYS]
        contract_rows.append([as_of, "Итого", "", *[m(v) for v in sums]])

    paths = []
    for name, header, rows in (("works", WORK_HEADER, works_rows),
                               ("contracts", CONTRACT_HEADER, contract_rows),
                               ("notes", NOTE_HEADER, note_rows)):
        path = out_dir / f"{name}_{stamp}.csv"
        with path.open("w", encoding=spec["encoding"], newline="") as fh:
            writer = csv.writer(fh, delimiter=spec["delimiter"], lineterminator="\r\n")
            writer.writerow(header)
            writer.writerows(rows)
        paths.append(path)
    return paths

