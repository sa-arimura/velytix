from datetime import date

import pytest

from csv_loader import load_csv
from validator import REQUIRED_COLUMNS, ErrorType, normalize_row, validate_csv


def test_trims_only_ascii_and_fullwidth_spaces_at_both_ends():
    values = ("　 001 　", "  甲　乙 丙　", "　 ", "", "　\t甲\n　")

    assert normalize_row(values) == ("001", "甲　乙 丙", "", "", "\t甲\n")
    assert values[0] == "　 001 　"


def _validate(*records):
    header = "order_id,line_no,order_date,product_code,product_name,quantity,unit_price"
    return validate_csv(load_csv("\n".join((header, *records)).encode()))


@pytest.mark.parametrize(
    ("line_no", "quantity", "unit_price", "expected"),
    [
        ("1", "1", "0", (1, 1, 0)),
        ("999999", "999999", "999999999", (999999, 999999, 999999999)),
        ("005", "00002", "00090", (5, 2, 90)),
        ("0" * 5000 + "1", "1", "0" * 5000, (1, 1, 0)),
    ],
)
def test_accepts_integer_boundaries_and_leading_zeros(line_no, quantity, unit_price, expected):
    result = _validate(f"001,{line_no},2024-02-29,0007,鉛筆,{quantity},{unit_price}")
    row, = result.valid_rows

    assert row.is_valid
    assert (row.order.line_no, row.order.quantity, row.order.unit_price) == expected
    assert row.order.order_id == "001"
    assert row.order.product_code == "0007"
    assert row.source.normalized_values[1] == line_no
    assert result.error_rows == ()


@pytest.mark.parametrize("value", ["5.0", "-1", "+1", "１", "1e3", "1,000", "1 0", "1\t", "1" * 5000])
def test_rejects_invalid_integer_syntax_or_overflow(value):
    row, = _validate(f'001,"{value}",2024-02-29,0007,鉛筆,"{value}","{value}"').error_rows

    assert [(error.kind, error.fields) for error in row.errors] == [
        (ErrorType.LINE_NUMBER, ("line_no",)),
        (ErrorType.QUANTITY, ("quantity",)),
        (ErrorType.UNIT_PRICE, ("unit_price",)),
    ]


@pytest.mark.parametrize(
    ("record", "kinds"),
    [
        ("001,0,2024-02-29,0007,鉛筆,0,0", (ErrorType.LINE_NUMBER, ErrorType.QUANTITY)),
        (
            "001,1000000,2024-02-29,0007,鉛筆,1000000,1000000000",
            (ErrorType.LINE_NUMBER, ErrorType.QUANTITY, ErrorType.UNIT_PRICE),
        ),
    ],
)
def test_rejects_numeric_values_just_outside_the_range(record, kinds):
    row, = _validate(record).error_rows
    assert tuple(error.kind for error in row.errors) == kinds


@pytest.mark.parametrize("value", ["2000-01-01", "2000-02-29", "2024-02-29", "2099-12-31"])
def test_accepts_valid_dates_and_date_boundaries(value):
    row, = _validate(f"001,1,{value},0007,鉛筆,2,90").valid_rows
    assert row.order.order_date == date.fromisoformat(value)


@pytest.mark.parametrize(
    "value",
    [
        "1999-12-31", "2100-01-01", "2023-02-29", "2024-02-30", "2024-13-01",
        "2024-2-09", "2024-02-9", "20240229", "2024/02/29", "２０２４-02-29",
        "2024-02-29T00:00",
    ],
)
def test_rejects_invalid_dates(value):
    row, = _validate(f"001,1,{value},0007,鉛筆,2,90").error_rows

    assert [error.kind for error in row.errors] == [ErrorType.ORDER_DATE]
    assert row.errors[0].fields == ("order_date",)


