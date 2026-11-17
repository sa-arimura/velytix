import csv
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from pathlib import Path

import pyarrow as pa
from streamlit.testing.v1 import AppTest

from analyzer import analyze_sales
from exporter import export_quantity_ranking, export_revenue_ranking


def test_initial_screen_renders() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Velytix（ヴェリティクス）"
    assert app.markdown[0].value == "受注データのチェックから売上分析まで、ひとつに。"


HEADER = "order_id,line_no,order_date,product_code,product_name,quantity,unit_price"
ORDERS = (
    f"{HEADER}\n"
    "　A01 ,1,2024-01-01,P01,Alpha,2,50\n"
    "B02,1,2024-01-04,P02,Beta,1,80\n"
    "C03,1,2024-01-04,P03,Gamma,0,50\n"
    "D04,1,2024-02-30,P04,Delta,2,50\n"
    "E05,1,2024-01-04,P05,Echo,2,50\n"
    "E05,001,2024-01-04,P05,Echo,2,50\n"
).encode()
APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _upload(content=ORDERS, name="orders.csv"):
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.file_uploader(key="orders_csv").upload(name, content, "text/csv").run()
    assert not app.exception
    return app


def _metric(app, label):
    return next(metric.value for metric in app.metric if metric.label == label)


def test_upload_displays_tabs_and_keeps_validation_during_search_and_raw_toggle():
    app = _upload()
    validation = app.session_state["validation"]

    assert [tab.label for tab in app.tabs] == ["データ一覧", "チェック結果", "売上分析"]
    assert app.success[0].value == "CSVを読み込みました（6件）。"
    assert app.dataframe[0].value["order_id"].iloc[0] == "A01"
    app.text_input(key="search").set_value("aLpH").run()
    app.toggle(key="show_raw").set_value(True).run()

    assert not app.exception
    assert app.dataframe[0].value["元CSV行番号"].tolist() == [2]
    assert app.dataframe[0].value["order_id"].tolist() == ["　A01 "]
    assert len(app.dataframe[1].value) == 6
    assert app.session_state["validation"] is validation


def test_check_filters_apply_or_keep_global_counts_and_support_cards_and_reset():
    app = _upload()
    assert app.multiselect(key="error_types").proto.placeholder == "エラー種別を選択"
    app.selectbox(key="check_status").set_value("正常").run()
    assert app.dataframe[1].value["元CSV行番号"].tolist() == [2, 3]

    app.multiselect(key="error_types").set_value(["数量の不正", "受注日の不正"]).run()
    assert not app.exception
    assert app.selectbox(key="check_status").disabled
    assert app.selectbox(key="check_status").value == "エラー"
    assert app.dataframe[1].value["元CSV行番号"].tolist() == [4, 5]
    assert _metric(app, "総データ数") == "6件"
    assert _metric(app, "正常データ数") == "2件"
    assert _metric(app, "エラーデータ数") == "4件"

    assert app.button(key="error_card_REQUIRED").proto.disabled
    app.button(key="error_card_DUPLICATE").click().run()
    assert app.dataframe[1].value["元CSV行番号"].tolist() == [4, 5, 6, 7]
    app.button(key="error_card_DUPLICATE").click().run()
    assert app.dataframe[1].value["元CSV行番号"].tolist() == [4, 5]

    app.multiselect(key="error_types").set_value([]).run()
    assert not app.selectbox(key="check_status").disabled
    app.button(key="reset_checks").click().run()
    assert app.selectbox(key="check_status").value == "すべて"
    assert len(app.dataframe[1].value) == 6


def test_sales_date_filters_handle_empty_and_reversed_ranges_and_reset_only_dates():
    app = _upload()
    app.selectbox(key="top_n").set_value("上位5件").run()
    assert _metric(app, "売上金額") == "180円"
    assert app.dataframe[2].value["構成比"].tolist() == ["55.6%", "44.4%"]

    app.date_input(key="start_date").set_value(date(2024, 1, 2)).run()
    app.date_input(key="end_date").set_value(date(2024, 1, 3)).run()
    assert not app.exception
    assert _metric(app, "売上金額") == "0円"
    assert app.info[0].value == "指定期間にデータがありません"
    assert app.dataframe[2].value.empty
    assert app.dataframe[3].value.empty

    app.date_input(key="end_date").set_value(date(2024, 1, 1)).run()
    assert "開始日は終了日以前" in app.error[0].value
    app.button(key="reset_dates").click().run()
    assert not app.error
    assert _metric(app, "売上金額") == "180円"
    assert app.selectbox(key="top_n").value == "上位5件"


