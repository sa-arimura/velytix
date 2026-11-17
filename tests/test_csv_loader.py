import codecs

import pytest

from csv_loader import CSVLoadError, load_csv


HEADER = "order_id,line_no,order_date,product_code,product_name,quantity,unit_price"
ROW = "001,005,2026-11-16,0007,鉛筆,02,090"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp932"])
def test_reads_supported_encodings(encoding):
    loaded = load_csv(f"{HEADER}\n{ROW}".encode(encoding))

    assert loaded.columns == tuple(HEADER.split(","))
    assert len(loaded.rows) == 1
    assert loaded.rows[0].row_number == 2
    assert loaded.rows[0].raw_values == tuple(ROW.split(","))


def test_prefers_utf8_when_bytes_are_also_valid_cp932():
    payload = f"{HEADER}\n{ROW.replace('鉛筆', 'é')}".encode()
    assert "é" not in payload.decode("cp932")
    assert load_csv(payload).rows[0].raw_values[4] == "é"


def test_retains_column_order_and_keeps_both_versions_of_every_field():
    payload = (
        f"notes,{HEADER}\n"
        '　納品  午前　,　001 , 005,2026-11-16,0007,"  青　色 鉛筆　", 02,090'
    )

    loaded = load_csv(payload.encode())
    row = loaded.rows[0]

    assert loaded.columns == ("notes", *HEADER.split(","))
    assert row.raw_values == (
        "　納品  午前　", "　001 ", " 005", "2026-11-16", "0007", "  青　色 鉛筆　", " 02", "090",
    )
    assert row.normalized_values == (
        "納品  午前", "001", "005", "2026-11-16", "0007", "青　色 鉛筆", "02", "090",
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_counts_physical_lines_and_preserves_quoted_content(newline):
    name = f'赤,青{newline}{newline}鉛筆 "限定"'
    escaped_name = name.replace('"', '""')
    row = f'001,1,2026-11-16,0007,"{escaped_name}",2,90'
    payload = newline.join([HEADER, "", row, "", ROW, ""])

    loaded = load_csv(payload.encode())

    assert [row.row_number for row in loaded.rows] == [3, 7]
    assert loaded.rows[0].raw_values[4] == name


def test_retains_structurally_valid_rows_with_missing_or_invalid_values():
    payload = f"{HEADER}\n,,,,,,\n001,5.0,不正,0007,　 ,1e3,\n"

    rows = load_csv(payload.encode()).rows

    assert rows[0].raw_values == ("",) * 7
    assert rows[1].normalized_values == ("001", "5.0", "不正", "0007", "", "1e3", "")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (HEADER.replace("order_id", "Order_id"), "必須列.*order_id"),
        (f"{HEADER},order_id", "重複.*order_id"),
        (f'{HEADER},notes,"notes"', "重複.*notes"),
        (HEADER, "データレコードがありません"),
        (f"{HEADER}\n\n", "データレコードがありません"),
        ("", "データレコードがありません"),
        (f"{HEADER}\n{ROW}\n ", "3行目の列数"),
        (f"{HEADER}\n001,1", "2行目の列数"),
        (f"{HEADER}\n{ROW},余分", "2行目の列数"),
        (f'{HEADER}\n{ROW.replace("鉛筆", '"鉛筆')}', "2行目.*引用符"),
        (f'{HEADER}\n{ROW.replace("鉛筆", '鉛"筆')}', "2行目.*引用符"),
        (f'{HEADER}\n{ROW.replace("鉛筆", '"鉛筆" ')}', "2行目.*引用符"),
        (f'{HEADER}\n\n001,1,日付,0007,"一行目\n二行目', "3行目.*引用符"),
    ],
)
def test_reports_file_level_errors(payload, message):
    with pytest.raises(CSVLoadError, match=message):
        load_csv(payload.encode())


def test_rejects_bytes_unsupported_by_both_decoders():
    with pytest.raises(CSVLoadError, match="UTF-8またはCP932"):
        load_csv(HEADER.encode() + b"\n\x81")


def test_accepts_exactly_ten_mb_and_rejects_one_byte_more():
    prefix = codecs.BOM_UTF8 + f"{HEADER},notes\n{ROW},".encode()
    notes_length = 10_000_000 - len(prefix)
    payload = prefix + b"a" * notes_length

    assert len(load_csv(payload).rows[0].raw_values[-1]) == notes_length
    with pytest.raises(CSVLoadError, match="10MBを超えています"):
        load_csv(payload + b"a")
