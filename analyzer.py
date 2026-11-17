"""検証済みの受注明細から売上を集計する。"""

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from enum import StrEnum
from typing import Literal

from validator import OrderValues, ValidationResult


_ONE_DAY = timedelta(days=1)
_DECIMAL_CONTEXT = Context(prec=40, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("開始日は終了日以前の日付を指定してください。")


@dataclass(frozen=True, slots=True)
class SalesTotals:
    order_count: int
    revenue: int
    quantity: int


@dataclass(frozen=True, slots=True)
class RankedProduct:
    rank: int
    product_code: str
    product_name: str
    revenue: int
    quantity: int
    average_unit_price: Decimal
    share: Decimal


class TrendUnit(StrEnum):
    DAILY = "日別"
    WEEKLY = "週別"
    MONTHLY = "月別"


@dataclass(frozen=True, slots=True)
class TrendPoint:
    period: DateRange
    label: str
    revenue: int


@dataclass(frozen=True, slots=True)
class SalesAnalysis:
    period: DateRange | None
    totals: SalesTotals
    revenue_ranking: tuple[RankedProduct, ...]
    quantity_ranking: tuple[RankedProduct, ...]
    trend_unit: TrendUnit | None
    trend: tuple[TrendPoint, ...]


def default_date_range(result: ValidationResult) -> DateRange | None:
    """正常な受注明細がある場合に、最初と最後の受注日を返す。"""
    return _date_range(_valid_orders(result))


def analyze_sales(
    result: ValidationResult,
    period: DateRange | None = None,
    top_n: Literal[5, 10, 20] | None = 10,
) -> SalesAnalysis:
    """指定期間を集計する。期間省略時は全正常明細、top_n=None は全商品を対象にする。"""
    if top_n not in (5, 10, 20, None):
        raise ValueError("ランキング件数は5件、10件、20件、またはすべてを指定してください。")

    orders = _valid_orders(result)
    if period is None:
        period = _date_range(orders)
    if period is None:
        return SalesAnalysis(None, SalesTotals(0, 0, 0), (), (), None, ())

    names = _representative_names(orders)
    unit = _trend_unit(period)
    order_ids = set()
    product_revenue: Counter[str] = Counter()
    product_quantity: Counter[str] = Counter()
    period_revenue: Counter[date] = Counter()
    for order in orders:
        if period.start <= order.order_date <= period.end:
            revenue = order.quantity * order.unit_price
            order_ids.add(order.order_id)
            product_revenue[order.product_code] += revenue
            product_quantity[order.product_code] += order.quantity
            period_revenue[_bucket_start(order.order_date, unit)] += revenue

    totals = SalesTotals(len(order_ids), product_revenue.total(), product_quantity.total())
    return SalesAnalysis(
        period=period,
        totals=totals,
        revenue_ranking=_rank_products(
            product_revenue, totals.revenue, product_revenue, product_quantity, names, top_n
        ),
        quantity_ranking=_rank_products(
            product_quantity, totals.quantity, product_revenue, product_quantity, names, top_n
        ),
        trend_unit=unit,
        trend=_build_trend(period, unit, period_revenue),
    )


def _valid_orders(result: ValidationResult) -> tuple[OrderValues, ...]:
    return tuple(row.order for row in result.valid_rows if row.order is not None)


def _date_range(orders: tuple[OrderValues, ...]) -> DateRange | None:
    if not orders:
        return None
    days = [order.order_date for order in orders]
    return DateRange(min(days), max(days))


def _representative_names(orders: tuple[OrderValues, ...]) -> dict[str, str]:
    counts: dict[str, Counter[str]] = {}
    for order in orders:
        counts.setdefault(order.product_code, Counter())[order.product_name] += 1

    # Counter は同数なら最初に追加された名前を優先する。明細は元CSV順。
    return {code: names.most_common(1)[0][0] for code, names in counts.items()}


def _rank_products(
    scores: Counter[str],
    total_score: int,
    revenues: Counter[str],
    quantities: Counter[str],
    names: dict[str, str],
    top_n: int | None,
) -> tuple[RankedProduct, ...]:
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    products = []
    previous_score = None
    rank = 0
    with localcontext(_DECIMAL_CONTEXT):
        for position, (code, score) in enumerate(ranked[:top_n], start=1):
            if score != previous_score:
                rank = position
            previous_score = score
            revenue = revenues[code]
            quantity = quantities[code]
            products.append(RankedProduct(
                rank=rank,
                product_code=code,
                product_name=names[code],
                revenue=revenue,
                quantity=quantity,
                average_unit_price=(Decimal(revenue) / quantity).quantize(Decimal("0.01")),
                share=Decimal(score) / total_score if total_score else Decimal(0),
            ))
    return tuple(products)


def _trend_unit(period: DateRange) -> TrendUnit:
    days = (period.end - period.start).days + 1
    if days <= 31:
        return TrendUnit.DAILY
    if days <= 180:
        return TrendUnit.WEEKLY
    return TrendUnit.MONTHLY


def _bucket_start(day: date, unit: TrendUnit) -> date:
    if unit == TrendUnit.WEEKLY:
        return day - timedelta(days=day.weekday())
    if unit == TrendUnit.MONTHLY:
        return day.replace(day=1)
    return day


def _bucket_end(start: date, end: date, unit: TrendUnit) -> date:
    if unit == TrendUnit.WEEKLY:
        return start + timedelta(days=min(6, (end - start).days))
    if unit == TrendUnit.MONTHLY:
        last_day = monthrange(start.year, start.month)[1]
        return min(start.replace(day=last_day), end)
    return start


def _build_trend(
    period: DateRange,
    unit: TrendUnit,
    revenues: Counter[date],
) -> tuple[TrendPoint, ...]:
    points = []
    bucket_start = _bucket_start(period.start, unit)
    while True:
        start = max(bucket_start, period.start)
        end = _bucket_end(bucket_start, period.end, unit)
        label = start.isoformat()
        if unit == TrendUnit.WEEKLY:
            label = f"{start.isoformat()}〜{end.isoformat()}"
        elif unit == TrendUnit.MONTHLY:
            label = start.isoformat()[:7]
        points.append(TrendPoint(DateRange(start, end), label, revenues[bucket_start]))
        if end == period.end:
            return tuple(points)
        bucket_start = end + _ONE_DAY
