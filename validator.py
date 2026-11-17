"""受注CSVのフィールドを正規化し、入力値を検証する。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csv_loader import LoadedCSV, OrderRow


REQUIRED_COLUMNS = (
    "order_id",
    "line_no",
    "order_date",
    "product_code",
    "product_name",
    "quantity",
    "unit_price",
)

_COLUMN_LABELS = dict(zip(
    REQUIRED_COLUMNS,
    ("受注ID", "明細番号", "受注日", "商品コード", "商品名", "数量", "単価"),
))
_DIGITS = re.compile(r"[0-9]+")
_DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MIN_DATE = date(2000, 1, 1)
_MAX_DATE = date(2099, 12, 31)


class ErrorType(StrEnum):
    REQUIRED = "必須項目の未入力"
    LINE_NUMBER = "明細番号の不正"
    QUANTITY = "数量の不正"
    UNIT_PRICE = "単価の不正"
    ORDER_DATE = "受注日の不正"
    DUPLICATE = "受注IDと明細番号の重複"


@dataclass(frozen=True, slots=True)
class ValidationError:
    kind: ErrorType
    message: str
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrderValues:
    """検証を通過した明細の集計用データ。"""

    order_id: str
    line_no: int
    order_date: date
    product_code: str
    product_name: str
    quantity: int
    unit_price: int


@dataclass(frozen=True, slots=True)
class ValidatedRow:
    source: OrderRow
    errors: tuple[ValidationError, ...]
    order: OrderValues | None

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class ValidationResult:
    columns: tuple[str, ...]
    rows: tuple[ValidatedRow, ...]

    @property
    def valid_rows(self) -> tuple[ValidatedRow, ...]:
        return tuple(row for row in self.rows if row.is_valid)

    @property
    def error_rows(self) -> tuple[ValidatedRow, ...]:
        return tuple(row for row in self.rows if row.errors)

    @property
    def total_count(self) -> int:
        return len(self.rows)

    @property
    def valid_count(self) -> int:
        return sum(row.is_valid for row in self.rows)

    @property
    def error_count(self) -> int:
        return self.total_count - self.valid_count

    @property
    def error_counts(self) -> dict[ErrorType, int]:
        counts = Counter(error.kind for row in self.rows for error in row.errors)
        return {kind: counts[kind] for kind in ErrorType}


def normalize_row(values: tuple[str, ...]) -> tuple[str, ...]:
    """各フィールドの両端から半角・全角スペースを取り除く。"""
    return tuple(value.strip(" \u3000") for value in values)


def validate_csv(loaded: LoadedCSV) -> ValidationResult:
    """読み込み済みのCSVを検証し、結果を元の並び順で返す。"""
    positions = {column: index for index, column in enumerate(loaded.columns)}
    keys = [
        _duplicate_key(
            row.normalized_values[positions["order_id"]],
            row.normalized_values[positions["line_no"]],
        )
        for row in loaded.rows
    ]
    # 他の項目にエラーがあっても、有効なキーなら重複件数に含める。
    key_counts = Counter(key for key in keys if key is not None)

    rows = []
    for row, key in zip(loaded.rows, keys):
        values = {
            column: row.normalized_values[positions[column]]
            for column in REQUIRED_COLUMNS
        }
        rows.append(_validate_row(row, values, key_counts[key] > 1))
    return ValidationResult(loaded.columns, tuple(rows))


def _parse_number(value: str, minimum: int, maximum: int) -> int | None:
    if _DIGITS.fullmatch(value) is None:
        return None

    # 先頭ゼロは許容し、範囲外の巨大な数字を int() に渡さない。
    digits = value.lstrip("0") or "0"
    if len(digits) > len(str(maximum)):
        return None
    number = int(digits)
    return number if minimum <= number <= maximum else None


def _parse_date(value: str) -> date | None:
    if _DATE_FORMAT.fullmatch(value) is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if _MIN_DATE <= parsed <= _MAX_DATE else None


def _duplicate_key(order_id: str, line_value: str) -> tuple[str, int] | None:
    line_no = _parse_number(line_value, 1, 999999)
    if order_id and line_no is not None:
        return order_id, line_no
    return None


def _validate_row(
    row: OrderRow,
    values: dict[str, str],
    is_duplicate: bool,
) -> ValidatedRow:
    errors = []
    missing = tuple(column for column in REQUIRED_COLUMNS if not values[column])
    if missing:
        labels = "、".join(_COLUMN_LABELS[column] for column in missing)
        errors.append(ValidationError(ErrorType.REQUIRED, f"{labels}が未入力です。", missing))

    line_no = _parse_number(values["line_no"], 1, 999999)
    quantity = _parse_number(values["quantity"], 1, 999999)
    unit_price = _parse_number(values["unit_price"], 0, 999999999)
    order_date = _parse_date(values["order_date"])
    format_checks = (
        ("line_no", line_no, ErrorType.LINE_NUMBER, "半角数字で1〜999999を入力してください。"),
        ("quantity", quantity, ErrorType.QUANTITY, "半角数字で1〜999999を入力してください。"),
        ("unit_price", unit_price, ErrorType.UNIT_PRICE, "半角数字で0〜999999999を入力してください。"),
        ("order_date", order_date, ErrorType.ORDER_DATE,
         "2000-01-01〜2099-12-31の実在する日付をYYYY-MM-DD形式で入力してください。"),
    )
    for field, parsed, kind, instruction in format_checks:
        if values[field] and parsed is None:
            errors.append(ValidationError(kind, f"{_COLUMN_LABELS[field]}は{instruction}", (field,)))

    if is_duplicate:
        errors.append(ValidationError(
            ErrorType.DUPLICATE,
            "同じ受注IDと明細番号の組み合わせが複数あります。",
            ("order_id", "line_no"),
        ))

    if errors:
        return ValidatedRow(row, tuple(errors), None)

    # この時点で必須項目は入力済みで、型変換もすべて成功している。
    assert line_no is not None and quantity is not None and unit_price is not None
    assert order_date is not None
    order = OrderValues(
        order_id=values["order_id"],
        line_no=line_no,
        order_date=order_date,
        product_code=values["product_code"],
        product_name=values["product_name"],
        quantity=quantity,
        unit_price=unit_price,
    )
    return ValidatedRow(row, (), order)
