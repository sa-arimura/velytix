from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest

from analyzer import DateRange, SalesTotals, TrendUnit, analyze_sales, default_date_range
from csv_loader import load_csv
from validator import validate_csv


def _validated(*rows):
    header = "order_id,line_no,order_date,product_code,product_name,quantity,unit_price"
    return validate_csv(load_csv("\n".join((header, *rows)).encode()))


def _period(start, end):
    return DateRange(date.fromisoformat(start), date.fromisoformat(end))


def test_date_range_and_kpis_use_only_valid_orders_and_include_both_dates():
    result = _validated(
        "A,1,2024-01-01,P,鉛筆,2,90",
        "A,2,2024-01-02,P,鉛筆,3,100",
        "B,1,2024-01-03,Q,ノート,4,80",
        "C,1,2024-01-04,Q,ノート,1,100",
        "D,1,2024-01-05,Q,ノート,0,100",
        "E,1,2023-12-31,Q,ノート,1,100",
        "E,001,2023-12-31,Q,ノート,1,100",
    )
    original_rows = result.rows

    assert default_date_range(result) == _period("2024-01-01", "2024-01-04")
    assert analyze_sales(result).period == default_date_range(result)
    analysis = analyze_sales(result, _period("2024-01-02", "2024-01-04"))
    assert analysis.totals == SalesTotals(3, 720, 8)
    assert [point.revenue for point in analysis.trend] == [300, 320, 100]
    assert analyze_sales(result).totals == SalesTotals(3, 900, 10)
    assert result.rows is original_rows


def test_rejects_reversed_dates():
    with pytest.raises(ValueError, match="開始日は終了日以前"):
        _period("2024-01-02", "2024-01-01")


def test_revenue_remains_an_exact_integer_above_float_precision():
    result = _validated(*(
        f"A,{line},2024-01-01,P,鉛筆,999999,999999999"
        for line in range(1, 21)
    ), "B,1,2024-01-01,P,鉛筆,1,1")
    analysis = analyze_sales(result)
    expected = 20 * 999999 * 999999999 + 1

    assert expected > 2**53
    assert analysis.totals.revenue == expected
    assert analysis.trend[0].revenue == expected
    assert analysis.revenue_ranking[0].revenue == expected
    assert analysis.revenue_ranking[0].average_unit_price == Decimal("999999949.00")


def test_both_rankings_use_competition_rank_and_product_code_order():
    result = _validated(
        "A,1,2024-01-01,A,鉛筆,2,100",
        "B,1,2024-01-01,C,ノート,3,30",
        "C,1,2024-01-01,B,メモ帳,5,18",
        "D,1,2024-01-01,D,消しゴム,3,10",
    )
    analysis = analyze_sales(result)

    assert [(row.rank, row.product_code) for row in analysis.revenue_ranking] == [
        (1, "A"), (2, "B"), (2, "C"), (4, "D"),
    ]
    assert [(row.rank, row.product_code) for row in analysis.quantity_ranking] == [
        (1, "B"), (2, "C"), (2, "D"), (4, "A"),
    ]


@pytest.mark.parametrize(("top_n", "count"), [(5, 5), (10, 10), (20, 20), (None, 22)])
def test_ranking_limit_applies_even_when_every_product_ties(top_n, count):
    result = _validated(*(
        f"A,{line},2024-01-01,{line:02d},鉛筆,1,1"
        for line in range(22, 0, -1)
    ))
    analysis = analyze_sales(result, top_n=top_n)
    expected = [f"{line:02d}" for line in range(1, count + 1)]

    for ranking in (analysis.revenue_ranking, analysis.quantity_ranking):
        assert [row.product_code for row in ranking] == expected
        assert {row.rank for row in ranking} == {1}
        assert ranking[0].share == analyze_sales(result).revenue_ranking[0].share
    assert len(analyze_sales(result).revenue_ranking) == 10


def test_representative_name_uses_full_valid_csv_and_breaks_ties_by_csv_order():
    result = _validated(
        "A,1,2024-01-03,P,旧名,1,10",
        "A,2,2024-01-03,Q,CSVで先の名前,1,10",
        "A,3,2024-01-01,P,最多の名前,1,10",
        "A,4,2024-01-01,Q,日付が先の名前,1,10",
        "A,5,2024-01-02,P,最多の名前,1,10",
        "A,6,2024-01-03,P,旧名,0,10",
    )
    expected = {"P": "最多の名前", "Q": "CSVで先の名前"}

    for day in ("2024-01-01", "2024-01-03"):
        analysis = analyze_sales(result, _period(day, day))
        assert {row.product_code: row.product_name for row in analysis.revenue_ranking} == expected