def test_ranking_limit_changes_both_tables():
    rows = "\n".join(f"A,{i},2024-01-01,P{i:02},商品,1,10" for i in range(1, 13))
    app = _upload(f"{HEADER}\n{rows}".encode())

    assert len(app.dataframe[2].value) == len(app.dataframe[3].value) == 10
    app.selectbox(key="top_n").set_value("上位5件").run()
    assert len(app.dataframe[2].value) == len(app.dataframe[3].value) == 5
    app.selectbox(key="top_n").set_value("すべて").run()
    assert len(app.dataframe[2].value) == len(app.dataframe[3].value) == 12


def test_no_valid_rows_disables_dates():
    app = _upload(f"{HEADER}\nA,1,2024-01-01,P,商品,0,10".encode())

    assert app.date_input(key="start_date").disabled
    assert app.date_input(key="end_date").disabled
    assert app.button(key="reset_dates").proto.disabled
    assert app.info[0].value == "正常データがありません。"
    assert _metric(app, "売上金額") == "0円"


def test_download_buttons_expose_three_csv_downloads():
    app = _upload()
    assert [button.proto.label for button in app.download_button[2:]] == [
        "チェック結果CSVをダウンロード",
        "売上ランキングCSVをダウンロード",
        "販売数量ランキングCSVをダウンロード",
    ]


def test_reload_and_replacement_reset_widgets_and_invalid_upload_clears_data():
    app = _upload()
    app.text_input(key="search").set_value("beta").run()
    app.toggle(key="show_raw").set_value(True).run()
    app.multiselect(key="error_types").set_value(["数量の不正"]).run()
    app.date_input(key="start_date").set_value(date(2024, 1, 2)).run()
    app.selectbox(key="top_n").set_value("上位5件").run()
    version = app.session_state["table_version"]
    previous_result = app.session_state["validation"]

    app.button(key="reload_csv").click().run()

    assert not app.exception
    assert app.session_state["table_version"] == version + 1
    assert app.session_state["validation"] is not previous_result
    assert app.text_input(key="search").value == ""
    assert not app.toggle(key="show_raw").value
    assert app.multiselect(key="error_types").value == []
    assert app.selectbox(key="check_status").value == "すべて"
    assert app.date_input(key="start_date").value == date(2024, 1, 1)
    assert app.selectbox(key="top_n").value == "上位10件"

    app.text_input(key="search").set_value("alpha").run()
    app.file_uploader(key="orders_csv").upload("other.csv", ORDERS, "text/csv").run()
    assert app.text_input(key="search").value == ""

    app.file_uploader(key="orders_csv").upload("invalid.csv", b"broken,header\nA,B", "text/csv").run()
    assert not app.exception
    assert "必須列" in app.error[0].value
    assert "validation" not in app.session_state
    assert not app.tabs
    assert not app.dataframe


def test_duplicate_styles_mark_both_key_cells():
    app = _upload()
    styles = app.dataframe[1].proto.arrow_data.styler.styles
    strong = next(line for line in styles.splitlines() if "#ffc5ce" in line)

    for row_index in (4, 5):
        assert f"_row{row_index}_col3" in strong
        assert f"_row{row_index}_col4" in strong
        assert f"_row{row_index}_col5" not in strong


def test_charts_include_revenue_bars_and_trend_for_empty_period():
    app = _upload()
    charts = app.get("vega_lite_chart")
    assert len(charts) == 2
    assert "bar" in charts[0].proto.spec
    assert "line" in charts[1].proto.spec

    app.date_input(key="start_date").set_value(date(2024, 1, 2)).run()
    app.date_input(key="end_date").set_value(date(2024, 1, 3)).run()
    charts = app.get("vega_lite_chart")
    assert len(charts) == 1
    assert "line" in charts[0].proto.spec



