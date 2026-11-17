import codecs
import csv
from decimal import Decimal
from io import StringIO

import pytest

from analyzer import DateRange, analyze_sales
from csv_loader import load_csv
from exporter import export_check_results, export_quantity_ranking, export_revenue_ranking
from validator import REQUIRED_COLUMNS, ValidationResult, validate_csv


CHECK_HEADERS = [
    "check_row_number", "check_status", "check_errors",
    *REQUIRED_COLUMNS, *(f"raw_{column}" for column in REQUIRED_COLUMNS),
]
RANKING_HEADERS = [
    "順位", "商品コード", "代表商品名", "売上金額", "販売数量", "平均単価", "構成比",
]


def _validate(*rows, columns=REQUIRED_COLUMNS):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return validate_csv(load_csv(buffer.getvalue().encode()))


def _read_csv(payload):
    assert payload.startswith(codecs.BOM_UTF8)
    assert not payload.startswith(codecs.BOM_UTF8 * 2)
    return list(csv.reader(StringIO(payload.decode("utf-8-sig"), newline=""), strict=True))


def test_check_csv_preserves_groups_physical_rows_errors_and_source_values():
    loaded = load_csv((
        "notes,product_name,unit_price,order_date,product_code,line_no,quantity,order_id,tag\r\n"
        '\r\n  午前　,"鉛筆,赤""限定""\r\nセット",0,2024-01-01,0007,005,2,　001 ,青\r\n'
        '\r\n  午後　,ノート,9,2024-02-30,0008,1,0,002,白\r\n'
    ).encode())
    result = validate_csv(loaded)

    headers, first, second = _read_csv(export_check_results(result))

    assert headers == [*CHECK_HEADERS, "notes", "tag"]
    assert first == [
        "3", "正常", "", "001", "005", "2024-01-01", "0007", '鉛筆,赤"限定"\r\nセット',
        "2", "0", "　001 ", "005", "2024-01-01", "0007", '鉛筆,赤"限定"\r\nセット',
        "2", "0", "  午前　", "青",
    ]
    assert second[:3] == [
        "6", "エラー", "；".join(error.message for error in result.rows[1].errors),
    ]
    assert second[2].count("；") == 1
    assert second[-2:] == ["  午後　", "白"]


def test_resolves_generated_header_collisions_without_renaming_extra_columns():
    extras = ("check_status", "check_status_1", "raw_order_id", "check_row_number_1")
    result = _validate(
        ("001", "1", "2024-01-01", "P", "鉛筆", "2", "90", "a", "b", "c", "d"),
        columns=(*REQUIRED_COLUMNS, *extras),
    )

    headers, row = _read_csv(export_check_results(result))
    expected = CHECK_HEADERS.copy()
    expected[1] = "check_status_2"
    expected[10] = "raw_order_id_1"

    assert headers == [*expected, *extras]
    assert len(headers) == len(set(headers))
    assert row[-4:] == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "unsafe",
    [
        "=1+1", "+2", "-2", "@SUM(A1)", "  =1", "　+1", "\t-1", "\r\n@A1",
        "\x01=1", "\x7f+2", "\ufeff-1", "\u200b@A1", "＝1", "＋1", "－1", "＠A1",
        '=HYPERLINK("https://example.test","x")\r\nnext',
    ],
)
def test_check_csv_protects_values_and_keeps_source_data_unchanged(unsafe):
    result = _validate(
        (unsafe, "1", "2024-01-01", unsafe, unsafe, "2", "90", unsafe),
        columns=(*REQUIRED_COLUMNS, "notes"),
    )
    source = result.rows[0].source
    raw, normalized = source.raw_values, source.normalized_values

    _, row = _read_csv(export_check_results(result))

    for index in (3, 6, 7):
        assert row[index] == "'" + unsafe.strip(" \u3000")
    for index in (10, 13, 14, 17):
        assert row[index] == "'" + unsafe
    assert source.raw_values is raw
    assert source.normalized_values is normalized
    assert source.raw_values[0] == unsafe


def test_sanitized_extra_headers_stay_unique_and_safe_headers_are_preserved():
    extras = ("=注記", "'=注記", " 元の列名 ", '追加,"詳細"\r\n備考')
    result = _validate(
        ("A", "1", "2024-01-01", "P", "鉛筆", "1", "90", "a", "b", "c", "d"),
        columns=(*REQUIRED_COLUMNS, *extras),
    )

    headers, row = _read_csv(export_check_results(result))

    assert headers[-4:] == ["''=注記", *extras[1:]]
    assert row[-4:] == ["a", "b", "c", "d"]
    assert result.columns[-4:] == extras


def test_exports_both_rankings_with_period_rank_shares_and_numeric_cells():
    result = _validate(
        ("A", "1", "2024-01-01", "=P", "　@商品 ", "3", "0"),
        ("A", "2", "2024-01-01", "=P", "　@商品 ", "1", "10"),
        ("B", "1", "2024-01-01", "0007", "ノート", "1", "30"),
        ("C", "1", "2024-01-02", "Q", "定規", "20", "50"),
    )
    day = result.valid_rows[0].order.order_date
    analysis = analyze_sales(result, DateRange(day, day))
    sales_bytes = export_revenue_ranking(analysis)
    quantities_bytes = export_quantity_ranking(analysis)

    assert _read_csv(sales_bytes) == [
        RANKING_HEADERS,
        ["1", "0007", "ノート", "30", "1", "30.00", "0.75"],
        ["2", "'=P", "'@商品", "10", "4", "2.50", "0.25"],
    ]
    assert _read_csv(quantities_bytes) == [
        RANKING_HEADERS,
        ["1", "'=P", "'@商品", "10", "4", "2.50", "0.8"],
        ["2", "0007", "ノート", "30", "1", "30.00", "0.2"],
    ]
    assert ",30,1,30.00,0.75\r\n" in sales_bytes.decode("utf-8-sig")
    assert analysis.revenue_ranking[1].product_code == "=P"
    assert analysis.revenue_ranking[1].product_name == "@商品"


@pytest.mark.parametrize("export", [export_revenue_ranking, export_quantity_ranking])
def test_exports_only_selected_top_n_and_keeps_full_period_share(export):
    result = _validate(*(
        ("A", str(line), "2024-01-01", f"{line:02d}", "商品", "1", "1")
        for line in range(1, 9)
    ))
    headers, *rows = _read_csv(export(analyze_sales(result, top_n=5)))

    assert headers == RANKING_HEADERS
    assert [row[1] for row in rows] == ["01", "02", "03", "04", "05"]
    assert [Decimal(row[-1]) for row in rows] == [Decimal("0.125")] * 5


def test_empty_exports_have_bom_and_headers_only():
    result = ValidationResult(REQUIRED_COLUMNS, ())
    analysis = analyze_sales(result)

    assert _read_csv(export_check_results(result)) == [CHECK_HEADERS]
    assert _read_csv(export_revenue_ranking(analysis)) == [RANKING_HEADERS]
    assert _read_csv(export_quantity_ranking(analysis)) == [RANKING_HEADERS]