def test_average_unit_price_and_shares_use_decimal_and_period_totals():
    result = _validated(
        "A,1,2024-01-01,P,鉛筆,7,0",
        "A,2,2024-01-01,P,鉛筆,1,1",
        "A,3,2024-01-01,Q,ノート,2,2",
        "A,4,2024-01-02,R,定規,20,50",
    )
    with localcontext() as context:
        context.prec = 3
        analysis = analyze_sales(result, _period("2024-01-01", "2024-01-01"))
    q, p = analysis.revenue_ranking

    assert p.average_unit_price == Decimal("0.13")
    assert str(q.average_unit_price) == "2.00"
    assert p.share == Decimal("0.2")
    assert analysis.quantity_ranking[0].share == Decimal("0.8")


def test_zero_revenue_retains_products_and_quantity_shares():
    result = _validated(
        "A,1,2024-01-01,B,筆箱,3,0",
        "B,1,2024-01-01,A,定規,1,0",
    )
    analysis = analyze_sales(result)

    assert analysis.totals == SalesTotals(2, 0, 4)
    assert [(row.rank, row.product_code, row.share) for row in analysis.revenue_ranking] == [
        (1, "A", Decimal(0)), (1, "B", Decimal(0)),
    ]
    assert [row.share for row in analysis.quantity_ranking] == [Decimal("0.75"), Decimal("0.25")]


@pytest.mark.parametrize(
    ("days", "unit"),
    [(1, TrendUnit.DAILY), (31, TrendUnit.DAILY), (32, TrendUnit.WEEKLY),
     (180, TrendUnit.WEEKLY), (181, TrendUnit.MONTHLY)],
)
def test_trend_unit_uses_inclusive_days_and_empty_period_is_zero_filled(days, unit):
    result = _validated("A,1,2024-01-01,P,鉛筆,1,1")
    start = date(2024, 2, 1)
    period = DateRange(start, start + timedelta(days=days - 1))
    analysis = analyze_sales(result, period)

    assert analysis.period == period
    assert analysis.trend_unit == unit
    assert analysis.totals == SalesTotals(0, 0, 0)
    assert analysis.revenue_ranking == analysis.quantity_ranking == ()
    assert analysis.trend[0].period.start == period.start
    assert analysis.trend[-1].period.end == period.end
    assert all(point.revenue == 0 for point in analysis.trend)


def test_weekly_trend_clips_edge_weeks_and_zero_fills_missing_weeks():
    result = _validated(*(
        f"A,{line},{day},P,鉛筆,1,{line}"
        for line, day in enumerate((
            "2024-01-02", "2024-01-03", "2024-01-07", "2024-01-08", "2024-02-04", "2024-02-05",
        ), 1)
    ))
    analysis = analyze_sales(result, _period("2024-01-03", "2024-02-04"))

    assert [(point.label, point.revenue) for point in analysis.trend] == [
        ("2024-01-03〜2024-01-07", 5),
        ("2024-01-08〜2024-01-14", 4),
        ("2024-01-15〜2024-01-21", 0),
        ("2024-01-22〜2024-01-28", 0),
        ("2024-01-29〜2024-02-04", 5),
    ]

    clipped = analyze_sales(result, _period("2024-01-03", "2024-02-03"))
    assert clipped.trend[-1].label == "2024-01-29〜2024-02-03"
    assert clipped.trend[-1].revenue == 0
    assert clipped.totals.revenue == 9


def test_monthly_trend_zero_fills_missing_months_and_clips_period():
    result = _validated(
        "A,1,2024-01-14,P,鉛筆,1,10",
        "A,2,2024-01-15,P,鉛筆,1,20",
        "A,3,2024-02-29,P,鉛筆,1,30",
        "A,4,2024-07-15,P,鉛筆,1,40",
        "A,5,2024-07-16,P,鉛筆,1,50",
    )
    period = _period("2024-01-15", "2024-07-15")
    analysis = analyze_sales(result, period)

    assert [point.label for point in analysis.trend] == [f"2024-{month:02d}" for month in range(1, 8)]
    assert [point.revenue for point in analysis.trend] == [20, 30, 0, 0, 0, 0, 40]
    assert analysis.trend[0].period == _period("2024-01-15", "2024-01-31")
    assert analysis.trend[-1].period == _period("2024-07-01", "2024-07-15")


def test_no_valid_rows_means_no_default_period_or_trend():
    result = _validated("A,1,2024-01-01,P,鉛筆,0,10")
    analysis = analyze_sales(result)

    assert default_date_range(result) is None
    assert analysis.period is analysis.trend_unit is None
    assert analysis.totals == SalesTotals(0, 0, 0)
    assert analysis.trend == analysis.revenue_ranking == analysis.quantity_ranking == ()


def test_daily_trend_includes_days_without_orders():
    result = _validated(
        "A,1,2024-02-28,P,鉛筆,2,10",
        "B,1,2024-03-01,P,鉛筆,3,10",
    )
    analysis = analyze_sales(result)

    assert [(point.label, point.revenue) for point in analysis.trend] == [
        ("2024-02-28", 20), ("2024-02-29", 0), ("2024-03-01", 30),
    ]