def test_sample_downloads_use_bundled_bytes_and_preserve_current_analysis(monkeypatch):
    import streamlit as st

    original = st.download_button
    captured = {}

    def record_download(label, data, file_name, *args, **kwargs):
        if file_name.startswith("sample_orders"):
            captured[file_name] = (data, kwargs["on_click"])
        return original(label, data, file_name, *args, **kwargs)

    monkeypatch.setattr(st, "download_button", record_download)
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert len(app.download_button) == 2
    assert not app.tabs

    app.file_uploader(key="orders_csv").upload("orders.csv", ORDERS, "text/csv").run()
    app.text_input(key="search").set_value("beta").run()
    validation = app.session_state["validation"]
    sales = _metric(app, "売上金額")
    assert len(app.download_button) == 5

    for filename in ("sample_orders_clean.csv", "sample_orders.csv"):
        payload, action = captured[filename]
        assert action == "ignore"
        assert payload == (APP_PATH.parent / filename).read_bytes()

    app.run()
    assert not app.exception
    assert app.session_state["validation"] is validation
    assert app.text_input(key="search").value == "beta"
    assert _metric(app, "売上金額") == sales


def test_bundled_samples_show_expected_kpis_and_removing_upload_restores_initial_screen():
    app = _upload((APP_PATH.parent / "sample_orders_clean.csv").read_bytes(), "clean.csv")
    assert _metric(app, "正常データ数") == "100件"
    assert _metric(app, "エラーデータ数") == "0件"
    assert _metric(app, "売上金額") == "295,030円"
    assert _metric(app, "受注件数") == "50件"
    assert _metric(app, "販売数量") == "948個"

    app.text_input(key="search").set_value("00001").run()
    app.file_uploader(key="orders_csv").upload(
        "mixed.csv", (APP_PATH.parent / "sample_orders.csv").read_bytes(), "text/csv"
    ).run()
    assert not app.exception
    assert app.text_input(key="search").value == ""
    assert _metric(app, "正常データ数") == "90件"
    assert _metric(app, "エラーデータ数") == "10件"
    assert _metric(app, "受注件数") == "45件"
    assert _metric(app, "売上金額") == "264,220円"
    assert _metric(app, "販売数量") == "850個"

    app.file_uploader(key="orders_csv").clear().run()
    assert not app.exception
    assert "validation" not in app.session_state
    assert not app.tabs
    assert len(app.download_button) == 2


def test_chart_labels_preserve_ranking_and_chronological_sales_data():
    app = _upload((APP_PATH.parent / "sample_orders_clean.csv").read_bytes())
    ranking, trend = [chart.proto for chart in app.get("vega_lite_chart")]
    rank_axis = json.loads(ranking.spec)["encoding"]["y"]
    assert rank_axis["axis"]["labelPadding"] > 2
    assert rank_axis["sort"] == {"field": "表示順", "order": "ascending"}

    labels = json.loads(trend.spec)["encoding"]
    assert labels["x"]["axis"]["labelAngle"] == 0
    assert labels["x"]["axis"]["labelOverlap"] == "greedy"
    assert labels["x"]["sort"] == {"field": "期間開始日", "order": "ascending"}
    assert labels["order"]["field"] == "期間開始日"
    rows = pa.ipc.open_stream(trend.data.data).read_all().to_pylist()
    assert [row["期間"] for row in rows] == [f"2024年{month}月" for month in range(1, 11)]
    assert rows[1]["売上金額"] == 0
    assert sum(row["売上金額"] for row in rows) == 295030

    app.date_input(key="end_date").set_value(date(2024, 3, 15)).run()
    weekly = app.get("vega_lite_chart")[-1].proto
    assert pa.ipc.open_stream(weekly.data.data).read_all().to_pylist()[0]["期間"] == "2024-01-03〜2024-01-07"

    app.date_input(key="end_date").set_value(date(2024, 1, 9)).run()
    daily = app.get("vega_lite_chart")[-1].proto
    assert pa.ipc.open_stream(daily.data.data).read_all().to_pylist()[0]["期間"] == "2024-01-03"


def test_ranking_csv_keeps_numeric_shares_and_matches_display_percentages():
    app = _upload((APP_PATH.parent / "sample_orders_clean.csv").read_bytes())
    analysis = analyze_sales(app.session_state["validation"])
    for table, export, expected in (
        (app.dataframe[2], export_revenue_ranking, analysis.revenue_ranking),
        (app.dataframe[3], export_quantity_ranking, analysis.quantity_ranking),
    ):
        rows = list(csv.DictReader(StringIO(export(analysis).decode("utf-8-sig"))))
        shares = [Decimal(row["構成比"]) for row in rows]
        assert shares == [product.share for product in expected]
        assert table.value["構成比"].tolist() == [
            f'{(share * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)}%'
            for share in shares
        ]