def test_all_blank_fields_generate_one_required_error():
    result = _validate("　, ,　,,,　, ", "001,,2024-02-29,0007,鉛筆,,")
    first, second = result.error_rows

    assert len(first.errors) == len(second.errors) == 1
    assert first.errors[0].kind == second.errors[0].kind == ErrorType.REQUIRED
    assert first.errors[0].fields == REQUIRED_COLUMNS
    assert second.errors[0].fields == ("line_no", "quantity", "unit_price")
    assert "明細番号、数量、単価が未入力です。" == second.errors[0].message
    assert result.error_counts[ErrorType.REQUIRED] == 2


def test_validates_all_duplicates_and_counts_each_error_kind_in_spec_order():
    result = _validate(
        "　001 ,1,2024-02-29,0007,鉛筆,2,90",
        '001,001,2023-02-29,0007,"赤,青\n鉛筆",0,-1',
        "",
        "001,1,2024-02-29,0007,,2,90",
        "001,2,2024-02-29,0007,鉛筆,2,90",
        "1,1,2024-02-29,0007,鉛筆,2,90",
    )

    assert [row.source.row_number for row in result.rows] == [2, 3, 6, 7, 8]
    assert [row.source.row_number for row in result.valid_rows] == [7, 8]
    assert [row.source.row_number for row in result.error_rows] == [2, 3, 6]
    assert [error.kind for error in result.rows[0].errors] == [ErrorType.DUPLICATE]
    assert [error.kind for error in result.rows[1].errors] == [
        ErrorType.QUANTITY, ErrorType.UNIT_PRICE, ErrorType.ORDER_DATE, ErrorType.DUPLICATE,
    ]
    assert [error.kind for error in result.rows[2].errors] == [
        ErrorType.REQUIRED, ErrorType.DUPLICATE,
    ]
    for row in result.error_rows:
        assert row.errors[-1].fields == ("order_id", "line_no")
        assert row.order is None
    assert (result.total_count, result.valid_count, result.error_count) == (5, 2, 3)
    assert list(result.error_counts.items()) == list(zip(ErrorType, [1, 0, 1, 1, 1, 3]))


def test_ineligible_rows_do_not_generate_duplicate_errors():
    result = _validate(
        ",1,2024-02-29,0007,鉛筆,2,90",
        "　,001,2024-02-29,0007,鉛筆,2,90",
        "001,0,2024-02-29,0007,鉛筆,2,90",
        "001,0,2024-02-29,0007,鉛筆,2,90",
        "001,,2024-02-29,0007,鉛筆,2,90",
        "001,,2024-02-29,0007,鉛筆,2,90",
        "001,1,2024-02-29,0007,鉛筆,2,90",
    )

    assert result.valid_count == 1
    assert [error.kind for row in result.error_rows for error in row.errors] == [
        ErrorType.REQUIRED, ErrorType.REQUIRED, ErrorType.LINE_NUMBER, ErrorType.LINE_NUMBER,
        ErrorType.REQUIRED, ErrorType.REQUIRED,
    ]
    assert result.error_counts[ErrorType.DUPLICATE] == 0


def test_preserves_source_rows_and_accepts_shuffled_columns_and_extra_fields():
    loaded = load_csv(
        (
            "notes,product_name,unit_price,order_date,product_code,line_no,quantity,order_id\n"
            "　 メモ　,鉛筆,0,2024-02-29,0007,01,2, 001 "
        ).encode()
    )

    result = validate_csv(loaded)
    row, = result.valid_rows

    assert result.columns == loaded.columns
    assert row.source is loaded.rows[0]
    assert row.source.raw_values == (
        "　 メモ　", "鉛筆", "0", "2024-02-29", "0007", "01", "2", " 001 ",
    )
    assert row.source.normalized_values == (
        "メモ", "鉛筆", "0", "2024-02-29", "0007", "01", "2", "001",
    )
    assert row.order.line_no == 1
    assert row.order.quantity == 2
    assert row.order.unit_price == 0
