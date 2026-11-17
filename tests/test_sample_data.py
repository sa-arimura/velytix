from codecs import BOM_UTF8
from datetime import date
from pathlib import Path

import pytest

from analyzer import DateRange, TrendUnit, analyze_sales
from csv_loader import load_csv
from generate_sample_data import generate_samples
from validator import ErrorType, REQUIRED_COLUMNS, validate_csv


PROJECT = Path(__file__).resolve().parents[1]
FILENAMES = ("sample_orders_clean.csv", "sample_orders.csv")


@pytest.mark.parametrize("filename", FILENAMES)
def test_generation_is_reproducible_and_matches_bundled_bom_csv(tmp_path, filename):
    generate_samples(tmp_path)
    path = tmp_path / filename
    first = path.read_bytes()
    generate_samples(tmp_path)

    assert first == path.read_bytes() == (PROJECT / filename).read_bytes()
    assert first.startswith(BOM_UTF8)
    assert not first.startswith(BOM_UTF8 * 2)
    loaded = load_csv(first)
    assert loaded.columns == REQUIRED_COLUMNS
    assert len(loaded.rows) == 100


def test_clean_sample_supports_sales_demo():
    result = validate_csv(load_csv((PROJECT / FILENAMES[0]).read_bytes()))
    analysis = analyze_sales(result)

    assert (result.valid_count, result.error_count) == (100, 0)
    assert analysis.totals.order_count == 50
    assert len(analysis.revenue_ranking) == 8
    assert len({product.quantity for product in analysis.quantity_ranking}) > 1
    assert len({product.revenue for product in analysis.revenue_ranking}) > 1
    prices = {
        row.order.unit_price for row in result.valid_rows if row.order.product_code == "001"
    }
    assert len(prices) > 1

    for period, unit in (
        (DateRange(date(2024, 1, 3), date(2024, 1, 9)), TrendUnit.DAILY),
        (DateRange(date(2024, 1, 3), date(2024, 3, 15)), TrendUnit.WEEKLY),
        (None, TrendUnit.MONTHLY),
    ):
        trend = analyze_sales(result, period)
        assert trend.trend_unit == unit
        assert any(point.revenue == 0 for point in trend.trend)
        assert any(point.revenue > 0 for point in trend.trend)


def test_mixed_sample_has_exact_error_counts_and_only_valid_rows_are_analyzed():
    payload = (PROJECT / FILENAMES[1]).read_bytes()
    result = validate_csv(load_csv(payload))

    assert (result.total_count, result.valid_count, result.error_count) == (100, 90, 10)
    assert all(result.error_counts[kind] > 0 for kind in ErrorType)
    assert any(len(row.errors) > 1 for row in result.error_rows)
    assert [
        row.source.normalized_values[:2]
        for row in result.error_rows
        if any(error.kind == ErrorType.DUPLICATE for error in row.errors)
    ] == [("E007", "1"), ("E007", "001")]

    clean_payload = (PROJECT / FILENAMES[0]).read_bytes()
    baseline = b"\n".join(clean_payload.split(b"\n")[:91]) + b"\n"
    expected = validate_csv(load_csv(baseline))
    assert analyze_sales(result) == analyze_sales(expected)
    assert analyze_sales(result).totals.order_count == 45
