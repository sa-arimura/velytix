"""Velytix の Streamlit エントリーポイント。"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import partial
from pathlib import Path
from typing import Literal

import altair as alt
import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from analyzer import (
    DateRange,
    RankedProduct,
    SalesAnalysis,
    TrendUnit,
    analyze_sales,
    default_date_range,
)
from csv_loader import CSVLoadError, load_csv
from exporter import export_check_results, export_quantity_ranking, export_revenue_ranking
from validator import ErrorType, ValidationResult, validate_csv


_STATUS_OPTIONS = ("すべて", "正常", "エラー")
_CHECK_KEYS = ("check_status", "error_types")
_DATE_KEYS = ("start_date", "end_date")
_FILE_WIDGET_KEYS = ("search", "show_raw", *_CHECK_KEYS, *_DATE_KEYS, "top_n")
_RANKING_COLUMNS = ("順位", "商品コード", "代表商品名", "売上金額", "販売数量", "平均単価", "構成比")
_SEARCH_COLUMNS = ("order_id", "product_code", "product_name")


def main() -> None:
    st.set_page_config(page_title="Velytix", layout="wide")
    st.title("Velytix（ヴェリティクス）")
    st.write("受注データのチェックから売上分析まで、ひとつに。")

    uploaded = st.file_uploader("受注CSVを選択", type="csv", max_upload_size=10, key="orders_csv")
    _show_sample_downloads()
    if uploaded is None:
        if "upload_id" in st.session_state:
            _clear_file_state()
            del st.session_state["upload_id"]
        return

    reload_requested = st.button("再読み込み", key="reload_csv")
    if reload_requested or st.session_state.get("upload_id") != uploaded.file_id:
        _clear_file_state()
        st.session_state["upload_id"] = uploaded.file_id
        with st.spinner("CSVを読み込み、データをチェックしています…"):
            try:
                st.session_state["validation"] = validate_csv(load_csv(uploaded.getvalue()))
            except CSVLoadError as error:
                st.session_state["load_error"] = str(error)

    if "load_error" in st.session_state:
        st.error(st.session_state["load_error"])
        return

    result = st.session_state["validation"]
    st.success(f"CSVを読み込みました（{result.total_count:,}件）。")
    data_tab, check_tab, sales_tab = st.tabs(("データ一覧", "チェック結果", "売上分析"))
    with data_tab:
        _show_data(result)
    with check_tab:
        _show_checks(result)
    with sales_tab:
        _show_sales(result)


def _show_sample_downloads() -> None:
    folder = Path(__file__).resolve().parent
    samples = (
        ("正常データ100件をダウンロード", "sample_orders_clean.csv"),
        ("エラー混在データ100件をダウンロード", "sample_orders.csv"),
    )
    with st.expander("サンプルCSV"):
        for label, filename in samples:
            st.download_button(
                label, (folder / filename).read_bytes(), filename, "text/csv",
                key=filename, on_click="ignore",
            )


def _clear_file_state() -> None:
    state = st.session_state
    for key in (*_FILE_WIDGET_KEYS, "validation", "load_error"):
        state.pop(key, None)
    # 表の並び替えはブラウザ側の状態なので、新しいキーで表を作り直す。
    state["table_version"] = state.get("table_version", 0) + 1


def _metadata_columns(columns: tuple[str, ...], *labels: str) -> tuple[str, ...]:
    names = []
    for label in labels:
        name = label
        while name in columns:
            name += "（表示）"
        names.append(name)
    return tuple(names)


def _show_data(result: ValidationResult) -> None:
    query = st.text_input("受注ID・商品コード・商品名で検索", key="search").strip().casefold()
    raw = st.toggle("元データを表示", key="show_raw")
    positions = [result.columns.index(name) for name in _SEARCH_COLUMNS]
    rows = []
    for row in result.rows:
        source = row.source
        values = source.raw_values if raw else source.normalized_values
        if not query or any(query in values[index].casefold() for index in positions):
            rows.append((source.row_number, *values))

    st.caption(f"{result.total_count:,}件中{len(rows):,}件を表示")
    columns = (*_metadata_columns(result.columns, "元CSV行番号"), *result.columns)
    table = pd.DataFrame(rows, columns=columns)
    _show_table(table, "data", height=400)


def _toggle_error(kind: str) -> None:
    selected = st.session_state.get("error_types", [])
    st.session_state["error_types"] = (
        [item for item in selected if item != kind] if kind in selected else [*selected, kind]
    )


def _reset_checks() -> None:
    st.session_state["check_status"] = "すべて"
    st.session_state["error_types"] = []


def _show_checks(result: ValidationResult) -> None:
    _metrics((
        ("総データ数", f"{result.total_count:,}件"),
        ("正常データ数", f"{result.valid_count:,}件"),
        ("エラーデータ数", f"{result.error_count:,}件"),
    ))

    counts = result.error_counts
    selected = st.session_state.get("error_types", [])
    for kinds in (list(ErrorType)[:3], list(ErrorType)[3:]):
        for column, kind in zip(st.columns(3), kinds):
            column.button(
                f"{kind.value} · {counts[kind]:,}件",
                key=f"error_card_{kind.name}",
                type="primary" if kind.value in selected else "secondary",
                disabled=counts[kind] == 0,
                on_click=_toggle_error,
                args=(kind.value,),
                width="stretch",
            )

    selected = st.multiselect(
        "エラー種別", [kind.value for kind in ErrorType], key="error_types",
        placeholder="エラー種別を選択",
    )
    if selected:
        st.session_state["check_status"] = "エラー"
    status = st.selectbox(
        "ステータス", _STATUS_OPTIONS, key="check_status", disabled=bool(selected)
    )
    st.button("フィルターをリセット", key="reset_checks", on_click=_reset_checks)

    rows = []
    highlights = {}
    columns = (*_metadata_columns(result.columns, "元CSV行番号", "ステータス", "エラー詳細"), *result.columns)
    for row in result.rows:
        label = "正常" if row.is_valid else "エラー"
        if status != "すべて" and status != label:
            continue
        if selected and not any(error.kind.value in selected for error in row.errors):
            continue
        if row.errors:
            highlights[len(rows)] = {field for error in row.errors for field in error.fields}
        rows.append((
            row.source.row_number,
            label,
            "；".join(error.message for error in row.errors),
            *row.source.normalized_values,
        ))

    table = pd.DataFrame(rows, columns=columns)
    if highlights and table.size <= pd.get_option("styler.render.max_elements"):
        dark = st.context.theme.type == "dark"
        row_style = "background-color: #482936; color: #fff1f4" if dark else "background-color: #fff0f0; color: #39131a"
        cell_style = "background-color: #84384d; color: #ffffff" if dark else "background-color: #ffc5ce; color: #39131a"

        def style_row(data: pd.Series) -> list[str]:
            if data.name not in highlights:
                return [""] * len(data)
            fields = highlights[data.name]
            return [
                cell_style if index >= 3 and field in fields else row_style
                for index, field in enumerate(data.index)
            ]

        table = table.style.apply(style_row, axis=1)
    elif highlights:
        st.caption("表示件数が多いため、ステータスとエラー詳細で対象を確認できます。")
    _show_table(table, "checks", height=400)
    st.download_button(
        "チェック結果CSVをダウンロード", partial(export_check_results, result),
        "check_results.csv", "text/csv", key="download_checks", on_click="ignore",
    )


def _metrics(items: tuple[tuple[str, str], ...]) -> None:
    for column, (label, value) in zip(st.columns(len(items)), items):
        column.metric(label, value)


def _reset_dates(initial: DateRange) -> None:
    st.session_state["start_date"] = initial.start
    st.session_state["end_date"] = initial.end


def _show_sales(result: ValidationResult) -> None:
    initial = default_date_range(result)
    st.session_state.setdefault("start_date", initial.start if initial else None)
    st.session_state.setdefault("end_date", initial.end if initial else None)
    start_col, end_col = st.columns(2)
    bounds = {"min_value": date(2000, 1, 1), "max_value": date(2099, 12, 31)}
    start = start_col.date_input(
        "開始日", value=None, key="start_date", disabled=initial is None, **bounds
    )
    end = end_col.date_input(
        "終了日", value=None, key="end_date", disabled=initial is None, **bounds
    )
    st.button(
        "日付範囲をリセット", key="reset_dates", disabled=initial is None,
        on_click=partial(_reset_dates, initial) if initial else None,
    )

    limits = {"上位5件": 5, "上位10件": 10, "上位20件": 20, "すべて": None}
    selected = st.selectbox("ランキング表示件数", tuple(limits), index=1, key="top_n")

    if initial is None:
        st.info("正常データがありません。")
        period = None
    elif start is None or end is None:
        st.info("開始日と終了日を指定してください。")
        return
    else:
        try:
            period = DateRange(start, end)
        except ValueError as error:
            st.error(str(error))
            return

    analysis = analyze_sales(result, period, top_n=limits[selected])
    totals = analysis.totals
    _metrics((
        ("受注件数", f"{totals.order_count:,}件"),
        ("売上金額", f"{totals.revenue:,}円"),
        ("販売数量", f"{totals.quantity:,}個"),
    ))
    if initial is not None and totals.order_count == 0:
        st.info("指定期間にデータがありません")
    _show_rankings(analysis)
    _show_trend(analysis)


def _ranking_table(products: tuple[RankedProduct, ...]) -> pd.DataFrame:
    with localcontext() as context:
        context.prec = 42
        return pd.DataFrame.from_records(
            (
                (
                    item.rank, item.product_code, item.product_name, f"{item.revenue:,}",
                    f"{item.quantity:,}", f"{item.average_unit_price:,.2f}",
                    f'{(item.share * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)}%',
                )
                for item in products
            ),
            columns=_RANKING_COLUMNS,
        )


def _show_table(
    table: pd.DataFrame | Styler, name: str, height: int | Literal["auto"] = "auto"
) -> None:
    # 選択イベントを使わない表は、親コンテナのキーで描画を作り直す。
    with st.container(key=f"{name}_{st.session_state.table_version}"):
        st.dataframe(table, hide_index=True, height=height)


def _show_rankings(analysis: SalesAnalysis) -> None:
    st.subheader("売上金額ランキング")
    _show_table(_ranking_table(analysis.revenue_ranking), "revenue_ranking")
    st.download_button(
        "売上ランキングCSVをダウンロード", partial(export_revenue_ranking, analysis),
        "revenue_ranking.csv", "text/csv", key="download_revenue", on_click="ignore",
    )
    if analysis.revenue_ranking:
        rows = [
            {
                "商品コード": item.product_code,
                "代表商品名": item.product_name,
                "売上金額": item.revenue,
                "売上金額（円）": f"{item.revenue:,}",
                "表示順": index,
            }
            for index, item in enumerate(analysis.revenue_ranking)
        ]
        chart = alt.Chart(alt.Data(values=rows)).mark_bar().encode(
            x=alt.X("売上金額:Q", title="売上金額（円）"),
            y=alt.Y(
                "商品コード:N",
                sort=alt.SortField("表示順", order="ascending"),
                axis=alt.Axis(
                    labelPadding=9, labelLimit=160, labelOverlap="greedy", titlePadding=12,
                ),
            ),
            tooltip=["商品コード:N", "代表商品名:N", "売上金額（円）:N"],
        ).properties(height=300)
        st.altair_chart(chart, width="stretch", key="revenue_chart")

    st.subheader("販売数量ランキング")
    _show_table(_ranking_table(analysis.quantity_ranking), "quantity_ranking")
    st.download_button(
        "販売数量ランキングCSVをダウンロード", partial(export_quantity_ranking, analysis),
        "quantity_ranking.csv", "text/csv", key="download_quantity", on_click="ignore",
    )


def _show_trend(analysis: SalesAnalysis) -> None:
    st.subheader("売上推移グラフ")
    if analysis.trend_unit is None:
        return
    st.caption(f"集計単位：{analysis.trend_unit.value}")
    rows = [
        {
            "期間": (
                f"{point.period.start.year}年{point.period.start.month}月"
                if analysis.trend_unit == TrendUnit.MONTHLY else point.label
            ),
            "期間開始日": point.period.start.isoformat(),
            "売上金額": point.revenue,
            "売上金額（円）": f"{point.revenue:,}",
        }
        for point in analysis.trend
    ]
    chart = alt.Chart(alt.Data(values=rows)).mark_line(point=True).encode(
        x=alt.X(
            "期間:O",
            sort=alt.SortField("期間開始日", order="ascending"),
            axis=alt.Axis(labelAngle=0, labelOverlap="greedy", labelSeparation=8),
        ),
        order=alt.Order("期間開始日:T", sort="ascending"),
        y=alt.Y("売上金額:Q", title="売上金額（円）", scale=alt.Scale(zero=True)),
        tooltip=["期間:O", "売上金額（円）:N"],
    ).properties(height=300)
    st.altair_chart(chart, width="stretch", key="sales_trend")


if __name__ == "__main__":
    main()
