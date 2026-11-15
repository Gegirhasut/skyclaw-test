"""Командная строка: python -m advance_report DATA_DIR DATE."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

from .calc import InvariantError, build_report
from .loader import DataError, load_dataset
from .report import CSV_FORMATS, render_console, write_csv

EXIT_OK, EXIT_DATA, EXIT_USAGE, EXIT_INTERNAL, EXIT_OUTPUT = 0, 1, 2, 3, 4


def parse_report_date(text: str) -> date:
    """ГГГГ-ММ-ДД или ДД.ММ.ГГГГ (как пишет бухгалтер)."""
    value = text.strip()
    for pattern, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y")):
        if re.fullmatch(pattern, value):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                break
    raise argparse.ArgumentTypeError(f"некорректная дата {text!r}: нужна ГГГГ-ММ-ДД или ДД.ММ.ГГГГ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m advance_report",
        description="Отчёт по работам и договорам: цена, выплачено, осталось оплатить — на дату.",
    )
    parser.add_argument("data_dir", type=Path, help="папка с contracts.csv, works.csv, payments.csv")
    parser.add_argument("date", type=parse_report_date, help="дата отчёта: 2026-11-10 или 10.11.2026")
    parser.add_argument("--out", type=Path, default=Path("report"),
                        help="куда сохранить CSV (по умолчанию ./report)")
    parser.add_argument("--csv-format", choices=sorted(CSV_FORMATS), default="excel",
                        help="excel — «;» и десятичная запятая для Excel (по умолчанию); "
                             "plain — «,» и точка")
    parser.add_argument("--no-csv", action="store_true", help="только вывод в консоль")
    return parser


def _utf8_streams() -> None:
    # В файл, пайп или NUL Windows-Python пишет в кодировке ANSI (cp1251/cp1252) и падает
    # на «»— и кириллице. Консоль Windows и так работает в UTF-8 — её не трогаем.
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if hasattr(stream, "reconfigure") and encoding != "utf8":
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)  # при ошибке аргументов — выход с кодом 2

    try:
        dataset = load_dataset(args.data_dir)
    except DataError as exc:
        print("Отчёт не построен: в выгрузке ошибки.", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_DATA

    try:
        report = build_report(dataset, args.date)
    except InvariantError as exc:
        print(f"Внутренняя ошибка расчёта, отчёт не выдан: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    sys.stdout.write(render_console(report))
    if not args.no_csv:
        try:
            paths = write_csv(report, args.out, args.csv_format)
        except OSError as exc:
            print(f"\nНе удалось сохранить CSV в {args.out}: {exc.strerror or exc}", file=sys.stderr)
            return EXIT_OUTPUT
        print("\nCSV сохранены:")
        for path in paths:
            print(f"  {path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
