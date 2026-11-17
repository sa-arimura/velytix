"""検証結果とランキングをダウンロード用のCSVに変換する。"""

import csv
from collections.abc import Iterable, Sequence
from io import StringIO
from unicodedata import category

from analyzer import RankedProduct, SalesAnalysis
from validator import REQUIRED_COLUMNS, ValidationResult


_CHECK_COLUMNS = ("check_row_number", "check_status", "check_errors")
_GENERATED_COLUMNS = (
    *_CHECK_COLUMNS,
    *REQUIRED_COLUMNS,
    *(f"raw_{column}" for column in REQUIRED_COLUMNS),
)
_RANKING_COLUMNS = (
    "順位", "商品コード", "代表商品名", "売上金額", "販売数量", "平均単価", "構成比",
)
_FORMULA_PREFIXES = "=+-@＝＋－＠"


def export_check_results(result: ValidationResult) -> bytes:
    """検証結果の全行を元CSV順に出力する。"""
    positions = {column: index for index, column in enumerate(result.columns)}
    required = [positions[column] for column in REQUIRED_COLUMNS]
    extra_columns = tuple(column for column in result.columns if column not in REQUIRED_COLUMNS)
    extras = [positions[column] for column in extra_columns]

    rows = (
        (
            row.source.row_number,
            "正常" if row.is_valid else "エラー",
            "；".join(error.message for error in row.errors),
            *(row.source.normalized_values[index] for index in required),
            *(row.source.raw_values[index] for index in required),
            *(row.source.raw_values[index] for index in extras),
        )
        for row in result.rows
    )
    return _write_csv(_check_headers(extra_columns), rows)


def export_revenue_ranking(analysis: SalesAnalysis) -> bytes:
    """指定期間・表示件数に対応する売上金額ランキングを出力する。"""
    return _export_ranking(analysis.revenue_ranking)


def export_quantity_ranking(analysis: SalesAnalysis) -> bytes:
    """指定期間・表示件数に対応する販売数量ランキングを出力する。"""
    return _export_ranking(analysis.quantity_ranking)


def _check_headers(extra_columns: tuple[str, ...]) -> tuple[str, ...]:
    # 連番が別の元ヘッダーや生成列の基本名にぶつからないよう先に予約する。
    original_extras = set(extra_columns)
    used = original_extras | set(_GENERATED_COLUMNS)
    generated = []
    for column in _GENERATED_COLUMNS:
        name = column
        if column in original_extras:
            suffix = 1
            while f"{column}_{suffix}" in used:
                suffix += 1
            name = f"{column}_{suffix}"
            used.add(name)
        generated.append(name)

    # ヘッダーも表計算ソフトではセルとして扱われる。
    safe_extras = []
    for column in extra_columns:
        name = _safe_text(column)
        if name != column:
            while name in used:
                name = "'" + name
            used.add(name)
        safe_extras.append(name)
    return (*generated, *safe_extras)


def _export_ranking(products: tuple[RankedProduct, ...]) -> bytes:
    rows = (
        (
            product.rank,
            product.product_code,
            product.product_name,
            product.revenue,
            product.quantity,
            format(product.average_unit_price, ".2f"),
            format(product.share, "f"),
        )
        for product in products
    )
    return _write_csv(_RANKING_COLUMNS, rows)


def _safe_text(value: str) -> str:
    """先頭に数式として解釈され得る文字がある場合、文字列マーカーを付ける。"""
    if value.startswith(("\t", "\r", "\n")):
        return "'" + value
    for character in value:
        if character.isspace() or category(character) in {"Cc", "Cf"}:
            continue
        if character in _FORMULA_PREFIXES:
            return "'" + value
        break
    return value


def _write_csv(headers: Sequence[str], rows: Iterable[Sequence[str | int]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(_safe_text(value) if isinstance(value, str) else value for value in row)
    return buffer.getvalue().encode("utf-8-sig")
