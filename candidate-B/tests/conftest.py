from __future__ import annotations

from pathlib import Path

import pytest

from advance_report.loader import CONTRACT_COLUMNS, PAYMENT_COLUMNS, WORK_COLUMNS

REAL_DATA = Path(__file__).resolve().parent.parent / "data"


def _write(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    lines = [",".join(columns)]
    for row in rows:
        values = []
        for col in columns:
            value = str(row.get(col, ""))
            if any(ch in value for ch in ',"'):
                value = '"' + value.replace('"', '""') + '"'
            values.append(value)
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def make_data(tmp_path):
    """Пишет маленькую выгрузку в tmp-папку и возвращает путь к ней.

    Строки — словари; незаданные колонки остаются пустыми.
    """

    def factory(contracts=None, works=None, payments=None) -> Path:
        _write(tmp_path / "contracts.csv", CONTRACT_COLUMNS, contracts or [])
        _write(tmp_path / "works.csv", WORK_COLUMNS, works or [])
        _write(tmp_path / "payments.csv", PAYMENT_COLUMNS, payments or [])
        return tmp_path

    return factory


@pytest.fixture
def real_data() -> Path:
    return REAL_DATA
